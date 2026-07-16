"""Mirror-aware HTTP helpers for plugin marketplace downloads."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp


DEFAULT_MARKETPLACE_OWNER = "diceframe"
DEFAULT_MARKETPLACE_REPO = "diceframe-plugins"
DEFAULT_MARKETPLACE_BRANCH = "main"
DEFAULT_MARKETPLACE_FILE = "plugin_details.json"

DEFAULT_MIRRORS: list[dict[str, Any]] = [
    {
        "id": "gitproxy-mrhjx",
        "name": "gitproxy.mrhjx.cn",
        "raw_prefix": "https://gitproxy.mrhjx.cn/https://raw.githubusercontent.com",
        "clone_prefix": "https://gitproxy.mrhjx.cn/https://github.com",
        "enabled": True,
        "priority": 1,
    },
    {
        "id": "ghproxy-vip",
        "name": "ghproxy.vip",
        "raw_prefix": "https://ghproxy.vip/https://raw.githubusercontent.com",
        "clone_prefix": "https://ghproxy.vip/https://github.com",
        "enabled": True,
        "priority": 2,
    },
    {
        "id": "github",
        "name": "GitHub 官方源",
        "raw_prefix": "https://raw.githubusercontent.com",
        "clone_prefix": "https://github.com",
        "enabled": True,
        "priority": 3,
    },
    {
        "id": "gh-proxy-com",
        "name": "gh-proxy.com",
        "raw_prefix": "https://gh-proxy.com/https://raw.githubusercontent.com",
        "clone_prefix": "https://gh-proxy.com/https://github.com",
        "enabled": True,
        "priority": 4,
    },
    {
        "id": "v6-gh-proxy",
        "name": "v6.gh-proxy.org",
        "raw_prefix": "https://v6.gh-proxy.org/https://raw.githubusercontent.com",
        "clone_prefix": "https://v6.gh-proxy.org/https://github.com",
        "enabled": True,
        "priority": 5,
    },
    {
        "id": "cdn-gh-proxy-com",
        "name": "cdn.gh-proxy.com",
        "raw_prefix": "https://cdn.gh-proxy.com/https://raw.githubusercontent.com",
        "clone_prefix": "https://cdn.gh-proxy.com/https://github.com",
        "enabled": True,
        "priority": 6,
    },
]


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    data: str | bytes | None = None
    error: str = ""
    url: str = ""
    mirror_id: str = ""
    mirror_name: str = ""
    mirror_index: int = 0
    total_mirrors: int = 0
    attempts: int = 0
    elapsed_ms: int = 0
    status: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "url": self.url,
            "mirror_id": self.mirror_id,
            "mirror_name": self.mirror_name,
            "mirror_index": self.mirror_index,
            "total_mirrors": self.total_mirrors,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
            "status": self.status,
        }


class MirrorManager:
    def __init__(self, config_path: Path, *, timeout_sec: int = 20, max_attempts: int = 2) -> None:
        self.config_path = config_path
        self.timeout_sec = timeout_sec
        self.max_attempts = max_attempts
        self.mirrors = self._load()

    def list(self) -> list[dict[str, Any]]:
        return [dict(mirror) for mirror in sorted(self.mirrors, key=lambda item: int(item.get("priority", 999)))]

    def enabled(self) -> list[dict[str, Any]]:
        return [mirror for mirror in self.list() if mirror.get("enabled")]

    def add(self, data: dict[str, Any]) -> dict[str, Any]:
        mirror_id = _clean_id(data.get("id"))
        if any(mirror.get("id") == mirror_id for mirror in self.mirrors):
            raise ValueError(f"镜像源 ID 已存在：{mirror_id}")
        mirror = self._normalize(data, default_priority=self._next_priority())
        self.mirrors.append(mirror)
        self._save()
        return dict(mirror)

    def update(self, mirror_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        for index, mirror in enumerate(self.mirrors):
            if mirror.get("id") == mirror_id:
                updated = dict(mirror)
                for key in ("name", "raw_prefix", "clone_prefix", "enabled", "priority"):
                    if key in patch:
                        updated[key] = patch[key]
                self.mirrors[index] = self._normalize(updated, default_priority=int(mirror.get("priority", 999)))
                self._save()
                return dict(self.mirrors[index])
        raise KeyError(f"镜像源不存在：{mirror_id}")

    def delete(self, mirror_id: str) -> dict[str, Any]:
        for index, mirror in enumerate(self.mirrors):
            if mirror.get("id") == mirror_id:
                removed = self.mirrors.pop(index)
                self._save()
                return {"id": mirror_id, "deleted": True, "name": removed.get("name", mirror_id)}
        raise KeyError(f"镜像源不存在：{mirror_id}")

    async def test(self, mirror_id: str = "") -> dict[str, Any]:
        if mirror_id:
            mirror = self._require(mirror_id)
            mirrors = [mirror]
        else:
            mirrors = self.enabled()
        if not mirrors:
            raise ValueError("没有启用的镜像源")
        results = []
        for index, mirror in enumerate(mirrors, 1):
            url = self.raw_url(
                DEFAULT_MARKETPLACE_OWNER,
                DEFAULT_MARKETPLACE_REPO,
                DEFAULT_MARKETPLACE_BRANCH,
                DEFAULT_MARKETPLACE_FILE,
                mirror,
            )
            result = await self._fetch(url, mirror, index, len(mirrors), binary=False, max_attempts=1)
            result_data = result.to_dict()
            result_data.pop("data", None)
            results.append(result_data)
        return {"ok": any(item["ok"] for item in results), "results": results}

    async def fetch_raw(
        self,
        owner: str,
        repo: str,
        branch: str,
        file_path: str,
        *,
        mirror_id: str = "",
    ) -> FetchResult:
        mirrors = [self._require(mirror_id)] if mirror_id else self.enabled()
        if not mirrors:
            return FetchResult(ok=False, error="没有启用的镜像源")
        last = FetchResult(ok=False, error="未尝试")
        for index, mirror in enumerate(mirrors, 1):
            url = self.raw_url(owner, repo, branch, file_path, mirror)
            last = await self._fetch(url, mirror, index, len(mirrors), binary=False)
            if last.ok:
                return last
        return FetchResult(
            ok=False,
            error=f"所有镜像源均失败：{last.error}",
            mirror_index=len(mirrors),
            total_mirrors=len(mirrors),
            attempts=sum(1 for _ in mirrors) * self.max_attempts,
        )

    async def fetch_github_url(self, url: str, *, mirror_id: str = "", binary: bool = False) -> FetchResult:
        normalized_url = validate_public_http_url(url)
        mirrors = [self._require(mirror_id)] if mirror_id else self.enabled()
        if not mirrors:
            return await self._fetch(normalized_url, {"id": "direct", "name": "直接访问"}, 1, 1, binary=binary)
        last = FetchResult(ok=False, error="未尝试")
        for index, mirror in enumerate(mirrors, 1):
            mirrored_url = self.mirror_github_url(normalized_url, mirror)
            last = await self._fetch(mirrored_url, mirror, index, len(mirrors), binary=binary)
            if last.ok:
                return last
        return FetchResult(ok=False, error=f"所有镜像源均失败：{last.error}", attempts=len(mirrors) * self.max_attempts)

    async def fetch_github_api(self, api_path: str, *, mirror_id: str = "", official_first: bool = False) -> FetchResult:
        clean_path = "/" + api_path.strip("/")
        mirrors = [self._require(mirror_id)] if mirror_id else self.enabled()
        if official_first and not mirror_id:
            mirrors = sorted(mirrors, key=lambda mirror: 0 if mirror.get("id") == "github" else 1)
        if not mirrors:
            return await self._fetch(f"https://api.github.com{clean_path}", {"id": "direct", "name": "直接访问"}, 1, 1, binary=False)
        last = FetchResult(ok=False, error="未尝试")
        for index, mirror in enumerate(mirrors, 1):
            url = self.github_api_url(clean_path, mirror)
            last = await self._fetch(url, mirror, index, len(mirrors), binary=False)
            if last.ok:
                return last
        return FetchResult(ok=False, error=f"所有镜像源均失败：{last.error}", attempts=len(mirrors) * self.max_attempts)

    def raw_url(self, owner: str, repo: str, branch: str, file_path: str, mirror: dict[str, Any]) -> str:
        raw_prefix = validate_public_http_url(str(mirror.get("raw_prefix", ""))).rstrip("/")
        clean_path = "/".join(part.strip("/") for part in (owner, repo, branch, file_path) if part)
        return f"{raw_prefix}/{clean_path}"

    def github_api_url(self, api_path: str, mirror: dict[str, Any]) -> str:
        clone_prefix = validate_public_http_url(str(mirror.get("clone_prefix", ""))).rstrip("/")
        if clone_prefix == "https://github.com":
            return f"https://api.github.com{api_path}"
        if clone_prefix.endswith("/https://github.com"):
            return clone_prefix[: -len("/https://github.com")] + f"/https://api.github.com{api_path}"
        return f"https://api.github.com{api_path}"

    def mirror_github_url(self, url: str, mirror: dict[str, Any]) -> str:
        parsed = urlparse(validate_public_http_url(url))
        hostname = (parsed.hostname or "").lower()
        path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if hostname == "raw.githubusercontent.com":
            return validate_public_http_url(str(mirror.get("raw_prefix", ""))).rstrip("/") + path_and_query
        if hostname == "github.com":
            return validate_public_http_url(str(mirror.get("clone_prefix", ""))).rstrip("/") + path_and_query
        return url

    def _load(self) -> list[dict[str, Any]]:
        if not self.config_path.exists():
            mirrors = [dict(item) for item in DEFAULT_MIRRORS]
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps({"mirrors": mirrors}, ensure_ascii=False, indent=2), encoding="utf-8")
            return mirrors
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        mirrors = data.get("mirrors") if isinstance(data, dict) else None
        if not isinstance(mirrors, list) or not mirrors:
            return [dict(item) for item in DEFAULT_MIRRORS]
        return [self._normalize(item, default_priority=index + 1) for index, item in enumerate(mirrors)]

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temp.write_text(json.dumps({"mirrors": self.list()}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.config_path)

    def _normalize(self, data: dict[str, Any], *, default_priority: int) -> dict[str, Any]:
        mirror_id = _clean_id(data.get("id"))
        name = str(data.get("name") or mirror_id).strip()
        raw_prefix = validate_public_http_url(str(data.get("raw_prefix") or ""), resolve=False).rstrip("/")
        clone_prefix = validate_public_http_url(str(data.get("clone_prefix") or ""), resolve=False).rstrip("/")
        priority = int(data.get("priority") or default_priority)
        return {
            "id": mirror_id,
            "name": name,
            "raw_prefix": raw_prefix,
            "clone_prefix": clone_prefix,
            "enabled": bool(data.get("enabled", True)),
            "priority": max(1, priority),
        }

    def _next_priority(self) -> int:
        return max((int(mirror.get("priority", 0)) for mirror in self.mirrors), default=0) + 1

    def _require(self, mirror_id: str) -> dict[str, Any]:
        for mirror in self.mirrors:
            if mirror.get("id") == mirror_id:
                return dict(mirror)
        raise KeyError(f"镜像源不存在：{mirror_id}")

    async def _fetch(
        self,
        url: str,
        mirror: dict[str, Any],
        mirror_index: int,
        total_mirrors: int,
        *,
        binary: bool,
        max_attempts: int | None = None,
    ) -> FetchResult:
        started = time.perf_counter()
        attempts = max_attempts or self.max_attempts
        last_error = ""
        last_status = 0
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        headers = {"User-Agent": "DiceFrame plugin marketplace"}
        for attempt in range(1, attempts + 1):
            try:
                async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                    async with session.get(url) as response:
                        last_status = response.status
                        if response.status >= 400:
                            text = await response.text()
                            last_error = f"HTTP {response.status}: {text[:160]}"
                            continue
                        data: str | bytes = await response.read() if binary else await response.text()
                        return FetchResult(
                            ok=True,
                            data=data,
                            url=url,
                            mirror_id=str(mirror.get("id", "")),
                            mirror_name=str(mirror.get("name", "")),
                            mirror_index=mirror_index,
                            total_mirrors=total_mirrors,
                            attempts=attempt,
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            status=response.status,
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                last_error = str(exc)
        return FetchResult(
            ok=False,
            error=last_error or "请求失败",
            url=url,
            mirror_id=str(mirror.get("id", "")),
            mirror_name=str(mirror.get("name", "")),
            mirror_index=mirror_index,
            total_mirrors=total_mirrors,
            attempts=attempts,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            status=last_status,
        )


def github_archive_url(repository_url: str, branch: str) -> str:
    owner, repo = parse_github_repository(repository_url)
    clean_branch = (branch or "main").strip().strip("/")
    if not clean_branch or ".." in clean_branch:
        raise ValueError("分支名称非法")
    return f"https://github.com/{owner}/{repo}/archive/refs/heads/{clean_branch}.zip"


def parse_github_repository(repository_url: str) -> tuple[str, str]:
    parsed = urlparse(validate_public_http_url(repository_url))
    if (parsed.hostname or "").lower() != "github.com":
        raise ValueError("目前只支持 GitHub 插件仓库")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError("GitHub 仓库 URL 必须包含 owner/repo")
    owner = parts[0]
    repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    if not owner or not repo:
        raise ValueError("GitHub 仓库 URL 无效")
    return owner, repo


def validate_public_http_url(url: str, *, resolve: bool = True) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        raise ValueError("URL 不能为空")
    parsed = urlparse(normalized)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL 只允许 http/https")
    if not parsed.hostname or not parsed.netloc:
        raise ValueError("URL 缺少主机名")
    if parsed.username or parsed.password:
        raise ValueError("URL 不允许内嵌认证信息")
    if parsed.fragment:
        raise ValueError("URL 不允许包含片段")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("URL 不允许访问本地主机")
    if resolve:
        try:
            addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"URL 主机无法解析：{hostname}") from exc
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise ValueError(f"URL 不允许访问非公网地址：{ip}")
    return normalized


def _clean_id(value: Any) -> str:
    mirror_id = str(value or "").strip().lower()
    if not mirror_id or not all(ch.isalnum() or ch in "-_" for ch in mirror_id):
        raise ValueError("镜像源 ID 只能包含小写字母、数字、-、_")
    return mirror_id
