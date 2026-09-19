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

from src.plugin_host.support import (
    content_delivery_mode,
    content_profile,
    validate_content_module_profile,
)

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


# ---- LIFE-00：安装前兼容性预览（母方案 §33/§123）--------------------------

_PREVIEW_SUPPORTED_FORMATS = ("diceframe:adventure-graph-v1", "diceframe:adventure-graph-v2")


def parse_requires(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse manifest ``requires.rulesets``（声明目标 runtime 与最低版本）。

    结构：``{"rulesets": [{"id": "core:dnd2024", "minimum_version": 2}]}``；
    缺省 = 无 runtime 要求（传统 content-pack）。
    """

    requires = manifest.get("requires")
    if requires is None:
        return []
    if not isinstance(requires, dict):
        raise ValueError("requires 必须是对象")
    rulesets = requires.get("rulesets")
    if rulesets is None:
        return []
    if not isinstance(rulesets, list):
        raise ValueError("requires.rulesets 必须是数组")
    parsed: list[dict[str, Any]] = []
    for entry in rulesets:
        if not isinstance(entry, dict):
            raise ValueError("requires.rulesets entry 必须是对象")
        extra = sorted(set(entry) - {"id", "minimum_version"})
        if extra:
            raise ValueError(f"requires.rulesets entry has unknown field: {extra[0]!r}")
        runtime_id = str(entry.get("id") or "").strip()
        if not runtime_id:
            raise ValueError("requires.rulesets entry id is required")
        minimum = entry.get("minimum_version", 1)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ValueError("requires.rulesets minimum_version must be a positive integer")
        parsed.append({"id": runtime_id, "minimum_version": minimum})
    return parsed


def preview_module_install(deps: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    """安装前兼容性预览：blockers（阻断）与 warnings（警告）分离（§57）。"""

    blockers: list[str] = []
    warnings: list[str] = []
    profile = content_profile(manifest)
    delivery = content_delivery_mode(manifest)
    try:
        validate_content_module_profile(manifest)
        requires = parse_requires(manifest)
    except ValueError as exc:
        return {"ok": True, "blockers": [str(exc)], "warnings": list(warnings)}

    for requirement in requires:
        runtime_id = str(requirement["id"])
        minimum = int(requirement["minimum_version"])
        try:
            runtime = deps.ruleset_registry.get(runtime_id, minimum_version=minimum)
        except Exception:  # noqa: BLE001 - registry 对未知/过旧 runtime 抛错
            runtime = None
        if runtime is None:
            blockers.append(f"ruleset_runtime_missing:{runtime_id}>={minimum}")

    format_id = str(manifest.get("format") or "")
    if format_id and format_id not in _PREVIEW_SUPPORTED_FORMATS:
        blockers.append(f"adventure_format_unsupported:{format_id}")

    if profile == "adventure-module" and not manifest.get("adventure_packages"):
        warnings.append("adventure_module_without_adventures")
    if delivery == "catalog":
        warnings.append("catalog_mode_content_not_autoloaded")

    return {
        "ok": True,
        "content_profile": profile,
        "content_delivery_mode": delivery,
        "requires": requires,
        "blockers": blockers,
        "warnings": warnings,
        "blockers_count": len(blockers),
    }


__all__ = [
    "ModuleInUse",
    "PROTECTED_MODULE_ACTIONS",
    "assert_module_action_allowed",
    "list_modules",
    "module_bound_games",
    "module_content",
    "module_detail",
    "parse_requires",
    "preview_module_install",
]


# ---- LIFE-01：绑定存档保护（母方案 §35/§36/§37/§124）------------------------

# 受保护操作 → 绑定存档存在时是否阻断。更新/禁用/卸载一律默认 block；
# 母方案 §35：更新改变 bound adventure digest → BLOCK。
PROTECTED_MODULE_ACTIONS = ("uninstall", "disable", "update")


def module_bound_games(
    deps: Any,
    module_id: str,
) -> list[dict[str, Any]]:
    """List the games bound to one module's adventures（§35 UI 数据源）。"""

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None:
        return []
    plugin_id = str(runtime.manifest.get("id") or "")
    adventure_ids: set[str] = set()
    if deps.adventure_registry is not None:
        source = deps.adventure_registry.source_for("plugin", plugin_id)
        if source is not None:
            try:
                adventure_ids = {
                    bundle.manifest.adventure_id for bundle in source.loader.list("")
                }
            except Exception:  # noqa: BLE001 - 坏包不拖垮保护检查
                adventure_ids = set()
    bound: list[dict[str, Any]] = []
    for instance in deps.list_instances():
        binding = getattr(instance, "adventure_binding", {}) or {}
        if str(binding.get("adventure_id") or "") in adventure_ids:
            bound.append({
                "game_key": "|".join(str(part) for part in instance.game_key),
                "adventure_id": str(binding.get("adventure_id") or ""),
                "run_id": str(instance.run_id or ""),
            })
    return bound


def assert_module_action_allowed(deps: Any, module_id: str, action: str) -> None:
    """Guard for uninstall/disable/update（母方案 §124：默认 block）。

    绑定存档存在时抛 :class:`ModuleInUse`；调用方（插件生命周期 API）
    把它转成结构化错误，UI 展示"哪些存档正在使用"。
    """

    if action not in PROTECTED_MODULE_ACTIONS:
        return
    bound = module_bound_games(deps, module_id)
    if bound:
        raise ModuleInUse(module_id, action, bound)


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


def module_usages(deps: Any, module_id: str) -> dict[str, Any]:
    """模块使用索引：哪个模块 / 哪个冒险被哪些存档绑定（只读聚合）。"""

    runtime = getattr(deps.plugin_host, "plugins", {}).get(str(module_id or ""))
    if runtime is None:
        return {"ok": False, "error_code": "MODULE_NOT_FOUND"}
    plugin_id = str(runtime.manifest.get("id") or "")
    adventure_ids: set[str] = set()
    if deps.adventure_registry is not None:
        source = deps.adventure_registry.source_for("plugin", plugin_id)
        if source is not None:
            try:
                adventure_ids = {
                    bundle.manifest.adventure_id for bundle in source.loader.list("")
                }
            except Exception:  # noqa: BLE001
                adventure_ids = set()
    by_adventure: dict[str, list[dict[str, Any]]] = {
        adventure_id: [] for adventure_id in sorted(adventure_ids)
    }
    for instance in deps.list_instances():
        binding = getattr(instance, "adventure_binding", {}) or {}
        adventure_id = str(binding.get("adventure_id") or "")
        if adventure_id in by_adventure:
            by_adventure[adventure_id].append({
                "game_key": "|".join(str(part) for part in instance.game_key),
                "run_id": str(instance.run_id or ""),
                "content_digest": str(binding.get("content_digest") or ""),
            })
    usages = [
        {"adventure_id": adventure_id, "games": games}
        for adventure_id, games in by_adventure.items()
    ]
    total = sum(len(item["games"]) for item in usages)
    return {"ok": True, "module_id": plugin_id, "usages": usages, "total_games": total}


__all__ = [
    "ModuleInUse",
    "PROTECTED_MODULE_ACTIONS",
    "assert_module_action_allowed",
    "list_modules",
    "module_bound_games",
    "module_content",
    "module_detail",
    "module_usages",
    "parse_requires",
    "preview_module_install",
]
