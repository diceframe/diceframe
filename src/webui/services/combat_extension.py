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
from src.engine.combat_config import CombatActionDecl, CombatExtensionConfig, combat_extension_from_template
from src.engine.language import localized_text
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
    buffs = tuple(
        dict(item) for item in (payload.get("buffs") or [])
        if isinstance(item, dict)
    )
    return (
        CombatState(
            entities=pools,
            hp_resource=config.hp_resource,
            barriers=config.barrier_resources,
            buffs=buffs,
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


def _entity_display_name(instance: GameInstance, entity_id: str) -> str:
    if entity_id.startswith("player:"):
        uid = entity_id.removeprefix("player:")
        return str((instance.players.get(uid) or {}).get("character_name") or uid)
    if entity_id.startswith("npc:"):
        npc_id = entity_id.removeprefix("npc:")
        npc = instance.npcs.get(npc_id) or {}
        return str(npc.get("character_name") or npc.get("name") or npc_id)
    return entity_id


def _append_public_summary(
    instance: GameInstance,
    config: CombatExtensionConfig,
    actor_entity: str,
    decl: CombatActionDecl,
    events: list[dict[str, Any]],
) -> None:
    """把动作结算摘要写进当前回合的公共状态变化（多人可见）。"""

    damage_by_target: dict[str, int] = {}
    healed = 0
    for event in events:
        if event.get("type") == "combat.damage_applied":
            damage_by_target[event["target_id"]] = damage_by_target.get("target", 0) or 0
            damage_by_target[event["target_id"]] = damage_by_target.get(event["target_id"], 0) + int(event.get("applied", 0) or 0)
        elif event.get("type") == "combat.resource_changed" and int(event.get("delta", 0) or 0) > 0:
            healed += int(event["delta"])
    parts = []
    if damage_by_target:
        parts.append("、".join(
            f"{_entity_display_name(instance, target)} -{amount}"
            for target, amount in sorted(damage_by_target.items())
        ))
    if healed:
        parts.append(f"恢复 {healed}")
    summary = localized_text(getattr(instance, "language", "") or "", {
        "en": f"Combat: {_entity_display_name(instance, actor_entity)} used {decl.name}"
              + (f" ({'; '.join(parts)})" if parts else ""),
        "zh-CN": f"战斗扩展：{_entity_display_name(instance, actor_entity)} 使用了 {decl.name}"
                 + (f"（{'；'.join(parts)}）" if parts else ""),
        "ja": f"戦闘：{_entity_display_name(instance, actor_entity)} が {decl.name} を使用"
              + (f"（{'; '.join(parts)}）" if parts else ""),
    })
    for entry in reversed(instance.log):
        if int(entry.get("round", -1) or -1) == int(instance.round_number):
            changes = entry.setdefault("state_changes", [])
            if summary not in changes:
                changes.append(summary)
            break

def _scheduler_combat_state(
    instance: GameInstance,
    config: CombatExtensionConfig,
    buffs: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """构建调度器视角的 plain combat-state：实体存活、速度、先攻修正。

    基础速度来自模板声明的 speed_formula（按实体属性求值，缺省常数 25）；
    生效 buff（stat 与 speed_stat 一致）叠加其上。
    """

    actors: dict[str, Any] = {}
    for entity_id, pools in _ensure_state(instance, config)[0].entities.items():
        sheet: dict[str, Any] = {}
        if entity_id.startswith("player:"):
            uid = entity_id.removeprefix("player:")
            sheet = instance.get_character_sheet(uid)
        elif entity_id.startswith("npc:"):
            npc = instance.npcs.get(entity_id.removeprefix("npc:")) or {}
            sheet = {"attributes": npc.get("attributes") or {}}
        attributes = {
            str(key): int(value)
            for key, value in (sheet.get("attributes") or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        context = FormulaContext(
            attributes=attributes, derived_stats={}, resources={},
            equipment_stats={}, actor_id=entity_id,
        )
        speed = 25
        if config.speed_formula:
            try:
                speed = evaluate_formula_bound(config.speed_formula, context)
            except ValueError:
                speed = 25
        speed += sum(
            int(buff.get("delta", 0) or 0)
            for buff in buffs
            if buff.get("entity_id") == entity_id
            and buff.get("stat") == config.scheduler.speed_stat
            and int(buff.get("remaining", 0) or 0) > 0
        )
        alive = not entity_id.startswith("player:") or not instance.is_dead(
            entity_id.removeprefix("player:")
        )
        actors[entity_id] = {
            "alive": bool(alive),
            config.scheduler.speed_stat: max(0, speed),
            "initiative_modifier": 0,
        }
    return {"actors": actors}


def scheduler_advance(
    instance: GameInstance,
    rule: Any,
) -> dict[str, Any]:
    """GM 推进 ATB 时间：gauge 按速度累积直至有人就绪；buff 时长随推进扣减。"""

    config = load_config(rule)
    if config is None or config.scheduler is None:
        return {"ok": False, "code": "COMBAT_EXTENSION_NOT_CONFIGURED",
                "error": "当前规则未声明战斗扩展调度器"}
    state, scheduler_state = _ensure_state(instance, config)
    scheduler = scheduler_from_config(config.scheduler)
    if scheduler_state is None:
        initialized = scheduler.initialize(
            _scheduler_combat_state(instance, config, state.buffs),
        )
        scheduler_state = initialized.state
        events = list(initialized.events)
    else:
        events = []
    combat_state = _scheduler_combat_state(instance, config, state.buffs)
    result = scheduler.advance(scheduler_state, combat_state)
    # buff 时长随推进扣减（仅作用于调度相关 stat 的修正）。
    ticked: list[dict[str, Any]] = []
    for buff in state.buffs:
        remaining = int(buff.get("remaining", 0) or 0) - 1
        if remaining > 0:
            ticked.append({**buff, "remaining": remaining})
    payload = {
        "schema_version": _COMBAT_EXTENSION_SCHEMA,
        "scheduler": result.state.to_dict(),
        "buffs": [dict(buff) for buff in ticked],
        "pools": {
            entity_id: {
                resource_id: {"current": pool.current, "maximum": pool.maximum,
                              "minimum": pool.minimum}
                for resource_id, pool in entity_pools.items()
            }
            for entity_id, entity_pools in state.entities.items()
        },
    }
    instance.combat_extension = payload
    events = [*events, *result.events]
    return {
        "ok": True,
        "events": events,
        "ready": list(result.ready_actor_ids),
        "gauges": dict(result.state.gauges),
    }

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
    entity_names: dict[str, str] = {}
    for uid, player in instance.players.items():
        entity_names[f"player:{uid}"] = str(
            player.get("character_name") or uid)
    for npc_id, npc in instance.npcs.items():
        entity_names[f"npc:{npc_id}"] = str(
            npc.get("character_name") or npc.get("name") or npc_id)
    return {
        "scheduler": {
            "kind": config.scheduler.kind if config.scheduler else None,
            "ready": list(scheduler_state.ready) if scheduler_state else [],
            "gauges": dict(scheduler_state.gauges) if scheduler_state else {},
        },
        "entities": sorted(entity_names),
        "entity_names": entity_names,
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

    scheduler_instance = (
        scheduler_from_config(config.scheduler)
        if config.scheduler and _scheduler is not None
        else None
    )
    if scheduler_instance is not None and requested_actor not in scheduler_instance.available_actors(
        _scheduler, _scheduler_combat_state(instance, config, state.buffs),
    ):
        return {"ok": False, "code": "SCHEDULER_NOT_READY",
                "error": "该实体尚未就绪（ATB 行动条未满），请等待 GM 推进时间"}
    if decl.consume_item is not None and requested_actor.startswith("player:"):
        uid = requested_actor.removeprefix("player:")
        sheet = instance.get_character_sheet(uid)
        inventory = sheet.get("inventory")
        if not isinstance(inventory, list):
            return {"ok": False, "code": "ITEM_MISSING",
                    "error": f"缺少物品: {decl.consume_item.item} x{decl.consume_item.qty}"}
        remaining = decl.consume_item.qty
        kept: list[Any] = []
        for row in inventory:
            if (remaining > 0 and isinstance(row, dict)
                    and str(row.get("name") or "") == decl.consume_item.item):
                qty = int(row.get("qty", 1) or 1)
                take = min(qty, remaining)
                remaining -= take
                if qty - take > 0:
                    kept.append({**row, "qty": qty - take})
                # 整行耗尽 → 直接丢弃，不留空壳行。
                continue
            kept.append(row)
        if remaining > 0:
            return {"ok": False, "code": "ITEM_MISSING",
                    "error": f"缺少物品: {decl.consume_item.item} x{decl.consume_item.qty}"}
        sheet["inventory"] = kept
        instance.set_character_sheet(uid, sheet)

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
        "buffs": [dict(buff) for buff in outcome.state.buffs],
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

    if scheduler_instance is not None:
        consumed = scheduler_instance.consume_turn(
            _scheduler,
            _scheduler_combat_state(instance, config, outcome.state.buffs),
            requested_actor,
        )
        payload["scheduler"] = consumed.state.to_dict()
    _append_public_summary(instance, config, requested_actor, decl, outcome.events)
    return {
        "ok": True,
        "intent_id": str(intent.get("intent_id") or ""),
        "events": list(outcome.events),
        "action": {"id": decl.action_id, "name": decl.name},
    }
