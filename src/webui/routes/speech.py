"""HTTP endpoints for server-side text-to-speech."""

from __future__ import annotations

from aiohttp import web

from src.tts import SpeechServiceError
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


async def api_speech_voices(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_speech_voices())


async def api_game_speech(request: web.Request) -> web.Response:
    return await _synthesize(request, game_key=request.match_info["game_key"])


async def api_test_speech(request: web.Request) -> web.Response:
    denied = _require_tts_admin(request)
    if denied is not None:
        return denied
    return await _synthesize(request)


async def api_speech_profiles(request: web.Request) -> web.Response:
    denied = _require_tts_admin(request)
    if denied is not None:
        return denied
    return web.json_response(_get_api(request).list_personal_speech_profiles())


async def api_speech_profile_create(request: web.Request) -> web.Response:
    return await _save_profile(request, "")


async def api_speech_profile_update(request: web.Request) -> web.Response:
    return await _save_profile(request, request.match_info["profile_id"])


async def api_speech_profile_delete(request: web.Request) -> web.Response:
    denied = _require_tts_admin(request)
    if denied is None:
        denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        result = _get_api(request).delete_personal_speech_profile(request.match_info["profile_id"])
    except KeyError as exc:
        return web.json_response({"ok": False, "error": str(exc.args[0])}, status=404)
    return web.json_response(result)


async def _save_profile(request: web.Request, profile_id: str) -> web.Response:
    denied = _require_tts_admin(request)
    if denied is None:
        denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "请求体必须是 JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "个人音色必须是 JSON 对象"}, status=400)
    try:
        result = _get_api(request).save_personal_speech_profile(
            profile_id,
            body,
            file_data=str(body.get("file_data") or ""),
            file_name=str(body.get("file_name") or ""),
        )
    except KeyError as exc:
        return web.json_response({"ok": False, "error": str(exc.args[0])}, status=404)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result)


def _require_tts_admin(request: web.Request) -> web.Response | None:
    query = getattr(request, "query", {})
    if query.get("user") or query.get("share"):
        return web.json_response({"ok": False, "error": "玩家分享页不可管理个人音色"}, status=403)
    if request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False) and not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": "仅管理员可以管理个人音色"}, status=403)
    return None


async def _synthesize(request: web.Request, game_key: str = "") -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "请求体必须是 JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)
    text = str(body.get("text") or "")
    voice = str(body.get("voice") or "")
    language = str(body.get("language") or "zh-CN")
    try:
        speed = float(body.get("speed") or 1.0)
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "speed 必须是数字"}, status=400)
    api = _get_api(request)
    try:
        if game_key:
            audio = await api.synthesize_speech(
                game_key,
                request.get("user_id", ""),
                text,
                voice,
                language,
                speed,
            )
        else:
            audio = await api.test_speech(text, voice, language, speed)
    except KeyError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    except PermissionError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=403)
    except (SpeechServiceError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.Response(
        body=audio.body,
        content_type=audio.content_type,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-DiceFrame-TTS-Cache": "hit" if audio.cached else "miss",
        },
    )


def register_speech(app: web.Application) -> None:
    app.router.add_get("/api/tts/voices", api_speech_voices)
    app.router.add_post("/api/tts/test", api_test_speech)
    app.router.add_get("/api/tts/profiles", api_speech_profiles)
    app.router.add_post("/api/tts/profiles", api_speech_profile_create)
    app.router.add_put("/api/tts/profiles/{profile_id}", api_speech_profile_update)
    app.router.add_delete("/api/tts/profiles/{profile_id}", api_speech_profile_delete)
    app.router.add_post("/api/games/{game_key}/speech", api_game_speech)
