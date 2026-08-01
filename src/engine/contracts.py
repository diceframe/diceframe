"""核心运行时协议的渐进式类型定义。

这些结构仍按原 JSON 字段序列化，TypedDict 只为编辑器和静态检查提供
跨模块字段契约，不改变旧存档、HTTP 或 Bot 消费格式。
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class PlayerData(TypedDict, total=False):
    user_id: str
    character_name: str
    character_sheet: dict[str, Any]


class ActionRecord(TypedDict, total=False):
    user_id: str
    text: str
    selected_attribute: str
    selected_skill: str
    target_text: str
    source: str
    dice_pending: bool
    dice_system: str
    dice_value: int
    dice_rolls: list[int]
    check_request: dict[str, Any]
    revision_count: int


class CheckResult(TypedDict, total=False):
    check_id: str
    label: str
    actor_uid: str
    actor_name: str
    dice: str
    attribute: str | None
    skill: str
    roll: int
    rolls: list[int]
    threshold: int
    hard_threshold: int
    extreme_threshold: int
    modifier: int
    modifier_breakdown: str | None
    total: int
    dc: int
    difficulty: str
    verdict: str
    advantage_mode: str
    advantage_note: str | None
    luck_spend_available: bool
    luck_cost: int | None
    luck_decision: Literal["pending", "spent", "declined"]
    is_critical: bool
    is_fumble: bool


class PendingPayment(TypedDict, total=False):
    id: str
    uid: str
    amount: int
    recipient_uid: str
    rewards: list[dict[str, Any]]
    reason: str
    status: Literal["pending", "accepted", "declined", "rejected"]
    round: int
    resolved_at: float


class RoundLogEntry(TypedDict, total=False):
    round: int
    actions: list[ActionRecord]
    gm_response: str
    state_changes: list[str]
    check_results: list[CheckResult]
    round_start_snapshot: dict[str, Any]
    pre_state_snapshot: dict[str, Any]
    swipes: list[str]
    current_swipe: int
    timestamp: str
    tags_summary: NotRequired[dict[str, Any]]


TokenBudgetBump = TypedDict(
    "TokenBudgetBump",
    {"kind": str, "from": int, "to": int},
)
