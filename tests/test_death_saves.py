"""死亡豁免二期：HP 归零→昏迷+每回合豁免；未声明规则保持即死。"""

from __future__ import annotations

import json
from pathlib import Path

from src.commands.check_planner import _planner_context
from src.commands.death_save_tracker import resolve_round_death_saves
from src.engine.character_utils import (
    apply_death_save,
    enter_downed_state,
    is_conscious,
    record_death_save_failures,
    sync_death_from_hp,
    wake_character,
)
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem

ROOT = Path(__file__).resolve().parents[1]


def _dnd() -> RuleSystem:
    return RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")


def _coc() -> RuleSystem:
    return RuleSystem.load(ROOT / "templates" / "rules" / "freeform_coc.json")


def test_rule_capability_default_dead_and_dnd5e_downed() -> None:
    assert _coc().death_mechanic == {"hp_zero": "dead"}
    assert _dnd().death_mechanic == {"hp_zero": "downed_death_saves"}


def test_hp_zero_enters_downed_instead_of_death_for_dnd5e() -> None:
    cs = {"hp": 0, "max_hp": 10}
    assert sync_death_from_hp(cs, 1, _dnd()) is False
    assert cs["status"] == "downed"
    assert cs["death_saves"] == {"success": 0, "failure": 0}
    assert not cs.get("deceased")
    assert not is_conscious(cs)


def test_hp_zero_legacy_rule_still_instant_death() -> None:
    cs = {"hp": 0, "max_hp": 10}
    assert sync_death_from_hp(cs, 1, _coc()) is True
    assert cs["deceased"] is True


def test_healing_wakes_downed_character() -> None:
    cs = {"hp": 0, "max_hp": 10}
    sync_death_from_hp(cs, 1, _dnd())
    cs["hp"] = 3
    assert wake_character(cs) is True
    assert "status" not in cs and "death_saves" not in cs
    assert is_conscious(cs)


def test_death_save_three_successes_stabilize() -> None:
    cs = {"hp": 0, "max_hp": 10}
    enter_downed_state(cs)
    assert apply_death_save(cs, 15) == "success"
    assert apply_death_save(cs, 12) == "success"
    assert apply_death_save(cs, 10) == "stable"
    assert cs["status"] == "stable"
    assert "death_saves" not in cs
    assert not cs.get("deceased")
    assert not is_conscious(cs)  # 稳定仍昏迷，不能行动


def test_death_save_three_failures_kill() -> None:
    cs = {"hp": 0, "max_hp": 10}
    enter_downed_state(cs)
    apply_death_save(cs, 9)
    apply_death_save(cs, 3)
    assert apply_death_save(cs, 5) == "dead"
    assert cs["deceased"] is True


def test_death_save_nat20_wakes_and_nat1_counts_double() -> None:
    cs = {"hp": 0, "max_hp": 10}
    enter_downed_state(cs)
    assert apply_death_save(cs, 1) == "failure_double"
    assert cs["death_saves"]["failure"] == 2

    cs2 = {"hp": 0, "max_hp": 10}
    enter_downed_state(cs2)
    assert apply_death_save(cs2, 20) == "wake"
    assert cs2["hp"] == 1 and is_conscious(cs2)


def test_round_tracker_rolls_and_appends_text(monkeypatch) -> None:
    rule = _dnd()
    instance = GameInstance(game_key=("web", "ds", "bot"), rule_id="dnd5e")
    instance.players["a"] = {
        "character_name": "尤落",
        "character_sheet": {"hp": 0, "max_hp": 10, "deceased": False},
    }
    sync_death_from_hp(instance.get_character_sheet("a"), 1, rule)
    rolls = iter([17])
    monkeypatch.setattr("random.randint", lambda _a, _b: next(rolls))

    text = resolve_round_death_saves(instance, rule)

    assert "尤落" in text and "17" in text
    cs = instance.get_character_sheet("a")
    assert cs["death_saves"] == {"success": 1, "failure": 0}


def test_round_tracker_skips_when_rule_lacks_mechanic() -> None:
    instance = GameInstance(game_key=("web", "ds2", "bot"), rule_id="freeform_coc")
    instance.players["a"] = {"character_name": "调查员", "character_sheet": {"hp": 0, "max_hp": 10}}
    assert resolve_round_death_saves(instance, _coc()) == ""


def test_stable_character_stays_stable_until_healed_or_hurt() -> None:
    cs = {"hp": 0, "max_hp": 10, "status": "stable"}
    assert sync_death_from_hp(cs, 2, _dnd()) is False
    assert cs["status"] == "stable"
    assert "death_saves" not in cs


def test_damage_to_stable_character_restarts_downed_and_records_failure() -> None:
    cs = {"hp": 0, "max_hp": 10, "status": "stable", "death_saves": {"success": 3, "failure": 1}}
    assert record_death_save_failures(cs, 1) == "recorded"
    assert cs["status"] == "downed"
    assert cs["death_saves"] == {"success": 0, "failure": 1}

    critical = {"hp": 0, "max_hp": 10, "status": "stable", "death_saves": {"success": 3, "failure": 1}}
    assert record_death_save_failures(critical, 2) == "recorded"
    assert critical["death_saves"] == {"success": 0, "failure": 2}


def test_round_tracker_reuses_same_round_outcome_without_reroll(monkeypatch) -> None:
    instance = GameInstance(game_key=("web", "ds-retry", "bot"), rule_id="dnd5e")
    instance.round_number = 4
    instance.players["a"] = {
        "character_name": "尤落",
        "character_sheet": {"hp": 0, "max_hp": 10, "status": "downed", "death_saves": {"success": 0, "failure": 0}},
    }
    calls = 0

    def fixed_roll(_a: int, _b: int) -> int:
        nonlocal calls
        calls += 1
        return 17

    monkeypatch.setattr("random.randint", fixed_roll)
    first = resolve_round_death_saves(instance, _dnd())
    second = resolve_round_death_saves(instance, _dnd())

    assert calls == 1
    assert first == second
    assert instance.get_character_sheet("a")["death_saves"] == {"success": 1, "failure": 0}


def test_round_tracker_caches_each_downed_player_independently(monkeypatch) -> None:
    instance = GameInstance(game_key=("web", "ds-party", "bot"), rule_id="dnd5e")
    instance.round_number = 4
    for uid in ("a", "b"):
        instance.players[uid] = {
            "character_name": uid,
            "character_sheet": {"hp": 0, "max_hp": 10, "status": "downed", "death_saves": {"success": 0, "failure": 0}},
        }
    rolls = iter([12, 3])
    monkeypatch.setattr("random.randint", lambda _a, _b: next(rolls))

    resolve_round_death_saves(instance, _dnd())

    outcomes = instance.death_save_outcomes["4"]
    assert outcomes["a"]["roll"] == 12
    assert outcomes["b"]["roll"] == 3
    assert instance.get_character_sheet("a")["death_saves"] == {"success": 1, "failure": 0}
    assert instance.get_character_sheet("b")["death_saves"] == {"success": 0, "failure": 1}


def test_planner_context_excludes_downed_players() -> None:
    instance = GameInstance(game_key=("web", "ds3", "bot"), rule_id="dnd5e")
    instance.players["a"] = {
        "character_name": "尤落",
        "character_sheet": {"attributes": {"str": 12}, "hp": 10, "max_hp": 10, "deceased": False},
    }
    instance.players["b"] = {
        "character_name": "星墨",
        "character_sheet": {"attributes": {"str": 10}, "hp": 0, "max_hp": 10, "status": "downed"},
    }
    instance.action_queue = [
        {"user_id": "a", "text": "我撬门"},
        {"user_id": "b", "text": "我也想动"},
    ]

    payload = json.loads(_planner_context(instance, _dnd()))

    assert {p["player_id"] for p in payload["players"]} == {"a"}


def test_damage_while_downed_accumulates_failures() -> None:
    cs = {"hp": 0, "max_hp": 10, "status": "downed", "death_saves": {"success": 1, "failure": 0}}
    assert record_death_save_failures(cs, 1) == "recorded"
    assert cs["death_saves"]["failure"] == 1
    # 非昏迷角色不受影响
    conscious = {"hp": 5, "max_hp": 10}
    assert record_death_save_failures(conscious, 1) is None


def test_damage_while_downed_critical_counts_double_and_can_kill() -> None:
    cs = {"hp": 0, "max_hp": 10, "status": "downed", "death_saves": {"success": 0, "failure": 1}}
    assert record_death_save_failures(cs, 2) == "dead"
    assert cs["deceased"] is True


def _combat_instance_with_downed_target() -> GameInstance:
    instance = GameInstance(game_key=("web", "ds4", "bot"), rule_id="dnd5e")
    instance.players["a"] = {
        "character_name": "袭击者",
        "character_sheet": {
            "attributes": {"str": 12},
            "hp": 10,
            "max_hp": 10,
            "equipment": [{"name": "长剑", "type": "weapon", "damage": 7, "damage_dice": "1d8", "slot": "main_hand"}],
        },
    }
    instance.players["b"] = {
        "character_name": "尤落",
        "character_sheet": {
            "attributes": {"dex": 10},
            "hp": 0,
            "max_hp": 10,
            "status": "downed",
            "death_saves": {"success": 0, "failure": 0},
        },
    }
    return instance


def test_combat_hit_on_downed_player_accumulates_failure(monkeypatch) -> None:
    from src.commands.combat_resolver import CombatResolver

    instance = _combat_instance_with_downed_target()
    request = {"check_id": "atk1", "actor_uid": "a", "kind": "attack", "opponent": "b", "dice_system": "d20", "attribute": "str", "target": 10}
    instance.action_queue = [{"user_id": "a", "text": "我补刀尤落。", "check_request": request}]
    instance.last_checks = [{
        "check_id": "atk1", "actor_uid": "a", "kind": "attack", "opponent": "b",
        "dice": "d20", "roll": 18, "total": 19, "verdict": "成功",
        "is_critical": False, "is_fumble": False,
    }]
    monkeypatch.setattr("random.randint", lambda _a, _b: 6)

    CombatResolver().resolve_combat(instance, "ignored", "hp_based", _dnd())

    cs = instance.get_character_sheet("b")
    assert cs["death_saves"]["failure"] == 1
    assert not cs.get("deceased")


def test_combat_hit_with_zero_actual_damage_does_not_accumulate_failure(monkeypatch) -> None:
    from src.commands.combat_resolver import CombatResolver

    instance = _combat_instance_with_downed_target()
    instance.players["a"]["character_sheet"]["attributes"]["str"] = 8
    instance.players["a"]["character_sheet"]["equipment"] = [
        {"name": "空手", "type": "weapon", "damage": 0, "slot": "main_hand"}
    ]
    request = {"check_id": "atk-zero", "actor_uid": "a", "kind": "attack", "opponent": "b", "dice_system": "d20", "attribute": "str", "target": 10}
    instance.action_queue = [{"user_id": "a", "text": "我攻击尤落。", "check_request": request}]
    instance.last_checks = [{
        "check_id": "atk-zero", "actor_uid": "a", "kind": "attack", "opponent": "b",
        "dice": "d20", "roll": 18, "total": 19, "verdict": "成功",
        "is_critical": False, "is_fumble": False,
    }]

    CombatResolver().resolve_combat(instance, "ignored", "hp_based", _dnd())

    assert instance.get_character_sheet("b")["death_saves"] == {"success": 0, "failure": 0}


def test_combat_critical_on_downed_player_counts_double(monkeypatch) -> None:
    from src.commands.combat_resolver import CombatResolver

    instance = _combat_instance_with_downed_target()
    instance.players["b"]["character_sheet"]["death_saves"]["failure"] = 1
    request = {"check_id": "atk2", "actor_uid": "a", "kind": "attack", "opponent": "b", "dice_system": "d20", "attribute": "str", "target": 10}
    instance.action_queue = [{"user_id": "a", "text": "我补刀尤落。", "check_request": request}]
    instance.last_checks = [{
        "check_id": "atk2", "actor_uid": "a", "kind": "attack", "opponent": "b",
        "dice": "d20", "roll": 20, "total": 21, "verdict": "成功",
        "is_critical": True, "is_fumble": False,
    }]
    monkeypatch.setattr("random.randint", lambda _a, _b: 6)

    CombatResolver().resolve_combat(instance, "ignored", "hp_based", _dnd())

    cs = instance.get_character_sheet("b")
    assert cs["deceased"] is True
