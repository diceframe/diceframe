"""Read-only projection of the bound adventure graph for external runtimes.

A packaged rules runtime works on a serialized *copy* of the game instance.  It
therefore never sees the adventure package the host installed: the graph lives
in the host's own adventure store, not in the plugin.  Rules that react to the
current node (encounter presets, node loot, choice gates) need that graph.

The host already owns resolution, validation and progress, so what crosses the
boundary is a projection, not a file path and not a second loader:

* only the bound package is projected (no directory scan, no fallback);
* only node/encounter fields a runtime can act on are copied;
* the projection is bounded independently of the caller's payload budget;
* nothing here is writable.  Progress stays host-owned, which is why the graph
  is sent as data instead of handing the runtime the adventure directory.

This is deliberately narrower than ``AdventureResolution``: a runtime may read
the graph, it may not read locale files, digests, world seeds or authoring
metadata.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2, validate_graph_v2

logger = logging.getLogger(__name__)

# The runtime protocol payload cap is 256 KiB.  Half of it is reserved for the
# instance, character sheets and the rule template, so the graph gets its own
# hard ceiling instead of relying on the transport to reject the request.
MAX_PROJECTION_BYTES = 96 * 1024
MAX_NODES = 256
MAX_CHOICES_PER_NODE = 12
MAX_DESCRIPTION_CHARS = 600
MAX_ENCOUNTER_PRESETS = 40

_NODE_FIELDS = (
    "id", "type", "name", "description", "choice_mode", "transitions",
    "encounter_ref", "loot", "chapter_id", "objectives",
)


def _bounded(value: Any, *, chars: int = MAX_DESCRIPTION_CHARS) -> Any:
    if isinstance(value, str):
        return value[:chars]
    return value


def _project_node(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    projected = {key: node[key] for key in _NODE_FIELDS if key in node}
    if not projected.get("id"):
        return None
    if isinstance(projected.get("description"), str):
        projected["description"] = _bounded(projected["description"])
    choices = projected.get("transitions")
    if isinstance(choices, list):
        projected["transitions"] = [
            {
                "choice_id": row.get("choice_id"),
                "choice_label": _bounded(row.get("choice_label") or "", chars=120),
                "to": row.get("to"),
            }
            for row in choices[:MAX_CHOICES_PER_NODE]
            if isinstance(row, dict)
        ]
    return projected


def _project_presets(entities: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog = entities.get("encounter_catalog")
    if not isinstance(catalog, dict):
        return {}
    projected: dict[str, dict[str, Any]] = {}
    for catalog_id, entry in list(catalog.items())[:MAX_ENCOUNTER_PRESETS]:
        if not isinstance(entry, dict):
            continue
        presets = entry.get("presets")
        projected[str(catalog_id)] = {
            "name": _bounded(entry.get("name") or "", chars=120),
            "presets": [row for row in (presets or [])[:MAX_ENCOUNTER_PRESETS]
                        if isinstance(row, dict)],
        }
    return projected


def _size(payload: dict[str, Any]) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_PROJECTION_BYTES + 1


def project_bound_adventure(
    instance: Any, resolver: Any, *, locale: str = "",
) -> dict[str, Any] | None:
    """Project the adventure the save is bound to, or ``None``.

    Resolution failures are *not* fatal: a runtime can still run a game whose
    package was removed or whose binding is stale.  The caller decides how
    loudly to report that; here it is a warning, because the alternative is
    failing every intent of an otherwise healthy save.
    """

    if resolver is None:
        return None
    binding = getattr(instance, "adventure_binding", None)
    if not isinstance(binding, dict) or not binding.get("adventure_id"):
        return None
    try:
        resolution = resolver.resolve_binding(binding, locale)
    except Exception as exc:  # noqa: BLE001 - removal/recovery must not break play
        logger.warning("adventure projection unavailable: %s", exc)
        return None

    bundle = getattr(resolution, "bundle", None)
    manifest = getattr(bundle, "manifest", None)
    if bundle is None or manifest is None:
        return None
    if str(getattr(manifest, "format", "")) != ADVENTURE_GRAPH_FORMAT_V2:
        return None
    try:
        graph = validate_graph_v2(bundle.adventure)
    except ValueError as exc:
        logger.warning("adventure projection rejected by the graph validator: %s", exc)
        return None

    nodes = [row for row in (_project_node(node) for node in (graph.get("nodes") or []))
             if row is not None][:MAX_NODES]
    projection: dict[str, Any] = {
        "adventure_id": str(getattr(manifest, "adventure_id", "") or ""),
        "version": str(getattr(manifest, "version", "") or ""),
        "format": ADVENTURE_GRAPH_FORMAT_V2,
        "name": _bounded(graph.get("name") or "", chars=160),
        "nodes": nodes,
    }
    entities = getattr(bundle, "entities", None)
    if isinstance(entities, dict):
        catalog = _project_presets(entities)
        if catalog:
            projection["encounter_catalog"] = catalog

    if _size(projection) > MAX_PROJECTION_BYTES:
        # Descriptions are the cheapest thing to lose: the host already feeds
        # the node text to the narrator through scene context.
        for row in projection["nodes"]:
            row.pop("description", None)
    if _size(projection) > MAX_PROJECTION_BYTES:
        projection.pop("encounter_catalog", None)
    if _size(projection) > MAX_PROJECTION_BYTES:
        logger.warning("adventure projection exceeds %d bytes; not sent", MAX_PROJECTION_BYTES)
        return None
    return projection


__all__ = ["MAX_PROJECTION_BYTES", "project_bound_adventure"]
