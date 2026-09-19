"""FIX-00 回归：Play / HTTP 基础读取链（真实 aiohttp + 真实 access control）。

Review Round 1 在顶层 head 上发现的三个回归：

1. ``api_detail`` 丢失 return（handler 隐式返回 None）；
2. ``api_game_adventure_projection`` 先 return game_detail，真正的投影不可达；
3. ``share_player_user_id`` 的 GET 白名单缺少 ``adventure``，分享链接拿不到
   AdventurePanel 依赖的只读投影。

这里全部走真实 aiohttp TestClient：真实 WebAccessControl 中间件、真实
``register_games`` 路由、真实 WebAPI + AdventureBundleLoader/Registry。
只有 LLM 未参与（这些端点本来就不调用 LLM）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import web_server
from src.adventures.bundle import AdventureBundleLoader
from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2
from src.engine.game_instance import GameInstance, GameRegistry
from src.lorebook.store import LorebookStore
from src.webui.access_password import hash_access_password
from src.webui.api import WebAPI
from src.webui.routes.games import register_games

from webapi_harness import write_world

ROOT = Path(__file__).parents[1]
BUILTIN_ADVENTURES = ROOT / "templates" / "adventures"

OWNER_PASSWORD = "owner-password"
GM_UID = "gm_user"
PLAYER_UID = "p1"
ADVENTURE_ID = "user:regression_quest"
DIRECTORY_ID = "regression-quest"
ROOM_PASSWORD = "room-password"
ROOM_TOKEN = "room-token-ok"


def _install_v2_adventure(adventures_dir: Path) -> None:
    """以真实目录布局安装一个 data-only v2 冒险：一个公开节点 + 一个 GM 节点。"""

    package = adventures_dir / DIRECTORY_ID
    shutil.copytree(BUILTIN_ADVENTURES / "lanterns_of_greymoor", package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["adventure_id"] = ADVENTURE_ID
    manifest["format"] = ADVENTURE_GRAPH_FORMAT_V2
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
    )
    adventure_path = package / "adventure.json"
    graph = json.loads(adventure_path.read_text(encoding="utf-8"))
    for key in ("steps", "choices", "start_step_id"):
        graph.pop(key, None)
    graph.update({
        "chapters": [
            {"id": "public", "name": "Public chapter", "visibility": "public"},
            {"id": "secret", "name": "GM secret", "visibility": "gm"},
        ],
        "nodes": [
            {
                "id": "gate", "type": "scene", "chapter_id": "public",
                "name": "Gate", "transitions": [{"to": "ritual"}],
            },
            {
                "id": "ritual", "type": "scene", "chapter_id": "secret",
                "visibility": "gm", "name": "Secret ritual", "transitions": [],
            },
        ],
        "objectives": [
            {"id": "find-key", "name": "Find the key", "node_ids": ["gate"]},
            {
                "id": "gm-plot", "name": "Secret plot", "visibility": "gm",
                "node_ids": ["ritual"],
            },
        ],
        "milestones": [],
        "start_node_ids": ["gate"],
        "visibility": "public",
    })
    adventure_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def play_env(tmp_path):
    data_dir = tmp_path / "data"
    worlds_dir = tmp_path / "worlds"
    rules_dir = tmp_path / "rules"
    adventures_dir = data_dir / "templates" / "adventures"
    rules_dir.mkdir(parents=True)
    adventures_dir.mkdir(parents=True)
    write_world(worlds_dir, "template_world")
    (rules_dir / "freeform_fantasy.json").write_text(
        json.dumps({
            "rule_id": "freeform_fantasy",
            "rule_name": "自由幻想",
            "dice_system": "d20",
            "combat_model": "hp_based",
            "attributes": [{"key": "str", "name": "力量", "min": 3, "max": 18}],
            "attribute_points": 60,
            "hp_formula": "20 + str",
            "max_skills": 3,
            "skill_mode": "narrative",
            "skill_pools": {"游侠": ["侦查"]},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    _install_v2_adventure(adventures_dir)

    registry = GameRegistry(data_dir / "saves")
    lorebook = LorebookStore(data_dir / "lorebook.db")
    lorebook.open()
    api = WebAPI(
        registry=registry,
        lorebook=lorebook,
        memory=None,
        rules_dir=rules_dir,
        handler=None,
        llm_client=None,
        worlds_dir=worlds_dir,
        adventures_dir=adventures_dir,
    )
    try:
        yield SimpleNamespace(
            api=api,
            registry=registry,
            adventures_dir=adventures_dir,
        )
    finally:
        lorebook.close()


def _make_game(
    play_env: SimpleNamespace,
    target_id: str,
    *,
    bind_adventure: bool = True,
    open_access: bool = True,
    room_password: str = "",
) -> tuple[str, GameInstance]:
    instance = GameInstance(
        game_key=("web", target_id, "web"),
        world_id="template_world",
        rule_id="freeform_fantasy",
    )
    instance.gm_uid = GM_UID
    instance.world_name = "template_world"
    instance.group_name = "回归局"
    instance.gm_style_override = {"note": "gm secret"}
    instance.players = {PLAYER_UID: {"character_name": "甲", "character_sheet": {}}}
    if bind_adventure:
        bundle = AdventureBundleLoader(play_env.adventures_dir).resolve(
            ADVENTURE_ID, "zh-CN",
        )
        assert instance.bind_adventure(bundle.binding("template_world")) is True
    instance.set_player_access(open_access)
    if room_password:
        instance.set_room_password(room_password)
        instance.set_room_token(ROOM_TOKEN)
    play_env.registry.register(instance)
    return "|".join(instance.game_key), instance


def _make_app(play_env: SimpleNamespace) -> web.Application:
    """只装真实中间件与真实游戏路由；这些端点的 HTTP 契约才是被测对象。"""

    app = web.Application(middlewares=[web_server.auth_middleware])
    app["api"] = play_env.api
    app["subsystems"] = SimpleNamespace(registry=play_env.registry)
    register_games(app)
    return app


@pytest.fixture(autouse=True)
def _owner_password(monkeypatch):
    monkeypatch.setitem(
        web_server.STATE, "access_token", hash_access_password(OWNER_PASSWORD),
    )


def _owner() -> dict[str, str]:
    return {"Authorization": f"Bearer {OWNER_PASSWORD}"}


def _shared(uid: str = PLAYER_UID, **extra) -> dict[str, str]:
    params = {"user": uid, "share": "1", **extra}
    return params


def _player_url(path: str, **extra) -> str:
    from urllib.parse import urlencode

    return f"{path}?{urlencode(_shared(**extra))}"


@pytest.mark.asyncio
async def test_owner_and_shared_player_read_detail_and_adventure(play_env) -> None:
    game_key, _instance = _make_game(play_env, "regression")
    app = _make_app(play_env)

    async with TestClient(TestServer(app)) as client:
        owner_detail = await client.get(
            f"/api/games/{game_key}", headers=_owner(),
        )
        player_detail = await client.get(_player_url(f"/api/games/{game_key}"))
        owner_adventure = await client.get(
            f"/api/games/{game_key}/adventure", headers=_owner(),
        )
        player_adventure = await client.get(
            _player_url(f"/api/games/{game_key}/adventure"),
        )
        owner_body = await owner_detail.json()
        player_body = await player_detail.json()
        owner_projection = await owner_adventure.json()
        player_projection = await player_adventure.json()

    assert owner_detail.status == 200
    assert owner_body["game_key"] == game_key
    assert owner_body["gm_uid"] == GM_UID
    assert owner_body["adventure_binding"]["adventure_id"] == ADVENTURE_ID
    assert owner_body["gm_style_override"] == {"note": "gm secret"}

    assert player_detail.status == 200
    assert player_body["game_key"] == game_key
    # 分享链接是玩家视角：GM-only 字段必须缺席。
    assert player_body["gm_style_override"] is None

    assert owner_adventure.status == 200
    assert owner_projection["ok"] is True
    # 回归 2：adventure 端点必须返回投影，而不是复用的 game_detail 响应。
    assert "game_key" not in owner_projection
    assert owner_projection["adventure"]["available"] is True
    assert owner_projection["adventure"]["binding"]["adventure_id"] == ADVENTURE_ID
    assert {
        node["id"] for node in owner_projection["adventure"]["projection"]["nodes"]
    } == {"gate", "ritual"}

    assert player_adventure.status == 200
    assert player_projection["adventure"]["available"] is True
    rendered = json.dumps(player_projection, ensure_ascii=False)
    assert "ritual" not in rendered
    assert "gm-plot" not in rendered


@pytest.mark.asyncio
async def test_missing_game_is_404_on_both_read_endpoints(play_env) -> None:
    app = _make_app(play_env)

    async with TestClient(TestServer(app)) as client:
        detail = await client.get("/api/games/web%7Cabsent%7Cweb", headers=_owner())
        adventure = await client.get(
            "/api/games/web%7Cabsent%7Cweb/adventure", headers=_owner(),
        )
        detail_status, adventure_status = detail.status, adventure.status

    assert detail_status == 404
    assert adventure_status == 404


@pytest.mark.asyncio
async def test_closed_player_access_blocks_shared_reads(play_env) -> None:
    game_key, _instance = _make_game(play_env, "closed", open_access=False)
    app = _make_app(play_env)

    async with TestClient(TestServer(app)) as client:
        detail = await client.get(_player_url(f"/api/games/{game_key}"))
        adventure = await client.get(_player_url(f"/api/games/{game_key}/adventure"))
        owner_adventure = await client.get(
            f"/api/games/{game_key}/adventure", headers=_owner(),
        )

    assert detail.status == 403
    assert adventure.status == 403
    # GM/owner 不受玩家入口关闭影响。
    assert owner_adventure.status == 200


@pytest.mark.asyncio
async def test_room_password_game_requires_matching_room_token(play_env) -> None:
    game_key, _instance = _make_game(
        play_env, "roomed", room_password=ROOM_PASSWORD,
    )
    app = _make_app(play_env)

    async with TestClient(TestServer(app)) as client:
        wrong = await client.get(
            _player_url(f"/api/games/{game_key}/adventure", room_token="wrong"),
        )
        correct = await client.get(
            _player_url(f"/api/games/{game_key}/adventure", room_token=ROOM_TOKEN),
        )
        owner = await client.get(
            f"/api/games/{game_key}/adventure", headers=_owner(),
        )
        wrong_body = await wrong.json()

    assert wrong.status == 403
    assert wrong_body["needs_room_password"] is True
    assert correct.status == 200
    assert owner.status == 200


@pytest.mark.asyncio
async def test_shared_player_without_user_param_is_still_rejected(play_env) -> None:
    """白名单只对带 ``user`` 的分享请求生效，匿名请求不能借 adventure 绕过鉴权。"""

    game_key, _instance = _make_game(play_env, "anonymous")
    app = _make_app(play_env)

    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{game_key}/adventure?share=1")

    assert response.status == 401


@pytest.mark.asyncio
async def test_unbound_game_projects_no_adventure(play_env) -> None:
    game_key, _instance = _make_game(play_env, "sandbox", bind_adventure=False)
    app = _make_app(play_env)

    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            f"/api/games/{game_key}/adventure", headers=_owner(),
        )
        body = await response.json()

    assert response.status == 200
    assert body == {"ok": True, "adventure": None}
