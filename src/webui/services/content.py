"""Public DiceFrame content fetcher with bounded online-first caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import aiohttp

GITHUB_CONTENT_BASE_URL = "https://raw.githubusercontent.com/diceframe/diceframe-content/main/content"
DEFAULT_BASE_URL = GITHUB_CONTENT_BASE_URL
_SUCCESS_TTL = 600
_FAILURE_TTL = 60
_TOTAL_TIMEOUT = 3
_PER_SOURCE_TIMEOUT = 1.5
_MAX_BYTES = 512 * 1024
_SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

_CACHE: dict[str, dict[str, Any]] = {}
_FAILURE_UNTIL: dict[str, float] = {}
_INFLIGHT: dict[str, asyncio.Task[str]] = {}


def base_urls() -> tuple[str, ...]:
    primary = os.getenv("DICEFRAME_CONTENT_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    fallback = GITHUB_CONTENT_BASE_URL.rstrip("/")
    urls: list[str] = []
    for url in (primary, fallback):
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def _validate_path(path: str) -> str:
    value = (path or "").strip().lstrip("/")
    if not _SAFE_PATH.fullmatch(value) or ".." in value.split("/"):
        raise ValueError("invalid public content path")
    return value


def _cache_file(cache_dir: Path | None, path: str) -> Path | None:
    if cache_dir is None:
        return None
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.cache"


def _read_disk_cache(cache_dir: Path | None, path: str) -> str:
    target = _cache_file(cache_dir, path)
    if target is None:
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_disk_cache(cache_dir: Path | None, path: str, text: str) -> None:
    target = _cache_file(cache_dir, path)
    if target is None:
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError:
        return


def disk_cache_age_seconds(cache_dir: Path | None, path: str) -> float | None:
    """公共内容磁盘缓存文件的年龄（秒）；无缓存或不可读时返回 None。

    供公告等服务判断返回正文是否来自缓存，而不是刚同步的在线内容。
    """
    target = _cache_file(cache_dir, path)
    if target is None:
        return None
    try:
        return time.time() - target.stat().st_mtime
    except OSError:
        return None


async def fetch_text(
    cache_dir: Path | None,
    path: str,
    *,
    force_refresh: bool = False,
    allow_cached: bool = True,
    mirror_manager: Any | None = None,
) -> str:
    """Fetch a public content path, with optional cache bypass for legal originals."""
    key = _validate_path(path)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if allow_cached and not force_refresh and cached and now - float(cached["fetched_at"]) < _SUCCESS_TTL:
        return str(cached["text"])
    if allow_cached and not force_refresh and now < _FAILURE_UNTIL.get(key, 0):
        return str(cached["text"]) if cached else _read_disk_cache(cache_dir, key)

    task = _INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            _fetch_remote(key, mirror_manager=mirror_manager)
            if mirror_manager is not None
            else _fetch_remote(key)
        )
        _INFLIGHT[key] = task

        def _cleanup(done: asyncio.Task[str]) -> None:
            if _INFLIGHT.get(key) is done:
                _INFLIGHT.pop(key, None)

        task.add_done_callback(_cleanup)

    try:
        text = await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except Exception:
        text = ""

    now = time.monotonic()
    if text:
        _CACHE[key] = {"text": text, "fetched_at": now}
        _FAILURE_UNTIL.pop(key, None)
        _write_disk_cache(cache_dir, key, text)
        return text

    _FAILURE_UNTIL[key] = now + _FAILURE_TTL
    if allow_cached:
        return str(cached["text"]) if cached else _read_disk_cache(cache_dir, key)
    return ""


async def fetch_json(
    cache_dir: Path | None,
    path: str,
    *,
    force_refresh: bool = False,
    allow_cached: bool = True,
    mirror_manager: Any | None = None,
) -> dict[str, Any] | None:
    text = await fetch_text(
        cache_dir,
        path,
        force_refresh=force_refresh,
        allow_cached=allow_cached,
        mirror_manager=mirror_manager,
    )
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


async def _fetch_remote(path: str, *, mirror_manager: Any | None = None) -> str:
    if mirror_manager is not None:
        primary = os.getenv("DICEFRAME_CONTENT_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
        if primary == DEFAULT_BASE_URL:
            try:
                result = await mirror_manager.fetch_github_url(
                    f"{DEFAULT_BASE_URL}/{path}",
                    binary=False,
                    max_bytes=_MAX_BYTES,
                    resolve=False,
                )
                if result.ok and isinstance(result.data, str) and result.data.strip():
                    return result.data
            except Exception:
                # Mirror configuration is an availability aid; retain the
                # existing direct-source and cache fallback when it fails.
                pass
    timeout = aiohttp.ClientTimeout(total=_PER_SOURCE_TIMEOUT)
    headers = {"User-Agent": "DiceFrame content"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for base in base_urls():
            text = await _fetch_one(session, f"{base}/{path}")
            if text:
                return text
    return ""


async def _fetch_one(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return ""
            if response.content_length is not None and response.content_length > _MAX_BYTES:
                return ""
            chunks = bytearray()
            async for chunk in response.content.iter_chunked(16 * 1024):
                chunks.extend(chunk)
                if len(chunks) > _MAX_BYTES:
                    return ""
            text = bytes(chunks).decode("utf-8")
            return text if text.strip() else ""
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError):
        return ""


class PublicContentService:
    """Bounded public-content fetcher rooted at one optional disk cache."""

    def __init__(self, cache_dir: Path | None = None, *, mirror_manager: Any | None = None) -> None:
        self._cache_dir = cache_dir
        self._mirror_manager = mirror_manager

    async def fetch_text(
        self,
        path: str,
        *,
        force_refresh: bool = False,
        allow_cached: bool = True,
    ) -> str:
        return await fetch_text(
            self._cache_dir,
            path,
            force_refresh=force_refresh,
            allow_cached=allow_cached,
            mirror_manager=self._mirror_manager,
        )

    async def fetch_json(
        self,
        path: str,
        *,
        force_refresh: bool = False,
        allow_cached: bool = True,
    ) -> dict[str, Any] | None:
        return await fetch_json(
            self._cache_dir,
            path,
            force_refresh=force_refresh,
            allow_cached=allow_cached,
            mirror_manager=self._mirror_manager,
        )

    def disk_cache_age(self, path: str) -> float | None:
        return disk_cache_age_seconds(self._cache_dir, path)

    def cache_file(self, path: str) -> Path | None:
        return _cache_file(self._cache_dir, path)
