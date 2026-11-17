import asyncio
import json
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.llm.client import LLMClient, ProviderConfig

import pytest

calls = []


async def provider(request):
    body = await request.json()
    calls.append({"tools": "tools" in body, "max_tokens": body.get("max_tokens")})
    if len(calls) == 1:
        return web.json_response({
            "choices": [{
                "message": {"content": "{\"tool_calls\": [{\"na"},
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 100},
        })
    content = json.dumps({"tool_calls": [
        {"name": "final_result", "arguments": {"answer": "done"}}]})
    return web.json_response({
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 200},
    })


@pytest.mark.asyncio
async def test_repro_direct_in_pytest(tmp_path):
    app = web.Application()
    app.router.add_post("/{p:.*}", provider)
    server = TestServer(app)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "m")], "test")
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    response = await client.call_tools(
        "s", "u",
        tools=[{"type": "function", "function": {"name": "final_result", "parameters": empty}}],
        max_tokens=4096)
    await server.close()
    print("TOTAL:", response.total_tokens, "PLANNER:", response.planner_attempts)
    print("CALLS:", calls)
    assert response.total_tokens == 300, response.total_tokens
