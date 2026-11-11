"""便携版启动器的生命周期集成测试（Windows）。

跑的是**真实编译的启动器**（带 LAUNCHER_TESTING：不拉起浏览器、不改注册表），后端用
tests/fixtures/launcher_lifecycle/fake_web_server.py——它用复制出来的解释器运行，握手
签名调用生产代码 launcher_link.py，所以 C# 与 Python 两边的契约是真的对上了。

覆盖 PR #733 审查要求的四个状态：双安装错误接管、接管旧进程后的更新/回滚、停止
超时拒绝更新、运行中服务自重启（含 HTTP ↔ 自签 HTTPS）与真实崩溃取证。应用窗口
（WebView2）本身不在这里测：这里用 --browser 模式，断言的是启动器的状态机。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scripts import build_launcher

pytestmark = pytest.mark.skipif(os.name != "nt", reason="启动器只能在 Windows 上用 csc 编译运行")

REPO = Path(__file__).resolve().parents[1]
FAKE_SERVER = REPO / "tests" / "fixtures" / "launcher_lifecycle" / "fake_web_server.py"


@pytest.fixture(scope="module")
def launcher_exe(tmp_path_factory) -> Path:
    try:
        sdk = build_launcher.ensure_webview2_sdk()
    except OSError as exc:  # 离线时没法取 SDK：跳过而不是误报失败。
        pytest.skip(f"WebView2 SDK unavailable: {exc}")
    output = tmp_path_factory.mktemp("launcher") / "DiceFrame.exe"
    subprocess.run(
        build_launcher.csc_command(output, sdk, defines=("LAUNCHER_TESTING",)),
        check=True,
        capture_output=True,
    )
    return output


@pytest.fixture(scope="module")
def interpreter_files() -> list[Path]:
    base = Path(getattr(sys, "_base_executable", sys.executable)).parent
    dlls = list(base.glob("python3*.dll"))
    if not (base / "python.exe").is_file() or not dlls:
        pytest.skip("找不到可复制的 Python 解释器")
    return [base / "python.exe", *dlls, *base.glob("vcruntime140*.dll")]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait(predicate, timeout: float, describe):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    pytest.fail(f"timed out after {timeout}s: {describe()}")


class Install:
    """一份最小的便携版安装：DiceFrame.exe + versions/<v>/{python,app} + data/。"""

    def __init__(self, root: Path, port: int, launcher_exe: Path, interpreter: list[Path]):
        self.root = root
        self.port = port
        self.interpreter = interpreter
        self.data = root / "data"
        self.updater = self.data / "_updater"
        self.updater.mkdir(parents=True)
        shutil.copyfile(launcher_exe, root / "DiceFrame.exe")
        (self.data / "config.json").write_text(json.dumps({"web_port": port}), encoding="utf-8")
        self.launcher: subprocess.Popen | None = None
        self.orphan: subprocess.Popen | None = None

    def add_version(self, version: str, **behavior) -> Path:
        slot = self.root / "versions" / f"v{version}"
        (slot / "python").mkdir(parents=True)
        (slot / "app").mkdir()
        for source in self.interpreter:
            try:
                os.link(source, slot / "python" / source.name)
            except OSError:
                shutil.copyfile(source, slot / "python" / source.name)
        shutil.copyfile(FAKE_SERVER, slot / "app" / "web_server.py")
        (slot / "app" / "behavior.json").write_text(
            json.dumps({"version": version, **behavior}), encoding="utf-8"
        )
        return slot

    def set_current(self, version: str) -> None:
        (self.updater / "current.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "version": version,
                    "relative_dir": f"versions\\v{version}",
                    "previous_relative_dir": "",
                }
            ),
            encoding="utf-8",
        )

    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "TRPG_WEB_PORT": str(self.port),
            "PYTHONHOME": sys.base_prefix,
            "DF_TEST_REPO": str(REPO),
        }

    def start_launcher(self) -> subprocess.Popen:
        self.launcher = subprocess.Popen(
            [str(self.root / "DiceFrame.exe"), "--browser"],
            cwd=self.root,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=(self.root / "launcher-out.txt").open("wb"),
            stderr=subprocess.STDOUT,
        )
        return self.launcher

    def start_orphan(self, version: str) -> subprocess.Popen:
        """模拟上一个启动器留下的孤儿后端：同一安装、同一数据目录，但不是本启动器的子进程。"""
        slot = self.root / "versions" / f"v{version}"
        self.orphan = subprocess.Popen(
            [str(slot / "python" / "python.exe"), str(slot / "app" / "web_server.py")],
            cwd=self.root,
            env={
                **self.env(),
                "TRPG_DATA_DIR": str(self.data),
                "TRPG_INSTALL_ROOT": str(self.root),
                "TRPG_ACTIVE_VERSION_DIR": str(slot),
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.orphan

    def output(self) -> str:
        path = self.root / "launcher-out.txt"
        return path.read_bytes().decode("utf-8", errors="replace") if path.exists() else ""

    def events(self) -> list[dict]:
        path = self.data / "test-events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def state(self) -> dict:
        try:
            return json.loads((self.updater / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def describe(self) -> str:
        return f"\n--- launcher output ---\n{self.output()}\n--- events ---\n{self.events()}\n--- state ---\n{self.state()}"

    def wait_output(self, text: str, timeout: float = 30, count: int = 1) -> None:
        _wait(lambda: self.output().count(text) >= count, timeout, lambda: f"{text!r} x{count}{self.describe()}")

    def wait_event(self, predicate, timeout: float = 30) -> dict:
        return _wait(
            lambda: next((event for event in self.events() if predicate(event)), None),
            timeout,
            self.describe,
        )

    def wait_state(self, timeout: float = 90) -> dict:
        return _wait(
            lambda: self.state() if self.state().get("state") in {"done", "failed", "rolled-back"} else None,
            timeout,
            self.describe,
        )

    def write_update_signal(self, version: str) -> None:
        staged = self.updater / "staged" / "DiceFrame.exe"
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / "DiceFrame.exe", staged)
        (self.updater / "restart_signal.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "expected_version": version,
                    "candidate_dir": str(self.root / "versions" / f"v{version}"),
                    "launcher_path": str(staged),
                    "probation_seconds": 10,
                }
            ),
            encoding="utf-8",
        )

    def health(self) -> dict:
        from urllib.request import urlopen

        with urlopen(f"http://127.0.0.1:{self.port}/api/system/update/health", timeout=3) as response:
            return json.loads(response.read())

    def close(self) -> None:
        for process in (self.launcher, self.orphan):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        # execv 出来的接班进程不是任何 Popen 的子进程：让它们自己退出，不按 PID 去杀。
        (self.data / "test-shutdown").write_text("", encoding="utf-8")
        time.sleep(1)


@pytest.fixture
def make_install(tmp_path, launcher_exe, interpreter_files):
    created: list[Install] = []

    def factory(name: str, port: int) -> Install:
        install = Install(tmp_path / name, port, launcher_exe, interpreter_files)
        created.append(install)
        return install

    yield factory
    for install in created:
        install.close()


def _serve_imitation(port: int) -> HTTPServer:
    """伪造 DiceFrame 公开元数据、但给不出实例证明的本地服务。"""

    body = json.dumps(
        {"server_version": "9.9.9", "ok": True, "pid": os.getpid(), "boot_id": "x", "proof": "0" * 64}
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("occupant", ["other_install", "imitation"])
def test_a_backend_that_cannot_prove_it_belongs_here_is_a_port_conflict(make_install, occupant):
    """同机两份安装 A/B 用同一端口：打开 B 必须报端口冲突，而不是打开 A 的页面。

    应用窗口模式下打开页面就会注入 B 的首次访问密码；这里断言根本没有打开任何地址。
    伪造 /api/config 的任意本地服务同样不能被接管。
    """
    port = _free_port()
    b = make_install("B", port)
    b.add_version("1.0.0")
    b.set_current("1.0.0")

    imitation = None
    if occupant == "other_install":
        a = make_install("A", port)
        a.add_version("1.0.0")
        a.set_current("1.0.0")
        a.start_launcher()
        a.wait_output("open-ui http://127.0.0.1:")
    else:
        imitation = _serve_imitation(port)
    try:
        launcher = b.start_launcher()
        assert launcher.wait(timeout=60) == 1, b.describe()
    finally:
        if imitation is not None:
            imitation.shutdown()

    output = b.output()
    assert "open-ui" not in output, b.describe()
    assert f"Port {port} is in use" in output, b.describe()
    # B 没有 spawn 自己的后端去抢端口。
    assert b.events() == [], b.describe()
    if occupant == "other_install":
        # A 没被要求停止，照常运行。
        assert all(event["event"] != "stop-requested" for event in a.events())
        assert a.launcher.poll() is None
        assert a.health()["version"] == "1.0.0"


def test_an_attached_backend_is_stopped_through_the_handshake_before_an_update(make_install):
    """新启动器接管同安装的孤儿后端 → 应用内更新：先优雅停止孤儿，再切到新版本。"""
    port = _free_port()
    install = make_install("install", port)
    install.add_version("1.0.0")
    install.add_version("2.0.0")
    install.set_current("1.0.0")
    orphan = install.start_orphan("1.0.0")
    install.wait_event(lambda event: event["event"] == "serving")

    launcher = install.start_launcher()
    install.wait_output(f"open-ui http://127.0.0.1:{port}")
    # 接管而不是另起一个必然 10048 的后端。
    assert [event["pid"] for event in install.events() if event["event"] == "serving"] == [orphan.pid]

    install.write_update_signal("2.0.0")
    state = install.wait_state()
    assert state["state"] == "done", install.describe()
    # 孤儿收到了带签名的停止请求并自己正常退出——不是被按 PID 杀掉。
    assert orphan.wait(timeout=10) == 0
    orphan_events = [event["event"] for event in install.events() if event["pid"] == orphan.pid]
    assert orphan_events == ["serving", "stop-requested", "exit"], install.describe()
    assert install.health()["version"] == "2.0.0"
    pointer = json.loads((install.updater / "current.json").read_text(encoding="utf-8"))
    assert pointer["relative_dir"] == "versions\\v2.0.0"
    assert launcher.poll() is None


def test_a_failed_candidate_rolls_back_to_a_working_old_version(make_install):
    """接管后的更新失败：旧版本由本启动器重新启动，服务和数据都还在。"""
    port = _free_port()
    install = make_install("install", port)
    install.add_version("1.0.0")
    install.add_version("2.0.0", on_start="crash")
    install.set_current("1.0.0")
    orphan = install.start_orphan("1.0.0")
    install.wait_event(lambda event: event["event"] == "serving")
    launcher = install.start_launcher()
    install.wait_output(f"open-ui http://127.0.0.1:{port}")

    install.write_update_signal("2.0.0")
    state = install.wait_state()
    assert state["state"] == "rolled-back", install.describe()
    assert orphan.wait(timeout=10) == 0
    health = install.health()
    assert health["version"] == "1.0.0"
    assert health["pid"] != orphan.pid
    assert launcher.poll() is None


def test_an_attached_backend_that_does_not_stop_blocks_the_update(make_install):
    """停止超时必须明确失败：不启动候选去和仍在运行的旧服务争端口。"""
    port = _free_port()
    install = make_install("install", port)
    install.add_version("1.0.0", ignore_stop=True)
    install.add_version("2.0.0")
    install.set_current("1.0.0")
    orphan = install.start_orphan("1.0.0")
    install.wait_event(lambda event: event["event"] == "serving")
    launcher = install.start_launcher()
    install.wait_output(f"open-ui http://127.0.0.1:{port}")

    install.write_update_signal("2.0.0")
    state = install.wait_state(timeout=120)
    assert state["state"] == "failed", install.describe()
    assert "无法停止" in state["error"]
    # 候选从没启动过；旧服务照常在线；启动器也还活着。
    assert all(event["version"] != "2.0.0" for event in install.events()), install.describe()
    assert orphan.poll() is None
    assert install.health()["pid"] == orphan.pid
    assert launcher.poll() is None
    assert not (install.updater / "restart_signal.json").exists()


def test_a_running_backend_restart_keeps_the_launcher_alive(make_install):
    """运行中切到自签 HTTPS、再切回 HTTP：服务端 execv 换 PID，启动器重新接管、不报崩溃；
    之后真实崩溃仍然按崩溃处理并留下取证记录。"""
    from src.web_transport.certificates.self_signed import SelfSignedCertificateProvider
    from src.web_transport.certificates.storage import CertificateStore

    port = _free_port()
    install = make_install("install", port)
    install.add_version("1.0.0")
    install.set_current("1.0.0")
    cert = SelfSignedCertificateProvider(CertificateStore(install.data)).prepare()
    (install.data / "test-tls.json").write_text(
        json.dumps({"cert": str(cert.cert_path), "key": str(cert.key_path)}), encoding="utf-8"
    )

    launcher = install.start_launcher()
    install.wait_output(f"open-ui http://127.0.0.1:{port}")

    (install.data / "test-restart").write_text("self_signed", encoding="utf-8")
    install.wait_output(f"reattached to https://127.0.0.1:{port}", timeout=60)
    assert launcher.poll() is None

    (install.data / "test-restart").write_text("off", encoding="utf-8")
    install.wait_output(f"reattached to http://127.0.0.1:{port}", timeout=60)
    assert launcher.poll() is None
    assert not (install.root / "logs" / "last-crash.json").exists(), install.describe()
    serving = [event for event in install.events() if event["event"] == "serving"]
    assert [event["scheme"] for event in serving] == ["http", "https", "http"]
    assert len({event["pid"] for event in serving}) == 3

    (install.data / "test-crash").write_text("", encoding="utf-8")
    assert launcher.wait(timeout=60) == 3, install.describe()
    crash = json.loads((install.root / "logs" / "last-crash.json").read_text(encoding="utf-8"))
    assert crash["exit_code"] == "0x00000003"
    assert crash["pid"] == serving[-1]["pid"]
