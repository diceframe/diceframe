"""S0-EFFECT1 P1：载荷的读时归一。"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.combat.effect_items import (
    LIFETIME_EXPIRING,
    LIFETIME_INSTANT,
    LIFETIME_MAINTAINED,
    legacy_label,
    lifetime_of,
    normalize_effect_item,
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


def test_the_legacy_event_label_is_reproduced() -> None:
    """事件里的 `source_rider` 是 replay 的一部分，归一后必须还原成同一个词。"""

    assert legacy_label({"condition": "prone"}) == "prone"
    assert legacy_label({"condition": "grappled", "escape_dc": 12}) == "grapple"
    assert legacy_label({"push": 10}) == "push"
    assert legacy_label({"condition": "poisoned"}) == "condition"


def test_lifetime_has_three_tiers() -> None:
    assert lifetime_of({"damage": {"dice": "1d6", "type": "fire"}}) == LIFETIME_INSTANT
    assert lifetime_of({"condition": "prone", "duration": "target_turn_end"}) == LIFETIME_EXPIRING
    # 需维护也有到期点，区别在于有没有维护者（重掷 / 专注）。
    assert lifetime_of({"condition": "paralyzed", "save_repeat": "end_of_turn"}) == LIFETIME_MAINTAINED
