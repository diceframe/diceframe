"""Authenticated DF Agent transport; no private application access."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from aiohttp import web

from src.engine.language import normalize_language
from src.webui.routes._common import _get_api, _require_confirmed_request
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
    if not isinstance(value, dict) or set(value) - {"route", "game_key", "world_id", "adventure_id", "book_id", "project_id"}:
        raise ValueError("Invalid page context")
    if any(not isinstance(v, str) or len(v) > 500 for v in value.values()):
        raise ValueError("Invalid page context value")
    if value.get("route") and value["route"] not in ROUTES:
        raise ValueError("Unknown page context")
    if value.get("project_id") and not re.fullmatch(r"project-[A-Za-z0-9_-]{1,60}", value["project_id"]):
        raise ValueError("Invalid page context project")
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
        result = await _get_api(request).assistant_agent_confirm(owner, sid, aid, digest, body["approved"], emit=emit)
        # Send the JSON result as a special event so the client can update state
        await response.write(f"event: confirm_result\ndata: {json.dumps(result, ensure_ascii=False)}\n\n".encode())
        await response.write(b"event: done\ndata: complete\n\n")
    except PermissionError as exc:
        await emit("error", {"code": "AGENT_REJECTED", "error": str(exc)})
    except (ValueError, TypeError) as exc:
        await emit("error", {"code": "AGENT_REJECTED", "error": str(exc)})
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        raise
    except Exception:
        logger.exception("DF Agent confirmed operation failed")
        await emit("error", {"code": "AGENT_FAILED",
            "error": "执行已中断，请检查状态后重试。"})
    return response


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
    except (ValueError, TypeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"})
    apply_session_cookie(request, response)
    await response.prepare(request)

    async def emit(event, payload):
        wire_event = "message" if event == "delta" else event
        await response.write(f"event: {wire_event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())

    try:
        result = await _get_api(request).assistant_agent_resume(owner, sid, aid, digest, emit=emit)
        await response.write(f"event: confirm_result\ndata: {json.dumps(result, ensure_ascii=False)}\n\n".encode())
        await response.write(b"event: done\ndata: complete\n\n")
    except PermissionError as exc:
        await emit("error", {"code": "AGENT_REJECTED", "error": str(exc)})
    except (ValueError, TypeError) as exc:
        await emit("error", {"code": "AGENT_REJECTED", "error": str(exc)})
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        raise
    except Exception:
        logger.exception("DF Agent resume failed")
        await emit("error", {"code": "AGENT_FAILED",
            "error": "执行已中断，请检查状态后重试。"})
    return response


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
            int(request.query.get('offset', '0')), request.query.get('game_key', '')))
    except (ValueError, TypeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)


_ATTACHMENT_EXTENSIONS = {'.txt', '.md', '.markdown', '.json', '.csv', '.log'}
_ATTACHMENT_MAX_BYTES = 2 * 1024 * 1024


async def api_agent_attachments(request):
    """上传一个文本文档进当前 agent 会话（规则书 / 剧本 / 设定资料）。

    内容保存在会话文件里；模型通过 read_attachment 分页读取，
    全文不进上下文（上下文预算保护）。
    """
    owner = _owner(request)
    if (denied := _require_confirmed_request(request)) is not None:
        return denied
    try:
        if request.content_type != 'multipart/form-data':
            raise ValueError('attachments require multipart/form-data')
        reader = await request.multipart()
        sid = ''
        name = ''
        payload = bytearray()
        files = 0
        async for part in reader:
            if part.name == 'session_id':
                sid = _id((await part.text()).strip(), optional=True)
            elif part.name == 'file':
                files += 1
                if files > 1 or not part.filename:
                    raise ValueError('attachments accept exactly one file')
                name = str(part.filename or '')
                while chunk := await part.read_chunk():
                    if len(payload) + len(chunk) > _ATTACHMENT_MAX_BYTES:
                        raise ValueError('attachment is too large (2MB limit)')
                    payload.extend(chunk)
        if not name:
            raise ValueError('attachment file is missing')
        if not name.lower().endswith(tuple(_ATTACHMENT_EXTENSIONS)):
            raise ValueError('attachment must be a text file: ' + ', '.join(sorted(_ATTACHMENT_EXTENSIONS)))
        result = _get_api(request).assistant_agent_attach(
            owner, sid, name, bytes(payload).decode('utf-8', errors='replace'))
    except (ValueError, TypeError, UnicodeError) as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)
    except PermissionError as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=403)
    return web.json_response(result)


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
    app.router.add_post('/api/assistant/agent/attachments', api_agent_attachments)
    app.router.add_post('/api/assistant/agent/authorization', api_agent_authorization)
    app.router.add_post('/api/assistant/agent/cancel', api_agent_cancel)
    app.router.add_post('/api/assistant/agent/plan', api_agent_plan)
    app.router.add_get('/api/assistant/agent/content-operations', api_agent_content_operations)
    app.router.add_post("/api/assistant/agent/run", api_agent_run)
    app.router.add_post("/api/assistant/agent/confirm", api_agent_confirm)
    app.router.add_post('/api/assistant/agent/resume', api_agent_resume)
    app.router.add_post("/api/assistant/agent/review", api_agent_review)
    app.router.add_get("/api/assistant/agent/session/{session_id}", api_agent_session)
