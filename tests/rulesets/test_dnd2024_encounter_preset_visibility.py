"""Product contract: D&D 2024 encounter presets and monster stat blocks are GM-only.

Non-GM viewers (seated players, and P2P guests the host bridge relays as a
delegated seat) never receive ``gameplay.encounter_presets`` nor any monster
stat block (attacks / abilities / saving throws) in any ruleset gameplay
response.  They only get a name/description summary of the encounter the
story is asking for, and, once combat is live, what ``combat.actors`` shows.
The GM / owner keeps the full catalog.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.engine.game_instance import GameInstance, GameRegistry
from src.webui.routes.games import register_games

from test_dnd2024_m5_http import (
    _EnabledRuntime, _M5Api, _character, _enemy, _ready_story_encounter,
)
from src.engine.modules import ruleset_runtime

_GAME = "/api/games/web%7Cpreset-gm%7Cweb_bot"
FORBIDDEN_STAT_BLOCK_KEYS = frozenset({"attacks", "abilities", "saving_throws", "attack_bonus"})
_SEAT = {"X-Test-User": "p1"}
_GM = {"X-Test-User": "gm"}
# What access_control sets when the host's P2P bridge relays a guest request
# (``?user=<guest>&share=1&delegate=1`` on the owner-authenticated host).
_P2P_GUEST = {"X-Test-User": "p1", "X-Test-Owner": "1", "X-Test-Preview": "1"}


def forbidden_key_paths(value: Any, path: str = "$") -> list[str]:
    """Every path in a payload where a GM-only monster stat-block key appears.

    Walks the whole payload, at any depth, by key name -- not by the shape of
    an enemy record -- so a stat block smuggled in under a new field is still
    caught.  ``modifier`` is deliberately not forbidden: resolved checks show it.
    """

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_STAT_BLOCK_KEYS:
                found.append(f"{path}.{key}")
            found.extend(forbidden_key_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(forbidden_key_paths(item, f"{path}[{index}]"))
    return found


def assert_no_stat_blocks(payload: Any, label: str) -> None:
    leaks = forbidden_key_paths(payload)
    assert leaks == [], f"{label} leaks GM-only stat-block keys: {leaks}"


def _app(registry: GameRegistry, runtime: _EnabledRuntime) -> web.Application:
    @web.middleware
    async def identity(request: web.Request, handler):
        request["user_id"] = request.headers.get("X-Test-User", "")
        request["owner_authenticated"] = request.headers.get("X-Test-Owner") == "1"
        preview = request.headers.get("X-Test-Preview") == "1"
        request["player_preview"] = preview
        request["player_delegate"] = preview
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app["api"] = _M5Api(registry, runtime)
    app["subsystems"] = SimpleNamespace(registry=registry)
    register_games(app)
    return app


def _pending_encounter(registry: GameRegistry) -> tuple[GameInstance, _EnabledRuntime, dict]:
    runtime = _EnabledRuntime()
    instance = GameInstance(
        game_key=("web", "preset-gm", "web_bot"), world_id="test-world",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    character = _character(runtime, "stalwart_guardian", "Guardian")
    instance.players["gm"] = {"character_name": "Guardian", "character_sheet": character}
    instance.players["p1"] = {
        "character_name": "Scout",
        "character_sheet": _character(runtime, "stalwart_guardian", "Scout"),
    }
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    encounter = _ready_story_encounter(runtime, instance)
    registry.register(instance)
    return instance, runtime, encounter


def _start(runtime: _EnabledRuntime, instance: GameInstance, encounter: dict) -> None:
    resolved = runtime.resolve_intent(instance, {
        "intent_id": "preset-gm-start", "type": "combat.start",
        "expected_version": encounter["expected_version"],
        "encounter_preset_id": encounter["encounter_preset_id"],
        "encounter_instance_id": encounter["encounter_instance_id"],
        "submitted_by": "gm", "enemies": [_enemy()],
    }, random.Random(7))
    assert resolved["ok"] is True
    runtime.apply_event_batch(instance, resolved["event_batch"])


@pytest.mark.asyncio
async def test_players_get_no_presets_but_gm_keeps_them_before_combat(tmp_path) -> None:
    registry = GameRegistry(tmp_path / "saves")
    _instance, runtime, encounter = _pending_encounter(registry)

    async with TestClient(TestServer(_app(registry, runtime))) as client:
        bodies = {}
        for name, headers in (("seat", _SEAT), ("p2p", _P2P_GUEST), ("gm", _GM)):
            response = await client.get(f"{_GAME}/available-actions", headers=headers)
            assert response.status == 200, name
            bodies[name] = await response.json()

    gm_presets = bodies["gm"]["gameplay"]["encounter_presets"]
    assert any(preset["id"] == encounter["encounter_preset_id"] for preset in gm_presets)
    assert forbidden_key_paths(gm_presets), "GM keeps the full stat blocks"
    for name in ("seat", "p2p"):
        gameplay = bodies[name]["gameplay"]
        assert "encounter_presets" not in gameplay, name
        assert_no_stat_blocks(bodies[name], name)
        # The story still names the encounter for the party, without numbers.
        preview = gameplay["encounter_preview"]
        assert preview["id"] == encounter["encounter_preset_id"]
        assert preview["name"]
        assert set(preview) <= {"id", "name", "description", "difficulty"}
    # A P2P guest is projected exactly like the seat it plays.
    assert bodies["p2p"]["gameplay"] == bodies["seat"]["gameplay"]


@pytest.mark.asyncio
async def test_live_combat_gives_players_only_the_actor_roster(tmp_path) -> None:
    registry = GameRegistry(tmp_path / "saves")
    instance, runtime, encounter = _pending_encounter(registry)
    _start(runtime, instance, encounter)

    async with TestClient(TestServer(_app(registry, runtime))) as client:
        seat = await (await client.get(f"{_GAME}/available-actions", headers=_SEAT)).json()
        p2p = await (await client.get(f"{_GAME}/available-actions", headers=_P2P_GUEST)).json()
        gm = await (await client.get(f"{_GAME}/available-actions", headers=_GM)).json()

    for label, body in (("seat", seat), ("p2p", p2p)):
        assert "encounter_presets" not in body["gameplay"]
        assert_no_stat_blocks(body, label)
        enemy = next(
            actor for actor in body["gameplay"]["combat"]["actors"]
            if actor["kind"] == "enemy"
        )
        assert enemy["hp"] == enemy["max_hp"] > 0
        assert enemy["armor_class"] > 0
    assert seat["gameplay"] == p2p["gameplay"]
    assert "encounter_presets" in gm["gameplay"]


@pytest.mark.asyncio
async def test_intent_responses_carry_no_stat_blocks_for_players(tmp_path) -> None:
    registry = GameRegistry(tmp_path / "saves")
    instance, runtime, encounter = _pending_encounter(registry)

    async with TestClient(TestServer(_app(registry, runtime))) as client:
        started = await client.post(f"{_GAME}/intents", headers=_GM, json={
            "intent_id": "http-preset-start", "type": "combat.start",
            "expected_version": encounter["expected_version"],
            "encounter_preset_id": encounter["encounter_preset_id"],
            "encounter_instance_id": encounter["encounter_instance_id"],
        })
        started_body = await started.json()
        version = int(ruleset_runtime.state(instance)["version"])
        messages = {}
        for name, headers in (("seat", _SEAT), ("p2p", _P2P_GUEST)):
            response = await client.post(f"{_GAME}/intents", headers=headers, json={
                "intent_id": f"preset-message-{name}", "type": "combat.message",
                "expected_version": version, "text": "Hold the line!",
            })
            assert response.status == 200, (name, await response.text())
            messages[name] = await response.json()
            version = int(ruleset_runtime.state(instance)["version"])

    assert started.status == 200
    # The GM's own response keeps the authoritative stat blocks.
    assert forbidden_key_paths(started_body["result"]["combat"])
    assert "encounter_presets" in started_body["gameplay"]
    for name, body in messages.items():
        assert body["result"]["applied"] is True, name
        assert_no_stat_blocks(body, name)
        assert "encounter_presets" not in body["gameplay"], name
        enemies = body["result"]["combat"]["enemies"]
        assert enemies and all(enemy["max_hp"] > 0 for enemy in enemies.values())


def test_resume_payload_is_projected_for_a_seat() -> None:
    """Automatic-turn payloads reach whoever toggled seat control."""

    runtime = _EnabledRuntime()
    raw = {
        "automatic_event_batches": [{"events": [{
            "type": "dnd2024.combat.started",
            "enemies": {"ogre": {"id": "ogre", "name": "Ogre", "hp": 59, "max_hp": 59,
                                 "armor_class": 11, "attacks": [{"id": "club"}]}},
        }]}],
        "automatic_results": [{"combat": {"enemies": {"ogre": {
            "id": "ogre", "hp": 40, "max_hp": 59, "abilities": {"str": 19},
        }}}}],
    }
    projected = runtime.project_intent_result(
        GameInstance(game_key=("web", "x", "y"), gm_uid="gm"), raw, "p1", False,
    )
    assert_no_stat_blocks(projected, "resume payload")
    assert projected["automatic_results"][0]["combat"]["enemies"]["ogre"]["hp"] == 40
    assert forbidden_key_paths(raw), "projection must not mutate the authoritative payload"


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer", ["seat", "p2p"])
async def test_player_intent_response_with_enemy_automation_has_no_stat_blocks(
    tmp_path, monkeypatch, viewer,
) -> None:
    """A player's end_turn hands the turn to the enemy: the server-run enemy
    turn comes back in that player's own /intents response."""

    import src.webui.services.ruleset_gameplay as gameplay_service

    # Seed 3 rolls initiative p1 -> goblin -> gm for this encounter.
    # P0 修复后默认 RNG 走模块接缝 _default_rng，构造期解析，monkeypatch 生效。
    monkeypatch.setattr(gameplay_service, "_default_rng", lambda: random.Random(3))
    registry = GameRegistry(tmp_path / "saves")
    instance, runtime, encounter = _pending_encounter(registry)
    headers = _SEAT if viewer == "seat" else _P2P_GUEST

    async with TestClient(TestServer(_app(registry, runtime))) as client:
        started = await client.post(f"{_GAME}/intents", headers=_GM, json={
            "intent_id": "auto-start", "type": "combat.start",
            "expected_version": encounter["expected_version"],
            "encounter_preset_id": encounter["encounter_preset_id"],
            "encounter_instance_id": encounter["encounter_instance_id"],
        })
        assert started.status == 200
        initiative = ruleset_runtime.state(instance)["combat"]["initiative"]
        assert initiative[:2] == ["player:p1", "enemy:goblin-minion-1"], initiative
        ended = await client.post(f"{_GAME}/intents", headers=headers, json={
            "intent_id": f"auto-end-{viewer}", "type": "end_turn",
            "expected_version": int(ruleset_runtime.state(instance)["version"]),
        })
        body = await ended.json()

    assert ended.status == 200, body
    result = body["result"]
    assert result["automatic_event_batches"], "the enemy turn must have run"
    assert result["automatic_results"]
    assert_no_stat_blocks(body, viewer)
    # The authoritative enemy record behind it still has its stat block.
    assert forbidden_key_paths(ruleset_runtime.state(instance)["combat"]["enemies"])
