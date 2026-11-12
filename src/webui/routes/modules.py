"""Content-module catalogue HTTP routes (API-00)."""

from __future__ import annotations

from aiohttp import web

from src.plugin_host.package_limits import MAX_PLUGIN_PACKAGE_BYTES
from src.webui.access_control import SEAT_TOKEN_HEADER
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
from src.localization.messages import t


def _viewer_is_gm(request: web.Request) -> bool:
    """包内容浏览的视角判定（issue #708）。

    - 有访问密码：非房主请求在中间件已被拒（401），这里按房主凭据判定，
      作为纵深防御——就算未来有旁路到达本路由，玩家视角也 fail-closed；
    - 无访问密码：没有可验证的身份边界。主机直开（无 share 标记）按 GM
      视角，保住创作工坊的正常使用；带 share 身份（``?user=`` / 席位令牌）
      的请求一律按玩家视角过滤。
    """

    if bool(request.get("owner_authenticated")):
        return True
    if bool(request.get(ACCESS_PASSWORD_CONFIGURED_KEY)):
        return False
    share_marked = bool(
        request.query.get("user")
        or request.headers.get(SEAT_TOKEN_HEADER)
        or request.get("seat_token_uid")
    )
    return not share_marked


async def api_modules(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_modules())


async def api_module_marketplace(request: web.Request) -> web.Response:
    """Online content-module catalogue (FIX-06 §8: ModulesView 的在线区块)."""

    result = await _get_api(request).list_module_marketplace(
        request.query.get("keyword", ""),
        request.query.get("ruleset", ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 502)


async def api_module_detail(request: web.Request) -> web.Response:
    result = _get_api(request).module_detail(
        request.match_info["module_id"], viewer_is_gm=_viewer_is_gm(request),
    )
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_adventures(request: web.Request) -> web.Response:
    result = _get_api(request).module_adventures(request.match_info["module_id"])
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_lorebooks(request: web.Request) -> web.Response:
    result = _get_api(request).module_lorebooks(
        request.match_info["module_id"], viewer_is_gm=_viewer_is_gm(request),
    )
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_module_cover(request: web.Request) -> web.StreamResponse:
    """已安装内容包的封面：只从包目录读文件，绝不代抓第三方地址。"""

    try:
        path = _get_api(request).module_cover_file(request.match_info["module_id"])
    except KeyError as exc:
        raise web.HTTPNotFound(text=str(exc)) from exc
    # URL 带版本号，版本不变图就不变，所以可以让浏览器长期缓存。
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def api_module_content(request: web.Request) -> web.Response:
    result = _get_api(request).module_content(
        request.match_info["module_id"],
        request.match_info["kind"],
        request.match_info["key"],
        request.query.get("language", ""),
        viewer_is_gm=_viewer_is_gm(request),
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
            {"ok": False, "error": t("webui.routes.modules.preview_multipart_required")}, status=400,
        )
    if request.content_length and request.content_length > MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024:
        return web.json_response({"ok": False, "error": t("webui.routes.modules.package_too_large")}, status=413)
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
                    return web.json_response({"ok": False, "error": t("webui.routes.modules.package_too_large")}, status=413)
                chunks.append(chunk)
            payload = b"".join(chunks)
        if not payload:
            return web.json_response({"ok": False, "error": t("webui.routes.modules.dfplugin_file_missing")}, status=400)
        if not filename.lower().endswith(".dfplugin"):
            return web.json_response({"ok": False, "error": t("webui.routes.modules.preview_dfplugin_only")}, status=400)
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
            {"ok": False, "error": t("webui.routes.modules.install_multipart_required")}, status=400,
        )
    if request.content_length and request.content_length > MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024:
        return web.json_response({"ok": False, "error": t("webui.routes.modules.package_too_large")}, status=413)
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
                    return web.json_response({"ok": False, "error": t("webui.routes.modules.package_too_large")}, status=413)
                chunks.append(chunk)
            payload = b"".join(chunks)
        if not payload:
            return web.json_response({"ok": False, "error": t("webui.routes.modules.dfplugin_file_missing")}, status=400)
        if not filename.lower().endswith(".dfplugin"):
            return web.json_response({"ok": False, "error": t("webui.routes.modules.install_dfplugin_only")}, status=400)
        result = await _get_api(request).import_module(payload, overwrite)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_external_module_preview(request: web.Request) -> web.Response:
    """Preview Foundry/Fantasy Grounds metadata without installing or executing it."""

    if request.content_type != "multipart/form-data":
        return web.json_response(
            {"ok": False, "error": t("webui.routes.modules.external_preview_multipart_required")}, status=400,
        )
    if request.content_length and request.content_length > MAX_PLUGIN_PACKAGE_BYTES:
        return web.json_response({"ok": False, "error": t("webui.routes.modules.external_module_too_large")}, status=413)
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
                    return web.json_response({"ok": False, "error": t("webui.routes.modules.external_module_too_large")}, status=413)
                chunks.append(chunk)
            payload = b"".join(chunks)
        if not payload:
            return web.json_response({"ok": False, "error": t("webui.routes.modules.external_metadata_missing")}, status=400)
        result = _get_api(request).preview_external_module_import(payload, filename)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_module_marketplace_install(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError(t("webui.routes.modules.body_must_be_json_object"))
        result = await _get_api(request).install_marketplace_module(
            request.match_info["module_id"], bool(body.get("overwrite")),
        )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=200 if result.get("ok") else 400)


def register_modules(app: web.Application) -> None:
    from src.webui.routes.module_resources import register
    register(app)
    app.router.add_get("/api/modules", api_modules)
    # 先注册静态段：``/api/modules/{module_id}`` 会吞掉 ``marketplace``。
    app.router.add_get("/api/modules/marketplace", api_module_marketplace)
    app.router.add_post("/api/modules/import", api_module_import)
    app.router.add_post("/api/modules/external/preview", api_external_module_preview)
    app.router.add_post("/api/modules/{module_id}/install", api_module_marketplace_install)
    app.router.add_get("/api/modules/{module_id}/adventures", api_module_adventures)
    app.router.add_get("/api/modules/{module_id}/lorebooks", api_module_lorebooks)
    app.router.add_get("/api/modules/{module_id}/cover", api_module_cover)
    app.router.add_get("/api/modules/{module_id}/content/{kind}/{key}", api_module_content)
    app.router.add_get("/api/modules/{module_id}/compatibility", api_module_compatibility)
    app.router.add_get("/api/modules/{module_id}/usages", api_module_usages)
    app.router.add_post("/api/modules/import/preview", api_module_import_preview)
    app.router.add_get("/api/modules/{module_id}", api_module_detail)
