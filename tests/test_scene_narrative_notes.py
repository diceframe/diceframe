"""R9-b: the scene lives in narrative_notes v2; every projection reads it unchanged."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import narrative_notes as module
from src.engine.modules import legacy_combat
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v39_to_v40,
    migrate_game_state_payload,
)
from src.webui.routes.sse import _play_public_signature
from src.webui.services.game_queries import LOBBY_NARRATIVE_FIELDS, game_detail, list_games, lobby_detail
from test_game_query_routes_http import ROOM_HEADER, _make_app, _make_game, _owner_password, play_env  # noqa: F401
from test_game_list_order import _query_dependencies
from test_share_gm_seat_guard import _cookie

KEY = ["web", "scene-notes", "bot"]
SCENE = "旧塔入口 · 午夜"
NOTES_V1 = {
    "schema_version": 1, "summary": {"narrative": "So far"}, "key_facts": ["gate"],
    "confirmed_items": ["rope"], "game_time": "dusk",
}


def v39_payload(**extra):
    return {
        "game_key": KEY, "state": "active_action", "instance_schema_version": 39,
        "scene": SCENE, "modules": {"narrative_notes": deepcopy(NOTES_V1)}, **extra,
    }


def legacy_save_of(instance: GameInstance, scene) -> dict:
    """The instance's own save, rewritten to the v39 shape with a top-level scene."""
    payload = deepcopy(instance.to_dict())
    notes = payload["modules"]["narrative_notes"]
    notes.pop("scene")
    notes["schema_version"] = 1
    payload["instance_schema_version"] = 39
    payload["scene"] = scene
    return payload


# ---- migration -----------------------------------------------------------------


def test_migration_upgrades_v1_slot_with_the_scene_and_is_idempotent():
    original = v39_payload(opaque={"keep": [1]})
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert original == before
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["modules"]["narrative_notes"] == {**NOTES_V1, "schema_version": 2, "scene": SCENE}
    assert "scene" not in migrated
    assert migrate_game_state_payload(migrated) == migrated
    step = _migrate_v39_to_v40(deepcopy(original))
    assert _migrate_v39_to_v40(deepcopy(step)) == step


def test_old_saves_travel_the_whole_ladder():
    payload = {"game_key": KEY, "state": "waiting", "instance_schema_version": 24,
               "summary": {"narrative": "x"}, "game_time": "dawn", "scene": SCENE}
    migrated = migrate_game_state_payload(payload)
    notes = migrated["modules"]["narrative_notes"]
    assert notes["schema_version"] == 2 and notes["scene"] == SCENE and notes["game_time"] == "dawn"


@pytest.mark.parametrize("raw", [None, [], "corrupt"])
def test_missing_slot_is_created_with_the_scene(raw):
    migrated = _migrate_v39_to_v40({"scene": SCENE, "modules": {"narrative_notes": raw}})
    assert migrated["modules"]["narrative_notes"] == {**module.fresh(), "scene": SCENE}


def test_unknown_slot_schema_is_left_untouched():
    slot = {"schema_version": 99, "opaque": True}
    migrated = migrate_game_state_payload(v39_payload(modules={"narrative_notes": deepcopy(slot)}))
    assert migrated["modules"]["narrative_notes"] == slot
    assert "scene" not in migrated


@pytest.mark.parametrize("scene", ["", None, 5, {"name": "odd"}, "  spaced \n"])
def test_scene_values_are_kept_verbatim(scene):
    migrated = migrate_game_state_payload(v39_payload(scene=scene))
    assert migrated["modules"]["narrative_notes"]["scene"] == scene
    restored = GameInstance.from_dict(v39_payload(scene=scene))
    assert module.scene(restored) == scene
    assert module.scene(GameInstance.from_dict(restored.to_dict())) == scene


def test_missing_scene_defaults_to_empty():
    payload = v39_payload()
    del payload["scene"]
    assert module.scene(GameInstance.from_dict(payload)) == ""
    raw = {**NOTES_V1, "schema_version": 2}
    assert module.ensure(raw) is raw and raw["scene"] == ""


def test_future_instance_version_is_rejected():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        GameInstance.from_dict({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})


def test_unknown_slot_schema_fails_closed_and_is_preserved():
    slot = {"schema_version": 1, **{k: v for k, v in NOTES_V1.items() if k != "schema_version"}}
    instance = GameInstance(game_key=tuple(KEY), modules={"narrative_notes": deepcopy(slot)})
    with pytest.raises(ModuleStateError, match="unsupported narrative_notes module schema"):
        module.scene(instance)
    with pytest.raises(ModuleStateError, match="unsupported narrative_notes module schema"):
        instance.set_scene("x")
    assert instance.modules["narrative_notes"] == slot
    assert instance.to_dict()["modules"]["narrative_notes"] == slot


# ---- proxy and reset -------------------------------------------------------------


def test_accessor_uses_the_live_slot_and_roundtrips():
    instance = GameInstance(game_key=tuple(KEY))
    assert "scene" not in {item.name for item in fields(instance)}
    assert "scene" not in vars(instance)
    instance.set_scene(SCENE)
    assert instance.modules["narrative_notes"]["scene"] == SCENE == module.scene(instance)
    encoded = instance.to_dict()
    assert "scene" not in encoded
    restored = GameInstance.from_dict(deepcopy(encoded))
    assert module.scene(restored) == SCENE
    other = GameInstance(game_key=("web", "other", "bot"))
    assert module.scene(other) == ""


@pytest.mark.asyncio
async def test_reset_clears_the_scene_with_the_other_notes():
    from tests.test_game_instance_reset_characterization import _make_populated_instance

    instance = _make_populated_instance()
    assert module.scene(instance)
    slot = instance.modules["narrative_notes"]
    await instance.reset()
    assert module.scene(instance) == ""
    assert instance.modules["narrative_notes"] is slot


def _ruleset_snapshot(instance, scene):
    return {
        "ruleset_state": {}, "event_ledger": [], "players": {},
        "combat_state": legacy_combat.combat_state(instance), "combat_active": legacy_combat.combat_active(instance),
        "initiative_order": [], "initiative_current": 0, "scene": scene,
    }


def test_ruleset_transaction_restores_the_scene():
    instance = GameInstance(game_key=tuple(KEY))
    instance.set_scene("after")
    instance.restore_ruleset_transaction(_ruleset_snapshot(instance, SCENE))
    assert module.scene(instance) == SCENE
    instance.restore_ruleset_transaction(_ruleset_snapshot(instance, None))
    assert module.scene(instance) == SCENE


# ---- projection contracts ----------------------------------------------------------


def _loaded(tmp_path):
    registry = GameRegistry(tmp_path)
    instance = GameInstance.from_dict(v39_payload())
    registry.register(instance)
    return registry, instance


def test_list_detail_recap_and_lobby_project_the_loaded_scene(tmp_path):
    registry, instance = _loaded(tmp_path)
    deps = _query_dependencies(registry)
    assert list_games(deps)["games"][0]["scene"] == SCENE
    detail = game_detail(deps, "|".join(KEY), viewer_is_gm=True)
    assert detail["scene"] == SCENE
    assert detail["recap"]["current_scene"] == SCENE
    assert lobby_detail(detail)["scene"] == SCENE
    assert LOBBY_NARRATIVE_FIELDS == ("scene",)


def test_llm_projection_carries_the_scene(tmp_path):
    _registry, instance = _loaded(tmp_path)
    assert instance.to_llm_view()["scene"] == SCENE


def test_sse_public_signature_tracks_the_scene(tmp_path):
    _registry, instance = _loaded(tmp_path)
    instance.state = GameState.ACTIVE_ACTION
    loaded = _play_public_signature(instance, "p1")
    fresh = GameInstance.from_dict(instance.to_dict())
    assert _play_public_signature(fresh, "p1") == loaded
    instance.set_scene("elsewhere")
    assert _play_public_signature(instance, "p1") != loaded


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["", "secret-pass"])
async def test_lobby_http_scene_contract_for_a_loaded_save(play_env, password):
    key, instance = _make_game(
        play_env, f"scene-lobby-{bool(password)}", bind_adventure=False, room_password=password,
    )
    loaded = GameInstance.from_dict(legacy_save_of(instance, SCENE))
    play_env.registry.register(loaded)
    assert play_env.registry.get(loaded.game_key) is loaded
    app = _make_app(play_env)
    from src.webui.session import SessionManager, session_middleware

    sessions = SessionManager(play_env.adventures_dir.parent / "sessions")
    app["session_manager"] = sessions
    app.middlewares.insert(0, session_middleware)
    stranger, _ = sessions.get_or_create(None)
    async with TestClient(TestServer(app)) as client:
        without_token = await (await client.get(
            f"/api/games/{key}?share=1", headers={**_cookie(stranger), "X-Room-Token": ""},
        )).json()
        with_token = await (await client.get(
            f"/api/games/{key}?share=1", headers={**_cookie(stranger), **ROOM_HEADER},
        )).json()
    assert with_token["viewer"] == {"kind": "outsider"}
    assert with_token["scene"] == SCENE
    if password:
        assert "scene" not in without_token
        assert without_token["has_room_password"] is True
    else:
        assert without_token["scene"] == SCENE
