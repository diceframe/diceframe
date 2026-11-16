from pathlib import Path

import pytest

from src.creator.worker import WorkerError, WorkerPolicy, run_worker, validate_command
from src.creator.worker_jobs import WorkerJobStore
from src.creator.projects import CreatorProjectStore
from src.webui.api import WebAPI


def test_worker_command_is_allowlisted_and_workspace_bound(tmp_path: Path):
    command, cwd = validate_command(["python", "-c", "print('ok')"], project_root=tmp_path)
    assert command[0] == "python"
    assert cwd == tmp_path.resolve()
    with pytest.raises(WorkerError):
        validate_command(["cmd.exe", "/c", "echo unsafe"], project_root=tmp_path)
    with pytest.raises(WorkerError):
        validate_command(["python", "-c", "pass"], project_root=tmp_path, cwd="../")


@pytest.mark.asyncio
async def test_worker_runs_without_shell_and_bounds_output(tmp_path: Path):
    result = await run_worker(
        ["python", "-c", "print('x' * 200)"],
        project_root=tmp_path,
        policy=WorkerPolicy(timeout_seconds=10, output_bytes=32),
    )
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["sandbox"] is False


@pytest.mark.asyncio
async def test_worker_timeout_kills_process_and_does_not_accept_profile_overrides(tmp_path: Path):
    with pytest.raises(WorkerError, match="profile"):
        await run_worker(["python", "-c", "pass"], project_root=tmp_path, env={"PATH": "bad"})
    with pytest.raises(WorkerError, match="credential"):
        await run_worker(["python", "-c", "pass"], project_root=tmp_path, env={"API_TOKEN": "secret"})
    result = await run_worker(
        ["python", "-c", "import time; time.sleep(1)"],
        project_root=tmp_path,
        policy=WorkerPolicy(timeout_seconds=0.05, output_bytes=128),
    )
    assert result["ok"] is False
    assert result["status"] == "timeout"


@pytest.mark.asyncio
async def test_worker_rejects_untrusted_source_without_real_isolation_backend(tmp_path: Path):
    with pytest.raises(WorkerError, match="isolated deployment backend"):
        await run_worker(
            ["python", "-c", "pass"],
            project_root=tmp_path,
            source_trust="untrusted",
        )


@pytest.mark.asyncio
async def test_worker_accepts_untrusted_source_only_from_attested_backend(tmp_path: Path):
    calls = []

    async def backend(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        return {"ok": True, "status": "completed", "sandbox": True}

    result = await run_worker(
        ["python", "-c", "pass"],
        project_root=tmp_path,
        source_trust="untrusted",
        sandbox_backend=backend,
    )
    assert result["source_trust"] == "untrusted"
    assert result["sandbox"] is True
    assert calls[0][0][0] == "python"


@pytest.mark.asyncio
async def test_webapi_creator_worker_is_revision_scoped_idempotent_and_rejects_untrusted(tmp_path: Path):
    api = object.__new__(WebAPI)
    api._creator_projects = CreatorProjectStore(tmp_path / "projects.json")
    api._creator_worker_jobs = WorkerJobStore(tmp_path / "worker-jobs.json")
    api._reg = type("Registry", (), {"save_dir": tmp_path / "saves"})()
    project = api._creator_projects.create("alice", "Worker API")
    project_id = project["project"]["id"]
    api._creator_projects.update(
        "alice", project_id, expected_revision=0,
        files={"README.md": "hello"},
    )
    first = await api.run_creator_worker(
        "alice", project_id, expected_revision=1,
        command=["python", "-c", "print(open('README.md').read())"],
        idempotency_key="run-1",
    )
    assert first["ok"] is True
    assert "hello" in first["result"]["stdout"]
    replay = await api.run_creator_worker(
        "alice", project_id, expected_revision=1,
        command=["python", "-c", "print('different')"],
        idempotency_key="run-1",
    )
    assert replay["reused"] is True
    blocked = await api.run_creator_worker(
        "alice", project_id, expected_revision=1,
        command=["python", "-c", "pass"], source_trust="untrusted",
    )
    assert blocked["ok"] is False
    assert "isolated deployment backend" in blocked["error"]


@pytest.mark.asyncio
async def test_webapi_creator_worker_persists_receipt_for_large_output(tmp_path: Path):
    """大输出成功任务必须正常 complete 落盘，而不是在收尾时因回执超限抛错。"""
    api = object.__new__(WebAPI)
    api._creator_projects = CreatorProjectStore(tmp_path / "projects.json")
    api._creator_worker_jobs = WorkerJobStore(tmp_path / "worker-jobs.json")
    api._reg = type("Registry", (), {"save_dir": tmp_path / "saves"})()
    project = api._creator_projects.create("alice", "Large Output")
    project_id = project["project"]["id"]

    finished = await api.run_creator_worker(
        "alice", project_id, expected_revision=0,
        command=["python", "-c", "print('x' * 200000)"],
    )

    assert finished["ok"] is True
    assert finished["job"]["status"] == "completed"
    assert finished["job"]["result"]["truncated"] is True
    receipt = api._creator_worker_jobs.get("alice", finished["job"]["id"])["job"]
    assert receipt["status"] == "completed"
    assert len(receipt["result"]["stdout"]) <= 128 * 1024


@pytest.mark.asyncio
async def test_webapi_creator_worker_timeout_receipt_is_persisted(tmp_path: Path):
    """超时任务的租约必须覆盖执行预算，回执落盘为 timeout 而不是卡在 running。"""
    api = object.__new__(WebAPI)
    api._creator_projects = CreatorProjectStore(tmp_path / "projects.json")
    api._creator_worker_jobs = WorkerJobStore(tmp_path / "worker-jobs.json")
    api._reg = type("Registry", (), {"save_dir": tmp_path / "saves"})()
    project = api._creator_projects.create("alice", "Timeout")
    project_id = project["project"]["id"]

    finished = await api.run_creator_worker(
        "alice", project_id, expected_revision=0,
        command=["python", "-c", "import time; time.sleep(5)"],
        policy=WorkerPolicy(timeout_seconds=1.0),
    )

    assert finished["ok"] is False
    assert finished["job"]["status"] == "timeout"
    receipt = api._creator_worker_jobs.get("alice", finished["job"]["id"])["job"]
    assert receipt["status"] == "timeout"
    assert receipt["lease_active"] is False
