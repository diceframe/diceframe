"""Shared command prefix and trigger policy helpers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TriggerConfig:
    prefixes: tuple[str, ...] = ("/df", "/diceframe", "跑团")
    mode: str = "prefix_only"
    bare_commands: frozenset[str] = field(default_factory=lambda: frozenset({
        "帮助", "help", "?", "？", "绑定", "bind", "解绑", "unbind", "加入", "join",
        "邀请", "invite", "新建角色", "车卡", "AI车卡", "ai车卡", "前情", "recap",
        "地图", "map", "状态", "status", "感知", "sense", "log", "支付", "pay",
        "确认支付", "拒绝支付", "rejectpay", "掷骰", "roll", "推进", "下一轮",
        "advance", "next", "暂离", "away", "回来", "return", "back", "行动", "做",
        "连接测试", "ping",
    }))


def has_explicit_prefix(text: str, prefixes: tuple[str, ...] | list[str]) -> bool:
    value = str(text or "").strip()
    for prefix in sorted({str(item).strip() for item in prefixes if str(item).strip()}, key=len, reverse=True):
        if value == prefix:
            return True
        if value.startswith(prefix) and (len(value) == len(prefix) or value[len(prefix)].isspace()):
            return True
    return False


def strip_prefix(text: str, prefixes: tuple[str, ...] | list[str]) -> str:
    value = str(text or "").strip()
    for prefix in sorted({str(item).strip() for item in prefixes if str(item).strip()}, key=len, reverse=True):
        if value == prefix:
            return ""
        if value.startswith(prefix) and (len(value) == len(prefix) or value[len(prefix)].isspace()):
            return value[len(prefix):].strip()
    return value


def should_trigger(text: str, *, mentioned_bot: bool, config: TriggerConfig | None = None) -> bool:
    config = config or TriggerConfig()
    value = str(text or "").strip()
    if has_explicit_prefix(value, config.prefixes):
        return True
    first = value.split(maxsplit=1)[0] if value else ""
    if config.mode == "prefix_only":
        return False
    if config.mode == "mention_bare":
        return mentioned_bot and first in config.bare_commands
    if config.mode == "prefix_or_mention":
        return mentioned_bot
    if config.mode == "bare":
        return first in config.bare_commands
    return False
