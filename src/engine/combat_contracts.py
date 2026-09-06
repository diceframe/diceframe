"""通用战斗扩展的对外契约（Issue 212）。

本模块只定义可序列化的动作/效果/资源结构，不做任何结算。规则名称、具体
法术、修仙/D&D 专属概念一律不进入本文件：它们是规则 runtime 动作目录中的
``action_id`` 或 ``metadata``，通用契约只认识 ``kind``。

依赖方向（保持不变）::

    generic contract
        -> generic effect / formula / resource primitives
        -> ruleset runtime adapter
        -> ruleset action catalog / reducer
        -> transport
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# 受限公式 DSL 的 JSON AST 节点。通用层不理解节点语义之外的内容，
# 由 combat_formulas 负责校验与求值。
FormulaNode = Mapping[str, Any]

# 动作 kind：通用分类，新增类别应扩展这里而不是在结算函数里加分支。
ACTION_KINDS = frozenset({"attack", "ability", "consumable"})

# 效果 kind：通用分类。具体"这是什么法术/什么遁术"由 action_id 与
# ruleset metadata 表达。
EFFECT_KINDS = frozenset(
    {
        "damage",
        "resource_change",
        "apply_status",
        "remove_status",
        "barrier",
        "modify_stat",
        "advance_scheduler",
    }
)


@dataclass(frozen=True)
class CheckSpec:
    """动作附带的检定声明；命中与成败由服务端检定引擎唯一决定。"""

    kind: str
    attribute: str | None = None
    defense: str | None = None
    difficulty: int | None = None


@dataclass(frozen=True)
class ResourceCost:
    """一次动作对某个资源池的消耗声明；金额是公式节点而非客户端数值。"""

    resource: str
    amount: FormulaNode


@dataclass(frozen=True)
class EffectSpec:
    """单条效果声明；``kind`` 是通用契约，金额同样是公式节点。"""

    kind: str
    amount: FormulaNode | None = None
    resource: str | None = None
    duration: int | None = None
    damage_type: str | None = None
    target_policy: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CombatAction:
    """一次可执行战斗动作的完整声明。

    ``action_id`` 是规则动作目录中的 canonical id（例如 ``item:longsword.attack``
    或 ``spell:fireball``），通用层不解释其含义。客户端只提交 intent
    （action_id + targets），这里的结构由规则 runtime 在服务端构建。
    """

    action_id: str
    actor_id: str
    target_ids: tuple[str, ...]
    kind: str
    check: CheckSpec | None = None
    costs: tuple[ResourceCost, ...] = ()
    effects: tuple[EffectSpec, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
