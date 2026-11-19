"""S0-F19 (part 1): the Hunter's five features exist and grant at the right levels.

F19 stopped because two prerequisites were missing: enemy defences (landed with
S0-MM1) and an option-swap entry point (the existing ``feature_choices`` slots).
This part lands the declarations the rest of the slice builds on, and proves
they are live rather than catalogue text -- the resolver must hand them out at
3/7/11/15 and nobody else may own them.

The two option groups use the same ``feature_option`` shape as Blessed Strikes,
so the whole slice rides on machinery that already resolves, validates and
projects choices.  What is *not* here yet, and is listed as such: the
short/long-rest swap, Hunter's Lore's private projection, and the combat riders
themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

BUNDLE_ROOT = Path("templates/rulesets/dnd2024_srd")
FEATURES = BUNDLE_ROOT / "content/class_feature_catalogs/srd_class_features.json"
HUNTER = "subclass:hunter"

#: level -> the features the Hunter owns at that level
GRANTS = {
    3: {"hunters_lore", "hunters_prey"},
    7: {"defensive_tactics"},
    11: {"superior_hunters_prey"},
    15: {"superior_hunters_defense"},
}
OPTIONS = {
    "hunters_prey": ["feature:colossus_slayer", "feature:horde_breaker"],
    "defensive_tactics": ["feature:escape_the_horde", "feature:multiattack_defense"],
}


def _catalog() -> dict:
    return json.loads(FEATURES.read_text(encoding="utf-8"))


def _feature(feature_id: str) -> dict:
    for feature in _catalog()["classes"]["ranger"]["features"]:
        if feature["id"] == feature_id:
            return feature
    raise AssertionError(f"feature missing: {feature_id}")


def _resolver() -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle("en"))


def _granted(level: int, subclass: str | None = HUNTER) -> set[str]:
    proxy: dict = {"build": {"class_levels": [{"class_ref": "class:ranger", "level": level}]}}
    if subclass:
        proxy["features"] = {"subclass_ref": subclass}
    return {feature.id for feature in _resolver().features_for(proxy)}


# ---------------------------------------------------------------------------
# 1. grants by level, and only for a Hunter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", sorted(GRANTS))
def test_a_hunter_owns_the_features_of_its_level(level: int) -> None:
    granted = _granted(level)
    expected: set[str] = set()
    for threshold, ids in GRANTS.items():
        if level >= threshold:
            expected |= ids
    assert expected <= granted, sorted(expected - granted)


def test_a_ranger_without_the_subclass_owns_none_of_them() -> None:
    granted = _granted(3, subclass=None)
    for ids in GRANTS.values():
        assert not (ids & granted)


def test_another_subclass_does_not_get_them() -> None:
    granted = _granted(11, subclass="subclass:fey_wanderer")
    for ids in GRANTS.values():
        assert not (ids & granted)


# ---------------------------------------------------------------------------
# 2. the two option groups are declared as real choices
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parent,options", sorted(OPTIONS.items()))
def test_option_groups_declare_exactly_their_two_options(parent: str, options: list[str]) -> None:
    choice = _feature(parent)["choice"]
    assert choice["kind"] == "feature_option"
    assert choice["count"] == 1
    assert choice["enabled_options"] == options
    for option in options:
        option_id = option.split(":", 1)[1]
        record = _feature(option_id)
        assert record["option_of"] == parent
        # 选项与父特性同一 owner：跨 owner 的选项会让解析器把两者挂在不同的
        # 角色上，测试 2 的越权场景就是这么来的。
        assert record["owner"] == HUNTER


def test_the_options_carry_the_numbers_the_riders_will_read() -> None:
    """不是文案：后续 rider 直接读这些参数。"""

    slayer = _feature("colossus_slayer")["parameters"]
    assert slayer["bonus_damage"] == "1d8"
    assert slayer["once_per_turn"] is True
    breaker = _feature("horde_breaker")["parameters"]
    assert breaker["extra_attacks"] == 1
    assert breaker["target_within_of_original_ft"] == 5
    assert _feature("superior_hunters_prey")["parameters"]["second_target_range_ft"] == 30


def test_every_hunter_feature_points_at_the_srd_page() -> None:
    for ids in GRANTS.values():
        for feature_id in ids:
            assert _feature(feature_id)["source_ref"].startswith("srd-5.2.1:p61:")


def test_the_bundle_loads_and_validates_the_option_references() -> None:
    """选项引用了不存在的特性时，加载期就该失败——这里只是确认它没有。"""

    catalog = Dnd2024Runtime().load_bundle("en")
    assert catalog is not None
    for parent, options in OPTIONS.items():
        for option in options:
            assert _feature(option.split(":", 1)[1])["option_of"] == parent


def test_the_labels_exist_in_both_locales() -> None:
    for locale in ("en", "zh-CN"):
        path = BUNDLE_ROOT / f"locales/{locale}/class_feature_catalogs/srd_class_features.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        names = {}
        for bucket in payload["fields"].values():
            if isinstance(bucket, dict):
                names.update(bucket)
        for feature_id in ("hunters_lore", "hunters_prey", "defensive_tactics",
                           "superior_hunters_prey", "superior_hunters_defense",
                           "colossus_slayer", "horde_breaker",
                           "escape_the_horde", "multiattack_defense"):
            assert names.get(feature_id), (locale, feature_id)
