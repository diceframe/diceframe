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

import hashlib
import json
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
from src.engine.game_instance import GameInstance, GameState

_COMBAT_EXTENSION_SCHEMA = 1


def _raw_extension_payload(instance: GameInstance) -> Mapping[str, Any]:
    """Return a supported persisted payload, or an empty safe view.

    Saves are user-importable data.  A future/invalid schema must never be
    interpreted as the current schema and then overwritten by a combat write.
    The legacy payloads produced before ``schema_version`` was added remain
    readable because the absent version is the original shape.
    """

    raw = getattr(instance, "combat_extension", None)
    if not isinstance(raw, Mapping):
        return {}
    version = raw.get("schema_version")
    if version is not None and (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != _COMBAT_EXTENSION_SCHEMA
    ):
        return {}
    return raw


def _extension_schema_supported(instance: GameInstance) -> bool:
    raw = getattr(instance, "combat_extension", None)
    if not isinstance(raw, Mapping):
        return True
    version = raw.get("schema_version")
    return version is None or (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == _COMBAT_EXTENSION_SCHEMA
    )


class CombatExtensionNotConfigured(ValueError):
    """当前规则未声明 combat 能力。"""


def load_config(rule: Any) -> CombatExtensionConfig | None:
    template = getattr(rule, "template", None)
    if not isinstance(template, Mapping) and not isinstance(template, dict):
        return None
    try:
        return combat_extension_from_template(template)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        # Rule templates are editable persisted data. A malformed combat
        # declaration disables this optional capability instead of turning
        # game-detail or action routes into a server error.
        return None


def _sheet_stat(sheet: Mapping[str, Any], stat: str) -> int:
    value = sheet.get(stat, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _player_record(instance: GameInstance, uid: str) -> Mapping[str, Any]:
    raw = instance.players.get(uid)
    return raw if isinstance(raw, Mapping) else {}


def _player_sheet(instance: GameInstance, uid: str) -> Mapping[str, Any]:
    raw = _player_record(instance, uid).get("character_sheet")
    return raw if isinstance(raw, Mapping) else {}


def _player_is_dead(instance: GameInstance, uid: str) -> bool:
    return bool(_player_sheet(instance, uid).get("deceased", False))


def _seed_pools(
    instance: GameInstance,
    config: CombatExtensionConfig,
    entity_id: str,
) -> dict[str, ResourcePool]:
    """从服务端权威数据播种资源池（角色卡字段 / 战斗会话状态）。"""

    pools: dict[str, ResourcePool] = {}
    payload = _raw_extension_payload(instance)
    raw_previous = payload.get("pools", {})
    previous_by_entity = raw_previous if isinstance(raw_previous, Mapping) else {}
    previous = previous_by_entity.get(entity_id, {})
    if not isinstance(previous, Mapping):
        previous = {}
    sheet: dict[str, Any] = {}
    if entity_id.startswith("player:"):
        uid = entity_id.removeprefix("player:")
        sheet = dict(_player_sheet(instance, uid))
    elif entity_id.startswith("npc:"):
        npc_id = entity_id.removeprefix("npc:")
        npc = instance.npcs.get(npc_id) or {}
        if isinstance(npc, Mapping):
            sheet = dict(npc)
            # Legacy NPC records usually have HP but no explicit maximum.
            # Their encounter-start HP is the healing cap for this combat.
            if "hp" in sheet and "max_hp" not in sheet:
                sheet["max_hp"] = _sheet_stat(sheet, "hp")
    for decl in config.resources:
        prior = previous.get(decl.resource_id) or {}
        if not isinstance(prior, Mapping):
            prior = {}
        current = prior.get("current")
        maximum = decl.maximum
        if decl.source == "hp":
            # HP and special stats are carried by the character/NPC record.  A
            # persisted pool is only the projection of that canonical value;
            # prefer the record when it is present so an edit/rest cannot be
            # silently overwritten by an old combat snapshot.
            if "hp" in sheet:
                current = _sheet_stat(sheet, "hp")
            elif current is None:
                current = 0
            if maximum is None:
                maximum = _sheet_stat(sheet, "max_hp") or None
        elif decl.source == "special_stat" and decl.stat:
            if str(decl.stat) in sheet:
                current = _sheet_stat(sheet, str(decl.stat))
            elif current is None:
                current = 0
            if maximum is None:
                maximum = _sheet_stat(sheet, f"max_{decl.stat}") or None
        elif decl.source == "combat_state":
            # Persisted combat-only values are validated and overlaid by
            # _ensure_state below; the seed itself is always safe.
            current = 0
        safe_maximum = (
            int(maximum)
            if isinstance(maximum, int) and not isinstance(maximum, bool)
            and maximum >= 0
            else None
        )
        safe_current = (
            current
            if isinstance(current, int) and not isinstance(current, bool)
            else 0
        )
        safe_current = max(0, safe_current)
        if safe_maximum is not None:
            safe_current = min(safe_current, safe_maximum)
        pools[decl.resource_id] = ResourcePool(
            resource_id=decl.resource_id,
            current=safe_current,
            maximum=safe_maximum,
            minimum=0,
        )
    return pools


def _record_with_pool_values(
    instance: GameInstance,
    config: CombatExtensionConfig,
    entity_id: str,
    pools: Mapping[str, ResourcePool],
) -> dict[str, Any] | None:
    """Return a staged player/NPC record update without mutating the aggregate."""

    if entity_id.startswith("player:"):
        uid = entity_id.removeprefix("player:")
        if uid not in instance.players:
            return None
        record = deepcopy(dict(_player_sheet(instance, uid)))
    elif entity_id.startswith("npc:"):
        npc = instance.npcs.get(entity_id.removeprefix("npc:"))
        if not isinstance(npc, Mapping):
            return None
        record = deepcopy(dict(npc))
    else:
        return None
    changed = False
    for decl in config.resources:
        pool = pools.get(decl.resource_id)
        if pool is None:
            continue
        if decl.source == "hp":
            record["hp"] = pool.current
            changed = True
        elif decl.source == "special_stat" and decl.stat:
            record[str(decl.stat)] = pool.current
            changed = True
    return record if changed else None


def _safe_pool_from_payload(raw: Any, fallback: ResourcePool) -> ResourcePool:
    """Rebuild one persisted pool, treating malformed nested data as absent."""

    if not isinstance(raw, Mapping):
        return fallback
    current = raw.get("current", fallback.current)
    maximum = raw.get("maximum", fallback.maximum)
    minimum = raw.get("minimum", fallback.minimum)
    if isinstance(current, bool) or not isinstance(current, int):
        return fallback
    if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int)):
        return fallback
    if isinstance(minimum, bool) or not isinstance(minimum, int):
        return fallback
    try:
        return ResourcePool(
            resource_id=fallback.resource_id,
            current=current,
            maximum=maximum,
            minimum=minimum,
        )
    except (TypeError, ValueError):
        return fallback


def _ensure_state(
    instance: GameInstance,
    config: CombatExtensionConfig,
) -> tuple[CombatState, SchedulerState | None]:
    payload = _raw_extension_payload(instance)
    # 玩家始终参与（队伍规模有界）；NPC 按需参与：只有已被目标锁定过
    # （payload 里已有池）的 NPC 才进入状态，避免百人 NPC 存档与面板爆炸。
    raw_prior_pools = payload.get("pools", {})
    prior_pools = raw_prior_pools if isinstance(raw_prior_pools, Mapping) else {}
    entity_ids = sorted(
        {f"player:{uid}" for uid in instance.players}
        | {f"npc:{npc_id}" for npc_id in instance.npcs
           if f"npc:{npc_id}" in prior_pools}
    )
    pools_payload: dict[str, Mapping[str, Any]] = {}
    for entity_id in entity_ids:
        raw_entity = prior_pools.get(entity_id, {})
        pools_payload[entity_id] = raw_entity if isinstance(raw_entity, Mapping) else {}
    pools = {
        entity_id: _seed_pools(instance, config, entity_id)
        for entity_id in entity_ids
    }
    for entity_id, declared in pools.items():
        for resource_id, pool in declared.items():
            resource_decl = next(
                (item for item in config.resources if item.resource_id == resource_id),
                None,
            )
            if resource_decl is None or resource_decl.source != "combat_state":
                continue
            raw = pools_payload.get(entity_id, {}).get(resource_id)
            if raw is not None:
                declared[resource_id] = _safe_pool_from_payload(raw, pool)
    scheduler_state = None
    raw_scheduler = payload.get("scheduler")
    if isinstance(raw_scheduler, dict) and raw_scheduler:
        try:
            scheduler_state = SchedulerState.from_dict(raw_scheduler)
            if config.scheduler is None or scheduler_state.kind != config.scheduler.kind:
                scheduler_state = None
        except (TypeError, ValueError, AttributeError, KeyError):
            # A malformed persisted scheduler disables scheduling until the
            # next explicit initialization; it must never crash projection.
            scheduler_state = None
    raw_buffs = payload.get("buffs")
    if not isinstance(raw_buffs, list):
        raw_buffs = []
    buffs: list[dict[str, Any]] = []
    for item in raw_buffs:
        if not isinstance(item, Mapping):
            continue
        entity_id = item.get("entity_id")
        stat = item.get("stat")
        delta = item.get("delta")
        remaining = item.get("remaining")
        if (
            not isinstance(entity_id, str) or not entity_id
            or not isinstance(stat, str) or not stat
            or isinstance(delta, bool) or not isinstance(delta, int)
            or isinstance(remaining, bool) or not isinstance(remaining, int)
            or remaining <= 0
        ):
            continue
        buffs.append(dict(item))
    return (
        CombatState(
            entities=pools,
            hp_resource=config.hp_resource,
            barriers=config.barrier_resources,
            buffs=tuple(buffs),
        ),
        scheduler_state,
    )


def _formula_context(
    instance: GameInstance,
    config: CombatExtensionConfig,
    entity_id: str,
    target_id: str | None,
    state: CombatState | None = None,
) -> FormulaContext:
    """Build a formula context from the authoritative player/NPC record."""

    if entity_id.startswith("player:"):
        sheet = _player_sheet(instance, entity_id.removeprefix("player:"))
    elif entity_id.startswith("npc:"):
        raw_npc = instance.npcs.get(entity_id.removeprefix("npc:"))
        sheet = raw_npc if isinstance(raw_npc, Mapping) else {}
    else:
        sheet = {}
    raw_attributes = sheet.get("attributes")
    if not isinstance(raw_attributes, Mapping):
        raw_attributes = {}
    attributes = {
        str(key): int(value)
        for key, value in raw_attributes.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    pools = (state or _ensure_state(instance, config)[0]).entities.get(entity_id, {})
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
        return str(_player_record(instance, uid).get("character_name") or uid)
    if entity_id.startswith("npc:"):
        npc_id = entity_id.removeprefix("npc:")
        raw_npc = instance.npcs.get(npc_id)
        npc = raw_npc if isinstance(raw_npc, Mapping) else {}
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
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "combat.damage_applied":
            target_id = event.get("target_id")
            if isinstance(target_id, str) and target_id:
                damage_by_target[target_id] = (
                    damage_by_target.get(target_id, 0)
                    + int(event.get("applied", 0) or 0)
                )
        elif event.get("type") == "combat.resource_changed" and int(event.get("delta", 0) or 0) > 0:
            healed += int(event["delta"])
    language = str(getattr(instance, "language", "") or "")
    parts: list[str] = []
    if damage_by_target:
        if language.lower().startswith("en"):
            parts.append("; ".join(
                f"{_entity_display_name(instance, target)} -{amount}"
                for target, amount in sorted(damage_by_target.items())
            ))
        elif language.lower().startswith("ja"):
            parts.append("、".join(
                f"{_entity_display_name(instance, target)} -{amount}"
                for target, amount in sorted(damage_by_target.items())
            ))
        else:
            parts.append("、".join(
                f"{_entity_display_name(instance, target)} -{amount}"
                for target, amount in sorted(damage_by_target.items())
            ))
    if healed:
        parts.append(
            f"healed {healed}" if language.lower().startswith("en")
            else f"{healed} 回復" if language.lower().startswith("ja")
            else f"恢复 {healed}"
        )
    summary = localized_text(language, {
        "en": f"Combat: {_entity_display_name(instance, actor_entity)} used {decl.name}"
              + (f" ({'; '.join(parts)})" if parts else ""),
        "zh-CN": f"战斗扩展：{_entity_display_name(instance, actor_entity)} 使用了 {decl.name}"
                 + (f"（{'；'.join(parts)}）" if parts else ""),
        "ja": f"戦闘：{_entity_display_name(instance, actor_entity)} が {decl.name} を使用"
              + (f"（{'; '.join(parts)}）" if parts else ""),
    })
    raw_log = instance.log if isinstance(instance.log, list) else []
    try:
        current_round = int(instance.round_number)
    except (TypeError, ValueError):
        current_round = 0
    for entry in reversed(raw_log):
        if not isinstance(entry, dict):
            continue
        try:
            entry_round = int(entry.get("round", -1) or -1)
        except (TypeError, ValueError):
            continue
        if entry_round == current_round:
            changes = entry.get("state_changes")
            if not isinstance(changes, list):
                changes = []
                entry["state_changes"] = changes
            if summary not in changes:
                changes.append(summary)
            return
    payload = instance.combat_extension
    if isinstance(payload, dict):
        pending = payload.setdefault("pending_summaries", [])
        if isinstance(pending, list) and summary not in pending:
            pending.append(summary)
            del pending[:-50]

def _scheduler_combat_state(
    instance: GameInstance,
    config: CombatExtensionConfig,
    buffs: tuple[dict[str, Any], ...],
    state: CombatState | None = None,
) -> dict[str, Any]:
    """构建调度器视角的 plain combat-state：实体存活、速度、先攻修正。

    基础速度来自模板声明的 speed_formula（按实体属性求值，缺省常数 25）；
    生效 buff（stat 与 speed_stat 一致）叠加其上。
    """

    actors: dict[str, Any] = {}
    for entity_id, pools in (state or _ensure_state(instance, config)[0]).entities.items():
        sheet: dict[str, Any] = {}
        if entity_id.startswith("player:"):
            uid = entity_id.removeprefix("player:")
            sheet = dict(_player_sheet(instance, uid))
        elif entity_id.startswith("npc:"):
            npc = instance.npcs.get(entity_id.removeprefix("npc:")) or {}
            if isinstance(npc, Mapping):
                sheet = {"attributes": npc.get("attributes") or {}}
        raw_attributes = sheet.get("attributes")
        if not isinstance(raw_attributes, Mapping):
            raw_attributes = {}
        attributes = {
            str(key): int(value)
            for key, value in raw_attributes.items()
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
        hp_pool = pools.get(config.hp_resource)
        alive = hp_pool is None or hp_pool.current > hp_pool.minimum
        if entity_id.startswith("player:"):
            alive = alive and not _player_is_dead(
                instance, entity_id.removeprefix("player:")
            )
        actors[entity_id] = {
            "alive": bool(alive),
            config.scheduler.speed_stat: max(0, speed),
            "initiative_modifier": 0,
        }
    return {"actors": actors}


def _reconcile_scheduler_state(
    scheduler: Any,
    scheduler_state: SchedulerState,
    combat_state: Mapping[str, Any],
) -> SchedulerState:
    """Bring a persisted scheduler roster up to date with engaged entities.

    NPCs join the extension lazily when first targeted.  A scheduler created
    before that moment must still be able to schedule them later.  The helper
    preserves the current holder where possible, adds new threshold gauges at
    zero, and lets each scheduler's public ``initialize`` method provide its
    deterministic roster ordering.
    """

    actors = combat_state.get("actors")
    if not isinstance(actors, Mapping):
        return scheduler_state
    actor_ids = {str(actor_id) for actor_id in actors}
    if scheduler_state.kind == "threshold":
        order = tuple(
            actor_id for actor_id in scheduler_state.order if actor_id in actor_ids
        )
        order = (*order, *sorted(actor_ids - set(order)))
        gauges = {
            actor_id: scheduler_state.gauges.get(actor_id, 0)
            for actor_id in actor_ids
        }
        ready = tuple(
            actor_id
            for actor_id in scheduler_state.ready
            if actor_id in actor_ids
            and gauges.get(actor_id, 0) >= getattr(scheduler.config, "threshold", 0)
        )
        return replace(
            scheduler_state,
            order=order,
            gauges=gauges,
            ready=ready,
        )

    try:
        desired = scheduler.initialize(combat_state).state.order
    except (TypeError, ValueError, AttributeError, KeyError):
        return scheduler_state
    current = None
    if 0 <= scheduler_state.turn_index < len(scheduler_state.order):
        current = scheduler_state.order[scheduler_state.turn_index]
    return replace(
        scheduler_state,
        order=desired,
        # -1 lets the next advance select index zero when the former holder
        # disappeared; normal persisted states always have a non-negative index.
        turn_index=desired.index(current) if current in desired else -1,
    )


def scheduler_advance(
    instance: GameInstance,
    rule: Any,
) -> dict[str, Any]:
    """GM 推进 ATB 时间：gauge 按速度累积直至有人就绪；buff 时长随推进扣减。"""

    config = load_config(rule)
    if config is None or config.scheduler is None:
        return {"ok": False, "code": "COMBAT_EXTENSION_NOT_CONFIGURED",
                "error": "当前规则未声明战斗扩展调度器"}
    if not _extension_schema_supported(instance):
        return {"ok": False, "code": "COMBAT_STATE_UNSUPPORTED",
                "error": "战斗扩展存档版本不受当前服务支持，请先迁移或重置战斗状态"}
    if instance.state != GameState.ACTIVE_ACTION:
        return {"ok": False, "code": "ROUND_NOT_ACCEPTING_ACTIONS",
                "error": "当前不在玩家行动阶段"}
    try:
        state, scheduler_state = _ensure_state(instance, config)
        scheduler = scheduler_from_config(config.scheduler)
        initialized_now = scheduler_state is None
        if initialized_now:
            initialized = scheduler.initialize(
                _scheduler_combat_state(instance, config, state.buffs, state),
            )
            scheduler_state = initialized.state
            events = list(initialized.events)
        else:
            events = []
        combat_state = _scheduler_combat_state(instance, config, state.buffs, state)
        if not initialized_now:
            scheduler_state = _reconcile_scheduler_state(
                scheduler, scheduler_state, combat_state,
            )
        # Round-robin and initiative schedulers expose their first actor from
        # initialize(). Advancing immediately would skip that actor. Threshold
        # scheduling is different: initialization only creates zero gauges, so
        # the first call must also perform the first time advance.
        if initialized_now and config.scheduler.kind != "threshold":
            result = initialized
            advanced = False
        else:
            result = scheduler.advance(scheduler_state, combat_state)
            # A threshold scheduler with an existing ready queue can return
            # its state unchanged. That is a no-op, so it must not consume a
            # buff duration or claim that time advanced.
            advanced = bool(result.events)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        return {"ok": False, "code": "SCHEDULER_REJECTED", "error": str(exc)}
    # buff 时长随推进扣减（仅作用于调度相关 stat 的修正）。
    ticked: list[dict[str, Any]] = []
    for buff in state.buffs:
        remaining = int(buff.get("remaining", 0) or 0) - (1 if advanced else 0)
        if remaining > 0:
            ticked.append({**buff, "remaining": remaining})
    old_payload = _raw_extension_payload(instance)
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
    # Idempotency records are action-specific but must survive scheduler
    # writes; dropping them here would make a retried action execute again.
    if isinstance(old_payload.get("intents"), Mapping):
        payload["intents"] = deepcopy(dict(old_payload["intents"]))
    if isinstance(old_payload.get("pending_summaries"), list):
        payload["pending_summaries"] = list(old_payload["pending_summaries"])[-50:]
    # Capture only after all scheduler calculations succeed. The snapshot is
    # still pre-mutation, while a rejected advance leaves no bookkeeping write.
    capture = getattr(instance, "capture_combat_extension_snapshot", None)
    if callable(capture):
        capture()
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
    ready: list[str] = []
    if scheduler_state is not None and config.scheduler is not None:
        try:
            scheduler = scheduler_from_config(config.scheduler)
            scheduler_state = _reconcile_scheduler_state(
                scheduler,
                scheduler_state,
                _scheduler_combat_state(instance, config, state.buffs, state),
            )
            ready = list(scheduler.available_actors(
                scheduler_state,
                _scheduler_combat_state(instance, config, state.buffs, state),
            ))
        except (TypeError, ValueError, AttributeError, KeyError):
            # A malformed persisted scheduler is already treated as inactive
            # by _ensure_state; keep projection fail-closed if actor data is
            # also malformed.
            ready = []
    actions = [
        {
            "id": decl.action_id,
            "kind": decl.kind,
            "name": decl.name,
            "costs": [
                {"resource": cost.resource, "amount": deepcopy(dict(cost.amount))}
                for cost in decl.costs
            ],
            **(
                {"consume_item": {"item": decl.consume_item.item, "qty": decl.consume_item.qty}}
                if decl.consume_item is not None else {}
            ),
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
        player_record = player if isinstance(player, Mapping) else {}
        entity_names[f"player:{uid}"] = str(
            player_record.get("character_name") or uid)
    for npc_id, npc in instance.npcs.items():
        npc_record = npc if isinstance(npc, Mapping) else {}
        entity_names[f"npc:{npc_id}"] = str(
            npc_record.get("character_name") or npc_record.get("name") or npc_id)
    return {
        "scheduler": {
            "kind": config.scheduler.kind if config.scheduler else None,
            "ready": ready,
            "gauges": dict(scheduler_state.gauges) if scheduler_state else {},
            "participants": list(scheduler_state.order) if scheduler_state else [],
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

    if not isinstance(intent, Mapping):
        return {"ok": False, "code": "INVALID_INTENT", "error": "动作 intent 必须是对象"}
    raw_intent_id = intent.get("intent_id")
    intent_id = raw_intent_id.strip() if isinstance(raw_intent_id, str) else ""
    if not intent_id or len(intent_id) > 160:
        return {"ok": False, "code": "INVALID_INTENT_ID", "error": "动作 intent 必须提供有效的 intent_id"}

    raw_action_id = intent.get("action_id")
    action_id = raw_action_id.strip() if isinstance(raw_action_id, str) else ""
    if not action_id:
        return {"ok": False, "code": "INVALID_ACTION_ID", "error": "动作必须提供有效的 action_id"}
    raw_actor = intent.get("actor_id")
    if raw_actor is None:
        requested_actor = f"player:{actor_uid}"
    elif isinstance(raw_actor, str) and raw_actor.strip():
        requested_actor = raw_actor.strip()
    else:
        return {"ok": False, "code": "INVALID_ACTOR_ID", "error": "actor_id 必须是非空字符串"}
    raw_targets = intent.get("target_ids")
    if raw_targets is None:
        target_ids_for_fingerprint: tuple[str, ...] = ()
    elif not isinstance(raw_targets, list):
        return {"ok": False, "code": "INVALID_TARGETS", "error": "target_ids 必须是字符串列表"}
    elif len(raw_targets) > 1:
        # The phase-2 contract has no server-declared area-target policy.
        # Accepting an arbitrary list would let one paid action apply its
        # effect repeatedly, so multi-target actions must be introduced with
        # an explicit ruleset capability first.
        return {"ok": False, "code": "INVALID_TARGETS", "error": "当前动作只支持一个目标"}
    elif any(not isinstance(item, str) or not item.strip() for item in raw_targets):
        return {"ok": False, "code": "INVALID_TARGETS", "error": "target_ids 必须是非空字符串列表"}
    else:
        target_ids_for_fingerprint = tuple(item.strip() for item in raw_targets)
    fingerprint = _intent_fingerprint(
        intent_id=intent_id,
        action_id=action_id,
        actor_id=requested_actor,
        target_ids=target_ids_for_fingerprint,
        actor_uid=actor_uid,
        viewer_is_gm=viewer_is_gm,
    )
    replay = _replay_intent(instance, intent_id, fingerprint, action_id)
    if replay is not None:
        return replay

    config = load_config(rule)
    if config is None:
        return {"ok": False, "code": "COMBAT_EXTENSION_NOT_CONFIGURED",
                "error": "当前规则未声明战斗扩展"}
    if not _extension_schema_supported(instance):
        return {"ok": False, "code": "COMBAT_STATE_UNSUPPORTED",
                "error": "战斗扩展存档版本不受当前服务支持，请先迁移或重置战斗状态"}
    decl = config.action(action_id)
    if decl is None:
        return {"ok": False, "code": "ACTION_NOT_FOUND", "error": "未知战斗动作"}

    is_gm_entity = requested_actor.startswith(("npc:", "enemy:"))
    if not viewer_is_gm and requested_actor != f"player:{actor_uid}":
        return {"ok": False, "code": "ACTOR_FORBIDDEN",
                "error": "只能驱动自己的战斗实体"}
    if is_gm_entity and not viewer_is_gm:
        return {"ok": False, "code": "ACTOR_FORBIDDEN", "error": "仅 GM 可驱动该实体"}
    if instance.state != GameState.ACTIVE_ACTION:
        return {"ok": False, "code": "ROUND_NOT_ACCEPTING_ACTIONS",
                "error": "当前不在玩家行动阶段"}

    target_ids = target_ids_for_fingerprint

    source_field_names: list[str] = []
    for item in config.resources:
        if item.source == "hp":
            source_field_names.extend(("hp", "max_hp"))
        elif item.source == "special_stat" and item.stat:
            source_field_names.extend((str(item.stat), f"max_{item.stat}"))
    source_fields = tuple(dict.fromkeys(source_field_names))
    involved = set(target_ids or (requested_actor,)) | {requested_actor}
    entity_fields = {entity_id: source_fields for entity_id in involved}
    if decl.consume_item is not None:
        entity_fields[requested_actor] = (*source_fields, "inventory")

    state, _scheduler = _ensure_state(instance, config)
    raw_scheduler_marker = _raw_extension_payload(instance).get(
        "scheduler", None,
    )
    if (
        config.scheduler is not None
        and raw_scheduler_marker is not None
        and _scheduler is None
    ):
        return {
            "ok": False,
            "code": "SCHEDULER_STATE_INVALID",
            "error": "战斗调度器状态损坏，请由 GM 重新初始化行动条",
        }
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
    if scheduler_instance is not None:
        scheduler_state = _reconcile_scheduler_state(
            scheduler_instance,
            _scheduler,
            _scheduler_combat_state(instance, config, state.buffs, state),
        )
        _scheduler = scheduler_state
        if requested_actor not in scheduler_instance.available_actors(
            scheduler_state,
            _scheduler_combat_state(instance, config, state.buffs, state),
        ):
            return {"ok": False, "code": "SCHEDULER_NOT_READY",
                    "error": "该实体尚未就绪（ATB 行动条未满），请等待 GM 推进时间"}

    actor_pool = state.entities.get(requested_actor, {}).get(config.hp_resource)
    if (
        actor_pool is not None and actor_pool.current <= actor_pool.minimum
    ) or (
        requested_actor.startswith("player:")
        and _player_is_dead(instance, requested_actor.removeprefix("player:"))
    ):
        return {"ok": False, "code": "ACTOR_DEFEATED",
                "error": "该实体已无法行动"}
    staged_records: dict[str, dict[str, Any]] = {}
    if decl.consume_item is not None:
        if requested_actor.startswith("player:"):
            uid = requested_actor.removeprefix("player:")
            record = deepcopy(dict(_player_sheet(instance, uid)))
        elif requested_actor.startswith("npc:"):
            raw_npc = instance.npcs.get(requested_actor.removeprefix("npc:"))
            record = deepcopy(dict(raw_npc)) if isinstance(raw_npc, Mapping) else {}
        else:
            record = {}
        inventory = record.get("inventory")
        if not isinstance(inventory, list):
            return {"ok": False, "code": "ITEM_MISSING",
                    "error": f"缺少物品: {decl.consume_item.item} x{decl.consume_item.qty}"}
        remaining = decl.consume_item.qty
        kept: list[Any] = []
        for row in inventory:
            if (remaining > 0 and isinstance(row, dict)
                    and str(row.get("name") or "") == decl.consume_item.item):
                raw_qty = row.get("qty", 1)
                if isinstance(raw_qty, bool) or not isinstance(raw_qty, int) or raw_qty <= 0:
                    kept.append(row)
                    continue
                qty = raw_qty
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
        record["inventory"] = kept
        staged_records[requested_actor] = record

    context = _formula_context(
        instance, config, requested_actor,
        target_ids[0] if target_ids else None, state,
    )
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

    # Calculate scheduler consumption before committing any state.  A failed
    # consume must leave both the action result and inventory untouched.
    consumed_scheduler = None
    if scheduler_instance is not None:
        try:
            consumed_scheduler = scheduler_instance.consume_turn(
                _scheduler,
                _scheduler_combat_state(
                    instance, config, outcome.state.buffs, outcome.state,
                ),
                requested_actor,
            )
        except (TypeError, ValueError, AttributeError, KeyError) as exc:
            return {"ok": False, "code": "ACTION_REJECTED", "error": str(exc)}

    # Stage all character/NPC writes.  Only entities whose pools changed are
    # written back; this preserves an external rest/edit on untouched actors.
    changed_entities = {
        entity_id
        for entity_id, entity_pools in outcome.state.entities.items()
        if entity_id not in state.entities
        or entity_pools != state.entities.get(entity_id)
    }
    for entity_id in changed_entities:
        staged = _record_with_pool_values(
            instance, config, entity_id, outcome.state.entities[entity_id],
        )
        if staged is not None:
            if entity_id in staged_records:
                # Preserve a pending inventory deduction while merging the
                # resource fields produced by the combat outcome.
                pending_inventory = staged_records[entity_id].get("inventory")
                staged_records[entity_id].update(staged)
                if pending_inventory is not None:
                    staged_records[entity_id]["inventory"] = pending_inventory
            else:
                staged_records[entity_id] = staged

    # 写回：combat_extension 池 + 角色卡承载字段。
    old_payload = _raw_extension_payload(instance)
    payload = {
        "schema_version": _COMBAT_EXTENSION_SCHEMA,
        "scheduler": (
            _scheduler.to_dict()
            if _scheduler
            else old_payload.get("scheduler")
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
    if consumed_scheduler is not None:
        payload["scheduler"] = consumed_scheduler.state.to_dict()
    if isinstance(old_payload.get("intents"), Mapping):
        payload["intents"] = deepcopy(dict(old_payload["intents"]))
    if isinstance(old_payload.get("pending_summaries"), list):
        payload["pending_summaries"] = list(old_payload["pending_summaries"])[-50:]

    result = {
        "ok": True,
        "intent_id": intent_id,
        "events": list(outcome.events),
        "action": {"id": decl.action_id, "name": decl.name},
    }
    _remember_intent(payload, intent_id, fingerprint, result)

    # Commit only after every fallible calculation has completed.
    capture = getattr(instance, "capture_combat_extension_snapshot", None)
    if callable(capture):
        capture(entity_fields)
    instance.combat_extension = payload
    for entity_id, record in staged_records.items():
        if entity_id.startswith("player:"):
            instance.set_character_sheet(entity_id.removeprefix("player:"), record)
        elif entity_id.startswith("npc:"):
            instance.npcs[entity_id.removeprefix("npc:")] = record
    _append_public_summary(instance, config, requested_actor, decl, outcome.events)
    return result


def _intent_fingerprint(
    *,
    intent_id: str,
    action_id: str,
    actor_id: str,
    target_ids: tuple[str, ...],
    actor_uid: str,
    viewer_is_gm: bool,
) -> str:
    payload = {
        "intent_id": intent_id,
        "action_id": action_id,
        "actor_id": actor_id,
        "target_ids": list(target_ids),
        "actor_uid": str(actor_uid),
        "viewer_is_gm": bool(viewer_is_gm),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _replay_intent(
    instance: GameInstance,
    intent_id: str,
    fingerprint: str,
    action_id: str,
) -> dict[str, Any] | None:
    payload = _raw_extension_payload(instance)
    records = payload.get("intents")
    if not isinstance(records, Mapping):
        return None
    if intent_id not in records:
        return None
    raw = records.get(intent_id)
    if not isinstance(raw, Mapping):
        return {"ok": False, "code": "INTENT_RECORD_INVALID",
                "error": "已保存的动作幂等记录损坏，请更换 intent_id"}
    if raw.get("fingerprint") != fingerprint:
        return {"ok": False, "code": "INTENT_ID_CONFLICT", "error": "intent_id 已用于另一条动作"}
    result = raw.get("result")
    action = result.get("action") if isinstance(result, Mapping) else None
    events = result.get("events") if isinstance(result, Mapping) else None
    if (
        not isinstance(result, Mapping)
        or result.get("ok") is not True
        or result.get("intent_id") != intent_id
        or not isinstance(action, Mapping)
        or action.get("id") != action_id
        or not isinstance(action.get("name"), str)
        or not isinstance(events, list)
        or any(not isinstance(event, Mapping) for event in events)
    ):
        return {"ok": False, "code": "INTENT_RECORD_INVALID",
                "error": "已保存的动作幂等记录损坏，请更换 intent_id"}
    replay = deepcopy(dict(result))
    replay["replayed"] = True
    return replay


def _remember_intent(
    payload: dict[str, Any],
    intent_id: str,
    fingerprint: str,
    result: Mapping[str, Any],
    *,
    max_records: int = 256,
) -> None:
    records = payload.get("intents")
    records = dict(records) if isinstance(records, Mapping) else {}
    records[intent_id] = {
        "fingerprint": fingerprint,
        "result": deepcopy(dict(result)),
    }
    if len(records) > max_records:
        # Dict insertion order is stable on supported Python versions; evict
        # the oldest records to keep save size bounded.
        for old_id in list(records)[: len(records) - max_records]:
            records.pop(old_id, None)
    payload["intents"] = records
