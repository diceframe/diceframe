"""规则路由 handler：列表 / 创建 / 详情 / 更新 / 删除。"""

from __future__ import annotations

import json

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.ruleset_draft_validation import MAX_BUILDER_DRAFT_BYTES


async def _builder_body(request: web.Request) -> dict | web.Response:
    if request.content_length and request.content_length > MAX_BUILDER_DRAFT_BYTES:
        return web.json_response(
            {"ok": False, "code": "REQUEST_TOO_LARGE", "error": "角色草稿过大"},
            status=413,
        )
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return web.json_response(
            {"ok": False, "code": "INVALID_JSON", "error": "请求正文必须是 JSON 对象"},
            status=400,
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "code": "INVALID_DRAFT", "error": "角色草稿必须是 JSON 对象"},
            status=400,
        )
    return body


async def api_rules(request: web.Request) -> web.Response:
    try:
        return web.json_response(_get_api(request).list_rules(request.query.get("language", "")))
    except ValueError as exc:
        return web.json_response(
            {"ok": False, "code": "CONTENT_VALIDATION_FAILED", "error": str(exc)},
            status=422,
        )


async def api_gameplays(request: web.Request) -> web.Response:
    """W2：玩法模式清单（只读），供「规则与玩法」页展示接线状态。"""

    return web.json_response(_get_api(request).list_gameplays())


async def api_rule_create(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    result = _get_api(request).save_custom_rule(body)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_rule_detail(request: web.Request) -> web.Response:
    try:
        return web.json_response(_get_api(request).get_rule_template(
            request.match_info["rule_id"], request.query.get("language", ""),
        ))
    except ValueError as exc:
        return web.json_response(
            {"ok": False, "code": "CONTENT_VALIDATION_FAILED", "error": str(exc)},
            status=422,
        )


async def api_rule_character_schema(request: web.Request) -> web.Response:
    result = _get_api(request).character_schema(
        request.match_info["rule_id"],
        request.query.get("language", ""),
    )
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_ruleset_experience(request: web.Request) -> web.Response:
    try:
        result = _get_api(request).ruleset_experience(
            request.match_info["rule_id"], request.query.get("language", ""),
        )
    except ValueError as exc:
        result = {"ok": False, "code": "CONTENT_VALIDATION_FAILED", "error": str(exc)}
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def _ruleset_builder_action(request: web.Request, action: str) -> web.Response:
    body = await _builder_body(request)
    if isinstance(body, web.Response):
        return body
    method = getattr(_get_api(request), f"ruleset_builder_{action}")
    try:
        result = method(
            request.match_info["rule_id"], body, request.query.get("language", ""),
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_DRAFT", "error": str(exc)}
    if result.get("ok"):
        status = 200
    elif result.get("code") == "RULE_NOT_FOUND":
        status = 404
    else:
        status = 422
    return web.json_response(result, status=status)


async def api_ruleset_builder_choices(request: web.Request) -> web.Response:
    return await _ruleset_builder_action(request, "choices")


async def api_ruleset_builder_validate(request: web.Request) -> web.Response:
    return await _ruleset_builder_action(request, "validate")


async def api_ruleset_builder_derive(request: web.Request) -> web.Response:
    return await _ruleset_builder_action(request, "derive")


async def api_ruleset_builder_finalize(request: web.Request) -> web.Response:
    return await _ruleset_builder_action(request, "finalize")


async def api_ruleset_progression(request: web.Request) -> web.Response:
    try:
        start_level = int(request.query.get("start_level", "1"))
        end_level = int(request.query.get("end_level", "20"))
        result = _get_api(request).ruleset_progression(
            request.match_info["rule_id"],
            request.query.get("class_ref", ""),
            start_level,
            end_level,
            request.query.get("language", ""),
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_PROGRESSION", "error": str(exc)}
    status = 200 if result.get("ok") else 404 if result.get("code") == "RULE_NOT_FOUND" else 422
    return web.json_response(result, status=status)


async def _ruleset_advancement_action(request: web.Request, action: str) -> web.Response:
    body = await _builder_body(request)
    if isinstance(body, web.Response):
        return body
    method = getattr(_get_api(request), f"ruleset_advancement_{action}")
    try:
        result = method(
            request.match_info["rule_id"], body, request.query.get("language", ""),
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_ADVANCEMENT", "error": str(exc)}
    status = 200 if result.get("ok") else 404 if result.get("code") == "RULE_NOT_FOUND" else 422
    return web.json_response(result, status=status)


async def api_ruleset_advancement_preview(request: web.Request) -> web.Response:
    return await _ruleset_advancement_action(request, "preview")


async def api_ruleset_advancement_apply(request: web.Request) -> web.Response:
    return await _ruleset_advancement_action(request, "apply")


async def api_ruleset_rest_resolve(request: web.Request) -> web.Response:
    body = await _builder_body(request)
    if isinstance(body, web.Response):
        return body
    try:
        result = _get_api(request).ruleset_rest_resolve(
            request.match_info["rule_id"], body, request.query.get("language", ""),
        )
    except ValueError as exc:
        result = {"ok": False, "code": "INVALID_REST", "error": str(exc)}
    status = 200 if result.get("ok") else 404 if result.get("code") == "RULE_NOT_FOUND" else 422
    return web.json_response(result, status=status)


async def api_rule_update(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    result = _get_api(request).update_custom_rule(request.match_info["rule_id"], body)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_rule_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    result = _get_api(request).delete_custom_rule(request.match_info["rule_id"])
    return web.json_response(result, status=200 if result.get("ok") else 422)


def register_rules(app: web.Application) -> None:
    app.router.add_get("/api/rules", api_rules)
    app.router.add_get("/api/gameplays", api_gameplays)
    app.router.add_post("/api/rules", api_rule_create)
    app.router.add_get("/api/rules/{rule_id}/character-schema", api_rule_character_schema)
    app.router.add_get("/api/rules/{rule_id}/experience", api_ruleset_experience)
    app.router.add_post("/api/rules/{rule_id}/builder/choices", api_ruleset_builder_choices)
    app.router.add_post("/api/rules/{rule_id}/builder/validate", api_ruleset_builder_validate)
    app.router.add_post("/api/rules/{rule_id}/builder/derive", api_ruleset_builder_derive)
    app.router.add_post("/api/rules/{rule_id}/builder/finalize", api_ruleset_builder_finalize)
    app.router.add_get("/api/rules/{rule_id}/progression", api_ruleset_progression)
    app.router.add_post("/api/rules/{rule_id}/advancement/preview", api_ruleset_advancement_preview)
    app.router.add_post("/api/rules/{rule_id}/advancement/apply", api_ruleset_advancement_apply)
    app.router.add_post("/api/rules/{rule_id}/rest/resolve", api_ruleset_rest_resolve)
    app.router.add_get("/api/rules/{rule_id}", api_rule_detail)
    app.router.add_route("PUT", "/api/rules/{rule_id}", api_rule_update)
    app.router.add_route("DELETE", "/api/rules/{rule_id}", api_rule_delete)
