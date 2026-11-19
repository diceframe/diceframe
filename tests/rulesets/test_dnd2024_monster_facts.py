"""S0-MM1: the monster facts an enemy stat block is made of.

The enemy contract used to carry roughly half a stat block and silently drop
the rest: a GM or an importer could submit ``size``, ``cr``, ``traits`` and get
a fight with none of them -- no error, nothing in the log.  Two different
questions are tested here:

* what the contract accepts (size, structured speeds, three defence lists,
  condition immunities, senses, challenge rating) and what it refuses
  (unknown fields, impossible combinations);
* whether the facts that *have* an authority today are actually consumed --
  the resistance boundary and Blindsight in the observation chain.

``size``, non-walk speeds and Darkvision are stored but not consumed yet: F17
and the 2D map are their consumers.  They are asserted as facts only, and the
execution report lists them by name as "stored, not used".
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.check_modes import observer_senses
from src.rulesets.dnd2024.combat.primitives import size_at_most
from src.rulesets.dnd2024.combat.validation import validate_enemy_profiles
from src.rulesets.dnd2024.director.temporary_encounter import (
    normalize_temporary_encounter,
    validate_final_temporary_enemies,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from test_dnd2024_channel_control import _cleric_sheet, _enemy, _setup, _start

LOCALE = "en"


def _state(instance):
    return ruleset_runtime.state(instance)


def _profile(**extra) -> dict:
    profile = _enemy()
    profile.update(extra)
    return profile


def _sheet() -> dict:
    return _cleric_sheet(Dnd2024Runtime().load_bundle(LOCALE), 9)


def _combat_with(**extra):
    engine, instance = _setup(_sheet())
    _start(engine, instance, enemies=[_profile(**extra)])
    return engine, instance


def _view(engine, instance, actor_id: str = "enemy:goblin-1") -> dict:
    return engine._actor_view(instance, _state(instance)["combat"], actor_id)


# ---------------------------------------------------------------------------
# 1-2. size: closed set, and unknown never means "medium"
# ---------------------------------------------------------------------------

def test_size_is_a_closed_set() -> None:
    for size in ("tiny", "small", "medium", "large", "huge", "gargantuan"):
        validate_enemy_profiles([_profile(size=size)])
    for bad in ("big", "", "Large", "SMALL", None, 3):
        with pytest.raises(Exception):
            validate_enemy_profiles([_profile(size=bad)])


def test_a_missing_size_is_unknown_and_never_medium() -> None:
    """旧数据没有这一栏：装载成功，但比较一律失败（fail closed）。

    "不知道多大"不等于"够小"。Trip 只对 Large 或更小生效；若缺省按 medium
    处理，一个 Gargantuan 巨人的旧存档会突然吃上 Trip。
    """

    validate_enemy_profiles([_enemy()])
    assert size_at_most("", "large") is False
    assert size_at_most("medium", "large") is True
    assert size_at_most("large", "large") is True
    assert size_at_most("huge", "large") is False
    assert size_at_most("gargantuan", "medium") is False
    assert size_at_most("tiny", "tiny") is True


# ---------------------------------------------------------------------------
# 3. speeds: the old scalar still means walk
# ---------------------------------------------------------------------------

def test_speeds_accepts_the_old_scalar_and_the_structured_form() -> None:
    validate_enemy_profiles([_profile(speed=30)])
    validate_enemy_profiles([
        _profile(speed=None, speeds={"walk": 30, "fly": 60, "hover": True}),
    ])
    for bad in (
        {"walk": 0},
        {"walk": -5},
        {"walk": 30, "hover": True},
        {"walk": 30, "unknown": 5},
        {"walk": "30"},
    ):
        with pytest.raises(Exception):
            validate_enemy_profiles([_profile(speeds=bad)])


# ---------------------------------------------------------------------------
# 4. defences: closed set, mutually exclusive
# ---------------------------------------------------------------------------

def test_defence_lists_are_closed_and_mutually_exclusive() -> None:
    validate_enemy_profiles([_profile(
        resist_damage_types=["fire"], immune_damage_types=["poison"],
        vulnerable_damage_types=["cold"],
    )])
    with pytest.raises(Exception):
        validate_enemy_profiles([_profile(resist_damage_types=["sunlight"])])
    with pytest.raises(Exception):
        validate_enemy_profiles([_profile(resist_damage_types=["fire", "fire"])])
    for left, right in (
        ("resist_damage_types", "immune_damage_types"),
        ("resist_damage_types", "vulnerable_damage_types"),
        ("immune_damage_types", "vulnerable_damage_types"),
    ):
        with pytest.raises(Exception):
            validate_enemy_profiles([_profile(**{left: ["fire"], right: ["fire"]})])


# ---------------------------------------------------------------------------
# 5. the resistance boundary reads the stat block, first and once
# ---------------------------------------------------------------------------

def test_statblock_defences_are_the_first_boundary() -> None:
    engine, instance = _combat_with(
        immune_damage_types=["fire"], vulnerable_damage_types=["cold"],
        resist_damage_types=["slashing"],
    )
    enemy = _view(engine, instance)

    assert engine._resist_damage(enemy, 20, "fire") == (0, "statblock:immune")
    assert engine._resist_damage(enemy, 20, "cold") == (40, "statblock:vulnerable")
    assert engine._resist_damage(enemy, 21, "slashing") == (10, "statblock:resist")
    assert engine._resist_damage(enemy, 20, "radiant") == (20, "")


# ---------------------------------------------------------------------------
# 6-7. senses and condition immunities reach the actor view
# ---------------------------------------------------------------------------

def test_statblock_blindsight_answers_at_its_own_range() -> None:
    engine, instance = _combat_with(senses={"blindsight": 30})
    enemy = _view(engine, instance)
    assert {"code": "blindsight", "range_ft": 30, "source": "statblock"} in (
        observer_senses(engine, enemy, distance=30)
    )
    assert observer_senses(engine, enemy, distance=31) == []

    engine, instance = _combat_with()
    plain = _view(engine, instance)
    assert observer_senses(engine, plain, distance=5) == []


def test_condition_immunities_block_the_application_and_say_so() -> None:
    """免疫不是"悄悄没生效"：`condition.applied` 换成可审计的 blocked。

    Poisoned 对该目标不落状态；事件里仍能看出"有人试图施加、被免疫挡下"。
    """

    engine, instance = _combat_with(condition_immunities=["poisoned"])
    combat = _state(instance)["combat"]
    assert _view(engine, instance)["condition_immunities"] == ["poisoned"]

    events = engine._statblock_immunity_events(combat, [
        {"type": "condition.applied", "target_id": "enemy:goblin-1",
         "condition": "poisoned", "source_actor_id": "player:gm"},
        {"type": "condition.applied", "target_id": "enemy:goblin-1",
         "condition": "prone", "source_actor_id": "player:gm"},
    ])
    assert [event["type"] for event in events] == ["condition.blocked", "condition.applied"]
    assert events[0] == {
        "type": "condition.blocked", "target_id": "enemy:goblin-1",
        "condition": "poisoned", "reason": "immune", "source_actor_id": "player:gm",
    }
    # 没有免疫的目标完全不改道（包括玩家，他们不吃 stat block 免疫力）。
    plain, plain_instance = _combat_with()
    passthrough = plain._statblock_immunity_events(_state(plain_instance)["combat"], [
        {"type": "condition.applied", "target_id": "enemy:goblin-1",
         "condition": "poisoned"},
    ])
    assert passthrough[0]["type"] == "condition.applied"


# ---------------------------------------------------------------------------
# 8-9. challenge rating and unknown fields
# ---------------------------------------------------------------------------

def test_challenge_rating_is_a_closed_set_and_xp_needs_it() -> None:
    for cr in ("0", "1/8", "1/4", "1/2", "1", "5", "30"):
        validate_enemy_profiles([_profile(cr=cr)])
    validate_enemy_profiles([_profile(cr=3)])
    for bad in ("2.5", "31", "", "CR 3"):
        with pytest.raises(Exception):
            validate_enemy_profiles([_profile(cr=bad)])
    with pytest.raises(Exception):
        validate_enemy_profiles([_profile(xp=50)])
    validate_enemy_profiles([_profile(cr="1/4", xp=50)])


def test_unknown_fields_fail_closed_and_are_all_named() -> None:
    with pytest.raises(Exception) as caught:
        validate_enemy_profiles([_profile(sizes="small", traits=[], cr_xp=1)])
    message = str(caught.value)
    assert "sizes" in message and "traits" in message and "cr_xp" in message


# ---------------------------------------------------------------------------
# 10-11. the shipped samples and the AI path
# ---------------------------------------------------------------------------

def test_the_shipped_samples_carry_their_new_facts() -> None:
    catalog = Dnd2024Runtime().load_bundle(LOCALE).get(
        "encounter_catalog", "srd_training_encounters",
    )
    enemies = [
        enemy for preset in catalog["presets"] for enemy in preset["enemies"]
    ]
    validate_enemy_profiles(deepcopy(enemies))
    by_id = {enemy["id"]: enemy for enemy in enemies}
    assert by_id["goblin-warrior-1"]["size"] == "small"
    assert by_id["goblin-warrior-1"]["cr"] == "1/4"
    assert by_id["wolf-1"]["size"] == "medium"


def test_the_ai_draft_path_keeps_the_statblock_facts() -> None:
    payload = {"title": "Ogre", "enemies": [{
        "name": "Ogre", "hp": 59, "armor_class": 11, "size": "large", "cr": "2",
        "attacks": [{
            "attack_bonus": 6, "damage": "2d8+4",
            "damage_type": "bludgeoning", "range": 5,
        }],
    }]}
    proposal = normalize_temporary_encounter(payload)
    enemy = proposal["enemies"][0]
    assert enemy["size"] == "large" and enemy["cr"] == "2"
    # 同一份边界：形状不合法的 CR 在这里就被拒，不会等到开战。
    broken = deepcopy(payload)
    broken["enemies"][0]["cr"] = "2.5"
    with pytest.raises(Exception):
        normalize_temporary_encounter(broken)


def test_the_ai_confirm_path_keeps_the_facts_and_drops_the_noise() -> None:
    """确认入口重建白名单档案：stat block 事实保留，模型噪声丢弃。

    这里与 combat.start 的分工不同——前者对未知字段**拒绝**，后者对模型输出
    **丢弃**（模型总会多写几个键），两者都过同一份 validate_enemy_profiles。
    """

    instance = _setup(_sheet())[1]
    validate_final_temporary_enemies(instance, [_profile(size="medium", cr="1/4")])

    noisy = _profile(size="large", cr="2", traits=["nimble"], xp=450)
    validate_final_temporary_enemies(instance, [noisy])  # 不因噪声整场拒绝

    # 这一层只挡"数值失控"，形状由 combat.start 的权威校验兜底——所以坏 CR
    # 在这里静默通过、在真正开战时被拒，两处都不能少。
    validate_final_temporary_enemies(instance, [_profile(cr="2.5")])
    with pytest.raises(Exception):
        validate_enemy_profiles([_profile(cr="2.5")])


# ---------------------------------------------------------------------------
# 12. an old save keeps fighting
# ---------------------------------------------------------------------------

def test_a_legacy_enemy_without_the_new_fields_still_fights() -> None:
    engine, instance = _setup(_sheet())
    _start(engine, instance, enemies=[{
        "id": "old-1", "name": "Old Goblin", "hp": 10, "armor_class": 13,
        "speed": 30, "position": 5, "creature_type": "humanoid",
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "attacks": [{
            "id": "scimitar", "attack_bonus": 4, "damage": "1d6+2",
            "damage_type": "slashing", "range": 5,
        }],
    }])
    view = _view(engine, instance, "enemy:old-1")
    assert view["size"] == ""
    assert view["speeds"] == {"walk": 30}
    assert view["senses"] == {}
    assert view["cr"] == "" and view["xp"] == 0
