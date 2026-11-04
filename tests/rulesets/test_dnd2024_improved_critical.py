"""Improved Critical / Superior Critical (Champion): ownership + crit threshold.

Two primitives are under test: subclass feature ownership derived from
``(class_id, level)``, and the ``crit_threshold`` parameter consumed by the
weapon / Unarmed Strike attack branch.  Every combat assertion goes through
``resolve_intent`` -> ``apply_batch`` like the web layer does.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_instance,
    with_equipment,
)


def _fighter_sheet(runtime: Dnd2024Runtime, level: int = 1) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Fighter"},
    )
    # 高等级必须走真实升级链；默认 helper 总把 ASI 加在 STR 上会撞 20 上限，
    # 因此这里按"仍有空间"的确定性规则分配。
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        asi = next(
            (
                requirement for requirement in preview.get("requirements") or []
                if requirement["id"] == "ability_score_increases"
            ),
            None,
        )
        if asi is not None:
            total = int(asi.get("total", 2))
            abilities = sheet["ruleset_character"]["abilities"]
            allowed = asi.get("allowed", "any")
            pool = list(abilities) if allowed == "any" else list(allowed)
            ability = next(
                key for key in pool if abilities[key] + total <= 20
            )
            picks["ability_score_increases"] = {ability: total}
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _bundle_with(runtime: Dnd2024Runtime, mutate) -> object:
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    mutate(entities["class_feature_catalog"]["srd_class_features"])
    return replace(bundle, entities=entities)


def _improved_critical(raw: dict) -> dict:
    return next(
        feature for feature in raw["classes"]["fighter"]["features"]
        if feature["id"] == "improved_critical"
    )


# --- #1 / #2 catalog validation -----------------------------------------


@pytest.mark.parametrize("value", [0, 20, 21, "19", True])
def test_catalog_rejects_an_invalid_crit_threshold(value) -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _improved_critical(raw)["parameters"]["crit_threshold"] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


def test_catalog_rejects_a_minimum_level_that_drifts_from_the_subclass_table() -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        # 子职表在 3 级授予 improved_critical；声明成 7 级必须 fail closed。
        _improved_critical(raw)["minimum_level"] = 7

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


# --- #3 to #6 subclass ownership ----------------------------------------


def test_level_three_fighter_owns_improved_critical_only() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = _fighter_sheet(runtime, 3)["ruleset_character"]

    assert resolver.has_feature(character, "improved_critical") is True
    assert resolver.has_feature(character, "superior_critical") is False


def test_ownership_is_derived_without_features_subclass_ref() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = deepcopy(_fighter_sheet(runtime, 5)["ruleset_character"])
    (character.get("features") or {}).pop("subclass_ref", None)

    assert "subclass_ref" not in (character.get("features") or {})
    assert resolver.has_feature(character, "improved_critical") is True


@pytest.mark.parametrize("level, expected", [(14, False), (15, True)])
def test_superior_critical_starts_at_level_fifteen(level: int, expected: bool) -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = _fighter_sheet(runtime, level)["ruleset_character"]

    assert resolver.has_feature(character, "superior_critical") is expected


def test_level_two_fighter_does_not_own_improved_critical() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = _fighter_sheet(runtime, 2)["ruleset_character"]

    assert resolver.has_feature(character, "improved_critical") is False


def test_other_classes_never_own_champion_features() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    for class_ref in ("class:rogue", "class:wizard"):
        character = {"build": {"class_levels": [{"class_ref": class_ref, "level": 3}]}}
        assert resolver.has_feature(character, "improved_critical") is False
        assert resolver.has_feature(character, "superior_critical") is False


# --- combat helpers ------------------------------------------------------


def _combat(
    runtime: Dnd2024Runtime, sheet: dict, *, enemy_first: bool = False,
) -> tuple[object, object]:
    engine, instance = monk_instance(runtime, sheet)
    rolls = [1, 20] if enemy_first else [20, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [{
            "id": "goblin-1", "name": "Goblin", "hp": 40, "armor_class": 12,
            "speed": 30, "position": 5, "initiative_modifier": 2,
            "abilities": {
                "str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8,
            },
            "saving_throws": {"dex": 2, "wis": -1, "con": 0},
            "attacks": [{
                "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            }],
        }],
    }, SequenceRng(rolls))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return engine, instance


def _attack(engine, instance, weapon_ref: str, rolls: list[int]) -> list[dict]:
    intent = {
        "intent_id": f"attack:{weapon_ref}", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": weapon_ref,
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _check(events: list[dict], kind: str = "attack") -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def _damage(events: list[dict], target_id: str = "enemy:goblin-1") -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed"
        and event.get("resource") == "hp"
        and event.get("target_id") == target_id
    )


def _enemy_state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]


def _paralyze(instance) -> None:
    _enemy_state(instance).setdefault("conditions", {})["paralyzed"] = {"source": "test"}


# --- #7 / #8 / #10 threshold behavior ------------------------------------


def test_level_three_fighter_crits_on_nineteen_with_a_weapon() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [19, 2, 3])

    check = _check(events)
    assert check["natural"] == 19
    assert check["critical"] is True
    assert check["success"] is True
    assert check["crit_threshold"] == 19
    damage = _damage(events)
    assert damage["critical"] is True
    assert len(damage["rolls"]) == 2  # 1d4 doubled


@pytest.mark.parametrize("natural", [18, 19])
def test_level_fifteen_threshold_is_eighteen_not_seventeen(natural: int) -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 15), "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [natural, 2, 3])

    check = _check(events)
    assert check["natural"] == natural
    assert check["critical"] is True
    assert check["crit_threshold"] == 18
    assert len(_damage(events)["rolls"]) == 2


def test_level_two_fighter_does_not_crit_on_nineteen() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 2), "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [19, 2])

    check = _check(events)
    assert check["critical"] is False
    assert check["success"] is True  # 19 + modifier clears AC 12 normally
    assert "crit_threshold" not in check
    damage = _damage(events)
    assert damage["critical"] is False
    assert len(damage["rolls"]) == 1


# --- #9 / #13 natural 1 never crits ---------------------------------------


def test_natural_one_misses_even_with_improved_critical() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [1])

    check = _check(events)
    assert check["natural"] == 1
    assert check["success"] is False
    assert check["critical"] is False
    assert check["crit_threshold"] == 19
    assert not [
        event for event in events
        if event["type"] == "resource.changed" and event.get("resource") == "hp"
    ]


def test_paralyzed_melee_hit_still_upgrades_to_a_crit() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)
    _paralyze(instance)

    events = _attack(engine, instance, "item:dagger", [14, 2, 3])

    check = _check(events)
    assert check["natural"] == 14
    assert check["success"] is True
    assert check["critical"] is True  # 5 尺内命中麻痹目标仍升级
    assert len(_damage(events)["rolls"]) == 2


def test_paralyzed_target_does_not_upgrade_a_natural_one() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)
    _paralyze(instance)

    events = _attack(engine, instance, "item:dagger", [1])

    check = _check(events)
    assert check["success"] is False
    assert check["critical"] is False
    assert not [
        event for event in events
        if event["type"] == "resource.changed" and event.get("resource") == "hp"
    ]


# --- #11 opportunity attack uses the same consumption point ----------------


def test_level_fifteen_opportunity_attack_crits_on_nineteen() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 15), "item:dagger")
    engine, instance = _combat(runtime, sheet, enemy_first=True)
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "enemy:goblin-1"

    moved = engine.resolve_intent(instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": -15,
    }, SequenceRng([]))
    assert moved["ok"] is True, moved
    assert engine.apply_batch(instance, moved["event_batch"])["applied"] is True
    decision = moved["pending_decision"]
    assert decision["kind"] == "opportunity_attack"

    decided = engine.resolve_intent(instance, {
        "intent_id": "decision-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "resolve",
    }, SequenceRng([19, 2, 3]))
    assert decided["ok"] is True, decided
    assert engine.apply_batch(instance, decided["event_batch"])["applied"] is True

    check = _check(decided["event_batch"]["events"])
    assert check["natural"] == 19
    assert check["critical"] is True
    assert check["crit_threshold"] == 18


# --- #12 unarmed strike uses the same branch ------------------------------


def test_level_three_unarmed_strike_crits_on_nineteen() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3))
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "unarmed_strike", [19])

    check = _check(events)
    assert check["natural"] == 19
    assert check["critical"] is True
    assert check["success"] is True
    assert check["crit_threshold"] == 19
    assert _damage(events)["critical"] is True


# --- #14 critical implies a hit regardless of AC ---------------------------


def test_nineteen_crits_against_a_target_above_the_total() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)
    _enemy_state(instance)["armor_class"] = 30

    events = _attack(engine, instance, "item:dagger", [19, 2, 3])

    check = _check(events)
    assert check["total"] < check["target"]
    assert check["success"] is True
    assert check["critical"] is True


# --- #15 spell attacks keep the natural-20 rule ----------------------------


def test_spell_attack_branch_keeps_the_natural_twenty_rule() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src/rulesets/dnd2024/combat/resolution.py"
    )
    text = source.read_text(encoding="utf-8")
    start = text.index('if mode == "spell_attack":')
    end = text.index('elif mode == "saving_throw":', start)
    branch = text[start:end]

    assert "critical = roll == 20" in branch
    assert "crit_threshold" not in branch
