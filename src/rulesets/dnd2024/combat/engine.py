"""Authoritative, versioned D&D 2024 combat intents and EventBatch reducer.

The engine keeps the intent surface (validate / resolve / apply_batch), the
projection entry point, encounter presets, and the small turn, economy, and
position helpers.  Three of its own rule surfaces live in sibling mixins:

* ``combat/availability.py`` -- what an actor may do next, and what an
  automatic actor picks;
* ``combat/conditions.py`` -- condition projection, immunity, and expiry;
* ``combat/environment.py`` -- cover, obscurement, and aura projection.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from random import SystemRandom
from typing import Any

from src.engine.modules import ruleset_runtime
from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.attunement import attuned_refs
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
    merge_live_character_projection,
)
from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog
from src.rulesets.dnd2024.content.encounter import expand_encounter_enemies
from src.rulesets.dnd2024.features.combat import spell_slot_count
from src.rulesets.dnd2024.features.models import SpellSlotCost, TURN_START_TIMING
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.resources import resource_state
from src.rulesets.dnd2024.play.contracts import EncounterAccess
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
from src.rulesets.events import EventBatchError, apply_event_batch, stable_batch_id


from .availability import CombatAvailabilityMixin
from .conditions import CombatConditionsMixin
from .environment import CombatEnvironmentMixin
from .check_modes import condition_check_bonus_rolls, condition_flags, effective_speed
from .check_windows import CombatCheckWindowsMixin
from .divine_actions import CombatDivineActionsMixin
from . import invocation_effects
from .stealth_actions import CombatStealthActionsMixin
from .martial_actions import CombatMartialActionsMixin
from .support_actions import CombatSupportActionsMixin
from .defensive_tactics import CombatDefensiveTacticsMixin
from .ongoing_effects import CombatOngoingEffectsMixin
from .primitives import (
    TURN_WINDOW_CLOSED_EVENT,
    CombatIntentError,
    actor_kind as _actor_kind,
    companion_actor as _companion_actor,
    canonical as _canonical,
    current_turn_slot as _current_turn_slot,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)
from .reducer import CombatReducerMixin
from .resolution import CombatResolutionMixin
from .spell_resolution import CombatSpellResolutionMixin
from .hit_resolution import CombatHitResolutionMixin
from .validation import CombatValidationMixin
from .view import CombatViewMixin
from .triggered_spells import CombatTriggeredSpellsMixin, hit_spell_decision
from .reaction_spells import CombatReactionSpellsMixin
from .visibility import public_pending_decisions


def _death_attunement_events(
    before_characters: dict[str, dict[str, Any]],
    after_characters: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Unbinding audit events and the characters that just died."""

    events: list[dict[str, Any]] = []
    dead_uids: set[str] = set()
    for uid, canonical in after_characters.items():
        previous = before_characters.get(uid) or {}
        newly_dead = (
            "dead" in (canonical.get("conditions") or {})
            and "dead" not in (previous.get("conditions") or {})
        )
        if not newly_dead:
            continue
        dead_uids.add(uid)
        lost = sorted(attuned_refs(previous) - attuned_refs(canonical))
        events.extend(
            {"type": "unattune_item", "item_ref": ref, "reason": "death"}
            for ref in lost
        )
    return events, dead_uids


@dataclass(slots=True)
class Dnd2024CombatEngine(
    # The three mixins below carry the engine's own availability, condition, and
    # environment rules; they must stay ahead of the sibling mixins so that
    # ``super(CombatAvailabilityMixin, self)`` still reaches CombatViewMixin.
    CombatAvailabilityMixin, CombatConditionsMixin, CombatEnvironmentMixin,
    CombatValidationMixin, CombatResolutionMixin,
    CombatSpellResolutionMixin, CombatHitResolutionMixin,
    CombatReducerMixin, CombatViewMixin, CombatTriggeredSpellsMixin,
    CombatReactionSpellsMixin, CombatCheckWindowsMixin, CombatDivineActionsMixin,
    CombatStealthActionsMixin,
    CombatMartialActionsMixin,
    CombatSupportActionsMixin,
    CombatOngoingEffectsMixin,
    CombatDefensiveTacticsMixin,
):
    bundle: LoadedRulesetBundle
    encounter_access: EncounterAccess = field(default_factory=EncounterAccess.blocked)
    encounter_catalog: dict[str, Any] | None = None
    # FIX-03 §5.3：运行时内容目录（adventure-local → module → core）。命中模块
    # encounter_profile / ContentRef enemy 时，敌人实例来自这里，而不是内联
    # statblock；未注入时退化为纯内联（legacy 冒险包行为不变）。
    content_catalog: Any | None = None
    content_source: str = ""
    # runtime.load_bundle 已在装载边界完成物品声明校验；引擎从那里拿到
    # bundle 时跳过重复的全量校验，直接构造 catalog。
    bundle_validated: bool = False
    # 死亡重算需要所有 locale 的物品名称索引；由 runtime 注入加载器，
    # 避免引擎自己猜测 bundle 目录。
    locale_loader: Any | None = None
    supported_locales: tuple[str, ...] = ()
    # 掩护等级查询的调用深度（同步方法内使用）：灵光投影内部重建 actor
    # 视图时视图不再嵌套查询掩护，环在此断开。0 = 不在掩护计算中。
    # 读写方是 environment mixin 的 ``cover_level``；字段属于引擎。
    _cover_depth: int = 0
    catalog: Dnd2024CombatCatalog = field(init=False)
    spells: Dnd2024SpellCatalog = field(init=False)
    features: Dnd2024ClassFeatureResolver = field(init=False)

    def __post_init__(self) -> None:
        self.catalog = Dnd2024CombatCatalog.from_bundle(
            self.bundle, validate_items=not self.bundle_validated,
        )
        self.spells = Dnd2024SpellCatalog.from_bundle(self.bundle)
        self.features = Dnd2024ClassFeatureResolver(self.bundle)

    def initialize_state(self, instance: Any) -> dict[str, Any]:
        state = ruleset_runtime.state(instance)
        if not isinstance(state, dict):
            raise CombatIntentError("ruleset_state must be an object")
        state.setdefault("state_schema_version", 1)
        state.setdefault("version", 0)
        state.setdefault("combat_history", [])
        # AI 队友的权威角色资源挂在 DND 自己的 party 状态下（不进 instance.players）；
        # 旧存档缺省自动补齐，行为不变。
        state.setdefault("party", {"companions": {}})
        state["party"].setdefault("companions", {})
        state.setdefault("combat", {
            "status": "none", "round": 0, "turn_index": 0,
            "initiative": [], "enemies": {}, "positions": {},
            "economy": {}, "pending_decisions": [], "position_mode": "theater",
            # 持握与骑乘是遭遇级状态；旧档没有这两个键时同样安全。
            "hands": {}, "mounted": {},
        })
        if state.get("state_schema_version") != 1:
            raise CombatIntentError("unsupported D&D 2024 combat state schema")
        if not isinstance(state.get("combat_history"), list):
            raise CombatIntentError("combat_history must be an array")
        return state
    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        try:
            self._validate(instance, intent)
        except CombatIntentError as exc:
            return {"ok": False, "code": "INVALID_INTENT", "error": str(exc)}
        return {"ok": True}

    def resolve_intent(
        self, instance: Any, intent: dict[str, Any], rng: Any,
    ) -> dict[str, Any]:
        state = self.initialize_state(instance)
        intent_id = str(intent.get("intent_id") or "")
        prior = next(
            (item for item in ruleset_runtime.event_ledger(instance) if item.get("intent_id") == intent_id),
            None,
        )
        if prior is not None:
            expected = intent.get("expected_version")
            if (
                isinstance(expected, bool) or not isinstance(expected, int)
                or str(prior.get("batch_id") or "") != stable_batch_id(intent, expected)
            ):
                return {
                    "ok": False,
                    "code": "INTENT_ID_CONFLICT",
                    "error": "intent_id was already used for a different request",
                }
            return {"ok": True, "event_batch": deepcopy(prior), "replayed": True}
        validation = self.validate_intent(instance, intent)
        if not validation["ok"]:
            return validation
        combat = state["combat"]
        intent_type = str(intent["type"])
        actor_id = str(intent.get("actor_id") or "")
        events = [{
            "type": "intent.submitted", "intent_type": intent_type,
            "actor_id": str(intent.get("actor_id") or ""),
            "submitted_by": str(intent.get("submitted_by") or ""),
        }]
        pending: dict[str, Any] | None = None
        if intent_type in {"encounter.ready", "encounter.unready"}:
            events.append({
                "type": "dnd2024.encounter.readiness.changed",
                "player_id": str(intent.get("submitted_by") or ""),
                "ready": intent_type == "encounter.ready",
            })
        elif intent_type == "combat.start":
            # The catalog is authoritative.  The client may select a preset, but
            # it must never be able to alter the enemy stat block in the event.
            resolved_intent = deepcopy(intent)
            preset_id = str(intent.get("encounter_preset_id") or "")
            if preset_id:
                preset = self._preset(preset_id)
                if preset is None:  # validation normally catches this
                    raise CombatIntentError("encounter preset is not available")
                resolved_intent["enemies"] = deepcopy(preset["enemies"])
            if self.encounter_access.mode == "story":
                resolved_intent.update({
                    "encounter_instance_id": self.encounter_access.encounter_instance_id,
                    "encounter_preset_id": self.encounter_access.encounter_preset_id,
                    "origin_step_id": self.encounter_access.origin_step_id,
                    "mode": "story",
                    "adventure_binding": {
                        "adventure_id": self.encounter_access.adventure_id,
                        "step_id": self.encounter_access.origin_step_id,
                        "encounter_preset_id": self.encounter_access.encounter_preset_id,
                        "encounter_instance_id": self.encounter_access.encounter_instance_id,
                    },
                })
            else:
                # 自由遭遇不属于任何冒险包；把这个事实写进权威状态，界面才能
                # 持续显示“自由战斗”，而叙事文本无法改变它。
                resolved_intent.update({"mode": "sandbox", "adventure_binding": None})
            events.extend(self._start_combat_event(instance, resolved_intent, rng))
        elif intent_type == "combat.end":
            events.append({"type": "dnd2024.combat.ended", "reason": "gm"})
        elif intent_type == "combat.set_cover":
            events.append({
                "type": "dnd2024.cover.declared",
                "target_id": str(intent["target_id"]),
                "level": str(intent["level"]),
                "source": "gm",
            })
        elif intent_type == "combat.set_obscurement":
            events.append({
                "type": "dnd2024.obscurement.declared",
                "target_id": str(intent["target_id"]),
                "level": str(intent["level"]),
                "source": "gm",
            })
        elif intent_type == "gm_condition":
            # GM 裁定原语：事件形状与法术/特性施加条件完全一致，所以 reducer、
            # 投影与到期逻辑都不需要第二套写法。同一 id 是覆盖写——重复施加
            # 即刷新来源，不叠加。
            target_id = str(intent["target_id"])
            condition_id = str(intent["condition"])
            if str(intent.get("action") or "") == "apply":
                event = {
                    "type": "condition.applied",
                    "target_id": target_id,
                    "condition": condition_id,
                    "source": "gm",
                }
                if intent.get("duration"):
                    event["duration"] = str(intent["duration"])
                events.append(event)
            else:
                events.append({
                    "type": "condition.removed",
                    "target_id": target_id,
                    "condition": condition_id,
                    "source": "gm",
                })
        elif intent_type == "combat.message":
            events.append({
                "type": "dnd2024.combat.message",
                "actor_id": _player_actor(str(intent.get("submitted_by") or "")),
                "text": str(intent.get("text") or "").strip(),
            })
        elif intent_type == "attack":
            # 换手只是这一次攻击里附带的一次装备/卸下，所以它和攻击在同一个
            # 批次里结算：事件先落地，攻击按换手之后的手部状态判定。
            actor_view = None
            if intent.get("weapon_swap") is not None:
                base = self._actor_view(instance, combat, str(intent["actor_id"]))
                hands = self._weapon_swap_hands(base, intent) or base.get("hands") or {}
                actor_view = {**base, "hands": hands}
                events.append({
                    "type": "dnd2024.weapon.swapped",
                    "actor_id": str(intent["actor_id"]),
                    "left": hands.get("left"),
                    "right": hands.get("right"),
                })
            second_target_of = str(intent.get("second_target_of") or "")
            events.extend(self._attack_events(
                instance, combat, intent, rng, actor_view=actor_view,
                # 第二击不付动作，也不占攻击次数：它的合法性由"本回合已对原目标
                # 出手"这条事实买断，校验层已经证明过。
                spend_attack_action=not bool(second_target_of),
                horde_breaker=bool(second_target_of),
            ))
        elif intent_type == "multiattack":
            events.extend(self._multiattack_events(instance, combat, intent, rng))
        elif intent_type == "save_action":
            events.extend(self._save_action_events(instance, combat, intent, rng))
        elif intent_type == "escape":
            events.extend(
                self._escape_events(combat, intent, self._actor_view(instance, combat, actor_id), rng)
            )
        elif intent_type == "stand_up":
            events.extend(self._stand_up_events(
                self._actor_view(instance, combat, actor_id), intent,
                speed=int((combat.get("economy") or {}).get("speed") or 0),
            ))
        elif intent_type == "superior_hunters_prey":
            events.extend(self._superior_prey_events(instance, combat, intent, rng))
        elif intent_type == "class_capability":
            events.extend(self._class_capability_events(instance, combat, intent, rng))
        elif intent_type == "cast_spell":
            window = self._counter_spell_window(instance, combat, intent)
            if window is not None:
                events.append({"type": "dnd2024.decision.pending", **window})
            else:
                events.extend(self._spell_events(instance, combat, intent, rng))
        elif intent_type == "activate_item":
            events.extend(self._activate_item_events(instance, combat, intent, rng))
        elif intent_type == "move":
            pending, movement_events = self._movement_events(instance, combat, intent)
            events.extend(movement_events)
        elif intent_type in {"dash", "dodge", "disengage"}:
            events.extend(self._basic_action_events(combat, intent_type, str(intent["actor_id"])))
        elif intent_type == "hide":
            events.extend(self._hide_events(
                instance, combat, str(intent["actor_id"]), rng,
                intent_id=str(intent.get("intent_id") or ""),
            ))
        elif intent_type == "land_aid":
            events.extend(self._land_aid_events(instance, combat, intent, rng))
        elif intent_type == "nature_sanctuary":
            events.extend(self._nature_sanctuary_events(instance, combat, intent))
        elif intent_type == "wild_resurgence":
            events.extend(self._wild_resurgence_events(instance, combat, intent))
        elif intent_type == "nature_magician":
            events.extend(self._nature_magician_events(instance, combat, intent))
        elif intent_type in {"form.assume", "form.revert"}:
            events.extend(self._form_events(instance, combat, intent))
        elif intent_type == "ability_check":
            events.extend(self._ability_check_events(
                combat, intent, self._actor_view(instance, combat, str(intent["actor_id"])), rng,
            ))
        elif intent_type == "end_turn":
            events.extend(self._end_turn_events(instance, combat, rng, intent))
        elif intent_type == "death_save":
            events.extend(self._death_save_events(instance, combat, intent, rng))
        elif intent_type == "stabilize":
            events.extend(self._stabilize_events(instance, combat, intent, rng))
        elif intent_type == "decision.resolve":
            events.extend(self._decision_events(instance, combat, intent, rng))
        else:  # pragma: no cover - validation guards this
            raise CombatIntentError("unsupported intent")
        expected = int(intent["expected_version"])
        if pending is None:
            pending = next(({key: value for key, value in event.items() if key != "type"} for event in events
                            if event.get("type") == "dnd2024.decision.pending"), None)
        # 回合开始窗口在读事件之前关闭：一旦这个回合干了别的事，"at the start
        # of your turn" 就已经过去了，不能回头补。窗口属于经济预算，所以它是
        # 一条事件而不是某个分支里顺手改的状态。
        events.extend(self._turn_window_closure_events(combat, intent))
        events.extend(self._incapacitation_cleanup_events(instance, combat, events))
        events.extend(self._source_incapacitation_events(instance, combat, events))
        events.extend(self._timed_condition_expiry_events(instance, combat))
        events.extend(self._shape_exit_events(instance, combat, events))
        events.extend(self._zone_expiry_events(instance, combat))
        # 世界时钟驱动的到期：一场在一分钟内结束的战斗会把效果留在身上，
        # 只要之后还有任何战斗意图或时钟越过那一分钟就会被清掉。end_turn
        # 由回合推进处统一查两个上限，这里不能重复发同一条移除事件。
        if intent_type != "end_turn" and combat.get("initiative"):
            events.extend(self._superior_defense_expiry_events(instance, combat))
        # S0-MM1：条件免疫是 stat block 事实，拦在"产出侧"一处，覆盖所有生产者。
        events = self._statblock_immunity_events(combat, events)

        batch = {
            "batch_id": stable_batch_id(intent, expected),
            "intent_id": intent_id,
            "intent_type": intent_type,
            "expected_version": expected,
            "result_version": expected + 1,
            "events": events,
            "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
        }
        return {"ok": True, "event_batch": batch, "pending_decision": pending}
    def _turn_window_closure_events(
        self, combat: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Close the turn-start window after anything but a chat message.

        The rule is not "one free action then close": it is that the window
        belongs to one instant of the turn.  So every intent the engine can
        settle closes it, including the activation itself and ``end_turn`` --
        an exclusion list only for messages, readiness pings, the start of the
        combat (which is the intent that *opens* it) and its end.
        """

        window = (combat.get("economy") or {}).get("turn_start_window")
        if not isinstance(window, dict):
            return []
        intent_type = str(intent.get("type") or "")
        if intent_type in {
            "combat.start", "combat.end", "combat.message",
            "encounter.ready", "encounter.unready",
        }:
            return []
        return [{
            "type": TURN_WINDOW_CLOSED_EVENT,
            "actor_id": str(window.get("actor_id") or ""),
            "reason": intent_type,
        }]
    def _combat_actor_ids(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[str]:
        """Every actor currently on the battlefield, in a stable order."""

        ids = [
            _player_actor(str(uid))
            for uid in sorted(getattr(instance, "players", {}) or {})
        ]
        party = (
            ruleset_runtime.state(instance).get("ruleset_state", {}) or {}
        ).get("party", {}) or {}
        ids.extend(
            _companion_actor(str(companion_id))
            for companion_id in sorted(party.get("companions", {}) or {})
        )
        ids.extend(
            _enemy_actor(str(enemy_id))
            for enemy_id in sorted((combat.get("enemies") or {}))
        )
        return ids

    #: 结束形状的四个"非主动"原因。
    _SHAPE_EXIT_REASONS = ("death", "incapacitated", "expired", "unavailable")

    def _active_form_payload(self, instance: Any, actor_id: str) -> dict[str, Any] | None:
        """The stored active shape of one player actor, read without composing a panel."""

        kind, raw_id = _actor_kind(str(actor_id or ""))
        if kind != "player" or raw_id not in (getattr(instance, "players", {}) or {}):
            return None
        forms = _canonical(instance.get_character_sheet(raw_id)).get("forms")
        active = forms.get("active") if isinstance(forms, dict) else None
        return active if isinstance(active, dict) else None

    def _shape_exit_events(
        self, instance: Any, combat: dict[str, Any],
        pending_events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """变形在失能、死亡或到期时结束。

        三个判定都可达：失能与死亡是 `condition.applied` 的结果，到期是逻辑时钟
        的差。这里挂在**每条意图的公共收尾**上，所以不必等到回合末——SRD 明确
        说失能/死亡立即结束，`end_turn` 才处理就晚了。
        """

        events: list[dict[str, Any]] = []
        pending_events = pending_events or []
        already_reverted = {
            str(event.get("actor_id") or "")
            for event in pending_events
            if event.get("type") == "dnd2024.form.reverted"
        }
        now = self._logical_minutes(instance)
        for actor_id in self._combat_actor_ids(instance, combat):
            if actor_id in already_reverted:
                continue
            try:
                view = self._actor_view(instance, combat, actor_id)
            except CombatIntentError:
                if self._active_form_payload(instance, actor_id) is not None:
                    # 存档里的形态引用已经读不出 stat block（目录改名、内容包停用）。
                    # 视图层按约定拒绝猜一份面板，但留着这条记录，这个角色就做不了
                    # 任何意图——包括变回原形——轮到他时整桌都走不下去。这里结束它。
                    events.append({
                        "type": "dnd2024.form.reverted", "actor_id": actor_id,
                        "reason": "unavailable",
                    })
                continue
            form = view.get("form_ref")
            if not form:
                continue
            conditions = deepcopy(view.get("conditions") or {})
            hp = int(view.get("hp", 0) or 0)
            temp_hp = int(view.get("temp_hp", 0) or 0)
            for event in pending_events:
                if str(event.get("target_id") or "") != actor_id:
                    continue
                event_type = str(event.get("type") or "")
                if event_type == "condition.applied":
                    conditions[str(event.get("condition") or "")] = {}
                elif event_type == "condition.removed":
                    conditions.pop(str(event.get("condition") or ""), None)
                elif event_type == "resource.changed":
                    resource = str(event.get("resource") or "hp")
                    if resource == "temp_hp":
                        temp_hp = max(temp_hp, int(event.get("amount", 0) or 0))
                        continue
                    delta = int(event.get("delta", 0) or 0)
                    if delta < 0 and temp_hp > 0 and not event.get("healing"):
                        absorbed = min(temp_hp, -delta)
                        temp_hp -= absorbed
                        delta += absorbed
                    next_hp = max(0, min(int(view.get("max_hp", hp) or hp), hp + delta))
                    if (
                        next_hp <= 0
                        and hp > 0
                        and isinstance(event.get("relentless"), dict)
                        and not event.get("relentless_deferred")
                    ):
                        # 与 reducer 一致：不屈狂暴把这次致命伤挂在决策上，HP 不动。
                        # 预演若把它当成已经落地，形态会在 HP 根本没降时被结束。
                        continue
                    hp = next_hp
                    if hp <= 0:
                        conditions["unconscious"] = {}
                    elif delta > 0:
                        conditions.pop("unconscious", None)
                        conditions.pop("stable", None)
                elif event_type == "dnd2024.death_save.resolved":
                    if event.get("dead"):
                        conditions["dead"] = {}
                    elif event.get("stable"):
                        conditions["stable"] = {}
            if "dead" in conditions:
                reason = "death"
            elif any(
                self._condition_implies_incapacitated(str(condition_id))
                for condition_id in conditions
            ):
                reason = "incapacitated"
            else:
                assumed = int(view.get("form_assumed_at_minute", 0) or 0)
                hours = int(view.get("form_duration_hours", 0) or 0)
                if hours <= 0 or now < assumed + hours * 60:
                    continue
                reason = "expired"
            events.append({
                "type": "dnd2024.form.reverted", "actor_id": actor_id,
                "reason": reason,
            })
        return events
    def add_player_to_active_combat(self, instance: Any, user_id: str) -> dict[str, Any]:
        """Enroll a newly joined player after the current combat actor.

        Membership changes happen outside the combat intent form, but still use
        the same versioned event ledger so stale client intents cannot skip the
        newly added actor.  The caller holds the instance state lock while this
        method runs.
        """

        state = self.initialize_state(instance)
        combat = state["combat"]
        if combat.get("status") != "active":
            return {"ok": True, "changed": False}
        actor_id = _player_actor(str(user_id))
        order = list(combat.get("initiative") or [])
        if actor_id in order:
            return {"ok": True, "changed": False}
        if str(user_id) not in instance.players:
            return {"ok": False, "error": "player does not exist"}
        turn_index = int(combat.get("turn_index", 0) or 0)
        insert_index = max(0, min(len(order), turn_index + 1))
        canonical = _canonical(instance.get_character_sheet(str(user_id)))
        modifier = self._initiative_modifier(
            canonical, self._initiative_view(instance, str(user_id), canonical),
        )
        roll = SystemRandom().randint(1, 20)
        expected_version = int(state.get("version", 0) or 0)
        intent = {
            "intent_id": f"player-join:{expected_version}:{user_id}",
            "type": "combat.player_joined",
            "expected_version": expected_version,
            "user_id": str(user_id),
        }
        batch = {
            "batch_id": stable_batch_id(intent, intent["expected_version"]),
            "intent_id": intent["intent_id"],
            "intent_type": intent["type"],
            "expected_version": intent["expected_version"],
            "result_version": expected_version + 1,
            "events": [
                {
                    "type": "intent.submitted",
                    "intent_type": intent["type"],
                    "actor_id": actor_id,
                    "submitted_by": str(user_id),
                },
                {
                    "type": "dnd2024.combat.player_joined",
                    "actor_id": actor_id,
                    "insert_index": insert_index,
                    "roll": roll,
                    "modifier": modifier,
                    "total": roll + modifier,
                    "position": 0,
                },
            ],
            "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
        }
        result = self.apply_batch(instance, batch)
        result["changed"] = bool(result.get("applied"))
        return result

    def revive_player(self, instance: Any, user_id: str, hp: int) -> dict[str, Any]:
        """Apply a ruleset-owned revival to canonical character state.

        Revival is versioned even when combat is not active so any previously
        issued combat action cannot race the newly conscious character state.
        """

        state = self.initialize_state(instance)
        if str(user_id) not in instance.players:
            return {"ok": False, "error": "player does not exist"}
        actor_id = _player_actor(str(user_id))
        expected_version = int(state.get("version", 0) or 0)
        intent = {
            "intent_id": f"player-revive:{expected_version}:{user_id}",
            "type": "character.revive",
            "expected_version": expected_version,
            "user_id": str(user_id),
        }
        batch = {
            "batch_id": stable_batch_id(intent, expected_version),
            "intent_id": intent["intent_id"],
            "intent_type": intent["type"],
            "expected_version": expected_version,
            "result_version": expected_version + 1,
            "events": [
                {
                    "type": "intent.submitted",
                    "intent_type": intent["type"],
                    "actor_id": actor_id,
                    "submitted_by": str(user_id),
                },
                {
                    "type": "dnd2024.character.revived",
                    "actor_id": actor_id,
                    "hp": int(hp),
                },
            ],
            "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
        }
        return self.apply_batch(instance, batch)

    def apply_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]:
        ruleset_runtime.require_writable(instance)
        ruleset_runtime.require_binding(instance, self.bundle.manifest.runtime_id)
        state = self.initialize_state(instance)
        snapshot = {
            "version": int(state.get("version", 0) or 0),
            "ruleset_state": deepcopy(state),
            "characters": {
                uid: deepcopy(_canonical(instance.get_character_sheet(uid)))
                for uid in instance.players
            },
        }
        # 死亡清理是 reducer 的副作用，不是客户端提交的事件；记录死亡前后的
        # 绑定差异，把一次死亡对应的解绑事件补进权威 ledger 与返回批次。
        before_characters = deepcopy(snapshot["characters"])
        # 特性免疫（Nature's Ward / Mindless Rage）在这里统一过滤：免疫是目标的
        # 属性，任何生产者（法术、rider、地形、GM 声明）都该得到同一个答案。
        combat_state = (state.get("combat") or {})
        if combat_state.get("status") == "active":
            filtered = self._feature_immunity_events(instance, combat_state, batch["events"])
            if filtered is not batch["events"]:
                batch = {**batch, "events": filtered}
        updated, ledger, duplicate = apply_event_batch(
            snapshot, ruleset_runtime.event_ledger(instance), batch, self._reduce_event,
        )
        result_batch = deepcopy(batch)
        if duplicate:
            stored = next(
                (item for item in ledger if str(item.get("batch_id") or "") == str(batch["batch_id"])),
                None,
            )
            if isinstance(stored, dict):
                result_batch = deepcopy(stored)
        else:
            derived, dead_uids = _death_attunement_events(
                before_characters, updated["characters"],
            )
            if derived:
                result_batch["events"] = [*result_batch["events"], *derived]
                for entry in reversed(ledger):
                    if str(entry.get("batch_id") or "") == str(batch["batch_id"]):
                        entry["events"] = deepcopy(result_batch["events"])
                        break
            ruleset_state = updated["ruleset_state"]
            ruleset_state["version"] = updated["version"]
            from src.commands.state_items import (
                remove_destroyed_item,
                take_from_inventory,
            )

            updated_sheets: dict[str, dict[str, Any]] = {}
            for uid, canonical in updated["characters"].items():
                existing = deepcopy(instance.get_character_sheet(uid))
                projection = merge_live_character_projection(existing, self._project_character(canonical))
                existing.update(projection)
                if uid in dead_uids:
                    # 死亡清空同调后 AC 必须立刻回落到未同调值，不能等下一次
                    # 换装或休息才纠正。名字索引必须覆盖全部 locale，否则旧
                    # 的非英文装备会在重算时丢失身份。
                    locale_bundles = (
                        [self.locale_loader(name) for name in self.supported_locales]
                        if self.locale_loader is not None and self.supported_locales
                        else []
                    )
                    Dnd2024CharacterStateReconciler(
                        self.bundle, locale_bundles=locale_bundles,
                    ).reconcile(existing, frozenset({"equipment"}))
                for event in result_batch.get("events") or []:
                    if (
                        str(event.get("type") or "") == "dnd2024.item.consumed"
                        and str(event.get("actor_id") or "") == f"player:{uid}"
                    ):
                        for _ in range(int(event.get("amount", 1) or 1)):
                            taken = take_from_inventory(existing, "", str(event["item_ref"]))
                            if taken is None:
                                raise EventBatchError(
                                    f"item is no longer carried: {event['item_ref']}"
                                )
                    elif (
                        str(event.get("type") or "") == "dnd2024.item.destroyed"
                        and str(event.get("actor_id") or "") == f"player:{uid}"
                    ):
                        # canonical 已按 ref 摘除，live 背包/装备行必须同步清理，
                        # 否则下一次 reconcile 会把行重新写回 canonical。
                        remove_destroyed_item(existing, str(event["item_ref"]))
                updated_sheets[uid] = existing
            ruleset_runtime.replace_state(instance, ruleset_state)
            for uid, sheet in updated_sheets.items():
                instance.set_character_sheet(uid, sheet)
            ruleset_runtime.replace_event_ledger(instance, ledger)
        return {
            "ok": True,
            "applied": not duplicate,
            "duplicate": duplicate,
            "state_version": int(ruleset_runtime.state(instance).get("version", 0) or 0),
            "event_batch": result_batch,
            "combat": deepcopy(ruleset_runtime.state(instance).get("combat") or {}),
        }

    def gameplay_view(
        self, instance: Any, viewer_id: str = "", viewer_is_gm: bool = True,
    ) -> dict[str, Any]:
        state = self.initialize_state(instance)
        combat = state["combat"]
        actors: list[dict[str, Any]] = []
        if combat.get("status") == "active":
            for target in self._all_targets(instance, combat):
                view = self._actor_view(instance, combat, str(target["actor_id"]))
                actors.append({
                    **target,
                    "armor_class": view["armor_class"],
                    "speed": view["speed"],
                    "conditions": deepcopy(view["conditions"]),
                    "concentration": deepcopy(view.get("concentration")),
                    "death_saves": deepcopy(view.get("death_saves") or {}),
                    # 职业资源投影永远是一张列表（player / companion 视图给出角色
                    # 真实条目，enemy 视图没有职业资源，就是空列表）。这里绝不能
                    # 退回 ``{}``：同一个字段出现两种形状会让客户端把对象当数组用。
                    "class_resources": deepcopy(view.get("class_resources") or []),
                    "spells": self._spell_statuses(
                        instance, view, combat.get("economy", {}),
                        hit_spell_decision(combat, view["actor_id"]),
                        is_current=view["actor_id"] == self._current_actor(combat),
                    ),
                })
        initiative = list(combat.get("initiative") or [])
        turn_index = int(combat.get("turn_index", 0) or 0)
        # ``turn_index`` 是 slot 下标；``initiative`` 是按人去重的列表，二者只在
        # 没有 Thief's Reflexes 时一一对应。当前行动者必须由 slot 回答，否则
        # 首轮第二个 slot 会把光标停在前一个行动者身上。
        current_slot = _current_turn_slot(combat)
        current_actor_id = str(current_slot["actor_id"]) if current_slot else ""
        return {
            "state_schema_version": int(state.get("state_schema_version", 1) or 1),
            "state_version": int(state.get("version", 0) or 0),
            "combat": {
                "status": str(combat.get("status") or "none"),
                "outcome": str(combat.get("outcome") or ""),
                "round": int(combat.get("round", 0) or 0),
                "turn_index": turn_index,
                "turn_slot_id": str(current_slot["slot_id"]) if current_slot else "",
                "current_actor_id": current_actor_id,
                "initiative": initiative,
                "position_mode": str(combat.get("position_mode") or "theater"),
                "economy": deepcopy(combat.get("economy") or {}),
                "reactions": deepcopy(combat.get("reactions") or {}),
                "pending_decisions": public_pending_decisions(
                    combat.get("pending_decisions") or [],
                    viewer_id=viewer_id, viewer_is_gm=viewer_is_gm,
                ),
                "encounter_instance_id": str(combat.get("encounter_instance_id") or ""),
                "encounter_preset_id": str(combat.get("encounter_preset_id") or ""),
                "origin_step_id": str(combat.get("origin_step_id") or ""),
                "mode": str(combat.get("mode") or ""),
                "adventure_binding": deepcopy(combat.get("adventure_binding")),
                "bardic_instances": self._active_bardic_view(instance, combat),
                # 私有侦测只交给持有者本人：同一条 combat 视图对别人是空的。
                "private_senses": (
                    self._divine_sense_projection(instance, combat, viewer_id)
                    if combat.get("status") == "active" else {}
                ),
                # G16：GM 的裁定面板要能"先读定义再施加"。玩家侧不给这份目录，
                # 他们只需要看到自己身上贴了什么。
                "condition_catalog": (
                    self.catalog.condition_catalog() if viewer_is_gm else []
                ),
                "actors": actors,
            },
            "encounter_presets": self.encounter_presets(),
            # 权威模式投影：前端据此区分“剧情绑定/剧情未准备/自由遭遇”，
            # 不再靠显示名或通用目录推断当前敌情来源。
            "encounter_access": {
                "mode": self.encounter_access.mode,
                "status": self.encounter_access.status,
                # 进行中的战斗不再开放 start 入口，视图也要如实反映这一点。
                "can_start": bool(
                    self.encounter_access.can_start and combat.get("status") != "active"
                ),
                "unprepared": bool(self.encounter_access.unprepared),
                "adventure_id": self.encounter_access.adventure_id,
                "encounter_preset_id": self.encounter_access.encounter_preset_id,
                "encounter_instance_id": self.encounter_access.encounter_instance_id,
                "origin_step_id": self.encounter_access.origin_step_id,
                "catalog": "adventure" if self.encounter_access.mode == "story" else "bundle",
            },
        }

    def encounter_presets(self) -> list[dict[str, Any]]:
        catalog = (
            deepcopy(self.encounter_catalog)
            if isinstance(self.encounter_catalog, dict)
            else self.bundle.get("encounter_catalog", "srd_training_encounters") or {}
        )
        raw_labels = catalog.get("labels")
        labels: dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
        raw_preset_labels = labels.get("presets")
        raw_profile_labels = labels.get("profiles")
        raw_attack_labels = labels.get("attacks")
        preset_labels: dict[str, Any] = (
            raw_preset_labels if isinstance(raw_preset_labels, dict) else {}
        )
        profile_labels: dict[str, Any] = (
            raw_profile_labels if isinstance(raw_profile_labels, dict) else {}
        )
        attack_labels: dict[str, Any] = (
            raw_attack_labels if isinstance(raw_attack_labels, dict) else {}
        )
        result: list[dict[str, Any]] = []
        seen_preset_ids: set[str] = set()
        for raw in [
            *(catalog.get("presets") or []),
            *self._module_presets(),
        ]:
            if not isinstance(raw, dict):
                continue
            preset = deepcopy(raw)
            if (
                self.encounter_access.mode == "sandbox"
                and str(preset.get("difficulty") or "") == "tutorial"
            ):
                continue
            preset_id = str(preset.get("id") or "")
            if not preset_id or preset_id in seen_preset_ids:
                continue
            seen_preset_ids.add(preset_id)
            text = preset_labels.get(preset_id, {})
            text = text if isinstance(text, dict) else {}
            preset["name"] = str(
                text.get("name") or preset.get("name") or preset_id or "Encounter"
            )
            preset["description"] = str(
                text.get("description") or preset.get("description") or ""
            )
            preset["enemies"] = self._preset_enemies(preset)
            for enemy in preset["enemies"]:
                profile_id = str(enemy.pop("profile_id", "") or "")
                enemy["name"] = str(
                    profile_labels.get(profile_id) or enemy.get("name") or profile_id or enemy["id"]
                )
                for attack in enemy.get("attacks") or []:
                    attack_id = str(attack.get("id") or "")
                    attack["name"] = str(attack_labels.get(attack_id) or attack_id)
            self._validate_enemies(preset["enemies"])
            result.append(preset)
        return result

    def _preset_enemies(self, preset: dict[str, Any]) -> list[dict[str, Any]]:
        """Enemy instances for one preset: module ContentRefs or inline statblocks.

        FIX-03 §5.3：引用式敌人（``enemies[].ref``）经运行时内容目录解析为
        canonical enemy 实例；内联 statblock（v1 冒险包）原样保留。解析失败
        fail closed —— 绝不用别的怪物顶上。
        """

        enemies = preset.get("enemies")
        if not isinstance(enemies, list):
            return []
        if not any(isinstance(entry, dict) and entry.get("ref") for entry in enemies):
            return deepcopy(enemies)
        catalog = self.content_catalog
        if catalog is None:
            raise CombatIntentError(
                "module encounter requires the runtime content catalog"
            )
        refs = [entry for entry in enemies if isinstance(entry, dict)]
        if len(refs) != len(enemies):
            raise CombatIntentError("encounter enemy entry must be an object")
        return expand_encounter_enemies(
            catalog,
            refs,
            default_source=str(preset.get("source_ref") or self.content_source or ""),
        )

    def _module_presets(self) -> list[dict[str, Any]]:
        """Encounter presets contributed by the runtime content catalog (§5.3)."""

        catalog = self.content_catalog
        if catalog is None:
            return []
        records = getattr(catalog, "records_for", None)
        source: Any
        if callable(records):
            source = records("encounter_profile")
        else:  # pragma: no cover - 目录实现始终提供 records_for
            source = {}
        presets: list[dict[str, Any]] = []
        for record in (source or {}).values():
            if not isinstance(record, dict):
                continue
            presets.append({
                "id": str(record.get("encounter_id") or ""),
                "name": str(record.get("name") or ""),
                "description": str(record.get("description") or ""),
                "difficulty": str(record.get("difficulty") or "standard"),
                "source_ref": str(record.get("source_ref") or ""),
                "enemies": deepcopy(record.get("enemies") or []),
            })
        return [preset for preset in presets if preset["id"]]

    def _preset(self, preset_id: str) -> dict[str, Any] | None:
        wanted = str(preset_id or "")
        if not wanted:
            return None
        return next(
            (preset for preset in self.encounter_presets() if preset.get("id") == wanted),
            None,
        )

    @staticmethod
    def _current_actor(combat: dict[str, Any]) -> str:
        # 当前行动者读的是 slot，不是 initiative 的下标：首轮里同一个角色可能
        # 占两个 slot（Thief's Reflexes），而 initiative 仍然按人去重。
        slot = _current_turn_slot(combat)
        if slot is None:
            raise CombatIntentError("combat initiative state is invalid")
        return str(slot["actor_id"])

    def _fresh_economy(self, actor: dict[str, Any]) -> dict[str, Any]:
        base = max(0, int(actor.get("speed", 0) or 0))
        # 速度修正来自条件注册表：speed_zero 压倒修正，其余按 speed_modifier 求和。
        speed = effective_speed(self.catalog.conditions, actor.get("conditions") or {}, base)
        economy = {
            "actor_id": actor["actor_id"], "action": 1, "bonus_action": 1,
            "reaction": 1, "movement": speed, "speed": speed,
            "attacks_remaining": 0, "disengaged": False,
        }
        # 回合开始窗口属于这个回合，因此随经济预算一起生成、一起消失。
        window = self._turn_start_window(actor)
        if window is not None:
            economy["turn_start_window"] = window
        return economy

    def _turn_start_window(self, actor: dict[str, Any]) -> dict[str, Any] | None:
        """The turn-start payment window this actor is offered right now.

        A window is opened only for a capability that *costs* the window: the
        mechanism exists so that "at the start of your turn" can be a price,
        not so every feature grows a free prompt.  When the resource cannot be
        paid the window is not opened at all -- an unpayable button is noise,
        and the capability's own cost check would refuse it anyway.

        A capability with no declared action cost is the whole reason this is a
        separate object: the window is neither an action nor a resource, so it
        cannot be read off the economy's remaining budgets.
        """

        if actor.get("kind") not in {"player", "companion"}:
            return None
        character = self._feature_character(actor)
        for definition in self.features.active_features_for(character):
            for capability in definition.capabilities:
                if capability.cost.timing != TURN_START_TIMING:
                    continue
                affordable = all(
                    spell_slot_count(actor.get("slots") or {}, cost.level) >= 1
                    if isinstance(cost, SpellSlotCost)
                    else int(resource_state(character, cost.resource_id).get("current", 0) or 0)
                    >= cost.amount
                    for cost in capability.cost.resources
                )
                if not affordable:
                    continue
                return {
                    "actor_id": str(actor["actor_id"]),
                    "feature_id": definition.id,
                    "capability_id": capability.id,
                }
        return None

    def _attacks_per_action(self, actor: dict[str, Any]) -> int:
        # Extra Attack 由职业表决定：progression 表说哪个职业在几级授予哪个
        # feature id，catalog 声明该 feature 的 attacks_per_action。多职业按
        # RAW 不叠加、取最大值，见 ``ClassFeatureResolver.attacks_per_action``。
        # Companion 复用玩家职业 Extra Attack 语义（canonical build 相同）；
        # 敌人没有职业等级，恒为 1。
        if actor["kind"] not in {"player", "companion"}:
            return 1
        return self.features.attacks_per_action(actor)

    def _attack_cost_event(
        self, combat: dict[str, Any], actor: dict[str, Any], *, resource: str = "",
        weapon: dict[str, Any] | None = None, attacks_per_action: int = 0,
    ) -> dict[str, Any]:
        return {
            "type": "dnd2024.attack.spent", "actor_id": actor["actor_id"],
            "resource": resource or self._spend_action_resource(combat),
            "attacks_per_action": (
                attacks_per_action or self._attacks_per_action(actor)
            ),
            # 归约需要知道"这一击用的是不是 Light 武器"，才能回答下一问：
            # "本回合还允许 Light 追加攻击吗"。
            "light_weapon": bool((weapon or {}).get("light")),
            # 但"用 Light 武器攻击过"还不够：追加攻击的参照点是**触发它的那一次
            # 攻击所用的武器**（"a different Light weapon"），所以账本必须记下
            # 每一下的武器，而不是只记一个布尔。空串代表徒手或没有武器档案。
            "weapon_ref": str((weapon or {}).get("weapon_ref") or ""),
            # Loading 的节流单位是"一次 action/Bonus/Reaction"，归约按窗口记账。
            "loading_weapon": bool((weapon or {}).get("loading")),
        }

    def _ally_within(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        target: dict[str, Any], feet: int,
    ) -> bool:
        """Whether a living, conscious ally of ``actor`` stands near ``target``.

        Pack Tactics is about the *target's* surroundings — "another enemy of the
        target within 5 feet of it" — not the attacker's, so the search is over
        the target's neighbours rather than the attacker's.
        """

        side = str(actor.get("side") or "")
        actor_id = str(actor.get("actor_id") or "")
        target_id = str(target.get("actor_id") or "")
        for candidate_id in combat.get("initiative") or []:
            if candidate_id in {actor_id, target_id}:
                continue
            try:
                candidate = self._actor_view(instance, combat, str(candidate_id))
            except CombatIntentError:  # pragma: no cover - initiative is authoritative
                continue
            if str(candidate.get("side") or "") != side or candidate["hp"] <= 0:
                continue
            if "incapacitated" in candidate["conditions"]:
                continue
            if self._distance(combat, str(candidate_id), target_id) <= feet:
                return True
        return False

    def _consume_attack_conditions(
        self, actor_id: str, target_id: str, actor: dict[str, Any], target: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Remove every condition the registry marks consumed by one attack roll.

        The old hard-coded pair lives in catalog data now; the observable
        behaviour is unchanged: whichever side a consuming declaration names,
        that holder loses the condition after the attack is resolved.
        """

        events = []
        for condition_id, entry in self.catalog.conditions.items():
            for declaration in entry.get("attack_modes") or []:
                if declaration.get("consume_on") != "attack_resolved":
                    continue
                holder_id, holder = (
                    (actor_id, actor) if declaration["side"] == "actor"
                    else (target_id, target)
                )
                if condition_id in holder["conditions"]:
                    events.append({
                        "type": "condition.removed", "target_id": holder_id,
                        "condition": condition_id,
                    })
                    break
        return events

    def _concentration_events(
        self, instance: Any, combat: dict[str, Any],
        target: dict[str, Any], damage: int, rng: Any,
    ) -> list[dict[str, Any]]:
        if not target.get("concentration") or damage <= 0:
            return []
        if self._relentless_hunter_holds(target):
            # 破坏专注的豁免根本没发生：不掷骰、不留"豁免失败"的假记录。
            # 也不能改发一条 message——``_concentration_with_dool`` 把首事件
            # 不带 ``success`` 当成失败，会为一次不需要的豁免开窗口。
            return []
        dc = max(10, damage // 2)
        modifier = int(target["saving_throws"].get("con", 0) or 0)
        concentration = target.get("concentration")
        extended = bool(
            isinstance(concentration, dict) and concentration.get("extended")
        )
        # 两个来源都是"掷两颗取高"，但它们回答的是不同问题：Extended Spell
        # 把持续时间拉长（F28），Eldritch Mind 只给维持专注的这一下优势。
        # 合并成"要不要掷第二颗"是对的，合并成"谁给的"就不行，所以来源分开记。
        advantage_sources: list[dict[str, str]] = []
        if extended:
            advantage_sources.append({"code": "extended_spell", "mode": "advantage"})
        concentration_feature = invocation_effects.concentration_advantage(
            self.features, self._feature_character(target),
        )
        if concentration_feature:
            advantage_sources.append(
                {"code": concentration_feature, "mode": "advantage"},
            )
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage_sources else None
        roll = max(first, second) if advantage_sources else first
        # 专注维持是一次体质豁免，条件声明的豁免加值骰同样生效。
        check_bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, target.get("conditions") or {}, "saving_throw", rng,
        )
        check_bonus = sum(row["roll"] for row in check_bonus_rolls)
        # 光环在受伤结算的这一刻求值：Shield、Uncanny Dodge、Divine Smite
        # 都先改完伤害再来这里，所以四个入口拿到的都是同一份位置与条件。
        aura = self._saving_throw_aura_bonus(instance, combat, target)
        total = roll + modifier + check_bonus + int(aura["bonus"])
        success = total >= dc
        events: list[dict[str, Any]] = [{
            "type": "check.resolved", "kind": "concentration", "actor_id": target["actor_id"],
            "rolls": [value for value in (first, second) if value is not None],
            "natural": roll, "modifier": modifier,
            **({"advantage_sources": advantage_sources} if advantage_sources else {}),
            "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total, "target": dc, "success": success,
            "roll_id": f"concentration:{target['actor_id']}:1",
        }]
        if not success:
            events.append({
                "type": "dnd2024.concentration.ended", "actor_id": target["actor_id"],
                "reason": "failed_concentration_save",
            })
        return events

    def _relentless_hunter_holds(self, target: dict[str, Any]) -> bool:
        """Whether this creature's current Concentration is exempt from damage.

        Both halves have to hold.  Reading only the feature would make every
        concentration spell immune, and reading only the spell would hand the
        exemption to every Ranger who never took the feature.  The spell is
        named by the declaration, so the pairing is content, not a constant
        buried here.
        """

        concentration = target.get("concentration")
        if not isinstance(concentration, dict):
            return False
        spell_ref = str(concentration.get("spell_ref") or "")
        if not spell_ref:
            return False
        declared = str(self.features.feature_value(
            self._feature_character(target),
            "relentless_hunter", "concentration_damage_exempt_spell_id", "",
        ) or "")
        return bool(declared) and spell_ref == f"spell:{declared}"

    @staticmethod
    def _position(combat: dict[str, Any], actor_id: str) -> int:
        return int(combat.get("positions", {}).get(actor_id, 0) or 0)

    def _distance(self, combat: dict[str, Any], first: str, second: str) -> int:
        return abs(self._position(combat, first) - self._position(combat, second))

    def _distance_after_move(
        self, combat: dict[str, Any], mover: str, target: str, distance: int,
    ) -> int:
        return abs(self._position(combat, mover) + distance - self._position(combat, target))
    @staticmethod
    def _initiative_rank(combat: dict[str, Any], actor_id: str) -> tuple[int, str]:
        """A stable tie-break key: initiative order first, then actor id."""

        order = [str(item) for item in (combat.get("initiative") or [])]
        try:
            return (order.index(actor_id), actor_id)
        except ValueError:
            return (len(order), actor_id)
    def attacked_this_turn(
        self, combat: dict[str, Any], actor_id: str,
    ) -> set[str]:
        """Targets this actor has already attacked in the current turn.

        "This turn" is ``(round, turn_index)``, not the actor's own turn: an
        Opportunity Attack happens on somebody else's turn and must not appear
        in the actor's own record.  Reading the pair off the log is what keeps
        that distinction instead of approximating it with "recently".
        """

        round_index = int(combat.get("round", 0) or 0)
        turn_index = int(combat.get("turn_index", 0) or 0)
        return {
            str(row.get("target_id") or "")
            for row in combat.get("attack_history") or []
            if str(row.get("actor_id") or "") == actor_id
            and int(row.get("round", 0) or 0) == round_index
            and int(row.get("turn_index", 0) or 0) == turn_index
        }

    def mark_damage_this_turn(
        self, combat: dict[str, Any], actor_id: str, target_id: str,
    ) -> bool:
        """Did this actor deal Hunter's Mark damage to ``target_id`` this turn?"""

        round_index = int(combat.get("round", 0) or 0)
        turn_index = int(combat.get("turn_index", 0) or 0)
        return any(
            str(row.get("actor_id") or "") == actor_id
            and str(row.get("target_id") or "") == target_id
            and int(row.get("round", 0) or 0) == round_index
            and int(row.get("turn_index", 0) or 0) == turn_index
            for row in combat.get("mark_damage_history") or []
        )
    def _class_resource_current(self, character: dict[str, Any], resource_id: str) -> int:
        state = (character.get("resources") or {}).get("class", {}).get(resource_id)
        if not isinstance(state, dict):
            return 0
        return int(state.get("current", 0) or 0)

    def _zone_expiry_events(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """区域到期：战斗内的回合上限、世界时钟的一分钟，或持有者失能/死亡。

        两个时钟都记：一场战斗不推进世界时钟，一次长休也不推进回合数，先到
        的那个结束效果。只记分钟的话，Sanctuary 会在整场战斗里一直在。
        """

        now = self._logical_minutes(instance)
        round_now = int(combat.get("round", 0) or 0)
        events: list[dict[str, Any]] = []
        for zone_id, zone in sorted((combat.get("zones") or {}).items()):
            if not isinstance(zone, dict):
                continue
            reason = ""
            round_expires = zone.get("expires_at_round")
            expires = zone.get("expires_at_minute")
            if (
                isinstance(round_expires, int) and not isinstance(round_expires, bool)
                and round_now >= round_expires
            ) or (
                isinstance(expires, int) and not isinstance(expires, bool) and now >= expires
            ):
                reason = "expired"
            elif zone.get("ends_on_owner_incapacitated"):
                owner_id = str(zone.get("owner_id") or "")
                if owner_id:
                    try:
                        owner = self._actor_view(instance, combat, owner_id)
                    except CombatIntentError:  # pragma: no cover - left the fight
                        owner = None
                    if owner is not None:
                        flagged = condition_flags(
                            self.catalog.conditions, owner.get("conditions") or {},
                        )["incapacitated"]
                        if flagged or owner.get("hp", 1) <= 0:
                            reason = "owner_incapacitated"
            if reason:
                events.append({
                    "type": "dnd2024.zone.removed", "zone_id": zone_id,
                    "reason": reason,
                })
        return events
    def _self_restoration_choice(
        self, actor: dict[str, Any],
    ) -> tuple[str, frozenset[str]] | None:
        """The feature that may remove one condition at this actor's turn end.

        The timing is part of the declaration, so the engine asks "is there a
        feature whose removal happens at my turn end" rather than "is this a
        monk".  Returns the feature id and the whitelist the server re-checks
        the player's choice against.
        """

        character = self._feature_character(actor)
        if not character:
            return None
        for definition in self.features.active_features_for(character):
            if definition.parameters.get("removable_at") != "own_turn_end":
                continue
            allowed = definition.parameters.get("removable_conditions")
            if isinstance(allowed, list) and allowed:
                return definition.id, frozenset(str(code) for code in allowed)
        return None
    def _require_range(
        self, combat: dict[str, Any], actor_id: str, target_id: str, maximum: int,
    ) -> None:
        distance = self._distance(combat, actor_id, target_id)
        if distance > maximum:
            raise CombatIntentError(f"target is out of range ({distance} > {maximum} feet)")

    @staticmethod
    def _conditions(
        snapshot: dict[str, Any], combat: dict[str, Any], actor_id: str,
    ) -> dict[str, Any]:
        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            return snapshot["characters"][raw_id].setdefault("conditions", {})
        if kind == "companion":
            companions = snapshot["ruleset_state"].setdefault("party", {}).setdefault("companions", {})
            companion = companions[raw_id]["ruleset_character"]
            return companion.setdefault("conditions", {})
        if kind == "enemy":
            return combat["enemies"][raw_id].setdefault("conditions", {})
        raise EventBatchError("condition target actor is invalid")

    def _wears_heavy_armor(self, snapshot: dict[str, Any], actor_id: str) -> bool:
        """Whether this actor's canonical equipment includes Heavy armor.

        Read through the equipment catalogue instead of a client flag, so a
        stale sheet cannot keep Persistent Rage alive by claiming light armor.
        """

        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            character = snapshot["characters"].get(raw_id) or {}
        elif kind == "companion":
            companions = snapshot["ruleset_state"].get("party", {}).get("companions", {})
            character = (companions.get(raw_id) or {}).get("ruleset_character") or {}
        else:
            return False
        equipment = self.features.equipment
        return any(
            equipment.armor_category(str(ref)) == "heavy"
            for ref in equipment.equipped_refs(character)
        )
    @staticmethod
    def _last_hostile_defeated(
        combat: dict[str, Any], target: dict[str, Any], damage: int,
    ) -> bool:
        if target["kind"] != "enemy" or target["hp"] - damage > 0:
            return False
        return not any(
            enemy_id != target["id"] and int(enemy.get("hp", 0) or 0) > 0
            for enemy_id, enemy in combat.get("enemies", {}).items()
        )
