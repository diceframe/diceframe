"""Read-only projection of the world authority (FIX-05 §7.2/§7.3).

World Runtime 的**读半区**：

- ``src/engine/world_state.py`` 仍是唯一 authority（唯一写入口 ``apply_world_ops``）；
- 本模块只提供读取与投影，**不 import 任何定义层**（adventures / rulesets / webui /
  plugin_host），因此 Adventure 等定义层可以安全依赖它（§7.2：Adventure 可 import
  world.contracts / read projection，不可 import/write world authority）。

``project_visible_state`` 是 viewer-safe 的有界投影（§7.3）：

```text
facts（按 visibility 过滤）
relevant entities   ← 当前位置（含 located_at/connects 拓扑）/ 在场角色 / 相关进程参与者
relevant relations  ← 与以上实体相连
relevant processes  ← 在场角色参与 / 指定进程
clock / revision / viewer / truncated
```

不做"dump 全世界"：每个容器都有上界，超出即标记 ``truncated``；GM 私有 visibility
（``gm``）绝不进入非 GM 投影。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from src.engine.world.contracts import MAX_CLOCK_DAY, MINUTES_PER_DAY

WORLD_STATE_SCHEMA_VERSION = 2

# 有界投影上界（§7.3：不要 dump 全世界）。
MAX_PROJECTED_ENTITIES = 32
MAX_PROJECTED_RELATIONS = 32
MAX_PROJECTED_PROCESSES = 16

# 位置相关性只认拓扑关系（entity 记录没有 location 字段）。
_LOCATION_RELATION_KINDS = ("located_at", "connects")


def state_payload(state: Any) -> Mapping[str, Any] | None:
    """The current-schema payload, or None for a missing/corrupt container."""

    if not isinstance(state, Mapping):
        return None
    if state.get("schema_version") != WORLD_STATE_SCHEMA_VERSION:
        return None
    return state


def world_revision(state: Any) -> int:
    payload = state_payload(state)
    if payload is None:
        return 0
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return 0
    return revision


def world_clock(state: Any) -> dict[str, int]:
    """The logical world time; a corrupt clock reads as day 1, 00:00."""

    payload = state_payload(state)
    raw = payload.get("clock") if payload is not None else None
    if not isinstance(raw, Mapping):
        return {"day": 1, "minute": 0}
    day, minute = raw.get("day"), raw.get("minute")
    if (
        isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= MAX_CLOCK_DAY
        or isinstance(minute, bool) or not isinstance(minute, int)
        or not 0 <= minute < MINUTES_PER_DAY
    ):
        return {"day": 1, "minute": 0}
    return {"day": day, "minute": minute}


def world_facts(state: Any) -> dict[str, dict[str, Any]]:
    """All established facts (copies).  Malformed entries are not guessed."""

    payload = state_payload(state)
    raw = payload.get("facts") if payload is not None else None
    if not isinstance(raw, Mapping):
        return {}
    facts: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, Mapping) and "value" in value:
            facts[key] = deepcopy(dict(value))
    return facts


def fact_value(state: Any, key: str, default: Any = None) -> Any:
    """Read one fact value without exposing the mutable container."""

    fact = world_facts(state).get(str(key or ""))
    return default if fact is None else fact.get("value", default)


def fact_visibility(state: Any, key: str) -> str:
    """Visibility of one fact, or an empty string when it is not established."""

    fact = world_facts(state).get(str(key or ""))
    return str(fact.get("visibility") or "") if fact is not None else ""


def world_scheduled_events(state: Any) -> dict[str, dict[str, Any]]:
    payload = state_payload(state)
    raw = payload.get("scheduled_events") if payload is not None else None
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): deepcopy(dict(value))
        for key, value in raw.items()
        if isinstance(value, Mapping)
    }


def _world_record_container(state: Any, name: str) -> dict[str, dict[str, Any]]:
    """Defensive reader for the v2 record containers (entities/relations/processes)."""

    payload = state_payload(state)
    raw = payload.get(name) if payload is not None else None
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): deepcopy(dict(value))
        for key, value in raw.items()
        if isinstance(value, Mapping)
    }


def world_entities(state: Any) -> dict[str, dict[str, Any]]:
    """All registered entities (copies).  Malformed entries are not guessed."""

    return _world_record_container(state, "entities")


def world_relations(state: Any) -> dict[str, dict[str, Any]]:
    """All established relations (copies).  Malformed entries are not guessed."""

    return _world_record_container(state, "relations")


def world_processes(state: Any) -> dict[str, dict[str, Any]]:
    """All known processes (copies).  Malformed entries are not guessed."""

    return _world_record_container(state, "processes")


def _is_public(record: Mapping[str, Any]) -> bool:
    return str(record.get("visibility") or "public") != "gm"


def _bounded(items: Iterable[tuple[str, dict[str, Any]]], limit: int) -> tuple[dict[str, Any], bool]:
    kept: dict[str, Any] = {}
    truncated = False
    for key, value in items:
        if len(kept) >= limit:
            truncated = True
            break
        kept[key] = value
    return kept, truncated


def project_visible_state(
    instance: Any, *, viewer_uid: str = "", viewer_is_gm: bool = False,
    location: str = "", participants: Iterable[str] = (), process_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """World truth as one specific viewer is allowed to see it（FIX-05 §7.3）。

    - ``public`` facts 可见；``gm`` facts 只对 GM 可见；
    - 相关实体：位于 ``location``、属于 ``participants``、或相关进程的参与者；
    - 相关关系：连接以上任一实体；
    - 相关进程：``process_ids`` 指定，或参与者参与的 running 进程；
    - 每个容器有上界，超出标记 ``truncated``；玩家投影不含 ``gm`` 记录。
    - 损坏容器投影成空世界而不是抛错（读路径静默降级）。
    """

    state = getattr(instance, "world_state", None)
    facts = world_facts(state)
    entities = world_entities(state)
    relations = world_relations(state)
    processes = world_processes(state)
    if not viewer_is_gm:
        # 玩家投影：gm 可见性的事实/实体/关系/进程一律不出现（fail closed）。
        facts = {key: fact for key, fact in facts.items() if _is_public(fact)}
        entities = {key: item for key, item in entities.items() if _is_public(item)}
        relations = {key: item for key, item in relations.items() if _is_public(item)}
        processes = {key: item for key, item in processes.items() if _is_public(item)}

    relevant_ids = {str(key) for key in entities}
    participants_set = {str(item) for item in participants if str(item)}
    location_key = str(location or "")
    processes_of_interest = [str(item) for item in process_ids if str(item)]

    if location_key or participants_set or processes_of_interest:
        # 相关投影：只保留与当前位置 / 在场角色 / 关注进程有关的记录。
        # Entity 记录本身不带 location 字段（位置是世界拓扑关系），所以"谁在
        # 这个地点"必须顺着 located_at / connects 关系读，而不是猜字段。
        relevant_ids = set()
        if location_key and location_key in entities:
            relevant_ids.add(location_key)
        for relation in (relations.values() if location_key else ()):
            if str(relation.get("kind") or "") not in _LOCATION_RELATION_KINDS:
                continue
            endpoints = {
                str(relation.get("from_ref") or ""),
                str(relation.get("to_ref") or ""),
            }
            if location_key in endpoints:
                relevant_ids.update(endpoints)
        relevant_ids.update(key for key in participants_set if key in entities)
        process_keys = set(processes_of_interest)
        for key, process in processes.items():
            process_participants = {
                str(item) for item in (process.get("participants") or []) if str(item)
            }
            if (
                key in process_keys
                or process_participants.intersection(participants_set)
                or (location_key and str(process.get("location") or "") == location_key)
            ):
                process_keys.add(key)
                relevant_ids.update(process_participants)
        for key, relation in relations.items():
            if (
                str(relation.get("from_ref") or "") in relevant_ids
                or str(relation.get("to_ref") or "") in relevant_ids
            ):
                relevant_ids.add(key)
        entities = {key: item for key, item in entities.items() if key in relevant_ids}
        relations = {
            key: item for key, item in relations.items()
            if str(item.get("from_ref") or "") in relevant_ids
            or str(item.get("to_ref") or "") in relevant_ids
        }
        processes = {key: item for key, item in processes.items() if key in process_keys}

    projected_entities, entities_truncated = _bounded(entities.items(), MAX_PROJECTED_ENTITIES)
    projected_relations, relations_truncated = _bounded(relations.items(), MAX_PROJECTED_RELATIONS)
    projected_processes, processes_truncated = _bounded(processes.items(), MAX_PROJECTED_PROCESSES)

    if viewer_is_gm:
        viewer = "gm"
    else:
        viewer = f"player:{viewer_uid}" if viewer_uid else "player"
    return {
        "schema_version": WORLD_STATE_SCHEMA_VERSION,
        "viewer": viewer,
        "revision": world_revision(state),
        "clock": world_clock(state),
        "facts": facts,
        "entities": projected_entities,
        "relations": projected_relations,
        "processes": projected_processes,
        "truncated": bool(
            entities_truncated or relations_truncated or processes_truncated
        ),
    }


__all__ = [
    "MAX_PROJECTED_ENTITIES",
    "MAX_PROJECTED_PROCESSES",
    "MAX_PROJECTED_RELATIONS",
    "WORLD_STATE_SCHEMA_VERSION",
    "fact_value",
    "fact_visibility",
    "project_visible_state",
    "state_payload",
    "world_clock",
    "world_entities",
    "world_facts",
    "world_processes",
    "world_relations",
    "world_revision",
    "world_scheduled_events",
]
