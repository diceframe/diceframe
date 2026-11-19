"""SRD 5.2.1 spell descriptions: they exist, they match the canonical list.

The description catalog is generated (``scripts/rulesets/import_srd_spell_descriptions.py``),
so the tests here are about the *contract* between it and the spell catalog,
not about any particular sentence:

* the two catalogs describe exactly the same spells -- a description without a
  spell would be unreachable prose, a spell without a description would make
  the UI fall back to a blank card;
* level and school agree, because a mismatched pair means the transcription
  was mapped onto the wrong id and every reader would be lied to;
* the file carries its own license and attribution, since it is the one file
  that is pure CC-BY prose;
* the bundle loads it, so nothing has to reach for a hard-coded path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
BUNDLE = Path("templates/rulesets/dnd2024_srd")
DESCRIPTIONS = BUNDLE / "content/spell_catalogs/srd_spell_descriptions.json"
CATALOG = BUNDLE / "content/spell_catalogs/srd_spells.json"


def _descriptions() -> dict:
    return json.loads(DESCRIPTIONS.read_text(encoding="utf-8"))


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_every_canonical_spell_has_a_description_and_nothing_else_does() -> None:
    described = set(_descriptions()["spells"])
    canonical = {str(entry["id"]) for entry in _catalog()["spells"]}
    assert described == canonical
    assert len(described) == 339


def test_level_and_school_agree_with_the_canonical_catalog() -> None:
    catalog = {str(entry["id"]): entry for entry in _catalog()["spells"]}
    for spell_id, prose in _descriptions()["spells"].items():
        assert prose["level"] == int(catalog[spell_id]["level"]), spell_id
        assert prose["school"] == str(catalog[spell_id]["school"]), spell_id


def test_no_description_is_empty_and_paragraphs_are_strings() -> None:
    for spell_id, prose in _descriptions()["spells"].items():
        assert prose["desc"], spell_id
        assert all(isinstance(part, str) and part.strip() for part in prose["desc"])


def test_the_file_states_its_own_license_and_source() -> None:
    payload = _descriptions()
    assert payload["license"]["id"] == "CC-BY-4.0"
    assert payload["license"]["attribution"] == "legal/ATTRIBUTION.md"
    assert payload["source_ref"].startswith("srd-5.2.1:")
    # 纯散文：automation_level 必须是 reference，不能冒充 deterministic。
    assert payload["automation_level"] == "reference"


def test_the_bundle_loads_the_descriptions_as_their_own_kind() -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    catalog = bundle.get("spell_description_catalog", "srd_spell_descriptions")
    assert catalog is not None
    assert len(catalog["spells"]) == 339
    # 抽查一条 2024 文本：2024 版把 Fireball 写成 "fiery explosion"，与 2014
    # 的 "explosion of flame" 不同；这条断言顺带防止有人用 2014 快照覆盖。
    fireball = catalog["spells"]["fireball"]["desc"][0]
    assert "fiery explosion" in fireball


@pytest.mark.parametrize("spell_id", ["ice_storm", "flame_strike", "sacred_flame"])
def test_the_prose_needed_by_the_cover_declaration_is_present(spell_id: str) -> None:
    """掩护那一列要用的三种来源，正文必须能读到。

    冰风暴/焰击的来源在上方，圣火术明文无视掩护——将来给效应标注
    ``cover_interaction`` 时，依据就是这里读到的句子。
    """

    prose = _descriptions()["spells"][spell_id]["desc"]
    text = " ".join(prose).lower()
    assert text
    if spell_id == "ice_storm":
        assert "hail" in text
    if spell_id == "flame_strike":
        assert "column" in text
    if spell_id == "sacred_flame":
        assert "cover" in text
