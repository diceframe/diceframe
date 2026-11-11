"""DiceFrame Hub 本地同源代理路由。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import suppress
from typing import Any

from aiohttp import web

from src.hub_client import HubConnectionUnavailable, HubHTTPError
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.localization.messages import t


class _ClientDisconnected(RuntimeError):
    """The browser closed an in-flight Hub detail request."""


async def _await_hub_read(request: web.Request, operation: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Cancel a slow upstream read shortly after the browser closes its modal."""
    task = asyncio.ensure_future(operation)
    try:
        while not task.done():
            transport = request.transport
            if transport is None or transport.is_closing():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise _ClientDisconnected
            await asyncio.wait({task}, timeout=0.2)
        return await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def api_hub_preferences(request: web.Request) -> web.Response:
    return web.json_response(await _get_api(request).hub_preferences(request.query.get("lang", "zh-CN")))


async def api_hub_preferences_update(request: web.Request) -> web.Response:
    data = await request.json()
    if not isinstance(data.get("telemetry_enabled"), bool):
        return web.json_response({"ok": False, "error": t("routes_hub.telemetry_enabled_not_boolean")}, status=400)
    legal_acceptance = data.get("legal_acceptance")
    if legal_acceptance is not None and not isinstance(legal_acceptance, dict):
        return web.json_response({"ok": False, "error": t("routes_hub.legal_acceptance_not_object")}, status=400)
    language = data.get("lang", "zh-CN")
    if not isinstance(language, str):
        return web.json_response({"ok": False, "error": t("routes_hub.lang_not_string")}, status=400)
    try:
        result = await _get_api(request).update_hub_preferences(
            data["telemetry_enabled"],
            legal_acceptance=legal_acceptance,
            language=language,
        )
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result)


async def api_hub_identity_delete(request: web.Request) -> web.Response:
    try:
        result = await _get_api(request).delete_hub_identity()
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result)


async def api_hub_rendezvous_room_create(request: web.Request) -> web.Response:
    if denied := _require_confirmed_request(request):
        return denied
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": t("routes_hub.body_not_json")}, status=400)
    peer_count = body.get("peer_count") if isinstance(body, dict) else None
    # 服务端兜底上限：与游戏侧 max_players 默认值对齐，防止信令房间
    # 批准了游戏侧必然拒绝的人数（第 7+ 人信令成功但进游戏必失败）。
    if isinstance(peer_count, bool) or not isinstance(peer_count, int) or not 2 <= peer_count <= 6:
        return web.json_response(
            {"ok": False, "error": t("routes_hub.peer_count_range")},
            status=400,
        )
    try:
        result = await _get_api(request).create_rendezvous_room(peer_count)
    except HubHTTPError as exc:
        return _hub_error_response(exc)
    except HubConnectionUnavailable:
        return web.json_response(
            {
                "ok": False,
                "error": t("routes_hub.hub_unreachable"),
                "error_code": "hub_connection_unavailable",
            },
            status=502,
        )
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result, status=201)


async def api_hub_rendezvous_config(request: web.Request) -> web.Response:
    try:
        result = await _get_api(request).rendezvous_config()
    except HubHTTPError as exc:
        return _hub_error_response(exc)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result)


async def api_hub_plugin_detail(request: web.Request) -> web.Response:
    try:
        result = await _await_hub_read(
            request,
            _get_api(request).hub_plugin_detail(request.match_info["plugin_id"]),
        )
    except _ClientDisconnected:
        return web.Response(status=499)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result)


async def api_hub_plugin_readme(request: web.Request) -> web.Response:
    try:
        # Hub 详情链路允许 60 秒，README 兜底同样受总时限约束，避免多层重试拖垮请求。
        result = await _await_hub_read(
            request,
            asyncio.wait_for(
                _get_api(request).hub_plugin_readme(request.match_info["plugin_id"]),
                timeout=60,
            ),
        )
    except _ClientDisconnected:
        return web.Response(status=499)
    except asyncio.TimeoutError:
        return web.json_response(
            {"ok": False, "html": "", "markdown": "", "error": t("routes_hub.readme_timeout")},
            status=504,
        )
    except Exception as exc:
        return web.json_response(
            {"ok": False, "html": "", "markdown": "", "error": str(exc)},
            status=502,
        )
    return web.json_response(result)


async def api_hub_plugin_ratings(request: web.Request) -> web.Response:
    try:
        result = await _await_hub_read(
            request,
            _get_api(request).hub_plugin_ratings(request.match_info["plugin_id"]),
        )
    except _ClientDisconnected:
        return web.Response(status=499)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result)


async def api_hub_plugin_like(request: web.Request) -> web.Response:
    try:
        result = await _get_api(request).set_hub_plugin_like(
            request.match_info["plugin_id"], request.method == "PUT"
        )
    except HubHTTPError as exc:
        return _hub_error_response(exc)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result)


async def api_hub_plugin_rating(request: web.Request) -> web.Response:
    try:
        if request.method == "DELETE":
            result = await _get_api(request).set_hub_plugin_rating(
                request.match_info["plugin_id"], None
            )
        else:
            data = await request.json()
            stars = data.get("stars")
            tags = data.get("tags", [])
            if not isinstance(stars, int) or isinstance(stars, bool) or not 1 <= stars <= 5:
                return web.json_response({"ok": False, "error": t("routes_hub.stars_range")}, status=400)
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                return web.json_response({"ok": False, "error": t("routes_hub.tags_not_string_array")}, status=400)
            result = await _get_api(request).set_hub_plugin_rating(
                request.match_info["plugin_id"], stars, tags
            )
    except HubHTTPError as exc:
        return _hub_error_response(exc)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    return web.json_response(result)


def _hub_error_response(exc: HubHTTPError) -> web.Response:
    """把 Hub 的 4xx 原样透传，detail 转成普通用户看得懂的中文提示。

    429 冷却时同时带 retry_after（秒数），前端 api() 据此显示"请 X 秒后重试"。
    """
    payload: dict[str, Any] = {"ok": False, "error": _hub_error_message(exc)}
    if exc.error_code:
        payload["error_code"] = exc.error_code
    if exc.retry_after:
        payload["retry_after"] = exc.retry_after
    return web.json_response(payload, status=exc.status)


def _hub_error_message(exc: HubHTTPError) -> str:
    if exc.status == 429 and exc.error_code == "installation_room_limit" and exc.detail:
        return exc.detail
    if exc.status == 429:
        return "操作太频繁，请稍后再试"
    if exc.status == 403 and "requires an installation" in exc.detail:
        return "需要先安装该插件才能评分"
    if exc.detail:
        return exc.detail
    return f"DiceFrame Hub HTTP {exc.status}"


def register_hub(app: web.Application) -> None:
    app.router.add_get("/api/hub/preferences", api_hub_preferences)
    app.router.add_patch("/api/hub/preferences", api_hub_preferences_update)
    app.router.add_delete("/api/hub/identity", api_hub_identity_delete)
    app.router.add_get("/api/hub/rendezvous/config", api_hub_rendezvous_config)
    app.router.add_post("/api/hub/rendezvous/rooms", api_hub_rendezvous_room_create)
    app.router.add_get("/api/hub/plugins/{plugin_id}", api_hub_plugin_detail)
    app.router.add_get("/api/hub/plugins/{plugin_id}/readme", api_hub_plugin_readme)
    app.router.add_get("/api/hub/plugins/{plugin_id}/ratings", api_hub_plugin_ratings)
    app.router.add_put("/api/hub/plugins/{plugin_id}/like", api_hub_plugin_like)
    app.router.add_delete("/api/hub/plugins/{plugin_id}/like", api_hub_plugin_like)
    app.router.add_put("/api/hub/plugins/{plugin_id}/rating", api_hub_plugin_rating)
    app.router.add_delete("/api/hub/plugins/{plugin_id}/rating", api_hub_plugin_rating)
