"""Manifest 插件查询、配置和生命周期。"""
from __future__ import annotations
import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.content.contracts import canonical_id
from src.content_modules.adapters import LegacyContentPackAdapter
from src.content_modules.refs import CONTENT_KIND_REGISTRY, ContentRefError
from src.content_modules.receipts import ImportReceipt, ImportReceiptStore
from src.content_modules.projection import entries_for_world
from src.engine.language import normalize_language
from src.lorebook.module_books import (
    book_fingerprint,
    declared_module_book_id,
    declared_module_book_resource,
    entry_fingerprint,
    materialize_module_book,
)
from src.plugin_host.content import safe_id_part
from src.plugin_host.support import (
    content_delivery_mode,
    list_plugin_types as _support_plugin_types,
    plugin_type_descriptor,
)
from src.bots.bridge_core.card_renderer import cleanup_card_cache

from src.webui.services.content_pack_export import (
    PluginExportDependencies, PluginExportMediaDependencies,
    export_content_pack, build_content_pack_manifest, _default_config_schema, _default_readme,
    _world_to_template, _entry_to_lorebook_entry, _card_to_character_template,
    _rule_files, _legacy_rule_locale_overlay, _export_resource_id,
)

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
    # Canonical Book-scoped writer.  ``None`` is an explicit compatibility
    # boundary for integrations that still expose only the world adapter.
    save_book_entry: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None


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



#: Receipts of plugin-delivered content are keyed under the plugin source kind.
PLUGIN_RECEIPT_SOURCE_KIND = "plugin"


def _import_receipts(dependencies: PluginContentDependencies) -> ImportReceiptStore | None:
    """Resolve the host-scoped receipt store without creating another authority."""

    root = getattr(dependencies.plugin_host, "data_dir", None)
    return ImportReceiptStore(root) if root else None


def _entry_fingerprint(entry: dict[str, Any] | None) -> str:
    """Digest of an entry's user-editable content, as the canonical store returns it."""

    return entry_fingerprint(entry)


def _book_fingerprint(lorebook: Any, book_id: str) -> str:
    """Digest of a module Book's user-editable state (book fields + entries).

    卸载与重导入都拿它和 receipt 里的包写入指纹比对：任何用户可见字段变化
    （书名、检索设置、条目的关键词 / 可见性 / 匹配策略…）都算改过。指纹实现
    与物化逻辑同处 ``module_books``，避免两处字段清单漂移。
    """

    return book_fingerprint(lorebook, book_id)


def _plugin_receipt(
    dependencies: PluginContentDependencies, plugin_id: str,
) -> ImportReceipt | None:
    """This plugin's import receipt, or None when it never recorded anything."""

    receipts = _import_receipts(dependencies)
    if receipts is None:
        return None
    return receipts.load(str(plugin_id), source_kind=PLUGIN_RECEIPT_SOURCE_KIND)


def _module_book_has_user_edits(
    dependencies: PluginContentDependencies, plugin_id: str, book_id: str,
) -> bool:
    """Whether an already materialized module Book must not be rewritten.

    三种状态：

    - 书不存在 → 不是用户改动，调用方可以建；
    - 有 receipt 基线 → 与当前指纹比对，不一致即用户改过；
    - 完全没有基线（receipt 里没有这本书）→ 无法证明内容是包写的（旧版本
      安装、人工复制、catalog 早期按需物化），按用户数据对待，不覆盖。
    """

    lorebook = dependencies.store.lorebook
    get_book = getattr(lorebook, "get_lorebook", None)
    if lorebook is None or not callable(get_book):
        return False
    if get_book(str(book_id)) is None:
        return False
    receipt = _plugin_receipt(dependencies, plugin_id)
    imported = receipt.fingerprint("lorebook_book", str(book_id)) if receipt else ""
    if not imported:
        return True
    return imported != _book_fingerprint(lorebook, str(book_id))


def _record_import_receipt(
    dependencies: PluginContentDependencies,
    plugin_id: str,
    object_type: str,
    object_id: str,
    *,
    updated: bool = False,
) -> None:
    receipts = _import_receipts(dependencies)
    if receipts is None or not str(object_id or ""):
        return
    fingerprint = ""
    lorebook = dependencies.store.lorebook
    if object_type == "lorebook_entry" and lorebook is not None:
        fingerprint = _entry_fingerprint(lorebook.get_entry(str(object_id)))
    elif object_type == "lorebook_book" and lorebook is not None:
        fingerprint = _book_fingerprint(lorebook, str(object_id))
    runtime = getattr(dependencies.plugin_host, "plugins", {}).get(str(plugin_id or ""))
    manifest = getattr(runtime, "manifest", {}) if runtime is not None else {}
    try:
        receipts.record(
            str(plugin_id),
            source_kind=PLUGIN_RECEIPT_SOURCE_KIND,
            source_version=str(manifest.get("version") or ""),
            source_digest=str(manifest.get("source_digest") or ""),
            object_type=object_type,
            object_id=str(object_id),
            updated=updated,
            fingerprint=fingerprint,
        )
    except OSError:
        logger.warning("记录插件导入 receipt 失败: %s", plugin_id, exc_info=True)


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
    result: dict[str, Any] = {
        "ok": True, "removed": 0, "cards_removed": 0, "worlds_removed": 0, "worlds_kept": [],
        "books_removed": 0, "books_kept": [], "entries_kept": [],
    }
    receipts: ImportReceiptStore | None = None
    receipt = None
    lorebook = dependencies.store.lorebook
    if dependencies.plugin_host and lorebook:
        receipts = _import_receipts(dependencies)
        receipt = (
            receipts.load(plugin_id, source_kind=PLUGIN_RECEIPT_SOURCE_KIND)
            if receipts is not None else None
        )
        detached = {
            (str(item.get("type") or ""), str(item.get("id") or ""))
            for item in (receipt.user_detached_objects if receipt else [])
        }
        # 1. 先记下插件创建的世界（删条目前）。有 receipt 时只删除 receipt
        # 明确拥有且未 detach 的对象；没有 receipt 的旧包继续走兼容清理。
        plugin_worlds = [str(w.get("id") or w.get("world_id") or "") for w in lorebook.list_plugin_worlds(plugin_id)]
        if receipt is None:
            result["removed"] = lorebook.delete_entries_by_plugin(plugin_id)
        else:
            entry_ids = {
                str(item.get("id") or "")
                for item in receipt.created_objects
                if str(item.get("type") or "") == "lorebook_entry"
                and ("lorebook_entry", str(item.get("id") or "")) not in detached
            }
            for entry_id in sorted(entry_ids):
                entry = lorebook.get_entry(entry_id)
                if entry is None or str(entry.get("source_plugin") or "") != str(plugin_id):
                    continue
                # A user edit after import detaches the entry from the package:
                # it now holds user content, so uninstall keeps it.
                imported = receipt.fingerprint("lorebook_entry", entry_id)
                if imported and imported != _entry_fingerprint(entry):
                    receipt.detach("lorebook_entry", entry_id)
                    result["entries_kept"].append(entry_id)
                    continue
                # LorebookStore.delete_entry() returns None; count what is really gone.
                lorebook.delete_entry(entry_id)
                if lorebook.get_entry(entry_id) is None:
                    result["removed"] += 1
        # 2. 插件创建的世界：无对局引用且删完条目后已空才删
        for wid in plugin_worlds:
            if not wid:
                continue
            if _world_in_use(wid, dependencies.store.list_games) or entries_for_world(lorebook, wid):
                result["worlds_kept"].append(wid)
                continue
            lorebook.delete_world_cascade(wid)
            result["worlds_removed"] += 1
        # 2.5 autoimport 按需物化的整本 module 书（issue #700）。有 receipt 时
        # 只删 receipt 明确拥有且未 detach 的书；没有 receipt 的旧包按
        # source 标记兜底。用户改过的书（指纹不一致）按 detach 语义保留。
        _cleanup_plugin_books(lorebook, receipt, plugin_id, result, detached)
    # 3. 卡库角色卡（source_plugin 标记），通过显式存储依赖清理。
    # 卡库清理是卸载的附带动作，失败不应阻断卸载，记录告警后继续。
    try:
        listed = dependencies.store.list_character_cards()
        cards = list(listed.get("cards", [])) if isinstance(listed, dict) else []
        receipt_card_ids = {
            str(item.get("id") or "")
            for item in (receipt.created_objects if receipt else [])
            if str(item.get("type") or "") == "character_card"
            and ("character_card", str(item.get("id") or "")) not in detached
        }
        for card in cards:
            if str(card.get("source_plugin") or "") == plugin_id and (
                receipt is None or str(card.get("id") or "") in receipt_card_ids
            ):
                dependencies.store.delete_character_card(str(card["id"]))
                result["cards_removed"] += 1
    except Exception:
        logger.warning("插件卡库清理失败，已跳过: %s", plugin_id, exc_info=True)
    if receipts is not None and receipt is not None:
        receipts.discard(plugin_id, source_kind=PLUGIN_RECEIPT_SOURCE_KIND)
    return result

def _autoimport_plugin_content(
    dependencies: PluginContentDependencies,
    plugin_id: str,
) -> None:
    """启用内容包时自动灌注全部内容资源，幂等：已存在则跳过，不重复创建。

    角色模板 -> 卡库（全局）；NPC/道具/法术/职业 -> 插件自己的世界（world_template
    的世界，sync 时已建好）。无 world_template 时只导角色模板。声明的 lorebook
    资源整本物化成用户书库的 Book（issue #700：字段名承诺「启用即落地」）。
    失败记日志不阻断启用。
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
    for kind in ("character_template", "npc", "item", "spell", "class", "lorebook"):
        for resource in resources.get(kind, []):
            if str(resource.get("plugin_id") or "") != plugin_id:
                continue
            try:
                if kind == "lorebook":
                    _autoimport_plugin_book(dependencies, plugin_id, resource, target_world)
                    continue
                resource = _materialize_content_portrait(dependencies.portraits, resource)
                import_target = CONTENT_KIND_REGISTRY.spec(kind).import_target
                if import_target == "character_card":
                    card = _content_to_character_card(resource)
                    result = dependencies.store.save_character_card(card)
                    if result.get("ok"):
                        _record_import_receipt(
                            dependencies, plugin_id, "character_card", str(card.get("id") or ""),
                        )
                elif target_world and dependencies.store.lorebook and dependencies.store.lorebook.get_world(target_world):
                    entry = _content_to_lore_entry(resource, kind, target_world)
                    if not dependencies.store.lorebook.get_entry(entry["id"]):
                        result = dependencies.store.save_entry(entry)
                        if result.get("ok"):
                            _record_import_receipt(
                                dependencies, plugin_id, "lorebook_entry", entry["id"],
                            )
            except Exception:
                logger.warning("自动灌注插件 %s 内容失败（%s）", plugin_id, kind, exc_info=True)


def _book_bound_to_running_game(lorebook: Any, book_id: str) -> bool:
    """这本书是否被正在进行的对局引用（game 域 binding）。"""
    try:
        for binding in lorebook.list_bindings(scope_kind="game") or []:
            if str(binding.get("book_id") or "") == str(book_id):
                return True
    except Exception:
        logger.warning("检查资料册对局引用失败，视为在用: %s", book_id, exc_info=True)
        return True
    return False


def _cleanup_plugin_books(
    lorebook: Any,
    receipt: ImportReceipt | None,
    plugin_id: str,
    result: dict[str, Any],
    detached: set[tuple[str, str]],
) -> None:
    """卸载时清理 autoimport 物化的 module 书（数据所有权仍是权威判据）。"""
    get_book = getattr(lorebook, "get_lorebook", None)
    if not callable(get_book):
        return
    if receipt is None:
        # 没有 receipt 就没有「包写入基线」：既不能证明书里的内容都是包写的，
        # 也不能证明用户没改过。整本删除有吃掉玩家数据的风险，一律保留。
        lister = getattr(lorebook, "list_lorebooks", None)
        if not callable(lister):
            return
        kept = sorted(
            str(book.get("id") or "")
            for book in (lister() or [])
            if str(book.get("source_kind") or "") == "module"
            and str(book.get("source_id") or "") == str(plugin_id)
        )
        if kept:
            logger.info(
                "插件 %s 没有导入 receipt，%d 本 module 资料册按用户数据保留",
                plugin_id, len(kept),
            )
            result["books_kept"].extend(kept)
        return
    book_ids = {
        str(item.get("id") or "")
        for item in receipt.created_objects
        if str(item.get("type") or "") == "lorebook_book"
        and ("lorebook_book", str(item.get("id") or "")) not in detached
    }
    for book_id in sorted(filter(None, book_ids)):
        book = get_book(book_id)
        if not isinstance(book, dict):
            continue
        # Receipt 是记账不是权威：现场书不再是 module:plugin_id 的就不动。
        if str(book.get("source_kind") or "") != "module" or str(book.get("source_id") or "") != str(plugin_id):
            continue
        imported = receipt.fingerprint("lorebook_book", book_id)
        if imported and imported != _book_fingerprint(lorebook, book_id):
            # 用户改过这本书（含只改关键词/可见性/检索设置）：detach 并保留。
            receipt.detach("lorebook_book", book_id)
            result["books_kept"].append(book_id)
            continue
        if _book_bound_to_running_game(lorebook, book_id):
            result["books_kept"].append(book_id)
            continue
        try:
            # delete_lorebook 级联删条目、binding 与派生 embedding。
            if lorebook.delete_lorebook(book_id):
                result["books_removed"] += 1
        except ValueError:
            # primary 世界书等受保护对象：保留并记录。
            result["books_kept"].append(book_id)


def _autoimport_plugin_book(
    dependencies: PluginContentDependencies,
    plugin_id: str,
    resource: dict[str, Any],
    target_world: str,
) -> None:
    """legacy_autoimport：一个声明的 lorebook 资源 = 用户书库里的一本书。

    整本物化（不是逐条 entry），带 ``module:plugin_id`` 来源标记与（包内有
    世界时的）world 域 runtime binding；receipt 记 book 级指纹供卸载时区分
    「包写的内容」与「用户改过的内容」。
    """

    lorebook = dependencies.store.lorebook
    if lorebook is None or not callable(getattr(lorebook, "create_lorebook", None)):
        return
    book_id = declared_module_book_id(plugin_id, resource)
    if _module_book_has_user_edits(dependencies, plugin_id, book_id):
        # 启用 / 更新 / 重启都会重放 autoimport：书里已有用户内容时宁可保留，
        # 不能用包里的旧条目覆盖（P1：重复导入不得静默吃掉玩家修改）。
        logger.info(
            "内容包 %s 的资料册 %s 已被用户修改，跳过重新导入", plugin_id, book_id,
        )
        return
    book_id = materialize_module_book(lorebook, plugin_id, resource, target_world=target_world)
    _record_import_receipt(dependencies, plugin_id, "lorebook_book", book_id)


def materialize_module_book_binding(
    dependencies: PluginContentDependencies,
    source_kind: str,
    source_id: str,
    book_id: str,
) -> bool:
    """创建事务里按需物化 module 来源的 book binding（issue #700 方案 B）。

    catalog 交付模式不启用即灌注；玩家在开局显式勾选模块资料册时，创建事务
    把该书按声明物化到用户书库后再校验。物化只认声明来源（module/plugin），
    其余来源（world/user/...）不猜、不建。
    """

    if str(source_kind or "").strip() not in {"module", "plugin"}:
        return False
    host = dependencies.plugin_host
    lorebook = dependencies.store.lorebook
    if not host or lorebook is None:
        return False
    get_book = getattr(lorebook, "get_lorebook", None)
    if callable(get_book) and get_book(str(book_id)) is not None:
        # 书已经在用户库里（可能被玩家改过）：开局只认它，不重写内容。
        return True
    resource = declared_module_book_resource(host, str(source_id), str(book_id))
    if not resource:
        return False
    materialize_module_book(lorebook, str(source_id), resource)
    # 按需物化的书同样登记 receipt：卸载时才有「包写入基线」判断用户是否改过。
    _record_import_receipt(dependencies, str(source_id), "lorebook_book", str(book_id))
    return True


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
        LegacyContentPackAdapter(
            sync_lorebooks=lambda: sync_plugin_lorebooks(dependencies.content),
            import_content=lambda value: _autoimport_plugin_content(
                dependencies.content, value,
            ),
        ).materialize(plugin_id)
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
        LegacyContentPackAdapter(
            sync_lorebooks=lambda: sync_plugin_lorebooks(dependencies.content),
            import_content=lambda value: _autoimport_plugin_content(
                dependencies.content, value,
            ),
        ).materialize(plugin_id)
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
        LegacyContentPackAdapter(
            sync_lorebooks=lambda: sync_plugin_lorebooks(dependencies.content),
            import_content=lambda value: _autoimport_plugin_content(
                dependencies.content, value,
            ),
        ).sync()
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
    dependencies: PluginLifecycleDependencies,
    plugin_id: str,
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    detail = await host.update_from_marketplace(plugin_id)
    # Same follow-up as a marketplace (over)install: an enabled legacy content
    # pack gets the new version's content right away, not after a restart.
    if not detail.get("up_to_date"):
        _maybe_autoimport_after_install(dependencies, plugin_id)
    return {"ok": True, **detail}

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
    *,
    target_book_id: str = "",
) -> dict[str, Any]:
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    kind = (kind or "").strip()
    resource_id = (resource_id or "").strip()
    plugin_id = (plugin_id or "").strip()
    target_world_id = (target_world_id or "").strip()
    target_book_id = (target_book_id or "").strip()
    resource = host.get_content_resource(kind, resource_id, plugin_id=plugin_id)
    if not resource:
        return {"ok": False, "error": "插件内容不存在或未启用"}
    try:
        import_target = CONTENT_KIND_REGISTRY.spec(kind).import_target
    except ContentRefError:
        return {
            "ok": False,
            "error": "该内容类型不支持导入",
            "error_code": "content_kind_not_importable",
        }
    resource = _materialize_content_portrait(dependencies.portraits, resource)
    if import_target == "character_card":
        card = _content_to_character_card(resource)
        result = dependencies.store.save_character_card(card)
        if result.get("ok"):
            result["imported_as"] = "character_card"
            result["source_plugin_id"] = resource.get("plugin_id", "")
            _record_import_receipt(
                dependencies,
                str(resource.get("plugin_id") or plugin_id),
                "character_card",
                str(card.get("id") or ""),
            )
        return result

    if import_target != "lorebook_entry":
        return {
            "ok": False,
            "error": "该内容类型仅支持目录引用，不能复制到世界书",
            "error_code": "content_kind_catalog_only",
        }

    lorebook = dependencies.store.lorebook
    if target_book_id:
        if not lorebook or not lorebook.get_lorebook(target_book_id):
            return {"ok": False, "error": "目标世界书不存在", "error_code": "book_not_found"}
        world_projection = getattr(lorebook, "bound_world_projection_for_book", None)
        target_world_id = (
            str(world_projection(target_book_id) or "")
            if callable(world_projection) else ""
        )
    elif not target_world_id:
        return {"ok": False, "error": "请选择要导入到的世界书"}
    if not lorebook or not lorebook.get_world(target_world_id):
        # A standalone canonical Book intentionally has no world projection.
        if not target_book_id:
            return {"ok": False, "error": "目标世界书不存在"}
    entry = _content_to_lore_entry(
        resource, kind, target_world_id, book_id=target_book_id,
    )
    if lorebook is not None and lorebook.get_entry(entry["id"]) and not overwrite:
        entry["id"] = f"{entry['id']}_{int(time.time() * 1000)}"
    result = (
        _save_plugin_book_entry(dependencies, target_book_id, entry)
        if target_book_id else dependencies.store.save_entry(entry)
    )
    if result.get("ok"):
        result["imported_as"] = "lorebook_entry"
        result["entry"] = entry
        result["source_plugin_id"] = resource.get("plugin_id", "")
        _record_import_receipt(
            dependencies,
            str(resource.get("plugin_id") or plugin_id),
            "lorebook_entry",
            entry["id"],
        )
    return result


def import_all_plugin_content(
    dependencies: PluginContentDependencies,
    plugin_id: str,
    target_world_id: str = "",
    *,
    target_book_id: str = "",
) -> dict[str, Any]:
    """一键导入插件全部内容：角色卡→卡库，NPC/道具/魔法/职业→指定世界书。"""
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    plugin_id = (plugin_id or "").strip()
    target_world_id = (target_world_id or "").strip()
    target_book_id = (target_book_id or "").strip()
    lorebook = dependencies.store.lorebook
    if target_book_id:
        if not lorebook or not lorebook.get_lorebook(target_book_id):
            return {"ok": False, "error": "目标世界书不存在", "error_code": "book_not_found"}
        world_projection = getattr(lorebook, "bound_world_projection_for_book", None)
        target_world_id = (
            str(world_projection(target_book_id) or "")
            if callable(world_projection) else ""
        )
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
                import_target = CONTENT_KIND_REGISTRY.spec(kind).import_target
                if import_target == "character_card":
                    card = _content_to_character_card(resource)
                    result = dependencies.store.save_character_card(card)
                    if result.get("ok"):
                        imported.append({"kind": kind, "name": _content_name(resource), "as": "character_card"})
                        _record_import_receipt(
                            dependencies, plugin_id, "character_card", str(card.get("id") or ""),
                        )
                    else:
                        errors.append({"kind": kind, "name": _content_name(resource), "error": result.get("error", "")})
                elif import_target == "lorebook_entry":
                    if not target_book_id and not target_world_id:
                        skipped.append({"kind": kind, "name": _content_name(resource), "reason": "未选择世界书"})
                        continue
                    lorebook = dependencies.store.lorebook
                    if not target_book_id and (not lorebook or not lorebook.get_world(target_world_id)):
                        return {"ok": False, "error": "目标世界书不存在"}
                    if lorebook is None:
                        return {"ok": False, "error": "目标世界书不存在"}
                    entry = _content_to_lore_entry(
                        resource, kind, target_world_id, book_id=target_book_id,
                    )
                    existed = bool(lorebook and lorebook.get_entry(entry["id"]))
                    if existed:
                        # 幂等：已存在则更新，不创建时间戳副本（避免重复导入产生重复条目）
                        if target_book_id:
                            result = _save_plugin_book_entry(
                                dependencies, target_book_id, entry,
                            )
                        else:
                            lorebook.update_entry(entry["id"], entry)
                            result = {"ok": True}
                    else:
                        result = (
                            _save_plugin_book_entry(dependencies, target_book_id, entry)
                            if target_book_id else dependencies.store.save_entry(entry)
                        )
                    if result.get("ok"):
                        imported.append({"kind": kind, "name": _content_name(resource), "as": "lorebook_entry"})
                        _record_import_receipt(
                            dependencies,
                            plugin_id,
                            "lorebook_entry",
                            entry["id"],
                            updated=existed,
                        )
                    else:
                        errors.append({"kind": kind, "name": _content_name(resource), "error": result.get("error", "")})
                else:
                    skipped.append({
                        "kind": kind,
                        "name": _content_name(resource),
                        "reason": "目录资源不支持复制导入",
                    })
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


def _save_plugin_book_entry(
    dependencies: PluginContentDependencies,
    book_id: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Write plugin content through the canonical Book boundary."""

    writer = dependencies.store.save_book_entry
    if callable(writer):
        return writer(str(book_id), entry)
    lorebook = dependencies.store.lorebook
    if lorebook is None:
        return {"ok": False, "error": "世界书存储不可用"}
    add_book_entry = getattr(lorebook, "add_book_entry", None)
    if callable(add_book_entry):
        add_book_entry(str(book_id), entry)
        return {"ok": True}
    return {"ok": False, "error": "目标世界书不支持规范导入", "error_code": "book_writer_unavailable"}


def _content_to_lore_entry(
    resource: dict[str, Any],
    kind: str,
    world_id: str,
    *,
    book_id: str = "",
) -> dict[str, Any]:
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
    owner_id = str(book_id or world_id)
    entry = {
        "id": f"{owner_id}_plugin_{safe_id_part(kind)}_{safe_id_part(plugin_id)}_{safe_id_part(resource_id)}",
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
    if book_id:
        entry["book_id"] = str(book_id)
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
