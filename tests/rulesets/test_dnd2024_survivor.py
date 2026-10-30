"""Survivor (Champion 18): turn-start regeneration + death-save advantage.

Two primitives are under test: the paired ``turn_start_regen_*`` declaration
consumed after ``dnd2024.turn.advanced``, and the ``death_save_advantage`` /
``death_save_crit_threshold`` pair consumed by the death-save roll.  Every
combat assertion goes through ``resolve_intent`` -> ``apply_batch`` like the
web layer does.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, advancement_choices


def _sheet(
    runtime: Dnd2024Runtime, level: int = 1, preset_id: str = "stalwart_guardian",
) -> dict:
    """A preset character advanced through the real chain to ``level``.

    默认 helper 总把 ASI 加在同类属性上会撞 20 上限，因此这里按"仍有空间"的
    确定性规则分配，与 S-P1 的 18 级构造同款。
    """

    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Arden"},
    )
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


def _survivor(raw: dict) -> dict:
    return next(
        feature for feature in raw["classes"]["fighter"]["features"]
        if feature["id"] == "survivor"
    )


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _goblin(*, position: int = 5, hp: int = 40) -> dict:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 2,
        "creature_type": "humanoid",
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _instance(bundle, sheet: dict) -> tuple[Dnd2024CombatEngine, GameInstance]:
    instance = GameInstance(
        game_key=("test", "dnd2024-survivor", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["ruleset_character"]["rule_binding"])
    return Dnd2024CombatEngine(bundle, EncounterAccess.sandbox()), instance


def _combat(
    runtime: Dnd2024Runtime, sheet: dict, *, enemy_first: bool = False,
) -> tuple[object, object]:
    engine, instance = _instance(runtime.load_bundle("en"), sheet)
    rolls = [1, 20] if enemy_first else [20, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng(rolls))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    expected = "enemy:goblin-1" if enemy_first else "player:gm"
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == expected
    return engine, instance


def _end_turn(engine, instance, actor_id: str, rolls: list[int] | None = None) -> list[dict]:
    intent = {
        "intent_id": f"end:{actor_id}", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id,
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls or [])))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _death_save(engine, instance, actor_id: str = "player:gm", rolls=()) -> list[dict]:
    intent = {
        "intent_id": "death-save-1", "type": "death_save", "actor_id": actor_id,
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _check(events: list[dict], kind: str = "death_save") -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def _resolved(events: list[dict]) -> dict:
    return next(event for event in events if event["type"] == "dnd2024.death_save.resolved")


def _regen(events: list[dict]) -> dict | None:
    return next(
        (
            event for event in events
            if event["type"] == "resource.changed" and event.get("resource") == "hp"
            and event.get("healing") is True
        ),
        None,
    )


def _bloodied(sheet: dict) -> tuple[dict, int, int]:
    canonical = sheet["ruleset_character"]
    resources = canonical["resources"]
    maximum = int(resources["max_hp"])
    hp = maximum // 2
    resources["hp"] = hp
    return sheet, hp, maximum


# --- #1 to #4 catalog validation ----------------------------------------


@pytest.mark.parametrize("value", [1, 20, "18", True])
def test_catalog_rejects_an_invalid_death_save_threshold(value) -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _survivor(raw)["parameters"]["death_save_crit_threshold"] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


@pytest.mark.parametrize("value", [-1, True, "5"])
def test_catalog_rejects_an_invalid_regeneration_base(value) -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _survivor(raw)["parameters"]["turn_start_regen_base"] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


@pytest.mark.parametrize("value", ["xyz", 3])
def test_catalog_rejects_an_invalid_regeneration_ability(value) -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _survivor(raw)["parameters"]["turn_start_regen_ability"] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


def test_catalog_requires_the_regeneration_pair_together() -> None:
    runtime = Dnd2024Runtime()

    def mutate_base_only(raw: dict) -> None:
        _survivor(raw)["parameters"].pop("turn_start_regen_ability")

    def mutate_ability_only(raw: dict) -> None:
        _survivor(raw)["parameters"].pop("turn_start_regen_base")

    for mutate in (mutate_base_only, mutate_ability_only):
        with pytest.raises(ClassFeatureError):
            Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


@pytest.mark.parametrize("value", [False, 1, "true"])
def test_catalog_rejects_a_non_true_death_save_advantage(value) -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _survivor(raw)["parameters"]["death_save_advantage"] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


# --- #5 / #6 ownership ---------------------------------------------------


def test_level_eighteen_fighter_owns_survivor() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = _sheet(runtime, 18)["ruleset_character"]

    assert resolver.has_feature(character, "survivor") is True
    assert resolver.has_feature(_sheet(runtime, 17)["ruleset_character"], "survivor") is False


def test_ownership_is_derived_without_features_subclass_ref() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = {
        "build": {"class_levels": [{"class_ref": "class:fighter", "level": 18}]},
    }

    assert resolver.has_feature(character, "survivor") is True


# --- #7 / #8 / #10 / #11 / #12 regeneration ------------------------------


def test_bloodied_fighter_regenerates_at_turn_start() -> None:
    runtime = Dnd2024Runtime()
    sheet, hp, maximum = _bloodied(_sheet(runtime, 18))
    con_modifier = ability_modifier(int(sheet["ruleset_character"]["abilities"]["con"]))
    engine, instance = _combat(runtime, sheet, enemy_first=True)
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "enemy:goblin-1"

    events = _end_turn(engine, instance, "enemy:goblin-1")

    types = [event["type"] for event in events]
    assert types[-2] == "dnd2024.turn.advanced"
    regen = _regen(events)
    assert regen is not None
    assert regen["amount"] == 5 + con_modifier
    assert regen["feature_id"] == "survivor"
    assert types.index("dnd2024.turn.advanced") < types.index("resource.changed")
    assert int(_canonical(instance)["resources"]["hp"]) == hp + 5 + con_modifier


@pytest.mark.parametrize("offset", [1, 0])
def test_regeneration_skips_above_bloodied_and_full_hp(offset: int) -> None:
    runtime = Dnd2024Runtime()
    sheet = _sheet(runtime, 18)
    resources = sheet["ruleset_character"]["resources"]
    maximum = int(resources["max_hp"])
    resources["hp"] = maximum // 2 + 1 if offset else maximum
    engine, instance = _combat(runtime, sheet, enemy_first=True)

    events = _end_turn(engine, instance, "enemy:goblin-1")

    assert _regen(events) is None


@pytest.mark.parametrize("condition", ["zero", "dead", "stable"])
def test_regeneration_never_ticks_on_a_downed_actor(condition: str) -> None:
    runtime = Dnd2024Runtime()
    sheet = _sheet(runtime, 18)
    engine, instance = _combat(runtime, sheet, enemy_first=True)
    canonical = _canonical(instance)
    canonical["resources"]["hp"] = 0
    if condition == "zero":
        canonical["conditions"] = {
            "unconscious": {"source": "zero_hp"},
            "death_saves": {"successes": 0, "failures": 1},
        }
    elif condition == "dead":
        canonical["conditions"] = {"dead": {"source": "death_saves"}}
    else:
        canonical["conditions"] = {"stable": {"duration": "until_healed"}}

    events = _end_turn(engine, instance, "enemy:goblin-1")

    assert _regen(events) is None
    if condition == "zero":
        assert "death_save" in [
            item["type"] for item in engine.available_intents(instance, "gm")
        ]


def test_regeneration_amount_uses_the_constitution_modifier() -> None:
    runtime = Dnd2024Runtime()
    sheet, _hp, _maximum = _bloodied(_sheet(runtime, 18))
    sheet["ruleset_character"]["abilities"]["con"] = 4
    engine, instance = _combat(runtime, sheet, enemy_first=True)

    events = _end_turn(engine, instance, "enemy:goblin-1")

    regen = _regen(events)
    assert regen is not None
    assert regen["amount"] == 2


def test_zero_base_and_negative_modifier_produce_no_event() -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _survivor(raw)["parameters"]["turn_start_regen_base"] = 0

    sheet, _hp, _maximum = _bloodied(_sheet(runtime, 18))
    sheet["ruleset_character"]["abilities"]["con"] = 4
    engine, instance = _combat_for_bundle(_bundle_with(runtime, mutate), sheet)

    events = _end_turn(engine, instance, "enemy:goblin-1")

    assert _regen(events) is None


def _combat_for_bundle(bundle, sheet: dict) -> tuple[object, object]:
    engine, instance = _instance(bundle, sheet)
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng([1, 20]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "enemy:goblin-1"
    return engine, instance


def test_regeneration_is_clamped_to_max_hp_by_the_reducer() -> None:
    runtime = Dnd2024Runtime()
    sheet = _sheet(runtime, 18)
    resources = sheet["ruleset_character"]["resources"]
    resources["max_hp"] = 10
    resources["hp"] = 5
    engine, instance = _combat(runtime, sheet, enemy_first=True)

    events = _end_turn(engine, instance, "enemy:goblin-1")

    regen = _regen(events)
    assert regen is not None
    assert regen["amount"] > 5
    assert int(_canonical(instance)["resources"]["hp"]) == 10


def test_cannot_regain_hp_blocks_the_regeneration() -> None:
    runtime = Dnd2024Runtime()
    sheet, hp, _maximum = _bloodied(_sheet(runtime, 18))
    sheet["ruleset_character"].setdefault("conditions", {})["cannot_regain_hp"] = {
        "source": "test",
    }
    engine, instance = _combat(runtime, sheet, enemy_first=True)

    events = _end_turn(engine, instance, "enemy:goblin-1")

    regen = _regen(events)
    assert regen is not None
    assert regen["delta"] == 0
    assert regen["blocked_by"] == "cannot_regain_hp"
    assert int(_canonical(instance)["resources"]["hp"]) == hp


# --- #13 ordering: repeat save -> turn.advanced -> regeneration -----------


def test_repeat_save_precedes_turn_advance_and_regeneration() -> None:
    runtime = Dnd2024Runtime()
    sheet, _hp, _maximum = _bloodied(_sheet(runtime, 18))
    engine, instance = _combat(runtime, sheet, enemy_first=True)
    enemies = ruleset_runtime.state(instance)["combat"]["enemies"]
    enemies["goblin-1"].setdefault("conditions", {})["paralyzed"] = {
        "source": "test",
        "save_repeat": {"ability": "wis", "dc": 15},
    }

    events = _end_turn(engine, instance, "enemy:goblin-1", rolls=[10])

    types = [event["type"] for event in events]
    assert types.index("check.resolved") < types.index("dnd2024.turn.advanced")
    assert types.index("dnd2024.turn.advanced") < types.index("resource.changed")
    assert _regen(events) is not None


# --- #14 to #18 death saves ---------------------------------------------


def _at_zero(engine, instance) -> None:
    canonical = _canonical(instance)
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 1},
    }


def test_death_save_has_advantage_and_provenance() -> None:
    runtime = Dnd2024Runtime()
    sheet = _sheet(runtime, 18)
    engine, instance = _combat(runtime, sheet)
    _at_zero(engine, instance)

    events = _death_save(engine, instance, rolls=[13, 4])

    check = _check(events)
    assert len(check["rolls"]) == 2
    assert check["advantage_mode"] == "advantage"
    assert check["advantage_sources"] == [{"code": "survivor", "mode": "advantage"}]
    assert check["crit_threshold"] == 18


@pytest.mark.parametrize("rolls", [[18, 5], [19, 2], [20, 3]])
def test_threshold_eighteen_to_twenty_treats_as_a_twenty(rolls: list[int]) -> None:
    runtime = Dnd2024Runtime()
    sheet = _sheet(runtime, 18)
    engine, instance = _combat(runtime, sheet)
    _at_zero(engine, instance)

    events = _death_save(engine, instance, rolls=rolls)

    resolved = _resolved(events)
    assert resolved["hp"] == 1
    assert resolved["successes"] == 0
    assert resolved["failures"] == 0
    assert int(_canonical(instance)["resources"]["hp"]) == 1


def test_level_seventeen_fighter_keeps_a_single_die_and_normal_success() -> None:
    runtime = Dnd2024Runtime()
    sheet = _sheet(runtime, 17)
    engine, instance = _combat(runtime, sheet)
    _at_zero(engine, instance)

    events = _death_save(engine, instance, rolls=[18])

    check = _check(events)
    assert len(check["rolls"]) == 1
    assert "advantage_mode" not in check
    assert "crit_threshold" not in check
    resolved = _resolved(events)
    assert resolved["hp"] == 0
    assert resolved["successes"] == 1


@pytest.mark.parametrize("rolls, failures, successes", [
    ([1, 1], 3, 0),
    ([1, 15], 1, 1),
])
def test_advantage_keeps_natural_one_and_takes_the_highest(
    rolls: list[int], failures: int, successes: int,
) -> None:
    runtime = Dnd2024Runtime()
    sheet = _sheet(runtime, 18)
    engine, instance = _combat(runtime, sheet)
    _at_zero(engine, instance)
    _canonical(instance)["conditions"]["death_saves"] = {"successes": 0, "failures": 1}

    events = _death_save(engine, instance, rolls=rolls)

    resolved = _resolved(events)
    assert resolved["successes"] == successes
    assert resolved["failures"] == failures
    assert resolved["hp"] == 0


# --- #19 combat start never ticks ---------------------------------------


def test_combat_start_does_not_regenerate() -> None:
    runtime = Dnd2024Runtime()
    sheet, hp, _maximum = _bloodied(_sheet(runtime, 18))
    engine, instance = _instance(runtime.load_bundle("en"), sheet)
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True, resolved
    events = resolved["event_batch"]["events"]
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    # 开场批次不经过 turn.advanced，裁剪行为由此锁定。
    assert _regen(events) is None
    assert int(_canonical(instance)["resources"]["hp"]) == hp


# --- #20 class isolation ------------------------------------------------


def test_other_classes_never_own_survivor_and_roll_one_die() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    for class_ref in ("class:rogue", "class:wizard"):
        character = {"build": {"class_levels": [{"class_ref": class_ref, "level": 18}]}}
        assert resolver.has_feature(character, "survivor") is False

    sheet = _sheet(runtime, 18, preset_id="lucky_scout")
    engine, instance = _combat(runtime, sheet)
    _at_zero(engine, instance)

    events = _death_save(engine, instance, rolls=[13])

    check = _check(events)
    assert len(check["rolls"]) == 1
    assert "advantage_mode" not in check
