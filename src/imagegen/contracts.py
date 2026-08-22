"""Stable contracts for DiceFrame image generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


IMAGE_PURPOSES = frozenset({"scene", "avatar", "item", "map", "freeform"})


def game_image_owner_id(game_key: Any) -> str:
    if isinstance(game_key, (list, tuple)):
        return ":".join(str(part) for part in game_key)
    return str(game_key or "")


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    purpose: str = "freeform"
    owner_type: str = "system"
    owner_id: str = ""
    aspect_ratio: str = ""
    style: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageGenerationResult:
    generation_id: str
    asset_id: str
    purpose: str
    prompt: str
    revised_prompt: str
    provider: str
    model: str
    created_at: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "asset_id": self.asset_id,
            "purpose": self.purpose,
            "prompt": self.prompt,
            "revised_prompt": self.revised_prompt,
            "provider": self.provider,
            "model": self.model,
            "created_at": self.created_at,
        }
