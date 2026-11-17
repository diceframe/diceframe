"""Drain/restart contracts for noisy and late external runtime processes."""

from __future__ import annotations

import io
import queue
import threading
import tracemalloc
from types import SimpleNamespace

from src.rulesets.stdio_transport import (
    MAX_STDERR_LINE_BYTES,
    MAX_STDERR_LINE_CHARS,
    MAX_STDERR_LINES,
    StdioRuntimeTransport,
)


def _drainer():
    transport = object.__new__(StdioRuntimeTransport)
    transport._stderr_seen = 0
    transport._stderr_dropped = 0
    seen = []
    transport._stderr_sink = seen.append
    return transport, seen


def test_unterminated_stderr_is_bounded_before_emission():
    transport, seen = _drainer()

    class Stream:
        remaining = 16 * 1024 * 1024

        def read(self, size):
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

    sizes = []
    original = transport._emit_stderr

    def emit(raw, **kwargs):
        sizes.append(len(raw))
        original(raw, **kwargs)

    transport._emit_stderr = emit
    tracemalloc.start()
    try:
        transport._drain_stderr(Stream())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sizes == [MAX_STDERR_LINE_BYTES]
    assert seen == ["x" * MAX_STDERR_LINE_CHARS + "…"]
    # Input is generated a chunk at a time. Growing input must not grow the
    # host's pending buffer (the previous drain retained the entire 16 MiB).
    assert peak < 256 * 1024


def test_long_stderr_line_does_not_consume_following_lines_or_line_budget():
    transport, seen = _drainer()
    stream = io.BytesIO(
        "界".encode() * 5000 + b"\nnext\n" + b"row\n" * (MAX_STDERR_LINES + 3)
    )
    transport._drain_stderr(stream)
    assert seen[0] == "界" * MAX_STDERR_LINE_CHARS + "…"
    assert seen[1] == "next"
    assert len(seen) == MAX_STDERR_LINES
    assert transport._stderr_dropped == 5
    assert stream.tell() == len(stream.getvalue())


def test_late_old_stdout_eof_never_enters_replacement_queue():
    transport = object.__new__(StdioRuntimeTransport)
    old_queue = queue.Queue()
    new_queue = queue.Queue()
    reading = threading.Event()
    release = threading.Event()

    class OldStream:
        def readline(self, size):
            reading.set()
            assert release.wait(5)
            return b""

    old_stream = OldStream()
    transport.process = SimpleNamespace(stdout=old_stream)
    transport._responses = old_queue
    reader = threading.Thread(target=transport._read_stdout, args=(old_stream, old_queue))
    reader.start()
    try:
        assert reading.wait(5)
        # Reopen replaces both the process and response queue while the old
        # reader is still awaiting EOF from its terminated child.
        transport.process = SimpleNamespace(stdout=io.BytesIO())
        transport._responses = new_queue
    finally:
        release.set()
        reader.join(5)
    assert not reader.is_alive()
    assert new_queue.empty()
    _, error = old_queue.get_nowait()
    assert "closed stdout" in str(error)
