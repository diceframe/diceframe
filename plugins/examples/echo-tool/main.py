from __future__ import annotations

import os

from src.plugin_sdk import ToolRuntime

runtime = ToolRuntime()


@runtime.tool(
    name="echo",
    title="Echo Text",
    description="Returns the supplied text with the configured prefix.",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to return."}
        },
        "required": ["text"],
        "additionalProperties": False,
    },
)
def echo(arguments: dict, context: dict) -> dict:
    text = str(arguments.get("text") or "")
    prefix = os.getenv("ECHO_TOOL_PREFIX", "Echo:").strip()
    rendered = f"{prefix} {text}" if prefix else text
    return {
        "content": [{"type": "text", "text": rendered}],
        "data": {"length": len(text)},
    }


if __name__ == "__main__":
    runtime.run()
