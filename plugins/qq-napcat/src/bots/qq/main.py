"""运行 QQ / NapCat TRPG Bot：python -m src.bots.qq.main。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from src.bots.bridge_core.client import DiceFrameClient
from src.bots.bridge_core.store import JsonBridgeStore
from src.bots.qq.adapter import QQTRPGAdapter
from src.bots.qq.config import QQBotConfig, _read_generation_file
from src.bots.qq.transport import NapCatTransport


async def run() -> None:
    config = QQBotConfig.from_env()
    _configure_file_logging(config)
    logger = logging.getLogger("trpg.qq.main")
    logger.info(
        "群聊插件启动: pid=%s parent_pid=%s data=%s api=%s napcat=%s",
        os.getpid(), config.parent_pid, config.data_path, config.trpg_api_base, config.ws_url,
    )
    config.validate()
    # SIGTERM（宿主 stop() 的 terminate()）时取消主任务，走 finally 链优雅退出并清理锁；
    # Python 默认 SIGTERM 处置是直接终止进程、不跑 finally，锁文件会残留。
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    def _request_shutdown() -> None:
        if main_task is not None:
            main_task.cancel()

    try:
        loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
    except (NotImplementedError, RuntimeError, ValueError):
        pass
    lock_path = config.data_path.parent / "qq-napcat.lock"
    with _single_instance_lock(lock_path, parent_pid=config.parent_pid):
        try:
            await _run_with_config(config)
        except asyncio.CancelledError:
            logger.info("收到终止信号，群聊插件优雅退出: pid=%s", os.getpid())
        finally:
            logger.info("群聊插件主循环退出: pid=%s", os.getpid())


async def _run_with_config(config: QQBotConfig) -> None:
    store = JsonBridgeStore(config.data_path)
    await store.load()
    api = DiceFrameClient(config.trpg_api_base, config.bot_token)
    adapter: QQTRPGAdapter

    async def on_payload(payload: dict) -> None:
        await adapter.handle_payload(payload)

    transport = NapCatTransport(config, on_payload)
    adapter = QQTRPGAdapter(api, store, transport, config)
    parent_watch_task = asyncio.create_task(_watch_parent_process(
        config.parent_pid, transport,
        generation_file=config.generation_file,
        initial_generation=config.host_generation,
    ))
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
def _single_instance_lock(path, *, parent_pid: int = 0):
    """只允许一个群聊插件进程使用同一份会话数据，避免多连接同时回复刷屏。

    读到存活锁且属主是本宿主 spawn 的合法实例时拒绝第二个实例；若属主是上一
    个宿主换代后的孤儿（实际父进程既不是我的父、也非 init），则接管：SIGTERM→
    有界等待→SIGKILL 后清锁重建，解决 Docker 下主程序重启残留孤儿导致开关打不开。
    锁文件保持纯 pid（v1.3.0 旧读代码用 int() 解析，附加字段会破坏兼容），
    属主宿主 pid 记在独立 sidecar 文件。parent_pid<=0（手动运行）时不接管。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            holder = _read_lock_pid(path)
            if holder and _pid_is_alive(holder):
                if _try_take_over_orphan(path, holder, parent_pid=parent_pid):
                    continue  # 孤儿已接管（杀掉并清锁），回到循环顶重新创建锁
                raise RuntimeError(f"QQ / NapCat 插件已在运行，PID={holder}；拒绝启动第二个实例以避免群内刷屏")
            with contextlib.suppress(OSError):
                path.unlink()
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))
        if parent_pid:
            sidecar = path.with_name(path.name + ".ppid")
            with contextlib.suppress(OSError):
                sidecar.write_text(str(parent_pid), encoding="ascii")
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()
        if parent_pid:
            with contextlib.suppress(OSError):
                path.with_name(path.name + ".ppid").unlink()


def _try_take_over_orphan(path: Path, holder: int, *, parent_pid: int) -> bool:
    """判定锁内进程是否为本宿主换代后的孤儿；是则接管（杀掉并清锁）返回 True。

    孤儿判定不依赖锁内记录值（旧格式 pid-only 锁没有记录），直接看属主实际父
    进程：/proc/<pid>/stat 的 ppid 既不是我的父、也不是 init(1)，说明属主的宿主
    已换代（主程序重启后 PID 复用或 execv 保 PID），可安全接管。这样 v1.3.0 时代
    残留的孤儿锁也能在升级后自愈。parent_pid<=0（手动运行、旧主程序未注入）或
    Windows（无 /proc）时不接管，保持原"拒绝第二个实例"行为。
    """
    if parent_pid <= 0 or os.name == "nt":
        return False
    my_ppid = os.getppid()
    if my_ppid <= 1:
        return False
    holder_ppid = _linux_proc_ppid(holder)
    if holder_ppid is None:
        return False  # 读不到属主父进程，保守不接管
    if holder_ppid == my_ppid:
        return False  # 同一宿主 spawn 的合法实例 → 拒绝
    if not _terminate_orphan(holder):
        return False
    # 重读锁：仅当属主仍是刚杀的孤儿才 unlink，防止另一实例同时接管已改写锁
    if _read_lock_pid(path) != holder:
        return True  # 锁已被他方改写，本实例重试后自然按新属主判断
    with contextlib.suppress(OSError):
        path.unlink()
    with contextlib.suppress(OSError):
        path.with_name(path.name + ".ppid").unlink()
    return True


def _terminate_orphan(pid: int) -> bool:
    """SIGTERM→有界等待→SIGKILL→有界等待；返回进程最终是否已消失。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _pid_is_alive(pid)
    if _wait_pid_gone(pid, timeout=3.0):
        return True
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return not _pid_is_alive(pid)
    return _wait_pid_gone(pid, timeout=2.0)


def _wait_pid_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.2)
    return False


def _linux_proc_ppid(pid: int) -> int | None:
    """读取 /proc/<pid>/stat 中父进程 PID（field 4）；进程不存在/不可读返回 None。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, PermissionError, OSError):
        return None
    rparen = stat.rfind(")")
    if rparen == -1:
        return None
    fields = stat[rparen + 2:].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _read_lock_pid(path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip() or "0")
    except Exception:
        return 0


def _pid_exists(pid: int) -> bool:
    """纯存活检测：只判断 PID 是否在运行，不校验进程身份。

    用于父进程监控（TRPG 主进程）——主进程不是 QQ 插件，不能用
    _pid_is_alive 的 cmdline 校验，否则会把容器 PID 1 之类的主进程
    误判为"已退出"导致插件无限重启。
    """
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
        state = _linux_proc_state(pid)
    except PermissionError:
        # 进程存在但不可读，保守视为存活
        return True
    return bool(state) and state != "Z"


def _pid_is_alive(pid: int) -> bool:
    """判断 PID 是否仍是本插件进程。

    仅凭 os.kill(pid, 0) 会把僵尸进程、以及 PID 被复用的无关进程误判为存活，
    导致残留锁文件永远清不掉、插件无限重启。这里在 Linux 下校验 /proc 中的
    进程身份，只有 cmdline 确实是 QQ 插件进程才算持有锁。
    """
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
        state = _linux_proc_state(pid)
        cmdline = _linux_proc_cmdline(pid)
    except PermissionError:
        # 进程存在但不可读，保守视为存活，避免误删有效锁
        return True
    if not state or state == "Z":
        # 进程不存在或已是僵尸：锁属于已退出实例，可清理
        return False
    if not cmdline:
        return False
    return "src.bots.qq.main" in cmdline or "qq/main.py" in cmdline


def _linux_proc_state(pid: int) -> str:
    """读取 /proc/<pid>/stat 中的进程状态；进程不存在时返回空串。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return ""
    except PermissionError:
        raise
    except OSError:
        return ""
    rparen = stat.rfind(")")
    return stat[rparen + 2 : rparen + 3] if rparen != -1 else ""


def _linux_proc_cmdline(pid: int) -> str:
    """读取 /proc/<pid>/cmdline（以空格连接参数）；不可读时返回空串。"""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return ""
    except PermissionError:
        raise
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


async def _watch_parent_process(
    parent_pid: int,
    transport: Any,
    *,
    interval_sec: float = 2.0,
    generation_file: Path | None = None,
    initial_generation: str = "",
) -> None:
    if parent_pid <= 0:
        return
    while True:
        await asyncio.sleep(interval_sec)
        if not _pid_exists(parent_pid):
            logging.getLogger("trpg.qq.main").warning(
                "检测到 TRPG 主进程已退出，群聊插件自动停止: parent_pid=%s", parent_pid
            )
            await transport.stop()
            return
        # 宿主世代检查：主程序被重启后世代值变化/文件缺失（含 os.execv 保 PID 重启，
        # PID 与 starttime 都不变时纯 PID 检测会误判父进程存活），插件应立即退出。
        if initial_generation and generation_file is not None:
            current = _read_generation_file(generation_file)
            if current != initial_generation:
                logging.getLogger("trpg.qq.main").warning(
                    "检测到宿主已换代（世代 %s→%s），群聊插件自动停止",
                    initial_generation, current or "<缺失>",
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
