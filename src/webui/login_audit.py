"""Small, bounded audit log for owner login attempts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web


logger = logging.getLogger("trpg")


class LoginAuditStore:
    """Persist only the newest login attempts without storing credentials."""

    def __init__(self, data_dir: Path, max_entries: int = 100) -> None:
        self.path = data_dir / "login_audit.json"
        self.max_entries = max(1, max_entries)
        self._entries = self._load()

    def record(self, ip: str, success: bool) -> None:
        self._entries.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "ip": ip or "unknown",
            "success": bool(success),
        })
        self._entries = self._entries[-self.max_entries:]
        self._save()

    def recent(self, limit: int = 50) -> list[dict]:
        safe_limit = min(max(1, limit), self.max_entries)
        return [dict(entry) for entry in reversed(self._entries[-safe_limit:])]

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with self.path.open(encoding="utf-8") as file:
                payload = json.load(file)
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            if not isinstance(entries, list):
                raise ValueError("entries is not a list")
            valid = [
                {
                    "at": str(entry["at"]),
                    "ip": str(entry["ip"]),
                    "success": bool(entry["success"]),
                }
                for entry in entries
                if isinstance(entry, dict)
                and "at" in entry
                and "ip" in entry
                and isinstance(entry.get("success"), bool)
            ]
            return valid[-self.max_entries:]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError):
            logger.warning("登录记录文件无法读取，将从空记录继续", exc_info=True)
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump({"entries": self._entries}, file, ensure_ascii=False, indent=2)
        temporary.replace(self.path)


LOGIN_AUDIT_KEY = web.AppKey("login_audit", LoginAuditStore)
