"""Canonical Lorebook v2 import routes; legacy world routes remain façades."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from aiohttp import web

from src.webui.routes._common import _get_api


async def api_lorebooks(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_lorebooks(
        request.query.get("world_id", ""), request.query.get("game_key", ""),
    ))


def _result_response(result: dict) -> web.Response:
    if result.get("ok"):
        return web.json_response(result)
    error = str(result.get("error") or "Request failed")
    status = 409 if "primary" in error.lower() or "already exists" in error.lower() else (
        404 if "not found" in error.lower() else 400
    )
    return web.json_response(result, status=status)


def _json_safe(value):
    """Serialize preview dataclasses for lightweight API adapters and tests."""

    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


async def api_lorebook_create(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "request must be an object"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "book must be an object"}, status=400)
    return _result_response(_get_api(request).create_lorebook(body))


async def api_lorebook_update(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "request must be an object"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "book must be an object"}, status=400)
    return _result_response(_get_api(request).update_lorebook(request.match_info["book_id"], body))


async def api_lorebook_delete(request: web.Request) -> web.Response:
    return _result_response(_get_api(request).delete_lorebook(request.match_info["book_id"]))


async def api_lorebook_bindings(request: web.Request) -> web.Response:
    if request.method == "GET":
        return _result_response(_get_api(request).list_lorebook_bindings(request.match_info["book_id"]))
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "request must be an object"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "binding must be an object"}, status=400)
    if body.get("scope_kind") not in {"global", "world", "game", "character"}:
        return web.json_response({"ok": False, "error": "invalid scope_kind"}, status=400)
    return _result_response(_get_api(request).create_lorebook_binding(request.match_info["book_id"], body))


async def api_lorebook_binding_update(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "request must be an object"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "binding must be an object"}, status=400)
    if "scope_kind" in body and body["scope_kind"] not in {"global", "world", "game", "character"}:
        return web.json_response({"ok": False, "error": "invalid scope_kind"}, status=400)
    return _result_response(_get_api(request).update_lorebook_binding(request.match_info["binding_id"], body))


async def api_lorebook_binding_delete(request: web.Request) -> web.Response:
    return _result_response(_get_api(request).delete_lorebook_binding(request.match_info["binding_id"]))


async def api_lorebook_import_preview(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "Lorebook payload must be an object"}, status=400)
    return web.json_response(_json_safe(_get_api(request).preview_lorebook_import(body)))


async def api_lorebook_import_commit(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("payload"), dict):
        return web.json_response({"ok": False, "error": "payload must be an object"}, status=400)
    result = _get_api(request).commit_lorebook_import(
        body["payload"], body.get("binding"), body.get("book_id"),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_lorebook_entries(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_lorebook_entries(request.match_info["book_id"]))


async def api_lorebook_entry_save(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "entry must be an object"}, status=400)
    return web.json_response(_get_api(request).save_lorebook_entry(request.match_info["book_id"], body))


async def api_lorebook_entry_update(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "entry must be an object"}, status=400)
    body["id"] = request.match_info["entry_id"]
    result = _get_api(request).save_lorebook_entry(request.match_info["book_id"], body)
    return web.json_response(result, status=200 if result.get("ok") else _entry_error_status(result))


async def api_lorebook_entry_delete(request: web.Request) -> web.Response:
    # The URL book_id participates in the lookup: an entry owned by another
    # book is never reachable through this route.
    result = _get_api(request).delete_lorebook_entry(
        request.match_info["book_id"], request.match_info["entry_id"],
    )
    return web.json_response(result, status=200 if result.get("ok") else _entry_error_status(result))


async def api_lorebook_entry_move(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "request must be an object"}, status=400)
    result = _get_api(request).move_lorebook_entry(
        request.match_info["book_id"], request.match_info["entry_id"],
        str(body.get("target_book_id") or ""),
    )
    if result.get("ok"):
        return web.json_response(result)
    code = str(result.get("error_code") or "")
    if code == "target_book_not_found":
        return web.json_response(result, status=404)
    return web.json_response(result, status=_entry_error_status(result))


def _entry_error_status(result: dict) -> int:
    """Map entry ownership failures onto explicit HTTP semantics (404 / 409)."""

    code = str(result.get("error_code") or "")
    if code == "entry_book_mismatch":
        return 409
    return 404


async def api_lorebook_export(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).export_lorebook(request.match_info["book_id"]))


async def api_lorebook_activation_preview(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "request must be an object"}, status=400)
    result = await _get_api(request).lorebook_activation_preview(body)
    return web.json_response(result, status=200 if result.get("ok") else 400)


def register_lorebooks(app: web.Application) -> None:
    app.router.add_get("/api/lorebooks", api_lorebooks)
    app.router.add_post("/api/lorebooks", api_lorebook_create)
    app.router.add_put("/api/lorebooks/{book_id}", api_lorebook_update)
    app.router.add_delete("/api/lorebooks/{book_id}", api_lorebook_delete)
    app.router.add_get("/api/lorebooks/{book_id}/bindings", api_lorebook_bindings)
    app.router.add_post("/api/lorebooks/{book_id}/bindings", api_lorebook_bindings)
    app.router.add_put("/api/lorebook-bindings/{binding_id}", api_lorebook_binding_update)
    app.router.add_delete("/api/lorebook-bindings/{binding_id}", api_lorebook_binding_delete)
    app.router.add_post("/api/lorebooks/import/preview", api_lorebook_import_preview)
    app.router.add_post("/api/lorebooks/import", api_lorebook_import_commit)
    app.router.add_get("/api/lorebooks/{book_id}/entries", api_lorebook_entries)
    app.router.add_post("/api/lorebooks/{book_id}/entries", api_lorebook_entry_save)
    app.router.add_put("/api/lorebooks/{book_id}/entries/{entry_id}", api_lorebook_entry_update)
    app.router.add_post(
        "/api/lorebooks/{book_id}/entries/{entry_id}/move", api_lorebook_entry_move,
    )
    app.router.add_delete("/api/lorebooks/{book_id}/entries/{entry_id}", api_lorebook_entry_delete)
    app.router.add_get("/api/lorebooks/{book_id}/export", api_lorebook_export)
    app.router.add_post("/api/lorebooks/activation-preview", api_lorebook_activation_preview)
