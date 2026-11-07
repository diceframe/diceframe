"""P2-A：百分制属性不再按数值大小猜旧卡。"""

from src.engine.checks import _percentile_value


def test_percentile_rule_values_are_used_as_is() -> None:
    # CoC 7e 的合法属性可以是 15 / 20，不应该被放大成 75 / 100
    assert _percentile_value({"attributes": {"dex": 15}}, "dex") == 15
    assert _percentile_value({"attributes": {"app": 20}}, "app") == 20
    assert _percentile_value({"attributes": {"str": 70}}, "str") == 70


def test_legacy_d20_cards_are_converted_only_when_declared() -> None:
    legacy = {"attribute_scale": "d20", "attributes": {"str": 12}}
    assert _percentile_value(legacy, "str") == 60

    legacy_flag = {"legacy_attributes": True, "attributes": {"con": 9}}
    assert _percentile_value(legacy_flag, "con") == 45

    # 没声明就按原值：宁可阈值偏低也不偷偷改变对抗结算
    assert _percentile_value({"attributes": {"con": 9}}, "con") == 9


def test_skills_stay_percentile_and_unknown_keys_resolve_to_none() -> None:
    state = {"skills": [{"name": "侦查", "value": 55}], "attributes": {"dex": 15}}
    assert _percentile_value(state, "侦查") == 55
    assert _percentile_value(state, "missing") is None
    assert _percentile_value(state, "") is None
