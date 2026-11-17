"""Agent 预算连续性与输出预算钳制。

三类用户可见故障的回归：供应商拒绝超范围 max_tokens 导致助手静默失败；
对话历史无界重发导致预算几轮烧光（0 步 40 万 token）；预算触顶硬停要求
手动点「继续任务」。
"""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.llm.client import LLMClient, ProviderConfig
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime, AgentTool
from src.webui.services.assistant_agent_framework import (
    MAX_AUTO_CONTINUES,
    _bound_history,
)


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
        await server.close()


@pytest.mark.asyncio
async def test_agent_history_stays_bounded_and_budget_auto_continues(tmp_path):
    """历史有界化 + 预算触顶自动续段：不再要求手动点「继续任务」。"""
    step_index = [0]
    usage_billed = [140_000, 90_000, 0]
    calls: list[dict] = []

    async def provider(request):
        body = await request.json()
        calls.append(body)
        step = step_index[0]
        step_index[0] += 1
        if step < 2:
            # 第一步烧掉巨额输入触发软阈值（总预算一半）→ 自动续段；
            # 续段后额度从剩余里扣（总预算不放大）。
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

    async def read(args, owner):
        return {"ok": True, "resource": "huge " + "汉" * 20000}

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
        await server.close()

    deltas = [value.get("delta", "") for event, value in events if event == "delta"]
    assert any("整理上下文并继续" in text for text in deltas), deltas
    # 计数永不归零：UI 显示跨段累计（补充设计 §2）。
    sid = next(value["session_id"] for event, value in events if event == "session")
    snap = agent.snapshot("owner", sid)
    used = int((snap.get("task") or {}).get("used_tokens") or 0)
    assert used == 140_000 + 90_000, used
    assert (snap.get("task") or {}).get("token_budget") == 256_000
    # 续段不重做已完成的工具调用（补充设计 §3）：3 次模型请求，read 只执行 1 次。
    assert step_index[0] == 3
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
async def test_budget_pause_when_remaining_is_insufficient(tmp_path):
    """续段后剩余额度不足以完成 → 明确暂停，累计不重置（补充设计 §3.7）。"""
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

    async def read(args, owner):
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
        await server.close()

    sid = next(value["session_id"] for event, value in events if event == "session")
    task = agent.snapshot("owner", sid)["task"]
    assert task["status"] == "budget_reached" and task["budget_reason"] == "tokens"
    assert task["used_tokens"] == 140_000 + 130_000
    assert task["token_budget"] == 256_000
    deltas = [value.get("delta", "") for event, value in events if event == "delta"]
    assert any("整理上下文并继续" in text for text in deltas)
    assert any("已用完" in text for text in deltas), deltas
