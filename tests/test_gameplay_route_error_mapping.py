"""回合入口的 HTTP 边界语义：业务错误结构化透传，未知异常不再伪装成功。

回归背景（生产 2026-10）：独立检定模型配置失效时，``/action`` 返回
HTTP 200 + ``phase:error`` 的伪成功，``/advance`` 冒泡裸 500。服务层统一
返回结构化 TurnResult 后，路由必须原样透传其状态码，且未知异常也要给出
非 2xx 的 JSON 响应。
"""

from __future__ import annotations

import json

import pytest

from src.webui.routes.game_gameplay_routes import api_action, api_advance

_CHECK_MODEL_409 = {
    "payload": {
        "ok": False,
        "error_code": "CHECK_MODEL_NOT_CONFIGURED",
        "error": "独立检定模型未配置，请前往设置选择有效服务商和模型，或关闭独立检定模型",
        "advanced": False,
        "phase": "error",
    },
    "status": 409,
}


class FakeRequest:
    def __init__(self, api, body=None):
        self.match_info = {"game_key": "web|room|bot"}
        self.app = {"api": api}
        self.headers = {}
        self.method = "POST"
        self.query = {}
        self._body = body if body is not None else {}

    def get(self, key, default=None):
        return {"user_id": "gm"}.get(key, default)

    async def json(self):
        return self._body


def response_json(response) -> dict:
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_action_passes_through_structured_409_from_service():
    """服务层返回的业务错误（含 error_code）必须带着非 2xx 透传给客户端。"""

    class FakeAPI:
        async def submit_action(self, *args, **kwargs):
            return dict(_CHECK_MODEL_409)

    response = await api_action(FakeRequest(FakeAPI(), body={"text": "调查石门"}))

    assert response.status == 409
    payload = response_json(response)
    assert payload["error_code"] == "CHECK_MODEL_NOT_CONFIGURED"
    assert payload["advanced"] is False


@pytest.mark.asyncio
async def test_action_unknown_failure_returns_500_instead_of_pseudo_success():
    """未知编程错误返回 500 JSON，绝不再是 HTTP 200 + phase:error 伪成功。"""

    class FakeAPI:
        async def submit_action(self, *args, **kwargs):
            raise RuntimeError("boom")

    response = await api_action(FakeRequest(FakeAPI(), body={"text": "调查石门"}))

    assert response.status == 500
    payload = response_json(response)
    assert payload["ok"] is False
    assert payload["advanced"] is False
    assert payload["phase"] == "error"


@pytest.mark.asyncio
async def test_advance_passes_through_structured_409_from_service():
    class FakeAPI:
        async def advance_turn(self, *args, **kwargs):
            return dict(_CHECK_MODEL_409)

    response = await api_advance(FakeRequest(FakeAPI(), body={"force": True}))

    assert response.status == 409
    assert response_json(response)["error_code"] == "CHECK_MODEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_advance_unknown_failure_returns_json_500_not_bare_error():
    """/advance 的未知异常兜底为 JSON 500，不再冒泡成裸 500。"""

    class FakeAPI:
        async def advance_turn(self, *args, **kwargs):
            raise RuntimeError("boom")

    response = await api_advance(FakeRequest(FakeAPI(), body={}))

    assert response.status == 500
    payload = response_json(response)
    assert payload["ok"] is False
    assert payload["advanced"] is False
