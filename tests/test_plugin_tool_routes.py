from __future__ import annotations

import json

import pytest

from src.webui.routes import plugins


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_plugin_tools(self):
        return {"ok": True, "tools": [{"plugin_id": "echo-tool", "name": "echo"}], "total": 1}

    async def invoke_plugin_tool(self, plugin_id, tool_name, arguments, context):
        self.calls.append((plugin_id, tool_name, arguments, context))
        return {"ok": True, "result": {"content": [{"type": "text", "text": arguments["text"]}]}}


class FakeRequest:
    def __init__(self, api, *, confirm=True, body=None):
        self.app = {"api": api}
        self.match_info = {"plugin_id": "echo-tool", "tool_name": "echo"}
        self.headers = {"X-TRPG-Confirm": "true"} if confirm else {}
        self._body = body if body is not None else {"arguments": {"text": "hello"}, "context": {}}

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_tool_route_lists_registered_tools():
    response = await plugins.api_plugin_tools(FakeRequest(FakeApi()))

    assert response.status == 200
    assert response_json(response)["tools"][0]["name"] == "echo"


@pytest.mark.asyncio
async def test_tool_invoke_requires_confirm_header():
    api = FakeApi()
    response = await plugins.api_plugin_tool_invoke(FakeRequest(api, confirm=False))

    assert response.status == 403
    assert api.calls == []


@pytest.mark.asyncio
async def test_tool_invoke_validates_and_delegates_json_objects():
    api = FakeApi()
    response = await plugins.api_plugin_tool_invoke(FakeRequest(api))

    assert response.status == 200
    assert api.calls == [("echo-tool", "echo", {"text": "hello"}, {})]
    assert response_json(response)["result"]["content"][0]["text"] == "hello"

    invalid = await plugins.api_plugin_tool_invoke(FakeRequest(api, body={"arguments": []}))
    assert invalid.status == 400
