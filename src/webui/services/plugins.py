"""Manifest 插件查询、配置和生命周期。"""
from __future__ import annotations
import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.content.contracts import canonical_id
from src.content.rule_locale import materialize_rule
from src.engine.language import normalize_language
from src.plugin_host.content import safe_id_part
from src.rules.loader import RuleBundleLoader
from src.plugin_host.support import (
    content_delivery_mode,
    list_plugin_types as _support_plugin_types,
    plugin_type_descriptor,
)
from src.bots.bridge_core.card_renderer import cleanup_card_cache

logger = logging.getLogger("trpg")


@dataclass(frozen=True)
class PluginHostDependencies:
    plugin_host: Any | None


@dataclass(frozen=True)
class PluginContentStoreDependencies:
    lorebook: Any | None
    list_games: Callable[[], list[Any]]
    list_character_cards: Callable[[], dict[str, Any]]
    save_character_card: Callable[[dict[str, Any]], dict[str, Any]]
    delete_character_card: Callable[[str], dict[str, Any]]
    save_entry: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class PluginPortraitDependencies:
    plugin_asset_path: Callable[[str, str], Path]
    avatar_file: Callable[[str], Path | None]
    generated_image_file: Callable[[str], Path | None]
    save_avatar_upload: Callable[[str, str], dict[str, Any]]


@dataclass(frozen=True)
class PluginContentDependencies:
    plugin_host: Any | None
    store: PluginContentStoreDependencies
    portraits: PluginPortraitDependencies


@dataclass(frozen=True)
class PluginLifecycleDependencies:
    plugin_host: Any | None
    content: PluginContentDependencies


@dataclass(frozen=True)
class PluginExportMediaDependencies:
    package_scene_image: Callable[
        [Any, dict[str, str | bytes]], dict[str, str] | None
    ]
    package_content_map: Callable[..., Any]
    avatar_file: Callable[[str], Path | None]
    generated_image_file: Callable[[str], Path | None]
    plugin_asset_path: Callable[[str, str], Path]


@dataclass(frozen=True)
class PluginExportDependencies:
    plugin_host: Any | None
    lorebook: Any | None
    rules_dir: Path
    list_character_cards: Callable[[], dict[str, Any]]
    media: PluginExportMediaDependencies


def list_plugins(dependencies: PluginHostDependencies) -> dict[str, Any]:
    host = dependencies.plugin_host
    return {"ok": True, "plugins": host.list_public() if host else []}

def list_plugin_types() -> dict[str, Any]:
    """插件类型清单（数据驱动前端筛选/展示）。"""
    return {"ok": True, "types": _support_plugin_types()}

async def rescan_plugins(dependencies: PluginHostDependencies) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用", "plugins": []}
    await host.rescan()
    return {"ok": True, "plugins": host.list_public()}

def plugin_detail(
    dependencies: PluginHostDependencies,
    plugin_id: str,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **host.public_detail(plugin_id)}

def read_plugin_docs(
    dependencies: PluginHostDependencies,
    plugin_id: str,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return host.read_docs(plugin_id)


def sync_plugin_lorebooks(
    dependencies: PluginContentDependencies,
) -> dict[str, Any]:
    """同步已启用插件的世界模板世界书到世界书库（幂等）。

    委托 PluginHost.sync_lorebooks：条目 id 加 `_plugin_{plugin_id}_` 标记，
    便于卸载时精确清理。list_worlds / list_world_templates 调用前同步，使
    世界书页面无需先开一把游戏即可看到插件贡献的条目。
    """
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, "synced": host.sync_lorebooks(dependencies.store.lorebook)}


def _world_in_use(world_id: str, list_games: Callable[[], list[Any]]) -> bool:
    """世界是否被正在进行的对局引用（有对局在用则不删）。"""
    try:
        for game in list_games():
            if str(getattr(game, "world_id", "") or "") == world_id:
                return True
    except Exception:
        logger.warning("检查世界对局引用失败，视为在用: %s", world_id, exc_info=True)
        return True
    return False


def cleanup_plugin_lorebook(
    dependencies: PluginContentDependencies,
    plugin_id: str,
) -> dict[str, Any]:
    """卸载插件时清理其贡献的持久化内容。

    世界书条目与卡库角色卡都带 `source_plugin` 来源标记，按数据查询精确删除，
    保留用户自建内容。插件创建的世界：无对局在用且删完插件条目后世界已空才删，
    否则（仍有用户内容）保留。
    """
    result: dict[str, Any] = {"ok": True, "removed": 0, "cards_removed": 0, "worlds_removed": 0, "worlds_kept": []}
    lorebook = dependencies.store.lorebook
    if dependencies.plugin_host and lorebook:
        # 1. 先记下插件创建的世界（删条目前），再删该插件来源的全部条目
        plugin_worlds = [str(w.get("id") or w.get("world_id") or "") for w in lorebook.list_plugin_worlds(plugin_id)]
        result["removed"] = lorebook.delete_entries_by_plugin(plugin_id)
        # 2. 插件创建的世界：无对局引用且删完条目后已空才删
        for wid in plugin_worlds:
            if not wid:
                continue
            if _world_in_use(wid, dependencies.store.list_games) or lorebook.list_entries(wid):
                result["worlds_kept"].append(wid)
                continue
            lorebook.delete_world_cascade(wid)
            result["worlds_removed"] += 1
    # 3. 卡库角色卡（source_plugin 标记），通过显式存储依赖清理。
    # 卡库清理是卸载的附带动作，失败不应阻断卸载，记录告警后继续。
    try:
        listed = dependencies.store.list_character_cards()
        cards = list(listed.get("cards", [])) if isinstance(listed, dict) else []
        for card in cards:
            if str(card.get("source_plugin") or "") == plugin_id:
                dependencies.store.delete_character_card(str(card["id"]))
                result["cards_removed"] += 1
    except Exception:
        logger.warning("插件卡库清理失败，已跳过: %s", plugin_id, exc_info=True)
    return result

def _autoimport_plugin_content(
    dependencies: PluginContentDependencies,
    plugin_id: str,
) -> None:
    """启用内容包时自动灌注全部内容资源，幂等：已存在则跳过，不重复创建。

    角色模板 -> 卡库（全局）；NPC/道具/法术/职业 -> 插件自己的世界（world_template
    的世界，sync 时已建好）。无 world_template 时只导角色模板。失败记日志不阻断启用。
    """
    host = dependencies.plugin_host
    if not host:
        return
    target_world = ""
    for item in host.contributions.list("world_template"):
        if item.plugin_id == plugin_id and item.key:
            target_world = str(item.key)
            break
    resources = host.list_content_resources()
    for kind in ("character_template", "npc", "item", "spell", "class"):
        for resource in resources.get(kind, []):
            if str(resource.get("plugin_id") or "") != plugin_id:
                continue
            try:
                resource = _materialize_content_portrait(dependencies.portraits, resource)
                if kind == "character_template":
                    dependencies.store.save_character_card(_content_to_character_card(resource))
                elif target_world and dependencies.store.lorebook and dependencies.store.lorebook.get_world(target_world):
                    entry = _content_to_lore_entry(resource, kind, target_world)
                    if not dependencies.store.lorebook.get_entry(entry["id"]):
                        dependencies.store.save_entry(entry)
            except Exception:
                logger.warning("自动灌注插件 %s 内容失败（%s）", plugin_id, kind, exc_info=True)


def _legacy_autoimport_allowed(host: Any, plugin_id: str) -> bool:
    """FIX-01 §3.2：catalog 交付模式的模组**不 autoimport**。

    ``legacy_autoimport``（含缺省字段的传统 content-pack）保持旧行为：启用即把
    世界书/角色模板灌进 Lorebook 与卡库。``catalog`` 模式（adventure-module 必须
    用它）只注册内容库 / Adventure 来源，运行时按需物化，绝不写用户数据。
    """

    runtime = getattr(host, "plugins", {}).get(str(plugin_id or ""))
    manifest = getattr(runtime, "manifest", None)
    if not isinstance(manifest, dict):
        # 未知插件（例如主题）维持旧行为。
        return True
    return content_delivery_mode(manifest) == "legacy_autoimport"


def _maybe_autoimport_after_install(
    dependencies: PluginLifecycleDependencies,
    plugin_id: str,
) -> None:
    """安装/更新内容包后自动同步世界书并灌入内容，避免已启用插件更新后内容缺失。"""
    host = dependencies.plugin_host
    if not host:
        return
    try:
        detail = host.public_detail(plugin_id)
    except Exception:
        return
    if not detail.get("enabled") or detail.get("status") != "active":
        return
    if not _legacy_autoimport_allowed(host, plugin_id):
        return
    try:
        sync_plugin_lorebooks(dependencies.content)
        _autoimport_plugin_content(dependencies.content, plugin_id)
    except Exception:
        logger.warning("安装后自动灌入插件内容失败，已跳过: %s", plugin_id, exc_info=True)


async def update_plugin_config(
    dependencies: PluginLifecycleDependencies,
    plugin_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host: return {"ok": False, "error": "插件宿主未启用"}
    result = await host.update_config(plugin_id, changes)
    # update_config 失败会抛异常，能走到这行即成功；public_detail 不含 ok，故不再判断 result.get("ok")。
    # 启用内容包/主题时立即同步世界书 + 自动灌注全部内容资源，避免用户还得手动一键导入。
    if changes.get("enabled") is True and _legacy_autoimport_allowed(host, plugin_id):
        sync_plugin_lorebooks(dependencies.content)
        _autoimport_plugin_content(dependencies.content, plugin_id)
    return {"ok": True, **result}

async def control_plugin(
    dependencies: PluginLifecycleDependencies,
    plugin_id: str,
    action: str,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host: return {"ok": False, "error": "插件宿主未启用"}
    # 前端进程开关要求强制启动/停止，不受 config.enabled 拦截（enabled 是"开机自启"概念）。
    start_kwargs = {"require_enabled": False} if action in ("start", "restart") else {}
    method = {"start": host.start, "stop": host.stop, "restart": host.restart}.get(action)
    if not method: return {"ok": False, "error": "插件操作无效"}
    await method(plugin_id, **start_kwargs)
    if action in ("start", "restart") and _legacy_autoimport_allowed(host, plugin_id):
        sync_plugin_lorebooks(dependencies.content)
    return {"ok": True, **host.public_detail(plugin_id)}

async def install_plugin(
    dependencies: PluginLifecycleDependencies,
    payload: bytes,
    overwrite: bool = False,
    expected_plugin_type: str = "",
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    detail = await host.install_from_zip(
        payload, overwrite=overwrite, expected_plugin_type=expected_plugin_type,
    )
    _maybe_autoimport_after_install(dependencies, detail.get("id", ""))
    return {"ok": True, **detail}

async def list_plugin_marketplace(
    dependencies: PluginHostDependencies,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用", "plugins": []}
    return await host.marketplace_plugins()

async def install_marketplace_plugin(
    dependencies: PluginLifecycleDependencies,
    plugin_id: str,
    overwrite: bool = False,
    expected_plugin_type: str = "",
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    result = await host.install_from_marketplace(
        plugin_id, overwrite=overwrite, expected_plugin_type=expected_plugin_type,
    )
    _maybe_autoimport_after_install(dependencies, plugin_id)
    return {"ok": True, **result}

async def update_marketplace_plugin(
    dependencies: PluginHostDependencies,
    plugin_id: str,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **await host.update_from_marketplace(plugin_id)}

# 卸载清理域注册表：新增清理域时实现 handler 并在此注册，再在类型 descriptor 的
# cleanup 列表声明。content_data 是 lorebook+worlds+cards 的耦合清理（必须先抓
# 世界列表再删条目，见 cleanup_plugin_lorebook），不可拆成独立域。
_CLEANUP_DOMAINS = {
    "content_data": cleanup_plugin_lorebook,
}


def _run_cleanup_domains(
    dependencies: PluginLifecycleDependencies,
    plugin_id: str,
) -> dict[str, Any]:
    """按插件类型 descriptor 声明的清理域执行，聚合各域返回的计数/保留信息。"""
    host = dependencies.plugin_host
    plugin_type = host.plugin_type_of(plugin_id) if host else ""
    descriptor = plugin_type_descriptor(plugin_type)
    result: dict[str, Any] = {}
    for domain_name in descriptor.get("cleanup", []):
        handler = _CLEANUP_DOMAINS.get(domain_name)
        if handler:
            result.update(handler(dependencies.content, plugin_id))
    return result


async def uninstall_plugin(
    dependencies: PluginLifecycleDependencies,
    plugin_id: str,
    delete_data: bool = False,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    # 卸载前按类型 descriptor 声明的清理域清理插件灌入的数据，避免残留
    cleanup = _run_cleanup_domains(dependencies, plugin_id)
    result = await host.uninstall(plugin_id, delete_data=delete_data)
    return {
        "ok": True,
        **result,
        "lorebook_removed": cleanup.get("removed", 0),
        "cards_removed": cleanup.get("cards_removed", 0),
        "worlds_removed": cleanup.get("worlds_removed", 0),
        "worlds_kept": cleanup.get("worlds_kept", []),
    }

def list_plugin_mirrors(dependencies: PluginHostDependencies) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用", "mirrors": []}
    return {"ok": True, **host.list_mirrors()}

def add_plugin_mirror(
    dependencies: PluginHostDependencies,
    data: dict[str, Any],
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, "mirror": host.add_mirror(data)}

def update_plugin_mirror(
    dependencies: PluginHostDependencies,
    mirror_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, "mirror": host.update_mirror(mirror_id, data)}

def delete_plugin_mirror(
    dependencies: PluginHostDependencies,
    mirror_id: str,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return {"ok": True, **host.delete_mirror(mirror_id)}

async def test_plugin_mirror(
    dependencies: PluginHostDependencies,
    mirror_id: str = "",
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    return await host.test_mirror(mirror_id)

def clear_plugin_card_cache(
    dependencies: PluginHostDependencies,
    plugin_id: str,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    if plugin_id != "qq-napcat":
        return {"ok": False, "error": "该插件没有可清理的卡片缓存"}
    host.public_detail(plugin_id)  # 触发 KeyError，保持和其他插件接口一致
    data_dir = Path(host.data_dir).resolve()
    card_dir = (data_dir / "bot" / "cards").resolve()
    if data_dir not in card_dir.parents:
        return {"ok": False, "error": "卡片缓存路径非法"}
    result = cleanup_card_cache(card_dir, delete_all=True)
    return {"ok": True, "path": str(card_dir), **result}


def list_plugin_contributions(
    dependencies: PluginHostDependencies,
    kind: str = "",
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用", "contributions": []}
    contributions = host.list_contributions((kind or "").strip())
    return {"ok": True, "contributions": contributions, "total": len(contributions)}


def list_plugin_themes(dependencies: PluginHostDependencies) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用", "themes": []}
    themes = host.list_themes()
    return {"ok": True, "themes": themes, "total": len(themes)}


def list_plugin_tools(dependencies: PluginHostDependencies) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用", "tools": []}
    tools = host.list_tools()
    return {"ok": True, "tools": tools, "total": len(tools)}


async def invoke_plugin_tool(
    dependencies: PluginHostDependencies,
    plugin_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    result = await host.call_tool(
        (plugin_id or "").strip(),
        (tool_name or "").strip(),
        arguments,
        context=context,
    )
    return {"ok": True, "plugin_id": plugin_id, "tool_name": tool_name, "result": result}


def list_plugin_content(
    dependencies: PluginHostDependencies,
    kind: str = "",
    world_id: str = "",
    rule_id: str = "",
    language: str = "",
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用", "resources": {}}
    resources = host.list_content_resources(
        (kind or "").strip(),
        world_id=(world_id or "").strip(),
        rule_id=(rule_id or "").strip(),
        language=(language or "").strip(),
    )
    total = sum(len(items) for items in resources.values())
    return {"ok": True, "resources": resources, "total": total}


def import_plugin_content(
    dependencies: PluginContentDependencies,
    kind: str,
    resource_id: str,
    plugin_id: str = "",
    target_world_id: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    kind = (kind or "").strip()
    resource_id = (resource_id or "").strip()
    plugin_id = (plugin_id or "").strip()
    target_world_id = (target_world_id or "").strip()
    resource = host.get_content_resource(kind, resource_id, plugin_id=plugin_id)
    if not resource:
        return {"ok": False, "error": "插件内容不存在或未启用"}
    resource = _materialize_content_portrait(dependencies.portraits, resource)
    if kind == "character_template":
        card = _content_to_character_card(resource)
        result = dependencies.store.save_character_card(card)
        if result.get("ok"):
            result["imported_as"] = "character_card"
            result["source_plugin_id"] = resource.get("plugin_id", "")
        return result

    if not target_world_id:
        return {"ok": False, "error": "请选择要导入到的世界书"}
    lorebook = dependencies.store.lorebook
    if not lorebook or not lorebook.get_world(target_world_id):
        return {"ok": False, "error": "目标世界书不存在"}
    entry = _content_to_lore_entry(resource, kind, target_world_id)
    if lorebook.get_entry(entry["id"]) and not overwrite:
        entry["id"] = f"{entry['id']}_{int(time.time() * 1000)}"
    result = dependencies.store.save_entry(entry)
    if result.get("ok"):
        result["imported_as"] = "lorebook_entry"
        result["entry"] = entry
        result["source_plugin_id"] = resource.get("plugin_id", "")
    return result


def import_all_plugin_content(
    dependencies: PluginContentDependencies,
    plugin_id: str,
    target_world_id: str = "",
) -> dict[str, Any]:
    """一键导入插件全部内容：角色卡→卡库，NPC/道具/魔法/职业→指定世界书。"""
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    plugin_id = (plugin_id or "").strip()
    target_world_id = (target_world_id or "").strip()
    resources = host.list_content_resources()
    kinds = ("character_template", "npc", "item", "spell", "class")
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for kind in kinds:
        for resource in resources.get(kind, []):
            if str(resource.get("plugin_id") or "") != plugin_id:
                continue
            try:
                resource = _materialize_content_portrait(dependencies.portraits, resource)
                if kind == "character_template":
                    card = _content_to_character_card(resource)
                    result = dependencies.store.save_character_card(card)
                    if result.get("ok"):
                        imported.append({"kind": kind, "name": _content_name(resource), "as": "character_card"})
                    else:
                        errors.append({"kind": kind, "name": _content_name(resource), "error": result.get("error", "")})
                else:
                    if not target_world_id:
                        skipped.append({"kind": kind, "name": _content_name(resource), "reason": "未选择世界书"})
                        continue
                    lorebook = dependencies.store.lorebook
                    if not lorebook or not lorebook.get_world(target_world_id):
                        return {"ok": False, "error": "目标世界书不存在"}
                    entry = _content_to_lore_entry(resource, kind, target_world_id)
                    if lorebook.get_entry(entry["id"]):
                        # 幂等：已存在则更新，不创建时间戳副本（避免重复导入产生重复条目）
                        lorebook.update_entry(entry["id"], entry)
                        result = {"ok": True}
                    else:
                        result = dependencies.store.save_entry(entry)
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


def plugin_asset_path(
    dependencies: PluginHostDependencies,
    plugin_id: str,
    relative_path: str,
) -> Path:
    host = dependencies.plugin_host
    if not host:
        raise KeyError("插件宿主未启用")
    return host.public_asset_path(plugin_id, relative_path)


def export_content_pack(
    dependencies: PluginExportDependencies,
    plugin_id: str,
    name: str,
    version: str,
    description: str,
    world_id: str = "",
    card_ids: list[str] | None = None,
    rule_id: str = "",
    flat: bool = False,
    include_portraits: bool = True,
    include_scene_images: bool = True,
    world_scene_image: dict[str, Any] | None = None,
    rule_scene_image: dict[str, Any] | None = None,
    include_map: bool = True,
    map_background: dict[str, Any] | None = None,
    map_icons: list[dict[str, Any]] | None = None,
    language: str = "",
) -> dict[str, Any]:
    """把应用内的世界/角色卡/规则导出成一个内容包 .dfplugin。

    以世界为锚点：世界的世界书条目 -> world_template.starter_lorebook（无损）；
    自定义规则 -> content/rules/；勾选的角色卡 -> content/characters/。
    返回 {"ok": True, "payload": bytes, "filename": ...}，payload 是 .dfplugin 字节。
    """
    host = dependencies.plugin_host
    if not host:
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
    map_package = None
    try:
        if canonical_id(plugin_id) != plugin_id:
            raise ValueError
    except ValueError:
        return {"ok": False, "error": "内容包 ID 必须使用小写英文开头，且只能包含小写英文、数字、下划线或短横线"}
    exported_rule_id = _export_resource_id(rule_id, "rule") if rule_id else ""
    exported_world_id = _export_resource_id(world_id, "world") if world_id else ""
    world_default_rule = exported_rule_id
    pack_locale = normalize_language(language or "zh-CN")
    world: dict[str, Any] | None = None
    if world_id:
        lorebook = dependencies.lorebook
        world = lorebook.get_world(world_id) if lorebook else None
        if not world:
            return {"ok": False, "error": "世界不存在"}
        if not language:
            pack_locale = normalize_language(str(world.get("language") or "zh-CN"))

    if rule_id:
        rule_files = _rule_files(
            rule_id,
            dependencies.rules_dir,
            exported_rule_id=exported_rule_id,
            default_locale=pack_locale,
        )
        for path, raw in list(rule_files.items()):
            try:
                rule_data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not path.startswith("content/rules/"):
                continue
            reference = rule_scene_image if isinstance(rule_scene_image, dict) else rule_data.get("scene_image")
            if include_scene_images and reference:
                packaged = dependencies.media.package_scene_image(reference, files)
                if packaged:
                    rule_data["scene_image"] = packaged
                elif isinstance(rule_scene_image, dict):
                    return {"ok": False, "error": "无法读取所选规则头图"}
                else:
                    rule_data.pop("scene_image", None)
            else:
                rule_data.pop("scene_image", None)
            rule_files[path] = json.dumps(rule_data, ensure_ascii=False, indent=2)
        files.update(rule_files)
        has_rule = bool(rule_files)

    if world_id:
        assert world is not None
        assert dependencies.lorebook is not None
        entries = dependencies.lorebook.list_entries(world_id)
        export_world = dict(world)
        export_world["id"] = exported_world_id
        export_world["world_id"] = exported_world_id
        template = _world_to_template(
            export_world,
            entries,
            world_default_rule,
            default_locale=pack_locale,
        )
        if include_map:
            try:
                map_package = dependencies.media.package_content_map(
                    plugin_id,
                    name,
                    export_world,
                    entries,
                    files,
                    background_selection=map_background,
                    icon_uploads=map_icons,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if map_package and map_package.default_map:
                template["default_map"] = map_package.default_map
        reference = world_scene_image if isinstance(world_scene_image, dict) else world.get("scene_image")
        if include_scene_images and reference:
            packaged = dependencies.media.package_scene_image(reference, files)
            if packaged:
                template["scene_image"] = packaged
            elif isinstance(world_scene_image, dict):
                return {"ok": False, "error": "无法读取所选世界头图"}
        for entry in template.get("starter_lorebook", []):
            _package_record_portrait(
                dependencies.media, entry, files, include=include_portraits,
            )
        files[f"content/worlds/{exported_world_id}.json"] = json.dumps(template, ensure_ascii=False, indent=2)
        has_world = True

    if card_ids:
        selected = set(card_ids)
        for card in dependencies.list_character_cards().get("cards", []):
            if str(card.get("id") or "") not in selected:
                continue
            packaged_portrait = (
                _package_portrait(dependencies.media, card.get("portrait"), files)
                if include_portraits
                else {}
            )
            tmpl = _card_to_character_template(
                card,
                world_id=exported_world_id,
                rule_id=exported_rule_id,
                packaged_portrait=packaged_portrait,
            )
            fname = str(tmpl["id"])
            files[f"content/characters/{fname}.json"] = json.dumps(tmpl, ensure_ascii=False, indent=2)
            has_cards = True

    if not (has_world or has_rule or has_cards):
        return {"ok": False, "error": "请至少选择一个世界、角色卡或规则"}

    has_portraits = any(path.startswith("assets/portraits/") for path in files)
    has_scene_images = any(path.startswith("assets/scenes/") for path in files)
    manifest = build_content_pack_manifest(
        plugin_id, name, version, description, has_world, has_rule, has_cards, has_portraits, has_scene_images,
        bool(map_package and map_package.has_definitions),
        bool(map_package and map_package.has_locations),
        bool(map_package and map_package.has_icons),
        bool(map_package and map_package.has_backgrounds),
        default_locale=pack_locale,
    )
    files["plugin.json"] = json.dumps(manifest, ensure_ascii=False, indent=2)
    files["config.schema.json"] = json.dumps(_default_config_schema(name), ensure_ascii=False, indent=2)
    files["README.md"] = _default_readme(
        name,
        description,
        has_world,
        has_rule,
        has_cards,
        bool(map_package and map_package.has_map),
        language=pack_locale,
    )

    payload = host.package_files(plugin_id, files, flat=flat)
    filename = f"{plugin_id}-{version}-src.zip" if flat else f"{plugin_id}-{version}.dfplugin"
    return {"ok": True, "payload": payload, "filename": filename}


def build_content_pack_manifest(
    plugin_id: str, name: str, version: str, description: str,
    has_world: bool, has_rule: bool, has_cards: bool, has_portraits: bool = False,
    has_scene_images: bool = False,
    has_map_definitions: bool = False, has_map_locations: bool = False,
    has_map_icons: bool = False, has_map_backgrounds: bool = False,
    default_locale: str = "zh-CN",
) -> dict[str, Any]:
    contributes: dict[str, list[str]] = {}
    if has_world:
        contributes["world_templates"] = ["content/worlds/*.json"]
    if has_rule:
        contributes["rules"] = ["content/rules/*.json"]
    if has_cards:
        contributes["character_templates"] = ["content/characters/*.json"]
    if has_portraits:
        contributes["portraits"] = ["assets/portraits/*"]
    if has_scene_images:
        contributes["scene_images"] = ["assets/scenes/*"]
    if has_map_definitions:
        contributes["map_definitions"] = ["maps/definitions/*.json"]
    if has_map_locations:
        contributes["map_locations"] = ["maps/locations/*.json"]
    if has_map_icons:
        contributes["map_icons"] = ["maps/icons/*.webp"]
    if has_map_backgrounds:
        contributes["map_backgrounds"] = ["maps/backgrounds/*.webp"]
    capabilities: list[str] = []
    if has_world:
        capabilities.append("content.world")
    if has_rule:
        capabilities.append("content.rule")
    if has_cards:
        capabilities.append("content.character-template")
    if has_scene_images:
        capabilities.append("content.scene-image")
    if any((has_map_definitions, has_map_locations, has_map_icons, has_map_backgrounds)):
        capabilities.append("content.map")
    return {
        "schema_version": 1,
        "content_schema_version": 2,
        "locale_schema_version": 1,
        "default_locale": normalize_language(default_locale or "zh-CN"),
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


def _default_readme(
    name: str,
    description: str,
    has_world: bool,
    has_rule: bool,
    has_cards: bool,
    has_map: bool = False,
    language: str = "zh-CN",
) -> str:
    """按内容语言生成兜底 README；作者自带 README 时不会被调用。"""
    lang_key = "en" if (language or "").lower().startswith("en") else ("ja" if (language or "").lower().startswith("ja") else "zh")
    copy = {
        "zh": {
            "content": "## 内容",
            "world": "- 世界模板（含世界书条目，启用后自动灌入）",
            "rule": "- 规则",
            "cards": "- 角色模板（可在插件内容目录导入角色卡库）",
            "map": "- 场景地图（地点、地图定义及所选图标/底图）",
            "usage": "## 用法",
            "step1": "1. 设置页 -> 插件 -> 导入本 .dfplugin",
            "step2": "2. 打开本内容包的开关",
            "step3": "3. 创建游戏时选择本世界与规则",
        },
        "en": {
            "content": "## Contents",
            "world": "- World template (with lorebook entries, auto-imported when enabled)",
            "rule": "- Rule",
            "cards": "- Character templates (importable from the plugin content catalog)",
            "map": "- Scene map (locations, map definitions, icons/backgrounds)",
            "usage": "## Usage",
            "step1": "1. Open Settings → Plugins and import this .dfplugin",
            "step2": "2. Enable this content pack",
            "step3": "3. Pick this world and rule when creating a game",
        },
        "ja": {
            "content": "## 内容",
            "world": "- ワールドテンプレート（ロアブックエントリを含み、有効化時に自動で取り込みます）",
            "rule": "- ルール",
            "cards": "- キャラクターテンプレート（プラグインコンテンツからインポート可能）",
            "map": "- シーンマップ（地点・マップ定義・選択したアイコン/背景）",
            "usage": "## 使い方",
            "step1": "1. 設定 → プラグイン から本 .dfplugin をインポート",
            "step2": "2. 本コンテンツパックを有効化",
            "step3": "3. ゲーム作成時に本ワールドとルールを選択",
        },
    }[lang_key]
    lines = [f"# {name}", ""]
    if description:
        lines += [description, ""]
    lines.append(copy["content"])
    if has_world:
        lines.append(copy["world"])
    if has_rule:
        lines.append(copy["rule"])
    if has_cards:
        lines.append(copy["cards"])
    if has_map:
        lines.append(copy["map"])
    lines += ["", copy["usage"], copy["step1"], copy["step2"], copy["step3"], ""]
    return "\n".join(lines)


def _world_to_template(
    world: dict[str, Any],
    entries: list[dict[str, Any]],
    default_rule: str = "",
    *,
    default_locale: str = "zh-CN",
) -> dict[str, Any]:
    world_id = str(world.get("id") or world.get("world_id") or "")
    template: dict[str, Any] = {
        "world_schema_version": 2,
        "default_locale": normalize_language(default_locale or world.get("language") or "zh-CN"),
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


def _card_to_character_template(
    card: dict[str, Any],
    world_id: str = "",
    rule_id: str = "",
    packaged_portrait: dict[str, str] | None = None,
) -> dict[str, Any]:
    # 去掉应用内部字段：source 是运行期来源标记、plugin_content_id 是插件资源回链、
    # schema_version/raw_sillytavern 是导入元数据、source_plugin 会泄露原插件身份。
    # portrait 是 {kind, id/asset_id} 引用（不是 base64）：builtin 可移植故保留，
    # upload 的 asset_id 在目标用户不存在会变成失效引用，故丢弃。
    skip = {"source", "plugin_content_id", "schema_version", "raw_sillytavern", "source_plugin", "portrait"}
    template = {k: v for k, v in card.items() if k not in skip}
    portrait = packaged_portrait if packaged_portrait is not None else card.get("portrait")
    if isinstance(portrait, dict) and portrait.get("kind") in {"builtin", "asset"}:
        template["portrait"] = dict(portrait)
    name = str(card.get("character_name") or card.get("id") or "character")
    template.setdefault("character_name", name)
    template["id"] = _export_resource_id(str(card.get("id") or name), "character")
    if world_id:
        template["world_id"] = world_id
    if rule_id:
        template["rule_id"] = rule_id
    return template


def _rule_files(
    rule_id: str,
    rules_dir: Path,
    *,
    exported_rule_id: str = "",
    default_locale: str = "zh-CN",
) -> dict[str, str]:
    """Export one canonical V2 rule plus typed locale overlays."""
    files: dict[str, str] = {}
    base = rules_dir / f"{rule_id}.json"
    if not base.exists():
        return files
    target_id = exported_rule_id or _export_resource_id(rule_id, "rule")
    raw = json.loads(base.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("规则文件必须是 JSON 对象")
    source_is_v2 = int(raw.get("rule_schema_version", 1) or 1) >= 2
    core = RuleBundleLoader().load(base)
    core = dict(core)
    core.pop("extends", None)
    core["rule_id"] = target_id
    core["rule_schema_version"] = 2
    core["default_locale"] = normalize_language(default_locale or raw.get("default_locale") or "zh-CN")
    core["locale_schema_version"] = 1
    files[f"content/rules/{target_id}.json"] = json.dumps(core, ensure_ascii=False, indent=2)

    if source_is_v2:
        for locale_dir in sorted((rules_dir / "locales").glob("*")):
            overlay_path = locale_dir / f"{rule_id}.json"
            if not overlay_path.is_file():
                continue
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            if not isinstance(overlay, dict):
                raise ValueError(f"规则 locale 必须是 JSON 对象：{overlay_path}")
            overlay = dict(overlay)
            overlay["target"] = {"kind": "rule", "id": target_id}
            materialize_rule(core, overlay)
            locale = str(overlay.get("locale") or locale_dir.name)
            files[f"locales/{locale}/rules/{target_id}.json"] = json.dumps(
                overlay, ensure_ascii=False, indent=2,
            )
    else:
        for suffix, locale in (("en", "en"), ("ja", "ja")):
            legacy = rules_dir / f"{rule_id}_{suffix}.json"
            if not legacy.is_file():
                continue
            localized = RuleBundleLoader().load(legacy)
            overlay = _legacy_rule_locale_overlay(core, localized, locale, target_id)
            materialize_rule(core, overlay)
            files[f"locales/{locale}/rules/{target_id}.json"] = json.dumps(
                overlay, ensure_ascii=False, indent=2,
            )
    return files


def _legacy_rule_locale_overlay(
    core: dict[str, Any],
    localized: dict[str, Any],
    locale: str,
    target_id: str,
) -> dict[str, Any]:
    """Adapt a V1 full-language copy into V2 display-only fields."""
    display_fields = (
        "rule_name", "name", "description", "attr_hint", "skill_hint",
        "gm_prompt_appendix", "difficulty_instructions", "currency",
    )
    overlay: dict[str, Any] = {
        "locale_schema_version": 1,
        "locale": locale,
        "target": {"kind": "rule", "id": target_id},
        "rule": {key: localized[key] for key in display_fields if key in localized},
    }
    for collection, identity_key, allowed_fields in (
        ("attributes", "key", ("name", "label", "hint")),
        ("classes", "id", ("name", "description")),
        ("special_stats", "key", ("name", "description", "label", "hint", "flavor")),
    ):
        core_items = [item for item in core.get(collection, []) if isinstance(item, dict)]
        localized_items = [item for item in localized.get(collection, []) if isinstance(item, dict)]
        values: dict[str, dict[str, Any]] = {}
        for index, core_item in enumerate(core_items):
            identity_value = str(core_item.get(identity_key) or "")
            if not identity_value or index >= len(localized_items):
                continue
            display = {
                field: localized_items[index][field]
                for field in allowed_fields if field in localized_items[index]
            }
            if display:
                values[identity_value] = display
        if values:
            overlay[collection] = values
    core_item_map = core.get("items")
    localized_item_map = localized.get("items")
    if isinstance(core_item_map, dict) and isinstance(localized_item_map, dict):
        values = {
            str(identity_value): {
                field: localized_item_map[identity_value][field]
                for field in ("name", "description")
                if isinstance(localized_item_map.get(identity_value), dict)
                and field in localized_item_map[identity_value]
            }
            for identity_value in core_item_map
            if identity_value in localized_item_map
        }
        overlay["items"] = {key: value for key, value in values.items() if value}
    return overlay


def _export_resource_id(value: str, prefix: str) -> str:
    """Keep canonical ids; deterministically hash legacy/non-ASCII identities."""
    text = str(value or "").strip()
    try:
        return canonical_id(text)
    except ValueError:
        ascii_slug = "".join(
            ch.lower() if ch.isascii() and ch.isalnum() else "_"
            for ch in text
        ).strip("_")
        ascii_slug = "_".join(part for part in ascii_slug.split("_") if part)
        if ascii_slug and ascii_slug[0].isalpha():
            try:
                return canonical_id(ascii_slug[:80])
            except ValueError:
                pass
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return canonical_id(f"{prefix}_{digest}")



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


def _materialize_content_portrait(
    dependencies: PluginPortraitDependencies,
    resource: dict[str, Any],
) -> dict[str, Any]:
    """Copy a declared plugin portrait into DiceFrame's persistent avatar store."""
    result = dict(resource)
    portrait = resource.get("portrait")
    if not isinstance(portrait, dict):
        return result
    kind = str(portrait.get("kind") or "")
    if kind == "builtin":
        return result
    if kind == "upload":
        asset_id = str(portrait.get("asset_id") or "")
        if asset_id and dependencies.avatar_file(asset_id):
            return result
        result.pop("portrait", None)
        return result
    if kind == "generated":
        asset_id = str(portrait.get("asset_id") or "")
        if asset_id and dependencies.generated_image_file(asset_id):
            return result
        result.pop("portrait", None)
        return result
    if kind != "plugin":
        result.pop("portrait", None)
        return result
    plugin_id = str(resource.get("plugin_id") or "")
    if not plugin_id or plugin_id != str(portrait.get("plugin_id") or ""):
        result.pop("portrait", None)
        return result
    path = dependencies.plugin_asset_path(plugin_id, str(portrait.get("path") or ""))
    if path.stat().st_size > 3 * 1024 * 1024:
        raise ValueError("内容包头像不能超过 3 MB")
    saved = dependencies.save_avatar_upload(
        base64.b64encode(path.read_bytes()).decode("ascii"), path.name,
    )
    if not saved.get("ok") or not isinstance(saved.get("portrait"), dict):
        raise ValueError(str(saved.get("error") or "内容包头像导入失败"))
    result["portrait"] = saved["portrait"]
    return result


def _package_record_portrait(
    dependencies: "PluginExportMediaDependencies",
    record: dict[str, Any],
    files: dict[str, str | bytes],
    *,
    include: bool,
) -> None:
    portrait = _package_portrait(dependencies, record.get("portrait"), files) if include else None
    if portrait:
        record["portrait"] = portrait
    else:
        record.pop("portrait", None)


def _package_portrait(
    dependencies: "PluginExportMediaDependencies",
    portrait: Any,
    files: dict[str, str | bytes],
) -> dict[str, str] | None:
    if not isinstance(portrait, dict):
        return None
    kind = str(portrait.get("kind") or "")
    if kind == "builtin":
        portrait_id = str(portrait.get("id") or "").strip()
        return {"kind": "builtin", "id": portrait_id} if portrait_id else None
    source: Path | None = None
    if kind == "upload":
        source = dependencies.avatar_file(str(portrait.get("asset_id") or ""))
    elif kind == "generated":
        source = dependencies.generated_image_file(str(portrait.get("asset_id") or ""))
    elif kind == "plugin":
        plugin_id = str(portrait.get("plugin_id") or "")
        if plugin_id:
            source = dependencies.plugin_asset_path(plugin_id, str(portrait.get("path") or ""))
    if source is None or not source.is_file() or source.stat().st_size > 3 * 1024 * 1024:
        return None
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    suffix = source.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".webp"
    relative_path = f"assets/portraits/{digest}{suffix}"
    files.setdefault(relative_path, payload)
    return {"kind": "asset", "path": relative_path}


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
    entry = {
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
    portrait = resource.get("portrait")
    if isinstance(portrait, dict) and portrait.get("kind") in {"builtin", "upload", "generated"}:
        entry["portrait"] = dict(portrait)
    return entry


def _content_description(resource: dict[str, Any], kind: str) -> str:
    """内容包资源 → 世界书条目 content，保真输出主文本。

    主文本（description/content/summary）原文直接作为条目内容，不再包装
    「类型：/来源插件：/描述：」标题——类型与来源插件已存世界书条目的独立
    字段（type/source_plugin），重复拼入内容只会污染 LLM 上下文、挤占 token，
    英文内容包还会出现中英混杂。effect/机制/背景 等补充字段保留标题区分，
    其余自定义字段以 JSON 数据块附加。
    """
    lines = []
    for key in ("description", "content", "summary"):
        value = resource.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
    for key, title in (
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
