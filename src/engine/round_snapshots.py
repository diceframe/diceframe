"""Round-related snapshot helpers for :class:`GameInstance`.

本模块只回答一件事：**如何复制 / 恢复单局的回合相关快照**。

- 玩家可回滚状态快照（``snapshot_players`` / ``restore_players``）；
- 判定入口的实体快照（npcs / 战斗视图记录 / world_state / adventure 进度 / 规则运行时权威）；
- 快照恢复后的过期战斗结算缓存清理（``drop_stale_combat_caches``）。

边界：

- 不拥有任何锁；调用方负责在正确的 GameInstance 锁 / authority 边界内调用；
- 不决定什么时候回滚、什么时候推进回合 —— 那是 aggregate 与 recovery 的职责；
- 不 runtime import ``src.engine.game_instance``（仅 ``TYPE_CHECKING`` 类型引用），
  依赖方向保持 ``game_instance → round_snapshots``。
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from src.engine import progression
from src.engine.game_state_contracts import PlayerRollbackSnapshot
from src.engine.world_state import ensure_world_state

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance

logger = logging.getLogger("trpg")


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


# ---------- 判定入口旧版实体快照 ----------------------------


def capture_round_entity_snapshot(instance: GameInstance) -> None:
    """判定入口快照旧版战斗实体与战斗状态。

    覆盖 `CombatResolver` 在判定阶段会直接改写的字段；combat_state /
    combat_active / initiative_* 只是派生战斗视图的只读记录，还原时不写回
    （权威在 ruleset_state）。旧快照里的 ``combat_enemies`` 键已退役，还原时忽略；
    与 ``round_start_snapshot``（玩家）和 ``combat_extension_round_snapshots``
    （D&D2024 权威战斗扩展）互补，三者合起来才是"本轮改过的东西"。
    """
    from src.engine import module_hooks
    from src.engine.modules import adventure_runtime_state, round_safety, ruleset_runtime

    round_safety.require_writable(instance)
    adventure_runtime_state.require_writable(instance)
    ruleset_runtime.require_writable(instance)
    round_safety.replace_entity_snapshot(instance, {
        "npcs": copy.deepcopy(instance.npcs),
        "combat_state": str(module_hooks.combat_view(instance)["combat_state"] or "none"),
        "combat_active": bool(module_hooks.combat_view(instance)["combat_active"]),
        "initiative_order": copy.deepcopy(list(module_hooks.combat_view(instance)["initiative_order"] or [])),
        "initiative_current": int(module_hooks.combat_view(instance)["initiative_current"] or 0),
        "world_state": copy.deepcopy(instance.world_state),
        # FIX-07 §10 步骤 23：Adventure 进度与世界真相由同一次权威事务写入
        # （FIX-04 §6.7），所以"本轮改过的东西"必须包含它——否则回滚会把世界
        # 退回去、把进度留在被丢弃的分支上。
        "adventure_progress": copy.deepcopy(adventure_runtime_state.progress(instance)),
        # 规则运行时权威（D&D 遭遇、canonical 角色、event_ledger）在提交点前就会被
        # 战斗信号 / Director 自动化写入，必须与世界真相一起回到判定入口（#511）。
        "ruleset_runtime": ruleset_runtime.round_entry_snapshot(instance),
    })


def restore_round_entity_snapshot(instance: GameInstance) -> bool:
    """还原判定入口的旧版实体快照；没有快照时返回 False（不动状态）。"""
    from src.engine.modules import adventure_runtime_state, round_safety, ruleset_runtime

    snapshot = round_safety.round_entity_snapshot(instance)
    if not isinstance(snapshot, dict) or not snapshot:
        return False

    adventure_runtime_state.require_writable(instance)
    ruleset_runtime.require_writable(instance)
    instance.npcs = copy.deepcopy(snapshot.get("npcs") or {})
    # 世界真相按整轮语义回滚（ADR 0003）：本轮写入的 world ops 随本轮撤销。
    if "world_state" in snapshot:
        instance.world_state = ensure_world_state(snapshot.get("world_state"))
    # Adventure 进度与世界真相同一事务（FIX-04 §6.7），必须一起回到判定入口。
    if "adventure_progress" in snapshot:
        adventure_runtime_state.replace_progress(
            instance, copy.deepcopy(snapshot.get("adventure_progress") or {}),
        )
    # 旧快照没有该键：保持现状（与 adventure_progress 的兼容方式一致）。
    if "ruleset_runtime" in snapshot:
        if not ruleset_runtime.restore_round_entry(instance, snapshot.get("ruleset_runtime")):
            logger.warning("判定入口 event_ledger 已被替换，未回卷账本: game=%s", instance.game_key)
    return True


# ---------- 过期战斗结算缓存 -------------------------------


def drop_stale_combat_caches(instance: GameInstance, *, all_targets: bool = False) -> None:
    """丢弃"状态已回滚、缓存却仍记录伤害"的战斗结算缓存。

    实体快照与玩家快照已经把相关实体恢复到判定入口，缓存描述的却是另一个
    状态；保留它会命中 ``CombatResolver`` 的缓存重放分支——既不重掷命中骰
    也不重新扣血——于是结算记录（伤害 5）与实际 HP（恢复后的满血）互相矛盾。

    ``all_targets=True``（实体快照已完整还原，含 npcs）时一律
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
    ``player:<uid>`` / ``npc:<id>``，两种都要认。退役的 ``enemy:<index>``
    与其它未知引用一样返回 None。
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
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
