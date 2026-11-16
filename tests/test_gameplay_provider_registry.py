import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import adventure_runtime_state
from src.gameplays.contracts import (
    BOT_SURFACE_FULL,
    GameplayMode,
    GameplayProvider,
    GameplayProviderRegistry,
)
from src.gameplays.view import project_gameplay_view
from src.webui.api import WebAPI


class Driver:
    async def process_round(self, instance, *, on_delta=None, on_reset=None):
        return "", None


def test_external_gameplay_provider_registers_mode_driver_and_projection():
    registry = GameplayProviderRegistry()
    registry.register(GameplayProvider(
        mode=GameplayMode("cards", "read_only", lambda request: None, "cards-driver", progression_mode="scene"),
        turn_driver=Driver(),
        project_view=lambda instance, viewer, is_gm: {"viewer": viewer, "gm": is_gm},
    ))
    assert registry.get("cards") is not None
    assert registry.driver_for("cards") is not None
    assert registry.project_view("cards", object(), "player") == {"viewer": "player", "gm": False}


def test_external_gameplay_provider_fails_closed_for_missing_or_invalid_projection():
    registry = GameplayProviderRegistry()
    with pytest.raises(ValueError, match="unavailable"):
        registry.validate_instance("missing", object())
    registry.register(GameplayProvider(
        mode=GameplayMode("broken", "none", lambda request: None, "broken-driver"),
        turn_driver=Driver(),
        project_view=lambda instance, viewer, is_gm: [],
    ))
    with pytest.raises(ValueError, match="projection"):
        registry.project_view("broken", object(), "viewer")


def test_provider_slot_projection_is_owner_supplied_and_missing_slots_fail_closed():
    registry = GameplayProviderRegistry()
    registry.register(GameplayProvider(
        mode=GameplayMode("slots", "full", lambda request: None, "slots-driver"),
        turn_driver=Driver(),
        project_view=lambda _instance, _viewer, _is_gm: {},
        project_slot=lambda _instance, viewer, is_gm, slot: {
            "slot": slot, "viewer": viewer, "is_gm": is_gm,
        },
    ))
    assert registry.project_slot("slots", object(), "alice", False, "character")["slot"] == "character"
    registry.register(GameplayProvider(
        mode=GameplayMode("noslots", "read_only", lambda request: None, "noslots-driver"),
        turn_driver=Driver(),
        project_view=lambda _instance, _viewer, _is_gm: {},
    ))
    with pytest.raises(ValueError, match="no slot projection"):
        registry.project_slot("noslots", object(), "alice", False, "gm")


@pytest.mark.asyncio
async def test_provider_action_is_owner_handled_and_missing_handler_fails_closed():
    registry = GameplayProviderRegistry()
    registry.register(GameplayProvider(
        mode=GameplayMode("cards", "full", lambda request: None, "cards-driver"),
        turn_driver=Driver(),
        project_view=lambda _instance, _viewer, _is_gm: {},
        handle_action=lambda _instance, viewer, _is_gm, action, arguments: {
            "viewer": viewer, "action": action, "arguments": arguments,
        },
    ))
    result = await registry.dispatch_action("cards", object(), "player", False, "cards.play", {"id": "c1"})
    assert result["action"] == "cards.play"
    registry.register(GameplayProvider(
        mode=GameplayMode("read", "read_only", lambda request: None, "read-driver"),
        turn_driver=Driver(),
        project_view=lambda _instance, _viewer, _is_gm: {},
    ))
    with pytest.raises(ValueError, match="no action handler"):
        await registry.dispatch_action("read", object(), "player", False, "x", {})


def test_gameplay_view_projection_never_falls_back_to_writable_trpg_for_unknown_mode():
    registry = GameplayProviderRegistry()
    unsupported = project_gameplay_view(registry, "unknown", object(), "viewer")
    assert unsupported == {
        "ok": False,
        "code": "UNSUPPORTED_GAMEPLAY",
        "mode_id": "unknown",
        "read_only": True,
        "diagnostic": "gameplay provider is unavailable",
    }


def test_webapi_uses_explicitly_injected_provider_for_gameplay_view():
    instance = GameInstance(game_key=("web", "provider", "main"), run_id="run-provider")
    adventure_runtime_state.replace_play_mode(instance, "board")
    registry = GameplayProviderRegistry()
    registry.register(GameplayProvider(
        mode=GameplayMode("board", BOT_SURFACE_FULL, lambda _request: None, "board-turn"),
        turn_driver=object(),
        project_view=lambda _instance, viewer_id, viewer_is_gm: {
            "viewer_id": viewer_id, "is_gm": viewer_is_gm, "slots": [],
        },
    ))
    api = object.__new__(WebAPI)
    api._reg = type("Registry", (), {"get": lambda _self, _key: instance})()
    api._gameplay_providers = registry
    result = api.gameplay_view("web|provider|main", "alice", False)
    assert result == {
        "ok": True,
        "mode_id": "board",
        "read_only": False,
        "view": {"viewer_id": "alice", "is_gm": False, "slots": []},
    }


def test_webapi_projects_provider_declarative_ui_against_owner_view():
    instance = GameInstance(game_key=("web", "ui", "main"), run_id="run-ui")
    adventure_runtime_state.replace_play_mode(instance, "cards")
    registry = GameplayProviderRegistry()
    registry.register(GameplayProvider(
        mode=GameplayMode("cards", BOT_SURFACE_FULL, lambda _request: None, "cards-turn"),
        turn_driver=Driver(),
        project_view=lambda _instance, _viewer, _is_gm: {
            "data": {"label": "Ready"},
            "ui": {"ui_sdk": 1, "surfaces": [{
                "slot": "gameplay-main", "type": "declarative",
                "root": {"component": "text", "binding": "view.data.label"},
            }]},
        },
    ))
    api = object.__new__(WebAPI)
    api._reg = type("Registry", (), {"get": lambda _self, _key: instance})()
    api._gameplay_providers = registry
    result = api.gameplay_view("web|ui|main", "player", False)
    root = result["view"]["ui"]["surfaces"][0]["root"]
    assert root["projected_value"] == "Ready"
