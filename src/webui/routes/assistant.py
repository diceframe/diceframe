"""AI 助手路由:SSE 流式,owner only。"""

from __future__ import annotations

from aiohttp import web

from src.engine.language import normalize_language
from src.webui.routes._common import _get_api
from src.localization.messages import t
_MAX_MESSAGES = 20
_MAX_MESSAGE_CHARS = 8000
_MAX_TOTAL_CHARS = 24_000


def _validated_messages(body: object) -> tuple[list[dict[str, str]], str]:
    if not isinstance(body, dict):
        raise ValueError(t("routes_assistant.body_must_be_json_object"))
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not 1 <= len(raw_messages) <= _MAX_MESSAGES:
        raise ValueError(t("routes_assistant.messages_count"))
    messages: list[dict[str, str]] = []
    total = 0
    for item in raw_messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            raise ValueError(t("routes_assistant.message_role_invalid"))
        content = item.get("content")
        if not isinstance(content, str) or len(content) > _MAX_MESSAGE_CHARS:
            raise ValueError(t("routes_assistant.message_content_invalid"))
        # Older frontends could append an empty assistant placeholder to the
        # request. Ignore empty history entries so a stale browser cache does
        # not make the assistant unusable after the client-side fix ships.
        if not content.strip():
            continue
        total += len(content)
        messages.append({"role": str(item["role"]), "content": content})
    if total > _MAX_TOTAL_CHARS:
        raise ValueError(t("routes_assistant.history_too_long"))
    if not messages or messages[-1]["role"] != "user":
        raise ValueError(t("routes_assistant.last_message_must_be_user"))
    norm_lang = normalize_language(body.get("language"))
    language = norm_lang if norm_lang in ("en", "de") else "zh-CN"
    return messages, language


async def api_assistant_chat(request: web.Request) -> web.StreamResponse:
    if not request.get("owner_authenticated"):
        return web.json_response({"error": t("routes_assistant.unauthorized")}, status=401)
    try:
        body = await request.json()
        messages, language = _validated_messages(body)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)
    await _get_api(request).assistant_chat(response, messages, language)
    return response


def register_assistant(app: web.Application) -> None:
    app.router.add_post("/api/assistant/chat", api_assistant_chat)
    from src.webui.routes.assistant_agent import register_agent
    register_agent(app)
