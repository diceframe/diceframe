"""Platform-neutral async client for DiceFrame bot-facing HTTP routes."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

import aiohttp

from src.bots.bridge_core.errors import DiceFrameHTTPError


class DiceFrameClient:
    def __init__(self, base_url: str, bot_token: str, timeout_sec: float = 120) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.bot_token = str(bot_token or "").strip()
        self.timeout_sec = max(1.0, float(timeout_sec or 120))
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def bind_game(self, game_key: str, bind_token: str) -> dict[str, Any]:
        return await self._request("POST", "/api/bot/bind-game", json={"game_key": game_key, "bind_token": bind_token})

    async def list_games(self) -> dict[str, Any]:
        return await self._request("GET", "/api/games")

    async def detail(self, game_key: str, actor: str = "") -> dict[str, Any]:
        return await self._request("GET", f"/api/games/{quote(game_key, safe='')}", actor=actor)

    async def game_detail(self, game_key: str, actor: str = "") -> dict[str, Any]:
        return await self.detail(game_key, actor)

    async def characters(self, game_key: str, actor: str = "") -> dict[str, Any]:
        return await self._request("GET", f"/api/games/{quote(game_key, safe='')}/characters", actor=actor)

    async def action(
        self,
        game_key: str,
        actor: str,
        text: str,
        *,
        confirm: bool = False,
        source: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "confirm": confirm}
        if confirm:
            body["server_roll"] = True
        if source:
            body["source"] = source
        return await self._request(
            "POST",
            f"/api/games/{quote(game_key, safe='')}/action",
            actor=actor,
            json=body,
        )

    async def advance(self, game_key: str, actor: str, *, force: bool = True) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/games/{quote(game_key, safe='')}/advance",
            actor=actor,
            json={"force": bool(force)},
        )

    async def update_character(self, game_key: str, actor: str, updates: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/api/games/{quote(game_key, safe='')}/character/{quote(actor, safe='')}",
            actor=actor,
            json=updates,
        )

    async def generate_character(self, prompt: str, *, game_key: str = "", rule_id: str = "") -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/generate-character",
            json={"prompt": prompt, "game_key": game_key, "rule_id": rule_id},
        )

    async def private_log(self, game_key: str, actor: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/games/{quote(game_key, safe='')}/private-log", actor=actor)

    async def resolve_payment(self, game_key: str, actor: str, payment_id: str, accepted: bool) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/games/{quote(game_key, safe='')}/payments/{quote(payment_id, safe='')}",
            actor=actor,
            json={"accepted": bool(accepted)},
        )

    async def set_player_away(self, game_key: str, actor: str, user_id: str, *, away: bool) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/games/{quote(game_key, safe='')}/players/{quote(user_id, safe='')}/away",
            actor=actor,
            json={"away": bool(away)},
        )

    async def set_away(self, game_key: str, actor: str, user_id: str, away: bool) -> dict[str, Any]:
        return await self.set_player_away(game_key, actor, user_id, away=away)

    async def map(self, game_key: str, actor: str = "") -> dict[str, Any]:
        return await self._request("GET", f"/api/games/{quote(game_key, safe='')}/map", actor=actor)

    async def public_config(self) -> dict[str, Any]:
        return await self._request("GET", "/api/config", auth=False)

    async def build_join_link(self, game_key: str, user: str = "") -> str:
        try:
            config = await self.public_config()
            base = str(config.get("public_base_url") or "").strip() or self.base_url
        except Exception:
            base = self.base_url
        return build_join_link(base, game_key, user)

    async def _request(self, method: str, path: str, *, actor: str = "", auth: bool = True, **kwargs: Any) -> dict[str, Any]:
        if not self.base_url:
            raise DiceFrameHTTPError("未配置 DiceFrame 服务地址")
        if auth and not self.bot_token:
            raise DiceFrameHTTPError("未配置 DiceFrame Bot Token")
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
            self._session = aiohttp.ClientSession(timeout=timeout)

        headers = dict(kwargs.pop("headers", {}) or {})
        if auth:
            headers["X-Bot-Token"] = self.bot_token
        if actor:
            headers["X-Bot-Actor"] = actor

        async with self._session.request(method, self.base_url + path, headers=headers, **kwargs) as response:
            try:
                data = await response.json(content_type=None)
            except Exception as exc:
                text = await response.text()
                raise DiceFrameHTTPError(f"DiceFrame 返回了非 JSON 响应：HTTP {response.status} {text[:120]}") from exc
            if response.status >= 400:
                error = data.get("error") or data.get("message") or f"HTTP {response.status}"
                raise DiceFrameHTTPError(str(error))
            if isinstance(data, dict) and data.get("ok") is False:
                raise DiceFrameHTTPError(str(data.get("error") or data.get("narration") or "DiceFrame 请求失败"))
            return data if isinstance(data, dict) else {"data": data}


def build_join_link(base_url: str, game_key: str, user: str = "") -> str:
    parsed = urlparse(str(base_url or "").strip())
    if not parsed.scheme:
        parsed = urlparse("http://" + str(base_url or "").strip())
    path = (parsed.path or "").rstrip("/") + "/"
    query = urlencode({"game": game_key, "share": "1", **({"user": user} if user else {})})
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", f"/join?{query}"))
