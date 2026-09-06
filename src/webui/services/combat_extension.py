"""通用战斗扩展服务层（Issue 212 phase 2 Slice 2.3）。

第一个消费规则集：freeform_wuxia（传统 legacy 管线 + 显式 combat 声明）。
本服务不接入 D&D 的事件账本/权威 intent 路由——那是 versioned-state 架构；
这里按 ADR 0004/方案 §11 的轻量形状实现：

- 客户端只提交 ``{"intent_id", "action_id", "target_ids", "actor_id"?}``；
- 全部数值由服务端公式求值，客户端提供的伤害/资源/行动条值一律忽略；
- 状态存于 ``instance.combat_extension``（调度器 + 实体资源池），角色卡
  承载的字段（HP、内力等 special_stat）在结算后写回角色卡保持单一权威；
- 权限：玩家只能驱动 ``player:<自己uid>`` 实体；GM 可驱动任意实体。
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from src.engine.combat_contracts import CombatAction
from src.engine.combat_config import CombatExtensionConfig, combat_extension_from_template
from src.engine.combat_effects import CombatState, apply_combat_action
from src.engine.combat_formulas import FormulaContext, evaluate_formula_bound
from src.engine.combat_resources import ResourcePool
from src.engine.combat_scheduler import (
    SchedulerConfig,
    SchedulerState,
    scheduler_from_config,
)
from src.engine.game_instance import GameInstance

_COMBAT_EXTENSION_SCHEMA = 1


class CombatExtensionNotConfigured(ValueError):
    """当前规则未声明 combat 能力。"""


def load_config(rule: Any) -> CombatExtensionConfig | None:
    template = getattr(rule, "template", None)
    if not isinstance(template, Mapping) and not isinstance(template, dict):
        return None
    return combat_extension_from_template(template)


def _sheet_stat(sheet: dict[str, Any], stat: str) -> int:
    value = sheet.get(stat, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _seed_pools(
    instance: GameInstance,
    config: CombatExtensionConfig,
    entity_id: str,
) -> dict[str, ResourcePool]:
    """从服务端权威数据播种资源池（角色卡字段 / 战斗会话状态）。"""

    pools: dict[str, ResourcePool] = {}
    previous = (instance.combat_extension or {}).get("pools", {}).get(entity_id, {})
    sheet: dict[str, Any] = {}
    if entity_id.startswith("player:"):
        uid = entity_id.removeprefix("player:")
        sheet = instance.get_character_sheet(uid)
    elif entity_id.startswith("npc:"):
        npc_id = entity_id.removeprefix("npc:")
        npc = instance.npcs.get(npc_id) or {}
        sheet = {"hp": int(npc.get("hp", 0) or 0), "max_hp": int(npc.get("hp", 0) or 0)}
    for decl in config.resources:
        prior = previous.get(decl.resource_id) or {}
        current = prior.get("current")
        maximum = decl.maximum
        if decl.source == "hp":
            if current is None:
                current = int(sheet.get("hp", 0) or 0)
            if maximum is None:
                maximum = int(sheet.get("max_hp", 0) or 0) or None
        elif decl.source == "special_stat" and decl.stat:
            if current is None:
                current = _sheet_stat(sheet, str(decl.stat))
            if maximum is None:
                maximum = _sheet_stat(sheet, f"max_{decl.stat}") or None
        elif decl.source == "combat_state" and current is None:
            current = 0
        pools[decl.resource_id] = ResourcePool(
            resource_id=decl.resource_id,
            current=int(current or 0),
            maximum=maximum,
            minimum=0,
        )
    return pools


def _write_back_to_sheets(
    instance: GameInstance,
    config: CombatExtensionConfig,
    entity_id: str,
    pools: dict[str, ResourcePool],
) -> None:
    """把角色卡承载的资源写回角色卡（HP / special_stat），保持单一权威。"""

    if not entity_id.startswith("player:"):
        return
    uid = entity_id.removeprefix("player:")
    if uid not in instance.players:
        return
    sheet = instance.get_character_sheet(uid)
    changed = False
    for decl in config.resources:
        pool = pools.get(decl.resource_id)
        if pool is None:
            continue
        if decl.source == "hp":
            sheet["hp"] = pool.current
            changed = True
        elif decl.source == "special_stat" and decl.stat:
            sheet[str(decl.stat)] = pool.current
            changed = True
    if changed:
        instance.set_character_sheet(uid, sheet)


def _ensure_state(
    instance: GameInstance,
    config: CombatExtensionConfig,
) -> tuple[CombatState, SchedulerState | None]:
    payload = instance.combat_extension or {}
    # 玩家始终参与（队伍规模有界）；NPC 按需参与：只有已被目标锁定过
    # （payload 里已有池）的 NPC 才进入状态，避免百人 NPC 存档与面板爆炸。
    prior_pools = payload.get("pools", {}) or {}
    entity_ids = sorted(
        {f"player:{uid}" for uid in instance.players}
        | {f"npc:{npc_id}" for npc_id in instance.npcs
           if f"npc:{npc_id}" in prior_pools}
    )
    pools_payload = {
        entity_id: {
            resource_id: dict(pool)
            for resource_id, pool in
            (payload.get("pools", {}).get(entity_id, {}) or {}).items()
        }
        for entity_id in entity_ids
    }
    pools = {
        entity_id: _seed_pools(instance, config, entity_id)
        for entity_id in entity_ids
    }
    for entity_id, declared in pools.items():
        for resource_id, pool in declared.items():
            raw = pools_payload.get(entity_id, {}).get(resource_id)
            if raw:
                declared[resource_id] = ResourcePool(
                    resource_id=resource_id,
                    current=int(raw.get("current", pool.current) or 0),
                    maximum=raw.get("maximum", pool.maximum),
                    minimum=raw.get("minimum", 0),
                )
    scheduler_state = None
    raw_scheduler = payload.get("scheduler")
    if isinstance(raw_scheduler, dict) and raw_scheduler:
        scheduler_state = SchedulerState.from_dict(raw_scheduler)
    return (
        CombatState(
            entities=pools,
            hp_resource=config.hp_resource,
            barriers=config.barrier_resources,
        ),
        scheduler_state,
    )


def _formula_context(
    instance: GameInstance,
    config: CombatExtensionConfig,
    entity_id: str,
    target_id: str | None,
) -> FormulaContext:
    """从角色卡构建公式上下文；NPC 实体的属性表为空（只能用常量公式）。"""

    uid = entity_id.removeprefix("player:") if entity_id.startswith("player:") else ""
    sheet = instance.get_character_sheet(uid) if uid else {}
    attributes = {
        str(key): int(value)
        for key, value in (sheet.get("attributes") or {}).items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    pools = _ensure_state(instance, config)[0].entities.get(entity_id, {})
    resources = {
        resource_id: pool.current for resource_id, pool in pools.items()
    }
    return FormulaContext(
        attributes=attributes,
        derived_stats={},
        resources=resources,
        equipment_stats={},
        actor_id=entity_id,
        target_id=target_id,
    )


def combat_extension_projection(
    instance: GameInstance,
    rule: Any,
    *,
    viewer_uid: str,
    viewer_is_gm: bool,
) -> dict[str, Any] | None:
    """game_detail 的能力投影：动作目录 + 本人资源池 + 调度器状态。

    未声明 combat 能力的规则返回 None；资源池只对本人与 GM 可见，
    调度器（行动条）是桌面公共信息。
    """

    config = load_config(rule)
    if config is None:
        return None
    state, scheduler_state = _ensure_state(instance, config)
    actions = [
        {
            "id": decl.action_id,
            "kind": decl.kind,
            "name": decl.name,
            "costs": [
                {"resource": cost.resource, "amount": deepcopy(dict(cost.amount))}
                for cost in decl.costs
            ],
        }
        for decl in config.actions
    ]
    pools: dict[str, Any] = {}
    for entity_id, entity_pools in state.entities.items():
        uid = entity_id.removeprefix("player:")
        if not viewer_is_gm and viewer_uid != uid:
            continue
        pools[entity_id] = {
            resource_id: {"current": pool.current, "maximum": pool.maximum}
            for resource_id, pool in entity_pools.items()
        }
    return {
        "scheduler": {
            "kind": config.scheduler.kind if config.scheduler else None,
            "ready": list(scheduler_state.ready) if scheduler_state else [],
            "gauges": dict(scheduler_state.gauges) if scheduler_state else {},
        },
        "entities": sorted(state.entities),
        "actions": actions,
        "pools": pools,
    }


def resolve_combat_action(
    instance: GameInstance,
    rule: Any,
    intent: Mapping[str, Any],
    *,
    actor_uid: str,
    viewer_is_gm: bool,
) -> dict[str, Any]:
    """服务端结算一次战斗动作 intent；成功后写回状态并按需存档。"""

    config = load_config(rule)
    if config is None:
        return {"ok": False, "code": "COMBAT_EXTENSION_NOT_CONFIGURED",
                "error": "当前规则未声明战斗扩展"}
    action_id = str(intent.get("action_id") or "")
    decl = config.action(action_id)
    if decl is None:
        return {"ok": False, "code": "ACTION_NOT_FOUND", "error": "未知战斗动作"}

    requested_actor = str(intent.get("actor_id") or f"player:{actor_uid}")
    is_gm_entity = requested_actor.startswith(("npc:", "enemy:"))
    if not viewer_is_gm and requested_actor != f"player:{actor_uid}":
        return {"ok": False, "code": "ACTOR_FORBIDDEN",
                "error": "只能驱动自己的战斗实体"}
    if is_gm_entity and not viewer_is_gm:
        return {"ok": False, "code": "ACTOR_FORBIDDEN", "error": "仅 GM 可驱动该实体"}

    target_ids = intent.get("target_ids")
    target_ids = tuple(
        str(item) for item in (target_ids if isinstance(target_ids, list) else [target_ids])
        if item
    )

    state, _scheduler = _ensure_state(instance, config)
    if requested_actor not in state.entities:
        # NPC 行动者按需播种（GM 驱动 NPC 的入口）。
        if (requested_actor.startswith("npc:")
                and requested_actor.removeprefix("npc:") in instance.npcs):
            state = replace(state, entities={
                **state.entities,
                requested_actor: _seed_pools(instance, config, requested_actor),
            })
        else:
            return {"ok": False, "code": "ACTOR_NOT_FOUND", "error": "战斗实体不存在"}
    for target_id in target_ids or (requested_actor,):
        if target_id in state.entities:
            continue
        if target_id.startswith("npc:") and target_id.removeprefix("npc:") in instance.npcs:
            # 按需参与：首次被锁定的 NPC 此刻才播种进战斗状态。
            state = replace(state, entities={
                **state.entities,
                target_id: _seed_pools(instance, config, target_id),
            })
            continue
        if target_id.startswith("player:") and target_id.removeprefix("player:") in instance.players:
            state = replace(state, entities={
                **state.entities,
                target_id: _seed_pools(instance, config, target_id),
            })
            continue
        return {"ok": False, "code": "TARGET_NOT_FOUND",
                "error": f"战斗目标不存在: {target_id}"}

    context = _formula_context(instance, config, requested_actor,
                               target_ids[0] if target_ids else None)
    combat_action = CombatAction(
        action_id=decl.action_id,
        actor_id=requested_actor,
        target_ids=target_ids or (requested_actor,),
        kind=decl.kind,
        costs=decl.costs,
        effects=decl.effects,
    )
    try:
        outcome = apply_combat_action(state, combat_action, context)
    except ValueError as exc:
        return {"ok": False, "code": "ACTION_REJECTED", "error": str(exc)}

    # 写回：combat_extension 池 + 角色卡承载字段。
    payload = {
        "schema_version": _COMBAT_EXTENSION_SCHEMA,
        "scheduler": (
            _scheduler.to_dict()
            if _scheduler
            else (instance.combat_extension or {}).get("scheduler")
        ),
        "pools": {
            entity_id: {
                resource_id: {
                    "current": pool.current,
                    "maximum": pool.maximum,
                    "minimum": pool.minimum,
                }
                for resource_id, pool in entity_pools.items()
            }
            for entity_id, entity_pools in outcome.state.entities.items()
        },
    }
    instance.combat_extension = payload
    for entity_id, entity_pools in outcome.state.entities.items():
        _write_back_to_sheets(instance, config, entity_id, dict(entity_pools))

    return {
        "ok": True,
        "intent_id": str(intent.get("intent_id") or ""),
        "events": list(outcome.events),
        "action": {"id": decl.action_id, "name": decl.name},
    }
