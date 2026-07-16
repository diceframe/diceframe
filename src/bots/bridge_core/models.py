"""Shared bridge input/output models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BridgeInput:
    stream_id: str
    platform_user_id: str
    text: str
    mentioned_bot: bool = False
    platform: str = ""
    raw_message: Any = None


@dataclass
class BridgeResult:
    handled: bool
    intercept: bool = False
    replies: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
