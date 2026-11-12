"""HTTP endpoints for server-side text-to-speech."""

from __future__ import annotations

from aiohttp import web

from src.tts import SpeechServiceError
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
from src.localization.messages import t


async def api_speech_voices(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_speech_voices())


async def api_game_speech(request: web.Request) -> web.Response:
    return await _synthesize(request, game_key=request.match_info["game_key"])


async def api_test_speech(request: web.Request) -> web.Response:
    denied = _require_tts_admin(request)
    if denied is not None:
        return denied
    return await _synthesize(request)


async def api_assistant_speech(request: web.Request) -> web.Response:
    """Read DF Agent replies aloud with the configured TTS provider.

    The agent is application-wide and owner-only, so this has no game
    timeline to check against; owner authentication is the boundary.
    """
    denied = _require_owner(request)
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
        return web.json_response({"ok": False, "error": t("webui.routes.speech.body_must_be_json")}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": t("webui.routes.speech.profile_not_object")}, status=400)
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
        return web.json_response({"ok": False, "error": t("webui.routes.speech.profiles_not_on_share_page")}, status=403)
    if request.get(ACCESS_PASSWORD_CONFIGURED_KEY, False) and not request.get("owner_authenticated", False):
        return web.json_response({"ok": False, "error": t("webui.routes.speech.profiles_admin_only")}, status=403)
    return None


def _require_owner(request: web.Request) -> web.Response | None:
    if not request.get("owner_authenticated", False) or not request.get("user_id"):
        return web.json_response({"ok": False, "error": "Owner authentication required"}, status=401)
    return None


async def _synthesize(request: web.Request, game_key: str = "") -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": t("webui.routes.speech.body_must_be_json")}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": t("webui.routes.speech.body_must_be_json_object")}, status=400)
    text = str(body.get("text") or "")
    voice = str(body.get("voice") or "")
    language = str(body.get("language") or "zh-CN")
    try:
        speed = float(body.get("speed") or 1.0)
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": t("webui.routes.speech.speed_not_number")}, status=400)
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
                owner=bool(request.get("owner_authenticated", False)),
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
    app.router.add_post("/api/assistant/speech", api_assistant_speech)
    app.router.add_get("/api/tts/profiles", api_speech_profiles)
    app.router.add_post("/api/tts/profiles", api_speech_profile_create)
    app.router.add_put("/api/tts/profiles/{profile_id}", api_speech_profile_update)
    app.router.add_delete("/api/tts/profiles/{profile_id}", api_speech_profile_delete)
    app.router.add_post("/api/games/{game_key}/speech", api_game_speech)
