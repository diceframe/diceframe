# -*- coding: utf-8 -*-
"""S0-F30 长时状态特性。

按任务书编号逐项覆盖；目前完成 Intimidating Presence（测试 1、10、11），
其余四个特性随后补入同一文件。
"""

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from tests.rulesets.test_dnd2024_channel_control import (
    _apply,
    _build,
    _capability,
    _conditions,
    _of_type,
    _setup,
    _start,
    _next_turn,
    _to_turn,
    _two_enemies,
)

BARBARIAN_ABILITIES = {"str": 14, "dex": 12, "con": 15, "int": 8, "wis": 10, "cha": 13}
BARBARIAN_SKILLS = ["skill:athletics", "skill:survival"]


def _barbarian(bundle, level: int) -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:barbarian", level,
        BARBARIAN_ABILITIES, BARBARIAN_SKILLS,
        "equipment_package:barbarian_a",
        "subclass:path_of_the_berserker" if level >= 3 else "",
    )


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle("en")


@pytest.fixture(scope="module")
def berserker(bundle):
    return _barbarian(bundle, 14)


def _capability_ids(bundle, sheet) -> set[str]:
    features = Dnd2024ClassFeatureResolver(bundle)
    character = sheet["ruleset_character"]
    views = features.combat_capabilities(
        character, {"action": 1, "bonus_action": 1}, actor_id="player:gm",
    )
    return {view.id for view in views}


def test_intimidating_presence_arrives_at_level_fourteen(bundle) -> None:
    assert "intimidating_presence" not in _capability_ids(bundle, _barbarian(bundle, 13))
    assert "intimidating_presence" in _capability_ids(bundle, _barbarian(bundle, 14))


def test_a_use_costs_a_bonus_action_and_marks_the_feature_used(berserker) -> None:
    engine, instance = _setup(berserker)
    _start(engine, instance, enemies=_two_enemies())

    events = _apply(engine, instance, _capability(
        engine, instance, "intimidating_presence", [1, 1],
        target_ids=["enemy:goblin-1"],
    ))

    spent = _of_type(events, "dnd2024.action.spent")
    assert [row.get("resource") for row in spent] == ["bonus_action"]
    usage = _of_type(events, "dnd2024.feature_usage.changed")
    assert len(usage) == 1
    assert usage[0]["after_count"] == 1


def test_a_second_use_before_a_long_rest_is_refused(berserker) -> None:
    engine, instance = _setup(berserker)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(
        engine, instance, "intimidating_presence", [1],
        target_ids=["enemy:goblin-1"],
    ))

    # 结束自己的回合并转回来：附赠动作刷新了，但特性本身仍处于已用状态。
    _next_turn(engine, instance, "player:gm")
    resolved = _capability(
        engine, instance, "intimidating_presence", [1],
        target_ids=["enemy:goblin-1"],
    )

    assert resolved["ok"] is False
    assert "long rest" in resolved["error"]


def test_a_failed_save_frightens_with_a_repeat_save(berserker) -> None:
    engine, instance = _setup(berserker)
    _start(engine, instance, enemies=_two_enemies())

    events = _apply(engine, instance, _capability(
        engine, instance, "intimidating_presence", [1, 1],
        target_ids=["enemy:goblin-1"],
    ))

    character = instance.get_character_sheet("gm")["ruleset_character"]
    expected_dc = (
        8
        + (int(character["abilities"]["str"]) - 10) // 2
        + int(character["derived"]["proficiency_bonus"])
    )
    check = _of_type(events, "check.resolved")[0]
    # DC 由力量与熟练算出，与法术 DC 无关。
    assert check["target"] == expected_dc
    assert check["ability"] == "wis"
    applied = _of_type(events, "condition.applied")[0]
    assert applied["condition"] == "frightened"
    assert applied["duration"] == "intimidating_presence"
    assert applied["save_repeat"] == {"ability": "wis", "dc": expected_dc}
    assert _conditions(instance, "enemy:goblin-1")["frightened"]


def test_a_successful_save_leaves_no_condition(berserker) -> None:
    engine, instance = _setup(berserker)
    _start(engine, instance, enemies=_two_enemies())

    events = _apply(engine, instance, _capability(
        engine, instance, "intimidating_presence", [20],
        target_ids=["enemy:goblin-1"],
    ))

    assert _of_type(events, "check.resolved")[0]["success"] is True
    assert not _of_type(events, "condition.applied")
    assert "frightened" not in (_conditions(instance, "enemy:goblin-1") or {})


SORCERER_ABILITIES = {"str": 8, "dex": 14, "con": 15, "int": 10, "wis": 12, "cha": 13}
SORCERER_SKILLS = ["skill:arcana", "skill:deception"]


def _sorcerer(bundle, level: int) -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:sorcerer", level,
        SORCERER_ABILITIES, SORCERER_SKILLS,
        "equipment_package:sorcerer_a",
        "subclass:draconic_sorcery" if level >= 3 else "",
    )


@pytest.fixture(scope="module")
def draconic(bundle):
    return _sorcerer(bundle, 14)


def _fly_speed(instance) -> int:
    from src.rulesets.dnd2024.combat.primitives import world_minutes
    from src.rulesets.dnd2024.features.resources import fly_speed_from

    character = instance.get_character_sheet("gm")["ruleset_character"]
    return fly_speed_from(character, world_minutes(instance))


def test_dragon_wings_arrives_at_level_fourteen(bundle) -> None:
    assert "dragon_wings" not in _capability_ids(bundle, _sorcerer(bundle, 13))
    assert "dragon_wings" in _capability_ids(bundle, _sorcerer(bundle, 14))


def test_dragon_wings_grants_a_fly_speed_that_survives_the_battle(draconic) -> None:
    engine, instance = _setup(draconic)
    _start(engine, instance, enemies=_two_enemies())

    events = _apply(engine, instance, _capability(engine, instance, "dragon_wings"))

    applied = _of_type(events, "dnd2024.long_effect.applied")
    assert applied[0]["effect"]["detail"] == {"fly_speed": 60}
    assert _fly_speed(instance) == 60
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert "dragon_wings" in character["long_effects"]


def test_dragon_wings_expires_on_the_world_clock(draconic) -> None:
    from src.engine.world_state import clock_from_minutes

    engine, instance = _setup(draconic)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "dragon_wings"))

    instance.world_state["clock"] = clock_from_minutes(61)

    assert _fly_speed(instance) == 0


def test_a_second_activation_needs_a_long_rest(draconic) -> None:
    engine, instance = _setup(draconic)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "dragon_wings"))

    _next_turn(engine, instance, "player:gm")
    resolved = _capability(engine, instance, "dragon_wings")

    assert resolved["ok"] is False
    assert "long rest" in resolved["error"]


PALADIN_ABILITIES = {"str": 12, "dex": 13, "con": 14, "int": 8, "wis": 10, "cha": 15}
PALADIN_SKILLS = ["skill:athletics", "skill:persuasion"]


def _devotion(bundle, level: int) -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:paladin", level,
        PALADIN_ABILITIES, PALADIN_SKILLS,
        "equipment_package:paladin_a",
        "subclass:oath_of_devotion" if level >= 3 else "",
    )


@pytest.fixture(scope="module")
def devotion20(bundle):
    return _devotion(bundle, 20)


def test_holy_nimbus_arrives_at_level_twenty(bundle) -> None:
    assert "holy_nimbus" not in _capability_ids(bundle, _devotion(bundle, 19))
    assert "holy_nimbus" in _capability_ids(bundle, _devotion(bundle, 20))


def test_an_enemy_that_starts_inside_takes_radiant_damage(devotion20) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _hp, _positions

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _positions(instance, **{"enemy:goblin-1": 10})
    before = _hp(instance, "enemy:goblin-1")

    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert "holy_nimbus" in (character.get("long_effects") or {})
    _to_turn(engine, instance, "player:gm")
    _to_turn(engine, instance, "enemy:goblin-1")

    from src.engine.modules import ruleset_runtime as _rt

    combat_state = _rt.state(instance)["combat"]
    rows = engine._projected_auras(instance, combat_state)
    assert "holy_nimbus" in [row["definition"].id for row in rows]
    expected = (int(character["abilities"]["cha"]) - 10) // 2 + int(
        character["derived"]["proficiency_bonus"]
    )
    assert before - _hp(instance, "enemy:goblin-1") == expected


def test_an_enemy_outside_the_aura_is_untouched(devotion20) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _hp, _positions

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _positions(instance, **{"enemy:goblin-1": 40})
    before = _hp(instance, "enemy:goblin-1")

    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))
    _to_turn(engine, instance, "player:gm")
    _to_turn(engine, instance, "enemy:goblin-1")

    assert _hp(instance, "enemy:goblin-1") == before


def test_the_aura_stops_when_the_world_clock_passes_ten_minutes(devotion20) -> None:
    from src.engine.world_state import clock_from_minutes
    from src.rulesets.dnd2024.combat.primitives import world_minutes
    from src.rulesets.dnd2024.features.resources import long_effect_active

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))

    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert long_effect_active(character, "holy_nimbus", world_minutes(instance))

    instance.world_state["clock"] = clock_from_minutes(11)

    assert long_effect_active(
        character, "holy_nimbus", world_minutes(instance),
    ) is None


MONK_ABILITIES = {"str": 12, "dex": 15, "con": 13, "int": 8, "wis": 14, "cha": 10}
MONK_SKILLS = ["skill:acrobatics", "skill:stealth"]


def _open_hand(bundle, level: int) -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:monk", level,
        MONK_ABILITIES, MONK_SKILLS,
        "equipment_package:monk_a",
        "subclass:warrior_of_the_open_hand" if level >= 3 else "",
    )


@pytest.fixture(scope="module")
def open_hand17(bundle):
    return _open_hand(bundle, 17)


def _apply_vibration(engine, instance, target_id: str = "enemy:goblin-1") -> None:
    from tests.rulesets.test_dnd2024_channel_control import _inject

    _inject(engine, instance, [{
        "type": "dnd2024.long_effect.applied",
        "actor_id": "player:gm",
        "effect_id": "quivering_palm",
        "effect": {
            "feature_id": "quivering_palm",
            "expires_at": 10**9,
            "detail": {"target_id": target_id},
        },
    }])


def test_quivering_palm_arrives_at_level_seventeen(bundle) -> None:
    assert "quivering_palm_end" not in _capability_ids(bundle, _open_hand(bundle, 16))
    assert "quivering_palm_end" in _capability_ids(bundle, _open_hand(bundle, 17))


def test_ending_the_vibration_deals_ten_d12_force_on_a_failed_save(open_hand17) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _inject  # noqa: F401

    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())
    _apply_vibration(engine, instance)

    events = _apply(engine, instance, _capability(
        engine, instance, "quivering_palm_end", [1] + [6] * 10,
    ))

    check = _of_type(events, "check.resolved")[0]
    assert check["ability"] == "con"
    assert check["success"] is False
    assert check["plane_check"] == "guided"
    damage = _of_type(events, "resource.changed")[0]
    assert damage["delta"] == -60
    assert damage["damage_type"] == "force"
    assert _of_type(events, "dnd2024.long_effect.ended")


def test_a_successful_save_halves_the_damage(open_hand17) -> None:
    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())
    _apply_vibration(engine, instance)

    events = _apply(engine, instance, _capability(
        engine, instance, "quivering_palm_end", [20] + [6] * 10,
    ))

    damage = _of_type(events, "resource.changed")[0]
    assert damage["delta"] == -30


def test_ending_without_a_vibration_is_refused(open_hand17) -> None:
    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())

    resolved = _capability(engine, instance, "quivering_palm_end", [1])

    assert resolved["ok"] is False


def test_a_hit_can_start_the_vibration_and_spend_four_focus(open_hand17) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())

    events, _decisions = _attack(
        engine, instance, "unarmed_strike", dice=[20, 5], quivering_palm=True,
    )

    applied = _of_type(events, "dnd2024.long_effect.applied")
    assert applied[0]["effect_id"] == "quivering_palm"
    assert applied[0]["effect"]["detail"]["target_id"] == "enemy:goblin-1"
    spend = _of_type(events, "dnd2024.class_resource.spent")
    assert [row["amount"] for row in spend] == [4]


WARLOCK_ABILITIES = {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15}
WARLOCK_SKILLS = ["skill:arcana", "skill:deception"]


def _fiend(bundle, level: int) -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:warlock", level,
        WARLOCK_ABILITIES, WARLOCK_SKILLS,
        "equipment_package:warlock_a",
        "subclass:fiend_patron" if level >= 3 else "",
    )


@pytest.fixture(scope="module")
def fiend14(bundle):
    return _fiend(bundle, 14)


def test_hurl_through_hell_needs_level_fourteen(bundle) -> None:
    from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver

    features = Dnd2024ClassFeatureResolver(bundle)
    low = _fiend(bundle, 13)["ruleset_character"]
    high = _fiend(bundle, 14)["ruleset_character"]
    assert not features.has_feature(low, "hurl_through_hell")
    assert features.has_feature(high, "hurl_through_hell")


def test_a_failed_save_hurls_the_target_through_hell(fiend14) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    engine, instance = _setup(fiend14)
    _start(engine, instance, enemies=_two_enemies())

    events, _decisions = _attack(
        engine, instance, "item:dagger", dice=[20, 1, 1] + [6] * 8, hurl_through_hell=True,
    )

    check = [row for row in _of_type(events, "check.resolved") if row.get("ability") == "cha"][0]
    assert check["success"] is False
    damage = [
        row for row in _of_type(events, "resource.changed")
        if row.get("damage_type") == "psychic"
    ][0]
    assert damage["delta"] <= -8
    applied = _of_type(events, "condition.applied")[0]
    assert applied["condition"] == "incapacitated"
    assert applied["banishment"] == "guided"
    assert _of_type(events, "dnd2024.feature.used")


def test_a_successful_save_still_spends_the_use(fiend14) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    engine, instance = _setup(fiend14)
    _start(engine, instance, enemies=_two_enemies())

    events, _decisions = _attack(
        engine, instance, "item:dagger", dice=[20, 1, 1, 20], hurl_through_hell=True,
    )

    check = [row for row in _of_type(events, "check.resolved") if row.get("ability") == "cha"][0]
    assert check["success"] is True
    assert not [
        row for row in _of_type(events, "resource.changed")
        if row.get("damage_type") == "psychic"
    ]
    assert not _of_type(events, "condition.applied")
    assert _of_type(events, "dnd2024.feature.used")
