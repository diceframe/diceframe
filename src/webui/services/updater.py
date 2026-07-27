"""应用自动更新 -- 下载 release asset + SHA-256 校验 + 暂存（一期）。

复用 MirrorManager 多镜像降级下载，帮国内用户加速。一期只做下载+校验+暂存，
不做自动应用/重启/回滚（二期）。状态持久化到 data/_updater/state.json。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from src.version import __version__
from src.webui.services import system

logger = logging.getLogger("trpg")

MAX_APP_UPDATE_BYTES = 200 * 1024 * 1024  # 应用更新包大小上限 200MB
_UPDATER_DIR_NAME = "_updater"
_STATE_FILE_NAME = "state.json"

# 产物名匹配（与 scripts/build_release.py / build_portable.py 产物名一致）
# source: DiceFrame-v{ver}-windows.zip ; portable: DiceFrame-v{ver}-windows-portable.zip
_ASSET_PATTERNS = {
    "source": re.compile(r"DiceFrame-v.+windows\.zip$", re.IGNORECASE),
    "portable": re.compile(r"DiceFrame-v.+windows-portable\.zip$", re.IGNORECASE),
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

_ACTIVE_STATES = {"downloading", "verifying"}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    """校验文件 SHA-256；expected 非合法 64 位 hex 或不匹配时抛 ValueError。"""
    normalized = (expected or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError(f"SHA-256 格式无效：{path.name}")
    actual = _file_sha256(path)
    if not hmac.compare_digest(actual, normalized):
        raise ValueError(f"SHA-256 校验失败：{path.name}（期望 {normalized[:8]}…，实际 {actual[:8]}…）")


def select_asset(latest: dict[str, Any], kind: str) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """从 latest.assets 选指定类型的 zip asset 及其同名 .sha256 asset。

    返回 (zip_asset, sha_asset_or_None)；找不到 zip 返回 None。
    .sha256 缺失时第二项为 None（调用方决定是否容缺校验）。
    """
    assets = latest.get("assets") or []
    pattern = _ASSET_PATTERNS.get(kind)
    if not pattern:
        return None
    target: dict[str, Any] | None = None
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.endswith(".sha256"):
            continue
        if pattern.search(name):
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
    """解析 sha256sum 输出（'hash  filename' 或纯 hash），返回 64 位 hex。"""
    for line in (content or "").splitlines():
        match = _SHA256_RE.search(line.strip())
        if match:
            return match.group(0)
    raise ValueError(f"无法从校验文件解析 SHA-256：{filename}")


def is_self_update_supported(root: Path) -> dict[str, Any]:
    """检测当前环境是否支持自动应用（二期才真正应用；一期仅展示给前端）。"""
    if Path("/.dockerenv").exists() or os.getenv("TRPG_DATA_DIR") == "/app/data":
        return {"supported": False, "reason": "docker",
                "hint": "Docker 环境无法原地更新，请用 docker compose pull && up -d"}
    if not os.access(root, os.W_OK):
        return {"supported": False, "reason": "readonly",
                "hint": "安装目录不可写，请手动下载更新包解压覆盖"}
    return {"supported": True, "reason": "", "hint": ""}


class UpdaterService:
    """应用更新状态机：idle -> downloading -> verifying -> staged/failed。

    下载为后台 asyncio.Task；进度（downloaded_bytes）只更新内存，
    状态转换时持久化 state.json。进程重启时未完成的下载标记为 failed。
    """

    def __init__(self, data_dir: Path, root: Path, mirrors: Any) -> None:
        self._dir = data_dir / _UPDATER_DIR_NAME
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._dir / _STATE_FILE_NAME
        self._root = root
        self._mirrors = mirrors
        self._state = self._load_state()
        self._task: asyncio.Task | None = None
        if self._state.get("state") in _ACTIVE_STATES:
            self._state.update(state="failed", error="进程重启，上次下载中断")
            self._save_state()

    def _load_state(self) -> dict[str, Any]:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                logger.warning("更新状态文件损坏，重置", exc_info=True)
        return {"state": "idle"}

    def _save_state(self) -> None:
        tmp = self._state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_file)

    def get_status(self) -> dict[str, Any]:
        return {**self._state, "current_version": __version__, "self_update": is_self_update_supported(self._root)}

    def is_busy(self) -> bool:
        return self._state.get("state") in _ACTIVE_STATES

    async def download_update(self, api: Any, kind: str) -> dict[str, Any]:
        if self._mirrors is None:
            return {"ok": False, "error": "镜像源不可用（插件宿主未初始化）"}
        if kind not in _ASSET_PATTERNS:
            return {"ok": False, "error": f"未知的更新包类型：{kind}"}
        if self.is_busy():
            return {"ok": False, "error": "已有更新任务在进行中"}
        check = await system.check_updates(api)
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
        self._task = asyncio.create_task(self._run_download(asset, sha_asset, version))
        return {"ok": True, "state": "downloading", "version": version, "asset": asset.get("name", "")}

    async def _run_download(self, asset: dict[str, Any], sha_asset: dict[str, Any] | None, version: str) -> None:
        asset_name = str(asset.get("name", ""))
        asset_url = str(asset.get("download_url", ""))
        target = self._dir / asset_name
        self._state = {
            "state": "downloading",
            "version": version,
            "asset": asset_name,
            "downloaded_bytes": 0,
            "total_bytes": int(asset.get("size", 0) or 0),
            "mirror_used": "",
            "error": "",
        }
        self._save_state()
        try:
            expected_sha = ""
            if sha_asset:
                sha_url = str(sha_asset.get("download_url", ""))
                sha_res = await self._mirrors.fetch_github_url(sha_url, binary=False, max_bytes=4096)
                if sha_res.ok and sha_res.data:
                    expected_sha = parse_sha256_file(str(sha_res.data), asset_name)
                else:
                    logger.warning("SHA-256 校验文件下载失败，跳过校验：%s", sha_res.error)

            def on_progress(downloaded: int, total: int) -> None:
                self._state["downloaded_bytes"] = downloaded
                if total:
                    self._state["total_bytes"] = total

            res = await self._mirrors.download_to_file(
                asset_url, target, max_bytes=MAX_APP_UPDATE_BYTES, on_progress=on_progress,
            )
            if not res.ok:
                self._state.update(state="failed", error=res.error or "下载失败")
                self._save_state()
                return
            self._state["mirror_used"] = res.mirror_name
            if expected_sha:
                self._state["state"] = "verifying"
                self._save_state()
                verify_sha256(target, expected_sha)
            self._state.update(
                state="staged",
                path=str(target),
                sha256=expected_sha,
                downloaded_at=int(time.time()),
                mirror_used=res.mirror_name,
            )
            self._save_state()
            logger.info("更新包已暂存：%s (版本 %s，镜像 %s)", asset_name, version, res.mirror_name)
        except Exception as exc:
            self._state.update(state="failed", error=str(exc))
            self._save_state()
            logger.exception("更新下载失败")
