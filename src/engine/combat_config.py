"""规则模板的通用战斗扩展声明解析（Issue 212 phase 2）。

规则模板通过显式 ``combat`` 块声明战斗扩展能力：调度器、资源池与动作
目录。本模块是唯一的解析入口——全部字段 fail closed 校验，非法声明直接
拒绝整个模板加载，绝不猜测（例如：出现 ``"speed": 20`` 并不会自动启用
ATB；``combat`` 块不存在时返回 None，游戏保持原有玩法）。

资源来源（``source``）：
- ``hp``：生命池（结算目标，必须有）；
- ``special_stat``：角色卡 special_stat 字段（如内力/灵力），``stat`` 指明
  canonical key，``costable`` 标记可否被消耗；
- ``combat_state``：战斗会话状态池（如护盾），存于战斗扩展状态。

普通展示字段不会被自动升级成资源——没有声明就没有资源池。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.engine.combat_contracts import (
    ACTION_KINDS,
    EffectSpec,
    ResourceCost,
)
from src.engine.combat_effects import CombatActionError, validate_effect_spec
from src.engine.combat_formulas import FormulaError, validate_formula
from src.engine.combat_scheduler import SchedulerConfig


class CombatConfigError(ValueError):
    """规则模板的 combat 声明非法：fail closed。"""


_RESOURCE_SOURCES = frozenset({"hp", "special_stat", "combat_state"})


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CombatConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _declared_keys(raw: Any) -> frozenset[str] | None:
    if not isinstance(raw, list):
        return None
    return frozenset(
        item["key"].strip()
        for item in raw
        if isinstance(item, Mapping)
        and isinstance(item.get("key"), str)
        and item["key"].strip()
    )


def _validate_formula_references(
    node: Mapping[str, Any],
    *,
    allowed_attributes: frozenset[str] | None,
    allowed_resources: frozenset[str],
    allow_dice: bool = True,
) -> None:
    op = node.get("op")
    ref = node.get("id")
    if op == "attribute" and allowed_attributes is not None and ref not in allowed_attributes:
        raise FormulaError(f"unknown attribute reference: {ref!r}")
    if op == "resource" and ref not in allowed_resources:
        raise FormulaError(f"unknown resource reference: {ref!r}")
    if op in {"derived_stat", "equipment_stat"}:
        raise FormulaError(f"{op} references are not available in this combat adapter")
    if op == "dice" and not allow_dice:
        raise FormulaError("dice is not allowed in scheduler speed_formula")
    args = node.get("args")
    if isinstance(args, list):
        for arg in args:
            if isinstance(arg, Mapping):
                _validate_formula_references(
                    arg,
                    allowed_attributes=allowed_attributes,
                    allowed_resources=allowed_resources,
                    allow_dice=allow_dice,
                )


@dataclass(frozen=True)
class CombatResourceDecl:
    """一个资源池声明。"""

    resource_id: str
    source: str
    stat: str | None = None
    maximum: int | None = None
    costable: bool = True
    damage_types: tuple[str, ...] | None = None  # 护盾可吸收的伤害类型；None=全部
    damage_priority: str | None = None  # "before_hp" 表示护盾


@dataclass(frozen=True)
class CombatItemCost:
    """动作与库存的联动声明：执行时从行动者背包扣除指定物品。"""

    item: str
    qty: int = 1


@dataclass(frozen=True)
class CombatActionDecl:
    """动作目录条目：canonical action_id + 服务端公式/效果声明。"""

    action_id: str
    kind: str
    name: str
    costs: tuple[ResourceCost, ...]
    effects: tuple[EffectSpec, ...]
    consume_item: CombatItemCost | None = None


@dataclass(frozen=True)
class CombatExtensionConfig:
    """一份规则模板的战斗扩展声明（无 combat 块的规则返回 None）。"""

    scheduler: SchedulerConfig | None
    resources: tuple[CombatResourceDecl, ...]
    actions: tuple[CombatActionDecl, ...]
    # ATB 每个实体的基础速度公式（按实体属性求值）；缺省为常数 25。
    speed_formula: Mapping[str, Any] | None = None

    @property
    def resource_ids(self) -> frozenset[str]:
        return frozenset(item.resource_id for item in self.resources)

    @property
    def hp_resource(self) -> str:
        for item in self.resources:
            if item.source == "hp":
                return item.resource_id
        raise CombatConfigError("combat declaration has no hp resource")

    @property
    def barrier_resources(self) -> dict[str, frozenset[str] | None]:
        return {
            item.resource_id: (
                frozenset(item.damage_types) if item.damage_types else None
            )
            for item in self.resources
            if item.damage_priority == "before_hp"
        }

    def action(self, action_id: str) -> CombatActionDecl | None:
        return next(
            (item for item in self.actions if item.action_id == action_id), None,
        )


def _parse_resources(
    raw: Any,
    *,
    allowed_special_stats: frozenset[str] | None = None,
) -> tuple[CombatResourceDecl, ...]:
    if not isinstance(raw, list) or not raw:
        raise CombatConfigError("combat.resources must be a non-empty list")
    decls: list[CombatResourceDecl] = []
    seen: set[str] = set()
    hp_count = 0
    for item in raw:
        if not isinstance(item, Mapping):
            raise CombatConfigError("combat.resources entries must be objects")
        resource_id = _required_string(item.get("id"), "resource id")
        if not resource_id or resource_id in seen:
            raise CombatConfigError(f"resource id must be unique and non-empty: {resource_id!r}")
        seen.add(resource_id)
        source = _required_string(item.get("source"), f"resource {resource_id!r} source")
        if source not in _RESOURCE_SOURCES:
            raise CombatConfigError(f"resource {resource_id!r} has unknown source: {source!r}")
        stat = item.get("stat")
        if source == "special_stat":
            if not isinstance(stat, str) or not stat.strip():
                raise CombatConfigError(
                    f"resource {resource_id!r} with source special_stat requires stat"
                )
            stat = stat.strip()
            if allowed_special_stats is not None and stat not in allowed_special_stats:
                raise CombatConfigError(
                    f"resource {resource_id!r} references undeclared special_stat: {stat!r}"
                )
        elif stat is not None:
            raise CombatConfigError(
                f"resource {resource_id!r} stat is only valid for special_stat"
            )
        maximum = item.get("maximum")
        if maximum is not None and (
            isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0
        ):
            raise CombatConfigError(f"resource {resource_id!r} maximum must be a positive integer")
        damage_types = item.get("damage_types")
        if damage_types is not None and (
            not isinstance(damage_types, list)
            or any(not isinstance(value, str) or not value for value in damage_types)
        ):
            raise CombatConfigError(f"resource {resource_id!r} damage_types must be string list")
        damage_priority = item.get("damage_priority")
        if damage_priority is not None and damage_priority != "before_hp":
            raise CombatConfigError(
                f"resource {resource_id!r} damage_priority must be before_hp"
            )
        if damage_priority is not None and source != "combat_state":
            raise CombatConfigError(
                f"resource {resource_id!r} damage_priority requires combat_state"
            )
        raw_costable = item.get("costable", True)
        if not isinstance(raw_costable, bool):
            raise CombatConfigError(
                f"resource {resource_id!r} costable must be a boolean"
            )
        if source == "hp":
            hp_count += 1
        decls.append(CombatResourceDecl(
            resource_id=resource_id,
            source=source,
            stat=stat,
            maximum=maximum,
            costable=raw_costable,
            damage_types=tuple(damage_types) if damage_types else None,
            damage_priority=damage_priority,
        ))
    if hp_count != 1:
        raise CombatConfigError("combat.resources must declare exactly one hp source pool")
    return tuple(decls)


def _parse_actions(
    raw: Any,
    allowed_resources: frozenset[str],
    costable_resources: frozenset[str],
    allowed_damage_types: frozenset[str] | None,
    allowed_attributes: frozenset[str] | None,
) -> tuple[CombatActionDecl, ...]:
    if not isinstance(raw, list):
        raise CombatConfigError("combat.actions must be a list")
    decls: list[CombatActionDecl] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise CombatConfigError("combat.actions entries must be objects")
        action_id = _required_string(item.get("id"), "action id")
        if not action_id or action_id in seen:
            raise CombatConfigError(f"action id must be unique and non-empty: {action_id!r}")
        seen.add(action_id)
        kind = _required_string(item.get("kind"), f"action {action_id!r} kind")
        if kind not in ACTION_KINDS:
            raise CombatConfigError(f"action {action_id!r} has unknown kind: {kind!r}")
        raw_name = item.get("name", action_id)
        name = _required_string(raw_name, f"action {action_id!r} name")
        raw_costs = item.get("costs", [])
        if not isinstance(raw_costs, list):
            raise CombatConfigError(f"action {action_id!r} costs must be a list")
        costs: list[ResourceCost] = []
        seen_costs: set[str] = set()
        for raw_cost in raw_costs:
            if not isinstance(raw_cost, Mapping):
                raise CombatConfigError(f"action {action_id!r} cost entries must be objects")
            resource = _required_string(
                raw_cost.get("resource"), f"action {action_id!r} cost resource",
            )
            if resource not in allowed_resources:
                raise CombatConfigError(
                    f"action {action_id!r} costs undeclared resource: {resource!r}"
                )
            if resource not in costable_resources:
                raise CombatConfigError(
                    f"action {action_id!r} costs non-costable resource: {resource!r}"
                )
            if resource in seen_costs:
                raise CombatConfigError(
                    f"action {action_id!r} has duplicate cost resource: {resource!r}"
                )
            seen_costs.add(resource)
            amount = raw_cost.get("amount")
            if not (isinstance(amount, Mapping) and amount.get("op")):
                raise CombatConfigError(f"action {action_id!r} cost amount must be a formula object")
            try:
                validate_formula(amount)
                _validate_formula_references(
                    amount,
                    allowed_attributes=allowed_attributes,
                    allowed_resources=allowed_resources,
                )
            except FormulaError as exc:
                raise CombatConfigError(
                    f"action {action_id!r} cost formula: {exc}"
                ) from exc
            costs.append(ResourceCost(resource=resource, amount=amount))
        raw_effects = item.get("effects", [])
        if not isinstance(raw_effects, list) or not raw_effects:
            raise CombatConfigError(f"action {action_id!r} requires non-empty effects")
        try:
            effects = tuple(
                validate_effect_spec(
                    raw_effect,
                    allowed_resources=allowed_resources,
                    allowed_damage_types=allowed_damage_types,
                )
                for raw_effect in raw_effects
            )
        except CombatActionError as exc:
            raise CombatConfigError(f"action {action_id!r}: {exc}") from exc
        for effect in effects:
            if effect.kind not in {"damage", "resource_change", "modify_stat"}:
                raise CombatConfigError(
                    f"action {action_id!r} uses unsupported effect kind: {effect.kind!r}"
                )
            if effect.amount is None:
                raise CombatConfigError(
                    f"action {action_id!r} effect {effect.kind!r} requires amount"
                )
            if effect.kind in {"resource_change", "modify_stat"} and not effect.resource:
                raise CombatConfigError(
                    f"action {action_id!r} effect {effect.kind!r} requires resource"
                )
            if effect.kind == "modify_stat" and effect.duration is None:
                raise CombatConfigError(
                    f"action {action_id!r} modify_stat requires duration"
                )
            if effect.kind == "damage" and (
                effect.resource is not None or effect.duration is not None
            ):
                raise CombatConfigError(
                    f"action {action_id!r} damage effect has incompatible fields"
                )
            if effect.kind == "resource_change" and (
                effect.damage_type is not None or effect.duration is not None
            ):
                raise CombatConfigError(
                    f"action {action_id!r} resource_change has incompatible fields"
                )
            if effect.kind == "modify_stat" and effect.damage_type is not None:
                raise CombatConfigError(
                    f"action {action_id!r} modify_stat has incompatible fields"
                )
            if effect.target_policy is not None:
                raise CombatConfigError(
                    f"action {action_id!r} target_policy is not supported"
                )
            try:
                validate_formula(effect.amount)
                _validate_formula_references(
                    effect.amount,
                    allowed_attributes=allowed_attributes,
                    allowed_resources=allowed_resources,
                )
            except FormulaError as exc:
                raise CombatConfigError(
                    f"action {action_id!r} effect formula: {exc}"
                ) from exc
        consume_item = None
        raw_consume = item.get("consume_item")
        if raw_consume is not None:
            if (
                not isinstance(raw_consume, Mapping)
                or not isinstance(raw_consume.get("item"), str)
                or not raw_consume["item"].strip()
            ):
                raise CombatConfigError(f"action {action_id!r} consume_item requires item name")
            qty = raw_consume.get("qty", 1)
            if isinstance(qty, bool) or not isinstance(qty, int):
                raise CombatConfigError(f"action {action_id!r} consume_item qty invalid")
            if qty <= 0:
                raise CombatConfigError(f"action {action_id!r} consume_item qty must be positive")
            consume_item = CombatItemCost(item=raw_consume["item"].strip(), qty=qty)
        decls.append(CombatActionDecl(
            action_id=action_id, kind=kind, name=name,
            costs=tuple(costs), effects=effects, consume_item=consume_item,
        ))
    return tuple(decls)


def combat_extension_from_template(template: Mapping[str, Any]) -> CombatExtensionConfig | None:
    """解析规则模板的 ``combat`` 块；无声明时返回 None（保持原玩法）。"""

    if not isinstance(template, Mapping):
        raise CombatConfigError("rule template must be an object")
    raw = template.get("combat")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise CombatConfigError("combat block must be an object")

    scheduler_raw = raw.get("scheduler")
    scheduler = (
        None if scheduler_raw is None
        else SchedulerConfig.from_payload(scheduler_raw)
    )

    allowed_special_stats = _declared_keys(template.get("special_stats"))
    allowed_attributes = _declared_keys(template.get("attributes"))
    resources = _parse_resources(
        raw.get("resources"),
        allowed_special_stats=allowed_special_stats,
    )
    allowed_resources = frozenset(item.resource_id for item in resources)
    costable_resources = frozenset(
        item.resource_id for item in resources if item.costable
    )
    declared_damage_types = raw.get("damage_types")
    if declared_damage_types is None:
        allowed_damage_types = None
    elif (
        not isinstance(declared_damage_types, list)
        or any(not isinstance(value, str) or not value for value in declared_damage_types)
    ):
        raise CombatConfigError("combat.damage_types must be a string list")
    else:
        # An empty optional vocabulary has historically meant "no restriction";
        # keep that behavior consistent with an omitted declaration.
        allowed_damage_types = frozenset(declared_damage_types) or None
    actions = _parse_actions(
        raw.get("actions"), allowed_resources, costable_resources,
        allowed_damage_types, allowed_attributes,
    )
    speed_formula = None
    if isinstance(scheduler_raw, Mapping):
        candidate = scheduler_raw.get("speed_formula")
        if candidate is not None and (not isinstance(candidate, Mapping) or not candidate.get("op")):
            raise CombatConfigError("scheduler.speed_formula must be a formula object")
        if candidate is not None:
            try:
                validate_formula(candidate)
                _validate_formula_references(
                    candidate,
                    allowed_attributes=allowed_attributes,
                    allowed_resources=frozenset(),
                    allow_dice=False,
                )
            except FormulaError as exc:
                raise CombatConfigError(
                    f"scheduler.speed_formula: {exc}"
                ) from exc
        speed_formula = candidate
    return CombatExtensionConfig(
        scheduler=scheduler, resources=resources, actions=actions,
        speed_formula=speed_formula,
    )
