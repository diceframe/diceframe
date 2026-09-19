"""Entity / Relation record ops for the authoritative world write path.

母方案 §96（WR-03）：``register_entity`` / ``retire_entity`` /
``add_relation`` / ``set_relation_status`` / ``remove_relation``。

边界：

- 本模块只实现**持锁调用方已经进入** ``apply_ops_to_state`` 草稿后的单条
  record op：校验、构造记录、写入 draft，不做锁、不做 IO、不调度。
- 错误以 :class:`WorldContractError` 抛出，由 ``world_state`` 的分发层统一
  转成 :class:`WorldStateError`——依赖方向保持
  ``world_state → world.ops → world.contracts`` 单向，不成环。
- 与 v1 op 同一严格度：重复 id、未知对象、未知字段一律 fail closed
  （与 ``schedule_event`` 拒绝重复 id 的既有先例一致）。
- Endpoint 只做 canonical id 结构校验，**不要求**已注册实体：空世界（旧存档
  惰性物化，母方案 §68）不允许被注册要求卡死。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.engine.world.contracts import (
    ENTITY_KINDS,
    MAX_PROCESS_PARTICIPANTS,
    RELATION_KINDS,
    RELATION_STATUSES,
    VISIBILITIES,
    WorldContractError,
    canonical_id,
    clock_from_total_minutes,
    clock_minutes,
    validate_entity_record,
    validate_process_record,
    validate_relation_record,
    validate_source_ref,
)

ENTITY_OP_KINDS = ("register_entity", "retire_entity")
RELATION_OP_KINDS = ("add_relation", "set_relation_status", "remove_relation")
PROCESS_OP_KINDS = ("start_process", "complete_process", "cancel_process", "fail_process")
RECORD_OP_KINDS = ENTITY_OP_KINDS + RELATION_OP_KINDS + PROCESS_OP_KINDS


def _reject_unknown_fields(raw: Mapping[str, Any], allowed: set[str], position: int) -> None:
    extra = sorted(str(key) for key in raw if key not in allowed)
    if extra:
        raise WorldContractError(f"world op #{position} has unknown field: {extra[0]!r}")


def _visibility(value: Any, position: int) -> str:
    if value is None:
        return "public"
    if value not in VISIBILITIES:
        raise WorldContractError(f"world op #{position} visibility is invalid: {value!r}")
    return str(value)


def _optional_source_ref(value: Any, position: int) -> str | None:
    if value is None:
        return None
    return validate_source_ref(value)


def apply_record_op(
    draft: dict[str, Any],
    raw: Mapping[str, Any],
    *,
    revision: int,
    position: int,
    now_minutes: int | None = None,
) -> dict[str, Any]:
    """Apply one record op to the validated draft and return the applied summary.

    ``now_minutes`` 是分发时草稿的当前逻辑时刻（绝对分钟），供
    ``start_process`` 校验 ``due_at`` 必须在未来；由 ``world_state`` 每条 op
    现场计算，本模块不自行读钟。
    """

    kind = raw.get("op")
    if kind in ENTITY_OP_KINDS:
        return _apply_entity_op(draft, raw, kind=kind, revision=revision, position=position)
    if kind in RELATION_OP_KINDS:
        return _apply_relation_op(draft, raw, kind=kind, revision=revision, position=position)
    if kind in PROCESS_OP_KINDS:
        return _apply_process_op(
            draft, raw, kind=kind, revision=revision, position=position,
            now_minutes=now_minutes,
        )
    raise WorldContractError(f"unknown world op: {kind!r}")


# ---------- Entity ---------------------------------------------------------


def _apply_entity_op(
    draft: dict[str, Any], raw: Mapping[str, Any], *,
    kind: str, revision: int, position: int,
) -> dict[str, Any]:
    if kind == "register_entity":
        _reject_unknown_fields(
            raw, {"op", "entity_id", "kind", "visibility", "source_ref"}, position,
        )
        entity_id = canonical_id(raw.get("entity_id"), field=f"world op #{position} entity_id")
        entity_kind = raw.get("kind")
        if entity_kind not in ENTITY_KINDS:
            raise WorldContractError(f"world op #{position} entity kind is invalid: {entity_kind!r}")
        visibility = _visibility(raw.get("visibility"), position)
        source_ref = _optional_source_ref(raw.get("source_ref"), position)
        if entity_id in draft["entities"]:
            raise WorldContractError(
                f"world op #{position} reuses entity id: {entity_id!r}"
            )
        record = {
            "entity_id": entity_id,
            "kind": entity_kind,
            "status": "active",
            "visibility": visibility,
            "source_ref": source_ref,
            "created_revision": revision,
        }
        validate_entity_record(record)
        draft["entities"][entity_id] = record
        return {
            "op": "register_entity", "entity_id": entity_id,
            "kind": entity_kind, "visibility": visibility,
        }

    _reject_unknown_fields(raw, {"op", "entity_id"}, position)
    entity_id = canonical_id(raw.get("entity_id"), field=f"world op #{position} entity_id")
    entity = draft["entities"].get(entity_id)
    if entity is None:
        raise WorldContractError(
            f"world op #{position} retires an unknown entity: {entity_id!r}"
        )
    if entity.get("status") != "active":
        raise WorldContractError(
            f"world op #{position} retires an inactive entity: {entity_id!r}"
        )
    visibility = str(entity.get("visibility") or "public")
    entity["status"] = "retired"
    validate_entity_record(entity)
    return {"op": "retire_entity", "entity_id": entity_id, "visibility": visibility}


# ---------- Relation -------------------------------------------------------


def _apply_relation_op(
    draft: dict[str, Any], raw: Mapping[str, Any], *,
    kind: str, revision: int, position: int,
) -> dict[str, Any]:
    if kind == "add_relation":
        _reject_unknown_fields(
            raw,
            {"op", "relation_id", "kind", "from_ref", "to_ref", "visibility", "source_ref"},
            position,
        )
        relation_id = canonical_id(raw.get("relation_id"), field=f"world op #{position} relation_id")
        relation_kind = raw.get("kind")
        if relation_kind not in RELATION_KINDS:
            raise WorldContractError(
                f"world op #{position} relation kind is invalid: {relation_kind!r}"
            )
        from_ref = canonical_id(raw.get("from_ref"), field=f"world op #{position} from_ref")
        to_ref = canonical_id(raw.get("to_ref"), field=f"world op #{position} to_ref")
        visibility = _visibility(raw.get("visibility"), position)
        source_ref = _optional_source_ref(raw.get("source_ref"), position)
        if relation_id in draft["relations"]:
            raise WorldContractError(
                f"world op #{position} reuses relation id: {relation_id!r}"
            )
        record = {
            "relation_id": relation_id,
            "kind": relation_kind,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "status": "active",
            "visibility": visibility,
            "source_ref": source_ref,
            "created_revision": revision,
        }
        validate_relation_record(record)
        draft["relations"][relation_id] = record
        return {
            "op": "add_relation", "relation_id": relation_id,
            "kind": relation_kind, "visibility": visibility,
        }

    if kind == "set_relation_status":
        _reject_unknown_fields(raw, {"op", "relation_id", "status"}, position)
        relation_id = canonical_id(raw.get("relation_id"), field=f"world op #{position} relation_id")
        relation = draft["relations"].get(relation_id)
        if relation is None:
            raise WorldContractError(
                f"world op #{position} updates an unknown relation: {relation_id!r}"
            )
        status = raw.get("status")
        if status not in RELATION_STATUSES:
            raise WorldContractError(
                f"world op #{position} relation status is invalid: {status!r}"
            )
        relation["status"] = status
        validate_relation_record(relation)
        return {
            "op": "set_relation_status", "relation_id": relation_id,
            "status": status, "visibility": str(relation.get("visibility") or "public"),
        }

    _reject_unknown_fields(raw, {"op", "relation_id"}, position)
    relation_id = canonical_id(raw.get("relation_id"), field=f"world op #{position} relation_id")
    if relation_id not in draft["relations"]:
        raise WorldContractError(
            f"world op #{position} removes an unknown relation: {relation_id!r}"
        )
    visibility = str(draft["relations"][relation_id].get("visibility") or "public")
    del draft["relations"][relation_id]
    return {
        "op": "remove_relation", "relation_id": relation_id, "visibility": visibility,
    }




# ---------- Process（母方案 §16/§97，WR-04）--------------------------------


def _apply_process_op(
    draft: dict[str, Any], raw: Mapping[str, Any], *,
    kind: str, revision: int, position: int, now_minutes: int | None,
) -> dict[str, Any]:
    if kind == "start_process":
        return _start_process(
            draft, raw, revision=revision, position=position, now_minutes=now_minutes,
        )
    _reject_unknown_fields(raw, {"op", "process_id"}, position)
    process_id = canonical_id(raw.get("process_id"), field=f"world op #{position} process_id")
    process = draft["processes"].get(process_id)
    if process is None:
        raise WorldContractError(
            f"world op #{position} settles an unknown process: {process_id!r}"
        )
    if process.get("status") != "running":
        raise WorldContractError(
            f"world op #{position} settles a non-running process: {process_id!r}"
        )
    status = {
        "complete_process": "completed",
        "cancel_process": "cancelled",
        "fail_process": "failed",
    }[kind]
    visibility = str(process.get("visibility") or "public")
    process["status"] = status
    validate_process_record(process)
    return {
        "op": kind, "process_id": process_id, "status": status,
        "visibility": visibility,
    }


def _start_process(
    draft: dict[str, Any], raw: Mapping[str, Any], *,
    revision: int, position: int, now_minutes: int | None,
) -> dict[str, Any]:
    _reject_unknown_fields(
        raw,
        {"op", "process_id", "kind", "participants", "location", "due_at", "visibility", "source_ref"},
        position,
    )
    process_id = canonical_id(raw.get("process_id"), field=f"world op #{position} process_id")
    process_kind = canonical_id(raw.get("kind"), field=f"world op #{position} process kind")
    if process_id in draft["processes"]:
        raise WorldContractError(
            f"world op #{position} reuses process id: {process_id!r}"
        )
    participants = raw.get("participants", [])
    if not isinstance(participants, list) or len(participants) > MAX_PROCESS_PARTICIPANTS:
        raise WorldContractError(
            f"world op #{position} process participants must be a list of at most "
            f"{MAX_PROCESS_PARTICIPANTS} ids"
        )
    participants = [
        canonical_id(item, field=f"world op #{position} process participant")
        for item in participants
    ]
    location = raw.get("location")
    if location is not None:
        canonical_id(location, field=f"world op #{position} process location")
    visibility = _visibility(raw.get("visibility"), position)
    source_ref = _optional_source_ref(raw.get("source_ref"), position)
    due_at = raw.get("due_at")
    if due_at is not None:
        due_minutes = clock_minutes(due_at)
        if due_minutes is None:
            raise WorldContractError(
                f"world op #{position} process due_at is invalid: {due_at!r}"
            )
        if now_minutes is None or due_minutes <= now_minutes:
            raise WorldContractError(
                f"world op #{position} starts a process already past its due_at: {process_id!r}"
            )
        normalized = clock_from_total_minutes(due_minutes)
        if normalized is None:  # pragma: no cover - clock_minutes 已限界
            raise WorldContractError(
                f"world op #{position} process due_at is invalid: {due_at!r}"
            )
        due_at = normalized
    record = {
        "process_id": process_id,
        "kind": process_kind,
        "status": "running",
        "participants": participants,
        "location": location,
        "started_at": dict(draft["clock"]),
        "due_at": due_at,
        "visibility": visibility,
        "source_ref": source_ref,
    }
    validate_process_record(record)
    draft["processes"][process_id] = record
    return {
        "op": "start_process", "process_id": process_id,
        "kind": process_kind, "visibility": visibility,
    }


__all__ = [
    "ENTITY_OP_KINDS",
    "PROCESS_OP_KINDS",
    "RECORD_OP_KINDS",
    "RELATION_OP_KINDS",
    "apply_record_op",
]
