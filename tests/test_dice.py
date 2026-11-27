"""骰子系统测试。"""

from types import SimpleNamespace

import pytest
from src.engine import check_verdict
from src.engine.dice import (
    DiceResult,
    check_coc,
    check_d100,
    check_d100_bonus,
    check_d20,
    check_d20_advantage,
    coc_success_level,
    d20_dc_cap,
    parse_player_roll,
    roll,
    roll_initiative,
)


class TestRoll:
    def test_d20(self):
        for _ in range(100):
            r = roll("d20")
            assert 1 <= r.total <= 20
            assert r.modifier == 0

    def test_d20_plus_mod(self):
        r = roll("d20+5")
        assert 6 <= r.total <= 25
        assert r.modifier == 5

    def test_2d6(self):
        for _ in range(100):
            r = roll("2d6")
            assert 2 <= r.total <= 12

    def test_d100(self):
        for _ in range(100):
            r = roll("d100")
            assert 1 <= r.total <= 100

    def test_invalid_formula(self):
        with pytest.raises(ValueError):
            roll("invalid")

    def test_dice_count_and_sides_limits(self):
        """公网 bot 防滥用：超大骰数/面数必须报错，0d6 也报错。"""
        with pytest.raises(ValueError):
            roll("999999999d6")
        with pytest.raises(ValueError):
            roll("0d6")
        with pytest.raises(ValueError):
            roll("d1")
        with pytest.raises(ValueError):
            roll("d20+101")

    def test_multi_dice_natural_excludes_modifier(self, monkeypatch):
        """natural 是未加修正的原始值：多骰=骰面之和，不含修正。"""
        monkeypatch.setattr("random.randint", lambda a, b: 3)
        r = roll("2d6+5")
        assert r.natural == 6
        assert r.total == 11


class TestCheckD20:
    def test_critical_success(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 20)
        result, verdict = check_d20(modifier=0, dc=15)
        assert verdict == check_verdict.CRITICAL_SUCCESS
        assert result.is_critical

    def test_fumble(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 1)
        result, verdict = check_d20(modifier=5, dc=10)
        assert verdict == check_verdict.CRITICAL_FAILURE
        assert result.is_fumble

    def test_success(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 15)
        result, verdict = check_d20(modifier=2, dc=15)
        assert verdict == check_verdict.SUCCESS

    def test_failure(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 8)
        result, verdict = check_d20(modifier=2, dc=15)
        assert verdict == check_verdict.FAILURE

    def test_natural_twenty_can_be_disabled_for_strict_rules(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 20)
        result, verdict = check_d20(modifier=2, dc=25, crit_on=None, fumble_on=None)
        assert result.total == 22
        assert verdict == check_verdict.FAILURE
        assert not result.is_critical

    def test_advantage_uses_higher_d20(self, monkeypatch):
        rolls = iter([7, 18])
        monkeypatch.setattr("random.randint", lambda a, b: next(rolls))

        result, verdict = check_d20_advantage(modifier=2, dc=15, advantage=True)

        assert result.rolls == [7, 18]
        assert result.natural == 18
        assert result.total == 20
        assert verdict == check_verdict.SUCCESS

    def test_disadvantage_uses_lower_d20(self, monkeypatch):
        rolls = iter([7, 18])
        monkeypatch.setattr("random.randint", lambda a, b: next(rolls))

        result, verdict = check_d20_advantage(modifier=2, dc=15, disadvantage=True)

        assert result.rolls == [7, 18]
        assert result.natural == 7
        assert result.total == 9
        assert verdict == check_verdict.FAILURE

    def test_advantage_and_disadvantage_cancel(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 12)

        result, verdict = check_d20_advantage(modifier=3, dc=15, advantage=True, disadvantage=True)

        assert result.rolls == [12]
        assert result.natural == 12
        assert result.total == 15
        assert verdict == check_verdict.SUCCESS


class TestCheckD100:
    def test_below_threshold(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 45)
        _, verdict = check_d100(threshold=50)
        assert verdict == check_verdict.REGULAR_SUCCESS

    def test_above_threshold(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 70)
        _, verdict = check_d100(threshold=50)
        assert verdict == check_verdict.FAILURE

    def test_extreme_success(self, monkeypatch):
        """统一 CoC 判定后：5 ≤ 50//5 走极难成功，不再是旧的大成功。"""
        monkeypatch.setattr("random.randint", lambda a, b: 5)
        result, verdict = check_d100(threshold=50)
        assert verdict == check_verdict.EXTREME_SUCCESS
        assert result.natural == 5

    def test_high_skill_96_is_failure_not_fumble(self, monkeypatch):
        """CoC 7e：技能 ≥ 50 时只有 100 才是大失败，96 只是失败。"""
        monkeypatch.setattr("random.randint", lambda a, b: 96)
        result, verdict = check_d100(threshold=80)
        assert verdict == check_verdict.FAILURE
        assert not result.is_fumble

    def test_low_skill_96_is_fumble(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 96)
        result, verdict = check_d100(threshold=40)
        assert verdict == check_verdict.CRITICAL_FAILURE
        assert result.is_fumble


class TestCheckD100Bonus:
    def test_bonus_dice_uses_best_tens(self, monkeypatch):
        rolls = iter([4, 8, 2])
        monkeypatch.setattr("random.randint", lambda a, b: next(rolls))
        result, verdict = check_d100_bonus(threshold=30, bonus_dice=1)
        assert result.total == 24
        assert verdict == check_verdict.REGULAR_SUCCESS

    def test_penalty_dice_uses_worst_tens(self, monkeypatch):
        rolls = iter([4, 2, 8])
        monkeypatch.setattr("random.randint", lambda a, b: next(rolls))
        result, verdict = check_d100_bonus(threshold=50, penalty_dice=1)
        assert result.total == 84
        assert verdict == check_verdict.FAILURE

    def test_zero_zero_is_one_hundred(self, monkeypatch):
        rolls = iter([0, 0])
        monkeypatch.setattr("random.randint", lambda a, b: next(rolls))
        result, verdict = check_d100_bonus(threshold=99)
        assert result.total == 100
        assert verdict == check_verdict.CRITICAL_FAILURE

    def test_bonus_with_zero_units_picks_lower_final_value(self, monkeypatch):
        """回归：个位=0、十位=[0,30] 时最终值为 [100,30]，奖励骰必须取 30。

        旧实现先对十位取 min 再转换 00→100，会把奖励骰结果反转成 100。
        """
        rolls = iter([0, 0, 3])
        monkeypatch.setattr("random.randint", lambda a, b: next(rolls))
        result, verdict = check_d100_bonus(threshold=50, bonus_dice=1)
        assert result.total == 30
        assert result.rolls == [100, 30]
        assert verdict == check_verdict.REGULAR_SUCCESS

    def test_penalty_with_zero_units_picks_hundred(self, monkeypatch):
        rolls = iter([0, 0, 3])
        monkeypatch.setattr("random.randint", lambda a, b: next(rolls))
        result, verdict = check_d100_bonus(threshold=50, penalty_dice=1)
        assert result.total == 100
        assert result.rolls == [100, 30]
        assert verdict == check_verdict.CRITICAL_FAILURE

    def test_rejects_unbounded_bonus_dice(self):
        with pytest.raises(ValueError):
            check_d100_bonus(threshold=50, bonus_dice=101)


def test_d20_dc_cap_default():
    assert d20_dc_cap(None) == 20


def test_d20_dc_cap_does_not_infer_from_high_dc_table():
    rule = SimpleNamespace(dc_table={"easy": 10, "extreme": 25})
    assert d20_dc_cap(rule) == 20


def test_d20_dc_cap_allows_bounded_custom_override():
    assert d20_dc_cap(SimpleNamespace(max_check_dc=30)) == 30
    assert d20_dc_cap(SimpleNamespace(max_check_dc=999)) == 40
    assert d20_dc_cap(SimpleNamespace(max_check_dc="invalid")) == 20


def test_roll_initiative_preserves_dex_modifier(monkeypatch):
    monkeypatch.setattr("random.randint", lambda a, b: 12)
    result = roll_initiative(3)
    assert result.natural == 12
    assert result.modifier == 3
    assert result.total == 15


class TestCheckCoc:
    def test_success_levels(self):
        assert coc_success_level(1, 60) == check_verdict.CRITICAL_SUCCESS
        assert coc_success_level(10, 60) == check_verdict.EXTREME_SUCCESS
        assert coc_success_level(30, 60) == check_verdict.HARD_SUCCESS
        assert coc_success_level(55, 60) == check_verdict.REGULAR_SUCCESS
        assert coc_success_level(70, 60) == check_verdict.FAILURE
        assert coc_success_level(96, 40) == check_verdict.CRITICAL_FAILURE

    def test_check_coc_uses_levels(self, monkeypatch):
        monkeypatch.setattr("random.randint", lambda a, b: 24)
        result, verdict = check_coc(60)
        assert result.natural == 24
        assert verdict == check_verdict.HARD_SUCCESS


class TestParsePlayerRoll:
    def test_parse_d20(self):
        r = parse_player_roll("掷骰 d20")
        assert r is not None

    def test_parse_2d6(self):
        r = parse_player_roll("/掷骰 2d6+1")
        assert r is not None
        assert "2d6" in r.formula

    def test_no_roll(self):
        assert parse_player_roll("我要攻击") is None

    def test_alias_chinese_words(self):
        """投骰/丢骰/骰子 等中文别名同样触发手动掷骰。"""
        for cmd in ("投骰 d20", "丢骰 2d6", "骰子 d100", "投个骰 d20+3"):
            assert parse_player_roll(cmd) is not None, cmd

    def test_alias_english(self):
        """roll / dice 英文触发词（大小写不敏感）。"""
        assert parse_player_roll("roll d20") is not None
        assert parse_player_roll("ROLL 2d6+1") is not None
        assert parse_player_roll("/roll d100") is not None
        assert parse_player_roll("dice 3d8") is not None

    def test_alias_with_slash(self):
        """别名后接斜杠指令也能解析。"""
        r = parse_player_roll("/投骰 d20")
        assert r is not None and "d20" in r.formula

    def test_no_false_positive_on_dice_word(self):
        """「骰子真有趣」「我要 roll 起来」等无公式文本不误触发。"""
        assert parse_player_roll("骰子真有趣") is None
        assert parse_player_roll("这把骰子不错") is None
        assert parse_player_roll("let's roll") is None
        assert parse_player_roll("我要攻击") is None
