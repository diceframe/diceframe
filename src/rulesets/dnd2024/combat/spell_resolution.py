"""Spell casting resolution: the spell event pipeline and everything it settles with."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from copy import deepcopy
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.engine.player_control import is_ai_controlled
from src.rulesets.dnd2024.features.resources import (
    OVERCHANNEL_FEATURE_ID,
    OVERCHANNEL_OWNER,
    USAGE_SCOPE_LONG_REST,
    feature_usage_count,
)
from .effect_items import normalize_spell_effect
from .spell_damage import damage_node_for
from .action_adapter import (
    double_dice_counts,
    formula_node_from_dice_formula,
    heal_ability_modifier_node,
    maximize_dice_nodes,
    roll_damage_node,
    spell_formula_node,
)
from .primitives import (
    CombatIntentError,
    actor_kind as _actor_kind,
    enemy_actor as _enemy_actor,
)
from .check_modes import (
    HUNTERS_MARK_REF,
    condition_check_bonus_rolls,
    condition_flags,
    owned_mark,
)
from src.rulesets.dnd2024.features.attack_declarations import (
    committed_hit_events,
    held_hit_declarations,
)
from . import invocation_effects
from ..spells.casting import free_cast_resource
from .cover import cover_axis
from .reaction_spells import effective_armor_class

if TYPE_CHECKING:
    from ..features.resolver import Dnd2024ClassFeatureResolver
    from ..spells.catalog import Dnd2024SpellCatalog
    from .catalog import Dnd2024CombatCatalog


# Potent Spellcasting is declared once per casting class with its own
# ``cantrip_source_class``; both declarations have to be asked separately.
POTENT_SPELLCASTING_OWNERS = ("class:cleric", "class:druid")


def _without_consumed_conditions(
    view: dict[str, Any], consumed: set[tuple[str, str]],
) -> dict[str, Any]:
    """Hide conditions an earlier attack roll of the same batch consumed.

    一次施法只产出一个事件批次，状态要到 ``apply_batch`` 才落地；多道射线在
    同一批次内循环时，每道都会读到施法前的条件集合。RAW 规定"你的下一次攻击
    掷骰"类条件在第一次掷骰后即消耗，因此循环用本函数的副本逐 beam 隐藏已消耗
    条件，既不提前改状态，也不让后续射线重复受益。
    """

    conditions = {
        code: entry
        for code, entry in (view.get("conditions") or {}).items()
        if (str(view.get("actor_id") or ""), str(code)) not in consumed
    }
    return {**view, "conditions": conditions}


class CombatSpellResolutionMixin:
    """Turn a validated cast into spell events, saving throws, and damage."""

    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by the combat engine and its other mixins; no runtime state.
        def _actor_view(self, instance: Any, combat: dict[str, Any], actor_id: str) -> dict[str, Any]: ...
        def _blessed_healer_events(
            self, actor: dict[str, Any], actor_id: str, slot_level: int,
            spell: dict[str, Any],
        ) -> list[dict[str, Any]]: ...
        def _canonical_for_actor(self, instance: Any, actor_id: str) -> dict[str, Any]: ...
        def _concentration_events(
            self, instance: Any, combat: dict[str, Any],
            target: dict[str, Any], damage: int, rng: Any,
        ) -> list[dict[str, Any]]: ...
        def _condition_immunity_blocker(
            self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
            condition_id: str,
        ) -> dict[str, str] | None: ...
        def _consume_attack_conditions(
            self, actor_id: str, target_id: str, actor: dict[str, Any], target: dict[str, Any],
        ) -> list[dict[str, Any]]: ...
        def _cover_record(
            self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        ) -> dict[str, Any]: ...
        def _cover_save_bonus(
            self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
            ability: str, *, axis: tuple[str, str] = ("horizontal", ""),
        ) -> dict[str, Any]: ...
        def _distance(self, combat: dict[str, Any], first: str, second: str) -> int: ...
        def _effective_conditions(
            self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        ) -> dict[str, Any]: ...
        def _enemy_zero_hp_feature_events(
            self, instance: Any, combat: dict[str, Any], source_actor_id: str,
            target: dict[str, Any], damage_taken: int,
        ) -> list[dict[str, Any]]: ...
        @staticmethod
        def _feature_character(actor: dict[str, Any]) -> dict[str, Any]: ...
        def _healing_rider_events(
            self, actor: dict[str, Any], target_id: str, target: dict[str, Any],
            slot_level: int, spell: dict[str, Any], actor_id: str,
        ) -> list[dict[str, Any]]: ...
        def _logical_minutes(self, instance: Any) -> int: ...
        def _mark_rider_row(self, actor: dict[str, Any], target_id: str) -> dict[str, Any] | None: ...
        @staticmethod
        def _position(combat: dict[str, Any], actor_id: str) -> int: ...
        def _resist_damage(
            self, target: dict[str, Any], amount: int, damage_type: str,
        ) -> tuple[int, str]: ...
        def _rider_damage_event(
            self, row: dict[str, Any], target: dict[str, Any],
            critical: bool, rng: Any,
        ) -> tuple[dict[str, Any] | None, int]: ...
        def _saving_throw_check(
            self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
            actor_id: str, ability: str, dc: int, source_actor_id: str,
            events: list[dict[str, Any]], rng: Any, *, against_spell: bool = False,
            forced_outcome: str = "",
        ) -> dict[str, Any]: ...
        @staticmethod
        def _spell_caster(actor: dict[str, Any], spell_ref: str) -> dict[str, Any]: ...
        def _spell_cost_events(self, actor, spell, slot_level, *, spend_action=True, quickened=False): ...
        def _strength_total_replacement(
            self, target: dict[str, Any], ability: str, total: int,
        ) -> tuple[int, dict[str, Any]]: ...
        def _sundering_bonus(
            self, target: dict[str, Any], actor_id: str,
        ) -> tuple[int, str]: ...
        def _triggered_spell_events(self, instance, combat, intent, rng): ...
        catalog: Dnd2024CombatCatalog
        features: Dnd2024ClassFeatureResolver
        spells: Dnd2024SpellCatalog

    @staticmethod
    def _spell_end_trigger_events(
        actor: dict[str, Any], trigger: str,
    ) -> list[dict[str, Any]]:
        """Events that end spells whose condition declares ``ends_on`` ``trigger``.

        Read from the *pre-resolution* actor view, so a condition applied by the
        very spell cast in this batch is never ended by that same cast: the old
        spell ends, the new one takes over intact.

        A condition owned by a concentration spell ends the whole spell: one
        ``dnd2024.concentration.ended`` per owner, and the existing reducer
        cascade removes every condition the owner had applied (to all targets).
        Conditions with no owner are removed individually.

        ``damage_dealt`` is a declared trigger code without its own call site:
        every damage in the engine originates from an attack roll or a spell
        cast, and both of those already trigger first.
        """

        events: list[dict[str, Any]] = []
        owners: set[str] = set()
        for condition_id, meta in (actor.get("conditions") or {}).items():
            if not isinstance(meta, dict):
                continue
            if trigger not in (meta.get("ends_on") or []):
                continue
            owner = str(meta.get("concentration_owner") or "")
            if not owner:
                events.append({
                    "type": "condition.removed", "target_id": actor["actor_id"],
                    "condition": condition_id,
                })
                continue
            if owner in owners:
                continue
            owners.add(owner)
            events.append({
                "type": "dnd2024.concentration.ended", "actor_id": owner,
                "reason": "spell_ended_early",
            })
        return events

    def _consume_save_markers(
        self, target_id: str, target: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Remove the "next saving throw" markers a save just consumed.

        Called at every real saving-throw site with the actor who rolled.  The
        two Staggering halves are independent: this only ever removes the
        saving-throw half, so the Opportunity Attack the other half forbids
        still lasts until the attacker's next turn.
        """

        conditions = target.get("conditions") or {}
        if "next_save_disadvantage" not in conditions:
            return []
        return [{
            "type": "condition.removed", "target_id": str(target_id),
            "condition": "next_save_disadvantage", "reason": "save_resolved",
        }]

    def _evasion_applies(self, target: dict[str, Any], ability: str) -> bool:
        """Whether Evasion rewrites this effect's damage branches.

        Evasion only matches the effect the declaration names -- a Dexterity
        save that already allows half damage on a success.  It is not a global
        halving of Dexterity saves, and an Incapacitated holder gets nothing.
        """

        if target.get("kind") not in {"player", "companion"}:
            return False
        flags = condition_flags(self.catalog.conditions, target.get("conditions") or {})
        if flags["incapacitated"]:
            return False
        character = self._feature_character(target)
        declared = self.features.feature_value(
            character, "evasion", "half_on_success_save", "",
        )
        return str(declared) == ability

    def _relentless_rage_marker(
        self, instance: Any, target: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The state a damage event needs so the reducer can offer Relentless Rage.

        The declaration answers ``what`` (DC ladder, ability, HP formula); the
        target's own feature state answers ``which DC``.  Nothing is rolled and
        no decision is opened here: only a damage batch that would actually
        reach 0 HP (temp HP already absorbed) may open the window, and that is
        the reducer's boundary.
        """

        if target.get("kind") not in {"player", "companion"}:
            return None
        if "rage" not in (target.get("conditions") or {}):
            return None
        character = self._feature_character(target)
        if not self.features.has_feature(character, "relentless_rage"):
            return None
        base_dc = self.features.feature_value(
            character, "relentless_rage", "base_dc", 10,
        )
        step = self.features.feature_value(
            character, "relentless_rage", "dc_step", 5,
        )
        multiplier = self.features.feature_value(
            character, "relentless_rage", "hp_multiplier", 2,
        )
        ability = str(self.features.feature_value(
            character, "relentless_rage", "save_ability", "con",
        ) or "con")
        stored = (character.get("features") or {}).get("relentless_rage")
        stored_dc = stored.get("next_dc") if isinstance(stored, dict) else None
        dc = int(base_dc)
        if isinstance(stored_dc, int) and not isinstance(stored_dc, bool):
            dc = stored_dc
        class_level = 0
        rows = (character.get("build") or {}).get("class_levels") or []
        for row in rows:
            if isinstance(row, dict) and str(row.get("class_ref")) == "class:barbarian":
                raw_level = row.get("level")
                if isinstance(raw_level, int) and not isinstance(raw_level, bool):
                    class_level = raw_level
                break
        if class_level <= 0:
            return None
        kind, raw_id = _actor_kind(str(target.get("actor_id") or ""))
        human = kind == "player" and not is_ai_controlled(instance, raw_id)
        return {
            "dc": dc,
            "dc_step": int(step) if isinstance(step, int) else 5,
            "hp_amount": int(multiplier) * class_level,
            "save_ability": ability,
            "assigned_to": raw_id if human else str(getattr(instance, "gm_uid", "") or ""),
        }

    def _mark_duration_hours(self, effect: dict[str, Any], slot_level: int) -> int:
        """How long this cast's instance may last, from the slot it used.

        There is no clock in the combat state, so this is the declared number
        carried by the instance rather than an expiry that gets enforced -- the
        same honesty the rest of the concentration model applies.
        """

        base = int(effect.get("mark_duration_hours", 1) or 1)
        thresholds = effect.get("mark_duration_hours_by_slot") or {}
        best = base
        for raw_level, hours in thresholds.items():
            level = int(raw_level)
            if int(slot_level) >= level and int(hours) > best:
                best = int(hours)
        return best

    def _mark_retarget_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        effect: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Move the mark to a new creature without casting anything.

        Validation already proved every precondition (own instance, the marked
        creature at 0 HP, the new one visible and in range, a Bonus Action to
        spend); this method only builds the events, and it fails closed if the
        instance went away between the two steps.

        No ``spell_cast`` trigger fires and no cost is paid: the spell instance
        keeps its identity, so its remaining duration is untouched.
        """

        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        mark = owned_mark(actor)
        if not mark:
            raise CombatIntentError("there is no Hunter's Mark to move")
        target_ids = intent.get("target_ids")
        target_id = str(target_ids[0]) if isinstance(target_ids, list) and target_ids else ""
        if not target_id:
            raise CombatIntentError("the mark needs a new target")
        if not effect.get("marks_target"):  # pragma: no cover - catalog guard
            raise CombatIntentError("only a mark instance can be moved")
        return [
            {
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": "bonus_action", "amount": 1,
            },
            {
                "type": "dnd2024.concentration.retargeted",
                "actor_id": actor_id, "spell_ref": HUNTERS_MARK_REF,
                "target_id": target_id,
                "previous_target_id": str(mark.get("mark_target_id") or ""),
            },
        ]

    def _potent_spellcasting_owner(
        self, actor: dict[str, Any], spell: dict[str, Any],
    ) -> str:
        """Which class's Potent Spellcasting option covers this spell, if any.

        The id is declared twice -- ``class:cleric`` and ``class:druid`` -- with
        different ``cantrip_source_class`` values, so ownership has to be
        resolved per declaration: a Cleric's option never boosts a Druid cantrip
        even though both are called Potent Spellcasting, and a character holding
        both options must not have one silently answer for the other.
        """

        if int(spell.get("level", 0) or 0) != 0:
            return ""
        refs = spell.get("class_refs") or []
        character = self._feature_character(actor)
        for owner in POTENT_SPELLCASTING_OWNERS:
            if not self.features.has_feature(
                character, "potent_spellcasting", owner=owner,
            ):
                continue
            source = str(self.features.feature_value(
                character, "potent_spellcasting", "cantrip_source_class", "",
                owner=owner,
            ) or "")
            if source and source in refs:
                return owner
        return ""

    def _potent_spellcasting_bonus(
        self, actor: dict[str, Any], spell: dict[str, Any],
    ) -> int:
        """The Wisdom modifier a Potent Spellcasting option adds to this cantrip."""

        owner = self._potent_spellcasting_owner(actor, spell)
        if not owner:
            return 0
        character = self._feature_character(actor)
        ability = str(
            self.features.feature_value(
                character, "potent_spellcasting", "ability", "", owner=owner,
            ) or ""
        )
        if not ability:
            return 0
        return ability_modifier(actor["abilities"].get(ability, 10))

    @staticmethod
    def is_sorcerer_spell(spell: dict[str, Any]) -> bool:
        """Whether this spell is on the Sorcerer list.

        Innate Sorcery's +1 DC and attack Advantage are limited to "your
        Sorcerer spells", so the list membership -- not the caster's level -- is
        what decides.  A multiclass caster's Wizard spell keeps its own DC.
        """

        return "class:sorcerer" in (spell.get("class_refs") or ())

    def _spell_save_dc(self, actor: dict[str, Any], spell: dict[str, Any]) -> int:
        """The DC for *this* spell: base, plus Innate Sorcery when it applies."""

        base = int(actor.get("spell_save_dc", 0) or 0)
        if (
            self.is_sorcerer_spell(spell)
            and "innate_sorcery" in (actor.get("conditions") or {})
        ):
            return base + 1
        return base

    def _spell_effective_range(
        self, actor: dict[str, Any], spell: dict[str, Any], effect: dict[str, Any],
        metamagic_plan: Any = None,
    ) -> int:
        """``effect["range"]`` after the Druid 15 Elemental Fury upgrade.

        Only the Druid side grows: Improved Elemental Fury extends Druid cantrips
        whose base range is already 10 feet or more by 300 feet, so a Touch or
        Self cantrip keeps its range.  The upgrade is a property of the option
        the character chose, not of the class row, hence the same owner-scoped
        question Potent Spellcasting damage asks.
        """

        base = int(effect["range"])
        # 祈唤给的射程加值先落在"生效射程"上，Distant Spell 再对它翻倍：
        # 反过来的话 15 级 Eldritch Spear 只会多 30 尺。
        extended = base + invocation_effects.spear_range_bonus(
            self.features, self._feature_character(actor),
            str(spell.get("ref") or ""), base,
        )
        if metamagic_plan is not None and metamagic_plan.has("distant_spell"):
            # Distant Spell：射程 ≥5 尺翻倍；触及变 30 尺。Self 不变——"自己"
            # 没有可以拉长的距离。
            text = str(effect.get("range_text") or spell.get("range") or "").lower()
            if text.startswith("self"):
                return base
            # Touch 在数据里也编码成 range: 5，所以看文本而不是看数值，
            # 否则 Cure Wounds 会被"翻倍"成 10 尺。
            if text.startswith("touch"):
                return 30
            if extended < 5:
                return 30
            return extended * 2
        if self._potent_spellcasting_owner(actor, spell) != "class:druid":
            return extended
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "improved_elemental_fury"):
            return extended
        bonus = int(self.features.feature_value(
            character, "improved_elemental_fury", "range_bonus_ft", 0,
        ) or 0)
        minimum = int(self.features.feature_value(
            character, "improved_elemental_fury", "range_minimum_ft", 0,
        ) or 0)
        if not bonus or base < minimum:
            return extended
        return extended + bonus

    def _add_cantrip_damage_bonus(
        self, actor: dict[str, Any], spell: dict[str, Any], spell_ref: str,
        node: dict[str, Any],
    ) -> dict[str, Any]:
        """Add flat per-roll cantrip bonuses as one constant.

        Potent Spellcasting (a class option) and Agonizing Blast (an invocation
        bound to this cantrip) are the same shape: a flat modifier on every
        damage roll.  Summing them here keeps every damage branch calling one
        function instead of each growing another branch, and the constant keeps
        crits from doubling it.
        """

        bonus = self._potent_spellcasting_bonus(actor, spell)
        bonus += invocation_effects.agonizing_bonus(
            self.features, self._feature_character(actor), actor, spell, spell_ref,
        )
        if not bonus:
            return node
        return {
            "op": "add",
            "args": [node, {"op": "constant", "value": bonus}],
        }

    def _cantrip_temp_hp_events(
        self, actor: dict[str, Any], actor_id: str, spell: dict[str, Any],
        effect: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Cleric 14's Improved Blessed Strikes: temp HP after a damaging cantrip.

        The wording gates on the spell ("when you cast a Cleric cantrip that
        deals damage"), not on the outcome, so a target that saved against the
        cantrip still enables the grant.  The amount is twice the Wisdom
        modifier and lands once per casting, on the caster or on one creature
        within 60 feet -- ``_validate_spell`` has already proved both.
        """

        target_id = str(intent.get("cantrip_temp_hp_target_id") or "")
        if not target_id:
            return []
        if int(spell.get("level", 0) or 0) != 0 or not effect.get("damage"):
            return []
        if self._potent_spellcasting_owner(actor, spell) != "class:cleric":
            return []
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "improved_blessed_strikes"):
            return []
        multiplier = int(self.features.feature_value(
            character, "improved_blessed_strikes", "temp_hp_ability_multiplier", 0,
        ) or 0)
        ability = str(self.features.feature_value(
            character, "potent_spellcasting", "ability", "", owner="class:cleric",
        ) or "")
        if not multiplier or not ability:
            return []
        amount = multiplier * ability_modifier(actor["abilities"].get(ability, 10))
        if amount <= 0:
            # 负的 Wisdom 修正不会产生"负临时 HP"：不授予，而不是扣血。
            return []
        return [{
            "type": "resource.changed", "resource": "temp_hp",
            "target_id": target_id, "delta": amount, "amount": amount,
            "source_actor_id": actor_id, "rolls": [],
            "granted_by_feature": "improved_blessed_strikes",
        }]

    def _maybe_overchannel(self, intent: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
        """门控已由 _validate_spell 完成（fail closed）；此处只做变换。"""

        if not intent.get("overchannel"):
            return node
        return maximize_dice_nodes(node)

    def _obscuring_zone_event(
        self, combat: dict[str, Any], intent: dict[str, Any], effect: dict[str, Any],
        actor_id: str, spell_ref: str, slot_level: int, target_ids: list[str],
        spell: dict[str, Any] | None = None, plan: Any = None,
    ) -> dict[str, Any]:
        """The zone a cast leaves behind: a fact on the battlefield, no targets.

        ``origin`` is a coordinate on the same one-dimensional axis positions
        already use, snapshotted at cast time from the first target/caster --
        that is the whole spatial model this slice gets, and it is enough for
        "walk in, walk out" without pretending to be a map.
        """

        anchor_id = target_ids[0] if target_ids else actor_id
        origin = self._position(combat, anchor_id)
        zone_id = f"{spell_ref}:{actor_id}:{intent.get('intent_id', '')}"
        return {
            "type": "dnd2024.zone.created",
            "zone_id": zone_id,
            "kind": "sphere",
            "level": str(effect.get("level") or "heavily"),
            "origin": origin,
            "radius_ft": int(effect.get("radius_ft", 0) or 0),
            "magical": bool(effect.get("magical")),
            "spell_ref": spell_ref,
            "caster_id": actor_id,
            "concentration": bool(effect.get("concentration")),
            "slot_level": slot_level,
            "rounds_left": self._duration_for(
                effect, spell, slot_level, plan,
            ).rounds or 10,
        }

    def _duration_for(
        self, effect: dict[str, Any], spell: dict[str, Any] | None,
        slot_level: int, plan: Any,
    ) -> Any:
        """One place that answers "how long does this cast last".

        Base comes from the catalogue (rounds / condition code / mark hours),
        upcasting adds its own increments, and Extended Spell doubles the
        result — all in `spells.duration`, so zones, conditions and marks can
        never disagree.
        """

        from ..spells import duration

        extended = bool(plan is not None and plan.has("extended_spell"))
        return duration.effective(
            effect, spell, slot_level=int(slot_level or 0), extended=extended,
        )

    def _damage_roll(
        self, damage_node: Any, plan: Any, rng: Any, *, allow_metamagic: bool = True,
    ) -> tuple[int, list[int], dict[str, Any] | None]:
        """Roll spell damage, then let Empowered Spell reroll the lowest dice.

        Re-rolling the *same node* is what keeps each die's size honest: the
        second pass draws d6/d8/d10 exactly where the first did, and only the
        chosen positions are adopted.  Picking the lowest dice is what a player
        would do after seeing them, and it keeps the server the only roller --
        the client never receives dice and sends back a "chosen" subset.
        """

        damage, rolls = roll_damage_node(damage_node, rng)
        if not allow_metamagic or plan is None or not plan.has("empowered_spell"):
            return damage, rolls, None
        count = int(plan.parameter("empowered_spell") or 0)
        if count <= 0 or not rolls:
            return damage, rolls, None
        chosen = sorted(range(len(rolls)), key=lambda index: rolls[index])[
            : min(count, len(rolls))
        ]
        _second, second_rolls = roll_damage_node(damage_node, rng)
        updated = list(rolls)
        for index in chosen:
            updated[index] = second_rolls[index]
        empowered = {
            "dice": chosen,
            "before": [rolls[index] for index in chosen],
            "after": [updated[index] for index in chosen],
        }
        return damage + (sum(updated) - sum(rolls)), updated, empowered

    def _metamagic_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor_id: str, spell: dict[str, Any], effect: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        """Settle the modifiers: pay the points, then hand back the effect.

        Transmuted Spell is applied by rewriting the *effect's* damage type on a
        copy, because every consumer below (damage, resistance, riders) reads
        that field and must see one answer.
        """

        from ..spells import metamagic

        if not (intent.get("metamagic") or []):
            return [], effect, None
        character = self._canonical_for_actor(instance, actor_id)
        stored = self.features.metamagic_known(character)
        known: dict[str, Any] = {option_id: {} for option_id in stored}
        economy = combat.get("economy") or {}
        active = "innate_sorcery" in (character.get("conditions") or {})
        plan = metamagic.plan(
            intent,
            spell=spell,
            effect=effect,
            known=known,
            points=int((character.get("resources") or {}).get("class", {})
                       .get("sorcery_points", {}).get("current", 0) or 0),
            incarnate=active and self.features.has_feature(
                character, "sorcery_incarnate",
            ),
            apotheosis=active and self.features.has_feature(
                character, "arcane_apotheosis",
            ),
            free_used_this_turn=bool(economy.get("free_metamagic_used")),
            cast_level_one_plus_this_turn=bool(economy.get("cast_level_one_plus")),
        )
        events: list[dict[str, Any]] = []
        if plan.cost:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": "sorcery_points", "amount": plan.cost,
            })
        if plan.free_used:
            events.append({
                "type": "dnd2024.metamagic.free_used", "actor_id": actor_id,
            })
        events.append({
            "type": "dnd2024.metamagic.applied", "actor_id": actor_id,
            "options": list(plan.applied), "cost": plan.cost,
        })
        if plan.has("transmuted_spell"):
            changed = dict(effect)
            changed["damage_type"] = plan.parameter("transmuted_spell")
            effect = changed
        return events, effect, plan

    def _spell_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        spell_ref = str(intent["spell_ref"])
        # 起源专长授予的法术按专长自己的施法属性结算命中、DC 与治疗加值。
        actor = self._spell_caster(self._actor_view(instance, combat, actor_id), spell_ref)
        spell = self.spells.get(spell_ref)
        if spell is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("spell is missing")
        effect = self.catalog.spell_effects[spell_ref.removeprefix("spell:")]
        if effect["mode"] == "triggered_damage":
            return self._triggered_spell_events(instance, combat, intent, rng)
        if intent.get("retarget_mark"):
            # 转移标记不是再施一次法：它是同一个法术实例换了个目标，所以
            # 不扣任何施法资源，也不重置剩余时长。
            return self._mark_retarget_events(instance, combat, intent, effect)
        slot_level = int(intent.get("slot_level", spell["level"]) or 0)
        metamagic_events, effect, metamagic_plan = self._metamagic_events(
            instance, combat, intent, actor_id, spell, effect,
        )
        # 一次施法只有一个启用状态：所有目标、所有射线共享它。
        overchannel = bool(intent.get("overchannel"))
        target_ids = intent.get("target_ids")
        if not isinstance(target_ids, list):
            target_ids = [intent.get("target_id")]
        target_ids = [str(item) for item in target_ids if item]
        # 一次施放只允许一次 Empowered / Seeking：多目标与多射线共享同一个
        # "已用过"的标记，否则 1 点就能在三束 Magic Missile 上各重掷一次。
        empowered_spent = False
        seeking_spent = False
        events: list[dict[str, Any]] = metamagic_events + [
            # 施法本身就会终止"施法即结束"的旧法术（例如隐形术的自重刷）。
            *self._spell_end_trigger_events(actor, "spell_cast"),
        ]
        ended_owners = {
            str(event["actor_id"]) for event in events
            if event.get("type") == "dnd2024.concentration.ended"
        }
        events.extend(self._spell_cost_events(
            actor, spell, slot_level,
            quickened=bool(metamagic_plan and metamagic_plan.has("quickened_spell")),
        ))

        if effect.get("concentration"):
            if actor.get("concentration") and actor_id not in ended_owners:
                events.append({
                    "type": "dnd2024.concentration.ended", "actor_id": actor_id,
                    "reason": "new_concentration_spell",
                })
            started: dict[str, Any] = {
                "type": "dnd2024.concentration.started", "actor_id": actor_id,
                "spell_ref": spell_ref, "target_ids": target_ids,
            }
            if metamagic_plan is not None and metamagic_plan.has("extended_spell"):
                # Extended Spell 的"维持专注有优势"跟着这次施放的实例走，
                # 不是角色的常驻状态：法术结束就该消失。
                started["extended"] = True
            if effect.get("marks_target"):
                # 唯一当前目标与剩余时长随实例一起落地：走这条唯一写入点，
                # 才能保证"一个施法者同时只有一个标记"是数据形状而非约定。
                started["mark_target_id"] = target_ids[0]
                started["mark_expires_hours"] = self._mark_duration_hours(
                    effect, slot_level,
                ) * (2 if metamagic_plan is not None
                     and metamagic_plan.has("extended_spell") else 1)
            events.append(started)
        cast_event = {
            "type": "dnd2024.spell.cast", "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": target_ids,
        }
        if overchannel:
            cast_event["overchannel"] = True
        events.append(cast_event)
        if str(effect["mode"]) == "obscuring_zone":
            # 区域法术没有目标结算：落一个区域事实就结束。区域从"目标所在的
            # 那一点"展开并在施放瞬间定死，之后不随生物移动。
            events.append(self._obscuring_zone_event(
                combat, intent, effect, actor_id, spell_ref, slot_level, target_ids,
                spell, metamagic_plan,
            ))
            return events
        # Lucky's advantage face declares one extra source for the first
        # spell attack roll of this cast; validation proves the pool.
        spend_luck = bool(intent.get("spend_luck")) and str(effect["mode"]) == "spell_attack"
        if spend_luck:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": "lucky_points",
            })
        defeated_targets: set[str] = set()
        # 多道射线/多目标按目标累计本次伤害：每束都用施法前的 HP 判断会漏掉
        # 几束合计才击倒的目标，全部敌人倒下时战斗也不会结束。
        dealt: dict[str, int] = {}
        # 真正恢复了 HP 的目标（被 ``cannot_regain_hp`` 拦住的不算）：法术
        # 治疗的两个 rider 都以"实际恢复"为前提，所以这份名单由结算过程
        # 自己记，而不是事后从事件猜。
        healed_targets: list[str] = []
        # 同一次施法的多道射线共用一个事件批次：已消耗条件只在局部隐藏，
        # 事件仍在批次末尾统一移除，状态写入时机不变。
        consumed_conditions: set[tuple[str, str]] = set()
        hit_declarations = held_hit_declarations(intent)
        for ray_index, target_id in enumerate(target_ids):
            target = self._actor_view(instance, combat, target_id)
            mode = str(effect["mode"])
            succeeded = False
            # 光环免疫拦截要把来源写回本次豁免事件；非 saving_throw 路径为 None。
            save_check_index: int | None = None
            if mode == "spell_attack":
                from .check_modes import attack_mode

                visible_actor = _without_consumed_conditions(actor, consumed_conditions)
                visible_target = _without_consumed_conditions(target, consumed_conditions)
                extras: list[dict[str, Any]] = []
                if spend_luck and ray_index == 0:
                    extras.append({"code": "lucky", "mode": "advantage"})
                if (
                    "innate_sorcery" in (visible_actor.get("conditions") or {})
                    and self.is_sorcerer_spell(spell)
                ):
                    # 原文限定"术士法术"：法师法术即使由同一个多职业角色施放，
                    # 也不吃这条优势。
                    extras.append({"code": "innate_sorcery", "mode": "advantage"})
                extra_sources = tuple(extras)
                check_mode = attack_mode(
                    self, instance, visible_actor, visible_target, False,
                    extra_sources=extra_sources, combat=combat,
                )
                rolls = [int(rng.randint(1, 20))]
                if check_mode["mode"]:
                    rolls.append(int(rng.randint(1, 20)))
                roll = max(rolls) if check_mode["mode"] == "advantage" else min(rolls)
                bonus = int(actor["spell_attack_bonus"])
                check_bonus_rolls = condition_check_bonus_rolls(
                    self.catalog.conditions,
                    self._effective_conditions(instance, combat, visible_actor),
                    "attack", rng,
                )
                # 加值骰逐掷骰各一枚：多道射线逐 beam 各掷一次。
                check_bonus = sum(row["roll"] for row in check_bonus_rolls)
                # 破甲一击的 +5 说的是"另一次攻击掷骰"，不区分武器还是法术：
                # 只给武器路径加会让友军的 Fire Bolt 白打，而条件挂到自然过期。
                sundering_bonus, sundering_condition = self._sundering_bonus(
                    visible_target, actor_id,
                )
                # 法术攻击同样对着被掩蔽的 AC：掩蔽是目标的位置事实，不是
                # 武器攻击的特例。
                cover = self._cover_record(instance, combat, target)
                total = roll + bonus + check_bonus + sundering_bonus
                target_ac = effective_armor_class(target) + int(
                    cover["armor_class_bonus"]
                )
                succeeded = roll != 1 and (roll == 20 or total >= target_ac)
                seeking: dict[str, Any] | None = None
                if (
                    not succeeded
                    and not seeking_spent
                    and metamagic_plan is not None
                    and metamagic_plan.has("seeking_spell")
                ):
                    seeking_spent = True
                    # Seeking Spell：未命中时重掷 d20 并**必须采用新结果**。
                    # 只重掷那一颗，加值与掩蔽都不重来。
                    first = roll
                    roll = int(rng.randint(1, 20))
                    total = roll + bonus + check_bonus + sundering_bonus
                    succeeded = roll != 1 and (roll == 20 or total >= target_ac)
                    seeking = {"before": first, "after": roll}
                critical = roll == 20
                if (
                    succeeded and not critical
                    and condition_flags(self.catalog.conditions, target["conditions"])["crit_on_hit_within_5"]
                    and self._distance(combat, actor_id, target_id) <= 5
                ):
                    # 法术攻击也是攻击检定：5 尺内命中麻痹/昏迷目标自动重击。
                    critical = True
                if sundering_bonus and sundering_condition:
                    # 消费时机与武器路径一致：+5 已经加进这一掷，命中与否都算用过。
                    events.append({
                        "type": "condition.removed", "target_id": target_id,
                        "condition": sundering_condition,
                        "reason": "sundering_consumed",
                    })
                    consumed_conditions.add((target_id, sundering_condition))
                events.append({
                    "type": "check.resolved", "kind": "spell_attack", "actor_id": actor_id,
                    **({"seeking": seeking} if seeking else {}),
                    "target_id": target_id, "rolls": rolls, "natural": roll,
                    "advantage_mode": check_mode["mode"], "advantage_sources": check_mode["sources"],
                    "modifier": bonus,
                    "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
                    "total": total,
                    **({"sundering_bonus": sundering_bonus} if sundering_bonus else {}),
                    "target": target_ac, "success": succeeded, "critical": critical,
                    **(
                        {
                            "cover_bonus": int(cover["armor_class_bonus"]),
                            "cover_sources": cover["sources"],
                        }
                        if int(cover["armor_class_bonus"]) else {}
                    ),
                })
                if succeeded:
                    # S0-EFFECT1 P4b：与命中触发法术共用同一个"效果项 → 伤害节点"。
                    _, payload_items = normalize_spell_effect(effect)
                    damage_node = damage_node_for(
                        payload_items[0], upcast_damage=effect.get("upcast_damage"),
                        spell=spell, slot_level=slot_level, actor=actor,
                        critical=critical,
                    )
                    # Potent Spellcasting 是加值不是骰：先翻倍骰数再追加，
                    # 否则重击会把这个固定加值也一起翻倍。
                    damage_node = self._add_cantrip_damage_bonus(
                        actor, spell, spell_ref, damage_node,
                    )
                    damage_node = self._maybe_overchannel(intent, damage_node)
                    damage, rolls, empowered = self._damage_roll(
                        damage_node, metamagic_plan, rng,
                        allow_metamagic=not empowered_spent,
                    )
                    empowered_spent = empowered_spent or bool(empowered)
                    if empowered:
                        events.append({
                            "type": "dnd2024.metamagic.rerolled",
                            "actor_id": actor_id, **empowered,
                        })
                    damage_type = str(
                        intent.get("damage_type") or effect.get("damage_type") or ""
                    )
                    damage, resistance = self._resist_damage(target, damage, damage_type)
                    damage_event = {
                        "type": "resource.changed", "resource": "hp", "target_id": target_id,
                        "delta": -damage, "amount": damage,
                        "damage_type": damage_type,
                        "critical": critical, "rolls": rolls,
                    }
                    if overchannel:
                        damage_event["overchannel"] = True
                    if resistance:
                        damage_event["resisted"] = True
                        damage_event["resistance"] = resistance
                    relentless = self._relentless_rage_marker(instance, target)
                    if relentless:
                        damage_event["relentless"] = relentless
                    events.append(damage_event)
                    # Repelling Blast：每次命中推一次，距离由玩家声明（校验
                    # 已确认 0-10、绑定这把戏法）。多道射线各自命中就各自推——
                    # 规则说的是"每次命中该戏法"；目标过大则这一次不推。
                    push_feature, push_ft = invocation_effects.repelling_push_ft(
                        self.features, self._feature_character(actor),
                        spell_ref, intent.get("spell_push_ft"),
                        str(target.get("size") or ""),
                    )
                    if push_ft:
                        direction = (
                            1 if self._position(combat, target_id)
                            >= self._position(combat, actor_id) else -1
                        )
                        events.append({
                            "type": "dnd2024.position.changed", "actor_id": target_id,
                            "distance": direction * push_ft, "forced": True,
                            "source_rider": push_feature,
                        })
                    # Hunter's Mark 认的是"任意攻击检定命中"，法术攻击也在内：
                    # 独立实例、独立抗性，但只算一次专注豁免。
                    mark_row = self._mark_rider_row(actor, target_id)
                    mark_event, mark_damage = (
                        self._rider_damage_event(mark_row, target, critical, rng)
                        if mark_row is not None else (None, 0)
                    )
                    if mark_event is not None:
                        events.append(mark_event)
                    events.extend(self._concentration_events(
                        instance, combat, target, damage + mark_damage, rng,
                    ))
                    dealt[target_id] = dealt.get(target_id, 0) + damage + mark_damage
                    total_dealt = dealt[target_id]
                    if (
                        target["kind"] == "enemy"
                        and target["hp"] - total_dealt <= 0
                        and target_id not in defeated_targets
                    ):
                        # 只有首次跨过 0 HP 的那一束触发；同批次后续射线照旧
                        # 产生伤害事件，但不再授予第二次临时 HP。
                        defeated_targets.add(target_id)
                        events.extend(self._enemy_zero_hp_feature_events(
                            instance, combat, actor_id, target, total_dealt,
                        ))
                    # A cast declares these effects for its first successful
                    # attack roll. A miss does not spend them; later rays do
                    # not get a fresh use from the unchanged actor snapshot.
                    if hit_declarations:
                        events.extend(committed_hit_events(
                            self, instance, combat, actor, target, hit_declarations, rng,
                        ))
                        hit_declarations = {}
                consumed_events = self._consume_attack_conditions(
                    actor_id, target_id, visible_actor, visible_target,
                )
                consumed_conditions.update(
                    (str(event["target_id"]), str(event["condition"]))
                    for event in consumed_events
                )
                events.extend(consumed_events)
            elif mode == "saving_throw":
                save_ability = str(effect["save"])
                spell_dc = self._spell_save_dc(actor, spell)
                base_dc = int(actor.get("spell_save_dc", 0) or 0)
                careful_targets = (
                    metamagic_plan.parameter("careful_spell")
                    if metamagic_plan and metamagic_plan.has("careful_spell") else ()
                )
                heightened = (
                    metamagic_plan.parameter("heightened_spell")
                    if metamagic_plan and metamagic_plan.has("heightened_spell") else ""
                )
                forced = ""
                careful_here = bool(careful_targets and target_id in careful_targets)
                if careful_here:
                    forced = "success"
                elif heightened and str(heightened) == target_id:
                    forced = "disadvantage"
                check_event = self._saving_throw_check(
                    instance, combat, target, target_id, save_ability,
                    spell_dc, actor_id, events, rng,
                    # Magic Resistance 只对抗法术的豁免：这一处是唯一由法术
                    # 产出的豁免，其余的（呼吸、rider）不享受它。
                    against_spell=True,
                    forced_outcome=forced,
                )
                if spell_dc != base_dc:
                    # Innate Sorcery 的 +1 要能在日志里对账：`target` 是实际 DC，
                    # `dc_base` 是基础值。只把和写进 target 会让"为什么这次是 16
                    # 不是 15"无据可查。
                    check_event["dc_base"] = base_dc
                    check_event["innate_sorcery"] = True
                if not check_event.get("auto_failed"):
                    # 掩蔽只帮敏捷豁免，Indomitable Might 用力量值替换总值；
                    # 两者都只改总数，掷骰面与 check_bonus 保持原样。自动失败
                    # 的豁免没有掷骰，两者都不该凭空给它一个总数。
                    cover = self._cover_save_bonus(
                        instance, combat, target, save_ability,
                        # 效应自己声明"水平掩体与我无关"时不读等级。
                        axis=cover_axis(effect),
                    )
                    total = int(check_event["total"]) + int(cover["bonus"])
                    total, replacement = self._strength_total_replacement(
                        target, save_ability, total,
                    )
                    if int(cover["bonus"]):
                        check_event["cover_bonus"] = int(cover["bonus"])
                        check_event["cover_sources"] = cover["sources"]
                    check_event["cover_level"] = str(cover.get("level") or "none")
                    check_event["cover_interaction"] = cover_axis(effect)[0]
                    check_event.update(replacement)
                    check_event["total"] = total
                    check_event["success"] = total >= spell_dc
                succeeded = check_event["success"]
                save_check_index = len(events)
                events.append(check_event)
                # 无论成功、失败还是自动失败，这次豁免都算"用掉了"那份
                # "下一次豁免有劣势"：消费点就是豁免本身，不是回合边界。
                events.extend(self._consume_save_markers(target_id, target))
                if effect.get("damage"):
                    damage_node = spell_formula_node(
                        str(effect["damage"]), effect.get("upcast_damage"),
                        spell, slot_level, actor,
                    )
                    # 加值先并入骰式，再走豁免减半与抗性：三者都只作用于
                    # 同一个总数，顺序不能倒。
                    damage_node = self._add_cantrip_damage_bonus(
                        actor, spell, spell_ref, damage_node,
                    )
                    damage_node = self._maybe_overchannel(intent, damage_node)
                    damage, rolls, empowered = self._damage_roll(
                        damage_node, metamagic_plan, rng,
                        allow_metamagic=not empowered_spent,
                    )
                    empowered_spent = empowered_spent or bool(empowered)
                    if empowered:
                        events.append({
                            "type": "dnd2024.metamagic.rerolled",
                            "actor_id": actor_id, **empowered,
                        })
                    half_on_success = bool(effect.get("half_on_success"))
                    if half_on_success and self._evasion_applies(target, save_ability):
                        # Evasion 改写的是"这条允许敏捷豁免半伤"的效果分支，不是
                        # 所有敏捷豁免：成功 0、失败 floor(amount/2)，之后抗性
                        # 仍只应用一次（顺序固定，不能重复减半）。
                        damage = 0 if succeeded else damage // 2
                    elif succeeded and careful_here:
                        # Careful Spell：自动成功的那个生物"若成功本应半伤则
                        # 不受伤"，所以这里是 0 而不是减半。
                        damage = 0
                    elif succeeded:
                        damage = damage // 2 if half_on_success else 0
                    damage_type = str(effect.get("damage_type") or "force")
                    # 抗性在豁免减半之后结算（RAW 顺序：先修正，后抗性）。
                    damage, resistance = self._resist_damage(target, damage, damage_type)
                    if damage:
                        damage_event = {
                            "type": "resource.changed", "resource": "hp",
                            "target_id": target_id, "delta": -damage, "amount": damage,
                            "damage_type": damage_type,
                            "critical": False, "rolls": rolls,
                        }
                        if overchannel:
                            damage_event["overchannel"] = True
                        if resistance:
                            damage_event["resisted"] = True
                            damage_event["resistance"] = resistance
                        relentless = self._relentless_rage_marker(instance, target)
                        if relentless:
                            damage_event["relentless"] = relentless
                        events.append(damage_event)
                        events.extend(self._concentration_events(instance, combat, target, damage, rng))
                        dealt[target_id] = dealt.get(target_id, 0) + damage
                        total_dealt = dealt[target_id]
                        if (
                            target["kind"] == "enemy"
                            and target["hp"] - total_dealt <= 0
                            and target_id not in defeated_targets
                        ):
                            defeated_targets.add(target_id)
                            events.extend(self._enemy_zero_hp_feature_events(
                                instance, combat, actor_id, target, total_dealt,
                            ))
            elif mode == "automatic_damage":
                damage_node = spell_formula_node(
                    str(effect["damage"]), effect.get("upcast_damage"),
                    spell, slot_level, actor,
                )
                damage_node = self._add_cantrip_damage_bonus(
                        actor, spell, spell_ref, damage_node,
                    )
                damage_node = self._maybe_overchannel(intent, damage_node)
                damage, rolls, empowered = self._damage_roll(
                    damage_node, metamagic_plan, rng,
                    allow_metamagic=not empowered_spent,
                )
                empowered_spent = empowered_spent or bool(empowered)
                if empowered:
                    events.append({
                        "type": "dnd2024.metamagic.rerolled",
                        "actor_id": actor_id, **empowered,
                    })
                damage_type = str(effect.get("damage_type") or "force")
                damage, resistance = self._resist_damage(target, damage, damage_type)
                damage_event = {
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": -damage, "amount": damage,
                    "damage_type": damage_type,
                    "critical": False, "rolls": rolls,
                }
                if overchannel:
                    damage_event["overchannel"] = True
                if resistance:
                    damage_event["resisted"] = True
                    damage_event["resistance"] = resistance
                relentless = self._relentless_rage_marker(instance, target)
                if relentless:
                    damage_event["relentless"] = relentless
                events.append(damage_event)
                events.extend(self._concentration_events(instance, combat, target, damage, rng))
                dealt[target_id] = dealt.get(target_id, 0) + damage
                total_dealt = dealt[target_id]
                if (
                    target["kind"] == "enemy"
                    and target["hp"] - total_dealt <= 0
                    and target_id not in defeated_targets
                ):
                    defeated_targets.add(target_id)
                    events.extend(self._enemy_zero_hp_feature_events(
                        instance, combat, actor_id, target, total_dealt,
                    ))
                succeeded = True
            elif mode == "healing":
                healing_node = spell_formula_node(
                    str(effect["healing"]), effect.get("upcast_healing"),
                    spell, slot_level, actor,
                )
                if effect.get("add_spell_ability"):
                    healing_node = heal_ability_modifier_node(healing_node, actor)
                # Owner-scoped on purpose: the short id alone would let any
                # future class that declares a same-named feature turn this
                # heal into a maximized one.
                maximized = self.features.has_feature(
                    self._feature_character(actor),
                    "supreme_healing",
                    owner="subclass:life_domain",
                )
                if maximized:
                    healing_node = maximize_dice_nodes(healing_node)
                healing, rolls = roll_damage_node(healing_node, rng)
                healing = max(1, healing)
                # 颤栗之触：目标在施法者回合结束前无法恢复生命值。
                blocked = "cannot_regain_hp" in target["conditions"]
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": 0 if blocked else healing, "amount": 0 if blocked else healing,
                    "healing": True, "rolls": rolls,
                    **({"maximized": True} if maximized else {}),
                    **({"blocked_by": "cannot_regain_hp"} if blocked else {}),
                })
                if not blocked and healing > 0:
                    healed_targets.append(target_id)
                    events.extend(self._healing_rider_events(
                        actor, target_id, target, slot_level, spell, actor_id,
                    ))
                succeeded = True
            elif mode == "temp_hp":
                # 升环是固定加值，不走 spell_formula_node 的同面骰 upcast 参数。
                node = spell_formula_node(str(effect["temp_hp"]), None, spell, slot_level, actor)
                levels = slot_level - int(spell["level"])
                flat = int(effect.get("upcast_flat", 0) or 0)
                if flat and levels > 0:
                    node = {"op": "add", "args": [node, {"op": "constant", "value": flat * levels}]}
                # Fiendish Vigor：经它自己的免槽施放时骰面取最大（False Life
                # 固定 12）。是否走的是那个槽由角色卡上的免槽行回答，不看
                # "这次施法是不是免费的"——两种都免费时不能混为一谈。
                paid = (
                    free_cast_resource(actor, spell_ref, int(spell["level"]))
                    if int(slot_level or 0) == 0 else None
                )
                maximized = invocation_effects.temp_hp_maximized(
                    self.features, self._feature_character(actor),
                    list(actor.get("free_cast_slots") or []), slot_level,
                    spell_ref, str((paid or {}).get("free_slot_id") or ""),
                )
                if maximized:
                    node = maximize_dice_nodes(node)
                amount, rolls = roll_damage_node(node, rng)
                events.append({
                    "type": "resource.changed", "resource": "temp_hp", "target_id": target_id,
                    "delta": amount, "amount": amount, "rolls": rolls,
                    **({"maximized": True} if maximized else {}),
                })
                succeeded = True
            elif mode == "teleport":
                # 传送不是移动：事件无 movement_cost，不扣经济、不触发借机攻击。
                events.append({
                    "type": "dnd2024.position.changed", "actor_id": target_id,
                    "distance": int(intent["teleport_distance"]), "forced": True,
                })
            elif mode == "dispel":
                # 逐 condition 判定：≤ 施放环阶自动终止；更高环阶走一次施法属性检定。
                # 专注来源整道终止（reducer 级联移除全部目标），非专注来源只移除本目标。
                dispelled_owners: set[str] = set()
                for condition_id, meta in list(target["conditions"].items()):
                    if not isinstance(meta, dict) or not meta.get("source_spell_ref"):
                        continue
                    source_level = int(meta.get("source_spell_level", 0) or 0)
                    if source_level > slot_level:
                        modifier = ability_modifier(actor["abilities"][actor["spell_ability"]])
                        roll = int(rng.randint(1, 20))
                        success = roll + modifier >= 10 + source_level
                        events.append({
                            "type": "check.resolved", "kind": "ability_check",
                            "actor_id": actor_id, "target_id": target_id,
                            "ability": actor["spell_ability"], "rolls": [roll],
                            "natural": roll, "modifier": modifier,
                            "check_bonus": 0, "check_bonus_rolls": [],
                            "total": roll + modifier, "target": 10 + source_level,
                            "success": success, "condition": condition_id,
                        })
                        if not success:
                            continue
                    owner = str(meta.get("concentration_owner") or "")
                    if not owner:
                        events.append({
                            "type": "condition.removed", "target_id": target_id,
                            "condition": condition_id, "reason": "dispelled",
                        })
                    elif owner not in dispelled_owners:
                        events.append({
                            "type": "dnd2024.concentration.ended", "actor_id": owner,
                            "reason": "dispelled",
                        })
                        dispelled_owners.add(owner)
            elif mode == "stabilize":
                events.append({
                    "type": "condition.applied", "target_id": target_id,
                    "condition": "stable", "duration": "until_healed",
                    "source_actor_id": actor_id,
                })
                succeeded = True
            elif mode == "revive":
                # 复活不是治疗：不经过 healing 管线、不发 resource.changed。
                # 状态语义（hp=1、清 dead/unconscious/stable/death_saves）由
                # reducer 的 character.revived 处理器承载，这里只发事件。
                events.append({
                    "type": "dnd2024.character.revived",
                    "actor_id": target_id, "hp": 1,
                })
                succeeded = True
            elif mode in {"buff", "debuff"}:
                # 标记类法术也走这条分支：它们不造成伤害、不施加条件，只把
                # "谁是我的目标"写进施法者的专注实例（已在上方落地）。
                succeeded = True
            condition = str(effect.get("condition") or "")
            condition_applies = (
                (mode in {"spell_attack", "automatic_damage"} and succeeded)
                or (mode == "saving_throw" and not succeeded)
                or mode in {"buff", "debuff"}
            )
            # 条件免疫：命中则根本不产生 condition.applied，并把拦截来源写回
            # 本次豁免事件（没有 check event 时改用一条 message 留痕）。
            # 豁免本身仍然是失败——被免疫的是条件的落地，不是掷骰结果。
            # 两个字段名区分"站在谁的灵光里"与"我自己带着免疫"：玩家看到的
            # 拦截原因不能只有一句"没生效"。
            # ``blocked`` is a bool in the healing branch above and the immunity
            # record here; mypy keeps the first inference, so both this
            # redeclaration and the dict below carry a waiver.  Giving the two
            # branches distinct names is the real fix, and it belongs with the
            # next change to this method rather than to a move.
            blocked: dict[str, str] | None = None  # type: ignore[no-redef]
            blocked_field = ""
            if condition and condition_applies:
                blocker = self._condition_immunity_blocker(
                    instance, combat, target, condition,
                )
                if blocker is not None:
                    scope = str(blocker.get("scope") or "")
                    blocked = {  # type: ignore[assignment]
                        key: value for key, value in blocker.items() if key != "scope"
                    }
                    blocked_field = (
                        "blocked_by_aura" if scope == "aura" else "blocked_by_feature"
                    )
            if blocked:
                if save_check_index is not None:
                    events[save_check_index]["blocked_condition"] = condition
                    events[save_check_index][blocked_field] = blocked
                else:
                    # 免疫不依赖豁免：buff/debuff/自动伤害同样经过这里。这些
                    # 路径没有 check event 可挂，但被吞掉的条件必须留痕，否则
                    # 只能靠怀疑；用既有 message 类型，不新增事件类型。
                    events.append({
                        "type": "dnd2024.combat.message", "target_id": target_id,
                        "text": f"{condition} has no effect on this creature",
                        "condition": condition,
                        "blocked_condition": condition,
                        blocked_field: blocked,
                    })
            elif condition and condition_applies:
                applied = {
                    "type": "condition.applied", "target_id": target_id,
                    "condition": condition,
                    "duration": self._duration_for(
                        effect, spell, slot_level, metamagic_plan,
                    ).as_condition_text(),
                    "source_actor_id": actor_id,
                    "concentration_owner": actor_id if effect.get("concentration") else "",
                    # 服务端来源快照：解除魔法只认这两个键，客户端不可声明。
                    "source_spell_ref": spell_ref,
                    "source_spell_level": (
                        int(slot_level) if int(slot_level) > 0 else int(spell["level"])
                    ),
                }
                # 服务端从目录效果拷贝提前终止声明；客户端永远无法声明它。
                if effect.get("ends_on") is not None:
                    applied["ends_on"] = deepcopy(effect["ends_on"])
                # 选择型抗性：施法时选定的伤害类型写进 condition 元数据，
                # 之后由结算期的抗性辅助读取（客户端不能直接写元数据）。
                if effect.get("resist_choice"):
                    applied["resist_damage_types"] = [str(intent["resist_type"])]
                # 每回合重复豁免：DC 是施放当刻的快照，之后施法者属性变化不影响。
                # 快照取的是**这个法术**的 DC：Innate Sorcery 的 +1 只对术士法术生
                # 效，若这里读基础 DC，首次豁免与后续豁免会差 1。
                if effect.get("save_repeat") is not None:
                    applied["save_repeat"] = {
                        "ability": str(effect["save_repeat"]),
                        "dc": self._spell_save_dc(actor, spell),
                    }
                for removed in effect.get("removes_conditions") or []:
                    if str(removed) in target["conditions"]:
                        events.append({
                            "type": "condition.removed", "target_id": target_id,
                            "condition": str(removed),
                        })
                events.append(applied)
                # 失能打断专注：目标自己正在专注时，施加失能族要终止其专注，
                # 由 reducer 级联移除该目标名下的专注 condition。
                if (self.catalog.conditions.get(condition) or {}).get(
                    "implies_incapacitated"
                ) and any(
                    isinstance(meta, dict)
                    and str(meta.get("concentration_owner") or "") == str(target_id)
                    for meta in (target.get("conditions") or {}).values()
                ):
                    events.append({
                        "type": "dnd2024.concentration.ended", "actor_id": target_id,
                        "reason": "incapacitated",
                    })
            if succeeded and int(effect.get("push", 0) or 0):
                direction = 1 if self._position(combat, target_id) >= self._position(combat, actor_id) else -1
                events.append({
                    "type": "dnd2024.position.changed", "actor_id": target_id,
                    "feet": int(effect["push"]), "away_from": actor_id,
                    "distance": direction * int(effect["push"]), "forced": True,
                })
        # 使用计数与自伤都落在法术效果之后、胜利判定之前：计数是这次施法的
        # 一部分，自伤是"施法后立即"的代价，二者都不该被战斗结束顺序吞掉。
        if overchannel:
            character = self._feature_character(actor)
            before = feature_usage_count(
                character, OVERCHANNEL_OWNER, OVERCHANNEL_FEATURE_ID,
                USAGE_SCOPE_LONG_REST,
            )
            use_index = before + 1
            events.append({
                "type": "dnd2024.feature_usage.changed",
                "actor_id": actor_id,
                "owner": OVERCHANNEL_OWNER,
                "feature_id": OVERCHANNEL_FEATURE_ID,
                "scope": USAGE_SCOPE_LONG_REST,
                "spell_ref": spell_ref,
                "slot_level": slot_level,
                "before_count": before,
                "after_count": use_index,
                "use_index": use_index,
            })
            if use_index >= 2:
                # 第 N 次（N ≥ 2）：每环阶掷 N 个 d12 黯蚀。声明式基数 2 加上
                # "每再用一次 +1d12"，与 SRD 原文的递增表述一一对应。
                base = int(self.features.feature_value(
                    character, OVERCHANNEL_FEATURE_ID, "initial_self_damage_dice", 2,
                    owner=OVERCHANNEL_OWNER,
                ) or 0)
                sides = int(self.features.feature_value(
                    character, OVERCHANNEL_FEATURE_ID, "self_damage_die", 12,
                    owner=OVERCHANNEL_OWNER,
                ) or 0)
                per_level = base + (use_index - 2)
                cost_node = formula_node_from_dice_formula(
                    f"{per_level * slot_level}d{sides}"
                )
                cost, cost_rolls = roll_damage_node(cost_node, rng)
                # 无视抗性与免疫：不走 _resist_damage；临时 HP 仍由 reducer 先吸收。
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": actor_id,
                    "delta": -cost, "amount": cost, "damage_type": "necrotic",
                    "overchannel": True, "overchannel_cost": True,
                    "ignore_resistance": True, "ignore_immunity": True,
                    "rolls": cost_rolls,
                })
                events.extend(self._concentration_events(instance, combat, actor, cost, rng))
        # 提升的祝福打击落在本次施法末：临时 HP 不是"命中后的效果"，它与
        # 目标是否真的掉血无关，所以不挂在任何单个目标的结算链上。
        events.extend(self._cantrip_temp_hp_events(actor, actor_id, spell, effect, intent))
        # Blessed Healer 按"这次施法"结算一次，所以落在目标循环之外：三个
        # 目标吃到的是一次治疗后的 2+环阶，而不是三份。
        if slot_level > 0 and any(
            target_id != actor_id for target_id in healed_targets
        ):
            events.extend(self._blessed_healer_events(
                actor, actor_id, slot_level, spell,
            ))
        living_enemy_ids = {
            _enemy_actor(enemy_id)
            for enemy_id, enemy in combat.get("enemies", {}).items()
            if int(enemy.get("hp", 0) or 0) > 0
        }
        if living_enemy_ids and living_enemy_ids.issubset(defeated_targets):
            events.append({"type": "dnd2024.combat.ended", "reason": "victory"})
        return events

    def _innate_sorcery_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        capability: Any,
    ) -> list[dict[str, Any]]:
        """Innate Sorcery: one minute of +1 DC and Advantage.

        The price is not decided here: the capability declares it（1 次使用，
        或次数用完后 2 点术法点），通用的成本块在本方法之前已经付掉。这里只
        记录**付的是哪一种**，因为 Arcane Apotheosis 与审计都要区分两条路径。
        """

        actor_id = str(intent["actor_id"])
        paid = "use" if capability.id == "innate_sorcery" else "points"
        return [{
            "type": "condition.applied", "target_id": actor_id,
            "condition": "innate_sorcery",
            "source_actor_id": actor_id,
            "paid_with": paid,
            "expires_at_minute": self._logical_minutes(instance) + 1,
        }]
