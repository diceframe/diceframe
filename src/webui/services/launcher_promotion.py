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

替换顺序保证任何一步失败都不会把安装目录的 DiceFrame.exe 留成残缺文件：先把新 exe
完整复制到**同一目录**的临时文件并校验哈希（这一步可能慢、可能失败，但不碰原 exe），
之后只剩两次同卷 rename——原 exe 改名为备份、临时文件改名为 DiceFrame.exe。
第二次 rename 失败时按原 exe 的哈希核对现场，必要时用备份恢复。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
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


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("无法清理启动器临时文件 %s：%s", path, exc)


def _copy_verified(source: Path, target: Path, expected_digest: str) -> None:
    """完整复制到 ``target`` 并落盘，再按哈希核对；任何不一致都按失败处理。"""

    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    if _sha256(target) != expected_digest:
        raise OSError("复制后的启动器与暂存文件不一致")


def _restore_original(launcher: Path, backup: Path, original_digest: str, log: logging.Logger) -> None:
    """切换失败后把 DiceFrame.exe 恢复成原文件：目标可能缺失，也可能是残缺/未知内容。"""

    try:
        if launcher.is_file() and _sha256(launcher) == original_digest:
            return
        if not backup.is_file() or _sha256(backup) != original_digest:
            log.error("启动器切换失败且找不到可用备份，请重新解压安装包覆盖 DiceFrame.exe")
            return
        if launcher.exists():
            launcher.unlink()
        os.replace(backup, launcher)
    except OSError as exc:
        log.error("启动器切换失败后恢复原文件也失败：%s", exc)


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
        staged_digest = _sha256(staged)
        original_digest = _sha256(launcher)
    except OSError:
        return False
    if staged_digest == original_digest:
        return False  # 已经是这一版，避免反复重命名运行中的 exe。

    # 临时文件必须与 DiceFrame.exe 同目录（同一文件系统），最后一步才是原子 rename。
    temporary = root / f".{_LAUNCHER_NAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    backup = root / _BACKUP_NAME
    try:
        _copy_verified(staged, temporary, staged_digest)
    except OSError as exc:
        active_log.warning("启动器提升失败（复制阶段），沿用当前启动器：%s", exc)
        _discard(temporary)
        return False

    try:
        # Windows 允许重命名正在执行的 exe，但不允许删除或就地覆写它：备份若是上次
        # 改名留下、仍在运行的旧 exe，这里删不掉，直接放弃本次提升，原 exe 原封不动。
        if backup.exists():
            backup.unlink()
        os.replace(launcher, backup)
    except OSError as exc:
        active_log.warning("启动器提升失败（备份阶段），沿用当前启动器：%s", exc)
        _discard(temporary)
        _restore_original(launcher, backup, original_digest, active_log)
        return False

    try:
        os.replace(temporary, launcher)
    except OSError as exc:
        active_log.warning("启动器提升失败（切换阶段），恢复原启动器：%s", exc)
        _restore_original(launcher, backup, original_digest, active_log)
        _discard(temporary)
        return False

    active_log.info("已将更新包中的启动器提前提升到安装根目录")
    # 暂存文件保留：旧启动器校验与收尾仍会用到 launcher_path。
    return True
