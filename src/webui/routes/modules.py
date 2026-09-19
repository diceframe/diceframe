"""Content-module catalogue HTTP routes (API-00)."""

from __future__ import annotations

from aiohttp import web

from src.plugin_host.package_limits import MAX_PLUGIN_PACKAGE_BYTES
from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_modules(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_modules())


async def api_module_detail(request: web.Request) -> web.Response:
    result = _get_api(request).module_detail(request.match_info["module_id"])
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_adventures(request: web.Request) -> web.Response:
    result = _get_api(request).module_adventures(request.match_info["module_id"])
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_content(request: web.Request) -> web.Response:
    result = _get_api(request).module_content(
        request.match_info["module_id"],
        request.match_info["kind"],
        request.match_info["key"],
        request.query.get("language", ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_compatibility(request: web.Request) -> web.Response:
    result = _get_api(request).module_compatibility(request.match_info["module_id"])
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_usages(request: web.Request) -> web.Response:
    result = _get_api(request).module_usages(request.match_info["module_id"])
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_import_preview(request: web.Request) -> web.Response:
    if request.content_type != "multipart/form-data":
        return web.json_response(
            {"ok": False, "error": "模组预览需要 multipart/form-data"}, status=400,
        )
    if request.content_length and request.content_length > MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024:
        return web.json_response({"ok": False, "error": "模组包不能超过 20 MB"}, status=413)
    try:
        reader = await request.multipart()
        payload = b""
        filename = ""
        async for part in reader:
            if part.name not in {"file", "package"}:
                continue
            filename = str(part.filename or "").strip()
            chunks: list[bytes] = []
            size = 0
            while chunk := await part.read_chunk():
                size += len(chunk)
                if size > MAX_PLUGIN_PACKAGE_BYTES:
                    return web.json_response({"ok": False, "error": "模组包不能超过 20 MB"}, status=413)
                chunks.append(chunk)
            payload = b"".join(chunks)
        if not payload:
            return web.json_response({"ok": False, "error": "缺少 .dfplugin 模组文件"}, status=400)
        if not filename.lower().endswith(".dfplugin"):
            return web.json_response({"ok": False, "error": "本地预览只接受 .dfplugin 文件"}, status=400)
        result = _get_api(request).preview_module_import(payload)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_module_import(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    if request.content_type != "multipart/form-data":
        return web.json_response(
            {"ok": False, "error": "模组安装需要 multipart/form-data"}, status=400,
        )
    if request.content_length and request.content_length > MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024:
        return web.json_response({"ok": False, "error": "模组包不能超过 20 MB"}, status=413)
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
            while chunk := await part.read_chunk():
                size += len(chunk)
                if size > MAX_PLUGIN_PACKAGE_BYTES:
                    return web.json_response({"ok": False, "error": "模组包不能超过 20 MB"}, status=413)
                chunks.append(chunk)
            payload = b"".join(chunks)
        if not payload:
            return web.json_response({"ok": False, "error": "缺少 .dfplugin 模组文件"}, status=400)
        if not filename.lower().endswith(".dfplugin"):
            return web.json_response({"ok": False, "error": "本地安装只接受 .dfplugin 文件"}, status=400)
        result = await _get_api(request).import_module(payload, overwrite)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_module_marketplace_install(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        result = await _get_api(request).install_marketplace_module(
            request.match_info["module_id"], bool(body.get("overwrite")),
        )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200 if result.get("ok") else 400)


def register_modules(app: web.Application) -> None:
    app.router.add_get("/api/modules", api_modules)
    app.router.add_post("/api/modules/import", api_module_import)
    app.router.add_post("/api/modules/{module_id}/install", api_module_marketplace_install)
    app.router.add_get("/api/modules/{module_id}/adventures", api_module_adventures)
    app.router.add_get("/api/modules/{module_id}/content/{kind}/{key}", api_module_content)
    app.router.add_get("/api/modules/{module_id}/compatibility", api_module_compatibility)
    app.router.add_get("/api/modules/{module_id}/usages", api_module_usages)
    app.router.add_post("/api/modules/import/preview", api_module_import_preview)
    app.router.add_get("/api/modules/{module_id}", api_module_detail)
