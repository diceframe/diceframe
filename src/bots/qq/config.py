"""群聊 Bot 环境变量配置。"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QQBotConfig:
    napcat_host: str
    napcat_port: int
    napcat_token: str
    heartbeat_sec: float
    reconnect_delay_sec: float
    action_timeout_sec: float
    reply_delay_min_sec: float
    reply_delay_max_sec: float
    command_dedup_window_sec: float
    card_cache_max_age_hours: float
    card_cache_max_files: int
    card_cache_cleanup_interval_sec: float
    web_sync_interval_sec: float
    link_reminder_enabled: bool
    ai_character_creation_enabled: bool
    connection_id: str
    chat_filter_enabled: bool
    show_dropped_logs: bool
    group_list_mode: str
    group_list: tuple[str, ...]
    private_list_mode: str
    private_list: tuple[str, ...]
    blocked_users: tuple[str, ...]
    block_official_bots: bool
    advance_allowed_users: tuple[str, ...]
    trpg_api_base: str
    bot_token: str
    data_path: Path
    parent_pid: int

    @property
    def ws_url(self) -> str:
        return f"ws://{self.napcat_host}:{self.napcat_port}"

    @classmethod
    def from_env(cls) -> "QQBotConfig":
        root = Path(__file__).resolve().parents[3]
        return cls(
            napcat_host=os.getenv("NAPCAT_HOST", "127.0.0.1"),
            napcat_port=int(os.getenv("NAPCAT_PORT", "3001")),
            napcat_token=os.getenv("NAPCAT_TOKEN", ""),
            heartbeat_sec=float(os.getenv("NAPCAT_HEARTBEAT_SEC", "30")),
            reconnect_delay_sec=float(os.getenv("NAPCAT_RECONNECT_DELAY_SEC", "5")),
            action_timeout_sec=float(os.getenv("NAPCAT_ACTION_TIMEOUT_SEC", "15")),
            reply_delay_min_sec=float(os.getenv("NAPCAT_REPLY_DELAY_MIN_SEC", "0.8")),
            reply_delay_max_sec=float(os.getenv("NAPCAT_REPLY_DELAY_MAX_SEC", "2.4")),
            command_dedup_window_sec=float(os.getenv("NAPCAT_COMMAND_DEDUP_WINDOW_SEC", "6")),
            card_cache_max_age_hours=float(os.getenv("NAPCAT_CARD_CACHE_MAX_AGE_HOURS", "24")),
            card_cache_max_files=int(os.getenv("NAPCAT_CARD_CACHE_MAX_FILES", "200")),
            card_cache_cleanup_interval_sec=float(os.getenv("NAPCAT_CARD_CACHE_CLEANUP_INTERVAL_SEC", "3600")),
            web_sync_interval_sec=float(os.getenv("NAPCAT_WEB_SYNC_INTERVAL_SEC", "5")),
            link_reminder_enabled=_env_bool("NAPCAT_LINK_REMINDER_ENABLED", True),
            ai_character_creation_enabled=_env_bool("NAPCAT_AI_CHARACTER_CREATION_ENABLED", True),
            connection_id=os.getenv("NAPCAT_CONNECTION_ID", "").strip(),
            chat_filter_enabled=_env_bool("NAPCAT_CHAT_FILTER_ENABLED", False),
            show_dropped_logs=_env_bool("NAPCAT_SHOW_DROPPED_LOGS", False),
            group_list_mode=_list_mode(os.getenv("NAPCAT_GROUP_LIST_MODE", "whitelist")),
            group_list=_env_list("NAPCAT_GROUP_LIST"),
            private_list_mode=_list_mode(os.getenv("NAPCAT_PRIVATE_LIST_MODE", "whitelist")),
            private_list=_env_list("NAPCAT_PRIVATE_LIST"),
            blocked_users=_env_list("NAPCAT_BLOCKED_USERS"),
            block_official_bots=_env_bool("NAPCAT_BLOCK_OFFICIAL_BOTS", True),
            advance_allowed_users=_env_list("NAPCAT_ADVANCE_ALLOWED_USERS"),
            trpg_api_base=os.getenv("TRPG_API_BASE", "http://127.0.0.1:9876").rstrip("/"),
            bot_token=os.getenv("TRPG_BOT_TOKEN", ""),
            data_path=Path(os.getenv("TRPG_BOT_DATA", str(root / "data" / "bot" / "qq_sessions.json"))),
            parent_pid=int(os.getenv("TRPG_PARENT_PID", "0") or "0"),
        )

    def validate(self) -> None:
        if not self.bot_token:
            raise ValueError("缺少 TRPG_BOT_TOKEN，Bot 不会以未鉴权模式启动")
        if not 1 <= self.napcat_port <= 65535:
            raise ValueError("NAPCAT_PORT 必须在 1 到 65535 之间")
        for value, name in (
            (self.heartbeat_sec, "心跳间隔"),
            (self.reconnect_delay_sec, "重连等待"),
            (self.action_timeout_sec, "动作超时"),
            (self.command_dedup_window_sec, "命令去重窗口"),
        ):
            if value <= 0:
                raise ValueError(f"{name}必须大于 0")
        if self.reply_delay_min_sec < 0 or self.reply_delay_max_sec < 0:
            raise ValueError("回复随机延迟不能小于 0")
        if self.reply_delay_max_sec < self.reply_delay_min_sec:
            raise ValueError("回复随机延迟上限不能小于下限")
        if self.card_cache_max_age_hours < 0:
            raise ValueError("卡片缓存保留时长不能小于 0")
        if self.card_cache_max_files < 0:
            raise ValueError("卡片缓存最多保留张数不能小于 0")
        if self.card_cache_cleanup_interval_sec < 0:
            raise ValueError("卡片缓存定时清理间隔不能小于 0")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw.replace(",", "\n").splitlines()
    if not isinstance(value, list):
        value = []
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _list_mode(value: str) -> str:
    return "blacklist" if str(value).lower() == "blacklist" else "whitelist"
