"""通用插件 HTTP API。"""
from __future__ import annotations
from aiohttp import web
from src.plugin_host.package_limits import MAX_PLUGIN_PACKAGE_BYTES
from src.plugin_host.runtime_protocol import PluginInvocationError, PluginProtocolError
from src.webui.routes._common import _get_api, _require_confirmed_request

async def api_plugins(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_plugins())

async def api_plugins_rescan(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    result = await _get_api(request).rescan_plugins()
    return web.json_response(result,status=200 if result.get("ok") else 400)

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

async def api_plugin_install(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    if request.content_type != "multipart/form-data":
        return web.json_response({"ok":False,"error":"插件安装需要 multipart/form-data"},status=400)
    if request.content_length and request.content_length > MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024:
        return web.json_response({"ok":False,"error":"插件包不能超过 20 MB"},status=413)
    try:
        reader = await request.multipart()
        payload = b""
        filename = ""
        overwrite = False
        async for part in reader:
            if part.name in {"overwrite", "replace"}:
                overwrite = (await part.text()).strip().lower() in {"1", "true", "yes", "on"}
                continue
            if part.name not in {"file", "package"}:
                continue
            filename = str(part.filename or "").strip()
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_PLUGIN_PACKAGE_BYTES:
                    return web.json_response({"ok":False,"error":"插件包不能超过 20 MB"},status=413)
                chunks.append(chunk)
            payload = b"".join(chunks)
        if not payload:
            return web.json_response({"ok":False,"error":"缺少 .dfplugin 插件文件"},status=400)
        if not filename.lower().endswith(".dfplugin"):
            return web.json_response({"ok":False,"error":"本地安装只接受 .dfplugin 文件；开源仓库请从插件商店安装"},status=400)
        result = await _get_api(request).install_plugin(payload, overwrite)
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_marketplace(request: web.Request) -> web.Response:
    result = await _get_api(request).list_plugin_marketplace()
    return web.json_response(result,status=200 if result.get("ok") else 502)

async def api_plugin_contributions(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_plugin_contributions(request.query.get("kind", "")))

async def api_plugin_themes(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_plugin_themes())

async def api_plugin_tools(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_plugin_tools())

async def api_plugin_tool_invoke(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok":False,"error":"请求体必须是 JSON 对象"},status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok":False,"error":"请求体必须是 JSON 对象"},status=400)
    arguments = body.get("arguments", {})
    context = body.get("context", {})
    if not isinstance(arguments, dict) or not isinstance(context, dict):
        return web.json_response({"ok":False,"error":"arguments 和 context 必须是对象"},status=400)
    try:
        result = await _get_api(request).invoke_plugin_tool(
            request.match_info["plugin_id"],
            request.match_info["tool_name"],
            arguments,
            context,
        )
    except KeyError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=404)
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    except PluginInvocationError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=422)
    except PluginProtocolError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=502)
    return web.json_response(result)

async def api_plugin_content(request: web.Request) -> web.Response:
    result = _get_api(request).list_plugin_content(
        request.query.get("kind", ""),
        request.query.get("world_id", ""),
        request.query.get("rule_id", ""),
    )
    return web.json_response(result)

async def api_plugin_content_import(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        body = await request.json()
        result = _get_api(request).import_plugin_content(
            body.get("kind", ""),
            body.get("id", body.get("resource_id", "")),
            body.get("plugin_id", ""),
            body.get("target_world_id", body.get("world_id", "")),
            bool(body.get("overwrite")),
        )
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_asset(request: web.Request) -> web.StreamResponse:
    try:
        path = _get_api(request).plugin_asset_path(request.match_info["plugin_id"], request.match_info["path"])
    except KeyError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=404)
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.FileResponse(path)

async def api_plugin_marketplace_install(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        body = await request.json()
        plugin_id = str(body.get("plugin_id") or "").strip()
        overwrite = bool(body.get("overwrite"))
        if not plugin_id:
            return web.json_response({"ok":False,"error":"缺少 plugin_id"},status=400)
        result = await _get_api(request).install_marketplace_plugin(plugin_id, overwrite)
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_marketplace_update(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        result = await _get_api(request).update_marketplace_plugin(request.match_info["plugin_id"])
    except KeyError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=404)
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_mirrors(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_plugin_mirrors())

async def api_plugin_mirror_add(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        result = _get_api(request).add_plugin_mirror(await request.json())
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_mirror_update(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        result = _get_api(request).update_plugin_mirror(request.match_info["mirror_id"], await request.json())
    except KeyError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=404)
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_mirror_delete(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        result = _get_api(request).delete_plugin_mirror(request.match_info["mirror_id"])
    except KeyError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=404)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_mirror_test(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = await _get_api(request).test_plugin_mirror(str(body.get("mirror_id") or ""))
    except (KeyError, ValueError) as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

async def api_plugin_uninstall(request: web.Request) -> web.Response:
    denied=_require_confirmed_request(request)
    if denied is not None: return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = await _get_api(request).uninstall_plugin(request.match_info["plugin_id"], bool(body.get("delete_data")))
    except KeyError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=404)
    except ValueError as exc:
        return web.json_response({"ok":False,"error":str(exc)},status=400)
    return web.json_response(result,status=200 if result.get("ok") else 400)

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
    app.router.add_post("/api/plugins/rescan",api_plugins_rescan)
    app.router.add_post("/api/plugins/install",api_plugin_install)
    app.router.add_get("/api/plugins/marketplace",api_plugin_marketplace)
    app.router.add_get("/api/plugins/contributions",api_plugin_contributions)
    app.router.add_get("/api/plugins/themes",api_plugin_themes)
    app.router.add_get("/api/plugins/tools",api_plugin_tools)
    app.router.add_post("/api/plugins/tools/{plugin_id}/{tool_name}",api_plugin_tool_invoke)
    app.router.add_get("/api/plugins/content",api_plugin_content)
    app.router.add_post("/api/plugins/content/import",api_plugin_content_import)
    app.router.add_get("/api/plugins/assets/{plugin_id}/{path:.*}",api_plugin_asset)
    app.router.add_post("/api/plugins/marketplace/install",api_plugin_marketplace_install)
    app.router.add_get("/api/plugins/mirrors",api_plugin_mirrors)
    app.router.add_post("/api/plugins/mirrors",api_plugin_mirror_add)
    app.router.add_post("/api/plugins/mirrors/test",api_plugin_mirror_test)
    app.router.add_put("/api/plugins/mirrors/{mirror_id}",api_plugin_mirror_update)
    app.router.add_delete("/api/plugins/mirrors/{mirror_id}",api_plugin_mirror_delete)
    app.router.add_get("/api/plugins/{plugin_id}",api_plugin_detail)
    app.router.add_put("/api/plugins/{plugin_id}/config",api_plugin_config)
    app.router.add_delete("/api/plugins/{plugin_id}",api_plugin_uninstall)
    app.router.add_post("/api/plugins/{plugin_id}/update",api_plugin_marketplace_update)
    app.router.add_post("/api/plugins/{plugin_id}/card-cache/clear",api_plugin_clear_card_cache)
    app.router.add_post(r"/api/plugins/{plugin_id}/{action:start|stop|restart}",api_plugin_control)
