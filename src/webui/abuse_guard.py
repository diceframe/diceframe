"""Small in-memory guard against request floods and expensive AI concurrency."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiohttp import web


LOGIN_PER_IP_LIMIT = 10
LOGIN_PER_IP_WINDOW_SECONDS = 10 * 60
LOGIN_GLOBAL_LIMIT = 60
LOGIN_GLOBAL_WINDOW_SECONDS = 60
WRITE_PER_IP_LIMIT = 60
WRITE_PER_IP_WINDOW_SECONDS = 60
WRITE_GLOBAL_LIMIT = 600
WRITE_GLOBAL_WINDOW_SECONDS = 60
AI_CONCURRENCY_LIMIT = 3
AI_SLOT_WAIT_SECONDS = 2.0
MAX_TRACKED_BUCKETS = 2000

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_AI_EXACT_PATHS = frozenset({
    "/api/generate-world",
    "/api/generate-rule",
    "/api/generate-character",
    "/api/generate-text",
    "/api/test-connection",
    "/api/test-embedding",
})
_AI_GAME_SUFFIXES = (
    "/action",
    "/advance",
    "/stream-action",
    "/reset",
    "/restart",
    "/switch-world",
)


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    retry_after: int = 0


class SlidingWindowLimiter:
    """Bounded sliding-window counters; restarting the app resets all counters."""

    def __init__(
        self,
        *,
        max_buckets: int = MAX_TRACKED_BUCKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_buckets = max(1, max_buckets)
        self._clock = clock
        self._buckets: dict[tuple[str, str], deque[float]] = {}
        self._last_seen: dict[tuple[str, str], float] = {}
        self._last_cleanup = 0.0

    def check(self, scope: str, identity: str, limit: int, window_seconds: int) -> LimitDecision:
        now = self._clock()
        self._cleanup(now, max(window_seconds, LOGIN_PER_IP_WINDOW_SECONDS))
        key = (scope, identity)
        bucket = self._buckets.setdefault(key, deque())
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        self._last_seen[key] = now
        if len(bucket) >= limit:
            retry_after = max(1, math.ceil(window_seconds - (now - bucket[0])))
            return LimitDecision(False, retry_after)
        bucket.append(now)
        self._evict_if_needed(key)
        return LimitDecision(True)

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)

    def _cleanup(self, now: float, retention_seconds: int) -> None:
        if now - self._last_cleanup < 60:
            return
        self._last_cleanup = now
        stale_before = now - retention_seconds
        for key, last_seen in list(self._last_seen.items()):
            if last_seen <= stale_before:
                self._last_seen.pop(key, None)
                self._buckets.pop(key, None)

    def _evict_if_needed(self, current_key: tuple[str, str]) -> None:
        while len(self._buckets) > self.max_buckets:
            candidates = (
                (key, seen)
                for key, seen in self._last_seen.items()
                if key != current_key
            )
            victim = min(candidates, key=lambda item: item[1], default=None)
            if victim is None:
                break
            self._last_seen.pop(victim[0], None)
            self._buckets.pop(victim[0], None)


class AbuseGuard:
    def __init__(
        self,
        *,
        login_per_ip_limit: int = LOGIN_PER_IP_LIMIT,
        login_per_ip_window: int = LOGIN_PER_IP_WINDOW_SECONDS,
        login_global_limit: int = LOGIN_GLOBAL_LIMIT,
        login_global_window: int = LOGIN_GLOBAL_WINDOW_SECONDS,
        write_per_ip_limit: int = WRITE_PER_IP_LIMIT,
        write_per_ip_window: int = WRITE_PER_IP_WINDOW_SECONDS,
        write_global_limit: int = WRITE_GLOBAL_LIMIT,
        write_global_window: int = WRITE_GLOBAL_WINDOW_SECONDS,
        ai_concurrency: int = AI_CONCURRENCY_LIMIT,
        ai_wait_seconds: float = AI_SLOT_WAIT_SECONDS,
        limiter: SlidingWindowLimiter | None = None,
    ) -> None:
        self.login_per_ip_limit = login_per_ip_limit
        self.login_per_ip_window = login_per_ip_window
        self.login_global_limit = login_global_limit
        self.login_global_window = login_global_window
        self.write_per_ip_limit = write_per_ip_limit
        self.write_per_ip_window = write_per_ip_window
        self.write_global_limit = write_global_limit
        self.write_global_window = write_global_window
        self.ai_wait_seconds = ai_wait_seconds
        self._limiter = limiter or SlidingWindowLimiter()
        self._ai_slots = asyncio.Semaphore(max(1, ai_concurrency))

    async def handle(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        ip = (request.remote or "unknown")[:128]

        if request.method == "POST" and request.path == "/api/login":
            denied = self._check_pair(
                "login-ip",
                ip,
                self.login_per_ip_limit,
                self.login_per_ip_window,
                "login-global",
                self.login_global_limit,
                self.login_global_window,
            )
            if denied:
                return _rate_limited_response(denied)
        elif request.method in _WRITE_METHODS and request.path.startswith("/api/"):
            denied = self._check_pair(
                "write-ip",
                ip,
                self.write_per_ip_limit,
                self.write_per_ip_window,
                "write-global",
                self.write_global_limit,
                self.write_global_window,
            )
            if denied:
                return _rate_limited_response(denied)

        if not _is_ai_request(request):
            return await handler(request)

        try:
            await asyncio.wait_for(self._ai_slots.acquire(), timeout=self.ai_wait_seconds)
        except TimeoutError:
            return _rate_limited_response(max(1, math.ceil(self.ai_wait_seconds)))
        try:
            return await handler(request)
        finally:
            self._ai_slots.release()

    def _check_pair(
        self,
        ip_scope: str,
        ip: str,
        ip_limit: int,
        ip_window: int,
        global_scope: str,
        global_limit: int,
        global_window: int,
    ) -> int:
        per_ip = self._limiter.check(ip_scope, ip, ip_limit, ip_window)
        if not per_ip.allowed:
            return per_ip.retry_after
        global_decision = self._limiter.check(global_scope, "*", global_limit, global_window)
        return 0 if global_decision.allowed else global_decision.retry_after


def _is_ai_request(request: web.Request) -> bool:
    if request.path in _AI_EXACT_PATHS:
        return request.method == "POST"
    if not request.path.startswith("/api/games/"):
        return False
    if request.method == "PUT" and "/swipe/" in request.path:
        return True
    return request.method == "POST" and request.path.endswith(_AI_GAME_SUFFIXES)


def _rate_limited_response(retry_after: int) -> web.Response:
    retry_after = max(1, retry_after)
    return web.json_response(
        {
            "ok": False,
            "error": (
                "操作太频繁，为避免连续请求影响游戏，系统暂时拦截了本次操作。"
                f"请等待约 {retry_after} 秒后再试。"
            ),
            "retry_after": retry_after,
        },
        status=429,
        headers={
            "Retry-After": str(retry_after),
            "Cache-Control": "no-store",
        },
    )


ABUSE_GUARD_KEY = web.AppKey("abuse_guard", AbuseGuard)


@web.middleware
async def abuse_guard_middleware(request: web.Request, handler):
    guard = request.app.get(ABUSE_GUARD_KEY)
    if guard is None:
        return await handler(request)
    return await guard.handle(request, handler)
