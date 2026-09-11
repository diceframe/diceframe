"""Windows 重启路径：中断必须跑完清理并按 restart_requested 重启。

Windows 没有 asyncio signal handler，DiceFrame 自己的重启接口
（signal.raise_signal(SIGINT)：系统更新 / HTTPS 切换 / 证书续期）会变成
run_until_complete() 之外的 KeyboardInterrupt。这里固定住修复后的行为：
cancel 任务 → 让协程的 finally 执行 → 读取重启标记。
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.web_transport.lifecycle import run_with_graceful_shutdown


def _loop_with_interrupt() -> tuple[asyncio.AbstractEventLoop, dict[str, int], list[bool]]:
    """返回 (loop, run_until_complete 调用计数, 清理标记)。

    第一次 run_until_complete() 先把事件循环跑起来（真实场景里中断发生在循环运行
    期间，任务已经启动并停在 await 上），然后抛 KeyboardInterrupt 模拟 Windows：
    没有 asyncio signal handler，中断在 run_until_complete() 之外抛出。
    """

    loop = asyncio.new_event_loop()
    calls = {"count": 0}
    cleaned: list[bool] = []
    original = loop.run_until_complete

    def flaky(future: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 1:
            original(asyncio.sleep(0.05))
            raise KeyboardInterrupt
        return original(future)

    loop.run_until_complete = flaky  # type: ignore[method-assign]
    return loop, calls, cleaned


def test_interrupt_still_runs_cleanup_and_reports_restart() -> None:
    loop, calls, cleaned = _loop_with_interrupt()

    async def serve() -> bool:
        try:
            await asyncio.sleep(3600)
        finally:
            cleaned.append(True)
        return False

    task = loop.create_task(serve())
    try:
        restart = run_with_graceful_shutdown(loop, task, lambda: True)
    finally:
        loop.close()

    assert calls["count"] == 2, "中断后必须再把任务跑完，否则清理不会执行"
    assert cleaned == [True], "runner.cleanup() 一类的收尾必须执行"
    assert restart is True
    assert task.cancelled() or task.done()


def test_interrupt_without_restart_flag_exits_cleanly() -> None:
    loop, _calls, cleaned = _loop_with_interrupt()

    async def serve() -> bool:
        try:
            await asyncio.sleep(3600)
        finally:
            cleaned.append(True)
        return False

    task = loop.create_task(serve())
    try:
        restart = run_with_graceful_shutdown(loop, task, lambda: False)
    finally:
        loop.close()

    assert cleaned == [True]
    assert restart is False


def test_normal_stop_returns_the_task_result() -> None:
    loop = asyncio.new_event_loop()

    async def serve() -> bool:
        return True

    task = loop.create_task(serve())
    try:
        assert run_with_graceful_shutdown(loop, task, lambda: False) is True
        assert loop.run_until_complete(asyncio.sleep(0)) is None
    finally:
        loop.close()
