"""Pydantic AI executor using DiceFrame's configured provider transport.

The upstream Agent owns the loop, validation feedback, retry budgets and
deferred calls. This adapter only translates the existing provider contract;
it does not discover credentials or grant application permissions.
"""
from __future__ import annotations

import json
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.usage import RequestUsage, UsageLimits

from src.llm.client import LLMToolCallError
from src.assistant_agent_limits import DEFAULT_TASK_MAX_TOKENS
from src.localization.messages import t


class AgentProviderUnavailable(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AgentModelResponseInvalid(ValueError):
    code = "MODEL_RESPONSE_INVALID"

    def __init__(self, language: str):
        super().__init__(
            "模型连续返回无法执行的工具调用，自动纠正后仍未通过校验。已有草稿已保留；可以继续当前任务，或在设置中切换支持工具调用的模型。"
            if language.startswith("zh") else
            "The model repeatedly returned invalid tool calls after automatic correction. Existing drafts are retained. Continue the task or choose a model with tool calling support in Settings.")


def provider_unavailable(error: LLMToolCallError, language: str) -> AgentProviderUnavailable:
    zh = language.startswith("zh")
    if 402 in error.status_codes:
        return AgentProviderUnavailable("MODEL_BALANCE_INSUFFICIENT",
            t("assistant_agent_framework.provider_insufficient_balance") if zh else
            "The model provider reported insufficient balance (402). Check the provider or choose an available model in Settings.")
    if error.status_codes & {401, 403}:
        return AgentProviderUnavailable("MODEL_ACCESS_DENIED",
            t("assistant_agent_framework.provider_access_denied") if zh else
            "The model provider denied access. Check provider credentials and model permissions in Settings.")
    if 429 in error.status_codes:
        return AgentProviderUnavailable("MODEL_RATE_LIMITED",
            t("assistant_agent_framework.provider_rate_limited") if zh else
            "The model provider is limiting requests (429). Retry later or choose another model in Settings.")
    return AgentProviderUnavailable("MODEL_UNAVAILABLE",
        t("assistant_agent_framework.provider_temporarily_unavailable") if zh else
        "The model provider is unavailable. Check the network or provider settings and retry.")


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=24000)]


class DiceFrameModel(Model):
    def __init__(self, client, emit: Callable, language: str, max_tokens: int, max_steps: int):
        super().__init__()
        self.client = client
        self.emit = emit
        self.language = language
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.response_correction = ""

    @property
    def model_name(self) -> str:
        return "diceframe-configured-provider"

    @property
    def system(self) -> str:
        return "diceframe"

    async def request(self, messages, model_settings, model_request_parameters):
        await self.emit("status", {"message": t("assistant_agent_framework.planning_next_step") if self.language.startswith("zh") else "Planning the next step…"})
        parameters = model_request_parameters
        definitions = [*parameters.output_tools, *parameters.function_tools]
        schemas = [{"type": "function", "function": {
            "name": tool.name, "description": tool.description or "",
            "parameters": tool.parameters_json_schema,
        }} for tool in definitions]
        # Keep the complete framework transcript, including correlated tool
        # observations and retry prompts. Provider failover stays in LLMClient.
        history = ModelMessagesTypeAdapter.dump_python(messages, mode="json", exclude_none=True)
        instructions = "\n\n".join(part.content for message in messages
            for part in message.parts if part.part_kind == "system-prompt")
        if parameters.instruction_parts:
            instructions += "\n\n" + "\n".join(part.content for part in parameters.instruction_parts)
        if self.response_correction:
            # Only our fixed protocol explanation enters the next request;
            # never echo malformed provider arguments into instructions.
            instructions += "\n\nCorrection for the previous response: " + self.response_correction
        try:
            response = await self.client.call_tools(instructions,
                json.dumps({"agent_history": history}, ensure_ascii=False),
                tools=schemas, max_tokens=self.max_tokens, force_tool_choice=False)
        except LLMToolCallError as exc:
            raise provider_unavailable(exc, self.language) from exc
        calls = getattr(response, "tool_calls", None)
        reported_tokens = max(0, int(getattr(response, 'total_tokens', 0) or 0))
        await self.emit('usage', {'total_tokens': reported_tokens})
        text = getattr(response, "content", "")
        parts = []
        self.response_correction = "Return one registered function call with JSON object arguments, or a nonempty final answer."
        if isinstance(calls, list) and 0 < len(calls) <= self.max_steps:
            candidates = []
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                    break
                args = call.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (ValueError, TypeError):
                        args = None
                if not isinstance(args, dict):
                    break
                candidates.append(ToolCallPart(call["name"], args))
            if len(candidates) == len(calls):
                # The framework executes our sequential read/draft tools in
                # order. Never mix a final answer or deferred write into a
                # batch, which could bypass the single approval contract.
                immediate = {tool.name for tool in parameters.function_tools if tool.kind == "function"}
                if len(candidates) == 1 or all(call.tool_name in immediate for call in candidates):
                    parts = candidates
                else:
                    self.response_correction = "Return one function at a time when an approval or final answer is needed. Wait for tool results before saving a draft."
        elif calls == [] and isinstance(text, str) and text.strip():
            parts.append(TextPart(text))
        if not parts:
            # Let upstream own retry limits. The next request also receives
            # the actual format correction instead of only an unknown name.
            parts.append(ToolCallPart("invalid_provider_response", {
                "error": self.response_correction}))
        else:
            self.response_correction = ""
        return ModelResponse(parts, model_name=self.model_name,
            usage=RequestUsage(output_tokens=reported_tokens))


async def execute_agent(client, tools, instructions: str, payload: dict, emit: Callable,
                        language: str, max_tokens: int, max_steps: int,
                        *, task_max_tokens: int = DEFAULT_TASK_MAX_TOKENS):
    agent = Agent(DiceFrameModel(client, emit, language, max_tokens, max_steps),
        output_type=[FinalAnswer, str, DeferredToolRequests], tools=tools,
        instructions=instructions, retries=3, name="DF Agent")

    @agent.output_validator
    def validate_output(ctx, output):
        if isinstance(output, str) and (not output.strip() or len(output) > 24000):
            raise ModelRetry("Return a nonempty final answer of at most 24000 characters.")
        return output

    try:
        result = await agent.run(json.dumps(payload, ensure_ascii=False),
            usage_limits=UsageLimits(request_limit=max_steps, tool_calls_limit=max_steps,
                                     total_tokens_limit=task_max_tokens), infer_name=False)
    except UnexpectedModelBehavior as exc:
        raise AgentModelResponseInvalid(language) from exc
    return result.output


__all__ = ["DeferredToolRequests", "FinalAnswer", "ModelRetry", "UsageLimitExceeded", "execute_agent"]
