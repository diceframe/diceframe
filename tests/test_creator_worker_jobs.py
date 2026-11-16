from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.creator.worker_jobs import WorkerJobError, WorkerJobStore, execute_job


def test_worker_job_is_idempotent_and_owner_scoped(tmp_path: Path):
    store = WorkerJobStore(tmp_path / "jobs.json", clock=lambda: 100.0)
    first = store.create("alice", "project-1", ["python", "-c", "pass"], idempotency_key="edit-1")
    second = store.create("alice", "project-1", ["python", "-c", "pass"], idempotency_key="edit-1")
    assert second["reused"] is True
    assert second["job"]["id"] == first["job"]["id"]
    with pytest.raises(KeyError):
        store.get("bob", first["job"]["id"])


@pytest.mark.asyncio
async def test_worker_job_requires_lease_and_persists_confirmed_result(tmp_path: Path):
    store = WorkerJobStore(tmp_path / "jobs.json", clock=lambda: 100.0)
    created = store.create("alice", "project-1", ["python", "-c", "pass"])
    claim = store.claim("alice", created["job"]["id"], lease_seconds=30)
    with pytest.raises(WorkerJobError, match="leased"):
        store.claim("alice", created["job"]["id"], lease_seconds=30)
    result = await execute_job(
        store, "alice", created["job"]["id"], claim["lease_id"],
        lambda job: {"status": "completed", "job": job["id"]},
    )
    assert result["job"]["status"] == "completed"
    assert store.get("alice", created["job"]["id"])["job"]["result"]["status"] == "completed"


def test_expired_lease_becomes_interrupted_and_can_be_retried(tmp_path: Path):
    now = [100.0]
    store = WorkerJobStore(tmp_path / "jobs.json", clock=lambda: now[0])
    created = store.create("alice", "project-1", ["python", "-c", "pass"])
    claim = store.claim("alice", created["job"]["id"], lease_seconds=10)
    now[0] = 111.0
    assert store.recover_expired() == [created["job"]["id"]]
    assert store.get("alice", created["job"]["id"])["job"]["status"] == "interrupted"
    retry = store.claim("alice", created["job"]["id"], lease_seconds=10)
    assert retry["job"]["attempt"] == 2
    with pytest.raises(WorkerJobError, match="invalid or expired"):
        store.complete("alice", created["job"]["id"], claim["lease_id"], {"bad": True})


@pytest.mark.asyncio
async def test_worker_failure_keeps_result_unconfirmed_and_allows_explicit_retry(tmp_path: Path):
    store = WorkerJobStore(tmp_path / "jobs.json")
    created = store.create("alice", "project-1", ["python", "-c", "pass"])
    claim = store.claim("alice", created["job"]["id"])
    result = await execute_job(
        store, "alice", created["job"]["id"], claim["lease_id"],
        lambda _job: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert result["job"]["status"] == "failed"
    assert result["job"]["result"] is None
    retry = store.claim("alice", created["job"]["id"])
    assert retry["job"]["attempt"] == 2
