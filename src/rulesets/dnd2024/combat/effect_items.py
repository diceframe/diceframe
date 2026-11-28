"""S0-EFFECT1：载荷（"对某个目标造成什么"）的唯一词汇。

本模块只回答 *造成什么*，不回答 *什么时候*、*对谁*：

* **时机**属于五个入口（掷骰面 / 命中后 / 主动使用 / 被动持续 / 反应），它们各有
  自己的生命周期，合并会吞掉差异；
* **目标集**属于来源（法术的 `target_count`、怪物动作的目标、攻击的 `target_id`）；
* **对掷骰本身的修饰**（优势、加值）属于窗口层，不是这里。

三档寿命（§3.3）：瞬间 / 带过期 / 需维护。需维护也有到期点，但它多一个维护者
（专注、每回合重掷），所以是独立的一档而不是"带过期的特例"。

本片（P1）只做"读时归一 + 字段闭集"，**不改变任何行为**：旧的 `kind` 形状继续合法，
事件形状一个字不动。
"""

from __future__ import annotations

from typing import Any

from .primitives import CombatIntentError

#: 效果项的字段闭集。少一个字段就意味着某种规则表达不了，多一个字段就意味着
#: 有一份数据会静默失效——所以未知字段一律拒绝。
EFFECT_ITEM_FIELDS = frozenset({
    "damage", "condition", "duration", "escape_dc", "push",
    "max_target_size", "save", "half_on_success", "save_repeat",
})

#: 旧 rider 的四种 kind。P2/P4 会逐步停止产出它们，但读的一方要一直认识。
LEGACY_RIDER_KINDS = frozenset({"condition", "prone", "grapple", "push"})

#: 寿命三档。``save_repeat`` 是第三档的标志：需要维护的效果一定有重掷，
#: 而"带过期"的条件没有维护者。专注（concentration）是 casting-shape，
#: 由施法侧回答，不进载荷。
LIFETIME_INSTANT = "instant"
LIFETIME_EXPIRING = "expiring"
LIFETIME_MAINTAINED = "maintained"


def normalize_effect_item(raw: Any) -> dict[str, Any] | None:
    """一条旧 rider 或一条新效果项 → 效果项。认不出来就返回 None。

    不做"尽力而为"的猜测：认不出的形状会让调用方跳过并记账，而不是半懂不懂地
    施加一半。``save`` 在本片保留 ``{"ability","dc"}``——DC 由来源提供是 P2 的
    事，P1 先把形状统一，不顺手改语义。
    """

    if not isinstance(raw, dict):
        return None
    unknown = sorted(set(raw) - EFFECT_ITEM_FIELDS - {"kind", "distance"})
    if unknown:
        # 静默丢弃未知字段等于把数据错误藏起来：调用方以为施加了，其实没有。
        raise CombatIntentError(f"effect item has unknown field(s): {unknown[0]}")
    kind = raw.get("kind")
    if kind is not None and kind not in LEGACY_RIDER_KINDS:
        return None
    item: dict[str, Any] = {}
    if kind == "push" or "push" in raw:
        distance = raw.get("push", raw.get("distance"))
        if not isinstance(distance, int) or isinstance(distance, bool) or distance <= 0:
            return None
        item["push"] = distance
    if kind in {"condition", "prone", "grapple"} or "condition" in raw:
        condition = str(raw.get("condition") or ("prone" if kind == "prone" else
                                                 "grappled" if kind == "grapple" else ""))
        if not condition:
            return None
        item["condition"] = condition
    if "damage" in raw:
        item["damage"] = raw["damage"]
    for key in ("duration", "max_target_size", "save", "half_on_success", "save_repeat"):
        if key in raw and raw[key] not in (None, "", {}):
            item[key] = raw[key]
    if "escape_dc" in raw:
        item["escape_dc"] = raw["escape_dc"]
    elif kind == "grapple":
        item["escape_dc"] = raw.get("escape_dc")
    if not item:
        return None
    return item


def normalize_rider(raw: Any) -> tuple[str, dict[str, Any]] | None:
    """一条旧 rider → ``(source, item)``。

    `source` 是**生产方声明的**种类，不是从条件名反推的——反推会把
    `{"kind":"condition","condition":"prone"}` 写成 `"prone"`，而旧事件写的是
    `"condition"`。事件的 `source_rider` 保留它，replay 才不变。
    """

    item = normalize_effect_item(raw)
    if item is None:
        return None
    source = str((raw or {}).get("kind") or "")
    if not source:
        # 新形状没有 kind：由 item 自己回答它是什么。
        source = "push" if "push" in item else "condition"
    return source, item


def normalize_rider_items(raw_items: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(raw_items, list):
        return []
    pairs: list[tuple[str, dict[str, Any]]] = []
    for raw in raw_items:
        pair = normalize_rider(raw)
        if pair is not None:
            pairs.append(pair)
    return pairs


def normalize_outcome_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    """一条 `save_action` 结果行 → 效果项列表。

    旧行是 ``{"damage": [{"dice","type"}, ...]}``（一到两段、各自独立抗性），
    新行直接是效果项。两种都收：旧数据零迁移是 §4 的承诺。
    """

    damage = row.get("damage")
    if isinstance(damage, list):
        items = [
            {"damage": {"dice": part["dice"], "type": str(part.get("type") or ""),
                        **({"half": True} if part.get("half") else {})}}
            for part in damage
            if isinstance(part, dict) and part.get("dice")
        ]
        return items
    item = normalize_effect_item(row)
    return [item] if item is not None else []


def normalize_effect_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        item = normalize_effect_item(raw)
        if item is not None:
            items.append(item)
    return items


def lifetime_of(item: dict[str, Any]) -> str:
    """三档寿命。``save_repeat``/专注就是"需维护"，其余按有没有 duration 分。"""

    if item.get("save_repeat"):
        return LIFETIME_MAINTAINED
    if item.get("duration"):
        return LIFETIME_EXPIRING
    return LIFETIME_INSTANT


def validate_effect_item(item: Any, label: str) -> None:
    if not isinstance(item, dict):
        raise CombatIntentError(f"{label} effect item must be an object")
    unknown = sorted(set(item) - EFFECT_ITEM_FIELDS)
    if unknown:
        raise CombatIntentError(f"{label} effect item has unknown field(s): {unknown[0]}")
    if not item:
        raise CombatIntentError(f"{label} effect item is empty")
    if "condition" in item and not str(item["condition"] or ""):
        raise CombatIntentError(f"{label} effect item condition is empty")
    if "push" in item:
        distance = item["push"]
        if isinstance(distance, bool) or not isinstance(distance, int) or distance <= 0:
            raise CombatIntentError(f"{label} effect item push distance is invalid")
