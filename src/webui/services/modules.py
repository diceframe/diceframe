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

from typing import Any

from src.plugin_host.support import content_delivery_mode, content_profile

_MODULE_CONTENT_KINDS = (
    "npc", "item", "spell", "class", "character_template", "world_template",
)


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


def list_modules(deps: Any) -> dict[str, Any]:
    """模块库列表（母方案 §51：已安装；在线/本地导入由 marketplace 提供）。"""

    modules: list[dict[str, Any]] = []
    for runtime in _installed_content_packs(deps.plugin_host):
        plugin_id = str(runtime.manifest.get("id") or "")
        profile = content_profile(runtime.manifest)
        counts = _contribution_counts(deps.plugin_host.contributions, plugin_id)
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
        })
    return {"ok": True, "modules": modules}


def module_detail(deps: Any, module_id: str) -> dict[str, Any]:
    """单个模组详情：概览 + 内容分组计数 + 冒险清单（只读）。"""

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None or str(runtime.manifest.get("plugin_type") or "") != "content-pack":
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}
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
    adventures: list[dict[str, Any]] = []
    source = (
        deps.adventure_registry.source_for("plugin", plugin_id)
        if deps.adventure_registry is not None
        else None
    )
    if source is not None:
        try:
            for bundle in source.loader.list(""):
                adventures.append({
                    "adventure_id": bundle.manifest.adventure_id,
                    "version": bundle.manifest.version,
                    "format": bundle.manifest.format,
                    "directory_id": bundle.root.name,
                })
        except Exception:  # noqa: BLE001 - 坏包不拖垮详情页
            adventures = []
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
        },
    }


def module_content(
    deps: Any, module_id: str, kind: str, key: str, *, language: str = "",
) -> dict[str, Any]:
    """内容库只读详情；委托 PluginContentCatalog（不新建存储）。"""

    catalog = getattr(deps.plugin_host, "content", None)
    if catalog is None:
        return {"ok": False, "error_code": "CONTENT_UNAVAILABLE"}
    resource = catalog.get_content_resource(kind, key, plugin_id=str(module_id or ""), language=language)
    if resource is None:
        return {"ok": False, "error_code": "CONTENT_NOT_FOUND"}
    return {"ok": True, "content": resource}


__all__ = ["list_modules", "module_content", "module_detail"]
