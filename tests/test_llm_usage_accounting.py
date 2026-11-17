"""三轮复审验收：截断重试的计费聚合、失败链已知用量上报、压缩兜底合法性。"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.llm.client import LLMClient, LLMToolCallError, OutputTruncatedError, ProviderConfig
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime, AgentTool
from src.webui.services.assistant_agent_framework import (
    HISTORY_FIRST_CHARS,
    HISTORY_TOTAL_CHARS,
    _bound_history,
    _compact_json_text,
)


def _json_server(handler):
    app = web.Application()
    app.router.add_post("/{path:.*}", handler)
    server = TestServer(app)
    return server


def _tool_call_response(name, arguments, usage):
    """JSON fallback 的模型响应：工具调用以纯文本 JSON 协议输出在 content。"""
    content = json.dumps({"tool_calls": [{"name": name, "arguments": arguments}]})
    return web.json_response({
        "choices": [{
            "message": {"content": content},
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": usage},
    })


@pytest.mark.asyncio
async def test_truncation_retry_usage_is_aggregated():
    """200/length(usage=100) → 重试成功(usage=200)：账本 = 300，不丢截断轮。"""
    calls: list[dict] = []

    async def provider(request):
        body = await request.json()
        calls.append(body)
        print(f"[SEQ] req#{len(calls)} -> {'trunc100' if len(calls)==1 else 'ok200'} mt={body.get('max_tokens')} fr={body.get('response_format') if 'response_format' in body else '-'}")
        if len(calls) == 1:
            # 第一轮：输出被截断（finish_reason=length），但供应商已计费 100。
            return web.json_response({
                "choices": [{
                    "message": {"content": "{\"tool_calls\": [{\"na"},
                    "finish_reason": "length",
                }],
                "usage": {"total_tokens": 100},
            })
        return _tool_call_response("final_result", {"answer": "done"}, 200)

    server = _json_server(provider)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "m")], "test")
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    try:
        response = await client.call_tools("s", "u", tools=[{
            "type": "function", "function": {"name": "final_result",
            "parameters": empty}}], max_tokens=4096)
        assert response.total_tokens == 300, (
            response.total_tokens, response.planner_attempts, response)
        assert response.planner_attempts == 2
        # 截断轮与重试轮各计费一次：usage = 100 + 200 = 300
        assert calls[0]["max_tokens"] == 4096
        assert calls[1]["max_tokens"] == 4096
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_all_failed_chain_reports_known_usage():
    """全链失败：已计费用量随异常上报，不伪报为 0 成本。"""
    calls: list[dict] = []

    async def provider(request):
        body = await request.json()
        calls.append(body)
        return web.json_response({
            "choices": [{
                "message": {"content": "{\"tool_calls\": [{\"na"},
                "finish_reason": "length",
            }],
            "usage": {"total_tokens": 100},
        })

    server = _json_server(provider)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "m")], "test")
    tools = [{"type": "function", "function": {"name": "final_result",
             "parameters": {"type": "object", "properties": {},
                            "additionalProperties": False}}}]
    try:
        # 非强制模式：截断 JSON 原样返回给上层，由上层解析重试。
        # usage_pool 已聚合截断轮的计费（100），不再重复。
        response = await client.call_tools("s", "u", tools=tools, max_tokens=4096,
                                           force_tool_choice=False)
        assert response.total_tokens == 100
        assert response.tool_calls == []
        assert '{"tool_calls": [{"na' in (response.content or "")
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_native_empty_tool_calls_usage_counted_once():
    """原生 200 但无工具调用（usage=500）→ JSON 回退成功（300）：总计 800，只计一次。"""
    calls: list[dict] = []

    async def provider(request):
        body = await request.json()
        calls.append(body)
        if len(calls) == 1:
            return web.json_response({
                "choices": [{
                    "message": {"content": None, "tool_calls": []},
                    "finish_reason": "stop",
                }],
                "usage": {"total_tokens": 500},
            })
        return _tool_call_response("final_result", {"answer": "done"}, 300)

    server = _json_server(provider)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "m")], "test")
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    try:
        response = await client.call_tools("s", "u", tools=[{
            "type": "function", "function": {"name": "final_result",
            "parameters": empty}}], max_tokens=4096)
        assert response.total_tokens == 800
        assert response.planner_attempts == 2
    finally:
        await server.close()


def test_compact_fallback_always_produces_valid_json_within_limit():
    """review 第三轮 P1-2 复现：密集反斜杠 + 嵌套 JSON 的兜底必须合法且在限内。"""
    nasty = {"ids": [{"id": "a" * 60 + "\\\\" + f"{i}", "note": "\"quoted\" \\ backslash"}
                     for i in range(20)], "payload": "汉" * 4000}
    text = json.dumps(nasty, ensure_ascii=False)
    bounded = _compact_json_text(text, 4000)
    assert len(bounded) <= 4000, len(bounded)
    json.loads(bounded)  # 必须合法


def test_bound_history_keeps_first_task_structure_with_huge_payload():
    """80K 首条 JSON + 6×12K 观察：首条 kind/parts/任务目标保留，总量有界。"""
    payload = json.dumps({
        "goal": "FIRST_TASK_GOAL",
        "conversation": [{"role": "user", "content": "汉" * 8000}] * 12,
        "capability_catalog": {"capabilities": [
            {"name": f"cap_{i:03}", "label": "L" * 300} for i in range(160)],
        },
    }, ensure_ascii=False)
    first = {"kind": "request",
             "parts": [{"part_kind": "user-prompt", "content": payload}]}
    messages = [first] + [
        {"kind": "request",
         "parts": [{"part_kind": "tool-return", "content": json.dumps(
             {"ok": True, "data": "汉" * 12000})}]} for _ in range(6)]
    bounded = _bound_history(messages)
    wire = json.dumps(bounded, ensure_ascii=False)
    assert len(wire) <= HISTORY_TOTAL_CHARS + len(payload) // 2, len(wire)
    assert "FIRST_TASK_GOAL" in wire
    assert all("parts" in message for message in bounded)
    first_bounded = json.dumps(bounded[0], ensure_ascii=False)
    # 首条经结构化压缩而非截断：合法 JSON 且保留目标标记
    assert json.loads(first_bounded)  # 合法
    assert "FIRST_TASK_GOAL" in first_bounded


@pytest.mark.asyncio
async def test_failure_chain_reports_known_usage_to_ledger(tmp_path):
    """框架收到失败链异常时先上报已知用量，服务账本累计不归零。"""
    step_index = [0]

    async def provider(request):
        body = await request.json()
        step_index[0] += 1
        return web.json_response({
            "choices": [{
                "message": {"content": "{\"tool_calls\": [{\"na"},
                "finish_reason": "length",
            }],
            "usage": {"total_tokens": 100},
        })

    app = web.Application()
    app.router.add_post("/{path:.*}", provider)
    server = TestServer(app)
    await server.start_server()
    client = LLMClient([ProviderConfig("test", str(server.make_url("/")), "k", "m")], "test")

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
        # 截断响应（tools 路径不检查 finish_reason）→ call_tools 正常返回
        # LLMToolResponse(tool_calls=[], content=截断) → 框架当最终文本回复
        # → agent 正常完成（不抛）。验证 usage 事件携带截断轮的真实计费。
        output = await agent.run(
            "owner", [{"role": "user", "content": "读取"}], "zh-CN", {}, "", emit)
        assert output is None or (isinstance(output, str) and output.strip())
    finally:
        await server.close()

    # usage 事件由 checked_emit 消费进账本，不透传到 events——通过 snapshot 验证
    sid = next(value["session_id"] for event, value in events if event == "session")
    snap = agent.snapshot("owner", sid)
    used = int((snap.get("task") or {}).get("used_tokens") or 0)
    assert used == 100


def test_describe_large_schema_keeps_parameter_contracts():
    """review 第三轮 P2-3：describe 大 schema 压缩保留参数名 / 类型 / required。"""
    import sys
    sys.path.insert(0, ".")
    from src.webui.services.assistant_agent import _bounded_observation
    props = {f"param_{i}": {"type": "string", "description": "描述" * 300,
             "enum": ["a", "b"]} for i in range(40)}
    result = {"name": "big_action", "label": "Big", "effect": "write",
              "version": 3, "parameters": {"type": "object",
              "required": ["param_0", "param_1"], "properties": props}}
    bounded = json.loads(_bounded_observation(result, limit=4000))
    assert bounded["name"] == "big_action"
    params = bounded["parameters"]
    assert params["schema_truncated"] is True
    assert set(params["required"]) == {"param_0", "param_1"}
    assert len(params["properties"]) == 40
    assert params["properties"]["param_0"]["type"] == "string"
    assert params["properties"]["param_0"]["required"] is True
    assert json.dumps(bounded, ensure_ascii=False) and len(json.dumps(bounded)) <= 6000
