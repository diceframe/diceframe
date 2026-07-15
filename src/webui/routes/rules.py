"""规则路由 handler：列表 / 创建 / 详情 / 更新 / 删除。"""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_rules(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_rules())


async def api_rule_create(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    return web.json_response(_get_api(request).save_custom_rule(body))


async def api_rule_detail(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).get_rule_template(request.match_info["rule_id"]))


async def api_rule_update(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    return web.json_response(_get_api(request).update_custom_rule(request.match_info["rule_id"], body))


async def api_rule_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    return web.json_response(_get_api(request).delete_custom_rule(request.match_info["rule_id"]))


def register_rules(app: web.Application) -> None:
    app.router.add_get("/api/rules", api_rules)
    app.router.add_post("/api/rules", api_rule_create)
    app.router.add_get("/api/rules/{rule_id}", api_rule_detail)
    app.router.add_route("PUT", "/api/rules/{rule_id}", api_rule_update)
    app.router.add_route("DELETE", "/api/rules/{rule_id}", api_rule_delete)
