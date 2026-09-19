"""World diagnostics inspector (WR-10, 母方案 §103/§169).

GM / Developer 的**只读**诊断投影：Entities / Relations / Processes /
Events / Memory source。它回答"这一局的世界里现在有什么、每样东西从哪来"，
用于诊断与 GM 面板（面板 UI 属后续 PR）。

边界：

- 只读：本模块不做任何 mutation、不拿锁——调用方在只读上下文中使用；
  数据来自防御性读取端，损坏容器一律降级为空，不抛异常。
- 可见性：``viewer_is_gm=True`` 得到全量记录（含 ``source_ref`` 可追溯性，
  母方案 §169）；普通玩家视图只得到 ``public`` 记录，且剥离 ``source_ref``
  等内部溯源字段——secret 永远不在 projection 层泄漏（母方案 §79/§191）。
- WorldEvent receipts 不持久化（§19）：Events 区展示的是当前实例上仍可见
  的最近结算（``last_world_events``）与 world revision 时点，完整历史由
  memory projection（WR-06）承接。
- Memory source 汇总走 ``MemoryStore.kind_summary``（只读、按 game_key 聚合）。
"""

from __future__ import annotations

from typing import Any

from src.engine.world_state import (
    world_clock,
    world_entities,
    world_facts,
    world_processes,
    world_relations,
    world_revision,
    world_scheduled_events,
)

_INTERNAL_FIELDS = ("source_ref", "created_revision")


def _clean_record(record: dict[str, Any], *, viewer_is_gm: bool) -> dict[str, Any]:
    if viewer_is_gm:
        return dict(record)
    return {
        key: value for key, value in record.items()
        if key not in _INTERNAL_FIELDS
    }


def _visible_container(
    records: dict[str, dict[str, Any]], *, viewer_is_gm: bool,
) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for key, record in sorted(records.items()):
        if not viewer_is_gm and str(record.get("visibility") or "") != "public":
            continue
        entry = {"id": key}
        entry.update(_clean_record(record, viewer_is_gm=viewer_is_gm))
        visible.append(entry)
    return visible


def world_inspector(instance: Any, *, viewer_is_gm: bool = False) -> dict[str, Any]:
    """Read-only diagnostics projection of the world for one viewer."""

    state = getattr(instance, "world_state", None)
    return {
        "schema_version": 2,
        "viewer": "gm" if viewer_is_gm else "player",
        "revision": world_revision(state),
        "clock": world_clock(state),
        "entities": _visible_container(world_entities(state), viewer_is_gm=viewer_is_gm),
        "relations": _visible_container(world_relations(state), viewer_is_gm=viewer_is_gm),
        "processes": _visible_container(world_processes(state), viewer_is_gm=viewer_is_gm),
        "fact_count": len(world_facts(state)),
        "scheduled_event_count": len(world_scheduled_events(state)),
        # 最近一轮确定性结算（含 failed），GM 排障用；非 GM 视图为空。
        "recent_events": list(getattr(instance, "last_world_events", []) or [])
        if viewer_is_gm else [],
    }


__all__ = ["world_inspector"]
