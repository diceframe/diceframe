"""攻击 CheckResult 唯一权威不变量测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.commands.check_planner import normalize_check_specs
from src.commands.combat_resolver import CombatResolver
from src.commands.round_processor import RoundProcessor
from src.engine.checks import (
    build_check_request,
    is_explicit_attack_action,
    resolve_check_request,
    roll_check_request,
)
from src.engine.combat import calculate_attack_damage, resolve_attack
from src.engine.game_instance import GameInstance, GameState
from src.rules.rule_system import RuleSystem


ROOT = Path(__file__).resolve().parents[1]


def _player(name: str, *, hp: int = 24, weapon_damage: int = 8, skills=None) -> dict:
    return {
        "character_name": name,
        "character_sheet": {
            "hp": hp,
            "max_hp": hp,
            "faction": "party",
            "attributes": {"str": 10, "dex": 12, "int": 10},
            "skills": list(skills or []),
            "equipment": [{"name": "长剑", "slot": "main_hand", "damage": weapon_damage}],
        },
    }


def _npc(name: str, *, hp: int = 30, armor: int = 0) -> dict:
    return {
        "name": name,
        "character_name": name,
        "hp": hp,
        "max_hp": hp,
        "armor": armor,
        "attributes": {"dex": 10},
    }


def _request(check_id: str, actor_uid: str, opponent: str, *, dice: str = "d20") -> dict:
    return {
        "check_id": check_id,
        "actor_uid": actor_uid,
        "actor_name": actor_uid,
        "dice_system": dice,
        "kind": "attack",
        "opponent": opponent,
        "attribute": "str",
        "target": 10,
        "advantage_mode": "",
    }


def _result(
    check_id: str,
    actor_uid: str,
    opponent: str,
    *,
    verdict: str = "成功",
    roll: int = 10,
    dice: str = "d20",
    critical: bool = False,
    fumble: bool = False,
) -> dict:
    return {
        "check_id": check_id,
        "actor_uid": actor_uid,
        "actor_name": actor_uid,
        "kind": "attack",
        "opponent": opponent,
        "dice": dice,
        "roll": roll,
        "rolls": [roll],
        "total": roll,
        "verdict": verdict,
        "is_critical": critical,
        "is_fumble": fumble,
    }


def _single_attack_instance() -> tuple[GameInstance, dict]:
    instance = GameInstance(game_key=("test", "combat-authority", "bot"), rule_id="dnd5e")
    instance.players = {"a": _player("甲")}
    instance.npcs = {"goblin": _npc("哥布林")}
    request = _request("attack-a", "a", "npc:goblin")
    instance.action_queue = [{
        "user_id": "a",
        "text": "我用长剑攻击哥布林。",
        "check_request": request,
    }]
    return instance, request


def test_deterministic_planner_marks_explicit_attack_and_binds_named_target() -> None:
    instance = GameInstance(game_key=("test", "offline-attack-plan", "bot"), rule_id="freeform_coc")
    instance.players = {
        "investigator": _player(
            "调查员",
            skills=[{"name": "手枪", "value": 75}],
        )
    }
    instance.npcs = {"cultist": _npc("教徒")}
    action = {
        "user_id": "investigator",
        "text": "我用手枪射击教徒。",
        "selected_skill": "手枪",
    }
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_coc.json")

    request = build_check_request(instance, action, rule)

    assert request is not None
    assert request["kind"] == "attack"
    assert request["opponent"] == "npc:cultist"


def test_ai_planner_can_bind_combat_enemy_reference() -> None:
    instance = GameInstance(game_key=("test", "enemy-attack-plan", "bot"), rule_id="dnd5e")
    instance.players = {"a": _player("甲")}
    instance.combat_enemies = [_npc("Goblin")]
    instance.action_queue = [{"user_id": "a", "text": "I attack Goblin."}]
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")

    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "a",
        "attribute": "str",
        "target": 12,
        "kind": "attack",
        "opponent": "Goblin",
    }])

    assert errors == []
    assert planned[0][1]["opponent"] == "enemy:0"


def test_attack_pipeline_rolls_exactly_once_and_combat_never_rerolls(monkeypatch) -> None:
    instance, request = _single_attack_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")
    calls = 0

    def counted_randint(_low: int, _high: int) -> int:
        nonlocal calls
        calls += 1
        return 12

    monkeypatch.setattr("src.engine.dice_rng.random.randint", counted_randint)
    rolled = roll_check_request(request, rule)
    action = instance.action_queue[0]
    action["dice_value"] = rolled["value"]
    action["dice_rolls"] = rolled["rolls"]
    check = resolve_check_request(instance, action, rule)

    assert check is not None
    assert calls == 1  # attack opponent 只是目标引用，不额外掷对手骰
    instance.last_checks = [check]
    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    assert calls == 1
    assert instance.npcs["goblin"]["hp"] < 30


def test_dnd_attack_critical_thresholds_do_not_leak_into_ordinary_checks() -> None:
    instance = GameInstance(game_key=("test", "dnd-attack-critical", "bot"), rule_id="dnd5e")
    instance.players = {"a": _player("甲")}
    instance.players["a"]["character_sheet"]["attributes"]["str"] = 3
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")
    base_request = {
        "check_id": "critical-scope",
        "actor_uid": "a",
        "dice_system": "d20",
        "attribute": "str",
        "target": 20,
    }

    ordinary = resolve_check_request(instance, {
        "user_id": "a",
        "text": "我尝试推开坚固石门。",
        "check_request": {**base_request, "kind": "check"},
        "dice_value": 20,
        "dice_rolls": [20],
    }, rule)
    attack = resolve_check_request(instance, {
        "user_id": "a",
        "text": "我攻击敌人。",
        "check_request": {**base_request, "kind": "attack"},
        "dice_value": 20,
        "dice_rolls": [20],
    }, rule)

    assert ordinary is not None and ordinary["verdict"] == "失败"
    assert ordinary["is_critical"] is False
    assert attack is not None and attack["verdict"] == "大成功"
    assert attack["is_critical"] is True


@pytest.mark.parametrize(
    ("verdict", "fumble"),
    [("失败", False), ("大失败", True)],
)
def test_failed_or_fumbled_authoritative_attack_never_deals_damage(
    verdict: str,
    fumble: bool,
) -> None:
    instance, _ = _single_attack_instance()
    instance.last_checks = [
        _result("attack-a", "a", "npc:goblin", verdict=verdict, roll=1 if fumble else 4, fumble=fumble)
    ]

    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    assert instance.npcs["goblin"]["hp"] == 30
    assert instance.pending_combat_results[0]["damage"] == 0


def test_coc_attack_uses_real_character_skill_threshold_not_fixed_fifty() -> None:
    instance = GameInstance(
        game_key=("test", "coc-combat-authority", "bot"),
        rule_id="freeform_coc",
    )
    instance.players = {
        "investigator": _player(
            "调查员",
            weapon_damage=8,
            skills=[{"name": "手枪", "value": 75}],
        )
    }
    instance.npcs = {"cultist": _npc("教徒", hp=20)}
    request = _request("coc-shot", "investigator", "npc:cultist", dice="d100")
    request["skill"] = "手枪"
    action = {
        "user_id": "investigator",
        "text": "我用手枪射击教徒。",
        "selected_skill": "手枪",
        "check_request": request,
        "dice_value": 70,
        "dice_rolls": [70],
    }
    instance.action_queue = [action]
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_coc.json")
    check = resolve_check_request(instance, action, rule)

    assert check is not None
    assert check["threshold"] == 75
    assert check["verdict"] == "普通成功"  # 70 会被旧固定 50 误判为失败
    instance.last_checks = [check]
    CombatResolver().resolve_combat(instance, "ignored", "lethal_narrative")

    assert instance.npcs["cultist"]["hp"] < 20
    assert instance.pending_combat_results[0]["check_id"] == "coc-shot"


def test_critical_changes_damage_without_any_hit_rng(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.engine.dice_rng.random.randint",
        lambda _low, _high: pytest.fail("战斗伤害层不应调用命中 RNG"),
    )
    normal_target = _npc("目标甲", hp=40)
    critical_target = _npc("目标乙", hp=40)

    normal = resolve_attack(
        "战士",
        normal_target,
        {"name": "长剑", "damage": 8},
        check_result=_result("normal", "a", "npc:a", roll=10),
    )
    critical = resolve_attack(
        "战士",
        critical_target,
        {"name": "长剑", "damage": 8},
        check_result=_result(
            "critical",
            "a",
            "npc:b",
            verdict="大成功",
            roll=20,
            critical=True,
        ),
    )

    assert critical.damage == normal.damage * 2


def test_multiplayer_different_targets_and_check_ids_never_cross() -> None:
    instance = GameInstance(game_key=("test", "multi-target", "bot"), rule_id="dnd5e")
    instance.players = {
        "a": _player("甲"),
        "b": _player("乙"),
        "c": _player("丙"),
    }
    instance.npcs = {
        "goblin": _npc("Goblin"),
        "orc": _npc("Orc"),
        "skeleton": _npc("Skeleton"),
    }
    targets = {
        "a": ("check-a", "npc:goblin", "Goblin"),
        "b": ("check-b", "npc:orc", "Orc"),
        "c": ("check-c", "npc:skeleton", "Skeleton"),
    }
    instance.action_queue = [
        {
            "user_id": uid,
            "text": f"{uid} attacks {target_name}",
            "check_request": _request(check_id, uid, target_ref),
        }
        for uid, (check_id, target_ref, target_name) in targets.items()
    ]
    # 故意打乱 CheckResult 顺序，确保不是按位置或 last_check 匹配。
    instance.last_checks = [
        _result("check-c", "c", "npc:skeleton", roll=13),
        _result("check-a", "a", "npc:goblin", roll=11),
        _result("check-b", "b", "npc:orc", roll=12),
    ]

    text = CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    records = {item["attacker_uid"]: item for item in instance.pending_combat_results}
    assert {uid: records[uid]["check_id"] for uid in records} == {
        "a": "check-a",
        "b": "check-b",
        "c": "check-c",
    }
    assert {uid: records[uid]["target"] for uid in records} == {
        "a": "Goblin",
        "b": "Orc",
        "c": "Skeleton",
    }
    assert "甲持长剑攻击Goblin" in text
    assert "乙持长剑攻击Orc" in text
    assert "丙持长剑攻击Skeleton" in text


def test_actor_cannot_consume_another_players_check_id() -> None:
    instance = GameInstance(game_key=("test", "crossed-check", "bot"), rule_id="dnd5e")
    instance.players = {"a": _player("甲"), "b": _player("乙")}
    instance.npcs = {"goblin": _npc("Goblin")}
    instance.action_queue = [{
        "user_id": "a",
        "text": "甲 attacks Goblin",
        "check_request": _request("check-b", "a", "npc:goblin"),
    }]
    instance.last_checks = [_result("check-b", "b", "npc:goblin")]

    assert CombatResolver().resolve_combat(instance, "ignored", "hp_based") == ""
    assert instance.npcs["goblin"]["hp"] == 30
    assert instance.pending_combat_results == []


def test_friendly_fire_reduction_is_applied_before_real_hp_mutation() -> None:
    instance = GameInstance(game_key=("test", "friendly-fire", "bot"), rule_id="dnd5e")
    instance.players = {
        "a": _player("甲", weapon_damage=10),
        "b": _player("乙", hp=30),
    }
    instance.action_queue = [{
        "user_id": "a",
        "text": "甲用长剑攻击乙。",
        "check_request": _request("friendly", "a", "b"),
    }]
    instance.last_checks = [_result("friendly", "a", "b", roll=10)]

    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    record = instance.pending_combat_results[0]
    target_hp = instance.get_character_sheet("b")["hp"]
    assert record["damage"] == 5
    assert record["target_hp_before"] - record["target_hp_after"] == record["damage"]
    assert 30 - target_hp == record["damage"]


def test_repeated_resolution_reuses_outcome_without_rng_or_second_hp_mutation(monkeypatch) -> None:
    instance, _ = _single_attack_instance()
    check = _result("attack-a", "a", "npc:goblin", roll=10)
    instance.last_checks = [check]
    monkeypatch.setattr(
        "src.engine.dice_rng.random.randint",
        lambda _low, _high: pytest.fail("重试不应产生新命中骰"),
    )
    resolver = CombatResolver()

    first_text = resolver.resolve_combat(instance, "ignored", "hp_based")
    hp_after_first = instance.npcs["goblin"]["hp"]
    second_text = resolver.resolve_combat(instance, "ignored", "hp_based")

    assert first_text == second_text
    assert instance.npcs["goblin"]["hp"] == hp_after_first
    assert len(instance.pending_combat_results) == 1
    assert instance.action_queue[0]["combat_outcome"]["target_hp_after"] == hp_after_first
    assert "combat_outcome" not in check


def test_dnd_attack_dc_comes_from_server_target_armor_class() -> None:
    instance = GameInstance(game_key=("test", "trusted-ac", "bot"), rule_id="dnd5e")
    instance.players = {"a": _player("甲")}
    instance.npcs = {
        "low": _npc("Low AC", armor=0),
        "high": _npc("High AC", armor=4),
    }
    instance.npcs["high"]["attributes"]["dex"] = 18
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")

    def resolve(target: str) -> dict:
        action = {
            "user_id": "a",
            "text": f"I attack {target}.",
            "check_request": {
                **_request(f"attack-{target}", "a", f"npc:{target}"),
                "target": 1,
            },
            "dice_value": 14,
            "dice_rolls": [14],
        }
        result = resolve_check_request(instance, action, rule)
        assert result is not None
        return result

    low = resolve("low")
    high = resolve("high")

    assert low["dc"] == 10
    assert low["verdict"] == "成功"
    assert high["dc"] == 18
    assert high["verdict"] == "失败"
    assert low["target_source"] == high["target_source"] == "server_armor_class"


def test_dnd_natural_one_is_attack_fumble_but_not_ordinary_auto_failure() -> None:
    instance = GameInstance(game_key=("test", "dnd-natural-one", "bot"), rule_id="dnd5e")
    instance.players = {"a": _player("甲")}
    instance.players["a"]["character_sheet"]["attributes"]["str"] = 20
    instance.npcs = {"goblin": _npc("Goblin")}
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")
    base = {
        "check_id": "natural-one",
        "actor_uid": "a",
        "dice_system": "d20",
        "attribute": "str",
        "target": 5,
    }

    ordinary = resolve_check_request(instance, {
        "user_id": "a",
        "text": "I push the loose door.",
        "check_request": {**base, "kind": "check"},
        "dice_value": 1,
        "dice_rolls": [1],
    }, rule)
    attack = resolve_check_request(instance, {
        "user_id": "a",
        "text": "I attack Goblin.",
        "check_request": {**base, "kind": "attack", "opponent": "npc:goblin"},
        "dice_value": 1,
        "dice_rolls": [1],
    }, rule)

    assert ordinary is not None and ordinary["verdict"] == "成功"
    assert ordinary["is_fumble"] is False
    assert attack is not None and attack["verdict"] == "大失败"
    assert attack["is_fumble"] is True


def test_attack_advantage_uses_two_confirmed_rolls_without_a_third(monkeypatch) -> None:
    instance, request = _single_attack_instance()
    request["advantage_mode"] = "advantage"
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")
    values = iter([4, 17])
    calls = 0

    def counted_randint(_low: int, _high: int) -> int:
        nonlocal calls
        calls += 1
        return next(values)

    monkeypatch.setattr("src.engine.dice_rng.random.randint", counted_randint)
    rolled = roll_check_request(request, rule)
    action = instance.action_queue[0]
    action["dice_value"] = rolled["value"]
    action["dice_rolls"] = rolled["rolls"]
    check = resolve_check_request(instance, action, rule)
    assert check is not None
    assert calls == 2
    assert check["rolls"] == [4, 17]
    assert check["roll"] == 17

    monkeypatch.setattr(
        "src.engine.dice_rng.random.randint",
        lambda *_args: pytest.fail("CombatResolver 不得重新进行命中检定"),
    )
    instance.last_checks = [check]
    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    assert instance.npcs["goblin"]["hp"] < 30


def test_coc_attack_thresholds_remain_character_owned_for_multiple_players() -> None:
    instance = GameInstance(game_key=("test", "coc-thresholds", "bot"), rule_id="freeform_coc")
    instance.players = {
        "a": _player("A", skills=[{"name": "手枪", "value": 75}]),
        "b": _player("B", skills=[{"name": "手枪", "value": 30}]),
    }
    instance.npcs = {"cultist-a": _npc("Cultist A"), "cultist-b": _npc("Cultist B")}
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_coc.json")
    checks = []
    for uid, target in (("a", "cultist-a"), ("b", "cultist-b")):
        request = {**_request(f"shot-{uid}", uid, f"npc:{target}", dice="d100"), "skill": "手枪"}
        action = {
            "user_id": uid,
            "text": f"{uid} 用手枪射击 {target}",
            "selected_skill": "手枪",
            "check_request": request,
            "dice_value": 60,
            "dice_rolls": [60],
        }
        instance.action_queue.append(action)
        check = resolve_check_request(instance, action, rule)
        assert check is not None
        checks.append(check)
    instance.last_checks = checks

    CombatResolver().resolve_combat(instance, "ignored", "lethal_narrative")

    by_actor = {item["attacker_uid"]: item for item in instance.pending_combat_results}
    assert checks[0]["threshold"] == 75 and checks[0]["verdict"] == "普通成功"
    assert checks[1]["threshold"] == 30 and checks[1]["verdict"] == "失败"
    assert by_actor["a"]["damage"] > 0
    assert by_actor["b"]["damage"] == 0


def test_coc_attack_ninety_six_uses_the_shared_fumble_threshold_rule() -> None:
    instance = GameInstance(game_key=("test", "coc-fumble", "bot"), rule_id="freeform_coc")
    instance.players = {
        "low": _player("Low", skills=[{"name": "手枪", "value": 40}]),
        "high": _player("High", skills=[{"name": "手枪", "value": 80}]),
    }
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_coc.json")
    verdicts = {}
    for uid in ("low", "high"):
        action = {
            "user_id": uid,
            "text": "用手枪射击目标",
            "selected_skill": "手枪",
            "check_request": {**_request(f"shot-{uid}", uid, "npc:target", dice="d100"), "skill": "手枪"},
            "dice_value": 96,
            "dice_rolls": [96],
        }
        result = resolve_check_request(instance, action, rule)
        assert result is not None
        verdicts[uid] = result["verdict"]

    assert verdicts == {"low": "大失败", "high": "失败"}


def test_two_attackers_apply_damage_to_one_target_in_real_order() -> None:
    instance = GameInstance(game_key=("test", "same-target", "bot"), rule_id="dnd5e")
    instance.players = {
        "a": _player("A", weapon_damage=10),
        "b": _player("B", weapon_damage=7),
    }
    instance.npcs = {"orc": _npc("Orc", hp=100)}
    instance.action_queue = [
        {"user_id": uid, "text": f"{uid} attacks Orc", "check_request": _request(f"check-{uid}", uid, "npc:orc")}
        for uid in ("a", "b")
    ]
    instance.last_checks = [
        _result(f"check-{uid}", uid, "npc:orc", roll=10)
        for uid in ("a", "b")
    ]

    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    first, second = instance.pending_combat_results
    assert (first["target_hp_before"], first["target_hp_after"], first["damage"]) == (100, 90, 10)
    assert (second["target_hp_before"], second["target_hp_after"], second["damage"]) == (90, 83, 7)
    assert instance.npcs["orc"]["hp"] == 83


def test_six_players_keep_check_target_and_damage_independent() -> None:
    instance = GameInstance(game_key=("test", "six-player-combat", "bot"), rule_id="dnd5e")
    for index in range(6):
        uid = f"p{index}"
        target = f"enemy-{index}"
        instance.players[uid] = _player(f"Player {index}")
        instance.npcs[target] = _npc(f"Enemy {index}", hp=50)
        instance.action_queue.append({
            "user_id": uid,
            "text": f"{uid} attacks Enemy {index}",
            "check_request": _request(f"check-{index}", uid, f"npc:{target}"),
        })
        verdict = "失败" if index == 0 else ("大成功" if index == 5 else "成功")
        instance.last_checks.append(_result(
            f"check-{index}",
            uid,
            f"npc:{target}",
            verdict=verdict,
            roll=20 if index == 5 else 10,
            critical=index == 5,
        ))

    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    records = {item["attacker_uid"]: item for item in instance.pending_combat_results}
    assert len(records) == 6
    for index in range(6):
        uid = f"p{index}"
        assert records[uid]["check_id"] == f"check-{index}"
        assert records[uid]["target"] == f"Enemy {index}"
    assert records["p0"]["damage"] == 0
    assert records["p5"]["damage"] > records["p4"]["damage"]


def test_combat_consumes_only_live_players_attack_checks() -> None:
    instance = GameInstance(game_key=("test", "combat-filter", "bot"), rule_id="dnd5e")
    instance.players = {uid: _player(uid.upper()) for uid in ("a", "b", "c", "d")}
    instance.players["c"]["character_sheet"]["deceased"] = True
    instance.npcs = {"orc": _npc("Orc", hp=60)}
    instance.action_queue = [
        {"user_id": "a", "text": "A attacks Orc", "check_request": _request("attack-a", "a", "npc:orc")},
        {"user_id": "b", "text": "B investigates Orc", "check_request": {**_request("check-b", "b", "npc:orc"), "kind": "check"}},
        {"user_id": "c", "text": "C attacks Orc", "check_request": _request("attack-c", "c", "npc:orc")},
    ]
    instance.last_checks = [
        _result("attack-a", "a", "npc:orc"),
        {**_result("check-b", "b", "npc:orc"), "kind": "check"},
        _result("attack-c", "c", "npc:orc"),
        _result("attack-d", "d", "npc:orc"),
    ]

    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    assert [item["attacker_uid"] for item in instance.pending_combat_results] == ["a"]


def test_negative_enemy_reference_is_rejected_instead_of_selecting_last_enemy() -> None:
    instance = GameInstance(game_key=("test", "negative-enemy", "bot"), rule_id="dnd5e")
    instance.players = {"a": _player("A")}
    instance.combat_enemies = [_npc("Last Enemy", hp=30)]
    instance.action_queue = [{
        "user_id": "a",
        "text": "A attacks an invalid enemy",
        "check_request": _request("negative", "a", "enemy:-1"),
    }]
    instance.last_checks = [_result("negative", "a", "enemy:-1")]

    CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    assert instance.combat_enemies[0]["hp"] == 30
    assert instance.pending_combat_results == []


def test_damage_calculation_is_pure_and_dice_system_aware() -> None:
    target = _npc("Target", hp=20, armor=2)
    successful_d20 = _result("d20", "a", "npc:target", roll=10)
    critical_d20 = _result("critical", "a", "npc:target", verdict="大成功", roll=20, critical=True)
    failed = _result("failed", "a", "npc:target", verdict="失败", roll=4)
    successful_d100 = _result("d100", "a", "npc:target", verdict="普通成功", roll=70, dice="d100")

    normal = calculate_attack_damage(successful_d20, weapon_damage=10, target_armor=2, same_faction=True)
    critical = calculate_attack_damage(critical_d20, weapon_damage=10, target_armor=2, same_faction=True)
    miss = calculate_attack_damage(failed, weapon_damage=10, target_armor=2)
    percentile = calculate_attack_damage(successful_d100, weapon_damage=8, target_armor=0)

    assert (normal, critical, miss, percentile) == (4, 9, 0, 8)
    assert target["hp"] == 20


def test_overkill_records_only_the_hp_damage_actually_applied() -> None:
    target = _npc("Nearly Down", hp=2)

    result = resolve_attack(
        "A",
        target,
        {"name": "Greatsword", "damage": 10},
        check_result=_result("overkill", "a", "npc:target", verdict="大成功", roll=20, critical=True),
    )

    assert result.damage == result.actual_damage == 2
    assert result.target_hp_before - result.target_hp_after == 2
    assert target["hp"] == 0


@pytest.mark.parametrize(
    "text",
    ["我不攻击哥布林", "我不会向星墨开枪", "I don't attack the guard"],
)
def test_negated_attack_language_never_enters_damage_resolution(text: str) -> None:
    assert is_explicit_attack_action(text) is False


def test_mixed_negation_still_keeps_the_real_attack() -> None:
    assert is_explicit_attack_action("我不开枪，改用刀攻击哥布林") is True


@pytest.mark.asyncio
async def test_pending_attack_luck_never_applies_hp_damage() -> None:
    instance, _ = _single_attack_instance()
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.last_checks = [{
        **_result("attack-a", "a", "npc:goblin", verdict="失败", roll=60, dice="d100"),
        "luck_spend_available": True,
        "luck_cost": 5,
        "luck_decision": "pending",
    }]
    processor = RoundProcessor.__new__(RoundProcessor)
    processor.registry = SimpleNamespace(get=lambda _key: instance)

    async def prepared(_instance: GameInstance) -> list[dict]:
        return list(instance.last_checks)

    processor.prepare_round_checks_ai = prepared
    processor._schedule_luck_timeouts = lambda _instance: None
    hp_before = instance.npcs["goblin"]["hp"]

    result = await processor.process_round(instance)

    assert result == ("", None)
    assert instance.npcs["goblin"]["hp"] == hp_before
    assert "combat_outcome" not in instance.action_queue[0]
