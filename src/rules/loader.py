"""Unified V1/V2 rule bundle loading entry point."""

from __future__ import annotations

import json
import copy
from pathlib import Path
from typing import Any

from src.compat.rules_v1 import load_v1_template
from src.content.rule_locale import materialize_rule
from src.engine.currency import is_v2_currency_system, validate_currency_system


class RuleBundleLoader:
    """Load a V1 JSON rule or a V2 bundle without changing RuleSystem behavior."""

    def load(self, path: str | Path) -> dict[str, Any]:
        source = Path(path)
        with source.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("rule bundle must be a JSON object")
        if raw.get("locale_schema_version") and raw.get("target"):
            target = raw.get("target") or {}
            core = source.parents[2] / f"{target.get('id')}.json"
            return materialize_rule(self.load(core), raw)
        if int(raw.get("rule_schema_version", raw.get("content_schema_version", 1)) or 1) >= 2:
            if not str(raw.get("rule_id") or raw.get("id") or "").strip():
                raise ValueError("V2 rule bundle requires rule_id")
            template = self._resolve_v2(source, raw)
        else:
            template = load_v1_template(source)
        # 显式 V2 货币声明在加载边界 fail-fast：坏 schema 的规则/插件不允许进入运行时。
        if is_v2_currency_system(template.get("currency_system")):
            validate_currency_system(template.get("currency_system"))
        return template

    def _resolve_v2(self, source: Path, raw: dict[str, Any], seen: set[Path] | None = None) -> dict[str, Any]:
        parent = raw.get("extends")
        if not parent:
            return raw
        seen = seen or set()
        source = source.resolve()
        if source in seen:
            raise ValueError("V2 rule inheritance cycle")
        seen.add(source)
        parent_path = Path(str(parent))
        if not parent_path.suffix:
            parent_path = parent_path.with_suffix(".json")
        if not parent_path.is_absolute():
            parent_path = source.parent / parent_path
        if not parent_path.exists():
            builtin = source.parents[2] / parent_path.name
            parent_path = builtin if builtin.exists() else parent_path
        parent_raw = json.loads(parent_path.read_text(encoding="utf-8"))
        parent_resolved = self._resolve_v2(parent_path, parent_raw, seen)
        return self._merge_core(parent_resolved, raw)

    @classmethod
    def _merge_core(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(result.get(key), dict) and isinstance(value, dict):
                result[key] = cls._merge_core(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    def load_rule(self, rules_dir: str | Path, rule_id: str, locale: str = "") -> dict[str, Any]:
        root = Path(rules_dir)
        core = root / f"{rule_id}.json"
        requested = str(locale or "").replace("_", "-")
        if not requested and core.exists():
            try:
                requested = str(json.loads(core.read_text(encoding="utf-8")).get("default_locale") or "")
            except (OSError, ValueError):
                requested = ""
        if requested:
            exact = root / "locales" / requested / f"{rule_id}.json"
            if exact.exists():
                return self.load(exact)
            base = requested.split("-", 1)[0]
            fallback = root / "locales" / base / f"{rule_id}.json"
            if fallback.exists():
                return self.load(fallback)
        return self.load(core)

    def load_system(self, path: str | Path, locale: str = "") -> Any:
        from src.rules.rule_system import RuleSystem
        source = Path(path)
        if locale and source.parent.name == "rules":
            return RuleSystem(self.load_rule(source.parent, source.stem, locale))
        return RuleSystem(self.load(path))
