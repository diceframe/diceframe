"""Session 中间件 —— UUID token + cookie，轻量身份系统。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("trpg")


class SessionManager:
    """管理 player session：cookie → user_id 映射。"""

    def __init__(self, data_dir: Path):
        self._path = data_dir / "sessions.json"
        self._sessions: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._sessions = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                self._sessions = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self._sessions, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        tmp_path.replace(self._path)

    def get_or_create(self, token: str | None) -> tuple[str, str]:
        """返回 (session_token, user_id)。"""
        if token and token in self._sessions:
            return token, self._sessions[token]["user_id"]

        token = token or uuid.uuid4().hex
        user_id = f"web_{token[:8]}"
        self._sessions[token] = {
            "user_id": user_id,
            "name": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        return token, user_id

    def set_name(self, token: str, name: str) -> None:
        if token in self._sessions:
            self._sessions[token]["name"] = name
            self._save()

    def get_name(self, token: str) -> str:
        return self._sessions.get(token, {}).get("name", "")

    def rebind(self, token: str, user_id: str) -> None:
        """把当前 session token 绑定到指定 user_id（换设备恢复身份用）。"""
        if token in self._sessions:
            self._sessions[token]["user_id"] = user_id
            self._save()


@web.middleware
async def session_middleware(request: web.Request, handler) -> web.StreamResponse:
    """aiohttp 中间件：解析 session token，注入 user_id。"""
    mgr: SessionManager = request.app.get("session_manager")
    if not mgr:
        return await handler(request)

    token = request.cookies.get("trpg_session")
    new_token, user_id = mgr.get_or_create(token)
    request["user_id"] = user_id
    request["session_token"] = new_token

    response = await handler(request)
    if new_token != token:
        response.set_cookie("trpg_session", new_token, httponly=True,
                           path="/", max_age=7 * 86400, samesite="Lax")
    return response
