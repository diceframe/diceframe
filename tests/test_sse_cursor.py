import json
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.webui.routes import sse as sse_routes
from src.webui.routes.sse import (
    _event_cursor,
    _parse_event_cursor,
    _play_action_signature,
    _play_public_signature,
    _signature_digest,
)


def _instance() -> GameInstance:
    inst = GameInstance(
        game_key=("web", "room", "bot"),
        state=GameState.ACTIVE_ACTION,
        round_number=12,
        scene="旧塔入口",
    )
    inst.players = {
        "p1": {
            "user_id": "p1",
            "character_name": "冒险者",
            "character_sheet": {"hp": 10, "max_hp": 10, "gold": 3},
        }
    }
    inst.private_log["p1"] = [
        {"round": 11, "text": "你发现墙后的风声。", "source": "gm"},
    ]
    inst.action_queue = [
        {"user_id": "p1", "text": "检查石门", "timestamp": "2026-08-22T10:00:00Z"},
    ]
    return inst


def _current_cursor(inst: GameInstance) -> str:
    return _event_cursor(
        inst.round_number,
        len(inst.private_log["p1"]),
        _play_action_signature(inst),
        _play_public_signature(inst, "p1"),
    )


def test_play_event_cursor_round_trip_includes_action_and_public_digests():
    event_id = _event_cursor(12, 4, '["action"]', '{"scene":"tower"}')
    parsed = _parse_event_cursor(event_id)

    assert event_id.startswith("r12.p4.a")
    assert parsed == (
        12,
        4,
        _signature_digest('["action"]'),
        _signature_digest('{"scene":"tower"}'),
    )


def test_legacy_play_event_cursor_remains_parseable():
    assert _parse_event_cursor("r12.p4.a0123456789") == (12, 4, "0123456789", "0")


def test_invalid_play_event_cursor_establishes_a_fresh_baseline():
    assert _parse_event_cursor("") is None
    assert _parse_event_cursor("invalid") is None
    assert _parse_event_cursor("r-1.p0.a0.s0") is None
    assert _parse_event_cursor(f"r{'1' * 11}.p0.a0.s0") is None


def test_same_state_reconnect_cursor_matches_all_server_baselines():
    inst = _instance()
    parsed = _parse_event_cursor(_current_cursor(inst))

    assert parsed is not None
    assert parsed[0] == inst.round_number
    assert parsed[1] == len(inst.private_log["p1"])
    assert parsed[2] == _signature_digest(_play_action_signature(inst))
    assert parsed[3] == _signature_digest(_play_public_signature(inst, "p1"))


def test_action_and_other_public_changes_advance_distinct_cursor_digests():
    inst = _instance()
    before = _parse_event_cursor(_current_cursor(inst))
    assert before is not None

    inst.action_queue[0]["text"] = "推开石门"
    after_action = _parse_event_cursor(_current_cursor(inst))
    assert after_action is not None
    assert after_action[2] != before[2]
    assert after_action[3] != before[3]

    inst.scene = "旧塔大厅"
    after_scene = _parse_event_cursor(_current_cursor(inst))
    assert after_scene is not None
    assert after_scene[2] == after_action[2]
    assert after_scene[3] != after_action[3]


class _FakeStreamResponse:
    def __init__(self, **_kwargs):
        self.written: list[bytes] = []

    async def prepare(self, _request):
        return None

    async def write(self, data: bytes):
        self.written.append(data)


class _FakePool:
    def __init__(self):
        self.connections = set()

    def add(self, game_key, user_id, response):
        self.connections.add((game_key, user_id, response))

    def remove(self, game_key, user_id, response):
        self.connections.discard((game_key, user_id, response))


class _FakeRequest:
    def __init__(self, inst: GameInstance, cursor: str = ""):
        self.match_info = {"game_key": "web|room|bot"}
        self.headers = {}
        self.query = {"cursor": cursor} if cursor else {}
        self._identity = {"user_id": "p1"}
        self.pool = _FakePool()
        self.app = {
            "connection_pool": self.pool,
            "subsystems": SimpleNamespace(
                registry=SimpleNamespace(get=lambda _key: inst),
            ),
        }

    def get(self, key, default=None):
        return self._identity.get(key, default)


def _payloads(response: _FakeStreamResponse) -> list[dict]:
    payloads = []
    for raw in response.written:
        for line in raw.decode().splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


async def _stop_stream(_delay):
    raise ConnectionResetError


async def _cancel_stream(_delay):
    raise sse_routes.asyncio.CancelledError


def _patch_stream(monkeypatch, inst: GameInstance) -> None:
    monkeypatch.setattr(
        sse_routes,
        "_get_api",
        lambda _request: SimpleNamespace(_parse_key=lambda _key: inst.game_key),
    )
    monkeypatch.setattr(sse_routes.web, "StreamResponse", _FakeStreamResponse)
    monkeypatch.setattr(sse_routes.asyncio, "sleep", _stop_stream)


@pytest.mark.asyncio
async def test_fresh_play_stream_sends_baseline_without_refresh(monkeypatch):
    inst = _instance()
    request = _FakeRequest(inst)
    _patch_stream(monkeypatch, inst)

    response = await sse_routes.sse_play(request)

    assert _payloads(response) == [{"type": "baseline"}]
    assert not request.pool.connections


@pytest.mark.asyncio
async def test_reconnect_without_new_state_sends_no_refresh(monkeypatch):
    inst = _instance()
    request = _FakeRequest(inst, _current_cursor(inst))
    _patch_stream(monkeypatch, inst)

    response = await sse_routes.sse_play(request)

    assert _payloads(response) == []
    assert not request.pool.connections


@pytest.mark.asyncio
async def test_reconnect_after_real_action_change_requests_refresh(monkeypatch):
    inst = _instance()
    cursor = _current_cursor(inst)
    inst.action_queue[0]["text"] = "推开石门"
    request = _FakeRequest(inst, cursor)
    _patch_stream(monkeypatch, inst)

    response = await sse_routes.sse_play(request)

    assert _payloads(response) == [{"type": "public_actions"}]
    assert not request.pool.connections


@pytest.mark.asyncio
async def test_server_shutdown_cancels_play_stream_without_leaking_connection(monkeypatch):
    inst = _instance()
    request = _FakeRequest(inst, _current_cursor(inst))
    _patch_stream(monkeypatch, inst)
    monkeypatch.setattr(sse_routes.asyncio, "sleep", _cancel_stream)

    with pytest.raises(sse_routes.asyncio.CancelledError):
        await sse_routes.sse_play(request)

    assert not request.pool.connections
