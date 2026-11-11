"""角色卡路由 handler：列表 / 保存 / 更新 / 删除 / 导入。"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.access_control import PAIRED_DEVICE_ID_KEY
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


def sees_full_card_library(request: web.Request) -> bool:
    """The server-wide card library belongs to the owner.

    - The owner sees it all.
    - Bot and plugin API tokens act for a seated actor at one table, so they
      are table participants: plugin cards only (explicit decision; the
      library is server-wide, not part of any one game).
    - Without an access password there is no authentication boundary, so the
      library stays as it was there.
    - Fail closed: if it is unknown whether a password is configured, the
      caller is treated as a table participant.
    """
    if request.get("owner_authenticated", False):
        return True
    if request.get("bot_authenticated", False):
        return False
    configured = request.get(ACCESS_PASSWORD_CONFIGURED_KEY)
    if configured is None:
        return False
    return not configured


async def api_character_cards(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_character_cards())


async def api_game_character_cards(request: web.Request) -> web.Response:
    api = _get_api(request)
    if not api.game_detail(request.match_info["game_key"]):
        return web.json_response({"error": "游戏不存在"}, status=404)
    if sees_full_card_library(request):
        return web.json_response(api.list_character_cards())
    # Players and visitors only get cards meant for any table (plugin
    # content); characters saved from other games stay with the owner.
    return web.json_response(api.list_shareable_character_cards())


async def api_character_card_save(request: web.Request) -> web.Response:
    body = await request.json()
    result = _get_api(request).save_character_card(body)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_character_card_update(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response(_get_api(request).update_character_card(request.match_info["card_id"], body))


async def api_character_card_profile_update(request: web.Request) -> web.Response:
    body = await request.json()
    result = _get_api(request).update_ruleset_character_card_profile(
        request.match_info["card_id"], body,
    )
    if result.get("ok"):
        return web.json_response(result)
    code = str(result.get("error_code") or "")
    status = 404 if code == "CHARACTER_NOT_FOUND" else 422
    return web.json_response(result, status=status)


async def _api_character_card_advancement(request: web.Request, action: str) -> web.Response:
    body = await request.json()
    try:
        api = _get_api(request)
        result = (
            api.apply_character_card_advancement(
                request.match_info["card_id"], body,
            )
            if action == "apply"
            else api.preview_character_card_advancement(
                request.match_info["card_id"], body,
            )
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_ADVANCEMENT", "error": str(exc)}
    if result.get("ok"):
        status = 200
    elif result.get("code") == "CHARACTER_NOT_FOUND":
        status = 404
    elif result.get("code") == "STALE_CHARACTER_REVISION":
        status = 409
    else:
        status = 422
    return web.json_response(result, status=status)


async def api_character_card_advancement_preview(request: web.Request) -> web.Response:
    return await _api_character_card_advancement(request, "preview")


async def api_character_card_advancement_apply(request: web.Request) -> web.Response:
    return await _api_character_card_advancement(request, "apply")


def _choice_status(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 200
    code = str(result.get("code") or "")
    if code == "CHARACTER_NOT_FOUND":
        return 404
    if code == "STALE_CHARACTER_REVISION":
        return 409
    return 422


async def api_character_card_choices_pending(request: web.Request) -> web.Response:
    try:
        result = _get_api(request).pending_character_card_choices(
            request.match_info["card_id"],
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_CHOICE_REPAIR", "error": str(exc)}
    return web.json_response(result, status=_choice_status(result))


async def api_character_card_choices_complete(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        result = _get_api(request).complete_character_card_choices(
            request.match_info["card_id"], body,
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_CHOICE_REPAIR", "error": str(exc)}
    return web.json_response(result, status=_choice_status(result))


async def api_character_card_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    return web.json_response(_get_api(request).delete_character_card(request.match_info["card_id"]))


def _as_bool(value: object, default: bool = True) -> bool:
    """Accept a real boolean or the string forms a form-encoded upload sends."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


_PLAN_CONFLICT_CODES = frozenset({
    "PLAN_STALE", "CARD_IDENTITY_CONFLICT", "LOREBOOK_IDENTITY_CONFLICT",
})


async def api_character_card_import_preview(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "request must be an object"}, status=400)
    result = _get_api(request).preview_character_card_import(body)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def _api_character_card_plan_commit(request: web.Request, body: dict) -> web.Response:
    result = _get_api(request).commit_character_card_plan(
        body, pushed_by_device=str(request.get(PAIRED_DEVICE_ID_KEY, "") or ""),
    )
    if result.get("ok"):
        return web.json_response(result)
    status = 409 if result.get("error_code") in _PLAN_CONFLICT_CODES else 400
    return web.json_response(result, status=status)


async def api_character_card_import(request: web.Request) -> web.Response:
    body = await request.json()
    if isinstance(body, dict) and "plan_digest" in body:
        return await _api_character_card_plan_commit(request, body)
    result = await _get_api(request).import_character_card(
        file_data=body.get("file_data", ""),
        file_name=body.get("file_name", "card.json"),
        target=str(body.get("target") or "character_card"),
        world_id=str(body.get("world_id") or ""),
        include_character_book=_as_bool(body.get("include_character_book"), True),
        character_uid=str(body.get("character_uid") or ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_character_card_export(request: web.Request) -> web.Response:
    body = await request.json()
    result = _get_api(request).export_character_cards(body.get("card_ids") or [])
    if not result.get("ok"):
        return web.json_response(result, status=400)
    filename = str(result.get("filename") or "characters.json")
    return web.Response(
        body=result["payload"],
        content_type=result.get("content_type", "application/json"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register_character_cards(app: web.Application) -> None:
    app.router.add_get("/api/character-cards", api_character_cards)
    app.router.add_get("/api/games/{game_key}/character-cards", api_game_character_cards)
    app.router.add_post("/api/character-cards", api_character_card_save)
    app.router.add_route("PUT", "/api/character-cards/{card_id}", api_character_card_update)
    app.router.add_patch(
        "/api/character-cards/{card_id}/profile", api_character_card_profile_update,
    )
    app.router.add_post(
        "/api/character-cards/{card_id}/advancement/preview",
        api_character_card_advancement_preview,
    )
    app.router.add_post(
        "/api/character-cards/{card_id}/advancement/apply",
        api_character_card_advancement_apply,
    )
    app.router.add_get(
        "/api/character-cards/{card_id}/choices/pending",
        api_character_card_choices_pending,
    )
    app.router.add_post(
        "/api/character-cards/{card_id}/choices/complete",
        api_character_card_choices_complete,
    )
    app.router.add_route("DELETE", "/api/character-cards/{card_id}", api_character_card_delete)
    app.router.add_post("/api/character-cards/import", api_character_card_import)
    app.router.add_post("/api/character-cards/import/preview", api_character_card_import_preview)
    app.router.add_post("/api/character-cards/export", api_character_card_export)
