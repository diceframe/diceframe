"""Unified V1/V2 rule bundle loading entry point."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.compat.rules_v1 import load_v1_template


class RuleBundleLoader:
    """Load a V1 JSON rule or a V2 bundle without changing RuleSystem behavior."""

    def load(self, path: str | Path) -> dict[str, Any]:
        source = Path(path)
        with source.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("rule bundle must be a JSON object")
        if int(raw.get("content_schema_version", 1) or 1) >= 2:
            if not str(raw.get("rule_id") or raw.get("id") or "").strip():
                raise ValueError("V2 rule bundle requires rule_id")
            return raw
        return load_v1_template(source)
