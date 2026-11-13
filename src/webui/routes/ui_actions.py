"""HTTP adapter for the guarded U1 declarative action subset."""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request


async def api_ui_action(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    if not request.get("owner_authenticated") or not request.get("user_id"):
        return web.json_response({"ok": False, "error": "UI action requires owner authentication"}, status=401)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        result = await _get_api(request).dispatch_ui_action(
            request.match_info["action_name"],
            str(request.get("user_id")),
            body,
        )
    except PermissionError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=403)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result)


def register_ui_actions(app: web.Application) -> None:
    app.router.add_post("/api/ui/actions/{action_name}", api_ui_action)
