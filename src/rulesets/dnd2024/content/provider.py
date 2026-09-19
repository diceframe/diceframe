"""D&D runtime content catalog chain (FIX-03 §5.1/§5.2, 母方案 §10/§29).

运行时的 gameplay catalog 真相属于 **D&D Runtime**，不属于 WebAPI：

```text
Adventure-local          冒险包自带内容（bundle 内联 encounter_catalog，v1 兼容）
    ↓
owning Module            绑定冒险所属 content-pack 模组声明的 ruleset_catalogs
    ↓
declared dependencies    其它已启用模组的 catalog（按 label 排序，显式来源优先）
    ↓
core D&D content         规则包自带的 catalog（srd_*）
```

组合根只注入"模组 catalog 来源"（这是 Web 层唯一知道的事实：哪些模组启用了、
各自声明了哪些 catalog 目录），链路顺序与解析语义由 runtime 自己决定。

引用语义沿用 ``src.content_modules.refs``：

- ``{"source": "module:x", "kind": "monster", "id": "y"}`` 这类**显式来源**只在
  该来源内解析，缺来源即 fail closed（不回溯、不 silent shadow）；
- v1 裸 ``kind:id`` 按链序解析，首个命中胜出（adventure-local 优先）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from src.rulesets.dnd2024.content.catalog import DndContentCatalog, catalog_from_sources

# 一个实例的模组 catalog 来源：(label, {kind: {record_id: record}})
ModuleContentSources = Callable[[Any], Iterable[tuple[str, Mapping[str, Mapping[str, Any]]]]]

OWNING_MODULE_LABEL_PREFIX = "module:"


def source_rank(label: str, owning_module_id: str) -> int:
    """Chain order: owning module first, then other modules, then core."""

    text = str(label or "")
    if owning_module_id and text == f"{OWNING_MODULE_LABEL_PREFIX}{owning_module_id}":
        return 0
    if text.startswith(OWNING_MODULE_LABEL_PREFIX):
        return 1
    return 2


def ordered_sources(
    sources: Iterable[tuple[str, Mapping[str, Mapping[str, Any]]]],
    *,
    owning_module_id: str = "",
) -> list[tuple[str, Mapping[str, Mapping[str, Any]]]]:
    """Deterministic, explicit chain order (owning module → other modules → core)."""

    return sorted(
        ((str(label), records) for label, records in sources),
        key=lambda item: (source_rank(item[0], owning_module_id), item[0]),
    )


def adventure_local_records(
    entities: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Catalog-shaped records inside one loaded adventure bundle (§5.1 chain head).

    v1 bundle 实体带的是 ``schema_version/kind/id/source_ref/automation_level``
    信封，不等于 catalog record；只有能过 catalog 契约的实体才进入链首来源，
    其余（例如 v1 内联 encounter_catalog）继续走冒险包自己的路径。
    """

    from src.rulesets.dnd2024.content.catalog import (
        CATALOG_KINDS,
        validate_catalog_record,
    )

    if not isinstance(entities, Mapping):
        return {}
    records: dict[str, dict[str, Any]] = {}
    for kind in CATALOG_KINDS:
        bucket = entities.get(kind)
        if not isinstance(bucket, Mapping):
            continue
        for record_id, record in bucket.items():
            try:
                validated = validate_catalog_record(kind, record)
            except Exception:  # noqa: BLE001 - 非 catalog 形状的冒险实体跳过
                continue
            records.setdefault(kind, {})[str(record_id)] = validated
    return records


class DndContentCatalogProvider:
    """Build one runtime content catalog for an instance from injected sources."""

    def __init__(
        self,
        module_sources: ModuleContentSources | None = None,
        core_sources: ModuleContentSources | None = None,
    ) -> None:
        self._module_sources = module_sources
        self._core_sources = core_sources

    def catalog_for(
        self,
        instance: Any,
        *,
        owning_module_id: str = "",
        adventure_label: str = "",
        adventure_records: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> DndContentCatalog:
        """Build the ordered chain; duplicate refs inside one label fail closed."""

        chain: list[tuple[str, Mapping[str, Mapping[str, Any]]]] = []
        if adventure_label and adventure_records:
            chain.append((str(adventure_label), adventure_records))
        chain.extend(self._collect(self._module_sources, instance))
        chain.extend(self._collect(self._core_sources, instance))
        ordered = ordered_sources(chain, owning_module_id=owning_module_id)
        if not ordered:
            return DndContentCatalog([])
        return catalog_from_sources(ordered)

    @staticmethod
    def _collect(
        provider: ModuleContentSources | None, instance: Any,
    ) -> list[tuple[str, Mapping[str, Mapping[str, Any]]]]:
        if provider is None:
            return []
        try:
            return [(str(label), records) for label, records in provider(instance)]
        except Exception:  # noqa: BLE001 - 组合根数据问题不应让战斗无法开始
            return []


__all__ = [
    "DndContentCatalogProvider",
    "ModuleContentSources",
    "adventure_local_records",
    "ordered_sources",
    "source_rank",
]
