"""SRD 5.2.1 equipment text: identity, category, cost, weight and prose.

Equipment cannot be imported the way spells were.  dnd5eapi's 2024
``/equipment`` entries carry identity and mechanics but their ``description``
array is empty for every single item -- there is simply no prose to fetch
there.  open5e's ``items`` endpoint (document ``srd-2024``) is the source that
has it, so the catalog is generated from open5e and the tests below pin the
contract, not the sentences.

The catalog is *reference* material.  It does not make an item purchasable,
equippable or owned -- that needs an ``item`` entity, which the ruleset
validates separately.  What it does give every reader is one place to answer
"what is this thing and what does it cost", including for items the ruleset has
no entity for yet (``poisoner_s_kit`` is exactly that case).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
BUNDLE = Path("templates/rulesets/dnd2024_srd")
TEXT = BUNDLE / "content/equipment_catalogs/srd_equipment.json"
ITEMS = BUNDLE / "content/items"


def _catalog() -> dict:
    return json.loads(TEXT.read_text(encoding="utf-8"))


def test_every_record_has_prose_and_a_stable_id() -> None:
    items = _catalog()["items"]
    assert len(items) == 203
    for item_id, entry in items.items():
        assert re.fullmatch(r"[a-z0-9][a-z0-9_]*", item_id), item_id
        assert entry["desc"], item_id
        assert all(isinstance(part, str) and part.strip() for part in entry["desc"])


def test_the_kit_the_cunning_strike_poison_needs_is_here() -> None:
    """F17 的 Poison 要求"随身携带 Poisoner's Kit"，此前全仓没有这条物品。

    注意 id 形状：撇号按既有约定折成 `_s_`（与法术目录的
    ``hunter_s_mark`` 一致），不是 ``poisoners_kit``。
    """

    kit = _catalog()["items"]["poisoner_s_kit"]
    assert kit["name"] == "Poisoner's Kit"
    assert kit["category"] == "tools"
    assert kit["cost_gp"] == 50
    assert kit["weight_lb"] == 2
    assert "poisoned object" in kit["desc"][0]


def test_the_catalog_covers_the_items_the_ruleset_already_has() -> None:
    """已有 item 实体必须能在文本目录里查到；缺的那些是有意留下的洞。

    这条也是缺口清单：`existing without text` 里的每一条，都是"仓库里有物品
    身份、但没有任何一句话解释它是什么"。数量若变化，说明有人补了文本或
    加了新物品没有配文本——两种都值得看一眼。
    """

    items = set(_catalog()["items"])
    existing = {path.stem for path in ITEMS.glob("*.json")}
    covered = existing & items
    assert len(existing) == 82
    assert len(covered) >= 50
    # 反向：文本目录里绝大多数条目在仓库里还没有 item 实体（203 vs 82）。
    assert len(items - existing) >= 120


def test_the_file_states_its_own_license_and_source() -> None:
    payload = _catalog()
    assert payload["license"]["id"] == "CC-BY-4.0"
    assert payload["license"]["attribution"] == "legal/ATTRIBUTION.md"
    assert "open5e" in payload["license"]["transcription"]
    assert payload["automation_level"] == "reference"


def test_the_bundle_loads_the_equipment_text_as_its_own_kind() -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    catalog = bundle.get("equipment_text_catalog", "srd_equipment")
    assert catalog is not None
    assert len(catalog["items"]) == 203
    assert catalog["items"]["rope"]["desc"]
