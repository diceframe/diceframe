"""Internal R00 experiment for remote ruleset DTO and transaction boundaries.

This module is intentionally not registered as a production RulesetRuntime
adapter.  It records the questions the external runtime experiment must answer:
JSON-only requests, bounded payloads, timeout/error fail-closed, idempotent
request receipts, and no mutation of the caller's live state.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


MAX_PROBE_BYTES = 256 * 1024
PROBE_PROTOCOL_VERSION = 1


class RuntimeProbeError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeProbeRequest:
    runtime_id: str
    runtime_version: int
    request_id: str
    operation: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROBE_PROTOCOL_VERSION,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "request_id": self.request_id,
            "operation": self.operation,
            "payload": deepcopy(self.payload),
        }


def normalize_probe_request(raw: Any) -> RuntimeProbeRequest:
    if not isinstance(raw, dict):
        raise RuntimeProbeError("runtime probe request must be an object")
    runtime_id = str(raw.get("runtime_id") or "").strip()
    request_id = str(raw.get("request_id") or "").strip()
    operation = str(raw.get("operation") or "").strip()
    version = raw.get("runtime_version")
    payload = raw.get("payload")
    if (
        int(raw.get("protocol_version", 0) or 0) != PROBE_PROTOCOL_VERSION
        or not runtime_id
        or not request_id
        or not operation
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(payload, dict)
    ):
        raise RuntimeProbeError("invalid runtime probe request")
    request = RuntimeProbeRequest(runtime_id, version, request_id, operation, deepcopy(payload))
    try:
        encoded = json.dumps(request.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeProbeError("runtime probe payload must be JSON") from exc
    if len(encoded) > MAX_PROBE_BYTES:
        raise RuntimeProbeError("runtime probe payload is too large")
    return request


class RuntimeProbeLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._receipts: dict[str, tuple[str, dict[str, Any]]] = {}
        if self.path is not None and self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError
                for request_id, value in raw.items():
                    if (
                        isinstance(request_id, str)
                        and isinstance(value, list)
                        and len(value) == 2
                        and isinstance(value[0], str)
                        and isinstance(value[1], dict)
                    ):
                        self._receipts[request_id] = (value[0], deepcopy(value[1]))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeProbeError("runtime probe ledger is invalid") from exc

    def lookup(self, request: RuntimeProbeRequest) -> dict[str, Any] | None:
        digest = _digest(request.to_dict())
        previous = self._receipts.get(request.request_id)
        if previous is None:
            return None
        if previous[0] != digest:
            raise RuntimeProbeError("request_id was reused with a different payload")
        return deepcopy(previous[1])

    def record(self, request: RuntimeProbeRequest, response: dict[str, Any]) -> None:
        self._receipts[request.request_id] = (_digest(request.to_dict()), deepcopy(response))
        if len(self._receipts) > 256:
            oldest = next(iter(self._receipts))
            self._receipts.pop(oldest, None)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps({
                key: [digest, response] for key, (digest, response) in self._receipts.items()
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.path)


class RuntimeProbeClient:
    def __init__(
        self,
        transport: Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]],
        *,
        ledger: RuntimeProbeLedger | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("runtime probe timeout must be between 0 and 30 seconds")
        self.transport = transport
        self.ledger = ledger or RuntimeProbeLedger()
        self.timeout_seconds = timeout_seconds

    async def call(
        self, raw_request: Any, *, live_state: Any = None,
        receipt_request: Any = None,
    ) -> dict[str, Any]:
        request = normalize_probe_request(raw_request)
        ledger_request = normalize_probe_request(receipt_request) if receipt_request is not None else request
        if ledger_request.request_id != request.request_id:
            raise RuntimeProbeError("runtime receipt request identity mismatch")
        cached = self.ledger.lookup(ledger_request)
        if cached is not None:
            return cached
        request_payload = request.to_dict()
        before = deepcopy(live_state)
        try:
            result = self.transport(request_payload)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise RuntimeProbeError("runtime probe timed out") from exc
        except RuntimeProbeError:
            raise
        except Exception as exc:
            raise RuntimeProbeError("runtime probe transport failed") from exc
        if not isinstance(result, dict) or result.get("request_id") != request.request_id:
            raise RuntimeProbeError("runtime probe response identity mismatch")
        response = deepcopy(result)
        if live_state != before:
            raise RuntimeProbeError("runtime probe mutated live state")
        self.ledger.record(ledger_request, response)
        return response

    def call_sync(
        self, raw_request: Any, *, live_state: Any = None,
        receipt_request: Any = None,
    ) -> dict[str, Any]:
        """Synchronous transport seam for the current runtime protocol."""

        request = normalize_probe_request(raw_request)
        ledger_request = normalize_probe_request(receipt_request) if receipt_request is not None else request
        if ledger_request.request_id != request.request_id:
            raise RuntimeProbeError("runtime receipt request identity mismatch")
        cached = self.ledger.lookup(ledger_request)
        if cached is not None:
            return cached
        before = deepcopy(live_state)
        try:
            result = self.transport(request.to_dict())
            if inspect.isawaitable(result):
                raise RuntimeProbeError("async transport requires RuntimeProbeClient.call")
        except RuntimeProbeError:
            raise
        except Exception as exc:
            raise RuntimeProbeError("runtime probe transport failed") from exc
        if not isinstance(result, dict) or result.get("request_id") != request.request_id:
            raise RuntimeProbeError("runtime probe response identity mismatch")
        if live_state != before:
            raise RuntimeProbeError("runtime probe mutated live state")
        response = deepcopy(result)
        self.ledger.record(ledger_request, response)
        return response
