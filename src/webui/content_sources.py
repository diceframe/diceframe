"""Project installed plugin content into adventure and D&D catalog sources."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.adventures import AdventureBundleLoader
from src.adventures.registry import AdventureSource, AdventureSourceRegistry

if TYPE_CHECKING:
    from src.plugin_host.models import PluginRuntime
    from src.rulesets.dnd2024.content.catalog import DndContentCatalog

logger = logging.getLogger("trpg")


def sync_plugin_adventure_sources(
    registry: AdventureSourceRegistry, runtimes: Iterable[PluginRuntime],
) -> None:
    """Rebuild the registry's plugin sources using declared-only loaders."""

    sources = []
    for runtime in runtimes:
        root = getattr(runtime, "adventure_packages_root", None)
        if root is None or runtime.status == "disabled":
            continue
        sources.append(AdventureSource(
            "plugin",
            str(runtime.manifest.get("id") or ""),
            AdventureBundleLoader(
                root,
                allowed_directory_ids=getattr(runtime, "adventure_package_directories", ()),
            ),
        ))
    registry.sync_plugin_sources(sources)


def load_module_catalogs(runtimes: Iterable[PluginRuntime]) -> list[tuple[str, Any]]:
    """Load declared catalogs; an invalid catalog is omitted and logged."""

    from src.rulesets.dnd2024.content.catalog import load_catalog_dir

    sources: list[tuple[str, Any]] = []
    for runtime in runtimes:
        root = getattr(runtime, "ruleset_catalogs_root", None)
        if root is None or runtime.status == "disabled":
            continue
        label = f"module:{runtime.manifest.get('id') or ''}"
        for directory in getattr(runtime, "ruleset_catalog_directories", ()):
            try:
                source = load_catalog_dir(Path(root) / directory, source_label=label)
            except Exception as exc:
                logger.warning(
                    "module catalog load failed: %s: %s", label, exc,
                )
                continue
            # The runtime consumes nested {kind: {record_id: record}} sources.
            nested: dict[str, dict[str, Any]] = {}
            for (kind, record_id), record in source.records.items():
                nested.setdefault(str(kind), {})[str(record_id)] = record
            sources.append((source.label, nested))
    return sources


def module_catalog(sources: list[tuple[str, Any]]) -> DndContentCatalog:
    from src.rulesets.dnd2024.content.catalog import catalog_from_sources

    return catalog_from_sources(sources or [])


def ordered_module_sources(
    catalogs: list[tuple[str, Any]], instance: Any = None,
) -> list[tuple[str, Any]]:
    """Put the bound adventure's owning module first; keep other sources in order."""

    sources = list(catalogs)
    owning = ""
    binding = getattr(instance, "adventure_binding", None)
    if isinstance(binding, dict) and str(binding.get("source_kind") or "") == "plugin":
        owning = str(binding.get("source_id") or "")
    if owning:
        owner_label = f"module:{owning}"
        sources.sort(key=lambda item: 0 if item[0] == owner_label else 1)
    return sources
