"""M2-01 dnd5e Actor/Item conversion: display DTO, draft candidate, report.

Contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §3.2 (six rules), §5.2 and
§6.16 (M2 FROZEN). Fixtures are synthetic documents built from the verified
dnd5e 6.1.x data-model source; each fixture's .meta.json records the source
files and expected report shape.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from src.compat.dnd5e import (
    Dnd5eConversionError,
    convert_actor,
    convert_item,
    default_catalog,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dnd5e"
FOUNDRY_COMPAT = Path(__file__).resolve().parents[1] / "src" / "compat" / "foundry"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _meta(name: str) -> dict[str, Any]:
    return _load(name)


@pytest.fixture(scope="module")
def catalog():
    return default_catalog("en")


@pytest.fixture(scope="module")
def srd_actor() -> dict[str, Any]:
    return convert_actor(_load("actor_srd_dwarf_fighter.json"), locale="en")


# ---- SRD Actor: refs, abilities, draft candidate -------------------------


def test_srd_actor_maps_catalog_refs_in_recorded_order(srd_actor) -> None:
    meta = _meta("actor_srd_dwarf_fighter.meta.json")
    assert srd_actor["catalog_refs"] == meta["expected_catalog_refs"]


def test_srd_actor_draft_candidate_matches_builder_draft_shape(srd_actor) -> None:
    draft = srd_actor["draft"]
    assert draft is not None
    for key in (
        "name", "level", "class_ref", "species_ref", "background_ref",
        "base_abilities", "ability_method", "background_ability_bonuses",
        "class_skill_refs", "equipment_package_ref", "language_refs",
    ):
        assert key in draft, key
    assert draft["level"] == 1
    assert draft["class_ref"] == "class:fighter"
    assert draft["species_ref"] == "species:dwarf"
    assert draft["background_ref"] == "background:soldier"
    assert draft["base_abilities"] == {
        "str": 17, "dex": 12, "con": 16, "int": 9, "wis": 13, "cha": 8,
    }
    assert draft["ability_method"] == "rolled"
    # Background +2/+1 split is not recorded by dnd5e; never fabricated.
    assert draft["background_ability_bonuses"] == {}
    assert draft["class_skill_refs"] == ["skill:history", "skill:perception"]
    assert draft["language_refs"] == ["language:common", "language:dwarvish"]
    assert draft["alignment"] == "lawful_good"


def test_srd_actor_display_is_presentation_safe(srd_actor) -> None:
    display = srd_actor["display"]
    assert display["name"] == "Dain Ironfoot"
    assert display["level"] == 1
    assert display["species"]["ref"] == "species:dwarf"
    assert display["species"]["name"] == "Dwarf"
    assert display["background"]["ref"] == "background:soldier"
    assert display["class"][0]["ref"] == "class:fighter"
    assert display["class"][0]["name"] == "Fighter"
    assert display["abilities"]["str"]["score"] == 17
    assert display["hp"] == {
        "value": 13, "max": 13, "temp": 0, "declared": True,
    }
    # Declared values only: AC is derived in dnd5e and the fixture only
    # carries the unpersisted calc mode, so it stays unknown.
    assert display["armor_class"] is None
    assert display["speed"] == 30
    assert {row["catalog_ref"] for row in display["items"] if row["catalog_ref"]} == {
        "item:longsword", "item:chain_mail", "feat:savage_attacker",
    }


def test_srd_actor_report_structure_and_levels(srd_actor) -> None:
    report = srd_actor["report"]
    for key in (
        "source_version", "level", "issues", "unsupported", "preserved_fields",
        "unknown_fields", "missing_references", "warnings",
        "runtime_reconciliation", "contains_script",
    ):
        assert key in report, key
    assert report["source_version"] == "6.1.2"
    assert report["level"] == "saved_not_runnable"
    kinds = {entry["kind"] for entry in report["unsupported"]}
    for kind in _meta("actor_srd_dwarf_fighter.meta.json")["expected_unsupported_kinds"]:
        assert kind in kinds, kind
    assert report["contains_script"] is False
    # Rule 4 leaves a reconciliation seam filled by m2-02; this slice is empty.
    assert report["runtime_reconciliation"] == []
    assert "system.someFutureField" in report["unknown_fields"]
    assert any(
        "background +2/+1 split cannot be reconstructed" in warning
        for warning in report["warnings"]
    )
    json.dumps(srd_actor, ensure_ascii=False)  # the whole result is serializable


def test_unmapped_species_class_background_are_preserved_not_guessed() -> None:
    result = convert_actor(_load("actor_custom_halfelf_artificer.json"), locale="en")
    report = result["report"]
    missing = {(entry["kind"], entry["external_id"]) for entry in report["missing_references"]}
    assert ("species", "half-elf") in missing
    assert ("class", "artificer") in missing
    assert ("background", "Haunted One") in missing
    assert ("item", "tinkers-wrench") in missing
    draft = result["draft"]
    assert draft is not None
    assert draft["class_ref"] is None
    assert draft["species_ref"] is None
    assert draft["background_ref"] is None
    # Raw display text survives verbatim.
    assert result["display"]["species"]["raw_name"] == "Half-Elf"
    assert result["display"]["background"]["raw_identifier"] == ""
    assert result["display"]["background"]["raw_name"] == "Haunted One"
    assert result["display"]["class"][0]["raw_name"] == "Artificer"
    kinds = {entry["kind"] for entry in report["unsupported"]}
    assert {"species", "class", "background"}.issubset(kinds)


# ---- Item conversion -----------------------------------------------------


def test_srd_item_maps_to_item_catalog_ref(catalog) -> None:
    result = convert_item(_load("item_srd_longsword.json"), catalog=catalog)
    assert result["catalog_ref"] == "item:longsword"
    assert result["display"]["damage"] == {
        "formula": "1d8", "types": ["slashing"], "declared": True,
    }
    assert result["display"]["quantity"] == 1
    assert result["display"]["armor"] == {"value": None, "dex": None}


def test_custom_item_is_preserved_and_reported(catalog) -> None:
    raw = _load("item_custom_blade.json")
    result = convert_item(raw, catalog=catalog)
    assert result["catalog_ref"] is None
    assert result["extensions"]["raw"] == raw
    assert result["report"]["level"] == "saved_not_runnable"
    kinds = {entry["kind"] for entry in result["report"]["unsupported"]}
    assert {"activity", "active_effect", "unsupported"}.issubset(kinds)
    assert "item.system.customProperty" in result["report"]["unknown_fields"]


def test_activity_and_active_effect_are_never_mapped(catalog) -> None:
    raw = _load("item_custom_blade.json")
    result = convert_item(raw, catalog=catalog)
    assert "activities" not in result
    assert "effects" not in result
    # Activity content never leaks into catalog references.
    assert "activity" not in json.dumps(result["catalog_ref"])
    paths = {entry["path"] for entry in result["report"]["unsupported"]}
    assert "item.system.activities" in paths
    assert "item.effects" in paths
    assert result["extensions"]["raw"]["system"]["activities"] == raw["system"]["activities"]


def test_item_conversion_does_not_mutate_source(catalog) -> None:
    raw = _load("item_srd_longsword.json")
    snapshot = deepcopy(raw)
    convert_item(raw, catalog=catalog)
    assert raw == snapshot


# ---- Fail closed ---------------------------------------------------------


def test_malformed_actor_is_rejected() -> None:
    with pytest.raises(Dnd5eConversionError) as excinfo:
        convert_actor(_load("malformed/actor_missing_name.json"))
    assert excinfo.value.error_code == "dnd5e_actor_malformed"

    with pytest.raises(Dnd5eConversionError) as not_object:
        convert_actor(["not", "an", "actor"])
    assert not_object.value.error_code == "dnd5e_actor_malformed"


def test_non_character_actor_type_is_rejected() -> None:
    with pytest.raises(Dnd5eConversionError) as excinfo:
        convert_actor(_load("malformed/actor_npc_type.json"))
    assert excinfo.value.error_code == "dnd5e_actor_unsupported"


def test_malformed_item_is_rejected() -> None:
    with pytest.raises(Dnd5eConversionError) as excinfo:
        convert_item(_load("malformed/item_missing_type.json"))
    assert excinfo.value.error_code == "dnd5e_item_malformed"

    with pytest.raises(Dnd5eConversionError):
        convert_item({"name": "No system", "type": "weapon", "system": []})


# ---- Boundaries ----------------------------------------------------------


def test_foundry_compat_never_imports_dnd5e() -> None:
    """§3.2 rule 6: src/compat/foundry does not know dnd5e."""

    violations: list[str] = []
    for path in sorted(FOUNDRY_COMPAT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module == "src.compat.dnd5e" or module.startswith("src.compat.dnd5e."):
                    violations.append(f"{path.name} imports {module}")
    assert not violations, "\n".join(violations)


def test_conversion_module_does_not_execute_foreign_code() -> None:
    """§3.2: no script execution, no process launch, no dynamic evaluation."""

    modules_dir = Path(__file__).resolve().parents[1] / "src" / "compat" / "dnd5e"
    for path in sorted(modules_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "subprocess", f"{path.name} imports subprocess"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "subprocess", f"{path.name} imports subprocess"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec"}, f"{path.name} calls {node.func.id}"