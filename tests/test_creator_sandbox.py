from __future__ import annotations

from pathlib import Path

import pytest

from src.creator.sandbox import DockerSandboxBackend, DockerSandboxLimits
from src.creator.worker import WorkerError, WorkerPolicy, run_worker


def test_docker_backend_builds_networkless_restricted_argv(tmp_path: Path):
    backend = DockerSandboxBackend(
        image="python:3.11-slim",
        limits=DockerSandboxLimits(memory_mb=256, pids=32, cpus="0.5"),
    )
    argv = backend.command(
        ["python", "-c", "print('ok')"],
        project_root=tmp_path,
        cwd=".",
        env={"LANG": "C.UTF-8"},
    )
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--env" in argv
    assert argv[-3:] == ["python", "-c", "print('ok')"]
    with pytest.raises(WorkerError, match="unsafe key"):
        backend.command(
            ["python", "-c", "pass"], project_root=tmp_path, cwd=".",
            env={"API_TOKEN": "secret"},
        )


@pytest.mark.asyncio
async def test_worker_accepts_docker_backend_attestation_for_untrusted_source(tmp_path: Path):
    calls = []

    async def executor(argv, *, timeout, output_bytes):
        calls.append((argv, timeout, output_bytes))
        return {"ok": True, "status": "completed", "returncode": 0}

    backend = DockerSandboxBackend(image="python:3.11-slim", executor=executor)
    result = await run_worker(
        ["python", "-c", "pass"],
        project_root=tmp_path,
        source_trust="untrusted",
        sandbox_backend=backend,
        policy=WorkerPolicy(timeout_seconds=2, output_bytes=128),
    )
    assert result["sandbox"] is True
    assert result["backend"] == "docker"
    assert result["source_trust"] == "untrusted"
    assert calls and calls[0][0][1] == "run"
