"""Event reducer stage for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.character.attunement import (
    AttunementError,
    canonical_item_ref,
    clear_attunement_on_death,
)
from ..items.charges import (
    ChargesError,
    spend_charges,
    validate_charges_declaration,
)
from src.rulesets.dnd2024.features.models import ClassFeatureError
from src.rulesets.dnd2024.features.resources import (
    feature_usage_count,
    set_feature_usage_count,
)
from src.rulesets.dnd2024.spells.feat_spells import concentration_holder
from src.rulesets.dnd2024.spells.font_of_magic import MAX_CREATED_SLOT_LEVEL
from src.rulesets.dnd2024.spells.casting import apply_free_cast_cost
from src.rulesets.events import EventBatchError
from .primitives import actor_kind as _actor_kind
from .primitives import FEATURE_STATE_KEYS, PERSISTENT_RAGE_DURATION, RELENTLESS_RAGE_DECISION


_ITEM_EVENT_FIELDS = {
    "dnd2024.item.consumed": frozenset({"type", "actor_id", "item_ref", "amount"}),
    "dnd2024.item.activated": frozenset({"type", "actor_id", "item_ref", "target_id", "cost"}),
    "dnd2024.item.charge_spent": frozenset({"type", "actor_id", "item_ref", "amount"}),
    "dnd2024.item.destroy_check": frozenset({
        "type", "actor_id", "item_ref", "roll", "threshold", "destroyed",
    }),
    "dnd2024.item.destroyed": frozenset({"type", "actor_id", "item_ref"}),
}


# 只有行动经济池可以被特性追加；其余 economy 字段（actor_id/speed/...）不是资源。
_GRANTABLE_ECONOMY = frozenset({
    "action", "bonus_action", "reaction", "movement", "surge_action",
})
# economy.granted 的 flags 是引擎内部记账（如 action_surge_used 的「每回合
# 一次」），不是向 economy 任意写字段的通道；未知 flag fail closed。
_GRANTABLE_FLAGS = frozenset({"action_surge_used"})
# 只在回合边界到期的条件时长；战斗结束时一并清除。
_TURN_SCOPED_DURATIONS = frozenset({
    "actor_turn_start", "target_turn_start", "caster_turn_end",
    "caster_next_turn_end", "after_next_attack", "attacker_next_turn_start",
})


class CombatReducerMixin:
    """Apply validated events to a detached authoritative snapshot."""

    __slots__ = ()

    def _reduce_event(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        state = snapshot["ruleset_state"]
        combat = state["combat"]
        event_type = str(event["type"])
        if event_type in {
            "intent.submitted", "check.resolved", "dnd2024.spell.cast",
            "dnd2024.combat.message", "dnd2024.spell.countered",
        }:
            return
        if event_type == "dnd2024.encounter.readiness.changed":
            request = state.get("encounter_request")
            if not isinstance(request, dict) or request.get("status") != "pending":
                raise EventBatchError("encounter readiness has no pending request")
            player_id = str(event["player_id"])
            ready_ids = {
                str(item) for item in request.get("ready_player_ids") or [] if str(item)
            }
            if event.get("ready"):
                ready_ids.add(player_id)
            else:
                ready_ids.discard(player_id)
            request["ready_player_ids"] = sorted(ready_ids)
            return
        if event_type == "dnd2024.combat.player_joined":
            actor_id = str(event["actor_id"])
            if not actor_id.startswith("player:"):
                raise EventBatchError("joined combat actor must be a player")
            if actor_id in combat.get("initiative", []):
                raise EventBatchError("player is already in combat initiative")
            raw_id = actor_id.removeprefix("player:")
            if raw_id not in snapshot["characters"]:
                raise EventBatchError("joined combat player does not exist")
            order = combat.setdefault("initiative", [])
            insert_index = max(0, min(len(order), int(event.get("insert_index", len(order)))))
            order.insert(insert_index, actor_id)
            combat.setdefault("initiative_rolls", []).append({
                "actor_id": actor_id,
                "roll": int(event.get("roll", 0) or 0),
                "modifier": int(event.get("modifier", 0) or 0),
                "total": int(event.get("total", 0) or 0),
                "kind": "player",
                "joined_late": True,
            })
            combat.setdefault("positions", {})[actor_id] = int(event.get("position", 0) or 0)
            combat.setdefault("reactions", {})[actor_id] = 1
            # slot 才是回合表：只往 initiative 里插人会让新加入者永远轮不到。
            # 插入点在"当前回合之后"，当前 slot 的下标因此保持不变，已结算的
            # 先攻顺序也不会被重排。
            slots = combat.get("turn_slots")
            if isinstance(slots, list):
                index = max(0, min(len(slots), int(combat.get("turn_index", 0) or 0) + 1))
                slots.insert(index, {
                    "slot_id": f"{actor_id}#1", "actor_id": actor_id,
                    "initiative_total": int(event.get("total", 0) or 0),
                    "kind": "normal",
                })
            return
        if event_type == "dnd2024.character.revived":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind != "player" or raw_id not in snapshot["characters"]:
                raise EventBatchError("revived actor must be an existing player")
            character = snapshot["characters"][raw_id]
            resources = character.setdefault("resources", {})
            max_hp = int(resources.get("max_hp", 0) or 0)
            hp = max(1, int(event.get("hp", 1) or 1))
            if max_hp > 0:
                hp = min(max_hp, hp)
            resources["hp"] = hp
            conditions = character.setdefault("conditions", {})
            for condition in ("dead", "unconscious", "stable", "death_saves"):
                conditions.pop(condition, None)
            return
        if event_type == "dnd2024.combat.started":
            state["combat"] = {
                "status": "active", "round": event["round"], "turn_index": 0,
                "initiative": deepcopy(event["initiative"]),
                # 首轮 turn 顺序的权威形态：一个角色可以有两个 slot（Thief's
                # Reflexes），``initiative`` 仍保持去重，因为其他读者只问
                # "谁在场"。旧事件没有这个字段时退回单 slot 语义。
                "turn_slots": deepcopy(event.get("turn_slots") or []),
                "initiative_rolls": deepcopy(event["initiative_rolls"]),
                "enemies": deepcopy(event["enemies"]),
                "positions": deepcopy(event["positions"]),
                "economy": deepcopy(event["economy"]),
                "pending_decisions": [], "position_mode": event["position_mode"],
                "reactions": deepcopy(event["reactions"]),
                "feature_usage": {},
                # Reckless 的"本回合第一次攻击"计数与 Studied Attacks 的目标
                # 标记都只活在一场战斗里：新战斗从零开始，不继承上一场。
                "attack_rolls": {},
                "attack_marks": {},
                "encounter_instance_id": str(event.get("encounter_instance_id") or ""),
                "encounter_preset_id": str(event.get("encounter_preset_id") or ""),
                "origin_step_id": str(event.get("origin_step_id") or ""),
                "mode": str(event.get("mode") or "sandbox"),
                "adventure_binding": deepcopy(event.get("adventure_binding")),
            }
            return
        if event_type == "dnd2024.reaction.spent":
            actor_id = str(event["actor_id"])
            reactions = combat.setdefault("reactions", {})
            if int(reactions.get(actor_id, 0) or 0) < 1:
                raise EventBatchError("reaction is already spent")
            reactions[actor_id] = 0
            return
        if event_type == "dnd2024.heroic_inspiration.changed":
            # Heroic Inspiration 是角色级布尔状态：持有则"再获得即丢失"，
            # 因此重复授予写成幂等，不叠层也不报错。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind != "player" or raw_id not in snapshot["characters"]:
                raise EventBatchError("only an existing player can hold Heroic Inspiration")
            if event.get("granted") is not True:
                raise EventBatchError("heroic inspiration events only grant")
            snapshot["characters"][raw_id]["heroic_inspiration"] = {
                "granted": True, "source": str(event.get("feature_id") or ""),
            }
            return
        if event_type == "dnd2024.combat.ended":
            combat["status"] = "ended"
            combat["outcome"] = str(event.get("reason") or "ended")
            combat["economy"] = {}
            # 狂暴靠每回合攻击或附赠动作续期；按回合计时的效果也只在回合
            # 边界到期。战斗结束后没有回合，它们随之结束；``duration=combat``
            # （如 Hide 的 Invisible）同样以本场战斗为界，不能带进下一场战斗。
            party = [*snapshot["characters"].values()]
            companions = (state.get("party") or {}).get("companions") or {}
            for companion in companions.values():
                canonical = companion.get("ruleset_character") if isinstance(companion, dict) else None
                if isinstance(canonical, dict):
                    party.append(canonical)
            for character in party:
                conditions = character.get("conditions") or {}
                for condition_id in [
                    key for key, value in conditions.items()
                    # 持久狂怒是"10 分钟"而不是"到战斗结束"：结束一场战斗
                    # 不在官方提前结束条件里，仍在期限内的它必须留下来。
                    if not (
                        isinstance(value, dict)
                        and value.get("duration") == PERSISTENT_RAGE_DURATION
                    )
                    and (
                        key == "rage" or (
                            isinstance(value, dict) and (
                                value.get("duration") in _TURN_SCOPED_DURATIONS
                                or value.get("duration") == "combat"
                            )
                        )
                    )
                ]:
                    conditions.pop(condition_id, None)
            encounter_id = str(combat.get("encounter_instance_id") or "")
            if encounter_id:
                history = state.setdefault("combat_history", [])
                if not any(
                    isinstance(item, dict) and item.get("encounter_instance_id") == encounter_id
                    for item in history
                ):
                    history.append({
                        "encounter_instance_id": encounter_id,
                        "encounter_preset_id": str(combat.get("encounter_preset_id") or ""),
                        "origin_step_id": str(combat.get("origin_step_id") or ""),
                        "outcome": combat["outcome"],
                    })
                    del history[:-64]
            return
        if event_type == "dnd2024.action.spent":
            resource = str(event["resource"])
            current = int(combat["economy"].get(resource, 0) or 0)
            amount = int(event.get("amount", 1) or 1)
            if current < amount:
                raise EventBatchError(f"combat resource is already spent: {resource}")
            combat["economy"][resource] = current - amount
            return
        if event_type in _ITEM_EVENT_FIELDS:
            unknown = sorted(set(event) - _ITEM_EVENT_FIELDS[event_type])
            if unknown:
                raise EventBatchError(f"unknown {event_type} field: {unknown[0]}")
            if event_type == "dnd2024.item.destroyed":
                kind, raw_id = _actor_kind(str(event["actor_id"]))
                if kind not in {"player", "companion"}:
                    raise EventBatchError("only player/companion items can be destroyed")
                character = (
                    snapshot["characters"][raw_id]
                    if kind == "player"
                    else self._companion_character(snapshot, raw_id)
                )
                try:
                    destroyed_ref = canonical_item_ref(event["item_ref"])
                except AttunementError as exc:
                    raise EventBatchError(str(exc)) from None
                # 只按 ref 移除：同名但身份不同的另一件物品不受影响。
                equipment = character.get("equipment")
                if isinstance(equipment, dict):
                    refs = equipment.get("item_refs")
                    if isinstance(refs, list):
                        equipment["item_refs"] = [
                            ref for ref in refs if ref != destroyed_ref
                        ]
                    item_state = equipment.get("item_state")
                    if isinstance(item_state, dict):
                        item_state.pop(destroyed_ref, None)
                attunement = character.get("attunement")
                if isinstance(attunement, dict):
                    bound = attunement.get("refs")
                    if isinstance(bound, list):
                        attunement["refs"] = [
                            ref for ref in bound if ref != destroyed_ref
                        ]
                return
            if event_type != "dnd2024.item.charge_spent":
                # 数量扣减由 apply_batch 通过 live backpack 权威接口落账；
                # reduced canonical 只记录事件事实。
                return
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion items hold charges")
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            try:
                item_ref = canonical_item_ref(event["item_ref"])
            except AttunementError as exc:
                raise EventBatchError(str(exc)) from None
            item = self.bundle.get("item", item_ref.removeprefix("item:"))
            try:
                maximum = validate_charges_declaration(item)
            except ChargesError as exc:
                raise EventBatchError(str(exc)) from None
            if maximum is None:
                raise EventBatchError("item has no charges")
            state = (character.get("equipment") or {}).get("item_state")
            charges = state.get(item_ref) if isinstance(state, dict) else None
            entry = charges.get("charges") if isinstance(charges, dict) else None
            current = entry.get("current") if isinstance(entry, dict) else None
            amount = event["amount"]
            try:
                new_current = spend_charges(current, amount, maximum)
            except ChargesError as exc:
                raise EventBatchError(str(exc)) from None
            entry["current"] = new_current
            return
        if event_type == "dnd2024.economy.granted":
            economy = combat["economy"]
            if str(event["actor_id"]) != str(economy.get("actor_id") or ""):
                raise EventBatchError("economy grants belong to the current actor")
            grants = event.get("grants") or {}
            flags = event.get("flags") or {}
            if not isinstance(grants, dict) or not isinstance(flags, dict):
                raise EventBatchError("economy grant payload is invalid")
            for key, amount in grants.items():
                if str(key) not in _GRANTABLE_ECONOMY:
                    raise EventBatchError(f"economy grant key is unknown: {key}")
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    raise EventBatchError("economy grant amount is invalid")
                economy[str(key)] = int(economy.get(str(key), 0) or 0) + amount
            for key, value in flags.items():
                if str(key) not in _GRANTABLE_FLAGS:
                    raise EventBatchError(f"economy grant flag is unknown: {key}")
                economy[str(key)] = value
            return
        if event_type == "dnd2024.turn_window.closed":
            # 窗口是"此刻允许支付"的记号，关掉它是幂等的：重复关闭、或在
            # 已经换了行动者的经济上关闭，都不能顺手改别的字段。
            economy = combat.get("economy") or {}
            window = economy.get("turn_start_window")
            if isinstance(window, dict) and str(window.get("actor_id") or "") == str(
                event.get("actor_id") or ""
            ):
                economy.pop("turn_start_window", None)
            return
        if event_type == "dnd2024.feature.used":
            # 「每回合一次」按真实攻击者 + 具体回合（round + turn_index）记账。
            # 借机攻击发生在别人的回合，因此不能读当前行动者的 economy。
            actor_id = str(event["actor_id"])
            round_index = int(event["round"])
            turn_index = int(event["turn_index"])
            feature_id = str(event["feature_id"])
            usage = combat.setdefault("feature_usage", {})
            if not isinstance(usage, dict):
                raise EventBatchError("feature usage ledger is invalid")
            record = usage.get(actor_id)
            if (
                not isinstance(record, dict)
                or int(record.get("round", -1)) != round_index
                or int(record.get("turn_index", -1)) != turn_index
            ):
                usage[actor_id] = {
                    "round": round_index, "turn_index": turn_index,
                    "feature_ids": [feature_id],
                }
                return
            feature_ids = record.setdefault("feature_ids", [])
            if feature_id in feature_ids:
                raise EventBatchError(f"feature is already used this turn: {feature_id}")
            feature_ids.append(feature_id)
            return
        if event_type == "dnd2024.feature_usage.changed":
            # 长休作用域计数器：before_count 是批次生成时的权威快照，不匹配
            # 说明批次过期或重复消费，宁可炸掉也不覆盖合法计数。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion feature usage is canonical")
            character = self._canonical_of(snapshot, kind, raw_id)
            owner = str(event["owner"])
            feature_id = str(event["feature_id"])
            scope = str(event["scope"])
            before = int(event["before_count"])
            after = int(event["after_count"])
            try:
                current = feature_usage_count(character, owner, feature_id, scope)
                if current != before:
                    raise EventBatchError(
                        f"feature usage for {owner}.{feature_id} moved: "
                        f"expected {before}, found {current}"
                    )
                if after != current + 1:
                    raise EventBatchError("feature usage must advance by exactly one")
                set_feature_usage_count(character, owner, feature_id, scope, after)
            except ClassFeatureError as exc:
                raise EventBatchError(str(exc)) from exc
            return
        if event_type == "dnd2024.feature_state.set":
            # 角色特性状态（Relentless Rage 的下一 DC）不是资源计数器，也不
            # 属于任何条件：它写在既有 ``features`` 结构里，键集合是闭集，
            # 未知字段一律拒绝，避免变成通用 character 写入口。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion feature state is canonical")
            character = self._canonical_of(snapshot, kind, raw_id)
            feature_id = str(event["feature_id"])
            allowed = FEATURE_STATE_KEYS.get(feature_id)
            values = event.get("values")
            if allowed is None or not isinstance(values, dict) or not values:
                raise EventBatchError("feature state is not writable")
            unknown = sorted(set(values) - allowed)
            if unknown:
                raise EventBatchError(f"feature state field is not writable: {unknown[0]}")
            block = character.get("features")
            if not isinstance(block, dict):
                raise EventBatchError("character feature state is missing")
            current = block.get(feature_id)
            current = current if isinstance(current, dict) else {}
            block[feature_id] = {**deepcopy(current), **deepcopy(values)}
            return
        if event_type == "dnd2024.class_resource.spent":
            # 职业资源永远写在既有的 canonical resources.class 结构里，
            # 不新增第二套资源表，也不允许扣成负数。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion class resources are canonical")
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            try:
                apply_free_cast_cost(character, event)
            except ValueError as exc:
                raise EventBatchError(str(exc)) from exc
            return
        if event_type == "dnd2024.bardic.granted":
            # 激励骰实例是唯一的状态记录：授予时间与到期分钟由 resolution
            # 从权威世界时钟算出写进事件，reducer 不读时钟。同一持有者的
            # 有效骰至多一个，重复授予 fail closed；过期实例在这里顺带清理
            # （读取/视图按同一谓词隐藏，无需后台任务）。
            granted_at = int(event["granted_at_minute"])
            instances = [
                item for item in combat.get("bardic_instances") or []
                if isinstance(item, dict)
                and int(item.get("expires_at_minute", 0) or 0) > granted_at
                and str(item.get("status") or "") == "active"
            ]
            holder_id = str(event["holder_id"])
            if any(str(item.get("holder_id") or "") == holder_id for item in instances):
                raise EventBatchError("the holder already has a Bardic Inspiration die")
            instances.append({
                "die_instance_id": str(event["die_instance_id"]),
                "holder_id": holder_id,
                "source_actor_id": str(event["actor_id"]),
                "die_sides": int(event["die_sides"]),
                "granted_at_minute": granted_at,
                "expires_at_minute": int(event["expires_at_minute"]),
                "status": "active",
            })
            combat["bardic_instances"] = instances
            return
        if event_type == "dnd2024.bardic.consumed":
            # 掷过的骰实例留在列表里留痕（status "rolled"），但读取谓词只认
            # "active"，所以它立即不可再用，也不会阻止新的授予。
            holder_id = str(event["holder_id"])
            combat["bardic_instances"] = [
                item if not (
                    isinstance(item, dict)
                    and str(item.get("holder_id") or "") == holder_id
                    and str(item.get("status") or "") == "active"
                ) else {**item, "status": "rolled"}
                for item in combat.get("bardic_instances") or []
            ]
            return
        if event_type == "dnd2024.class_resource.refunded":
            # 失败退款（Peerless Skill）：同一批次先扣后退，上限仍是目录
            # 声明的最大值，不会退成超过上限。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion class resources are canonical")
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            class_state = character.setdefault("resources", {}).setdefault("class", {})
            state = class_state.get(str(event["resource_id"]))
            if not isinstance(state, dict):
                raise EventBatchError("class resource is not initialized")
            amount = int(event.get("amount", 1) or 1)
            state["current"] = min(int(state.get("maximum", 0) or 0), int(state.get("current", 0) or 0) + amount)
            return
        if event_type == "dnd2024.class_resource.refilled":
            # Superior Inspiration: "regain expended uses until you have two".
            # The target is a floor, not a gift: never raise above the
            # catalogue maximum (a +1 Charisma bard still caps at one use).
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion class resources are canonical")
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            class_state = character.setdefault("resources", {}).setdefault("class", {})
            state = class_state.get(str(event["resource_id"]))
            if not isinstance(state, dict):
                raise EventBatchError("class resource is not initialized")
            maximum = int(state.get("maximum", 0) or 0)
            target = min(int(event["target"]), maximum)
            state["current"] = max(int(state.get("current", 0) or 0), target)
            return
        if event_type == "dnd2024.attack.spent":
            economy = combat["economy"]
            if int(economy.get("attacks_remaining", 0) or 0) > 0:
                economy["attacks_remaining"] -= 1
            else:
                # 支付选择由 resolution 的单一入口决定：普通 action 先用完，
                # 只有它耗尽时 Action Surge 的 surge_action 才支付攻击。
                resource = str(event.get("resource") or "action")
                if int(economy.get(resource, 0) or 0) < 1:
                    raise EventBatchError("Attack action is already spent")
                economy[resource] -= 1
                economy["attacks_remaining"] = max(0, int(event["attacks_per_action"]) - 1)
            return
        if event_type == "dnd2024.attack_roll.recorded":
            # Reckless Attack 的窗口是"本回合第一次攻击检定"，所以要按
            # actor + round + turn_index 记一个计数。跨回合不做显式清零：
            # 回合标识变了就等于归零，和 feature_usage 的记账方式一致。
            actor_id = str(event["actor_id"])
            round_index = int(event["round"])
            turn_index = int(event["turn_index"])
            ledger = combat.setdefault("attack_rolls", {})
            if not isinstance(ledger, dict):
                raise EventBatchError("attack roll ledger is invalid")
            record = ledger.get(actor_id)
            if (
                not isinstance(record, dict)
                or int(record.get("round", -1)) != round_index
                or int(record.get("turn_index", -1)) != turn_index
            ):
                ledger[actor_id] = {
                    "round": round_index, "turn_index": turn_index, "count": 1,
                }
                return
            record["count"] = int(record.get("count", 0) or 0) + 1
            return
        if event_type == "dnd2024.attack_mark.set":
            # 目标绑定的攻击状态（Studied Attacks）：按 actor -> kind -> target
            # 存放，同一 actor 对同一目标只有一份，重复建立就地覆盖而不是叠加。
            actor_id = str(event["actor_id"])
            kind = str(event["kind"])
            target_id = str(event["target_id"])
            marks = combat.setdefault("attack_marks", {})
            if not isinstance(marks, dict):
                raise EventBatchError("attack marks are invalid")
            per_kind = marks.setdefault(kind, {})
            if not isinstance(per_kind, dict):
                raise EventBatchError("attack marks are invalid")
            per_actor = per_kind.setdefault(actor_id, {})
            if not isinstance(per_actor, dict):
                raise EventBatchError("attack marks are invalid")
            per_actor[target_id] = {
                "turns_remaining": int(event.get("turns_remaining", 1) or 0),
                "expiry": str(event.get("expiry") or ""),
                "round": int(combat.get("round", 0)),
                "turn_index": int(combat.get("turn_index", 0)),
            }
            return
        if event_type == "dnd2024.attack_mark.cleared":
            actor_id = str(event["actor_id"])
            kind = str(event["kind"])
            target_id = str(event["target_id"])
            marks = combat.get("attack_marks")
            per_kind = marks.get(kind) if isinstance(marks, dict) else None
            per_actor = per_kind.get(actor_id) if isinstance(per_kind, dict) else None
            if isinstance(per_actor, dict):
                per_actor.pop(target_id, None)
            return
        if event_type == "dnd2024.movement.granted":
            amount = int(event["amount"])
            combat["economy"]["movement"] = int(combat["economy"].get("movement", 0)) + amount
            if str(event.get("reason") or "") == "critical_hit":
                # 暴击后移动单独记账：它既进池（玩家仍要提交 move），又带着
                # 一份"本次移动免借机攻击"的额度。下一次移动消费掉它，
                # 不会延续到后续任何移动。
                combat["economy"]["crit_move"] = amount
            elif str(event.get("reason") or "") == "brutal_forceful":
                # Forceful Blow 的跟步额度同样只覆盖一次移动，而且多一个
                # 方向约束：只能朝被推开的目标走。方向在掷骰侧判定，这里只
                # 记录额度与目标。
                combat["economy"]["brutal_move"] = {
                    "amount": amount,
                    "target_id": str(event.get("target_id") or ""),
                }
            return
        if event_type == "dnd2024.position.changed":
            actor_id = str(event["actor_id"])
            combat["positions"][actor_id] = int(combat["positions"].get(actor_id, 0)) + int(
                event["distance"]
            )
            cost = int(event.get("movement_cost", 0) or 0)
            if cost:
                remaining = int(combat["economy"].get("movement", 0) or 0)
                if remaining < cost:
                    raise EventBatchError("movement resource is already spent")
                combat["economy"]["movement"] = remaining - cost
            if event.get("crit_move"):
                # 额度只覆盖它自己这一次移动；消费后即失效，其他移动要按
                # 常规规则判定借机攻击。
                combat["economy"]["crit_move"] = 0
            if event.get("brutal_move"):
                combat["economy"].pop("brutal_move", None)
            return
        if event_type == "resource.changed":
            if str(event.get("resource") or "hp") == "temp_hp":
                self._apply_temp_hp_change(snapshot, event)
            else:
                self._apply_hp_change(snapshot, event)
            return
        if event_type == "condition.applied":
            conditions = self._conditions(snapshot, combat, str(event["target_id"]))
            conditions[str(event["condition"])] = {
                key: deepcopy(value) for key, value in event.items()
                if key not in {"type", "target_id", "condition"}
            }
            if event["condition"] == "stable":
                kind, raw = _actor_kind(str(event["target_id"]))
                if kind == "player":
                    snapshot["characters"][raw].setdefault("conditions", {})[
                        "death_saves"
                    ] = {"successes": 3, "failures": 0}
            return
        if event_type == "condition.removed":
            self._conditions(snapshot, combat, str(event["target_id"])).pop(
                str(event["condition"]), None
            )
            return
        if event_type == "dnd2024.condition.sustained":
            conditions = self._conditions(snapshot, combat, str(event["target_id"]))
            condition_id = str(event["condition"])
            if condition_id not in conditions:
                raise EventBatchError("sustained condition is not active")
            conditions[condition_id]["sustained_this_turn"] = True
            return
        if event_type == "dnd2024.spell.slot_spent":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion spell slots are canonical")
            slots = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )["spellcasting"]["class"]["slots_current"]
            level = str(event["slot_level"])
            if int(slots.get(level, 0) or 0) < 1:
                raise EventBatchError("spell slot is already spent")
            slots[level] -= 1
            if not event.get("exchange"):
                # Font 的"槽换次数"不是施法：不占用本回合的施法标记。
                combat["economy"]["slot_spell_cast"] = True
            return
        if event_type == "dnd2024.spell.font_slot_spent":
            # 临时槽是普通槽表之外的一栏，所以它有自己的事件：一个槽位
            # 事件只能扣一处，否则同一笔施法会同时吃临时槽和普通槽。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion spell slots are canonical")
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            block = character["spellcasting"]["class"]
            stored = block.get("font_slots")
            if not isinstance(stored, dict):
                raise EventBatchError("font slots are not initialized")
            level = str(event["slot_level"])
            remaining = stored.get(level)
            if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 1:
                raise EventBatchError("temporary spell slot is already spent")
            if remaining == 1:
                # 用完即删：留下的 0 会被读成"还有一个空槽"，也再没人需要它。
                stored.pop(level)
            else:
                stored[level] = remaining - 1
            if not event.get("exchange"):
                combat["economy"]["slot_spell_cast"] = True
            return
        if event_type == "dnd2024.spell.font_slot_created":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion spell slots are canonical")
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            block = character["spellcasting"]["class"]
            level = int(event["slot_level"])
            if not 1 <= level <= MAX_CREATED_SLOT_LEVEL:
                # 伪造的高环临时槽在这里就死掉，不等它去骗过施法校验。
                raise EventBatchError("temporary spell slot level is illegal")
            stored = block.get("font_slots")
            if not isinstance(stored, dict):
                stored = {}
                block["font_slots"] = stored
            key = str(level)
            stored[key] = int(stored.get(key, 0) or 0) + 1
            return
        if event_type == "dnd2024.feat_spell.free_cast_spent":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion feat spells are canonical")
            try:
                apply_free_cast_cost(self._canonical_of(snapshot, kind, raw_id), event)
            except ValueError as exc:
                raise EventBatchError(str(exc)) from exc
            return
        if event_type == "dnd2024.free_cast.spent":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                raise EventBatchError("only player/companion free casts are canonical")
            try:
                apply_free_cast_cost(self._canonical_of(snapshot, kind, raw_id), event)
            except ValueError as exc:
                raise EventBatchError(str(exc)) from exc
            return
        if event_type == "dnd2024.concentration.started":
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind in {"player", "companion"}:
                instance = {
                    "spell_ref": event["spell_ref"],
                    "target_ids": deepcopy(event["target_ids"]),
                }
                # 只有把"唯一当前目标"写进实例，同一施法者"同时只有一个标记"
                # 才是数据形状：任何第二个标记都必须先覆盖这一条。
                if event.get("mark_target_id"):
                    instance["mark_target_id"] = str(event["mark_target_id"])
                if event.get("mark_expires_hours") is not None:
                    instance["mark_expires_hours"] = int(event["mark_expires_hours"])
                concentration_holder(self._canonical_of(snapshot, kind, raw_id))[
                    "concentration"
                ] = instance
            return
        if event_type == "dnd2024.concentration.retargeted":
            # 转移只改目标：spell_ref 与剩余时长都留在原实例上，所以它不是
            # 一次新的专注，也不会被写成第二个实例。
            kind, raw_id = _actor_kind(str(event["actor_id"]))
            if kind not in {"player", "companion"}:
                return
            holder = concentration_holder(self._canonical_of(snapshot, kind, raw_id))
            instance = holder.get("concentration")
            if not isinstance(instance, dict):
                raise EventBatchError("no concentration instance to retarget")
            if str(instance.get("spell_ref") or "") != str(event["spell_ref"]):
                raise EventBatchError("the concentration instance holds another spell")
            if not instance.get("mark_target_id"):
                raise EventBatchError("the concentration instance is not a mark")
            instance["mark_target_id"] = str(event["target_id"])
            # 标记法术的 target_ids 与 mark_target_id 是同一件事的两种写法；
            # 一个写入点同时更新两者，才不会出现"标已转移、目标列表还停在
            # 上一只生物"的实例。
            instance["target_ids"] = [str(event["target_id"])]
            return
        if event_type == "dnd2024.concentration.ended":
            owner = str(event["actor_id"])
            kind, raw_id = _actor_kind(owner)
            if kind in {"player", "companion"}:
                concentration_holder(self._canonical_of(snapshot, kind, raw_id))[
                    "concentration"
                ] = None
            self._remove_concentration_conditions(snapshot, combat, owner)
            return
        if event_type == "dnd2024.turn.advanced":
            self._expire_conditions(
                snapshot, combat, str(event["actor_id"]),
                str(event["previous_actor_id"]),
            )
            combat["round"] = int(event["round"])
            combat["turn_index"] = int(event["turn_index"])
            combat["economy"] = deepcopy(event["economy"])
            combat.setdefault("reactions", {})[str(event["actor_id"])] = 1
            return
        if event_type == "dnd2024.death_save.resolved":
            _kind, raw_id = _actor_kind(str(event["actor_id"]))
            character = snapshot["characters"][raw_id]
            character.setdefault("conditions", {})["death_saves"] = {
                "successes": int(event["successes"]), "failures": int(event["failures"]),
            }
            if event.get("hp"):
                character["resources"]["hp"] = int(event["hp"])
                character["conditions"].pop("unconscious", None)
            if event.get("stable"):
                character["conditions"]["stable"] = {"duration": "until_healed"}
            if event.get("dead"):
                character["conditions"]["dead"] = {"source": "death_saves"}
                character["conditions"].pop("unconscious", None)
                # 死亡终止全部同调；复活不会把绑定还回来。
                clear_attunement_on_death(character)
            return
        if event_type == "dnd2024.decision.pending":
            combat.setdefault("pending_decisions", []).append({
                key: deepcopy(value) for key, value in event.items() if key != "type"
            })
            return
        if event_type == "dnd2024.decision.cleared":
            combat["pending_decisions"] = [
                item for item in combat.get("pending_decisions", [])
                if item.get("decision_id") != event["decision_id"]
            ]
            return
        raise EventBatchError(f"unsupported event type: {event_type}")

    def _companion_character(self, snapshot: dict[str, Any], companion_id: str) -> dict[str, Any]:
        """从 detached snapshot 读取 companion 的权威 canonical 角色。"""
        companions = (
            snapshot["ruleset_state"].setdefault("party", {}).setdefault("companions", {})
        )
        companion = companions.get(companion_id)
        if not isinstance(companion, dict) or not isinstance(
            companion.get("ruleset_character"), dict,
        ):
            raise EventBatchError("companion canonical character is missing")
        return companion["ruleset_character"]

    def _canonical_of(self, snapshot: dict[str, Any], kind: str, raw_id: str) -> dict[str, Any]:
        if kind == "player":
            return snapshot["characters"][raw_id]
        return self._companion_character(snapshot, raw_id)

    def _apply_temp_hp_change(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        """授予临时生命值：玩家/同伴走 keep-max，敌人 fail closed。

        临时生命值既不是治疗也不是生命值本身：不碰 unconscious / stable /
        death_saves / hp，也不受 max_hp clamp。规则书的“保留原值或新值”简化为
        保留较高者（确定性引擎下较低值永远是非理性选择）。
        """
        target_id = str(event["target_id"])
        kind, raw_id = _actor_kind(target_id)
        if kind not in {"player", "companion"}:
            raise EventBatchError("temp HP target actor is invalid")
        character = (
            snapshot["characters"][raw_id]
            if kind == "player"
            else self._companion_character(snapshot, raw_id)
        )
        resources = character["resources"]
        resources["temp_hp"] = max(
            int(resources.get("temp_hp", 0) or 0), int(event["amount"])
        )

    def _apply_hp_change(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        target_id = str(event["target_id"])
        kind, raw_id = _actor_kind(target_id)
        delta = int(event["delta"])
        # 临时 HP 会在下面被吸收，从而把 delta 改成 0；死亡豁免必须依据
        # “0 HP 角色是否真的受到伤害”的原始事实，而不是吸收后的剩余伤害。
        raw_damage = delta
        if kind in {"player", "companion"}:
            character = (
                snapshot["characters"][raw_id]
                if kind == "player"
                else self._companion_character(snapshot, raw_id)
            )
            resources = character["resources"]
            before = int(resources.get("hp", 0) or 0)
            maximum = int(resources.get("max_hp", 0) or 0)
            # 优先失去临时生命值：伤害先吸收，余下的才扣 hp；事件的 amount
            # 仍是打出的伤害，吸收只反映在资源状态上。
            temp = int(resources.get("temp_hp", 0) or 0)
            if delta < 0 and temp > 0 and not event.get("healing"):
                absorbed = min(temp, -delta)
                resources["temp_hp"] = temp - absorbed
                delta += absorbed
            next_hp = max(0, min(maximum, before + delta))
            relentless = event.get("relentless")
            if (
                isinstance(relentless, dict)
                and next_hp <= 0
                and before > 0
                and not event.get("relentless_deferred")
            ):
                # 归零前拦截：临时 HP 已经吸收，但 HP 本身不动、昏迷与结束
                # 狂暴都还没发生。伤害随决策挂起，等本人决定是否用这次特性。
                self._open_relentless_rage(snapshot, target_id, before, event, relentless)
                return
            resources["hp"] = next_hp
            conditions = character.setdefault("conditions", {})
            self._end_damage_conditions(conditions, before, next_hp, event)
            if resources["hp"] <= 0:
                conditions["unconscious"] = {"source": "zero_hp"}
                # 昏迷即失能：狂暴立即结束。
                conditions.pop("rage", None)
                death = conditions.setdefault("death_saves", {"successes": 0, "failures": 0})
                if before == 0 and raw_damage < 0:
                    death["failures"] = min(
                        3, int(death.get("failures", 0)) + (2 if event.get("critical") else 1)
                    )
                    if death["failures"] >= 3:
                        conditions["dead"] = {"source": "damage_at_zero_hp"}
                        conditions.pop("unconscious", None)
                        clear_attunement_on_death(character)
            elif delta > 0:
                conditions.pop("unconscious", None)
                conditions.pop("stable", None)
                conditions["death_saves"] = {"successes": 0, "failures": 0}
        elif kind == "enemy":
            enemy = snapshot["ruleset_state"]["combat"]["enemies"][raw_id]
            before_hp = int(enemy["hp"])
            enemy["hp"] = max(0, min(int(enemy["max_hp"]), before_hp + delta))
            self._end_damage_conditions(
                enemy.setdefault("conditions", {}), before_hp, int(enemy["hp"]), event,
            )
            if enemy["hp"] <= 0:
                enemy.setdefault("conditions", {})["defeated"] = {"source": "zero_hp"}
        else:
            raise EventBatchError("HP target actor is invalid")

    @staticmethod
    def _end_damage_conditions(
        conditions: dict[str, Any], before_hp: int, after_hp: int,
        event: dict[str, Any],
    ) -> None:
        """Remove the conditions whose own text says damage ends them.

        Turn Undead is the first such effect: "this effect ends early on the
        creature if it takes any damage".  The flag lives on the condition, so
        a later effect with the same wording needs no branch here.

        ``turn_effect_preserved`` is how one *specific* damage source opts out:
        Sear Undead damages the turned Undead without ending the turn, and that
        sentence belongs to the damage event, not to this sweep.  Protection
        from Pose is not required either -- only a *reduction in Hit Points*
        ends an effect, so a fully resisted hit leaves it in place.
        """

        if after_hp >= before_hp or event.get("turn_effect_preserved"):
            return
        for condition_id, meta in list(conditions.items()):
            if isinstance(meta, dict) and meta.get("ends_on_damage"):
                conditions.pop(condition_id, None)

    def _open_relentless_rage(
        self, snapshot: dict[str, Any], target_id: str, before: int,
        event: dict[str, Any], marker: dict[str, Any],
    ) -> None:
        """Hold the damage and offer the zero-HP survival save.

        ``decision_id`` is derived from round/turn/HP so a replay that reaches
        the same damage produces the same window instead of a second one.
        """

        combat = snapshot["ruleset_state"]["combat"]
        decision_id = (
            f"relentless-rage:{target_id}:{combat.get('round', 0)}:"
            f"{combat.get('turn_index', 0)}:{before}"
        )
        pending = combat.setdefault("pending_decisions", [])
        if any(item.get("decision_id") == decision_id for item in pending):
            return
        pending.append({
            "decision_id": decision_id,
            "kind": RELENTLESS_RAGE_DECISION,
            "actor_id": target_id,
            "assigned_to": str(marker.get("assigned_to") or ""),
            "options": ["use", "decline"],
            "dc": int(marker["dc"]),
            "dc_step": int(marker.get("dc_step", 5) or 5),
            "hp_amount": int(marker.get("hp_amount", 0) or 0),
            "save_ability": str(marker.get("save_ability") or "con"),
            "damage_event": {**deepcopy(event), "relentless_deferred": True},
        })
