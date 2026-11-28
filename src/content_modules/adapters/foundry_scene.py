"""Portable Tactical Scene definitions produced from Foundry Scene documents.

Workplan §6.17 m3-02/m3-03. A compendium Scene record and an adventure scene
row both convert through the frozen ``src.compat.foundry.scene_adapter``; the
resulting ``TacticalSceneDocument`` *is* the portable scene schema
(:data:`src.content_modules.refs.PORTABLE_SCENE_SCHEMA`). This module only adds
the envelope shared by content packages and adventure bundles plus the checks
that keep the definition storable:

- the source version is read from ``_stats.coreVersion`` and never guessed;
- the converted document is normalized, credential-scrubbed and checked for
  unsafe asset paths by the same helper the single-scene import uses;
- tokens, walls and lights are whatever the adapter converted — nothing is
  invented here, and no script is ever executed.

Nothing in this module touches a game slot, an engine instance or a transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from src.compat.foundry.import_plan import sanitize_scene_document
from src.compat.foundry.report import ConversionReport
from src.compat.foundry.scene_adapter import import_scene
from src.content_modules.refs import PORTABLE_SCENE_SCHEMA


def detect_scene_version(*sources: Any) -> str | None:
    """Return ``"v<major>"`` from the first ``_stats.coreVersion`` found.

    The version is only reported, never assumed: when no source declares a
    core version this returns ``None`` and the caller must fail closed.
    """

    for source in sources:
        if not isinstance(source, Mapping):
            continue
        stats = source.get("_stats")
        if not isinstance(stats, Mapping):
            continue
        core = stats.get("coreVersion")
        if not isinstance(core, str) or not core.strip():
            continue
        head = core.strip().lower().lstrip("v").split(".", 1)[0]
        if head.isdigit():
            return f"v{head}"
    return None


def portable_scene(
    raw: Any, *, source_version: Any = None,
) -> tuple[dict[str, Any], ConversionReport]:
    """Convert one Foundry Scene object into a sanitized portable definition.

    ``source_version`` wins when supplied; otherwise ``_stats.coreVersion``
    decides. Both unknown and future versions fail closed through the frozen
    adapter instead of guessing a schema.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("Foundry scene must be a JSON object")
    declared = source_version or detect_scene_version(raw)
    if declared is None:
        raise ValueError(
            "the Foundry scene declares no _stats.coreVersion; its format is not guessed"
        )
    document, report = import_scene(deepcopy(dict(raw)), source_version=declared)
    sanitized, scrubbed = sanitize_scene_document(document)
    for path in scrubbed:
        report.warn(f"{path}: credential-like field scrubbed; imports never carry secrets")
    return sanitized, report


def scene_definition(document: Mapping[str, Any]) -> dict[str, Any]:
    """The portable payload shared by compendium drafts and adventure entities."""

    return {"spec": PORTABLE_SCENE_SCHEMA, "tactical_scene": deepcopy(dict(document))}


def scene_entity(
    document: Mapping[str, Any],
    *,
    entity_id: str,
    source_ref: str,
    name: str = "",
) -> dict[str, Any]:
    """Wrap a portable definition as one adventure bundle entity.

    The envelope is the adventure entity contract (schema_version/kind/id/
    source_ref/automation_level); the scene itself stays a TacticalSceneDocument.
    """

    return {
        "schema_version": 1,
        "kind": "scene",
        "id": entity_id,
        "source_ref": source_ref,
        "automation_level": "reference",
        "name": name or str(document.get("display_name") or entity_id),
        **scene_definition(document),
    }


__all__ = [
    "PORTABLE_SCENE_SCHEMA",
    "detect_scene_version",
    "portable_scene",
    "scene_definition",
    "scene_entity",
]