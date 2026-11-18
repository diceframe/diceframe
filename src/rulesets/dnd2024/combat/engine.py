"""Authoritative, versioned D&D 2024 combat intents and EventBatch reducer."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from random import SystemRandom
from typing import Any

from src.engine.player_control import is_ai_controlled
from src.engine.modules import ruleset_runtime
from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.attunement import (
    attuned_refs,
    item_requires_attunement,
    owned_ref_counts,
)
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.character.primitives import ABILITY_IDS
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
    merge_live_character_projection,
)
from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog
from src.rulesets.dnd2024.content.encounter import expand_encounter_enemies
from src.rulesets.dnd2024.features import (
    CombatCapabilityView,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.features.resources import resource_state
from src.rulesets.dnd2024.items.activation import (
    ItemActivationError,
    validate_activation_declaration,
    validate_consumable_declaration,
)
from src.rulesets.dnd2024.items.charges import (
    ChargesError,
    charges_of,
    validate_charges_declaration,
)
from src.rulesets.dnd2024.play.contracts import EncounterAccess
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
from src.rulesets.events import EventBatchError, apply_event_batch, stable_batch_id


from .check_modes import (
    HUNTERS_MARK_REF,
    condition_check_bonus_rolls,
    condition_flags,
    effective_speed,
    mark_find_sources,
    owned_mark,
    sense_declarations,
)
from .check_windows import (
    CHECK_WINDOW_DECISION,
    CombatCheckWindowsMixin,
    DAMAGE_WINDOW_DECISION,
    LUCKY_RESOURCE,
)
from .primitives import (
    INTENT_TYPES,
    PERSISTENT_RAGE_DURATION,
    RAGE_MAX_ROUNDS,
    DIVINE_SENSE_DURATION,
    SMITE_OF_PROTECTION_CONDITION,
    TURN_UNDEAD_DURATION,
    TURNS_SCOPED_DURATIONS,
    TURN_WINDOW_CLOSED_EVENT,
    UNARMED_STRIKE_REF,
    WORLD_CLOCK_DURATIONS,
    CombatIntentError,
    actor_kind as _actor_kind,
    companion_actor as _companion_actor,
    canonical as _canonical,
    current_turn_slot as _current_turn_slot,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)
from .readiness import required_encounter_player_ids
from .reducer import CombatReducerMixin
from .resolution import CombatResolutionMixin
from .validation import CombatValidationMixin
from .view import CombatViewMixin
from .triggered_spells import CombatTriggeredSpellsMixin, HIT_SPELL_DECISION, hit_spell_decision
from .reaction_spells import (
    COUNTER_SPELL_DECISION,
    CombatReactionSpellsMixin,
    HIT_REACTION_DECISION,
)
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
    CombatValidationMixin, CombatResolutionMixin, CombatReducerMixin, CombatViewMixin, CombatTriggeredSpellsMixin,
    CombatReactionSpellsMixin, CombatCheckWindowsMixin,
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

    def _available_class_capabilities(
        self, actor: dict[str, Any], economy: dict[str, Any],
        *, include_unavailable: bool = False,
    ) -> tuple[CombatCapabilityView, ...]:
        """Feature capabilities with the actor's live conditions applied.

        A capability that requires a condition the actor does not hold, or that
        forbids one the actor currently holds, is never offered: Rage cannot be
        re-entered while raging, and Sustain Rage is meaningless outside it.
        """

        # slots=True rebuilds the class, so zero-argument super() fails on
        # Python < 3.14 (the __class__ cell still points at the old class).
        views = super(Dnd2024CombatEngine, self)._available_class_capabilities(
            actor, economy, include_unavailable=include_unavailable,
        )
        if not views:
            return views
        character = self._feature_character(actor)
        conditions = actor.get("conditions") or {}
        usable: list[CombatCapabilityView] = []
        for view in views:
            capability = self.features.declared_capability(character, view.id)
            if capability is None:
                continue
            if (
                capability.requires_condition
                and capability.requires_condition not in conditions
            ):
                continue
            if (
                capability.forbidden_condition
                and capability.forbidden_condition in conditions
            ):
                continue
            if capability.once_per_turn and economy.get(f"{capability.id}_used"):
                # 同一回合第二次使用：与被 requires/forbidden_condition 过滤
                # 的能力同等处理——不出现在可用列表，伪造请求也走同一条拒绝。
                continue
            usable.append(view)
        return tuple(usable)

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        state = self.initialize_state(instance)
        combat = state["combat"]
        version = int(state["version"])
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        viewer_is_gm = actor_id == gm_uid
        if combat.get("status") != "active":
            actions: list[dict[str, Any]] = []
            request = state.get("encounter_request")
            required_ids = set(required_encounter_player_ids(instance))
            if (
                isinstance(request, dict)
                and request.get("status") == "pending"
                and actor_id in required_ids
            ):
                ready_ids = {
                    str(item) for item in request.get("ready_player_ids") or [] if str(item)
                }
                ready = actor_id in ready_ids
                actions.append({
                    "type": "encounter.unready" if ready else "encounter.ready",
                    "label": "Cancel ready" if ready else "Ready",
                    "expected_version": version,
                })
            if actor_id != gm_uid or not self.encounter_access.can_start:
                return actions
            guided_preset_id = (
                self.encounter_access.encounter_preset_id
                if self.encounter_access.mode == "story"
                else ""
            )
            action = {
                "type": "combat.start", "label": "Start combat", "expected_version": version,
                "requires": ["encounter_preset_id"] if guided_preset_id else ["enemies"],
            }
            if guided_preset_id:
                action["encounter_preset_id"] = guided_preset_id
                action["encounter_instance_id"] = self.encounter_access.encounter_instance_id
            actions.append(action)
            return actions
        communication = [{
            "type": "combat.message", "label": "Combat message",
            "expected_version": version,
        }] if actor_id in instance.players else []
        current = self._current_actor(combat)
        # 被命中时的反应法术（护盾术）与掷后检定窗口通常出现在敌人回合：
        # 防守方/窗口主人不必是当前行动者。
        reactions = [
            item for item in combat.get("pending_decisions", [])
            if item.get("kind") in {
                HIT_REACTION_DECISION, CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION,
            } and item.get("assigned_to") == actor_id
        ]
        if reactions:
            return [*communication, {
                "type": "decision.resolve", "label": "Reaction spell",
                "expected_version": version,
                "decisions": public_pending_decisions(
                    reactions, viewer_id=actor_id, viewer_is_gm=viewer_is_gm,
                ),
            }]
        if actor_id == gm_uid:
            # 反制/机会攻击等决策指派给 GM，可能出现在非自身回合（例如
            # 敌方回合触发反制窗口）。不能因“不是当前玩家”而对真实 GM 隐藏。
            gm_pending = [
                item for item in combat.get("pending_decisions", [])
                if item.get("assigned_to") == actor_id
                and item.get("kind") not in {HIT_SPELL_DECISION, HIT_REACTION_DECISION}
            ]
            if gm_pending:
                return [*communication, {
                    "type": "decision.resolve", "label": "Resolve reaction",
                    "expected_version": version,
                    "decisions": public_pending_decisions(
                        gm_pending, viewer_id=actor_id, viewer_is_gm=viewer_is_gm,
                    ),
                }]
        if current.startswith("enemy:"):
            return communication
        requested = _player_actor(actor_id)
        if requested != current:
            return communication
        pending = [
            item for item in combat.get("pending_decisions", [])
            if item.get("assigned_to") == actor_id
        ]
        if pending:
            if pending[0].get("kind") == HIT_SPELL_DECISION:
                spells = self._available_spells(
                    instance, self._actor_view(instance, combat, current),
                    combat.get("economy", {}), pending[0],
                )
                return [*communication, {"type": "decision.resolve", "expected_version": version,
                        "decisions": public_pending_decisions(
                            pending, viewer_id=actor_id, viewer_is_gm=viewer_is_gm,
                        )}, *([{
                            "type": "cast_spell", "actor_id": current, "expected_version": version,
                            "spells": [row for row in spells if row["mode"] == "triggered_damage"],
                            "targets": self._all_targets(instance, combat),
                        }] if any(row["mode"] == "triggered_damage" for row in spells) else [])]
            return [*communication, {
                "type": "decision.resolve", "label": "Resolve reaction",
                "expected_version": version,
                "decisions": public_pending_decisions(
                    pending, viewer_id=actor_id, viewer_is_gm=viewer_is_gm,
                ),
            }]
        actor = self._actor_view(instance, combat, current)
        if actor["hp"] <= 0 and actor["kind"] == "player":
            if "stable" in actor["conditions"]:
                return [*communication, {
                    "type": "end_turn", "label": "End turn",
                    "expected_version": version, "actor_id": current,
                }]
            return [*communication, {
                "type": "death_save", "label": "Death saving throw",
                "expected_version": version, "actor_id": current,
                "lucky_advantage_available": self._window_pool_available(
                    instance, current, LUCKY_RESOURCE,
                ),
            }]
        economy = combat.get("economy", {})
        actions: list[dict[str, Any]] = []
        if int(economy.get("action", 0) or 0) > 0 or int(
            economy.get("attacks_remaining", 0) or 0
        ) > 0 or int(economy.get("surge_action", 0) or 0) > 0:
            weapons = self._available_attacks(actor)
            if weapons:
                actions.append({
                    "type": "attack", "label": "Attack", "actor_id": current,
                    "expected_version": version, "weapons": weapons,
                    "targets": self._hostile_targets(instance, combat, current),
                    "lucky_advantage_available": self._window_pool_available(
                        instance, current, LUCKY_RESOURCE,
                    ),
                    **self._attack_state_options(actor, combat),
                })
            actions.extend([
                {"type": "dash", "label": "Dash", "actor_id": current, "expected_version": version},
                {"type": "dodge", "label": "Dodge", "actor_id": current, "expected_version": version},
                {"type": "disengage", "label": "Disengage", "actor_id": current, "expected_version": version},
                {"type": "hide", "label": "Hide", "actor_id": current, "expected_version": version},
            ])
            adjacent_downed = [
                target for target in self._all_targets(instance, combat)
                if target["kind"] == actor["kind"] and target["hp"] <= 0
                and self._distance(combat, current, target["actor_id"]) <= 5
            ]
            if adjacent_downed:
                actions.append({
                    "type": "stabilize", "label": "Stabilize", "actor_id": current,
                    "expected_version": version, "targets": adjacent_downed,
                })
        spells = self._available_spells(
            instance, actor, economy, hit_spell_decision(combat, current),
        )
        # 当前标记与"能不能转移"由服务端给出：客户端不该自己从专注里推断
        # "这个目标归零了没有"。这条入口不能跟着 ``spells`` 一起消失：免费
        # 次数与法术位都花光之后，转移标记仍是一次合法提交，而过去只有在
        # 还有法术可放时才投影这一行（S0-F18 §4.1.7）。
        mark = self._hunters_mark_state(instance, combat, actor)
        if spells or bool(mark and mark.get("can_retarget")):
            entry: dict[str, Any] = {
                "type": "cast_spell", "label": "Cast a spell", "actor_id": current,
                "expected_version": version, "spells": spells,
                "targets": self._all_targets(instance, combat),
            }
            if mark is not None:
                entry["hunters_mark"] = mark
            senses = sense_declarations(self, actor)
            if senses:
                entry["senses"] = senses
            actions.append(entry)
        items = self._available_item_activations(instance, combat, actor, economy)
        if items:
            actions.append({
                "type": "activate_item", "label": "Use an item", "actor_id": current,
                "expected_version": version, "items": items,
                "targets": self._party_targets(instance, combat),
            })
        # 职业特性提供的战斗能力（v1: Monk 的附赠徒手打击 / 疾风连击 /
        # 坚守防御 / 疾风步）。服务端只暴露当前真实可用的 capability，因此
        # 不会出现空的「附赠动作」入口；按钮自带成本与目标要求。
        for capability in self._available_class_capabilities(actor, economy):
            entry = capability.to_dict()
            entry.update({
                "type": "class_capability",
                "actor_id": current,
                "expected_version": version,
            })
            if capability.requires_hostile_target:
                entry["targets"] = self._hostile_targets(instance, combat, current)
            actions.append(entry)
        if int(economy.get("movement", 0) or 0) > 0:
            actions.append({
                "type": "move", "label": "Move", "actor_id": current,
                "expected_version": version, "movement_remaining": economy["movement"],
            })
        end_turn_entry: dict[str, Any] = {
            "type": "end_turn", "label": "End turn", "actor_id": current,
            "expected_version": version,
        }
        # 回合末移除是一个"可选"越项：面板必须能看出可移除哪几项，否则
        # 玩家只能靠猜该传什么值；候选来自声明白名单与当前实有条件，服务端
        # 在 intent 边界仍会重验同一份事实。
        choice = self._self_restoration_choice(actor)
        if choice is not None:
            _feature_id, removable = choice
            options = sorted(
                code for code in (actor.get("conditions") or {}) if code in removable
            )
            if options:
                end_turn_entry["self_restoration_options"] = options
        actions.append(end_turn_entry)
        if actor_id == gm_uid:
            actions.append({
                "type": "combat.end", "label": "End combat", "expected_version": version,
            })
        return [*communication, *actions]

    def _hunters_mark_state(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The current mark this actor owns, as the server sees it.

        ``can_retarget`` is only "the creature it names is down": the range,
        the visibility and the Bonus Action are re-proved at the intent
        boundary, so a stale panel can never talk its way into a move.
        """

        mark = owned_mark(actor)
        if not mark:
            return None
        target_id = str(mark.get("mark_target_id") or "")
        target = self._actor_view(instance, combat, target_id) if target_id else {}
        effect = self.catalog.spell_effects.get(
            HUNTERS_MARK_REF.removeprefix("spell:")
        ) or {}
        formula = str(effect.get("mark_damage") or "")
        sides = formula.rsplit("d", 1)[-1] if "d" in formula else ""
        upgrade = int(self.features.feature_value(
            self._feature_character(actor), "foe_slayer", "mark_damage_die", 0,
        ) or 0)
        return {
            "spell_ref": str(mark.get("spell_ref") or ""),
            "target_id": target_id,
            "target_hp": int(target.get("hp", 0) or 0),
            "damage_die": f"1d{upgrade or sides}",
            "expires_hours": mark.get("mark_expires_hours"),
            "can_retarget": bool(target_id) and int(target.get("hp", 0) or 0) <= 0,
        }

    def hunters_mark_find_advantage(
        self, instance: Any, actor_id: str, skill_id: str, target_id: str,
    ) -> list[dict[str, Any]]:
        """Advantage sources for a Wisdom check made to find a creature.

        The combat engine does not roll skill checks, so this is the authority
        an exploration-side check consumes: it answers with the same sourced
        list the attack matrix uses, which keeps "who is my quarry" in exactly
        one place instead of letting each caller re-read concentration.
        """

        state = self.initialize_state(instance)
        combat = state["combat"]
        actor = self._actor_view(instance, combat, f"player:{actor_id}")
        return mark_find_sources(self, actor, skill_id, target_id)

    def _attack_state_options(
        self, actor: dict[str, Any], combat: dict[str, Any],
    ) -> dict[str, Any]:
        """What this actor may declare before an attack roll, per the server.

        The client echoes these keys back as intent fields; the same facts are
        re-derived in validation, so a stale panel cannot submit a declaration
        the character no longer qualifies for.  Absent keys mean "this actor
        owns no such feature at all", which is why a plain Fighter's attack row
        carries neither.
        """

        if actor["kind"] not in {"player", "companion"}:
            return {}
        character = self._feature_character(actor)
        actor_id = str(actor["actor_id"])
        options: dict[str, Any] = {}

        def parameter(feature_id: str, key: str) -> Any:
            return self.features.feature_value(character, feature_id, key, None)

        condition = str(parameter("reckless_attack", "self_condition") or "")
        if condition:
            record = (combat.get("attack_rolls") or {}).get(actor_id)
            same_turn = (
                isinstance(record, dict)
                and int(record.get("round", -1)) == int(combat.get("round", 0))
                and int(record.get("turn_index", -1)) == int(combat.get("turn_index", 0))
            )
            options["reckless_attack"] = {
                "available": (
                    condition not in (actor.get("conditions") or {})
                    and not same_turn
                ),
                "condition": condition,
                "ability": str(parameter("reckless_attack", "ability") or ""),
            }

        if self.features.has_feature(character, "brutal_strike"):
            allowed = [str(value) for value in parameter(
                "brutal_strike", "effect_options",
            ) or []]
            if self.features.has_feature(character, "improved_brutal_strike"):
                allowed.extend(
                    str(value) for value in parameter(
                        "improved_brutal_strike", "additional_effect_options",
                    ) or []
                )
            class_state = self.features.class_state(character)
            level = int(class_state[1]) if class_state else 0
            cap = int(parameter("brutal_strike", "effects_per_use") or 0)
            if level >= int(parameter("brutal_strike", "upgrade_level") or 0):
                cap = int(
                    parameter("brutal_strike", "effects_per_use_upgrade") or cap
                )
            used = self._feature_used_this_turn(combat, actor_id, "brutal_strike")
            reckless_ready = bool(
                str(parameter("reckless_attack", "self_condition") or "")
                in (actor.get("conditions") or {})
                or (options.get("reckless_attack") or {}).get("available")
            )
            options["brutal_strike"] = {
                "available": bool(allowed and cap and reckless_ready and not used),
                "effects": allowed,
                "count": cap,
            }

        mark_kind = str(parameter("studied_attacks", "mark_kind") or "")
        if mark_kind:
            marks = (
                ((combat.get("attack_marks") or {}).get(mark_kind) or {})
                .get(actor_id) or {}
            )
            options["studied_attacks"] = {
                "mark_kind": mark_kind,
                "targets": sorted(str(key) for key in marks),
            }
        return options

    def _available_item_activations(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        economy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Project carried items this actor may legally activate right now.

        Only player seats expose a live backpack; companion item authority is
        intentionally left at the D3-11 boundary.  Illegal declarations,
        ownership, attunement, charge and duplicate problems are omitted
        instead of being shown as submittable actions.
        """

        if actor["kind"] != "player":
            return []
        payload = self._activation_payload(instance, actor)
        canonical = self._canonical_actor(instance, actor)
        owned = owned_ref_counts(payload)
        attuned = attuned_refs(canonical)
        items: list[dict[str, Any]] = []
        for item_ref, count in sorted(owned.items()):
            if count < 1:
                continue
            item = self.bundle.get("item", item_ref.removeprefix("item:"))
            if not isinstance(item, dict):
                continue
            try:
                declaration = validate_activation_declaration(item)
                validate_consumable_declaration(item)
                maximum = validate_charges_declaration(item)
            except (ItemActivationError, ChargesError):
                continue
            if declaration is None:
                continue
            cost = declaration["cost"]
            if int(economy.get(cost, 0) or 0) < 1:
                continue
            if (
                item_requires_attunement(self.bundle, item_ref)
                and item_ref not in attuned
            ):
                continue
            charge_cost = declaration.get("charge_cost")
            if maximum is not None:
                if charge_cost is None or count > 1:
                    continue
                current = charges_of(canonical, item_ref)
                if current is None or current < int(charge_cost):
                    continue
            elif charge_cost is not None:
                continue
            items.append({
                "item_ref": item_ref,
                "name": str(item.get("name") or item_ref),
                "cost": cost,
                "target": declaration["target"],
            })
        return items

    def _party_targets(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # 0 HP 的倒地队友仍是合法治疗目标；只有 dead 才由校验层拒绝。
        return [
            target for target in self._all_targets(instance, combat)
            if target.get("side") == "party" and not target.get("is_dead")
        ]

    def next_automatic_intent(self, instance: Any) -> dict[str, Any] | None:
        """Choose one bounded server-owned automatic operation.

        The method declares an intent only; validation, dice, events, and state
        mutation still pass through the same authoritative pipeline as player
        actions.

        Enemy and companion actors are server-owned.  An AI-hosted player seat
        (``player:<uid>`` whose control mode is ``ai`` at this moment) is served
        by the same deterministic ladder, under the same GM/server automation
        authority.  A ``human`` or ``unclaimed`` player seat still yields no
        automatic intent here: the server never plays a human's character.
        """

        state = self.initialize_state(instance)
        combat = state["combat"]
        if combat.get("status") != "active":
            return None
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        version = int(state.get("version", 0) or 0)
        pending = list(combat.get("pending_decisions") or [])
        if pending:
            decision = pending[0]
            if decision.get("kind") == HIT_SPELL_DECISION:
                return self._automatic_hit_spell(instance, combat, decision, version)
            if decision.get("kind") == HIT_REACTION_DECISION:
                return self._automatic_hit_reaction(instance, combat, decision, version)
            if decision.get("kind") == CHECK_WINDOW_DECISION:
                # A window owned by a human seat yields no automatic intent:
                # the server never spends a player's resource for them.
                return self._automatic_check_window(instance, combat, decision, version)
            if decision.get("kind") == DAMAGE_WINDOW_DECISION:
                return self._automatic_damage_window(instance, combat, decision, version)
            if decision.get("kind") == COUNTER_SPELL_DECISION:
                # 反制是 GM 的人类判断；但 GM 自己在队伍里有角色时（单人局里
                # GM 就是施法者），由他替敌人决定等于施法者决定自己是否被反制。
                # 这时与借机攻击一致，由服务器替敌人确定性地选择反制。
                if gm_uid not in instance.players:
                    return None
                return {
                    "intent_id": f"auto:decision:{version}:{decision.get('decision_id', '')}",
                    "type": "decision.resolve",
                    "expected_version": version,
                    "submitted_by": gm_uid,
                    "decision_id": str(decision.get("decision_id") or ""),
                    "option": "counter",
                }
            threats = [str(item) for item in decision.get("threat_actor_ids") or []]
            option = (
                "resolve"
                if "resolve" in decision.get("options", [])
                and any(actor_id.startswith("enemy:") for actor_id in threats)
                else "decline"
            )
            return {
                "intent_id": f"auto:decision:{version}:{decision.get('decision_id', '')}",
                "type": "decision.resolve",
                "expected_version": version,
                "submitted_by": gm_uid,
                "decision_id": str(decision.get("decision_id") or ""),
                "option": option,
            }
        actor_id = self._current_actor(combat)
        actor_kind, actor_raw_id = _actor_kind(actor_id)
        if actor_kind not in {"enemy", "companion", "player"}:
            return None
        base = {
            "intent_id": (
                f"auto:{actor_kind}:{version}:{actor_id}"
            ),
            "expected_version": version,
            "submitted_by": gm_uid,
            "actor_id": actor_id,
        }
        end_turn = {**base, "intent_id": f"{base['intent_id']}:end", "type": "end_turn"}
        if actor_kind == "player":
            # AI 托管席位才由服务器代打；真人 / 未认领席位的回合仍然等着人来操作。
            if not is_ai_controlled(instance, actor_raw_id):
                return None
            return self._ai_player_automatic_intent(instance, combat, base)
        actor = self._actor_view(instance, combat, actor_id)
        flags = condition_flags(self.catalog.conditions, actor["conditions"])
        if flags["incapacitated"] and int(actor.get("hp", 0) or 0) > 0:
            # 失能单位不能执行动作，但它的回合照常存在（回合结束的重复豁免
            # 也靠 end_turn 触发）；直接结束回合，否则校验拒绝会卡住整条自动化。
            return end_turn
        if actor_kind == "companion":
            # AI 队友第一阶段：确定性优先级（治疗濒危 → 攻击 → 移动 → Dodge →
            # End Turn），由 GM/server automation authority 提交，走同一权威链。
            return self._allied_automatic_intent(instance, combat, base)
        targets = [
            target for target in self._hostile_targets(instance, combat, actor_id)
            if int(target.get("hp", 0) or 0) > 0
        ]
        targets.sort(key=lambda target: (
            self._distance(combat, actor_id, str(target["actor_id"])),
            int(target.get("hp", 0) or 0),
            str(target["actor_id"]),
        ))
        economy = combat.get("economy") or {}
        can_act = (
            int(economy.get("action", 0) or 0) > 0
            or int(economy.get("attacks_remaining", 0) or 0) > 0
            or int(economy.get("surge_action", 0) or 0) > 0
        )
        if can_act and targets:
            target = targets[0]
            distance = self._distance(combat, actor_id, str(target["actor_id"]))
            attacks = [
                attack for attack in actor.get("attacks") or []
                if distance <= int(attack.get("long_range") or attack.get("range", 5) or 5)
            ]
            attacks.sort(key=lambda attack: (
                distance > int(attack.get("range", 5) or 5),
                -int(attack.get("attack_bonus", 0) or 0),
                str(attack.get("id") or ""),
            ))
            if attacks:
                return {
                    **base,
                    "intent_id": f"{base['intent_id']}:attack",
                    "type": "attack",
                    "target_id": str(target["actor_id"]),
                    "attack_id": str(attacks[0]["id"]),
                }
            movement = int(economy.get("movement", 0) or 0)
            if movement > 0 and not flags["speed_zero"]:
                actor_position = self._position(combat, actor_id)
                target_position = self._position(combat, str(target["actor_id"]))
                desired_range = max(
                    (int(item.get("range", 5) or 5) for item in actor.get("attacks") or []),
                    default=5,
                )
                needed = max(0, abs(target_position - actor_position) - desired_range)
                distance_to_move = min(movement, needed)
                if distance_to_move > 0:
                    signed = distance_to_move if target_position > actor_position else -distance_to_move
                    return {
                        **base,
                        "intent_id": f"{base['intent_id']}:move",
                        "type": "move",
                        "distance": signed,
                    }
            return {
                **base,
                "intent_id": f"{base['intent_id']}:dodge",
                "type": "dodge",
            }
        return {
            **base,
            "intent_id": f"{base['intent_id']}:end",
            "type": "end_turn",
        }

    def _ai_player_automatic_intent(
        self, instance: Any, combat: dict[str, Any], base: dict[str, Any],
    ) -> dict[str, Any]:
        """AI 托管玩家席位的确定性战斗回合（不接 LLM，读自己的角色卡）。

        与 companion 共用同一条 ``_allied_automatic_intent`` 阶梯；唯一真实差异是
        0 HP 的处理：companion 不做死亡豁免，玩家角色必须做，否则战斗会卡在这个
        席位上。候选意图先过一次真实校验，不合法就退回合法的 ``end_turn``，保证
        托管席位的回合一定会结束。
        """
        actor = self._actor_view(instance, combat, base["actor_id"])
        if actor["hp"] <= 0:
            if "stable" in actor["conditions"]:
                return {**base, "intent_id": f"{base['intent_id']}:end", "type": "end_turn"}
            return {
                **base,
                "intent_id": f"{base['intent_id']}:death_save",
                "type": "death_save",
            }
        candidate = self._allied_automatic_intent(instance, combat, base)
        if candidate.get("type") == "end_turn":
            return candidate
        try:
            self._validate(instance, candidate)
        except CombatIntentError:
            # 阶梯给出的候选在当前状态下不合法（例如只剩 bonus action 才能治疗）：
            # 不抛出、不空转，退回合法 end_turn，让回合继续推进。
            return {**base, "intent_id": f"{base['intent_id']}:end", "type": "end_turn"}
        return candidate

    def _allied_automatic_intent(
        self, instance: Any, combat: dict[str, Any], base: dict[str, Any],
    ) -> dict[str, Any]:
        """AI 己方角色（companion 与 AI 托管 PC）第一阶段确定性自动行动。

        优先级：治疗濒危己方 → 攻击最近敌对目标 → 向目标移动 → Dodge →
        End Turn。只看这个 actor 自己的角色卡（装备/法术/HP/速度），intent 仍走
        validate/resolve/apply 同一权威链。
        """
        actor_id = base["actor_id"]
        actor = self._actor_view(instance, combat, actor_id)
        economy = combat.get("economy") or {}
        can_act = (
            int(economy.get("action", 0) or 0) > 0
            or int(economy.get("attacks_remaining", 0) or 0) > 0
            or int(economy.get("surge_action", 0) or 0) > 0
        )
        allies = [
            target for target in self._all_targets(instance, combat)
            if target["side"] == "party" and target["actor_id"] != actor_id
        ]
        if can_act:
            downed = [target for target in allies if int(target.get("hp", 0) or 0) <= 0]
            wounded = [
                target for target in allies
                if int(target.get("max_hp", 0) or 0) > 0
                and 0 < int(target.get("hp", 0) or 0) / int(target["max_hp"]) <= 0.4
            ]
            candidates = downed or wounded
            healing = sorted(self._companion_healing_options(actor))
            if candidates and healing:
                spell_ref, slot_level = healing[0]
                return {
                    **base,
                    "intent_id": f"{base['intent_id']}:cast",
                    "type": "cast_spell", "spell_ref": spell_ref,
                    "slot_level": slot_level,
                    "target_ids": [str(candidates[0]["actor_id"])],
                }
            targets = [
                target for target in self._hostile_targets(instance, combat, actor_id)
                if int(target.get("hp", 0) or 0) > 0
            ]
            targets.sort(key=lambda target: (
                self._distance(combat, actor_id, str(target["actor_id"])),
                int(target.get("hp", 0) or 0),
                str(target["actor_id"]),
            ))
            if targets:
                target = targets[0]
                distance = self._distance(combat, actor_id, str(target["actor_id"]))
                weapons = [
                    weapon for weapon in self._available_attacks(actor)
                    if distance <= int(weapon.get("long_range") or weapon.get("range", 5) or 5)
                ]
                weapons.sort(key=lambda weapon: (
                    distance > int(weapon.get("range", 5) or 5),
                    str(weapon.get("weapon_ref") or weapon.get("id") or ""),
                ))
                if weapons:
                    return {
                        **base,
                        "intent_id": f"{base['intent_id']}:attack",
                        "type": "attack",
                        "target_id": str(target["actor_id"]),
                        "weapon_ref": str(weapons[0].get("weapon_ref") or ""),
                    }
                movement = int(economy.get("movement", 0) or 0)
                if movement > 0:
                    actor_position = self._position(combat, actor_id)
                    target_position = self._position(combat, str(target["actor_id"]))
                    desired_range = max(
                        (
                            int(weapon.get("range", 5) or 5)
                            for weapon in self._available_attacks(actor)
                        ),
                        default=5,
                    )
                    needed = max(0, abs(target_position - actor_position) - desired_range)
                    distance_to_move = min(movement, needed)
                    if distance_to_move > 0:
                        signed = (
                            distance_to_move
                            if target_position > actor_position else -distance_to_move
                        )
                        return {
                            **base,
                            "intent_id": f"{base['intent_id']}:move",
                            "type": "move", "distance": signed,
                        }
            return {**base, "intent_id": f"{base['intent_id']}:dodge", "type": "dodge"}
        return {**base, "intent_id": f"{base['intent_id']}:end", "type": "end_turn"}

    def _companion_healing_options(self, actor: dict[str, Any]) -> list[tuple[str, int]]:
        """companion 当前可用的确定性治疗法术（spell_ref, slot_level）。"""
        options: list[tuple[str, int]] = []
        for spell_ref in actor.get("spell_refs") or []:
            spell = self.spells.get(spell_ref)
            effect = self.catalog.spell_effects.get(str(spell_ref).removeprefix("spell:"))
            if spell is None or effect is None or str(effect.get("mode")) != "healing":
                continue
            level = int(spell.get("level") or 0)
            if level == 0:
                options.append((spell_ref, 0))
                continue
            slot_level = next(
                (
                    candidate for candidate in sorted(
                        int(key) for key in actor.get("slots") or {}
                    )
                    if int((actor.get("slots") or {}).get(str(candidate), 0) or 0) > 0
                    and candidate >= level
                ),
                None,
            )
            if slot_level is not None:
                options.append((spell_ref, slot_level))
        return options

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
            events.extend(self._attack_events(
                instance, combat, intent, rng, actor_view=actor_view,
            ))
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
            events.extend(self._hide_events(instance, combat, intent, rng))
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
        # 世界时钟驱动的到期：一场在一分钟内结束的战斗会把效果留在身上，
        # 只要之后还有任何战斗意图或时钟越过那一分钟就会被清掉。end_turn
        # 由回合推进处统一查两个上限，这里不能重复发同一条移除事件。
        if intent_type != "end_turn" and combat.get("initiative"):
            events.extend(self._superior_defense_expiry_events(instance, combat))
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

    def _incapacitation_cleanup_events(
        self, instance: Any, combat: dict[str, Any], events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """End effects whose own text says the Incapacitated condition ends them.

        Superior Defense lasts "1 minute or until you have the Incapacitated
        condition", and the condition can arrive from a Stunning Strike, a spell
        or the environment.  Resolving it where the condition is *gained* would
        mean a branch per source; resolving it once per batch from the merged
        condition set means a new source needs no change here at all.

        The metadata flag is on the effect, not on the class, so nothing in this
        method names a feature.
        """

        applied: dict[str, set[str]] = {}
        for event in events:
            if event.get("type") != "condition.applied":
                continue
            target_id = str(event.get("target_id") or "")
            if target_id:
                applied.setdefault(target_id, set()).add(
                    str(event.get("condition") or "")
                )
        cleanup: list[dict[str, Any]] = []
        for actor_id, gained in applied.items():
            try:
                view = self._actor_view(instance, combat, actor_id)
            except CombatIntentError:  # pragma: no cover - target came from an event
                continue
            conditions = dict(view.get("conditions") or {})
            # 本批的新条件还没落地，必须并进来才看得到"刚刚被失能"这件事。
            for condition_id in gained:
                conditions.setdefault(condition_id, {})
            if not condition_flags(self.catalog.conditions, conditions)["incapacitated"]:
                continue
            for condition_id, meta in (view.get("conditions") or {}).items():
                if condition_id in gained:
                    continue
                if not isinstance(meta, dict) or not meta.get("ends_on_incapacitated"):
                    continue
                cleanup.append({
                    "type": "condition.removed", "target_id": actor_id,
                    "condition": condition_id, "reason": "incapacitated",
                })
        return cleanup

    def _source_incapacitation_events(
        self, instance: Any, combat: dict[str, Any], events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Effects that end when their *source* becomes Incapacitated or dies.

        Turn Undead is written that way: "This effect ends early on the
        creature if it takes any damage, if you have the Incapacitated
        condition, or if you die".  The ender is therefore not the bearer's
        own state but the caster's, and the bearer may be any actor on the
        table -- so the sweep runs once per batch over every condition that
        names a source, instead of a branch inside the caster's damage path.
        """

        lost: set[str] = set()
        for event in events:
            if event.get("type") != "condition.applied":
                continue
            target_id = str(event.get("target_id") or "")
            if target_id:
                lost.add(target_id)
        cleanup: list[dict[str, Any]] = []
        for source_id in lost:
            try:
                view = self._actor_view(instance, combat, source_id)
            except CombatIntentError:  # pragma: no cover - source came from an event
                continue
            conditions = dict(view.get("conditions") or {})
            for event in events:
                if (
                    event.get("type") == "condition.applied"
                    and str(event.get("target_id") or "") == source_id
                ):
                    conditions.setdefault(str(event.get("condition") or ""), {})
            flags = condition_flags(self.catalog.conditions, conditions)
            if not (flags["incapacitated"] or "dead" in conditions):
                continue
            for other_id in self._combat_actor_ids(instance, combat):
                if other_id == source_id:
                    continue
                try:
                    other = self._actor_view(instance, combat, other_id)
                except CombatIntentError:  # pragma: no cover - actor came from combat
                    continue
                for condition_id, meta in (other.get("conditions") or {}).items():
                    if not isinstance(meta, dict):
                        continue
                    if not meta.get("ends_on_source_incapacitated"):
                        continue
                    if str(meta.get("source_actor_id") or "") != source_id:
                        continue
                    cleanup.append({
                        "type": "condition.removed", "target_id": other_id,
                        "condition": condition_id, "reason": "source incapacitated",
                    })
        return cleanup

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

    def _timed_condition_expiry_events(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """End effects that run on the world clock rather than on turns.

        Divine Sense and the Sacred Weapon bond both run for ten minutes and
        are not tied to a fight; a combat that ends before the clock passes
        must leave them in place, and a clock that passes outside combat must
        still end them.  Every intent sweeps the same limit, so no second timer
        exists to disagree with the ledger.
        """

        now = self._logical_minutes(instance)
        events: list[dict[str, Any]] = []
        for actor_id in self._combat_actor_ids(instance, combat):
            try:
                view = self._actor_view(instance, combat, actor_id)
            except CombatIntentError:  # pragma: no cover - actor came from combat
                continue
            for condition_id, meta in (view.get("conditions") or {}).items():
                if not isinstance(meta, dict):
                    continue
                if meta.get("duration") not in WORLD_CLOCK_DURATIONS:
                    continue
                expires_at = meta.get("expires_at_minute")
                if isinstance(expires_at, bool) or not isinstance(expires_at, int):
                    continue
                if now >= expires_at:
                    events.append({
                        "type": "condition.removed", "target_id": actor_id,
                        "condition": condition_id, "reason": "ten minutes passed",
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
        modifier = int(canonical.get("derived", {}).get("initiative", 0) or 0)
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
                if not capability.cost.turn_start:
                    continue
                affordable = all(
                    int(resource_state(character, cost.resource_id).get("current", 0) or 0)
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
        weapon: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "dnd2024.attack.spent", "actor_id": actor["actor_id"],
            "resource": resource or self._spend_action_resource(combat),
            "attacks_per_action": self._attacks_per_action(actor),
            # 归约需要知道"这一击用的是不是 Light 武器"，才能回答下一问：
            # "本回合还允许 Light 追加攻击吗"。
            "light_weapon": bool((weapon or {}).get("light")),
            # Loading 的节流单位是"一次 action/Bonus/Reaction"，归约按窗口记账。
            "loading_weapon": bool((weapon or {}).get("loading")),
        }

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
        roll = int(rng.randint(1, 20))
        modifier = int(target["saving_throws"].get("con", 0) or 0)
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
            "rolls": [roll], "natural": roll, "modifier": modifier,
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

    def _projected_auras(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Every base aura a living, conscious party member projects right now.

        One row per (holder, base aura).  ``range_ft`` already folds in that
        holder's own Aura Expansion, so a level-18 paladin widens only their own
        aura and a level-6 ally beside them keeps 10 feet -- the expansion is a
        property of who owns it, never a global maximum.

        The emanation is off as a whole whenever the holder cannot sustain it:
        any condition that implies Incapacitated disables it, and so does being
        at 0 HP or carrying the ``dead`` condition.  That second half cannot be
        left to the condition registry alone -- ``dead`` is not a catalog
        condition yet, and the reducer removes ``unconscious`` the moment it
        marks a creature dead, so a corpse would otherwise keep radiating a
        bonus to its allies.  Enemies never project a class aura, which is why
        the roster scan starts from ``side == "party"`` rather than from every
        combatant.
        """

        rows: list[dict[str, Any]] = []
        for ally in self._all_targets(instance, combat):
            if str(ally.get("side") or "") != "party":
                continue
            holder_id = str(ally["actor_id"])
            actor = self._actor_view(instance, combat, holder_id)
            conditions = actor.get("conditions") or {}
            flags = condition_flags(self.catalog.conditions, conditions)
            if (
                flags.get("incapacitated")
                or "dead" in conditions
                or int(actor.get("hp", 1)) <= 0
            ):
                continue
            character = self._feature_character(actor)
            owned = self.features.active_features_for(character)
            for definition in owned:
                declared = definition.parameters.get("aura_range_ft")
                if (
                    isinstance(declared, bool) or not isinstance(declared, int)
                    or declared < 1
                ):
                    continue
                range_ft = declared
                for extension in owned:
                    if extension.parameters.get("aura_expands_feature_id") != definition.id:
                        continue
                    override = extension.parameters.get("aura_range_override_ft")
                    if (
                        isinstance(override, int) and not isinstance(override, bool)
                        and override >= 1
                    ):
                        range_ft = max(range_ft, override)
                rows.append({
                    "holder_id": holder_id,
                    "definition": definition,
                    "range_ft": range_ft,
                    "character": character,
                    "owned": owned,
                })
        return rows

    @staticmethod
    def _initiative_rank(combat: dict[str, Any], actor_id: str) -> tuple[int, str]:
        """A stable tie-break key: initiative order first, then actor id."""

        order = [str(item) for item in (combat.get("initiative") or [])]
        try:
            return (order.index(actor_id), actor_id)
        except ValueError:
            return (len(order), actor_id)

    def _saving_throw_aura_bonus(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
    ) -> dict[str, Any]:
        """The single best saving-throw aura bonus covering the target.

        RAW lets a creature inside two auras choose one.  Settling that on the
        sheet would need a prompt the passive path cannot show, so the highest
        bonus wins and equal bonuses break on initiative order and then actor
        id.  Bonuses never stack: ``sources`` names the winner alone.

        An uncovered target gets ``{"bonus": 0, "sources": []}`` so every caller
        can emit ``aura_bonus``/``aura_sources`` unconditionally and keep the
        existing event shape for characters who never met a paladin.
        """

        target_id = str(target.get("actor_id") or "")
        if not target_id or str(target.get("side") or "") != "party":
            return {"bonus": 0, "sources": []}
        best: dict[str, Any] | None = None
        best_rank: tuple[int, str] = (0, "")
        for row in self._projected_auras(instance, combat):
            definition = row["definition"]
            ability = definition.parameters.get("aura_save_bonus_ability")
            if not isinstance(ability, str) or ability not in ABILITY_IDS:
                continue
            if self._distance(combat, row["holder_id"], target_id) > row["range_ft"]:
                continue
            floor = definition.parameters.get("aura_save_bonus_minimum")
            minimum = floor if isinstance(floor, int) and not isinstance(floor, bool) else 1
            score = (row["character"].get("abilities") or {}).get(ability)
            modifier = ability_modifier(int(score)) if isinstance(score, int) else 0
            bonus = max(minimum, modifier)
            rank = self._initiative_rank(combat, row["holder_id"])
            if best is None or bonus > best["bonus"] or (
                bonus == best["bonus"] and rank < best_rank
            ):
                best = {
                    "bonus": bonus,
                    "feature_id": definition.id,
                    "owner": definition.owner,
                    "source_actor_id": row["holder_id"],
                    "range": row["range_ft"],
                    "ability": ability,
                    "ability_modifier": modifier,
                }
                best_rank = rank
        if best is None:
            return {"bonus": 0, "sources": []}
        return {"bonus": best.pop("bonus"), "sources": [best]}

    def _cover_record(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
    ) -> dict[str, Any]:
        """The best cover this creature has right now: Smite of Protection.

        Cover is positional and momentary, so it is answered by a query instead
        of being written onto anyone.  The condition lives on the Paladin, and
        the reach comes from their own Aura of Protection -- which means an ally
        who walks out loses it and one who walks back in (while it is still
        running) gets it again, without a second copy of the radius.

        Bonuses never stack, with each other or with a higher cover that a
        future slice may add: the best single source wins, and several Paladins
        holding the same benefit still give out exactly one +2.
        """

        empty = {"armor_class_bonus": 0, "dex_save_bonus": 0, "sources": []}
        target_id = str(target.get("actor_id") or "")
        if not target_id or str(target.get("side") or "") != "party":
            return empty
        best: dict[str, Any] | None = None
        for row in self._projected_auras(instance, combat):
            holder_id = str(row["holder_id"])
            holder = self._actor_view(instance, combat, holder_id)
            meta = (holder.get("conditions") or {}).get(SMITE_OF_PROTECTION_CONDITION)
            if not isinstance(meta, dict):
                continue
            # 掩蔽借的是条件自己声明的那道灵光：既不复制半径，也不默认套用
            # 任何一道光环，光环换名或换属性时这里跟着走。
            source_aura = str(meta.get("cover_source_feature_id") or "aura_of_protection")
            if str(row["definition"].id) != source_aura:
                continue
            if self._distance(combat, holder_id, target_id) > int(row["range_ft"]):
                continue
            armor_class_bonus = int(meta.get("cover_armor_class_bonus", 2) or 0)
            dex_save_bonus = int(meta.get("cover_dex_save_bonus", 2) or 0)
            if armor_class_bonus < 1 and dex_save_bonus < 1:
                continue
            rank = self._initiative_rank(combat, holder_id)
            if best is None or (armor_class_bonus, dex_save_bonus) > (
                best["armor_class_bonus"], best["dex_save_bonus"]
            ) or (
                (armor_class_bonus, dex_save_bonus)
                == (best["armor_class_bonus"], best["dex_save_bonus"])
                and rank < best["_rank"]
            ):
                best = {
                    "armor_class_bonus": armor_class_bonus,
                    "dex_save_bonus": dex_save_bonus,
                    "sources": [{
                        "feature_id": "smite_of_protection",
                        "source_actor_id": holder_id,
                        "range_ft": int(row["range_ft"]),
                        "source_spell_ref": str(meta.get("source_spell_ref") or ""),
                    }],
                    "_rank": rank,
                }
        if best is None:
            return empty
        best.pop("_rank", None)
        return best

    def _cover_save_bonus(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        ability: str,
    ) -> dict[str, Any]:
        """Cover's contribution to one saving throw, which is Dex-only.

        Asked separately from the AC half so a caller never has to remember
        which ability cover helps: anything but Dexterity answers zero and the
        event stays shaped like every other save.
        """

        if str(ability or "") != "dex":
            return {"bonus": 0, "sources": []}
        cover = self._cover_record(instance, combat, target)
        return {
            "bonus": int(cover["dex_save_bonus"]),
            "sources": cover["sources"],
        }

    def _passive_condition_immunity(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        condition_id: str,
    ) -> dict[str, str] | None:
        """The feature the target owns that makes ``condition_id`` a no-op.

        Unlike an aura this needs no projection and no distance: the feature is
        on the target, so the only question is whether its gate is met.  The
        gate is a condition id (Mindless Rage is only on while Rage is on), and
        it is read from the *effective* condition set -- a condition that an
        aura already suppressed cannot switch a second immunity on.
        """

        wanted = str(condition_id or "")
        target_id = str(target.get("actor_id") or "")
        if not wanted or not target_id or str(target.get("side") or "") != "party":
            return None
        character = self._feature_character(target)
        if not character:
            return None
        conditions = self._effective_conditions(instance, combat, target)
        for definition in self.features.active_features_for(character):
            declared = definition.parameters.get("immune_conditions")
            if not isinstance(declared, list) or wanted not in declared:
                continue
            gate = definition.parameters.get("immune_while_condition")
            if isinstance(gate, str) and gate and gate not in conditions:
                continue
            return {
                "feature_id": definition.id,
                "owner": definition.owner,
                "source_actor_id": target_id,
            }
        return None

    def _condition_immunity_blocker(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        condition_id: str,
    ) -> dict[str, str] | None:
        """Why ``condition_id`` cannot affect the target at all right now.

        One question, two shapes of answer: an emanation the target stands in,
        or a feature the target carries.  Both are reported as the same record
        so the settlement path has exactly one branch, and ``scope`` says which
        kind it was -- an interception whose reason cannot be read is not an
        audit trail.
        """

        aura = self._aura_condition_immunity_source(
            instance, combat, target, condition_id,
        )
        if aura is not None:
            return {**aura, "scope": "aura"}
        passive = self._passive_condition_immunity(
            instance, combat, target, condition_id,
        )
        if passive is not None:
            return {**passive, "scope": "feature"}
        return None

    def _suppressed_condition_codes(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> frozenset[str]:
        """Conditions the actor still carries but that have no effect now.

        Only suppression by an aura is modelled, and only for the condition
        ids some declaration actually immunizes -- asking the aura machinery
        about a condition nothing can intercept would cost a projection per
        condition for an answer that is always "no".
        """

        if str(actor.get("side") or "") != "party":
            return frozenset()
        declared = {
            code
            for definition in self.features.catalog.features_by_key.values()
            for code in (definition.parameters.get("aura_condition_immunities") or [])
            if isinstance(code, str)
        }
        present = declared & set((actor.get("conditions") or {}).keys())
        if not present:
            return frozenset()
        return frozenset(
            code for code in present
            if self._aura_condition_immunity_source(instance, combat, actor, code) is not None
        )

    def _effective_conditions(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> dict[str, Any]:
        """The condition set the mechanics should read.

        Canonical state keeps every entry, its source and its duration: an aura
        does not delete a condition, it makes it inert while the creature
        stands inside.  This view is the other half of that sentence, and it is
        what ``condition_flags``, attack modes, speed and intent validation
        must consult so that "suppressed" means the same thing everywhere.
        """

        conditions = actor.get("conditions") or {}
        suppressed = self._suppressed_condition_codes(instance, combat, actor)
        if not suppressed:
            return conditions
        return {
            code: value for code, value in conditions.items() if code not in suppressed
        }

    def _effective_target(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> dict[str, Any]:
        """A shallow copy of ``actor`` whose ``conditions`` are the effective set.

        Pure helpers such as ``attack_sources`` take a view and read
        ``conditions`` from it; giving them this copy is cheaper and less
        invasive than threading a second condition argument through every call.
        """

        suppressed = self._suppressed_condition_codes(instance, combat, actor)
        if not suppressed:
            return actor
        return {**actor, "conditions": self._effective_conditions(instance, combat, actor)}

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

    def _aura_condition_immunity_source(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        condition_id: str,
    ) -> dict[str, str] | None:
        """The aura that makes ``condition_id`` a no-op for the target now.

        A condition immunity rides on a base aura named by
        ``aura_source_feature_id``, so it uses that aura's range and dies with
        it when the holder is Incapacitated.  It is not an independent
        emanation, which is exactly why the old free-standing ``aura_range``
        declaration is gone.

        The catalog is consulted first: if no declaration lists ``condition_id``
        anywhere, nothing can intercept it and projecting the auras would be
        wasted work.  That early exit is what makes it affordable to ask this
        question on every condition-applying path rather than only on saves.

        The first qualifying source in catalog order is reported.  Two Devotion
        paladins therefore produce one interception with one stable source
        rather than two stacked settlements.
        """

        wanted = str(condition_id or "")
        target_id = str(target.get("actor_id") or "")
        if not wanted or not target_id or str(target.get("side") or "") != "party":
            return None
        declared = {
            code
            for definition in self.features.catalog.features_by_key.values()
            for code in (definition.parameters.get("aura_condition_immunities") or [])
            if isinstance(code, str)
        }
        if wanted not in declared:
            return None
        for row in self._projected_auras(instance, combat):
            if self._distance(combat, row["holder_id"], target_id) > row["range_ft"]:
                continue
            for extension in row["owned"]:
                if extension.parameters.get("aura_source_feature_id") != row["definition"].id:
                    continue
                immune = extension.parameters.get("aura_condition_immunities")
                if not isinstance(immune, list) or wanted not in immune:
                    continue
                return {
                    "feature_id": extension.id,
                    "owner": extension.owner,
                    "source_actor_id": row["holder_id"],
                }
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

    def _remove_concentration_conditions(
        self, snapshot: dict[str, Any], combat: dict[str, Any], owner: str,
    ) -> None:
        actor_ids = [
            *(_player_actor(uid) for uid in snapshot["characters"]),
            *(
                _companion_actor(companion_id)
                for companion_id in (snapshot["ruleset_state"].get("party", {}) or {}).get(
                    "companions", {},
                )
            ),
            *(_enemy_actor(enemy_id) for enemy_id in combat.get("enemies", {})),
        ]
        for actor_id in actor_ids:
            conditions = self._conditions(snapshot, combat, actor_id)
            for condition_id in [
                key for key, value in conditions.items()
                if isinstance(value, dict) and value.get("concentration_owner") == owner
            ]:
                conditions.pop(condition_id, None)

    def _expire_attack_marks(
        self, combat: dict[str, Any], actor_id: str,
    ) -> None:
        """Age the accepting actor's own attack marks at the start of their turn.

        Studied Attacks lasts "before the end of your next turn", which is one
        turn longer than a turn-start expiry: the mark must survive the whole
        of the attacker's next turn.  A ``turns_remaining`` counter therefore
        counts *turn starts still to come* -- created at 1, it drops to 0 as
        that turn begins (and stays usable), and is removed when the following
        turn begins.
        """

        marks = combat.get("attack_marks")
        if not isinstance(marks, dict):
            return
        for per_kind in marks.values():
            if not isinstance(per_kind, dict):
                continue
            per_actor = per_kind.get(str(actor_id))
            if not isinstance(per_actor, dict):
                continue
            for target_id in list(per_actor):
                row = per_actor[target_id]
                remaining = int(row.get("turns_remaining", 0) or 0) if isinstance(
                    row, dict
                ) else 0
                if remaining > 0:
                    row["turns_remaining"] = remaining - 1
                else:
                    per_actor.pop(target_id, None)

    def _expire_conditions(
        self, snapshot: dict[str, Any], combat: dict[str, Any], actor_id: str,
        previous_actor_id: str,
    ) -> None:
        self._expire_attack_marks(combat, actor_id)
        conditions = self._conditions(snapshot, combat, actor_id)
        for condition_id in [
            key for key, value in conditions.items()
            if isinstance(value, dict) and value.get("duration") in {
                "actor_turn_start", "target_turn_start",
            }
        ]:
            conditions.pop(condition_id, None)
        all_actor_ids = [
            *(_player_actor(uid) for uid in snapshot["characters"]),
            *(
                _companion_actor(companion_id)
                for companion_id in (snapshot["ruleset_state"].get("party", {}) or {}).get(
                    "companions", {},
                )
            ),
            *(_enemy_actor(enemy_id) for enemy_id in combat.get("enemies", {})),
        ]
        for target_id in all_actor_ids:
            target_conditions = self._conditions(snapshot, combat, target_id)
            for condition_id, value in list(target_conditions.items()):
                if not isinstance(value, dict):
                    continue
                if (
                    value.get("duration") == "attacker_next_turn_start"
                    and value.get("source_actor_id") == actor_id
                ):
                    # "Until the start of your next turn": the source actor's
                    # turn has just begun, so this instance expires now.
                    target_conditions.pop(condition_id, None)
                    continue
                if value.get("duration") in TURNS_SCOPED_DURATIONS:
                    # 1 分钟在回合制里就是十个自己的回合：每到自己回合结束
                    # 减一，归零移除。计数放在条件上，所以中断、存档、重放
                    # 都读同一份事实，不需要外部计时器。计时属于**持有着**
                    # 的回合，而不是施法者的回合，因此这一支在下面的
                    # ``source_actor_id`` 过滤之前。
                    if target_id != previous_actor_id:
                        continue
                    remaining = int(value.get("turns_remaining", 0) or 0) - 1
                    if remaining <= 0:
                        target_conditions.pop(condition_id, None)
                    else:
                        value["turns_remaining"] = remaining
                    continue
                if value.get("source_actor_id") != previous_actor_id:
                    continue
                duration = value.get("duration")
                if duration == "caster_turn_end":
                    target_conditions.pop(condition_id, None)
                elif duration == "caster_next_turn_end":
                    remaining = int(value.get("turns_remaining", 2) or 2)
                    if remaining <= 1:
                        target_conditions.pop(condition_id, None)
                    else:
                        value["turns_remaining"] = remaining - 1
        if previous_actor_id:
            # Rage 的回合级生命周期。规则保证狂暴"持续至你的下个回合结束"：
            # 进入回合结束时只把状态标记为已确立（第一轮无条件存续，本回合
            # 是否攻击或延长都不影响）；从下个回合结束起，未续期即结束，
            # 续期则消耗剩余轮数，归零即结束。昏迷则无条件结束。
            previous_conditions = self._conditions(snapshot, combat, previous_actor_id)
            rage = previous_conditions.get("rage")
            if isinstance(rage, dict) and rage.get("duration") == PERSISTENT_RAGE_DURATION:
                # 持久狂怒：不靠攻击或附赠动作续期，单纯 Incapacitated /
                # Stunned / Paralyzed 也不结束；只有 Unconscious 或真的穿上
                # 重甲结束。剩余轮数每轮照常递减，没有旧 Rage 那条"进入的那个
                # 回合免费"的豁免——10 分钟是从进入那一刻起算的 100 轮。
                if (
                    "unconscious" in previous_conditions
                    or self._wears_heavy_armor(snapshot, previous_actor_id)
                ):
                    previous_conditions.pop("rage", None)
                else:
                    remaining = int(
                        rage.get("turns_remaining", RAGE_MAX_ROUNDS) or RAGE_MAX_ROUNDS
                    ) - 1
                    if remaining <= 0:
                        previous_conditions.pop("rage", None)
                    else:
                        rage["turns_remaining"] = remaining
                        rage["sustained_this_turn"] = False
            elif isinstance(rage, dict):
                # 失能即结束狂暴：unconscious / incapacitated 以及所有
                # 蕴含失能的条件（paralyzed / stunned / petrified）。
                if any(
                    condition in previous_conditions
                    for condition in (
                        "unconscious", "incapacitated", "paralyzed", "stunned", "petrified",
                    )
                ):
                    previous_conditions.pop("rage", None)
                elif not rage.get("established"):
                    rage["established"] = True
                    rage["sustained_this_turn"] = False
                elif rage.get("sustained_this_turn"):
                    remaining = int(
                        rage.get("turns_remaining", RAGE_MAX_ROUNDS) or RAGE_MAX_ROUNDS
                    ) - 1
                    if remaining <= 0:
                        previous_conditions.pop("rage", None)
                    else:
                        rage["turns_remaining"] = remaining
                        rage["sustained_this_turn"] = False
                else:
                    previous_conditions.pop("rage", None)

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
