"""Adventure reward → D&D runtime reward intent bridge (DNDMOD-03, 母方案 §113).

Adventure / module 声明的物品奖励是 **ContentRef**；本模块把引用解析为
既有权威奖励路径可消费的 reward intent —— **绝不直接写 inventory**：

```text
Adventure outcome: reward ContentRef (item:castle_sigil)
    → DndContentCatalog 解析（source-aware）
    → reward intent（name / category / provenance）
    → 既有 proposal / state applier 权威路径（payer/GM 决策不变）
    → inventory 由 character state authority 提交
```

硬边界：

- FREE_GRANT / 付费 proposal 的既有闸门（``filter_unconfirmed_purchase_grants``）
  原样生效：本模块产出的 intent 只是把"给什么"结构化，"是否给、怎么给"仍由
  权威结算路径决定（母方案 §26 / ADR-0002）。
- item 数量不在这里：数量语义归 character/economy authority（母方案 §189）。
"""

from __future__ import annotations

from typing import Any

from src.content_modules.refs import ContentRef, ContentRefError, parse_content_ref
from src.rulesets.dnd2024.content.catalog import DndContentCatalog
from src.rulesets.dnd2024.content.contracts import ITEM_CATEGORIES

# catalog item category → 既有 reward 分类（state_items.classify_item 词表的
# 权威子集）；catalog 显式类别优先于启发式分类。
_REWARD_CATEGORY_BY_ITEM_CATEGORY = {
    "equipment": "equipment",
    "consumable": "consumable",
    "treasure": "treasure",
    "tool": "misc",
    "key_item": "key_item",
}


class RewardIntentError(ValueError):
    """A reward reference cannot be turned into a reward intent: fail closed."""


def reward_intent_from_ref(
    catalog: DndContentCatalog,
    raw_ref: Any,
    *,
    default_source: str,
    recipient_uid: str,
) -> dict[str, Any]:
    """Resolve one item ContentRef into a structured reward intent.

    返回的 intent 交给既有权威奖励路径（proposal rewards / state applier），
    本模块不写任何状态。
    """

    try:
        ref: ContentRef = parse_content_ref(raw_ref, default_source=default_source)
    except ValueError as exc:
        raise RewardIntentError(f"reward ref: {exc}") from exc
    if ref.kind != "item":
        raise RewardIntentError(
            f"reward ref must point at an item: {ref.canonical()}"
        )
    record = catalog.resolve(ref)
    if record is None:
        raise RewardIntentError(f"reward item ref unresolved: {ref.canonical()}")
    category = str(record.get("category") or "")
    if category not in ITEM_CATEGORIES:
        raise RewardIntentError(f"reward item category is invalid: {category!r}")
    return {
        "kind": "item_grant",
        "ref": {"source": ref.source, "kind": ref.kind, "id": ref.id},
        "recipient_uid": str(recipient_uid or ""),
        "name": str(record.get("name") or ""),
        "category": _REWARD_CATEGORY_BY_ITEM_CATEGORY[category],
        "source_ref": str(record.get("source_ref") or ref.source),
        # 提示既有权威路径：该 reward 的分类来自 catalog 显式声明，
        # classify_item 的关键词启发式不应覆盖它。
        "category_authoritative": True,
    }


def reward_intents_from_outcome(
    catalog: DndContentCatalog,
    outcome: dict[str, Any],
    *,
    default_source: str,
    recipient_uid: str,
) -> list[dict[str, Any]]:
    """Resolve every reward ref in one adventure outcome; empty when none.

    ``outcome["rewards"]`` 形如 ``[{"ref": ..., "recipient_uid"?}, ...]``；
    未知字段 fail closed。
    """

    if not isinstance(outcome, dict):
        raise RewardIntentError("adventure outcome must be an object")
    rewards = outcome.get("rewards")
    if rewards is None:
        return []
    if not isinstance(rewards, list):
        raise RewardIntentError("adventure outcome rewards must be a list")
    intents: list[dict[str, Any]] = []
    for entry in rewards:
        if not isinstance(entry, dict):
            raise RewardIntentError("adventure reward entry must be an object")
        extra = sorted(set(entry) - {"ref", "recipient_uid"})
        if extra:
            raise RewardIntentError(f"adventure reward entry has unknown field: {extra[0]!r}")
        intents.append(reward_intent_from_ref(
            catalog,
            entry.get("ref"),
            default_source=default_source,
            recipient_uid=str(entry.get("recipient_uid") or recipient_uid),
        ))
    return intents


__all__ = ["RewardIntentError", "reward_intent_from_ref", "reward_intents_from_outcome"]
