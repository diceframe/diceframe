"""验证 web_server.auth_middleware 对 Bot 渠道的放行/拦截规则。

重点：bot 调公开生成端点（/api/generate-character 等）不带 X-Bot-Actor 时，
不应被“代表玩家无效”拦截——这些端点不针对特定游戏、不代表玩家。
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import web_server
from src.webui.access_password import hash_access_password
from src.webui.sse_ticket import SseTicketStore


def test_generation_defaults_migration_raises_only_the_old_narrative_default():
    old_default = {"narrative_max_tokens": 1024}
    previous_default = {
        "narrative_max_tokens": 1536,
        "generation_defaults_version": 2,
    }
    custom = {"narrative_max_tokens": 1280}

    assert web_server._migrate_generation_defaults(old_default) is True
    assert old_default["narrative_max_tokens"] == web_server.DEFAULT_NARRATIVE_MAX_TOKENS
    assert old_default["generation_defaults_version"] == 3
    assert web_server._migrate_generation_defaults(old_default) is False

    assert web_server._migrate_generation_defaults(previous_default) is True
    assert previous_default["narrative_max_tokens"] == web_server.DEFAULT_NARRATIVE_MAX_TOKENS
    assert previous_default["generation_defaults_version"] == 3

    assert web_server._migrate_generation_defaults(custom) is True
    assert custom["narrative_max_tokens"] == 1280
    assert custom["generation_defaults_version"] == 3


def test_invalid_config_is_quarantined_instead_of_silently_discarded(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{坏掉的 JSON", encoding="utf-8")

    loaded = web_server._load_json_object(path, "测试配置")

    assert loaded == {}
    assert not path.exists()
    backups = list(tmp_path.glob("config.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{坏掉的 JSON"


def test_non_object_config_is_quarantined(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text("[]", encoding="utf-8")

    loaded = web_server._load_json_object(path, "测试敏感配置")

    assert loaded == {}
    assert not path.exists()
    assert len(list(tmp_path.glob("secrets.corrupt-*.json"))) == 1


async def _ok(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _identity(request: web.Request) -> web.Response:
    return web.json_response({"user_id": request.get("user_id", "")})


class FakeAPI:
    def __init__(self) -> None:
        self._players = {"actor-1"}

    def bot_actor_allowed(self, game_key: str, user_id: str) -> bool:
        return user_id in self._players

    def game_detail(self, game_key: str) -> dict:
        return {"player_access_open": True, "gm_uid": "actor-1"}


class FakePluginHost:
    def authenticate_api_token(self, token: str):
        if token == "plugin-token":
            return {"plugin_id": "test-adapter", "permissions": ["diceframe.http"]}
        return None


def _make_app(api: FakeAPI) -> web.Application:
    app = web.Application()
    app.middlewares.append(web_server.auth_middleware)
    app["api"] = api
    app["plugin_host"] = None
    app.router.add_post("/api/generate-character", _ok)
    app.router.add_post("/api/games", _ok)
    app.router.add_post("/api/games/{game_key}/action", _ok)
    app.router.add_get("/api/bot/ping", _ok)
    app.router.add_post("/api/config/bot-token", web_server.api_bot_token_post)
    return app


def _make_sse_auth_app() -> web.Application:
    app = web.Application(middlewares=[web_server.auth_middleware])
    app["sse_tickets"] = SseTicketStore()
    app.router.add_get("/api/games/{game_key}/sse", _identity)
    return app


@pytest.fixture
def bot_enabled(monkeypatch):
    monkeypatch.delenv("TRPG_BOT_TOKEN", raising=False)
    monkeypatch.setitem(web_server.STATE, "bot_token", "tok")
    # 关掉 owner 门，确保请求走 bot 分支而非 owner 分支
    monkeypatch.setitem(web_server.STATE, "access_token", "")


@pytest.mark.asyncio
async def test_bot_generate_character_without_actor_is_allowed(bot_enabled):
    app = _make_app(FakeAPI())
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/generate-character", headers={"X-Bot-Token": "tok"}, json={})
        assert r.status == 200


@pytest.mark.asyncio
async def test_bot_api_does_not_depend_on_qq_plugin_enabled(bot_enabled, monkeypatch):
    monkeypatch.setitem(web_server.STATE, "qq_bot_enabled", False)
    app = _make_app(FakeAPI())
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/bot/ping", headers={"X-Bot-Token": "tok"})
        assert r.status == 200


@pytest.mark.asyncio
async def test_bot_ping_rejects_wrong_global_token(bot_enabled):
    app = _make_app(FakeAPI())
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/bot/ping", headers={"X-Bot-Token": "wrong"})
        assert r.status == 401
        body = await r.json()
        assert body["error"] == "Bot 服务未授权"


@pytest.mark.asyncio
async def test_plugin_specific_token_authenticates_without_global_token(bot_enabled):
    app = _make_app(FakeAPI())
    app["plugin_host"] = FakePluginHost()
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/bot/ping", headers={"X-Bot-Token": "plugin-token"})
        assert r.status == 200


@pytest.mark.asyncio
async def test_bot_game_action_without_actor_rejected(bot_enabled):
    app = _make_app(FakeAPI())
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/games/web%7Cx%7Cy/action", headers={"X-Bot-Token": "tok"}, json={})
        assert r.status == 403
        body = await r.json()
        assert body["error"] == "Bot 代表玩家无效"


@pytest.mark.asyncio
async def test_bot_game_action_with_valid_actor_allowed(bot_enabled):
    app = _make_app(FakeAPI())
    async with TestClient(TestServer(app)) as client:
        r = await client.post(
            "/api/games/web%7Cx%7Cy/action",
            headers={"X-Bot-Token": "tok", "X-Bot-Actor": "actor-1"},
            json={},
        )
        assert r.status == 200


@pytest.mark.asyncio
async def test_bot_non_public_empty_gamekey_path_rejected(bot_enabled):
    # game_key 为空但路径不在公开白名单（如 /api/games 列表），仍按代表玩家无效拒绝
    app = _make_app(FakeAPI())
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/games", headers={"X-Bot-Token": "tok"}, json={})
        assert r.status == 403


@pytest.mark.asyncio
async def test_owner_can_reveal_and_regenerate_bot_token(bot_enabled, monkeypatch):
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    app = _make_app(FakeAPI())
    async with TestClient(TestServer(app)) as client:
        revealed = await client.post(
            "/api/config/bot-token",
            headers={"X-TRPG-Confirm": "true"},
            json={"action": "reveal"},
        )
        assert revealed.status == 200
        assert (await revealed.json())["token"] == "tok"

        regenerated = await client.post(
            "/api/config/bot-token",
            headers={"X-TRPG-Confirm": "true"},
            json={"action": "regenerate"},
        )
        assert regenerated.status == 200
        body = await regenerated.json()
        assert body["regenerated"] is True
        assert body["token"] != "tok"
        assert web_server.STATE["bot_token"] == body["token"]


def test_ensure_bot_token_migrates_legacy_qq_secret(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "plugins" / "qq-napcat"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "secrets.json").write_text('{"bot_token":"legacy-token"}', encoding="utf-8")
    monkeypatch.setattr(web_server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setitem(web_server.STATE, "bot_token", "")

    assert web_server._ensure_bot_token() == "legacy-token"
    assert web_server.STATE["bot_token"] == "legacy-token"


@pytest.mark.asyncio
async def test_sse_ticket_authenticates_once_without_exposing_owner_password(monkeypatch):
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    ticket, _ = app["sse_tickets"].issue("web|room|bot", "player-1")
    async with TestClient(TestServer(app)) as client:
        accepted = await client.get("/api/games/web%7Croom%7Cbot/sse", params={"ticket": ticket})
        assert accepted.status == 200
        assert (await accepted.json())["user_id"] == "player-1"

        reused = await client.get("/api/games/web%7Croom%7Cbot/sse", params={"ticket": ticket})
        assert reused.status == 401


@pytest.mark.asyncio
async def test_sse_query_no_longer_accepts_owner_password(monkeypatch):
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/games/web%7Croom%7Cbot/sse", params={"token": "owner-secret"})
        assert response.status == 401


@pytest.mark.asyncio
async def test_share_link_player_can_post_sse_ticket(monkeypatch):
    """share-link 玩家通过 ?user=&share=1 调 POST /sse-ticket 不应被 owner 门 401。

    回归：_share_player_user_id 的 POST 白名单漏了 sse-ticket，玩家拿不到 ticket、
    EventSource 反复重连，narration_delta 全丢、最终经轮询一次性返回（无流式）。
    """
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    app.router.add_post("/api/games/{game_key}/sse-ticket", _identity)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/games/web%7Croom%7Cbot/sse-ticket?user=player-1&share=1")
        assert r.status == 200
        assert (await r.json())["user_id"] == "player-1"
