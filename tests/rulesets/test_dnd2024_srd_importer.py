"""The SRD beast importer: the four places where prose turns into numbers.

Every case here is a defect the review found in the generated catalog, pinned
so the next regeneration cannot bring it back.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/rulesets/import_srd_beasts.py"


def _importer():
    spec = importlib.util.spec_from_file_location("import_srd_beasts_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMP = _importer()


def test_a_flat_primary_damage_is_not_replaced_by_the_second_part() -> None:
    """"1 Piercing damage plus 3 (1d6) Poison damage" is 1, not 1d6."""

    action = {
        "name": "Sting",
        "desc": "Melee Attack Roll: +2, reach 5 ft. 1 Piercing damage plus "
                "3 (1d6) Poison damage.",
        "attacks": [],
    }

    profile = IMP.parse_prose_attack(action, 0)

    assert profile is not None
    assert profile["damage"] == [
        {"dice": "1", "type": "piercing"},
        {"dice": "1d6", "type": "poison"},
    ]


def test_a_ranged_prose_line_keeps_its_long_range() -> None:
    action = {
        "name": "Enchanting Bow",
        "desc": "Ranged Attack Roll: +6, range 40/160 ft. 1 Piercing damage.",
        "attacks": [],
    }

    profile = IMP.parse_prose_attack(action, 0)

    assert profile is not None
    assert (profile["range"], profile["long_range"]) == (40, 160)


def test_a_thrown_weapons_alternate_range_wins_over_melee_reach() -> None:
    action = {
        "name": "Light Hammer",
        "desc": "Melee or Ranged Attack Roll: +4, reach 10 ft. or range 20/60 ft. "
                "9 (3d4 + 2) Bludgeoning damage.",
        "attacks": [],
    }

    profile = IMP.parse_prose_attack(action, 0)

    assert profile is not None
    assert (profile["range"], profile["long_range"]) == (20, 60)


def test_a_conditional_second_part_is_not_imported() -> None:
    desc = ("Melee Attack Roll: +4, reach 5 ft. 5 (1d6 + 2) Slashing damage, plus "
            "2 (1d4) Slashing damage if the attack roll had Advantage.")

    assert IMP.second_damage_part(desc) is None


def test_usage_limits_come_from_the_structured_field() -> None:
    action = {
        "name": "Fire Breath",
        "desc": "Dexterity Saving Throw: DC 11. Failure: 14 (4d6) Fire damage. "
                "Success: Half damage.",
        "usage_limits": {"type": "RECHARGE_ON_ROLL", "param": 5},
    }

    parsed = IMP.parse_save_action(action)

    assert parsed is not None
    assert parsed["usage_limits"] == {"recharge": {"from": 5, "to": 6}}


def test_a_per_day_action_reads_its_count() -> None:
    action = {
        "name": "Legendary Thing",
        "desc": "Dexterity Saving Throw: DC 13. Failure: 9 (2d8) Fire damage.",
        "usage_limits": {"type": "PER_DAY", "param": 3},
    }

    parsed = IMP.parse_save_action(action)

    assert parsed is not None
    assert parsed["usage_limits"] == {"per_day": 3}


# ---------------------------------------------------------------------------
# 缺口账本：`unmapped` 必须与真实映射同构
# ---------------------------------------------------------------------------

def _record(**overrides) -> dict:
    record = {
        "key": "probe", "name": "Probe", "challenge_rating": 2,
        "actions": [], "traits": [],
    }
    record.update(overrides)
    return record


def test_a_bonus_action_is_a_gap_because_nothing_consumes_it() -> None:
    """`map_actions` 只处理 ACTION；Bonus/Reaction 动作整条丢掉，必须记账。"""

    record = _record(actions=[{
        "name": "Trample", "action_type": "BONUS_ACTION",
        "desc": "Dexterity Saving Throw: DC 16. Failure: 17 (2d10 + 6) Bludgeoning damage.",
    }])

    assert IMP.unmapped(record) == ["Trample (Bonus Action)"]


def test_a_recharge_attack_keeps_its_damage_but_loses_its_limit() -> None:
    """攻击型 Recharge 不被 `map_attacks` 读，限次会没了——这也是一条缺口。"""

    record = _record(actions=[{
        "name": "Rock", "action_type": "ACTION",
        "desc": "Ranged Attack Roll: +9, range 25/50 ft. Hit: 13 (2d6 + 6) Bludgeoning damage.",
        "usage_limits": {"type": "RECHARGE_ON_ROLL", "param": 6},
    }])

    gaps = IMP.unmapped(record)
    assert "Rock (Recharge)" in gaps


def test_a_plain_attack_is_not_a_gap() -> None:
    record = _record(actions=[{
        "name": "Claw", "action_type": "ACTION",
        "desc": "Melee Attack Roll: +5, reach 5 ft. Hit: 7 (1d8 + 3) Slashing damage.",
    }])

    assert IMP.unmapped(record) == []


def test_an_unparsable_multiattack_is_a_gap() -> None:
    """"... in any combination" 没有表达力：整条不解析，算缺口。"""

    record = _record(actions=[
        {"name": "Bite", "action_type": "ACTION",
         "desc": "Melee Attack Roll: +5, reach 5 ft. Hit: 7 (1d8 + 3) Piercing damage."},
        {"name": "Multiattack", "action_type": "ACTION",
         "desc": "The probe makes two Bite attacks in any combination."},
    ])

    assert "Multiattack" in IMP.unmapped(record)


def test_a_rider_that_lasts_until_the_source_turn_ends_keeps_its_anchor() -> None:
    """"until the end of the couatl's next turn" 是"带过期"档里的一个锚点。

    以前这种写法返回 None，整条 rider 被丢掉；它和 `_next_turn_start` 只差边界
    在哪一侧，不是两种机制。
    """

    assert IMP.rider_duration("end", "couatl") == "attacker_next_turn_end"
    assert IMP.rider_duration("start", "couatl") == "attacker_next_turn_start"
    # 没有主语时仍然是"目标自己"的边界。
    assert IMP.rider_duration("end", "") == "target_turn_end"
