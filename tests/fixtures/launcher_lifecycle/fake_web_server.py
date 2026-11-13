# -*- coding: utf-8 -*-
"""便携版启动器生命周期测试用的假后端（tests/test_launcher_lifecycle.py）。

被测启动器把它当成 ``versions/<v>/app/web_server.py``，用复制出来的解释器启动。
只用标准库，但握手签名直接调用生产代码 ``src/webui/services/launcher_link.py``
（按文件加载，不导入整个 src 包），所以 C# 与 Python 两边的契约是真的对上了。

行为由同目录 ``behavior.json`` 决定：``version``、``on_start``（"serve" / "crash"）、
``ignore_stop``。运行中的触发器放在数据目录：``test-restart``（内容为 "self_signed"
或 "off"，模拟切换 HTTPS 后 execv 自重启）、``test-crash``（以退出码 3 崩溃）、
``test-shutdown``（测试收尾，立即退出）。
每个关键事件追加到数据目录的 ``test-events.jsonl``，供测试断言。
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ["TRPG_DATA_DIR"])
PORT = int(os.environ["TRPG_WEB_PORT"])
BEHAVIOR = json.loads((APP_DIR / "behavior.json").read_text(encoding="utf-8"))
VERSION = BEHAVIOR["version"]
BOOT_ID = secrets.token_hex(8)

_spec = importlib.util.spec_from_file_location(
    "launcher_link",
    Path(os.environ["DF_TEST_REPO"]) / "src" / "webui" / "services" / "launcher_link.py",
)
link = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(link)

state = {"restart": False}


def log(event: str, **extra) -> None:
    record = {"event": event, "pid": os.getpid(), "version": VERSION, "boot_id": BOOT_ID, **extra}
    with (DATA_DIR / "test-events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def tls_mode() -> str:
    try:
        return json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8")).get("tls_mode", "off")
    except (OSError, ValueError):
        return "off"


class Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 允许两个进程绑同一端口：与真实后端一样不开。
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定的方法名
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self._json(200, {"server_version": VERSION})
        elif parsed.path == "/api/system/update/health":
            self._json(200, {"ok": True, "version": VERSION, "pid": os.getpid(), "boot_id": BOOT_ID})
        elif parsed.path == "/api/system/launcher/identity":
            key = link.read_instance_key(DATA_DIR)
            nonce = parse_qs(parsed.query).get("nonce", [""])[0]
            if key is None:
                self._json(404, {"ok": False})
            elif not link.valid_nonce(nonce):
                self._json(400, {"ok": False})
            else:
                self._json(
                    200,
                    {
                        "ok": True,
                        "boot_id": BOOT_ID,
                        "pid": os.getpid(),
                        "version": VERSION,
                        "proof": link.identity_proof(key, nonce, BOOT_ID, os.getpid()),
                    },
                )
        else:
            self._json(200, {"page": "fake"})

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/system/launcher/stop":
            self._json(404, {"ok": False})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        key = link.read_instance_key(DATA_DIR)
        valid = (
            key is not None
            and link.valid_nonce(body.get("nonce"))
            and body.get("boot_id") == BOOT_ID
            and link.proof_matches(link.stop_proof(key, body["nonce"], BOOT_ID), body.get("proof"))
        )
        if not valid:
            log("stop-rejected")
            self._json(403, {"ok": False})
            return
        log("stop-requested")
        self._json(202, {"ok": True, "stopping": True})
        if not BEHAVIOR.get("ignore_stop"):
            threading.Timer(0.3, self.server.shutdown).start()

    def log_message(self, *args):
        pass


def watch_triggers(server: Server) -> None:
    while True:
        if (DATA_DIR / "test-shutdown").exists():
            # 测试收尾：这份安装的所有假后端（含 execv 出来的接班进程）一起退出。
            os._exit(0)
        crash = DATA_DIR / "test-crash"
        restart = DATA_DIR / "test-restart"
        if crash.exists():
            crash.unlink()
            log("crash")
            os._exit(3)
        if restart.exists():
            mode = restart.read_text(encoding="utf-8").strip() or "off"
            restart.unlink()
            config_path = DATA_DIR / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["tls_mode"] = mode
            config_path.write_text(json.dumps(config), encoding="utf-8")
            state["restart"] = True
            log("restart-requested", mode=mode)
            server.shutdown()
            return
        time.sleep(0.2)


def main() -> int:
    if BEHAVIOR.get("on_start") == "crash":
        log("start-crash")
        return 7
    server = Server(("127.0.0.1", PORT), Handler)
    scheme = "http"
    if tls_mode() == "self_signed":
        paths = json.loads((DATA_DIR / "test-tls.json").read_text(encoding="utf-8"))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(paths["cert"], paths["key"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    threading.Thread(target=watch_triggers, args=(server,), daemon=True).start()
    log("serving", scheme=scheme)
    server.serve_forever(poll_interval=0.2)
    server.server_close()
    if state["restart"]:
        # 与 web_server.py 相同：先留下重启意图，再 execv（Windows 上会换 PID、以 0 退出）。
        link.record_restart_intent(DATA_DIR, boot_id=BOOT_ID, pid=os.getpid())
        log("execv")
        os.execv(sys.executable, [sys.executable, *sys.argv])
    log("exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
