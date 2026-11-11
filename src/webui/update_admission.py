"""Hold business writes until the launcher commits a version/data pair."""

from __future__ import annotations

import os

from aiohttp import web

from src.docker_launcher.data_versions import activation_pending
from src.localization.messages import t


@web.middleware
async def update_admission(request: web.Request, handler):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.path not in {"/api/login"} and activation_pending(os.environ):
            return web.json_response(
                {"ok": False, "error": t("webui_update_admission.update_validating"),
                 "error_code": "UPDATE_VALIDATING"},
                status=503, headers={"Retry-After": "2"},
            )
    return await handler(request)
