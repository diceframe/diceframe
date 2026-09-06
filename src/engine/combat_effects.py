"""通用战斗效果引擎（Issue 212 PR 2）。

本模块把"动作 → 资源消耗 → 效果 → 事件"变成一条固定的服务端管线：

    校验 action/costs/effects → 求值全部公式（一次） → 原子扣资源消耗
    → 逐目标应用效果（护盾先于 HP） → 生成权威事件 → 返回新状态

设计约束：
- 纯函数：传入 CombatState，返回新 CombatState 与事件；失败时抛
  CombatActionError，调用方保留旧状态（天然支持回滚）；
- 公式在资源消耗提交**之前**统一求值一次：任何求值失败都让整个动作被
  拒绝，绝不出现"扣了资源没生效"，也绝不产生"校验一次、结算又骰一次"
  的双重随机；
- 客户端永远不提供 damage/mana_after/gauge_after 等最终值；
- 护盾（barrier）只是带吸收范围的普通资源池，伤害先扣护盾、剩余再扣
  HP，不做任何独立特判；
- 本阶段支持 ``damage`` 与 ``resource_change`` 两种效果；其余 kind
  （apply_status/remove_status/barrier/modify_stat/advance_scheduler）
  显式拒绝——Phase 2 再接入，绝不静默忽略。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from src.engine.combat_contracts import (
    EFFECT_KINDS,
    CombatAction,
    EffectSpec,
    ResourceCost,
)
from src.engine.combat_formulas import FormulaContext, evaluate_formula_bound
from src.engine.combat_resources import (
    ResourcePool,
    ResourceError,
    commit_spend,
    restore_pool,
)


class CombatActionError(ValueError):
    """动作校验/结算失败：整个 action 被拒绝，不产生任何状态变更。"""


@dataclass(frozen=True)
class CombatState:
    """通用战斗状态：每个实体一组资源池 + 本局资源角色声明。

    ``hp_resource`` 指向充当生命值的资源 id；``barriers`` 声明充当护盾的
    资源 id 及其可吸收的伤害类型（None = 全部）。角色声明来自规则 runtime
    的显式 capability，而不是从角色字段猜测。
    """

    entities: Mapping[str, Mapping[str, ResourcePool]]
    hp_resource: str = "hp"
    barriers: Mapping[str, frozenset[str] | None] = field(default_factory=dict)
    # 临时状态修正（buff）：{"entity_id", "stat", "delta", "remaining"}。
    # 时长扣减由宿主在调度推进时负责，本模块只做应用与存储。
    buffs: tuple[dict[str, Any], ...] = ()

    def entity_pools(self, entity_id: str) -> Mapping[str, ResourcePool]:
        pools = self.entities.get(entity_id)
        if pools is None:
            raise CombatActionError(f"unknown combat entity: {entity_id!r}")
        return pools

    def hp_pool(self, entity_id: str) -> ResourcePool:
        pool = self.entity_pools(entity_id).get(self.hp_resource)
        if pool is None:
            raise CombatActionError(
                f"entity {entity_id!r} has no hp resource {self.hp_resource!r}"
            )
        return pool


@dataclass(frozen=True)
class CombatActionOutcome:
    """一次成功动作的结果：新状态 + 权威事件序列（JSON 可序列化）。"""

    state: CombatState
    events: tuple[dict[str, Any], ...]


def validate_effect_spec(
    effect: Mapping[str, Any],
    *,
    allowed_resources: frozenset[str] | set[str] | None = None,
    allowed_damage_types: frozenset[str] | set[str] | None = None,
) -> EffectSpec:
    """把外部（规则目录/模板）给出的原始效果映射校验为 EffectSpec。

    ``allowed_resources`` / ``allowed_damage_types`` 是规则 runtime 显式
    声明的能力边界：未声明集合时仅做结构校验；声明了集合则引用必须命中，
    否则整个 action 被拒绝（fail closed）。
    """

    if not isinstance(effect, Mapping):
        raise CombatActionError("effect must be an object")
    kind = effect.get("kind")
    if kind not in EFFECT_KINDS:
        raise CombatActionError(f"unknown effect kind: {kind!r}")
    resource = effect.get("resource")
    if resource is not None:
        if not isinstance(resource, str) or not resource.strip():
            raise CombatActionError("effect resource must be a non-empty string")
        if allowed_resources is not None and resource not in allowed_resources:
            raise CombatActionError(
                f"effect references undeclared resource: {resource!r}"
            )
    damage_type = effect.get("damage_type")
    if damage_type is not None:
        if not isinstance(damage_type, str) or not damage_type.strip():
            raise CombatActionError("damage_type must be a non-empty string")
        if allowed_damage_types is not None and damage_type not in allowed_damage_types:
            raise CombatActionError(
                f"effect uses undeclared damage type: {damage_type!r}"
            )
    amount = effect.get("amount")
    if amount is not None and not (isinstance(amount, Mapping) and amount.get("op")):
        raise CombatActionError("effect amount must be a formula object")
    duration = effect.get("duration")
    if duration is not None and (
        isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0
    ):
        raise CombatActionError("effect duration must be a positive integer")
    return EffectSpec(
        kind=str(kind),
        amount=amount,
        resource=str(resource) if resource is not None else None,
        duration=duration,
        damage_type=str(damage_type) if damage_type is not None else None,
        target_policy=effect.get("target_policy"),
        metadata={},
    )


def validate_costs(
    costs: Sequence[ResourceCost],
    *,
    allowed_resources: frozenset[str] | set[str] | None = None,
) -> tuple[ResourceCost, ...]:
    """校验动作消耗声明：资源必须在能力边界内、无重复、公式结构合法。"""

    validated: list[ResourceCost] = []
    seen: set[str] = set()
    for cost in costs:
        if allowed_resources is not None and cost.resource not in allowed_resources:
            raise CombatActionError(
                f"action costs undeclared resource: {cost.resource!r}"
            )
        if cost.resource in seen:
            raise CombatActionError(f"duplicate cost for resource: {cost.resource!r}")
        if not (isinstance(cost.amount, Mapping) and cost.amount.get("op")):
            raise CombatActionError("cost amount must be a formula object")
        validated.append(cost)
    return tuple(validated)


def _barrier_order(
    target_pools: Mapping[str, ResourcePool],
    barriers: Mapping[str, frozenset[str] | None],
) -> list[str]:
    """确定性护盾扣减顺序：canonical resource_id 字典序。"""

    return sorted(
        resource_id for resource_id in target_pools if resource_id in barriers
    )


def _barrier_absorbs(
    barriers: Mapping[str, frozenset[str] | None],
    resource_id: str,
    damage_type: str | None,
) -> bool:
    allowed = barriers.get(resource_id)
    if allowed is None:
        return True
    if damage_type is None:
        return True
    return damage_type in allowed


def _apply_damage(
    state: CombatState,
    target_id: str,
    amount: int,
    damage_type: str | None,
    *,
    source_action_id: str,
    events: list[dict[str, Any]],
) -> CombatState:
    if amount <= 0:
        raise CombatActionError(f"damage amount must be positive: {amount}")
    pools = dict(state.entity_pools(target_id))
    remaining = amount

    for resource_id in _barrier_order(pools, state.barriers):
        if remaining <= 0:
            break
        if not _barrier_absorbs(state.barriers, resource_id, damage_type):
            continue
        barrier = pools[resource_id]
        absorbed = min(barrier.current, remaining)
        if absorbed <= 0:
            continue
        pools[resource_id] = replace(barrier, current=barrier.current - absorbed)
        events.append({
            "type": "combat.resource_changed",
            "entity_id": target_id,
            "resource": resource_id,
            "before": barrier.current,
            "delta": -absorbed,
            "after": barrier.current - absorbed,
            "source_action": source_action_id,
        })
        remaining -= absorbed

    hp = pools.get(state.hp_resource)
    if hp is None:
        raise CombatActionError(
            f"entity {target_id!r} has no hp resource {state.hp_resource!r}"
        )
    applied = min(remaining, hp.current - hp.minimum)
    if applied > 0:
        pools[state.hp_resource] = replace(hp, current=hp.current - applied)
        events.append({
            "type": "combat.resource_changed",
            "entity_id": target_id,
            "resource": state.hp_resource,
            "before": hp.current,
            "delta": -applied,
            "after": hp.current - applied,
            "source_action": source_action_id,
        })
    events.append({
        "type": "combat.damage_applied",
        "target_id": target_id,
        "amount": amount,
        "applied": amount - remaining + applied,
        "damage_type": damage_type,
        "source_action": source_action_id,
    })
    return replace(state, entities={**state.entities, target_id: pools})


def _apply_resource_change(
    state: CombatState,
    target_id: str,
    spec: EffectSpec,
    amount: int,
    *,
    source_action_id: str,
    events: list[dict[str, Any]],
) -> CombatState:
    if spec.resource is None:
        raise CombatActionError("resource_change effect requires a resource")
    pools = dict(state.entity_pools(target_id))
    pool = pools.get(spec.resource)
    if pool is None:
        raise CombatActionError(
            f"entity {target_id!r} has no resource {spec.resource!r}"
        )
    if amount > 0:
        after = restore_pool(pool, amount)
    elif amount < 0:
        # 负向资源变化按 clamp 扣到 minimum：目标缺资源不该让动作失败。
        drain = min(pool.current - pool.minimum, -amount)
        after = replace(pool, current=pool.current - drain)
    else:
        raise CombatActionError("resource_change amount evaluated to zero")
    if after.current != pool.current:
        events.append({
            "type": "combat.resource_changed",
            "entity_id": target_id,
            "resource": spec.resource,
            "before": pool.current,
            "delta": after.current - pool.current,
            "after": after.current,
            "source_action": source_action_id,
        })
        pools[spec.resource] = after
        return replace(state, entities={**state.entities, target_id: pools})
    return state


def apply_effects(
    state: CombatState,
    effects: Sequence[EffectSpec],
    context: FormulaContext,
    *,
    source_action_id: str = "",
) -> CombatActionOutcome:
    """对 context.target_id 指向的单个目标应用一组效果（底层原语）。

    多目标动作请走 :func:`apply_combat_action`；本函数失败即抛
    CombatActionError，调用方保留旧状态。
    """

    events: list[dict[str, Any]] = []
    current = state
    for spec in effects:
        if context.target_id is None:
            raise CombatActionError("effect application requires a target")
        if spec.kind == "damage":
            try:
                amount = evaluate_formula_bound(spec.amount or {}, context)
            except ValueError as exc:
                raise CombatActionError(f"damage amount rejected: {exc}") from exc
            current = _apply_damage(
                current, context.target_id, amount, spec.damage_type,
                source_action_id=source_action_id, events=events,
            )
        elif spec.kind == "resource_change":
            try:
                amount = evaluate_formula_bound(spec.amount or {}, context)
            except ValueError as exc:
                raise CombatActionError(f"resource_change amount rejected: {exc}") from exc
            current = _apply_resource_change(
                current, context.target_id, spec, amount,
                source_action_id=source_action_id, events=events,
            )
        elif spec.kind == "modify_stat":
            stat = spec.resource
            if not stat:
                raise CombatActionError("modify_stat effect requires a stat")
            try:
                amount = evaluate_formula_bound(spec.amount or {}, context)
            except ValueError as exc:
                raise CombatActionError(f"modify_stat amount rejected: {exc}") from exc
            if amount == 0:
                raise CombatActionError("modify_stat amount evaluated to zero")
            duration = spec.duration
            if not duration or duration <= 0:
                raise CombatActionError("modify_stat requires a positive duration")
            if context.target_id is None:
                raise CombatActionError("modify_stat effect requires a target")
            entry = {"entity_id": context.target_id, "stat": stat,
                     "delta": amount, "remaining": duration}
            current = replace(current, buffs=(*current.buffs, entry))
            events.append({
                "type": "combat.buff_applied", "entity_id": context.target_id,
                "stat": stat, "delta": amount, "duration": duration,
                "source_action": source_action_id,
            })
        else:
            raise CombatActionError(
                f"effect kind {spec.kind!r} is not supported in this phase"
            )
    return CombatActionOutcome(state=current, events=tuple(events))


def apply_combat_action(
    state: CombatState,
    action: CombatAction,
    context: FormulaContext,
    *,
    allowed_resources: frozenset[str] | set[str] | None = None,
    allowed_damage_types: frozenset[str] | set[str] | None = None,
) -> CombatActionOutcome:
    """动作级管线：校验 → 一次性求值 → 原子扣消耗 → 逐目标应用 → 事件。

    任何一步失败抛 CombatActionError，不返回半成品状态。
    """

    actor_pools = state.entity_pools(context.actor_id)

    # 1) 消耗声明校验 + 求值（公式失败即整体拒绝）。
    cost_amounts: dict[str, int] = {}
    for cost in validate_costs(action.costs, allowed_resources=allowed_resources):
        if cost.resource not in actor_pools:
            raise CombatActionError(
                f"actor {context.actor_id!r} has no resource {cost.resource!r}"
            )
        try:
            amount = evaluate_formula_bound(cost.amount, context)
        except ValueError as exc:
            raise CombatActionError(f"cost amount rejected: {exc}") from exc
        if amount < 0:
            raise CombatActionError("cost amount must not be negative")
        cost_amounts[cost.resource] = amount

    # 2) 效果声明校验（能力边界内；未接入的 kind 显式拒绝）。
    effects: list[EffectSpec] = []
    for spec in action.effects:
        validated = validate_effect_spec(
            {"kind": spec.kind, "amount": spec.amount, "resource": spec.resource,
             "duration": spec.duration, "damage_type": spec.damage_type,
             "target_policy": spec.target_policy},
            allowed_resources=allowed_resources,
            allowed_damage_types=allowed_damage_types,
        )
        if validated.kind not in {"damage", "resource_change", "modify_stat"}:
            raise CombatActionError(
                f"effect kind {validated.kind!r} is not supported in this phase"
            )
        effects.append(validated)

    # 3) 效果金额一次性求值（整个动作共享一次骰运，多目标同值不同扣）。
    effect_amounts: dict[int, int] = {}
    for index, spec in enumerate(effects):
        if spec.amount is None:
            raise CombatActionError(f"{spec.kind} effect requires an amount formula")
        try:
            effect_amounts[index] = evaluate_formula_bound(spec.amount, context)
        except ValueError as exc:
            raise CombatActionError(f"effect amount rejected: {exc}") from exc

    # 4) 原子扣消耗；此后效果应用只消费已求值金额，不会再失败到
    #    "扣了资源没生效"（目标实体缺失仍属拒绝路径，状态不被返回）。
    try:
        actor_after_cost = commit_spend(actor_pools, cost_amounts)
    except ResourceError as exc:
        raise CombatActionError(f"action cost rejected: {exc}") from exc
    state = replace(state, entities={**state.entities, context.actor_id: actor_after_cost})

    events: list[dict[str, Any]] = []
    for resource in sorted(cost_amounts):
        amount = cost_amounts[resource]
        if not amount:
            continue
        before = actor_pools[resource].current
        events.append({
            "type": "combat.resource_changed",
            "entity_id": context.actor_id,
            "resource": resource,
            "before": before,
            "delta": -amount,
            "after": before - amount,
            "source_action": action.action_id,
        })

    # 5) 逐目标应用效果；目标实体必须存在，缺失即整体拒绝。
    current = state
    for index, spec in enumerate(effects):
        amount = effect_amounts[index]
        targets = action.target_ids or (context.actor_id,)
        for target_id in targets:
            if target_id not in current.entities:
                raise CombatActionError(f"unknown combat entity: {target_id!r}")
            if spec.kind == "damage":
                current = _apply_damage(
                    current, target_id, amount, spec.damage_type,
                    source_action_id=action.action_id, events=events,
                )
            elif spec.kind == "modify_stat":
                duration = spec.duration or 0
                if duration <= 0:
                    raise CombatActionError("modify_stat requires a positive duration")
                entry = {"entity_id": target_id, "stat": spec.resource or "",
                         "delta": amount, "remaining": duration}
                current = replace(current, buffs=(*current.buffs, entry))
                events.append({
                    "type": "combat.buff_applied", "entity_id": target_id,
                    "stat": spec.resource, "delta": amount, "duration": duration,
                    "source_action": action.action_id,
                })
            else:
                current = _apply_resource_change(
                    current, target_id, spec, amount,
                    source_action_id=action.action_id, events=events,
                )
    return CombatActionOutcome(state=current, events=tuple(events))
