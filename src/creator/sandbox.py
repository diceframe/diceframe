"""Explicit Docker-backed sandbox runner for untrusted Creator source.

This backend is opt-in.  The normal Windows/portable worker never invokes
Docker implicitly; callers must inject :class:`DockerSandboxBackend` and keep
the deployment's Docker daemon policy under their control.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .worker import WorkerError, WorkerPolicy, validate_command


_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/@:-]{0,127}$")
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SECRET = re.compile(r"password|api.?key|token|secret|credential", re.I)


@dataclass(frozen=True, slots=True)
class DockerSandboxLimits:
    memory_mb: int = 512
    pids: int = 128
    cpus: str = "1.0"

    def __post_init__(self) -> None:
        if type(self.memory_mb) is not int or not 64 <= self.memory_mb <= 16384:
            raise WorkerError("Docker memory limit is outside the supported range")
        if type(self.pids) is not int or not 16 <= self.pids <= 4096:
            raise WorkerError("Docker PID limit is outside the supported range")
        if not re.fullmatch(r"(?:0?\.\d{1,2}|[1-9]\d?(?:\.\d{1,2})?)", str(self.cpus)):
            raise WorkerError("Docker CPU limit is invalid")


Executor = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]


class DockerSandboxBackend:
    """Callable backend accepted by ``creator.worker.run_worker``."""

    def __init__(
        self,
        *,
        image: str,
        limits: DockerSandboxLimits | None = None,
        executor: Executor | None = None,
    ) -> None:
        if not isinstance(image, str) or not _IMAGE.fullmatch(image.strip()):
            raise WorkerError("Docker image reference is invalid")
        self.image = image.strip()
        self.limits = limits or DockerSandboxLimits()
        self._executor = executor or self._execute

    def command(
        self,
        command: Sequence[str],
        *,
        project_root: Path,
        cwd: str,
        env: dict[str, str] | None,
    ) -> list[str]:
        normalized, workdir = validate_command(command, project_root=project_root, cwd=cwd)
        root = Path(project_root).resolve()
        relative_cwd = workdir.relative_to(root).as_posix() if workdir != root else ""
        container_cwd = "/workspace" + (f"/{relative_cwd}" if relative_cwd else "")
        argv = [
            "docker", "run", "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit", str(self.limits.pids),
            "--memory", f"{self.limits.memory_mb}m",
            "--cpus", str(self.limits.cpus),
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "-v", f"{root}:/workspace:rw",
            "-w", container_cwd,
        ]
        for key, value in (env or {}).items():
            normalized_key = str(key).upper()
            if not _ENV_KEY.fullmatch(normalized_key) or _SECRET.search(normalized_key):
                raise WorkerError("Docker sandbox env contains an unsafe key")
            if normalized_key in {"PATH", "PYTHONPATH", "NODE_PATH", "HOME", "USERPROFILE"}:
                raise WorkerError("Docker sandbox may not override host path or profile variables")
            argv.extend(["--env", f"{normalized_key}={value}"])
        return [*argv, self.image, *normalized]

    async def __call__(
        self,
        command: Sequence[str],
        *,
        project_root: Path,
        cwd: str,
        policy: WorkerPolicy,
        env: dict[str, str] | None,
    ) -> dict[str, Any]:
        argv = self.command(command, project_root=project_root, cwd=cwd, env=env)
        result = self._executor(argv, timeout=policy.timeout_seconds, output_bytes=policy.output_bytes)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise WorkerError("Docker sandbox backend returned an invalid receipt")
        return {**result, "sandbox": True, "backend": "docker"}

    async def _execute(
        self, argv: list[str], *, timeout: float, output_bytes: int,
    ) -> dict[str, Any]:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise WorkerError("Docker executable is unavailable") from exc
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            stdout, stderr = await process.communicate()
        out = (stdout or b"")[:output_bytes].decode("utf-8", errors="replace")
        err = (stderr or b"")[:output_bytes].decode("utf-8", errors="replace")
        return {
            "ok": not timed_out and process.returncode == 0,
            "status": "timeout" if timed_out else ("completed" if process.returncode == 0 else "failed"),
            "returncode": None if timed_out else process.returncode,
            "stdout": out,
            "stderr": err,
            "truncated": len(stdout or b"") > output_bytes or len(stderr or b"") > output_bytes,
        }
