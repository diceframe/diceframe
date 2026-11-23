"""Deterministic acceptance checks for generated narrative turns.

The writer may produce prose and structured tags in one response, but the
round pipeline must accept them as one contract. This module owns the small,
side-effect-free gate used before any state applier or narrative effect runs.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class NarrativeQualityError(RuntimeError):
    """The writer failed the quality gate after its single repair attempt."""

    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(str(issue) for issue in issues if str(issue).strip())
        detail = ", ".join(self.issues) or "unknown_quality_failure"
        super().__init__(f"narrative quality gate rejected output: {detail}")


def inspect_narrative_output(
    response: Any,
    data: Any,
    *,
    allowed_player_uids: set[str] | None = None,
) -> list[str]:
    """Return stable issue codes for a generated response without mutation."""

    issues: list[str] = []
    narration = str(getattr(response, "narration", "") or "").strip()
    if not narration:
        issues.append("empty_narration")

    if not isinstance(data, dict):
        return [*issues, "invalid_result"]

    state_update = data.get("state_update", {})
    if state_update is None:
        state_update = {}
    if not isinstance(state_update, dict):
        issues.append("invalid_state_update")
        state_update = {}

    players = state_update.get("players", {})
    if players is None:
        players = {}
    if not isinstance(players, dict):
        issues.append("invalid_players_section")
        players = {}
    for uid, player_update in players.items():
        uid_text = str(uid or "").strip()
        if not uid_text:
            issues.append("empty_player_uid")
        if allowed_player_uids is not None and uid_text not in allowed_player_uids:
            issues.append(f"unauthorized_player_uid:{uid_text}")
        if not isinstance(player_update, dict):
            issues.append(f"invalid_player_update:{uid_text or '?'}")

    for field in ("loot", "free_grants", "economy_proposals", "pending_payments"):
        if field in state_update and not isinstance(state_update.get(field), list):
            issues.append(f"invalid_{field}")

    for field in ("memory_delta", "info_asymmetry", "plot_update"):
        value = data.get(field)
        if value is not None and not isinstance(value, dict):
            issues.append(f"invalid_{field}")

    return issues[:8]


def quality_repair_instruction(language: str, issues: Iterable[str]) -> str:
    """Build a compact writer-only repair instruction for one retry."""

    issue_lines = "\n".join(f"- {issue}" for issue in list(issues)[:8])
    if str(language or "").lower().startswith("en"):
        return (
            "QUALITY GATE REPAIR REQUIRED. Rewrite the complete response once.\n"
            "Fix every listed contract issue, keep the narration grounded in the supplied context, "
            "and emit the required `---` state-tag separator. Use only authorized roster IDs in player "
            "updates. Keep every structured section as the required JSON/list shape.\n"
            f"Issues:\n{issue_lines}"
        )
    return (
        "【质量门修复】请完整重写本次回复。\n"
        "修复下面列出的全部契约问题，正文必须基于给定上下文，必须输出 `---` 状态标签分隔线。"
        "玩家状态只能使用本轮允许的角色 ID，所有结构化区块必须保持既定的对象/列表形状。\n"
        f"问题：\n{issue_lines}"
    )


__all__ = [
    "NarrativeQualityError",
    "inspect_narrative_output",
    "quality_repair_instruction",
]
