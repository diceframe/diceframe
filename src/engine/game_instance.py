"""GameInstance 状态机 —— 单个跑团游戏的全部运行时状态与生命周期。"""

from __future__ import annotations

import asyncio
import copy
import logging
from contextlib import asynccontextmanager
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable
from uuid import uuid4

from src.engine import instance_lifecycle, progression, round_recovery, round_snapshots, turn_state
from src.engine.action_gate import (
    AI_SEAT_COMMIT_POLICY, GateRequest, SOURCE_AI_SEAT, SOURCE_HUMAN,
    StructuredIntentRequirement, check_run_unchanged, evaluate,
)
from src.engine.contracts import (
    ActionRecord,
    CheckResult,
    PlayerData,
    RoundLogEntry,
    StoryRecap,
    TableTalkExchange,
    TokenBudgetBump,
)
from src.engine.game_state import GameState
from src.engine.game_state_codec import GameStateCodec
from src.engine.game_state_contracts import (
    GameContextView,
    GamePersistedState,
    PlayerRollbackSnapshot,
)
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.engine.module_state import ensure_module_states
from src.engine.modules import (
    combat_extension_state,
    economy_state,
    lorebook_runtime,
    media,
    player_control_state,
    private_channels,
    progression_state,
)
from src.engine.narrative_perspective import validate_narrative_perspective
from src.engine.player_control import (
    PlayerControlError,
    begin_away_hosting,
    control_change_block,
    control_mode,
    end_away_hosting,
    ensure_control,
    ensure_controls,
    normalize_away_control_policy,
    set_control,
)
from src.engine.round_snapshots import (
    snapshot_players as _snapshot_players,
    restore_players,
)
from src.engine.world_state import ensure_world_state, fresh_world_state
from src.lorebook.activation import TIMED_COUNTER_KEYS, advance_timed_state
from src.migrations.instance import CURRENT_INSTANCE_SCHEMA_VERSION

if TYPE_CHECKING:
    from src.engine.plot_tracker import PlotTracker
    from src.engine.puzzle import PuzzleManager

logger = logging.getLogger("trpg")

MAX_SAVE_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_SAVE_STATE_BYTES = 16 * 1024 * 1024
MAX_SAVE_CHATLOG_BYTES = 128 * 1024 * 1024
MAX_SAVE_UNPACKED_BYTES = 128 * 1024 * 1024


# ---------- 游戏状态枚举 ------------------------------------
# GameState 已抽到 src/engine/game_state.py（无依赖契约模块）；
# 此处 re-export 保持 `from src.engine.game_instance import GameState` 兼容。

# 玩家快照实现已迁到 src/engine/round_snapshots.py；上面的 alias import 保持
# `from src.engine.game_instance import _snapshot_players / restore_players`
# 的既有调用方（round_processor / swipe_generator 等）不变。


# ---------- GameInstance ------------------------------------

# FIX-02 §4.2：adventure binding 的基础身份字段。来源身份（source_kind/source_id）
# 是可选新增字段；engine 只按身份字段比较，不认识 adventure 域的具体语义。
_ADVENTURE_BINDING_BASE_FIELDS = (
    "adventure_id", "version", "format", "content_digest", "world_id",
)


def _same_adventure_binding(current: Any, candidate: Any) -> bool:
    """Whether two bindings describe the same immutable package.

    基础身份必须一致；来源身份只在**双方都声明**时比较——这样旧存档的
    5 字段绑定可以与重启/重开后重新解析出的来源感知绑定视为同一绑定，
    而"同一个 id 换成另一个来源"仍然被拒绝。
    """

    if not isinstance(current, dict) or not isinstance(candidate, dict):
        return current == candidate
    if current == candidate:
        return True
    for key in _ADVENTURE_BINDING_BASE_FIELDS:
        if str(current.get(key) or "") != str(candidate.get(key) or ""):
            return False
    current_source = str(current.get("source_kind") or "")
    candidate_source = str(candidate.get("source_kind") or "")
    if not current_source or not candidate_source:
        return True
    return (
        current_source == candidate_source
        and str(current.get("source_id") or "") == str(candidate.get("source_id") or "")
    )


@dataclass
class GameInstance:
    """单个跑团游戏的全部运行时状态。

    一个 GameInstance 对应一个 (platform, group_id, account_id) 三元组。
    所有状态变更通过方法进行，外部不应直接修改字段。
    每个实例自带 asyncio.Lock，保证单局操作的并发安全。
    """

    game_key: tuple[str, str, str]      # (platform, target_id, account_id)
    instance_schema_version: int = CURRENT_INSTANCE_SCHEMA_VERSION
    run_id: str = field(default_factory=lambda: f"run_{uuid4().hex}")
    memory_namespace: str = ""
    world_id: str | None = None
    rule_id: str = "freeform_fantasy"
    ruleset_runtime: dict[str, Any] = field(default_factory=dict)
    ruleset_state: dict[str, Any] = field(default_factory=dict)
    adventure_binding: dict[str, Any] = field(default_factory=dict)
    # FIX-04 §6.2：Adventure v2 进度（active/completed nodes/objectives/milestones +
    # history）的权威持久化位置。v1 的 campaign 进度仍在 ruleset_state，两者并存
    # 互不迁移（母方案 §71/§122）。
    adventure_progress: dict[str, Any] = field(default_factory=dict)
    # Explicitly separates standard free play from an adventure story flow.
    # Empty means legacy/in-memory construction; runtime derives from the
    # bound adventure until creation/migration writes an explicit mode.
    play_mode: str = ""
    event_ledger: list[dict[str, Any]] = field(default_factory=list)
    world_name: str = ""
    group_name: str = ""
    state: GameState = GameState.CREATED

    # 玩家与 NPC
    players: dict[str, PlayerData] = field(default_factory=dict)       # user_id -> {...}
    npcs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 回合
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

    # 场景
    scene: str = ""
    game_time: str = ""

    # 日志与摘要
    log: list[RoundLogEntry] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    key_facts: list = field(default_factory=list)

    # 权威世界真相（Issue #284）：世界事实 / 逻辑时钟 / 定时事件容器。它不是
    # key_facts 这类叙事摘要，也不属于 ruleset_state；唯一写入口是
    # ``src.engine.world_state.apply_world_ops``。
    world_state: dict[str, Any] = field(default_factory=fresh_world_state)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)

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
    manual_roll_requests: list[dict[str, Any]] = field(default_factory=list)
    # 本轮规划发现的无价购买意图（payer/target/quantity）。只在回合内存中
    # 传递：供结算阶段的同轮 LOOT 拦截使用，从不持久化、不产生金额。
    round_unpriced_purchase_intents: list[dict] = field(default_factory=list)
    # 当前判定阶段是否已生成结构化检定；幸运选择必须发生在 LLM 叙事之前。
    round_checks_prepared: bool = False
    # 进入判定阶段前的玩家状态；整轮撤回时用于退还本轮消耗的幸运。
    round_start_snapshot: PlayerRollbackSnapshot = field(default_factory=dict)
    # 进入判定阶段前的旧版战斗实体状态（npcs / combat_enemies / 战斗状态）。
    # 旧版 hp_based 路径（CombatResolver）直接改写这些记录、且不经过
    # combat_extension 的快照机制；判定失败回滚必须能把它们一起还原，
    # 否则"没讲成的回合"会留下伤害。D&D2024 权威战斗另有
    # combat_extension_round_snapshots。见 capture_round_entity_snapshot。
    round_entity_snapshot: dict[str, Any] = field(default_factory=dict)
    # Death-save outcomes are keyed by round and player UID so narrative/API
    # retries reuse the same roll without leaking it into future rounds.
    death_save_outcomes: dict[str, dict[str, dict]] = field(default_factory=dict)

    # GM 私密指令：只注入 GM 上下文，不作为玩家/系统行动公开记录
    gm_directives: list[dict] = field(default_factory=list)

    # 状态变化 recap：最近一回合的 state_update（前端渲染用）
    last_state_update: dict | None = None

    # 本轮裁判标注的越权声明（仅多人局且开关启用时注入 GM 上下文）
    last_overreach: list = field(default_factory=list)

    # 本轮由 server 判定的行动合法性矛盾（Issue #284）：每条含
    # player / code / location / current，供可信裁定块与前端提示使用。
    # 与 last_overreach 分开：越权是玩家替世界/他人声明事实，合法性是玩家
    # 自己的动作与权威世界真相矛盾。
    last_world_legality: list = field(default_factory=list)

    # 本轮逻辑时间推进后确定性结算的定时事件（Issue #284 / WP6）：每条形如
    # {"event_id", "label", "due_at", "status": "applied"|"failed", "error"?}，
    # 供 GM 可信块叙述与前端提示使用。
    last_world_events: list = field(default_factory=list)

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

    # 当前对局 GM 叙事风格覆盖：None=跟随世界 gm_style；dict=显式覆盖
    # （全缺省 dict 也是合法的"恢复中性风格"，与 None 语义严格区分）。
    gm_style_override: dict[str, str] | None = None

    # 叙事语言
    language: str = DEFAULT_LANGUAGE  # "zh-CN" / "en"

    # 入口模式
    entry_point: str = "web"  # "web" / "plugin"

    # 战斗结算缓存（供 WebUI 展示）
    pending_combat_results: list[dict] = field(default_factory=list)

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
    # 奖励自动结算策略（表级偏好，重开保留）：{"mode": "auto_small_cash"|"gm_confirm",
    # "auto_reward_cap": int}。{} 表示未设置，结算时回退规则模板 economy_defaults
    # 与服务器全局配置；归属见 economy.resolve_auto_reward_policy。
    economy_reward_policy: dict = field(default_factory=dict)
    # 内部：每条 pending 幸运检定的超时定时器（check_id -> asyncio.Task），不序列化
    _luck_timers: dict = field(default_factory=dict, repr=False)
    # 内部：正在处理本轮的 task（仅判定期间有值，不序列化）。GM 明确要求强制推进
    # 时用它中止在飞生成；`_process_lock` 本身拿不到持有者。
    _process_task: asyncio.Task[Any] | None = field(default=None, repr=False, compare=False)
    # 内部：本次取消是否来自显式的抢占请求（区分关服/断连等外部取消）。
    _preempt_requested: bool = field(default=False, repr=False, compare=False)
    # 恢复后是否仍有待幸运决定的检定（recover_all 设置，供前端提示；定时器不跨重启）
    pending_luck_after_recovery: bool = False
    _tag_fail_streak: int = field(default=0, repr=False)
    # D1: 已确认事项（CONFIRMED 标签累积），注入 LLM 上下文防重复讨论
    confirmed_items: list = field(default_factory=list)

    @property
    def scene_image(self) -> dict[str, str]:
        return media.scene_image(self)

    @scene_image.setter
    def scene_image(self, value: Any) -> None:
        media.replace_scene_image(self, value)

    @property
    def map_background(self) -> dict[str, str]:
        return media.map_background(self)

    @map_background.setter
    def map_background(self, value: Any) -> None:
        media.replace_map_background(self, value)

    @property
    def private_log(self) -> dict[str, list[dict[str, Any]]]:
        return private_channels.private_log(self)

    @private_log.setter
    def private_log(self, value: Any) -> None:
        private_channels.replace_private_log(self, value)

    @property
    def table_talk(self) -> list[TableTalkExchange]:
        return private_channels.table_talk(self)

    @table_talk.setter
    def table_talk(self, value: Any) -> None:
        private_channels.replace_table_talk(self, value)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = f"run_{uuid4().hex}"
        if not self.memory_namespace:
            self.memory_namespace = f"{self.game_key!s}::run:{self.run_id}"
        # 世界真相容器：未设置/损坏 → 空世界；未知 schema 原样保留，由写入路径
        # 明确拒绝，绝不把用户数据猜成默认值。
        self.world_state = ensure_world_state(self.world_state)
        self.modules = ensure_module_states(self.modules)
        economy_state.ensure_run_id(self)
        # 每个席位都带一个显式控制器（human / ai / unclaimed）：旧存档、内存构造
        # 与手工修改过的 players 都在这里补齐，读取方永远不必自己猜。唯一写入口
        # 仍是 src.engine.player_control.set_control。
        ensure_controls(self)

    @property
    def round_number(self) -> int:
        return progression_state.round_value(self)

    @round_number.setter
    def round_number(self, value: int) -> None:
        progression_state.set_round_value(self, value)

    @property
    def economy(self) -> dict[str, Any]:
        return economy_state.state(self)

    @economy.setter
    def economy(self, value: Any) -> None:
        economy_state.replace_state(self, value)

    @property
    def combat_extension(self) -> dict[str, Any]:
        """Live opaque combat state; an empty dict means disabled."""
        return combat_extension_state.current(self)

    @combat_extension.setter
    def combat_extension(self, value: Any) -> None:
        combat_extension_state.replace_current(self, value)

    @property
    def combat_extension_round_snapshots(self) -> dict[str, Any]:
        """Live snapshots captured before each round's first combat write."""
        return combat_extension_state.round_snapshots(self)

    @combat_extension_round_snapshots.setter
    def combat_extension_round_snapshots(self, value: Any) -> None:
        combat_extension_state.replace_round_snapshots(self, value)

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

        # Reject unsupported slots before changing either run identity.
        economy_state.state(self)
        old = self.run_id
        self.run_id = f"run_{uuid4().hex}"
        self.memory_namespace = f"{self.game_key!s}::run:{self.run_id}"
        self.economy = economy_state.fresh_economy_state(self.run_id)
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

    @property
    def active_human_players(self) -> set[str]:
        """当前需要等待行动的真人席位：存活、未暂离且控制模式为 ``human``。

        AI 托管与未认领的席位由服务器或无人负责，不能阻塞多人的 ready
        barrier；``active_alive_players`` 仍是"在场存活"的完整集合，供幸运
        超时等只看人数的地方使用。
        """
        return {
            uid for uid in self.active_alive_players
            if control_mode(self, uid) == "human"
        }

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
        # 席位写入即带控制器：新建角色、规则快照恢复等所有路径都经过这里，因此
        # 内存中的名册与落盘后的名册不会出现"有的席位没有 control"的差别。
        ensure_control(player)
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
        economy_reward_policy: dict | None = None,
    ) -> None:
        """集中更新入口与房间身份配置，保留旧存档字段。

        luck_timeout_seconds：每玩家幸运超时秒数（0=禁用，异步局建议 0）。
        economy_reward_policy：本局奖励自动结算策略；非法载荷整体拒绝。
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
        if economy_reward_policy is not None:
            from src.engine.economy import normalize_reward_policy

            if not isinstance(economy_reward_policy, dict):
                raise ValueError("奖励策略无效")
            if not str(economy_reward_policy.get("mode") or "").strip():
                # 显式清空：回退规则模板默认与服务器全局配置。
                self.economy_reward_policy = {}
            else:
                normalized = normalize_reward_policy(economy_reward_policy)
                if not normalized:
                    raise ValueError("奖励策略无效（mode 或 auto_reward_cap 不合法）")
                self.economy_reward_policy = normalized

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
        """Bind one immutable adventure package, or explicitly select sandbox.

        FIX-02 §4.2：绑定新增可选的来源身份 ``source_kind`` / ``source_id``，
        让"同一个 adventure_id 存在于多个来源"时能明确解析。旧存档的 5 字段绑定
        （无来源身份）继续合法，按"当前唯一"解析；两者必须成对出现，混合形状
        一律拒绝（不猜）。读取旧绑定时不改写存档：来源身份只在**新绑定**里落盘。
        """

        value = dict(binding or {})
        if value:
            required = {"adventure_id", "version", "format", "content_digest", "world_id"}
            allowed = required | {"source_kind", "source_id"}
            if not required.issubset(value) or set(value) - allowed:
                return False
            if not all(str(value.get(key) or "") for key in required):
                return False
            has_kind = "source_kind" in value
            has_id = "source_id" in value
            if has_kind != has_id:
                return False
            if has_kind and not str(value.get("source_kind") or "").strip():
                return False
            if str(value["world_id"]) != str(self.world_id or ""):
                return False
        if self.adventure_binding and not _same_adventure_binding(
            self.adventure_binding, value,
        ):
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
        for player in players.values():
            ensure_control(player)
        self.players = players

    def restore_ruleset_transaction(self, snapshot: dict[str, Any]) -> None:
        """Restore the bounded state touched by an authoritative ruleset transaction.

        Callers capture the snapshot before invoking a runtime reducer. Keeping
        the rollback assignment here preserves the aggregate write boundary
        while allowing ruleset orchestration to remain transaction-aware.
        """
        if "round_number" in snapshot:
            progression.require_writable(self)
            restored_round = int(snapshot["round_number"])
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
            progression.restore_from_snapshot(self, restored_round)

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

    def set_gm_style_override(self, raw: Any) -> None:
        """设置当前对局 GM 叙事风格覆盖。

        None=跟随世界 gm_style；dict=显式覆盖（normalize 后保存，全缺省 dict
        是合法的"恢复中性风格"）；其它类型拒绝，绝不静默解释成跟随世界。
        """

        from src.content.gm_style import normalize_gm_style_override

        self.gm_style_override = normalize_gm_style_override(raw)

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
        self.last_world_legality.clear()
        self.last_world_events.clear()
        self.round_unpriced_purchase_intents.clear()
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
        progression.require_writable(self)
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

    def capture_combat_extension_snapshot(
        self,
        entity_fields: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        """Capture state and source fields before this round's combat writes."""
        round_snapshots.capture_combat_extension_snapshot(self, entity_fields)

    def current_combat_extension_snapshot(self) -> dict[str, Any]:
        """Return the current combat state using this round's tracked fields."""
        return round_snapshots.current_combat_extension_snapshot(self)

    def restore_combat_extension_snapshot(self, snapshot: Any) -> bool:
        """Restore a snapshot produced by the combat-extension snapshot API."""
        return round_snapshots.restore_combat_extension_snapshot(self, snapshot)

    def discard_combat_extension_snapshots_from(self, round_number: int) -> None:
        """Drop live snapshots belonging to a discarded history branch."""
        round_snapshots.discard_combat_extension_snapshots_from(self, round_number)

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
        """恢复到上一轮开始前；返回恢复后的轮次，没有日志时返回 None。

        这是"回滚一个已经完成的历史回合"（historical rollback），与判定失败的
        :meth:`abort_round_processing` 是两个不同 contract。实现见
        ``round_recovery.rollback_last_round_locked``。
        """
        async with self._lock:
            if self.log:
                # Reject before history or snapshots can be changed.
                progression.require_writable(self)
                economy_state.state(self)
                combat_extension_state.current(self)
            return round_recovery.rollback_last_round_locked(self)

    async def abort_round_processing(self) -> bool:
        """判定阶段处理失败：回退到行动阶段，保留行动队列等待重试。

        与 rollback_last_round（回滚已完成回合）不同：经济、日志、round_number
        均未落定，不做改动；仅撤销本轮判定入口到叙事生成失败之间的状态变更。
        行动上已掷的骰值保留（骰值只在缺失时重掷），因此重试时骰值稳定；
        check_request 会由检定规划按行动内容重新生成，不保证与失败那次完全一致。

        行动里的战斗结算缓存（combat_outcome）不是"输入"而是失败那一轮的
        产物：血量已经被玩家快照 / 战斗扩展快照 / 实体快照恢复的实体，其缓存
        必须丢弃，否则重试会命中 ``CombatResolver`` 的缓存重放分支——既不重掷
        命中骰也不重新扣血——使结算记录（伤害 5）与实际 HP（恢复后的满血）互相
        矛盾；无法核对血量的旧存档则保留缓存，避免重试重复扣血。判定见
        ``_drop_stale_combat_caches``。
        """
        async with self._lock:
            if self.state == GameState.ACTIVE_JUDGMENT:
                progression.require_writable(self)
                economy_state.state(self)
                combat_extension_state.current(self)
            return round_recovery.abort_round_processing_locked(self)

    def _drop_stale_combat_caches(self, *, all_targets: bool = False) -> None:
        """丢弃"状态已回滚、缓存却仍记录伤害"的战斗结算缓存（逻辑见 round_snapshots）。"""
        round_snapshots.drop_stale_combat_caches(self, all_targets=all_targets)

    def _entity_hp(self, target_ref: str) -> int | None:
        """按战斗目标引用取当前血量；未知目标返回 None（逻辑见 round_snapshots）。"""
        return round_snapshots.entity_hp(self, target_ref)

    @asynccontextmanager
    async def track_round_processing(self) -> AsyncIterator[None]:
        """登记本轮处理 task，供 GM 抢占时定位并取消。

        与 ``_process_lock`` 配合使用：先拿锁再登记，退出时先注销。锁只能表达
        "被占用"，拿不到持有者，所以抢占需要这个显式引用。
        """
        task = asyncio.current_task()
        self._process_task = task
        try:
            yield
        finally:
            if self._process_task is task:
                self._process_task = None

    def round_processing_in_flight(self) -> bool:
        """本轮是否真的有在飞的处理 task（区别于"锁被占用"）。"""
        task = self._process_task
        return task is not None and not task.done()

    def consume_preempt_request(self) -> bool:
        """读取并清除抢占标记；供被取消的处理边界决定退出语义。"""
        requested = self._preempt_requested
        self._preempt_requested = False
        return requested

    async def cancel_round_processing(self, *, timeout: float = 10.0) -> bool:
        """中止在飞的本轮处理，等它退出（含失败回滚）后返回是否成功。

        只给"GM 明确强制推进"这类需要抢占的入口使用：取消会让 ``process_round``
        把对局退回行动阶段并把取消转成结构化结果，不会留下半截状态。等 task 真正
        退出再返回，调用方才能安全地重新推进（否则立刻又撞上 ``_process_lock``）。
        超时说明处理没有在期限内退出，此时不保证已回滚，调用方按"仍在处理中"处理。
        """
        task = self._process_task
        if task is None or task.done() or task is asyncio.current_task():
            return False
        self._preempt_requested = True
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if task not in done:
            logger.warning(
                "中止在飞回合处理超时: game=%s timeout=%ss", self.game_key, timeout,
            )
            return False
        # 认领异常，避免 "Task exception was never retrieved"；被取消时本方法
        # 自身会抛 CancelledError，属预期结果。
        try:
            task.exception()
        except asyncio.CancelledError:
            pass
        # 处理边界正常消费过标记时这里是空操作；若取消落在边界之外（刚好卡在
        # 处理返回与 task 结束之间），标记会残留并把下一次取消误判成抢占，清掉。
        self.consume_preempt_request()
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

    def pending_luck_checks(self, user_id: str = "") -> list[dict]:
        """返回当前等待玩家决定是否消耗幸运的检定。"""
        return [
            dict(check)
            for check in self.last_checks
            if check.get("luck_decision") == "pending"
            and (not user_id or str(check.get("actor_uid") or "") == user_id)
        ]

    def effective_luck_timeout_seconds(self) -> int:
        """Return the timeout used by the runtime for pending Luck decisions.

        Keep an explicitly configured timeout intact.  The historical default
        of 60 seconds is lengthened for a live multiplayer table so one
        player's pending choice is not treated as an early failure while the
        rest of the party is still deciding.  A value of zero continues to
        disable the timer for asynchronous games.
        """
        timeout = int(self.luck_timeout_seconds or 0)
        if timeout <= 0:
            return 0
        if timeout == 60 and not self.solo_mode and len(self.active_alive_players) > 1:
            return 180
        return timeout

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
        """多人模式下，所有未暂离的存活真人席位都提交行动后才自动推进（语义见 turn_state）。"""
        return turn_state.all_alive_ready(self)

    def human_actions_ready(self) -> bool:
        """真人一侧是否已经交齐，可以轮到服务器 AI 补行动（语义见 turn_state）。"""
        return turn_state.human_actions_ready(self)

    def multiplayer_status(self) -> dict:
        """返回多人协调所需的轻量状态（实现见 turn_state）。"""
        return turn_state.multiplayer_status(self)

    # ---------- 回合推进 ------------------------------------

    def should_advance(self) -> bool:
        """任一满足即推进：所有存活玩家已就绪，或单人模式下任一玩家已行动。"""
        return turn_state.should_advance(self)

    async def start_round(self, *, expected_run_id: str = "") -> None:
        """开启新一轮行动阶段；可选 run fence 在状态锁内检查。"""
        async with self._lock:
            if expected_run_id and check_run_unchanged(
                self, GateRequest(actor_uid="", source=SOURCE_HUMAN, expected_run_id=expected_run_id),
            ):
                return
            progression.require_writable(self)
            economy_state.state(self)
            turn_state.start_round_locked(self)

    async def add_action(self, user_id: str, action_text: str,
                         selected_attribute: str = "", selected_skill: str = "",
                         target_text: str = "", source: str = "",
                         dice_pending: bool = False, dice_system: str = "",
                         check_request: dict | None = None,
                         count_revision: bool = True,
                         action_metadata: dict | None = None,
                         defer_out_of_phase: bool = True,
                         expected_run_id: str = "") -> bool:
        """玩家声明行动。判决阶段中的发言缓存到下一轮。

        selected_attribute/selected_skill/target_text 为前端可选提交的结构化
        归因字段（P1），供检定与 prompt 直接使用，避免靠文本启发式猜。

        ``action_metadata`` 是调用方自带的机器可读标记（例如服务器 AI 行动的
        ``source`` / ``control_revision`` / ``generated_for_round``），只用于去重
        与调试，不参与任何裁定；``defer_out_of_phase=False`` 表示这条行动带有
        轮次身份，判定阶段只能拒绝，不得缓存进下一轮。非空
        ``expected_run_id`` 在两层锁内复核；不匹配返回 False，不修改队列。
        """
        async with self.authoritative_write() as write_entered, self._lock:
            if not write_entered or self._process_lock.locked():
                return False
            if expected_run_id and check_run_unchanged(
                self, GateRequest(actor_uid=user_id, source=SOURCE_HUMAN, expected_run_id=expected_run_id),
            ):
                return False
            return self._add_action_locked(
                user_id, action_text,
                selected_attribute=selected_attribute,
                selected_skill=selected_skill,
                target_text=target_text,
                source=source,
                dice_pending=dice_pending,
                dice_system=dice_system,
                check_request=check_request,
                count_revision=count_revision,
                action_metadata=action_metadata,
                defer_out_of_phase=defer_out_of_phase,
            )

    def _add_action_locked(self, user_id: str, action_text: str, *,
                           selected_attribute: str = "", selected_skill: str = "",
                           target_text: str = "", source: str = "",
                           dice_pending: bool = False, dice_system: str = "",
                           check_request: dict | None = None,
                           count_revision: bool = True,
                           action_metadata: dict | None = None,
                           defer_out_of_phase: bool = True) -> bool:
        """``add_action`` 的持锁实现（调用方必须已持有写权限与 ``_lock``）。

        单独抽出来是为了让需要「先复核再写入」的调用方能在**同一个** boundary
        内完成两件事：自己在锁内复核，再调用这里追加，而不是写两层加锁。
        实现见 ``turn_state.add_action_locked``。
        """
        progression.require_writable(self)
        return turn_state.add_action_locked(
            self, user_id, action_text,
            selected_attribute=selected_attribute,
            selected_skill=selected_skill,
            target_text=target_text,
            source=source,
            dice_pending=dice_pending,
            dice_system=dice_system,
            check_request=check_request,
            count_revision=count_revision,
            action_metadata=action_metadata,
            defer_out_of_phase=defer_out_of_phase,
        )

    def has_action_from_source(self, user_id: str, round_number: int, source: str) -> bool:
        """这一轮该席位是否已有一条来自 ``source`` 的行动（持锁与只读都安全）。"""
        return turn_state.has_action_from_source(self, user_id, round_number, source)

    async def commit_ai_player_action(
        self,
        user_id: str,
        action_text: str,
        *,
        source: str,
        expected_run_id: str,
        expected_round_number: int,
        expected_control_revision: int,
        action_metadata: dict | None = None,
        requires_structured_intent: StructuredIntentRequirement = False,
    ) -> str:
        """Atomically re-confirm a hosted seat and commit its action.

        The LLM call deliberately happens *outside* every lock; the danger is that
        the world moves between "the model answered" and "we write the answer".
        So the re-confirmation and the write are one boundary: this method takes
        the authoritative write permission and ``_lock`` once, re-checks every
        identity the caller captured (run, round, seat, control mode, control
        revision, phase), re-checks that the **human gate is still open**, that
        this round has no action from ``source`` yet, and only then appends.

        The human gate matters on its own: a hosted seat only ever acts *after*
        the humans are done. If somebody claims another seat (or a player returns
        from away) while this result was in flight, the table suddenly has an
        active human who has not submitted yet, and an action written now would
        break "AI acts after the humans" — so it is discarded instead.

        Returns ``""`` on commit, otherwise the reason the result was dropped:
        ``rejected`` / ``run_changed`` / ``round_changed`` / ``seat_removed`` /
        ``control_changed`` / ``phase_changed`` / ``human_gate_changed`` /
        ``duplicate`` / ``STRUCTURED_INTENT_REQUIRED`` / ``action_rejected``.
        A supplied structured-intent predicate reads current admission state here,
        synchronously under both locks, after the existing rejection checks.
        Callers must treat a non-empty
        result as "write nothing"; the action never lands.
        """
        async with self.authoritative_write() as write_entered, self._lock:
            if not write_entered or self._process_lock.locked():
                # 判定/重写正在进行：这一轮不再接受带轮次身份的行动。
                return "rejected"
            stale = self.ai_player_action_stale_reason(
                user_id,
                expected_run_id=expected_run_id,
                expected_round_number=expected_round_number,
                expected_control_revision=expected_control_revision,
            )
            if stale:
                return stale
            code = evaluate(
                self,
                GateRequest(
                    actor_uid=user_id,
                    source=SOURCE_AI_SEAT,
                    expected_run_id=expected_run_id,
                    expected_round_number=expected_round_number,
                    expected_control_revision=expected_control_revision,
                    action_source=source,
                    requires_structured_intent=requires_structured_intent,
                ),
                AI_SEAT_COMMIT_POLICY,
            )
            if code:
                return code
            added = self._add_action_locked(
                user_id, action_text,
                source=source,
                action_metadata=action_metadata,
                defer_out_of_phase=False,
            )
            return "" if added else "action_rejected"

    def ai_player_action_stale_reason(
        self,
        user_id: str,
        *,
        expected_run_id: str,
        expected_round_number: int,
        expected_control_revision: int,
    ) -> str:
        """Why an in-flight hosted-seat result must be discarded, else ``""``（实现见 turn_state）。"""
        return turn_state.ai_player_action_stale_reason(
            self,
            user_id,
            expected_run_id=expected_run_id,
            expected_round_number=expected_round_number,
            expected_control_revision=expected_control_revision,
        )

    def has_pending_dice(self, user_id: str | None = None) -> bool:
        return turn_state.has_pending_dice(self, user_id)

    def pending_dice_actions(self, user_id: str | None = None) -> list[dict]:
        return turn_state.pending_dice_actions(self, user_id)

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
            return turn_state.apply_action_roll_locked(
                self, user_id, dice_system, value, rolls=rolls, source=source,
            )

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
            return self._set_player_away_locked(user_id, away)

    def _set_player_away_locked(self, user_id: str, away: bool) -> bool:
        """``set_player_away`` 的持锁实现（调用方必须已持有 ``_lock``；实现见 turn_state）。

        单独抽出来是为了让「暂离」和它可能触发的控制权转换能在**同一个**
        transaction 内完成：``self._lock`` 不可重入，所以调用方不能在持锁时再调
        :meth:`set_player_away`。
        """
        return turn_state.set_player_away_locked(self, user_id, away)

    async def apply_away_transition(self, user_id: str, away: bool, *, policy: str) -> str:
        """暂离/回来：安全边界复核 + 在场状态 + 控制权转换，同一个 transaction。

        "暂离"和"把角色交给 AI"必须是**一次**可观察的状态变更：如果先写
        ``away_players`` 再另起一个事务去改控制权，中间就会存在
        ``away=true`` + ``control=human`` 的窗口，而那个组合恰恰是「玩家已经离开、
        但 AI 没有接手」——一个谁都不负责的席位。

        ``policy`` 为 ``pause`` 时只改在场状态（旧语义，不做安全边界检查）；
        ``ai_takeover`` 的托管与"回来"的归还都要求安全边界，否则返回
        ``CONTROL_CHANGE_BUSY``。成功返回 ``""``。
        """
        async with self.authoritative_write() as write_entered, self._lock:
            if not write_entered:
                return "CONTROL_CHANGE_BUSY"
            handover = away and policy == "ai_takeover"
            if handover or not away:
                block = control_change_block(self)
                if block:
                    return block
            if not self._set_player_away_locked(user_id, away):
                return "UNKNOWN_PLAYER"
            if handover:
                begin_away_hosting(self, user_id)
            elif not away:
                # 只在席位确实是「临时托管」时归还；GM 永久交给 AI 的席位不会被抢回。
                end_away_hosting(self, user_id)
            return ""

    async def apply_player_control_change(self, user_id: str, mode: str) -> str:
        """GM 托管控件：安全边界复核 + 控制权变更，同一个 transaction。

        返回 ``""`` 表示已切换；否则 ``CONTROL_CHANGE_BUSY`` / ``UNKNOWN_PLAYER``
        / ``CONTROL_REJECTED``。复核与实际写入之间没有任何 ``await``，因此回合处理
        一旦开始占用边界，控制权转换就不可能插进去。
        """
        async with self.authoritative_write() as write_entered, self._lock:
            if not write_entered or self._process_lock.locked():
                return "CONTROL_CHANGE_BUSY"
            block = control_change_block(self)
            if block:
                return block
            if user_id not in self.players:
                return "UNKNOWN_PLAYER"
            try:
                set_control(self, user_id, mode)
            except PlayerControlError:
                logger.warning(
                    "拒绝托管切换: game_key=%s uid=%s mode=%s",
                    self.game_key, user_id, mode, exc_info=True,
                )
                return "CONTROL_REJECTED"
            return ""

    async def advance_round(self) -> bool:
        """显式推进回合。未行动的存活玩家标记为已就绪。"""
        async with self._lock:
            from src.engine.economy import has_blocking_economy_decision

            progression.require_writable(self)
            if has_blocking_economy_decision(self):
                return False
            return self._do_advance_locked()

    async def try_advance(self) -> bool:
        """原子推进：检查条件 + 推进在同一个锁内完成，消除 TOCTOU 竞态。"""
        async with self._lock:
            from src.engine.economy import has_blocking_economy_decision

            progression.require_writable(self)
            if has_blocking_economy_decision(self):
                return False
            if self.state != GameState.ACTIVE_ACTION:
                return False
            if not self.should_advance():
                return False
            return self._do_advance_locked()

    def _do_advance_locked(self) -> bool:
        """在锁内执行推进（调用方需持锁；实现见 turn_state）。"""
        progression.require_writable(self)
        return turn_state.do_advance_locked(self)

    def capture_round_entity_snapshot(self) -> None:
        """判定入口快照旧版战斗实体与战斗状态（实现见 round_snapshots）。"""
        round_snapshots.capture_round_entity_snapshot(self)

    def restore_round_entity_snapshot(self) -> bool:
        """还原判定入口的旧版实体快照；没有快照时返回 False（实现见 round_snapshots）。"""
        return round_snapshots.restore_round_entity_snapshot(self)

    async def finish_judgment(
        self,
        gm_response: str,
        pre_state_snapshot: dict | None = None,
        state_changes: list[str] | None = None,
        pre_combat_extension_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """判定完成，记录本轮并开启下一轮。

        pre_state_snapshot 应为 _apply_state_update 之前拍摄的快照，
        确保 swipe 重生成时恢复到本轮初始状态而非应用后状态。
        state_changes 为本轮玩家可见状态变动摘要，随 log entry 持久化供群机器人单独转发。
        日志提交与下一轮开启共用状态锁，中间不释放锁或 await。
        """
        async with self._lock:
            progression.require_writable(self)
            economy_state.state(self)
            round_recovery.finish_judgment_locked(
                self,
                gm_response,
                pre_state_snapshot=pre_state_snapshot,
                state_changes=state_changes,
                pre_combat_extension_snapshot=pre_combat_extension_snapshot,
            )
            turn_state.start_round_locked(self)

    async def finish_judgment_with_swipe(
        self,
        gm_response: str,
        original_round: int,
        state_changes: list[str] | None = None,
    ) -> None:
        """为已有轮次添加 swipe（不推进回合）。"""
        async with self._lock:
            economy_state.state(self)
            round_recovery.finish_judgment_with_swipe_locked(
                self, gm_response, original_round, state_changes=state_changes,
            )

    async def switch_swipe(self, round_num: int, swipe_idx: int) -> bool:
        """切换指定轮次的 swipe 展示（实现见 round_recovery；并发语义与基线一致）。"""
        return round_recovery.switch_swipe(self, round_num, swipe_idx)

    # ---------- 状态转换 ------------------------------------
    # 持锁 mutation detail 见 src/engine/instance_lifecycle.py。

    async def activate(self) -> None:
        async with self._lock:
            progression.require_writable(self)
            instance_lifecycle.activate_locked(self)

    async def pause(self) -> None:
        async with self._lock:
            instance_lifecycle.pause_locked(self)

    async def resume(self, *, expected_run_id: str = "") -> None:
        async with self._lock:
            if expected_run_id and check_run_unchanged(
                self, GateRequest(actor_uid="", source=SOURCE_HUMAN, expected_run_id=expected_run_id),
            ):
                return
            progression.require_writable(self)
            instance_lifecycle.resume_locked(self)

    async def end(self) -> None:
        async with self._lock:
            instance_lifecycle.end_locked(self)

    async def reset(self, keep_seed: bool = True) -> None:
        """重开一局：保留配置身份，轮换 run identity 并清空运行时状态。

        真实契约由 ``tests/test_game_instance_reset_characterization.py`` 冻结；
        实现是基线 reset() 的机械迁移（见 ``instance_lifecycle.reset_locked``）。
        """
        async with self._lock:
            # Validate fallible slots before rotating the run or clearing state.
            progression.require_writable(self)
            combat_extension_state.current(self)
            lorebook_runtime.timers(self)
            instance_lifecycle.reset_locked(self, keep_seed=keep_seed)

    # ---------- 序列化 --------------------------------------

    @property
    def away_control_policy(self) -> str:
        return player_control_state.away_control_policy(self)

    @away_control_policy.setter
    def away_control_policy(self, value: Any) -> None:
        player_control_state.set_away_control_policy_value(self, normalize_away_control_policy(value))

    @property
    def lorebook_timed_state(self) -> dict[str, dict]:
        return lorebook_runtime.timers(self)

    @lorebook_timed_state.setter
    def lorebook_timed_state(self, value: Any) -> None:
        lorebook_runtime.replace_timers(self, value)

    def update_lorebook_timed_state(self) -> None:
        """Tick persisted Lorebook timers, supporting the pre-v2 shape.

        The tick *timing* stays here (this is the authoritative turn boundary);
        the lifecycle *semantics* live in :mod:`src.lorebook.activation` so
        ``sticky → cooldown`` ordering is testable without a GameInstance.
        """
        expired: list[str] = []
        for entry_id, state in self.lorebook_timed_state.items():
            if not isinstance(state, dict):
                expired.append(entry_id)
                continue
            if any(key in state for key in TIMED_COUNTER_KEYS):
                if advance_timed_state(state):
                    expired.append(entry_id)
                continue
            remaining = max(0, int(state.get("remaining", 0) or 0) - 1)
            if remaining <= 0:
                expired.append(entry_id)
            else:
                state["remaining"] = remaining
        for entry_id in expired:
            self.lorebook_timed_state.pop(entry_id, None)

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
            # 回合内的无价购买意图是 process-local 协调状态：不入持久化投影，
            # 生成期间的合法结算回写不得清空它（否则同轮 LOOT 拦截会失效）。
            "round_unpriced_purchase_intents",
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
