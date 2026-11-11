"""Small in-memory guard against request floods and expensive AI concurrency."""

from __future__ import annotations

import asyncio
import ipaddress
import math
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiohttp import web

from src.webui.services._common import canonical_game_key
from src.webui.services.room_password import is_room_password_rejected
from src.localization.messages import t


LOGIN_PER_IP_LIMIT = 10
LOGIN_PER_IP_WINDOW_SECONDS = 10 * 60
LOGIN_GLOBAL_LIMIT = 60
LOGIN_GLOBAL_WINDOW_SECONDS = 60
WRITE_PER_IP_LIMIT = 60
WRITE_PER_IP_WINDOW_SECONDS = 60
WRITE_GLOBAL_LIMIT = 600
WRITE_GLOBAL_WINDOW_SECONDS = 60
# 房间密码猜测：每个「IP + 规范化后的游戏」一个令牌桶。容量 5（前 5 次错误
# 不受影响），之后每 30 秒回补 1 次机会——是减速而不是封锁：
# - 每次尝试在进入处理器之前就先扣一个令牌（并发请求也会被计入），只有处理器
#   明确标记「密码错误」才算消耗，其它结果（成功、别的错误）原样退还；
# - 等待时间只由令牌缺口决定，被 429 拒掉的请求不扣令牌，也就不会把下一次机会
#   往后推；任何人最多等一个回补周期（30 秒），不存在长时间锁死；
# - 只按单局计，不设跨局的 IP 总上限：同一 IP 在某一局的失败绝不影响它在
#   其它局的尝试，每一局被猜测的速度都单独受限。
# 共用出口 IP（隧道/代理/NAT）后面的正常玩家与捣乱者共享同一个桶，只能保证
# 「等待有上限、不被锁死」，无法在同一 IP 内区分人：捣乱者可以和正常玩家抢
# 每一个回补的令牌。按会话再分一层桶是可能的后续改进。IPv6 按 /64 计（见
# client_identity）。身份按 request.remote
# （直连对端地址）计；不信任 X-Forwarded-For，因为本项目没有「可信反向代理」配置。
# 与 owner 登录、通用写入分桶，互不挤占。
ROOM_PASSWORD_BURST = 5
ROOM_PASSWORD_REFILL_SECONDS = 30
# The body is read before taking the per-IP+game lock, and the lock wait is
# bounded, so a client that stalls its upload cannot hold the lock.
ROOM_PASSWORD_BODY_TIMEOUT_SECONDS = 10.0
ROOM_PASSWORD_LOCK_TIMEOUT_SECONDS = 10.0
# 建卡器的 choices/validate/derive/finalize 是无状态计算（POST 只为携带草稿），
# 单独计额：引导建卡每改一步都会请求，不能挤占同 IP 其它玩家的写额度。
BUILDER_PER_IP_LIMIT = 300
BUILDER_PER_IP_WINDOW_SECONDS = 60
BUILDER_GLOBAL_LIMIT = 3000
BUILDER_GLOBAL_WINDOW_SECONDS = 60
AI_CONCURRENCY_LIMIT = 3
AI_SLOT_WAIT_SECONDS = 2.0
MAX_TRACKED_BUCKETS = 2000

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 凭据兑换端点与登录共用限流桶：两者都能用于暴力猜测 owner 访问权。
_LOGIN_PATHS = frozenset({"/api/login", "/api/pairing/claim"})
_ROOM_PASSWORD_PATH = re.compile(r"^/api/games/([^/]+)/verify-room-password/?$")
_BUILDER_PATH = re.compile(r"^/api/rules/[^/]+/builder/(?:choices|validate|derive|finalize)$")
_AI_EXACT_PATHS = frozenset({
    "/api/generate-world",
    "/api/generate-rule",
    "/api/generate-character",
    "/api/generate-text",
    "/api/image-prompts/optimize",
    "/api/test-connection",
    "/api/test-embedding",
    "/api/assistant/chat",
    "/api/assistant/agent/run",
    "/api/assistant/agent/confirm",
    "/api/assistant/speech",
    "/api/assistant/transcription",
    "/api/tts/test",
})
_AI_GAME_SUFFIXES = (
    "/action",
    "/kp-question",
    "/advance",
    "/luck",
    "/stream-action",
    "/story-recap",
    "/reset",
    "/restart",
    "/switch-world",
    "/speech",
    # 分镜分析走的是主文本模型，和上面几条一样要占 AI 槽位。
    # 生图接口（/current-round、POST .../generated-images）故意不进这里：
    # 单次可长达 imagegen_timeout_seconds，占满槽位会把玩家行动一起饿死。
    "/storyboard/analyze",
)


def client_identity(remote: str | None) -> str:
    """The rate-limit identity of a peer address.

    Global IPv6 clients usually control a whole /64 and can rotate addresses
    inside it, so every bucket keys global IPv6 by its /64 prefix. Private,
    link-local and loopback IPv6 (a table on the host's own LAN) stay per
    address, like IPv4 (IPv4-mapped IPv6 counts as the IPv4 address).
    Unparseable values are used as they are.
    """
    raw = str(remote or "").strip()
    if not raw:
        return "unknown"
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return raw[:128]
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        if not address.is_global:
            return str(address)
        return str(ipaddress.IPv6Network(f"{address}/64", strict=False))
    return str(address)


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

    def now(self) -> float:
        return self._clock()

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
        room_password_burst: int = ROOM_PASSWORD_BURST,
        room_password_refill_seconds: float = ROOM_PASSWORD_REFILL_SECONDS,
        room_password_body_timeout: float = ROOM_PASSWORD_BODY_TIMEOUT_SECONDS,
        room_password_lock_timeout: float = ROOM_PASSWORD_LOCK_TIMEOUT_SECONDS,
        builder_per_ip_limit: int = BUILDER_PER_IP_LIMIT,
        builder_per_ip_window: int = BUILDER_PER_IP_WINDOW_SECONDS,
        builder_global_limit: int = BUILDER_GLOBAL_LIMIT,
        builder_global_window: int = BUILDER_GLOBAL_WINDOW_SECONDS,
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
        self.room_password_burst = max(1, room_password_burst)
        self.room_password_refill_seconds = max(1.0, float(room_password_refill_seconds))
        self.room_password_body_timeout = room_password_body_timeout
        self.room_password_lock_timeout = room_password_lock_timeout
        # (ip, game) -> (tokens, last refill time); bounded like the limiter.
        self._room_password_buckets: dict[tuple[str, str], tuple[float, float]] = {}
        # (ip, game) -> (lock, requests using it); dropped when the last one leaves.
        self._room_password_locks: dict[tuple[str, str], tuple[asyncio.Lock, int]] = {}
        self.builder_per_ip_limit = builder_per_ip_limit
        self.builder_per_ip_window = builder_per_ip_window
        self.builder_global_limit = builder_global_limit
        self.builder_global_window = builder_global_window
        self.ai_wait_seconds = ai_wait_seconds
        self._limiter = limiter or SlidingWindowLimiter()
        self._ai_slots = asyncio.Semaphore(max(1, ai_concurrency))

    async def handle(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        ip = client_identity(request.remote)

        if request.method == "POST" and request.path in _LOGIN_PATHS:
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
        elif request.method == "POST" and (room_match := _ROOM_PASSWORD_PATH.match(request.path)):
            key = (ip, canonical_game_key(request.match_info.get("game_key") or room_match.group(1))[:256])
            return await self._guard_room_password(key, request, handler)
        elif request.method == "POST" and _BUILDER_PATH.match(request.path):
            denied = self._check_pair(
                "builder-ip",
                ip,
                self.builder_per_ip_limit,
                self.builder_per_ip_window,
                "builder-global",
                self.builder_global_limit,
                self.builder_global_window,
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
        except asyncio.TimeoutError:
            return _rate_limited_response(max(1, math.ceil(self.ai_wait_seconds)))
        try:
            return await handler(request)
        finally:
            self._ai_slots.release()

    def _room_password_tokens(self, key: tuple[str, str], now: float) -> float:
        tokens, updated = self._room_password_buckets.get(key, (float(self.room_password_burst), now))
        refilled = (now - updated) / self.room_password_refill_seconds
        return min(float(self.room_password_burst), tokens + max(0.0, refilled))

    async def _guard_room_password(
        self,
        key: tuple[str, str],
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Run attempts for one IP + game one at a time.

        Checking the bucket and recording the outcome happen under the same
        lock, so parallel requests cannot all pass the check before any
        failure is recorded. Correct passwords never take a token, so a
        table joining at once from one address is only serialised, not refused.
        """
        # Read the whole body before queueing for the lock (the handler's
        # request.json() reuses it): a stalled upload must time out on its
        # own, never while holding the lock. No bucket is touched here.
        try:
            await asyncio.wait_for(request.read(), timeout=self.room_password_body_timeout)
        except asyncio.TimeoutError:
            return web.json_response(
                {"ok": False, "error": t("webui_abuse_guard.body_read_timeout")}, status=408, headers={"Cache-Control": "no-store"},
            )
        lock, users = self._room_password_locks.get(key, (None, 0))
        if lock is None:
            lock = asyncio.Lock()
        self._room_password_locks[key] = (lock, users + 1)
        try:
            try:
                # Lock.acquire() never holds the lock after being cancelled,
                # so a timeout here cannot leak it.
                await asyncio.wait_for(lock.acquire(), timeout=self.room_password_lock_timeout)
            except asyncio.TimeoutError:
                return _rate_limited_response(math.ceil(self.room_password_lock_timeout))
            try:
                wait = self._room_password_wait(key)
                if wait:
                    return _rate_limited_response(wait)
                response = await handler(request)
                if is_room_password_rejected(response):
                    self._consume_room_password_token(key)
                return response
            finally:
                lock.release()
        finally:
            lock, users = self._room_password_locks[key]
            if users <= 1:
                self._room_password_locks.pop(key, None)
            else:
                self._room_password_locks[key] = (lock, users - 1)

    def _room_password_wait(self, key: tuple[str, str]) -> int:
        """Seconds until the next attempt is allowed, from the token deficit only.

        Refused (429) attempts take nothing, so they never push the next slot back.
        """
        tokens = self._room_password_tokens(key, self._limiter.now())
        if tokens >= 1:
            return 0
        return max(1, math.ceil((1 - tokens) * self.room_password_refill_seconds))

    def _consume_room_password_token(self, key: tuple[str, str]) -> None:
        now = self._limiter.now()
        tokens = self._room_password_tokens(key, now)
        self._room_password_buckets[key] = (max(0.0, tokens - 1), now)
        self._evict_room_password_buckets(key)

    def _evict_room_password_buckets(self, current: tuple[str, str]) -> None:
        if len(self._room_password_buckets) <= MAX_TRACKED_BUCKETS:
            return
        # Full buckets carry no state; drop them first, then the least recent.
        now = self._limiter.now()
        for key in [k for k in self._room_password_buckets if k != current]:
            if self._room_password_tokens(key, now) >= self.room_password_burst:
                self._room_password_buckets.pop(key, None)
        while len(self._room_password_buckets) > MAX_TRACKED_BUCKETS:
            victim = min(
                (k for k in self._room_password_buckets if k != current),
                key=lambda k: self._room_password_buckets[k][1],
                default=None,
            )
            if victim is None:
                break
            self._room_password_buckets.pop(victim, None)

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
                t("webui_abuse_guard.rate_limited", retry_after=retry_after)
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
