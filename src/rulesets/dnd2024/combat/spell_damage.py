"""S0-EFFECT1 P4b：效果项 → 伤害节点。

法术侧原本没有"应用点"：每个站点各自拿 `effect["damage"]` 造一棵伤害树，再叠
自己的机制（升环、暴击、吟唱加值、超魔法、过载、抗性）。这里只抽**第一步**——
"效果项 + 升环骰 + 情境 → 伤害节点"，各站点自己的机制仍留在站点里。

抽的理由不是少写两行，而是"载荷从哪个字段读"必须只有一个答案；否则 P4a 拆出来的
效果项永远只有一半站点认识。
"""

from __future__ import annotations

from typing import Any

from .action_adapter import double_dice_counts, spell_formula_node


def damage_node_for(
    item: dict[str, Any], *, upcast_damage: Any, spell: dict[str, Any],
    slot_level: int, actor: dict[str, Any], critical: bool = False,
) -> Any:
    payload = item.get("damage")
    if not isinstance(payload, dict) or not payload.get("dice"):
        raise ValueError("effect item has no damage to build a node from")
    node = spell_formula_node(
        str(payload["dice"]), upcast_damage, spell, slot_level, actor,
    )
    # 重击翻的是骰数，不是加值：与各站点原来的顺序一致。
    return double_dice_counts(node) if critical else node
