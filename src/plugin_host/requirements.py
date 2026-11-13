"""Versioned, minimal plugin requirement parsing.

The host supports the established ``requires.rulesets`` shape and a bounded
hard ``requires.plugins`` edge.  This is an install/activation preflight, not
a general package solver: it never downloads, upgrades, or guesses optional
features.
"""

from __future__ import annotations

from typing import Any, Mapping
import re


_PLUGIN_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_ruleset_requirements(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate and normalize the manifest's hard ruleset requirement list."""

    requires = manifest.get("requires")
    if requires is None:
        return []
    if not isinstance(requires, dict):
        raise ValueError("requires 必须是对象")
    rulesets = requires.get("rulesets")
    if rulesets is None:
        return []
    if not isinstance(rulesets, list):
        raise ValueError("requires.rulesets 必须是数组")
    if len(rulesets) > 32:
        raise ValueError("requires.rulesets 不能超过 32 项")
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in rulesets:
        if not isinstance(entry, dict):
            raise ValueError("requires.rulesets entry 必须是对象")
        extra = sorted(set(entry) - {"id", "minimum_version"})
        if extra:
            raise ValueError(f"requires.rulesets entry has unknown field: {extra[0]!r}")
        runtime_id = str(entry.get("id") or "").strip()
        if not runtime_id:
            raise ValueError("requires.rulesets entry id is required")
        if len(runtime_id) > 160:
            raise ValueError("requires.rulesets entry id is too long")
        minimum = entry.get("minimum_version", 1)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ValueError("requires.rulesets minimum_version must be a positive integer")
        if runtime_id in seen:
            raise ValueError(f"requires.rulesets contains duplicate id: {runtime_id}")
        seen.add(runtime_id)
        parsed.append({"id": runtime_id, "minimum_version": minimum})
    return parsed


def parse_plugin_requirements(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """Validate hard package dependencies declared by one plugin."""

    requires = manifest.get("requires")
    if requires is None:
        return []
    if not isinstance(requires, dict):
        raise ValueError("requires 必须是对象")
    declared = requires.get("plugins")
    if declared is None:
        return []
    if not isinstance(declared, list) or len(declared) > 32:
        raise ValueError("requires.plugins 必须是最多 32 项的数组")
    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in declared:
        if not isinstance(entry, dict) or set(entry) - {"id", "minimum_version"}:
            raise ValueError("requires.plugins entry fields are invalid")
        plugin_id = str(entry.get("id") or "").strip()
        if not _PLUGIN_ID_RE.fullmatch(plugin_id):
            raise ValueError("requires.plugins entry id is invalid")
        minimum = str(entry.get("minimum_version") or "").strip()
        if not minimum or len(minimum) > 64:
            raise ValueError("requires.plugins minimum_version is invalid")
        if plugin_id in seen:
            raise ValueError(f"requires.plugins contains duplicate id: {plugin_id}")
        seen.add(plugin_id)
        parsed.append({"id": plugin_id, "minimum_version": minimum})
    return parsed


def version_tuple(value: Any) -> tuple[int, ...]:
    """Parse the bounded numeric part of a plugin version for preflight."""

    text = str(value or "").strip().lstrip("vV")
    if not text:
        raise ValueError("plugin version is empty")
    raw_parts = text.split(".")
    if not 1 <= len(raw_parts) <= 4:
        raise ValueError("plugin version is invalid")
    parts: list[int] = []
    for raw in raw_parts:
        number = raw.split("-", 1)[0]
        if not number.isdigit():
            raise ValueError("plugin version is not numeric")
        parts.append(int(number))
    if not parts:
        raise ValueError("plugin version is invalid")
    return tuple(parts)


def plugin_version_satisfies(minimum: str, current: str) -> bool:
    try:
        left, right = version_tuple(minimum), version_tuple(current)
    except ValueError:
        return False
    width = max(len(left), len(right))
    return right + (0,) * (width - len(right)) >= left + (0,) * (width - len(left))


def dependency_rows(
    manifests: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build a bounded, deterministic dependency projection for installed packages.

    This is intentionally a *plan*, not a solver: it never downloads, selects an
    alternative version, or mutates package state.  Callers can therefore show the
    exact blockers before a destructive update or uninstall.
    """

    rows: list[dict[str, Any]] = []
    for plugin_id in sorted(manifests):
        manifest = manifests[plugin_id]
        try:
            requirements = parse_plugin_requirements(manifest)
            parse_error = ""
        except ValueError as exc:
            requirements, parse_error = [], str(exc)
        dependencies: list[dict[str, Any]] = []
        blockers: list[str] = []
        if parse_error:
            blockers.append(parse_error)
        for requirement in requirements:
            dependency_id = requirement["id"]
            dependency = manifests.get(dependency_id)
            available = dependency is not None
            satisfied = bool(
                available
                and plugin_version_satisfies(
                    requirement["minimum_version"],
                    str(dependency.get("version") or ""),
                )
            )
            item = {
                "id": dependency_id,
                "minimum_version": requirement["minimum_version"],
                "available": available,
                "satisfied": satisfied,
            }
            if available:
                item["version"] = str(dependency.get("version") or "")
            dependencies.append(item)
            if not available:
                blockers.append(f"missing plugin dependency: {dependency_id}")
            elif not satisfied:
                blockers.append(f"plugin dependency version mismatch: {dependency_id}")
        rows.append({
            "id": plugin_id,
            "version": str(manifest.get("version") or ""),
            "dependencies": dependencies,
            "blockers": blockers,
        })
    return rows
