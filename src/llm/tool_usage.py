"""Per-HTTP-attempt accounting for tool planning, not a provider billing oracle.

The context is opt-in and task-local: ordinary chat/stream calls are unchanged.
Only bounded metadata is logged; prompts, arguments, URLs, credentials and raw
provider errors are never included. Missing usage remains explicitly unknown.
"""
from __future__ import annotations

import json as json_module
import logging
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("trpg.llm_usage")
run_id: ContextVar[str | None] = ContextVar("llm_usage_run_id", default=None)
_active: ContextVar[ToolUsageLedger | None] = ContextVar("tool_usage_ledger", default=None)


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def reported_usage(data: Any) -> dict[str, Any]:
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _count(usage.get("prompt_tokens", usage.get("input_tokens")))
    output_tokens = _count(usage.get("completion_tokens", usage.get("output_tokens")))
    # Anthropic's input_tokens excludes separately reported prompt-cache input.
    if input_tokens is not None and "input_tokens" in usage:
        input_tokens += sum(_count(usage.get(key)) or 0 for key in
                            ("cache_read_input_tokens", "cache_creation_input_tokens"))
    total = _count(usage.get("total_tokens"))
    source = "reported_total" if total is not None else "unknown"
    if total is None and input_tokens is not None and output_tokens is not None:
        total, source = input_tokens + output_tokens, "reported_components"
    details = usage.get("completion_tokens_details", usage.get("output_tokens_details"))
    reasoning = _count(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    return {"total_tokens": total, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "reasoning_tokens": reasoning,
            "usage_source": source}


@dataclass
class ToolUsageLedger:
    run: str = field(default_factory=lambda: run_id.get() or uuid.uuid4().hex)
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    stage: str = "native"
    provider: str = ""
    model: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        unknown = sum(item["total_tokens"] is None for item in self.attempts)
        result = {"run_id": self.run, "call_id": self.call_id,
                  "total_tokens": sum(item["total_tokens"] or 0 for item in self.attempts),
                  "attempted_requests": len(self.attempts),
                  "unknown_usage_requests": unknown, "usage_complete": unknown == 0}
        for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
            known = sum(item[key] or 0 for item in self.attempts)
            result["known_" + key] = known
            result[key] = known if self.attempts and all(item[key] is not None for item in self.attempts) else None
        return result


@contextmanager
def collect_tool_usage():
    ledger = ToolUsageLedger()
    token = _active.set(ledger)
    try:
        yield ledger
    finally:
        _active.reset(token)


@asynccontextmanager
async def observed_post(session, url, *, json: dict[str, Any], negotiation_retry: bool = False, **kwargs):
    ledger = _active.get()
    if ledger is None:
        async with session.post(url, json=json, **kwargs) as response:
            yield response
        return
    item = {"run_id": ledger.run, "call_id": ledger.call_id,
            "request_id": uuid.uuid4().hex, "attempt": len(ledger.attempts) + 1,
            "provider": ledger.provider, "model": ledger.model, "stage": ledger.stage,
            "negotiation_retry": negotiation_retry,
            "status": None, **reported_usage(None)}
    tracing = logger.isEnabledFor(logging.INFO)
    if tracing:
        # Serialized character counts, deliberately not token estimates.
        item.update(input_chars=len(json_module.dumps({key: json[key] for key in ('system', 'messages') if key in json}, ensure_ascii=False)),
                    schema_chars=len(json_module.dumps(json.get('tools', []), ensure_ascii=False)),
                    max_output_tokens=json.get('max_completion_tokens', json.get('max_tokens')))
    ledger.attempts.append(item)
    try:
        async with session.post(url, json=json, **kwargs) as response:
            item["status"] = response.status
            try:
                # aiohttp caches the body; callers can still parse it normally.
                data = await response.json()
            except Exception:
                data = None
            item.update(reported_usage(data))
            if isinstance(data, dict):
                choices = data.get('choices')
                finish = (choices[0].get('finish_reason') if isinstance(choices, list) and choices
                          and isinstance(choices[0], dict) else data.get('stop_reason'))
                item['output_truncated'] = finish in ('length', 'max_tokens')
            yield response
    finally:
        if tracing:
            logger.info('tool_request_usage %s', json_module.dumps(item, ensure_ascii=True, sort_keys=True))
