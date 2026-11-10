# -*- coding: utf-8 -*-
"""更新期间把包里的新启动器尽早提升到安装根目录。

背景：应用内更新的收尾（HandleUpdate）由**当前正在运行的旧启动器**执行。
旧版启动器有两个已知缺陷：HTTPS 下本机健康检查走的是默认 Ssl3/Tls（后端最低
TLS 1.2），以及大小写重复的环境变量会让子进程创建失败。两者都会让更新被判失败
并回滚，而新版 exe 只有在健康检查通过之后才会被 PromoteLauncher 提升到根目录
——于是 HTTPS 用户会一直升不上来。

候选版本目录里的 app/ 是新代码，会被旧启动器当作健康检查对象启动。因此新版后端
在启动早期就把 restart_signal 里已暂存的 exe 提升到安装根目录：即使本次更新最终
回滚，用户下次双击也是修复版启动器，再执行一次更新即可由新启动器完成收尾。

这里只做文件替换，不改用户的 HTTPS 配置、不削弱 TLS、不删除暂存 exe（旧启动器
收尾时还要用它）。任何异常都降级为「沿用当前启动器」，绝不能影响后端启动。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("trpg")

_SIGNAL_NAME = "restart_signal.json"
_LAUNCHER_NAME = "DiceFrame.exe"
_BACKUP_NAME = "DiceFrame.exe.old"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def promote_pending_launcher(
    data_dir: Path,
    install_root: str | Path,
    *,
    platform: str | None = None,
    log: logging.Logger | None = None,
) -> bool:
    """若存在待应用的更新信号，把暂存的启动器提升到安装根目录。

    返回是否真的替换了文件。``platform`` 仅供跨平台测试注入；生产默认取 os.name。
    """
    active_log = log or logger
    if (platform or os.name) != "nt":
        return False
    if not str(install_root).strip():
        return False

    root = Path(install_root)
    launcher = root / _LAUNCHER_NAME
    updater_dir = Path(data_dir) / "_updater"
    signal_path = updater_dir / _SIGNAL_NAME
    if not launcher.is_file() or not signal_path.is_file():
        return False

    try:
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        staged = Path(str(payload.get("launcher_path", "")))
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    if staged.suffix.lower() != ".exe":
        return False
    # 只接受更新器自己的暂存目录，拒绝信号里被塞进来的任意路径。
    if not _within(staged, updater_dir) or not staged.is_file():
        return False

    try:
        if _sha256(staged) == _sha256(launcher):
            return False  # 已经是这一版，避免反复重命名运行中的 exe。
    except OSError:
        return False

    backup = root / _BACKUP_NAME
    try:
        # Windows 允许重命名正在执行的 exe，但不允许就地覆写它。
        if backup.exists():
            backup.unlink()
        os.replace(launcher, backup)
        shutil.copy2(staged, launcher)
    except OSError as exc:
        active_log.warning("启动器提升失败，沿用当前启动器：%s", exc)
        try:
            if not launcher.exists() and backup.exists():
                os.replace(backup, launcher)
        except OSError:
            pass
        return False

    active_log.info("已将更新包中的启动器提前提升到安装根目录")
    # 暂存文件保留：旧启动器校验与收尾仍会用到 launcher_path。
    return True
