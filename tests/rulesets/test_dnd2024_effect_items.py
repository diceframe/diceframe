"""S0-EFFECT1 P1：载荷的读时归一。"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.combat.effect_items import (
    LIFETIME_EXPIRING,
    LIFETIME_INSTANT,
    LIFETIME_MAINTAINED,
    lifetime_of,
    normalize_effect_item,
    normalize_rider,
)


@pytest.mark.parametrize("raw, expected", [
    ({"kind": "condition", "condition": "poisoned"}, {"condition": "poisoned"}),
    ({"kind": "prone"}, {"condition": "prone"}),
    ({"kind": "grapple", "escape_dc": 12, "max_target_size": "medium"},
     {"condition": "grappled", "escape_dc": 12, "max_target_size": "medium"}),
    ({"kind": "push", "distance": 10}, {"push": 10}),
    ({"kind": "condition", "condition": "restrained",
      "save": {"ability": "dex", "dc": 13}, "duration": "target_turn_end"},
     {"condition": "restrained", "save": {"ability": "dex", "dc": 13},
      "duration": "target_turn_end"}),
])
def test_legacy_riders_normalize_without_losing_meaning(raw, expected) -> None:
    assert normalize_effect_item(raw) == expected


def test_an_unknown_kind_is_not_guessed() -> None:
    """认不出来就返回 None：半懂的施加比不施加更坏。"""

    assert normalize_effect_item({"kind": "teleport", "distance": 30}) is None
    assert normalize_effect_item("rubbish") is None


def test_a_legal_but_ambiguous_rider_normalises_lossily_and_that_is_why_p1_has_no_consumer() -> None:
    """`kind:"condition"` + `prone` 与 `kind:"prone"` 归一后不可区分。

    旧事件按 `kind` 写字，所以这两种输入的事件不同；归一后信息丢了。因此 P1
    不把归一连到 rider 路径上——那正是"事件一字不动"的要求。这条用例把限制
    固定下来，免得有人以为 normalize 是旧事件的逆函数。
    """

    assert normalize_effect_item({"kind": "condition", "condition": "prone"}) == \
        normalize_effect_item({"kind": "prone"})


def test_an_unknown_field_is_refused_not_dropped() -> None:
    """静默丢弃未知字段 = 调用方以为施加了，其实没有。"""

    with pytest.raises(Exception):
        normalize_effect_item({"kind": "prone", "surprise": 1})


def test_lifetime_has_three_tiers() -> None:
    assert lifetime_of({"damage": {"dice": "1d6", "type": "fire"}}) == LIFETIME_INSTANT
    assert lifetime_of({"condition": "prone", "duration": "target_turn_end"}) == LIFETIME_EXPIRING
    # 需维护也有到期点，区别在于有没有维护者（重掷 / 专注）。
    assert lifetime_of({"condition": "paralyzed", "save_repeat": "wis"}) == LIFETIME_MAINTAINED


def test_the_declared_source_survives_normalisation() -> None:
    """事件里的 `source_rider` 由生产方声明，不靠条件名反推。

    `{"kind":"condition","condition":"prone"}` 与 `{"kind":"prone"}` 归一后虽然
    得到同一个 item，但 `source` 各自保留原值——这正是 P1 不能接入、而 P2a
    能接入的原因。
    """

    assert normalize_rider({"kind": "condition", "condition": "prone"}) == (
        "condition", {"condition": "prone"},
    )
    assert normalize_rider({"kind": "prone"}) == ("prone", {"condition": "prone"})


def test_a_kind_less_item_declares_its_own_source() -> None:
    assert normalize_rider({"push": 10}) == ("push", {"push": 10})
    assert normalize_rider({"condition": "poisoned"}) == ("condition", {"condition": "poisoned"})
