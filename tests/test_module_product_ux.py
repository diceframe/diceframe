"""FIX-06 验收：Module 产品 UX 收尾的服务端契约（施工单 §8）。

覆盖：

```text
ModulesView 在线区块 → 只列 content-pack；installed / update_available 由服务端判定
市场不可达            → ok=False + error_code（不是"空列表"）
ModuleDetail 按钮     → 一律取服务端 guard 结果（actions/bound_games），与 enforce 同源
HTTP                  → /api/modules/marketplace 不被 {module_id} 路由吞掉
```

修复前：模块页面只有"已安装 + 本地导入"（在线模组要去插件页），详情页没有任何受保护
操作的可用性信息——按钮状态只能由前端猜。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.engine import persistence
from src.engine.game_instance import GameInstance, GameState
from src.webui.services.modules import (
    MODULE_ACTIONS,
    ModuleInUse,
    module_marketplace,
)
from tests.test_module_install_hardening import (
    ADVENTURE_ID,
    MODULE_ID,
    _make_api,
    _module_app,
    _module_files,
    _zip_payload,
)


class _FakeMarketIndex:
    """Marketplace index double: the host annotates installed state itself."""

    def __init__(self, items: list[dict]) -> None:
        self._items = items
        self.calls = 0

    async def list_plugins(self) -> dict:
        self.calls += 1
        return {"ok": True, "plugins": list(self._items), "total": len(self._items)}


class _BrokenMarketIndex:
    async def list_plugins(self) -> dict:
        return {"ok": False, "error": "mirror unreachable", "plugins": []}


def _market_item(plugin_id: str, *, plugin_type: str = "content-pack", **overrides) -> dict:
    item = {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "1.0.0",
        "plugin_type": plugin_type,
        "content_profile": "adventure-module",
        "content_delivery_mode": "catalog",
        "adventure_count": 1,
        "ruleset_targets": ["core:dnd2024"],
        "tags": ["adventure"],
        "trust_level": "community",
        "distribution": "repository",
        "installable": True,
    }
    item.update(overrides)
    return item


@pytest.fixture()
def env(tmp_path):
    api, host, lorebook = _make_api(tmp_path / "data")
    try:
        yield SimpleNamespace(
            api=api, host=host, lorebook=lorebook,
            data_dir=tmp_path / "data", tmp_path=tmp_path,
        )
    finally:
        lorebook.close()


async def _persist_bound_save(env, *, target_id: str = "room-a") -> str:
    registry = env.api._reg
    instance = GameInstance(game_key=("web", target_id, "bot"), world_id="greymoor")
    instance.state = GameState.PAUSED
    assert instance.bind_adventure({
        "adventure_id": ADVENTURE_ID, "version": "1.0.0",
        "format": "diceframe:adventure-graph-v1",
        "content_digest": "sha256:deadbeef", "world_id": "greymoor",
    })
    await persistence.save(registry, instance)
    registry.remove(instance.game_key)
    return "|".join(instance.game_key)


# ---- §8 ModulesView: online modules ----------------------------------------


@pytest.mark.asyncio
async def test_online_modules_list_only_content_packs(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))
    env.host.marketplace = _FakeMarketIndex([
        _market_item(MODULE_ID, version="1.0.0", latest={"version": "1.2.0"}),
        _market_item("other-module", latest={"version": "0.9.0"}),
        _market_item("some-tool", plugin_type="tool"),
    ])

    result = await env.api.list_module_marketplace()

    assert result["ok"] is True
    assert [item["id"] for item in result["modules"]] == [MODULE_ID, "other-module"]
    installed = result["modules"][0]
    assert installed["installed"] is True
    assert installed["installed_version"] == "1.0.0"
    # 已装 1.0.0、市场最新 1.2.0 → 有可用更新（展示级比较，不猜）。
    assert installed["update_available"] is True
    fresh = result["modules"][1]
    assert fresh["installed"] is False
    assert fresh["update_available"] is False
    assert fresh["adventure_count"] == 1
    assert fresh["ruleset_targets"] == ["core:dnd2024"]


@pytest.mark.asyncio
async def test_online_modules_keyword_search_and_offline_state(env) -> None:
    env.host.marketplace = _FakeMarketIndex([
        _market_item("castle-module", tags=["castle"]),
        _market_item("harbor-module", tags=["harbor"]),
    ])

    filtered = await env.api.list_module_marketplace("harbor")
    assert [item["id"] for item in filtered["modules"]] == ["harbor-module"]

    env.host.marketplace = _BrokenMarketIndex()
    offline = await env.api.list_module_marketplace()
    # 市场故障必须是"不可用"，不能伪装成"没有模组"。
    assert offline["ok"] is False
    assert offline["error_code"] == "MODULE_MARKETPLACE_UNAVAILABLE"
    assert offline["modules"] == []


@pytest.mark.asyncio
async def test_online_modules_report_unwired_marketplace(env) -> None:
    deps = replace(env.api._module_dependencies, list_marketplace_plugins=None)

    result = await module_marketplace(deps)

    assert result["ok"] is False
    assert result["error_code"] == "MODULE_MARKETPLACE_UNAVAILABLE"


# ---- §8 ModuleDetail: buttons follow the server guard -----------------------


@pytest.mark.asyncio
async def test_module_detail_actions_are_allowed_until_a_save_binds_it(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))

    free = env.api.module_detail(MODULE_ID)["module"]
    assert free["bound_games"] == []
    assert set(free["actions"]) == set(MODULE_ACTIONS)
    assert all(state["allowed"] for state in free["actions"].values())

    game_key = await _persist_bound_save(env)

    bound = env.api.module_detail(MODULE_ID)["module"]
    assert [row["game_key"] for row in bound["bound_games"]] == [game_key]
    for action in ("update", "disable", "uninstall"):
        state = bound["actions"][action]
        assert state["allowed"] is False
        assert state["reason"] == "MODULE_IN_USE"
        assert [row["game_key"] for row in state["games"]] == [game_key]


@pytest.mark.asyncio
async def test_module_detail_guard_never_disagrees_with_enforcement(env) -> None:
    """按钮状态与 enforce 是同一个判定：allowed=false ⇒ 服务端一定拒绝。"""

    await env.api.import_module(_zip_payload(_module_files(catalog=True)))
    await _persist_bound_save(env)

    state = env.api.module_detail(MODULE_ID)["module"]["actions"]["uninstall"]
    assert state["allowed"] is False
    with pytest.raises(ModuleInUse):
        await env.api.uninstall_plugin(MODULE_ID)

    # 存在使用中的存档时，列表页也要能读到原因（不是只有详情页）。
    modules = {item["id"]: item for item in env.api.list_modules()["modules"]}
    assert MODULE_ID in modules


@pytest.mark.asyncio
async def test_module_detail_lists_adventures_without_a_synced_registry(env) -> None:
    """§8 Adventures 段不能依赖"碰巧同步过 registry"（server restart 后必须还在）。"""

    await env.api.import_module(_zip_payload(_module_files(catalog=True)))

    api2, host2, lorebook2 = _make_api(env.data_dir)
    try:
        host2.discover()
        # 新进程里 discovery 找到了包，但 plugin 来源尚未进入注册表。
        assert api2._adventure_source_registry.source_for("plugin", MODULE_ID) is None

        detail = api2.module_detail(MODULE_ID)["module"]
        assert [row["adventure_id"] for row in detail["adventures"]] == [ADVENTURE_ID]
        assert detail["adventures"][0]["version"] == "1.0.0"
    finally:
        lorebook2.close()


@pytest.mark.asyncio
async def test_module_detail_guard_is_unknown_for_non_modules(env) -> None:
    # 通用插件面可以装 tool；它不该出现在模组详情里（§8：模组库不是插件列表）。
    await env.api.install_plugin(_zip_payload(_module_files(
        plugin_type="tool", adventure=False, content=False,
    )))

    detail = env.api.module_detail(MODULE_ID)

    assert detail["ok"] is False
    assert detail["error_code"] == "MODULE_NOT_FOUND"


# ---- HTTP surface -----------------------------------------------------------


@pytest.mark.asyncio
async def test_module_marketplace_route_is_not_shadowed_by_module_detail(env) -> None:
    env.host.marketplace = _FakeMarketIndex([_market_item("castle-module")])

    async with TestClient(TestServer(_module_app(env.api))) as client:
        response = await client.get("/api/modules/marketplace")
        body = await response.json()

    assert response.status == 200
    assert body["ok"] is True
    assert [item["id"] for item in body["modules"]] == ["castle-module"]


@pytest.mark.asyncio
async def test_module_marketplace_route_reports_offline_market(env) -> None:
    env.host.marketplace = _BrokenMarketIndex()

    async with TestClient(TestServer(_module_app(env.api))) as client:
        response = await client.get("/api/modules/marketplace")
        body = await response.json()

    assert response.status == 502
    assert body["error_code"] == "MODULE_MARKETPLACE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_module_detail_route_serves_guard_state(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))
    game_key = await _persist_bound_save(env)

    async with TestClient(TestServer(_module_app(env.api))) as client:
        response = await client.get(f"/api/modules/{MODULE_ID}")
        body = await response.json()

    assert response.status == 200
    module = body["module"]
    assert [row["game_key"] for row in module["bound_games"]] == [game_key]
    assert module["actions"]["disable"]["allowed"] is False
