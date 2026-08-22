"""战斗集成测试 —— 战斗检测 → 结算 → 叙事一致性。"""

from __future__ import annotations

from src.engine.combat import (
    AttackResult,
    calc_hp_based_damage,
    calc_lethal_damage,
    resolve_attack,
)


def _check(
    roll: int = 12,
    *,
    verdict: str = "成功",
    critical: bool = False,
    fumble: bool = False,
) -> dict:
    return {
        "check_id": "attack-check",
        "actor_uid": "fighter",
        "kind": "attack",
        "dice": "d20",
        "roll": roll,
        "total": roll,
        "verdict": verdict,
        "is_critical": critical,
        "is_fumble": fumble,
    }


class TestCalcDamage:
    def test_hp_based_normal(self):
        dmg = calc_hp_based_damage(weapon_damage=6, attr_modifier=2, target_armor=0)
        assert dmg >= 1

    def test_hp_based_critical(self):
        check = _check(20, critical=True)
        dmg = calc_hp_based_damage(weapon_damage=6, attr_modifier=2, check_result=check)
        # Critical doubles base (6 + 2 = 8, doubled = 16, min 1)
        assert dmg >= 8

    def test_hp_based_fumble(self):
        check = _check(1, verdict="大失败", fumble=True)
        dmg = calc_hp_based_damage(weapon_damage=6, attr_modifier=2, check_result=check)
        assert dmg == 0  # 大失败直接 0 伤害，不应用 max(1, ...) 最小伤害下限

    def test_lethal_damage(self):
        dmg = calc_lethal_damage(6, 2, 4)
        # weapon(6) + attr*2(4) - armor//2(2) = 8
        assert dmg >= 6


class TestResolveAttack:
    def test_narrative_model(self):
        result = resolve_attack("战士", {"character_name": "哥布林", "hp": 20, "armor": 2},
                                {"name": "长剑", "damage": 7}, combat_model="narrative",
                                check_result=_check())
        assert result.attacker == "战士"
        assert result.target == "哥布林"
        assert result.damage == 0
        assert "叙事模式" in result.description

    def test_hp_based_model(self):
        result = resolve_attack("战士", {"character_name": "哥布林", "hp": 20, "armor": 2},
                                {"name": "长剑", "damage": 7}, attr_value=14, combat_model="hp_based",
                                check_result=_check())
        assert result.attacker == "战士"
        assert result.target == "哥布林"
        assert result.target_hp_after < result.target_hp_before or result.target_hp_after == result.target_hp_before
        assert isinstance(result.damage, int)

    def test_lethal_narrative_model(self):
        result = resolve_attack("战士", {"character_name": "哥布林", "hp": 20, "armor": 2},
                                {"name": "战斧", "damage": 9}, attr_value=16,
                                combat_model="lethal_narrative", check_result=_check())
        assert result.damage > 0
        assert result.target_hp_after < result.target_hp_before

    def test_unarmed(self):
        result = resolve_attack("战士", {"character_name": "哥布林", "hp": 20, "armor": 0},
                                None, attr_value=10, combat_model="hp_based", check_result=_check())
        assert result.damage >= 1

    def test_difficulty_easy(self):
        result = resolve_attack("战士", {"character_name": "哥布林", "hp": 30, "armor": 2},
                                {"name": "长剑", "damage": 7}, attr_value=14,
                                combat_model="hp_based", difficulty="轻松", check_result=_check())
        # B2 后难度不再缩放 HP（原 *0.7 每次受击叠加膨胀），只调 crit/fumble 阈值
        assert result.target_hp_before == 30

    def test_difficulty_hardcore(self):
        result = resolve_attack("战士", {"character_name": "哥布林", "hp": 10, "armor": 2},
                                {"name": "长剑", "damage": 7}, attr_value=14,
                                combat_model="hp_based", difficulty="硬核", check_result=_check())
        # B2 后难度不再缩放 HP（原 *1.3 每次受击叠加膨胀），只调 crit/fumble 阈值
        assert result.target_hp_before == 10

    def test_kill_target(self):
        result = resolve_attack("战士", {"character_name": "哥布林", "hp": 2, "armor": 0},
                                {"name": "巨剑", "damage": 10}, attr_value=18,
                                combat_model="hp_based", check_result=_check(20, critical=True))
        assert result.target_hp_after == 0
        assert "倒地" in result.description or "昏迷" in result.description

    def test_fumble_deals_zero_damage(self):
        """大失败造成 0 伤害，目标 HP 不变（不触发 max(1, ...) 最小伤害下限）。"""
        target = {"character_name": "哥布林", "hp": 20, "armor": 0}
        result = resolve_attack("战士", target, {"name": "长剑", "damage": 7},
                                attr_value=14, combat_model="hp_based",
                                check_result=_check(1, verdict="大失败", fumble=True))
        assert result.damage == 0
        assert result.actual_damage == 0
        assert result.target_hp_after == 20

    def test_missing_check_result_fails_closed_without_hp_mutation(self):
        target = {"character_name": "哥布林", "hp": 20, "max_hp": 20, "armor": 0}

        result = resolve_attack("战士", target, {"name": "长剑", "damage": 7})

        assert result.damage == 0
        assert target["hp"] == 20
        assert "缺少服务端攻击检定" in result.description
