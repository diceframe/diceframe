"""S0-F13: condition immunity, suppression and removal.

Three features share one question -- "does this condition actually affect this
creature right now?" -- and they answer it in three different places:

* Mindless Rage *blocks* a new condition and *ends* one that was already there
  when the Rage starts;
* Self-Restoration *removes* one condition at the owner's own turn end;
* Aura of Courage *suppresses* an existing condition: the entry keeps its
  source and duration, the mechanics stop applying.

Everything goes through ``validate_intent`` -> ``resolve_intent`` ->
``apply_batch`` except three cases, each named where it appears: a same-side
spell target (validation refuses it, so the resolver entry point is driven the
way S0-E2 drives it), a condition with no spell in the catalog that inflicts it
(Frightened), and the declaration/label checks which are catalog facts.
"""

from __future__ import annotations

import json
from copy import deepcopy

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.check_modes import attack_mode
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.resting import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_draft,
    monk_instance,
    start_combat,
)

RUNTIME = Dnd2024Runtime()


# ---------------------------------------------------------------------------
# characters
# ---------------------------------------------------------------------------


def _draft(class_ref: str, name: str = "Hero") -> dict:
    draft = dict(monk_draft(RUNTIME))
    draft["class_ref"] = class_ref
    draft["name"] = name
    choices = RUNTIME.builder_choices(None, draft)
    draft["class_skill_refs"] = [
        item["ref"] for item in choices["class_skills"]
    ][: choices["class_skill_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    draft["equipment_package_ref"] = choices["equipment_packages"][0]["ref"]
    draft["background_equipment_package_ref"] = (
        choices["background_equipment_packages"][0]["ref"]
    )
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    recommended = choices.get("recommended_class_spells") or {}
    if recommended.get("cantrip_ids") or recommended.get("prepared_spell_ids"):
        draft["class_spell_choices"] = {
            "cantrip_refs": [f"spell:{item}" for item in recommended["cantrip_ids"]],
            "prepared_spell_refs": [
                f"spell:{item}" for item in recommended.get("prepared_spell_ids") or []
            ],
        }
    errors = RUNTIME.validate_character(None, draft)
    assert errors == [], errors
    return draft


def _answer_ability_increase(sheet: dict, requirement: dict, choices: dict) -> None:
    total = int(requirement.get("total", 2))
    allowed = requirement.get("allowed", "any")
    candidates = (
        [str(item) for item in allowed] if isinstance(allowed, list)
        else ["str", "dex", "con", "wis", "int", "cha"]
    )
    scores = sheet["ruleset_character"].get("abilities") or {}
    chosen = next(
        (ability for ability in candidates if int(scores.get(ability, 10)) + total <= 20),
        None,
    )
    if chosen:
        choices["ability_score_increases"] = {chosen: total}


def _answer_spell_slot(sheet: dict, requirement: dict, choices: dict) -> None:
    build = sheet["ruleset_character"]["build"]
    previous = build.get("class_spell_choices") or {}

    def merged(existing: list, options: list, count: int) -> list:
        refs = list(existing)
        for row in options:
            if row["ref"] not in refs:
                refs.append(row["ref"])
        return refs[:count]

    choices["class_spell_choices"] = {
        "cantrip_refs": merged(
            previous.get("cantrip_refs") or [], requirement.get("cantrips") or [],
            int(requirement.get("cantrip_count", 0) or 0),
        ),
        "prepared_spell_refs": merged(
            previous.get("prepared_spell_refs") or [],
            requirement.get("leveled_spells") or [],
            int(requirement.get("prepared_spell_count", 0) or 0),
        ),
    }


def _advance(sheet: dict, level: int, *, subclass_ref: str | None = None) -> dict:
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        preview = RUNTIME.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        for requirement in preview.get("requirements") or []:
            requirement_id = str(requirement.get("id") or "")
            if requirement_id == "subclass_ref" and subclass_ref:
                values = [option["value"] for option in requirement["options"]]
                assert subclass_ref in values, (subclass_ref, values)
                choices["subclass_ref"] = subclass_ref
            if requirement_id == "ability_score_increases":
                _answer_ability_increase(sheet, requirement, choices)
            if requirement.get("kind") == "spell_selection":
                _answer_spell_slot(sheet, requirement, choices)
        preview = RUNTIME.preview_advancement(None, sheet, choices)
        assert preview.get("ok") is True, preview.get("errors")
        sheet = RUNTIME.apply_advancement(None, sheet, choices)
    return sheet


def _preset_sheet(preset_id: str, level: int = 1, *, subclass_ref: str | None = None) -> dict:
    choices = RUNTIME.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    sheet = RUNTIME.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": preset_id},
    )
    return _advance(sheet, level, subclass_ref=subclass_ref) if level > 1 else sheet


def _berserker(level: int = 6) -> dict:
    return _preset_sheet(
        "relentless_vanguard", level, subclass_ref="subclass:path_of_the_berserker",
    )


def _barbarian_without_berserker(level: int = 5) -> dict:
    return _preset_sheet("relentless_vanguard", level)


def _monk(level: int = 10) -> dict:
    sheet = RUNTIME.finalize_character(None, monk_draft(RUNTIME))
    return _advance(sheet, level) if level > 1 else sheet


def _paladin(level: int = 10) -> dict:
    sheet = RUNTIME.finalize_character(None, _draft("class:paladin", "Paladin"))
    return _advance(sheet, level) if level > 1 else sheet


def _cleric(level: int = 1) -> dict:
    return _preset_sheet("kindly_bulwark", level)


# ---------------------------------------------------------------------------
# combat plumbing
# ---------------------------------------------------------------------------


def _combat(sheet: dict, *, position: int = 5):
    engine, instance = monk_instance(RUNTIME, sheet)
    start_combat(engine, instance, position=position)
    return engine, instance


def _party_combat(sheets: dict[str, dict], *, positions: dict[str, int] | None = None):
    """Start a fight with several real party members at fixed positions."""

    first = "gm"
    engine, instance = monk_instance(RUNTIME, sheets[first], uid=first)
    for uid, sheet in sheets.items():
        if uid != first:
            instance.players[uid] = {
                "character_name": uid.title(), "character_sheet": sheet,
            }
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": first, "enemies": [{
            "id": "goblin-1", "name": "Goblin", "hp": 60, "armor_class": 12,
            "speed": 30, "position": 30, "initiative_modifier": 2,
            "creature_type": "humanoid",
            "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
            "saving_throws": {"dex": 2, "wis": -1, "con": 0},
            "attacks": [{
                "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            }],
        }],
    }, SequenceRng([20, *([1] * len(sheets)), 1]))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    if positions:
        _place(instance, **positions)
    return engine, instance


def _add_caster(instance, sheet: dict | None = None) -> None:
    instance.players["caster"] = {
        "character_name": "Caster", "character_sheet": sheet or _cleric(1),
    }


def _place(instance, **positions: int) -> None:
    combat = _combat_state(instance)
    for actor, position in positions.items():
        combat["positions"][actor] = position


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _canonical_conditions(instance, uid: str = "gm") -> dict:
    return _canonical(instance, uid).setdefault("conditions", {})


def _combat_state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _view(engine, instance, actor_id: str) -> dict:
    return engine._actor_view(instance, _combat_state(instance), actor_id)


def _conditions(engine, instance, actor_id: str) -> dict:
    return _view(engine, instance, actor_id)["conditions"]


def _seed_condition(
    instance, uid: str, code: str, *, source: str = "enemy:goblin-1",
    **extra,
) -> None:
    """Put a condition on a character the catalog has no spell for.

    Frightened is the case: SRD content in this bundle declares no spell that
    inflicts it, and the fixtures that already needed one (the failed-d20 suite)
    seed it the same way.  Everything the condition *does* is then exercised
    through the real intent chain.
    """

    _canonical(instance, uid).setdefault("conditions", {})[code] = {
        "source_actor_id": source, "duration": "1_hour", "turns_remaining": 10,
        **extra,
    }


def _apply(engine, instance, intent: dict, rng=None) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng or SequenceRng([]))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _end_turn(engine, instance, *, actor_id: str = "player:gm", choice=None,
              rolls=(), by: str | None = None) -> list[dict]:
    intent = {
        "intent_id": f"end:{actor_id}:{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": by or (
            actor_id.removeprefix("player:")
            if actor_id.startswith("player:") else "gm"
        ),
        "actor_id": actor_id,
    }
    if choice is not None:
        intent["self_restoration_condition"] = choice
    return _apply(engine, instance, intent, SequenceRng(list(rolls)))


def _move(engine, instance, distance: int, *, actor_id: str = "player:gm") -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": f"move:{actor_id}:{ruleset_runtime.state(instance)['version']}",
        "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id, "distance": distance,
    })


def _direct_cast(engine, instance, target_id: str, spell_ref: str, rolls=(),
                 *, actor_id: str = "player:caster", slot_level: int = 1) -> list[dict]:
    return engine._spell_events(
        instance,
        _combat_state(instance),
        {
            "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": [target_id],
        },
        SequenceRng(list(rolls)),
    )


def _apply_events(engine, instance, events: list[dict]) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    batch = {
        "batch_id": f"batch_{version}",
        "intent_id": f"resolve:{version}", "intent_type": "cast_spell",
        "expected_version": version, "result_version": version + 1,
        "events": [
            {"type": "intent.submitted", "intent_type": "cast_spell",
             "actor_id": "player:caster", "submitted_by": "gm"},
            *events,
        ],
        "source_ref": "test",
    }
    applied = engine.apply_batch(instance, batch)
    assert applied["applied"] is True, applied


def _first_weapon(engine, instance, uid: str = "gm") -> str:
    for item in engine.available_intents(instance, uid):
        if item["type"] == "attack" and item.get("weapons"):
            return str(item["weapons"][0]["weapon_ref"])
    raise AssertionError("no weapon available")


def _saving_throw_check(events: list[dict]) -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "saving_throw"
    )


def _removals(events: list[dict]) -> list[dict]:
    return [event for event in events if event["type"] == "condition.removed"]


class _CountingRng(SequenceRng):
    def __init__(self, values: list[int]) -> None:
        super().__init__(values)
        self.d20 = 0

    def randint(self, minimum: int, maximum: int) -> int:
        if (minimum, maximum) == (1, 20):
            self.d20 += 1
        return super().randint(minimum, maximum)


# ---------------------------------------------------------------------------
# 1-2: declarations
# ---------------------------------------------------------------------------


def test_the_three_declarations_arrive_with_their_owners() -> None:
    """1. Owner, level, source_ref and parameters are catalog facts."""

    resolver = Dnd2024ClassFeatureResolver(RUNTIME.load_bundle("en"))
    expected = {
        "mindless_rage": ("subclass:path_of_the_berserker", 6, "srd-5.2.1:p30:mindless-rage"),
        "self_restoration": ("class:monk", 10, "srd-5.2.1:p52:self-restoration"),
        "aura_of_courage": ("class:paladin", 10, "srd-5.2.1:p55:aura-of-courage"),
    }
    characters = {
        "mindless_rage": _berserker(6),
        "self_restoration": _monk(10),
        "aura_of_courage": _paladin(10),
    }
    for feature_id, (owner, minimum, source_ref) in expected.items():
        definitions = [
            definition
            for definition in resolver.features_for(characters[feature_id]["ruleset_character"])
            if definition.id == feature_id
        ]
        assert len(definitions) == 1, (feature_id, definitions)
        definition = definitions[0]
        assert definition.owner == owner
        assert definition.minimum_level == minimum
        assert definition.source_ref == source_ref

    assert resolver.feature_value(
        _berserker(6)["ruleset_character"], "mindless_rage", "immune_conditions", [],
    ) == ["charmed", "frightened"]
    assert resolver.feature_value(
        _monk(10)["ruleset_character"], "self_restoration", "removable_conditions", [],
    ) == ["charmed", "frightened", "poisoned"]
    assert resolver.feature_value(
        _paladin(10)["ruleset_character"], "aura_of_courage",
        "aura_condition_immunities", [],
    ) == ["frightened"]


def test_the_labels_exist_in_both_locales() -> None:
    """1. A declaration without a label is a blank row in the sheet."""

    for locale, expected in (
        ("en", {"mindless_rage": "Mindless Rage", "self_restoration": "Self-Restoration",
                "aura_of_courage": "Aura of Courage"}),
        ("zh-CN", {"mindless_rage": "无智狂怒", "self_restoration": "自我恢复",
                   "aura_of_courage": "勇气灵光"}),
    ):
        path = (
            "templates/rulesets/dnd2024_srd/locales/"
            f"{locale}/class_feature_catalogs/srd_class_features.json"
        )
        with open(path, encoding="utf-8") as handle:
            labels = json.load(handle)["fields"]["labels"]
        for feature_id, label in expected.items():
            assert labels[feature_id] == label, (locale, feature_id)


def test_a_level_below_grants_nothing() -> None:
    """1. The level gate is the class level, not the character's presence."""

    resolver = Dnd2024ClassFeatureResolver(RUNTIME.load_bundle("en"))
    assert "mindless_rage" not in [
        definition.id
        for definition in resolver.features_for(
            _barbarian_without_berserker(5)["ruleset_character"],
        )
    ]
    assert "self_restoration" not in [
        definition.id for definition in resolver.features_for(_monk(9)["ruleset_character"])
    ]
    assert "aura_of_courage" not in [
        definition.id for definition in resolver.features_for(_paladin(9)["ruleset_character"])
    ]


# ---------------------------------------------------------------------------
# 3-7: Mindless Rage
# ---------------------------------------------------------------------------


def test_a_raging_berserker_blocks_a_new_charmed() -> None:
    """2. Immunity means the entry is never created, not created-and-ignored."""

    engine, instance = _combat(_berserker(6))
    _add_caster(instance)
    _apply(engine, instance, {
        "intent_id": "rage-1", "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "capability_id": "rage",
    })
    assert "rage" in _conditions(engine, instance, "player:gm")

    events = _direct_cast(engine, instance, "player:gm", "spell:charm_person", rolls=[1])
    check = _saving_throw_check(events)

    assert check["success"] is False
    assert check["blocked_condition"] == "charmed"
    assert check["blocked_by_feature"] == {
        "feature_id": "mindless_rage",
        "owner": "subclass:path_of_the_berserker",
        "source_actor_id": "player:gm",
    }
    assert "blocked_by_aura" not in check
    assert not [event for event in events if event["type"] == "condition.applied"]
    _apply_events(engine, instance, events)
    assert "charmed" not in _conditions(engine, instance, "player:gm")


def test_mindless_rage_does_not_immunize_anything_else() -> None:
    """2. The declaration names two conditions; nothing is generalized."""

    engine, instance = _combat(_berserker(6))
    _apply(engine, instance, {
        "intent_id": "rage-1", "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "capability_id": "rage",
    })
    view = _view(engine, instance, "player:gm")
    combat = _combat_state(instance)

    assert engine._condition_immunity_blocker(instance, combat, view, "frightened") is not None
    for code in ("poisoned", "stunned", "paralyzed", "blinded"):
        assert engine._condition_immunity_blocker(instance, combat, view, code) is None


def test_entering_the_rage_ends_what_was_already_there() -> None:
    """3. Two conditions, two removal events, one batch."""

    sheet = _berserker(6)
    engine, instance = _combat(sheet)
    _seed_condition(instance, "gm", "charmed")
    _seed_condition(instance, "gm", "frightened")

    events = _apply(engine, instance, {
        "intent_id": "rage-1", "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "capability_id": "rage",
    })

    removals = _removals(events)
    assert {event["condition"] for event in removals} == {"charmed", "frightened"}
    assert all(event["reason"] == "feature:mindless_rage" for event in removals)
    assert all(event["feature_id"] == "mindless_rage" for event in removals)
    canonical = _canonical(instance)["conditions"]
    assert "charmed" not in canonical and "frightened" not in canonical
    assert "rage" in canonical


def test_sustaining_the_rage_does_not_clear_again() -> None:
    """4. Sustain is not a second entry; the cleared conditions never return."""

    engine, instance = _combat(_berserker(6))
    _seed_condition(instance, "gm", "charmed")
    _apply(engine, instance, {
        "intent_id": "rage-1", "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "capability_id": "rage",
    })
    # Sustain is a Bonus Action like the entry itself, so it needs a new turn.
    _end_turn(engine, instance)
    _end_turn(engine, instance, actor_id="enemy:goblin-1")
    _seed_condition(instance, "gm", "frightened")

    events = _apply(engine, instance, {
        "intent_id": "sustain-1", "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "capability_id": "rage_sustain",
    })
    assert _removals(events) == []
    assert "frightened" in _canonical_conditions(instance)


def test_after_the_rage_ends_the_condition_lands_again() -> None:
    """4. Immunity is tied to the Rage, and removal is not reversible."""

    engine, instance = _combat(_berserker(6))
    _seed_condition(instance, "gm", "charmed")
    _apply(engine, instance, {
        "intent_id": "rage-1", "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "capability_id": "rage",
    })
    _add_caster(instance)
    _canonical(instance)["conditions"].pop("rage", None)

    events = _direct_cast(engine, instance, "player:gm", "spell:charm_person", rolls=[1])
    _apply_events(engine, instance, events)

    assert "charmed" in _conditions(engine, instance, "player:gm")


def test_a_barbarian_without_the_subclass_keeps_the_condition() -> None:
    """5. Immunity does not leak to a barbarian who never chose the path."""

    sheet = _barbarian_without_berserker(5)
    engine, instance = _combat(sheet)
    _add_caster(instance)

    events = _direct_cast(engine, instance, "player:gm", "spell:charm_person", rolls=[1])
    check = _saving_throw_check(events)

    assert "blocked_condition" not in check
    _apply_events(engine, instance, events)
    assert "charmed" in _conditions(engine, instance, "player:gm")


# ---------------------------------------------------------------------------
# 8-12: Self-Restoration
# ---------------------------------------------------------------------------


def test_the_turn_end_removes_the_chosen_condition_only() -> None:
    """6-7. One condition, chosen by the player, one event with a source."""

    engine, instance = _combat(_monk(10))
    for code in ("charmed", "frightened", "poisoned"):
        _seed_condition(instance, "gm", code)

    events = _end_turn(engine, instance, choice="frightened")

    removals = _removals(events)
    assert len(removals) == 1
    assert removals[0]["condition"] == "frightened"
    assert removals[0]["reason"] == "feature:self_restoration"
    canonical = _canonical(instance)["conditions"]
    assert set(canonical) >= {"charmed", "poisoned"}
    assert "frightened" not in canonical


def test_a_second_turn_cannot_remove_the_same_condition_twice() -> None:
    """7. A repeated choice is refused once the condition is gone."""

    engine, instance = _combat(_monk(10))
    _seed_condition(instance, "gm", "charmed")
    _end_turn(engine, instance, choice="charmed")
    _end_turn(engine, instance, actor_id="enemy:goblin-1")

    resolved = engine.resolve_intent(instance, {
        "intent_id": "end-again", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "self_restoration_condition": "charmed",
    }, SequenceRng([]))

    assert resolved["ok"] is False
    assert "does not have" in str(resolved.get("error"))


def test_the_panel_is_told_which_conditions_may_be_removed() -> None:
    """6. The client renders a choice list, it does not invent one."""

    engine, instance = _combat(_monk(10))
    _seed_condition(instance, "gm", "charmed")
    _seed_condition(instance, "gm", "stunned")

    end_turn = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "end_turn"
    )
    assert end_turn["self_restoration_options"] == ["charmed"]

    other_engine, other_instance = _combat(_berserker(6))
    other_end_turn = next(
        item for item in other_engine.available_intents(other_instance, "gm")
        if item["type"] == "end_turn"
    )
    assert "self_restoration_options" not in other_end_turn


def test_no_choice_means_no_removal() -> None:
    """8. The engine never picks a condition for the player."""

    engine, instance = _combat(_monk(10))
    _seed_condition(instance, "gm", "charmed")

    events = _end_turn(engine, instance)

    assert _removals(events) == []
    assert "charmed" in _canonical(instance)["conditions"]


def test_an_illegal_choice_is_refused() -> None:
    """8. Wrong list, wrong holder, wrong type: all refused before any write."""

    engine, instance = _combat(_monk(10))
    _seed_condition(instance, "gm", "charmed")
    _seed_condition(instance, "gm", "stunned")

    for value in ("stunned", "exhaustion", "blinded", 7):
        version = ruleset_runtime.state(instance)["version"]
        resolved = engine.resolve_intent(instance, {
            "intent_id": f"end-{value}", "type": "end_turn",
            "expected_version": version, "submitted_by": "gm",
            "actor_id": "player:gm", "self_restoration_condition": value,
        }, SequenceRng([]))
        assert resolved["ok"] is False, value
        assert ruleset_runtime.state(instance)["version"] == version


def test_a_character_without_the_feature_cannot_use_it() -> None:
    """8. The option is not a generic "delete a condition" channel."""

    engine, instance = _combat(_berserker(6))
    _seed_condition(instance, "gm", "charmed")

    resolved = engine.resolve_intent(instance, {
        "intent_id": "end-1", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "self_restoration_condition": "charmed",
    }, SequenceRng([]))

    assert resolved["ok"] is False
    assert "no feature" in str(resolved.get("error"))


def test_exhaustion_and_stunned_are_never_on_the_list() -> None:
    """9. The whitelist is the whole rule; nothing else is removable."""

    engine, instance = _combat(_monk(10))
    _seed_condition(instance, "gm", "stunned")
    _canonical_conditions(instance)["exhaustion"] = 2

    for value in ("exhaustion", "stunned"):
        resolved = engine.resolve_intent(instance, {
            "intent_id": f"end-{value}", "type": "end_turn",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm", "actor_id": "player:gm",
            "self_restoration_condition": value,
        }, SequenceRng([]))
        assert resolved["ok"] is False
    assert _canonical(instance)["conditions"]["exhaustion"] == 2


def test_forgoing_food_is_declared_but_has_no_producer_yet() -> None:
    """10. Boundary: nothing in this repository raises Exhaustion.

    The declaration carries the source it would ignore, and the only existing
    Exhaustion writer -- a Long Rest -- still lowers it by exactly one level.
    Inventing a "food" path here would mean guessing provenance, which the task
    explicitly forbids, so the gap is pinned instead of faked.
    """

    resolver = Dnd2024ClassFeatureResolver(RUNTIME.load_bundle("en"))
    assert resolver.feature_value(
        _monk(10)["ruleset_character"], "self_restoration",
        "exhaustion_sources_ignored", [],
    ) == ["food_drink"]

    engine, instance = _combat(_monk(10))
    _canonical_conditions(instance)["exhaustion"] = 2
    _canonical_conditions(instance).pop("stable", None)
    _canonical_conditions(instance).pop("unconscious", None)

    rested = Dnd2024RestEngine(RUNTIME.load_bundle("en")).complete_long_rest(
        instance.get_character_sheet("gm"),
    )
    assert rested["character"]["conditions"]["exhaustion"] == 1


# ---------------------------------------------------------------------------
# 13-19: Aura of Courage
# ---------------------------------------------------------------------------


def _aura_party() -> tuple[object, object]:
    """Paladin at 0, monk at 10 (inside), wizard-free second seat outside."""

    engine, instance = _party_combat(
        {"gm": _paladin(10), "monk": _monk(10)},
        positions={"player:gm": 0, "player:monk": 10, "enemy:goblin-1": 20},
    )
    return engine, instance


def test_the_paladin_and_an_ally_inside_are_immune_to_frightened() -> None:
    """11. Self and allies in the aura, nobody else."""

    engine, instance = _aura_party()
    combat = _combat_state(instance)

    paladin = _view(engine, instance, "player:gm")
    ally = _view(engine, instance, "player:monk")
    enemy = _view(engine, instance, "enemy:goblin-1")

    inside = engine._condition_immunity_blocker(instance, combat, paladin, "frightened")
    assert inside is not None
    assert inside["feature_id"] == "aura_of_courage"
    assert inside["scope"] == "aura"
    assert engine._condition_immunity_blocker(instance, combat, ally, "frightened") is not None
    assert engine._condition_immunity_blocker(instance, combat, enemy, "frightened") is None

    _place(instance, **{"player:monk": 11})
    outside = _view(engine, instance, "player:monk")
    assert engine._condition_immunity_blocker(instance, combat, outside, "frightened") is None


def test_an_existing_frightened_is_kept_but_loses_its_teeth() -> None:
    """13. Suppression is a view, never a deletion."""

    engine, instance = _aura_party()
    _seed_condition(instance, "monk", "frightened")
    combat = _combat_state(instance)

    ally = _view(engine, instance, "player:monk")
    effective = engine._effective_conditions(instance, combat, ally)
    assert "frightened" in ally["conditions"]
    assert "frightened" not in effective
    assert ally["conditions"]["frightened"]["source_actor_id"] == "enemy:goblin-1"
    assert ally["conditions"]["frightened"]["turns_remaining"] == 10

    target = _view(engine, instance, "enemy:goblin-1")
    suppressed = attack_mode(engine, instance, ally, target, False, combat=combat)
    assert suppressed["mode"] == ""
    _place(instance, **{"player:monk": 11})
    outside = _view(engine, instance, "player:monk")
    active = attack_mode(engine, instance, outside, target, False, combat=combat)
    assert active["mode"] == "disadvantage"
    assert active["sources"] == [{"code": "frightened", "mode": "disadvantage"}]


def test_leaving_the_aura_restores_the_same_entry() -> None:
    """14. Nothing is re-applied, refreshed or duplicated on the way out."""

    engine, instance = _aura_party()
    _seed_condition(instance, "monk", "frightened")
    before = deepcopy(_canonical(instance, "monk")["conditions"]["frightened"])

    _place(instance, **{"player:monk": 11})
    after = _canonical(instance, "monk")["conditions"]["frightened"]

    assert after == before
    assert set(_canonical(instance, "monk")["conditions"]) == {"frightened"}


def test_an_incapacitated_paladin_switches_the_aura_off() -> None:
    """15. The emanation dies with the holder, and comes back with them."""

    engine, instance = _aura_party()
    _seed_condition(instance, "monk", "frightened")
    combat = _combat_state(instance)
    assert "frightened" not in engine._effective_conditions(
        instance, combat, _view(engine, instance, "player:monk"),
    )

    _seed_condition(instance, "gm", "paralyzed")
    assert "frightened" in engine._effective_conditions(
        instance, combat, _view(engine, instance, "player:monk"),
    )

    _canonical(instance, "gm")["conditions"].pop("paralyzed")
    assert "frightened" not in engine._effective_conditions(
        instance, combat, _view(engine, instance, "player:monk"),
    )


def test_a_suppressed_condition_can_still_be_removed_for_good() -> None:
    """16. Removal inside the aura is real; leaving does not resurrect it."""

    engine, instance = _aura_party()
    _seed_condition(instance, "monk", "frightened")

    _end_turn(engine, instance, actor_id="player:gm")
    events = _end_turn(engine, instance, actor_id="player:monk", choice="frightened")
    assert [event["condition"] for event in _removals(events)] == ["frightened"]

    _place(instance, **{"player:monk": 11})
    assert "frightened" not in _canonical(instance, "monk")["conditions"]


def test_two_paladins_sharing_a_body_do_not_stack_or_delete() -> None:
    """18. Two auras, one suppression, one canonical entry."""

    engine, instance = _party_combat(
        {"gm": _paladin(10), "second": _paladin(10), "monk": _monk(10)},
        positions={"player:gm": 0, "player:second": 4, "player:monk": 10},
    )
    _seed_condition(instance, "monk", "frightened")
    combat = _combat_state(instance)

    ally = _view(engine, instance, "player:monk")
    assert "frightened" not in engine._effective_conditions(instance, combat, ally)
    assert set(ally["conditions"]) == {"frightened"}
    target = _view(engine, instance, "enemy:goblin-1")
    assert attack_mode(engine, instance, ally, target, False, combat=combat)["mode"] == ""


def test_suppression_reads_the_same_in_flags_modes_and_validation() -> None:
    """19. One view, four readers -- and the intent boundary agrees."""

    engine, instance = _aura_party()
    _seed_condition(instance, "monk", "frightened")
    combat = _combat_state(instance)
    _place(instance, **{"player:monk": 30, "enemy:goblin-1": 20})

    outside = _view(engine, instance, "player:monk")
    assert engine._effective_conditions(instance, combat, outside)["frightened"]
    with_closer = engine._distance_after_move(combat, "player:monk", "enemy:goblin-1", -5)
    assert with_closer < engine._distance(combat, "player:monk", "enemy:goblin-1")

    _place(instance, **{"player:monk": 5})
    inside = _view(engine, instance, "player:monk")
    assert "frightened" not in engine._effective_conditions(instance, combat, inside)


def test_a_frightened_ally_cannot_walk_towards_its_fear() -> None:
    """19. The movement restriction is enforced at the intent boundary."""

    engine, instance = _party_combat(
        {"gm": _monk(10)}, positions={"player:gm": 30, "enemy:goblin-1": 20},
    )
    _seed_condition(instance, "gm", "frightened")

    closer = engine.resolve_intent(instance, {
        "intent_id": "towards", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "distance": -5,
    }, SequenceRng([]))
    assert closer["ok"] is False
    assert "cannot move closer" in str(closer.get("error"))

    away = _move(engine, instance, 5)
    assert any(event["type"] == "dnd2024.position.changed" for event in away)


def test_a_charmed_creature_cannot_attack_its_charmer() -> None:
    """Charmed has a mechanical effect, so its immunity is not a no-op."""

    engine, instance = _party_combat(
        {"gm": _monk(10)}, positions={"player:gm": 5, "enemy:goblin-1": 5},
    )
    weapon = _first_weapon(engine, instance, "gm")
    _seed_condition(instance, "gm", "charmed")

    blocked = engine.resolve_intent(instance, {
        "intent_id": "attack-charmer", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": weapon,
    }, SequenceRng([10]))
    assert blocked["ok"] is False
    assert "charmed" in str(blocked.get("error"))

    _canonical(instance, "gm")["conditions"].pop("charmed")
    allowed = engine.resolve_intent(instance, {
        "intent_id": "attack-free", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": weapon,
    }, SequenceRng([10, 5, 4]))
    assert allowed["ok"] is True


# ---------------------------------------------------------------------------
# 20-21: persistence, replay, concurrency
# ---------------------------------------------------------------------------


def test_the_entry_and_its_suppression_survive_a_save_and_load() -> None:
    """20. Suppression is recomputed from facts, never stored as a flag."""

    engine, instance = _aura_party()
    _seed_condition(instance, "monk", "frightened")

    stored = json.loads(json.dumps(_canonical(instance, "monk")))
    instance.players["monk"]["character_sheet"]["ruleset_character"] = stored

    combat = _combat_state(instance)
    ally = engine._actor_view(instance, combat, "player:monk")
    assert "frightened" in ally["conditions"]
    assert "frightened" not in engine._effective_conditions(instance, combat, ally)


def test_replaying_the_removal_batch_does_not_remove_twice() -> None:
    """20. A second apply of the same batch is a no-op, not a second removal."""

    engine, instance = _combat(_monk(10))
    _seed_condition(instance, "gm", "charmed")
    intent = {
        "intent_id": "end-1", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "self_restoration_condition": "charmed",
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng([]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    replay = engine.apply_batch(instance, resolved["event_batch"])
    assert replay["applied"] is False
    assert "charmed" not in _canonical(instance)["conditions"]


def test_a_stale_version_is_refused_without_a_partial_write() -> None:
    """21. Concurrency is refused at the boundary, not repaired afterwards."""

    engine, instance = _combat(_monk(10))
    _seed_condition(instance, "gm", "charmed")
    version = ruleset_runtime.state(instance)["version"]

    resolved = engine.resolve_intent(instance, {
        "intent_id": "end-stale", "type": "end_turn",
        "expected_version": version - 1, "submitted_by": "gm",
        "actor_id": "player:gm", "self_restoration_condition": "charmed",
    }, SequenceRng([]))

    assert resolved["ok"] is False
    assert "version conflict" in str(resolved.get("error"))
    assert "charmed" in _canonical(instance)["conditions"]
    assert ruleset_runtime.state(instance)["version"] == version


def test_the_declarations_fail_closed_when_malformed() -> None:
    """1. Every new parameter shape is rejected at load, not at first use."""

    from dataclasses import replace

    from src.rulesets.dnd2024.features.models import ClassFeatureError

    mutations = [
        ("mindless_rage", {"immune_conditions": []}),
        ("mindless_rage", {"immune_conditions": "charmed"}),
        ("mindless_rage", {"immune_conditions": ["charmed", "charmed"]}),
        ("mindless_rage", {"immune_while_condition": ""}),
        ("mindless_rage", {"removes_conditions_on_gaining": ["charmed"]}),
        ("self_restoration", {"removable_at": "any_time"}),
        ("self_restoration", {"removable_conditions": []}),
        ("self_restoration", {"exhaustion_sources_ignored": []}),
    ]

    for feature_id, override in mutations:
        base = RUNTIME.load_bundle("en")
        bundle = replace(base, entities={k: dict(v) for k, v in base.entities.items()})
        raw = bundle.entities["class_feature_catalog"]["srd_class_features"]
        raw = {**raw, "classes": {
            cls: {**body, "features": [dict(item) for item in body["features"]]}
            for cls, body in raw["classes"].items()
        }}
        bundle.entities["class_feature_catalog"]["srd_class_features"] = raw
        target = next(
            item for cls in raw["classes"].values() for item in cls["features"]
            if item["id"] == feature_id
        )
        target["parameters"].update(override)

        rejected = False
        try:
            Dnd2024ClassFeatureResolver(bundle)
        except ClassFeatureError:
            rejected = True
        assert rejected, (feature_id, override)
