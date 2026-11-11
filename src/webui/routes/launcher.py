"""便携版启动器握手路由：实例归属证明与受鉴权的停止（见 services/launcher_link）。

两个端点都必须匿名可达——启动器手里没有房主会话——鉴权完全落在实例密钥上：
身份端点只对启动器给的 nonce 出 HMAC，不泄露密钥；停止端点只接受用同一把密钥
对当前 boot_id 的签名。没有密钥（Docker、源码运行）时两者都回答不可用。
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from aiohttp import web

from src.version import __version__
from src.webui.services.launcher_link import (
    LAUNCHER_LINK_UNAVAILABLE,
    identity_proof,
    proof_matches,
    read_instance_key,
    stop_proof,
    valid_nonce,
)

LAUNCHER_DATA_DIR_KEY = web.AppKey("launcher_data_dir", Path)

IDENTITY_PATH = "/api/system/launcher/identity"
STOP_PATH = "/api/system/launcher/stop"


def _unavailable() -> web.Response:
    return web.json_response(
        {"ok": False, "error_code": LAUNCHER_LINK_UNAVAILABLE, "error": "启动器握手不可用"},
        status=404,
    )


def _instance_key(request: web.Request) -> bytes | None:
    data_dir = request.app.get(LAUNCHER_DATA_DIR_KEY)
    return read_instance_key(data_dir) if data_dir is not None else None


async def api_launcher_identity(request: web.Request) -> web.Response:
    key = _instance_key(request)
    if key is None:
        return _unavailable()
    nonce = request.query.get("nonce", "")
    if not valid_nonce(nonce):
        return web.json_response({"ok": False, "error": "nonce 无效"}, status=400)
    boot_id = str(request.app["runtime_control"]["boot_id"])
    pid = os.getpid()
    return web.json_response(
        {
            "ok": True,
            "boot_id": boot_id,
            "pid": pid,
            "version": __version__,
            "proof": identity_proof(key, nonce, boot_id, pid),
        },
        headers={"Cache-Control": "no-store"},
    )


async def _stop_after_response() -> None:
    """与重启接口同一条优雅关闭路径；stop_requested 让它清理后不再 execv。"""

    await asyncio.sleep(0.5)
    signal.raise_signal(signal.SIGINT)


async def api_launcher_stop(request: web.Request) -> web.Response:
    key = _instance_key(request)
    if key is None:
        return _unavailable()
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not valid_nonce(body.get("nonce")):
        return web.json_response({"ok": False, "error": "请求无效"}, status=400)
    control = request.app["runtime_control"]
    boot_id = str(control["boot_id"])
    if body.get("boot_id") != boot_id:
        # 启动器要停的是另一次启动（已经重启过）：不能误停新的进程。
        return web.json_response({"ok": False, "error": "boot_id 不匹配"}, status=409)
    if not proof_matches(stop_proof(key, str(body["nonce"]), boot_id), body.get("proof")):
        return web.json_response({"ok": False, "error": "签名无效"}, status=403)
    already = bool(control.get("stop_requested")) or bool(control["restart_requested"])
    control["stop_requested"] = True
    if not already:
        control["restart_task"] = asyncio.create_task(_stop_after_response())
    # 已经在重启中时不再发第二个中断：stop_requested 会让那次清理之后不再 execv。
    return web.json_response({"ok": True, "stopping": True}, status=202)


def register_launcher(app: web.Application) -> None:
    app.router.add_get(IDENTITY_PATH, api_launcher_identity)
    app.router.add_post(STOP_PATH, api_launcher_stop)
