import json
import sys
import textwrap
import time

import pytest
from pathlib import Path

from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.runtime_probe import RuntimeProbeError
from src.rulesets.stdio_transport import StdioRuntimeTransport


def _child(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", textwrap.dedent(code)]


def test_stdio_transport_forwards_json_and_supports_remote_adapter():
    transport = StdioRuntimeTransport(_child(
        """
        import json, sys
        for line in sys.stdin:
            request = json.loads(line)
            print(json.dumps({"request_id": request["request_id"], "ok": True, "result": {"operation": request["operation"]}}), flush=True)
        """
    ))
    try:
        runtime = RemoteRulesetRuntimeAdapter(
            "external:stdio", 1, RulesetCapabilities(), transport,
        )
        assert runtime.describe_experience({}, "en")["operation"] == "describe_experience"
        runtime.close()
        assert transport.process.poll() is not None
    finally:
        transport.close()


def test_stdio_transport_times_out_and_terminates_child():
    transport = StdioRuntimeTransport(_child(
        """
        import time
        time.sleep(5)
        """
    ), timeout_seconds=0.05)
    with pytest.raises(RuntimeProbeError, match="timed out"):
        transport({"request_id": "r1", "operation": "slow"})
    assert transport.process.poll() is not None
    transport.close(force=True)


def test_stdio_transport_rejects_shell_and_malformed_json_output():
    with pytest.raises(ValueError):
        StdioRuntimeTransport(["cmd.exe", "/c", "echo unsafe"])
    with pytest.raises(ValueError, match="credential"):
        StdioRuntimeTransport(_child("pass"), env={"API_KEY": "secret"})
    with pytest.raises(ValueError, match="path environment"):
        StdioRuntimeTransport(_child("pass"), env={"PATH": "unsafe"})
    with pytest.raises(ValueError, match="allowlisted"):
        StdioRuntimeTransport(_child("pass"), env={"UNTRUSTED": "value"})
    transport = StdioRuntimeTransport(_child("print('not-json', flush=True)"))
    try:
        with pytest.raises(RuntimeProbeError, match="invalid JSON"):
            transport({"request_id": "r1", "operation": "bad"})
    finally:
        transport.close(force=True)


def test_stdio_transport_bounds_one_response_line():
    transport = StdioRuntimeTransport(_child(
        """
        import sys
        print('x' * 300000, flush=True)
        """
    ))
    try:
        with pytest.raises(RuntimeProbeError, match="too large"):
            transport({"request_id": "r1", "operation": "large"})
    finally:
        transport.close(force=True)


def test_stdio_transport_reopens_a_child_that_died_on_a_timeout(tmp_path: Path):
    """一次超时不能把整条规则永久变成不可用。

    子进程第一代在启动时故意卡住（模拟冷启动里卡死的 runtime），被超时杀掉之后
    第二代应当重新拉起并正常应答。
    """

    marker = tmp_path / "spawned"
    child = _child(
        f"""
        import json, sys, time
        from pathlib import Path
        marker = Path({str(marker)!r})
        if not marker.exists():
            marker.write_text("1", encoding="utf-8")
            time.sleep(2)
        for line in sys.stdin:
            request = json.loads(line)
            print(json.dumps({{"request_id": request["request_id"], "ok": True,
                              "result": {{"operation": request["operation"]}}}}), flush=True)
        """
    )
    transport = StdioRuntimeTransport(child, timeout_seconds=0.4)
    try:
        with pytest.raises(RuntimeProbeError, match="timed out"):
            transport({"request_id": "r1", "operation": "slow"})
        assert marker.exists()
        answered = transport({"request_id": "r2", "operation": "fast"})
        assert answered["result"]["operation"] == "fast"
    finally:
        transport.close(force=True)


def test_stdio_transport_forwards_child_stderr_to_the_host():
    seen: list[str] = []
    transport = StdioRuntimeTransport(
        _child("""
        import sys
        sys.stderr.write("boom from child\\n")
        sys.stderr.flush()
        """),
        stderr_sink=seen.append,
    )
    try:
        with pytest.raises(RuntimeProbeError, match="closed stdout"):
            transport({"request_id": "r1", "operation": "dying"})
        for _ in range(50):
            if any("boom from child" in line for line in seen):
                break
            time.sleep(0.02)
    finally:
        transport.close(force=True)
    assert any("boom from child" in line for line in seen)


def test_stdio_transport_pins_the_child_to_utf8_stdio():
    """子进程必须按 UTF-8 解 stdin，否则中文请求行会被 ANSI 码页解坏。

    Windows 中文环境下 Python 从管道读 stdin 默认用 GBK。UTF-8 的中文第三个
    字节会被当成 GBK 前导字节，把后面的 `}`、`]`、字母一起吞掉，JSON 结构就
    坏了；runtime 只能丢弃请求，宿主看到的是 20 秒超时。
    """

    transport = StdioRuntimeTransport(_child("""
        import json, sys
        for line in sys.stdin:
            request = json.loads(line)
            print(json.dumps({"request_id": request["request_id"], "ok": True,
                              "result": {"stdin_encoding": sys.stdin.encoding}}), flush=True)
        """))
    try:
        answer = transport({"request_id": "r1", "operation": "encoding"})
    finally:
        transport.close(force=True)
    assert str(answer["result"]["stdin_encoding"]).lower().replace("_", "-") == "utf-8"


def test_stdio_transport_round_trips_a_large_non_ascii_request():
    """建局时 game_binding 会带上整份规则模板（几十 KB 中文）。

    小请求恰好躲过编码破坏，所以这条用真实体量的中文负载把回归钉住。
    """

    detail = "最终物语：飞空艇、梅基多巨坑、艾洛尼亚帝国、德斯迪蒙娜、罗盘、幼钟式；" * 300
    transport = StdioRuntimeTransport(_child("""
        import json, sys
        for line in sys.stdin:
            request = json.loads(line)
            payload = request.get("payload") or {}
            print(json.dumps({"request_id": request["request_id"], "ok": True,
                              "result": {"detail_len": len(payload.get("detail") or ""),
                                         "detail_tail": (payload.get("detail") or "")[-4:]}},
                             ensure_ascii=False), flush=True)
        """))
    try:
        answer = transport({
            "request_id": "r1",
            "operation": "game_binding",
            "payload": {"detail": detail},
        })
    finally:
        transport.close(force=True)
    assert answer["result"]["detail_len"] == len(detail)
    assert answer["result"]["detail_tail"] == detail[-4:]


def test_stdio_runtime_restart_reuses_host_state_and_request_receipts(tmp_path: Path):
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime

    child = """
    import json, sys
    for line in sys.stdin:
        request = json.loads(line)
        if request["operation"] != "apply_event_batch":
            result = {}
        else:
            payload = request["payload"]
            current = payload["instance"]["modules"]["ruleset_runtime"]
            batch = payload["batch"]
            ledger = list(current.get("event_ledger", []))
            state = dict(current.get("state", {}))
            if not any(row.get("batch_id") == batch.get("batch_id") for row in ledger):
                state["version"] = int(state.get("version", 0)) + 1
                ledger.append(batch)
            result = {"ok": True, "applied": True, "state": state, "event_ledger": ledger}
        print(json.dumps({"request_id": request["request_id"], "ok": True, "result": result}), flush=True)
    """
    instance = GameInstance(game_key=("test", "group", "stdio"), run_id="run-stdio")
    ruleset_runtime.bind(instance, {
        "id": "external:stdio", "version": 1, "rule_id": "stdio",
        "content_version": "1", "state_schema_version": 1,
    })
    ledger = tmp_path / "receipts.json"
    first_transport = StdioRuntimeTransport(_child(child))
    first = RemoteRulesetRuntimeAdapter(
        "external:stdio", 1, RulesetCapabilities(), first_transport, ledger_path=ledger,
    )
    first.apply_event_batch(instance, {"batch_id": "b1"})
    assert ruleset_runtime.state(instance)["version"] == 1
    persisted = instance.to_dict()
    restored_instance = GameInstance.from_dict(persisted)
    first.close()

    second_transport = StdioRuntimeTransport(_child(child))
    second = RemoteRulesetRuntimeAdapter(
        "external:stdio", 1, RulesetCapabilities(), second_transport, ledger_path=ledger,
    )
    # The identical request is served by the durable receipt without touching
    # the replacement child process; a new batch then uses the persisted host
    # state/ledger and appends exactly once.
    second.apply_event_batch(restored_instance, {"batch_id": "b1"})
    second.apply_event_batch(restored_instance, {"batch_id": "b2"})
    assert ruleset_runtime.state(restored_instance)["version"] == 2
    assert [row["batch_id"] for row in ruleset_runtime.event_ledger(restored_instance)] == ["b1", "b2"]
    second.close()
