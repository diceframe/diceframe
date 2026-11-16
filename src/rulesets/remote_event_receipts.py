"""Host-owned apply receipts within the remote ruleset event-ledger boundary.

These rows/annotations are persisted and rewound with the existing ledger, but
are never sent to the child. See docs/RULESET_REMOTE_EVENT_BRIDGE.md.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from src.rulesets.runtime_probe import RuntimeProbeError

RECEIPT_KEY = "_host_remote_apply_v1"
MAX_EVENT_LEDGER_ROWS = 4096
MAX_EVENT_BATCH_ID_LENGTH = 200


def digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeProbeError("remote apply identity must be a finite JSON DTO") from exc
    return hashlib.sha256(encoded).hexdigest()


def batch_identity(batch: Any) -> str:
    batch_id = batch.get("batch_id") if isinstance(batch, dict) else None
    if (not isinstance(batch_id, str) or not batch_id or batch_id != batch_id.strip()
            or len(batch_id) > MAX_EVENT_BATCH_ID_LENGTH):
        raise RuntimeProbeError("remote event batch has an invalid batch id")
    return batch_id


def child_ledger(ledger: Any) -> list[dict[str, Any]]:
    """Project only child-owned rows; reject malformed persisted host metadata."""
    if not isinstance(ledger, list) or len(ledger) > MAX_EVENT_LEDGER_ROWS:
        raise RuntimeProbeError("host event ledger is invalid")
    projected = []
    seen = set()
    for row in ledger:
        batch_id = batch_identity(row)
        if batch_id in seen:
            raise RuntimeProbeError("host event ledger has invalid batch ids")
        seen.add(batch_id)
        receipt = row.get(RECEIPT_KEY)
        if RECEIPT_KEY in row:
            if (not isinstance(receipt, dict) or type(receipt.get("child_row")) is not bool
                    or not isinstance(receipt.get("result"), dict)
                    or not isinstance(receipt.get("batch_digest"), str)
                    or not isinstance(receipt.get("runtime_id"), str)
                    or type(receipt.get("runtime_version")) is not int
                    or not isinstance(receipt.get("scope"), str)):
                raise RuntimeProbeError("host remote apply receipt is invalid")
            if not receipt["child_row"]:
                continue
        projected.append({key: deepcopy(value) for key, value in row.items() if key != RECEIPT_KEY})
    return projected


def replay(
    ledger: list[dict[str, Any]], batch: dict[str, Any],
    runtime_id: str, runtime_version: int, scope: str,
) -> dict[str, Any] | None:
    child_ledger(ledger)  # validate before looking up even an early row
    batch_id = batch_identity(batch)
    batch_digest = digest(batch)
    for row in ledger:
        if row["batch_id"] != batch_id:
            continue
        receipt = row.get(RECEIPT_KEY)
        if receipt is None:
            # Historical child rows alone cannot prove the original payload.
            raise RuntimeProbeError("remote batch already exists without a host apply receipt")
        if (receipt["runtime_id"] != runtime_id or receipt["runtime_version"] != runtime_version
                or receipt["scope"] != scope or receipt["batch_digest"] != batch_digest):
            raise RuntimeProbeError("batch_id was reused with a different payload or runtime identity")
        return {**deepcopy(receipt["result"]), "applied": False, "replayed": True}
    return None


def record(
    current: list[dict[str, Any]], child: list[dict[str, Any]] | None,
    batch: dict[str, Any], result: dict[str, Any],
    runtime_id: str, runtime_version: int, scope: str,
) -> list[dict[str, Any]]:
    """Keep host history intact; annotate the new child row or append a host row."""
    previous_child = child_ledger(current)
    batch_id = batch_identity(batch)
    digest(result)  # the persisted public receipt must itself be finite JSON
    if any(row["batch_id"] == batch_id for row in current):
        raise RuntimeProbeError("remote batch already exists")
    if child is None:
        row = {"batch_id": batch_id}
    else:
        # No same-version post-image, bulk append, or rewritten child history.
        if (child[:len(previous_child)] != previous_child
                or len(child) != len(previous_child) + 1):
            raise RuntimeProbeError("remote bridge must append exactly one new batch")
        row = deepcopy(child[-1])
        if row.get("batch_id") != batch_id:
            raise RuntimeProbeError("remote event ledger appended the wrong batch")
    if len(current) >= MAX_EVENT_LEDGER_ROWS:
        raise RuntimeProbeError("host event ledger is too large")
    row[RECEIPT_KEY] = {
        "runtime_id": runtime_id, "runtime_version": runtime_version, "scope": scope,
        "batch_digest": digest(batch), "result": deepcopy(result), "child_row": child is not None,
    }
    return [*deepcopy(current), row]
