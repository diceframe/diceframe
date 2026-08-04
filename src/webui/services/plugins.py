"""Manifest 插件查询、配置和生命周期。"""
from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.bots.qq.card_renderer import cleanup_card_cache
from src.plugin_host.content import safe_id_part
from src.plugin_host.support import list_plugin_types as _support_plugin_types, plugin_type_descriptor

if TYPE_CHECKING:
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")

def list_plugins(api: "WebAPI") -> dict[str, Any]:
    return {"ok": True, "plugins": api._plugins.list_public() if api._plugins else []}

def list_plugin_types(api: "WebAPI") -> dict[str, Any]:
    """插件类型清单（数据驱动前端筛选/展示）。"""
    return {"ok": True, "types": _support_plugin_types()}

async def rescan_plugins(api: "WebAPI") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "plugins": []}
    await api._plugins.rescan()
    return {"ok": True, "plugins": api._plugins.list_public()}

def plugin_detail(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    if not api._plugins: return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **api._plugins.public_detail(plugin_id)}

def read_plugin_docs(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    if not api._plugins: return {"ok": False, "error": "插件宿主未启用"}
    return api._plugins.read_docs(plugin_id)


def sync_plugin_lorebooks(api: "WebAPI") -> dict[str, Any]:
    """同步已启用插件的世界模板世界书到世界书库（幂等）。

    委托 PluginHost.sync_lorebooks：条目 id 加 `_plugin_{plugin_id}_` 标记，
    便于卸载时精确清理。list_worlds / list_world_templates 调用前同步，使
    世界书页面无需先开一把游戏即可看到插件贡献的条目。
    """
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, "synced": api._plugins.sync_lorebooks(api._lore)}


def _world_in_use(world_id: str, registry) -> bool:
    """世界是否被正在进行的对局引用（有对局在用则不删）。"""
    if not registry:
        return False
    try:
        for game in registry.list_all():
            if str(getattr(game, "world_id", "") or "") == world_id:
                return True
    except Exception:
        logger.warning("检查世界对局引用失败，视为在用: %s", world_id, exc_info=True)
        return True
    return False


def cleanup_plugin_lorebook(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    """卸载插件时清理其贡献的持久化内容。

    世界书条目与卡库角色卡都带 `source_plugin` 来源标记，按数据查询精确删除，
    保留用户自建内容。插件创建的世界：无对局在用且删完插件条目后世界已空才删，
    否则（仍有用户内容）保留。
    """
    result: dict[str, Any] = {"ok": True, "removed": 0, "cards_removed": 0, "worlds_removed": 0, "worlds_kept": []}
    if api._plugins and api._lore:
        # 1. 先记下插件创建的世界（删条目前），再删该插件来源的全部条目
        plugin_worlds = [str(w.get("id") or w.get("world_id") or "") for w in api._lore.list_plugin_worlds(plugin_id)]
        result["removed"] = api._lore.delete_entries_by_plugin(plugin_id)
        # 2. 插件创建的世界：无对局引用且删完条目后已空才删
        for wid in plugin_worlds:
            if not wid:
                continue
            if _world_in_use(wid, api._reg) or api._lore.list_entries(wid):
                result["worlds_kept"].append(wid)
                continue
            api._lore.delete_world_cascade(wid)
            result["worlds_removed"] += 1
    # 3. 卡库角色卡（source_plugin 标记），通过 WebAPI 委托，避免跨 service 导入。
    # 卡库清理是卸载的附带动作，失败不应阻断卸载，记录告警后继续。
    try:
        listed = api.list_character_cards()
        cards = list(listed.get("cards", [])) if isinstance(listed, dict) else []
        for card in cards:
            if str(card.get("source_plugin") or "") == plugin_id:
                api.delete_character_card(str(card["id"]))
                result["cards_removed"] += 1
    except Exception:
        logger.warning("插件卡库清理失败，已跳过: %s", plugin_id, exc_info=True)
    return result

def _autoimport_plugin_content(api: "WebAPI", plugin_id: str) -> None:
    """启用内容包时自动灌注全部内容资源，幂等：已存在则跳过，不重复创建。

    角色模板 -> 卡库（全局）；NPC/道具/法术/职业 -> 插件自己的世界（world_template
    的世界，sync 时已建好）。无 world_template 时只导角色模板。失败记日志不阻断启用。
    """
    if not api._plugins:
        return
    target_world = ""
    for item in api._plugins.contributions.list("world_template"):
        if item.plugin_id == plugin_id and item.key:
            target_world = str(item.key)
            break
    resources = api._plugins.list_content_resources()
    for kind in ("character_template", "npc", "item", "spell", "class"):
        for resource in resources.get(kind, []):
            if str(resource.get("plugin_id") or "") != plugin_id:
                continue
            try:
                if kind == "character_template":
                    api.save_character_card(_content_to_character_card(resource))
                elif target_world and api._lore and api._lore.get_world(target_world):
                    entry = _content_to_lore_entry(resource, kind, target_world)
                    if not api._lore.get_entry(entry["id"]):
                        api.save_entry(entry)
            except Exception:
                logger.warning("自动灌注插件 %s 内容失败（%s）", plugin_id, kind, exc_info=True)


def _maybe_autoimport_after_install(api: "WebAPI", plugin_id: str) -> None:
    """安装/更新内容包后自动同步世界书并灌入内容，避免已启用插件更新后内容缺失。"""
    if not api._plugins:
        return
    try:
        detail = api._plugins.public_detail(plugin_id)
    except Exception:
        return
    if not detail.get("enabled") or detail.get("status") != "active":
        return
    try:
        sync_plugin_lorebooks(api)
        _autoimport_plugin_content(api, plugin_id)
    except Exception:
        logger.warning("安装后自动灌入插件内容失败，已跳过: %s", plugin_id, exc_info=True)


async def update_plugin_config(api: "WebAPI", plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    if not api._plugins: return {"ok": False, "error": "插件宿主未启用"}
    result = await api._plugins.update_config(plugin_id, changes)
    # update_config 失败会抛异常，能走到这行即成功；public_detail 不含 ok，故不再判断 result.get("ok")。
    # 启用内容包/主题时立即同步世界书 + 自动灌注全部内容资源，避免用户还得手动一键导入。
    if changes.get("enabled") is True:
        sync_plugin_lorebooks(api)
        _autoimport_plugin_content(api, plugin_id)
    return {"ok": True, **result}

async def control_plugin(api: "WebAPI", plugin_id: str, action: str) -> dict[str, Any]:
    if not api._plugins: return {"ok": False, "error": "插件宿主未启用"}
    method = {"start": api._plugins.start, "stop": api._plugins.stop, "restart": api._plugins.restart}.get(action)
    if not method: return {"ok": False, "error": "插件操作无效"}
    await method(plugin_id)
    if action in ("start", "restart"):
        sync_plugin_lorebooks(api)
    return {"ok": True, **api._plugins.public_detail(plugin_id)}

async def install_plugin(api: "WebAPI", payload: bytes, overwrite: bool = False) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    detail = await api._plugins.install_from_zip(payload, overwrite=overwrite)
    _maybe_autoimport_after_install(api, detail.get("id", ""))
    return {"ok": True, **detail}

async def list_plugin_marketplace(api: "WebAPI") -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用", "plugins": []}
    return await api._plugins.marketplace_plugins()

async def install_marketplace_plugin(api: "WebAPI", plugin_id: str, overwrite: bool = False) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    result = await api._plugins.install_from_marketplace(plugin_id, overwrite=overwrite)
    _maybe_autoimport_after_install(api, plugin_id)
    return {"ok": True, **result}

async def update_marketplace_plugin(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **await api._plugins.update_from_marketplace(plugin_id)}

# 卸载清理域注册表：新增清理域时实现 handler 并在此注册，再在类型 descriptor 的
# cleanup 列表声明。content_data 是 lorebook+worlds+cards 的耦合清理（必须先抓
# 世界列表再删条目，见 cleanup_plugin_lorebook），不可拆成独立域。
_CLEANUP_DOMAINS = {
    "content_data": cleanup_plugin_lorebook,
}


def _run_cleanup_domains(api: "WebAPI", plugin_id: str) -> dict[str, Any]:
    """按插件类型 descriptor 声明的清理域执行，聚合各域返回的计数/保留信息。"""
    plugin_type = api._plugins.plugin_type_of(plugin_id) if api._plugins else ""
    descriptor = plugin_type_descriptor(plugin_type)
    result: dict[str, Any] = {}
    for domain_name in descriptor.get("cleanup", []):
        handler = _CLEANUP_DOMAINS.get(domain_name)
        if handler:
            result.update(handler(api, plugin_id))
    return result


async def uninstall_plugin(api: "WebAPI", plugin_id: str, delete_data: bool = False) -> dict[str, Any]:
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    # 卸载前按类型 descriptor 声明的清理域清理插件灌入的数据，避免残留
    cleanup = _run_cleanup_domains(api, plugin_id)
    result = await api._plugins.uninstall(plugin_id, delete_data=delete_data)
    return {
        "ok": True,
        **result,
        "lorebook_removed": cleanup.get("removed", 0),
        "cards_removed": cleanup.get("cards_removed", 0),
        "worlds_removed": cleanup.get("worlds_removed", 0),
        "worlds_kept": cleanup.get("worlds_kept", []),
    }

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


def import_all_plugin_content(
    api: "WebAPI",
    plugin_id: str,
    target_world_id: str = "",
) -> dict[str, Any]:
    """一键导入插件全部内容：角色卡→卡库，NPC/道具/魔法/职业→指定世界书。"""
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    plugin_id = (plugin_id or "").strip()
    target_world_id = (target_world_id or "").strip()
    resources = api._plugins.list_content_resources()
    kinds = ("character_template", "npc", "item", "spell", "class")
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for kind in kinds:
        for resource in resources.get(kind, []):
            if str(resource.get("plugin_id") or "") != plugin_id:
                continue
            try:
                if kind == "character_template":
                    card = _content_to_character_card(resource)
                    result = api.save_character_card(card)
                    if result.get("ok"):
                        imported.append({"kind": kind, "name": _content_name(resource), "as": "character_card"})
                    else:
                        errors.append({"kind": kind, "name": _content_name(resource), "error": result.get("error", "")})
                else:
                    if not target_world_id:
                        skipped.append({"kind": kind, "name": _content_name(resource), "reason": "未选择世界书"})
                        continue
                    if not api._lore.get_world(target_world_id):
                        return {"ok": False, "error": "目标世界书不存在"}
                    entry = _content_to_lore_entry(resource, kind, target_world_id)
                    if api._lore.get_entry(entry["id"]):
                        # 幂等：已存在则更新，不创建时间戳副本（避免重复导入产生重复条目）
                        api._lore.update_entry(entry["id"], entry)
                        result = {"ok": True}
                    else:
                        result = api.save_entry(entry)
                    if result.get("ok"):
                        imported.append({"kind": kind, "name": _content_name(resource), "as": "lorebook_entry"})
                    else:
                        errors.append({"kind": kind, "name": _content_name(resource), "error": result.get("error", "")})
            except Exception as exc:
                errors.append({"kind": kind, "name": _content_name(resource), "error": str(exc)})
    return {
        "ok": True,
        "plugin_id": plugin_id,
        "imported": imported,
        "imported_count": len(imported),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "errors": errors,
        "error_count": len(errors),
    }


def plugin_asset_path(api: "WebAPI", plugin_id: str, relative_path: str) -> Path:
    if not api._plugins:
        raise KeyError("插件宿主未启用")
    return api._plugins.public_asset_path(plugin_id, relative_path)


def export_content_pack(
    api: "WebAPI",
    plugin_id: str,
    name: str,
    version: str,
    description: str,
    world_id: str = "",
    card_ids: list[str] | None = None,
    rule_id: str = "",
    flat: bool = False,
) -> dict[str, Any]:
    """把应用内的世界/角色卡/规则导出成一个内容包 .dfplugin。

    以世界为锚点：世界的世界书条目 -> world_template.starter_lorebook（无损）；
    自定义规则 -> content/rules/；勾选的角色卡 -> content/characters/。
    返回 {"ok": True, "payload": bytes, "filename": ...}，payload 是 .dfplugin 字节。
    """
    if not api._plugins:
        return {"ok": False, "error": "插件宿主未启用"}
    plugin_id = (plugin_id or "").strip()
    name = (name or "").strip()
    version = (version or "0.1.0").strip() or "0.1.0"
    description = (description or "").strip()
    world_id = (world_id or "").strip()
    rule_id = (rule_id or "").strip()
    card_ids = [str(c).strip() for c in card_ids if str(c).strip()] if isinstance(card_ids, list) else []
    if not plugin_id or not name:
        return {"ok": False, "error": "请填写内容包 ID 和名称"}

    files: dict[str, str | bytes] = {}
    has_world = has_rule = has_cards = False
    world_default_rule = rule_id if rule_id else ""

    if rule_id:
        files.update(_rule_files(rule_id, api._rules_dir))
        has_rule = bool(files)

    if world_id:
        world = api._lore.get_world(world_id)
        if not world:
            return {"ok": False, "error": "世界不存在"}
        entries = api._lore.list_entries(world_id)
        template = _world_to_template(world, entries, world_default_rule)
        files[f"content/worlds/{world_id}.json"] = json.dumps(template, ensure_ascii=False, indent=2)
        has_world = True

    if card_ids:
        selected = set(card_ids)
        for card in api.list_character_cards().get("cards", []):
            if str(card.get("id") or "") not in selected:
                continue
            tmpl = _card_to_character_template(card, world_id=world_id, rule_id=rule_id)
            fname = safe_id_part(card.get("character_name") or card.get("id") or "character") or "character"
            files[f"content/characters/{fname}.json"] = json.dumps(tmpl, ensure_ascii=False, indent=2)
            has_cards = True

    if not (has_world or has_rule or has_cards):
        return {"ok": False, "error": "请至少选择一个世界、角色卡或规则"}

    manifest = build_content_pack_manifest(plugin_id, name, version, description, has_world, has_rule, has_cards)
    files["plugin.json"] = json.dumps(manifest, ensure_ascii=False, indent=2)
    files["config.schema.json"] = json.dumps(_default_config_schema(name), ensure_ascii=False, indent=2)
    files["README.md"] = _default_readme(name, description, has_world, has_rule, has_cards)

    payload = api._plugins.package_files(plugin_id, files, flat=flat)
    filename = f"{plugin_id}-{version}-src.zip" if flat else f"{plugin_id}-{version}.dfplugin"
    return {"ok": True, "payload": payload, "filename": filename}


def build_content_pack_manifest(
    plugin_id: str, name: str, version: str, description: str,
    has_world: bool, has_rule: bool, has_cards: bool,
) -> dict[str, Any]:
    contributes: dict[str, list[str]] = {}
    if has_world:
        contributes["world_templates"] = ["content/worlds/*.json"]
    if has_rule:
        contributes["rules"] = ["content/rules/*.json"]
    if has_cards:
        contributes["character_templates"] = ["content/characters/*.json"]
    capabilities: list[str] = []
    if has_world:
        capabilities.append("content.world")
    if has_rule:
        capabilities.append("content.rule")
    if has_cards:
        capabilities.append("content.character-template")
    return {
        "schema_version": 1,
        "id": plugin_id,
        "name": name,
        "version": version,
        "description": description,
        "plugin_type": "content-pack",
        "config_schema": "config.schema.json",
        "capabilities": capabilities,
        "permissions": ["plugin.config", "content.read", "content.import"],
        "contributes": contributes,
        "docs": "README.md",
    }


def _default_config_schema(name: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "title": f"启用{name}",
                "description": "启用后，本包的世界、规则与角色卡会出现在创建游戏界面和插件内容目录。",
                "default": False,
                "ui": {"control": "switch", "order": 10},
            }
        },
    }


def _default_readme(name: str, description: str, has_world: bool, has_rule: bool, has_cards: bool) -> str:
    lines = [f"# {name}", ""]
    if description:
        lines += [description, ""]
    lines.append("## 内容")
    if has_world:
        lines.append("- 世界模板（含世界书条目，启用后自动灌入）")
    if has_rule:
        lines.append("- 规则")
    if has_cards:
        lines.append("- 角色模板（可在插件内容目录导入角色卡库）")
    lines += ["", "## 用法", "1. 设置页 -> 插件 -> 导入本 .dfplugin", "2. 打开本内容包的开关", "3. 创建游戏时选择本世界与规则", ""]
    return "\n".join(lines)


def _world_to_template(world: dict[str, Any], entries: list[dict[str, Any]], default_rule: str = "") -> dict[str, Any]:
    world_id = str(world.get("id") or world.get("world_id") or "")
    template: dict[str, Any] = {
        "world_id": world_id,
        "world_name": str(world.get("name") or world.get("world_name") or world_id),
        "description": str(world.get("description") or ""),
        "language": str(world.get("language") or "zh-CN"),
        "starter_lorebook": [_entry_to_lorebook_entry(e) for e in entries if isinstance(e, dict)],
    }
    if default_rule:
        template["default_rule"] = default_rule
    return template


def _entry_to_lorebook_entry(entry: dict[str, Any]) -> dict[str, Any]:
    # 保留条目全部业务字段，仅去掉内部追踪字段（world_id/source_plugin/时间戳）。
    # starter_lorebook 经 sync_lorebooks 用 dict(raw) 原样写回 add_entry，实现
    # 导出 -> 装回的元数据无损往返（unreliable/match_mode/order/group/sticky 等）。
    # 不把 spell/class 拆到 content/spells|classes：那会改走 _content_to_lore_entry，
    # 其 _content_description 会把 content 包装成 "类型：法术\n内容：..."，反而破坏保真。
    skip = {"world_id", "source_plugin", "created_at", "updated_at"}
    keywords = entry.get("keywords")
    if not isinstance(keywords, list):
        keywords = [keywords] if keywords else []
    result = {k: v for k, v in entry.items() if k not in skip}
    result["keywords"] = [str(k).strip() for k in keywords if str(k).strip()]
    result.setdefault("type", "other")
    result.setdefault("tier", "background")
    return result


def _card_to_character_template(card: dict[str, Any], world_id: str = "", rule_id: str = "") -> dict[str, Any]:
    # 去掉应用内部字段：source 是运行期来源标记、plugin_content_id 是插件资源回链、
    # schema_version/raw_sillytavern 是导入元数据、source_plugin 会泄露原插件身份。
    # portrait 是 {kind, id/asset_id} 引用（不是 base64）：builtin 可移植故保留，
    # upload 的 asset_id 在目标用户不存在会变成失效引用，故丢弃。
    skip = {"source", "plugin_content_id", "schema_version", "raw_sillytavern", "source_plugin", "portrait"}
    template = {k: v for k, v in card.items() if k not in skip}
    portrait = card.get("portrait")
    if isinstance(portrait, dict) and portrait.get("kind") == "builtin":
        template["portrait"] = portrait
    name = str(card.get("character_name") or card.get("id") or "character")
    template.setdefault("character_name", name)
    template["id"] = safe_id_part(card.get("id") or name)
    if world_id:
        template["world_id"] = world_id
    if rule_id:
        template["rule_id"] = rule_id
    return template


def _rule_files(rule_id: str, rules_dir: Path) -> dict[str, str]:
    """读取自定义规则文件（中文版 + 英文版若存在），返回 {相对路径: 文本}。"""
    files: dict[str, str] = {}
    base = rules_dir / f"{rule_id}.json"
    if base.exists():
        files[f"content/rules/{rule_id}.json"] = base.read_text(encoding="utf-8")
    en = rules_dir / f"{rule_id}_en.json"
    if en.exists():
        files[f"content/rules/{rule_id}_en.json"] = en.read_text(encoding="utf-8")
    return files



def _content_name(resource: dict[str, Any]) -> str:
    return str(resource.get("character_name") or resource.get("name") or resource.get("id") or "未命名").strip()


def _content_to_character_card(resource: dict[str, Any]) -> dict[str, Any]:
    card = dict(resource)
    card.pop("readonly", None)
    plugin_name = str(resource.get("plugin_name") or resource.get("plugin_id") or "插件内容包")
    card["source"] = f"插件内容包：{plugin_name}"
    card["plugin_content_id"] = resource.get("id", "")
    card.setdefault("character_name", _content_name(resource))
    card["id"] = f"plugin_{safe_id_part(resource.get('plugin_id', 'pack'))}_{safe_id_part(resource.get('id', int(time.time_ns())))}"
    card["source_plugin"] = str(resource.get("plugin_id") or "").strip()
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
        "id": f"{world_id}_plugin_{safe_id_part(kind)}_{safe_id_part(plugin_id)}_{safe_id_part(resource_id)}",
        "world_id": world_id,
        "name": name,
        "type": kind if kind in ("npc", "item", "spell", "class") else "other",
        "keywords": clean_keywords[:12],
        "content": _content_description(resource, kind),
        "tier": tier,
        "unreliable": bool(resource.get("unreliable", False)),
        "match_mode": match_mode,
        "order": _int_or_default(resource.get("order"), 120),
        "group": "插件内容包",
        "source_plugin": plugin_id,
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
