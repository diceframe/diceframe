"""Validation for the bounded argument schemas used by built-in tools."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any


def validate_arguments(value: Any, schema: dict, path: str = "arguments") -> Any:
    """Validate the bounded JSON-schema subset used by this tool registry."""
    if not schema:
        # An empty JSON schema delegates shape validation to the owning
        # command contract, e.g. ConfigStore's typed configuration validator.
        return deepcopy(value)
    kind = schema.get("type")
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "boolean": isinstance(value, bool),
               "integer": isinstance(value, int) and not isinstance(value, bool),
               "number": isinstance(value, (int, float)) and not isinstance(value, bool)}
    if kind not in matches or not matches[kind]:
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid choice")
    if kind == "object":
        props = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            raise ValueError(f"{path}: missing required field")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(f"{path}: unknown field")
        for key, item in value.items():
            if key in props:
                validate_arguments(item, props[key], f"{path}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 32):
            raise ValueError(f"{path}: invalid array size")
        for index, item in enumerate(value):
            validate_arguments(item, schema["items"], f"{path}[{index}]")
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 8192):
            raise ValueError(f"{path}: invalid text length")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{path}: invalid text")
    elif kind in {"integer", "number"}:
        if not schema.get("minimum", -1e12) <= value <= schema.get("maximum", 1e12) or not math.isfinite(value):
            raise ValueError(f"{path}: out of range")
    return deepcopy(value)
