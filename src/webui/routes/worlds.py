"""世界与世界书路由 handler：世界 CRUD / 模板 / 世界书条目 CRUD。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import MAX_LOREBOOK_CHARS, _get_api, _require_confirmed_request


async def api_worlds(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_worlds())


async def api_world_create(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response(_get_api(request).create_world(
        body.get("name", ""),
        body.get("description", ""),
        body.get("language", ""),
    ))


async def api_delete_world(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    world_id = request.match_info["world_id"]
    return web.json_response(_get_api(request).delete_world(world_id))


async def api_world_templates(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_world_templates())


async def api_lorebook(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_entries(request.match_info["world_id"]))


async def api_lorebook_create(request: web.Request) -> web.Response:
    body = await request.json()
    if len(str(body.get("content", ""))) > MAX_LOREBOOK_CHARS:
        return web.json_response({"error": f"世界书条目过长（上限 {MAX_LOREBOOK_CHARS} 字）"}, status=400)
    _get_api(request).save_entry(body)
    return web.json_response({"ok": True})


async def api_lorebook_generate(request: web.Request) -> web.Response:
    body = await request.json()
    prompt = str(body.get("prompt", "")).strip()
    if len(prompt) > MAX_LOREBOOK_CHARS:
        return web.json_response({"error": f"生成描述过长（上限 {MAX_LOREBOOK_CHARS} 字）"}, status=400)
    result = await _get_api(request).generate_lorebook_entries(
        request.match_info["world_id"],
        prompt,
        str(body.get("language", "") or ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_lorebook_update(request: web.Request) -> web.Response:
    entry_id = request.match_info["entry_id"]
    body = await request.json()
    if "content" in body and len(str(body.get("content", ""))) > MAX_LOREBOOK_CHARS:
        return web.json_response({"error": f"世界书条目过长（上限 {MAX_LOREBOOK_CHARS} 字）"}, status=400)
    _get_api(request).update_entry(entry_id, body)
    return web.json_response({"ok": True})


async def api_lorebook_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    entry_id = request.match_info["entry_id"]
    _get_api(request).delete_entry(entry_id)
    return web.json_response({"ok": True})


def register_worlds(app: web.Application) -> None:
    app.router.add_route("GET", "/api/worlds", api_worlds)
    app.router.add_route("POST", "/api/worlds", api_world_create)
    app.router.add_route("DELETE", "/api/worlds/{world_id}", api_delete_world)
    app.router.add_get("/api/world-templates", api_world_templates)
    app.router.add_get("/api/lorebook/{world_id}", api_lorebook)
    app.router.add_post("/api/lorebook", api_lorebook_create)
    app.router.add_post("/api/lorebook/{world_id}/generate", api_lorebook_generate)
    app.router.add_route("PUT", "/api/lorebook/{entry_id}", api_lorebook_update)
    app.router.add_route("DELETE", "/api/lorebook/{entry_id}", api_lorebook_delete)
