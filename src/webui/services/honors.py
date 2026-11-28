# -*- coding: utf-8 -*-
"""Honors / commemorative badge verification, local persistence, and wall proxy.

This module is strictly decoupled from gameplay mechanics and GameInstance
persistence. Credentials are verified offline with an Ed25519 public key and
stored in ``<host_data_dir>/honors.json``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger("trpg")

TOKEN_PREFIX = "DFB1"
_BADGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_HOLDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,39}$")
_MAX_BADGES_PER_TOKEN = 64
_MAX_NAME_LENGTH = 64
_WALL_CACHE_TTL_SECONDS = 300.0
_REMOTE_TIMEOUT_SECONDS = 5.0

# Public Ed25519 key (32-byte raw key encoded as base64url without padding).
# Can be overridden via DICEFRAME_HONORS_PUBLIC_KEY for testing or custom deployments.
DEFAULT_HONORS_PUBLIC_KEY = "FmA0z2-et40rt-zlQtCTAXdV9I0rzTX33feCLNREfvk"
DEFAULT_REDEEM_URL = "https://diceframe.com/redeem"
DEFAULT_HONORS_API_BASE = "https://diceframe.com/badge"


class HonorsTokenError(ValueError):
    """Raised when an honors credential token fails structural or signature checks."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty base64url segment")
    padding = "=" * (-len(cleaned) % 4)
    return base64.urlsafe_b64decode(cleaned + padding)


def load_ed25519_public_key(encoded: str) -> Ed25519PublicKey:
    """Decode a 32-byte raw base64url Ed25519 public key."""
    try:
        raw = _b64url_decode(encoded)
    except Exception as exc:
        raise ValueError("invalid honors public key encoding") from exc
    if len(raw) != 32:
        raise ValueError("honors public key must be 32 raw bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def sign_honors_token(
    payload: dict[str, Any],
    private_key: Ed25519PrivateKey | bytes,
) -> str:
    """Sign a canonical DFB1 honors credential token (for issuers and tests)."""
    key = (
        private_key
        if isinstance(private_key, Ed25519PrivateKey)
        else Ed25519PrivateKey.from_private_bytes(private_key)
    )
    normalized = _validate_payload_dict(payload)
    payload_bytes = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload_segment = _b64url_encode(payload_bytes)
    signing_input = f"{TOKEN_PREFIX}.{payload_segment}".encode("ascii")
    signature = key.sign(signing_input)
    return f"{TOKEN_PREFIX}.{payload_segment}.{_b64url_encode(signature)}"


def _validate_payload_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HonorsTokenError("invalid_payload", "Credential payload must be a JSON object.")

    holder_id = str(raw.get("id") or "").strip()
    if not _HOLDER_ID_RE.match(holder_id):
        raise HonorsTokenError("invalid_holder_id", "Credential holder ID is missing or invalid.")

    name = str(raw.get("name") or "").strip()
    if not name or len(name) > _MAX_NAME_LENGTH:
        raise HonorsTokenError("invalid_name", "Credential display name is missing or too long.")

    raw_badges = raw.get("badges")
    if not isinstance(raw_badges, list) or not raw_badges:
        raise HonorsTokenError("invalid_badges", "Credential must include at least one badge ID.")

    badges: list[str] = []
    seen: set[str] = set()
    for item in raw_badges:
        if not isinstance(item, str):
            raise HonorsTokenError("invalid_badges", "Badge ID must be a string.")
        bid = item.strip()
        if not _BADGE_ID_RE.match(bid):
            raise HonorsTokenError("invalid_badges", f"Invalid badge ID: {bid!r}")
        if bid not in seen:
            seen.add(bid)
            badges.append(bid)
        if len(badges) > _MAX_BADGES_PER_TOKEN:
            raise HonorsTokenError("invalid_badges", "Too many badges in credential.")

    iat_raw = raw.get("iat")
    if not isinstance(iat_raw, int) or isinstance(iat_raw, bool) or iat_raw <= 0:
        raise HonorsTokenError("invalid_iat", "Credential issue timestamp is invalid.")

    normalized: dict[str, Any] = {
        "id": holder_id,
        "name": name,
        "badges": badges,
        "iat": iat_raw,
    }
    note = raw.get("note")
    if isinstance(note, str) and note.strip():
        normalized["note"] = note.strip()[:120]
    return normalized


def verify_honors_token(
    token: str,
    public_key: Ed25519PublicKey,
) -> dict[str, Any]:
    """Verify a DFB1 offline honors token and return its validated payload."""
    if not isinstance(token, str):
        raise HonorsTokenError("invalid_format", "Credential token must be a string.")
    cleaned = token.strip()
    parts = cleaned.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise HonorsTokenError(
            "invalid_format",
            "Invalid credential format. Expected a DFB1 credential string.",
        )

    _, payload_segment, sig_segment = parts
    try:
        payload_bytes = _b64url_decode(payload_segment)
        sig_bytes = _b64url_decode(sig_segment)
    except Exception as exc:
        raise HonorsTokenError("invalid_encoding", "Credential contains malformed base64url data.") from exc

    if len(sig_bytes) != 64:
        raise HonorsTokenError("invalid_signature", "Credential signature length is invalid.")

    signing_input = f"{TOKEN_PREFIX}.{payload_segment}".encode("ascii")
    try:
        public_key.verify(sig_bytes, signing_input)
    except InvalidSignature as exc:
        raise HonorsTokenError("invalid_signature", "Credential signature verification failed.") from exc

    try:
        decoded = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise HonorsTokenError("invalid_payload", "Credential payload is not valid UTF-8 JSON.") from exc

    return _validate_payload_dict(decoded)


def builtin_badge_catalog() -> list[dict[str, Any]]:
    """Offline fallback catalog of commemorative DiceFrame honors."""
    return [
        {
            "id": "pioneer",
            "category": "milestone",
            "shape": "shield",
            "wall_mode": "listed",
            "theme": "starlight",
            "craft": {"zh-CN": "星尘深蓝珐琅 × 拉丝香槟金", "en": "Starlight Enamel × Brushed Gold"},
            "name": {"zh-CN": "先驱者", "en": "Pioneer"},
            "subtitle": {"zh-CN": "早期启航限量纪念", "en": "Early Voyage Edition"},
            "description": {
                "zh-CN": "授予早期参与公测、反馈关键体验并陪伴 DiceFrame 启航的限量冒险者。",
                "en": "Awarded to early adventurers who tested milestones and helped launch DiceFrame.",
            },
            "obtain_hint": {
                "zh-CN": "参与早期公测、大版本启航活动或官方限量纪念抽奖获得。",
                "en": "Earned via early testing, major launch events, or limited community giveaways.",
            },
        },
        {
            "id": "world_weaver",
            "category": "creation",
            "shape": "coin",
            "wall_mode": "listed",
            "theme": "celestial",
            "craft": {"zh-CN": "星穹深蓝釉环 × 浮雕古金", "en": "Celestial Ring × Relief Gold"},
            "name": {"zh-CN": "世界编织者", "en": "World Weaver"},
            "subtitle": {"zh-CN": "模组与世界观创作奖", "en": "World & Adventure Award"},
            "description": {
                "zh-CN": "授予模组与世界观创作活动获奖者，以及优质世界书与设定集作者。",
                "en": "Awarded to adventure contest winners and creators of memorable worlds and lorebooks.",
            },
            "obtain_hint": {
                "zh-CN": "参与模组/世界观创作征集活动，或分享优质开源世界书与设定集获得。",
                "en": "Earned by winning world/adventure contests or publishing featured lorebooks.",
            },
        },
        {
            "id": "chronicler",
            "category": "creation",
            "shape": "wax-seal",
            "wall_mode": "listed",
            "theme": "crimson",
            "craft": {"zh-CN": "绯红火漆浮雕 × 烫金印纹", "en": "Crimson Wax Seal × Gilded Crest"},
            "name": {"zh-CN": "吟游诗人", "en": "Chronicler"},
            "subtitle": {"zh-CN": "精彩战报与二创精选", "en": "Featured Campaign Chronicle"},
            "description": {
                "zh-CN": "授予分享精彩跑团战报、创意二创与深度攻略的社区记录者。",
                "en": "Awarded to storytellers sharing campaign chronicles, guides, and creative works.",
            },
            "obtain_hint": {
                "zh-CN": "在社区分享精彩跑团战报、深度教程或精选二创作品获得。",
                "en": "Earned by sharing featured campaign chronicles, guides, or fan creations.",
            },
        },
        {
            "id": "arcane_forger",
            "category": "community",
            "shape": "hexagon",
            "wall_mode": "listed",
            "theme": "jade",
            "craft": {"zh-CN": "秘林翡翠珐琅 × 雅致铂香槟", "en": "Jade Enamel × Platinum Champagne"},
            "name": {"zh-CN": "工坊匠人", "en": "Arcane Forger"},
            "subtitle": {"zh-CN": "代码、翻译与插件共建", "en": "Code, Translation & Plugins"},
            "description": {
                "zh-CN": "授予为 DiceFrame 贡献代码、多语言翻译、社区插件或关键缺陷定位的共建者。",
                "en": "Awarded to contributors of code, translations, plugins, and critical bug reports.",
            },
            "obtain_hint": {
                "zh-CN": "向仓库贡献代码 PR、维护多语言翻译、开发社区插件或定位关键缺陷获得。",
                "en": "Earned through merged PRs, translations, community plugins, or critical bug reports.",
            },
        },
        {
            "id": "voyage_mark",
            "category": "milestone",
            "shape": "compass",
            "wall_mode": "counter",
            "theme": "bronze",
            "craft": {"zh-CN": "古铜星辰罗盘 × 哑光精钢", "en": "Antique Bronze × Brushed Steel"},
            "name": {"zh-CN": "启航印记", "en": "Voyage Mark"},
            "subtitle": {"zh-CN": "全服活动纪念章", "en": "Community Milestone Mark"},
            "description": {
                "zh-CN": "全服活动纪念印记：个人点亮专属编号并收藏于陈列柜，社区荣誉墙汇总展示点亮总人数。",
                "en": "Community-wide milestone mark with a personal serial number; counted in aggregate on the wall.",
            },
            "obtain_hint": {
                "zh-CN": "参与官方大版本发布或全服庆典纪念活动即可领取。",
                "en": "Claimable during major release celebrations and community milestone events.",
            },
        },
    ]


@dataclass
class _CachedWall:
    fetched_at: float
    payload: dict[str, Any]


class HonorsService:
    """Owns offline credential verification, local persistence, and optional wall fetches."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        public_key_b64: str | None = None,
        api_base: str | None = None,
        redeem_url: str | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._store_path = self._data_dir / "honors.json"
        key_text = (
            public_key_b64
            if public_key_b64 is not None
            else os.environ.get("DICEFRAME_HONORS_PUBLIC_KEY", "").strip()
            or DEFAULT_HONORS_PUBLIC_KEY
        )
        self._public_key = load_ed25519_public_key(key_text)
        base_text = (
            api_base
            if api_base is not None
            else os.environ.get("DICEFRAME_HONORS_API_BASE", DEFAULT_HONORS_API_BASE)
        )
        self._api_base = base_text.strip().rstrip("/")
        redeem_text = (
            redeem_url
            if redeem_url is not None
            else os.environ.get("DICEFRAME_HONORS_REDEEM_URL", DEFAULT_REDEEM_URL)
        )
        self._redeem_url = redeem_text.strip() or DEFAULT_REDEEM_URL
        self._wall_cache: dict[str, _CachedWall] = {}

    def _read_raw_store(self) -> dict[str, Any]:
        if not self._store_path.exists():
            return {}
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.warning("Honors store is unreadable; treating as empty.")
            return {}

    def _write_raw_store(self, data: dict[str, Any]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self._store_path)

    def _active_credential(self) -> tuple[dict[str, Any] | None, str | None]:
        stored = self._read_raw_store()
        token = stored.get("token")
        if not isinstance(token, str) or not token.strip():
            return None, None
        try:
            payload = verify_honors_token(token, self._public_key)
        except HonorsTokenError as exc:
            logger.warning("Stored honors credential failed verification: %s", exc.code)
            return None, exc.code

        featured = str(stored.get("featured_badge_id") or "").strip()
        if featured not in payload["badges"]:
            featured = payload["badges"][0]
        activated_at = stored.get("activated_at")
        return {
            "id": payload["id"],
            "name": payload["name"],
            "badges": payload["badges"],
            "iat": payload["iat"],
            **({"note": payload["note"]} if "note" in payload else {}),
            "featured_badge_id": featured,
            "activated_at": activated_at if isinstance(activated_at, int) else payload["iat"],
        }, None

    def get_state(self) -> dict[str, Any]:
        active, warning = self._active_credential()
        result: dict[str, Any] = {
            "ok": True,
            "catalog": builtin_badge_catalog(),
            "active": active,
            "remote_configured": bool(self._api_base),
            "redeem_url": self._redeem_url,
        }
        if warning:
            result["warning"] = warning
        return result

    def activate_token(self, token: str) -> dict[str, Any]:
        payload = verify_honors_token(token, self._public_key)
        existing = self._read_raw_store()
        previous_featured = str(existing.get("featured_badge_id") or "").strip()
        featured = (
            previous_featured
            if previous_featured in payload["badges"]
            else payload["badges"][0]
        )
        self._write_raw_store(
            {
                "version": 1,
                "token": token.strip(),
                "featured_badge_id": featured,
                "activated_at": int(time.time()),
            }
        )
        return self.get_state()

    def set_featured_badge(self, badge_id: str) -> dict[str, Any]:
        active, _ = self._active_credential()
        if active is None:
            raise HonorsTokenError("not_activated", "No verified honors credential is active.")
        cleaned = str(badge_id or "").strip()
        if cleaned and cleaned not in active["badges"]:
            raise HonorsTokenError("badge_not_owned", "Selected badge is not in the active credential.")
        stored = self._read_raw_store()
        stored["featured_badge_id"] = cleaned or active["badges"][0]
        self._write_raw_store(stored)
        return self.get_state()

    def clear_credential(self) -> dict[str, Any]:
        try:
            self._store_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove honors.json during clear.")
        return self.get_state()

    def _offline_wall_response(
        self,
        *,
        page: int,
        limit: int,
        badge: str,
        query: str,
        reason: str,
    ) -> dict[str, Any]:
        active, _ = self._active_credential()
        items: list[dict[str, Any]] = []
        counters: dict[str, int] = {}
        catalog = builtin_badge_catalog()
        mode_by_id = {entry["id"]: entry.get("wall_mode", "listed") for entry in catalog}

        if active:
            listed_badges = [b for b in active["badges"] if mode_by_id.get(b, "listed") == "listed"]
            for bid in active["badges"]:
                if mode_by_id.get(bid) == "counter":
                    counters[bid] = counters.get(bid, 0) + 1
            if listed_badges:
                matches_badge = not badge or badge in listed_badges
                q = query.lower()
                matches_query = (
                    not q
                    or q in active["name"].lower()
                    or q in active["id"].lower()
                )
                if matches_badge and matches_query:
                    items.append(
                        {
                            "id": active["id"],
                            "name": active["name"],
                            "badges": listed_badges,
                            "featured_badge_id": active["featured_badge_id"],
                            "is_self": True,
                        }
                    )

        return {
            "ok": True,
            "source": "offline",
            "reason": reason,
            "page": page,
            "limit": limit,
            "total": len(items),
            "items": items,
            "counters": counters,
            "catalog": catalog,
            "revoked": [],
        }

    async def fetch_wall(
        self,
        *,
        page: int = 1,
        limit: int = 60,
        badge: str = "",
        query: str = "",
        refresh: bool = False,
    ) -> dict[str, Any]:
        safe_page = max(1, min(page, 500))
        safe_limit = max(1, min(limit, 100))
        safe_badge = badge.strip()[:48]
        safe_query = query.strip()[:64]

        if not self._api_base:
            return self._offline_wall_response(
                page=safe_page,
                limit=safe_limit,
                badge=safe_badge,
                query=safe_query,
                reason="remote_not_configured",
            )

        cache_key = f"{safe_page}:{safe_limit}:{safe_badge}:{safe_query}"
        now = time.monotonic()
        cached = self._wall_cache.get(cache_key)
        if not refresh and cached and (now - cached.fetched_at) < _WALL_CACHE_TTL_SECONDS:
            return cached.payload

        params: dict[str, str] = {
            "page": str(safe_page),
            "limit": str(safe_limit),
        }
        if safe_badge:
            params["badge"] = safe_badge
        if safe_query:
            params["q"] = safe_query

        url = f"{self._api_base}/api/honors"
        try:
            timeout = aiohttp.ClientTimeout(total=_REMOTE_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        raise RuntimeError(f"honors wall HTTP {response.status}")
                    raw = await response.json()
        except Exception as exc:
            logger.info("Honors wall remote fetch unavailable (%s); falling back offline.", exc)
            return self._offline_wall_response(
                page=safe_page,
                limit=safe_limit,
                badge=safe_badge,
                query=safe_query,
                reason="remote_unavailable",
            )

        if not isinstance(raw, dict):
            return self._offline_wall_response(
                page=safe_page,
                limit=safe_limit,
                badge=safe_badge,
                query=safe_query,
                reason="remote_invalid",
            )

        merged_catalog = _merge_catalog(builtin_badge_catalog(), raw.get("catalog"))
        revoked = [str(item) for item in raw.get("revoked", []) if isinstance(item, str)]
        result = {
            "ok": True,
            "source": "remote",
            "page": int(raw.get("page") or safe_page),
            "limit": int(raw.get("limit") or safe_limit),
            "total": int(raw.get("total") or 0),
            "items": raw.get("items") if isinstance(raw.get("items"), list) else [],
            "counters": raw.get("counters") if isinstance(raw.get("counters"), dict) else {},
            "catalog": merged_catalog,
            "revoked": revoked,
        }
        self._wall_cache[cache_key] = _CachedWall(fetched_at=now, payload=result)
        return result


def _merge_catalog(
    builtin: list[dict[str, Any]],
    remote: Any,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {entry["id"]: dict(entry) for entry in builtin}
    order: list[str] = [entry["id"] for entry in builtin]
    if isinstance(remote, list):
        for entry in remote:
            if not isinstance(entry, dict):
                continue
            bid = str(entry.get("id") or "").strip()
            if not _BADGE_ID_RE.match(bid):
                continue
            if bid not in merged:
                order.append(bid)
            merged[bid] = {**merged.get(bid, {}), **entry, "id": bid}
    return [merged[bid] for bid in order]
