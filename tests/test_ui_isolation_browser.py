from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest

from src.webui.ui_isolation import build_frame_descriptor, frame_response_headers


def _server(handler_factory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_real_browser_enforces_frame_csp_network_and_ancestor_policy():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    probes: list[str] = []
    frame_server = None
    attacker_server = None
    frame_thread = None
    attacker_thread = None
    try:
        # The frame server is created first so its explicit origin is known to
        # the descriptor and to the attacker page's iframe URL.
        def frame_handler_factory(*args, **kwargs):
            class FrameHandler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    path = urlsplit(self.path).path
                    if path == "/index.html":
                        descriptor = build_frame_descriptor(
                            "demo-pack", "index.html", origin=f"http://127.0.0.1:{frame_server.server_port}",
                            assets=("app.js",),
                        )
                        body = b"""<!doctype html><script>window.inlineRan=true</script><script src='/app.js'></script>"""
                        headers = frame_response_headers(descriptor)
                        headers.update({"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body))})
                    elif path == "/app.js":
                        body = b"window.externalRan=true; fetch('/network-probe').catch(()=>{});"
                        headers = {"Content-Type": "text/javascript", "Content-Length": str(len(body))}
                    elif path == "/network-probe":
                        probes.append("network")
                        body = b"unexpected"
                        headers = {"Content-Type": "text/plain", "Content-Length": str(len(body))}
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_args):
                    return

            return FrameHandler(*args, **kwargs)

        # ThreadingHTTPServer expects a class, so create it after the server
        # object exists via a small indirection.
        frame_server = ThreadingHTTPServer(("127.0.0.1", 0), FrameHandlerPlaceholder)
        frame_server.RequestHandlerClass = frame_handler_factory
        frame_thread = Thread(target=frame_server.serve_forever, daemon=True)
        frame_thread.start()
        frame_url = f"http://127.0.0.1:{frame_server.server_port}/index.html"

        def attacker_handler_factory(*args, **kwargs):
            class AttackerHandler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    body = f"<iframe src='{frame_url}'></iframe>".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_args):
                    return

            return AttackerHandler(*args, **kwargs)

        attacker_server = ThreadingHTTPServer(("127.0.0.1", 0), attacker_handler_factory)
        attacker_thread = Thread(target=attacker_server.serve_forever, daemon=True)
        attacker_thread.start()
        attacker_url = f"http://127.0.0.1:{attacker_server.server_port}/attacker.html"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(frame_url)
            page.wait_for_timeout(300)
            assert page.evaluate("window.externalRan === true") is True
            assert page.evaluate("window.inlineRan === true") is False
            assert probes == []
            assert context.cookies() == []

            attacker = context.new_page()
            attacker.goto(attacker_url)
            attacker.wait_for_timeout(500)
            assert len(attacker.frames) == 2
            assert attacker.frames[1].url.startswith("chrome-error://")
            browser.close()
    finally:
        if attacker_server is not None:
            attacker_server.shutdown()
            attacker_server.server_close()
        if frame_server is not None:
            frame_server.shutdown()
            frame_server.server_close()
        if attacker_thread is not None:
            attacker_thread.join(timeout=2)
        if frame_thread is not None:
            frame_thread.join(timeout=2)


class FrameHandlerPlaceholder(BaseHTTPRequestHandler):
    """Temporary class replaced before the server thread starts."""

    def do_GET(self):  # noqa: N802
        self.send_response(500)
        self.end_headers()

    def log_message(self, *_args):
        return
