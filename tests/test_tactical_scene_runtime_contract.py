"""B1 Scene contract, codec and module-slot boundary tests."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.module_state import (
    ModuleStateError,
    ensure_module_states,
    get_module_state,
    module_state_spec,
    set_module_state,
)
from src.engine.modules import tactical_scene
from src.tactical_scene.codec import (
    decode_tactical_scene_document,
    decode_tactical_scene_with_report,
    decode_token_document,
    encode_tactical_scene_document,
    encode_token_document,
)
from src.tactical_scene.contract import (
    TacticalSceneContractError,
    normalize_tactical_scene_payload,
    validate_tactical_scene_payload,
    validate_token_payload,
)


def _asset(asset_id: str = "asset_map") -> dict[str, str]:
    return {
        "asset_id": asset_id,
        "digest": "sha256:abc123",
        "source_path": f"assets/{asset_id}.png",
        "mime": "image/png",
    }


def _content_ref(**overrides: str) -> dict[str, str]:
    ref = {
        "source_kind": "module",
        "source_id": "example-module",
        "kind": "scene",
        "id": "scene_castle",
        "digest": "sha256:def456",
    }
    ref.update(overrides)
    return ref


def _token(**overrides):
    token = {
        "schema_version": 1,
        "kind": "token",
        "canonical_id": "token_hero",
        "actor_ref": _content_ref(kind="npc", id="hero"),
        "position": {"x": 100.0, "y": 200.0, "elevation": 5.0, "level": 0},
        "size": {"width": 1.0, "height": 1.0},
        "rotation": 45.0,
        "texture": _asset("token_texture"),
        "ring": {"enabled": True},
        "alpha": 0.9,
        "display": {"name": "Hero"},
        "disposition": 1,
        "hidden": False,
        "locked": True,
        "sight": {"enabled": True},
        "detection_modes": ["basicSight"],
        "light": {"dim": 20},
        "bars": {"bar1": {"attribute": "hp"}},
        "delta": {"conditions": []},
        "external_ids": {"foundry_v14": "tokABC"},
        "flags": {"dnd2024": {"initiative": 12}},
        "extensions": {},
        "provenance": {"import": "foundry"},
    }
    token.update(overrides)
    return token


def _scene(**overrides):
    scene = {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": "scene_castle",
        "display_name": "Castle",
        "navigation_name": "Castle",
        "source_ref": _content_ref(),
        "external_ids": {"foundry_v14": "scn123", "foundry_uuid": "Scene.scn123"},
        "ownership": {"default": 0, "gm": 3},
        "sort": 10,
        "background": _asset("background"),
        "foreground": None,
        "thumbnail": _asset("thumbnail"),
        "dimensions": {
            "width": 2400,
            "height": 1600,
            "padding": 0.25,
            "offset_x": -10,
            "offset_y": 20,
            "initial_view": {"x": 100.0, "y": 200.0, "zoom": 1.5},
        },
        "grid": {
            "type": "square",
            "size": 100,
            "scale": 1.0,
            "units": "ft",
            "color": "#000000",
            "alpha": 0.5,
            "distance": 5.0,
        },
        "environment": {
            "darkness": 0.5,
            "global_illumination": False,
            "fog": {"exploration": True, "reset_policy": "manual"},
            "weather": "rain",
        },
        "placeables": {
            "tokens": {"token_hero": _token()},
            "tiles": {},
            "walls": {},
            "lights": {},
            "sounds": {},
            "notes": {},
            "drawings": {},
            "templates": {},
            "regions": {},
        },
        "levels": [{"canonical_id": "level_ground", "elevation": 0.0}],
        "flags": {"dnd2024": {"note": "keep"}},
        "extensions": {"vendor": {"custom": True}},
        "provenance": {"import": "foundry", "version": "14.0"},
        "revision": 3,
    }
    scene.update(overrides)
    return scene


def test_valid_scene_round_trip_is_stable_and_does_not_mutate_input():
    raw = _scene()
    original = deepcopy(raw)

    document = validate_tactical_scene_payload(raw)
    assert raw == original
    assert document["canonical_id"] == "scene_castle"
    assert document["source_ref"] == _content_ref()
    assert document["external_ids"] == {"foundry_v14": "scn123", "foundry_uuid": "Scene.scn123"}
    assert document["ownership"] == {"default": 0, "gm": 3}
    assert document["background"] == _asset("background")
    assert document["dimensions"]["initial_view"] == {"x": 100.0, "y": 200.0, "zoom": 1.5}
    token = document["placeables"]["tokens"]["token_hero"]
    assert token["actor_ref"] == _content_ref(kind="npc", id="hero")
    assert token["position"] == {"x": 100.0, "y": 200.0, "elevation": 5.0, "level": 0}

    encoded = encode_tactical_scene_document(document)
    decoded = decode_tactical_scene_document(encoded)
    assert decoded == document
    assert encode_tactical_scene_document(decoded) == encoded


def test_scene_requires_canonical_id_kind_and_correct_types():
    missing_canonical = _scene()
    missing_canonical.pop("canonical_id")
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(missing_canonical)
    assert excinfo.value.reason == "missing_field"
    assert excinfo.value.code == "tactical_scene_invalid_payload"

    wrong_kind = _scene(kind="actor")
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(wrong_kind)
    assert excinfo.value.reason == "invalid_kind"

    bad_type = _scene(sort="first")
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(bad_type)
    assert excinfo.value.reason == "invalid_type"

    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(["not", "an", "object"])
    assert excinfo.value.reason == "not_an_object"


def test_unknown_fields_are_preserved_in_extensions_without_execution():
    raw = _scene()
    raw["foundry_extra"] = {"nested": [1, 2]}
    raw["macro_source"] = "console.log('never run')"
    raw["placeables"]["tokens"]["token_hero"]["future_mode"] = {"enabled": True}

    document, report = normalize_tactical_scene_payload(raw)

    assert document["extensions"]["foundry_extra"] == {"nested": [1, 2]}
    assert document["extensions"]["macro_source"] == "console.log('never run')"
    assert "foundry_extra" not in document
    token = document["placeables"]["tokens"]["token_hero"]
    assert token["extensions"]["future_mode"] == {"enabled": True}
    assert "scene.foundry_extra" in report.unknown_fields
    assert "scene.placeables.tokens.token_hero.future_mode" in report.unknown_fields

    round_tripped = decode_tactical_scene_document(encode_tactical_scene_document(document))
    assert round_tripped["extensions"]["macro_source"] == "console.log('never run')"


def test_unknown_field_conflicting_with_existing_extension_fails_closed():
    raw = _scene(extensions={"future": {"a": 1}})
    raw["future"] = {"b": 2}
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(raw)
    assert excinfo.value.reason == "extension_conflict"


def test_future_scene_schema_fails_closed():
    raw = _scene(schema_version=2)
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(raw)
    assert excinfo.value.reason == "unsupported_schema_version"
    assert excinfo.value.code == "tactical_scene_unsupported_schema"

    token = _token(schema_version=99)
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_token_payload(token)
    assert excinfo.value.reason == "unsupported_schema_version"


def test_missing_actor_and_asset_refs_are_reported_not_guessed():
    raw = _scene()
    raw["placeables"]["tokens"]["token_hero"]["actor_ref"] = None
    raw["placeables"]["tokens"]["token_hero"]["texture"] = None

    document, report = normalize_tactical_scene_payload(raw)

    token = document["placeables"]["tokens"]["token_hero"]
    assert token["actor_ref"] is None
    assert token["texture"] is None
    missing = {(ref.kind, ref.path) for ref in report.missing_references}
    assert ("actor_ref", "scene.placeables.tokens.token_hero.actor_ref") in missing
    assert ("asset_ref", "scene.placeables.tokens.token_hero.texture") in missing
    assert report.to_dict()["missing_references"]


def test_empty_scene_is_valid_and_round_trips():
    raw = {"schema_version": 1, "kind": "scene", "canonical_id": "scene_empty"}
    document = validate_tactical_scene_payload(raw)
    assert document["placeables"]["tokens"] == {}
    assert document["placeables"]["regions"] == {}
    assert document["dimensions"]["initial_view"] == {"x": 0.0, "y": 0.0, "zoom": 1.0}
    assert document["revision"] == 0
    assert decode_tactical_scene_document(encode_tactical_scene_document(document)) == document


def test_unknown_grid_type_is_saved_and_reported_as_degraded():
    document, report = normalize_tactical_scene_payload(_scene(grid={"type": "gritty_hex"}))
    assert document["grid"]["type"] == "gritty_hex"
    assert "scene.grid.type:gritty_hex" in report.degraded


def _wall(canonical_id: str = "wall_1", **overrides):
    wall = {
        "schema_version": 1,
        "kind": "wall",
        "canonical_id": canonical_id,
        "endpoints": {"x0": 0.0, "y0": 0.0, "x1": 400.0, "y1": 0.0},
        "direction": 0,
        "door": 1,
        "door_state": 2,
        "restrictions": {"move": 1, "sight": 1, "sound": 1, "light": 0},
    }
    wall.update(overrides)
    return wall


def _light(canonical_id: str = "light_1", **overrides):
    light = {
        "schema_version": 1,
        "kind": "light",
        "canonical_id": canonical_id,
        "position": {"x": 300.0, "y": 200.0},
    }
    light.update(overrides)
    return light


def _note(canonical_id: str = "note_1", **overrides):
    note = {
        "schema_version": 1,
        "kind": "note",
        "canonical_id": canonical_id,
        "position": {"x": 120.0, "y": 80.0},
    }
    note.update(overrides)
    return note


def test_typed_wall_keeps_its_fields_and_preserves_unknown_ones():
    raw = _scene()
    raw["placeables"]["walls"] = {
        "wall_1": _wall(threshold={"light": 10}, doorSound="lock"),
    }

    document, report = normalize_tactical_scene_payload(raw)

    stored = document["placeables"]["walls"]["wall_1"]
    assert stored["endpoints"] == {"x0": 0.0, "y0": 0.0, "x1": 400.0, "y1": 0.0}
    assert stored["door"] == 1
    assert stored["door_state"] == 2
    assert stored["restrictions"] == {"move": 1, "sight": 1, "sound": 1, "light": 0}
    assert stored["extensions"] == {"threshold": {"light": 10}, "doorSound": "lock"}
    assert "scene.placeables.walls.wall_1.threshold" in report.unknown_fields
    assert decode_tactical_scene_document(encode_tactical_scene_document(document)) == document


def test_typed_light_and_note_default_their_optional_fields():
    raw = _scene()
    raw["placeables"]["lights"] = {"light_1": _light()}
    raw["placeables"]["notes"] = {"note_1": _note()}

    document = validate_tactical_scene_payload(raw)

    light = document["placeables"]["lights"]["light_1"]
    assert light["rotation"] == 0.0
    assert light["radius"] == {"dim": 0.0, "bright": 0.0}
    assert light["color"] == ""
    assert light["intensity"] == 1.0
    assert light["angle"] == 360.0
    assert light["hidden"] is False
    assert light["provides_vision"] is False
    assert light["blocked_by_walls"] is False
    note = document["placeables"]["notes"]["note_1"]
    assert note["journal"] == {"entry_id": None, "page_id": None}
    assert note["texture"] is None
    assert note["visible_globally"] is False


def test_typed_placeables_require_a_canonical_id():
    for collection, sample in (("walls", _wall()), ("lights", _light()), ("notes", _note())):
        raw = _scene()
        without_id = {name: value for name, value in sample.items() if name != "canonical_id"}
        raw["placeables"][collection] = {"sample": without_id}

        with pytest.raises(TacticalSceneContractError) as excinfo:
            validate_tactical_scene_payload(raw)

        assert excinfo.value.reason == "missing_field"


@pytest.mark.parametrize(
    ("collection", "sample", "path"),
    [
        ("walls", _wall(door=9), "scene.placeables.walls.wall_1.door"),
        ("walls", _wall(direction=-1), "scene.placeables.walls.wall_1.direction"),
        ("walls", _wall(door_state=3), "scene.placeables.walls.wall_1.door_state"),
        ("walls", _wall(restrictions={"move": 4, "sight": 1}), "scene.placeables.walls.wall_1.restrictions.move"),
        ("walls", _wall(restrictions={"step": 1}), "scene.placeables.walls.wall_1.restrictions"),
        (
            "walls",
            _wall(endpoints={"x0": 0, "y0": 0, "x1": float("inf"), "y1": 0}),
            "scene.placeables.walls.wall_1.endpoints.x1",
        ),
        ("walls", _wall(endpoints={"x0": 0, "y0": 0}), "scene.placeables.walls.wall_1.endpoints.x1"),
        ("walls", _wall(kind="token"), "scene.placeables.walls.wall_1.kind"),
        ("lights", _light(intensity=1.5), "scene.placeables.lights.light_1.intensity"),
        ("lights", _light(radius={"dim": -1.0}), "scene.placeables.lights.light_1.radius.dim"),
        ("lights", _light(color="red"), "scene.placeables.lights.light_1.color"),
        ("lights", _light(angle=361.0), "scene.placeables.lights.light_1.angle"),
        ("lights", _light(rotation=400.0), "scene.placeables.lights.light_1.rotation"),
        ("lights", _light(position={"x": 1.0}), "scene.placeables.lights.light_1.position.y"),
        ("notes", _note(journal={"entry_id": ""}), "scene.placeables.notes.note_1.journal.entry_id"),
    ],
)
def test_typed_placeables_reject_values_outside_their_range(collection, sample, path):
    raw = _scene()
    raw["placeables"][collection] = {sample["canonical_id"]: sample}

    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(raw)

    assert path in excinfo.value.detail


def test_opaque_placeables_keep_their_type_specific_fields():
    raw = _scene()
    raw["placeables"]["tiles"] = {
        "tile_1": {"canonical_id": "tile_1", "x": 0, "y": 0, "door": "closed"},
    }
    document = validate_tactical_scene_payload(raw)
    assert document["placeables"]["tiles"]["tile_1"]["door"] == "closed"

    raw["placeables"]["tiles"] = {"tile_1": {"x": 0, "y": 0}}
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_tactical_scene_payload(raw)
    assert excinfo.value.reason == "missing_field"


def test_token_contract_round_trip_and_validation():
    token = _token()
    document = validate_token_payload(token)
    assert document["kind"] == "token"
    assert document["schema_version"] == 1
    encoded = encode_token_document(document)
    assert decode_token_document(encoded) == document

    missing_kind = _token()
    missing_kind.pop("kind")
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_token_payload(missing_kind)
    assert excinfo.value.reason == "missing_field"

    bad_position = _token(position={"x": "left"})
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_token_payload(bad_position)
    assert excinfo.value.reason == "invalid_type"

    bad_texture = _token(texture={"asset_id": "a", "digest": "", "source_path": "data:abc", "mime": ""})
    with pytest.raises(TacticalSceneContractError) as excinfo:
        validate_token_payload(bad_texture)
    assert excinfo.value.reason == "invalid_asset_ref"


def test_scene_slot_is_registered_and_fresh_on_instances():
    spec = module_state_spec("tactical_scene")
    assert spec.schema_version == 1

    instance = GameInstance(game_key=("web", "t", "u"))
    assert instance.modules["tactical_scene"] == tactical_scene.fresh()
    assert get_module_state(instance, "tactical_scene") is tactical_scene.slot(instance)
    assert tactical_scene.scenes(instance) == {}
    assert tactical_scene.active_scene_ref(instance) is None
    assert tactical_scene.revision(instance) == 0


def test_scene_slot_ensure_repairs_missing_and_invalid_values():
    assert tactical_scene.ensure(None) == tactical_scene.fresh()
    assert tactical_scene.ensure({"schema_version": 1, "scenes": [], "active_scene_ref": "", "revision": "x"}) == {
        "schema_version": 1,
        "scenes": {},
        "active_scene_ref": None,
        "revision": 0,
    }
    assert tactical_scene.ensure({
        "schema_version": 1,
        "scenes": {},
        "active_scene_ref": "scene_castle",
        "revision": 4,
    })["active_scene_ref"] == "scene_castle"


def test_scene_slot_set_and_unknown_schema_rejection():
    instance = GameInstance(game_key=("web", "t", "u"))
    slot = set_module_state(instance, "tactical_scene", {
        "schema_version": 1,
        "scenes": {},
        "active_scene_ref": "scene_castle",
        "revision": 2,
    })
    assert slot is instance.modules["tactical_scene"]
    assert tactical_scene.active_scene_ref(instance) == "scene_castle"
    assert tactical_scene.revision(instance) == 2

    with pytest.raises(ModuleStateError, match="unsupported tactical_scene module schema"):
        set_module_state(instance, "tactical_scene", {"schema_version": 99})
    assert instance.modules["tactical_scene"] is slot


def test_unknown_scene_slot_schema_is_preserved_verbatim():
    raw = {"schema_version": 2, "scenes": {"future": {"private": [1]}}}
    assert tactical_scene.ensure(raw) is raw
    modules = ensure_module_states({"tactical_scene": raw})
    assert modules["tactical_scene"] is raw

    instance = GameInstance(game_key=("web", "t", "u"), modules={"tactical_scene": raw})
    with pytest.raises(ModuleStateError, match="unsupported tactical_scene module schema"):
        get_module_state(instance, "tactical_scene")
    with pytest.raises(ModuleStateError):
        tactical_scene.scenes(instance)


def test_legacy_save_without_scene_slot_gets_fresh_empty_slot():
    saved = GameInstance(game_key=("web", "t", "u")).to_dict()
    saved["modules"].pop("tactical_scene")

    restored = GameInstance.from_dict(saved)

    assert restored.modules["tactical_scene"] == tactical_scene.fresh()
    assert tactical_scene.scenes(restored) == {}


def test_decode_tactical_scene_with_report_exposes_unknown_and_missing_entries():
    raw = _scene()
    raw.pop("canonical_id")  # invalid on purpose
    with pytest.raises(TacticalSceneContractError):
        decode_tactical_scene_with_report(raw)