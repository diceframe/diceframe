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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable

from src.docker_launcher.contracts import (
    PLATFORM as DOCKER_PLATFORM,
    PYTHON_ABI as DOCKER_PYTHON_ABI,
    RUNTIME_API as DOCKER_RUNTIME_API,
    safe_extract_package as safe_extract_docker_package,
    validate_package_tree as validate_docker_package_tree,
)
from src.docker_launcher.data_versions import data_for_payload, runtime_for_data
from src.version import __version__
from src.localization.messages import t

logger = logging.getLogger("trpg")

MAX_APP_UPDATE_BYTES = 200 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
DEFAULT_PROBATION_SECONDS = 90
_UPDATER_DIR_NAME = "_updater"
_STATE_FILE_NAME = "state.json"
_RESTART_SIGNAL_NAME = "restart_signal.json"
_ROLLBACK_LAUNCHER_PREFIX = "launcher-rollback-"

_ASSET_PATTERNS = {
    "source": re.compile(r"DiceFrame-v.+windows\.zip$", re.IGNORECASE),
    "portable": re.compile(r"DiceFrame-v.+windows-portable\.zip$", re.IGNORECASE),
    "docker": re.compile(
        r"^DiceFrame-v[0-9A-Za-z][0-9A-Za-z._+-]{0,63}-"
        r"docker-update-linux-amd64\.zip$"
    ),
}


@dataclass(frozen=True)
class UpdaterDependencies:
    data_dir: Path
    root: Path
    mirrors: Any
    check_updates: Callable[[], Awaitable[dict[str, Any]]]
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
_BUILTIN_PLUGIN_DIRS = {"examples", "qq-napcat"}


def _migrate_user_plugin_packages(
    source_plugins_dir: Path, packages_dir: Path
) -> None:
    """把旧 plugins/ 里非内置的插件复制到 data/plugin-packages/，跨版本保留。

    用复制而非移动：apply 可能失败回滚，复制保留旧版本目录的插件，回滚到
    旧版本后用户插件仍可用；移动会让旧版本读不到用户插件。跳过 examples
    内置示例与无效目录；目标已存在则跳过（幂等）。
    """
    if not source_plugins_dir.is_dir():
        return
    packages_dir.mkdir(parents=True, exist_ok=True)
    for child in sorted(source_plugins_dir.iterdir()):
        name = child.name
        if name.startswith(".") or name in _BUILTIN_PLUGIN_DIRS:
            continue
        if not child.is_dir() or not (child / "plugin.json").is_file():
            continue
        dest = packages_dir / name
        if dest.exists():
            continue
        shutil.copytree(child, dest)


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
        raise ValueError(t("webui.services.updater.sha256_format_invalid", name=path.name))
    actual = _file_sha256(path)
    if not hmac.compare_digest(actual, normalized):
        raise ValueError(
            t("webui.services.updater.sha256_mismatch", name=path.name, expected=normalized[:8], actual=actual[:8])
        )


def select_asset(
    latest: dict[str, Any], kind: str, expected_version: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """Select one exact update asset and its checksum manifest."""
    assets = latest.get("assets") or []
    pattern = _ASSET_PATTERNS.get(kind)
    if not pattern:
        return None
    target: dict[str, Any] | None = None
    for asset in assets:
        name = str(asset.get("name", ""))
        if not name.endswith(".sha256") and pattern.search(name):
            if kind == "docker":
                normalized = expected_version.strip().lstrip("vV")
                expected_name = f"DiceFrame-v{normalized}-docker-update-linux-amd64.zip"
                if not normalized or name != expected_name:
                    continue
            target = asset
            break
    if not target:
        return None
    sha_asset = next((asset for asset in assets if str(asset.get("name", "")) == "SHA256SUMS"), None)
    if sha_asset is None:
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
    raise ValueError(t("webui.services.updater.sha256_parse_failed", filename=filename))


def parse_sha256_manifest(content: str, filename: str) -> str:
    """Return exactly one checksum bound to filename; reject ambiguity."""

    matches: list[str] = []
    for raw in (content or "").splitlines():
        parts = raw.strip().split()
        if len(parts) < 2 or parts[-1].lstrip("*") != filename:
            continue
        digest = parts[0].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(t("webui.services.updater.manifest_sha256_invalid", filename=filename))
        matches.append(digest)
    if len(matches) != 1:
        raise ValueError(t("webui.services.updater.manifest_single_record", filename=filename))
    return matches[0]


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _extended_length_path(text: str) -> str:
    """把 Windows 绝对路径改写成扩展长度形式（``\\\\?\\`` 前缀）。

    便携版常被解压在多层嵌套目录里，系统又默认没开 LongPathsEnabled：普通路径
    超过 260 字符（MAX_PATH）就打不开，更新包里最深的文件会让解压直接失败
    （FileNotFoundError），哪怕最终装进 ``versions/`` 的路径并不超长——解压临时
    目录比最终位置深得多。扩展长度路径不受这个限制。

    扩展长度路径不再做 ``.``/``..``、正斜杠等规范化，调用方必须传已规范化的
    绝对路径（见 :func:`_io_path`）。
    """
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _io_path(path: Path) -> Path:
    """更新包解压、搬移、清理时实际交给文件系统的路径。

    Windows 上换成扩展长度形式；其它平台原样返回。只用于 I/O 调用本身——越界
    检查等路径比较仍用普通路径，免得前缀形式与普通形式互相比不上。
    """
    if os.name != "nt":
        return path
    return Path(_extended_length_path(os.path.abspath(path)))


def _rmtree(path: Path, *, ignore_errors: bool = False) -> None:
    shutil.rmtree(_io_path(path), ignore_errors=ignore_errors)


def _move(source: Path, target: Path) -> None:
    # 跨卷（数据目录单独放在别的盘）时 shutil.move 会退回复制 + 删除，同样要走长路径。
    shutil.move(str(_io_path(source)), str(_io_path(target)))


def _archive_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(t("webui.services.updater.archive_unsafe_path", name=name))
    return path


def safe_extract_archive(archive: Path, destination: Path) -> Path:
    """Safely extract a release zip and return its single top-level directory."""
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError(t("webui.services.updater.archive_empty_or_too_many"))

        total_size = 0
        top_levels: set[str] = set()
        checked: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for member in members:
            path = _archive_member_path(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(t("webui.services.updater.archive_symlink", filename=member.filename))
            total_size += member.file_size
            if total_size > MAX_EXTRACTED_BYTES:
                raise ValueError(t("webui.services.updater.archive_too_large"))
            top_levels.add(path.parts[0])
            checked.append((member, path))

        if len(top_levels) != 1:
            raise ValueError(t("webui.services.updater.archive_single_root"))

        root = destination.resolve()
        for member, relative in checked:
            target = (destination / Path(*relative.parts)).resolve()
            if not _path_within(target, root):
                raise ValueError(t("webui.services.updater.archive_path_outside", filename=member.filename))
            io_target = _io_path(target)
            if member.is_dir():
                io_target.mkdir(parents=True, exist_ok=True)
                continue
            io_target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, io_target.open("wb") as output:
                shutil.copyfileobj(source, output)

    extracted_root = destination / next(iter(top_levels))
    if not extracted_root.is_dir():
        raise ValueError(t("webui.services.updater.archive_root_invalid"))
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
        raise ValueError(t("webui.services.updater.version_format_invalid", version=version))
    return "v" + normalized


def is_self_update_supported(root: Path) -> dict[str, Any]:
    """Describe whether self-update is supported and which install mode is active."""
    install_mode = os.getenv("TRPG_INSTALL_MODE", "").strip().lower()
    if install_mode == "docker-managed":
        runtime_text = os.getenv("TRPG_DOCKER_RUNTIME_ROOT", "").strip()
        if not runtime_text:
            return {
                "supported": False, "mode": "docker", "reason": "runtime",
                "hint": t("webui.services.updater.docker_base_image_outdated"),
            }
        runtime_root = Path(runtime_text)
        if not runtime_root.is_dir() or not os.access(runtime_root, os.W_OK):
            return {
                "supported": False, "mode": "docker", "reason": "readonly",
                "hint": t("webui.services.updater.docker_managed_dir_not_writable"),
            }
        return {"supported": True, "mode": "docker", "reason": "", "hint": ""}
    if Path("/.dockerenv").exists() or os.getenv("TRPG_DATA_DIR") == "/app/data":
        return {
            "supported": False,
            "mode": "docker",
            "reason": "docker",
            "hint": (
                t("webui.services.updater.docker_manual_update")
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
                "hint": t("webui.services.updater.portable_dir_not_writable"),
            }
        return {"supported": True, "mode": "portable", "reason": "", "hint": ""}

    if (root / ".git").exists():
        return {
            "supported": False,
            "mode": "development",
            "reason": "development",
            "hint": t("webui.services.updater.git_checkout_manual"),
        }
    if not os.access(root, os.W_OK):
        return {
            "supported": False,
            "mode": "source",
            "reason": "readonly",
            "hint": t("webui.services.updater.install_dir_not_writable"),
        }
    return {"supported": True, "mode": "source", "reason": "", "hint": ""}


class UpdaterService:
    """Persistent updater state machine."""

    def __init__(self, dependencies: UpdaterDependencies) -> None:
        self._data_dir = dependencies.data_dir.resolve()
        self._dir = runtime_for_data(self._data_dir, os.environ) or self._data_dir / _UPDATER_DIR_NAME
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._dir / _STATE_FILE_NAME
        self._restart_signal = self._dir / _RESTART_SIGNAL_NAME
        self._root = dependencies.root.resolve()
        self._mirrors = dependencies.mirrors
        self._check_updates = dependencies.check_updates
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
            "rollback": self.get_rollback_status(),
        }

    def is_busy(self) -> bool:
        return self._state.get("state") in _ACTIVE_STATES

    def _read_portable_pointer_dir(
        self, install_root: Path, field: str,
    ) -> Path | None:
        pointer = self._dir / "current.json"
        if not pointer.exists():
            return None
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            relative = str(payload.get(field) or "")
            versions = (install_root / "versions").resolve()
            candidate = (install_root / relative).resolve()
            if (
                not relative
                or not _path_within(candidate, versions)
                or not (candidate / "app" / "web_server.py").is_file()
                or not (candidate / "python" / "python.exe").is_file()
            ):
                return None
            return candidate
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("便携版当前版本指针无效", exc_info=True)
            return None

    @staticmethod
    def _portable_payload_version(directory: Path) -> str:
        version_file = directory / "app" / "src" / "version.py"
        try:
            text = version_file.read_text(encoding="utf-8")
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
            if match:
                return match.group(1).strip().lstrip("vV")
        except OSError:
            pass
        return directory.name.strip().lstrip("vV")

    def _read_docker_pointer_dir(self, runtime_root: Path, field: str) -> Path | None:
        pointer = runtime_root / "current.json"
        if not pointer.exists():
            return None
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            relative = str(payload.get(field) or "")
            versions = (runtime_root / "docker-versions").resolve()
            candidate = (runtime_root / relative).resolve()
            if not relative or not _path_within(candidate, versions):
                return None
            return candidate
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("Docker 当前版本指针无效", exc_info=True)
            return None

    def _rollback_target(self) -> tuple[str, Path, str] | None:
        support = is_self_update_supported(self._root)
        if not support.get("supported"):
            return None
        mode = str(support.get("mode") or "")
        if mode == "portable":
            install_root_text = os.getenv("TRPG_INSTALL_ROOT", "").strip()
            if not install_root_text:
                return None
            install_root = Path(install_root_text).resolve()
            current = self._read_portable_pointer_dir(install_root, "relative_dir")
            target = self._read_portable_pointer_dir(install_root, "previous_relative_dir")
            if not current or not target or current == target:
                return None
            version = self._portable_payload_version(target)
            try:
                _safe_version_dir(version)
            except ValueError:
                return None
            return mode, target, version
        if mode == "docker":
            runtime_text = os.getenv("TRPG_DOCKER_RUNTIME_ROOT", "").strip()
            if not runtime_text:
                return None
            runtime_root = Path(runtime_text).resolve()
            current = self._read_docker_pointer_dir(runtime_root, "relative_dir")
            target = self._read_docker_pointer_dir(runtime_root, "previous_relative_dir")
            if not current or not target or current == target:
                return None
            try:
                manifest = validate_docker_package_tree(
                    target,
                    platform=DOCKER_PLATFORM,
                    python_abi=DOCKER_PYTHON_ABI,
                    runtime_api=DOCKER_RUNTIME_API,
                )
                if os.getenv("TRPG_DOCKER_DATA_HOME"):
                    data_for_payload(runtime_root, Path(os.environ["TRPG_DOCKER_DATA_HOME"]).resolve(), target)
            except (OSError, ValueError):
                logger.warning("Docker 上一版本包无效", exc_info=True)
                return None
            return mode, target, str(manifest["version"])
        return None

    def get_rollback_status(self) -> dict[str, Any]:
        support = is_self_update_supported(self._root)
        mode = str(support.get("mode") or "")
        if mode not in {"portable", "docker"}:
            return {
                "available": False,
                "mode": mode,
                "target_version": "",
                "reason": t("webui.services.updater.no_launcher_previous_version"),
            }
        target = self._rollback_target()
        if target is None:
            return {
                "available": False,
                "mode": mode,
                "target_version": "",
                "reason": t("webui.services.updater.no_previous_copy"),
            }
        return {
            "available": True,
            "mode": target[0],
            "target_version": target[2],
            "reason": "",
            "restores_data": target[0] == "docker" and bool(os.getenv("TRPG_DOCKER_DATA_HOME")),
        }

    async def rollback_update(self) -> dict[str, Any]:
        if self.is_busy():
            return {"ok": False, "error": t("webui.services.updater.update_in_progress")}
        target = self._rollback_target()
        if target is None:
            return {"ok": False, "error": self.get_rollback_status()["reason"]}

        mode, target_dir, version = target
        if mode == "docker" and os.getenv("TRPG_DOCKER_DATA_HOME"):
            from src.compat.program_rollback_access import prepare_rollback_access

            runtime = Path(os.environ["TRPG_DOCKER_RUNTIME_ROOT"]).resolve()
            home = Path(os.environ["TRPG_DOCKER_DATA_HOME"]).resolve()
            target_data = data_for_payload(runtime, home, target_dir)
            try:
                await asyncio.to_thread(prepare_rollback_access, target_data)
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": t("webui.services.updater.rollback_data_failed", exc=exc)}
        signal: dict[str, Any] = {
            "schema": 1,
            "operation": "rollback",
            "mode": mode,
            "expected_version": version,
            "candidate_dir": str(target_dir),
            "relative_dir": target_dir.relative_to(
                Path(os.getenv("TRPG_DOCKER_RUNTIME_ROOT", "")).resolve()
                if mode == "docker"
                else Path(os.getenv("TRPG_INSTALL_ROOT", "")).resolve()
            ).as_posix(),
            "probation_seconds": DEFAULT_PROBATION_SECONDS,
            "created_at": int(time.time()),
        }
        if mode == "portable":
            install_root = Path(os.getenv("TRPG_INSTALL_ROOT", "")).resolve()
            launcher = install_root / "DiceFrame.exe"
            if not launcher.is_file():
                return {"ok": False, "error": t("webui.services.updater.portable_launcher_missing")}
            staged_launcher = self._dir / (
                _ROLLBACK_LAUNCHER_PREFIX + _safe_version_dir(version) + ".exe"
            )
            try:
                shutil.copy2(launcher, staged_launcher)
            except OSError as exc:
                return {"ok": False, "error": t("webui.services.updater.rollback_launcher_failed", exc=exc)}
            signal["launcher_path"] = str(staged_launcher)

        self._state.update(
            state="restarting",
            operation="rollback",
            version=version,
            kind=mode,
            candidate_dir=str(target_dir),
            error="",
            restart_needed=False,
        )
        self._save_state()
        _atomic_json(self._restart_signal, signal)
        return {
            "ok": True,
            "state": "restarting",
            "operation": "rollback",
            "version": version,
        }

    async def download_update(self, kind: str) -> dict[str, Any]:
        if self._mirrors is None:
            return {"ok": False, "error": t("webui.services.updater.mirror_unavailable")}
        if kind not in _ASSET_PATTERNS:
            return {"ok": False, "error": t("webui.services.updater.package_kind_unknown", kind=kind)}
        if self.is_busy():
            return {"ok": False, "error": t("webui.services.updater.update_in_progress")}

        support = is_self_update_supported(self._root)
        if not support.get("supported"):
            return {
                "ok": False,
                "error": support.get("hint") or t("webui.services.updater.auto_update_unsupported"),
            }
        mode = str(support.get("mode", ""))
        if kind != mode:
            return {"ok": False, "error": t(
                "webui.services.updater.portable_package_required" if mode == "portable" else "webui.services.updater.source_package_required"
            )}

        check = await self._check_updates()
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error", "版本检查失败")}
        latest = check.get("latest") or {}
        if not latest:
            return {"ok": False, "error": t("webui.services.updater.latest_not_found"), "no_release": True}
        version = str(latest.get("version", ""))
        selection = select_asset(latest, kind, version)
        if not selection:
            return {"ok": False, "error": t("webui.services.updater.package_kind_not_found", kind=kind)}

        asset, sha_asset = selection
        if kind == "docker":
            if not sha_asset:
                return {"ok": False, "error": t("webui.services.updater.docker_checksums_missing")}
            size = int(asset.get("size", 0) or 0)
            if size <= 0 or size > MAX_APP_UPDATE_BYTES:
                return {"ok": False, "error": t("webui.services.updater.docker_package_size_invalid")}
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
        partial = target.with_name(target.name + ".part")
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
                    sha_url, binary=False, max_bytes=4096, resolve=False,
                )
                if sha_result.ok and sha_result.data:
                    expected_sha = (
                        parse_sha256_manifest(str(sha_result.data), asset_name)
                        if kind == "docker" or str(sha_asset.get("name", "")) == "SHA256SUMS"
                        else parse_sha256_file(str(sha_result.data), asset_name)
                    )
                else:
                    if kind == "docker":
                        raise ValueError(t("webui.services.updater.docker_checksums_download_failed"))
                    logger.warning("SHA-256 校验文件下载失败，跳过校验：%s", sha_result.error)
            elif kind == "docker":
                raise ValueError(t("webui.services.updater.docker_checksums_required"))

            def on_progress(downloaded: int, total: int) -> None:
                self._state["downloaded_bytes"] = downloaded
                if total:
                    self._state["total_bytes"] = total

            result = await self._mirrors.download_to_file(
                asset_url,
                partial,
                max_bytes=MAX_APP_UPDATE_BYTES,
                on_progress=on_progress,
                resolve=False,
            )
            if not result.ok:
                self._state.update(state="failed", error=result.error or "下载失败")
                self._save_state()
                return
            self._state["mirror_used"] = result.mirror_name
            if expected_sha:
                self._state["state"] = "verifying"
                self._save_state()
                verify_sha256(partial, expected_sha)
            os.replace(partial, target)
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
            partial.unlink(missing_ok=True)
            if kind == "docker":
                target.unlink(missing_ok=True)
            self._state.update(state="failed", error=str(exc))
            self._save_state()
            logger.exception("更新下载失败")

    async def apply_update(self) -> dict[str, Any]:
        if self.is_busy():
            return {"ok": False, "error": t("webui.services.updater.update_in_progress")}
        if self._state.get("state") != "staged":
            return {"ok": False, "error": t("webui.services.updater.no_downloaded_package")}

        archive = Path(str(self._state.get("path", "")))
        if not archive.is_file() or not _path_within(archive, self._dir):
            return {"ok": False, "error": t("webui.services.updater.downloaded_package_missing")}

        support = is_self_update_supported(self._root)
        if not support.get("supported"):
            return {"ok": False, "error": support.get("hint") or t("webui.services.updater.auto_update_unsupported")}

        kind = str(self._state.get("kind", ""))
        mode = str(support.get("mode", ""))
        if kind != mode:
            return {"ok": False, "error": t(
                "webui.services.updater.portable_package_required" if mode == "portable" else "webui.services.updater.source_package_required"
            )}

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
            elif mode == "docker":
                result = await asyncio.to_thread(
                    self._prepare_docker_update, archive, version
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
                logger.info("Docker 更新已准备，等待 launcher 切换到 %s", version)
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

    def _prepare_docker_update(self, archive: Path, version: str) -> dict[str, Any]:
        runtime_text = os.getenv("TRPG_DOCKER_RUNTIME_ROOT", "").strip()
        if not runtime_text:
            raise ValueError(t("webui.services.updater.docker_managed_dir_missing"))
        runtime_root = Path(runtime_text).resolve()
        if runtime_root != self._dir.resolve():
            raise ValueError(t("webui.services.updater.docker_managed_dir_mismatch"))
        versions_dir = runtime_root / "docker-versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        version_dir = versions_dir / _safe_version_dir(version)
        if not _path_within(version_dir, versions_dir):
            raise ValueError(t("webui.services.updater.docker_candidate_dir_outside"))

        current = self._read_docker_current_dir(runtime_root)
        if current and current == version_dir.resolve():
            raise ValueError(t("webui.services.updater.cannot_overwrite_running_docker"))
        active_text = os.getenv("TRPG_ACTIVE_VERSION_DIR", "").strip()
        if active_text:
            active = Path(active_text).resolve()
            if not _path_within(active, versions_dir):
                raise ValueError(t("webui.services.updater.docker_active_dir_outside"))
            if active == version_dir.resolve():
                raise ValueError(t("webui.services.updater.cannot_overwrite_running_docker"))
        extract_dir = runtime_root / ("extract-" + _safe_version_dir(version))
        installing = versions_dir / (version_dir.name + ".installing")
        shutil.rmtree(extract_dir, ignore_errors=True)
        shutil.rmtree(installing, ignore_errors=True)
        extract_dir.mkdir(parents=True)
        try:
            package_root = safe_extract_docker_package(archive, extract_dir)
            manifest = validate_docker_package_tree(
                package_root,
                expected_version=version,
                platform=DOCKER_PLATFORM,
                python_abi=DOCKER_PYTHON_ABI,
                runtime_api=DOCKER_RUNTIME_API,
            )
            if version_dir.exists():
                existing_manifest = validate_docker_package_tree(
                    version_dir,
                    expected_version=version,
                    platform=DOCKER_PLATFORM,
                    python_abi=DOCKER_PYTHON_ABI,
                    runtime_api=DOCKER_RUNTIME_API,
                )
                if existing_manifest != manifest:
                    raise ValueError(t("webui.services.updater.docker_candidate_conflict"))
                manifest = existing_manifest
            else:
                os.replace(package_root, installing)
                os.replace(installing, version_dir)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            shutil.rmtree(installing, ignore_errors=True)

        legacy_plugins = Path(os.getenv("TRPG_LEGACY_PLUGIN_DIR", "/app/plugins"))
        _migrate_user_plugin_packages(legacy_plugins, self._data_dir / "plugin-packages")
        return {
            "schema": 1,
            "mode": "docker",
            "expected_version": str(manifest["version"]),
            "relative_dir": version_dir.relative_to(runtime_root).as_posix(),
            "candidate_dir": str(version_dir),
            "probation_seconds": int(manifest["probation_seconds"]),
            "created_at": int(time.time()),
        }

    def _read_docker_current_dir(self, runtime_root: Path) -> Path | None:
        pointer = runtime_root / "current.json"
        if not pointer.exists():
            return None
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            candidate = (runtime_root / str(payload.get("relative_dir") or "")).resolve()
            versions = (runtime_root / "docker-versions").resolve()
            if _path_within(candidate, versions):
                return candidate
        except Exception:
            logger.warning("Docker 当前版本指针无效", exc_info=True)
        return None

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
                        _rmtree(backup)
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
            raise ValueError(t("webui.services.updater.portable_root_missing"))
        install_root = Path(install_root_text).resolve()
        versions_dir = install_root / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        version_dir = versions_dir / _safe_version_dir(version)
        if not _path_within(version_dir, versions_dir):
            raise ValueError(t("webui.services.updater.candidate_dir_outside"))

        extract_dir = self._dir / ("extract-" + _safe_version_dir(version))
        candidate_temp: Path | None = None
        if extract_dir.exists():
            _rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        try:
            package_root = safe_extract_archive(archive, extract_dir)
            required = (
                package_root / "app" / "web_server.py",
                package_root / "python" / "python.exe",
                package_root / "DiceFrame.exe",
            )
            if not all(path.is_file() for path in required):
                raise ValueError(t("webui.services.updater.portable_package_invalid"))

            current_pointer = self._read_current_version_dir(install_root)
            if version_dir.exists():
                if current_pointer and version_dir.resolve() == current_pointer:
                    raise ValueError(t("webui.services.updater.cannot_overwrite_active_version"))
                _rmtree(version_dir)

            candidate_temp = versions_dir / (version_dir.name + ".installing")
            if candidate_temp.exists():
                _rmtree(candidate_temp)
            candidate_temp.mkdir()
            _move(package_root / "app", candidate_temp / "app")
            _move(package_root / "python", candidate_temp / "python")
            os.replace(candidate_temp, version_dir)

            launcher_staged = self._dir / (
                "launcher-" + _safe_version_dir(version) + ".exe"
            )
            shutil.copy2(package_root / "DiceFrame.exe", launcher_staged)
        finally:
            if candidate_temp is not None and candidate_temp.exists():
                _rmtree(candidate_temp, ignore_errors=True)
            _rmtree(extract_dir, ignore_errors=True)

        # 迁移旧 plugins/ 里的用户插件：同时覆盖 versions 布局的 active 版本
        # 与根目录旧布局（1.9.5 及更早的便携版），旧布局用户插件在根 app/ 下。
        sources = []
        if current_pointer:
            sources.append(current_pointer / "app" / "plugins")
        sources.append(install_root / "app" / "plugins")
        for source in sources:
            _migrate_user_plugin_packages(
                source,
                self._dir.parent / "plugin-packages",
            )

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
            _rmtree(extract_dir)
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
                raise ValueError(t("webui.services.updater.source_package_invalid"))

            _migrate_user_plugin_packages(
                self._root / "plugins",
                self._dir.parent / "plugin-packages",
            )

            entries = [
                entry
                for entry in package_root.iterdir()
                if entry.name not in _PROTECTED_SOURCE_ENTRIES
            ]
            if not entries:
                raise ValueError(t("webui.services.updater.source_package_empty"))

            backup_dir.mkdir(parents=True)
            for source in entries:
                target = self._root / source.name
                if target.exists():
                    backup_target = backup_dir / source.name
                    _move(target, backup_target)
                    moved_old.append((backup_target, target))

            for source in entries:
                target = self._root / source.name
                _move(source, target)
                installed_new.append(target)
            return backup_dir
        except Exception as original:
            rollback_errors: list[str] = []
            for target in reversed(installed_new):
                try:
                    if target.is_dir():
                        _rmtree(target)
                    elif target.exists():
                        target.unlink()
                except Exception as exc:
                    rollback_errors.append(f"删除新文件 {target.name} 失败：{exc}")
            for backup, target in reversed(moved_old):
                try:
                    if backup.exists():
                        _move(backup, target)
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
                    t("webui.services.updater.source_update_rolled_back", original=original)
                ) from original
            raise
        finally:
            _rmtree(extract_dir, ignore_errors=True)
