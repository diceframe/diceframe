"""Deterministic mechanics snapshots for locale-invariance checks."""

from __future__ import annotations

import json
from typing import Any

_DISPLAY_FIELDS = frozenset({"name", "title", "description", "label", "hint", "flavor", "aliases", "keywords", "prompt", "prompt_prose"})


def mechanics_snapshot(value: dict[str, Any]) -> str:
    mechanics = {key: item for key, item in value.items() if key not in _DISPLAY_FIELDS}
    return json.dumps(mechanics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
