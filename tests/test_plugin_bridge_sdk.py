from __future__ import annotations

import io
import json

from src.plugin_sdk import BridgeExtensionRuntime


def test_bridge_runtime_initializes_and_dispatches_hooks():
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocol_version": 1}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "bridge.apply", "params": {
            "name": "hello",
            "stage": "before_message",
            "payload": {"text": "/hello"},
        }}),
    ]) + "\n"
    output = io.StringIO()
    runtime = BridgeExtensionRuntime(stdin=io.StringIO(requests), stdout=output)

    @runtime.extension(
        name="hello",
        title="Hello",
        description="Handles one command.",
        stages=["before_message"],
        priority=20,
    )
    def hello(_stage, payload):
        return {
            "handled": payload.get("text") == "/hello",
            "outputs": [{"type": "text", "text": "hello"}],
        }

    runtime.run()
    responses = [json.loads(line) for line in output.getvalue().splitlines()]

    assert responses[0]["result"]["bridge_extensions"][0]["name"] == "hello"
    assert responses[0]["result"]["bridge_extensions"][0]["priority"] == 20
    assert responses[1]["result"]["handled"] is True
    assert responses[1]["result"]["outputs"][0]["text"] == "hello"
