"""Locale-backed non-mechanical messages used by the intent/economy boundary."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from src.engine.intent.lexicon import intent_languages

_RESOURCE_DIR = Path(__file__).parent / "locales"


@lru_cache(maxsize=None)
def _load_messages(language: str) -> dict[str, str]:
    path = _RESOURCE_DIR / language / "messages.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8")) or {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(value, str) and value
    } if isinstance(data, dict) else {}


def locale_message(language: str, key: str, fallback: str = "") -> str:
    for candidate in intent_languages(language):
        value = _load_messages(candidate).get(str(key))
        if value:
            return value
    return fallback
