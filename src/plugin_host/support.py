"""Truthful support levels for DiceFrame plugin types."""

from __future__ import annotations

from typing import Any

PLUGIN_TYPE_SUPPORT: dict[str, dict[str, str]] = {
    "channel-adapter": {
        "level": "supported",
        "summary": "可作为独立进程连接聊天平台并调用 DiceFrame HTTP API",
    },
    "content-pack": {
        "level": "supported",
        "summary": "可注册规则、世界、角色、NPC、道具、法术和职业内容",
    },
    "theme": {
        "level": "supported",
        "summary": "可注册安全的主题颜色变量",
    },
    "map-pack": {
        "level": "partial",
        "summary": "可注册地点和地图素材，暂不包含实时战棋与地图编辑器",
    },
    "import-export": {
        "level": "reserved",
        "summary": "仅保留清单类型，尚未接入统一导入导出流程",
    },
    "provider": {
        "level": "reserved",
        "summary": "仅保留清单类型，尚未接入模型 Provider 运行时",
    },
    "tool": {
        "level": "supported",
        "summary": "可通过受限 JSON-RPC 协议注册并执行结构化工具",
    },
}


def plugin_type_support(plugin_type: str) -> dict[str, Any]:
    support = PLUGIN_TYPE_SUPPORT.get(plugin_type)
    if support:
        return dict(support)
    return {"level": "unsupported", "summary": "DiceFrame 不识别此插件类型"}
