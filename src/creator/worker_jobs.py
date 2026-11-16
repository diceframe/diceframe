"""Durable, owner-scoped Creator worker job receipts.

The store is deliberately separate from process execution.  It records what
was requested and whether the result was confirmed, so a restart never turns a
lost worker response into an implicit success.  A caller may inject the actual
trusted or sandboxed runner through :meth:`execute`.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence


MAX_JOBS = 256
MAX_IDEMPOTENCY_KEY = 160
MAX_RESULT_BYTES = 128 * 1024
MAX_LEASE_SECONDS = 300


class WorkerJobError(ValueError):
    pass


def _now() -> float:
    return time.time()


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkerJobError("worker job value must be JSON serializable") from exc


def _job_id() -> str:
    return "job-" + secrets.token_urlsafe(12).replace("-", "_").replace("=", "")[:16]


class WorkerJobStore:
    """Small atomic JSON store for Creator worker lifecycle receipts."""

    def __init__(self, path: Path, *, clock: Callable[[], float] | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or _now

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "jobs": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerJobError("worker job receipt store is corrupt") from exc
        if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("jobs"), dict):
            raise WorkerJobError("unsupported worker job receipt schema")
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    @staticmethod
    def _owner(value: str) -> str:
        owner = str(value or "").strip()
        if not owner or len(owner) > 200:
            raise WorkerJobError("worker job requires an owner")
        return owner

    @staticmethod
    def _project(value: str) -> str:
        project = str(value or "").strip()
        if not project or len(project) > 64:
            raise WorkerJobError("worker job project id is invalid")
        return project

    @staticmethod
    def _command(command: Sequence[str]) -> list[str]:
        if not isinstance(command, (list, tuple)) or not command or len(command) > 64:
            raise WorkerJobError("worker job command is invalid")
        normalized = [str(item) for item in command]
        if any(not item or "\x00" in item or len(item.encode("utf-8")) > 4096 for item in normalized):
            raise WorkerJobError("worker job command contains an invalid argument")
        return normalized

    @staticmethod
    def _key(value: str | None) -> str:
        key = str(value or "").strip()
        if len(key) > MAX_IDEMPOTENCY_KEY:
            raise WorkerJobError("worker job idempotency key is too long")
        return key

    def _owned(self, state: dict[str, Any], owner: str, job_id: str) -> dict[str, Any]:
        row = state["jobs"].get(str(job_id))
        if not isinstance(row, dict) or row.get("owner_id") != self._owner(owner):
            raise KeyError("worker job not found")
        return row

    def create(
        self,
        owner_id: str,
        project_id: str,
        command: Sequence[str],
        *,
        cwd: str = ".",
        source_trust: str = "trusted",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner = self._owner(owner_id)
        project = self._project(project_id)
        normalized = self._command(command)
        if source_trust not in {"trusted", "untrusted"}:
            raise WorkerJobError("worker job source trust is invalid")
        key = self._key(idempotency_key)
        if len(str(cwd or ".")) > 240 or "\x00" in str(cwd):
            raise WorkerJobError("worker job cwd is invalid")
        state = self._load()
        if key:
            for row in state["jobs"].values():
                if (
                    isinstance(row, dict)
                    and row.get("owner_id") == owner
                    and row.get("project_id") == project
                    and row.get("idempotency_key") == key
                ):
                    return {"ok": True, "job": self._public(row), "reused": True}
        if len(state["jobs"]) >= MAX_JOBS:
            raise WorkerJobError("worker job receipt limit reached")
        now = self._clock()
        row = {
            "id": _job_id(),
            "owner_id": owner,
            "project_id": project,
            "command": normalized,
            "cwd": str(cwd or "."),
            "source_trust": source_trust,
            "idempotency_key": key,
            "status": "queued",
            "attempt": 0,
            "lease_id": "",
            "lease_expires_at": 0.0,
            "result": None,
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        state["jobs"][row["id"]] = row
        self._save(state)
        return {"ok": True, "job": self._public(row), "reused": False}

    def claim(self, owner_id: str, job_id: str, *, lease_seconds: int = 60) -> dict[str, Any]:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
            raise WorkerJobError("worker lease is outside the supported range")
        state = self._load()
        row = self._owned(state, owner_id, job_id)
        now = self._clock()
        status = str(row.get("status") or "")
        expires = float(row.get("lease_expires_at") or 0)
        if status == "completed":
            raise WorkerJobError("worker job is already completed")
        if status == "running" and expires > now:
            raise WorkerJobError("worker job is leased by another runner")
        if status == "running" and expires <= now:
            row["status"] = "interrupted"
            row["error"] = "worker lease expired; result is unconfirmed"
            status = "interrupted"
        if status not in {"queued", "failed", "timeout", "interrupted"}:
            raise WorkerJobError("worker job is not claimable")
        lease = secrets.token_urlsafe(18)
        row.update({
            "status": "running",
            "attempt": int(row.get("attempt") or 0) + 1,
            "lease_id": lease,
            "lease_expires_at": now + lease_seconds,
            "updated_at": now,
        })
        self._save(state)
        return {"ok": True, "lease_id": lease, "job": self._public(row)}

    def _finish(self, owner_id: str, job_id: str, lease_id: str, *, status: str, result: Any = None, error: str = "") -> dict[str, Any]:
        state = self._load()
        row = self._owned(state, owner_id, job_id)
        if row.get("status") != "running" or not lease_id or row.get("lease_id") != lease_id:
            raise WorkerJobError("worker job lease is invalid or expired")
        if self._clock() > float(row.get("lease_expires_at") or 0):
            raise WorkerJobError("worker job lease expired; result is unconfirmed")
        if status == "completed" and _json_size(result) > MAX_RESULT_BYTES:
            raise WorkerJobError("worker job result is too large")
        row.update({
            "status": status,
            "lease_id": "",
            "lease_expires_at": 0.0,
            "result": result if status == "completed" else None,
            "error": str(error or "")[:4096],
            "updated_at": self._clock(),
        })
        self._save(state)
        return {"ok": True, "job": self._public(row)}

    def complete(self, owner_id: str, job_id: str, lease_id: str, result: Any) -> dict[str, Any]:
        return self._finish(owner_id, job_id, lease_id, status="completed", result=result)

    def fail(self, owner_id: str, job_id: str, lease_id: str, error: str, *, timeout: bool = False) -> dict[str, Any]:
        return self._finish(owner_id, job_id, lease_id, status="timeout" if timeout else "failed", error=error)

    def recover_expired(self, *, now: float | None = None) -> list[str]:
        state = self._load()
        current = self._clock() if now is None else float(now)
        recovered: list[str] = []
        for job_id, row in state["jobs"].items():
            if not isinstance(row, dict) or row.get("status") != "running":
                continue
            if float(row.get("lease_expires_at") or 0) <= current:
                row.update({
                    "status": "interrupted",
                    "lease_id": "",
                    "lease_expires_at": 0.0,
                    "error": "worker lease expired; result is unconfirmed",
                    "updated_at": current,
                })
                recovered.append(str(job_id))
        if recovered:
            self._save(state)
        return recovered

    def get(self, owner_id: str, job_id: str) -> dict[str, Any]:
        state = self._load()
        return {"ok": True, "job": self._public(self._owned(state, owner_id, job_id))}

    def list(self, owner_id: str, project_id: str | None = None) -> dict[str, Any]:
        owner = self._owner(owner_id)
        project = self._project(project_id) if project_id is not None else None
        state = self._load()
        rows = [
            self._public(row)
            for row in state["jobs"].values()
            if isinstance(row, dict) and row.get("owner_id") == owner
            and (project is None or row.get("project_id") == project)
        ]
        return {"ok": True, "jobs": sorted(rows, key=lambda item: str(item["id"]))}

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        result = {key: row.get(key) for key in (
            "id", "project_id", "command", "cwd", "source_trust", "idempotency_key",
            "status", "attempt", "result", "error", "created_at", "updated_at",
        )}
        # A lease token is a capability, never part of a read projection.
        result["lease_active"] = bool(row.get("status") == "running" and row.get("lease_id"))
        return result


Runner = Callable[[dict[str, Any]], Awaitable[Any] | Any]


async def execute_job(
    store: WorkerJobStore,
    owner_id: str,
    job_id: str,
    lease_id: str,
    runner: Runner,
) -> dict[str, Any]:
    """Run an already claimed job and persist only a confirmed result."""

    job = store.get(owner_id, job_id)["job"]
    try:
        result = runner(job)
        if inspect.isawaitable(result):
            result = await result
    except asyncio.TimeoutError as exc:
        return store.fail(owner_id, job_id, lease_id, str(exc) or "worker timed out", timeout=True)
    except Exception as exc:
        return store.fail(owner_id, job_id, lease_id, str(exc) or "worker failed")
    return store.complete(owner_id, job_id, lease_id, result)
