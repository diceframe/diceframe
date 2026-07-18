from __future__ import annotations

import io
import json

from src.plugin_sdk import ToolRuntime


def test_tool_runtime_initializes_and_dispatches_calls():
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocol_version": 1}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tool.call", "params": {
            "name": "sum", "arguments": {"a": 2, "b": 3}, "context": {"game_key": "demo"},
        }}),
    ]) + "\n"
    output = io.StringIO()
    runtime = ToolRuntime(stdin=io.StringIO(requests), stdout=output)

    @runtime.tool(
        name="sum",
        title="Sum",
        description="Adds two numbers.",
        input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
    )
    def add(arguments, context):
        return {"value": arguments["a"] + arguments["b"], "game_key": context["game_key"]}

    runtime.run()
    responses = [json.loads(line) for line in output.getvalue().splitlines()]

    assert responses[0]["result"]["tools"][0]["name"] == "sum"
    assert responses[1]["result"] == {"value": 5, "game_key": "demo"}
