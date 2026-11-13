"""Administrator-only local embedding model catalog and installation routes."""

from __future__ import annotations

import asyncio
from aiohttp import web

from src.webui.routes._common import _require_confirmed_request
from src.memory.model_catalog import LocalModelManager, ModelCatalogError
from src.network_proxy import effective_proxy_url


def _manager(request: web.Request) -> LocalModelManager:
    subsystems = request.app.get("subsystems")
    memory_store = getattr(subsystems, "memory_store", None)
    models_dir = memory_store.db_path.parent / "models" if memory_store is not None else None
    if models_dir is None:
        raise ModelCatalogError("embedding model storage is not ready")
    state = request.app.get("config_state") or {}
    return LocalModelManager(
        models_dir,
        proxy_url=effective_proxy_url(
            bool(state.get("proxy_enabled")), state.get("proxy_url", ""),
        ),
    )


async def api_embedding_models(request: web.Request) -> web.Response:
    if not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": "owner authentication required"}, status=403)
    try:
        return web.json_response({"ok": True, "models": _manager(request).catalog()})
    except ModelCatalogError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=422)


async def api_embedding_model_install(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        result = await _manager(request).install(request.match_info["model_id"])
    except ModelCatalogError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=422)
    return web.json_response({"ok": True, "model": result})


async def _run_rebuild(app: web.Application) -> None:
    subsystems = app.get("subsystems")
    store = getattr(subsystems, "memory_store", None)
    if store is None or store.embedding_client is None:
        return
    while await store.embed_all_pending():
        await asyncio.sleep(0)


async def api_embedding_rebuild(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    if not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": "owner authentication required"}, status=403)
    subsystems = request.app.get("subsystems")
    store = getattr(subsystems, "memory_store", None)
    if store is None or store.embedding_client is None:
        return web.json_response({"ok": False, "error": "请先启用并测试一个可用的向量模型"}, status=409)
    previous = request.app.get("_embedding_backfill_task")
    if previous is not None and not previous.done():
        return web.json_response({"ok": True, "state": "rebuilding"})
    request.app["_embedding_backfill_task"] = asyncio.create_task(
        _run_rebuild(request.app), name="embedding-profile-rebuild-manual",
    )
    return web.json_response({"ok": True, "state": "rebuilding"})


def register_embedding_models(application: web.Application) -> None:
    application.router.add_get("/api/embedding/models", api_embedding_models)
    application.router.add_post("/api/embedding/models/{model_id}/install", api_embedding_model_install)
    application.router.add_post("/api/embedding/rebuild", api_embedding_rebuild)
