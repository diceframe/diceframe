"""Hold business writes until the launcher commits a version/data pair."""

from __future__ import annotations

import os

from aiohttp import web

from src.docker_launcher.data_versions import activation_pending
from src.localization.messages import t


# 首跑引导路径不做版本闸门：协议与遥测同意、登录本身不写对局数据，且首次
# 启动时应用可能比 launcher 的版本提交先就绪，把它们挡住只会让新用户卡在
# 「同意用户协议」这一步。
_ADMISSION_EXEMPT_PATHS = {"/api/login", "/api/hub/preferences"}


@web.middleware
async def update_admission(request: web.Request, handler):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.path not in _ADMISSION_EXEMPT_PATHS and activation_pending(os.environ):
            return web.json_response(
                {"ok": False, "error": t("webui.update_admission.update_validating"),
                 "error_code": "UPDATE_VALIDATING"},
                status=503, headers={"Retry-After": "2"},
            )
    return await handler(request)
