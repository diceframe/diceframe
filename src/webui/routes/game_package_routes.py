"""Save-package export, import, and deletion routes."""

from __future__ import annotations

import logging
from urllib.parse import quote

from aiohttp import web

from src.engine.game_instance import MAX_SAVE_PACKAGE_BYTES
from src.webui.routes._common import (
    _get_api,
    _require_confirmed_request,
)

logger = logging.getLogger("trpg")
from src.webui.routes.game_route_common import _can_delete_save


class _SavePackageTooLarge(ValueError):
    pass


async def _read_save_upload(
    reader,
    *,
    max_bytes: int = MAX_SAVE_PACKAGE_BYTES,
) -> bytes:
    payload = bytearray()
    found = False
    async for part in reader:
        if part.name not in {"file", "save"}:
            continue
        if found:
            raise ValueError("只能上传一个存档文件")
        found = True
        while True:
            chunk = await part.read_chunk()
            if not chunk:
                break
            if len(payload) + len(chunk) > max_bytes:
                raise _SavePackageTooLarge
            payload.extend(chunk)
    return bytes(payload)


async def api_export_game(request: web.Request) -> web.Response:
    """导出单存档为可移植 zip（含状态、完整历史与冒险头图）。"""
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    inst = api.get_game_instance(game_key)
    if not inst:
        return web.json_response({"error": "not found"}, status=404)
    session_uid = request.get("user_id", "")
    if not session_uid or session_uid != inst.gm_uid:
        return web.json_response({"error": "仅 GM 可导出游戏"}, status=403)

    result = api.export_game_package(game_key)
    if not result.get("ok"):
        error = {"error": str(result.get("error") or "导出失败")}
        if result.get("error_code"):
            error["error_code"] = str(result["error_code"])
        return web.json_response(error, status=int(result.get("status") or 500))
    body = result["payload"]
    filename = str(result["filename"])
    ascii_fallback = str(result["ascii_filename"])
    return web.Response(
        body=body,
        content_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(body)),
        },
    )


async def api_import_game(request: web.Request) -> web.Response:
    """导入存档 zip 为新对局（owner 级操作，需确认头）。"""
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    api = _get_api(request)
    if request.content_type != "multipart/form-data":
        return web.json_response(
            {"ok": False, "error": "存档导入需要 multipart/form-data"}, status=400
        )
    if request.content_length and request.content_length > MAX_SAVE_PACKAGE_BYTES:
        return web.json_response(
            {"ok": False, "error": "存档包不能超过 50 MB"}, status=413
        )
    try:
        reader = await request.multipart()
        payload = await _read_save_upload(reader)
        if not payload:
            return web.json_response(
                {"ok": False, "error": "缺少存档 zip 文件"}, status=400
            )
        result = await api.import_game_package(payload)
    except _SavePackageTooLarge:
        return web.json_response(
            {"ok": False, "error": "存档包不能超过 50 MB"}, status=413
        )
    except Exception as exc:
        logger.exception("导入存档失败")
        return web.json_response(
            {"ok": False, "error": f"导入存档失败：{exc}"}, status=400
        )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_batch_delete_games(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    session_uid = request.get("user_id", "")
    if not session_uid:
        return web.json_response(
            {"ok": False, "error": "未登录，无法删除存档"}, status=403
        )
    api = _get_api(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "请求体不是合法 JSON"}, status=400
        )
    keys = body.get("game_keys")
    if not isinstance(keys, list) or not keys:
        return web.json_response(
            {"ok": False, "error": "game_keys 必须是非空列表"}, status=400
        )
    if len(keys) > 100:
        return web.json_response({"ok": False, "error": "单次最多 100 个"}, status=400)

    deleted: list[str] = []
    failed: list[dict] = []
    for raw in keys:
        if not isinstance(raw, str) or not raw:
            failed.append({"key": str(raw), "error": "key 类型错误"})
            continue
        access = api.saved_game_access(raw)
        if not access.get("exists"):
            failed.append({"key": raw, "error": "游戏不存在或存档目录不存在"})
            continue
        gm_uid = str(access.get("gm_uid") or "")
        if not gm_uid:
            failed.append({"key": raw, "error": "存档缺少 GM 身份，拒绝删除"})
            continue
        if not _can_delete_save(request, session_uid, gm_uid):
            failed.append({"key": raw, "error": "非 GM 不可删除"})
            continue
        result = api.delete_game(raw)
        if result.get("ok"):
            deleted.append(raw)
        else:
            failed.append({"key": raw, "error": str(result.get("error") or "删除失败")})
    return web.json_response({"ok": True, "deleted": deleted, "failed": failed})


async def api_delete_game(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    session_uid = request.get("user_id", "")
    if not session_uid:
        return web.json_response({"error": "未登录，无法删除存档"}, status=403)
    api = _get_api(request)
    game_key = request.match_info["game_key"]
    access = api.saved_game_access(game_key)
    if not access.get("exists"):
        return web.json_response({"error": "not found"}, status=404)
    gm_uid = str(access.get("gm_uid") or "")
    if not gm_uid:
        return web.json_response({"error": "存档缺少 GM 身份，拒绝删除"}, status=403)
    if not _can_delete_save(request, session_uid, gm_uid):
        return web.json_response({"error": "仅 GM 可删除游戏"}, status=403)
    result = api.delete_game(request.match_info["game_key"])
    if not result.get("ok"):
        error = str(result.get("error") or "删除失败")
        status = 404 if error == "存档目录不存在" else 500
        return web.json_response({"error": error}, status=status)
    return web.json_response(result)
