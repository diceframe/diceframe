"""Stable PID 1 launcher for managed DiceFrame Docker installations."""

from __future__ import annotations

import functools
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .contracts import (
        LAUNCHER_SCHEMA,
        PLATFORM,
        RUNTIME_API,
        atomic_json,
        current_python_abi,
        path_within,
        safe_extract_package,
        safe_version_dir,
        validate_package_tree,
        verify_sha256,
        version_key as _version_key,
    )
except ImportError:  # Executed directly from /opt/diceframe-launcher.
    from contracts import (  # type: ignore[no-redef]
        LAUNCHER_SCHEMA,
        PLATFORM,
        RUNTIME_API,
        atomic_json,
        current_python_abi,
        path_within,
        safe_extract_package,
        safe_version_dir,
        validate_package_tree,
        verify_sha256,
        version_key as _version_key,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_checksum(path: Path, filename: str) -> str:
    matches: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        parts = raw.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            matches.append(parts[0].lower())
    if len(matches) != 1:
        raise ValueError(f"seed checksum must contain exactly one entry for {filename}")
    return matches[0]


def _normalized_fingerprint(text: str) -> str:
    """指纹文件存的是冒号分隔大写 hex；比较时统一成无分隔大写。"""
    return re.sub(r"[:\-\s]", "", str(text or "")).upper()


def _resolve_health_endpoint(data_dir: Path, env: dict[str, str] | None = None) -> tuple[str, str]:
    """决定本机健康探测的 scheme 与期望的自签证书指纹。

    与 Windows launcher 的 ResolveServerEndpoint 同一契约：默认 HTTP；
    config.json 声明 self_signed 且 fingerprint.txt 存在时用 HTTPS 并按
    指纹固定证书。指纹文件缺失说明证书从未生成（服务端会回退 HTTP），
    保持 http。lets_encrypt 模式应用确实在说 HTTPS，探测改走 https。
    TRPG_DOCKER_HEALTH_SCHEME 可显式覆盖（http/https），供特殊部署自救；
    强制 https 只覆盖 scheme，self_signed 模式仍保留证书指纹固定。
    """
    env = env if env is not None else os.environ
    forced = str(env.get("TRPG_DOCKER_HEALTH_SCHEME") or "").strip().lower()
    transport = _read_json(data_dir / "config.json").get("web_transport") or {}
    tls_mode = str(transport.get("tls_mode") or "").strip().lower() if isinstance(transport, dict) else ""
    fingerprint = ""
    if tls_mode == "self_signed":
        try:
            fingerprint = _normalized_fingerprint(
                (data_dir / "certs" / "self-signed" / "fingerprint.txt").read_text(encoding="utf-8")
            )
        except OSError:
            fingerprint = ""
    if forced == "http":
        return "http", ""
    if forced == "https":
        return "https", fingerprint
    if tls_mode == "self_signed":
        return ("https", fingerprint) if fingerprint else ("http", "")
    if tls_mode == "lets_encrypt":
        return "https", ""
    return "http", ""


def _chain_only_context() -> ssl.SSLContext:
    """验证证书链但不校验主机名：回环探测对域名证书必然主机名不符。"""
    context = ssl.create_default_context()
    context.check_hostname = False
    return context


def _verify_pinned_certificate(der: bytes, expected: str) -> None:
    actual = hashlib.sha256(der).hexdigest().upper()
    if actual != expected:
        raise ssl.SSLError(
            f"健康探测证书指纹不匹配（实际 {actual[:12]}...，期望 {expected[:12]}...）"
        )


def _unverified_context() -> ssl.SSLContext:
    """仅用于自签探测的载体上下文；真正的信任判定在指纹比对里。"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS 握手后按 fingerprint.txt 校验证书，替代系统信任链。"""

    def __init__(self, *args: Any, fingerprint: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pinned_fingerprint = fingerprint

    def connect(self) -> None:
        super().connect()
        der = self.sock.getpeercert(binary_form=True) or b""
        _verify_pinned_certificate(der, self._pinned_fingerprint)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """把固定指纹注入本机探测连接（与 Windows launcher 同一契约）。"""

    def __init__(self, fingerprint: str) -> None:
        super().__init__(context=_unverified_context())
        self._fingerprint = fingerprint

    def https_open(self, req: urllib.request.Request) -> Any:  # type: ignore[override]
        connection_factory = functools.partial(
            _PinnedHTTPSConnection, fingerprint=self._fingerprint
        )
        return self.do_open(connection_factory, req, context=self._context)


class DockerLauncher:
    def __init__(
        self,
        runtime_root: Path,
        seed_archive: Path,
        seed_checksum: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 9876,
        python: str = sys.executable,
        startup_timeout: float = 60.0,
        poll_interval: float = 0.5,
        data_dir: Path | None = None,
    ) -> None:
        self.runtime_root = runtime_root.resolve()
        self.versions_dir = self.runtime_root / "docker-versions"
        self.current_file = self.runtime_root / "current.json"
        self.seed_state_file = self.runtime_root / "seed.json"
        self.state_file = self.runtime_root / "state.json"
        self.signal_file = self.runtime_root / "restart_signal.json"
        self.seed_archive = seed_archive.resolve()
        self.seed_checksum = seed_checksum.resolve()
        self.host = host
        self.port = int(port)
        self.python = python
        self.startup_timeout = startup_timeout
        self.poll_interval = poll_interval
        # 健康探测的 scheme 由 data 目录里的 Web Transport 配置决定。
        self.data_dir = (
            Path(data_dir) if data_dir is not None
            else Path(os.getenv("TRPG_DATA_DIR", "/app/data"))
        )
        self.child: subprocess.Popen[bytes] | None = None
        self.active_dir: Path | None = None
        self.stopping = False
        self._last_health_error = ""
        self.versions_dir.mkdir(parents=True, exist_ok=True)

    def _validate_dir(self, directory: Path, expected_version: str | None = None) -> dict[str, Any]:
        resolved = directory.resolve()
        if not path_within(resolved, self.versions_dir) or not resolved.is_dir():
            raise ValueError("managed Docker version directory is invalid")
        return validate_package_tree(
            resolved,
            expected_version=expected_version,
            platform=PLATFORM,
            python_abi=current_python_abi(),
            runtime_api=RUNTIME_API,
            launcher_schema=LAUNCHER_SCHEMA,
        )

    def _pointer_dir(self, field: str) -> Path | None:
        pointer = _read_json(self.current_file)
        relative = str(pointer.get(field) or "")
        if not relative:
            return None
        candidate = (self.runtime_root / relative).resolve()
        return candidate if path_within(candidate, self.versions_dir) else None

    def _validate_unmanaged_tree(self, directory: Path, version: str) -> dict[str, Any]:
        return validate_package_tree(
            directory,
            expected_version=version,
            platform=PLATFORM,
            python_abi=current_python_abi(),
            runtime_api=RUNTIME_API,
            launcher_schema=LAUNCHER_SCHEMA,
        )

    def ensure_seed(self) -> tuple[Path, dict[str, Any]]:
        if not self.seed_archive.is_file() or not self.seed_checksum.is_file():
            raise ValueError("managed Docker image seed is missing")
        expected = _read_checksum(self.seed_checksum, self.seed_archive.name)
        cached = _read_json(self.seed_state_file)
        if str(cached.get("sha256") or "") == expected:
            version = str(cached.get("version") or "")
            seed_dir = self.versions_dir / safe_version_dir(version)
            try:
                return seed_dir, self._validate_dir(seed_dir, version)
            except (OSError, ValueError):
                pass

        verify_sha256(self.seed_archive, expected)
        extract_parent = Path(tempfile.mkdtemp(prefix="seed-", dir=self.runtime_root))
        installing: Path | None = None
        try:
            package_root = safe_extract_package(self.seed_archive, extract_parent)
            manifest = self._validate_unmanaged_tree(package_root, "")
            seed_dir = self.versions_dir / safe_version_dir(manifest["version"])
            if seed_dir.exists():
                self._validate_dir(seed_dir, manifest["version"])
            else:
                installing = self.versions_dir / (seed_dir.name + ".installing")
                shutil.rmtree(installing, ignore_errors=True)
                os.replace(package_root, installing)
                os.replace(installing, seed_dir)
            atomic_json(self.seed_state_file, {
                "schema": 1, "version": manifest["version"], "sha256": expected,
            })
            return seed_dir, manifest
        finally:
            shutil.rmtree(extract_parent, ignore_errors=True)
            if installing is not None:
                shutil.rmtree(installing, ignore_errors=True)

    def _spawn(self, directory: Path) -> subprocess.Popen[bytes]:
        manifest = self._validate_dir(directory)
        app_dir = directory / "app"
        environment = os.environ.copy()
        site_packages = directory / str(manifest["site_packages"])
        old_pythonpath = environment.get("PYTHONPATH", "")
        environment.update({
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(site_packages) + (os.pathsep + old_pythonpath if old_pythonpath else ""),
            "TRPG_INSTALL_MODE": "docker-managed",
            "TRPG_DOCKER_RUNTIME_ROOT": str(self.runtime_root),
            "TRPG_ACTIVE_VERSION_DIR": str(directory),
        })
        process = subprocess.Popen(
            [self.python, str(directory / str(manifest["entrypoint"]))],
            cwd=app_dir,
            env=environment,
        )
        self.child = process
        self.active_dir = directory
        return process

    def _health(
        self,
        expected_version: str,
        health_path: str = "/api/system/update/health",
    ) -> bool:
        if self.child is None or self.child.poll() is not None:
            self._last_health_error = "候选进程已退出"
            return False
        normalized_path = "/" + str(health_path or "").lstrip("/")
        scheme, fingerprint = _resolve_health_endpoint(self.data_dir)
        attempts = ["https", "http"] if scheme == "https" else ["http"]
        last_error = ""
        for attempt_scheme in attempts:
            url = f"{attempt_scheme}://{self.host}:{self.port}{normalized_path}"
            try:
                if attempt_scheme == "https":
                    if fingerprint:
                        opener = urllib.request.build_opener(
                            _PinnedHTTPSHandler(fingerprint)
                        )
                    else:
                        opener = urllib.request.build_opener(
                            urllib.request.HTTPSHandler(context=_chain_only_context())
                        )
                    response = opener.open(url, timeout=2)
                else:
                    response = urllib.request.urlopen(url, timeout=2)
                with response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError, http.client.HTTPException) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if not isinstance(payload, dict):
                self._last_health_error = "健康接口响应不是 JSON 对象"
                return False
            reported = str(payload.get("version") or "").strip().lstrip("vV")
            expected = str(expected_version or "").strip().lstrip("vV")
            if not payload.get("ok"):
                self._last_health_error = "健康接口返回 ok=false"
                return False
            if reported != expected:
                self._last_health_error = f"版本不匹配（期望 {expected}，实际 {reported or '缺失'}）"
                return False
            self._last_health_error = ""
            return True
        self._last_health_error = last_error or "健康探测失败"
        return False

    def _wait_healthy(self, directory: Path) -> bool:
        manifest = self._validate_dir(directory)
        expected = str(manifest["version"])
        health_path = str(manifest["health_path"])
        deadline = time.monotonic() + self.startup_timeout
        while not self.stopping and time.monotonic() < deadline:
            if self._health(expected, health_path):
                break
            if self.child is not None and self.child.poll() is not None:
                return False
            time.sleep(self.poll_interval)
        else:
            return False

        # A newly swapped process can briefly drop loopback connections while
        # aiohttp finishes startup tasks. Do not roll back on one transient
        # probe failure; a continuous outage for this bounded grace period is
        # still treated as a failed candidate.
        probation_failure_started: float | None = None
        failure_grace = min(20.0, max(5.0, self.startup_timeout / 3.0))
        probation_deadline = time.monotonic() + float(manifest["probation_seconds"])
        while not self.stopping and time.monotonic() < probation_deadline:
            if self._health(expected, health_path):
                probation_failure_started = None
            else:
                if self.child is not None and self.child.poll() is not None:
                    return False
                if probation_failure_started is None:
                    probation_failure_started = time.monotonic()
                elif time.monotonic() - probation_failure_started >= failure_grace:
                    return False
            time.sleep(min(2.0, self.poll_interval))
        return not self.stopping

    def _stop_child(self, timeout: float = 15.0) -> None:
        process = self.child
        if process is None or process.poll() is not None:
            self.child = None
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.child = None

    def _relative(self, directory: Path) -> str:
        return directory.resolve().relative_to(self.runtime_root).as_posix()

    def _commit(self, directory: Path, previous: Path | None) -> None:
        manifest = self._validate_dir(directory)
        payload = {
            "schema": 1,
            "mode": "docker",
            "version": manifest["version"],
            "relative_dir": self._relative(directory),
            "previous_relative_dir": self._relative(previous) if previous and previous != directory else "",
            "runtime_api": RUNTIME_API,
        }
        atomic_json(self.current_file, payload)

    def _write_state(self, state: str, **values: Any) -> None:
        current = _read_json(self.state_file)
        atomic_json(self.state_file, {**current, "state": state, **values})

    def _prune(self) -> None:
        keep = {path.resolve() for path in (self._pointer_dir("relative_dir"), self._pointer_dir("previous_relative_dir")) if path}
        for directory in self.versions_dir.iterdir():
            if directory.is_dir() and directory.resolve() not in keep:
                shutil.rmtree(directory, ignore_errors=True)

    def _launch_candidate(self, candidate: Path, previous: Path | None) -> bool:
        self._stop_child()
        self._spawn(candidate)
        if self._wait_healthy(candidate):
            self._commit(candidate, previous)
            self._write_state(
                "done", version=self._validate_dir(candidate)["version"],
                restart_needed=False, applied_at=int(time.time()), error="",
            )
            self.signal_file.unlink(missing_ok=True)
            self._prune()
            return True

        self._stop_child()
        detail = self._last_health_error
        error = f"candidate {candidate.name} failed its health check"
        if detail:
            error += f" ({detail})"
        if previous is not None:
            try:
                self._spawn(previous)
                if self._wait_healthy(previous):
                    self._write_state("rolled-back", error=error, restart_needed=False)
                    self.signal_file.unlink(missing_ok=True)
                    return False
                self._stop_child()
            except Exception:
                self._stop_child()
                pass
        self._write_state("failed", error=error, restart_needed=False)
        self.signal_file.unlink(missing_ok=True)
        return False

    def _choose_startup(self, seed_dir: Path, seed_manifest: dict[str, Any]) -> tuple[Path, Path | None]:
        current = self._pointer_dir("relative_dir")
        previous = self._pointer_dir("previous_relative_dir")
        valid_current: tuple[Path, dict[str, Any]] | None = None
        valid_previous: Path | None = None
        if current:
            try:
                current_manifest = self._validate_dir(current)
                valid_current = (current, current_manifest)
            except (OSError, ValueError):
                valid_current = None
        if previous:
            try:
                self._validate_dir(previous)
                valid_previous = previous
            except (OSError, ValueError):
                valid_previous = None
        if valid_current:
            current_dir, current_manifest = valid_current
            if _version_key(current_manifest["version"]) >= _version_key(seed_manifest["version"]):
                return current_dir, valid_previous
            return seed_dir, current_dir
        if valid_previous:
            return valid_previous, None
        return seed_dir, None

    def _handle_restart_signal(self) -> None:
        signal_payload = _read_json(self.signal_file)
        if signal_payload.get("schema") != 1 or signal_payload.get("mode") != "docker":
            raise ValueError("restart signal contract is invalid")
        relative = str(signal_payload.get("relative_dir") or "")
        version = str(signal_payload.get("expected_version") or "").strip().lstrip("vV")
        candidate = (self.runtime_root / relative).resolve()
        if not relative or not path_within(candidate, self.versions_dir):
            raise ValueError("restart signal candidate path is invalid")
        self._validate_dir(candidate, version)
        active = self.active_dir.resolve() if self.active_dir else None
        if active is not None and candidate.resolve() == active:
            # 重复应用当前已在运行的版本（前端状态滞后或用户重复点击）：
            # 清掉 signal 即可，绝不为“更新到自己”杀掉健康进程重启。
            self.signal_file.unlink(missing_ok=True)
            self._write_state(
                "done", version=self._validate_dir(candidate)["version"],
                restart_needed=False, applied_at=int(time.time()), error="",
            )
            return
        previous = self.active_dir
        self._launch_candidate(candidate, previous)

    def request_stop(self, _signum: int, _frame: Any) -> None:
        self.stopping = True
        self._stop_child()

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        seed_dir, seed_manifest = self.ensure_seed()
        selected, previous = self._choose_startup(seed_dir, seed_manifest)
        self._spawn(selected)
        if not self._wait_healthy(selected):
            self._stop_child()
            fallbacks = [path for path in (previous, seed_dir) if path and path != selected]
            for fallback in fallbacks:
                try:
                    self._spawn(fallback)
                    if self._wait_healthy(fallback):
                        selected = fallback
                        break
                    self._stop_child()
                except Exception:
                    self._stop_child()
            else:
                return 1
        self._commit(selected, previous if previous != selected else None)
        self._prune()

        while not self.stopping:
            if self.child is None or self.child.poll() is not None:
                return int(self.child.returncode if self.child else 1)
            if self.signal_file.exists():
                try:
                    self._handle_restart_signal()
                except Exception as exc:
                    self.signal_file.unlink(missing_ok=True)
                    self._write_state("failed", error=str(exc), restart_needed=False)
            time.sleep(self.poll_interval)
        return 0


def main() -> int:
    runtime_root = Path(os.getenv("TRPG_DOCKER_RUNTIME_ROOT", "/app/data/_updater"))
    seed_archive = Path(os.getenv("TRPG_DOCKER_SEED_ARCHIVE", "/opt/diceframe-seed/update.zip"))
    seed_checksum = Path(os.getenv("TRPG_DOCKER_SEED_CHECKSUM", "/opt/diceframe-seed/update.sha256"))
    launcher = DockerLauncher(
        runtime_root,
        seed_archive,
        seed_checksum,
        host=os.getenv("TRPG_DOCKER_HEALTH_HOST", "127.0.0.1"),
        port=int(os.getenv("TRPG_WEB_PORT", "9876")),
    )
    try:
        return launcher.run()
    except Exception as exc:
        print(f"DiceFrame Docker launcher failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
