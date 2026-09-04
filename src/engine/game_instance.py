"""GameInstance 状态机 —— 单个跑团游戏的全部运行时状态与生命周期。"""

from __future__ import annotations

import asyncio
import copy
import logging
from contextlib import asynccontextmanager
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable
from uuid import uuid4

from src.engine.contracts import (
    ActionRecord,
    CheckResult,
    PlayerData,
    RoundLogEntry,
    StoryRecap,
    TableTalkExchange,
    TokenBudgetBump,
)
from src.engine.dice import parse_player_roll, roll as dice_roll, check_d20
from src.engine.character_utils import apply_resource_delta, get_resource
from src.engine.game_state_codec import GameStateCodec
from src.engine.game_state_contracts import (
    GameContextView,
    GamePersistedState,
    PlayerRollbackSnapshot,
)
from src.engine.health import record_health_event
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.engine.narrative_perspective import validate_narrative_perspective

if TYPE_CHECKING:
    from src.engine.plot_tracker import PlotTracker
    from src.engine.puzzle import PuzzleManager

logger = logging.getLogger("trpg")

MAX_SAVE_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_SAVE_STATE_BYTES = 16 * 1024 * 1024
MAX_SAVE_CHATLOG_BYTES = 128 * 1024 * 1024
MAX_SAVE_UNPACKED_BYTES = 128 * 1024 * 1024


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


def _snapshot_players(instance: GameInstance) -> PlayerRollbackSnapshot:
    """快照所有玩家可回滚状态（含死亡玩家，便于 swipe 复活）。

    覆盖运行时可变字段（HP/金币/SAN/LUCK/MANA/状态/背包/装备/法术）；
    不含 identity/progression（race/class/level/xp/skills 不随 swipe 回滚）。
    """
    import copy
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


# ---------- GameInstance ------------------------------------

@dataclass
class GameInstance:
    """单个跑团游戏的全部运行时状态。

    一个 GameInstance 对应一个 (platform, group_id, account_id) 三元组。
    所有状态变更通过方法进行，外部不应直接修改字段。
    每个实例自带 asyncio.Lock，保证单局操作的并发安全。
    """

    game_key: tuple[str, str, str]      # (platform, target_id, account_id)
    instance_schema_version: int = 6
    run_id: str = field(default_factory=lambda: f"run_{uuid4().hex}")
    memory_namespace: str = ""
    economy: dict[str, Any] = field(default_factory=dict)
    world_id: str | None = None
    rule_id: str = "freeform_fantasy"
    ruleset_runtime: dict[str, Any] = field(default_factory=dict)
    ruleset_state: dict[str, Any] = field(default_factory=dict)
    adventure_binding: dict[str, Any] = field(default_factory=dict)
    event_ledger: list[dict[str, Any]] = field(default_factory=list)
    scene_image: dict[str, str] = field(default_factory=dict)
    map_background: dict[str, str] = field(default_factory=dict)
    world_name: str = ""
    group_name: str = ""
    state: GameState = GameState.CREATED

    # 玩家与 NPC
    players: dict[str, PlayerData] = field(default_factory=dict)       # user_id -> {...}
    npcs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 回合
    round_number: int = 0
    action_queue: list[ActionRecord] = field(default_factory=list)
    pending_actions: list[ActionRecord] = field(default_factory=list)
    ready_players: set[str] = field(default_factory=set)
    away_players: set[str] = field(default_factory=set)

    # 战斗
    combat_active: bool = False
    combat_enemies: list[dict[str, Any]] = field(default_factory=list)
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
    private_log: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # user_id → 私聊历史
    # 公开桌边问答与回合日志分离；正常 GM 上下文不会读取此字段。
    table_talk: list[TableTalkExchange] = field(default_factory=list)

    # 场景
    scene: str = ""
    game_time: str = ""

    # 日志与摘要
    log: list[RoundLogEntry] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    key_facts: list = field(default_factory=list)

    # 运行时跟踪：chatlog.jsonl 已持久化的 log 条数（不入存档，仅用于增量追加）
    last_saved_log_count: int = 0

    # 统计
    total_llm_calls: int = 0
    total_tokens: int = 0
    started_at: str = ""
    last_activity: str = ""

    # 谜题
    puzzle_manager: PuzzleManager | None = None

    # 剧情追踪
    plot_tracker: PlotTracker | None = None

    # 判定卡片：最近一次检定的结构化结果（前端渲染用）
    last_check: CheckResult | None = None
    last_checks: list[CheckResult] = field(default_factory=list)
    # 当前判定阶段是否已生成结构化检定；幸运选择必须发生在 LLM 叙事之前。
    round_checks_prepared: bool = False
    # 进入判定阶段前的玩家状态；整轮撤回时用于退还本轮消耗的幸运。
    round_start_snapshot: PlayerRollbackSnapshot = field(default_factory=dict)
    # Death-save outcomes are keyed by round and player UID so narrative/API
    # retries reuse the same roll without leaking it into future rounds.
    death_save_outcomes: dict[str, dict[str, dict]] = field(default_factory=dict)

    # GM 私密指令：只注入 GM 上下文，不作为玩家/系统行动公开记录
    gm_directives: list[dict] = field(default_factory=list)

    # 状态变化 recap：最近一回合的 state_update（前端渲染用）
    last_state_update: dict | None = None

    # 本轮裁判标注的越权声明（仅多人局且开关启用时注入 GM 上下文）
    last_overreach: list = field(default_factory=list)

    # 最近一回合因输出截断触发的 token 预算升档（给 GM 的低打扰提示）
    last_token_budget_bump: TokenBudgetBump | None = None

    # 单人模式
    solo_mode: bool = False  # True=单人模式, 行动后自动推进

    # 种子码
    seed_code: str = ""

    # 难度
    difficulty: str = "标准"  # 轻松 / 标准 / 硬核

    # 叙事视角（展示偏好，不参与规则判定）
    narrative_perspective: str = "auto"  # auto / immersive / third_person

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

    # 系统健康 / 降级事件
    health_events: list[dict] = field(default_factory=list)
    health_status: dict = field(default_factory=dict)

    # 内部：并发锁
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # 内部：process_round/generate_swipe 互斥锁，防并发处理同一实例
    _process_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Runtime-only authority gate. Lock order is authority -> process -> state.
    _authority_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _authority_owner: asyncio.Task[Any] | None = field(default=None, repr=False)
    _authority_depth: int = field(default=0, repr=False)
    _rewrite_in_progress: bool = field(default=False, repr=False)
    _save_fail_count: int = field(default=0, repr=False)
    # 幸运超时（秒）：每条 pending 幸运检定独立倒计时，到点按失败继续；0=禁用（异步局可设 0）
    luck_timeout_seconds: int = 60
    # 内部：每条 pending 幸运检定的超时定时器（check_id -> asyncio.Task），不序列化
    _luck_timers: dict = field(default_factory=dict, repr=False)
    # 恢复后是否仍有待幸运决定的检定（recover_all 设置，供前端提示；定时器不跨重启）
    pending_luck_after_recovery: bool = False
    _tag_fail_streak: int = field(default=0, repr=False)
    # D1: 已确认事项（CONFIRMED 标签累积），注入 LLM 上下文防重复讨论
    confirmed_items: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = f"run_{uuid4().hex}"
        if not self.memory_namespace:
            self.memory_namespace = f"{self.game_key!s}::run:{self.run_id}"
        if not isinstance(self.economy, dict) or not self.economy:
            self.economy = self._fresh_economy_state()
        else:
            self.economy.setdefault("schema_version", 2)
            self.economy.setdefault("run_id", self.run_id)
            self.economy.setdefault("next_sequence", 1)
            self.economy.setdefault("proposals", [])
            self.economy.setdefault("transactions", [])
            self.economy.setdefault("idempotency_records", {})
            self.economy.setdefault("effect_groups", [])
            self.economy.setdefault("external_effects_outbox", [])
            self.economy.setdefault("outcomes", [])
            self.economy.setdefault("decision_revision", 0)

    def _fresh_economy_state(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "run_id": self.run_id,
            "next_sequence": 1,
            "proposals": [],
            "transactions": [],
            "idempotency_records": {},
            "effect_groups": [],
            "external_effects_outbox": [],
            "outcomes": [],
            "decision_revision": 0,
        }

    @asynccontextmanager
    async def authoritative_write(self) -> AsyncIterator[bool]:
        """Enter the atomic live-aggregate writer gate.

        Writers arriving during a historical rewrite are rejected before any
        mutation. The gate is task-reentrant so a service transaction may call
        guarded aggregate methods without deadlocking itself.
        """

        task = asyncio.current_task()
        if task is not None and self._authority_owner is task:
            self._authority_depth += 1
            try:
                yield True
            finally:
                self._authority_depth -= 1
            return
        if self._rewrite_in_progress:
            yield False
            return
        await self._authority_lock.acquire()
        self._authority_owner = task
        self._authority_depth = 1
        try:
            # A queued writer may resume after a rewrite. At that point the
            # rewrite is committed and the caller must revalidate identity.
            yield True
        finally:
            self._authority_depth = 0
            self._authority_owner = None
            self._authority_lock.release()

    @asynccontextmanager
    async def historical_rewrite(self) -> AsyncIterator[bool]:
        """Own the authority gate for a complete staged historical rewrite."""

        if self._authority_lock.locked() and self._rewrite_in_progress:
            yield False
            return
        await self._authority_lock.acquire()
        self._authority_owner = asyncio.current_task()
        self._authority_depth = 1
        self._rewrite_in_progress = True
        try:
            yield True
        finally:
            self._rewrite_in_progress = False
            self._authority_depth = 0
            self._authority_owner = None
            self._authority_lock.release()

    def rotate_run_identity(self) -> tuple[str, str]:
        """Start an isolated run namespace and return ``(old, new)``."""

        old = self.run_id
        self.run_id = f"run_{uuid4().hex}"
        self.memory_namespace = f"{self.game_key!s}::run:{self.run_id}"
        self.economy = self._fresh_economy_state()
        return old, self.run_id

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
        rule_id: str,
        world_name: str,
        group_name: str,
        seed_code: str,
        difficulty: str,
        language: str,
        state: GameState = GameState.WAITING,
    ) -> None:
        """配置新局身份信息；调用方在并发路径中应持有 ``_lock``。"""
        self.world_id = world_id
        self.rule_id = rule_id or "freeform_fantasy"
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
        luck_timeout_seconds: int | None = None,
        narrative_perspective: str | None = None,
    ) -> None:
        """集中更新入口与房间身份配置，保留旧存档字段。

        luck_timeout_seconds：每玩家幸运超时秒数（0=禁用，异步局建议 0）。
        """
        if solo_mode is not None:
            self.solo_mode = bool(solo_mode)
        if entry_point is not None:
            self.entry_point = entry_point
        if room_password is not None:
            self.room_password = room_password
        if gm_uid is not None:
            self.gm_uid = gm_uid
        if luck_timeout_seconds is not None:
            if not 0 <= int(luck_timeout_seconds) <= 3600:
                raise ValueError("幸运超时需在 0..3600 秒之间（0=禁用）")
            self.luck_timeout_seconds = int(luck_timeout_seconds)
        if narrative_perspective is not None:
            self.set_narrative_perspective(narrative_perspective)

    def bind_ruleset_runtime(self, binding: dict[str, Any]) -> bool:
        """Bind versioned ruleset state once; reject mixed-runtime characters."""

        normalized = {
            "id": str(binding.get("runtime_id") or ""),
            "version": int(binding.get("runtime_version", 0) or 0),
            "content_version": str(binding.get("content_version") or ""),
            "state_schema_version": int(binding.get("state_schema_version", 0) or 0),
        }
        if not all((
            normalized["id"], normalized["version"],
            normalized["content_version"], normalized["state_schema_version"],
        )):
            return False
        if self.ruleset_runtime and self.ruleset_runtime != normalized:
            return False
        self.ruleset_runtime = normalized
        if not self.ruleset_state:
            self.ruleset_state = {
                "state_schema_version": normalized["state_schema_version"],
            }
        return True

    def bind_adventure(self, binding: dict[str, Any] | None) -> bool:
        """Bind one immutable adventure package, or explicitly select sandbox."""

        value = dict(binding or {})
        if value:
            required = {"adventure_id", "version", "format", "content_digest", "world_id"}
            if set(value) != required or not all(str(value.get(key) or "") for key in required):
                return False
            if str(value["world_id"]) != str(self.world_id or ""):
                return False
        if self.adventure_binding and self.adventure_binding != value:
            return False
        self.adventure_binding = value
        return True

    def set_scene_image(self, reference: dict[str, str]) -> None:
        """Set the portable adventure scene-image reference."""
        self.scene_image = dict(reference or {})

    def set_map_background(self, selection: dict[str, str]) -> None:
        """Set this save's validated map-background selection."""
        self.map_background = dict(selection or {})

    def replace_players(self, players: dict[str, PlayerData]) -> None:
        self.players = players

    def restore_ruleset_transaction(self, snapshot: dict[str, Any]) -> None:
        """Restore the bounded state touched by an authoritative ruleset transaction.

        Callers capture the snapshot before invoking a runtime reducer. Keeping
        the rollback assignment here preserves the aggregate write boundary
        while allowing ruleset orchestration to remain transaction-aware.
        """
        self.ruleset_state = copy.deepcopy(snapshot["ruleset_state"])
        self.event_ledger = copy.deepcopy(snapshot["event_ledger"])
        self.players = copy.deepcopy(snapshot["players"])
        self.combat_state = str(snapshot["combat_state"])
        self.combat_active = bool(snapshot["combat_active"])
        self.initiative_order = copy.deepcopy(snapshot["initiative_order"])
        self.initiative_current = int(snapshot["initiative_current"])
        if "scene" in snapshot and snapshot["scene"] is not None:
            self.scene = str(snapshot["scene"])
        if "last_activity" in snapshot:
            self.last_activity = str(snapshot["last_activity"])
        if "log" in snapshot:
            self.log = copy.deepcopy(snapshot["log"])
        if "round_number" in snapshot:
            self.round_number = int(snapshot["round_number"])

    def set_player_access(self, open_access: bool) -> None:
        self.player_access_open = bool(open_access)

    def set_bot_bind_token(self, token: str) -> None:
        self.bot_bind_token = token

    def set_room_password(self, password: str) -> None:
        """设置房间密码；非空时要求至少 4 位。空串表示取消密码（开放房）。"""
        if password and len(password) < 4:
            raise ValueError("房间密码至少 4 位")
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

    def set_narrative_perspective(self, perspective: str) -> None:
        self.narrative_perspective = validate_narrative_perspective(perspective)

    def append_log_entry(self, entry: RoundLogEntry) -> None:
        self.log.append(entry)

    async def append_story_recap(
        self,
        recap: StoryRecap,
        *,
        target_entry: RoundLogEntry,
        tokens: int = 0,
    ) -> bool:
        """Attach a public recap to one real round without creating a fake round."""
        async with self._lock:
            target = next((entry for entry in self.log if entry is target_entry), None)
            if target is None:
                return False
            recaps = target.get("story_recaps")
            if not isinstance(recaps, list):
                recaps = []
                target["story_recaps"] = recaps
            recaps.append(recap)
            self.record_llm_usage(tokens)
            self.last_activity = datetime.now(timezone.utc).isoformat()
            return True

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

    def append_table_talk(self, exchange: TableTalkExchange, *, limit: int = 50) -> None:
        """Append a bounded public table-talk exchange without touching turn state."""
        self.table_talk.append(exchange)
        if len(self.table_talk) > limit:
            del self.table_talk[:-limit]

    def add_gm_directive(self, directive: dict) -> None:
        self.gm_directives.append(directive)

    def clear_private_messages(self, uid: str) -> None:
        self.private_log.pop(uid, None)

    def record_check(self, check: CheckResult) -> None:
        """记录结构化检定，并保持 last_check 与 last_checks 一致。"""
        self.last_checks.append(check)
        self.last_check = check

    def sync_last_check(self, check: CheckResult) -> None:
        """刷新最近检定快照，同时隔离可变的轮次检定记录。"""
        self.last_check = dict(check)

    def reset_round_checks(self, *, prepared: bool = False) -> None:
        self.last_check = None
        self.last_checks.clear()
        self.last_overreach.clear()
        self.round_checks_prepared = prepared

    def mark_log_persisted(self) -> None:
        """记录当前日志已经完整写入增量聊天日志。"""
        self.last_saved_log_count = len(self.log)

    def restore_log_history(self, history: list[RoundLogEntry]) -> None:
        """原子替换恢复后的完整日志，并同步持久化游标。"""
        self.log = history
        self.mark_log_persisted()

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
            from src.engine.economy import reconcile_rollback_snapshot, reverse_round_economy

            reverse_round_economy(self, int(last.get("round", self.round_number) or self.round_number))
            snapshot = last.get("round_start_snapshot") or last.get("pre_state_snapshot", {})
            if isinstance(snapshot, dict) and snapshot:
                restore_players(self, reconcile_rollback_snapshot(self, snapshot, int(last.get("round", self.round_number) or self.round_number)))
            self.round_number = max(1, int(last.get("round", self.round_number) or 1))
            self.action_queue.clear()
            self.pending_actions.clear()
            self.ready_players.clear()
            # ``reverse_round_economy`` restores still-valid proposals whose
            # settlement happened in the rolled-back round.  Do not clear the
            # compatibility projection after that restoration.
            self.reset_round_checks()
            # Explicit rollback starts a fresh attempt for that round; do not
            # let a discarded outcome affect the replay or a later round.
            self.death_save_outcomes.clear()
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
        """原子处理一次幸运选择（逻辑见 src/engine/luck_resolver.py，P2-G Step 1）。"""
        return await luck_resolver.resolve_luck_decision(
            self, check_id, actor_uid, spend, rule=rule, allow_gm=allow_gm,
        )

    async def decline_pending_luck(self) -> list[dict]:
        """GM 强制推进时将所有未选择的幸运检定按失败继续（逻辑见 luck_resolver）。"""
        return await luck_resolver.decline_pending_luck(self)

    async def system_decline_luck(self, check_id: str) -> dict:
        """幸运超时定时器触发：按失败继续单条幸运检定（逻辑见 luck_resolver）。"""
        return await luck_resolver.system_decline_luck(self, check_id)

    def _cancel_luck_timer(self, check_id: str) -> None:
        """取消并移除某条检定的幸运超时定时器（逻辑见 luck_resolver）。"""
        luck_resolver._cancel_luck_timer(self, check_id)

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
            current = str(self.round_number)
            self.death_save_outcomes = {
                current: self.death_save_outcomes.get(current, {})
            }
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
        async with self.authoritative_write() as write_entered, self._lock:
            if not write_entered or self._process_lock.locked():
                return False
            if user_id in self.players:
                cs = self.get_character_sheet(user_id)
                if cs.get("deceased"):
                    return False  # 死亡玩家不能行动
                self.away_players.discard(user_id)
            action_entry: ActionRecord = {
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
        async with self.authoritative_write() as write_entered, self._lock:
            if not write_entered:
                return False
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
            from src.engine.economy import has_blocking_economy_decision

            if has_blocking_economy_decision(self):
                return False
            return self._do_advance_locked()

    async def try_advance(self) -> bool:
        """原子推进：检查条件 + 推进在同一个锁内完成，消除 TOCTOU 竞态。"""
        async with self._lock:
            from src.engine.economy import has_blocking_economy_decision

            if has_blocking_economy_decision(self):
                return False
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

    async def finish_judgment_with_swipe(
        self,
        gm_response: str,
        original_round: int,
        state_changes: list[str] | None = None,
    ) -> None:
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
                    if state_changes is not None:
                        entry["state_changes"] = list(state_changes)
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
            saved_narrative_perspective = self.narrative_perspective
            saved_language = normalize_language(self.language)
            saved_ruleset_runtime = copy.deepcopy(self.ruleset_runtime)
            saved_adventure_binding = copy.deepcopy(self.adventure_binding)
            self.rotate_run_identity()
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
            self.confirmed_items.clear()
            self.private_log.clear()
            self.table_talk.clear()
            self.last_check = None
            self.last_checks.clear()
            self.round_checks_prepared = False
            self.round_start_snapshot.clear()
            self.last_state_update = None
            self.last_token_budget_bump = None
            self.gm_directives.clear()
            self.ruleset_runtime = saved_ruleset_runtime
            self.ruleset_state = (
                {"state_schema_version": int(saved_ruleset_runtime.get("state_schema_version", 1) or 1)}
                if saved_ruleset_runtime else {}
            )
            self.adventure_binding = saved_adventure_binding
            self.event_ledger.clear()
            self.state = GameState.CREATED
            self.world_id = saved_world_id
            self.world_name = saved_world_name
            self.group_name = saved_group_name
            self.solo_mode = saved_solo
            self.narrative_perspective = saved_narrative_perspective
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

    def to_dict(self) -> GamePersistedState:
        """Return the stable persisted projection for this aggregate."""
        return GameStateCodec.encode(self)

    def replace_persisted_state_from(self, source: "GameInstance") -> None:
        """Commit a staged aggregate without replacing its runtime identity.

        Economy decisions are prepared against an isolated ``GameInstance`` so
        a failing dependent effect cannot leave half of a transaction applied.
        Only persisted domain fields cross this boundary; locks, timers, save
        counters and other process-local coordination remain attached to the
        live instance.
        """

        if source.game_key != self.game_key or source.run_id != self.run_id:
            raise ValueError("staged game state belongs to a different run")
        runtime_only = {
            "last_saved_log_count",
            "pending_luck_after_recovery",
        }
        for name, value in source.__dict__.items():
            if name.startswith("_") or name in runtime_only:
                continue
            setattr(self, name, copy.deepcopy(value))

    def to_llm_view(self) -> GameContextView:
        """LLM 决策所需的精简状态视图。

        排除运行时元数据（health_events、total_tokens 等）和重复数据
        （log、summary、key_facts、confirmed_items、plot_tracker 等），
        这些由 context_builder 单独注入。含属性修正和护甲计算。
        """
        from src.engine.legacy_game_projection import project_legacy_game_context

        return project_legacy_game_context(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GameInstance":
        """Reconstruct the aggregate from its persisted projection."""
        return GameStateCodec.decode(
            data,
            instance_type=cls,
            state_type=GameState,
        )


# ---------- GameRegistry -----------------------------------

class GameRegistry:
    """全局游戏实例管理器。

    按 game_key 索引所有 GameInstance，负责持久化。
    插件 on_load 时创建单例，on_unload 时 save_all_active。
    """

    def __init__(self, save_dir: Path):
        self._instances: dict[tuple, GameInstance] = {}
        self._save_locks: dict[tuple, asyncio.Lock] = {}
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
        """构造存档路径（逻辑见 src/engine/persistence.py，P2-G Step 2）。"""
        return persistence._save_path(self, game_key)

    def save_package_state_path(self, game_key: tuple) -> Path:
        """Return the state path used by the portable save-package boundary."""

        return persistence._save_path(self, game_key)

    async def save(self, instance: GameInstance) -> None:
        """Persist only the live aggregate for this game key.

        A reset/restart keeps the public game key while rotating ``run_id``.
        Rejecting stale object identities prevents an old request that resumes
        late from overwriting the newly installed run on disk.
        """

        lock = self._save_locks.setdefault(instance.game_key, asyncio.Lock())
        async with lock:
            current = self.get(instance.game_key)
            if current is not None and current is not instance:
                raise RuntimeError("stale game instance cannot overwrite the current run")
            await persistence.save(self, instance)

    async def replace_current(
        self,
        expected: GameInstance,
        candidate: GameInstance,
    ) -> None:
        """Atomically persist and install a replacement run."""

        if candidate.game_key != expected.game_key:
            raise ValueError("replacement game key mismatch")
        lock = self._save_locks.setdefault(expected.game_key, asyncio.Lock())
        async with lock:
            if self.get(expected.game_key) is not expected:
                raise RuntimeError("game run changed while replacement was being prepared")
            await persistence.save(self, candidate)
            self.register(candidate)

    # P2-G Step 2：_chatlog_path / _append_chatlog / _truncate_chatlog 已迁到
    # src/engine/persistence.py（仅 persistence 内部使用，无外部调用，不设委托）。

    async def load(self, game_key: tuple) -> GameInstance | None:
        """加载存档（逻辑见 persistence）。"""
        return await persistence.load(self, game_key)

    # P2-G Step 2：_restore_chatlog 已迁到 persistence.py（仅内部使用，无外部调用）。

    async def recover_all(self) -> list[GameInstance]:
        """启动时恢复未完成对局（逻辑见 persistence）。"""
        return await persistence.recover_all(self)

    async def import_save_zip(
        self,
        payload: bytes,
        *,
        platform: str = "web",
        account_id: str = "web_bot",
        scene_image_importer: Callable[[bytes], dict[str, Any]] | None = None,
        map_background_importer: Callable[[bytes], dict[str, Any]] | None = None,
    ) -> dict:
        """导入导出的存档 zip（逻辑见 persistence）。"""
        return await persistence.import_save_zip(
            self,
            payload,
            platform=platform,
            account_id=account_id,
            scene_image_importer=scene_image_importer,
            map_background_importer=map_background_importer,
        )

    async def save_all_active(self) -> None:
        """保存所有活跃对局（逻辑见 persistence）。"""
        await persistence.save_all_active(self)


# 底部导入避免循环依赖：luck_resolver/persistence 顶部 import 本模块的
# GameState/GameInstance/GameRegistry，本模块的薄委托方法在调用时才解析模块名
#（此时对应模块已加载）。
from src.engine import luck_resolver, persistence  # noqa: E402
