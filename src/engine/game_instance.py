"""GameInstance 状态机 —— 单个跑团游戏的全部运行时状态与生命周期。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from src.engine.dice import parse_player_roll, roll as dice_roll, check_d20
from src.engine.health import record_health_event
from src.engine.language import DEFAULT_LANGUAGE, normalize_language

logger = logging.getLogger("trpg")


# ---------- 游戏状态枚举 ------------------------------------

class GameState(Enum):
    """游戏生命周期状态。"""
    CREATED = "created"                  # 已创建，等待开始
    WAITING = "waiting"                  # 等待玩家加入
    ACTIVE_ACTION = "active_action"      # 行动阶段：接受玩家声明
    ACTIVE_JUDGMENT = "active_judgment"  # 判定阶段：LLM 处理中
    PUZZLE = "puzzle"                    # 谜题阶段：等待玩家解谜
    PAUSED = "paused"                    # 暂停（bot 重启后恢复为此状态）
    ENDED = "ended"                      # 已结束


def _snapshot_players(instance) -> dict:
    """快照所有玩家可回滚状态（含死亡玩家，便于 swipe 复活）。

    覆盖运行时可变字段（HP/金币/SAN/LUCK/MANA/状态/背包/装备/法术）；
    不含 identity/progression（race/class/level/xp/skills 不随 swipe 回滚）。
    """
    import copy
    snap = {}
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


def restore_players(instance, snapshot: dict) -> None:
    """从快照恢复玩家可回滚状态（含 deceased/death_round，便于 swipe 复活）。"""
    for uid, snap in snapshot.items():
        if uid not in instance.players:
            continue
        cs = instance.get_character_sheet(uid)
        for key, value in snap.items():
            cs[key] = value
        instance.players[uid]["character_sheet"] = cs


def _referenced_player_ids(log: list) -> set[str]:
    """从历史日志里提取真正参与过本局的玩家 ID。"""
    referenced: set[str] = set()
    for entry in log or []:
        for action in entry.get("actions", []) or []:
            uid = action.get("user_id")
            if uid and uid != "system":
                referenced.add(uid)
        snapshot = entry.get("pre_state_snapshot", {})
        if isinstance(snapshot, dict):
            referenced.update(uid for uid in snapshot if uid and uid != "system")
    return referenced


def _prune_ghost_players(instance) -> None:
    """加载旧存档时清理明显不属于本局的幽灵玩家。

    只在有历史日志依据时执行；等待房间、无日志新局、从未推进的多人局不会被误删。
    """
    if len(instance.players) <= 1 or not instance.log:
        return
    referenced = _referenced_player_ids(instance.log)
    if not referenced:
        return
    ghost_ids = sorted(uid for uid in instance.players if uid not in referenced)
    if not ghost_ids:
        return
    for uid in ghost_ids:
        instance.players.pop(uid, None)
        instance.ready_players.discard(uid)
        instance.away_players.discard(uid)
    instance.action_queue = [a for a in instance.action_queue if a.get("user_id") not in ghost_ids]
    instance.pending_actions = [a for a in instance.pending_actions if a.get("user_id") not in ghost_ids]
    logger.warning("加载存档时移除幽灵玩家: game_key=%s, players=%s", instance.game_key, ghost_ids)


# ---------- GameInstance ------------------------------------

@dataclass
class GameInstance:
    """单个跑团游戏的全部运行时状态。

    一个 GameInstance 对应一个 (platform, group_id, account_id) 三元组。
    所有状态变更通过方法进行，外部不应直接修改字段。
    每个实例自带 asyncio.Lock，保证单局操作的并发安全。
    """

    game_key: tuple[str, str, str]      # (platform, target_id, account_id)
    world_id: str | None = None
    world_name: str = ""
    group_name: str = ""
    state: GameState = GameState.CREATED

    # 玩家与 NPC
    players: dict = field(default_factory=dict)       # user_id -> {...}
    npcs: dict = field(default_factory=dict)

    # 回合
    round_number: int = 0
    action_queue: list = field(default_factory=list)
    pending_actions: list = field(default_factory=list)
    ready_players: set = field(default_factory=set)
    away_players: set = field(default_factory=set)

    # 战斗
    combat_active: bool = False
    combat_enemies: list = field(default_factory=list)
    combat_state: str = "none"  # "none" / "active"
    initiative_order: list[str] = field(default_factory=list)
    initiative_current: int = 0

    # 玩家管理
    max_players: int = 6
    gm_uid: str = ""  # 创建游戏的 GM 的 user_id
    player_access_open: bool = True  # False 时所有玩家分享链接失效
    bot_bind_token: str = ""  # 渠道 Bot 绑定本局的一次性管理凭证
    room_password: str = ""  # 房间密码（空=开放）；玩家凭此进入游戏，替代后台 access_token
    room_token: str = ""  # 玩家凭房间密码换取的会话凭证（random secrets，校验通过后颁发）
    private_log: dict[str, list[dict]] = field(default_factory=dict)  # user_id → 私聊历史

    # 场景
    scene: str = ""
    game_time: str = ""

    # 日志与摘要
    log: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    key_facts: list = field(default_factory=list)

    # 统计
    total_llm_calls: int = 0
    total_tokens: int = 0
    started_at: str = ""
    last_activity: str = ""

    # 谜题
    puzzle_manager: object | None = None   # PuzzleManager 实例

    # 剧情追踪
    plot_tracker: object | None = None     # PlotTracker 实例

    # 判定卡片：最近一次检定的结构化结果（前端渲染用）
    last_check: dict | None = None

    # 状态变化 recap：最近一回合的 state_update（前端渲染用）
    last_state_update: dict | None = None

    # 单人模式
    solo_mode: bool = False  # True=单人模式, 行动后自动推进

    # 种子码
    seed_code: str = ""

    # 难度
    difficulty: str = "标准"  # 轻松 / 标准 / 硬核

    # 叙事语言
    language: str = DEFAULT_LANGUAGE  # "zh-CN" / "en"

    # 入口模式
    entry_point: str = "web"  # "web" / "plugin"

    # 战斗结算缓存（供 WebUI 展示）
    pending_combat_results: list[dict] = field(default_factory=list)

    # 世界书时间效应状态
    lorebook_timed_state: dict[str, dict] = field(default_factory=dict)

    # WebUI 快捷行动建议
    quick_actions: list[str] = field(default_factory=list)

    # 等待玩家确认的支付请求
    pending_payments: list[dict] = field(default_factory=list)

    # 系统健康 / 降级事件
    health_events: list[dict] = field(default_factory=list)
    health_status: dict = field(default_factory=dict)

    # 内部：并发锁
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # 内部：process_round/generate_swipe 互斥锁，防并发处理同一实例
    _process_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # D1: 已确认事项（CONFIRMED 标签累积），注入 LLM 上下文防重复讨论
    confirmed_items: list = field(default_factory=list)

    # ---------- 状态查询 ------------------------------------

    @property
    def alive_players(self) -> set[str]:
        """当前存活的玩家 user_id 集合。"""
        return {
            uid for uid in self.players
            if self.is_alive(uid)
        }

    @property
    def active_alive_players(self) -> set[str]:
        """当前需要参与行动等待的存活玩家。暂离玩家仍在队伍中，但不阻塞回合。"""
        return self.alive_players.difference(self.away_players)

    def get_character_sheet(self, uid: str) -> dict:
        """获取指定玩家的角色卡，不存在时返回空 dict。"""
        return self.players.get(uid, {}).get("character_sheet", {})

    def set_character_sheet(self, uid: str, character_sheet: dict) -> bool:
        """写回指定玩家的角色卡；玩家不存在时返回 False。"""
        if uid not in self.players:
            return False
        self.players[uid]["character_sheet"] = character_sheet
        return True

    def iter_player_sheets(self):
        """遍历玩家及其角色卡，yield (uid, player_data, character_sheet)。"""
        for uid, player in self.players.items():
            yield uid, player, self.get_character_sheet(uid)

    def is_alive(self, uid: str) -> bool:
        """玩家是否存活（存在且未标记 deceased）。"""
        return uid in self.players and not self.get_character_sheet(uid).get("deceased", False)

    def is_dead(self, uid: str) -> bool:
        """玩家是否已死亡。"""
        return uid in self.players and self.get_character_sheet(uid).get("deceased", False)

    def can_accept_actions(self) -> bool:
        return self.state == GameState.ACTIVE_ACTION

    def all_alive_ready(self) -> bool:
        """多人模式下，所有未暂离的存活角色都提交行动后才自动推进。"""
        active = self.active_alive_players
        if not active:
            return False
        return active.issubset(self.ready_players)

    def multiplayer_status(self) -> dict:
        """返回多人协调所需的轻量状态。"""
        alive = self.alive_players
        active = self.active_alive_players
        ready = active.intersection(self.ready_players)
        waiting = active.difference(self.ready_players)
        away = alive.intersection(self.away_players)

        def player_label(uid: str) -> str:
            return self.players.get(uid, {}).get("character_name") or uid

        return {
            "state": self.state.value,
            "round_number": self.round_number,
            "solo_mode": self.solo_mode,
            "player_count": len(self.players),
            "max_players": self.max_players,
            "ready_count": len(ready),
            "alive_count": len(alive),
            "active_count": len(active),
            "away_count": len(away),
            "ready_players": [
                {"user_id": uid, "character_name": player_label(uid)}
                for uid in sorted(ready)
            ],
            "waiting_players": [
                {"user_id": uid, "character_name": player_label(uid)}
                for uid in sorted(waiting)
            ],
            "away_players": [
                {"user_id": uid, "character_name": player_label(uid)}
                for uid in sorted(away)
            ],
            "can_accept_actions": self.can_accept_actions(),
            "can_advance": self.can_accept_actions() and bool(self.action_queue),
            "action_count": len(self.action_queue),
            "submitted_actions": [
                {
                    "user_id": a.get("user_id", ""),
                    "character_name": player_label(a.get("user_id", "")),
                    "text": a.get("text", ""),
                    "revision_count": int(a.get("revision_count", 1) or 1),
                    "dice_pending": bool(a.get("dice_pending")),
                    "dice_system": str(a.get("dice_system", "") or ""),
                    "dice_roll_source": str(a.get("dice_roll_source", "") or ""),
                }
                for a in self.action_queue
            ],
            "pending_action_count": len(self.pending_actions),
            "gm_uid": self.gm_uid,
            "player_access_open": self.player_access_open,
        }

    # ---------- 回合推进 ------------------------------------

    def should_advance(self) -> bool:
        """任一满足即推进：所有存活玩家已就绪，或单人模式下任一玩家已行动。"""
        if self.has_pending_dice():
            return False
        if self.solo_mode and self.action_queue:
            return True
        return self.all_alive_ready()

    async def start_round(self) -> None:
        """开启新一轮行动阶段。"""
        async with self._lock:
            self.round_number += 1
            self.state = GameState.ACTIVE_ACTION
            self.action_queue.clear()
            self.ready_players.clear()
            if self.pending_actions:
                self.action_queue.extend(self.pending_actions)
                self.pending_actions.clear()
            self.last_activity = datetime.now(timezone.utc).isoformat()
            logger.info("Round %d 开始 - game_key=%s", self.round_number, self.game_key)

    async def add_action(self, user_id: str, action_text: str,
                         selected_attribute: str = "", selected_skill: str = "",
                         target_text: str = "", source: str = "",
                         dice_pending: bool = False, dice_system: str = "",
                         count_revision: bool = True) -> bool:
        """玩家声明行动。判决阶段中的发言缓存到下一轮。

        selected_attribute/selected_skill/target_text 为前端可选提交的结构化
        归因字段（P1），供检定与 prompt 直接使用，避免靠文本启发式猜。
        """
        async with self._lock:
            if user_id in self.players:
                cs = self.get_character_sheet(user_id)
                if cs.get("deceased"):
                    return False  # 死亡玩家不能行动
                self.away_players.discard(user_id)
            action_entry = {
                "user_id": user_id, "text": action_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "selected_attribute": selected_attribute,
                "selected_skill": selected_skill,
                "target_text": target_text,
                "source": source,
            }
            if dice_pending:
                action_entry["dice_pending"] = True
                action_entry["dice_system"] = dice_system or "d20"
            if not self.can_accept_actions():
                self.pending_actions.append(action_entry)
                return False
            if not self.solo_mode:
                existing_index = next(
                    (index for index, action in enumerate(self.action_queue)
                     if action.get("user_id") == user_id),
                    None,
                )
                if existing_index is not None:
                    existing = self.action_queue[existing_index]
                    old_roll = next(
                        (line for line in str(existing.get("text", "")).splitlines()
                         if line.startswith("(系统掷骰:") and line.endswith(")")),
                        "",
                    )
                    if old_roll:
                        clean_text = "\n".join(
                            line for line in str(action_text).splitlines()
                            if not (line.startswith("(系统掷骰:") and line.endswith(")"))
                        ).rstrip()
                        action_entry["text"] = f"{clean_text}\n{old_roll}"
                        action_entry["dice_pending"] = False
                        action_entry["dice_system"] = existing.get("dice_system", "")
                        action_entry["dice_roll_source"] = existing.get("dice_roll_source", "")
                    old_revision = int(existing.get("revision_count", 1) or 1)
                    action_entry["revision_count"] = old_revision + 1 if count_revision else old_revision
                    self.action_queue[existing_index] = action_entry
                else:
                    action_entry["revision_count"] = 1
                    self.action_queue.append(action_entry)
            else:
                self.action_queue.append(action_entry)
            self.ready_players.add(user_id)
            self.last_activity = datetime.now(timezone.utc).isoformat()
            return True

    def has_pending_dice(self, user_id: str | None = None) -> bool:
        return any(
            action.get("dice_pending")
            and (user_id is None or action.get("user_id") == user_id)
            for action in self.action_queue
        )

    def pending_dice_actions(self, user_id: str | None = None) -> list[dict]:
        return [
            action for action in self.action_queue
            if action.get("dice_pending")
            and (user_id is None or action.get("user_id") == user_id)
        ]

    async def apply_action_roll(self, user_id: str, dice_system: str, value: int, *, source: str = "player") -> bool:
        """Attach a resolved roll to a pending action without counting as an edit."""
        async with self._lock:
            action = next(
                (
                    item for item in self.action_queue
                    if item.get("user_id") == user_id and item.get("dice_pending")
                ),
                None,
            )
            if not action:
                return False
            clean_text = "\n".join(
                line for line in str(action.get("text", "")).splitlines()
                if not (line.startswith("(系统掷骰:") and line.endswith(")"))
            ).rstrip()
            system = dice_system or str(action.get("dice_system") or "d20")
            action["text"] = f"{clean_text}\n(系统掷骰: {system}={int(value)})"
            action["dice_pending"] = False
            action["dice_system"] = system
            action["dice_roll_source"] = source
            action["dice_value"] = int(value)
            self.ready_players.add(user_id)
            self.last_activity = datetime.now(timezone.utc).isoformat()
            return True

    async def remove_player(self, user_id: str) -> bool:
        """移除玩家，清理关联状态。"""
        async with self._lock:
            if user_id not in self.players:
                return False
            del self.players[user_id]
            self.ready_players.discard(user_id)
            self.away_players.discard(user_id)
            self.action_queue = [a for a in self.action_queue if a.get("user_id") != user_id]
            self.pending_actions = [a for a in self.pending_actions if a.get("user_id") != user_id]
            logger.info("玩家已移除: %s, game_key=%s", user_id, self.game_key)
            return True

    async def set_player_away(self, user_id: str, away: bool = True) -> bool:
        """标记玩家暂离/回来。暂离玩家仍在队伍中，但不阻塞多人回合。"""
        async with self._lock:
            if user_id not in self.players or not self.is_alive(user_id):
                return False
            if away:
                self.away_players.add(user_id)
                self.ready_players.discard(user_id)
            else:
                self.away_players.discard(user_id)
            self.last_activity = datetime.now(timezone.utc).isoformat()
            return True

    async def advance_round(self) -> bool:
        """显式推进回合。未行动的存活玩家标记为已就绪。"""
        async with self._lock:
            return self._do_advance_locked()

    async def try_advance(self) -> bool:
        """原子推进：检查条件 + 推进在同一个锁内完成，消除 TOCTOU 竞态。"""
        async with self._lock:
            if self.state != GameState.ACTIVE_ACTION:
                return False
            if not self.should_advance():
                return False
            return self._do_advance_locked()

    def _do_advance_locked(self) -> bool:
        """在锁内执行推进（调用方需持锁）。"""
        if self.state != GameState.ACTIVE_ACTION:
            return False
        for uid in self.alive_players:
            self.ready_players.add(uid)
        self.state = GameState.ACTIVE_JUDGMENT
        logger.info("进入判定阶段 - game_key=%s, actions=%d",
                     self.game_key, len(self.action_queue))
        return True

    async def finish_judgment(self, gm_response: str, pre_state_snapshot: dict | None = None, state_changes: list[str] | None = None) -> None:
        """判定完成，记录本轮并开启下一轮。

        pre_state_snapshot 应为 _apply_state_update 之前拍摄的快照，
        确保 swipe 重生成时恢复到本轮初始状态而非应用后状态。
        state_changes 为本轮玩家可见状态变动摘要，随 log entry 持久化供群机器人单独转发。
        """
        import copy
        async with self._lock:
            self.log.append({
                "round": self.round_number,
                "actions": list(self.action_queue),
                "gm_response": gm_response,
                "state_changes": list(state_changes or []),
                "swipes": [],
                "current_swipe": 0,
                "pre_state_snapshot": pre_state_snapshot if pre_state_snapshot is not None else _snapshot_players(self),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.total_llm_calls += 1
            self.last_activity = datetime.now(timezone.utc).isoformat()
        await self.start_round()

    async def finish_judgment_with_swipe(self, gm_response: str, original_round: int) -> None:
        """为已有轮次添加 swipe（不推进回合）。"""
        async with self._lock:
            for entry in self.log:
                if entry.get("round") == original_round:
                    swipes = entry.setdefault("swipes", [])
                    if not swipes:
                        swipes.append(entry.get("gm_response", ""))
                    swipes.append(gm_response)
                    entry["current_swipe"] = len(swipes) - 1
                    entry["gm_response"] = gm_response
                    break
            self.total_llm_calls += 1
            self.last_activity = datetime.now(timezone.utc).isoformat()

    async def switch_swipe(self, round_num: int, swipe_idx: int) -> bool:
        """切换指定轮次的 swipe 展示。"""
        for entry in self.log:
            if entry.get("round") == round_num:
                swipes = entry.get("swipes", [])
                if not swipes or swipe_idx >= len(swipes):
                    return False
                entry["current_swipe"] = swipe_idx
                entry["gm_response"] = swipes[swipe_idx]
                logger.info("Swipe 切换: round=%d → %d/%d", round_num, swipe_idx, len(swipes))
                return True
        return False

    # ---------- 状态转换 ------------------------------------

    async def activate(self) -> None:
        async with self._lock:
            self.state = GameState.ACTIVE_ACTION
            if not self.started_at:
                self.started_at = datetime.now(timezone.utc).isoformat()
            self.last_activity = datetime.now(timezone.utc).isoformat()
            logger.info("游戏激活 - game_key=%s", self.game_key)

    async def pause(self) -> None:
        async with self._lock:
            self.state = GameState.PAUSED

    async def resume(self) -> None:
        async with self._lock:
            self.state = GameState.ACTIVE_ACTION

    async def end(self) -> None:
        async with self._lock:
            self.state = GameState.ENDED

    async def reset(self, keep_seed: bool = True) -> None:
        async with self._lock:
            saved_seed = self.seed_code if keep_seed else ""
            saved_world_id = self.world_id
            saved_world_name = self.world_name
            saved_group_name = self.group_name
            saved_solo = self.solo_mode
            saved_language = normalize_language(self.language)
            self.players.clear()
            self.npcs.clear()
            self.round_number = 0
            self.action_queue.clear()
            self.pending_actions.clear()
            self.ready_players.clear()
            self.combat_active = False
            self.combat_enemies.clear()
            self.combat_state = "none"
            self.initiative_order.clear()
            self.initiative_current = 0
            self.scene = ""
            self.game_time = ""
            self.log.clear()
            self.summary.clear()
            self.key_facts.clear()
            self.total_llm_calls = 0
            self.total_tokens = 0
            self.started_at = ""
            self.last_activity = ""
            self.puzzle_manager = None
            self.plot_tracker = None
            self.pending_combat_results.clear()
            self.lorebook_timed_state.clear()
            self.health_events.clear()
            self.health_status.clear()
            self.quick_actions.clear()
            self.pending_payments.clear()
            self.confirmed_items.clear()
            self.private_log.clear()
            self.last_check = None
            self.last_state_update = None
            self.state = GameState.CREATED
            self.world_id = saved_world_id
            self.world_name = saved_world_name
            self.group_name = saved_group_name
            self.solo_mode = saved_solo
            self.language = saved_language
            self.seed_code = saved_seed
            logger.info("游戏已重置 (seed=%s) - game_key=%s", self.seed_code, self.game_key)

    # ---------- 序列化 --------------------------------------

    def update_lorebook_timed_state(self) -> None:
        """每轮开始前更新世界书时间效应状态：remaining - 1，归零则移除。"""
        expired = [eid for eid, state in self.lorebook_timed_state.items()
                   if state["remaining"] <= 1]
        for eid in expired:
            del self.lorebook_timed_state[eid]
        for state in self.lorebook_timed_state.values():
            state["remaining"] -= 1

    # ---------- 序列化 --------------------------------------

    def to_dict(self) -> dict:
        data = {
            "game_key": list(self.game_key),
            "world_id": self.world_id,
            "world_name": self.world_name,
            "group_name": self.group_name,
            "state": self.state.value,
            "players": self.players,
            "npcs": self.npcs,
            "round_number": self.round_number,
            "action_queue": self.action_queue,
            "pending_actions": self.pending_actions,
            "ready_players": sorted(self.ready_players),
            "away_players": sorted(self.away_players),
            "combat_active": self.combat_active,
            "combat_enemies": self.combat_enemies,
            "combat_state": self.combat_state,
            "initiative_order": self.initiative_order,
            "initiative_current": self.initiative_current,
            "scene": self.scene,
            "game_time": self.game_time,
            "log": self.log[-100:],
            "summary": self.summary,
            "key_facts": self.key_facts,
            "total_llm_calls": self.total_llm_calls,
            "total_tokens": self.total_tokens,
            "started_at": self.started_at,
            "last_activity": self.last_activity,
            "solo_mode": self.solo_mode,
            "seed_code": self.seed_code,
            "difficulty": self.difficulty,
            "language": normalize_language(self.language),
            "entry_point": self.entry_point,
            "max_players": self.max_players,
            "gm_uid": self.gm_uid,
            "player_access_open": self.player_access_open,
            "bot_bind_token": self.bot_bind_token,
            "room_password": self.room_password,
            "room_token": self.room_token,
            "pending_combat_results": self.pending_combat_results,
            "lorebook_timed_state": self.lorebook_timed_state,
            "quick_actions": self.quick_actions,
            "pending_payments": [
                p for p in self.pending_payments
                if isinstance(p, dict) and p.get("status") == "pending"
            ],
            "health_events": self.health_events[-100:],
            "health_status": self.health_status,
            "last_check": self.last_check,
            "last_state_update": self.last_state_update,
            "confirmed_items": self.confirmed_items,
            "private_log": self.private_log,
        }
        if self.puzzle_manager and hasattr(self.puzzle_manager, "to_active_dict"):
            data["puzzles"] = self.puzzle_manager.to_active_dict()
        if self.plot_tracker and hasattr(self.plot_tracker, "to_dict"):
            data["plot_tracker"] = self.plot_tracker.to_dict()
        return data

    def to_llm_view(self) -> dict:
        """LLM 决策所需的精简状态视图。

        排除运行时元数据（health_events、total_tokens 等）和重复数据
        （log、summary、key_facts、confirmed_items、plot_tracker 等），
        这些由 context_builder 单独注入。含属性修正和护甲计算。
        """
        players_view: dict[str, dict] = {}
        for uid, pdata in self.players.items():
            cs = pdata.get("character_sheet", {})
            attrs = cs.get("attributes", {})
            equipment = cs.get("equipment", [])
            skills = cs.get("skills", [])
            if skills and isinstance(skills[0], str):
                skills = [{"name": s, "value": 20} for s in skills]
            sheet: dict = {
                "hp": cs.get("hp", 0),
                "max_hp": cs.get("max_hp", 0),
                "class": cs.get("class", ""),
                "race": cs.get("race", ""),
                "level": cs.get("level", 1),
                "xp": cs.get("xp", 0),
                "gold": cs.get("gold", 0),
                "attributes": attrs,
                "_modifiers": {k: (v - 10) // 2 for k, v in attrs.items()},
                "equipment": equipment,
                "_armor": sum(
                    eq.get("armor", 1) if eq.get("type") in ("armor", "clothing")
                    else eq.get("armor", 0)
                    for eq in equipment
                ),
                "skills": skills,
                "inventory": cs.get("inventory", []),
            }
            if cs.get("background"):
                sheet["background"] = cs["background"]
            if cs.get("deceased"):
                sheet["deceased"] = True
            ss: dict[str, int] = {}
            for key in ("sanity", "qi", "luck", "cyberware", "cyberware_load", "humanity", "heat"):
                if key in cs:
                    ss[key] = cs[key]
            if ss:
                sheet["_special_stats"] = ss
            players_view[uid] = {
                "character_name": pdata.get("character_name", ""),
                "attendance": "away" if uid in self.away_players else "active",
                "character_sheet": sheet,
            }
        away_names = [
            self.players.get(uid, {}).get("character_name") or uid
            for uid in sorted(self.away_players)
            if uid in self.players and self.is_alive(uid)
        ]
        state: dict = {
            "world_name": self.world_name,
            "round_number": self.round_number,
            "scene": self.scene,
            "game_time": self.game_time,
            "difficulty": self.difficulty,
            "language": normalize_language(self.language),
            "players": players_view,
            "away_players": away_names,
            "npcs": self.npcs,
            "combat_state": self.combat_state,
            "combat_enemies": self.combat_enemies,
            "initiative_order": self.initiative_order,
            "initiative_current": self.initiative_current,
            "quick_actions": self.quick_actions,
        }
        if away_names:
            state["attendance_note"] = "暂离角色默认跟随队伍，不主动做重大决定，不承担关键风险；除非玩家回来或 GM 明确点名。"
        if self.combat_state == "active":
            state["combat_active"] = True
        if self.solo_mode:
            state["solo_mode"] = True
        if self.puzzle_manager and hasattr(self.puzzle_manager, "to_active_dict"):
            puzzles = self.puzzle_manager.to_active_dict()
            if puzzles:
                state["puzzles"] = puzzles
        return state

    @classmethod
    def from_dict(cls, data: dict) -> "GameInstance":
        inst = cls(
            game_key=tuple(data["game_key"]),
            world_id=data.get("world_id"),
            world_name=data.get("world_name", ""),
            group_name=data.get("group_name", ""),
            state=GameState(data["state"]),
            players=data.get("players", {}),
            npcs=data.get("npcs", {}),
            round_number=data.get("round_number", 0),
            action_queue=data.get("action_queue", []),
            pending_actions=data.get("pending_actions", []),
            combat_active=data.get("combat_active", False),
            combat_enemies=data.get("combat_enemies", []),
            combat_state=data.get("combat_state", "none"),
            initiative_order=data.get("initiative_order", []),
            initiative_current=data.get("initiative_current", 0),
            scene=data.get("scene", ""),
            game_time=data.get("game_time", ""),
            log=data.get("log", []),
            summary=data.get("summary", {}),
            key_facts=data.get("key_facts", []),
            total_llm_calls=data.get("total_llm_calls", 0),
            total_tokens=data.get("total_tokens", 0),
            started_at=data.get("started_at", ""),
            last_activity=data.get("last_activity", ""),
            solo_mode=data.get("solo_mode", False),
            seed_code=data.get("seed_code", ""),
            difficulty=data.get("difficulty", "标准"),
            language=normalize_language(data.get("language", DEFAULT_LANGUAGE)),
            entry_point=data.get("entry_point", "web"),
            max_players=data.get("max_players", 6),
            gm_uid=data.get("gm_uid", ""),
            player_access_open=data.get("player_access_open", True),
            bot_bind_token=data.get("bot_bind_token", ""),
            room_password=data.get("room_password", ""),
            room_token=data.get("room_token", ""),
            pending_combat_results=data.get("pending_combat_results", []),
            lorebook_timed_state=data.get("lorebook_timed_state", {}),
            quick_actions=data.get("quick_actions", []),
            pending_payments=[p for p in data.get("pending_payments", []) if isinstance(p, dict) and p.get("status") == "pending"],
            health_events=data.get("health_events", []),
            health_status=data.get("health_status", {}),
            last_check=data.get("last_check"),
            last_state_update=data.get("last_state_update"),
        )
        inst.ready_players = set(data.get("ready_players", []))
        inst.away_players = set(data.get("away_players", []))
        inst.confirmed_items = data.get("confirmed_items", [])
        inst.private_log = data.get("private_log", {})
        puzzles_data = data.get("puzzles")
        if puzzles_data:
            from src.engine.puzzle import PuzzleManager
            inst.puzzle_manager = PuzzleManager.from_dict(puzzles_data)
        plot_data = data.get("plot_tracker")
        if plot_data:
            from src.engine.plot_tracker import PlotTracker
            inst.plot_tracker = PlotTracker.from_dict(plot_data)
        else:
            from src.engine.plot_tracker import PlotTracker
            inst.plot_tracker = PlotTracker()
        _prune_ghost_players(inst)
        return inst


# ---------- GameRegistry -----------------------------------

class GameRegistry:
    """全局游戏实例管理器。

    按 game_key 索引所有 GameInstance，负责持久化。
    插件 on_load 时创建单例，on_unload 时 save_all_active。
    """

    def __init__(self, save_dir: Path):
        self._instances: dict[tuple, GameInstance] = {}
        self.save_dir = Path(save_dir)

    # ---------- CRUD ---------------------------------------

    def get(self, game_key: tuple) -> GameInstance | None:
        return self._instances.get(game_key)

    def get_or_create(self, game_key: tuple) -> GameInstance:
        if game_key not in self._instances:
            self._instances[game_key] = GameInstance(game_key=game_key)
        return self._instances[game_key]

    def register(self, instance: GameInstance) -> None:
        self._instances[instance.game_key] = instance

    def remove(self, game_key: tuple) -> None:
        self._instances.pop(game_key, None)

    def list_active(self) -> list[GameInstance]:
        return [i for i in self._instances.values()
                if i.state not in (GameState.ENDED,)]

    def list_all(self) -> list[GameInstance]:
        return list(self._instances.values())

    @staticmethod
    def make_game_key(platform: str, target_id: str, account_id: str) -> tuple:
        return (platform, target_id, account_id)

    # ---------- 持久化 -------------------------------------

    _KEY_SEPARATOR = "#"

    def _save_path(self, game_key: tuple) -> Path:
        key_str = self._KEY_SEPARATOR.join(str(x) for x in game_key)
        path = self.save_dir / key_str / "state.json"
        base = self.save_dir.resolve()
        parent = path.parent.resolve()
        if base != parent and base not in parent.parents:
            raise ValueError(f"非法 game_key 存档路径: {game_key}")
        return path

    async def save(self, instance: GameInstance) -> None:
        """写入存档: tmp -> backup rename -> tmp rename。"""
        sp = self._save_path(instance.game_key)
        sp.parent.mkdir(parents=True, exist_ok=True)
        backup = sp.with_name("state.backup.json")

        data = instance.to_dict()
        tmp = sp.with_name("state.tmp.json")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        if sp.exists():
            sp.replace(backup)
        tmp.replace(sp)

    async def load(self, game_key: tuple) -> GameInstance | None:
        """加载存档，优先 state.json，回退到 backup。兼容旧版 , 分隔存档目录。"""
        sp = self._save_path(game_key)
        backup = sp.with_name("state.backup.json")

        if not sp.exists():
            for old_sep in (",", "|"):
                old_key_str = old_sep.join(str(x) for x in game_key)
                old_sp = self.save_dir / old_key_str / "state.json"
                old_backup = self.save_dir / old_key_str / "state.backup.json"
                if old_sp.exists() or old_backup.exists():
                    sp = old_sp
                    backup = old_backup
                    break

        recovered_from_backup = False
        if not sp.exists():
            if not backup.exists():
                return None
            sp = backup
            recovered_from_backup = True
            logger.warning("主存档不存在，使用备份: %s", sp)

        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.exception("存档 JSON 损坏: %s", sp)
            if backup.exists() and sp != backup:
                data = json.loads(backup.read_text(encoding="utf-8"))
                recovered_from_backup = True
            else:
                return None

        instance = GameInstance.from_dict(data)
        if recovered_from_backup:
            record_health_event(
                instance,
                component="save",
                code="SAVE_RECOVERED_FROM_BACKUP",
                severity="warning",
                title="已从备份存档恢复",
                message="主存档缺失或损坏，系统已加载 state.backup.json。",
                impact="最近一次保存后的少量进度可能未恢复。",
                fallback="backup_state",
                repair_hint="建议检查 data/saves 目录权限、磁盘空间和 state.json 格式。",
            )
        self.register(instance)
        logger.info("存档已加载: %s, round=%d", game_key, instance.round_number)
        return instance

    async def recover_all(self) -> list[GameInstance]:
        """启动时扫描 saves/ 恢复所有未完成对局，设为 PAUSED。"""
        recovered: list[GameInstance] = []
        if not self.save_dir.exists():
            return recovered

        for entry in self.save_dir.iterdir():
            if not entry.is_dir():
                continue
            if not (entry / "state.json").exists() and \
               not (entry / "state.backup.json").exists():
                continue
            try:
                parts = entry.name.split(self._KEY_SEPARATOR)
                if len(parts) < 3:
                    for old_sep in ("|", ","):
                        parts = entry.name.split(old_sep)
                        if len(parts) >= 3:
                            break
                game_key = tuple(parts[:3])
                instance = await self.load(game_key)
                if instance and instance.state != GameState.ENDED:
                    instance.state = GameState.PAUSED
                    recovered.append(instance)
            except Exception:
                logger.exception("恢复存档失败: %s", entry.name)

        logger.info("存档恢复完成: %d 个对局", len(recovered))
        return recovered

    async def save_all_active(self) -> None:
        for instance in self.list_active():
            try:
                await self.save(instance)
            except Exception:
                logger.exception("保存失败: %s", instance.game_key)
                record_health_event(
                    instance,
                    component="save",
                    code="SAVE_FAILED",
                    severity="error",
                    title="存档失败",
                    message="当前游戏仍在内存中，但服务器重启后可能丢失最近进度。",
                    impact="重启后可能回到旧回合。",
                    fallback="memory_only",
                    repair_hint="检查 data/saves 权限、磁盘空间和 JSON 文件是否被占用。",
                )
