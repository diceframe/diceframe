"""Authenticated DF Agent transport; no private application access."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from aiohttp import web

from src.engine.language import normalize_language
from src.webui.routes._common import _get_api
from src.webui.routes.assistant import _validated_messages
from src.webui.services.assistant_agent_application import ROUTES
from src.webui.session import apply_session_cookie
from src.localization.messages import t

logger = logging.getLogger("trpg")


def _owner(request):
    if not request.get("owner_authenticated") or not request.get("user_id"):
        raise web.HTTPUnauthorized(text=json.dumps({"ok": False, "error": "Owner authentication required"}), content_type="application/json")
    return request["user_id"]


def _body(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError("Invalid agent request fields")
    return value


def _id(value, optional=False):
    if optional and value == "":
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid agent identity")
    return value


def _context(value):
    if not isinstance(value, dict) or set(value) - {"route", "game_key", "world_id", "adventure_id", "book_id"}:
        raise ValueError("Invalid page context")
    if any(not isinstance(v, str) or len(v) > 500 for v in value.values()):
        raise ValueError("Invalid page context value")
    if value.get("route") and value["route"] not in ROUTES:
        raise ValueError("Unknown page context")
    return value


async def api_agent_run(request):
    owner = _owner(request)
    try:
        body = _body(await request.json(), ("messages", "language", "context", "session_id"))
        messages, _ = _validated_messages(body)
        language = normalize_language(body.get("language"))
        context = _context(body.get("context", {}))
        sid = _id(body.get("session_id", ""), optional=True)
    except (ValueError, TypeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"})
    apply_session_cookie(request, response)
    await response.prepare(request)

    async def emit(event, payload):
        wire_event = "message" if event == "delta" else event
        await response.write(f"event: {wire_event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
    try:
        await _get_api(request).assistant_agent_run(owner, messages, language, context, sid, emit)
        await response.write(b"event: done\ndata: complete\n\n")
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        raise
    except (ValueError, PermissionError) as exc:
        await emit("error", {"code": getattr(exc, "code", "AGENT_REJECTED"), "error": str(exc)})
    except Exception:
        logger.exception("DF Agent run failed")
        await emit("error", {"code": "AGENT_FAILED", "error": t("webui.routes.assistant_agent.agent_run_failed") if language.startswith("zh") else "DF Agent failed. Please try again."})
    return response


async def api_agent_confirm(request):
    owner = _owner(request)
    try:
        body = _body(await request.json(), ("session_id", "approval_id", "plan_digest", "approved"))
        sid, aid = _id(body.get("session_id")), _id(body.get("approval_id"))
        digest = body.get("plan_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or not isinstance(body.get("approved"), bool):
            raise ValueError("Invalid confirmation")
        return web.json_response(await _get_api(request).assistant_agent_confirm(owner, sid, aid, digest, body["approved"]))
    except PermissionError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception("DF Agent confirmed operation failed")
        return web.json_response({"ok": False, "recovery_required": True,
            "error": "Execution interrupted. Recover the task status before retrying."}, status=500)


async def api_agent_review(request):
    owner = _owner(request)
    try:
        body = _body(await request.json(), ("session_id", "draft_id", "draft"))
        return web.json_response(await _get_api(request).assistant_agent_review(owner, _id(body.get("session_id")),
            _id(body.get("draft_id")), body.get("draft")))
    except PermissionError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def api_agent_resume(request):
    owner = _owner(request)
    try:
        body = _body(await request.json(), ('session_id', 'approval_id', 'plan_digest'))
        sid, aid = _id(body.get('session_id')), _id(body.get('approval_id'))
        digest = body.get('plan_digest')
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise ValueError('Invalid execution digest')
        return web.json_response(await _get_api(request).assistant_agent_resume(owner, sid, aid, digest))
    except PermissionError as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)


async def api_agent_session(request):
    owner = _owner(request)
    try:
        return web.json_response(_get_api(request).assistant_agent_session(owner, _id(request.match_info["session_id"])))
    except PermissionError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)


async def api_agent_plan(request):
    owner = _owner(request)
    try:
        body = _body(await request.json(), ('tool', 'arguments', 'session_id'))
        sid = _id(body.get('session_id', ''), optional=True)
        return web.json_response(await _get_api(request).assistant_agent_plan(owner, body.get('tool'), body.get('arguments'), sid))
    except PermissionError as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)


async def api_agent_content_operations(request):
    owner = _owner(request)
    return web.json_response(_get_api(request).assistant_content_operations(owner))


async def api_agent_capabilities(request):
    owner = _owner(request)
    try:
        return web.json_response(_get_api(request).assistant_agent_capabilities(
            owner, request.query.get('q', ''), request.query.get('domain', ''),
            int(request.query.get('offset', '0'))))
    except (ValueError, TypeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)


async def api_agent_authorization(request):
    owner = _owner(request)
    try:
        body = _body(await request.json(), ('session_id', 'mode', 'scope', 'domains', 'max_steps'))
        sid = _id(body.get('session_id', ''), optional=True)
        return web.json_response(_get_api(request).assistant_agent_authorize(owner, sid,
            body.get('mode'), body.get('scope', {}), body.get('domains', []), body.get('max_steps', 32)))
    except PermissionError as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)


async def api_agent_cancel(request):
    owner = _owner(request)
    try:
        body = _body(await request.json(), ('session_id',))
        return web.json_response(_get_api(request).assistant_agent_cancel(owner, _id(body.get('session_id'))))
    except PermissionError as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)


def register_agent(app):
    app.router.add_get('/api/assistant/agent/capabilities', api_agent_capabilities)
    app.router.add_post('/api/assistant/agent/authorization', api_agent_authorization)
    app.router.add_post('/api/assistant/agent/cancel', api_agent_cancel)
    app.router.add_post('/api/assistant/agent/plan', api_agent_plan)
    app.router.add_get('/api/assistant/agent/content-operations', api_agent_content_operations)
    app.router.add_post("/api/assistant/agent/run", api_agent_run)
    app.router.add_post("/api/assistant/agent/confirm", api_agent_confirm)
    app.router.add_post('/api/assistant/agent/resume', api_agent_resume)
    app.router.add_post("/api/assistant/agent/review", api_agent_review)
    app.router.add_get("/api/assistant/agent/session/{session_id}", api_agent_session)
