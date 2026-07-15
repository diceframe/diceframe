"""OneBot message segment parsing helpers for the QQ adapter."""

from __future__ import annotations

import re
from typing import Any


def mentions_self(segments: list[dict[str, Any]], self_id: str) -> bool:
    return bool(self_id) and any(
        segment.get("type") == "at" and str((segment.get("data") or {}).get("qq")) == self_id
        for segment in segments
    )


def message_text(segments: list[dict[str, Any]]) -> str:
    return "".join(
        str((segment.get("data") or {}).get("text") or "")
        for segment in segments
        if segment.get("type") == "text"
    )


def invite_target_user_ids(
    segments: list[dict[str, Any]],
    self_id: str,
    platform_user_id: str,
    text: str,
) -> list[str]:
    targets: list[str] = []
    for segment in segments:
        if segment.get("type") != "at":
            continue
        qq = str((segment.get("data") or {}).get("qq") or "").strip()
        if not qq or qq == str(self_id):
            continue
        if qq not in targets:
            targets.append(qq)
    normalized = re.sub(r"\s+", "", text.strip().lower())
    if normalized in {"邀请我", "私聊邀请我", "发我邀请", "invite me", "inviteme"}:
        actor = str(platform_user_id or "")
        if actor and actor not in targets:
            targets.append(actor)
    return targets
