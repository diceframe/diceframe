"""DiceFrame 插件类型 descriptor：单一来源。

每个插件类型的支持级别、运行方式、推断权限、必需权限、内容贡献映射、是否可筛选
都集中在这里。新增插件类型只改本表，宿主/策略/注册表/前端按数据驱动，不再散落硬编码。
"""

from __future__ import annotations

from typing import Any

# 运行方式
PROCESS_MODE_STATIC = "static"                # 无进程：内容包/主题
PROCESS_MODE_RPC_TOOL = "rpc-tool"            # JSON-RPC stdio 进程：工具
PROCESS_MODE_RPC_BRIDGE = "rpc-bridge"         # JSON-RPC stdio 进程：Bot Bridge 扩展
PROCESS_MODE_RPC_PROVIDER = "rpc-provider"    # JSON-RPC stdio 进程：能力 Provider（生图等）
PROCESS_MODE_PLAIN_SUBPROCESS = "plain-subprocess"  # 普通进程（非 RPC）：渠道适配器
PROCESS_MODE_NONE = "none"                     # 预留类型，无运行时

PROCESS_MODES = (
    PROCESS_MODE_STATIC,
    PROCESS_MODE_RPC_TOOL,
    PROCESS_MODE_RPC_BRIDGE,
    PROCESS_MODE_RPC_PROVIDER,
    PROCESS_MODE_PLAIN_SUBPROCESS,
    PROCESS_MODE_NONE,
)

# 内容贡献字段 -> 资源 kind 映射（原 registry.py 的 _CONTENT/_THEME/_MAP_CONTRIBUTIONS）
_CONTENT_CONTRIBUTIONS = {
    "rules": "rule",
    "world_templates": "world_template",
    "character_templates": "character_template",
    "characters": "character_template",
    "npcs": "npc",
    "npc": "npc",
    "items": "item",
    "spells": "spell",
    "classes": "class",
    "portraits": "portrait_asset",
    "scene_images": "scene_image_asset",
    "map_definitions": "map_definition",
    "map_locations": "map_location",
    "map_icons": "map_icon",
    "map_backgrounds": "map_scene",
}
_THEME_CONTRIBUTIONS = {"theme": "theme", "themes": "theme"}
_VOICE_CONTRIBUTIONS = {
    "voices": "voice_profile",
    "voice_assets": "voice_asset",
}
MAP_CONTRIBUTION_FIELDS = frozenset({
    "map_definitions",
    "map_locations",
    "map_icons",
    "map_backgrounds",
})

PLUGIN_TYPE_SUPPORT: dict[str, dict[str, Any]] = {
    "channel-adapter": {
        "level": "supported",
        "summary": "可作为独立进程连接聊天平台并调用 DiceFrame HTTP API",
        "process_mode": PROCESS_MODE_PLAIN_SUBPROCESS,
        "inferred_permissions": ["network.client", "diceframe.http"],
        "required_permission": None,
        "contributes": None,
        "filterable": True,
        "filter_order": 5,
    },
    "content-pack": {
        "level": "supported",
        "summary": "可注册规则、世界、角色、NPC、道具、法术、职业和地图内容",
        "process_mode": PROCESS_MODE_STATIC,
        "inferred_permissions": ["content.read", "content.import"],
        "required_permission": None,
        "contributes": _CONTENT_CONTRIBUTIONS,
        "filterable": True,
        "filter_order": 1,
        "cleanup": ["content_data"],
    },
    "theme": {
        "level": "supported",
        "summary": "可注册安全的主题颜色变量",
        "process_mode": PROCESS_MODE_STATIC,
        "inferred_permissions": ["theme.tokens"],
        "required_permission": None,
        "contributes": _THEME_CONTRIBUTIONS,
        "filterable": True,
        "filter_order": 2,
    },
    "voice-pack": {
        "level": "supported",
        "summary": "可选的 TTS 音色预设、试听与小型参考音频；也可直接使用个人音色",
        "process_mode": PROCESS_MODE_STATIC,
        "inferred_permissions": ["voice.assets"],
        "required_permission": None,
        "contributes": _VOICE_CONTRIBUTIONS,
        "filterable": True,
        "filter_order": 3,
    },
    "import-export": {
        "level": "reserved",
        "summary": "仅保留清单类型，尚未接入统一导入导出流程",
        "process_mode": PROCESS_MODE_NONE,
        "inferred_permissions": [],
        "required_permission": None,
        "contributes": None,
        "filterable": False,
        "filter_order": 0,
    },
    "provider": {
        "level": "supported",
        "summary": "以能力 Provider 进程提供外部服务（如 OpenAI 兼容图像生成），由宿主按能力调用",
        "process_mode": PROCESS_MODE_RPC_PROVIDER,
        "inferred_permissions": ["network.client"],
        "required_permission": None,
        "contributes": None,
        "filterable": True,
        "filter_order": 6,
    },
    "tool": {
        "level": "supported",
        "summary": "可通过受限 JSON-RPC 协议注册并执行结构化工具",
        "process_mode": PROCESS_MODE_RPC_TOOL,
        "inferred_permissions": ["tool.execute"],
        "required_permission": "tool.execute",
        "contributes": None,
        "filterable": True,
        "filter_order": 4,
    },
    "bot-extension": {
        "level": "supported",
        "summary": "可扩展 Bot Bridge 命令、消息处理和文本/图片/卡片渲染",
        "process_mode": PROCESS_MODE_RPC_BRIDGE,
        "inferred_permissions": ["bot.extend"],
        "required_permission": "bot.extend",
        "contributes": None,
        "filterable": False,
        "filter_order": 0,
    },
}

_DEFAULT_DESCRIPTOR: dict[str, Any] = {
    "level": "unsupported",
    "summary": "DiceFrame 不识别此插件类型",
    "process_mode": PROCESS_MODE_NONE,
    "inferred_permissions": [],
    "required_permission": None,
    "contributes": None,
    "filterable": False,
    "filter_order": 0,
}

# 派生集合：无进程类型（可省 entrypoint / declarative 风险）与 RPC 进程类型
STATIC_PLUGIN_TYPES = frozenset(
    t for t, d in PLUGIN_TYPE_SUPPORT.items() if d["process_mode"] == PROCESS_MODE_STATIC
)
RPC_PLUGIN_TYPES = frozenset(
    t for t, d in PLUGIN_TYPE_SUPPORT.items()
    if d["process_mode"] in (PROCESS_MODE_RPC_TOOL, PROCESS_MODE_RPC_BRIDGE, PROCESS_MODE_RPC_PROVIDER)
)


def plugin_type_support(plugin_type: str) -> dict[str, Any]:
    """返回面向商店/前端的 support level + summary（兼容旧调用方）。"""
    descriptor = PLUGIN_TYPE_SUPPORT.get(plugin_type)
    if descriptor:
        return {"level": descriptor["level"], "summary": descriptor["summary"]}
    return {"level": "unsupported", "summary": "DiceFrame 不识别此插件类型"}


def plugin_type_descriptor(plugin_type: str) -> dict[str, Any]:
    """返回完整 descriptor 副本；未知类型返回默认 descriptor。"""
    descriptor = PLUGIN_TYPE_SUPPORT.get(plugin_type)
    if descriptor:
        return dict(descriptor)
    return dict(_DEFAULT_DESCRIPTOR)


def list_plugin_types() -> list[dict[str, Any]]:
    """返回全部插件类型（按 filter_order 升序），供前端筛选/展示数据驱动。"""
    items = []
    for type_id, descriptor in PLUGIN_TYPE_SUPPORT.items():
        items.append({
            "id": type_id,
            "level": descriptor["level"],
            "filterable": bool(descriptor.get("filterable")),
            "filter_order": int(descriptor.get("filter_order", 0)),
        })
    items.sort(key=lambda item: (item["filter_order"], item["id"]))
    return items
