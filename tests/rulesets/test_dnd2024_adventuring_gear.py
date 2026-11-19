"""Adventuring Gear: the narrow list the rulebook's own subsection declares.

The catalog is not "equipment". Weapons, armour, tools, packs, mounts,
vehicles, foci and trade goods each have their own table; Adventuring Gear is
the subsection that lists acid, ball bearings, chain, rope, torch and their
neighbours.  Every test here pins that boundary as well as the content, because
a catalog is only as useful as its scope is honest.

One source: dnd5eapi's 2024 ``adventuring-gear`` category supplies membership,
prose, price, weight and -- for the packs -- a structured contents list.  Seven
entries (the focus components) have no description in that source at all; they
are kept with an empty ``desc`` rather than filled in from memory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
BUNDLE = Path("templates/rulesets/dnd2024_srd")
TEXT = BUNDLE / "content/equipment_catalogs/srd_adventuring_gear.json"
NO_PROSE = {
    "crystal", "orb", "rod", "sprig_of_mistletoe", "staff", "wand", "yew_wand",
}


def _catalog() -> dict:
    return json.loads(TEXT.read_text(encoding="utf-8"))


def test_the_catalog_is_the_adventuring_gear_list_and_nothing_else() -> None:
    assert len(_catalog()["items"]) == 86
    items = set(_catalog()["items"])
    # 属于别的表：武器、护甲、工具、坐骑、载具、装备包、戏法焦点以外的杂物。
    for outside in ("longsword", "leather_armor", "thieves_tools",
                    "poisoner_s_kit", "riding_horse", "rowboat"):
        assert outside not in items
    # 属于本表：
    for inside in ("acid", "rope", "torch", "chain", "caltrops", "rations"):
        assert inside in items


def test_ids_are_stable_and_every_entry_carrying_prose_has_real_text() -> None:
    for item_id, entry in _catalog()["items"].items():
        assert re.fullmatch(r"[a-z0-9][a-z0-9_]*", item_id), item_id
        if item_id in NO_PROSE:
            assert entry["desc"] == [], item_id
            continue
        assert entry["desc"], item_id
        assert all(isinstance(part, str) and part.strip() for part in entry["desc"])


def test_the_rules_text_that_only_this_catalog_has() -> None:
    """冒险装备的价值全在描述里：一条绳子的规则只写在这里。"""

    rope = _catalog()["items"]["rope"]["desc"][0]
    assert "Utilize action" in rope and "knot" in rope
    caltrops = " ".join(_catalog()["items"]["caltrops"]["desc"])
    assert "5-foot-square" in caltrops


def test_cost_and_weight_come_from_the_source() -> None:
    rope = _catalog()["items"]["rope"]
    assert rope["cost_gp"] == 1
    assert rope["weight_lb"] == 5
    # 不是每个条目都有价格（戏法卷轴按等级定价），缺就缺，不补 0。
    assert "cost_gp" in rope


def test_packs_carry_their_contents_as_structure_not_only_as_prose() -> None:
    """买一个包等于拿到清单里的物品；这条关系必须能读，不能只写在正文里。"""

    pack = _catalog()["items"]["burglar_s_pack"]
    assert pack["cost_gp"] == 16
    assert pack["contents"], "pack lost its contents"
    by_item = {row["item"]: row["quantity"] for row in pack["contents"]}
    assert by_item["backpack"] == 1
    assert by_item["ball_bearings"] == 1
    assert by_item["candle"] == 10 and by_item["rations"] == 5
    assert all(row["quantity"] >= 1 for row in pack["contents"])
    # 容器身份同样是结构化事实。
    assert _catalog()["items"]["scholar_s_pack"]["container"] == "backpack"


def test_the_file_states_its_own_scope_license_and_source() -> None:
    payload = _catalog()
    assert payload["scope"] == "adventuring-gear"
    assert payload["license"]["id"] == "CC-BY-4.0"
    assert payload["license"]["attribution"] == "legal/ATTRIBUTION.md"
    assert "dnd5eapi" in payload["license"]["transcription"]
    assert payload["automation_level"] == "reference"


def test_the_bundle_loads_it_as_its_own_kind() -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    catalog = bundle.get("equipment_text_catalog", "srd_adventuring_gear")
    assert catalog is not None
    assert len(catalog["items"]) == 86
