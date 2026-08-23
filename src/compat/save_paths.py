"""Stable boundary for legacy save directory naming."""

from __future__ import annotations

from pathlib import Path

from src.engine.persistence import _save_path


def save_path(registry: object, game_key: tuple) -> Path:
    return _save_path(registry, game_key)  # type: ignore[arg-type]


def legacy_save_paths(registry: object, game_key: tuple) -> tuple[Path, ...]:
    parts = [str(item) for item in game_key]
    return tuple(registry.save_dir / separator.join(parts) / "state.json" for separator in (",", "|"))  # type: ignore[attr-defined]
