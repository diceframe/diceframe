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
    _enemy,
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
    from src.rulesets.dnd2024.features.resources import world_minutes
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
    from src.rulesets.dnd2024.features.resources import world_minutes
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
        same_plane=True,
    ))

    check = _of_type(events, "check.resolved")[0]
    assert check["ability"] == "con"
    assert check["success"] is False
    assert check["plane_asserted"] is True
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
        same_plane=True,
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


def test_holy_ward_grants_advantage_against_a_fiend(devotion20) -> None:
    from src.engine.modules import ruleset_runtime as _rt
    from tests.rulesets.test_dnd2024_channel_control import SequenceRng, _enemy

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=[_enemy(creature_type="fiend")])
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))

    combat = _rt.state(instance)["combat"]
    actor = engine._actor_view(instance, combat, "player:gm")
    check = engine._saving_throw_check(
        instance, combat, actor, "player:gm", "wis", 15, "enemy:goblin-1",
        [], SequenceRng([5, 6]),
    )

    codes = [row.get("code") for row in check.get("advantage_sources") or []]
    assert "holy_nimbus" in codes


def test_holy_ward_ignores_a_humanoid_source(devotion20) -> None:
    from src.engine.modules import ruleset_runtime as _rt
    from tests.rulesets.test_dnd2024_channel_control import SequenceRng, _enemy

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=[_enemy(creature_type="humanoid")])
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))

    combat = _rt.state(instance)["combat"]
    actor = engine._actor_view(instance, combat, "player:gm")
    check = engine._saving_throw_check(
        instance, combat, actor, "player:gm", "wis", 15, "enemy:goblin-1",
        [], SequenceRng([5]),
    )

    codes = [row.get("code") for row in check.get("advantage_sources") or []]
    assert "holy_nimbus" not in codes


def test_holy_nimbus_is_restored_by_spending_a_level_five_slot(devotion20) -> None:
    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))

    events = _apply(
        engine, instance, _capability(engine, instance, "holy_nimbus_restore"),
    )

    slot = _of_type(events, "dnd2024.spell.slot_spent")
    assert slot[0]["slot_level"] == 5
    usage = _of_type(events, "dnd2024.feature_usage.restored")
    assert usage[0]["before_count"] == 1
    # 恢复后同一次战斗里还能再点亮（先过一回合拿回附赠动作）。
    _next_turn(engine, instance, "player:gm")
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))


def test_restoring_without_a_use_is_refused(devotion20) -> None:
    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())

    resolved = _capability(engine, instance, "holy_nimbus_restore")

    assert resolved["ok"] is False
    assert "no use" in resolved["error"]


def test_dragon_wings_is_restored_with_three_sorcery_points(draconic) -> None:
    engine, instance = _setup(draconic)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "dragon_wings"))

    events = _apply(
        engine, instance, _capability(engine, instance, "dragon_wings_restore"),
    )

    spend = _of_type(events, "dnd2024.class_resource.spent")
    assert [(row["resource_id"], row["amount"]) for row in spend] == [
        ("sorcery_points", 3),
    ]
    _next_turn(engine, instance, "player:gm")
    _apply(engine, instance, _capability(engine, instance, "dragon_wings"))


def test_hurl_is_restored_by_spending_a_pact_slot(fiend14) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    engine, instance = _setup(fiend14)
    _start(engine, instance, enemies=_two_enemies())
    _attack(engine, instance, "item:dagger", dice=[20, 1, 1], hurl_through_hell=True)

    events = _apply(
        engine, instance,
        _capability(engine, instance, "hurl_through_hell_restore"),
    )

    slot = _of_type(events, "dnd2024.spell.slot_spent")
    assert slot[0]["slot_level"] == 5
    assert _of_type(events, "dnd2024.feature_usage.restored")


def test_dragon_wings_can_be_dismissed_without_an_action(draconic) -> None:
    engine, instance = _setup(draconic)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "dragon_wings"))
    assert _fly_speed(instance) == 60

    events = _apply(
        engine, instance, _capability(engine, instance, "dragon_wings_end"),
    )

    assert _of_type(events, "dnd2024.long_effect.ended")
    assert _fly_speed(instance) == 0
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert "dragon_wings" not in (character.get("long_effects") or {})


def test_holy_nimbus_can_be_dismissed_without_an_action(devotion20) -> None:
    from src.engine.modules import ruleset_runtime as _rt

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))

    _apply(engine, instance, _capability(engine, instance, "holy_nimbus_end"))

    combat = _rt.state(instance)["combat"]
    rows = engine._projected_auras(instance, combat)
    assert "holy_nimbus" not in [row["definition"].id for row in rows]


def test_intimidating_presence_is_restored_with_a_rage_use(berserker) -> None:
    engine, instance = _setup(berserker)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(
        engine, instance, "intimidating_presence", [1],
        target_ids=["enemy:goblin-1"],
    ))

    events = _apply(engine, instance, _capability(
        engine, instance, "intimidating_presence_restore",
    ))

    spend = _of_type(events, "dnd2024.class_resource.spent")
    assert [(row["resource_id"], row["amount"]) for row in spend] == [("rages", 1)]
    assert _of_type(events, "dnd2024.feature_usage.restored")


def test_a_long_effect_without_an_expiry_is_rejected(berserker) -> None:
    from src.rulesets.events import EventBatchError
    from tests.rulesets.test_dnd2024_channel_control import _inject

    engine, instance = _setup(berserker)
    _start(engine, instance, enemies=_two_enemies())

    with pytest.raises(EventBatchError):
        _inject(engine, instance, [{
            "type": "dnd2024.long_effect.applied",
            "actor_id": "player:gm",
            "effect_id": "dragon_wings",
            "effect": {"feature_id": "dragon_wings"},
        }])


def test_replaying_an_earlier_expiry_does_not_extend_it(draconic) -> None:
    engine, instance = _setup(draconic)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "dragon_wings"))
    character = instance.get_character_sheet("gm")["ruleset_character"]
    original = character["long_effects"]["dragon_wings"]["expires_at"]

    from tests.rulesets.test_dnd2024_channel_control import _inject

    _inject(engine, instance, [{
        "type": "dnd2024.long_effect.applied",
        "actor_id": "player:gm",
        "effect_id": "dragon_wings",
        "effect": {
            "feature_id": "dragon_wings",
            "expires_at": original - 1000,
            "detail": {"fly_speed": 60},
        },
    }])

    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert character["long_effects"]["dragon_wings"]["expires_at"] == original


def test_the_view_stops_showing_an_expired_effect(draconic) -> None:
    from src.engine.modules import ruleset_runtime as _rt

    engine, instance = _setup(draconic)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "dragon_wings"))

    from src.engine.world_state import clock_from_minutes

    instance.world_state["clock"] = clock_from_minutes(61)
    combat = _rt.state(instance)["combat"]
    view = engine._actor_view(instance, combat, "player:gm")

    assert view["fly_speed"] == 0
    assert "dragon_wings" not in (view.get("long_effects") or {})


def test_restoring_without_a_level_five_slot_is_refused(devotion20) -> None:
    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character["spellcasting"]["class"]["slots_current"]["5"] = 0

    resolved = _capability(engine, instance, "holy_nimbus_restore")

    assert resolved["ok"] is False
    assert "level 5 spell slot" in resolved["error"]


def test_asking_for_hurl_after_the_use_is_gone_fails_loudly(fiend14) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    from tests.rulesets.test_dnd2024_channel_control import _inject

    engine, instance = _setup(fiend14)
    _start(engine, instance, enemies=_two_enemies())
    # 直接把长休账记满：本用例只验证"账满了就明确失败"，不依赖第一次攻击
    # 的掷骰细节（那条路径由别的用例覆盖）。
    _inject(engine, instance, [{
        "type": "dnd2024.feature_usage.changed", "actor_id": "player:gm",
        "owner": "hurl_through_hell", "feature_id": "hurl_through_hell",
        "scope": "long_rest", "before_count": 0, "after_count": 1,
        "use_index": 1,
    }])

    # 走 resolve_intent 而不是 _attack：那个 helper 自带"必须成功"的断言，
    # 本用例要的正是它不许成功。
    from tests.rulesets.test_dnd2024_channel_control import SequenceRng, _intent

    resolved = engine.resolve_intent(instance, _intent(
        instance, actor_id="player:gm", type="attack",
        target_id="enemy:goblin-1", weapon_ref="item:dagger",
        hurl_through_hell=True,
    ), SequenceRng([20, 1]))

    assert resolved["ok"] is False
    assert "no use left" in resolved["error"]


def test_the_incapacitation_lasts_one_round_not_ten(fiend14) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    engine, instance = _setup(fiend14)
    _start(engine, instance, enemies=_two_enemies())

    events, _decisions = _attack(
        engine, instance, "item:dagger", dice=[20, 1, 1], hurl_through_hell=True,
    )

    applied = _of_type(events, "condition.applied")[0]
    assert applied["duration"] == "caster_next_turn_end"
    assert applied["turns_remaining"] == 2


def test_the_quivering_save_goes_through_the_shared_authority(
    open_hand17,
) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _inject

    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())
    _apply_vibration(engine, instance)
    _inject(engine, instance, [{
        "type": "condition.applied", "target_id": "enemy:goblin-1",
        "condition": "incapacitated", "duration": "test",
    }])

    events = _apply(engine, instance, _capability(
        engine, instance, "quivering_palm_end", [20], same_plane=True,
    ))

    check = _of_type(events, "check.resolved")[0]
    # 权威函数才会给 roll_id、光环与掩护字段；手写豁免这三样都没有。
    assert "roll_id" in check
    assert "aura_bonus" in check


def test_the_vibration_lasts_a_number_of_days_equal_to_monk_level(open_hand17) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())
    events, _decisions = _attack(
        engine, instance, "unarmed_strike", dice=[20, 5], quivering_palm=True,
    )

    applied = _of_type(events, "dnd2024.long_effect.applied")[0]
    # 17 级 = 17 天 = 24480 分钟。
    assert applied["effect"]["expires_at"] == 17 * 1440


def test_a_second_vibration_on_another_target_is_not_declared(open_hand17) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _attack

    from tests.rulesets.test_dnd2024_channel_control import _positions

    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())
    _attack(engine, instance, "unarmed_strike", dice=[20, 5], quivering_palm=True)

    _positions(instance, **{"enemy:goblin-2": 5})
    _next_turn(engine, instance, "player:gm")
    events, _decisions = _attack(
        engine, instance, "unarmed_strike", dice=[20, 5],
        target_id="enemy:goblin-2", quivering_palm=True,
    )

    assert not _of_type(events, "dnd2024.long_effect.applied")


def test_ending_without_a_same_plane_assertion_is_refused(open_hand17) -> None:
    engine, instance = _setup(open_hand17)
    _start(engine, instance, enemies=_two_enemies())
    _apply_vibration(engine, instance)

    resolved = _capability(engine, instance, "quivering_palm_end", [1])

    assert resolved["ok"] is False
    assert "same-plane" in resolved["error"]


def test_a_lower_slot_cannot_impersonate_the_level_five_cost(devotion20) -> None:
    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character["spellcasting"]["class"]["slots_current"]["5"] = 0
    character["spellcasting"]["class"]["slots_current"]["4"] = 3

    resolved = _capability(engine, instance, "holy_nimbus_restore")

    assert resolved["ok"] is False
    assert "level 5" in resolved["error"]


def test_friends_inside_the_aura_take_nothing(devotion20) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _hp, _positions

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _positions(instance, **{"player:gm": 10, "enemy:goblin-1": 12})
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))
    before = _hp(instance, "player:gm")

    _to_turn(engine, instance, "player:gm")

    assert _hp(instance, "player:gm") == before


def test_radiant_resistance_halves_the_aura_damage(devotion20) -> None:
    from tests.rulesets.test_dnd2024_channel_control import _hp, _positions

    foe = _enemy()
    foe["resist_damage_types"] = ["radiant"]
    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=[foe])
    _positions(instance, **{"enemy:goblin-1": 10})
    before = _hp(instance, "enemy:goblin-1")

    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))
    _to_turn(engine, instance, "player:gm")
    _to_turn(engine, instance, "enemy:goblin-1")

    character = instance.get_character_sheet("gm")["ruleset_character"]
    full = (int(character["abilities"]["cha"]) - 10) // 2 + int(
        character["derived"]["proficiency_bonus"]
    )
    assert before - _hp(instance, "enemy:goblin-1") == full // 2


def test_no_light_kind_is_declared_for_the_aura(devotion20) -> None:
    from src.engine.modules import ruleset_runtime as _rt

    engine, instance = _setup(devotion20)
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(engine, instance, "holy_nimbus"))
    view = engine._actor_view(instance, _rt.state(instance)["combat"], "player:gm")

    assert "sunlight" not in view
    applied = instance.get_character_sheet("gm")["ruleset_character"]["long_effects"]
    assert "light_kind" not in applied["holy_nimbus"]["detail"]
