# -*- coding: utf-8 -*-
"""进程生命周期辅助：把 "运行到收到停止信号" 与 "清理后是否重启" 分开。

Windows 的事件循环不支持 ``loop.add_signal_handler``，DiceFrame 自己的重启接口
（``signal.raise_signal(signal.SIGINT)``，见系统更新 / HTTPS 切换 / 证书续期）
在那里会以 ``KeyboardInterrupt`` 的形式在 ``run_until_complete()`` **之外**抛出。
如果只是让异常穿透，serve 协程的 ``finally``（``runner.cleanup()``）不会执行，
``os.execv`` 也不会被触发 —— 便携版会表现为"点重启后进程直接退出"。

本模块把这段控制流固定下来，便于单测。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


def run_with_graceful_shutdown(
    loop: asyncio.AbstractEventLoop,
    task: "asyncio.Task[Any] | asyncio.Future[Any]",
    restart_requested: Callable[[], bool],
    *,
    wait: Callable[[Awaitable[Any]], Any] | None = None,
) -> bool:
    """运行 ``task`` 直到停止；被中断时先跑完清理再返回是否重启。

    正常路径返回 ``task`` 的结果（约定为 bool）。
    中断路径（``await`` 之外的 ``KeyboardInterrupt``）：cancel 任务并把任务跑完，
    让协程的 ``finally`` 执行清理，然后按 ``restart_requested()`` 决定是否重启。
    """

    run = wait or loop.run_until_complete
    try:
        return bool(run(task))
    except KeyboardInterrupt:
        task.cancel()
        try:
            run(task)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        return bool(restart_requested())
