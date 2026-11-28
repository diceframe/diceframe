"""FVTT Compendium pack source parsing (workplan §6.17 m3-01, §7.3).

Format knowledge only. This module reads a compendium pack *source file*
(JSON array, JSONL/NDJSON, or YAML), identifies each document's type and
reports what it found. It never opens a LevelDB pack directory (v11+ install
artifact, ``DEFERRED``), never downloads assets and never executes anything —
scripts and macros are only *flagged*.

The same boundary rule as ``scene_adapter``: this module knows FVTT document
shapes, not any ruleset. ``system`` payloads are carried as opaque data; they
are never interpreted.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

CODE_COMPENDIUM_MALFORMED = "foundry_compendium_malformed"
CODE_COMPENDIUM_FORMAT_UNSUPPORTED = "foundry_compendium_format_unsupported"

#: Same ceiling as the single-scene import (§8.1); the sync route is stricter.
MAX_COMPENDIUM_BYTES = 16 * 1024 * 1024
#: Matches the per-Book entry ceiling; one source file never needs more.
MAX_COMPENDIUM_RECORDS = 2000

DOCUMENT_TYPES = (
    "Actor", "Item", "JournalEntry", "Scene", "RollTable", "Playlist", "Macro",
)

_FORMAT_BY_SUFFIX = {
    ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".yaml": "yaml", ".yml": "yaml",
}


class FoundryCompendiumError(ValueError):
    """A compendium source cannot be parsed; ``code`` is the frozen error code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class FoundryRecord:
    """One document from a pack source, typed and left otherwise untouched."""

    id: str
    document_type: str
    name: str
    raw: dict[str, Any]
    contains_script: bool = False


@dataclass(frozen=True, slots=True)
class CompendiumSource:
    """Parsed pack source: records in file order plus parse-level facts."""

    records: tuple[FoundryRecord, ...]
    source_format: str
    pack_name: str = ""


def _malformed(detail: str) -> Any:
    raise FoundryCompendiumError(CODE_COMPENDIUM_MALFORMED, detail)


def _format_unsupported(detail: str) -> Any:
    raise FoundryCompendiumError(CODE_COMPENDIUM_FORMAT_UNSUPPORTED, detail)


def detect_source_format(text: str, *, filename: str = "") -> str:
    """The declared format wins; otherwise JSON, then JSONL, then YAML."""

    suffix = PurePosixPath(str(filename or "").replace("\\", "/")).suffix.lower()
    if suffix in _FORMAT_BY_SUFFIX:
        return _FORMAT_BY_SUFFIX[suffix]
    stripped = text.lstrip("\ufeff \t\r\n")
    if not stripped:
        _malformed("the compendium source is empty")
    if stripped.startswith(("[", "{")):
        try:
            json.loads(stripped)
        except ValueError:
            pass
        else:
            return "json"
    if _looks_like_jsonl(text):
        return "jsonl"
    return "yaml"


def _looks_like_jsonl(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        try:
            if not isinstance(json.loads(line), dict):
                return False
        except ValueError:
            return False
    return True


def _decode(payload: bytes | str) -> str:
    if isinstance(payload, str):
        text = payload
    else:
        raw = bytes(payload or b"")
        if len(raw) > MAX_COMPENDIUM_BYTES:
            _malformed(f"the compendium source exceeds {MAX_COMPENDIUM_BYTES} bytes")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # A LevelDB pack directory never reaches this function as text.
            _malformed("the compendium source is not UTF-8 text (LevelDB packs are not read)")
    if len(text.encode("utf-8")) > MAX_COMPENDIUM_BYTES:
        _malformed(f"the compendium source exceeds {MAX_COMPENDIUM_BYTES} bytes")
    return text


def _json_rows(text: str) -> tuple[list[Any], dict[str, Any] | None]:
    try:
        value = json.loads(text)
    except ValueError as exc:
        _malformed(f"the compendium JSON is invalid: {exc}")
    return _rows_from_value(value)


def _yaml_rows(text: str) -> tuple[list[Any], dict[str, Any] | None]:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _malformed(f"the compendium YAML is invalid: {exc}")
    if value is None:
        _malformed("the compendium source is empty")
    return _rows_from_value(value)


def _jsonl_rows(text: str) -> tuple[list[Any], dict[str, Any] | None]:
    rows: list[Any] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            _malformed(f"JSONL line {number} is invalid: {exc}")
    return rows, None


def _rows_from_value(value: Any) -> tuple[list[Any], dict[str, Any] | None]:
    """A list of documents, a single document, or a pack envelope."""

    if isinstance(value, list):
        return value, None
    if isinstance(value, Mapping):
        envelope_rows = value.get("documents") or value.get("records") or value.get("docs")
        if isinstance(envelope_rows, list):
            name = str(value.get("name") or value.get("title") or "").strip()
            return envelope_rows, {"name": name, "type": str(value.get("documentType") or value.get("type") or "")}
        return [value], None
    _malformed("the compendium source must contain document objects")


def _infer_document_type(raw: Mapping[str, Any]) -> str:
    if isinstance(raw.get("command"), str):
        return "Macro"
    if isinstance(raw.get("pages"), list):
        return "JournalEntry"
    if isinstance(raw.get("results"), list):
        return "RollTable"
    if isinstance(raw.get("sounds"), list):
        return "Playlist"
    if any(key in raw for key in ("tokens", "walls", "levels")) or (
        isinstance(raw.get("grid"), Mapping) and "width" in raw and "height" in raw
    ):
        return "Scene"
    if isinstance(raw.get("items"), list) or isinstance(raw.get("prototypeToken"), Mapping):
        return "Actor"
    if isinstance(raw.get("system"), Mapping):
        return "Item"
    return ""


def _document_type(raw: Mapping[str, Any], declared: str) -> str:
    document_name = raw.get("documentName")
    if isinstance(document_name, str) and document_name in DOCUMENT_TYPES:
        return document_name
    if declared in DOCUMENT_TYPES:
        return declared
    return _infer_document_type(raw)


def _has_script_flag(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in {"script", "scripts", "macro", "macros", "esmodule", "esmodules"} and child:
                return True
            if _has_script_flag(child):
                return True
    elif isinstance(value, list):
        return any(_has_script_flag(child) for child in value)
    return False


def _contains_script(raw: Mapping[str, Any]) -> bool:
    command = raw.get("command")
    return (isinstance(command, str) and bool(command.strip())) or _has_script_flag(raw.get("flags"))


def parse_compendium_source(
    payload: bytes | str | Sequence[Any] | Mapping[str, Any],
    *,
    filename: str = "",
    pack_type: str = "",
) -> CompendiumSource:
    """Parse one pack source into typed records; fail closed on anything else.

    ``pack_type`` is the pack's declared document type (from a module
    manifest), used when a document carries no ``documentName`` and its shape
    is ambiguous. The detection rules are otherwise structural.
    """

    envelope: dict[str, Any] | None = None
    if isinstance(payload, (list, Mapping)):
        rows, envelope = _rows_from_value(payload)
        source_format = "json"
        text = ""
    elif isinstance(payload, (bytes, str)):
        text = _decode(payload)
        source_format = detect_source_format(text, filename=filename)
        if source_format == "json":
            rows, envelope = _json_rows(text)
        elif source_format == "jsonl":
            rows, envelope = _jsonl_rows(text)
        elif source_format == "yaml":
            rows, envelope = _yaml_rows(text)
        else:
            _format_unsupported(f"compendium format is not supported: {source_format!r}")
    else:
        _malformed("the compendium source must be JSON/YAML text, a list or an object")

    if isinstance(envelope, Mapping) and not pack_type:
        pack_type = str(envelope.get("type") or "").strip()
    pack_name = str((envelope or {}).get("name") or "").strip()

    if not rows:
        _malformed("the compendium source contains no documents")
    if len(rows) > MAX_COMPENDIUM_RECORDS:
        _malformed(f"the compendium source exceeds {MAX_COMPENDIUM_RECORDS} documents")

    records: list[FoundryRecord] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _malformed(f"document {index} must be an object")
        record_id = row.get("_id")
        if not isinstance(record_id, str) or not record_id.strip():
            _malformed(f"document {index} has no _id")
        if record_id in seen:
            _malformed(f"document id is duplicated: {record_id!r}")
        seen.add(record_id)
        raw = dict(row)
        name = str(raw.get("name") or "").strip() or record_id
        records.append(FoundryRecord(
            id=record_id,
            document_type=_document_type(raw, pack_type),
            name=name,
            raw=raw,
            contains_script=_contains_script(raw),
        ))
    return CompendiumSource(records=tuple(records), source_format=source_format, pack_name=pack_name)


__all__ = [
    "CODE_COMPENDIUM_FORMAT_UNSUPPORTED",
    "CODE_COMPENDIUM_MALFORMED",
    "CompendiumSource",
    "DOCUMENT_TYPES",
    "FoundryCompendiumError",
    "FoundryRecord",
    "MAX_COMPENDIUM_BYTES",
    "MAX_COMPENDIUM_RECORDS",
    "detect_source_format",
    "parse_compendium_source",
]