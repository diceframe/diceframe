import json
import sys
import textwrap

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
