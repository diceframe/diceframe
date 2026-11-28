"""M2-02: dnd5e Actor -> dnd2024 character creation and runtime reconciliation.

Contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §3.2 (rule 4: dnd2024 is
the only calculator after import), §5.2, §6.16 and the m2-02 task card. The
fixtures are the m2-01 samples; user-collected choices are supplied explicitly
and never defaulted by the converter.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from src.engine.participant_view import Viewer
from src.webui.services import dnd5e_import as dnd5e_service
from webapi_harness import web_api, write_world  # noqa: F401  (pytest fixtures)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dnd5e"

DND2024_RULE = {
    "rule_id": "dnd2024_srd",
    "rule_name": "5E 2024 SRD",
    "dice_system": "d20",
    "combat_model": "hp_based",
    "runtime": {"id": "core:dnd2024", "minimum_version": 1},
    "attributes": [
        {"key": key, "name": key.upper(), "min": 3, "max": 20}
        for key in ("str", "dex", "con", "int", "wis", "cha")
    ],
}

#: Choices m2-01 deliberately leaves for the user; nothing here is inferred.
COMPLETE = {
    "background_ability_bonuses": {"str": 2, "con": 1},
    "equipment_package_ref": "equipment_package:fighter_a",
    "background_equipment_package_ref": "equipment_package:soldier_a",
    "language_refs": ["language:common", "language:dwarvish", "language:giant"],
}


def _actor(name: str = "actor_srd_dwarf_fighter.json") -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def _make_dnd2024_game(api, registry, worlds_dir, *, name: str = "dnd5e_world"):
    (api._rules_dir / "dnd2024_srd.json").write_text(
        json.dumps(DND2024_RULE, ensure_ascii=False), encoding="utf-8",
    )
    write_world(worlds_dir, name, default_rule="dnd2024_srd")
    preset = api.ruleset_builder_choices(
        "dnd2024_srd", {"locale": "en"}, "en",
    )["choices"]["quick_presets"][0]
    finalized = api.ruleset_builder_finalize(
        "dnd2024_srd",
        {**preset["draft"], "locale": "en", "name": "Existing Seat"},
        "en",
    )["character"]
    created = await api.create_game(
        name, "dnd5e import", rule_id="dnd2024_srd",
        players=[finalized], language="en",
    )
    assert created["ok"] is True, created
    instance = registry.get(api._parse_key(created["game_key"]))
    return created["game_key"], instance


def _gm(instance) -> Viewer:
    return Viewer("gm", str(instance.gm_uid or ""))


def _deps(api) -> dnd5e_service.Dnd5eImportDependencies:
    return dnd5e_service.Dnd5eImportDependencies(
        get_instance=api.get_game_instance,
        create_player=api.create_player,
        builder_choices=api.ruleset_builder_choices,
        builder_validate=api.ruleset_builder_validate,
        builder_finalize=api.ruleset_builder_finalize,
    )


def _preview(api, game_key, viewer, actor, selections, locale="en"):
    return dnd5e_service.preview(
        _deps(api), game_key, viewer, actor, selections=selections, locale=locale,
    )


async def _commit(api, game_key, viewer, actor, selections, locale="en"):
    return await dnd5e_service.commit(
        _deps(api), game_key, viewer, actor, selections=selections, locale=locale,
    )


# ---- preview -------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_srd_actor_previews_valid_character_without_creating(
    web_api,
) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)
    before = len(instance.players)

    result = _preview(api, game_key, _gm(instance), _actor(), COMPLETE)

    assert result["ok"] is True, result
    assert result["valid"] is True, result
    assert result["missing"] == []
    assert result["errors"] == []
    draft = result["draft"]
    assert draft["class_ref"] == "class:fighter"
    assert draft["species_ref"] == "species:dwarf"
    assert draft["background_ref"] == "background:soldier"
    # The converter's declared scores stay the base; user bonuses are added by
    # dnd2024, and the report says so instead of rewriting the declaration.
    assert draft["base_abilities"]["str"] == 17
    assert draft["background_ability_bonuses"] == {"str": 2, "con": 1}
    assert draft["language_refs"] == [
        "language:common", "language:dwarvish", "language:giant",
    ]
    canonical = result["character"]["ruleset_character"]
    assert canonical["abilities"]["str"] == 19
    assert canonical["abilities"]["con"] == 17
    assert canonical["resources"]["max_hp"] == 14
    assert len(instance.players) == before  # preview never creates a seat


@pytest.mark.asyncio
async def test_missing_user_choices_are_reported_and_never_guessed(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)

    result = _preview(api, game_key, _gm(instance), _actor(), {})

    assert result["ok"] is True
    assert result["valid"] is False
    assert "character" not in result
    fields = {entry["field"] for entry in result["missing"]}
    assert {
        "background_ability_bonuses",
        "equipment_package_ref",
        "background_equipment_package_ref",
        "language_refs",
    } <= fields
    assert result["draft"]["background_ability_bonuses"] == {}
    assert result["draft"]["equipment_package_ref"] is None
    assert len(result["draft"]["language_refs"]) == 2


@pytest.mark.asyncio
async def test_level_above_one_fails_closed_without_progression(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)
    actor = deepcopy(_actor())
    class_item = next(item for item in actor["items"] if item.get("type") == "class")
    class_item["system"]["levels"] = 2

    preview = _preview(api, game_key, _gm(instance), actor, COMPLETE)

    assert preview["ok"] is True
    assert preview["valid"] is False
    fields = {entry["field"] for entry in preview["missing"]}
    assert "progression.history" in fields
    assert preview["draft"]["level"] == 2
    assert any("level 1" in error for error in preview["errors"])

    before = len(instance.players)
    committed = await _commit(api, game_key, _gm(instance), actor, COMPLETE)
    assert committed["ok"] is False
    assert committed["error_code"] == "dnd5e_level_requires_progression"
    assert len(instance.players) == before


# ---- commit --------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_creates_character_and_dnd2024_is_the_only_calculator(
    web_api,
) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)
    before = set(instance.players)

    result = await _commit(api, game_key, _gm(instance), _actor(), COMPLETE)

    assert result["ok"] is True, result
    uid = result["created"]["user_id"]
    assert uid in instance.players
    assert set(instance.players) - before == {uid}
    assert result["created"]["character_name"] == "Dain Ironfoot"

    sheet = instance.get_character_sheet(uid)
    canonical = sheet["ruleset_character"]
    # Persisted mechanics come from dnd2024, not from the dnd5e declaration.
    assert sheet["max_hp"] == canonical["resources"]["max_hp"] == 14
    assert sheet["hp"] == canonical["resources"]["hp"] == 14
    assert sheet["armor_class"] == canonical["derived"]["armor_class"]
    assert sheet["attributes"] == canonical["abilities"]
    assert sheet["attributes"]["str"] == 19
    # The dnd5e declaration is still available only as reference in the report.
    declared = {
        entry["field"]: entry for entry in result["report"]["runtime_reconciliation"]
    }
    assert declared["hp.max"]["declared"] == 13
    assert declared["hp.max"]["recomputed"] == 14


@pytest.mark.asyncio
async def test_commit_refuses_incomplete_draft_and_creates_no_seat(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)
    before = len(instance.players)

    result = await _commit(api, game_key, _gm(instance), _actor(), {})

    assert result["ok"] is False
    assert result["error_code"] == "dnd5e_creation_incomplete"
    assert {entry["field"] for entry in result["missing"]} >= {
        "background_ability_bonuses", "language_refs",
    }
    assert len(instance.players) == before


# ---- reconciliation ------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_reports_declared_vs_recomputed_differences(
    web_api,
) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)

    result = _preview(api, game_key, _gm(instance), _actor(), COMPLETE)

    entries = {
        entry["field"]: entry for entry in result["report"]["runtime_reconciliation"]
    }
    assert set(entries) == {
        "abilities.str", "abilities.con", "hp.max", "hp.value",
    }
    assert entries["abilities.str"]["declared"] == 17
    assert entries["abilities.str"]["recomputed"] == 19
    assert entries["abilities.con"]["declared"] == 16
    assert entries["abilities.con"]["recomputed"] == 17
    assert entries["hp.max"]["declared"] == 13
    assert entries["hp.max"]["recomputed"] == 14
    # The preview's own display still reports the untouched declaration.
    assert result["display"]["hp"]["max"] == 13
    assert result["display"]["abilities"]["str"]["score"] == 17
    json.dumps(result, ensure_ascii=False)


# ---- permissions and boundaries ------------------------------------------


@pytest.mark.asyncio
async def test_non_gm_cannot_preview_or_commit(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)
    seat = Viewer("seat", next(iter(instance.players)))
    before = len(instance.players)

    preview = _preview(api, game_key, seat, _actor(), COMPLETE)
    committed = await _commit(api, game_key, seat, _actor(), COMPLETE)

    assert preview["ok"] is False
    assert preview["error_code"] == "dnd5e_import_forbidden"
    assert committed["ok"] is False
    assert committed["error_code"] == "dnd5e_import_forbidden"
    assert len(instance.players) == before


@pytest.mark.asyncio
async def test_non_dnd2024_game_is_rejected(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    created = await api.create_game(
        "template_world", "freeform import", players=[{"character_name": "A"}],
    )
    assert created["ok"] is True
    instance = registry.get(api._parse_key(created["game_key"]))

    result = _preview(api, created["game_key"], _gm(instance), _actor(), COMPLETE)

    assert result["ok"] is False
    assert result["error_code"] == "dnd5e_ruleset_required"


@pytest.mark.asyncio
async def test_unknown_selection_field_is_rejected(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)

    result = _preview(api, game_key, _gm(instance), _actor(), {**COMPLETE, "speed": 40})

    assert result["ok"] is False
    assert result["error_code"] == "dnd5e_selection_invalid"


@pytest.mark.asyncio
async def test_actor_with_unmapped_class_species_background_fails_closed(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    game_key, instance = await _make_dnd2024_game(api, registry, worlds_dir)

    result = _preview(api, game_key, _gm(instance), _actor("actor_custom_halfelf_artificer.json"), {})

    # The custom actor maps to no dnd2024 class/species/background; the draft
    # candidates stay empty and the preview never guesses a replacement.
    assert result["ok"] is True
    assert result["valid"] is False
    fields = {entry["field"] for entry in result["missing"]}
    assert {"class_ref", "species_ref", "background_ref"} <= fields
    assert "character" not in result