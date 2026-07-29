from __future__ import annotations

import json

import pytest

from src.webui.routes import bot
from src.webui.services import bot_extensions


class FakeApi:
    def __init__(self, asset_path=None):
        self.calls = []
        self.asset_path = asset_path

    def bot_extension_capabilities(self):
        return {"protocol_version": 1, "stages": ["before_message", "after_result", "render"], "extensions": 2}

    async def apply_bot_extensions(self, stage, payload):
        self.calls.append((stage, payload))
        return {
            "ok": True,
            "handled": True,
            "payload": payload,
            "outputs": [{"type": "text", "text": "handled"}],
            "applied": [{"plugin_id": "demo", "name": "command"}],
        }

    def bot_extension_asset_path(self, plugin_id, relative_path):
        self.calls.append((plugin_id, relative_path))
        if self.asset_path is None:
            raise KeyError("missing")
        return self.asset_path


class FakeRequest(dict):
    def __init__(self, api, *, body=None, match_info=None, plugin_identity=None):
        super().__init__()
        self.app = {"api": api}
        self._body = body
        self.match_info = match_info or {}
        if plugin_identity:
            self["plugin_authenticated"] = plugin_identity

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_bridge_extension_route_adds_trusted_caller_identity():
    api = FakeApi()
    request = FakeRequest(
        api,
        body={
            "stage": "before_message",
            "payload": {"platform": "qq", "kind": "command", "text": "/demo"},
        },
        plugin_identity={"plugin_id": "qq-napcat"},
    )

    response = await bot.api_apply_bridge_extensions(request)
    body = response_json(response)

    assert response.status == 200
    assert body["handled"] is True
    assert api.calls[0][0] == "before_message"
    assert api.calls[0][1]["_caller"] == {"plugin_id": "qq-napcat", "managed": True}


@pytest.mark.asyncio
async def test_bridge_extension_route_rejects_non_object_payload():
    response = await bot.api_apply_bridge_extensions(
        FakeRequest(FakeApi(), body={"stage": "render", "payload": []})
    )

    assert response.status == 400
    assert "payload" in response_json(response)["error"]


@pytest.mark.asyncio
async def test_bridge_extension_service_is_noop_without_plugin_host():
    class Api:
        _plugins = None

    payload = {"platform": "maibot", "kind": "text", "text": "hello"}
    result = await bot_extensions.apply(Api(), "render", payload)

    assert result == {
        "ok": True,
        "handled": False,
        "payload": payload,
        "outputs": [],
        "applied": [],
    }
