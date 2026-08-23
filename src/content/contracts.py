"""Stable identity and resource contracts for Content V2."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")


def canonical_id(value: str) -> str:
    result = str(value or "").strip().lower().replace(" ", "_")
    if not _ID.fullmatch(result):
        raise ValueError(f"invalid canonical id: {value!r}")
    return result


@dataclass(frozen=True)
class ResourceRef:
    owner: str
    kind: str
    local_id: str

    def __post_init__(self) -> None:
        if not str(self.owner).strip() or not str(self.kind).strip():
            raise ValueError("resource owner and kind are required")
        object.__setattr__(self, "owner", str(self.owner).strip())
        object.__setattr__(self, "kind", canonical_id(self.kind))
        object.__setattr__(self, "local_id", canonical_id(self.local_id))

    def __str__(self) -> str:
        return f"{self.owner}:{self.kind}:{self.local_id}"

    @classmethod
    def parse(cls, value: str) -> "ResourceRef":
        parts = str(value or "").split(":")
        if len(parts) < 3:
            raise ValueError("resource reference must be owner:kind:local_id")
        return cls(":".join(parts[:-2]), parts[-2], parts[-1])


@dataclass(frozen=True)
class ContentResource:
    ref: ResourceRef
    data: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.data, dict):
            raise TypeError("content resource data must be an object")
