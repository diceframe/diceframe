"""Manifest 插件查询、配置和生命周期。"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.bots.qq.card_renderer import cleanup_card_cache

if TYPE_CHECKING:
    from src.webui.api import WebAPI

def list_plugins(api: "WebAPI") -> dict[str, Any]:
    return {"ok": True, "plugins": api._plugins.list_public() if api._plugins else []}

async def rescan_plugins(api: "WebAPI") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "plugins": []}
    await api._plugins.rescan()
    return {"ok": True, "plugins": api._plugins.list_public()}

def plugin_detail(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    if not api._plugins: return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **api._plugins.public_detail(plugin_id)}

async def update_plugin_config(api: "WebAPI", plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    if not api._plugins: return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **await api._plugins.update_config(plugin_id, changes)}

async def control_plugin(api: "WebAPI", plugin_id: str, action: str) -> dict[str, Any]:
    if not api._plugins: return {"ok": False, "error": "插件宿主未启用"}
    method = {"start": api._plugins.start, "stop": api._plugins.stop, "restart": api._plugins.restart}.get(action)
    if not method: return {"ok": False, "error": "插件操作无效"}
    await method(plugin_id)
    return {"ok": True, **api._plugins.public_detail(plugin_id)}

async def install_plugin(api: "WebAPI", payload: bytes, overwrite: bool = False) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **await api._plugins.install_from_zip(payload, overwrite=overwrite)}

async def list_plugin_marketplace(api: "WebAPI") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "plugins": []}
    return await api._plugins.marketplace_plugins()

async def install_marketplace_plugin(api: "WebAPI", plugin_id: str, overwrite: bool = False) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **await api._plugins.install_from_marketplace(plugin_id, overwrite=overwrite)}

async def update_marketplace_plugin(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **await api._plugins.update_from_marketplace(plugin_id)}

async def uninstall_plugin(api: "WebAPI", plugin_id: str, delete_data: bool = False) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **await api._plugins.uninstall(plugin_id, delete_data=delete_data)}

def list_plugin_mirrors(api: "WebAPI") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "mirrors": []}
    return {"ok": True, **api._plugins.list_mirrors()}

def add_plugin_mirror(api: "WebAPI", data: dict[str, Any]) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, "mirror": api._plugins.add_mirror(data)}

def update_plugin_mirror(api: "WebAPI", mirror_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, "mirror": api._plugins.update_mirror(mirror_id, data)}

def delete_plugin_mirror(api: "WebAPI", mirror_id: str) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **api._plugins.delete_mirror(mirror_id)}

async def test_plugin_mirror(api: "WebAPI", mirror_id: str = "") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return await api._plugins.test_mirror(mirror_id)

def clear_plugin_card_cache(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    if plugin_id != "qq-napcat":
        return {"ok": False, "error": "该插件没有可清理的卡片缓存"}
    api._plugins.public_detail(plugin_id)  # 触发 KeyError，保持和其他插件接口一致
    data_dir = Path(api._plugins.data_dir).resolve()
    card_dir = (data_dir / "bot" / "cards").resolve()
    if data_dir not in card_dir.parents:
        return {"ok": False, "error": "卡片缓存路径非法"}
    result = cleanup_card_cache(card_dir, delete_all=True)
    return {"ok": True, "path": str(card_dir), **result}


def list_plugin_contributions(api: "WebAPI", kind: str = "") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "contributions": []}
    contributions = api._plugins.list_contributions((kind or "").strip())
    return {"ok": True, "contributions": contributions, "total": len(contributions)}


def list_plugin_themes(api: "WebAPI") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "themes": []}
    themes = api._plugins.list_themes()
    return {"ok": True, "themes": themes, "total": len(themes)}


def list_plugin_tools(api: "WebAPI") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "tools": []}
    tools = api._plugins.list_tools()
    return {"ok": True, "tools": tools, "total": len(tools)}


async def invoke_plugin_tool(
    api: "WebAPI",
    plugin_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    result = await api._plugins.call_tool(
        (plugin_id or "").strip(),
        (tool_name or "").strip(),
        arguments,
        context=context,
    )
    return {"ok": True, "plugin_id": plugin_id, "tool_name": tool_name, "result": result}


def list_plugin_content(api: "WebAPI", kind: str = "", world_id: str = "", rule_id: str = "") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "resources": {}}
    resources = api._plugins.list_content_resources(
        (kind or "").strip(),
        world_id=(world_id or "").strip(),
        rule_id=(rule_id or "").strip(),
    )
    total = sum(len(items) for items in resources.values())
    return {"ok": True, "resources": resources, "total": total}


def import_plugin_content(
    api: "WebAPI",
    kind: str,
    resource_id: str,
    plugin_id: str = "",
    target_world_id: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    kind = (kind or "").strip()
    resource_id = (resource_id or "").strip()
    plugin_id = (plugin_id or "").strip()
    target_world_id = (target_world_id or "").strip()
    resource = api._plugins.get_content_resource(kind, resource_id, plugin_id=plugin_id)
    if not resource:
        return {"ok": False, "error": "插件内容不存在或未启用"}
    if kind == "character_template":
        card = _content_to_character_card(resource)
        result = api.save_character_card(card)
        if result.get("ok"):
            result["imported_as"] = "character_card"
            result["source_plugin_id"] = resource.get("plugin_id", "")
        return result

    if not target_world_id:
        return {"ok": False, "error": "请选择要导入到的世界书"}
    if not api._lore.get_world(target_world_id):
        return {"ok": False, "error": "目标世界书不存在"}
    entry = _content_to_lore_entry(resource, kind, target_world_id)
    if api._lore.get_entry(entry["id"]) and not overwrite:
        entry["id"] = f"{entry['id']}_{int(time.time() * 1000)}"
    result = api.save_entry(entry)
    if result.get("ok"):
        result["imported_as"] = "lorebook_entry"
        result["entry"] = entry
        result["source_plugin_id"] = resource.get("plugin_id", "")
    return result


def plugin_asset_path(api: "WebAPI", plugin_id: str, relative_path: str) -> Path:
    if not api._plugins:
        raise KeyError("插件宿主未启用")
    return api._plugins.public_asset_path(plugin_id, relative_path)


def _safe_id_part(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", text)
    return text.strip("_")[:48] or "content"


def _content_name(resource: dict[str, Any]) -> str:
    return str(resource.get("character_name") or resource.get("name") or resource.get("id") or "未命名").strip()


def _content_to_character_card(resource: dict[str, Any]) -> dict[str, Any]:
    card = dict(resource)
    card.pop("readonly", None)
    plugin_name = str(resource.get("plugin_name") or resource.get("plugin_id") or "插件内容包")
    card["source"] = f"插件内容包：{plugin_name}"
    card["plugin_content_id"] = resource.get("id", "")
    card.setdefault("character_name", _content_name(resource))
    card["id"] = f"plugin_{_safe_id_part(resource.get('plugin_id', 'pack'))}_{_safe_id_part(resource.get('id', int(time.time_ns())))}"
    return card


def _content_to_lore_entry(resource: dict[str, Any], kind: str, world_id: str) -> dict[str, Any]:
    name = _content_name(resource)
    plugin_id = str(resource.get("plugin_id") or "plugin")
    resource_id = str(resource.get("id") or name)
    tier = str(resource.get("tier") or "background")
    if tier not in {"core", "background", "archived"}:
        tier = "background"
    match_mode = str(resource.get("match_mode") or "any")
    if match_mode not in {"any", "all", "not_any", "not_all"}:
        match_mode = "any"
    keywords = resource.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = [keywords]
    clean_keywords = [str(item).strip() for item in keywords if str(item).strip()]
    if name and name not in clean_keywords:
        clean_keywords.insert(0, name)
    return {
        "id": f"{world_id}_plugin_{_safe_id_part(kind)}_{_safe_id_part(plugin_id)}_{_safe_id_part(resource_id)}",
        "world_id": world_id,
        "name": name,
        "type": "npc" if kind == "npc" else "item" if kind == "item" else "other",
        "keywords": clean_keywords[:12],
        "content": _content_description(resource, kind),
        "tier": tier,
        "unreliable": bool(resource.get("unreliable", False)),
        "match_mode": match_mode,
        "order": _int_or_default(resource.get("order"), 120),
        "group": "插件内容包",
    }


def _content_description(resource: dict[str, Any], kind: str) -> str:
    lines = []
    label = {
        "npc": "NPC",
        "item": "道具",
        "spell": "法术",
        "class": "职业",
    }.get(kind, "内容")
    lines.append(f"类型：{label}")
    plugin_name = str(resource.get("plugin_name") or resource.get("plugin_id") or "").strip()
    if plugin_name:
        lines.append(f"来源插件：{plugin_name}")
    for key, title in (
        ("description", "描述"),
        ("summary", "摘要"),
        ("content", "内容"),
        ("effect", "效果"),
        ("mechanics", "机制"),
        ("background", "背景"),
    ):
        value = resource.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{title}：{value.strip()}")
    details = {
        key: value for key, value in resource.items()
        if key not in {
            "id", "name", "character_name", "description", "summary", "content",
            "effect", "mechanics", "background", "plugin_id", "plugin_name",
            "source", "readonly", "world_id", "worlds", "rule_id", "rules",
            "keywords", "tier", "unreliable", "match_mode", "order",
        }
        and value not in (None, "", [], {})
    }
    if details:
        lines.append("数据：" + json.dumps(details, ensure_ascii=False, indent=2))
    return "\n".join(lines).strip()


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
