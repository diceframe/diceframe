"""Tools: the table that carries a check and a DC, not just a description.

Tools are their own table (artisan's tools, kits, instruments, gaming sets).
What makes them worth importing is not the prose -- the source has none for
any of the 36 -- but the structured parts: the ability a tool keys off, the
``utilize`` entries (task + DC + success type), what it can craft, and for kits
the contents.

Poisoner's Kit is the one this project has been waiting on: the Cunning Strike
poison option requires carrying it, and until now the tree had no such item at
all. It now carries `ability: int`, `utilize: Detect a poisoned object (DC 10)`
and `craft: [poison_basic]`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
BUNDLE = Path("templates/rulesets/dnd2024_srd")
TEXT = BUNDLE / "content/equipment_catalogs/srd_tools.json"


def _catalog() -> dict:
    return json.loads(TEXT.read_text(encoding="utf-8"))


def test_the_catalog_is_the_tools_table() -> None:
    items = _catalog()["items"]
    assert len(items) == 36
    for inside in ("thieves_tools", "poisoners_kit", "herbalism_kit",
                   "smiths_tools", "lute", "playing_cards"):
        assert inside in items
    # 别的表：冒险装备、武器、护甲。
    for outside in ("rope", "torch", "longsword", "leather_armor"):
        assert outside not in items


def test_ids_are_stable_and_the_apostrophe_folds_to_s() -> None:
    for item_id in _catalog()["items"]:
        assert re.fullmatch(r"[a-z0-9][a-z0-9_]*", item_id), item_id
    assert "poisoners_kit" in _catalog()["items"]


def test_the_poisoners_kit_carries_what_cunning_strike_needs() -> None:
    kit = _catalog()["items"]["poisoners_kit"]
    assert kit["name"] == "Poisoner's Kit"
    assert kit["ability"] == "int"
    assert kit["utilize"] == [
        {"task": "Detect a poisoned object", "dc": 10, "success": "none"}
    ]
    assert kit["craft"] == ["poison_basic"]
    assert kit["weight_lb"] == 2


def test_utilize_entries_are_structured_for_every_tool_that_has_one() -> None:
    items = _catalog()["items"]
    with_utilize = {key: value["utilize"] for key, value in items.items()
                    if value.get("utilize")}
    assert with_utilize, "no tool kept its utilize entries"
    for rows in with_utilize.values():
        for row in rows:
            assert row["task"]
            assert isinstance(row["dc"], int) and row["dc"] > 0


def test_the_file_states_its_own_scope_license_and_source() -> None:
    payload = _catalog()
    assert payload["scope"] == "tools"
    assert payload["license"]["id"] == "CC-BY-4.0"
    assert "dnd5eapi" in payload["license"]["transcription"]
    assert payload["automation_level"] == "reference"


def test_the_bundle_loads_it_as_its_own_kind() -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    catalog = bundle.get("equipment_text_catalog", "srd_tools")
    assert catalog is not None
    assert len(catalog["items"]) == 36
