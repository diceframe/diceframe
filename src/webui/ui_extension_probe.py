"""Internal U00 experiment for declarative extension UI validation.

This is a host-side validator only.  It does not execute third-party
JavaScript and it does not define the final public UI SDK.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
import json


MAX_SURFACES = 8
MAX_NODES = 128
MAX_DEPTH = 12
MAX_PROJECTED_BYTES = 128 * 1024
SLOTS = frozenset({"character-sheet", "gameplay-main", "action-panel", "gm-panel"})
COMPONENTS = frozenset({"group", "text", "value", "progress", "button", "list"})
_BINDING = re.compile(r"^view(?:\.[a-zA-Z][a-zA-Z0-9_-]*)*$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class DeclarativeUiError(ValueError):
    pass


def _string(value: Any, field: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DeclarativeUiError(f"{field} must be a bounded non-empty string")
    return value.strip()


def _walk(node: Any, *, depth: int, count: list[int]) -> dict[str, Any]:
    if depth > MAX_DEPTH or not isinstance(node, dict):
        raise DeclarativeUiError("UI tree exceeds the supported shape")
    count[0] += 1
    if count[0] > MAX_NODES:
        raise DeclarativeUiError("UI tree contains too many nodes")
    kind = _string(node.get("component"), "component", 32)
    if kind not in COMPONENTS:
        raise DeclarativeUiError(f"unknown declarative component: {kind}")
    allowed = {"component", "children", "label", "binding", "action", "arguments", "value", "min", "max"}
    if set(node) - allowed:
        raise DeclarativeUiError("UI node contains unsupported fields")
    normalized: dict[str, Any] = {"component": kind}
    if "label" in node:
        normalized["label"] = _string(node["label"], "label", 200)
    if "binding" in node:
        binding = _string(node["binding"], "binding", 200)
        if not _BINDING.fullmatch(binding):
            raise DeclarativeUiError("UI binding must stay inside the projected view")
        normalized["binding"] = binding
    if "action" in node:
        action = _string(node["action"], "action", 64)
        if not _ACTION.fullmatch(action):
            raise DeclarativeUiError("UI action name is invalid")
        normalized["action"] = action
    if "arguments" in node:
        arguments = node["arguments"]
        if not isinstance(arguments, dict):
            raise DeclarativeUiError("UI action arguments must be an object")
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise DeclarativeUiError("UI action arguments must be JSON") from exc
        if len(encoded) > 8 * 1024:
            raise DeclarativeUiError("UI action arguments are too large")
        normalized["arguments"] = deepcopy(arguments)
    if "value" in node:
        if isinstance(node["value"], (dict, list)):
            raise DeclarativeUiError("UI literal values must be scalar")
        normalized["value"] = node["value"]
    if "children" in node:
        children = node["children"]
        if not isinstance(children, list) or len(children) > MAX_NODES:
            raise DeclarativeUiError("UI children must be a bounded array")
        normalized["children"] = [
            _walk(child, depth=depth + 1, count=count) for child in children
        ]
    return normalized


def normalize_declarative_descriptor(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("ui_sdk") != 1:
        raise DeclarativeUiError("unsupported declarative UI SDK")
    surfaces = raw.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces or len(surfaces) > MAX_SURFACES:
        raise DeclarativeUiError("UI surfaces must be a bounded non-empty array")
    seen: set[str] = set()
    normalized = {"ui_sdk": 1, "surfaces": []}
    for surface in surfaces:
        if not isinstance(surface, dict) or set(surface) - {"slot", "type", "root"}:
            raise DeclarativeUiError("UI surface contains unsupported fields")
        slot = _string(surface.get("slot"), "slot", 64)
        if slot not in SLOTS or slot in seen:
            raise DeclarativeUiError("UI surface slot is unknown or duplicated")
        if surface.get("type") != "declarative":
            raise DeclarativeUiError("U00 only accepts declarative surfaces")
        seen.add(slot)
        normalized["surfaces"].append({
            "slot": slot,
            "type": "declarative",
            "root": _walk(surface.get("root"), depth=0, count=[0]),
        })
    return normalized


def project_binding(projection: dict[str, Any], binding: str) -> Any:
    if not _BINDING.fullmatch(binding):
        raise DeclarativeUiError("invalid projection binding")
    value: Any = projection
    for part in binding.split(".")[1:]:
        if not isinstance(value, dict) or part not in value:
            raise DeclarativeUiError("projection binding is not available")
        value = value[part]
    return deepcopy(value)


def project_declarative_descriptor(
    descriptor: Any,
    projection: dict[str, Any],
) -> dict[str, Any]:
    """Apply a host-owned ViewModel projection to a validated descriptor.

    The descriptor remains declarative: bindings are resolved only against the
    supplied, already-authorized ``view`` object.  A missing binding or an
    oversized result fails closed instead of rendering a partial tree whose
    privacy boundary is ambiguous.  Actions and literal values are preserved;
    dispatch still belongs to the owning action service.
    """

    normalized = normalize_declarative_descriptor(descriptor)
    if not isinstance(projection, dict):
        raise DeclarativeUiError("projected view must be an object")

    def walk(node: dict[str, Any]) -> dict[str, Any]:
        result = {key: deepcopy(value) for key, value in node.items() if key != "children"}
        binding = node.get("binding")
        if binding is not None:
            result["projected_value"] = project_binding(projection, binding)
        if "children" in node:
            result["children"] = [walk(child) for child in node["children"]]
        return result

    projected = {
        "ui_sdk": 1,
        "surfaces": [
            {"slot": surface["slot"], "type": surface["type"], "root": walk(surface["root"])}
            for surface in normalized["surfaces"]
        ],
    }
    try:
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DeclarativeUiError("projected view contains non-JSON data") from exc
    if len(encoded) > MAX_PROJECTED_BYTES:
        raise DeclarativeUiError("projected view is too large")
    return projected
