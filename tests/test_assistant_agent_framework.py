"""Product contracts exercised by the real Pydantic AI executor."""
from copy import deepcopy

import pytest

from src.llm.client import LLMToolResponse
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime, AgentTool


class Provider:
    def __init__(self, *calls):
        self.calls = list(calls)
        self.requests = []

    async def call_tools(self, system, user, **kwargs):
        self.requests.append((system, user, kwargs))
        return LLMToolResponse([self.calls.pop(0)], 0, "test")


def tool(handler, mutation=False):
    return AgentTool("change", "Change", {"type": "object", "properties": {
        "value": {"type": "string", "minLength": 1, "maxLength": 50}},
        "required": ["value"], "additionalProperties": False}, handler, mutation=mutation)


async def execute(tmp_path, provider, tools=(), **limits):
    agent = AgentRuntime(AgentDependencies(lambda: provider, lambda lang: None,
        tuple(tools), tmp_path / "agent", **limits))
    events = []
    async def emit(event, data):
        events.append((event, deepcopy(data)))
    await agent.run("owner", [{"role": "user", "content": "help me"}], "en", {}, "", emit)
    return agent, events


@pytest.mark.asyncio
async def test_final_answer_is_a_typed_framework_output_not_an_empty_argument_wrapper(tmp_path):
    provider = Provider({"name": "final_result", "arguments": {"answer": "DF Agent is ready."}})
    _, events = await execute(tmp_path, provider)
    assert ("delta", {"delta": "DF Agent is ready."}) in events
    schemas = provider.requests[0][2]["tools"]
    assert all(item["function"]["name"] != "agent_step" for item in schemas)
    assert provider.requests[0][2]["force_tool_choice"] is False


@pytest.mark.asyncio
async def test_default_task_budget_allows_repeated_tool_context_without_enlarging_replies(tmp_path):
    calls = []
    async def read(args, owner):
        calls.append(args['value'])
        return {'ok': True}
    class ContextHeavyProvider:
        def __init__(self):
            self.requests = []
        async def call_tools(self, system, user, **kwargs):
            self.requests.append(kwargs)
            call = ({'name': 'change', 'arguments': {'value': 'read'}} if len(self.requests) <= 10
                    else {'name': 'final_result', 'arguments': {'answer': 'Done after ten tool reads'}})
            return LLMToolResponse([call], 8000, 'test')
    provider = ContextHeavyProvider()
    agent, events = await execute(tmp_path, provider, [tool(read)])
    assert len(calls) == 10
    assert ('delta', {'delta': 'Done after ten tool reads'}) in events
    assert all(request['max_tokens'] == 4096 for request in provider.requests)
    sid = next(value['session_id'] for name, value in events if name == 'session')
    assert agent.snapshot('owner', sid)['task']['token_budget'] == 256000


@pytest.mark.asyncio
async def test_configured_task_budget_is_independent_of_each_reply(tmp_path):
    class ProviderWithUsage:
        async def call_tools(self, *args, **kwargs):
            assert kwargs['max_tokens'] == 512
            return LLMToolResponse([{'name': 'final_result', 'arguments': {'answer': 'done'}}], 8000, 'test')
    agent, events = await execute(tmp_path, ProviderWithUsage(), max_tokens=512, task_max_tokens=16000)
    sid = next(value['session_id'] for name, value in events if name == 'session')
    assert agent.snapshot('owner', sid)['task']['status'] == 'completed'
    assert agent.snapshot('owner', sid)['task']['token_budget'] == 16000


@pytest.mark.asyncio
async def test_tool_loop_still_stops_at_request_budget_with_specific_reason(tmp_path):
    calls = []
    async def read(args, owner):
        calls.append(args['value'])
        return {'ok': True}
    class LoopingProvider:
        async def call_tools(self, *args, **kwargs):
            return LLMToolResponse([{'name': 'change', 'arguments': {'value': 'read'}}], 1000, 'test')
    agent, events = await execute(tmp_path, LoopingProvider(), [tool(read)])
    sid = next(value['session_id'] for name, value in events if name == 'session')
    task = agent.snapshot('owner', sid)['task']
    assert len(calls) == 24
    assert task['status'] == 'budget_reached' and task['budget_reason'] == 'steps'
    assert task['used_tokens'] == 24000


@pytest.mark.asyncio
async def test_reported_token_budget_stops_before_dispatch_and_retains_usage(tmp_path):
    writes = []

    async def handler(args, owner):
        writes.append(args)
        return {"ok": True}

    class ExpensiveProvider:
        async def call_tools(self, *args, **kwargs):
            return LLMToolResponse([{"name": "change", "arguments": {"value": "must-not-run"}}],
                                  300000, "test")

    agent, events = await execute(tmp_path, ExpensiveProvider(), [tool(handler, mutation=True)])
    sid = next(data["session_id"] for event, data in events if event == "session")
    snapshot = agent.snapshot("owner", sid)
    # 总预算被单响应击穿时在派发前硬停：mutation 不执行、pending 不产生；
    # 已计费用量跨段累计完整保留（安全防线，补充设计 §7.8）。
    assert writes == [] and snapshot["pending"] is None
    assert snapshot["task"]["status"] == "budget_reached"
    assert snapshot["task"]["used_tokens"] == 300000
    assert snapshot["task"]["token_budget"] == 256000
    assert snapshot['task']['budget_reason'] == 'tokens'
    assert any(name == "task" and value.get("used_tokens") == 300000 for name, value in events)


@pytest.mark.asyncio
async def test_framework_corrects_invalid_arguments_before_any_business_execution(tmp_path):
    writes = []
    async def handler(args, owner):
        writes.append((args, owner))
        return {"ok": True}
    provider = Provider(
        {"name": "change", "arguments": {"value": True, "admin": True}},
        {"name": "change", "arguments": {"value": "valid"}},
    )
    agent, events = await execute(tmp_path, provider, [tool(handler, mutation=True)])
    assert writes == []
    assert len(provider.requests) == 2
    assert "retry-prompt" in provider.requests[-1][1]
    plan = next(data for event, data in events if event == "approval")
    sid = next(data["session_id"] for event, data in events if event == "session")
    await agent.confirm("owner", sid, plan["approval_id"], plan["plan_digest"], True)
    assert writes == [({"value": "valid"}, "owner")]


@pytest.mark.asyncio
async def test_framework_tool_observation_and_validation_retry_are_in_next_model_history(tmp_path):
    seen = []
    async def handler(args, owner):
        seen.append(args)
        return {"ok": True, "resource": "catalog-result", "api_key": "must-not-leak"}
    provider = Provider(
        {"name": "change", "arguments": {"value": "read"}},
        {"name": "final_result", "arguments": {"answer": ""}},
        {"name": "final_result", "arguments": {"answer": "Done"}},
    )
    _, events = await execute(tmp_path, provider, [tool(handler)])
    assert seen == [{"value": "read"}]
    assert "catalog-result" in provider.requests[-1][1]
    assert "must-not-leak" not in provider.requests[-1][1]
    assert "retry-prompt" in provider.requests[-1][1]
    assert ("delta", {"delta": "Done"}) in events


@pytest.mark.asyncio
async def test_unknown_tool_is_corrected_by_framework_without_granting_capabilities(tmp_path):
    provider = Provider(
        {"name": "shell", "arguments": {"command": "untrusted"}},
        {"name": "final_result", "arguments": {"answer": "Use the supported application tools."}},
    )
    _, events = await execute(tmp_path, provider)
    assert "retry-prompt" in provider.requests[-1][1]
    assert not any(event in {"approval", "tool_start"} for event, _ in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ['[]', 'invalid-json', {"ctx": "spoofed", "value": "wrong"}])
async def test_invalid_argument_shapes_are_corrected_without_dispatch(tmp_path, arguments):
    writes = []
    async def handler(args, owner):
        writes.append(args)
        return {"ok": True}
    provider = Provider(
        {"name": "change", "arguments": arguments},
        {"name": "final_result", "arguments": {"answer": "Please choose a supported action."}},
    )
    _, events = await execute(tmp_path, provider, [tool(handler, mutation=True)])
    assert writes == []
    assert not any(event == "approval" for event, _ in events)
    assert "retry-prompt" in provider.requests[-1][1]


@pytest.mark.asyncio
async def test_provider_balance_error_is_actionable_and_excludes_raw_provider_details(tmp_path):
    from src.llm.client import LLMToolCallError
    class UnavailableProvider:
        async def call_tools(self, *args, **kwargs):
            raise LLMToolCallError("private-provider-detail", frozenset({402}))
    agent = AgentRuntime(AgentDependencies(lambda: UnavailableProvider(), lambda lang: None, (), tmp_path / "agent"))
    events = []
    async def emit(name, value):
        events.append((name, deepcopy(value)))
    with pytest.raises(ValueError) as raised:
        await agent.run("owner", [{"role": "user", "content": "help"}], "en", {}, "", emit)
    assert "402" in str(raised.value) and "Settings" in str(raised.value)
    assert "private-provider-detail" not in str(raised.value)
    assert raised.value.code == "MODEL_BALANCE_INSUFFICIENT"
    sid = next(data["session_id"] for name, data in events if name == "session")
    assert agent.snapshot("owner", sid)["task"]["status"] == "failed"


@pytest.mark.asyncio
async def test_framework_runs_batched_read_tools_sequentially_before_one_confirmation(tmp_path):
    calls = []
    writes = []

    async def read(args, owner):
        calls.append(args["value"])
        return {"ok": True, "resource": args["value"]}

    async def write(args, owner):
        writes.append(args)
        return {"ok": True}

    class BatchedProvider(Provider):
        async def call_tools(self, system, user, **kwargs):
            if not self.requests:
                self.requests.append((system, user, kwargs))
                return LLMToolResponse([
                    {"name": "change", "arguments": {"value": "worlds"}},
                    {"name": "change", "arguments": {"value": "adventures"}},
                ], 0, "test")
            return await super().call_tools(system, user, **kwargs)

    provider = BatchedProvider({"name": "write", "arguments": {"value": "reviewed"}})
    write_tool = AgentTool("write", "Write", tool(write).schema, write, mutation=True)
    _, events = await execute(tmp_path, provider, [tool(read), write_tool])
    assert calls == ["worlds", "adventures"]
    assert writes == []
    assert len(provider.requests) == 2
    assert all(value in provider.requests[-1][1] for value in calls)
    assert next(data for event, data in events if event == "approval")["tool"] == "write"


@pytest.mark.asyncio
async def test_mixed_batch_retries_with_precise_feedback_and_never_dispatches(tmp_path):
    dispatched = []

    async def handler(args, owner):
        dispatched.append(args)
        return {"ok": True}

    class MixedProvider(Provider):
        async def call_tools(self, system, user, **kwargs):
            if not self.requests:
                self.requests.append((system, user, kwargs))
                return LLMToolResponse([
                    {"name": "read", "arguments": {"value": "catalog"}},
                    {"name": "change", "arguments": {"value": "must-not-run"}},
                ], 0, "test")
            assert "one function" in system and "approval" in system
            return await super().call_tools(system, user, **kwargs)

    provider = MixedProvider({"name": "final_result", "arguments": {"answer": "Please choose the adventure."}})
    read_tool = AgentTool("read", "Read", tool(handler).schema, handler)
    _, events = await execute(tmp_path, provider, [read_tool, tool(handler, mutation=True)])
    assert not dispatched
    assert not any(event == "approval" for event, _ in events)
    assert ("delta", {"delta": "Please choose the adventure."}) in events


@pytest.mark.asyncio
async def test_retry_exhaustion_has_actionable_error_and_keeps_reviewed_draft(tmp_path):
    class InvalidProvider:
        def __init__(self):
            self.count = 0

        async def call_tools(self, *args, **kwargs):
            self.count += 1
            if self.count == 1:
                return LLMToolResponse([{"name": "draft", "arguments": {}}], 0, "test")
            return LLMToolResponse([], 0, "test")

    async def draft(args, owner):
        return {"ok": True, "kind": "adventure", "draft": {"items": []}, "blockers": [], "plan_digest": "sha256:test"}

    provider = InvalidProvider()
    draft_tool = AgentTool("draft", "Draft", {"type": "object", "properties": {}, "additionalProperties": False}, draft, creates_draft=True)
    agent = AgentRuntime(AgentDependencies(lambda: provider, lambda lang: None, (draft_tool,), tmp_path / "agent"))
    events = []

    async def emit(event, value):
        events.append((event, deepcopy(value)))

    with pytest.raises(ValueError) as raised:
        await agent.run("owner", [{"role": "user", "content": "随便想一个冒险包"}], "zh-CN", {}, "", emit)
    assert raised.value.code == "MODEL_RESPONSE_INVALID"
    assert "工具调用" in str(raised.value) and "草稿" in str(raised.value)
    assert "invalid_provider_response" not in str(raised.value)
    sid = next(data["session_id"] for event, data in events if event == "session")
    snapshot = agent.snapshot("owner", sid)
    assert snapshot["status"] == "idle" and len(snapshot["drafts"]) == 1
    assert snapshot["pending"] is None
