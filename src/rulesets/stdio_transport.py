"""Bounded line-delimited JSON transport for the R02 runtime experiment.

The transport owns only process I/O and request serialization.  It does not
claim to sandbox the child process, grant it permissions, or define a public
runtime SDK.  The injected command must be supplied by an owning lifecycle
service after its own installation and permission checks.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from src.rulesets.runtime_probe import MAX_PROBE_BYTES, RuntimeProbeError

logger = logging.getLogger("trpg.rulesets")

MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 4096
# 子进程 stderr 只做诊断用：留够定位问题的行，但不让它把宿主日志淹掉。
MAX_STDERR_LINES = 64
MAX_STDERR_LINE_CHARS = 400
_SHELL_EXECUTABLES = frozenset({"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "sh", "bash", "zsh"})
_SAFE_ENV_KEYS = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TZ",
    "LANG", "LC_ALL", "PYTHONIOENCODING",
})
_OVERRIDABLE_ENV_KEYS = frozenset({"TZ", "LANG", "LC_ALL", "PYTHONIOENCODING"})
_SECRET_ENV = re.compile(r"password|api.?key|token|secret|credential", re.I)
# 子进程标准输出的兜底解码：Windows 上 Python 默认按 ANSI 码页写管道，
# runtime 自己覆盖了标准流编码时仍要能把响应读出来。
_FALLBACK_ENCODINGS = ("mbcs",) if os.name == "nt" else ()


def _decode_stream_bytes(raw: bytes) -> tuple[str, str | None]:
    """UTF-8 优先，失败退回本机 ANSI 码页，最后才有损替换。"""

    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        pass
    for encoding in _FALLBACK_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "replace"


class StdioRuntimeTransport:
    """Synchronous, serialized JSON-lines transport over a child process."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 5.0,
        stderr_sink: Callable[[str], None] | None = None,
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
        self._stderr_sink = stderr_sink
        self._stderr_seen = 0
        self._stderr_dropped = 0
        self._cwd = Path(cwd) if cwd is not None else None
        child_env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV_KEYS}
        if env:
            child_env.update({key.upper(): value for key, value in env.items()})
        # 协议流是 UTF-8，子进程必须按 UTF-8 解 stdin。Windows 中文环境下 Python
        # 子进程从管道读 stdin 用的是 ANSI 码页（GBK），UTF-8 的中文会被解成乱码，
        # 而且第三个字节常常被当成 GBK 前导字节、把后面的结构字符一起吞掉，
        # 于是 json.loads 失败、runtime 报「无法解析的请求行」并丢弃——宿主这侧
        # 只能看到 20 秒超时（小于 4 KB 的纯 ASCII 请求不受影响，所以只在真正
        # 带规则数据建局时才炸）。强制 UTF-8 模式同样让子进程的 stderr 可读。
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        self._child_env = child_env
        self._encoding_fallback_warned = False
        # 故障关闭（超时/协议错）与生命周期关闭要分开：前者允许下一次调用
        # 重新拉起子进程，后者（宿主停用插件）之后一律拒绝。
        self._restartable = True
        self._spawn()

    def _spawn(self) -> None:
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=str(self._cwd) if self._cwd is not None else None,
                env=self._child_env,
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
            try:
                raw = stream.readline(MAX_PROBE_BYTES + 1)
            except (OSError, ValueError):  # 关闭与读并发时按 EOF 处理
                self._responses.put((None, RuntimeProbeError("runtime process closed stdout")))
                return
            if not raw:
                self._responses.put((None, RuntimeProbeError("runtime process closed stdout")))
                return
            if len(raw) > MAX_PROBE_BYTES:
                self._responses.put((None, RuntimeProbeError("runtime response is too large")))
                return
            text, fallback = _decode_stream_bytes(raw)
            if fallback is not None and not self._encoding_fallback_warned:
                self._encoding_fallback_warned = True
                logger.warning(
                    "外部规则 runtime 响应不是 UTF-8（按 %s 解码）。宿主已强制子进程使用 "
                    "UTF-8 标准流，runtime 自己覆盖了编码设置时才会走到这里。",
                    fallback,
                )
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                self._responses.put((None, RuntimeProbeError("runtime returned invalid JSON")))
                return
            if not isinstance(value, dict):
                self._responses.put((None, RuntimeProbeError("runtime response must be an object")))
                return
            self._responses.put((value, None))

    def _drain_stderr(self) -> None:
        """Forward the child's stderr to the host log instead of swallowing it.

        以前这里读到即丢。外部 runtime 崩在 import、协议解析或规则加载里时，
        宿主只看得到「进程超时」——真正的原因全在这一侧，等于没有可诊断性。
        """

        stream = self.process.stderr
        if stream is None:
            return
        pending = b""
        while True:
            try:
                chunk = stream.read(4096)
            except (OSError, ValueError):
                return
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                self._emit_stderr(line)
        if pending:
            self._emit_stderr(pending)

    def _emit_stderr(self, raw: bytes) -> None:
        text, _ = _decode_stream_bytes(raw)
        text = text.strip()
        if not text:
            return
        if self._stderr_seen >= MAX_STDERR_LINES:
            self._stderr_dropped += 1
            return
        self._stderr_seen += 1
        if len(text) > MAX_STDERR_LINE_CHARS:
            text = text[:MAX_STDERR_LINE_CHARS] + "…"
        if self._stderr_sink is not None:
            try:
                self._stderr_sink(text)
            except Exception:  # noqa: BLE001 - 诊断输出不能反噬传输
                logger.debug("runtime stderr sink failed", exc_info=True)
            return
        logger.warning("外部规则 runtime stderr: %s", text)

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise RuntimeProbeError("runtime request must be JSON") from exc
        if len(encoded) > MAX_PROBE_BYTES:
            raise RuntimeProbeError("runtime request is too large")
        with self._lock:
            if self._closed:
                # 上一次请求把子进程拖死了。插件仍在启用状态，能力还在，
                # 所以这里重新拉起一次，而不是让整条规则永久不可用。
                if not self._restartable:
                    raise RuntimeProbeError("runtime transport is closed")
                logger.warning("外部规则 runtime 子进程已终止，重新拉起")
                self._reopen()
            if self.process.poll() is not None:
                self._reopen()
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
                logger.warning(
                    "外部规则 runtime 未在 %.1fs 内响应（请求 %d 字节，操作 %s，子进程%s）",
                    self.timeout_seconds,
                    len(encoded),
                    str(request.get("operation") or "?"),
                    "已退出" if self.process.poll() is not None else "仍在运行",
                )
                self._terminate(force=True)
                raise RuntimeProbeError("runtime process timed out") from exc
            if error is not None:
                self._terminate(force=True)
                if isinstance(error, RuntimeProbeError):
                    raise error
                raise RuntimeProbeError("runtime process transport failed") from error
            if response is None:
                raise RuntimeProbeError("runtime returned no response")
            return response

    def _reopen(self) -> None:
        self._responses = queue.Queue()
        self._stderr_seen = 0
        self._stderr_dropped = 0
        self._closed = False
        self._spawn()

    def close(self, *, force: bool = False) -> None:
        self._restartable = False
        self._terminate(force=force)

    def _terminate(self, *, force: bool) -> None:
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
        # stdout/stderr 交给两个读线程收尾：子进程退出后管道 EOF，线程自己结束。
        # 在这里抢着 close 会和读线程撞车，把最后几行诊断输出吞掉。
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass

    def __enter__(self) -> "StdioRuntimeTransport":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
