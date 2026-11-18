"""Pydantic AI executor using DiceFrame's configured provider transport.

The upstream Agent owns the loop, validation feedback, retry budgets and
deferred calls. This adapter only translates the existing provider contract;
it does not discover credentials or grant application permissions.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any, Callable

from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.usage import RequestUsage, UsageLimits

from src.llm.client import LLMToolCallError
from src.llm.tool_usage import run_id as usage_run_id
from src.assistant_agent_limits import DEFAULT_TASK_MAX_TOKENS
from src.localization.messages import t
from src.webui.services.assistant_creator_reads import FILE_TOOL, READ_TOOLS, preserve_source_observations

usage_logger = logging.getLogger('trpg.llm_usage')


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
            t("webui.services.assistant_agent_framework.provider_insufficient_balance") if zh else
            "The model provider reported insufficient balance (402). Check the provider or choose an available model in Settings.")
    if error.status_codes & {401, 403}:
        return AgentProviderUnavailable("MODEL_ACCESS_DENIED",
            t("webui.services.assistant_agent_framework.provider_access_denied") if zh else
            "The model provider denied access. Check provider credentials and model permissions in Settings.")
    if 429 in error.status_codes:
        return AgentProviderUnavailable("MODEL_RATE_LIMITED",
            t("webui.services.assistant_agent_framework.provider_rate_limited") if zh else
            "The model provider is limiting requests (429). Retry later or choose another model in Settings.")
    return AgentProviderUnavailable("MODEL_UNAVAILABLE",
        t("webui.services.assistant_agent_framework.provider_temporarily_unavailable") if zh else
        "The model provider is unavailable. Check the network or provider settings and retry.")


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=24000)]


# 每轮请求会把对话历史整包重发；不做有界化时输入随轮数线性膨胀（旧消息里
# 一条 18K 字符的工具观察 × N 轮就能烧掉几十万 token）。最近几条保留较大
# 窗口保证连贯，更早的只留要点；总量再封一道顶。
HISTORY_RECENT_MESSAGES = 6
HISTORY_RECENT_CHARS = 4000
HISTORY_OLDER_CHARS = 1200
HISTORY_FIRST_CHARS = 12000
# 总量上限按可严格执行的口径设定：首条（结构化压缩后 ≤12K）+ 最近窗口
# （每条压缩后 ≤4K × 6）+ 中间回收余量；所有消息经压缩后均有单条上限，
# 因此该总量可被真正执行（review 第二轮 P1-2）。
HISTORY_TOTAL_CHARS = 60000
# 观察结果进入历史的单条上限：模型不需要完整原文也能继续工作。
OBSERVATION_CHARS = 6000


# 可解析 JSON 的字符串不做「合法即整保」：那会让 40K 总量彻底失效
# （review P1-2 复现：6 条 12K 观察输出 71K）。改为解析后结构化压缩——
# 保留身份 / 状态 / 引用键与条目名，重序列化，保证合法且有界。
_JSON_KEEP_KEYS = ("ok", "error", "code", "id", "name", "label", "draft_id",
                   "approval_id", "source_id", "revision", "next_action",
                   "total", "offset", "has_more", "returned_count",
                   "omitted_count", "blocked", "status", "kind", "version")


def _compact_json_text(value: str, limit: int) -> str:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return value[:limit] + "\u2026[截断]"
    if not isinstance(data, (dict, list)):
        return value[:limit] + "\u2026[截断]"

    def compact(node: Any, key: str = "", strict: bool = False) -> Any:
        """两段式：宽松（保留短的非白名单值）→ 严格（只留白名单键）。

        兜底永远是合法 JSON（truncated 包装），绝不产生半截字符串——
        否则下游解析器与模型视图都会损坏（review 第二轮复现）。
        """
        if isinstance(node, dict):
            kept = {}
            for key_, item in node.items():
                if key_ in _JSON_KEEP_KEYS:
                    kept[key_] = compact(item, key_, strict)
                elif isinstance(item, (dict, list)):
                    kept[key_] = compact(item, key_, strict)
                elif isinstance(item, str):
                    cap = 160 if not strict else 0
                    kept[key_] = item if len(item) <= cap else item[:cap] + "\u2026"
                elif not strict:
                    kept[key_] = item
                # strict：非白名单标量直接丢弃
            return kept
        if isinstance(node, list):
            kept = [compact(item, key, strict) for item in node]
            if len(kept) > 20:
                omitted = len(kept) - 20
                kept = kept[:20]
                if kept and isinstance(kept[0], dict):
                    kept[0] = {**kept[0], "omitted_count": omitted}
            return kept
        return node

    shrunk = compact(data)
    text = json.dumps(shrunk, ensure_ascii=False)
    if len(text) <= limit:
        return text
    shrunk = compact(data, strict=True)
    text = json.dumps(shrunk, ensure_ascii=False)
    if len(text) <= limit:
        return text
    # 兜底：递减 preview 长度直到整包合法且在限内（review 第三轮 P1-2——
    # 对已序列化 JSON 直接 [:limit] 截断可能切断转义序列，产生非法 JSON）。
    preview_len = max(200, limit - 200)
    while preview_len >= 50:
        candidate = json.dumps({"truncated": True, "preview": text[:preview_len]},
                               ensure_ascii=False)
        if len(candidate) <= limit:
            json.loads(candidate)  # 合法性验证；不过则继续收缩
            return candidate
        preview_len //= 2
    return json.dumps({"truncated": True}, ensure_ascii=False)


def _bound_text(value, limit):
    if isinstance(value, str) and len(value) > limit:
        if value[:1] in {"{", "["}:
            return _compact_json_text(value, limit)
        return value[:limit] + "\u2026[截断]"
    return value


# 结构性字段绝不截断：ids、版本、错误码、路径是恢复与审计的锚点（补充
# 设计 §4）；长度只约束「内容」类键。
_STRUCTURE_KEYS = frozenset({
    "tool_call_id", "id", "draft_id", "approval_id", "plan_digest", "source_id",
    "revision", "code", "path", "run_id", "session_id", "uuid", "digest",
})
_CONTENT_KEYS = frozenset({"content", "text", "answer", "delta", "instructions",
                           "description", "error"})


def _walk_bound(node, limit, *, key: str = ""):
    if isinstance(node, dict):
        if (node.get('part_kind') == 'tool-return' and node.get('tool_name') == FILE_TOOL
                and isinstance(node.get('content'), dict)):
            return node  # Store-bounded exact range, or an explicit locator without source.
        return {key_: _walk_bound(item, limit, key=key_) for key_, item in node.items()}
    if isinstance(node, list):
        return [_walk_bound(item, limit, key=key) for item in node]
    if isinstance(node, str):
        if key in _STRUCTURE_KEYS:
            return node
        if key not in _CONTENT_KEYS:
            # 未登记的键保守起见也不截断；体积由观察上限与总量兜底。
            return node
    return _bound_text(node, limit)


def _latest_user_request(dumped: list) -> str:
    """从首条任务 payload 的 conversation 里取最新用户指令（截断前的原始文本）。

    payload 首条始终是 {'agent_history': ...} 之外的任务 JSON 字符串；其
    conversation 字段是服务端会话记忆构建的。解析失败（旧调用方/异构
    payload）时返回空串，不改变任何既有行为。
    """
    try:
        content = dumped[0]['parts'][0]['content']
        if not isinstance(content, str) or not content.startswith('{'):
            return ''
        conversation = json.loads(content).get('conversation')
        if not isinstance(conversation, list):
            return ''
        for message in reversed(conversation):
            if (message or {}).get('role') == 'user' and str(message.get('content') or '').strip():
                return str(message['content'])[:500]
    except (KeyError, IndexError, TypeError, ValueError):
        return ''
    return ''


def _bound_history(history: list) -> list:
    """三段固定 + 预算回收：第 0 条任务消息、最近窗口、按预算回收的中间历史。

    第 0 条（任务目标 / 初始 payload）与最近窗口无条件保留——先单独提取
    固定，再从尾部向前在剩余预算内回收更早的历史，最后按原始顺序拼接。
    反向循环遇顶 break 绝不会影响这两段（review 第二轮 P1-1：旧实现的正向
    break 根本走不到 index 0，保护形同虚设）。超大的第 0 条走结构化压缩
    而非任意截断；被舍弃的中间消息直接移除，不插合成条目（会破坏
    pydantic-ai 的消息形状契约）。
    """

    if not history:
        return []
    total = len(history)
    recent_start = max(1, total - HISTORY_RECENT_MESSAGES)

    seen_ranges: set[tuple] = set()

    def bound_at(index: int, limit: int):
        return _walk_bound(preserve_source_observations(history[index], seen_ranges), limit)

    # 1) 第 0 条任务消息：结构化压缩到固定预算（身份 / 目录索引保留）。
    first = _walk_bound(history[0], HISTORY_FIRST_CHARS)
    first_cost = len(json.dumps(first, ensure_ascii=False))
    if first_cost > HISTORY_FIRST_CHARS:
        # 结构保留的深度收缩：内容键与白名单键按更小的帽压缩，消息的
        # kind / parts 形状原样保留（review 第三轮 P1-2：truncated 包装
        # 会丢掉任务结构，伪装成别的消息继续跑）。
        first = _walk_bound(history[0], 600)
        first_cost = len(json.dumps(first, ensure_ascii=False))
        if first_cost > HISTORY_FIRST_CHARS:
            first = _walk_bound(history[0], 200)

    # 2) 最近窗口无条件保留。
    recent: list = []
    for index in range(total - 1, recent_start - 1, -1):
        recent.append(bound_at(index, HISTORY_RECENT_CHARS))
    recent.reverse()
    recent_cost = sum(len(json.dumps(message, ensure_ascii=False)) for message in recent)

    # 3) 中间历史从新到旧回收，直到把剩余预算用完。
    middle: list = []
    budget = max(0, HISTORY_TOTAL_CHARS - first_cost - recent_cost)
    for index in range(recent_start - 1, 0, -1):
        message = bound_at(index, HISTORY_OLDER_CHARS)
        cost = len(json.dumps(message, ensure_ascii=False))
        if running_cost(cost, middle) > budget:
            # If an older source page no longer fits, keep its ref/offsets when
            # possible instead of leaving a clipped string labelled as exact.
            message = preserve_source_observations(message, seen_ranges, omit_source=True)
            cost = len(json.dumps(message, ensure_ascii=False))
            if running_cost(cost, middle) > budget:
                break
        middle.append(message)
    middle.reverse()
    return [first, *middle, *recent]


def running_cost(cost: int, middle: list) -> int:
    return sum(len(json.dumps(message, ensure_ascii=False)) for message in middle) + cost


class DiceFrameModel(Model):
    def __init__(self, client, emit: Callable, language: str, max_tokens: int, max_steps: int,
                 task_max_tokens: int):
        super().__init__()
        self.client = client
        self.emit = emit
        self.language = language
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.response_correction = ""
        self.task_max_tokens = task_max_tokens
        # Only reported usage counts. Missing usage isn't a zero-cost guarantee.
        self.task_used = 0

    @property
    def model_name(self) -> str:
        return "diceframe-configured-provider"

    @property
    def system(self) -> str:
        return "diceframe"

    def _budget_exceeded(self) -> UsageLimitExceeded:
        return UsageLimitExceeded(f'Exceeded the total_tokens_limit of {self.task_max_tokens} '
                                  f'(total_tokens={self.task_used})')

    async def _record_usage(self, response) -> dict:
        usage = getattr(response, 'usage', None)
        if not isinstance(usage, dict):
            # Compatibility with planner adapters that only return a total.
            reported = getattr(response, 'total_tokens', None)
            attempts = max(1, int(getattr(response, 'attempted_requests', 0) or 0))
            unknown = attempts if reported is None else 0
            usage = {'total_tokens': max(0, int(reported or 0)), 'attempted_requests': attempts,
                     'unknown_usage_requests': unknown, 'usage_complete': unknown == 0}
        self.task_used += usage['total_tokens']
        await self.emit('usage', usage)
        return usage

    async def request(self, messages, model_settings, model_request_parameters):
        if self.task_used >= self.task_max_tokens:
            raise self._budget_exceeded()
        await self.emit("status", {"message": t("webui.services.assistant_agent_framework.planning_next_step") if self.language.startswith("zh") else "Planning the next step…"})
        parameters = model_request_parameters
        definitions = [*parameters.output_tools, *parameters.function_tools]
        schemas = [{"type": "function", "function": {
            "name": tool.name, "description": tool.description or "",
            "parameters": tool.parameters_json_schema,
        }} for tool in definitions]
        # Keep the correlated tool observations and retry prompts, bounded:
        # provider failover stays in LLMClient, context size stays in here.
        dumped = ModelMessagesTypeAdapter.dump_python(messages, mode="json", exclude_none=True)
        history = _bound_history(dumped)
        instructions = "\n\n".join(part.content for message in messages
            for part in message.parts if part.part_kind == "system-prompt")
        if parameters.instruction_parts:
            instructions += "\n\n" + "\n".join(part.content for part in parameters.instruction_parts)
        if self.response_correction:
            # Only our fixed protocol explanation enters the next request;
            # never echo malformed provider arguments into instructions.
            instructions += "\n\nCorrection for the previous response: " + self.response_correction
        # 工具往返变长后，任务 payload 会被压到历史头部，部分模型只盯着
        # 尾部的工具返回、声称「没看到请求」（creator 实测翻车点）。把最新
        # 用户指令重新钉在每次请求的末尾，随历史增长始终可见。
        latest = _latest_user_request(dumped)
        user_message = json.dumps({'agent_history': history, **(
            {'latest_user_request': latest} if latest else {})}, ensure_ascii=False)
        if usage_logger.isEnabledFor(logging.INFO):
            project_observations = [part.get('content') for message in history for part in message.get('parts', [])
                                    if part.get('part_kind') == 'tool-return'
                                    and part.get('tool_name') in READ_TOOLS]
            usage_logger.info('agent_request_history %s', json.dumps({
                'run_id': usage_run_id.get(), 'history_chars': len(user_message),
                'instructions_chars': len(instructions),
                'creator_observations': len(project_observations),
                'creator_observation_chars': sum(len(part) if isinstance(part, str)
                    else len(json.dumps(part, ensure_ascii=False)) for part in project_observations),
            }))
        try:
            response = await self.client.call_tools(instructions, user_message,
                tools=schemas, max_tokens=self.max_tokens, force_tool_choice=False)
        except LLMToolCallError as exc:
            if exc.usage is not None:
                await self._record_usage(exc)
            raise provider_unavailable(exc, self.language) from exc
        usage = await self._record_usage(response)
        if self.task_used > self.task_max_tokens:
            # Keep billed usage, but never dispatch a tool or create an approval
            # from the response that crossed the task ceiling.
            raise self._budget_exceeded()
        calls = getattr(response, "tool_calls", None)
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
        # Preserve the actual breakdown when reported. The authoritative task
        # limit above uses reported totals, not a fabricated output-token count.
        return ModelResponse(parts, model_name=self.model_name, usage=RequestUsage(
            input_tokens=usage.get('known_input_tokens', 0), output_tokens=usage.get('known_output_tokens', 0)))


async def execute_agent(client, tools, instructions: str, payload: dict, emit: Callable,
                        language: str, max_tokens: int, max_steps: int,
                        *, task_max_tokens: int = DEFAULT_TASK_MAX_TOKENS, run_id: str | None = None):
    model = DiceFrameModel(client, emit, language, max_tokens, max_steps, task_max_tokens)
    agent = Agent(model,
        output_type=[FinalAnswer, str, DeferredToolRequests], tools=tools,
        instructions=instructions, retries=3, name="DF Agent")

    @agent.output_validator
    def validate_output(ctx, output):
        if isinstance(output, str) and (not output.strip() or len(output) > 24000):
            raise ModelRetry("Return a nonempty final answer of at most 24000 characters.")
        return output

    # One framework run: a soft UsageLimits exception can occur before the
    # response is appended, so capturing and restarting there loses paid work.
    # Context remains bounded per request. Framework request/tool limits and
    # validation retries are never reset mid-task.
    usage_context = usage_run_id.set(run_id or uuid.uuid4().hex)
    try:
        result = await agent.run(json.dumps(payload, ensure_ascii=False),
            usage_limits=UsageLimits(request_limit=max_steps, tool_calls_limit=max_steps), infer_name=False)
        return result.output
    except UnexpectedModelBehavior as exc:
        raise AgentModelResponseInvalid(language) from exc
    finally:
        usage_run_id.reset(usage_context)


__all__ = ["DeferredToolRequests", "FinalAnswer", "ModelRetry", "UsageLimitExceeded", "execute_agent"]
