"""通用插件 HTTP API。"""
from __future__ import annotations
from aiohttp import web
from src.webui.routes._common import _get_api, _require_confirmed_request

async def api_plugins(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_plugins())

async def api_plugin_detail(request: web.Request) -> web.Response:
    try: return web.json_response(_get_api(request).plugin_detail(request.match_info["plugin_id"]))
    except KeyError as exc: return web.json_response({"ok":False,"error":str(exc)},status=404)

async def api_plugin_config(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try: return web.json_response(await _get_api(request).update_plugin_config(request.match_info["plugin_id"],await request.json()))
    except KeyError as exc: return web.json_response({"ok":False,"error":str(exc)},status=404)
    except ValueError as exc: return web.json_response({"ok":False,"error":str(exc)},status=400)

async def api_plugin_control(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try: return web.json_response(await _get_api(request).control_plugin(request.match_info["plugin_id"],request.match_info["action"]))
    except KeyError as exc: return web.json_response({"ok":False,"error":str(exc)},status=404)

async def api_plugin_clear_card_cache(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        result = _get_api(request).clear_plugin_card_cache(request.match_info["plugin_id"])
    except KeyError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=404)
    status = 200 if result.get("ok") else 400
    return web.json_response(result,status=status)

def register_plugins(app: web.Application) -> None:
    app.router.add_get("/api/plugins",api_plugins)
    app.router.add_get("/api/plugins/{plugin_id}",api_plugin_detail)
    app.router.add_put("/api/plugins/{plugin_id}/config",api_plugin_config)
    app.router.add_post("/api/plugins/{plugin_id}/card-cache/clear",api_plugin_clear_card_cache)
    app.router.add_post(r"/api/plugins/{plugin_id}/{action:start|stop|restart}",api_plugin_control)
