"""角色卡路由 handler：列表 / 保存 / 更新 / 删除 / 导入。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_character_cards(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_character_cards())


async def api_game_character_cards(request: web.Request) -> web.Response:
    api = _get_api(request)
    if not api.game_detail(request.match_info["game_key"]):
        return web.json_response({"error": "游戏不存在"}, status=404)
    return web.json_response(api.list_character_cards())


async def api_character_card_save(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response(_get_api(request).save_character_card(body))


async def api_character_card_update(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response(_get_api(request).update_character_card(request.match_info["card_id"], body))


async def api_character_card_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    return web.json_response(_get_api(request).delete_character_card(request.match_info["card_id"]))


async def api_character_card_import(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response(await _get_api(request).import_character_card(
        file_data=body.get("file_data", ""),
        file_name=body.get("file_name", "card.json"),
    ))


def register_character_cards(app: web.Application) -> None:
    app.router.add_get("/api/character-cards", api_character_cards)
    app.router.add_get("/api/games/{game_key}/character-cards", api_game_character_cards)
    app.router.add_post("/api/character-cards", api_character_card_save)
    app.router.add_route("PUT", "/api/character-cards/{card_id}", api_character_card_update)
    app.router.add_route("DELETE", "/api/character-cards/{card_id}", api_character_card_delete)
    app.router.add_post("/api/character-cards/import", api_character_card_import)
