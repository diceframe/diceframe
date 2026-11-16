"""Run Playwright against a temporary, deterministic DiceFrame instance."""

from __future__ import annotations

from contextlib import nullcontext
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e_fake_hub import running_fake_hub
from scripts.prepare_e2e_data import prepare_e2e_data


FRONTEND = ROOT / "frontend-v2"
HOST = "127.0.0.1"
# 默认 18000；可用 TRPG_E2E_PORT 覆盖，避免与用户正在运行的实例端口冲突。
PORT = int(os.getenv("TRPG_E2E_PORT") or 18000)


def _port_is_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((HOST, PORT)) == 0


def _wait_until_ready(process: subprocess.Popen, log_file: Path) -> None:
    health_url = f"http://{HOST}:{PORT}/api/system/update/health"
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f"DiceFrame exited before E2E startup:\n{log_file.read_text(encoding='utf-8', errors='replace')}")
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"DiceFrame did not become ready:\n{log_file.read_text(encoding='utf-8', errors='replace')}")


def _keep_server_log(log_file: Path) -> None:
    # The data dir is temporary; CI sets this so a failing run keeps the log.
    target = os.getenv("DICEFRAME_E2E_SERVER_LOG")
    if target and log_file.is_file():
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(log_file, target)


def main() -> int:
    if _port_is_open():
        print(f"Port {PORT} is already in use; stop the existing DiceFrame server before E2E.", file=sys.stderr)
        return 2
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    playwright = FRONTEND / "node_modules" / ".bin" / ("playwright.cmd" if os.name == "nt" else "playwright")
    if not npm or not playwright.is_file():
        print("npm or the local Playwright binary was not found; run npm ci first.", file=sys.stderr)
        return 2
    built = subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=False)
    if built.returncode:
        return built.returncode

    # Manual opt-out preserves DICEFRAME_HUB_URL (or the production default).
    hub_context = nullcontext() if os.getenv("DICEFRAME_E2E_REAL_HUB") == "1" else running_fake_hub()
    with hub_context as hub, tempfile.TemporaryDirectory(prefix="diceframe-e2e-") as temp_name:
        data_dir = Path(temp_name) / "data"
        prepare_e2e_data(data_dir)
        log_file = Path(temp_name) / "server.log"
        env = os.environ.copy()
        if hub is not None:
            env["DICEFRAME_HUB_URL"] = hub.base_url
            print(f"E2E Hub: {hub.base_url} (local fake)", flush=True)
        env["TRPG_DATA_DIR"] = str(data_dir)
        env["DICEFRAME_E2E_DATA_DIR"] = str(data_dir)
        env["TRPG_WEB_PORT"] = str(PORT)
        # 整套 smoke 都从 127.0.0.1 写，单窗口峰值已到 62，超过生产默认的 60/IP/分钟，
        # 429 会落在当时正在跑的那条 spec 上（通常是把面板打成不渲染的 peer-connect）。
        # 这里只抬本轮测试服务器的预算，产品默认值不动，也不许用例自己重试 429。
        env["DICEFRAME_ABUSE_WRITE_PER_IP_LIMIT"] = "300"
        with log_file.open("w", encoding="utf-8") as output:
            server = subprocess.Popen(
                [sys.executable, str(ROOT / "web_server.py")],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_until_ready(server, log_file)
                completed = subprocess.run(
                    [str(playwright), "test", *sys.argv[1:]],
                    cwd=FRONTEND,
                    env=env,
                    check=False,
                )
                return completed.returncode
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)
                _keep_server_log(log_file)


if __name__ == "__main__":
    raise SystemExit(main())
