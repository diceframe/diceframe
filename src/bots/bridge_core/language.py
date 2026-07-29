"""Language helpers shared by Bot Bridge services and adapters."""

from __future__ import annotations

import re

from src.engine.language import DEFAULT_LANGUAGE, is_english, normalize_language


def bridge_language(value: object, fallback: str = DEFAULT_LANGUAGE) -> str:
    return normalize_language(str(value or fallback))


def bridge_is_english(value: object) -> bool:
    return is_english(bridge_language(value))


def bridge_text(language: object, zh: str, en: str, **values: object) -> str:
    template = en if bridge_is_english(language) else zh
    return template.format(**values)


def infer_command_language(text: object, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Infer only from explicit English command words, never from free-form actions."""
    value = re.sub(r"\s+", " ", str(text or "").strip().lower())
    first = value.split(" ", 1)[0] if value else ""
    if first in {
        "help", "bind", "unbind", "join", "invite", "status", "recap", "summary",
        "map", "roll", "advance", "next", "pay", "confirm", "reject", "rejectpay",
        "sense", "log", "away", "return", "back", "ping", "character", "create",
    }:
        return "en"
    return bridge_language(fallback)
