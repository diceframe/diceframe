"""应用更新路由：状态查询 + 下载触发（一期只下载暂存，不自动应用）。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_update_status(request: web.Request) -> web.Response:
    updater = request.app["updater"]
    return web.json_response(updater.get_status())


async def api_update_download(request: web.Request) -> web.Response:
    confirmed = _require_confirmed_request(request)
    if confirmed is not None:
        return confirmed
    kind = (request.query.get("kind") or "source").strip().lower()
    if kind not in {"source", "portable"}:
        return web.json_response({"ok": False, "error": "kind 必须为 source 或 portable"}, status=400)
    updater = request.app["updater"]
    result = await updater.download_update(_get_api(request), kind)
    return web.json_response(result)


def register_updater(app: web.Application) -> None:
    app.router.add_get("/api/system/update/status", api_update_status)
    app.router.add_post("/api/system/update/download", api_update_download)
