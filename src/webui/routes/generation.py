"""AI 生成路由 handler：生成世界 / 角色 / 文本 / 导入酒馆卡。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_generate_world(request: web.Request) -> web.Response:
    body = await request.json()
    result = await _get_api(request).generate_world(
        prompt=body.get("prompt", ""),
        rule_id=body.get("rule_id", ""),
        language=body.get("language", ""),
    )
    return web.json_response(result)


async def api_generate_rule(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    result = await _get_api(request).generate_rule(
        prompt=body.get("prompt", ""),
        source_rule_id=body.get("source_rule_id", ""),
    )
    return web.json_response(result)


async def api_generate_character(request: web.Request) -> web.Response:
    body = await request.json()
    result = await _get_api(request).generate_character(
        prompt=body.get("prompt", ""),
        game_key=body.get("game_key", ""),
        rule_id=body.get("rule_id", ""),
    )
    return web.json_response(result)


async def api_generate_text(request: web.Request) -> web.Response:
    body = await request.json()
    result = await _get_api(request).generate_text(
        prompt=body.get("prompt", ""),
        system_hint=body.get("system_hint", ""),
    )
    return web.json_response(result)


async def api_import_tavern_card(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    result = await _get_api(request).import_tavern_card(
        file_path=body.get("file_path", ""),
        file_data=body.get("file_data", ""),
        file_name=body.get("file_name", "card.png"),
        game_key=body.get("game_key", ""),
    )
    return web.json_response(result)


def register_generation(app: web.Application) -> None:
    app.router.add_post("/api/generate-world", api_generate_world)
    app.router.add_post("/api/generate-rule", api_generate_rule)
    app.router.add_post("/api/generate-character", api_generate_character)
    app.router.add_post("/api/generate-text", api_generate_text)
    app.router.add_post("/api/import-tavern-card", api_import_tavern_card)
