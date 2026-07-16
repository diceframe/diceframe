"""运行 QQ / NapCat TRPG Bot：python -m src.bots.qq.main。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
from typing import Any

from src.bots.bridge_core.client import DiceFrameClient
from src.bots.bridge_core.store import JsonBridgeStore
from src.bots.qq.adapter import QQTRPGAdapter
from src.bots.qq.config import QQBotConfig
from src.bots.qq.transport import NapCatTransport


async def run() -> None:
    config = QQBotConfig.from_env()
    _configure_file_logging(config)
    logging.getLogger("trpg.qq.main").info(
        "群聊插件启动: pid=%s parent_pid=%s data=%s api=%s napcat=%s",
        os.getpid(), config.parent_pid, config.data_path, config.trpg_api_base, config.ws_url,
    )
    config.validate()
    lock_path = config.data_path.parent / "qq-napcat.lock"
    with _single_instance_lock(lock_path):
        try:
            await _run_with_config(config)
        finally:
            logging.getLogger("trpg.qq.main").info("群聊插件主循环退出: pid=%s", os.getpid())


async def _run_with_config(config: QQBotConfig) -> None:
    store = JsonBridgeStore(config.data_path)
    await store.load()
    api = DiceFrameClient(config.trpg_api_base, config.bot_token)
    adapter: QQTRPGAdapter

    async def on_payload(payload: dict) -> None:
        await adapter.handle_payload(payload)

    transport = NapCatTransport(config, on_payload)
    adapter = QQTRPGAdapter(api, store, transport, config)
    parent_watch_task = asyncio.create_task(_watch_parent_process(config.parent_pid, transport))
    card_cleanup_task = asyncio.create_task(_periodic_card_cache_cleanup(adapter, config))
    web_sync_task = asyncio.create_task(_periodic_web_sync(adapter, config))
    try:
        await transport.run()
    finally:
        for task in (parent_watch_task, card_cleanup_task, web_sync_task):
            task.cancel()
        for task in (parent_watch_task, card_cleanup_task, web_sync_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await transport.stop()
        await api.close()


def _configure_file_logging(config: QQBotConfig) -> None:
    log_path = config.data_path.parent / "qq-napcat.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == str(log_path) for handler in root.handlers):
        return
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


@contextlib.contextmanager
def _single_instance_lock(path):
    """只允许一个群聊插件进程使用同一份会话数据，避免多连接同时回复刷屏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            pid = _read_lock_pid(path)
            if pid and _pid_is_alive(pid):
                raise RuntimeError(f"QQ / NapCat 插件已在运行，PID={pid}；拒绝启动第二个实例以避免群内刷屏")
            with contextlib.suppress(OSError):
                path.unlink()
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def _read_lock_pid(path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip() or "0")
    except Exception:
        return 0


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        try:
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return str(pid) in output
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def _watch_parent_process(parent_pid: int, transport: Any, interval_sec: float = 2.0) -> None:
    if parent_pid <= 0:
        return
    while True:
        await asyncio.sleep(interval_sec)
        if not _pid_is_alive(parent_pid):
            logging.getLogger("trpg.qq.main").warning(
                "检测到 TRPG 主进程已退出，群聊插件自动停止: parent_pid=%s", parent_pid
            )
            await transport.stop()
            return


async def _periodic_card_cache_cleanup(adapter: QQTRPGAdapter, config: QQBotConfig) -> None:
    interval = float(config.card_cache_cleanup_interval_sec or 0)
    if interval <= 0:
        return
    logger = logging.getLogger("trpg.qq.main")
    while True:
        await adapter._cleanup_card_cache()
        logger.debug("QQ 卡片缓存定时清理完成，下次间隔 %.1f 秒", interval)
        await asyncio.sleep(interval)


async def _periodic_web_sync(adapter: QQTRPGAdapter, config: QQBotConfig) -> None:
    """后台轮询 web 端游戏进度，把网页玩家触发的行动+叙事转发到绑定群。"""
    interval = float(getattr(config, "web_sync_interval_sec", 0) or 0)
    if interval <= 0:
        return
    logger = logging.getLogger("trpg.qq.main")
    logger.info("Web 同步轮询已启动，间隔 %.1f 秒", interval)
    while True:
        try:
            await adapter._poll_web_notifications()
        except Exception:
            logger.warning("Web 同步轮询异常", exc_info=True)
        await asyncio.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
