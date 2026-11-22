"""Administrator-only local embedding model catalog and installation routes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from aiohttp import web

from src.webui.routes._common import _require_confirmed_request
from src.memory.local_embedding import LocalModelError, check_local_embedding_runtime
from src.memory.model_catalog import LocalModelManager, ModelCatalogError
from src.network_proxy import effective_proxy_url


def _manager(request: web.Request) -> LocalModelManager:
    subsystems = request.app.get("subsystems")
    memory_store = getattr(subsystems, "memory_store", None)
    models_dir = memory_store.db_path.parent / "models" if memory_store is not None else None
    if models_dir is None:
        raise ModelCatalogError("embedding model storage is not ready")
    state = request.app.get("config_state") or {}
    plugin_host = request.app.get("plugin_host")
    return LocalModelManager(
        models_dir,
        proxy_url=effective_proxy_url(
            bool(state.get("proxy_enabled")), state.get("proxy_url", ""),
        ),
        mirror_manager=getattr(plugin_host, "mirrors", None),
    )


def _require_confirmed_owner(request: web.Request) -> web.Response | None:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    if not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": "owner authentication required"}, status=403)
    return None


async def api_embedding_models(request: web.Request) -> web.Response:
    if not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": "owner authentication required"}, status=403)
    try:
        return web.json_response({"ok": True, "models": await asyncio.to_thread(_manager(request).catalog)})
    except ModelCatalogError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=422)


async def api_embedding_model_install(request: web.Request) -> web.Response:
    denied = _require_confirmed_owner(request)
    if denied is not None:
        return denied
    try:
        await asyncio.to_thread(check_local_embedding_runtime)
    except LocalModelError as exc:
        return web.json_response(
            {"ok": False, "error": str(exc), "code": "LOCAL_EMBEDDING_RUNTIME_UNAVAILABLE"},
            status=422,
        )
    try:
        result = await _manager(request).install(request.match_info["model_id"])
    except ModelCatalogError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=422)
    return web.json_response({"ok": True, "model": result})


async def api_embedding_model_uninstall(request: web.Request) -> web.Response:
    """W5：删除已安装本地模型；当前启用的模型拒绝删除（fail closed）。"""

    denied = _require_confirmed_owner(request)
    if denied is not None:
        return denied
    state = request.app.get("config_state") or {}
    active_model_id = (
        str(state.get("embedding_local_model_id") or "")
        if str(state.get("embedding_source") or "") == "local" else ""
    )
    try:
        result = _manager(request).uninstall(
            request.match_info["model_id"], active_model_id=active_model_id,
        )
    except ModelCatalogError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=422)
    return web.json_response(result)


async def api_embedding_catalog_refresh(request: web.Request) -> web.Response:
    """W5-2：管理员触发拉取远端模型目录（diceframe-models index 或分片）。

    远端缓存写入 data/models/remote-catalog.json；读取路径不发网络请求。
    """

    denied = _require_confirmed_owner(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        url = str((body or {}).get("url") or "").strip()
        result = await _manager(request).refresh_remote_catalog(url)
    except ModelCatalogError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=422)
    return web.json_response(result)


async def _run_rebuild(app: web.Application) -> None:
    subsystems = app.get("subsystems")
    store = getattr(subsystems, "memory_store", None)
    if store is None or store.embedding_client is None:
        return
    app.pop("_embedding_backfill_error", None)
    try:
        while True:
            processed = await store.embed_all_pending()
            if processed:
                await asyncio.sleep(0)
                continue
            if store.embedding_status().get("pending", 0):
                detail = str(getattr(store.embedding_client, "last_error", "") or "").strip()
                app["_embedding_backfill_error"] = (
                    "向量服务未返回有效结果，待重建条目仍未完成；请检查模型连接后重试。"
                    + (f"（{detail}）" if detail else "")
                )
            return
    except Exception as exc:
        app["_embedding_backfill_error"] = f"索引重建失败：{exc}"


async def api_embedding_rebuild(request: web.Request) -> web.Response:
    denied = _require_confirmed_owner(request)
    if denied is not None:
        return denied
    subsystems = request.app.get("subsystems")
    store = getattr(subsystems, "memory_store", None)
    if store is None or store.embedding_client is None:
        return web.json_response({"ok": False, "error": "请先启用并测试一个可用的向量模型"}, status=409)
    previous = request.app.get("_embedding_backfill_task")
    if previous is not None and not previous.done():
        return web.json_response({"ok": True, "state": "rebuilding"})
    request.app.pop("_embedding_backfill_error", None)
    request.app["_embedding_backfill_task"] = asyncio.create_task(
        _run_rebuild(request.app), name="embedding-profile-rebuild-manual",
    )
    return web.json_response({"ok": True, "state": "rebuilding"})


def register_embedding_models(
    application: web.Application,
    activate: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> None:
    async def activate_model(request: web.Request) -> web.StreamResponse:
        denied = _require_confirmed_owner(request)
        if denied is not None:
            return denied
        return await activate(request)

    application.router.add_get("/api/embedding/models", api_embedding_models)
    application.router.add_post("/api/embedding/models/{model_id}/install", api_embedding_model_install)
    application.router.add_post("/api/embedding/models/{model_id}/activate", activate_model)
    application.router.add_delete("/api/embedding/models/{model_id}", api_embedding_model_uninstall)
    application.router.add_post("/api/embedding/catalog/refresh", api_embedding_catalog_refresh)
    application.router.add_post("/api/embedding/rebuild", api_embedding_rebuild)
