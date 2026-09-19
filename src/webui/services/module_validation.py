"""Shared content-module package validation (FIX-01 / LIFE-05, 母方案 §33/§107/§123).

单一校验入口：preview / 本地安装 / 市场安装 / CLI validator 全部走本模块，
因此"预览显示 blocker、直接调 API 却能装上"这种半 enforceable 的状态不可能出现。

```text
PluginHost.install_from_zip  ─┐
PluginHost.inspect(预览)      ├─→ validate_module_package(...) ─→ blockers
scripts/validate_content_module.py ┘
```

硬规则：

- **服务端 hard gate**：``blockers`` 非空 ⇒ 安装事务必须在任何 mutation 之前
  拒绝（``assert_module_package_installable``）；
- **深度校验在安装事务内**：声明的 Adventure 包逐个真装载（graph/refs/runtime
  mechanics），声明的 ruleset catalog 逐个真装载（record 契约），任一失败即
  整个安装失败，不做 "装上去再 logger.warning 跳过"；
- **module surface 只处理 data-only content-pack**：``require_content_pack``
  用于 ``/api/modules/*`` 安装面；
- **generic 层不认识具体 ruleset**：catalog 装载经
  ``src.rulesets.module_catalogs`` 的注册表 seam 交给目标 runtime。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.adventures import AdventureBundleError, AdventureBundleLoader
from src.content_modules.refs import ContentRefError, parse_content_ref
from src.plugin_host.support import (
    content_delivery_mode,
    content_profile,
    validate_content_module_profile,
)
from src.rulesets.module_catalogs import (
    ModuleCatalogError,
    load_declared_module_catalog,
)

MODULE_PLUGIN_TYPE = "content-pack"
_PREVIEW_SUPPORTED_FORMATS = (
    "diceframe:adventure-graph-v1",
    "diceframe:adventure-graph-v2",
)


class ModulePackageError(ValueError):
    """The package must not be installed: blockers are hard, not advisory."""

    def __init__(self, plugin_id: str, blockers: Iterable[str]) -> None:
        self.plugin_id = str(plugin_id or "")
        self.blockers = tuple(str(item) for item in blockers)
        super().__init__(
            f"module package {self.plugin_id!r} is not installable: "
            + "; ".join(self.blockers)
        )


@dataclass(frozen=True)
class ModuleValidationDependencies:
    """Explicit, duck-typed validation context (registry + default runtime)."""

    ruleset_registry: Any | None = None
    default_runtime_requirement: Callable[[], dict[str, Any]] | None = None


@dataclass(frozen=True)
class ModulePackageValidation:
    """One validated (or rejected) module package."""

    plugin_id: str
    manifest: dict[str, Any]
    content_profile: str
    content_delivery_mode: str
    requires: tuple[dict[str, Any], ...] = ()
    effective_runtime_id: str = ""
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    adventures: tuple[dict[str, Any], ...] = ()
    catalogs: tuple[dict[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.blockers

    def preview(self) -> dict[str, Any]:
        """The install-preview response shape consumed by the module UI."""

        return {
            "ok": True,
            "plugin_id": self.plugin_id,
            "content_profile": self.content_profile,
            "content_delivery_mode": self.content_delivery_mode,
            "requires": [dict(item) for item in self.requires],
            "effective_runtime_id": self.effective_runtime_id,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "blockers_count": len(self.blockers),
            "adventures": [dict(item) for item in self.adventures],
            "catalogs": [dict(item) for item in self.catalogs],
        }


def parse_requires(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Parse manifest ``requires.rulesets``（声明目标 runtime 与最低版本）。

    结构：``{"rulesets": [{"id": "core:dnd2024", "minimum_version": 2}]}``；
    缺省 = 无 runtime 要求（传统 content-pack）。
    """

    requires = (manifest or {}).get("requires")
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


def _deps_value(deps: Any, name: str) -> Any:
    return getattr(deps, name, None) if deps is not None else None


def _default_runtime_id(deps: Any) -> str:
    provider = _deps_value(deps, "default_runtime_requirement")
    if not callable(provider):
        return ""
    try:
        requirement = provider() or {}
    except Exception:  # noqa: BLE001 - 默认 runtime 解析失败不阻断整个校验
        return ""
    return str(requirement.get("id") or "")


def _runtime_available(deps: Any, runtime_id: str, minimum: int) -> bool:
    registry = _deps_value(deps, "ruleset_registry")
    if registry is None:
        return False
    try:
        runtime = registry.get(runtime_id, minimum_version=minimum)
    except Exception:  # noqa: BLE001 - registry 对未知/过旧 runtime 抛错
        return False
    return runtime is not None


def _resolve_declared_paths(
    directory: Path, declared: Iterable[Any], label: str,
) -> tuple[Path | None, list[str], list[str]]:
    """Resolve declared relative directories inside ``directory`` (fail closed)."""

    base = directory.resolve()
    resolved: list[Path] = []
    blockers: list[str] = []
    for pattern in declared:
        if not isinstance(pattern, str) or not pattern.strip():
            blockers.append(f"{label}_path_invalid")
            continue
        normalized = pattern.strip().replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
            blockers.append(f"{label}_path_unsafe:{normalized}")
            continue
        target = (base / candidate).resolve()
        if target != base and base not in target.parents:
            blockers.append(f"{label}_path_unsafe:{normalized}")
            continue
        resolved.append(target)
    if not resolved:
        return None, [], blockers
    parents = {item.parent for item in resolved}
    if len(parents) != 1:
        blockers.append(f"{label}_shared_parent_required")
        return None, [], blockers
    return parents.pop(), sorted(item.name for item in resolved), blockers


def _validate_declared_adventures(
    plugin_id: str, manifest: Mapping[str, Any], directory: Path | None, deps: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Really load every declared Adventure bundle (graph/refs/runtime structure)."""

    declared = manifest.get("adventure_packages")
    if not declared or directory is None:
        return [], []
    root, directories, blockers = _resolve_declared_paths(
        directory, declared, "adventure_packages",
    )
    if root is None:
        return [], blockers
    loader = AdventureBundleLoader(root, allowed_directory_ids=directories)
    summaries: list[dict[str, Any]] = []
    for name in directories:
        try:
            bundle = loader.load(name, "")
        except AdventureBundleError as exc:
            blockers.append(f"adventure_package_invalid:{name}:{exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - 任何装载失败都必须阻断安装
            blockers.append(f"adventure_package_invalid:{name}:{exc}")
            continue
        runtime_id = str(bundle.manifest.required_runtime_id or "")
        minimum = int(bundle.manifest.required_runtime_version or 1)
        if not _runtime_available(deps, runtime_id, minimum):
            blockers.append(
                f"adventure_runtime_missing:{name}:{runtime_id}>={minimum}"
            )
        summaries.append({
            "directory": name,
            "adventure_id": bundle.manifest.adventure_id,
            "version": bundle.manifest.version,
            "format": bundle.manifest.format,
            "content_digest": bundle.content_digest,
        })
    return summaries, blockers


def _declared_ref_values(record: Mapping[str, Any]) -> Iterable[Any]:
    """Values that the module format declares as ContentRefs (never ``source_ref``)."""

    for key, value in record.items():
        name = str(key)
        if name == "source_ref" or not (name == "ref" or name.endswith("_ref")):
            continue
        if isinstance(value, list):
            yield from value
        elif value is not None:
            yield value


def _validate_declared_catalogs(
    plugin_id: str,
    manifest: Mapping[str, Any],
    directory: Path | None,
    runtime_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Really load every declared ruleset catalog through the owning runtime."""

    declared = manifest.get("ruleset_catalogs")
    if not declared or directory is None:
        return [], []
    root, directories, blockers = _resolve_declared_paths(
        directory, declared, "ruleset_catalogs",
    )
    if root is None:
        return [], blockers
    summaries: list[dict[str, Any]] = []
    for name in directories:
        try:
            source = load_declared_module_catalog(
                runtime_id, root / name, source_label=f"module:{plugin_id}",
            )
        except ModuleCatalogError:
            # runtime 不支持声明式 catalog：不能猜契约，直接阻断。
            blockers.append(f"ruleset_catalog_unsupported:{runtime_id or 'unknown'}")
            continue
        except Exception as exc:  # noqa: BLE001 - 坏 catalog 必须阻断安装
            blockers.append(f"ruleset_catalog_invalid:{name}:{exc}")
            continue
        blockers.extend(
            _content_ref_blockers(
                plugin_id, getattr(source, "records", {}) or {}, name,
            )
        )
        summaries.append({
            "directory": name,
            "label": str(getattr(source, "label", "") or ""),
            "records": len(getattr(source, "records", {}) or {}),
        })
    return summaries, blockers


def _content_ref_blockers(
    plugin_id: str,
    records: Mapping[Any, Any],
    directory: str,
) -> list[str]:
    """Structural ContentRef integrity of module-authored catalog records."""

    blockers: list[str] = []
    default_source = f"module:{plugin_id}"
    for record in records.values():
        if not isinstance(record, Mapping):
            continue
        for raw in _declared_ref_values(record):
            try:
                parse_content_ref(raw, default_source=default_source)
            except ContentRefError as exc:
                blockers.append(f"content_ref_invalid:{directory}:{exc}")
    return blockers


def validate_module_package(
    manifest: Mapping[str, Any],
    *,
    deps: Any = None,
    directory: Path | None = None,
    require_content_pack: bool = False,
) -> ModulePackageValidation:
    """Validate one module package; deep checks need an extracted ``directory``."""

    manifest = dict(manifest or {})
    plugin_id = str(manifest.get("id") or "")
    profile = content_profile(manifest)
    delivery = content_delivery_mode(manifest)
    blockers: list[str] = []
    warnings: list[str] = []
    adventures: list[dict[str, Any]] = []
    catalogs: list[dict[str, Any]] = []
    plugin_type = str(manifest.get("plugin_type") or "")

    if require_content_pack and plugin_type != MODULE_PLUGIN_TYPE:
        blockers.append(f"plugin_type_not_content_pack:{plugin_type or 'missing'}")

    is_module = plugin_type == MODULE_PLUGIN_TYPE
    if not is_module and not require_content_pack:
        # 非 content-pack 走通用插件面：模组专属字段由 host 校验（adventure_packages /
        # ruleset_catalogs 仅 content-pack），这里没有可深校验的模块内容。
        return ModulePackageValidation(
            plugin_id=plugin_id,
            manifest=manifest,
            content_profile=profile,
            content_delivery_mode=delivery,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    try:
        validate_content_module_profile(manifest)
        requires = parse_requires(manifest)
    except ValueError as exc:
        return ModulePackageValidation(
            plugin_id=plugin_id,
            manifest=manifest,
            content_profile=profile,
            content_delivery_mode=delivery,
            blockers=tuple([*blockers, str(exc)]),
            warnings=tuple(warnings),
        )

    for requirement in requires:
        runtime_id = str(requirement["id"])
        minimum = int(requirement["minimum_version"])
        if not _runtime_available(deps, runtime_id, minimum):
            blockers.append(f"ruleset_runtime_missing:{runtime_id}>={minimum}")

    format_id = str(manifest.get("format") or "")
    if format_id and format_id not in _PREVIEW_SUPPORTED_FORMATS:
        blockers.append(f"adventure_format_unsupported:{format_id}")

    # ruleset catalog 的契约属于目标 runtime：声明了 requires 就用它，否则用
    # 组合根声明的默认 runtime（与 Adventure 绑定同一默认，不猜字段）。
    effective_runtime_id = (
        str(requires[0]["id"]) if requires else _default_runtime_id(deps)
    )

    adventure_summaries, adventure_blockers = _validate_declared_adventures(
        plugin_id, manifest, directory, deps,
    )
    adventures.extend(adventure_summaries)
    blockers.extend(adventure_blockers)

    catalog_summaries, catalog_blockers = _validate_declared_catalogs(
        plugin_id, manifest, directory, effective_runtime_id,
    )
    catalogs.extend(catalog_summaries)
    blockers.extend(catalog_blockers)

    if profile == "adventure-module" and not manifest.get("adventure_packages"):
        warnings.append("adventure_module_without_adventures")
    if delivery == "catalog":
        warnings.append("catalog_mode_content_not_autoloaded")

    return ModulePackageValidation(
        plugin_id=plugin_id,
        manifest=manifest,
        content_profile=profile,
        content_delivery_mode=delivery,
        requires=tuple(requires),
        effective_runtime_id=effective_runtime_id,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        adventures=tuple(adventures),
        catalogs=tuple(catalogs),
    )


def assert_module_package_installable(validation: ModulePackageValidation) -> None:
    """Raise :class:`ModulePackageError` when the package has any blocker."""

    if validation.blockers:
        raise ModulePackageError(validation.plugin_id, validation.blockers)


def validate_module_install(
    directory: Path,
    manifest: Mapping[str, Any],
    *,
    deps: Any = None,
    require_content_pack: bool = False,
) -> ModulePackageValidation:
    """Validate an extracted package and refuse installation when blocked."""

    validation = validate_module_package(
        manifest,
        deps=deps,
        directory=directory,
        require_content_pack=require_content_pack,
    )
    assert_module_package_installable(validation)
    return validation


__all__ = [
    "MODULE_PLUGIN_TYPE",
    "ModulePackageError",
    "ModulePackageValidation",
    "ModuleValidationDependencies",
    "assert_module_package_installable",
    "parse_requires",
    "validate_module_install",
    "validate_module_package",
]
