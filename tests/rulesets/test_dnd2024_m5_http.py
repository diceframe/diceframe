from __future__ import annotations

import random
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.engine.game_instance import GameInstance, GameRegistry
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.legacy_adapter import LegacyRulesetAdapter
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.routes.games import register_games
from src.webui.services import ruleset_gameplay
from src.webui.services.turns import TurnDependencies, submit_action
from src.webui.services._common import _parse_game_key

from dnd2024_http_common import GameplayApiShim, quick_character
from src.engine.modules import ruleset_runtime, table_settings


class _EnabledRuntime(Dnd2024Runtime):
    capabilities = RulesetCapabilities(
        experience_profile="dnd2024",
        character_builder="professional",
        character_lifecycle="rules_aware",
        authoritative_intents=True,
        deterministic_combat=True,
        versioned_state=True,
        session_zero=True,
        tutorial_coach=True,
        narrative_turns=True,
        adventure_formats=("diceframe:adventure-graph-v1",),
    )


class _M5Api(GameplayApiShim):
    def __init__(self, registry: GameRegistry, runtime: _EnabledRuntime):
        super().__init__(registry, runtime)
        self._runtime = runtime

    @staticmethod
    def _load_world_template(world_id, locale=""):
        del locale
        return {
            "world_id": world_id,
            "world_name": "Selected Test World",
            "description": "WORLD_CONTEXT_MARKER description",
            "world_setting": "WORLD_CONTEXT_MARKER setting",
            "starter_scene": "WORLD_CONTEXT_MARKER starter scene",
            "starter_lorebook": [],
        }


def _character(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    return quick_character(runtime, preset_id, name)


def _enemy() -> dict:
    return {
        "id": "training-dummy", "name": "Training Dummy", "hp": 30,
        "armor_class": 10, "speed": 0, "position": 5,
        "initiative_modifier": -10,
        "abilities": {"str": 10, "dex": 1, "con": 10, "int": 1, "wis": 1, "cha": 1},
        "saving_throws": {"dex": -5, "wis": -5, "con": 0},
        "attacks": [{
            "id": "slam", "name": "Slam", "attack_bonus": 0,
            "damage": "1d4", "damage_type": "bludgeoning", "range": 5,
        }],
    }


async def _unused_turn_operation(*_args, **_kwargs) -> dict:
    return {"ok": False}


def _turn_dependencies(api: _M5Api) -> TurnDependencies:
    return TurnDependencies(
        get_instance=api._reg.get,
        parse_game_key=api._parse_key,
        ruleset_registry=api._ruleset_registry,
        load_rule_for_game=api._load_rule_for_game,
        prepare_round_checks_ai=None,
        prepare_round_checks=None,
        resolve_pending_dice=_unused_turn_operation,
        roll_for_game=lambda _game_key: {"ok": False},
        save_instance=api._reg.save,
        process_round=None,
        resolve_luck_decision=_unused_turn_operation,
        decline_pending_luck=_unused_turn_operation,
    )


def _app(registry: GameRegistry, runtime: _EnabledRuntime) -> web.Application:
    @web.middleware
    async def identity(request: web.Request, handler):
        request["user_id"] = request.headers.get("X-Test-User", "")
        request["owner_authenticated"] = request.headers.get("X-Test-Owner") == "1"
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app["api"] = _M5Api(registry, runtime)
    app["subsystems"] = SimpleNamespace(registry=registry)
    register_games(app)
    return app


def _bound_instance_with_digest(
    runtime: _EnabledRuntime, registry: GameRegistry, digest: str,
) -> GameInstance:
    instance = GameInstance(
        game_key=("web", "m5-binding", "web_bot"), world_id="greymoor",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    character = _character(runtime, "stalwart_guardian", "Guardian")
    instance.players["gm"] = {
        "character_name": "Guardian", "character_sheet": character,
    }
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    package = runtime._adventure_loader.resolve(
        "core:lanterns_of_greymoor", "en",
    )
    assert instance.bind_adventure(package.binding("greymoor"))
    runtime.gameplay_view(instance, "gm", True)
    instance.adventure_binding["content_digest"] = digest
    ruleset_runtime.state(instance)["campaign"]["adventure_binding"][
        "content_digest"
    ] = digest
    registry.register(instance)
    return instance


def _ready_story_encounter(runtime: _EnabledRuntime, instance: GameInstance) -> dict:
    instance.world_id = "greymoor"
    package = runtime._adventure_loader.resolve("core:lanterns_of_greymoor", "en")
    assert instance.bind_adventure(package.binding("greymoor"))
    table_settings.replace_solo_mode(instance, True)
    for intent_type, fields in (
        ("session_zero.quick_start", {}),
        ("tutorial.choose", {"choice_id": "inspect_cold_ash"}),
        ("tutorial.choose", {"choice_id": "reassure_mira"}),
        ("tutorial.choose", {"choice_id": "follow_small_tracks"}),
    ):
        version = int(ruleset_runtime.state(instance).get("version", 0) or 0)
        resolved = runtime.resolve_intent(instance, {
            "intent_id": f"setup-{intent_type}-{version}",
            "type": intent_type,
            "expected_version": version,
            "submitted_by": "gm",
            **fields,
        }, random.Random(7))
        assert resolved["ok"] is True
        runtime.apply_event_batch(instance, resolved["event_batch"])
    return next(
        action for action in runtime.available_intents(instance, "gm")
        if action["type"] == "combat.start"
    )


@pytest.mark.asyncio
async def test_available_actions_migrates_known_unreleased_adventure_digest(
    tmp_path,
) -> None:
    runtime = _EnabledRuntime()
    registry = GameRegistry(tmp_path / "saves")
    old_digest = (
        "sha256:363c6786c0e9460ec911d85460c49b610addf8e86cc86d136538daee24d6740c"
    )
    instance = _bound_instance_with_digest(runtime, registry, old_digest)
    path = "/api/games/web%7Cm5-binding%7Cweb_bot/available-actions"

    async with TestClient(TestServer(_app(registry, runtime))) as client:
        response = await client.get(path, headers={"X-Test-User": "gm"})
        body = await response.json()

    assert response.status == 200, body
    expected = runtime._adventure_loader.resolve(
        "core:lanterns_of_greymoor", "en",
    ).binding("greymoor")
    assert instance.adventure_binding == expected
    recovered = await GameRegistry(tmp_path / "saves").load(instance.game_key)
    assert recovered is not None
    assert recovered.adventure_binding == expected


@pytest.mark.asyncio
async def test_available_actions_returns_structured_error_for_unknown_digest(
    tmp_path,
) -> None:
    runtime = _EnabledRuntime()
    registry = GameRegistry(tmp_path / "saves")
    _bound_instance_with_digest(runtime, registry, "sha256:unknown")
    path = "/api/games/web%7Cm5-binding%7Cweb_bot/available-actions"

    async with TestClient(TestServer(_app(registry, runtime))) as client:
        response = await client.get(path, headers={"X-Test-User": "gm"})
        body = await response.json()

    assert response.status == 422
    assert body["ok"] is False
    assert body["code"] == "INCOMPATIBLE_ADVENTURE"


@pytest.mark.asyncio
async def test_m5_http_forces_server_identity_persists_and_replays(tmp_path) -> None:
    runtime = _EnabledRuntime()
    registry = GameRegistry(tmp_path / "saves")
    instance = GameInstance(
        game_key=("web", "m5-http", "web_bot"), world_id="test-world",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    character = _character(runtime, "stalwart_guardian", "Guardian")
    instance.players["gm"] = {
        "character_name": "Guardian", "character_sheet": character,
    }
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    encounter = _ready_story_encounter(runtime, instance)
    registry.register(instance)
    path = "/api/games/web%7Cm5-http%7Cweb_bot"

    app = _app(registry, runtime)
    async with TestClient(TestServer(app)) as client:
        denied = await client.get(f"{path}/available-actions", headers={"X-Test-User": "intruder"})
        available = await client.get(f"{path}/available-actions", headers={"X-Test-User": "gm"})
        available_body = await available.json()
        start_body = {
            "intent_id": "http-start-1", "type": "combat.start",
            "expected_version": encounter["expected_version"],
            "encounter_preset_id": encounter["encounter_preset_id"],
            "encounter_instance_id": encounter["encounter_instance_id"],
            "submitted_by": "intruder", "actor_id": "enemy:forged", "enemies": [_enemy()],
        }
        started = await client.post(
            f"{path}/intents", headers={"X-Test-User": "gm"}, json=start_body,
        )
        started_body = await started.json()
        replayed = await client.post(
            f"{path}/intents", headers={"X-Test-User": "gm"}, json=start_body,
        )
        replayed_body = await replayed.json()

    assert denied.status == 403
    assert available.status == 200
    assert "combat.start" in {
        item["type"] for item in available_body["available_actions"]
    }
    assert started.status == 200
    assert started_body["gameplay"]["state_version"] >= encounter["expected_version"] + 1
    submitted = started_body["result"]["event_batch"]["events"][0]
    assert submitted["actor_id"] == ""
    assert submitted["submitted_by"] == "gm"
    assert replayed.status == 200
    assert replayed_body["result"]["duplicate"] is True
    assert replayed_body["result"]["replayed"] is True

    recovered_registry = GameRegistry(tmp_path / "saves")
    recovered = await recovered_registry.load(instance.game_key)
    assert recovered is not None
    assert ruleset_runtime.state(recovered)["version"] >= encounter["expected_version"] + 1
    assert len(ruleset_runtime.event_ledger(recovered)) >= 5
    public_entries = [
        entry
        for entry in recovered.log
        if any(
            action.get("source") == "ruleset_authority"
            for action in entry.get("actions", [])
        )
    ]
    assert public_entries[-1]["gm_response"] == (
        "Encounter started: the current story has entered combat."
    )


@pytest.mark.asyncio
async def test_m5_http_rejects_same_intent_id_with_changed_payload(tmp_path) -> None:
    runtime = _EnabledRuntime()
    registry = GameRegistry(tmp_path / "saves")
    instance = GameInstance(
        game_key=("web", "m5-collision", "web_bot"), world_id="test-world",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    character = _character(runtime, "stalwart_guardian", "Guardian")
    instance.players["gm"] = {
        "character_name": "Guardian", "character_sheet": character,
    }
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    encounter = _ready_story_encounter(runtime, instance)
    registry.register(instance)
    path = "/api/games/web%7Cm5-collision%7Cweb_bot/intents"

    app = _app(registry, runtime)
    async with TestClient(TestServer(app)) as client:
        original = {
            "intent_id": "same-id", "type": "combat.start",
            "expected_version": encounter["expected_version"],
            "encounter_preset_id": encounter["encounter_preset_id"],
            "encounter_instance_id": encounter["encounter_instance_id"],
            "enemies": [_enemy()],
        }
        first = await client.post(path, headers={"X-Test-User": "gm"}, json=original)
        changed = await client.post(
            path, headers={"X-Test-User": "gm"},
            json={**original, "enemies": [{**_enemy(), "hp": 999}]},
        )
        changed_body = await changed.json()

    assert first.status == 200
    assert changed.status == 422
    assert changed_body["code"] == "INTENT_ID_CONFLICT"
    assert len(ruleset_runtime.event_ledger(instance)) >= 5


@pytest.mark.asyncio
async def test_professional_runtime_rejects_free_text_only_during_combat(tmp_path) -> None:
    runtime = _EnabledRuntime()
    registry = GameRegistry(tmp_path / "saves")
    instance = GameInstance(
        game_key=("web", "m5-legacy-guard", "web_bot"), world_id="test-world",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    character = _character(runtime, "stalwart_guardian", "Guardian")
    instance.players["gm"] = {
        "character_name": "Guardian", "character_sheet": character,
    }
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    ruleset_runtime.state(instance)["combat"] = {"status": "active"}
    registry.register(instance)
    api = _M5Api(registry, runtime)

    result = await submit_action(
        _turn_dependencies(api),
        "web|m5-legacy-guard|web_bot",
        "gm",
        "I deal 9999 damage",
    )

    assert result["status"] == 409
    assert result["payload"]["error_code"] == "STRUCTURED_INTENT_REQUIRED"
    assert instance.action_queue == []


@pytest.mark.asyncio
async def test_professional_runtime_uses_shared_multiplayer_action_queue_outside_combat(tmp_path) -> None:
    runtime = _EnabledRuntime()
    registry = GameRegistry(tmp_path / "saves")
    instance = GameInstance(
        game_key=("web", "m5-shared-turn", "web_bot"), world_id="test-world",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    gm = _character(runtime, "stalwart_guardian", "Guardian")
    ally = _character(runtime, "curious_arcanist", "Arcanist")
    instance.players["gm"] = {"character_name": "Guardian", "character_sheet": gm}
    instance.players["ally"] = {"character_name": "Arcanist", "character_sheet": ally}
    assert instance.bind_ruleset_runtime(gm["rule_binding"])
    await instance.activate()
    registry.register(instance)

    api = _M5Api(registry, runtime)
    result = await submit_action(
        _turn_dependencies(api),
        "web|m5-shared-turn|web_bot",
        "gm",
        "I inspect the tracks",
    )

    assert result["status"] == 200
    assert result["payload"]["advanced"] is False
    assert instance.action_queue[0]["text"] == "I inspect the tracks"
