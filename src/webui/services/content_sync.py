"""Content sync use cases behind ``/api/content/*``.

Parses the request shape and delegates to the kind-neutral orchestrator in
``src.content_modules.sync`` with the importers/exporters the facade injects
(one per portable schema). Authentication, the paired-device identity rule
and size limits are transport concerns and live in the route.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.content_modules import sync
from src.content_modules.adapters.compendium import CompendiumSyncImporter
from src.content_modules.plan import declared_source_ref

IMPORT_SOURCE_INVALID = "IMPORT_SOURCE_INVALID"
CURSOR_INVALID = "CURSOR_INVALID"

_LIST_FIELDS = frozenset({"kind", "cursor", "limit"})


def _importers(dependencies: ContentSyncDependencies, pushed_by_device: str) -> Mapping[str, Any]:
    """Registered importers plus the FVTT compendium pack reader.

    A ``foundry-compendium`` item rides the lorebook slot and delegates every
    other document to the wrapped lorebook importer unchanged (workplan §6.17
    m3-01). Wrapping here keeps the WebAPI registration untouched.
    """

    found = dict(dependencies.importers(pushed_by_device))
    lorebook = found.get("lorebook-v3")
    if lorebook is not None:
        found["lorebook-v3"] = CompendiumSyncImporter(lorebook)
    return found


@dataclass(frozen=True)
class ContentSyncDependencies:
    #: Importers keyed by portable schema, built for one request (they may
    #: carry request context such as the pushing device).
    importers: Callable[[str], Mapping[str, sync.KindImporter]]
    exporters: Callable[[], Mapping[str, sync.KindExporter]]


def _failure(exc: sync.SyncItemError) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "error_code": exc.code, "error": str(exc)}
    if exc.client_ref:
        result["client_ref"] = exc.client_ref
    return result


def parse_import_request(body: dict[str, Any]) -> tuple[sync.SyncSource, list[sync.SyncItem]]:
    try:
        kind, source_id = declared_source_ref(body.get("source"))
    except ValueError as exc:
        raise sync.SyncItemError(IMPORT_SOURCE_INVALID, str(exc)) from exc
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise sync.SyncItemError(sync.REQUEST_INVALID, "items must be a non-empty list")
    items: list[sync.SyncItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise sync.SyncItemError(sync.REQUEST_INVALID, "every item must be an object")
        hint = raw.get("canonical_hint")
        if hint is not None and not isinstance(hint, str):
            raise sync.SyncItemError(sync.REQUEST_INVALID, "canonical_hint must be a string")
        items.append(sync.SyncItem(
            client_ref=str(raw.get("client_ref") or ""),
            kind=str(raw.get("kind") or ""),
            format=str(raw.get("format") or ""),
            document=raw.get("document"),
            canonical_hint=str(hint or ""),
        ))
    return sync.SyncSource(kind, source_id), items


def preview_import(
    dependencies: ContentSyncDependencies, body: dict[str, Any], *, pushed_by_device: str = "",
) -> dict[str, Any]:
    try:
        source, items = parse_import_request(body)
        return sync.preview(_importers(dependencies, pushed_by_device), source, items)
    except sync.SyncItemError as exc:
        return _failure(exc)


def commit_import(
    dependencies: ContentSyncDependencies, body: dict[str, Any], *, pushed_by_device: str = "",
) -> dict[str, Any]:
    decisions = body.get("decisions") or {}
    if not isinstance(decisions, dict):
        return {"ok": False, "error_code": sync.REQUEST_INVALID, "error": "decisions must be an object"}
    digest = body.get("plan_digest")
    if not isinstance(digest, str) or not digest:
        return {"ok": False, "error_code": "PLAN_DIGEST_REQUIRED", "error": "commit needs the previewed plan_digest"}
    try:
        source, items = parse_import_request(body)
        return sync.commit(
            _importers(dependencies, pushed_by_device), source, items,
            confirmed_digest=digest, decisions=decisions,
        )
    except sync.SyncItemError as exc:
        return _failure(exc)


def _requested(body: dict[str, Any]) -> list[tuple[str, str]]:
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise sync.SyncItemError(sync.REQUEST_INVALID, "items must be a non-empty list")
    requested: list[tuple[str, str]] = []
    for raw in raw_items:
        if not isinstance(raw, dict) or not isinstance(raw.get("canonical_id"), str):
            raise sync.SyncItemError(sync.REQUEST_INVALID, "every item needs kind and canonical_id")
        requested.append((str(raw.get("kind") or ""), raw["canonical_id"]))
    return requested


def export_content(
    dependencies: ContentSyncDependencies, body: dict[str, Any], *, server_instance_id: str,
) -> dict[str, Any]:
    try:
        return sync.export(dependencies.exporters(), _requested(body), server_instance_id=server_instance_id)
    except sync.SyncItemError as exc:
        return _failure(exc)


def content_status(dependencies: ContentSyncDependencies, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return sync.status(dependencies.exporters(), _requested(body))
    except sync.SyncItemError as exc:
        return _failure(exc)


def _encode_cursor(kind: str, after: str) -> str:
    raw = json.dumps({"kind": kind, "after": after}, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: Any, kind: str) -> str:
    """The last listed id; the cursor is opaque to clients and bound to its kind."""

    if cursor is None:
        return ""
    if not isinstance(cursor, str) or not cursor or len(cursor) > 1024:
        raise sync.SyncItemError(CURSOR_INVALID, "cursor must be a next_cursor from a previous page")
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        decoded = None
    if (
        not isinstance(decoded, dict) or decoded.get("kind") != kind
        or not isinstance(decoded.get("after"), str) or not decoded["after"]
    ):
        raise sync.SyncItemError(CURSOR_INVALID, "cursor must be a next_cursor from a previous page of this kind")
    return decoded["after"]


def parse_list_request(body: dict[str, Any], *, max_limit: int) -> tuple[str, str, int]:
    """``(kind, after, limit)`` from a list request; fails closed on anything else."""

    unknown = sorted(set(body) - _LIST_FIELDS)
    if unknown:
        raise sync.SyncItemError(sync.REQUEST_INVALID, f"unknown list request field(s): {unknown}")
    kind = body.get("kind")
    if not isinstance(kind, str) or not kind:
        raise sync.SyncItemError(sync.REQUEST_INVALID, "kind must be a content kind")
    limit = body.get("limit", max_limit)
    if limit is None:
        limit = max_limit
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= max_limit:
        raise sync.SyncItemError(sync.REQUEST_INVALID, f"limit must be an integer from 1 to {max_limit}")
    return kind, _decode_cursor(body.get("cursor"), kind), limit


def list_content(dependencies: ContentSyncDependencies, body: dict[str, Any], *, max_limit: int) -> dict[str, Any]:
    """One page of the owner's library of one kind. Read-only."""

    try:
        kind, after, limit = parse_list_request(body, max_limit=max_limit)
        rows, last = sync.list_page(dependencies.exporters(), kind, after=after, limit=limit)
    except sync.SyncItemError as exc:
        return _failure(exc)
    return {"ok": True, "items": rows, "next_cursor": _encode_cursor(kind, last) if last else None}


__all__ = [
    "ContentSyncDependencies",
    "commit_import",
    "content_status",
    "export_content",
    "list_content",
    "parse_import_request",
    "parse_list_request",
    "preview_import",
]
