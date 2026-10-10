"""验证 web_server.auth_middleware 对 Bot 渠道的放行/拦截规则。

重点：bot 调公开生成端点（/api/generate-character 等）不带 X-Bot-Actor 时，
不应被“代表玩家无效”拦截——这些端点不针对特定游戏、不代表玩家。
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from types import SimpleNamespace

import web_server
from src.engine.modules import room_access
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
    assert old_default["generation_defaults_version"] == 6
    assert web_server._migrate_generation_defaults(old_default) is False

    assert web_server._migrate_generation_defaults(previous_default) is True
    assert previous_default["narrative_max_tokens"] == web_server.DEFAULT_NARRATIVE_MAX_TOKENS
    assert previous_default["generation_defaults_version"] == 6

    assert web_server._migrate_generation_defaults(custom) is True
    assert custom["narrative_max_tokens"] == 1280
    assert custom["generation_defaults_version"] == 6


def test_generation_defaults_migration_raises_old_analysis_default():
    """v4/v6: 旧默认 analysis_max_tokens=512 一路提升到 4096，自定义值保留。"""
    old_analysis = {"analysis_max_tokens": 512, "generation_defaults_version": 3}
    custom_analysis = {"analysis_max_tokens": 768, "generation_defaults_version": 3}

    assert web_server._migrate_generation_defaults(old_analysis) is True
    assert old_analysis["analysis_max_tokens"] == 4096
    assert old_analysis["generation_defaults_version"] == 6
    assert web_server._migrate_generation_defaults(old_analysis) is False

    assert web_server._migrate_generation_defaults(custom_analysis) is True
    assert custom_analysis["analysis_max_tokens"] == 768
    assert custom_analysis["generation_defaults_version"] == 6


def test_generation_defaults_migration_raises_old_summary_brief_textgen_defaults():
    """v5/v6: summary/brief/text_gen 旧默认提升到 4096，自定义值保留。"""
    old_defaults = {
        "summary_max_tokens": 400,
        "brief_max_tokens": 300,
        "text_gen_max_tokens": 400,
        "generation_defaults_version": 4,
    }
    custom = {
        "summary_max_tokens": 500,
        "brief_max_tokens": 350,
        "text_gen_max_tokens": 600,
        "generation_defaults_version": 4,
    }

    assert web_server._migrate_generation_defaults(old_defaults) is True
    assert old_defaults["summary_max_tokens"] == 4096
    assert old_defaults["brief_max_tokens"] == 4096
    assert old_defaults["text_gen_max_tokens"] == 4096
    assert old_defaults["generation_defaults_version"] == 6
    assert web_server._migrate_generation_defaults(old_defaults) is False

    assert web_server._migrate_generation_defaults(custom) is True
    assert custom["summary_max_tokens"] == 500
    assert custom["brief_max_tokens"] == 350
    assert custom["text_gen_max_tokens"] == 600
    assert custom["generation_defaults_version"] == 6


def test_generation_defaults_migration_v6_raises_all_caps_to_4096():
    """v6: 全部生成类 token 上限统一提升到 4096，自定义值保留。"""
    previous_defaults = {
        "narrative_max_tokens": 2048,
        "character_gen_max_tokens": 2048,
        "analysis_max_tokens": 1024,
        "summary_max_tokens": 1024,
        "brief_max_tokens": 1024,
        "text_gen_max_tokens": 1024,
        "generation_defaults_version": 5,
    }
    custom = {
        "narrative_max_tokens": 6144,
        "text_gen_max_tokens": 8192,
        "generation_defaults_version": 5,
    }

    assert web_server._migrate_generation_defaults(previous_defaults) is True
    assert previous_defaults["narrative_max_tokens"] == 4096
    assert previous_defaults["character_gen_max_tokens"] == 4096
    assert previous_defaults["analysis_max_tokens"] == 4096
    assert previous_defaults["summary_max_tokens"] == 4096
    assert previous_defaults["brief_max_tokens"] == 4096
    assert previous_defaults["text_gen_max_tokens"] == 4096
    assert previous_defaults["generation_defaults_version"] == 6
    assert web_server._migrate_generation_defaults(previous_defaults) is False

    assert web_server._migrate_generation_defaults(custom) is True
    assert custom["narrative_max_tokens"] == 6144
    assert custom["text_gen_max_tokens"] == 8192
    assert custom["generation_defaults_version"] == 6


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
        self.exists = True

    def bot_actor_allowed(self, game_key: str, user_id: str) -> bool:
        return user_id in self._players

    def game_detail(self, game_key: str) -> dict:
        return {"player_access_open": True, "gm_uid": "actor-1"} if self.exists else None


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


SHARE_GAME_KEY = "web|room|bot"


def _make_sse_auth_app() -> web.Application:
    """A real auth middleware in front of one game whose seat player-1 holds a token."""
    app = web.Application(middlewares=[web_server.auth_middleware])
    app["sse_tickets"] = SseTicketStore()
    instance = SimpleNamespace(
        # Fresh room_access state: no room password, player access open.
        modules={}, gm_uid="gm", players={"player-1": {}},
    )
    app["seat_token"] = room_access.issue_seat_token(instance, "player-1")
    app["api"] = SimpleNamespace(_parse_key=lambda key: tuple(key.split("|")))
    app["subsystems"] = SimpleNamespace(registry=SimpleNamespace(
        get=lambda key: instance if "|".join(key) == SHARE_GAME_KEY else None,
    ))
    app.router.add_get("/api/games/{game_key}/sse", _identity)
    return app


def _seat(app: web.Application) -> dict[str, str]:
    return {"X-Seat-Token": app["seat_token"]}


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
async def test_bot_deleted_game_returns_machine_readable_not_found(bot_enabled):
    api = FakeAPI()
    api.exists = False
    app = _make_app(api)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/games/web%7Cx%7Cy/action",
            headers={"X-Bot-Token": "tok", "X-Bot-Actor": "actor-1"},
            json={},
        )
        assert response.status == 404
        assert (await response.json())["code"] == "GAME_NOT_FOUND"


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
        r = await client.post(
            "/api/games/web%7Croom%7Cbot/sse-ticket?share=1", headers=_seat(app),
        )
        assert r.status == 200
        assert (await r.json())["user_id"] == "player-1"


@pytest.mark.asyncio
async def test_share_link_player_can_use_ruleset_gameplay_endpoints(monkeypatch):
    """专业规则的玩家分享链接不能被 owner 密码门拦住。"""
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    app.router.add_get("/api/games/{game_key}/available-actions", _identity)
    app.router.add_post("/api/games/{game_key}/intents", _identity)
    app.router.add_post("/api/games/{game_key}/decisions/{decision_id}", _identity)
    async with TestClient(TestServer(app)) as client:
        query = {"user": "player-1", "share": "1"}
        available = await client.get(
            "/api/games/web%7Croom%7Cbot/available-actions", params=query, headers=_seat(app),
        )
        intent = await client.post(
            "/api/games/web%7Croom%7Cbot/intents", params=query, headers=_seat(app),
        )
        decision = await client.post(
            "/api/games/web%7Croom%7Cbot/decisions/check-1", params=query, headers=_seat(app),
        )
        responses = (available, intent, decision)
        bodies = [await response.json() for response in responses]

    assert [response.status for response in responses] == [200] * 3
    assert all(body["user_id"] == "player-1" for body in bodies)


@pytest.mark.asyncio
async def test_share_link_player_can_use_table_talk_endpoints(monkeypatch):
    """桌边问答的读写端点必须沿用玩家分享链接身份。"""
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    app.router.add_get("/api/games/{game_key}/table-talk", _identity)
    app.router.add_post("/api/games/{game_key}/kp-question", _identity)
    async with TestClient(TestServer(app)) as client:
        query = {"user": "player-1", "share": "1", "delegate": "1"}
        feed = await client.get(
            "/api/games/web%7Croom%7Cbot/table-talk", params=query, headers=_seat(app),
        )
        question = await client.post(
            "/api/games/web%7Croom%7Cbot/kp-question", params=query, headers=_seat(app),
        )
        bodies = [await feed.json(), await question.json()]

    assert [feed.status, question.status] == [200, 200]
    assert all(body["user_id"] == "player-1" for body in bodies)


@pytest.mark.asyncio
async def test_share_link_player_can_resolve_own_luck_decision(monkeypatch):
    """CoC 分享链接必须能调用嵌套的 /checks/{id}/luck 玩家端点。"""
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    app.router.add_post("/api/games/{game_key}/checks/{check_id}/luck", _identity)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/games/web%7Croom%7Cbot/checks/check-1/luck?share=1&delegate=1",
            headers=_seat(app),
        )
        body = await response.json()

    assert response.status == 200
    assert body["user_id"] == "player-1"


@pytest.mark.asyncio
async def test_share_link_player_can_reveal_own_check(monkeypatch):
    """点击揭示骰子是玩家端点：分享链接不应被 owner 密码门拦截。"""
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    app.router.add_post("/api/games/{game_key}/checks/{check_id}/reveal", _identity)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/games/web%7Croom%7Cbot/checks/check-1/reveal?share=1",
            headers=_seat(app),
        )
        body = await response.json()

    assert response.status == 200
    assert body["user_id"] == "player-1"


@pytest.mark.asyncio
async def test_share_link_player_can_upload_and_read_game_avatar(monkeypatch):
    """玩家头像走游戏作用域端点，不应被 owner 密码门拦截。"""
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    app.router.add_post("/api/games/{game_key}/avatars", _identity)
    app.router.add_get("/api/games/{game_key}/avatars/{asset_id}", _identity)
    async with TestClient(TestServer(app)) as client:
        uploaded = await client.post(
            "/api/games/web%7Croom%7Cbot/avatars?share=1", headers=_seat(app),
        )
        loaded = await client.get(
            "/api/games/web%7Croom%7Cbot/avatars/abc?share=1", headers=_seat(app),
        )
        uploaded_body = await uploaded.json()
        loaded_body = await loaded.json()

    assert uploaded.status == 200
    assert loaded.status == 200
    assert uploaded_body["user_id"] == "player-1"
    assert loaded_body["user_id"] == "player-1"


@pytest.mark.asyncio
async def test_share_link_user_param_alone_no_longer_identifies_a_seat(monkeypatch):
    """The public uid in ?user= is not proof of a seat: no token, no identity."""
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    app.router.add_post("/api/games/{game_key}/sse-ticket", _identity)
    app.router.add_get("/api/games/{game_key}/table-talk", _identity)
    async with TestClient(TestServer(app)) as client:
        ticket = await client.post("/api/games/web%7Croom%7Cbot/sse-ticket?user=player-1&share=1")
        feed = await client.get("/api/games/web%7Croom%7Cbot/table-talk?user=player-1&share=1")
        bodies = [await ticket.json(), await feed.json()]

    assert [ticket.status, feed.status] == [401, 401]
    assert all(body["error_code"] == "SEAT_TOKEN_REQUIRED" for body in bodies)


@pytest.mark.asyncio
async def test_sse_stream_does_not_accept_user_param_for_share_players(monkeypatch):
    """Share players subscribe only through a ticket bound to their token's seat."""
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("owner-secret"))
    app = _make_sse_auth_app()
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/games/web%7Croom%7Cbot/sse?user=player-1&share=1")
        body = await response.json()

    assert response.status == 401
    assert body["error_code"] == "SEAT_TOKEN_REQUIRED"
