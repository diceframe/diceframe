"""What an actor may do next: intents, forms, items, and the automatic pick."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from src.engine.player_control import is_ai_controlled
from src.rulesets.dnd2024.character.attunement import (
    attuned_refs,
    item_requires_attunement,
    owned_ref_counts,
)
from src.rulesets.dnd2024.features.attack_declarations import (
    cunning_strike_capacity,
    cunning_strike_options,
    sneak_attack_dice_count,
)
from src.rulesets.dnd2024.features import CombatCapabilityView
from src.rulesets.dnd2024.features.resources import resource_state
from src.rulesets.dnd2024.forms import bootstrap_known_forms
from src.rulesets.dnd2024.spells.font_of_magic import declaring_class_level
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
from . import spatial
from .weapon_masteries import WEAPON_MASTERY_FEATURE_ID, cleave_grant, nick_active
from .check_modes import (
    HUNTERS_MARK_REF,
    condition_flags,
    mark_find_sources,
    owned_mark,
    sense_declarations,
)
from .check_windows import CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION, LUCKY_RESOURCE
from .obscurement import (
    LAND_AID_RANGE_FT,
    SANCTUARY_MOVE_LIMIT_FT,
    SANCTUARY_RANGE_FT,
    sanctuary_zone_id,
)
from .primitives import (
    slot_available,
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
    condition_escapes,
    player_actor as _player_actor,
    stand_up_declaration,
)
from .readiness import required_encounter_player_ids
from .triggered_spells import HIT_SPELL_DECISION, hit_spell_decision
from .reaction_spells import COUNTER_SPELL_DECISION, HIT_REACTION_DECISION
from .visibility import public_pending_decisions

if TYPE_CHECKING:
    from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
    from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog
    from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
    from src.rulesets.dnd2024.play.contracts import EncounterAccess
    from src.rulesets.bundle import LoadedRulesetBundle


class CombatAvailabilityMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by the combat engine and its other mixins; no runtime state.
        @staticmethod
        def _action_usage(actor: dict[str, Any], action_id: str) -> dict[str, Any]: ...
        def _activation_payload(self, instance: Any, actor: dict[str, Any]) -> Any: ...
        def _actor_view(self, instance: Any, combat: dict[str, Any], actor_id: str) -> dict[str, Any]: ...
        def _all_targets(self, instance: Any, combat: dict[str, Any]) -> list[dict[str, Any]]: ...
        def _automatic_check_window(
            self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
        ) -> dict[str, Any] | None: ...
        def _automatic_damage_window(
            self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
        ) -> dict[str, Any] | None: ...
        def _automatic_hit_reaction(
            self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
        ) -> dict[str, Any] | None: ...
        def _automatic_hit_spell(self, instance, combat, decision, version): ...
        def _available_attacks(self, actor: dict[str, Any]) -> list[dict[str, Any]]: ...
        def _available_spells(
            self, instance: Any, actor: dict[str, Any], economy: dict[str, Any],
            trigger: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]: ...
        def _canonical_actor(self, instance: Any, actor: dict[str, Any]) -> dict[str, Any]: ...
        def _class_resource_current(self, character: dict[str, Any], resource_id: str) -> int: ...
        @staticmethod
        def _current_actor(combat: dict[str, Any]) -> str: ...
        def _distance(self, combat: dict[str, Any], first: str, second: str) -> int: ...
        @staticmethod
        def _feature_character(actor: dict[str, Any]) -> dict[str, Any]: ...
        @staticmethod
        def _feature_used_this_turn(
            combat: dict[str, Any], actor_id: str, feature_id: str,
        ) -> bool: ...
        def _form_item_mode(
            self, instance: Any, actor_id: str, item_ref: str,
        ) -> str: ...
        def _hostile_targets(
            self, instance: Any, combat: dict[str, Any], actor_id: str,
        ) -> list[dict[str, Any]]: ...
        @staticmethod
        def _position(combat: dict[str, Any], actor_id: str) -> int: ...
        def _self_restoration_choice(
            self, actor: dict[str, Any],
        ) -> tuple[str, frozenset[str]] | None: ...
        def _statblock_by_ref(self, form_ref: str) -> dict[str, Any] | None: ...
        @staticmethod
        def _turn_decision_error(combat: dict[str, Any], intent_type: str) -> str: ...
        def _validate(self, instance: Any, intent: dict[str, Any]) -> None: ...
        def _window_pool_available(
            self, instance: Any, actor_id: str, resource_id: str,
        ) -> bool: ...
        bundle: LoadedRulesetBundle
        catalog: Dnd2024CombatCatalog
        encounter_access: EncounterAccess
        features: Dnd2024ClassFeatureResolver
        def hide_preconditions(
            self, instance: Any, combat: dict[str, Any], actor_id: str,
        ) -> dict[str, Any]: ...
        def initialize_state(self, instance: Any) -> dict[str, Any]: ...
        spells: Dnd2024SpellCatalog
        @staticmethod
        def stand_up_cost(
            actor: dict[str, Any], declared: dict[str, Any], *, speed: int | None = None,
        ) -> int: ...

    def _available_class_capabilities(
        self, actor: dict[str, Any], economy: dict[str, Any],
        *, include_unavailable: bool = False,
    ) -> tuple[CombatCapabilityView, ...]:
        """Feature capabilities with the actor's live conditions applied.

        A capability that requires a condition the actor does not hold, or that
        forbids one the actor currently holds, is never offered: Rage cannot be
        re-entered while raging, and Sustain Rage is meaningless outside it.
        """

        # Two-argument super(): the engine class is rebuilt by
        # @dataclass(slots=True), and the base implementation lives in
        # CombatViewMixin, which this mixin must precede in the MRO.
        # mypy only sees this mixin's own bases, not the engine MRO that puts
        # CombatViewMixin behind it, so the cooperative call needs a waiver.
        views = super(CombatAvailabilityMixin, self)._available_class_capabilities(  # type: ignore[misc]
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

    def _available_form_intents(
        self, instance: Any, actor: dict[str, Any], economy: dict[str, Any],
        version: int,
    ) -> list[dict[str, Any]]:
        """Project the normal UI entries for Wild Shape's form intents."""

        if actor.get("kind") != "player":
            return []
        if condition_flags(
            self.catalog.conditions, actor.get("conditions") or {},
        )["incapacitated"]:
            # 失能者不能执行附赠动作：校验层会拒绝，投影层就不该把入口摆出来。
            return []
        feature_character = self._feature_character(actor)
        if not self.features.has_feature(feature_character, "wild_shape"):
            return []
        _kind, raw_id = _actor_kind(str(actor.get("actor_id") or ""))
        character = _canonical(instance.get_character_sheet(raw_id))
        forms = character.get("forms")
        forms = forms if isinstance(forms, dict) else {}
        active = forms.get("active")
        known = forms.get("known")
        known = known.get("wild_shape") if isinstance(known, dict) else known
        if not isinstance(known, list) or not known:
            entries = [
                entry
                for catalog in self.bundle.list("monster_catalog")
                for entry in catalog.get("entries") or []
            ]
            level = declaring_class_level(character, "druid")
            known = bootstrap_known_forms(entries, int(level))
        form_options: list[dict[str, Any]] = []
        for form_ref in known:
            entry = self._statblock_by_ref(str(form_ref))
            if entry is None:
                continue
            form_options.append({
                "ref": str(entry.get("id") or ""),
                "name": str(entry.get("name") or entry.get("id") or ""),
                "cr": str(entry.get("cr") or "0"),
            })
        actions: list[dict[str, Any]] = []
        if isinstance(active, dict):
            if int(economy.get("bonus_action", 0) or 0) > 0:
                actions.append({
                    "type": "form.revert",
                    "label": "Revert form",
                    "actor_id": actor["actor_id"],
                    "expected_version": version,
                })
        if form_options and (
            int(economy.get("bonus_action", 0) or 0) > 0
            and int((resource_state(character, "wild_shape") or {}).get("current", 0) or 0) > 0
        ):
            actions.append({
                "type": "form.assume",
                "label": "Wild Shape",
                "actor_id": actor["actor_id"],
                "expected_version": version,
                "forms": form_options,
            })
        return actions

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
        if current.startswith("enemy:") and not viewer_is_gm:
            return communication
        requested = _player_actor(actor_id)
        if requested != current and not (viewer_is_gm and current.startswith("enemy:")):
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
        if self._turn_decision_error(combat, "end_turn"):
            return communication
        actor = self._actor_view(instance, combat, current)
        end_turn_entry: dict[str, Any] = {
            "type": "end_turn", "label": "End turn", "actor_id": current,
            "expected_version": version,
        }
        if "dead" in actor["conditions"]:
            actions = [*communication, end_turn_entry]
            if viewer_is_gm:
                actions.append({
                    "type": "combat.end", "label": "End combat", "expected_version": version,
                })
            return actions
        if actor["hp"] <= 0 and actor["kind"] == "player":
            if "stable" in actor["conditions"]:
                return [*communication, end_turn_entry]
            return [*communication, {
                "type": "death_save", "label": "Death saving throw",
                "expected_version": version, "actor_id": current,
                "lucky_advantage_available": self._window_pool_available(
                    instance, current, LUCKY_RESOURCE,
                ),
            }]
        economy = combat.get("economy", {})
        actions: list[dict[str, Any]] = []  # type: ignore[no-redef]
        actions.extend(self._available_form_intents(instance, actor, combat.get("economy", {}), version))
        # Light 追加攻击花的是附赠动作，所以它**不**受"动作是否已花"影响；
        # 放进下面的动作门禁里会让它永远不出现（动作先被普通攻击用掉）。
        actions.extend(
            self._light_extra_attack_rows(instance, actor, combat, economy, version)
        )
        cleave_row = self._cleave_row(instance, actor, combat, version)
        if cleave_row:
            actions.append(cleave_row)
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
            hide_gate = self.hide_preconditions(instance, combat, current)
            actions.extend([
                {"type": "dash", "label": "Dash", "actor_id": current, "expected_version": version},
                {"type": "dodge", "label": "Dodge", "actor_id": current, "expected_version": version},
                {"type": "disengage", "label": "Disengage", "actor_id": current, "expected_version": version},
                {
                    "type": "hide", "label": "Hide", "actor_id": current,
                    "expected_version": version,
                    "available": bool(hide_gate["allowed"]),
                    "unavailable_reason": str(hide_gate["reason"]),
                },
            ])
            adjacent_downed = [
                target for target in self._all_targets(instance, combat)
                if target["kind"] == actor["kind"] and target["hp"] <= 0
                and not target["is_dead"]
                and self._distance(combat, current, target["actor_id"]) <= 5
            ]
            if adjacent_downed:
                actions.append({
                    "type": "stabilize", "label": "Stabilize", "actor_id": current,
                    "expected_version": version, "targets": adjacent_downed,
                })
            if actor["kind"] == "enemy":
                targets = self._hostile_targets(instance, combat, current)
                for declaration in actor.get("actions") or []:
                    if not isinstance(declaration, dict):
                        continue
                    kind = str(declaration.get("kind") or "")
                    action_id = str(declaration.get("id") or "")
                    if not action_id or kind not in {"multiattack", "save_action"}:
                        continue
                    if kind == "save_action" and not self._action_usage(actor, action_id)["available"]:
                        continue
                    entry: dict[str, Any] = {
                        "type": kind,
                        "label": str(declaration.get("name") or action_id),
                        "actor_id": current,
                        "expected_version": version,
                        "action_id": action_id,
                        "targets": targets,
                    }
                    choices = declaration.get("choice")
                    if isinstance(choices, list):
                        entry["choice_ids"] = [
                            str(row.get("label") or "")
                            for row in choices
                            if isinstance(row, dict) and str(row.get("label") or "")
                        ]
                    actions.append(entry)
        spells = self._available_spells(
            instance, actor, economy, hit_spell_decision(combat, current),
        )
        # 当前标记与"能不能转移"由服务端给出：客户端不该自己从专注里推断
        # "这个目标归零了没有"。这条入口不能跟着 ``spells`` 一起消失：免费
        # 次数与法术位都花光之后，转移标记仍是一次合法提交，而过去只有在
        # 还有法术可放时才投影这一行（S0-F18 §4.1.7）。
        mark = self._hunters_mark_state(instance, combat, actor)
        if spells or bool(mark and mark.get("can_retarget")):
            entry: dict[str, Any] = {  # type: ignore[no-redef]
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
        actions.extend(self._land_feature_actions(instance, combat, current, actor))
        if int(economy.get("movement", 0) or 0) > 0:
            actions.append({
                "type": "move", "label": "Move", "actor_id": current,
                "expected_version": version, "movement_remaining": economy["movement"],
            })
        # 挣脱与起身都是"从状态里长出来的动作"：面板不该自己推断能不能用、
        # 需要多少成本，所以两条入口都由注册表 + 实例现算。
        for escape in condition_escapes(self.catalog.conditions, actor["conditions"]):
            actions.append({
                "type": "escape", "label": "Escape", "actor_id": current,
                "expected_version": version,
                "condition": escape["condition"],
                "escape_dc": escape["escape_dc"],
                "checks": escape["checks"],
                "cost": escape["cost"],
                "available": int(economy.get(escape["cost"], 0) or 0) > 0,
            })
        stand_up = stand_up_declaration(self.catalog.conditions, actor["conditions"])
        if stand_up is not None:
            effective_speed = economy.get("speed")
            cost = self.stand_up_cost(
                actor, stand_up,
                speed=int(effective_speed) if isinstance(effective_speed, int) else None,
            )
            actions.append({
                "type": "stand_up", "label": "Stand up", "actor_id": current,
                "expected_version": version,
                "condition": stand_up["condition"],
                "movement_cost": cost,
                "available": int(economy.get("movement", 0) or 0) >= cost,
            })
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

        # 诡诈打击：面板要能显示"有哪些效果、每个几颗骰、这级能用几个、
        # 池里还剩几颗"，否则玩家只能盲选。空元组 = 没有这个特性，所以
        # 键的存在本身就说明可用。
        cunning = cunning_strike_options(self.features, character)
        if cunning:
            options["cunning_strike"] = {
                # 本回合的偷袭已经用掉时，这一击不可能再花骰：面板要能在
                # 提交前说明原因，而不是让玩家选完只收到一个 skipped。
                "available": not self._feature_used_this_turn(
                    combat, actor_id, "sneak_attack",
                ),
                "effects": [effect.id for effect in cunning],
                "count": cunning_strike_capacity(self.features, character),
                "costs": {effect.id: effect.dice_cost for effect in cunning},
                "dice": sneak_attack_dice_count(self.features, character),
                # 效果名来自 bundle 自己的 locale 表，客户端只做兜底，避免
                # 前后端各存一份"淬毒/摔绊"而在改名时不同步。
                "names": {
                    effect.id: effect.name for effect in cunning if effect.name
                },
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
            if self._form_item_mode(instance, actor["actor_id"], item_ref) in {"merge", "drop"}:
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
            if not slot_available(economy, cost):
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

    def _light_extra_attack_rows(
        self, instance: Any, actor: dict[str, Any], combat: dict[str, Any],
        economy: dict[str, Any], version: int,
    ) -> list[dict[str, Any]]:
        """The Light property's extra attack, as its own player action.

        Two rows at most, because the cost depends on the weapon picked in the
        dock: weapons without Nick spend the Bonus Action, and a Nick weapon
        spends nothing but is once per turn.  Merging them into one row would
        make the tile lie about its cost for one of the two cases.
        """

        if actor["kind"] not in {"player", "companion"}:
            return []
        record = (combat.get("light_attacks") or {}).get(str(actor["actor_id"]))
        if not isinstance(record, dict):
            return []
        if (
            int(record.get("round", -1)) != int(combat.get("round", 0) or 0)
            or int(record.get("turn_index", -1))
            != int(combat.get("turn_index", 0) or 0)
        ):
            return []
        light_refs = [
            str(used) for used in record.get("weapon_refs") or []
            if (self.catalog.weapons.get(str(used).removeprefix("item:")) or {}).get("light")
        ]
        if not light_refs:
            return []
        offhand = [
            weapon for weapon in self._available_attacks(actor)
            if weapon.get("light")
            # 与校验同一条判据：至少要有一把"用过且不同"的 Light 武器当参照。
            and any(ref != str(weapon.get("weapon_ref") or "") for ref in light_refs)
        ]
        if not offhand:
            return []
        character = self._feature_character(actor)
        free = [w for w in offhand if nick_active(self.features, character, w)]
        paid = [w for w in offhand if w not in free]
        targets = self._hostile_targets(instance, combat, str(actor["actor_id"]))
        rows: list[dict[str, Any]] = []
        for group, cost, available, reason in (
            (paid, "bonus_action", int(economy.get("bonus_action", 0) or 0) > 0,
             "Bonus Action is already spent"),
            (free, "", not bool(record.get("nick_used")),
             "Nick already gave its extra attack this turn"),
        ):
            if not group:
                continue
            rows.append({
                "type": "light_attack",
                "label": "Light extra attack",
                "actor_id": str(actor["actor_id"]),
                "expected_version": version,
                "weapons": group,
                "targets": targets,
                "cost": cost,
                "nick": cost == "",
                "available": available,
                "unavailable_reason": "" if available else reason,
            })
        return rows

    def _cleave_row(
        self, instance: Any, actor: dict[str, Any], combat: dict[str, Any],
        version: int,
    ) -> dict[str, Any] | None:
        """Cleave, offered only when the prerequisite is already true.

        The row carries ``cleave_from``: the panel posts an ordinary attack with
        that field, so the engine has one attack path instead of a second one
        for granted attacks.
        """

        if actor["kind"] not in {"player", "companion"}:
            return None
        if not self.features.has_feature(
            self._feature_character(actor), WEAPON_MASTERY_FEATURE_ID,
        ):
            return None
        actor_id = str(actor["actor_id"])
        round_index = int(combat.get("round", 0) or 0)
        turn_index = int(combat.get("turn_index", 0) or 0)
        granted = (combat.get("granted_attacks") or {}).get(actor_id) or {}
        if (
            granted.get("cleave")
            and int(granted.get("round", -1)) == round_index
            and int(granted.get("turn_index", -1)) == turn_index
        ):
            return None
        weapons = [w for w in self._available_attacks(actor) if cleave_grant(w)]
        if not weapons:
            return None
        hits = [
            row for row in combat.get("attack_history") or []
            if str(row.get("actor_id") or "") == actor_id
            and int(row.get("round", 0) or 0) == round_index
            and int(row.get("turn_index", 0) or 0) == turn_index
            and bool(row.get("hit"))
            and any(
                str(w.get("weapon_ref") or "") == str(row.get("weapon_ref") or "")
                for w in weapons
            )
        ]
        if not hits:
            return None
        original_id = str(hits[-1].get("target_id") or "")
        hit_weapon_ref = str(hits[-1].get("weapon_ref") or "")
        hit_weapon = next(
            (w for w in weapons if str(w.get("weapon_ref") or "") == hit_weapon_ref),
            weapons[0],
        )
        limit = int((cleave_grant(hit_weapon) or {}).get("target_within_ft") or 0)
        reach = int(hit_weapon.get("long_range") or hit_weapon.get("range", 5) or 5)
        targets = [
            target for target in self._hostile_targets(instance, combat, actor_id)
            if int(target["hp"]) > 0
            and str(target["actor_id"]) != original_id
            and self._distance(combat, str(target["actor_id"]), original_id) <= limit
            and self._distance(combat, actor_id, str(target["actor_id"])) <= reach
        ]
        if not targets:
            return None
        return {
            "type": "cleave",
            "label": "Cleave",
            "actor_id": actor_id,
            "expected_version": version,
            "weapons": weapons,
            "targets": targets,
            "cleave_from": original_id,
            "cost": "",
        }

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
                if spatial.is_grid(combat):
                    target_id = str(target["actor_id"])
                    desired_range = max(
                        (int(item.get("range", 5) or 5) for item in actor.get("attacks") or []),
                        default=5,
                    )
                    needed = max(
                        0, self._distance(combat, actor_id, target_id) - desired_range,
                    )
                    cell = spatial.automatic_move_target(
                        combat, actor_id, target_id, min(movement, needed),
                    )
                    if cell is not None:
                        return {
                            **base,
                            "intent_id": f"{base['intent_id']}:move",
                            "type": "move",
                            "target_cell": cell,
                        }
                else:
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
        conditions = actor["conditions"]
        if "dead" in conditions or (actor["hp"] <= 0 and "stable" in conditions):
            return {**base, "intent_id": f"{base['intent_id']}:end", "type": "end_turn"}
        if actor["hp"] <= 0:
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
            target for target in self._party_targets(instance, combat)
            if target["actor_id"] != actor_id
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
                    target_id = str(target["actor_id"])
                    desired_range = max(
                        (
                            int(weapon.get("range", 5) or 5)
                            for weapon in self._available_attacks(actor)
                        ),
                        default=5,
                    )
                    if spatial.is_grid(combat):
                        needed = max(
                            0, self._distance(combat, actor_id, target_id) - desired_range,
                        )
                        cell = spatial.automatic_move_target(
                            combat, actor_id, target_id, min(movement, needed),
                        )
                        if cell is not None:
                            return {
                                **base,
                                "intent_id": f"{base['intent_id']}:move",
                                "type": "move",
                                "target_cell": cell,
                            }
                    else:
                        actor_position = self._position(combat, actor_id)
                        target_position = self._position(combat, target_id)
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

    def _land_feature_actions(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Circle of the Land 的入口：只在真的能付得起时出现。

        引擎里有意图 ≠ 玩家能用。这里的判据与 validation 完全一致（特性、
        动作/附赠动作、Wild Shape 次数、自己回合），差别只是"能不能付"而不是
        "提交后会不会被拒"。
        """

        if str(actor.get("kind") or "") != "player":
            return []
        if condition_flags(
            self.catalog.conditions, actor.get("conditions") or {},
        )["incapacitated"]:
            # 失能者不能执行动作：校验层会拒绝，投影层就不该把入口摆出来。
            return []
        character = self._feature_character(actor)
        state = self.initialize_state(instance)
        combat_state = state.get("combat") or {}
        if str(combat_state.get("status") or "") != "active":
            return []
        economy = combat_state.get("economy") or {}
        version = int(state.get("version", 0) or 0)
        actions: list[dict[str, Any]] = []
        if (
            self.features.has_feature(character, "lands_aid")
            and int(economy.get("action", 0) or 0) > 0
        ):
            uses = self._class_resource_current(character, "wild_shape")
            if uses >= 1:
                actions.append({
                    "type": "land_aid", "label": "Land's Aid", "actor_id": actor_id,
                    "expected_version": version, "origin_limit_ft": LAND_AID_RANGE_FT,
                    "radius_ft": 10, "wild_shape_uses": uses,
                    "targets": self._all_targets(instance, combat),
                })
        if self.features.has_feature(character, "natures_sanctuary"):
            zone_id = sanctuary_zone_id(actor_id)
            existing = (combat_state.get("zones") or {}).get(zone_id)
            if isinstance(existing, dict) and int(economy.get("bonus_action", 0) or 0) > 0:
                actions.append({
                    "type": "nature_sanctuary", "label": "Move the sanctuary",
                    "mode": "move", "zone_id": zone_id, "actor_id": actor_id,
                    "expected_version": version, "origin_limit_ft": SANCTUARY_RANGE_FT,
                    "move_limit_ft": SANCTUARY_MOVE_LIMIT_FT,
                })
            elif (
                int(economy.get("action", 0) or 0) > 0
                and self._class_resource_current(character, "wild_shape") >= 1
            ):
                actions.append({
                    "type": "nature_sanctuary", "label": "Nature's Sanctuary",
                    "mode": "create", "actor_id": actor_id,
                    "expected_version": version, "origin_limit_ft": SANCTUARY_RANGE_FT,
                    "size_ft": 15,
                })
        return actions
