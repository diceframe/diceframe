"""B3 FVTT scene adapter: import, export, round-trip and fail-closed tests."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from src.compat.foundry import (
    CODE_SCENE_MALFORMED,
    CODE_VERSION_UNSUPPORTED,
    NAMESPACE_V13,
    NAMESPACE_V14,
    FoundrySceneError,
    export_scene,
    import_scene,
)
from src.compat.foundry.report import ConversionLevel
from src.content_modules.adapters.foundry_scene import (
    PORTABLE_SCENE_SCHEMA,
    detect_scene_version,
    portable_scene,
    scene_definition,
    scene_entity,
)
from src.tactical_scene.contract import validate_tactical_scene_payload

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "foundry"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_cases() -> list[Any]:
    cases = []
    for folder in ("v14/scene", "v13/scene", "unsupported", "malformed"):
        for meta_path in sorted((FIXTURES / folder).glob("*.meta.json")):
            fixture_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".json"))
            cases.append(
                pytest.param(
                    fixture_path,
                    _load_json(meta_path),
                    id=f"{folder}/{fixture_path.stem}",
                )
            )
    return cases


@pytest.fixture(scope="module")
def core_v14() -> tuple[dict[str, Any], Any, Any]:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    document, report = import_scene(raw, source_version="v14")
    return raw, document, report


# ---- fixture-driven contract ---------------------------------------------


@pytest.mark.parametrize(("fixture_path", "meta"), _fixture_cases())
def test_fixture_matches_recorded_expectations(
    fixture_path: Path, meta: dict[str, Any],
) -> None:
    raw = _load_json(fixture_path)
    expected_error = meta["expected_error"]
    if expected_error is not None:
        with pytest.raises(FoundrySceneError) as excinfo:
            import_scene(raw, source_version=meta["source_version"])
        assert excinfo.value.code == expected_error
        return

    document, report = import_scene(raw, source_version=meta["source_version"])
    assert meta["source"] == "synthetic-from-api-schema"
    assert report.level.value == meta["expected_level"]
    issue_levels = {issue.level.value for issue in report.issues}
    for level in meta["expected_issue_levels"]:
        assert level in issue_levels
    for expected in meta["expected_warnings"]:
        assert any(expected in warning for warning in report.warnings), expected
    for unknown in meta["expected_unknown_fields"]:
        assert unknown in report.unknown_fields
    for field in meta["preserved_fields"]:
        assert field in report.preserved_fields

    validate_tactical_scene_payload(document)
    assert document["canonical_id"].startswith("fvtt.")
    json.dumps(report.to_dict())


# ---- v14 round-trip -------------------------------------------------------


def test_v14_import_export_import_is_stable(core_v14: tuple[Any, Any, Any]) -> None:
    raw, document, _report = core_v14
    exported = export_scene(document)
    json.dumps(exported)

    assert exported["_id"] == raw["_id"]
    assert exported["name"] == "Ruined Keep"
    assert exported["environment"]["darknessLevel"] == 0.35
    assert exported["grid"]["type"] == 1
    assert exported["grid"]["style"] == "solidLines"
    assert exported["levels"][0]["background"]["src"] == "worlds/test/ruined-keep.webp"
    assert exported["tokens"][0]["actorId"] == "actor00000000001"
    assert exported["tokens"][0]["detectionModes"] == {
        "basicSight": {"enabled": True, "range": 60}
    }
    assert exported["someFutureField"] == {"nested": [1, 2, 3]}

    reimported, _reimport_report = import_scene(exported, source_version="v14")
    assert reimported == document
    assert export_scene(reimported) == exported


def test_scene_identity_is_deterministic_and_not_the_display_name(
    core_v14: tuple[Any, Any, Any],
) -> None:
    raw, document, _report = core_v14
    again, _ = import_scene(deepcopy(raw), source_version="v14")
    assert again["canonical_id"] == document["canonical_id"]
    assert "ruined" not in document["canonical_id"].lower()
    assert document["external_ids"] == {NAMESPACE_V14: raw["_id"]}


def test_flags_and_unknown_fields_are_preserved() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    document, report = import_scene(source, source_version="v14")
    assert document["flags"] == source["flags"]
    assert document["extensions"]["someFutureField"] == source["someFutureField"]
    assert document["extensions"]["_stats"] == source["_stats"]
    assert "scene.someFutureField" in report.unknown_fields

    exported = export_scene(document)
    assert exported["flags"] == source["flags"]
    assert exported["active"] is False
    assert exported["navigation"] is True
    assert exported["_stats"] == source["_stats"]


def test_missing_actor_and_asset_references_are_reported_not_guessed() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    document, report = import_scene(source, source_version="v14")

    actor_refs = [ref for ref in report.missing_references if ref["kind"] == "actor_ref"]
    assert any(ref["external_id"] == "actor00000000001" for ref in actor_refs)
    level_refs = [ref for ref in report.missing_references if ref["kind"] == "level_ref"]
    assert any(ref["external_id"] == "lvl0000000000001" for ref in level_refs)

    token = next(iter(document["placeables"]["tokens"].values()))
    assert token["actor_ref"]["source_kind"] == "foundry"
    assert token["actor_ref"]["id"] == "actor00000000001"
    assert token["texture"]["source_path"] == "worlds/test/goblin.webp"
    assert token["texture"]["digest"] == ""
    assert document["background"]["source_path"] == "worlds/test/ruined-keep.webp"


def test_token_level_and_media_residuals_round_trip() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    document, _ = import_scene(source, source_version="v14")
    token = next(iter(document["placeables"]["tokens"].values()))
    assert token["position"]["level"] == 0
    assert token["extensions"]["level"] == "lvl0000000000001"
    assert token["extensions"]["texture"] == {"tint": "#ffffff", "scaleX": 1, "scaleY": 1}

    exported = export_scene(document)
    token_out = exported["tokens"][0]
    assert token_out["level"] == "lvl0000000000001"
    assert token_out["texture"] == {
        "src": "worlds/test/goblin.webp",
        "tint": "#ffffff",
        "scaleX": 1,
        "scaleY": 1,
    }


# ---- version handling -----------------------------------------------------


def test_unknown_declared_version_fails_closed() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    with pytest.raises(FoundrySceneError) as excinfo:
        import_scene(source, source_version="v15")
    assert excinfo.value.code == CODE_VERSION_UNSUPPORTED


def test_declared_and_detected_version_mismatch_fails_closed() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    with pytest.raises(FoundrySceneError) as excinfo:
        import_scene(source, source_version="v13")
    assert excinfo.value.code == CODE_VERSION_UNSUPPORTED


def test_missing_or_unsupported_core_version_without_stats_is_allowed() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    source.pop("_stats")
    document, report = import_scene(source, source_version="v14")
    assert document["provenance"]["core_version"] == ""
    assert report.level.value == "missing_external_ref"


def test_v13_payload_with_v14_levels_is_future_structure() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    source["_stats"] = {"coreVersion": "13.350"}
    document, report = import_scene(source, source_version="v13")
    issue_levels = {issue.level.value for issue in report.issues}
    assert "unknown_future_structure" in issue_levels
    assert "initialLevel" not in document["external_ids"]


# ---- malformed and security ----------------------------------------------


def test_malformed_payloads_are_rejected() -> None:
    with pytest.raises(FoundrySceneError) as excinfo:
        import_scene([], source_version="v14")  # type: ignore[arg-type]
    assert excinfo.value.code == CODE_SCENE_MALFORMED

    source = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    source["_id"] = ""
    with pytest.raises(FoundrySceneError) as excinfo:
        import_scene(source, source_version="v14")
    assert excinfo.value.code == CODE_SCENE_MALFORMED


def test_exporting_an_invalid_scene_document_fails_closed() -> None:
    with pytest.raises(FoundrySceneError) as excinfo:
        export_scene({"kind": "scene"})
    assert excinfo.value.code == CODE_SCENE_MALFORMED


def test_inline_data_uri_assets_are_rejected_not_inlined() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    source["levels"][0]["background"]["src"] = "data:image/webp;base64,AAAA"
    document, report = import_scene(source, source_version="v14")
    assert document["background"] is None
    assert any(
        ref["kind"] == "asset_ref" and ref["external_id"] == "<inline-data>"
        for ref in report.missing_references
    )
    assert "data:" not in json.dumps({k: v for k, v in document.items() if k != "extensions"})


def test_script_content_is_flagged_and_preserved_verbatim() -> None:
    source = _load_json(FIXTURES / "unsupported" / "scene_script_flags.json")
    document, report = import_scene(source, source_version="v14")
    assert report.contains_script is True
    assert report.level.value == "contains_script"
    assert document["flags"]["world"]["macros"]["combatLoop"]["command"] == (
        "game.combat.nextRound();"
    )
    token = next(iter(document["placeables"]["tokens"].values()))
    assert token["flags"]["module-a"]["script"].startswith("TODO:")

    exported = export_scene(document)
    assert exported["flags"]["world"]["macros"]["combatLoop"]["command"] == (
        "game.combat.nextRound();"
    )


def test_unknown_grid_type_is_saved_and_degraded() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    source["grid"]["type"] = "triangular"
    document, report = import_scene(source, source_version="v14")
    assert document["grid"]["type"] == "triangular"
    assert report.level.value == "degraded_display"
    assert any(
        issue.level.value == "degraded_display" and issue.path == "scene.grid.type"
        for issue in report.issues
    )


def test_import_does_not_mutate_the_source_payload() -> None:
    source = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    snapshot = deepcopy(source)
    import_scene(source, source_version="v14")
    assert source == snapshot


# ---- v13 import compatibility -------------------------------------------


def test_v13_scene_uses_legacy_shapes_and_reports_degradation() -> None:
    raw = _load_json(FIXTURES / "v13" / "scene" / "scene_v13_legacy.json")
    document, report = import_scene(raw, source_version="v13")

    assert document["external_ids"] == {NAMESPACE_V13: raw["_id"]}
    assert document["background"]["source_path"] == "worlds/legacy/crypt.webp"
    assert document["foreground"]["source_path"] == "worlds/legacy/crypt-fg.webp"
    assert document["environment"]["fog"]["exploration"] is True
    assert document["environment"]["global_illumination"] is True
    assert document["dimensions"]["initial_view"]["zoom"] == 0.75
    assert document["grid"]["units"] == "ft"

    issue_levels = {issue.level.value for issue in report.issues}
    assert "degraded_display" in issue_levels
    assert "saved_not_runnable" in issue_levels
    assert document["placeables"]["templates"]
    template = next(iter(document["placeables"]["templates"].values()))
    assert template["t"] == "circle"
    assert template["external_ids"] == {NAMESPACE_V13: "tpl0000000000001"}

    assert document["extensions"]["foregroundElevation"] == 5
    assert document["extensions"]["background"]["tint"] == "#ffffff"


def test_v13_scene_exports_without_levels_using_documented_top_level_media() -> None:
    raw = _load_json(FIXTURES / "v13" / "scene" / "scene_v13_legacy.json")
    document, _ = import_scene(raw, source_version="v13")
    exported = export_scene(document)
    assert "levels" not in exported
    assert exported["background"]["src"] == "worlds/legacy/crypt.webp"
    assert exported["background"]["tint"] == "#ffffff"
    assert exported["foreground"]["src"] == "worlds/legacy/crypt-fg.webp"
    assert "exploration" not in exported["fog"]


# ---- typed placeables: wall, light, note -----------------------------------


def _typed_placeables_fixture(version: str) -> tuple[Any, Any, Any]:
    name = "scene_placeables.json" if version == "v14" else "scene_v13_placeables.json"
    raw = _load_json(FIXTURES / version / "scene" / name)
    document, report = import_scene(raw, source_version=version)
    return raw, document, report


def _by_external_id(placeables: dict[str, Any], external_id: str) -> dict[str, Any]:
    return next(
        item for item in placeables.values()
        if external_id in item["external_ids"].values()
    )


def _without_empty_flags(item: dict[str, Any]) -> dict[str, Any]:
    """The export omits ``flags`` when the source carried an empty object."""

    return {key: value for key, value in item.items() if key != "flags" or value}


def test_typed_placeables_use_diceframe_names_and_keep_the_rest_as_residual() -> None:
    _raw, document, report = _typed_placeables_fixture("v14")
    placeables = document["placeables"]

    wall = _by_external_id(placeables["walls"], "wallChapel00001")
    assert wall["kind"] == "wall"
    assert wall["endpoints"] == {"x0": 0.0, "y0": 0.0, "x1": 1200.0, "y1": 0.0}
    assert wall["door"] == 0
    assert wall["restrictions"] == {"move": 1, "sight": 1, "sound": 1, "light": 1}
    assert wall["extensions"] == {
        "threshold": {"light": 10, "sight": 10, "sound": 10, "attenuation": False},
    }

    secret = _by_external_id(placeables["walls"], "wallChapel00002")
    assert secret["door"] == 2
    assert secret["door_state"] == 2
    assert set(secret["extensions"]) == {"doorSound", "threshold"}

    light = _by_external_id(placeables["lights"], "lightChapel0001")
    assert light["kind"] == "light"
    assert light["position"] == {"x": 300.0, "y": 450.0}
    assert light["radius"] == {"dim": 40.0, "bright": 20.0}
    assert light["intensity"] == 0.6
    assert light["angle"] == 360.0
    assert light["color"] == "#ffaa00"
    assert light["hidden"] is False
    assert light["blocked_by_walls"] is True
    assert set(light["extensions"]["config"]) == {
        "animation", "darkness", "priority", "attenuation", "luminosity",
        "saturation", "contrast", "shadows", "coloration", "negative",
    }
    assert light["extensions"]["name"] == "brazier"

    assert _by_external_id(placeables["lights"], "lightChapel0002")["hidden"] is True

    note = _by_external_id(placeables["notes"], "noteChapel0001")
    assert note["kind"] == "note"
    assert note["journal"] == {"entry_id": "JE0000000000001", "page_id": "JP0000000000001"}
    assert note["text"] == "Altar"
    assert note["font_size"] == 24.0
    assert note["icon_size"] == 40.0
    assert note["visible_globally"] is True
    assert note["texture"]["source_path"] == "worlds/test/icons/altar.svg"
    assert set(note["extensions"]) == {"fontFamily", "textAnchor", "sort", "texture"}

    # FVTT field names never reach the typed documents.
    for collection in ("walls", "lights", "notes"):
        for typed in placeables[collection].values():
            for key in ("c", "ds", "dir", "entryId", "pageId", "textColor", "config", "global"):
                assert key not in typed

    # The journal entry is not part of a scene import: reported, never resolved.
    assert {
        "kind": "journal_ref", "path": "scene.notes[0].entryId",
        "external_id": "JE0000000000001",
    } in report.missing_references
    assert report.contains_script is False
    assert report.level is ConversionLevel.DEGRADED_DISPLAY


def test_unknown_wall_vocabulary_is_preserved_and_never_interpreted() -> None:
    _raw, document, report = _typed_placeables_fixture("v14")

    wall = _by_external_id(document["placeables"]["walls"], "wallChapel00003")

    assert wall["door"] == 0
    assert wall["restrictions"]["move"] == 0
    assert wall["extensions"] == {"door": 7, "move": "weird"}
    assert wall["flags"] == {"wall-editor": {"snap": True}}
    degraded = {
        issue.path for issue in report.issues
        if issue.level is ConversionLevel.DEGRADED_DISPLAY
    }
    assert degraded == {"scene.walls[2].door", "scene.walls[2].move"}


def test_typed_placeables_round_trip_through_export() -> None:
    raw, document, _report = _typed_placeables_fixture("v14")

    exported = export_scene(document)

    # A placeable whose source carried every mapped field is reproduced exactly.
    assert exported["walls"][0] == _without_empty_flags(raw["walls"][0])
    assert exported["walls"][1] == _without_empty_flags(raw["walls"][1])
    assert exported["lights"][0] == _without_empty_flags(raw["lights"][0])
    assert exported["lights"][1] == _without_empty_flags(raw["lights"][1])
    assert exported["notes"][0] == _without_empty_flags(raw["notes"][0])
    # Unknown vocabulary comes back verbatim instead of the neutral typed value.
    assert exported["walls"][2]["door"] == 7
    assert exported["walls"][2]["move"] == "weird"

    reimported, _ = import_scene(exported, source_version="v14")
    assert reimported == document
    assert export_scene(reimported) == exported


def test_v13_typed_placeables_use_the_same_typed_shapes() -> None:
    _raw, document, _report = _typed_placeables_fixture("v13")
    placeables = document["placeables"]

    assert _by_external_id(placeables["walls"], "wallChapel00002")["door"] == 2
    light = _by_external_id(placeables["lights"], "lightChapel0001")
    assert light["radius"] == {"dim": 40.0, "bright": 20.0}
    # A v13 light has no name; nothing is invented for it.
    assert "name" not in light["extensions"]
    assert _by_external_id(placeables["notes"], "noteChapel0001")["journal"] == {
        "entry_id": "JE0000000000001", "page_id": "JP0000000000001",
    }
    assert all(NAMESPACE_V13 in item["external_ids"] for item in placeables["walls"].values())


def test_clamped_light_numbers_keep_their_source_values_for_export() -> None:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    raw["lights"] = [
        {"_id": "lightOutRange001", "x": 1, "y": 2, "config": {"dim": -5, "alpha": 1.7, "color": "red"}},
    ]

    document, _report = import_scene(raw, source_version="v14")

    light = _by_external_id(document["placeables"]["lights"], "lightOutRange001")
    assert light["radius"] == {"dim": 0.0, "bright": 0.0}
    assert light["intensity"] == 1.0
    assert light["color"] == ""
    assert light["extensions"]["config"] == {"color": "red", "dim": -5, "alpha": 1.7}

    exported = export_scene(document)
    assert exported["lights"][0]["config"] == {
        "color": "red", "dim": -5, "alpha": 1.7, "bright": 0.0, "angle": 360.0,
    }
    reimported, _ = import_scene(exported, source_version="v14")
    assert reimported == document


def test_a_wall_with_a_malformed_line_is_rejected() -> None:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    for coordinates in ([0, 0, 100], [0, 0, "x", 100], "0,0,100,100"):
        raw["walls"] = [{"_id": "wallBadLine00001", "c": coordinates, "flags": {}}]
        with pytest.raises(FoundrySceneError) as excinfo:
            import_scene(raw, source_version="v14")
        assert excinfo.value.code == CODE_SCENE_MALFORMED
        assert "scene.walls[0].c" in str(excinfo.value)


def test_scene_without_environment_does_not_gain_one_on_export() -> None:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    del raw["environment"]

    document, _report = import_scene(raw, source_version="v14")
    exported = export_scene(document)

    assert "environment" not in exported
    reimported, _ = import_scene(exported, source_version="v14")
    assert reimported == document

# ---- portable scene definition (workplan §6.17 m3-02/m3-03) ---------------


def test_portable_scene_is_the_adapter_document_with_converted_placeables() -> None:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_core.json")
    document, report = portable_scene(raw)
    expected, _ = import_scene(raw, source_version="v14")

    assert report.source_version == "v14"
    assert document == expected
    validate_tactical_scene_payload(document)
    # Tokens come from the adapter; nothing is invented for empty collections.
    assert len(document["placeables"]["tokens"]) == len(raw["tokens"])
    assert document["placeables"]["walls"] == {}
    assert scene_definition(document) == {
        "spec": PORTABLE_SCENE_SCHEMA, "tactical_scene": document,
    }


def test_portable_scene_requires_a_declared_core_version() -> None:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    raw.pop("_stats", None)

    assert detect_scene_version(raw) is None
    with pytest.raises(ValueError):
        portable_scene(raw)


def test_portable_scene_version_comes_from_stats_and_is_never_guessed() -> None:
    assert detect_scene_version({"_stats": {"coreVersion": "14.370"}}) == "v14"
    assert detect_scene_version({"_stats": {"coreVersion": "13.350"}}) == "v13"
    assert detect_scene_version({}, {"_stats": {"coreVersion": "v14.1"}}) == "v14"
    assert detect_scene_version({"name": "no stats"}) is None


def test_portable_scene_rejects_malformed_rows() -> None:
    with pytest.raises(ValueError):
        portable_scene(["not", "an", "object"])
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    raw["_id"] = ""
    with pytest.raises(FoundrySceneError) as excinfo:
        portable_scene(raw)
    assert excinfo.value.code == CODE_SCENE_MALFORMED


def test_portable_scene_scrubs_credentials_and_rejects_unsafe_assets() -> None:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    raw["apiKey"] = "sk-should-not-travel"
    document, report = portable_scene(raw)
    assert "apiKey" not in document["extensions"]
    assert any("credential-like" in warning for warning in report.warnings)

    unsafe = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    unsafe["levels"][0]["background"]["src"] = "https://example.com/map.webp"
    with pytest.raises(ValueError):
        portable_scene(unsafe)


def test_portable_scene_keeps_script_content_inert() -> None:
    raw = _load_json(FIXTURES / "unsupported" / "scene_script_flags.json")
    document, report = portable_scene(raw)

    assert report.contains_script is True
    assert document["flags"]["world"]["macros"]["combatLoop"]["command"] == (
        "game.combat.nextRound();"
    )


def test_scene_entity_is_an_adventure_entity_envelope() -> None:
    raw = _load_json(FIXTURES / "v14" / "scene" / "scene_minimal.json")
    document, _report = portable_scene(raw)

    entity = scene_entity(
        document, entity_id="scene_abc123", source_ref="user:import_1", name="Plaza",
    )

    assert entity["schema_version"] == 1
    assert entity["kind"] == "scene" and entity["id"] == "scene_abc123"
    assert entity["source_ref"] == "user:import_1"
    assert entity["automation_level"] == "reference"
    assert entity["name"] == "Plaza"
    assert entity["spec"] == PORTABLE_SCENE_SCHEMA
    assert entity["tactical_scene"] == document
