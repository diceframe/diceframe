"""Adventure outcome declarations → authority intents (ADV2-04, 母方案 §26/§120).

Adventure **不写 authority**（§26）：它只声明 *Outcome Proposal*；本模块把
声明转换为三类 intent，交由既有权威路径执行：

\`\`\`text
{"type": "world_op", ...}        → world ops（engine apply_world_ops 应用）
{"type": "item_reward", ...}     → DNDMOD-03 reward intent（proposal 权威路径）
{"type": "progress_event", ...}  → Adventure progress 事件（ADV2-06 进度模型）
\`\`\`

边界：

- 本模块是**纯声明层**：不碰 GameInstance、不写世界、不发奖励；"是否执行、
  如何执行"由调用方在权威事务内决定（与 DNDMOD-03 的 reward 桥同一分工）。
- world_op 载荷在结构层只校验"有 op 名"；完整校验由唯一写入口
  apply_world_ops fail closed 把关（单一真值，不复制 op 词表）。
- 未知 outcome 类型 / 未知字段 fail closed（防模组偷偷发明新效果类型）。
"""

from __future__ import annotations

from typing import Any

from src.rulesets.dnd2024.content.rewards import (
    RewardIntentError,
    reward_intent_from_ref,
)

OUTCOME_TYPES = ("world_op", "item_reward", "progress_event")

_PROGRESS_EVENT_KINDS = (
    "node_completed",
    "objective_completed",
    "milestone_reached",
)


class OutcomeError(ValueError):
    """An adventure outcome declaration is invalid: fail closed."""


def validate_outcome(raw: Any) -> dict[str, Any]:
    """Validate one outcome declaration and return it unchanged."""

    if not isinstance(raw, dict):
        raise OutcomeError("outcome must be an object")
    outcome_type = str(raw.get("type") or "")
    if outcome_type not in OUTCOME_TYPES:
        raise OutcomeError(f"outcome type is not supported: {outcome_type!r}")
    if outcome_type == "world_op":
        op = raw.get("op")
        if not isinstance(op, dict) or not str(op.get("op") or ""):
            raise OutcomeError("world_op outcome requires an op payload")
        return {"type": outcome_type, "op": dict(op)}
    if outcome_type == "item_reward":
        extra = sorted(set(raw) - {"type", "ref", "recipient_uid"})
        if extra:
            raise OutcomeError(f"item_reward outcome has unknown field: {extra[0]!r}")
        if not raw.get("ref"):
            raise OutcomeError("item_reward outcome requires ref")
        return {
            "type": outcome_type,
            "ref": raw.get("ref"),
            "recipient_uid": str(raw.get("recipient_uid") or ""),
        }
    extra = sorted(set(raw) - {"type", "kind", "id"})
    if extra:
        raise OutcomeError(f"progress_event outcome has unknown field: {extra[0]!r}")
    kind = str(raw.get("kind") or "")
    if kind not in _PROGRESS_EVENT_KINDS:
        raise OutcomeError(f"progress event kind is invalid: {kind!r}")
    return {
        "type": outcome_type,
        "kind": kind,
        "id": str(raw.get("id") or ""),
    }


def outcomes_to_intents(
    outcomes: list[dict[str, Any]] | None,
    *,
    catalog: Any = None,
    default_source: str,
    recipient_uid: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Convert outcome declarations into authority intents.

    Returns ``{"world_ops": [...], "reward_intents": [...],
    "progress_events": [...]}``。world_ops 的完整校验在唯一写入口；
    reward intent 由 DNDMOD-03 桥解析（catalog 缺席时 fail closed）。
    """

    world_ops: list[dict[str, Any]] = []
    reward_intents: list[dict[str, Any]] = []
    progress_events: list[dict[str, Any]] = []
    for raw in outcomes or []:
        outcome = validate_outcome(raw)
        outcome_type = outcome["type"]
        if outcome_type == "world_op":
            world_ops.append(outcome["op"])
        elif outcome_type == "item_reward":
            if catalog is None:
                raise OutcomeError("item_reward requires a content catalog")
            try:
                reward_intents.append(reward_intent_from_ref(
                    catalog,
                    outcome["ref"],
                    default_source=default_source,
                    recipient_uid=outcome["recipient_uid"] or recipient_uid,
                ))
            except RewardIntentError as exc:
                raise OutcomeError(str(exc)) from exc
        else:
            progress_events.append({
                "kind": outcome["kind"],
                "id": outcome["id"],
            })
    return {
        "world_ops": world_ops,
        "reward_intents": reward_intents,
        "progress_events": progress_events,
    }


__all__ = ["OUTCOME_TYPES", "OutcomeError", "outcomes_to_intents", "validate_outcome"]
