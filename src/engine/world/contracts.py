"""World Runtime v2 contracts: Entity / Relation / Process / WorldEvent.

本模块只定义**数据契约与校验**，不持久化、不拿锁、不触碰
``GameInstance``。持久化容器与写入口在 WR-02 / WR-03 落地；本 PR 的产物是
后续所有 world ops / projection / memory 工作共用的 shape 真值。

设计边界（母方案 §13-§20）：

- Entity 只负责身份 / 类型 / 生命周期 / 可见性 / 来源。HP、AC、spell slots、
  余额、战斗先攻**永远不进** entity —— 它们属于 rules runtime / economy。
- Relation 是结构（located_at / connects / member_of / ...），Fact 是当前
  属性。二者都是 canonical id，不是本地化 display name。
- Process 表达"正在发生"（ritual / npc_travel / siege ...），只经权威世界
  时钟推进结算，没有后台 tick。
- WorldEvent 是"服务器确认一件世界层事件真实发生"的 receipt，用于
  provenance / memory projection / timeline / diagnostics。它不是事件溯源
  数据库：authority 始终是当前 WorldState。
- 所有 id 沿用 WorldState v1 的 canonical key 语法（``[A-Za-z0-9][A-Za-z0-9_.:-]*``），
  拒绝空白、Unicode 展示名与路径分隔符。

持久化形态是 plain dict（TypedDict 描述 shape），与 v1 facts/scheduled_events
的存档风格一致：codec 只做不透明透传，校验集中在 world 写入口。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

# ---- canonical id / source_ref 语法 ---------------------------------------

# 与 WorldState v1 的 fact key 同一语法：canonical 世界坐标，不是 display name。
CANONICAL_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
MAX_CANONICAL_ID_CHARS = 120

# source_ref / ContentRef 的 source 段：``<source_kind>:<source_id>``。
# v1 的 ``kind:id`` 引用由调用方解释为 adventure-local source（母方案 §9），
# 本模块只负责 shape 校验，不做跨包解析。
SOURCE_REF_KINDS: Final = (
    "builtin",    # 随 DiceFrame 发布的内置世界/规则
    "core",       # core ruleset bundle / SRD
    "module",     # content-pack adventure-module
    "adventure",  # adventure package 本地内容
    "world",      # world template 自带
    "gm",         # GM 手工建立
    "player",     # 玩家行动合法衍生
    "system",     # 服务器规则结算衍生
)
SOURCE_REF_PATTERN: Final = re.compile(
    r"^([a-z][a-z0-9_-]{0,31}):(.{1,119})$"
)
MAX_SOURCE_REF_CHARS = 152


class WorldContractError(ValueError):
    """A world contract record is invalid: fail closed."""


def canonical_id(value: Any, *, field: str = "id") -> str:
    """Validate one canonical world id and return it unchanged."""

    if not isinstance(value, str) or not CANONICAL_ID_PATTERN.fullmatch(value):
        raise WorldContractError(f"world {field} is not a canonical id: {value!r}")
    return value


def validate_source_ref(value: Any, *, field: str = "source_ref") -> str:
    """Validate ``<source_kind>:<source_id>`` provenance and return it as-is."""

    if not isinstance(value, str) or len(value) > MAX_SOURCE_REF_CHARS:
        raise WorldContractError(f"world {field} is invalid: {value!r}")
    match = SOURCE_REF_PATTERN.fullmatch(value)
    if match is None or match.group(1) not in SOURCE_REF_KINDS:
        raise WorldContractError(f"world {field} is invalid: {value!r}")
    if not CANONICAL_ID_PATTERN.fullmatch(match.group(2)):
        raise WorldContractError(f"world {field} is invalid: {value!r}")
    return value


# ---- 可见性（与 WorldState v1 一致：public / gm）--------------------------

VISIBILITIES: Final = ("public", "gm")


def _visibility(value: Any, field: str) -> str:
    if value not in VISIBILITIES:
        raise WorldContractError(f"world {field} visibility is invalid: {value!r}")
    return str(value)


# ---- 逻辑时钟（单一真值；world_state 的错误包装委托到这里）----------------

MINUTES_PER_DAY = 1440
MAX_CLOCK_DAY = 365_000


def clock_minutes(value: Any) -> int | None:
    """Convert ``{"day": n, "minute": m}`` into absolute logical minutes.

    Pure helper shared by the world write path and record ops; returns ``None``
    for any unusable value instead of raising.
    """

    if not isinstance(value, Mapping):
        return None
    day, minute = value.get("day"), value.get("minute")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= MAX_CLOCK_DAY:
        return None
    if isinstance(minute, bool) or not isinstance(minute, int):
        return None
    if not 0 <= minute < MINUTES_PER_DAY:
        return None
    return (day - 1) * MINUTES_PER_DAY + minute


def clock_from_total_minutes(total: int) -> dict[str, int] | None:
    """Inverse of :func:`clock_minutes`; ``None`` when out of world bounds."""

    if total < 0:
        return None
    day, minute = divmod(int(total), MINUTES_PER_DAY)
    day += 1
    if day > MAX_CLOCK_DAY:
        return None
    return {"day": day, "minute": minute}


# ---- Entity（母方案 §14）--------------------------------------------------

ENTITY_KINDS: Final = (
    "pc",         # 玩家角色在世界中的存在（机制数据仍在 ruleset 侧）
    "npc",        # 有身份的剧情 NPC
    "creature",   # 野生/召唤生物
    "location",   # 地点
    "item",       # 世界中"某件东西"的存在（数量/背包归 rules/economy）
    "object",     # 场景物件（桥、门、祭坛）
    "faction",    # 阵营/组织
)
ENTITY_STATUSES: Final = ("active", "retired")
MAX_ENTITY_SOURCE_REF_CHARS = MAX_SOURCE_REF_CHARS


def validate_entity_record(record: Any) -> dict[str, Any]:
    """Validate one persisted Entity record shape and return it unchanged.

    Required: ``entity_id`` / ``kind`` / ``status`` / ``visibility`` /
    ``source_ref`` / ``created_revision``.  Unknown fields fail closed:
    adding mechanics fields later must go through the contract, not through
    loose dicts.
    """

    if not isinstance(record, dict):
        raise WorldContractError("world entity must be an object")
    allowed = {"entity_id", "kind", "status", "visibility", "source_ref", "created_revision"}
    extra = sorted(set(record) - allowed)
    if extra:
        raise WorldContractError(f"world entity has unknown field: {extra[0]!r}")
    entity_id = canonical_id(record.get("entity_id"), field="entity_id")
    kind = record.get("kind")
    if kind not in ENTITY_KINDS:
        raise WorldContractError(f"world entity kind is invalid: {kind!r}")
    status = record.get("status")
    if status not in ENTITY_STATUSES:
        raise WorldContractError(f"world entity status is invalid: {status!r}")
    visibility = _visibility(record.get("visibility"), "entity")
    source_ref = record.get("source_ref")
    if source_ref is not None:
        validate_source_ref(source_ref, field="entity source_ref")
    revision = record.get("created_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise WorldContractError(f"world entity created_revision is invalid: {revision!r}")
    return {
        "entity_id": entity_id,
        "kind": kind,
        "status": status,
        "visibility": visibility,
        "source_ref": source_ref,
        "created_revision": revision,
    }


# ---- Relation（母方案 §15）------------------------------------------------

RELATION_KINDS: Final = (
    "located_at", "connects", "member_of", "controls",
    "hostile_to", "allied_with", "owns",
)
RELATION_STATUSES: Final = ("active", "inactive", "severed")
MAX_RELATION_ENDPOINTS = 2


def validate_relation_record(record: Any) -> dict[str, Any]:
    """Validate one persisted Relation record shape and return it unchanged."""

    if not isinstance(record, dict):
        raise WorldContractError("world relation must be an object")
    allowed = {
        "relation_id", "kind", "from_ref", "to_ref", "status",
        "visibility", "source_ref", "created_revision",
    }
    extra = sorted(set(record) - allowed)
    if extra:
        raise WorldContractError(f"world relation has unknown field: {extra[0]!r}")
    relation_id = canonical_id(record.get("relation_id"), field="relation_id")
    kind = record.get("kind")
    if kind not in RELATION_KINDS:
        raise WorldContractError(f"world relation kind is invalid: {kind!r}")
    endpoints = []
    for side in ("from_ref", "to_ref"):
        endpoints.append(canonical_id(record.get(side), field=f"relation {side}"))
    status = record.get("status")
    if status not in RELATION_STATUSES:
        raise WorldContractError(f"world relation status is invalid: {status!r}")
    visibility = _visibility(record.get("visibility"), "relation")
    source_ref = record.get("source_ref")
    if source_ref is not None:
        validate_source_ref(source_ref, field="relation source_ref")
    revision = record.get("created_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise WorldContractError(f"world relation created_revision is invalid: {revision!r}")
    return {
        "relation_id": relation_id,
        "kind": kind,
        "from_ref": endpoints[0],
        "to_ref": endpoints[1],
        "status": status,
        "visibility": visibility,
        "source_ref": source_ref,
        "created_revision": revision,
    }


# ---- Process（母方案 §16）-------------------------------------------------

PROCESS_STATUSES: Final = ("running", "completed", "cancelled", "failed")
MAX_PROCESS_PARTICIPANTS = 32


def validate_process_record(record: Any) -> dict[str, Any]:
    """Validate one persisted Process record shape and return it unchanged.

    ``started_at`` / ``due_at`` 是权威**逻辑时钟**（``{"day", "minute"}``），
    不是 wall clock；Process 只能被权威时间推进结算，这是母方案 §16/§80 的
    硬边界。
    """

    if not isinstance(record, dict):
        raise WorldContractError("world process must be an object")
    allowed = {
        "process_id", "kind", "status", "participants", "location",
        "started_at", "due_at", "visibility", "source_ref",
    }
    extra = sorted(set(record) - allowed)
    if extra:
        raise WorldContractError(f"world process has unknown field: {extra[0]!r}")
    process_id = canonical_id(record.get("process_id"), field="process_id")
    kind = canonical_id(record.get("kind"), field="process kind")
    status = record.get("status")
    if status not in PROCESS_STATUSES:
        raise WorldContractError(f"world process status is invalid: {status!r}")
    participants = record.get("participants")
    if not isinstance(participants, list) or len(participants) > MAX_PROCESS_PARTICIPANTS:
        raise WorldContractError(
            f"world process participants must be a list of at most "
            f"{MAX_PROCESS_PARTICIPANTS} ids"
        )
    participants = [canonical_id(item, field="process participant") for item in participants]
    location = record.get("location")
    if location is not None:
        canonical_id(location, field="process location")
    _validate_clock(record.get("started_at"), required=True, field="process started_at")
    due_at = record.get("due_at")
    if due_at is not None:
        _validate_clock(due_at, required=True, field="process due_at")
    visibility = _visibility(record.get("visibility"), "process")
    source_ref = record.get("source_ref")
    if source_ref is not None:
        validate_source_ref(source_ref, field="process source_ref")
    return {
        "process_id": process_id,
        "kind": kind,
        "status": status,
        "participants": participants,
        "location": location,
        "started_at": record.get("started_at"),
        "due_at": due_at,
        "visibility": visibility,
        "source_ref": source_ref,
    }


def _validate_clock(value: Any, *, required: bool, field: str) -> None:
    if value is None:
        if required:
            raise WorldContractError(f"world {field} is required")
        return
    if (
        not isinstance(value, dict)
        or set(value) != {"day", "minute"}
        or isinstance(value.get("day"), bool) or not isinstance(value.get("day"), int)
        or value.get("day", 0) < 1
        or isinstance(value.get("minute"), bool) or not isinstance(value.get("minute"), int)
        or not 0 <= value.get("minute", -1) <= 1439
    ):
        raise WorldContractError(f"world {field} is not a logical clock: {value!r}")


# ---- WorldEvent（母方案 §18/§19）------------------------------------------

WORLD_EVENT_KINDS: Final = (
    # 世界 ops（v1 既有语义，v2 起每次提交产出 receipt）
    "fact_set", "fact_removed", "time_advanced",
    "event_scheduled", "event_cancelled", "event_settled",
    # v2 实体 / 关系 / 进程生命周期
    "entity_registered", "entity_retired",
    "relation_added", "relation_status_changed", "relation_removed",
    "process_started", "process_settled",
)


def validate_world_event_record(record: Any) -> dict[str, Any]:
    """Validate one WorldEvent receipt shape and return it unchanged.

    Receipt 是"已经提交的世界事实"的凭据：携带稳定 ``event_id``、产生它的
    ``kind``、提交时的 world ``revision`` 与逻辑 ``clock``、``source_round``
    与可见性。它不是可执行指令，也不是 EventBus 消息。
    """

    if not isinstance(record, dict):
        raise WorldContractError("world event must be an object")
    allowed = {
        "event_id", "kind", "revision", "clock", "source_round",
        "visibility", "summary", "subject",
    }
    extra = sorted(set(record) - allowed)
    if extra:
        raise WorldContractError(f"world event has unknown field: {extra[0]!r}")
    event_id = canonical_id(record.get("event_id"), field="world event_id")
    kind = record.get("kind")
    if kind not in WORLD_EVENT_KINDS:
        raise WorldContractError(f"world event kind is invalid: {kind!r}")
    revision = record.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise WorldContractError(f"world event revision is invalid: {revision!r}")
    _validate_clock(record.get("clock"), required=True, field="world event clock")
    source_round = record.get("source_round")
    if isinstance(source_round, bool) or not isinstance(source_round, int) or source_round < 0:
        raise WorldContractError(f"world event source_round is invalid: {source_round!r}")
    visibility = _visibility(record.get("visibility"), "world event")
    subject = record.get("subject")
    if subject is not None:
        canonical_id(subject, field="world event subject")
    summary = record.get("summary", "")
    if not isinstance(summary, str) or len(summary) > 400:
        raise WorldContractError(f"world event summary is invalid: {summary!r}")
    return {
        "event_id": event_id,
        "kind": kind,
        "revision": revision,
        "clock": dict(record["clock"]),
        "source_round": source_round,
        "visibility": visibility,
        "subject": subject,
        "summary": summary,
    }


__all__ = [
    "CANONICAL_ID_PATTERN",
    "ENTITY_KINDS",
    "ENTITY_STATUSES",
    "MAX_CANONICAL_ID_CHARS",
    "MAX_CLOCK_DAY",
    "MAX_PROCESS_PARTICIPANTS",
    "MINUTES_PER_DAY",
    "PROCESS_STATUSES",
    "RELATION_KINDS",
    "RELATION_STATUSES",
    "SOURCE_REF_KINDS",
    "VISIBILITIES",
    "WORLD_EVENT_KINDS",
    "WorldContractError",
    "canonical_id",
    "clock_from_total_minutes",
    "clock_minutes",
    "validate_entity_record",
    "validate_process_record",
    "validate_relation_record",
    "validate_source_ref",
    "validate_world_event_record",
]
