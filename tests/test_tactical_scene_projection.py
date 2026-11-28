"""B2 Scene read projection whitelist tests."""

from __future__ import annotations

from copy import deepcopy

from src.tactical_scene.contract import validate_tactical_scene_payload
from src.tactical_scene.projection import (
    LIGHT_PLAYER_FIELDS,
    TACTICAL_SCENE_PLAYER_EXCLUDED_FIELDS,
    TACTICAL_SCENE_PLAYER_FIELDS,
    TOKEN_PLAYER_EXCLUDED_FIELDS,
    TOKEN_PLAYER_FIELDS,
    WALL_PLAYER_FIELDS,
    actor_ref_key,
    project_tactical_scene_gm,
    project_tactical_scene_player,
)


def _asset(asset_id: str) -> dict[str, str]:
    return {
        "asset_id": asset_id,
        "digest": "sha256:abc123",
        "source_path": f"assets/{asset_id}.png",
        "mime": "image/png",
    }


def _actor_ref(actor_id: str) -> dict[str, str]:
    return {
        "source_kind": "module",
        "source_id": "example-module",
        "kind": "npc",
        "id": actor_id,
        "digest": "sha256:def456",
    }


def _token(canonical_id: str, *, actor_ref=None, hidden=False, disposition=1):
    return {
        "schema_version": 1,
        "kind": "token",
        "canonical_id": canonical_id,
        "actor_ref": actor_ref,
        "position": {"x": 100.0, "y": 200.0, "elevation": 0.0, "level": 0},
        "size": {"width": 1.0, "height": 1.0},
        "rotation": 0.0,
        "texture": _asset(f"texture_{canonical_id}"),
        "ring": {"enabled": True, "private": "gm"},
        "alpha": 1.0,
        "display": {"name": canonical_id},
        "disposition": disposition,
        "hidden": hidden,
        "locked": False,
        "sight": {"enabled": True},
        "detection_modes": ["basicSight"],
        "light": {"dim": 20},
        "bars": {"bar1": {"attribute": "hp"}},
        "delta": {"conditions": ["secret"]},
        "external_ids": {"foundry_v14": f"tok_{canonical_id}"},
        "flags": {"dnd2024": {"initiative": 12}},
        "extensions": {"private": {"a": 1}},
        "provenance": {"import": "foundry"},
    }


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


def _scene_payload() -> dict:
    return {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": "scene_castle",
        "display_name": "Castle",
        "navigation_name": "Castle",
        "source_ref": {**_actor_ref("scene"), "kind": "scene"},
        "external_ids": {"foundry_v14": "scn123"},
        "ownership": {"default": 0, "gm": 3},
        "sort": 10,
        "background": _asset("background"),
        "foreground": None,
        "thumbnail": _asset("thumbnail"),
        "dimensions": {
            "width": 2400,
            "height": 1600,
            "padding": 0.25,
            "offset_x": 0,
            "offset_y": 0,
            "initial_view": {"x": 0.0, "y": 0.0, "zoom": 1.0},
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
            "tokens": {
                "token_hero": _token("token_hero", actor_ref=_actor_ref("hero")),
                "token_hidden": _token("token_hidden", hidden=True),
                "token_secret": _token(
                    "token_secret", actor_ref=_actor_ref("villain"), disposition=-2,
                ),
                "token_owned_secret": _token(
                    "token_owned_secret", actor_ref=_actor_ref("owned"), disposition=-2,
                ),
            },
            "tiles": {"tile_1": {"canonical_id": "tile_1", "private": "gm"}},
            "walls": {
                "wall_1": _wall("wall_1", extensions={"threshold": {"light": 10}}),
                "wall_2": _wall("wall_2", flags={"gm": {"secret": True}}),
            },
            "lights": {
                "light_1": _light("light_1", flags={"gm": {"note": "x"}}, extensions={"name": "brazier"}),
                "light_hidden": _light("light_hidden", hidden=True),
            },
            "sounds": {},
            "notes": {"note_1": _note("note_1", text="gm note")},
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


def test_gm_projection_is_the_complete_document():
    document = validate_tactical_scene_payload(_scene_payload())

    projection = project_tactical_scene_gm(document)

    assert projection == document
    assert projection["ownership"] == {"default": 0, "gm": 3}
    assert projection["flags"] == {"dnd2024": {"note": "keep"}}
    assert projection["placeables"]["tokens"]["token_hidden"]["hidden"] is True
    assert projection["placeables"]["walls"]["wall_1"]["door"] == 1
    assert projection["placeables"]["walls"]["wall_1"]["extensions"] == {"threshold": {"light": 10}}
    assert projection["placeables"]["lights"]["light_hidden"]["hidden"] is True
    assert projection["placeables"]["notes"]["note_1"]["text"] == "gm note"


def test_seat_projection_uses_only_the_frozen_whitelist():
    document = validate_tactical_scene_payload(_scene_payload())

    projection = project_tactical_scene_player(document, uid="p1", owned_actor_refs=set())

    assert set(projection) == set(TACTICAL_SCENE_PLAYER_FIELDS) | {"placeables"}
    for field in TACTICAL_SCENE_PLAYER_EXCLUDED_FIELDS:
        assert field not in projection
    assert projection["display_name"] == "Castle"
    assert projection["revision"] == 3
    assert projection["levels"] == [{"canonical_id": "level_ground", "elevation": 0.0}]


def test_seat_projection_keeps_typed_placeables_and_drops_hidden_ones():
    document = validate_tactical_scene_payload(_scene_payload())

    projection = project_tactical_scene_player(document, uid="p1", owned_actor_refs=set())

    assert set(projection["placeables"]) == {"tokens", "walls", "lights"}
    tokens = projection["placeables"]["tokens"]
    assert "token_hidden" not in tokens
    assert set(tokens) == {"token_hero", "token_secret", "token_owned_secret"}


def test_seat_gets_walls_and_visible_lights_but_never_notes():
    document = validate_tactical_scene_payload(_scene_payload())

    placeables = project_tactical_scene_player(document, uid="p1", owned_actor_refs=set())["placeables"]

    assert set(placeables["walls"]) == {"wall_1", "wall_2"}
    assert placeables["walls"]["wall_1"] == {
        "canonical_id": "wall_1",
        "endpoints": {"x0": 0.0, "y0": 0.0, "x1": 400.0, "y1": 0.0},
        "direction": 0,
        "door": 1,
        "door_state": 2,
        "restrictions": {"move": 1, "sight": 1, "sound": 1, "light": 0},
    }
    for wall in placeables["walls"].values():
        assert set(wall) == set(WALL_PLAYER_FIELDS)
    assert set(placeables["lights"]) == {"light_1"}
    assert set(placeables["lights"]["light_1"]) == set(LIGHT_PLAYER_FIELDS)
    for light in placeables["lights"].values():
        assert "flags" not in light
        assert "extensions" not in light
    assert "notes" not in placeables
    assert "tiles" not in placeables


def test_seat_token_carries_only_the_frozen_token_whitelist():
    document = validate_tactical_scene_payload(_scene_payload())

    tokens = project_tactical_scene_player(
        document, uid="p1", owned_actor_refs=set(),
    )["placeables"]["tokens"]

    token = tokens["token_hero"]
    assert set(token) == set(TOKEN_PLAYER_FIELDS)
    for field in TOKEN_PLAYER_EXCLUDED_FIELDS:
        assert field not in token
    assert token["display"] == {"name": "token_hero"}
    assert token["bars"] == {"bar1": {"attribute": "hp"}}


def test_actor_ref_is_kept_only_when_owned_or_publicly_disposed():
    document = validate_tactical_scene_payload(_scene_payload())
    owned = actor_ref_key(_actor_ref("owned"))

    tokens = project_tactical_scene_player(
        document,
        uid="p1",
        owned_actor_refs={owned},
        public_dispositions=frozenset({-1, 0, 1}),
    )["placeables"]["tokens"]

    assert tokens["token_hero"]["actor_ref"] == _actor_ref("hero")
    assert tokens["token_secret"]["actor_ref"] is None
    assert tokens["token_owned_secret"]["actor_ref"] == _actor_ref("owned")


def test_projection_never_mutates_the_normalized_document():
    document = validate_tactical_scene_payload(_scene_payload())
    snapshot = deepcopy(document)

    gm = project_tactical_scene_gm(document)
    gm["placeables"]["tokens"]["token_hidden"]["hidden"] = False
    seat = project_tactical_scene_player(document, uid="p1", owned_actor_refs=set())
    seat["grid"]["type"] = "hex"

    assert document == snapshot