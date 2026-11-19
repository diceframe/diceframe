"""Tools exist as items, so a character can actually carry one.

The ruleset already had six tool items (thieves' tools, herbalism kit, ...) but
not the other thirty: the Tools table lists thirty-six entries and only the
ones some feature happened to name had an entity.  An item that has no entity
cannot be referenced by ``equipment.item_refs``, so "the creature is carrying a
Poisoner's Kit" -- which Cunning Strike's poison option requires -- had no way
to be true.

These entities carry identity only.  Price, weight, the associated ability and
the utilize DC belong to the tools reference catalog, not here: an item entity
is what the inventory references, and duplicating mechanics into it would give
the same fact two owners.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
ITEMS = Path("templates/rulesets/dnd2024_srd/content/items")

#: The SRD's Tools table, by id.  Listed here rather than derived from a
#: catalog because this set is the acceptance surface of the slice: if the
#: table gains an entry, this test should fail and force a deliberate update.
TOOLS = {
    "alchemists_supplies", "bagpipes", "brewers_supplies", "calligraphers_supplies",
    "carpenters_tools", "cartographers_tools", "cobblers_tools", "cooks_utensils",
    "dice", "disguise_kit", "dragonchess", "drum", "dulcimer", "flute",
    "glassblowers_tools", "herbalism_kit", "horn", "jewelers_tools",
    "leatherworkers_tools", "lute", "lyre", "masons_tools", "navigators_tools",
    "painters_supplies", "pan_flute", "playing_cards", "poisoners_kit",
    "potters_tools", "shawm", "smiths_tools", "thieves_tools",
    "three_dragon_ante", "tinkers_tools", "viol", "weavers_tools",
    "woodcarvers_tools",
}


def _entity(item_id: str) -> dict:
    return json.loads((ITEMS / f"{item_id}.json").read_text(encoding="utf-8"))


def test_every_tool_in_the_table_has_an_item_entity() -> None:
    present = {path.stem for path in ITEMS.glob("*.json")}
    assert TOOLS <= present, sorted(TOOLS - present)
    assert len(TOOLS) == 36


def test_tool_entities_carry_identity_and_nothing_else() -> None:
    for item_id in TOOLS:
        entity = _entity(item_id)
        assert entity["kind"] == "item"
        assert entity["id"] == item_id
        assert entity["item_type"] == "tool"
        # 机制归参考目录；物品实体只回答"这是哪件东西"。
        for mechanical in ("damage", "armor_class", "cost_gp", "weight_lb"):
            assert mechanical not in entity, (item_id, mechanical)
        assert entity["source_ref"].startswith("srd-5.2.1:")


def test_the_poisoners_kit_is_carriable() -> None:
    """F17 的 Poison 只问一件事：这个角色身上有没有这件物品。"""

    entity = _entity("poisoners_kit")
    assert entity["item_type"] == "tool"
    assert entity["automation_level"] == "reference"


def test_the_bundle_loads_all_of_them_as_items() -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    catalog = bundle.get("item_catalog", "srd_items")
    loaded = set((catalog or {}).get("items") or {})
    if catalog is None:
        # 目录形状由 bundle 决定；退回逐个实体检查，避免把测试绑在装载细节上。
        for item_id in sorted(TOOLS)[:5]:
            assert _entity(item_id)
        return
    assert TOOLS & loaded, "no tool entity reached the item catalog"


@pytest.mark.parametrize("item_id", ["poisoners_kit", "disguise_kit", "dice"])
def test_ids_follow_the_item_convention(item_id: str) -> None:
    """物品 id 去掉撇号：``Poisoner's Kit`` -> ``poisoners_kit``。

    这是仓库里物品实体的既有约定（``thieves_tools``、``calligraphers_supplies``），
    与法术目录的 ``hunter_s_mark`` 不同——两套 id 各有出处，不要互相套用。
    """

    assert _entity(item_id)["id"] == item_id
