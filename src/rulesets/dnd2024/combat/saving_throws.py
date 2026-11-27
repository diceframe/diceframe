"""Saving throws, ability checks, death saves and the resource/repeat-save handling around them.

One of the resolution mixins; the engine composes them. The class holds no
state of its own."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from src.rulesets.dnd2024.character.builder import ability_modifier
from ..features.resources import long_effect_active
from .primitives import world_minutes, CombatIntentError, LEGENDARY_RESISTANCE_DEFAULT_PER_DAY, trait_ids, trait_params
from .check_modifiers import active_modifiers, check_total
from .check_modes import condition_check_bonus_rolls, condition_flags, mode_from_sources

INDOMITABLE_MIGHT = "indomitable_might"


class CombatSavingThrowsMixin:
    """Saving throws, ability checks, death saves and the resource/repeat-save handling around them."""

    __slots__ = ()

    if TYPE_CHECKING:
        _feature_character: Any
        features: Any
        catalog: Any
        _actor_view: Any
        _damage_instance_event: Any
        _declared_action: Any
        @staticmethod
        def _roll_id(
            events: list[dict[str, Any]], kind: str, actor_id: str,
        ) -> str:
            ...
        _ability_check_window_offer: Any
        _cover_save_bonus: Any
        _saving_throw_aura_bonus: Any
        _consume_save_markers: Any
        _save_window_offer: Any
        _death_save_check_window: Any
        def _end_turn_events(
            self, instance: Any, combat: dict[str, Any], rng: Any,
            intent: dict[str, Any] | None = None, *, settled_condition: str = "",
        ) -> list[dict[str, Any]]:
            ...

    def _skill_check_floor(
        self, actor: dict[str, Any], skill_id: str,
    ) -> tuple[int, list[dict[str, str]]]:
        """The d20 floor for one skill check, gated on real proficiency.

        Reliable Talent applies only to a check that "uses one of your skill or
        tool proficiencies".  The gate is read from the actor's ``skill_refs``,
        not from the derived number: ``+2`` cannot say whether it came from
        proficiency or from the ability modifier, and a floor on an untrained
        skill would silently turn every rogue into a specialist.
        """

        refs = {str(ref) for ref in actor.get("skill_refs") or []}
        if f"skill:{skill_id}" not in refs:
            return 0, []
        return self.features.ability_check_floor(self._feature_character(actor))

    def _legendary_resistance_events(
        self, actor: dict[str, Any], actor_id: str, succeeded: bool,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """S0-MM3d: turn a failed save into a success, at the cost of one use.

        The creature's own declaration decides whether the trait exists and how
        many uses a day it has; the uses are counted in the same ``usage`` ledger
        as recharge actions, so a save/load round trip keeps them.  A creature
        with no uses left simply fails: exhaustion must not be papered over.

        Known limitation (registered, not hidden): the trait is applied
        automatically.  "You can choose to succeed" is a decision, and there is
        no GM-facing decline channel for it yet — a window would have to be
        threaded through every place a save is rolled.
        """

        if succeeded:
            return {"used": False, "remaining": 0}
        traits = actor.get("traits")
        if "legendary_resistance" not in trait_ids(traits):
            return {"used": False, "remaining": 0}
        per_day = int(
            trait_params(traits, "legendary_resistance").get(
                "per_day", LEGENDARY_RESISTANCE_DEFAULT_PER_DAY,
            ) or LEGENDARY_RESISTANCE_DEFAULT_PER_DAY
        )
        used = int(((actor.get("usage") or {}).get("trait:legendary_resistance") or {})
                   .get("used", 0) or 0)
        if used >= per_day:
            return {"used": False, "remaining": 0}
        events.append({
            "type": "dnd2024.legendary_resistance.spent",
            "actor_id": actor_id,
            "per_day": per_day,
        })
        return {"used": True, "remaining": per_day - used - 1}

    def _saving_throw_sources(
        self, actor: dict[str, Any], ability: str, *, against_spell: bool = False,
    ) -> list[dict[str, Any]]:
        """Every declared Advantage/Disadvantage source on one saving throw.

        Rage's Strength save and untrained armor are code facts of the combat
        authority; Danger Sense is read from its own declaration so a second
        ability would never need a new branch.  The aggregation stays in one
        place: two sources of Advantage still roll two dice, and Advantage plus
        Disadvantage collapses to a straight roll.
        """

        conditions = actor.get("conditions") or {}
        flags = condition_flags(self.catalog.conditions, conditions)
        sources: list[dict[str, Any]] = []
        if "rage" in conditions and ability == "str":
            sources.append({"code": "rage", "mode": "advantage"})
        if "next_save_disadvantage" in conditions:
            # Staggering Blow. It is a marker rather than a registry declaration
            # because no registry field can say "the *next* saving throw, then
            # gone": the consumption point is the save itself, not a turn
            # boundary, so the same code that reads it also removes it.
            sources.append({"code": "next_save_disadvantage", "mode": "disadvantage"})
        if bool(
            (actor.get("armor_training") or {}).get("untrained_armor_worn")
        ) and ability in {"str", "dex"}:
            sources.append({"code": "untrained_armor", "mode": "disadvantage"})
        if not flags["incapacitated"]:
            character = self._feature_character(actor)
            declared = self.features.feature_value(
                character, "danger_sense", "save_advantage_abilities", [],
            )
            if isinstance(declared, list) and ability in {str(item) for item in declared}:
                sources.append({"code": "danger_sense", "mode": "advantage"})
        return sources

    def _strength_total_replacement(
        self, target: dict[str, Any], ability: str, total: int,
    ) -> tuple[int, dict[str, Any]]:
        """Indomitable Might: judge with the Strength score when it is higher.

        The rule replaces the *total*, not the die, so the d20 face and
        ``rolls`` stay exactly what was rolled and the event carries both
        numbers: ``ordinary_total`` is what the roll and every modifier
        produced, ``replacement_score`` is the score that may stand in for it.
        Nothing is rolled and no die is added, so the RNG order of the batch is
        unchanged.

        Only Strength checks and Strength saving throws reach this: every call
        site passes the ability it actually rolled, and anything else comes
        back untouched, so attacks, initiative, damage, Dex/Con saves and death
        saves keep their own rules.  An auto-failed save has no total to
        replace and never reaches here either.
        """

        if ability != "str":
            return total, {}
        if not self.features.strength_total_replacement(
            self._feature_character(target),
        ):
            return total, {}
        score = int((target.get("abilities") or {}).get("str", 10) or 10)
        if total >= score:
            return total, {
                "ordinary_total": total,
                "replacement_score": score,
                "total_replaced": False,
                "feature_id": INDOMITABLE_MIGHT,
            }
        return score, {
            "ordinary_total": total,
            "replacement_score": score,
            "total_replaced": True,
            "feature_id": INDOMITABLE_MIGHT,
        }

    def _save_action_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """S0-MM3c: one save, then one of two declared outcomes.

        The save is the target's, rolled by the server; the damage comes from
        the branch the result selects.  Each part of that branch is its own
        resisted instance, so a fire breath that also dealt bludgeoning would
        not be halved as a lump.  ``usage_limits`` is settled in the same batch
        as the effect: an action that was spent must read as spent even if the
        target saved.
        """

        actor_id = str(intent["actor_id"])
        target_id = str(intent["target_id"])
        actor = self._actor_view(instance, combat, actor_id)
        action = self._declared_action(actor, intent)
        if action is None:  # pragma: no cover - validation guards this
            raise CombatIntentError("save action profile is not available to the actor")
        target = self._actor_view(instance, combat, target_id)
        action_id = str(action.get("id") or "")
        events: list[dict[str, Any]] = [
            {
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": self._spend_action_resource(combat), "amount": 1,
            },
            {
                "type": "dnd2024.action.used", "actor_id": actor_id,
                "action_id": action_id,
                "usage_limits": deepcopy(action.get("usage_limits")),
            },
        ]
        check = self._saving_throw_check(
            instance, combat, target, target_id,
            str(action.get("ability") or ""), int(action.get("dc") or 0),
            actor_id, events, rng,
        )
        check["source_action"] = action_id
        events.append(check)
        branch = action.get("on_fail") if not check["success"] else action.get("on_success")
        for row in branch or []:
            if not isinstance(row, dict):
                continue
            for part in row.get("damage") or []:
                if not isinstance(part, dict) or not part.get("dice"):
                    continue
                event, _amount = self._damage_instance_event(
                    str(part["dice"]), str(part.get("type") or ""), target,
                    False, rng, half=bool(check["success"] and part.get("half")),
                )
                if event is not None:
                    events.append(event)
        return events

    @staticmethod
    def _spend_action_resource(combat: dict[str, Any]) -> str:
        """Which economy pool pays for a non-magical action.

        Action Surge grants a separate ``surge_action`` pool precisely so the
        Magic action can never spend it: spell costs only read ``action`` /
        ``bonus_action``.  A non-magical action therefore spends the surge pool
        first, keeping the ordinary action free for a later Magic action.
        """

        economy = combat.get("economy", {}) or {}
        if int(economy.get("surge_action", 0) or 0) > 0:
            return "surge_action"
        return "action"

    def _ability_check_events(
        self, combat: dict[str, Any], intent: dict[str, Any], actor: dict[str, Any],
        rng: Any,
    ) -> list[dict[str, Any]]:
        """A bare ability check, at whatever cost the caller declared.

        The arithmetic is the same one players already use (``ability_modifier``);
        what varies is only what the check costs.  Fighting this into ``hide`` or
        ``stabilize`` would have meant a third copy of the roll, which is exactly
        the duplication GAP-3 records.
        """

        ability = str(intent.get("ability") or "")
        dc = int(intent.get("dc") or 0)
        cost = str(intent.get("check_cost") or "action")
        actor_id = str(actor["actor_id"])
        events: list[dict[str, Any]] = []
        if cost != "none":
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": cost, "amount": 1,
            })
        modifier = ability_modifier(int((actor.get("abilities") or {}).get(ability, 10)))
        roll = int(rng.randint(1, 20))
        total = roll + modifier
        events.append({
            "type": "check.resolved", "kind": "ability_check", "actor_id": actor_id,
            "ability": ability, "rolls": [roll], "natural": roll,
            "modifier": modifier, "total": total, "target": dc,
            "success": total >= dc,
            "roll_id": self._roll_id(events, "ability_check", actor_id),
        })
        return events

    def _skill_check_bonus(
        self, actor: dict[str, Any], skill: str, base: int,
    ) -> tuple[int, str, list[str]]:
        """The skill's bonus after content-declared replacements and adds.

        No declaration means no work: the fast path returns the derived value
        unchanged, so every existing skill check keeps exactly what it had.
        """

        character = self._feature_character(actor)
        parts = (character.get("proficiencies") or {}).get("skill_parts") or {}
        part = parts.get(skill) if isinstance(parts, dict) else None
        default_ability = str((part or {}).get("ability") or "")
        rows = active_modifiers(
            self.features.check_modifiers(character, "skill_check"),
            skill=skill,
            ability=default_ability,
            conditions=actor.get("conditions") or {},
        )
        if not rows and isinstance(part, dict):
            return base, default_ability, []
        total, effective, _modifier, bonus_sources = check_total(
            part,
            actor.get("abilities") or {},
            default_ability=default_ability or "int",
            fallback_total=base,
            rows=rows,
            proficiency_bonus=int(actor.get("proficiency_bonus", 0) or 0),
        )
        return total, effective, bonus_sources

    def _skill_check_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any], rng: Any,
        *, skill: str, dc: int, kind: str, events: list[dict[str, Any]],
        sources: list[dict[str, Any]] | None = None, intent_id: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """One d20 skill check: the only place a skill is rolled in combat.

        ``kind`` doubles as the event kind and the window settlement kind, which
        is what the pre-roll bonus windows key off (``"hide"``,
        ``"peerless_skill"``...).  Returns the events so far -- possibly ending
        in a pending window -- plus the check row, or ``None`` when a window now
        owns the outcome.

        The DC belongs to the caller: Hide passes a placeholder derived from the
        enemies' Wisdom, which is a registered gap rather than an accident here.
        """

        actor_id = str(actor["actor_id"])
        base_bonus = int(actor["skill_values"].get(skill, 0) or 0)
        bonus, check_ability, bonus_sources = self._skill_check_bonus(
            actor, skill, base_bonus,
        )
        declared = list(sources or ())
        mode = mode_from_sources(declared)
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if mode == "disadvantage" else None
        rolls = [first] if second is None else [first, second]
        natural = min(rolls) if mode == "disadvantage" else first
        floor, floor_sources = self._skill_check_floor(actor, skill)
        effective = max(natural, floor) if floor else natural
        total = effective + bonus
        success = total >= dc
        event: dict[str, Any] = {
            "type": "check.resolved", "kind": kind, "actor_id": actor_id,
            # ``ability`` 历史上装的是技能名（大量消费者按现状读它），所以
            # 有效属性单独一个字段，不改变既有含义。
            "ability": skill, "rolls": rolls, "natural": effective,
            "modifier": bonus, "total": total, "target": dc, "success": success,
            "roll_id": self._roll_id(events, kind, actor_id),
        }
        if check_ability:
            event["skill"] = skill
            event["effective_ability"] = check_ability
        if bonus_sources:
            event["check_modifier_sources"] = bonus_sources
        if floor:
            event["raw_d20"] = natural
            event["effective_d20"] = effective
            event["d20_floor"] = floor
            event["d20_floor_sources"] = floor_sources
        if mode:
            event["advantage_mode"] = mode
            event["advantage_sources"] = declared
        window = self._ability_check_window_offer(
            instance, combat, actor, event,
            {"kind": kind, "intent_id": intent_id},
        )
        if window is not None:
            events.append({"type": "dnd2024.decision.pending", **window})
            return events, None
        return events, event

    def _repeat_save_rows(
        self, actor: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        """The conditions that repeat a save at the end of this actor's turn."""

        rows: list[tuple[str, dict[str, Any]]] = []
        for condition_id, meta in (actor.get("conditions") or {}).items():
            if not isinstance(meta, dict):
                continue
            repeat = meta.get("save_repeat")
            if not isinstance(repeat, dict):
                continue
            if not str(repeat.get("ability") or "") or int(repeat.get("dc", 0) or 0) <= 0:
                continue
            rows.append((str(condition_id), deepcopy(meta)))
        return rows

    def _repeat_save_check(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        actor_id: str, meta: dict[str, Any], events: list[dict[str, Any]], rng: Any,
    ) -> dict[str, Any]:
        """Roll one repeated save, without deciding what it does."""

        repeat = meta.get("save_repeat") or {}
        check = self._saving_throw_check(
            instance, combat, actor, actor_id, str(repeat.get("ability") or ""),
            int(repeat.get("dc", 0) or 0), str(meta.get("source_actor_id") or ""), events, rng,
        )
        check["repeat_save"] = True
        return check

    def _holy_ward_applies(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        source_actor_id: str,
    ) -> bool:
        """Whether the aura holder is being forced to save by a Fiend or Undead."""

        if not source_actor_id:
            return False
        character = self._feature_character(actor)
        if long_effect_active(
            character, "holy_nimbus", world_minutes(instance),
        ) is None:
            return False
        try:
            source = self._actor_view(instance, combat, source_actor_id)
        except Exception:  # noqa: BLE001 - 来源可能是已离场的叙事对象
            return False
        # 名单来自特性参数：改内容不需要改代码。
        declared = self.features.feature_value(
            character, "holy_nimbus", "holy_ward_creature_types", None,
        )
        accepted = {
            str(code).lower() for code in (declared or ("fiend", "undead"))
        }
        return str(source.get("creature_type") or "").lower() in accepted

    def _saving_throw_check(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        actor_id: str, ability: str, dc: int, source_actor_id: str,
        events: list[dict[str, Any]], rng: Any, *, against_spell: bool = False,
        forced_outcome: str = "",
    ) -> dict[str, Any]:
        """Shared save authority for spells, repeated saves and hit riders.

        Two stat-block traits attach here because this is the only place that
        knows a save is being rolled at all: Magic Resistance (Advantage when
        the effect is a spell) and Legendary Resistance (a failed save becomes
        a success, at the cost of one use).
        """

        modifier = int(actor["saving_throws"].get(ability, 0) or 0)
        auto_fail = ability in condition_flags(
            self.catalog.conditions, actor["conditions"],
        )["auto_fail_saves"]
        if auto_fail:
            return {
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": actor_id,
                "source_actor_id": source_actor_id,
                "ability": ability, "rolls": [], "natural": 0,
                "modifier": modifier, "check_bonus": 0, "check_bonus_rolls": [],
                "aura_bonus": 0, "aura_sources": [],
                "total": 0,
                "target": dc, "success": False, "auto_failed": True,
                "roll_id": self._roll_id(events, "saving_throw", actor_id),
            }
        if forced_outcome == "success":
            # Careful Spell：选中的生物直接成功，不掷骰。掷了会让"自动成功"
            # 看起来像一次运气好的豁免，日志里也就无法区分两者。
            return {
                "type": "check.resolved", "kind": "saving_throw",
                "actor_id": actor_id, "source_actor_id": source_actor_id,
                "ability": ability, "rolls": [], "natural": 0,
                "modifier": modifier, "check_bonus": 0, "check_bonus_rolls": [],
                "aura_bonus": 0, "aura_sources": [],
                "total": dc, "target": dc, "success": True,
                "auto_succeeded": True, "metamagic": "careful_spell",
                "roll_id": self._roll_id(events, "saving_throw", actor_id),
            }
        save_sources = self._saving_throw_sources(
            actor, ability, against_spell=against_spell,
        )
        if forced_outcome == "disadvantage":
            # Heightened Spell：只改这一条豁免，不写到目标身上。
            save_sources = [
                *save_sources, {"code": "heightened_spell", "mode": "disadvantage"},
            ]
        if self._holy_ward_applies(instance, combat, actor, source_actor_id):
            # Holy Ward：光环持有者被邪魔或亡灵迫使的豁免有优势。来源生物
            # 类型来自权威视图，不由客户端声明。
            save_sources.append({"code": "holy_nimbus", "mode": "advantage"})
        traits = actor.get("traits")
        if against_spell and "magic_resistance" in trait_ids(traits):
            save_sources.append({"code": "magic_resistance", "mode": "advantage"})
        save_mode = mode_from_sources(save_sources)
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if save_mode else None
        if save_mode == "advantage":
            roll = max(first, second if second is not None else first)
        elif save_mode == "disadvantage":
            roll = min(first, second if second is not None else first)
        else:
            roll = first
        check_bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, actor["conditions"], "saving_throw", rng,
        )
        check_bonus = sum(row["roll"] for row in check_bonus_rolls)
        # 光环按本次回合末结算时的位置与失能状态求值，不用施放时快照；
        # 快照里只有 DC 和豁免属性，这两项不随移动变化。
        aura = self._saving_throw_aura_bonus(instance, combat, actor)
        cover = self._cover_save_bonus(instance, combat, actor, ability)
        total = (
            roll + modifier + check_bonus
            + int(aura["bonus"]) + int(cover["bonus"])
        )
        total, replacement = self._strength_total_replacement(actor, ability, total)
        legendary = self._legendary_resistance_events(
            actor, actor_id, total >= dc, events,
        )
        check_event = {
            "type": "check.resolved", "kind": "saving_throw",
            "actor_id": actor_id,
            "source_actor_id": source_actor_id,
            "ability": ability,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": roll, "modifier": modifier,
            "check_bonus": check_bonus, "check_bonus_rolls": check_bonus_rolls,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total, "target": dc,
            "success": total >= dc or legendary["used"],
            "roll_id": self._roll_id(events, "saving_throw", actor_id),
            **replacement,
        }
        if legendary["used"]:
            # 掷骰结果原样留着：日志要能看出"这一掷本来是失败的"。
            check_event["legendary_resistance"] = legendary["remaining"]
        if int(cover["bonus"]):
            check_event["cover_bonus"] = int(cover["bonus"])
            check_event["cover_sources"] = cover["sources"]
        if save_sources:
            check_event["advantage_mode"] = save_mode
            check_event["advantage_sources"] = save_sources
        return check_event

    def _save_repeat_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, rng: Any,
        *, pending: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Repeat a condition's save at the end of the target's own turn.

        Only conditions whose metadata carries a ``save_repeat`` snapshot
        participate: the ability and DC were frozen when the spell landed. A
        success removes just this target's condition; it never ends the caster's
        concentration, so other targets of an upcast spell keep theirs.

        A window pauses this loop rather than the whole turn: the rows it has
        not reached yet travel in the settlement, and the window finisher
        resumes from exactly there.
        """

        actor = self._actor_view(instance, combat, actor_id)
        rows = list(pending) if pending is not None else self._repeat_save_rows(actor)
        events: list[dict[str, Any]] = []
        while rows:
            condition_id, meta = rows.pop(0)
            check = self._repeat_save_check(
                instance, combat, actor, actor_id, meta, events, rng,
            )
            window = self._save_window_offer(
                instance, combat, actor, check,
                {
                    "surface": "repeat_save", "condition": condition_id,
                    "remaining": [list(row) for row in rows],
                },
                "save", source=str(meta.get("source_actor_id") or actor_id),
                target_id=actor_id,
            )
            if window is not None:
                events.append({"type": "dnd2024.decision.pending", **window})
                return events
            events.append(check)
            events.extend(self._consume_save_markers(actor_id, actor))
            if check["success"]:
                events.append({
                    "type": "condition.removed", "target_id": actor_id,
                    "condition": condition_id, "reason": "save_repeat",
                })
        return events

    def _death_save_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(intent["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        character = self._feature_character(actor)
        advantage_sources = list(
            self.features.feature_flag_source_rows(character, "death_save_advantage")
        )
        crit_threshold = int(self.features.min_feature_value(
            character, "death_save_crit_threshold", 20,
        ))
        # Lucky's advantage face is declared before the roll and consumed
        # unconditionally; validation proves the pool exists and has a point.
        spend_luck = bool(intent.get("spend_luck"))
        if spend_luck:
            # Lucky is a feat, not a class feature: it has no class owner to
            # report, and the pre-existing attack-roll provenance says the same.
            advantage_sources.append({"code": "lucky", "mode": "advantage"})
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if advantage_sources else None
        roll = max(first, second) if second is not None else first
        # A death saving throw is still a saving throw, so conditional d4s
        # (Bless) apply after the d20 faces and before the outcome is read.
        check_bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, actor["conditions"], "saving_throw", rng,
        )
        check_bonus = sum(row["roll"] for row in check_bonus_rolls)
        # Aura of Protection covers death saves too.  The bonus is itemised
        # rather than folded into ``modifier`` so the event still shows a bare
        # d20 plus separately-attributable sources.
        aura = self._saving_throw_aura_bonus(instance, combat, actor)
        modifier = (
            int(actor["proficiency_bonus"])
            if self.features.saving_throw_proficiency_all(character) else 0
        )
        total = roll + modifier + check_bonus + int(aura["bonus"])
        check_event = {
            "type": "check.resolved", "kind": "death_save", "actor_id": actor_id,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": roll, "modifier": modifier,
            "check_bonus": check_bonus,
            "check_bonus_rolls": check_bonus_rolls, "bless_bonus": check_bonus,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total,
            "target": 10,
            "success": roll != 1 and (roll >= crit_threshold or total >= 10),
            "roll_id": f"death_save:{actor_id}:1",
        }
        if advantage_sources:
            check_event["advantage_mode"] = "advantage"
            check_event["advantage_sources"] = [
                {**source, "mode": "advantage"} for source in advantage_sources
            ]
        if crit_threshold != 20:
            # Named for this save on purpose: ``crit_threshold`` already means
            # the attack crit range elsewhere in the same event shape.
            check_event["death_save_crit_threshold"] = crit_threshold
        events: list[dict[str, Any]] = []
        if spend_luck:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
                "resource_id": "lucky_points",
            })
        window = self._death_save_check_window(instance, combat, actor, check_event)
        if window is not None:
            # The save has been rolled but its failures are not yet applied:
            # nothing is irreversible until the window owner decides.
            events.append({"type": "dnd2024.decision.pending", **window})
            return events
        events.append(check_event)
        # 死亡豁免同样是豁免：劣势必在这里被用掉，不能留到"复活后的第一次豁免"。
        events.extend(self._consume_save_markers(actor_id, actor))
        events.extend(
            self._death_save_outcome_events(instance, combat, actor, check_event, rng)
        )
        return events

    def _death_save_outcome_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        check: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Apply a resolved death save to the stable/dead tally.

        Shared by the straight-through path and the window finisher; the only
        values a window can change are the save total and its success flag, so
        the natural-roll special cases stay authoritative here.
        """

        actor_id = str(actor["actor_id"])
        roll = int(check.get("natural", 0) or 0)
        crit_threshold = int(check.get("death_save_crit_threshold", 20) or 20)
        successes = int(actor["death_saves"].get("successes", 0) or 0)
        failures = int(actor["death_saves"].get("failures", 0) or 0)
        hp = 0
        if roll >= crit_threshold:
            hp = 1
            successes = failures = 0
        elif roll == 1:
            failures += 2
        elif bool(check.get("success")):
            successes += 1
        else:
            failures += 1
        stable = successes >= 3
        dead = failures >= 3
        settled_condition = "dead" if dead else "stable" if stable else ""
        return [
            {
                "type": "dnd2024.death_save.resolved", "actor_id": actor_id,
                # §4.5.4 asks for the natural face, but
                # test_dark_ones_own_luck_adds_a_d10_to_a_failed_death_save
                # pins the window-boosted total here and sits outside this
                # slice's file whitelist.  Recorded as a deviation instead of
                # changing another slice's test.
                "roll": int(check.get("total", roll) or roll),
                "successes": min(successes, 3), "failures": min(failures, 3),
                "stable": stable, "dead": dead, "hp": hp,
            },
            *([] if hp else self._end_turn_events(
                instance, combat, rng, settled_condition=settled_condition,
            )),
        ]

    def _stabilize_events(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor = self._actor_view(instance, combat, str(intent["actor_id"]))
        target_id = str(intent["target_id"])
        roll = int(rng.randint(1, 20))
        medicine = int(actor["skill_values"].get("medicine", 0) or 0)
        floor, floor_sources = self._skill_check_floor(actor, "medicine")
        effective = max(roll, floor) if floor else roll
        success = effective + medicine >= 10
        check: dict[str, Any] = {
            "type": "check.resolved", "kind": "medicine", "actor_id": actor["actor_id"],
            "target_id": target_id, "rolls": [roll], "natural": effective,
            "modifier": medicine, "total": effective + medicine, "target": 10,
            "success": success,
        }
        if floor:
            check["raw_d20"] = roll
            check["effective_d20"] = effective
            check["d20_floor"] = floor
            check["d20_floor_sources"] = floor_sources
        events = [{
            "type": "dnd2024.action.spent", "actor_id": actor["actor_id"],
            "resource": self._spend_action_resource(combat), "amount": 1,
        }, check]
        if success:
            events.append({
                "type": "condition.applied", "target_id": target_id,
                "condition": "stable", "duration": "until_healed",
                "source_actor_id": actor["actor_id"],
            })
        return events

    def _finish_relentless_rage(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Resolve the zero-HP survival window.

        Declining or failing submits the held damage, so the ordinary 0 HP
        consequences run exactly once.  A success replaces those consequences
        with ``hp = 2 x Barbarian level``; the Rage never ended, so nothing has
        to be restored afterwards and no healing trigger fires.  Every actual
        use -- success or failure -- raises the next DC; a decline does not.
        """

        actor_id = str(decision["actor_id"])
        damage_event = deepcopy(decision["damage_event"])
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        if intent["option"] != "use":
            events.append(damage_event)
            return events
        actor = self._actor_view(instance, combat, actor_id)
        ability = str(decision.get("save_ability") or "con")
        dc = int(decision["dc"])
        modifier = int(actor["saving_throws"].get(ability, 0) or 0)
        sources = self._saving_throw_sources(actor, ability)
        mode = mode_from_sources(sources)
        first = int(rng.randint(1, 20))
        second = int(rng.randint(1, 20)) if mode else None
        if mode == "advantage":
            natural = max(first, second if second is not None else first)
        elif mode == "disadvantage":
            natural = min(first, second if second is not None else first)
        else:
            natural = first
        bonus_rolls = condition_check_bonus_rolls(
            self.catalog.conditions, actor["conditions"], "saving_throw", rng,
        )
        bonus = sum(row["roll"] for row in bonus_rolls)
        aura = self._saving_throw_aura_bonus(instance, combat, actor)
        total = natural + modifier + bonus + int(aura["bonus"])
        success = total >= dc
        check: dict[str, Any] = {
            "type": "check.resolved", "kind": "saving_throw", "actor_id": actor_id,
            "ability": ability,
            "rolls": [value for value in (first, second) if value is not None],
            "natural": natural, "modifier": modifier,
            "check_bonus": bonus, "check_bonus_rolls": bonus_rolls,
            "aura_bonus": int(aura["bonus"]), "aura_sources": aura["sources"],
            "total": total, "target": dc, "success": success,
            "feature_id": "relentless_rage", "trigger": "zero_hp",
        }
        if sources:
            check["advantage_mode"] = mode
            check["advantage_sources"] = sources
        events.append(check)
        events.extend(self._consume_save_markers(actor_id, actor))
        # DC 递增写在 save 之后、结果之前：失败也是一次实际使用。
        events.append({
            "type": "dnd2024.feature_state.set", "actor_id": actor_id,
            "feature_id": "relentless_rage", "owner": "class:barbarian",
            "values": {"next_dc": dc + int(decision.get("dc_step") or 5)},
        })
        if not success:
            events.append(damage_event)
            return events
        maximum = int(actor.get("max_hp", 0) or 0)
        target_hp = int(decision.get("hp_amount", 0) or 0)
        if maximum > 0:
            target_hp = min(maximum, target_hp)
        events.append({
            "type": "resource.changed", "resource": "hp", "target_id": actor_id,
            "delta": target_hp - int(actor.get("hp", 0) or 0), "amount": 0,
            # ``healing`` 只用于拒绝临时 HP 吸收：这是"HP 改为某值"，不是伤害。
            "healing": True,
            "relentless_rage": True, "save_total": total, "dc": dc,
            "hp_after": target_hp,
        })
        return events
