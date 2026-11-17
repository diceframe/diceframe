"""Bounded line-delimited JSON transport for the R02 runtime experiment.

The transport owns only process I/O and request serialization.  It does not
claim to sandbox the child process, grant it permissions, or define a public
runtime SDK.  The injected command must be supplied by an owning lifecycle
service after its own installation and permission checks.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import re
from pathlib import Path
from typing import Any, Sequence

from src.rulesets.runtime_probe import MAX_PROBE_BYTES, RuntimeProbeError


MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 4096
_SHELL_EXECUTABLES = frozenset({"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "sh", "bash", "zsh"})
_SAFE_ENV_KEYS = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TZ",
    "LANG", "LC_ALL", "PYTHONIOENCODING",
})
_OVERRIDABLE_ENV_KEYS = frozenset({"TZ", "LANG", "LC_ALL", "PYTHONIOENCODING"})
_SECRET_ENV = re.compile(r"password|api.?key|token|secret|credential", re.I)


class StdioRuntimeTransport:
    """Synchronous, serialized JSON-lines transport over a child process."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.command = self._validate_command(command)
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("stdio runtime timeout must be between 0 and 30 seconds")
        if env is not None and (
            not isinstance(env, dict)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in env.items())
        ):
            raise ValueError("stdio runtime env must be a string map")
        if env is not None and any(_SECRET_ENV.search(key) for key in env):
            raise ValueError("stdio runtime may not receive credential environment variables")
        if env is not None and any(key.upper() not in _SAFE_ENV_KEYS for key in env):
            raise ValueError("stdio runtime env key is not allowlisted")
        if env is not None and any(key.upper() not in _OVERRIDABLE_ENV_KEYS for key in env):
            raise ValueError("stdio runtime may not override process path environment")
        self.timeout_seconds = float(timeout_seconds)
        self._closed = False
        self._lock = threading.RLock()
        self._responses: queue.Queue[tuple[dict[str, Any] | None, BaseException | None]] = queue.Queue()
        child_env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV_KEYS}
        if env:
            child_env.update({key.upper(): value for key, value in env.items()})
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=str(cwd) if cwd is not None else None,
                env=child_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
            )
        except OSError as exc:
            raise RuntimeProbeError("failed to start runtime process") from exc
        self._reader = threading.Thread(target=self._read_stdout, name="diceframe-runtime-stdout", daemon=True)
        self._stderr_reader = threading.Thread(target=self._drain_stderr, name="diceframe-runtime-stderr", daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    @staticmethod
    def _validate_command(command: Sequence[str]) -> list[str]:
        if not isinstance(command, (list, tuple)) or not command or len(command) > MAX_ARGUMENTS:
            raise ValueError("runtime command must be a bounded argv list")
        normalized = [str(item) for item in command]
        if any(not item or "\x00" in item or len(item.encode("utf-8")) > MAX_ARGUMENT_BYTES for item in normalized):
            raise ValueError("runtime command contains an invalid argument")
        if Path(normalized[0]).name.casefold() in _SHELL_EXECUTABLES:
            raise ValueError("runtime command may not invoke a shell")
        return normalized

    def _read_stdout(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self._responses.put((None, RuntimeProbeError("runtime process has no stdout")))
            return
        while True:
            raw = stream.readline(MAX_PROBE_BYTES + 1)
            if not raw:
                self._responses.put((None, RuntimeProbeError("runtime process closed stdout")))
                return
            if len(raw) > MAX_PROBE_BYTES:
                self._responses.put((None, RuntimeProbeError("runtime response is too large")))
                return
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._responses.put((None, RuntimeProbeError("runtime returned invalid JSON")))
                return
            if not isinstance(value, dict):
                self._responses.put((None, RuntimeProbeError("runtime response must be an object")))
                return
            self._responses.put((value, None))

    def _drain_stderr(self) -> None:
        stream = self.process.stderr
        if stream is not None:
            while stream.read(4096):
                pass

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise RuntimeProbeError("runtime transport is closed")
        try:
            encoded = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise RuntimeProbeError("runtime request must be JSON") from exc
        if len(encoded) > MAX_PROBE_BYTES:
            raise RuntimeProbeError("runtime request is too large")
        with self._lock:
            if self.process.poll() is not None:
                raise RuntimeProbeError("runtime process exited")
            if self.process.stdin is None:
                raise RuntimeProbeError("runtime process has no stdin")
            try:
                self.process.stdin.write(encoded)
                self.process.stdin.flush()
            except OSError as exc:
                raise RuntimeProbeError("runtime stdin is unavailable") from exc
            try:
                response, error = self._responses.get(timeout=self.timeout_seconds)
            except queue.Empty as exc:
                self.close(force=True)
                raise RuntimeProbeError("runtime process timed out") from exc
            if error is not None:
                self.close(force=True)
                if isinstance(error, RuntimeProbeError):
                    raise error
                raise RuntimeProbeError("runtime process transport failed") from error
            if response is None:
                raise RuntimeProbeError("runtime returned no response")
            return response

    def close(self, *, force: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        process = self.process
        if process.poll() is None:
            try:
                if force:
                    process.kill()
                else:
                    process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def __enter__(self) -> "StdioRuntimeTransport":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
