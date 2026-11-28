"""Reasoning effort levels and their request parameters per API format.

A configured level is an explicit user setting: it is sent as-is with every
request routed through that provider, narration and tool calls alike, and a
level the endpoint rejects surfaces as that endpoint's error. Unset, each
request path keeps its existing default, and only those automatic defaults
are negotiated away by the transport.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.llm.client import ProviderConfig

# ``reasoning_effort`` on Chat Completions and ``reasoning.effort`` on Responses.
# Not every reasoning model accepts every level; the endpoint decides.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
# Anthropic Messages carries effort in ``output_config.effort`` and has no "off"
# level: effort applies with or without a ``thinking`` field.
ANTHROPIC_REASONING_EFFORTS = frozenset(REASONING_EFFORTS[2:])
# An identifier a provider documents but the presets do not cover. It is passed
# through like a preset; the endpoint decides whether it is valid.
CUSTOM_REASONING_EFFORT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# Once documented here as a switch, but no known endpoint takes it as a level:
# unset is expressed by leaving the setting empty.
RESERVED_REASONING_EFFORTS = frozenset({"off"})


def normalize_reasoning_effort(value: Any, api_format: str = "openai") -> str:
    """Return ``""`` when unset, otherwise the preset or custom identifier; raise ValueError when unusable."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("reasoning effort must be a string")
    level = value.strip()
    if not level:
        return ""
    if level.lower() in RESERVED_REASONING_EFFORTS:
        raise ValueError("an unset reasoning effort is an empty value, not 'off'")
    if level.lower() in REASONING_EFFORTS:
        level = level.lower()
        allowed = ANTHROPIC_REASONING_EFFORTS if api_format.strip().lower() == "anthropic" else REASONING_EFFORTS
        if level not in allowed:
            raise ValueError(f"Reasoning effort {level} is not a level of the Anthropic format")
    elif not CUSTOM_REASONING_EFFORT.fullmatch(level):
        raise ValueError(f"Unsupported reasoning effort: {level}")
    return level


def _disables_thinking_by_default(provider: "ProviderConfig") -> bool:
    """The official DeepSeek endpoint thinks by default (verified parameter)."""
    return (
        provider.model_name.strip().lower().startswith("deepseek-v4")
        and "api.deepseek.com" in provider.base_url.strip().lower()
    )


def chat_reasoning_parameters(provider: "ProviderConfig", *, tool_calls: bool) -> dict[str, Any]:
    """Thinking controls for a Chat Completions body.

    An explicit ``provider.reasoning_effort`` replaces every default below.
    Unset, each path keeps its existing behavior:

    1. official DeepSeek endpoint → ``{"thinking": {"type": "disabled"}}``
    2. tool calls → ``{"reasoning_effort": "none"}``; Chat endpoints from GPT-5.6 on
       accept native function tools only at ``none``. The transport negotiates: an
       explicit unsupported-parameter response drops it for the retry.
    3. narration and the Anthropic format → nothing
    """
    if provider.reasoning_effort:
        return {"reasoning_effort": provider.reasoning_effort}
    if _disables_thinking_by_default(provider):
        return {"thinking": {"type": "disabled"}}
    if tool_calls and (provider.api_format or "openai").strip().lower() != "anthropic":
        return {"reasoning_effort": "none"}
    return {}


def anthropic_reasoning_parameters(provider: "ProviderConfig") -> dict[str, Any]:
    """Reasoning effort for Anthropic Messages: ``output_config.effort``.

    ``thinking`` is deliberately left alone. Effort takes effect without it, while
    some models reject ``disabled`` or ``adaptive`` with a 400.
    """
    if not provider.reasoning_effort:
        return {}
    return {"output_config": {"effort": provider.reasoning_effort}}
