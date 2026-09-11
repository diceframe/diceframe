"""``check_channels`` 纯策略：同一情境事实不得重复计入多条渠道。"""

from __future__ import annotations

import dataclasses

import pytest

from src.engine.check_channels import CheckChannels, normalize_check_channels

BASELINE = 12
CAP = 20


def normalize(**overrides):
    kwargs = {
        "target": 15,
        "modifier": 0,
        "advantage_mode": "",
        "baseline_dc": BASELINE,
        "dc_cap": CAP,
        "supports_advantage": True,
    }
    kwargs.update(overrides)
    return normalize_check_channels(**kwargs)


def test_result_is_frozen_dataclass() -> None:
    result = normalize()
    assert isinstance(result, CheckChannels)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.modifier = 3  # type: ignore[misc]


def test_untouched_channels_are_preserved() -> None:
    result = normalize(target=15, modifier=-2, advantage_mode="disadvantage",
                       dc_reason="门闩锈死", advantage_reason="脚下是湿滑的碎石",
                       modifier_reason="火把熄灭后光线昏暗")
    assert result.target == 15
    assert result.modifier == -2
    assert result.advantage_mode == "disadvantage"
    assert result.notes == ()
    assert result.dropped == {}


def test_unsupported_or_unknown_advantage_mode_falls_back_to_normal() -> None:
    for mode, supports in (("advantage", False), ("lucky", True), ("normal", True)):
        result = normalize(advantage_mode=mode, advantage_reason="光线昏暗",
                           supports_advantage=supports)
        assert result.advantage_mode == ""
        # 既有 planner 行为：这一步是归一化，不产生折叠 note。
        assert result.notes == ()


def test_modifier_without_reason_is_zeroed() -> None:
    result = normalize(modifier=-4, modifier_reason="   ")
    assert result.modifier == 0
    assert result.notes == ("modifier_without_reason",)
    assert result.dropped == {"modifier": -4}


def test_advantage_without_reason_is_preserved_and_only_audited() -> None:
    """缺少依据只追加审计提示：不降级、不改写掷骰方式、不写 dropped。"""
    result = normalize(advantage_mode="disadvantage", advantage_reason="")
    assert result.advantage_mode == "disadvantage"
    assert result.notes == ("advantage_without_reason",)
    assert result.dropped == {}


def test_reasonless_advantage_does_not_collapse_a_reasoned_dc() -> None:
    """没有 reason 的 advantage 不参与同源比较，也不会折叠掉带 reason 的 DC。"""
    result = normalize(
        target=18,
        advantage_mode="disadvantage",
        advantage_reason="",
        dc_reason="门闩锈死",
    )
    assert result.advantage_mode == "disadvantage"
    assert result.target == 18
    assert result.notes == ("advantage_without_reason",)
    assert result.dropped == {}


def test_same_reason_across_all_three_channels_keeps_only_advantage() -> None:
    result = normalize(
        target=18,
        modifier=-6,
        advantage_mode="disadvantage",
        dc_reason="光线昏暗难以看清门闩",
        advantage_reason="光线昏暗难以看清门闩！",
        modifier_reason="  光线昏暗, 难以看清门闩  ",
    )
    assert result.advantage_mode == "disadvantage"
    assert result.target == BASELINE
    assert result.modifier == 0
    assert result.notes == ("same_fact_as_advantage", "same_fact_as_advantage")
    assert result.dropped == {"target": 18, "modifier": -6}


def test_same_reason_between_dc_and_modifier_keeps_dc() -> None:
    result = normalize(
        target=18,
        modifier=-3,
        dc_reason="门闩锈死",
        modifier_reason="门闩锈死！",
    )
    assert result.target == 18
    assert result.modifier == 0
    assert result.notes == ("same_fact_as_dc",)
    assert result.dropped == {"modifier": -3}


def test_short_reasons_are_not_treated_as_the_same_fact() -> None:
    result = normalize(target=18, modifier=-3, dc_reason="雨", modifier_reason="雨")
    assert result.target == 18
    assert result.modifier == -3
    assert result.notes == ()


def test_duplicate_dc_reset_is_clamped_to_the_dc_cap() -> None:
    result = normalize(
        target=35,
        modifier=0,
        advantage_mode="disadvantage",
        dc_reason="门闩锈死",
        advantage_reason="门闩锈死",
        baseline_dc=30,
        dc_cap=20,
    )
    assert result.target == 20
    assert result.notes == ("same_fact_as_advantage",)
    assert result.dropped == {"target": 35}


def test_obvious_double_penalty_is_collapsed_to_one_channel() -> None:
    result = normalize(
        target=18,
        modifier=-6,
        advantage_mode="disadvantage",
        dc_reason="门闩锈死需要蛮力",
        advantage_reason="被泥水浸透难以发力",
        modifier_reason="强风让人站不稳",
    )
    assert result.advantage_mode == "disadvantage"
    assert result.target == 18
    assert result.modifier == 0
    assert result.notes == ("stacked_penalty",)
    assert result.dropped == {"modifier": -6}


def test_stacking_fallback_uses_exactly_minus_five() -> None:
    kept = normalize(
        target=18,
        modifier=-4,
        advantage_mode="disadvantage",
        dc_reason="门闩锈死需要蛮力",
        advantage_reason="被泥水浸透难以发力",
        modifier_reason="强风让人站不稳",
    )
    assert kept.modifier == -4
    assert kept.notes == ()


def test_obvious_double_bonus_is_collapsed_to_one_channel() -> None:
    result = normalize(
        target=8,
        modifier=5,
        advantage_mode="advantage",
        dc_reason="门闩早已松动",
        advantage_reason="从背后接近守卫",
        modifier_reason="同伙在旁协助照明",
    )
    assert result.advantage_mode == "advantage"
    assert result.target == 8
    assert result.modifier == 0
    assert result.notes == ("stacked_bonus",)
    assert result.dropped == {"modifier": 5}


def test_stacking_bonus_fallback_uses_exactly_plus_five() -> None:
    kept = normalize(
        target=8,
        modifier=4,
        advantage_mode="advantage",
        dc_reason="门闩早已松动",
        advantage_reason="从背后接近守卫",
        modifier_reason="同伙在旁协助照明",
    )
    assert kept.modifier == 4
    assert kept.notes == ()


def test_d100_without_a_model_target_skips_the_dc_channel() -> None:
    result = normalize(
        target=None,
        modifier=-6,
        advantage_mode="disadvantage",
        baseline_dc=None,
        dc_reason="模型写的情境 DC 在 d100 下不被采用",
        advantage_reason="在黑暗中摸索",
        modifier_reason="双手被反绑",
    )
    assert result.target is None
    assert result.modifier == -6
    assert result.advantage_mode == "disadvantage"
    assert result.notes == ()
    assert result.dropped == {}


def test_identical_reasons_survive_when_only_the_modifier_channel_exists() -> None:
    result = normalize(
        target=None,
        modifier=-6,
        advantage_mode="",
        baseline_dc=None,
        dc_reason="光线昏暗",
        modifier_reason="光线昏暗",
    )
    assert result.modifier == -6
    assert result.notes == ()
