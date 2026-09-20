"""Content Module（模组）统一只读 facade（MOD-04，母方案 §50/§52/§108）。

普通用户的"模组 / 内容库（Compendium）"是产品层抽象；技术实现完全复用
既有 PluginHost 组件（PluginRuntime / ContributionRegistry /
PluginContentCatalog / AdventureSourceRegistry），**不新建数据库、不新建
lifecycle**。本模块只做三件事：

- ``list_modules``：已安装的 content-pack（含传统包与 adventure-module 模组）
  的模块卡片数据（母方案 §52：名称 / 版本 / 规则目标 / 冒险数 / 内容数 /
  状态 / 来源）；
- ``module_detail``：单个模组的概览 + 内容分组 + 冒险清单（§53 Tabs 的数据源）;
- ``module_content``：内容库只读详情（委托 ``PluginContentCatalog.get_content_resource``）。

边界：

- 只读：不安装、不启停、不改文件（生命周期在 LIFE 组，走既有 PluginHost API）。
- 不把插件类型混淆：provider / tool / bot-extension 等不进入模块库（§51）。
- 性能（母方案 §165）：列表只返回元数据计数，不读取全部 JSON body。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from src.plugin_host.support import (
    content_delivery_mode,
    content_profile,
)
from src.version import version_below
from src.webui.services.module_validation import (
    MODULE_PLUGIN_TYPE,
    ModulePackageValidation,
    parse_requires,
    validate_module_package,
)

_MODULE_CONTENT_KINDS = (
    "npc", "item", "spell", "class", "character_template", "world_template",
)


@dataclass(frozen=True)
class ModuleDependencies:
    """Explicit read-side dependencies for the module catalogue facade."""

    plugin_host: Any | None
    adventure_registry: Any | None
    lorebook_store: Any | None = None
    ruleset_registry: Any | None = None
    list_instances: Callable[[], list[Any]] | None = None
    # FIX-01 §3.7：保护目标是"所有持久化存档"，不能只看内存 active GameInstance。
    # ``list_save_metadata`` 扫描存档目录，覆盖 paused / ended / 加载失败但元数据
    # 仍可读的对局；``refresh_adventure_sources`` 对应 §3.6——任何 destructive
    # module action 前先刷新来源注册表，不依赖"碰巧同步过"。
    list_save_metadata: Callable[[], list[dict[str, Any]]] | None = None
    refresh_adventure_sources: Callable[[], None] | None = None
    # 组合根声明的默认 runtime：模块声明了 ruleset_catalogs 但没写 requires 时，
    # 用它判断 catalog 契约归属（与 Adventure 绑定同一默认，不猜字段）。
    default_runtime_requirement: Callable[[], dict[str, Any]] | None = None
    # FIX-06 §8：ModulesView 的"在线模组"区块复用既有插件市场索引（不新建市场）。
    # 组合根注入 ``PluginHost.marketplace_plugins``（已带 installed / 版本信息）。
    list_marketplace_plugins: Callable[[], Awaitable[dict[str, Any]]] | None = None


def _installed_content_packs(plugin_host: Any) -> list[Any]:
    runtimes = getattr(plugin_host, "plugins", {}) or {}
    return sorted(
        (
            runtime
            for runtime in runtimes.values()
            if str(runtime.manifest.get("plugin_type") or "") == "content-pack"
        ),
        key=lambda runtime: str(runtime.manifest.get("id") or ""),
    )


def _adventure_count(adventure_registry: Any, plugin_id: str) -> int:
    if adventure_registry is None:
        return 0
    source = adventure_registry.source_for("plugin", plugin_id)
    if source is None:
        return 0
    try:
        return len(source.loader.list(""))
    except Exception:  # noqa: BLE001 - 诊断列表绝不因单个坏包失败
        return 0


def _contribution_counts(contributions: Any, plugin_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in contributions.list():
        if item.plugin_id != plugin_id:
            continue
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return counts


def _module_lorebooks(deps: ModuleDependencies, module_id: str) -> list[dict[str, Any]]:
    """Read canonical Lorebook books owned by a module source.

    Legacy world lore is deliberately excluded.  A module only appears here
    after an explicit canonical import/binding with ``source_kind=module`` and
    ``source_id=<module id>``; listing modules must not materialize world lore.
    """

    store = getattr(deps, "lorebook_store", None)
    lister = getattr(store, "list_lorebooks", None)
    if not callable(lister):
        return []
    try:
        books = lister() or []
    except Exception:  # noqa: BLE001 - a broken optional source must not hide modules
        return []
    return [
        {
            "id": str(book.get("id") or ""),
            "name": str(book.get("name") or book.get("id") or ""),
            "description": str(book.get("description") or ""),
            "language": str(book.get("language") or "zh-CN"),
            "enabled": bool(book.get("enabled", True)),
            "source_kind": "module",
            "source_id": str(module_id),
        }
        for book in books
        if str(book.get("source_kind") or "") == "module"
        and str(book.get("source_id") or "") == str(module_id)
    ]


def list_modules(deps: ModuleDependencies) -> dict[str, Any]:
    """模块库列表（母方案 §51：已安装；在线/本地导入由 marketplace 提供）。"""

    modules: list[dict[str, Any]] = []
    for runtime in _installed_content_packs(deps.plugin_host):
        plugin_id = str(runtime.manifest.get("id") or "")
        profile = content_profile(runtime.manifest)
        counts = _contribution_counts(deps.plugin_host.contributions, plugin_id)
        lorebooks = _module_lorebooks(deps, plugin_id)
        modules.append({
            "id": plugin_id,
            "name": str(runtime.manifest.get("name") or plugin_id),
            "version": str(runtime.manifest.get("version") or ""),
            "plugin_type": str(runtime.manifest.get("plugin_type") or ""),
            "content_profile": profile,
            "content_delivery_mode": content_delivery_mode(runtime.manifest),
            "is_module": profile == "adventure-module",
            "status": str(runtime.status or ""),
            "adventure_count": _adventure_count(deps.adventure_registry, plugin_id),
            "content_counts": counts,
            "lorebook_count": len(lorebooks),
        })
    return {"ok": True, "modules": modules}


def _refresh_adventure_sources(deps: ModuleDependencies) -> None:
    """Best-effort refresh of the plugin adventure-source registry (FIX-01 §3.6).

    读模型与 guard 都不能依赖"碰巧同步过 registry"（server restart / 刚启用 /
    刚安装都会让来源集合过期）。刷新失败不影响只读结果。
    """

    refresh = getattr(deps, "refresh_adventure_sources", None)
    if not callable(refresh):
        return
    try:
        refresh()
    except Exception:  # noqa: BLE001 - 刷新失败不拖垮只读目录
        pass


async def module_marketplace(
    deps: ModuleDependencies, *, keyword: str = "",
) -> dict[str, Any]:
    """在线模组库（FIX-06 §8：ModulesView 的 Online / Marketplace 区块）。

    复用既有插件市场索引（``PluginHost.marketplace_plugins``，不新建市场、不新建
    索引），只保留 data-only ``content-pack``：模块库不是"另一个插件列表"，把
    provider / tool 混进来会让用户以为它们能当模组装。

    ``installed`` / ``installed_version`` 由宿主给出（服务端事实），
    ``update_available`` 用既有 ``version_below`` 做展示级比较；市场不可达时返回
    ``ok=False``，UI 显示离线提示而不是空列表（空列表会被读成"没有模组"）。
    """

    lister = getattr(deps, "list_marketplace_plugins", None)
    if not callable(lister):
        return {
            "ok": False, "error_code": "MODULE_MARKETPLACE_UNAVAILABLE",
            "error": "marketplace is not wired", "modules": [],
        }
    try:
        listing = await lister()
    except Exception as exc:  # noqa: BLE001 - 市场故障不能变成 500
        return {
            "ok": False, "error_code": "MODULE_MARKETPLACE_UNAVAILABLE",
            "error": str(exc), "modules": [],
        }
    if not listing.get("ok"):
        return {
            "ok": False, "error_code": "MODULE_MARKETPLACE_UNAVAILABLE",
            "error": str(listing.get("error") or ""), "modules": [],
        }
    needle = str(keyword or "").strip().lower()
    modules: list[dict[str, Any]] = []
    for item in listing.get("plugins") or []:
        if str(item.get("plugin_type") or "") != MODULE_PLUGIN_TYPE:
            continue
        if needle and needle not in _market_search_text(item):
            continue
        installed_version = str(item.get("installed_version") or "")
        latest = item.get("latest") if isinstance(item.get("latest"), dict) else {}
        latest_version = str(latest.get("version") or item.get("version") or "")
        modules.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "version": str(item.get("version") or ""),
            "latest_version": latest_version,
            "description": str(item.get("description") or ""),
            "content_profile": str(item.get("content_profile") or ""),
            "content_delivery_mode": str(item.get("content_delivery_mode") or ""),
            "adventure_count": int(item.get("adventure_count") or 0),
            "ruleset_targets": list(item.get("ruleset_targets") or []),
            "languages": list(item.get("languages") or []),
            "tags": list(item.get("tags") or []),
            "trust_level": str(item.get("trust_level") or ""),
            "distribution": str(item.get("distribution") or ""),
            "repository_url": str(item.get("repository_url") or ""),
            "release_url": str(item.get("release_url") or ""),
            "stars": int(item.get("stars") or 0),
            "installed": bool(item.get("installed")),
            "installed_version": installed_version,
            "update_available": bool(
                installed_version and latest_version
                # version_below(minimum, current)：已装版本是否**低于**市场最新版本。
                and version_below(latest_version, installed_version)
            ),
            "installable": bool(item.get("installable", True)),
            "verification_error": str(item.get("verification_error") or ""),
            "needs_core_update": bool(item.get("needs_core_update")),
            "min_app_version": str(item.get("min_app_version") or ""),
        })
    return {
        "ok": True,
        "modules": modules,
        "total": len(modules),
        "source": listing.get("source") or {},
    }


def _market_search_text(item: dict[str, Any]) -> str:
    parts = [str(item.get("id") or ""), str(item.get("name") or "")]
    parts.extend(str(tag) for tag in (item.get("tags") or []))
    parts.extend(str(target) for target in (item.get("ruleset_targets") or []))
    return " ".join(parts).lower()


def module_detail(deps: ModuleDependencies, module_id: str) -> dict[str, Any]:
    """单个模组详情：概览 + 内容分组计数 + 冒险清单 + 使用存档/按钮 guard。"""

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None or str(runtime.manifest.get("plugin_type") or "") != MODULE_PLUGIN_TYPE:
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}
    # FIX-06 §8：详情页的"使用中的存档"与 update/disable/uninstall 按钮状态都来自
    # 服务端 guard 结果（同一绑定存档判定，且顺带刷新来源注册表），前端不自行推断。
    guard = module_action_guard(deps, module_id)
    bound_games = list(guard.get("bound_games") or [])
    actions = guard.get("actions") or {}
    plugin_id = str(runtime.manifest.get("id") or "")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in deps.plugin_host.contributions.list():
        if item.plugin_id != plugin_id:
            continue
        grouped.setdefault(item.kind, []).append({
            "key": item.key,
            "title": item.title,
            "description": item.description,
        })
    adventures = _module_adventure_rows(deps, module_id)
    lorebooks = _module_lorebooks(deps, module_id)
    return {
        "ok": True,
        "module": {
            "id": plugin_id,
            "name": str(runtime.manifest.get("name") or plugin_id),
            "version": str(runtime.manifest.get("version") or ""),
            "content_profile": content_profile(runtime.manifest),
            "content_delivery_mode": content_delivery_mode(runtime.manifest),
            "status": str(runtime.status or ""),
            "content_counts": _contribution_counts(deps.plugin_host.contributions, plugin_id),
            "content": grouped,
            "adventures": adventures,
            "lorebooks": lorebooks,
            "bound_games": bound_games,
            "actions": actions,
        },
    }


def module_lorebooks(deps: ModuleDependencies, module_id: str) -> dict[str, Any]:
    """List canonical Lorebook sources owned by one installed module."""

    detail = module_detail(deps, module_id)
    if not detail.get("ok"):
        return detail
    return {
        "ok": True,
        "module_id": str(module_id),
        "lorebooks": list(detail["module"].get("lorebooks") or []),
    }


def module_adventures(deps: ModuleDependencies, module_id: str) -> dict[str, Any]:
    """Dedicated adventure-list read model for one module (API-02)."""

    detail = module_detail(deps, module_id)
    if not detail.get("ok"):
        return detail
    module = detail["module"]
    return {
        "ok": True,
        "module_id": module["id"],
        "adventures": module["adventures"],
    }


def module_content(
    deps: ModuleDependencies, module_id: str, kind: str, key: str, *, language: str = "",
) -> dict[str, Any]:
    """内容库只读详情；委托 PluginContentCatalog（不新建存储）。"""

    catalog = getattr(deps.plugin_host, "content", None)
    if catalog is None:
        return {"ok": False, "error_code": "CONTENT_UNAVAILABLE"}
    resource = catalog.get_content_resource(kind, key, plugin_id=str(module_id or ""), language=language)
    if resource is None:
        return {"ok": False, "error_code": "CONTENT_NOT_FOUND"}
    return {"ok": True, "content": resource}


# ---- LIFE-00：安装前兼容性预览（母方案 §33/§123）--------------------------
#
# FIX-01 §3.3：预览与安装共用同一套校验（src.webui.services.module_validation），
# 不再各自维护一份规则。这里只保留 facade 形状。


def preview_module_install(
    deps: ModuleDependencies,
    manifest: dict[str, Any],
    *,
    directory: Any | None = None,
    require_content_pack: bool = True,
) -> dict[str, Any]:
    """安装前兼容性预览：blockers（阻断）与 warnings（警告）分离（§57）。

    ``directory`` 给出已解压的包目录时同时做深度校验（Adventure 包真装载 /
    ruleset catalog 真装载）；模块安装面默认要求 content-pack。
    """

    validation = validate_module_package(
        manifest,
        deps=deps,
        directory=directory,
        require_content_pack=require_content_pack,
    )
    return validation.preview()


def validate_module_directory(
    deps: ModuleDependencies,
    directory: Any,
    manifest: dict[str, Any],
    *,
    require_content_pack: bool = True,
) -> ModulePackageValidation:
    """Validate one extracted package for the module install surface (raises)."""

    from src.webui.services.module_validation import validate_module_install

    return validate_module_install(
        directory,
        manifest,
        deps=deps,
        require_content_pack=require_content_pack,
    )


def module_compatibility(deps: ModuleDependencies, module_id: str) -> dict[str, Any]:
    """Preview the installed module's declared runtime compatibility."""

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None or str(runtime.manifest.get("plugin_type") or "") != MODULE_PLUGIN_TYPE:
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}
    result = preview_module_install(deps, runtime.manifest)
    return {**result, "module_id": str(runtime.manifest.get("id") or "")}


# ---- LIFE-01：绑定存档保护（母方案 §35/§36/§37/§124）------------------------

# 受保护操作 → 绑定存档存在时是否阻断。更新/禁用/卸载/覆盖安装一律默认 block；
# 母方案 §35：更新改变 bound adventure digest → BLOCK。
# FIX-01 §3.5：``overwrite``（本地覆盖安装 / 市场覆盖安装 / 后台自动更新）也是
# 改 package bytes 的破坏性操作，必须与 update 同等受保护。
PROTECTED_MODULE_ACTIONS = ("uninstall", "disable", "update", "overwrite", "stop")

# FIX-06 §8：模块详情页会呈现给用户的受保护操作（``stop`` 是 ``disable`` 在
# 插件控制路由里的内部别名，不作为独立按钮暴露）。
MODULE_ACTIONS = ("update", "disable", "uninstall", "overwrite")


def _module_adventure_rows(deps: ModuleDependencies, module_id: str) -> list[dict[str, Any]]:
    """The adventure packages a module declares, as read-model rows.

    FIX-01 §3.6 / FIX-06 §8：**不依赖"碰巧同步过 registry"**（否则 server restart
    之后详情页的 Adventures 段永远是空的）。来源优先级：

    1. runtime 自己声明的 declared-only 包目录（安装后即存在，``discover()`` 重建，
       且与 enabled/disabled 状态无关）；
    2. 退回到来源注册表（plugin 来源在禁用时会被同步移除，因此只作兜底）。
    """

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None:
        return []
    plugin_id = str(runtime.manifest.get("id") or "")
    bundles: list[Any] | None = None
    root = getattr(runtime, "adventure_packages_root", None)
    directories = tuple(getattr(runtime, "adventure_package_directories", ()) or ())
    if root is not None and directories:
        from src.adventures import AdventureBundleLoader

        try:
            loader = AdventureBundleLoader(root, allowed_directory_ids=directories)
            bundles = list(loader.list(""))
        except Exception:  # noqa: BLE001 - 坏包不拖垮详情页
            bundles = []
    if bundles is None:
        registry = deps.adventure_registry
        source = (
            registry.source_for("plugin", plugin_id) if registry is not None else None
        )
        if source is None:
            return []
        try:
            bundles = list(source.loader.list(""))
        except Exception:  # noqa: BLE001 - 坏包不拖垮详情页
            return []
    return [
        {
            "adventure_id": bundle.manifest.adventure_id,
            "version": bundle.manifest.version,
            "format": bundle.manifest.format,
            "directory_id": bundle.root.name,
        }
        for bundle in bundles
    ]


def module_adventure_ids(deps: ModuleDependencies, module_id: str) -> set[str]:
    """The adventure ids a module package declares (protection target)."""

    return {
        str(row["adventure_id"])
        for row in _module_adventure_rows(deps, module_id)
        if row["adventure_id"]
    }


def _active_bindings(deps: ModuleDependencies) -> list[dict[str, Any]]:
    """Bindings from in-memory instances (active / paused / ended)."""

    rows: list[dict[str, Any]] = []
    # 可选依赖：缺失/未接线的读模型按"没有内存实例"处理，不抛（与 §3.6/§3.7 一致）。
    lister = getattr(deps, "list_instances", None)
    for instance in (lister or (lambda: []))():
        binding = getattr(instance, "adventure_binding", {}) or {}
        rows.append({
            "game_key": "|".join(str(part) for part in instance.game_key),
            "adventure_id": str(binding.get("adventure_id") or ""),
            "run_id": str(instance.run_id or ""),
            "content_digest": str(binding.get("content_digest") or ""),
            "source_kind": str(binding.get("source_kind") or ""),
            "source_id": str(binding.get("source_id") or ""),
            "state": str(getattr(getattr(instance, "state", ""), "value", "") or ""),
            "metadata_readable": True,
        })
    return rows


def _persisted_bindings(deps: ModuleDependencies) -> list[dict[str, Any]]:
    """Bindings from persisted saves (FIX-01 §3.7).

    扫描存档目录而不是内存 registry，因此重启后、ENDED 对局、以及加载/恢复失败
    但元数据仍可读的存档同样受保护。
    """

    scanner = getattr(deps, "list_save_metadata", None)
    if not callable(scanner):
        return []
    rows: list[dict[str, Any]] = []
    try:
        entries = scanner() or []
    except Exception:  # noqa: BLE001 - 扫描失败不拖垮只读目录
        return []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rows.append({
            "game_key": str(entry.get("game_key") or ""),
            "adventure_id": str(entry.get("adventure_id") or ""),
            "run_id": str(entry.get("run_id") or ""),
            "content_digest": str(entry.get("content_digest") or ""),
            "source_kind": str(entry.get("source_kind") or ""),
            "source_id": str(entry.get("source_id") or ""),
            "state": str(entry.get("state") or ""),
            "metadata_readable": bool(entry.get("metadata_readable", False)),
        })
    return rows


def module_bound_rows(
    deps: ModuleDependencies,
    module_id: str,
) -> list[dict[str, Any]]:
    """Every save (memory + persisted) bound to one module, deduped by game key."""

    adventure_ids = module_adventure_ids(deps, module_id)
    if not adventure_ids:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for row in (*_active_bindings(deps), *_persisted_bindings(deps)):
        if row["adventure_id"] not in adventure_ids:
            continue
        # Source-aware plugin bindings protect only their owning module.  A
        # legacy id-only save remains conservatively protected by every module
        # that declares that id because its origin cannot be proven.
        if row.get("source_kind") == "plugin" and row.get("source_id") != module_id:
            continue
        key = row["game_key"]
        current = merged.get(key)
        # 内存实例更权威（有 state / 最新 run_id）；缺失时用存档元数据补齐。
        if current is None or not current.get("state"):
            merged[key] = {**current, **row} if current else row
    return sorted(merged.values(), key=lambda item: item["game_key"])


def module_bound_games(
    deps: ModuleDependencies,
    module_id: str,
) -> list[dict[str, Any]]:
    """List the games bound to one module's adventures（§35 UI 数据源）。"""

    return [
        {
            "game_key": row["game_key"],
            "adventure_id": row["adventure_id"],
            "run_id": row["run_id"],
        }
        for row in module_bound_rows(deps, module_id)
    ]


def _module_bound_games_for_guard(
    deps: ModuleDependencies, module_id: str,
) -> list[dict[str, Any]]:
    """Bound saves blocking a protected action — the one guard computation.

    FIX-01 §3.6：guard 前先刷新 module/adventure 来源注册表，避免
    "server restart → registry 为空 → destructive action fail-open"。
    FIX-06 §8：UI 的按钮状态与 enforce 路径共用本函数，两者不可能给出不同结论。
    """

    refresh = getattr(deps, "refresh_adventure_sources", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:  # noqa: BLE001 - 刷新失败不得让 guard 变成 fail-open
            pass
    return module_bound_games(deps, module_id)


def assert_module_action_allowed(deps: ModuleDependencies, module_id: str, action: str) -> None:
    """Guard for uninstall/disable/update/overwrite/stop（母方案 §124：默认 block）。

    绑定存档存在时抛 :class:`ModuleInUse`；调用方（插件生命周期 API / 插件宿主）
    把它转成结构化错误，UI 展示"哪些存档正在使用"。
    """

    if action not in PROTECTED_MODULE_ACTIONS:
        return
    bound = _module_bound_games_for_guard(deps, module_id)
    if bound:
        raise ModuleInUse(module_id, action, bound)


def module_action_guard(deps: ModuleDependencies, module_id: str) -> dict[str, Any]:
    """Per-action server verdict for the module detail buttons（FIX-06 §8）。

    按钮状态必须来自 server guard 结果，而不是前端猜：这里用与
    :func:`assert_module_action_allowed` 完全相同的绑定存档判定，一次性给出每个
    受保护 action 的 ``allowed`` / ``reason`` / ``games``。用户点击时服务端仍会
    再判一次（同一函数），所以这只是"提前显示结论"，不是放行。
    """

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None or str(runtime.manifest.get("plugin_type") or "") != MODULE_PLUGIN_TYPE:
        return {"ok": False, "error_code": "MODULE_NOT_FOUND", "actions": {}}
    bound = _module_bound_games_for_guard(deps, module_id)
    actions: dict[str, dict[str, Any]] = {}
    for action in MODULE_ACTIONS:
        blocked = bool(bound)
        actions[action] = {
            "allowed": not blocked,
            "reason": "MODULE_IN_USE" if blocked else "",
            "games": list(bound) if blocked else [],
        }
    return {
        "ok": True,
        "module_id": str(runtime.manifest.get("id") or ""),
        "actions": actions,
        "bound_games": list(bound),
    }


class ModuleInUse(ValueError):
    """The module is bound by games; the protected action must not proceed."""

    def __init__(self, module_id: str, action: str, games: list[dict[str, Any]]) -> None:
        self.module_id = str(module_id)
        self.action = str(action)
        self.games = list(games)
        super().__init__(
            f"module {self.module_id!r} is used by {len(self.games)} game(s); "
            f"{self.action} blocked"
        )




# ---- LIFE-02：Module Usage Index（母方案 §125/§169）-------------------------


def module_usages(deps: ModuleDependencies, module_id: str) -> dict[str, Any]:
    """模块使用索引：哪个模块 / 哪个冒险被哪些存档绑定（只读聚合）。

    FIX-01 §3.7：与 bound-save guard 同源——内存实例 + 持久化存档元数据。
    """

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None:
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}
    plugin_id = str(runtime.manifest.get("id") or "")
    adventure_ids = module_adventure_ids(deps, module_id)
    by_adventure: dict[str, list[dict[str, Any]]] = {
        adventure_id: [] for adventure_id in sorted(adventure_ids)
    }
    for row in module_bound_rows(deps, module_id):
        adventure_id = row["adventure_id"]
        if adventure_id in by_adventure:
            by_adventure[adventure_id].append({
                "game_key": row["game_key"],
                "run_id": row["run_id"],
                "content_digest": row["content_digest"],
                "state": row["state"],
                "metadata_readable": row["metadata_readable"],
            })
    usages = [
        {"adventure_id": adventure_id, "games": games}
        for adventure_id, games in by_adventure.items()
    ]
    total = sum(len(item["games"]) for item in usages)
    return {"ok": True, "module_id": plugin_id, "usages": usages, "total_games": total}


__all__ = [
    "MODULE_ACTIONS",
    "ModuleDependencies",
    "ModuleInUse",
    "PROTECTED_MODULE_ACTIONS",
    "assert_module_action_allowed",
    "list_modules",
    "module_action_guard",
    "module_adventures",
    "module_adventure_ids",
    "module_bound_games",
    "module_bound_rows",
    "module_content",
    "module_compatibility",
    "module_detail",
    "module_marketplace",
    "module_usages",
    "parse_requires",
    "preview_module_install",
    "validate_module_directory",
]
