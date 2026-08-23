"""Typed locale overlays; mechanics cannot be changed by translations."""

from __future__ import annotations

from typing import Any

_LINGUISTIC = frozenset({"aliases", "keywords", "skill_candidates", "prompt", "prompt_prose"})
_DISPLAY = frozenset({"name", "title", "description", "label", "hint", "flavor"})
_ALLOWED = _LINGUISTIC | _DISPLAY


class LocaleOverlayError(ValueError):
    pass


def _base_locale(locale: str) -> str:
    return str(locale or "").replace("_", "-").split("-", 1)[0].lower()


def resolve_locale(locales: dict[str, dict[str, Any]], locale: str, default_locale: str = "zh-CN") -> dict[str, Any]:
    candidates = [str(locale), _base_locale(locale), str(default_locale), _base_locale(default_locale)]
    for candidate in candidates:
        value = locales.get(candidate)
        if isinstance(value, dict):
            return value
    return {}


def apply_locale_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(overlay, dict):
        raise LocaleOverlayError("locale overlay must be an object")
    forbidden = set(overlay) - _ALLOWED
    if forbidden:
        raise LocaleOverlayError(f"locale overlay contains mechanics fields: {sorted(forbidden)}")
    result = dict(base)
    result.update(overlay)
    return result
