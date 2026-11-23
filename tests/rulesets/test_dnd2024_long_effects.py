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
