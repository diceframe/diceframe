from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.webui.routes import games, memory


class FakeHandler:
    async def generate_swipe(self, inst, round_num: int) -> str:
        inst.generated_swipe = round_num
        return "new branch"


class FakeAPI:
    def __init__(self, registry):
        self.calls: list[tuple] = []
        self.registry = registry
        self._reg = registry
        self._handler = FakeHandler()

    def _parse_key(self, key: str) -> tuple[str, ...]:
        return tuple(key.split("|"))

    async def reset_game(self, game_key: str) -> dict:
        self.calls.append(("reset", game_key))
        return {"ok": True}

    async def restart_game(self, game_key: str) -> dict:
        self.calls.append(("restart", game_key))
        return {"ok": True}

    async def switch_world(self, game_key: str, world_id: str) -> dict:
        self.calls.append(("switch", game_key, world_id))
        return {"ok": True, "world_id": world_id}

    def delete_game(self, game_key: str) -> dict:
        key = self._parse_key(game_key)
        save_dir = self.registry._save_path(key).parent
        if not save_dir.exists():
            return {"ok": False, "error": "存档目录不存在"}
        import shutil
        shutil.rmtree(save_dir)
        self.registry.remove(key)
        return {"ok": True}

    def get_log(self, game_key: str, page: int, per_page: int) -> dict:
        self.calls.append(("log", game_key, page, per_page))
        return {"log": [], "total": 0, "page": page, "total_pages": 1}

    def list_memories(self, game_key: str, keyword: str = "", limit: int = 20, offset: int = 0) -> dict:
        self.calls.append(("memories", game_key, keyword, limit, offset))
        return {"entries": [], "total": 0}


class FakeRegistry:
    def __init__(self, root: Path):
        self.root = root
        self.items: dict[tuple[str, ...], SimpleNamespace] = {}
        self.removed: list[tuple[str, ...]] = []

    def get(self, key: tuple[str, ...]):
        return self.items.get(key)

    def _save_path(self, key: tuple[str, ...]) -> Path:
        return self.root / "__".join(key) / "state.json"

    def remove(self, key: tuple[str, ...]) -> None:
        self.removed.append(key)
        self.items.pop(key, None)


def make_request(
    registry: FakeRegistry,
    *,
    user_id: str = "gm",
    owner_authenticated: bool = False,
    confirm: bool = True,
    body: dict | None = None,
    method: str = "POST",
    query: dict | None = None,
):
    api = FakeAPI(registry)
    request_method = method
    request_query = query or {}

    class FakeRequest:
        match_info = {"game_key": "web|room|bot"}
        app = {"api": api, "subsystems": SimpleNamespace(registry=registry)}
        headers = {"X-TRPG-Confirm": "true"} if confirm else {}
        method = request_method
        query = request_query

        def get(self, key: str, default=None):
            return {"user_id": user_id, "owner_authenticated": owner_authenticated}.get(key, default)

        async def json(self):
            return body or {}

    return FakeRequest(), api


def response_json(response) -> dict:
    return json.loads(response.text)


class FakeSwipeInst(SimpleNamespace):
    async def switch_swipe(self, round_num: int, swipe_idx: int) -> bool:
        self.switched_swipe = (round_num, swipe_idx)
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [games.api_reset_game, games.api_restart_game, games.api_switch_world, games.api_delete_game])
async def test_dangerous_game_routes_require_confirm_header(tmp_path, handler):
    registry = FakeRegistry(tmp_path)
    key = ("web", "room", "bot")
    registry.items[key] = SimpleNamespace(gm_uid="gm")
    registry._save_path(key).parent.mkdir(parents=True)
    req, _api = make_request(registry, confirm=False, body={"world_id": "next"})

    response = await handler(req)

    assert response.status == 403
    assert response_json(response)["error"] == "缺少确认头"


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [games.api_reset_game, games.api_restart_game, games.api_switch_world, games.api_delete_game])
async def test_dangerous_game_routes_reject_non_gm(tmp_path, handler):
    registry = FakeRegistry(tmp_path)
    key = ("web", "room", "bot")
    registry.items[key] = SimpleNamespace(gm_uid="gm")
    registry._save_path(key).parent.mkdir(parents=True)
    req, _api = make_request(registry, user_id="player", body={"world_id": "next"})

    response = await handler(req)

    assert response.status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "expected_call"),
    [
        (games.api_reset_game, ("reset", "web|room|bot")),
        (games.api_restart_game, ("restart", "web|room|bot")),
        (games.api_switch_world, ("switch", "web|room|bot", "next_world")),
    ],
)
async def test_gm_confirmed_state_routes_call_service(tmp_path, handler, expected_call):
    registry = FakeRegistry(tmp_path)
    registry.items[("web", "room", "bot")] = SimpleNamespace(gm_uid="gm")
    req, api = make_request(registry, body={"world_id": "next_world"})

    response = await handler(req)

    assert response.status == 200
    assert response_json(response)["ok"] is True
    assert api.calls == [expected_call]


@pytest.mark.asyncio
async def test_gm_confirmed_delete_removes_only_owned_save(tmp_path):
    registry = FakeRegistry(tmp_path)
    key = ("web", "room", "bot")
    registry.items[key] = SimpleNamespace(gm_uid="gm")
    save_dir = registry._save_path(key).parent
    save_dir.mkdir(parents=True)
    registry._save_path(key).write_text('{"gm_uid":"gm"}', encoding="utf-8")
    req, _api = make_request(registry)

    response = await games.api_delete_game(req)

    assert response.status == 200
    assert response_json(response)["ok"] is True
    assert not save_dir.exists()
    assert registry.removed == [key]


@pytest.mark.asyncio
async def test_owner_confirmed_delete_can_remove_orphaned_save(tmp_path):
    registry = FakeRegistry(tmp_path)
    key = ("web", "room", "bot")
    registry.items[key] = SimpleNamespace(gm_uid="old_browser_session")
    save_dir = registry._save_path(key).parent
    save_dir.mkdir(parents=True)
    registry._save_path(key).write_text('{"gm_uid":"old_browser_session"}', encoding="utf-8")
    req, _api = make_request(registry, user_id="current_owner_session", owner_authenticated=True)

    response = await games.api_delete_game(req)

    assert response.status == 200
    assert response_json(response)["ok"] is True
    assert not save_dir.exists()
    assert registry.removed == [key]


@pytest.mark.asyncio
async def test_batch_delete_checks_gm_per_save(tmp_path):
    registry = FakeRegistry(tmp_path)
    owned = ("web", "owned", "bot")
    foreign = ("web", "foreign", "bot")
    registry.items[owned] = SimpleNamespace(gm_uid="gm")
    registry.items[foreign] = SimpleNamespace(gm_uid="other")
    for key in (owned, foreign):
        registry._save_path(key).parent.mkdir(parents=True)
        registry._save_path(key).write_text(json.dumps({"gm_uid": registry.items[key].gm_uid}), encoding="utf-8")

    req, _api = make_request(
        registry,
        body={"game_keys": ["web|owned|bot", "web|foreign|bot"]},
    )

    response = await games.api_batch_delete_games(req)
    payload = response_json(response)

    assert response.status == 200
    assert payload["deleted"] == ["web|owned|bot"]
    assert payload["failed"] == [{"key": "web|foreign|bot", "error": "非 GM 不可删除"}]
    assert registry.removed == [owned]
    assert not registry._save_path(owned).parent.exists()
    assert registry._save_path(foreign).parent.exists()


@pytest.mark.asyncio
async def test_owner_batch_delete_can_remove_mixed_gm_saves(tmp_path):
    registry = FakeRegistry(tmp_path)
    first = ("web", "first", "bot")
    second = ("web", "second", "bot")
    registry.items[first] = SimpleNamespace(gm_uid="old_one")
    registry.items[second] = SimpleNamespace(gm_uid="old_two")
    for key in (first, second):
        registry._save_path(key).parent.mkdir(parents=True)
        registry._save_path(key).write_text(json.dumps({"gm_uid": registry.items[key].gm_uid}), encoding="utf-8")

    req, _api = make_request(
        registry,
        user_id="current_owner_session",
        owner_authenticated=True,
        body={"game_keys": ["web|first|bot", "web|second|bot"]},
    )

    response = await games.api_batch_delete_games(req)
    payload = response_json(response)

    assert response.status == 200
    assert payload["deleted"] == ["web|first|bot", "web|second|bot"]
    assert payload["failed"] == []
    assert registry.removed == [first, second]
    assert not registry._save_path(first).parent.exists()
    assert not registry._save_path(second).parent.exists()


@pytest.mark.asyncio
async def test_swipe_route_rejects_non_gm(tmp_path):
    registry = FakeRegistry(tmp_path)
    registry.items[("web", "room", "bot")] = FakeSwipeInst(gm_uid="gm")
    req, _api = make_request(registry, user_id="player", body={"swipe_index": 1})
    req.match_info = {"game_key": "web|room|bot", "round": "3"}

    response = await games.api_swipe(req)

    assert response.status == 403


@pytest.mark.asyncio
async def test_swipe_route_allows_gm_switch(tmp_path):
    registry = FakeRegistry(tmp_path)
    inst = FakeSwipeInst(gm_uid="gm")
    registry.items[("web", "room", "bot")] = inst
    req, _api = make_request(registry, body={"swipe_index": 2})
    req.match_info = {"game_key": "web|room|bot", "round": "3"}

    response = await games.api_swipe(req)

    assert response.status == 200
    assert response_json(response)["ok"] is True
    assert inst.switched_swipe == (3, 2)


@pytest.mark.asyncio
async def test_log_route_rejects_invalid_pagination(tmp_path):
    registry = FakeRegistry(tmp_path)
    req, _api = make_request(registry, query={"page": "abc"}, method="GET")

    response = await games.api_log(req)

    assert response.status == 400
    assert response_json(response)["error"] == "分页参数必须是整数"


@pytest.mark.asyncio
async def test_log_route_clamps_pagination_bounds(tmp_path):
    registry = FakeRegistry(tmp_path)
    req, api = make_request(
        registry,
        query={"page": "0", "per_page": "99999"},
        method="GET",
    )

    response = await games.api_log(req)

    assert response.status == 200
    assert api.calls == [("log", "web|room|bot", 1, 200)]


@pytest.mark.asyncio
async def test_log_route_clamps_zero_per_page(tmp_path):
    registry = FakeRegistry(tmp_path)
    req, api = make_request(registry, query={"per_page": "0"}, method="GET")

    response = await games.api_log(req)

    assert response.status == 200
    assert api.calls == [("log", "web|room|bot", 1, 1)]


@pytest.mark.asyncio
async def test_memory_route_rejects_invalid_pagination(tmp_path):
    registry = FakeRegistry(tmp_path)
    req, _api = make_request(registry, query={"limit": "abc"}, method="GET")

    response = await memory.api_memories(req)

    assert response.status == 400
    assert response_json(response)["error"] == "分页参数必须是整数"
