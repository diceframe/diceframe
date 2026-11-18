"""Agent 预算连续性与输出预算钳制。

回归覆盖供应商 max_tokens 钳制、有界请求历史、已计费响应连续处理，
以及硬预算触顶后不派发越界响应、不自动开启新一轮。
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.llm.client import LLMClient, ProviderConfig
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime, AgentTool
from src.webui.services.assistant_agent_framework import _bound_history


def test_bound_history_caps_old_messages_and_total():
    huge = [{"parts": [{"content": "汉" * 9000}]} for _ in range(14)]
    bounded = _bound_history(huge)
    total = len(json.dumps(bounded, ensure_ascii=False))
    assert total < 45000, total
    assert len(bounded) <= len(huge)
    recent = json.dumps(bounded[-1], ensure_ascii=False)
    assert len(recent) <= 6000


def _provider_app(calls: list[dict], *, reject_max_tokens_above: int | None = None):
    async def provider(request):
        body = await request.json()
        calls.append(body)
        sent = int(body.get("max_tokens") or 0)
        if reject_max_tokens_above is not None and sent > reject_max_tokens_above:
            return web.json_response({"error": {
                "message": f"Invalid max_tokens value, the valid range of max_tokens is [1, {reject_max_tokens_above}]",
                "type": "invalid_request_error",
                "param": "max_tokens",
                "code": "invalid_request_error",
            }}, status=400)
        return web.json_response({"choices": [{"message": {"content": "好的，已完成。"},
            "finish_reason": "stop"}], "usage": {"total_tokens": sent}})

    app = web.Application()
    app.router.add_post("/{path:.*}", provider)
    return app


@pytest.mark.asyncio
async def test_provider_max_tokens_range_error_is_clamped_and_retried():
    """超范围 max_tokens 触发 400 时按供应商上报的上限钳制重试，不再静默失败。"""
    calls: list[dict] = []
    app = _provider_app(calls, reject_max_tokens_above=393216)
    server = TestServer(app)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "test-model")], "test")
    try:
        response = await client.call("你好", "", max_tokens=25_600_000)
        assert "好的" in response.content
        assert calls[0]["max_tokens"] == 25_600_000
        assert calls[1]["max_tokens"] == 393216
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_agent_retains_every_response_across_the_former_soft_threshold(tmp_path):
    """A soft threshold must not discard a paid response or its tool call."""
    step_index = [0]
    usage_billed = [140_000, 90_000, 0]
    calls: list[dict] = []

    async def provider(request):
        body = await request.json()
        calls.append(body)
        step = step_index[0]
        step_index[0] += 1
        if step < 2:
            # 第一步超过旧软阈值但仍在任务预算内，响应必须正常处理。
            return web.json_response({
                "choices": [{
                    "message": {"content": None, "tool_calls": [{
                        "id": f"s{step}", "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"total_tokens": usage_billed[step]},
            })
        return web.json_response({"choices": [{"message": {"content": "任务完成。"},
            "finish_reason": "stop"}], "usage": {"total_tokens": usage_billed[step]}})

    app = web.Application()
    app.router.add_post("/{path:.*}", provider)
    server = TestServer(app)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "test-model")], "test")

    reads = []

    async def read(args, owner):
        reads.append(len(reads) + 1)
        return {"ok": True, "read_number": reads[-1], "resource": "huge " + "汉" * 20000}

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    agent = AgentRuntime(AgentDependencies(llm_client=lambda: client,
        configuration_error=lambda lang: None,
        tools=(AgentTool("read", "Read", empty, read),),
        storage_dir=tmp_path / "agent"))
    events: list[tuple] = []

    async def emit(event, payload):
        events.append((event, deepcopy(payload)))

    try:
        await agent.run("owner", [{"role": "user", "content": "读取并总结"}], "zh-CN", {}, "", emit)
    finally:
        await client.close()
        await server.close()

    deltas = [value.get("delta", "") for event, value in events if event == "delta"]
    assert any("任务完成" in text for text in deltas), deltas
    assert reads == [1, 2]
    histories = [json.loads(body["messages"][1]["content"])["agent_history"] for body in calls]
    returns = [part for message in histories[-1] for part in message["parts"]
               if part["part_kind"] == "tool-return"]
    assert len(returns) == 2
    assert all(any(part["part_kind"] == "tool-call" for part in message["parts"])
               for message in histories[-1] if message["kind"] == "response")
    # UI displays usage accumulated across the same run.
    sid = next(value["session_id"] for event, value in events if event == "session")
    snap = agent.snapshot("owner", sid)
    used = int((snap.get("task") or {}).get("used_tokens") or 0)
    assert used == 140_000 + 90_000, used
    assert (snap.get("task") or {}).get("token_budget") == 256_000
    # Two paid read requests, two executions, then one final answer.
    assert step_index[0] == 3
    assert snap["task"]["status"] == "completed"
    # 每轮发给模型的历史都有界：不随轮数线性膨胀。
    for body in calls[1:]:
        serialized = json.dumps(body, ensure_ascii=False)
        assert len(serialized) < 200_000, len(serialized)


def test_bound_history_keeps_the_newest_request():
    """review P1-1 回归：总量超顶只舍弃更早历史，最新请求绝不丢。"""
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "parts": [{"part_kind": "user-prompt" if i % 2 == 0 else "text",
                    "content": "汉" * 1500}]}
        for i in range(50)
    ]
    bounded = _bound_history(msgs)
    assert len(bounded) < len(msgs), "总量封顶必须生效"
    newest = bounded[-1]["parts"][-1]["content"]
    assert newest == "汉" * 1500, "最新的用户请求必须完整保留"
    # 直接丢弃最旧消息，不插合成条目（消息形状契约）。
    assert all("parts" in message for message in bounded)


@pytest.mark.asyncio
async def test_over_budget_response_is_billed_but_not_dispatched(tmp_path):
    """硬预算被单个响应越过：累计不重置，越界工具不派发。"""
    step_index = [0]
    usage_billed = [140_000, 130_000]
    calls: list[dict] = []

    async def provider(request):
        body = await request.json()
        calls.append(body)
        step = step_index[0]
        step_index[0] += 1
        if step < len(usage_billed):
            return web.json_response({
                "choices": [{
                    "message": {"content": None, "tool_calls": [{
                        "id": f"s{step}", "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"total_tokens": usage_billed[step]},
            })
        return web.json_response({"choices": [{"message": {"content": "不应到达"},
            "finish_reason": "stop"}], "usage": {"total_tokens": 1}})

    app = web.Application()
    app.router.add_post("/{path:.*}", provider)
    server = TestServer(app)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "test-model")], "test")

    reads = []

    async def read(args, owner):
        reads.append(True)
        return {"ok": True}

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    agent = AgentRuntime(AgentDependencies(llm_client=lambda: client,
        configuration_error=lambda lang: None,
        tools=(AgentTool("read", "Read", empty, read),),
        storage_dir=tmp_path / "agent"))
    events: list[tuple] = []

    async def emit(event, payload):
        events.append((event, deepcopy(payload)))

    try:
        await agent.run("owner", [{"role": "user", "content": "读取"}], "zh-CN", {}, "", emit)
    finally:
        await client.close()
        await server.close()

    sid = next(value["session_id"] for event, value in events if event == "session")
    task = agent.snapshot("owner", sid)["task"]
    assert task["status"] == "budget_reached" and task["budget_reason"] == "tokens"
    assert task["used_tokens"] == 140_000 + 130_000
    assert task["token_budget"] == 256_000
    deltas = [value.get("delta", "") for event, value in events if event == "delta"]
    assert reads == [True]  # The response exceeding the hard budget is not dispatched.
    assert step_index[0] == 2
    assert any("已用完" in text for text in deltas), deltas
