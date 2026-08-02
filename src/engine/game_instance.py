"""GameInstance 状态机 —— 单个跑团游戏的全部运行时状态与生命周期。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from src.engine.contracts import (
    ActionRecord,
    CheckResult,
    PendingPayment,
    PlayerData,
    RoundLogEntry,
    TokenBudgetBump,
)
from src.engine.dice import parse_player_roll, roll as dice_roll, check_d20
from src.engine.character_utils import apply_resource_delta, get_resource
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
    players: dict[str, PlayerData] = field(default_factory=dict)       # user_id -> {...}
    npcs: dict[str, dict] = field(default_factory=dict)

    # 回合
    round_number: int = 0
    action_queue: list[ActionRecord] = field(default_factory=list)
    pending_actions: list[ActionRecord] = field(default_factory=list)
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
    log: list[RoundLogEntry] = field(default_factory=list)
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
    last_check: CheckResult | None = None
    last_checks: list[CheckResult] = field(default_factory=list)
    # 当前判定阶段是否已生成结构化检定；幸运选择必须发生在 LLM 叙事之前。
    round_checks_prepared: bool = False
    # 进入判定阶段前的玩家状态；整轮撤回时用于退还本轮消耗的幸运。
    round_start_snapshot: dict = field(default_factory=dict)

    # GM 私密指令：只注入 GM 上下文，不作为玩家/系统行动公开记录
    gm_directives: list[dict] = field(default_factory=list)

    # 状态变化 recap：最近一回合的 state_update（前端渲染用）
    last_state_update: dict | None = None

    # 最近一回合因输出截断触发的 token 预算升档（给 GM 的低打扰提示）
    last_token_budget_bump: TokenBudgetBump | None = None

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
    pending_payments: list[PendingPayment] = field(default_factory=list)

    # 系统健康 / 降级事件
    health_events: list[dict] = field(default_factory=list)
    health_status: dict = field(default_factory=dict)

    # 内部：并发锁
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # 内部：process_round/generate_swipe 互斥锁，防并发处理同一实例
    _process_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _save_fail_count: int = field(default=0, repr=False)
    _tag_fail_streak: int = field(default=0, repr=False)
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

    def get_player(self, uid: str) -> PlayerData | None:
        return self.players.get(uid)

    def put_player(self, uid: str, player: PlayerData) -> None:
        """Insert or replace one complete player record."""
        self.players[uid] = player

    def set_player_name(self, uid: str, character_name: str) -> bool:
        if uid not in self.players:
            return False
        self.players[uid]["character_name"] = character_name
        return True

    # ---------- 统一状态写入 --------------------------------

    def configure_game(
        self,
        *,
        world_id: str | None,
        world_name: str,
        group_name: str,
        seed_code: str,
        difficulty: str,
        language: str,
        state: GameState = GameState.WAITING,
    ) -> None:
        """配置新局身份信息；调用方在并发路径中应持有 ``_lock``。"""
        self.world_id = world_id
        self.world_name = world_name
        self.group_name = group_name
        self.seed_code = seed_code
        self.difficulty = difficulty
        self.language = normalize_language(language)
        self.state = state

    def configure_session(
        self,
        *,
        solo_mode: bool | None = None,
        entry_point: str | None = None,
        room_password: str | None = None,
        gm_uid: str | None = None,
    ) -> None:
        """集中更新入口与房间身份配置，保留旧存档字段。"""
        if solo_mode is not None:
            self.solo_mode = bool(solo_mode)
        if entry_point is not None:
            self.entry_point = entry_point
        if room_password is not None:
            self.room_password = room_password
        if gm_uid is not None:
            self.gm_uid = gm_uid

    def replace_players(self, players: dict[str, PlayerData]) -> None:
        self.players = players

    def set_player_access(self, open_access: bool) -> None:
        self.player_access_open = bool(open_access)

    def set_bot_bind_token(self, token: str) -> None:
        self.bot_bind_token = token

    def set_room_password(self, password: str) -> None:
        self.room_password = password
        self.room_token = ""

    def set_room_token(self, token: str) -> None:
        self.room_token = token

    def set_scene(self, scene: str) -> None:
        self.scene = scene

    def set_world(self, world_id: str, world_name: str) -> None:
        self.world_id = world_id
        self.world_name = world_name

    def set_difficulty(self, difficulty: str) -> None:
        self.difficulty = difficulty

    def set_solo_mode(self, solo_mode: bool) -> None:
        self.solo_mode = bool(solo_mode)
        if self.solo_mode and self.action_queue and self.state == GameState.ACTIVE_ACTION:
            self.ready_players.update(self.alive_players)

    def append_log_entry(self, entry: RoundLogEntry) -> None:
        self.log.append(entry)

    def set_latest_log_tags_summary(self, summary: dict) -> bool:
        if not self.log:
            return False
        self.log[-1]["tags_summary"] = summary
        return True

    def set_summary_narrative(self, narrative: str) -> None:
        self.summary["narrative"] = narrative

    def set_quick_actions(self, actions: list[str]) -> None:
        self.quick_actions = [str(action) for action in actions if str(action).strip()]

    def set_key_facts(self, facts: list) -> None:
        self.key_facts = list(facts)

    def add_confirmed_items(self, items: list[str], *, limit: int = 50) -> None:
        existing = set(self.confirmed_items)
        for item in items:
            if item not in existing:
                self.confirmed_items.append(item)
                existing.add(item)
        if len(self.confirmed_items) > limit:
            del self.confirmed_items[:-limit]

    def append_private_message(self, uid: str, message: dict) -> None:
        self.private_log.setdefault(uid, []).append(message)

    def add_gm_directive(self, directive: dict) -> None:
        self.gm_directives.append(directive)

    def clear_private_messages(self, uid: str) -> None:
        self.private_log.pop(uid, None)

    def queue_payment(self, payment: PendingPayment) -> None:
        self.pending_payments.append(payment)

    def remove_payments_for_player(self, uid: str) -> None:
        self.pending_payments = [
            payment
            for payment in self.pending_payments
            if payment.get("uid") != uid and payment.get("recipient_uid") != uid
        ]

    def prune_resolved_payments(self) -> None:
        self.pending_payments = [
            payment
            for payment in self.pending_payments
            if payment.get("status") == "pending"
        ]

    def mark_payment_resolved(
        self,
        payment_id: str,
        status: Literal["accepted", "declined", "rejected"],
        *,
        resolved_at: float,
    ) -> PendingPayment | None:
        for payment in self.pending_payments:
            if payment.get("id") == payment_id:
                payment["status"] = status
                payment["resolved_at"] = resolved_at
                return payment
        return None

    def record_check(self, check: CheckResult) -> None:
        """记录结构化检定，并保持 last_check 与 last_checks 一致。"""
        self.last_checks.append(check)
        self.last_check = check

    def reset_round_checks(self, *, prepared: bool = False) -> None:
        self.last_check = None
        self.last_checks.clear()
        self.round_checks_prepared = prepared

    def complete_round_check_preparation(self) -> None:
        if self.last_checks:
            self.last_check = self.last_checks[-1]
        self.round_checks_prepared = True

    def begin_round_processing(self) -> None:
        """清理仅属于上一轮展示的短期状态。"""
        self.last_token_budget_bump = None
        self.pending_combat_results.clear()
        self.update_lorebook_timed_state()

    def set_token_budget_bump(self, initial: int, used: int, *, kind: str = "narrative") -> None:
        self.last_token_budget_bump = (
            {"kind": kind, "from": initial, "to": used}
            if used > initial > 0
            else None
        )

    def set_state_update_recap(self, state_update: dict | None) -> None:
        self.last_state_update = state_update or None

    def consume_gm_directives(self, directive_ids: set[str]) -> None:
        if not directive_ids:
            return
        self.gm_directives = [
            directive
            for directive in self.gm_directives
            if str(directive.get("id") or "") not in directive_ids
        ]

    def record_llm_usage(self, tokens: int = 0, *, calls: int = 1) -> None:
        self.total_tokens += max(0, int(tokens or 0))
        self.total_llm_calls += max(0, int(calls or 0))

    def record_combat_result(self, result: dict) -> None:
        self.pending_combat_results.append(result)

    def begin_combat(self, initiative_order: list[str]) -> None:
        self.initiative_order = list(initiative_order)
        self.initiative_current = 0
        self.combat_state = "active"
        self.combat_active = True

    def end_combat(self) -> None:
        self.combat_state = "none"
        self.combat_active = False
        self.initiative_order.clear()
        self.initiative_current = 0

    def record_save_success(self) -> None:
        self._save_fail_count = 0

    def record_save_failure(self) -> int:
        self._save_fail_count += 1
        return self._save_fail_count

    def set_tag_failure_streak(self, streak: int) -> None:
        self._tag_fail_streak = max(0, int(streak))

    def ensure_round_managers(self) -> None:
        """惰性初始化回合管理器，避免调用方直接替换运行时组件。"""
        if self.plot_tracker is None:
            from src.engine.plot_tracker import PlotTracker

            self.plot_tracker = PlotTracker()
        if self.puzzle_manager is None:
            from src.engine.puzzle import PuzzleManager

            self.puzzle_manager = PuzzleManager()

    async def rollback_last_round(self) -> int | None:
        """恢复到上一轮开始前；返回恢复后的轮次，没有日志时返回 None。"""
        async with self._lock:
            if not self.log:
                return None
            last = self.log.pop()
            snapshot = last.get("round_start_snapshot") or last.get("pre_state_snapshot", {})
            if isinstance(snapshot, dict) and snapshot:
                restore_players(self, snapshot)
            self.round_number = max(1, int(last.get("round", self.round_number) or 1))
            self.action_queue.clear()
            self.pending_actions.clear()
            self.ready_players.clear()
            self.pending_payments.clear()
            self.reset_round_checks()
            self.round_start_snapshot.clear()
            self.state = GameState.ACTIVE_ACTION
            self.last_activity = datetime.now(timezone.utc).isoformat()
            return self.round_number

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

    def pending_luck_checks(self, user_id: str = "") -> list[dict]:
        """返回当前等待玩家决定是否消耗幸运的检定。"""
        return [
            dict(check)
            for check in self.last_checks
            if check.get("luck_decision") == "pending"
            and (not user_id or str(check.get("actor_uid") or "") == user_id)
        ]

    async def resolve_luck_decision(
        self,
        check_id: str,
        actor_uid: str,
        spend: bool,
        *,
        rule=None,
        allow_gm: bool = False,
    ) -> dict:
        """原子处理一次幸运选择，重复请求不会重复扣除资源。"""
        async with self._lock:
            target = next(
                (check for check in self.last_checks if str(check.get("check_id") or "") == check_id),
                None,
            )
            if not target:
                return {"ok": False, "code": "CHECK_NOT_FOUND", "error": "检定不存在或已过期"}
            owner_uid = str(target.get("actor_uid") or "")
            if actor_uid != owner_uid and not (allow_gm and actor_uid == self.gm_uid):
                return {"ok": False, "code": "LUCK_FORBIDDEN", "error": "只能处理自己的幸运选择"}

            desired = "spent" if spend else "declined"
            current_decision = str(target.get("luck_decision") or "")
            if current_decision and current_decision != "pending":
                if current_decision == desired:
                    return {
                        "ok": True,
                        "already_resolved": True,
                        "check_result": dict(target),
                    }
                return {"ok": False, "code": "LUCK_ALREADY_RESOLVED", "error": "该检定的幸运选择已经处理"}
            if self.state != GameState.ACTIVE_JUDGMENT or not self.round_checks_prepared:
                return {"ok": False, "code": "LUCK_NOT_PENDING", "error": "当前没有等待处理的幸运选择"}
            if current_decision != "pending":
                return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不能消耗幸运"}

            if spend:
                if str(target.get("dice") or "").lower() != "d100" or str(target.get("verdict") or "") != "失败":
                    return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不能消耗幸运"}
                roll_value = int(target.get("roll", 0) or 0)
                threshold = int(target.get("threshold", 0) or 0)
                cost = roll_value - threshold
                if cost <= 0:
                    return {"ok": False, "code": "LUCK_NOT_AVAILABLE", "error": "该检定不需要消耗幸运"}
                character_sheet = self.get_character_sheet(owner_uid)
                resource = get_resource(character_sheet, "luck")
                current_luck = int((resource or {}).get("current", character_sheet.get("luck", 0)) or 0)
                if current_luck < cost:
                    return {
                        "ok": False,
                        "code": "LUCK_INSUFFICIENT",
                        "error": f"幸运不足：需要 {cost} 点，当前只有 {current_luck} 点",
                    }
                remaining = apply_resource_delta(character_sheet, "luck", -cost, rule)
                target["original_verdict"] = target.get("verdict")
                target["verdict"] = "成功"
                target["luck_spent"] = cost
                target["luck_remaining"] = remaining

            target["luck_decision"] = desired
            target["luck_spend_available"] = False
            target["luck_resolved_at"] = datetime.now(timezone.utc).isoformat()
            if self.last_check and str(self.last_check.get("check_id") or "") == check_id:
                self.last_check = dict(target)
            self.last_activity = datetime.now(timezone.utc).isoformat()
            return {"ok": True, "check_result": dict(target)}

    async def decline_pending_luck(self) -> list[dict]:
        """GM 强制推进时将所有未选择的幸运检定按失败继续。"""
        async with self._lock:
            declined: list[dict] = []
            now = datetime.now(timezone.utc).isoformat()
            for check in self.last_checks:
                if check.get("luck_decision") != "pending":
                    continue
                check["luck_decision"] = "declined"
                check["luck_spend_available"] = False
                check["luck_resolved_at"] = now
                declined.append(dict(check))
            if declined:
                if self.last_check:
                    last_id = str(self.last_check.get("check_id") or "")
                    replacement = next(
                        (check for check in self.last_checks if str(check.get("check_id") or "") == last_id),
                        None,
                    )
                    if replacement:
                        self.last_check = dict(replacement)
                self.last_activity = now
            return declined

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
                    **({"check_request": a.get("check_request")} if a.get("check_request") else {}),
                }
                for a in self.action_queue
                if a.get("user_id") in self.players
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
            self.round_checks_prepared = False
            self.round_start_snapshot.clear()
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
                         check_request: dict | None = None,
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
            if check_request:
                action_entry["check_request"] = dict(check_request)
            if not self.can_accept_actions():
                self.pending_actions.append(action_entry)
                return False
            # 切换行动时替换同玩家的旧条目（solo 与多人一致）：
            # 避免 solo 模式反复追加堆积多条行动、触发 3 条上限，
            # 也让未掷骰的旧检定随替换作废，不再卡住掷骰。
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
                    action_entry["dice_value"] = existing.get("dice_value")
                    action_entry["dice_rolls"] = list(existing.get("dice_rolls") or [])
                    action_entry["check_request"] = existing.get("check_request")
                old_revision = int(existing.get("revision_count", 1) or 1)
                action_entry["revision_count"] = old_revision + 1 if count_revision else old_revision
                self.action_queue[existing_index] = action_entry
            else:
                action_entry["revision_count"] = 1
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

    async def apply_action_roll(
        self,
        user_id: str,
        dice_system: str,
        value: int,
        *,
        rolls: list[int] | None = None,
        source: str = "player",
    ) -> bool:
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
            action["dice_rolls"] = [int(item) for item in (rolls or [value])]
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
        self.round_checks_prepared = False
        self.round_start_snapshot = _snapshot_players(self)
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
                "check_results": [dict(item) for item in self.last_checks],
                "round_start_snapshot": (
                    copy.deepcopy(self.round_start_snapshot)
                    if self.round_start_snapshot else _snapshot_players(self)
                ),
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
            self.last_checks.clear()
            self.round_checks_prepared = False
            self.round_start_snapshot.clear()
            self.last_state_update = None
            self.last_token_budget_bump = None
            self.gm_directives.clear()
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
            "last_checks": self.last_checks,
            "round_checks_prepared": self.round_checks_prepared,
            "round_start_snapshot": self.round_start_snapshot,
            "last_state_update": self.last_state_update,
            "last_token_budget_bump": self.last_token_budget_bump,
            "gm_directives": self.gm_directives,
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
            last_checks=data.get("last_checks") or [],
            round_checks_prepared=bool(data.get("round_checks_prepared", False)),
            round_start_snapshot=data.get("round_start_snapshot") or {},
            last_state_update=data.get("last_state_update"),
            last_token_budget_bump=data.get("last_token_budget_bump"),
            gm_directives=data.get("gm_directives", []),
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
        parts = [str(x) for x in game_key]
        if any(not part or "/" in part or "\\" in part or part in {".", ".."} for part in parts):
            raise ValueError(f"非法 game_key 存档路径: {game_key}")
        key_str = self._KEY_SEPARATOR.join(parts)
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
        """启动时恢复未完成对局；待幸运选择保持可处理，其余对局暂停。"""
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
                    if not (
                        instance.state == GameState.ACTIVE_JUDGMENT
                        and instance.round_checks_prepared
                        and instance.pending_luck_checks()
                    ):
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
