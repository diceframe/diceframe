"""Round-related snapshot helpers for :class:`GameInstance`.

本模块只回答一件事：**如何复制 / 恢复单局的回合相关快照**。

- 玩家可回滚状态快照（``snapshot_players`` / ``restore_players``）；
- 通用战斗扩展的按回合快照（capture / current / restore / discard）；
- 判定入口的旧版战斗实体快照（npcs / combat_enemies / 战斗状态 / world_state）；
- 快照恢复后的过期战斗结算缓存清理（``drop_stale_combat_caches``）。

边界：

- 不拥有任何锁；调用方负责在正确的 GameInstance 锁 / authority 边界内调用；
- 不决定什么时候回滚、什么时候推进回合 —— 那是 aggregate 与 recovery 的职责；
- 不 runtime import ``src.engine.game_instance``（仅 ``TYPE_CHECKING`` 类型引用），
  依赖方向保持 ``game_instance → round_snapshots``。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from src.engine.game_state_contracts import PlayerRollbackSnapshot
from src.engine.world_state import ensure_world_state

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance


def snapshot_players(instance: GameInstance) -> PlayerRollbackSnapshot:
    """快照所有玩家可回滚状态（含死亡玩家，便于 swipe 复活）。

    覆盖运行时可变字段（HP/金币/SAN/LUCK/MANA/状态/背包/装备/法术）；
    不含 identity/progression（race/class/level/xp/skills 不随 swipe 回滚）。
    """
    snap: PlayerRollbackSnapshot = {}
    for uid in instance.players:
        cs = instance.get_character_sheet(uid)
        snap[uid] = {
            "hp": cs.get("hp", 0),
            "max_hp": cs.get("max_hp", 0),
            "gold": cs.get("gold", 0),
            "deceased": cs.get("deceased", False),
            "death_round": cs.get("death_round"),
        }
        for opt in ("status", "sanity", "max_sanity", "luck", "max_luck",
                    "mana", "currency", "resources", "spells_known"):
            if opt in cs:
                snap[uid][opt] = copy.deepcopy(cs[opt])
        for lst in ("inventory", "equipment", "key_items"):
            snap[uid][lst] = copy.deepcopy(cs.get(lst, []))
    return snap


def restore_players(instance: GameInstance, snapshot: PlayerRollbackSnapshot) -> None:
    """从快照恢复玩家可回滚状态（含 deceased/death_round，便于 swipe 复活）。"""
    for uid, snap in snapshot.items():
        if uid not in instance.players:
            continue
        cs = instance.get_character_sheet(uid)
        for key, value in snap.items():
            cs[key] = value
        instance.players[uid]["character_sheet"] = cs


# ---------- 通用战斗扩展按回合快照 --------------------------


def capture_combat_extension_snapshot(
    instance: GameInstance,
    entity_fields: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Capture state and source fields before this round's combat writes.

    The extension payload alone is insufficient because HP, declared
    special stats, and consumable inventory live on character/NPC records.
    Later actions may engage new entities, so their fields are merged into
    the same pre-mutation snapshot when first touched.
    """

    try:
        key = str(int(instance.round_number or 0))
    except (TypeError, ValueError):
        key = "0"
    if not isinstance(instance.combat_extension_round_snapshots, dict):
        instance.combat_extension_round_snapshots = {}
    snapshot = instance.combat_extension_round_snapshots.get(key)
    if not isinstance(snapshot, dict):
        snapshot = None
    elif "combat_extension" not in snapshot:
        # Older in-memory snapshots stored the raw extension payload.
        # Wrap it before adding source-field tracking so that a legacy
        # branch remains restorable without exposing malformed containers.
        snapshot = {
            "schema_version": 1,
            "combat_extension": copy.deepcopy(snapshot),
            "entity_fields": {},
        }
        instance.combat_extension_round_snapshots[key] = snapshot
    elif (
        isinstance(snapshot.get("schema_version"), bool)
        or snapshot.get("schema_version") != 1
        or not isinstance(snapshot.get("combat_extension"), dict)
    ):
        snapshot = None
    if snapshot is None:
        snapshot = {
            "schema_version": 1,
            "combat_extension": copy.deepcopy(
                instance.combat_extension if isinstance(instance.combat_extension, dict) else {}
            ),
            "entity_fields": {},
        }
        instance.combat_extension_round_snapshots[key] = snapshot
    snapshot = instance.combat_extension_round_snapshots[key]
    if isinstance(entity_fields, Mapping) and isinstance(snapshot, dict):
        captured = snapshot.setdefault("entity_fields", {})
        if not isinstance(captured, dict):
            captured = {}
            snapshot["entity_fields"] = captured
        for entity_id, raw_fields in entity_fields.items():
            if (
                not isinstance(entity_id, str)
                or not entity_id
                or not isinstance(raw_fields, (tuple, list, set, frozenset))
            ):
                continue
            fields = tuple(dict.fromkeys(
                field_name
                for field_name in raw_fields
                if isinstance(field_name, str) and field_name
            ))
            if not fields:
                continue
            source: Mapping[str, Any] | None = None
            if entity_id.startswith("player:"):
                uid = entity_id.removeprefix("player:")
                if uid in instance.players:
                    raw_sheet = instance.get_character_sheet(uid)
                    if isinstance(raw_sheet, Mapping):
                        source = raw_sheet
            elif entity_id.startswith("npc:"):
                npc = instance.npcs.get(entity_id.removeprefix("npc:"))
                if isinstance(npc, Mapping):
                    source = npc
            if source is None:
                continue
            entry = captured.get(entity_id)
            if not isinstance(entry, dict):
                entry = {"values": {}, "missing": []}
                captured[entity_id] = entry
            values = entry.get("values")
            if not isinstance(values, dict):
                values = {}
                entry["values"] = values
            missing = entry.get("missing")
            if not isinstance(missing, list):
                missing = []
                entry["missing"] = missing
            missing[:] = [
                field_name
                for field_name in missing
                if isinstance(field_name, str)
                and field_name
                and field_name not in values
            ]
            for field_name in fields:
                if field_name in values or field_name in missing:
                    continue
                if field_name in source:
                    values[field_name] = copy.deepcopy(source[field_name])
                else:
                    missing.append(field_name)
    # Save size stays bounded even in very long sessions. Finished-round
    # logs carry their own copy, so only recent/current snapshots are needed.
    if len(instance.combat_extension_round_snapshots) > 100:
        def _sort_key(value: str) -> tuple[int, str]:
            try:
                return int(value), value
            except (TypeError, ValueError):
                return -1, value
        for old_key in sorted(instance.combat_extension_round_snapshots, key=_sort_key)[:-100]:
            instance.combat_extension_round_snapshots.pop(old_key, None)


def current_combat_extension_snapshot(instance: GameInstance) -> dict[str, Any]:
    """Return the current combat state using this round's tracked fields."""

    try:
        key = str(int(instance.round_number or 0))
    except (TypeError, ValueError):
        key = "0"
    if not isinstance(instance.combat_extension_round_snapshots, dict):
        instance.combat_extension_round_snapshots = {}
    tracked = instance.combat_extension_round_snapshots.get(key, {})
    raw_fields = tracked.get("entity_fields") if isinstance(tracked, dict) else {}
    entity_fields: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_fields, dict):
        for entity_id, entry in raw_fields.items():
            if not isinstance(entry, dict):
                continue
            values = entry.get("values")
            absent = entry.get("missing")
            if (
                not isinstance(entity_id, str)
                or not entity_id
                or not isinstance(values, dict)
                or not isinstance(absent, list)
                or any(not isinstance(name, str) or not name for name in values)
                or any(not isinstance(name, str) or not name for name in absent)
                or len(set(absent)) != len(absent)
                or set(values).intersection(absent)
            ):
                continue
            entity_fields[entity_id] = tuple((*values.keys(), *absent))

    extension_state = copy.deepcopy(
        instance.combat_extension if isinstance(instance.combat_extension, dict) else {}
    )
    extension_state.pop("pending_summaries", None)
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "combat_extension": extension_state,
        "entity_fields": {},
    }
    for entity_id, fields in entity_fields.items():
        source: Mapping[str, Any] | None = None
        if entity_id.startswith("player:"):
            uid = entity_id.removeprefix("player:")
            if uid in instance.players:
                raw_sheet = instance.get_character_sheet(uid)
                if isinstance(raw_sheet, Mapping):
                    source = raw_sheet
        elif entity_id.startswith("npc:"):
            npc = instance.npcs.get(entity_id.removeprefix("npc:"))
            if isinstance(npc, Mapping):
                source = npc
        if source is None:
            continue
        values = {
            field_name: copy.deepcopy(source[field_name])
            for field_name in fields if field_name in source
        }
        snapshot["entity_fields"][entity_id] = {
            "values": values,
            "missing": [field_name for field_name in fields if field_name not in source],
        }
    return snapshot


def restore_combat_extension_snapshot(instance: GameInstance, snapshot: Any) -> bool:
    """Restore a snapshot produced by the combat-extension snapshot API."""

    if not isinstance(snapshot, dict):
        return False
    if "combat_extension" not in snapshot:
        # Compatibility with the short-lived raw-payload snapshot shape.
        try:
            restored_extension = copy.deepcopy(snapshot)
        except (TypeError, ValueError, RecursionError):
            return False
        instance.combat_extension = restored_extension
        return True
    version = snapshot.get("schema_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
        or not isinstance(snapshot.get("combat_extension"), dict)
    ):
        return False
    entity_fields = snapshot.get("entity_fields")
    if entity_fields is None:
        entity_fields = {}
    if not isinstance(entity_fields, dict):
        return False
    staged: list[tuple[dict[str, Any], dict[str, Any], list[str]]] = []
    for entity_id, entry in entity_fields.items():
        if not isinstance(entity_id, str) or not isinstance(entry, dict):
            return False
        target: dict[str, Any] | None = None
        if entity_id.startswith("player:"):
            uid = entity_id.removeprefix("player:")
            if uid in instance.players:
                raw_sheet = instance.get_character_sheet(uid)
                if isinstance(raw_sheet, dict):
                    target = raw_sheet
        elif entity_id.startswith("npc:"):
            npc = instance.npcs.get(entity_id.removeprefix("npc:"))
            if isinstance(npc, dict):
                target = npc
        if target is None:
            continue
        values = entry.get("values")
        absent = entry.get("missing")
        if (
            not isinstance(values, dict)
            or not isinstance(absent, list)
            or any(not isinstance(field_name, str) or not field_name for field_name in values)
            or any(not isinstance(field_name, str) or not field_name for field_name in absent)
            or len(set(absent)) != len(absent)
            or set(values).intersection(absent)
        ):
            return False
        if target is None:
            continue
        try:
            copied_values = {
                field_name: copy.deepcopy(value)
                for field_name, value in values.items()
            }
        except (TypeError, ValueError, RecursionError):
            return False
        staged.append((target, copied_values, list(absent)))
    try:
        restored_extension = copy.deepcopy(snapshot["combat_extension"])
    except (TypeError, ValueError, RecursionError):
        return False
    instance.combat_extension = restored_extension
    for target, values, absent in staged:
        target.update(values)
        for field_name in absent:
            target.pop(field_name, None)
    return True


def discard_combat_extension_snapshots_from(instance: GameInstance, round_number: int) -> None:
    """Drop live snapshots belonging to a discarded history branch."""

    if not isinstance(instance.combat_extension_round_snapshots, dict):
        instance.combat_extension_round_snapshots = {}
        return
    for key in list(instance.combat_extension_round_snapshots):
        try:
            snapshot_round = int(key)
        except (TypeError, ValueError):
            # Malformed keys cannot be associated with the retained branch.
            instance.combat_extension_round_snapshots.pop(key, None)
            continue
        if snapshot_round >= round_number:
            instance.combat_extension_round_snapshots.pop(key, None)


# ---------- 判定入口旧版实体快照 ----------------------------


def capture_round_entity_snapshot(instance: GameInstance) -> None:
    """判定入口快照旧版战斗实体与战斗状态。

    覆盖 `CombatResolver` / `initiate_combat` 在判定阶段会直接改写的字段；
    与 ``round_start_snapshot``（玩家）和 ``combat_extension_round_snapshots``
    （D&D2024 权威战斗扩展）互补，三者合起来才是"本轮改过的东西"。
    """
    instance.round_entity_snapshot = {
        "npcs": copy.deepcopy(instance.npcs),
        "combat_enemies": copy.deepcopy(instance.combat_enemies),
        "combat_state": str(instance.combat_state or "none"),
        "combat_active": bool(instance.combat_active),
        "initiative_order": copy.deepcopy(list(instance.initiative_order or [])),
        "initiative_current": int(instance.initiative_current or 0),
        "world_state": copy.deepcopy(instance.world_state),
        # FIX-07 §10 步骤 23：Adventure 进度与世界真相由同一次权威事务写入
        # （FIX-04 §6.7），所以"本轮改过的东西"必须包含它——否则回滚会把世界
        # 退回去、把进度留在被丢弃的分支上。
        "adventure_progress": copy.deepcopy(instance.adventure_progress),
    }


def restore_round_entity_snapshot(instance: GameInstance) -> bool:
    """还原判定入口的旧版实体快照；没有快照时返回 False（不动状态）。"""
    snapshot = instance.round_entity_snapshot
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    instance.npcs = copy.deepcopy(snapshot.get("npcs") or {})
    instance.combat_enemies = copy.deepcopy(snapshot.get("combat_enemies") or [])
    instance.combat_state = str(snapshot.get("combat_state") or "none")
    instance.combat_active = bool(snapshot.get("combat_active"))
    instance.initiative_order = copy.deepcopy(list(snapshot.get("initiative_order") or []))
    instance.initiative_current = int(snapshot.get("initiative_current") or 0)
    # 世界真相按整轮语义回滚（ADR 0003）：本轮写入的 world ops 随本轮撤销。
    if "world_state" in snapshot:
        instance.world_state = ensure_world_state(snapshot.get("world_state"))
    # Adventure 进度与世界真相同一事务（FIX-04 §6.7），必须一起回到判定入口。
    if "adventure_progress" in snapshot:
        instance.adventure_progress = copy.deepcopy(snapshot.get("adventure_progress") or {})
    return True


# ---------- 过期战斗结算缓存 -------------------------------


def drop_stale_combat_caches(instance: GameInstance, *, all_targets: bool = False) -> None:
    """丢弃"状态已回滚、缓存却仍记录伤害"的战斗结算缓存。

    实体快照与玩家快照已经把相关实体恢复到判定入口，缓存描述的却是另一个
    状态；保留它会命中 ``CombatResolver`` 的缓存重放分支——既不重掷命中骰
    也不重新扣血——于是结算记录（伤害 5）与实际 HP（恢复后的满血）互相矛盾。

    ``all_targets=True``（实体快照已完整还原，含 npcs/combat_enemies）时一律
    丢弃。旧存档没有实体快照（``all_targets=False``）时退化为按目标核对：
    只有该目标"本轮结算后的最终血量"与当前血量不一致才丢弃，血量没有被回滚
    的目标或目标已无法解析时保留缓存，避免重试重复扣血。
    """
    by_target: dict[str, list[tuple[dict[str, Any], Mapping[str, Any]]]] = {}
    for action in instance.action_queue:
        outcome = action.get("combat_outcome") if isinstance(action, dict) else None
        if isinstance(outcome, dict):
            by_target.setdefault(str(outcome.get("target_ref") or ""), []).append(
                (action, outcome),
            )
    for target_ref, entries in by_target.items():
        if not all_targets:
            # resolve_combat 按 action_queue 顺序结算，故最后一条即最终态。
            final_hp = entries[-1][1].get("target_hp_after")
            current_hp = entity_hp(instance, target_ref)
            if current_hp is None or final_hp is None or current_hp == final_hp:
                # 无法核对，或血量仍是结算后的值：保留缓存，重放安全且必要。
                continue
        for action, _outcome in entries:
            action.pop("combat_outcome", None)


def entity_hp(instance: GameInstance, target_ref: str) -> int | None:
    """按战斗目标引用取当前血量；未知目标返回 None。

    旧版 ``CombatResolver`` 用裸 uid 表示玩家目标，战斗扩展用
    ``player:<uid>`` / ``npc:<id>`` / ``enemy:<index>``，两种都要认。
    """
    raw: Any = None
    if target_ref in instance.players:
        raw = instance.get_character_sheet(target_ref).get("hp")
    elif target_ref.startswith("player:"):
        uid = target_ref.removeprefix("player:")
        if uid in instance.players:
            raw = instance.get_character_sheet(uid).get("hp")
    elif target_ref.startswith("npc:"):
        npc = instance.npcs.get(target_ref.removeprefix("npc:"))
        if isinstance(npc, Mapping):
            raw = npc.get("hp")
    elif target_ref.startswith("enemy:"):
        try:
            enemy = instance.combat_enemies[int(target_ref.removeprefix("enemy:"))]
        except (ValueError, IndexError):
            return None
        if isinstance(enemy, Mapping):
            raw = enemy.get("hp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
