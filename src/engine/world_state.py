"""Authoritative world truth: facts, logical clock, and scheduled events.

`#284` needs one authoritative owner for "what is currently true in the world"
so a player action cannot silently teleport, walk over a destroyed bridge, or
rewrite a fact the table already established.  That owner is the single-game
aggregate: ``GameInstance.world_state``.

This module is the only writer (``apply_world_ops``) and the only place that
validates the shape.  Callers must not poke ``instance.world_state["facts"]``
directly; they must go through world ops so that validation, bounds, revision
bookkeeping and provenance stay in one place.

Persisted schema (``schema_version = 2``)::

    {
      "schema_version": 2,
      "revision": 3,
      "clock": {"day": 1, "minute": 720},
      "facts": {
        "actor:p1.location": {
          "value": "village_east",
          "visibility": "public",
          "source_round": 4,
          "updated_revision": 3
        }
      },
      "scheduled_events": {
        "ritual:clearing": {
          "event_id": "ritual:clearing",
          "due_at": {"day": 1, "minute": 840},
          "status": "pending",
          "label": "清林仪式完成",
          "ops": [
            {"op": "set_fact", "key": "ritual:clearing.status", "value": "completed"}
          ]
        }
      },
      "entities": {},
      "relations": {},
      "processes": {}
    }

v2 在 v1（facts / clock / scheduled_events）之上新增 Entity / Relation /
Process 三个容器（母方案 §13-§16）。v1 容器由 :func:`ensure_world_state` 与
实例迁移（instance schema 15 → 16）幂等升级：旧事实原样保留，新容器一律为
空，不猜测、不回填。记录 shape 由 ``src.engine.world.contracts`` 校验。

Design boundaries (deliberately small for the first version):

- No entity graph, no relations, no map topology, no pathfinding.  A fact is a
  canonical key plus a scalar value and its visibility.
- No background work: world ops are pure, synchronous mutations applied by the
  existing authoritative flow while it already holds the aggregate locks.  This
  module never starts threads, writes files, or performs network calls.
- Facts are canonical world coordinates, never localized display names: keys
  match ``[A-Za-z0-9_.:-]`` so a translated label cannot become an identity.
- ``advance_time`` only moves the logical clock.  Settling due scheduled events
  is a separate concern (see the follow-up work package) so this container
  cannot start executing events by accident.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping, Sequence
from copy import deepcopy
from typing import Any

from src.engine.world.contracts import (
    MAX_CLOCK_DAY,
    MINUTES_PER_DAY,
    WorldContractError,
    clock_from_total_minutes,
    clock_minutes,
    validate_entity_record,
    validate_process_record,
    validate_relation_record,
)
from src.engine.world import ops as world_record_ops

WORLD_STATE_SCHEMA_VERSION = 2

# 事实可见性：第一版只有公开与 GM 私有。更细的 ACL / group graph 不在本层。
FACT_VISIBILITIES = ("public", "gm")
# 事件状态：pending 待结算；applied 已确定执行；cancelled 由 cancel_event 写入；
# failed 表示到期时 ops 已不再可应用（例如它要移除的事实已经不存在）——失败事件
# 不会重试，也不会让其它事件或时间推进一起卡住。四者都是持久化值，不能靠内存
# 标记推断。
EVENT_STATUSES = ("pending", "applied", "cancelled", "failed")
OP_KINDS = (
    "set_fact", "remove_fact", "advance_time", "schedule_event", "cancel_event",
    # 仅由 server 侧结算写入（见 world_events.advance_world_time），planner 不会
    # 产生这个 op。
    "complete_event",
    # WorldState v2 record ops（母方案 §96/§97，实现见 world/ops.py）。
    "register_entity", "retire_entity",
    "add_relation", "set_relation_status", "remove_relation",
    "start_process", "complete_process", "cancel_process", "fail_process",
)
RECORD_OP_KINDS = world_record_ops.RECORD_OP_KINDS
SETTLED_EVENT_STATUSES = ("applied", "failed")
# 定时事件内部只允许改事实；不允许事件嵌套调度或自己推进时间（否则结算顺序
# 会依赖递归，不再确定性）。
EVENT_OP_KINDS = ("set_fact", "remove_fact")

# 逻辑时钟常量与换算的单一真值在 world.contracts；此处 re-export 保持既有导入面。
MAX_FACTS = 512
MAX_SCHEDULED_EVENTS = 256
MAX_OPS_PER_BATCH = 64
# v2 记录容器上限（母方案 §167：具体值基于现有限制制定；与 facts 同量级）。
MAX_ENTITIES = 512
MAX_RELATIONS = 512
MAX_PROCESSES = 128
MAX_STRING_CHARS = 400
MAX_LABEL_CHARS = 160
MAX_ABSOLUTE_INT = 1_000_000_000
MAX_ADVANCE_MINUTES = MINUTES_PER_DAY * 30
# canonical key：允许平台 uid / canonical ref 常见字符，但拒绝空白、Unicode 展示名
# 与路径分隔符——世界坐标不能是翻译后的 display name。
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


class WorldStateError(ValueError):
    """A world op or a persisted world state is invalid: fail closed."""


def fresh_world_state() -> dict[str, Any]:
    """The empty world truth of a new game (day 1, 00:00, no facts)."""

    return {
        "schema_version": WORLD_STATE_SCHEMA_VERSION,
        "revision": 0,
        "clock": {"day": 1, "minute": 0},
        "facts": {},
        "scheduled_events": {},
        # WorldState v2 容器（母方案 §13）：Entity / Relation / Process。
        # 它们只承载身份 / 结构 / 生命周期，机制数据（HP / AC / 法术位 /
        # 余额 / 先攻）永远不进世界容器。
        "entities": {},
        "relations": {},
        "processes": {},
    }


def ensure_world_state(raw: Any) -> dict[str, Any]:
    """Return a usable container for ``GameInstance.world_state``.

    Unset / malformed input becomes a fresh empty state.  A persisted v1
    container (schema 13-15 saves) is upgraded to the v2 shape by adding the
    empty ``entities`` / ``relations`` / ``processes`` containers: old facts,
    clock, and scheduled events are preserved verbatim, and nothing is guessed
    into the new containers (母方案 §67).  The upgrade is idempotent.  Any
    other non-empty mapping is passed through unchanged: persisted data is
    untrusted, and an unsupported or corrupted payload must be rejected by the
    write path instead of being silently overwritten with a default that would
    destroy user data.
    """

    if isinstance(raw, Mapping) and raw:
        state = deepcopy(dict(raw))
        if state.get("schema_version") == 1:
            return _upgrade_world_state_v1_to_v2(state)
        return state
    return fresh_world_state()


def _upgrade_world_state_v1_to_v2(state: dict[str, Any]) -> dict[str, Any]:
    """Materialize the v2 containers on a v1 payload without guessing."""

    state["schema_version"] = WORLD_STATE_SCHEMA_VERSION
    for key in ("entities", "relations", "processes"):
        if key not in state:
            state[key] = {}
    return state


# ---- 读取入口：投影与合法性判断都必须经这里，且对损坏存档保持沉默降级 ----


def _current_payload(state: Any) -> Mapping[str, Any] | None:
    if not isinstance(state, Mapping):
        return None
    if state.get("schema_version") != WORLD_STATE_SCHEMA_VERSION:
        return None
    return state


def world_revision(state: Any) -> int:
    payload = _current_payload(state)
    if payload is None:
        return 0
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return 0
    return revision


def world_clock(state: Any) -> dict[str, int]:
    """The logical world time; a corrupt clock reads as day 1, 00:00."""

    payload = _current_payload(state)
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

    payload = _current_payload(state)
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


def world_scheduled_events(state: Any) -> dict[str, dict[str, Any]]:
    payload = _current_payload(state)
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

    payload = _current_payload(state)
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


def clock_to_minutes(clock: Any) -> int:
    """Absolute logical minutes of a ``{"day", "minute"}`` clock (0 = day 1)."""

    minutes = _instant_minutes(clock, "clock")
    if minutes is None:
        raise WorldStateError(f"invalid world clock: {clock!r}")
    return minutes


def clock_from_minutes(total: int, *, position: int = 0) -> dict[str, int]:
    """Inverse of :func:`clock_to_minutes`, bounded by the world clock limits."""

    return _clock_from_minutes(int(total), position)


def ensure_clock(clock: Any, *, fallback: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Validated clock, or ``fallback``/day 1 when the value is unusable."""

    minutes = _instant_minutes(clock, "clock")
    if minutes is not None:
        return _clock_from_minutes(minutes, 0)
    if fallback is not None:
        fallback_minutes = _instant_minutes(fallback, "clock")
        if fallback_minutes is not None:
            return _clock_from_minutes(fallback_minutes, 0)
    return {"day": 1, "minute": 0}


def project_visible_state(
    instance: Any, *, viewer_uid: str = "", viewer_is_gm: bool = False,
) -> dict[str, Any]:
    """World truth as one specific viewer is allowed to see it.

    ``public`` facts are visible to everyone; ``gm`` facts only to the GM.
    Player-facing surfaces must go through this projection instead of reading
    ``instance.world_state`` directly, so hidden world truth cannot leak into a
    player context by accident.  A corrupt container projects as an empty world
    rather than raising.
    """

    state = getattr(instance, "world_state", None)
    facts = world_facts(state)
    if not viewer_is_gm:
        facts = {
            key: fact for key, fact in facts.items()
            if str(fact.get("visibility") or "") == "public"
        }
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
    }


def fact_visibility(state: Any, key: str) -> str:
    """Visibility of one fact, or an empty string when it is not established."""

    fact = world_facts(state).get(str(key or ""))
    return str(fact.get("visibility") or "") if fact is not None else ""


# ---- 写入口 ---------------------------------------------------------------


def apply_world_ops(
    instance: Any,
    ops: Sequence[Mapping[str, Any]],
    *,
    source_round: int | None = None,
) -> dict[str, Any]:
    """Apply one batch of world ops atomically and return what changed.

    The whole batch either applies or nothing is written: ops are first applied
    to a detached draft, and only a fully valid result is committed back to
    ``instance.world_state``.  Callers run this inside the existing
    authoritative write path; this function does no locking, IO, or scheduling.
    """

    if not isinstance(ops, Sequence) or isinstance(ops, (str, bytes)):
        raise WorldStateError("world ops must be a list")
    if not ops:
        raise WorldStateError("world ops must not be empty")
    if len(ops) > MAX_OPS_PER_BATCH:
        raise WorldStateError(f"world ops exceed {MAX_OPS_PER_BATCH} entries")
    draft, summary = apply_ops_to_state(
        getattr(instance, "world_state", None), ops,
        source_round=_source_round(instance, source_round),
    )
    instance.world_state = draft
    return summary


def apply_ops_to_state(
    state: Any, ops: Sequence[Mapping[str, Any]], *, source_round: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure form of one world-op batch: returns ``(new_state, summary)``.

    Used by :func:`apply_world_ops` and by authoritative compositions that need
    to stack several batches (time advance plus due-event settlement) before
    committing them once.  Nothing is written to ``instance`` here.
    """

    if not isinstance(ops, Sequence) or isinstance(ops, (str, bytes)):
        raise WorldStateError("world ops must be a list")
    if not ops:
        raise WorldStateError("world ops must not be empty")
    if len(ops) > MAX_OPS_PER_BATCH:
        raise WorldStateError(f"world ops exceed {MAX_OPS_PER_BATCH} entries")
    round_number = _source_round_value(source_round)
    draft = _validated_copy(state)
    revision = int(draft["revision"]) + 1
    applied: list[dict[str, Any]] = []
    for position, raw in enumerate(ops):
        applied.append(_apply_op(
            draft, raw, revision=revision, source_round=round_number,
            position=position, inside_event=False,
        ))
    if len(draft["facts"]) > MAX_FACTS:
        raise WorldStateError(f"world state exceeds {MAX_FACTS} facts")
    if len(draft["scheduled_events"]) > MAX_SCHEDULED_EVENTS:
        raise WorldStateError(
            f"world state exceeds {MAX_SCHEDULED_EVENTS} scheduled events"
        )
    for name, limit in (
        ("entities", MAX_ENTITIES), ("relations", MAX_RELATIONS), ("processes", MAX_PROCESSES),
    ):
        if len(draft[name]) > limit:
            raise WorldStateError(f"world state exceeds {limit} {name}")
    draft["revision"] = revision
    # 持久化形状按 canonical key 排序：存档 diff 与测试断言都不依赖插入顺序。
    draft["facts"] = {key: draft["facts"][key] for key in sorted(draft["facts"])}
    draft["scheduled_events"] = {
        key: draft["scheduled_events"][key] for key in sorted(draft["scheduled_events"])
    }
    return draft, {
        "revision": revision,
        "clock": dict(draft["clock"]),
        "applied": applied,
    }


def _source_round(instance: Any, source_round: int | None) -> int:
    value = source_round if source_round is not None else getattr(instance, "round_number", 0)
    return _source_round_value(value)


def _source_round_value(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _validated_copy(state: Any) -> dict[str, Any]:
    """Validate the stored container and return a detached mutable draft."""

    payload = _current_payload(state)
    if payload is None:
        raw_version = state.get("schema_version") if isinstance(state, Mapping) else None
        if raw_version is not None and raw_version != WORLD_STATE_SCHEMA_VERSION:
            raise WorldStateError(
                f"unsupported world state schema: {raw_version!r}"
            )
        raise WorldStateError("world state is missing or malformed")
    draft = deepcopy(dict(payload))
    revision = draft.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise WorldStateError("world state revision is invalid")
    clock = draft.get("clock")
    if (
        not isinstance(clock, Mapping)
        or _instant_minutes(clock, "clock") is None
    ):
        raise WorldStateError("world state clock is invalid")
    facts = draft.get("facts")
    if not isinstance(facts, Mapping):
        raise WorldStateError("world state facts must be an object")
    for key, fact in facts.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise WorldStateError(f"world state fact key is invalid: {key!r}")
        if not isinstance(fact, Mapping) or "value" not in fact:
            raise WorldStateError(f"world state fact is invalid: {key!r}")
        if fact.get("visibility") not in FACT_VISIBILITIES:
            raise WorldStateError(f"world state fact visibility is invalid: {key!r}")
    events = draft.get("scheduled_events")
    if not isinstance(events, Mapping):
        raise WorldStateError("world state scheduled_events must be an object")
    for key, event in events.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise WorldStateError(f"scheduled event key is invalid: {key!r}")
        if not isinstance(event, Mapping) or event.get("event_id") != key:
            raise WorldStateError(f"scheduled event is invalid: {key!r}")
        if event.get("status") not in EVENT_STATUSES:
            raise WorldStateError(f"scheduled event status is invalid: {key!r}")
        if _instant_minutes(event.get("due_at"), "due_at") is None:
            raise WorldStateError(f"scheduled event due_at is invalid: {key!r}")
        event_ops = event.get("ops")
        if not isinstance(event_ops, list) or not event_ops:
            raise WorldStateError(f"scheduled event ops are invalid: {key!r}")
        # 读取路径必须和写入路径服从同一套 op shape contract：损坏的持久化事件
        # 不能被静默归一化成一个「什么都没做却标记 applied」的成功结果。
        for nested_position, nested in enumerate(event_ops):
            _validate_event_op(nested, nested_position)
    # WorldState v2 容器：Entity / Relation / Process 记录按 world contracts
    # 校验（fail closed），且容器 key 必须与记录自身 id 一致——损坏或被外部
    # 工具改坏的记录不会以"看起来正常"的形状重新进入权威状态。
    for name, validate_record, id_field in (
        ("entities", validate_entity_record, "entity_id"),
        ("relations", validate_relation_record, "relation_id"),
        ("processes", validate_process_record, "process_id"),
    ):
        container = draft.get(name)
        if not isinstance(container, Mapping):
            raise WorldStateError(f"world state {name} must be an object")
        for key, record in container.items():
            if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
                raise WorldStateError(f"world state {name} key is invalid: {key!r}")
            try:
                validated = validate_record(record)
            except WorldContractError as exc:
                raise WorldStateError(
                    f"world state {name} record is invalid: {key!r}: {exc}"
                ) from exc
            if validated.get(id_field) != key:
                raise WorldStateError(
                    f"world state {name} record id does not match its key: {key!r}"
                )
    draft["facts"] = dict(facts)
    draft["scheduled_events"] = dict(events)
    draft["clock"] = dict(clock)
    return draft


def _validate_event_op(raw: Any, position: int) -> None:
    """Structural validation of one nested event op (no state references)."""

    if not isinstance(raw, Mapping):
        raise WorldStateError(f"scheduled event op #{position} must be an object")
    kind = raw.get("op")
    if kind not in EVENT_OP_KINDS:
        raise WorldStateError(f"scheduled event ops cannot use {kind!r}")
    if kind == "set_fact":
        _reject_unknown_fields(raw, {"op", "key", "value", "visibility"}, position)
        key = _fact_key(raw.get("key"), position)
        _fact_value(raw.get("value"), key)
        if raw.get("visibility") is not None:
            _visibility(raw.get("visibility"), position)
        return
    _reject_unknown_fields(raw, {"op", "key"}, position)
    _fact_key(raw.get("key"), position)


def _apply_op(
    draft: dict[str, Any],
    raw: Any,
    *,
    revision: int,
    source_round: int,
    position: int,
    inside_event: bool,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise WorldStateError(f"world op #{position} must be an object")
    kind = raw.get("op")
    if kind not in OP_KINDS:
        raise WorldStateError(f"unknown world op: {kind!r}")
    if inside_event and kind not in EVENT_OP_KINDS:
        raise WorldStateError(f"scheduled event ops cannot use {kind!r}")
    if kind == "set_fact":
        return _op_set_fact(draft, raw, revision=revision, source_round=source_round, position=position)
    if kind == "remove_fact":
        return _op_remove_fact(draft, raw, position=position)
    if kind == "advance_time":
        return _op_advance_time(draft, raw, position=position)
    if kind == "schedule_event":
        return _op_schedule_event(draft, raw, revision=revision, position=position)
    if kind == "cancel_event":
        return _op_cancel_event(draft, raw, position=position)
    if kind in RECORD_OP_KINDS:
        try:
            return world_record_ops.apply_record_op(
                draft, raw, revision=revision, position=position,
                now_minutes=_instant_minutes(draft["clock"], "clock"),
            )
        except WorldContractError as exc:
            raise WorldStateError(str(exc)) from exc
    return _op_complete_event(draft, raw, position=position)


def _op_set_fact(
    draft: dict[str, Any], raw: Mapping[str, Any], *,
    revision: int, source_round: int, position: int,
) -> dict[str, Any]:
    _reject_unknown_fields(raw, {"op", "key", "value", "visibility"}, position)
    key = _fact_key(raw.get("key"), position)
    value = _fact_value(raw.get("value"), key)
    facts = draft["facts"]
    previous = facts.get(key)
    declared = raw.get("visibility")
    if declared is None:
        # 更新已有事实时不写 visibility 就沿用原值：缺失字段不能让一条 GM 私有
        # 事实因为一次数值更新而意外变成公开事实。
        visibility = str(previous["visibility"]) if isinstance(previous, Mapping) else "public"
    else:
        visibility = _visibility(declared, position)
    facts[key] = {
        "value": value,
        "visibility": visibility,
        "source_round": source_round,
        "updated_revision": revision,
    }
    return {
        "op": "set_fact",
        "key": key,
        "created": not isinstance(previous, Mapping),
        "visibility": visibility,
    }


def _op_remove_fact(
    draft: dict[str, Any], raw: Mapping[str, Any], *, position: int,
) -> dict[str, Any]:
    _reject_unknown_fields(raw, {"op", "key"}, position)
    key = _fact_key(raw.get("key"), position)
    if key not in draft["facts"]:
        raise WorldStateError(f"world op #{position} removes an unknown fact: {key!r}")
    draft["facts"].pop(key)
    return {"op": "remove_fact", "key": key}


def _op_advance_time(
    draft: dict[str, Any], raw: Mapping[str, Any], *, position: int,
) -> dict[str, Any]:
    _reject_unknown_fields(raw, {"op", "minutes"}, position)
    minutes = raw.get("minutes")
    if (
        isinstance(minutes, bool) or not isinstance(minutes, int)
        or not 0 < minutes <= MAX_ADVANCE_MINUTES
    ):
        raise WorldStateError(
            f"world op #{position} needs 1..{MAX_ADVANCE_MINUTES} minutes"
        )
    total = _instant_minutes(draft["clock"], "clock")
    if total is None:  # pragma: no cover - _validated_copy already checked
        raise WorldStateError("world state clock is invalid")
    clock = _clock_from_minutes(total + minutes, position)
    draft["clock"] = clock
    # 到期的 scheduled_events 不在这里结算：本 work package 只维护数据容器，
    # 定时结算由后续 work package 在同一写入口上实现。
    return {"op": "advance_time", "minutes": minutes, "clock": dict(clock)}


def _op_schedule_event(
    draft: dict[str, Any], raw: Mapping[str, Any], *, revision: int, position: int,
) -> dict[str, Any]:
    _reject_unknown_fields(
        raw, {"op", "event_id", "due_at", "ops", "label", "status"}, position,
    )
    event_id = _event_id(raw.get("event_id"), position)
    events = draft["scheduled_events"]
    if event_id in events:
        raise WorldStateError(f"world op #{position} reuses event id: {event_id!r}")
    due_at = _instant(raw.get("due_at"), position)
    now = _instant_minutes(draft["clock"], "clock")
    if now is None:  # pragma: no cover - _validated_copy already checked
        raise WorldStateError("world state clock is invalid")
    if _instant_minutes(due_at, "due_at") <= now:
        raise WorldStateError(
            f"world op #{position} schedules an event in the past: {event_id!r}"
        )
    raw_ops = raw.get("ops")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise WorldStateError(f"world op #{position} event needs non-empty ops")
    if len(raw_ops) > MAX_OPS_PER_BATCH:
        raise WorldStateError(
            f"world op #{position} event exceeds {MAX_OPS_PER_BATCH} ops"
        )
    # 事件描述的是未来：调度时只校验结构（op 种类、key/value/visibility 形状），
    # 引用合法性以结算时刻的状态为准（见 world_events：无法应用的事件会被标记
    # failed，而不是让调度期拒绝一个未来才成立的 op）。
    for nested_position, nested in enumerate(raw_ops):
        _validate_event_op(nested, nested_position)
    label = raw.get("label", "")
    if label is None:
        label = ""
    if not isinstance(label, str) or len(label) > MAX_LABEL_CHARS:
        raise WorldStateError(f"world op #{position} event label is invalid")
    status = raw.get("status", "pending")
    if status != "pending":
        raise WorldStateError(
            f"world op #{position} can only schedule a pending event"
        )
    events[event_id] = {
        "event_id": event_id,
        "due_at": due_at,
        "status": "pending",
        "label": label,
        "ops": [deepcopy(dict(item)) for item in raw_ops],
    }
    return {"op": "schedule_event", "event_id": event_id, "due_at": dict(due_at)}


def _op_cancel_event(
    draft: dict[str, Any], raw: Mapping[str, Any], *, position: int,
) -> dict[str, Any]:
    _reject_unknown_fields(raw, {"op", "event_id"}, position)
    event_id = _event_id(raw.get("event_id"), position)
    event = draft["scheduled_events"].get(event_id)
    if not isinstance(event, Mapping):
        raise WorldStateError(f"world op #{position} cancels an unknown event: {event_id!r}")
    if event.get("status") != "pending":
        raise WorldStateError(
            f"world op #{position} cancels a non-pending event: {event_id!r}"
        )
    event["status"] = "cancelled"
    return {"op": "cancel_event", "event_id": event_id}


def _op_complete_event(
    draft: dict[str, Any], raw: Mapping[str, Any], *, position: int,
) -> dict[str, Any]:
    """Mark one due event as settled (server-side settlement only).

    A pending event can move to ``applied`` or ``failed`` exactly once; the
    pending → settled transition is what makes settlement idempotent across
    retries, duplicate saves, and page refreshes.
    """

    _reject_unknown_fields(
        raw, {"op", "event_id", "status", "error"}, position,
    )
    event_id = _event_id(raw.get("event_id"), position)
    event = draft["scheduled_events"].get(event_id)
    if not isinstance(event, MutableMapping):
        raise WorldStateError(
            f"world op #{position} completes an unknown event: {event_id!r}"
        )
    if event.get("status") != "pending":
        raise WorldStateError(
            f"world op #{position} completes a non-pending event: {event_id!r}"
        )
    status = raw.get("status")
    if status not in SETTLED_EVENT_STATUSES:
        raise WorldStateError(
            f"world op #{position} has an invalid settled status: {status!r}"
        )
    error = raw.get("error", "")
    if error is None:
        error = ""
    if not isinstance(error, str) or len(error) > MAX_LABEL_CHARS:
        raise WorldStateError(f"world op #{position} event error is invalid")
    event["status"] = str(status)
    event["settled_at"] = dict(draft["clock"])
    if status == "failed":
        event["error"] = error
    else:
        event.pop("error", None)
    return {"op": "complete_event", "event_id": event_id, "status": str(status)}


# ---- 字段校验 -------------------------------------------------------------


def _reject_unknown_fields(raw: Mapping[str, Any], allowed: set[str], position: int) -> None:
    extra = sorted(str(key) for key in raw if key not in allowed)
    if extra:
        raise WorldStateError(f"world op #{position} has unknown field: {extra[0]!r}")


def _fact_key(value: Any, position: int) -> str:
    if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
        raise WorldStateError(f"world op #{position} fact key is invalid: {value!r}")
    return value


def _event_id(value: Any, position: int) -> str:
    if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
        raise WorldStateError(f"world op #{position} event id is invalid: {value!r}")
    return value


def _visibility(value: Any, position: int) -> str:
    if value not in FACT_VISIBILITIES:
        raise WorldStateError(f"world op #{position} visibility is invalid: {value!r}")
    return str(value)


def _fact_value(value: Any, key: str) -> Any:
    """Fact values are scalars only: no entity graph, no nested structures."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -MAX_ABSOLUTE_INT <= value <= MAX_ABSOLUTE_INT:
            raise WorldStateError(f"fact value is out of range: {key!r}")
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise WorldStateError(f"fact value is too long: {key!r}")
        if any(character < " " and character not in "\t" for character in value):
            raise WorldStateError(f"fact value has control characters: {key!r}")
        return value
    raise WorldStateError(f"fact value must be a string, integer, or boolean: {key!r}")


def _instant(value: Any, position: int) -> dict[str, int]:
    minutes = _instant_minutes(value, f"world op #{position} due_at")
    if minutes is None:
        raise WorldStateError(f"world op #{position} due_at is invalid: {value!r}")
    return _clock_from_minutes(minutes, position)


def _instant_minutes(value: Any, field: str) -> int | None:
    """Convert ``{"day": n, "minute": m}`` into absolute logical minutes."""

    return clock_minutes(value)


def _clock_from_minutes(total: int, position: int) -> dict[str, int]:
    clock = clock_from_total_minutes(total)
    if clock is None:
        if total < 0:
            raise WorldStateError(f"world op #{position} moves the clock before day 1")
        raise WorldStateError(f"world op #{position} moves the clock too far")
    return clock


__all__ = [
    "EVENT_STATUSES",
    "FACT_VISIBILITIES",
    "OP_KINDS",
    "RECORD_OP_KINDS",
    "SETTLED_EVENT_STATUSES",
    "WORLD_STATE_SCHEMA_VERSION",
    "WorldStateError",
    "apply_ops_to_state",
    "apply_world_ops",
    "clock_from_minutes",
    "clock_to_minutes",
    "ensure_clock",
    "ensure_world_state",
    "fact_value",
    "fact_visibility",
    "fresh_world_state",
    "project_visible_state",
    "world_clock",
    "world_entities",
    "world_facts",
    "world_processes",
    "world_relations",
    "world_revision",
    "world_scheduled_events",
]
