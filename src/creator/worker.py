"""Bounded Creator source worker experiment (A03 groundwork).

The worker is an execution budget and workspace boundary, not an operating
system sandbox.  Deployment code must provide the real container/ACL/network
isolation before exposing this to untrusted projects.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


MAX_TIMEOUT_SECONDS = 300
MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 4096
MAX_OUTPUT_BYTES = 1024 * 1024
ALLOWED_EXECUTABLES = frozenset({"python", "python.exe", "pytest", "pytest.exe", "node", "node.exe"})
_SECRET_ENV = re.compile(r"password|api.?key|token|secret|credential", re.I)


class WorkerError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerPolicy:
    timeout_seconds: float = 60.0
    output_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if not 0 < float(self.timeout_seconds) <= MAX_TIMEOUT_SECONDS:
            raise WorkerError("worker timeout is outside the supported budget")
        if type(self.output_bytes) is not int or not 1 <= self.output_bytes <= MAX_OUTPUT_BYTES:
            raise WorkerError("worker output budget is outside the supported limit")


def _workspace(root: Path, relative: str) -> Path:
    base = Path(root).resolve()
    value = str(relative or ".").replace("\\", "/")
    candidate = (base / value).resolve()
    if candidate != base and base not in candidate.parents:
        raise WorkerError("worker cwd escapes the project workspace")
    if not candidate.is_dir():
        raise WorkerError("worker cwd is not a directory")
    return candidate


def validate_command(command: Sequence[str], *, project_root: Path, cwd: str = ".") -> tuple[list[str], Path]:
    if not isinstance(command, (list, tuple)) or not command or len(command) > MAX_ARGUMENTS:
        raise WorkerError("worker command must be a bounded non-empty argv list")
    normalized = [str(item) for item in command]
    if any(not item or len(item.encode("utf-8")) > MAX_ARGUMENT_BYTES for item in normalized):
        raise WorkerError("worker argument is empty or too large")
    executable = Path(normalized[0]).name.casefold()
    if executable not in ALLOWED_EXECUTABLES:
        raise WorkerError("worker executable is not allowlisted")
    if any("\x00" in item for item in normalized):
        raise WorkerError("worker command contains NUL")
    return normalized, _workspace(project_root, cwd)


def _bounded_output(value: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(value) > limit
    return value[:limit].decode("utf-8", errors="replace"), truncated


async def run_worker(
    command: Sequence[str],
    *,
    project_root: Path,
    cwd: str = ".",
    policy: WorkerPolicy | None = None,
    env: dict[str, str] | None = None,
    source_trust: str = "trusted",
    sandbox_backend: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run one explicit argv in the project workspace with bounded output.

    ``source_trust="untrusted"`` is deliberately rejected until the caller
    supplies a real OS/container sandbox.  The bounded runner is suitable for
    an author-owned workspace only; it must not become an accidental fallback
    for arbitrary uploaded source.
    """

    normalized, workdir = validate_command(command, project_root=project_root, cwd=cwd)
    selected = policy or WorkerPolicy()
    if source_trust not in {"trusted", "untrusted"}:
        raise WorkerError("worker source trust must be trusted or untrusted")
    if source_trust == "untrusted":
        if sandbox_backend is None:
            raise WorkerError("untrusted worker execution requires an isolated deployment backend")
        result = sandbox_backend(
            normalized,
            project_root=project_root,
            cwd=str(workdir),
            policy=selected,
            env=env,
        )
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict) or result.get("sandbox") is not True:
            raise WorkerError("isolated deployment backend did not attest sandbox execution")
        return {**result, "source_trust": "untrusted"}
    if env is not None:
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
            raise WorkerError("worker env must be a string map")
        if any(key.upper() in {"PYTHONPATH", "NODE_PATH", "PATH", "HOME", "USERPROFILE"} for key in env):
            raise WorkerError("worker cannot override host path or profile variables")
        if any(_SECRET_ENV.search(key) for key in env):
            raise WorkerError("worker cannot receive credential environment variables")
    child_env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "NODE_PATH", "PYTHONHOME"}
    }
    if env:
        child_env.update(env)
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *normalized,
        cwd=str(workdir),
        env=child_env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=selected.timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout, stderr = await process.communicate()
    out, out_truncated = _bounded_output(stdout or b"", selected.output_bytes)
    err, err_truncated = _bounded_output(stderr or b"", selected.output_bytes)
    return {
        "ok": not timed_out and process.returncode == 0,
        "status": "timeout" if timed_out else ("completed" if process.returncode == 0 else "failed"),
        "returncode": None if timed_out else process.returncode,
        "stdout": out,
        "stderr": err,
        "truncated": out_truncated or err_truncated,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "workspace": str(workdir),
        "sandbox": False,
        "source_trust": source_trust,
    }
