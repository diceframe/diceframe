"""Narrator-proposed adventure completion settlement (GAL track P6).

The ``ADVENTURE_COMPLETE`` narrator tag already produces advisory
completion suggestions (validated evidence rounds, GM-only projection).
A rule that declares an ``adventure_progression`` block may let the engine
authoritatively confirm those suggestions after a round commits: the
standard completion transaction (gates, world ops, ending freeze, reward
intents, all-or-nothing rollback) stays the single authority — this module
only decides *whether* to invoke it and what to do when it refuses.

Fail-closed rule: a rejected proposal is dropped (the route stays locked);
the narrator may re-propose with fresh evidence next round. A confirmed
completion is narration-only — this module never hands the narrator state
write access; every state change happens inside the completion transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.engine.modules.adventure_runtime_state import (
    clear_completion_suggestion,
    progress,
)

logger = logging.getLogger("trpg")

# One authoritative auto-confirmation per settle pass. A transition narration
# that itself proposes another completion must wait for the next round —
# this caps any self-amplifying chain.
MAX_AUTO_CONFIRMATIONS_PER_PASS = 1


@dataclass
class CompletionSettlement:
    node_id: str
    activated_nodes: list[str] = field(default_factory=list)
    transition_narration: str = ""


async def settle_adventure_completion(
    instance: Any,
    *,
    config: Any | None,
    confirm: Callable[[str], Awaitable[dict]],
    narration: Callable[[dict, list[str]], Awaitable[str]] | None = None,
) -> CompletionSettlement | None:
    """Confirm narrator-proposed node completions through the engine's authority.

    ``config`` is the rule-declared ``AdventureProgressionConfig``; ``None``
    (rule declares nothing) keeps the advisory-only behaviour unchanged.
    ``confirm(node_id)`` must run the standard completion transaction under the
    same concurrency boundary the GM command uses (authoritative write, lock,
    stale-run check) and raise on rejection — the transaction restores its own
    before-images. ``narration(result, activated_nodes)`` optionally produces
    one scene-transition narration for a confirmed completion; its failure
    degrades to no narration and never rolls the completion back.
    """

    if config is None or not getattr(config, "auto_confirm_completion", False):
        return None

    suggestions = progress(instance).get("completion_suggestions")
    if not isinstance(suggestions, list):
        return None

    confirmed = 0
    for suggestion in list(suggestions):
        if confirmed >= MAX_AUTO_CONFIRMATIONS_PER_PASS:
            break
        if not isinstance(suggestion, dict):
            continue
        node_id = str(suggestion.get("node_id") or "")
        if not node_id:
            continue
        try:
            result = await confirm(node_id)
        except Exception as exc:
            # fail closed：确认失败 = 路线保持锁定，丢弃该提议（模型下轮可带
            # 新证据重提）；complete 事务自身已恢复 before-image。
            clear_completion_suggestion(instance, node_id)
            logger.warning(
                "叙事者节点完成提议被引擎拒绝，已丢弃: game=%s node=%s err=%s",
                getattr(instance, "game_key", ""), node_id, exc,
            )
            continue
        confirmed += 1
        activated = [
            str(item) for item in (result or {}).get("activated_nodes") or []
        ]
        transition = ""
        if (
            getattr(config, "activation_narration", False)
            and narration is not None
        ):
            try:
                transition = await narration(result or {}, activated)
            except Exception:
                logger.warning(
                    "场景过渡叙事生成失败，节点完成结果保持不变: game=%s node=%s",
                    getattr(instance, "game_key", ""), node_id, exc_info=True,
                )
        return CompletionSettlement(
            node_id=node_id,
            activated_nodes=activated,
            transition_narration=str(transition or ""),
        )
    return None
