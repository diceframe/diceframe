"""Application self-update service.

The updater deliberately separates downloading from applying. Portable builds
are installed side-by-side and switched by DiceFrameLauncher after a health
check. Source archives use a backup/restore transaction and require a manual
restart.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from src.version import __version__

logger = logging.getLogger("trpg")

MAX_APP_UPDATE_BYTES = 200 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
DEFAULT_PROBATION_SECONDS = 60
_UPDATER_DIR_NAME = "_updater"
_STATE_FILE_NAME = "state.json"
_RESTART_SIGNAL_NAME = "restart_signal.json"

_ASSET_PATTERNS = {
    "source": re.compile(r"DiceFrame-v.+windows\.zip$", re.IGNORECASE),
    "portable": re.compile(r"DiceFrame-v.+windows-portable\.zip$", re.IGNORECASE),
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ACTIVE_STATES = {"downloading", "verifying", "applying", "restarting"}
_INTERRUPTIBLE_STATES = {"downloading", "verifying", "applying"}
_PROTECTED_SOURCE_ENTRIES = {
    ".git",
    ".codex",
    ".claude",
    "_updater",
    "data",
    "dist",
    "logs",
}


class UpdateRolledBackError(RuntimeError):
    """An apply transaction failed, but all changed files were restored."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    normalized = (expected or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError(f"SHA-256 格式无效：{path.name}")
    actual = _file_sha256(path)
    if not hmac.compare_digest(actual, normalized):
        raise ValueError(
            f"SHA-256 校验失败：{path.name}（期望 {normalized[:8]}…，实际 {actual[:8]}…）"
        )


def select_asset(
    latest: dict[str, Any], kind: str
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """Select the requested release zip and its optional SHA-256 sidecar."""
    assets = latest.get("assets") or []
    pattern = _ASSET_PATTERNS.get(kind)
    if not pattern:
        return None
    target: dict[str, Any] | None = None
    for asset in assets:
        name = str(asset.get("name", ""))
        if not name.endswith(".sha256") and pattern.search(name):
            target = asset
            break
    if not target:
        return None
    sha_name = str(target.get("name", "")) + ".sha256"
    sha_asset = next(
        (asset for asset in assets if str(asset.get("name", "")) == sha_name),
        None,
    )
    return target, sha_asset


def parse_sha256_file(content: str, filename: str) -> str:
    for line in (content or "").splitlines():
        match = _SHA256_RE.search(line.strip())
        if match:
            return match.group(0)
    raise ValueError(f"无法从校验文件解析 SHA-256：{filename}")


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _archive_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"更新包包含不安全路径：{name}")
    return path


def safe_extract_archive(archive: Path, destination: Path) -> Path:
    """Safely extract a release zip and return its single top-level directory."""
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("更新包为空或文件数量超出限制")

        total_size = 0
        top_levels: set[str] = set()
        checked: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for member in members:
            path = _archive_member_path(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"更新包不允许符号链接：{member.filename}")
            total_size += member.file_size
            if total_size > MAX_EXTRACTED_BYTES:
                raise ValueError("更新包解压后大小超出限制")
            top_levels.add(path.parts[0])
            checked.append((member, path))

        if len(top_levels) != 1:
            raise ValueError("更新包必须只包含一个顶层目录")

        root = destination.resolve()
        for member, relative in checked:
            target = (destination / Path(*relative.parts)).resolve()
            if not _path_within(target, root):
                raise ValueError(f"更新包路径越界：{member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    extracted_root = destination / next(iter(top_levels))
    if not extracted_root.is_dir():
        raise ValueError("更新包顶层目录无效")
    return extracted_root


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _safe_version_dir(version: str) -> str:
    normalized = version.strip().lstrip("vV")
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}", normalized):
        raise ValueError(f"版本号格式无效：{version}")
    return "v" + normalized


def is_self_update_supported(root: Path) -> dict[str, Any]:
    """Describe whether self-update is supported and which install mode is active."""
    if Path("/.dockerenv").exists() or os.getenv("TRPG_DATA_DIR") == "/app/data":
        return {
            "supported": False,
            "mode": "docker",
            "reason": "docker",
            "hint": (
                "Docker 环境请使用 docker compose pull && docker compose up -d 更新；"
                "NAS 用户也可以前往设备自带的容器管理界面检查并拉取新镜像"
            ),
        }

    install_root_env = os.getenv("TRPG_INSTALL_ROOT", "").strip()
    if install_root_env:
        install_root = Path(install_root_env).resolve()
        if not os.access(install_root, os.W_OK):
            return {
                "supported": False,
                "mode": "portable",
                "reason": "readonly",
                "hint": "便携版安装目录不可写，请移动到可写目录后重试",
            }
        return {"supported": True, "mode": "portable", "reason": "", "hint": ""}

    if (root / ".git").exists():
        return {
            "supported": False,
            "mode": "development",
            "reason": "development",
            "hint": "Git 开发目录不会自动覆盖，请使用 git pull 更新",
        }
    if not os.access(root, os.W_OK):
        return {
            "supported": False,
            "mode": "source",
            "reason": "readonly",
            "hint": "安装目录不可写，请手动下载更新包",
        }
    return {"supported": True, "mode": "source", "reason": "", "hint": ""}


class UpdaterService:
    """Persistent updater state machine."""

    def __init__(self, data_dir: Path, root: Path, mirrors: Any) -> None:
        self._data_dir = data_dir.resolve()
        self._dir = self._data_dir / _UPDATER_DIR_NAME
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._dir / _STATE_FILE_NAME
        self._restart_signal = self._dir / _RESTART_SIGNAL_NAME
        self._root = root.resolve()
        self._mirrors = mirrors
        self._state = self._load_state()
        self._task: asyncio.Task | None = None

        state = self._state.get("state")
        if state in _INTERRUPTIBLE_STATES:
            self._state.update(state="failed", error="进程重启，上次更新操作中断")
            self._save_state()
        elif state == "restarting" and not self._restart_signal.exists():
            self._state.update(state="failed", error="更新重启握手文件缺失")
            self._save_state()

    def _load_state(self) -> dict[str, Any]:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                logger.warning("更新状态文件损坏，已重置", exc_info=True)
        return {"state": "idle"}

    def _save_state(self) -> None:
        _atomic_json(self._state_file, self._state)

    def get_status(self) -> dict[str, Any]:
        # During a portable switchover the launcher owns the terminal state.
        # The candidate/rollback server started while state was "restarting",
        # so refresh its in-memory copy after the launcher commits the result.
        if self._state.get("state") == "restarting":
            persisted = self._load_state()
            if persisted.get("state") != "restarting":
                self._state = persisted
        return {
            **self._state,
            "current_version": __version__,
            "self_update": is_self_update_supported(self._root),
        }

    def is_busy(self) -> bool:
        return self._state.get("state") in _ACTIVE_STATES

    async def download_update(self, api: Any, kind: str) -> dict[str, Any]:
        if self._mirrors is None:
            return {"ok": False, "error": "镜像服务不可用（插件宿主未初始化）"}
        if kind not in _ASSET_PATTERNS:
            return {"ok": False, "error": f"未知的更新包类型：{kind}"}
        if self.is_busy():
            return {"ok": False, "error": "已有更新任务在进行中"}

        support = is_self_update_supported(self._root)
        if not support.get("supported"):
            return {
                "ok": False,
                "error": support.get("hint") or "当前环境不支持自动更新",
            }
        mode = str(support.get("mode", ""))
        if kind != mode:
            expected = "便携版" if mode == "portable" else "完整源码"
            return {"ok": False, "error": f"当前安装方式需要下载{expected}更新包"}

        check = await api.check_updates()
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error", "版本检查失败")}
        latest = check.get("latest") or {}
        if not latest:
            return {"ok": False, "error": "未找到最新版本", "no_release": True}
        selection = select_asset(latest, kind)
        if not selection:
            return {"ok": False, "error": f"未找到 {kind} 类型的更新包"}

        asset, sha_asset = selection
        version = str(latest.get("version", ""))
        self._task = asyncio.create_task(
            self._run_download(asset, sha_asset, version, kind)
        )
        return {
            "ok": True,
            "state": "downloading",
            "version": version,
            "asset": asset.get("name", ""),
        }

    async def _run_download(
        self,
        asset: dict[str, Any],
        sha_asset: dict[str, Any] | None,
        version: str,
        kind: str,
    ) -> None:
        asset_name = str(asset.get("name", ""))
        asset_url = str(asset.get("download_url", ""))
        target = self._dir / Path(asset_name).name
        self._state = {
            "state": "downloading",
            "version": version,
            "kind": kind,
            "asset": asset_name,
            "downloaded_bytes": 0,
            "total_bytes": int(asset.get("size", 0) or 0),
            "mirror_used": "",
            "error": "",
            "restart_needed": False,
        }
        self._save_state()
        try:
            expected_sha = ""
            if sha_asset:
                sha_url = str(sha_asset.get("download_url", ""))
                sha_result = await self._mirrors.fetch_github_url(
                    sha_url, binary=False, max_bytes=4096
                )
                if sha_result.ok and sha_result.data:
                    expected_sha = parse_sha256_file(
                        str(sha_result.data), asset_name
                    )
                else:
                    logger.warning(
                        "SHA-256 校验文件下载失败，跳过校验：%s",
                        sha_result.error,
                    )

            def on_progress(downloaded: int, total: int) -> None:
                self._state["downloaded_bytes"] = downloaded
                if total:
                    self._state["total_bytes"] = total

            result = await self._mirrors.download_to_file(
                asset_url,
                target,
                max_bytes=MAX_APP_UPDATE_BYTES,
                on_progress=on_progress,
            )
            if not result.ok:
                self._state.update(state="failed", error=result.error or "下载失败")
                self._save_state()
                return
            self._state["mirror_used"] = result.mirror_name
            if expected_sha:
                self._state["state"] = "verifying"
                self._save_state()
                verify_sha256(target, expected_sha)
            self._state.update(
                state="staged",
                path=str(target),
                sha256=expected_sha,
                downloaded_at=int(time.time()),
                mirror_used=result.mirror_name,
            )
            self._save_state()
            logger.info(
                "更新包已暂存：%s（版本 %s，镜像 %s）",
                asset_name,
                version,
                result.mirror_name,
            )
        except Exception as exc:
            self._state.update(state="failed", error=str(exc))
            self._save_state()
            logger.exception("更新下载失败")

    async def apply_update(self) -> dict[str, Any]:
        if self.is_busy():
            return {"ok": False, "error": "已有更新任务在进行中"}
        if self._state.get("state") != "staged":
            return {"ok": False, "error": "没有可应用的已下载更新包"}

        archive = Path(str(self._state.get("path", "")))
        if not archive.is_file() or not _path_within(archive, self._dir):
            return {"ok": False, "error": "已下载更新包不存在或路径无效"}

        support = is_self_update_supported(self._root)
        if not support.get("supported"):
            return {"ok": False, "error": support.get("hint") or "当前环境不支持自动更新"}

        kind = str(self._state.get("kind", ""))
        mode = str(support.get("mode", ""))
        if kind != mode:
            expected = "便携版" if mode == "portable" else "完整源码"
            return {"ok": False, "error": f"当前安装方式需要下载{expected}更新包"}

        version = str(self._state.get("version", ""))
        self._state.update(state="applying", error="", restart_needed=False)
        self._save_state()
        self._task = asyncio.create_task(
            self._run_apply(archive, version, mode)
        )
        return {"ok": True, "state": "applying", "version": version}

    async def _run_apply(self, archive: Path, version: str, mode: str) -> None:
        try:
            if mode == "portable":
                result = await asyncio.to_thread(
                    self._prepare_portable_update, archive, version
                )
                self._remove_completed_archive(archive)
                self._state.update(
                    state="restarting",
                    candidate_dir=result["candidate_dir"],
                    error="",
                    restart_needed=False,
                )
                self._save_state()
                _atomic_json(self._restart_signal, result)
                logger.info("便携版更新已准备，等待启动器切换到 %s", version)
            else:
                backup = await asyncio.to_thread(
                    self._apply_source_update, archive, version
                )
                self._remove_completed_archive(archive)
                self._prune_source_backups(backup)
                self._state.update(
                    state="done",
                    backup_dir=str(backup),
                    applied_at=int(time.time()),
                    error="",
                    restart_needed=True,
                )
                self._save_state()
                logger.info("源码更新已应用，等待手动重启：%s", version)
        except UpdateRolledBackError as exc:
            self._state.update(
                state="rolled-back",
                error=str(exc),
                restart_needed=False,
            )
            self._save_state()
            logger.warning("源码更新失败，已回滚：%s", exc)
        except Exception as exc:
            self._state.update(state="failed", error=str(exc), restart_needed=False)
            self._save_state()
            logger.exception("应用更新失败")

    def _remove_completed_archive(self, archive: Path) -> None:
        try:
            archive = archive.resolve()
            if _path_within(archive, self._dir) and archive.is_file():
                archive.unlink()
        except OSError:
            logger.warning("更新成功，但未能删除下载包：%s", archive, exc_info=True)

    def _prune_source_backups(self, keep: Path) -> None:
        try:
            keep = keep.resolve()
            updater_dir = self._dir.resolve()
            for backup in updater_dir.glob("backup-*"):
                try:
                    resolved = backup.resolve()
                    if (
                        resolved != keep
                        and _path_within(resolved, updater_dir)
                        and backup.is_dir()
                    ):
                        shutil.rmtree(backup)
                except OSError:
                    logger.warning(
                        "未能删除旧的源码更新备份：%s", backup, exc_info=True
                    )
        except OSError:
            logger.warning("未能清理旧的源码更新备份", exc_info=True)

    def _prepare_portable_update(
        self, archive: Path, version: str
    ) -> dict[str, Any]:
        install_root_text = os.getenv("TRPG_INSTALL_ROOT", "").strip()
        if not install_root_text:
            raise ValueError("缺少便携版安装根目录")
        install_root = Path(install_root_text).resolve()
        versions_dir = install_root / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        version_dir = versions_dir / _safe_version_dir(version)
        if not _path_within(version_dir, versions_dir):
            raise ValueError("候选版本目录越界")

        extract_dir = self._dir / ("extract-" + _safe_version_dir(version))
        candidate_temp: Path | None = None
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        try:
            package_root = safe_extract_archive(archive, extract_dir)
            required = (
                package_root / "app" / "web_server.py",
                package_root / "python" / "python.exe",
                package_root / "DiceFrame.exe",
            )
            if not all(path.is_file() for path in required):
                raise ValueError("便携版更新包结构无效")

            current_pointer = self._read_current_version_dir(install_root)
            if version_dir.exists():
                if current_pointer and version_dir.resolve() == current_pointer:
                    raise ValueError("不能覆盖当前正在使用的版本目录")
                shutil.rmtree(version_dir)

            candidate_temp = versions_dir / (version_dir.name + ".installing")
            if candidate_temp.exists():
                shutil.rmtree(candidate_temp)
            candidate_temp.mkdir()
            shutil.move(str(package_root / "app"), str(candidate_temp / "app"))
            shutil.move(str(package_root / "python"), str(candidate_temp / "python"))
            os.replace(candidate_temp, version_dir)

            launcher_staged = self._dir / (
                "launcher-" + _safe_version_dir(version) + ".exe"
            )
            shutil.copy2(package_root / "DiceFrame.exe", launcher_staged)
        finally:
            if candidate_temp is not None and candidate_temp.exists():
                shutil.rmtree(candidate_temp, ignore_errors=True)
            shutil.rmtree(extract_dir, ignore_errors=True)

        return {
            "schema": 1,
            "expected_version": version.strip().lstrip("vV"),
            "candidate_dir": str(version_dir),
            "launcher_path": str(launcher_staged),
            "probation_seconds": DEFAULT_PROBATION_SECONDS,
            "created_at": int(time.time()),
        }

    def _read_current_version_dir(self, install_root: Path) -> Path | None:
        pointer = self._dir / "current.json"
        if not pointer.exists():
            return None
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            relative = str(payload.get("relative_dir", ""))
            candidate = (install_root / relative).resolve()
            versions = (install_root / "versions").resolve()
            if _path_within(candidate, versions):
                return candidate
        except Exception:
            logger.warning("当前版本指针无效", exc_info=True)
        return None

    def _apply_source_update(self, archive: Path, version: str) -> Path:
        extract_dir = self._dir / ("extract-" + _safe_version_dir(version))
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = self._dir / f"backup-{timestamp}"
        moved_old: list[tuple[Path, Path]] = []
        installed_new: list[Path] = []
        try:
            package_root = safe_extract_archive(archive, extract_dir)
            if not (
                (package_root / "web_server.py").is_file()
                and (package_root / "src").is_dir()
                and (package_root / "static-v2" / "index.html").is_file()
            ):
                raise ValueError("完整源码更新包结构无效")

            entries = [
                entry
                for entry in package_root.iterdir()
                if entry.name not in _PROTECTED_SOURCE_ENTRIES
            ]
            if not entries:
                raise ValueError("完整源码更新包没有可安装内容")

            backup_dir.mkdir(parents=True)
            for source in entries:
                target = self._root / source.name
                if target.exists():
                    backup_target = backup_dir / source.name
                    shutil.move(str(target), str(backup_target))
                    moved_old.append((backup_target, target))

            for source in entries:
                target = self._root / source.name
                shutil.move(str(source), str(target))
                installed_new.append(target)
            return backup_dir
        except Exception as original:
            rollback_errors: list[str] = []
            for target in reversed(installed_new):
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                except Exception as exc:
                    rollback_errors.append(f"删除新文件 {target.name} 失败：{exc}")
            for backup, target in reversed(moved_old):
                try:
                    if backup.exists():
                        shutil.move(str(backup), str(target))
                except Exception as exc:
                    rollback_errors.append(f"恢复旧文件 {target.name} 失败：{exc}")
            if backup_dir.exists() and not any(backup_dir.iterdir()):
                backup_dir.rmdir()
            if rollback_errors:
                raise RuntimeError(
                    f"源码更新失败且回滚不完整：{original}；"
                    + "；".join(rollback_errors)
                ) from original
            if moved_old or installed_new:
                raise UpdateRolledBackError(
                    f"源码更新失败，已恢复旧版本：{original}"
                ) from original
            raise
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
