"""DiceFrame 插件类型 descriptor：单一来源。

每个插件类型的支持级别、运行方式、推断权限、必需权限、内容贡献映射、是否可筛选
都集中在这里。新增插件类型只改本表，宿主/策略/注册表/前端按数据驱动，不再散落硬编码。
"""

from __future__ import annotations

from .contracts import (
    PluginSupportView,
    PluginTypeDescriptor,
    PluginTypeListView,
)

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

PLUGIN_TYPE_SUPPORT: dict[str, PluginTypeDescriptor] = {
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

_DEFAULT_DESCRIPTOR: PluginTypeDescriptor = {
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


def plugin_type_support(plugin_type: str) -> PluginSupportView:
    """返回面向商店/前端的 support level + summary（兼容旧调用方）。"""
    descriptor = PLUGIN_TYPE_SUPPORT.get(plugin_type)
    if descriptor:
        return {"level": descriptor["level"], "summary": descriptor["summary"]}
    return {"level": "unsupported", "summary": "DiceFrame 不识别此插件类型"}


def plugin_type_descriptor(plugin_type: str) -> PluginTypeDescriptor:
    """返回完整 descriptor 副本；未知类型返回默认 descriptor。"""
    descriptor = PLUGIN_TYPE_SUPPORT.get(plugin_type)
    if descriptor:
        return descriptor.copy()
    return _DEFAULT_DESCRIPTOR.copy()


def list_plugin_types() -> list[PluginTypeListView]:
    """返回全部插件类型（按 filter_order 升序），供前端筛选/展示数据驱动。"""
    items: list[PluginTypeListView] = []
    for type_id, descriptor in PLUGIN_TYPE_SUPPORT.items():
        items.append({
            "id": type_id,
            "level": descriptor["level"],
            "filterable": bool(descriptor.get("filterable")),
            "filter_order": int(descriptor.get("filter_order", 0)),
        })
    items.sort(key=lambda item: (item["filter_order"], item["id"]))
    return items


# ---- Content Module profile（母方案 MOD-00，WR2/MOD 程序）------------------

# 模组（用户视角）在技术上仍是 plugin_type: content-pack；content_profile 与
# content_delivery_mode 是 manifest 上的可选扩展字段：
# - content_profile: "content-pack"（默认，传统内容包）| "adventure-module"
# - content_delivery_mode: "legacy_autoimport"（默认，启用即灌注世界/卡库）
#   | "catalog"（启用只注册内容库，运行 Adventure 时按需物化）
# 兼容红线（母方案 §73）：旧包零行为变化——缺省字段一律 legacy_autoimport；
# adventure-module MUST catalog（母方案 §6），不得静默灌注。
CONTENT_PROFILES = ("content-pack", "adventure-module")
CONTENT_DELIVERY_MODES = ("legacy_autoimport", "catalog")


def content_profile(manifest: dict) -> str:
    """Return the declared content profile, defaulting to ``content-pack``."""

    profile = str((manifest or {}).get("content_profile") or "").strip()
    return profile if profile in CONTENT_PROFILES else "content-pack"


def content_delivery_mode(manifest: dict) -> str:
    """Return the declared delivery mode, defaulting to ``legacy_autoimport``."""

    mode = str((manifest or {}).get("content_delivery_mode") or "").strip()
    return mode if mode in CONTENT_DELIVERY_MODES else "legacy_autoimport"


def validate_content_module_profile(manifest: dict) -> None:
    """Validate the content module profile fields; fail closed on bad combos.

    - 未知 content_profile / content_delivery_mode 拒绝；
    - ``content_profile: "adventure-module"`` 必须 ``catalog``（母方案 §6
      MUST：新模组安装=注册内容库，运行 Adventure 时按需物化，绝不启用即
      灌注）。
    """

    manifest = manifest or {}
    raw_profile = manifest.get("content_profile")
    if raw_profile is not None and str(raw_profile).strip() not in CONTENT_PROFILES:
        raise ValueError(f"不支持的 content_profile：{raw_profile!r}")
    raw_mode = manifest.get("content_delivery_mode")
    if raw_mode is not None and str(raw_mode).strip() not in CONTENT_DELIVERY_MODES:
        raise ValueError(f"不支持的 content_delivery_mode：{raw_mode!r}")
    if content_profile(manifest) == "adventure-module" and content_delivery_mode(manifest) != "catalog":
        raise ValueError(
            "adventure-module 必须使用 content_delivery_mode: catalog"
            "（安装只注册内容库，运行时按需物化）"
        )


def is_adventure_module(manifest: dict) -> bool:
    """Whether this content-pack presents itself as a user-facing 模组."""

    return content_profile(manifest) == "adventure-module"
