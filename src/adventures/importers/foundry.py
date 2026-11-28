"""Foundry Adventure JSON document adapter (public document export shape)."""
from __future__ import annotations

import json
import re

from src.adventures.importers.common import NativeDraft, local_id, prose
from src.content_modules.adapters.foundry_scene import (
    detect_scene_version,
    portable_scene,
    scene_entity,
)

# Same power the Adventure bundle loader already refuses outright (bundle.py
# ``_FORBIDDEN_KEYS``). A scene definition carrying one of these keys cannot be
# stored in a bundle, so the row falls back to a reference instead of failing
# the whole import.
_BUNDLE_FORBIDDEN_KEYS = frozenset({
    "python", "javascript", "script", "code", "eval", "module", "callable",
})


def _has_bundle_forbidden_key(value) -> bool:
    if isinstance(value, dict):
        if any(str(key).lower() in _BUNDLE_FORBIDDEN_KEYS for key in value):
            return True
        return any(_has_bundle_forbidden_key(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_bundle_forbidden_key(child) for child in value)
    return False


def parse_foundry(payload: bytes, filename: str, runtime: dict, language: str) -> dict:
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Foundry Adventure must be UTF-8 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not any(key in data for key in ("journal", "actors", "scenes", "items")):
        raise ValueError("expected a Foundry Adventure document export, not a pack database or arbitrary JSON")
    builder = NativeDraft(payload, filename, data["name"], runtime, language, "foundry-adventure")
    collections = {}
    for kind in ("journal", "actors", "scenes", "items"):
        rows = data.get(kind, [])
        if not isinstance(rows, list) or len(rows) > 128 or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"Foundry {kind} must be a bounded document array")
        if any(not isinstance(row.get("_id"), str) or not row["_id"] for row in rows) or len({row["_id"] for row in rows}) != len(rows):
            raise ValueError(f"Foundry {kind} requires unique document identities")
        collections[kind] = rows
    actor_refs = {}
    for row in collections["actors"]:
        system = row.get("system") if isinstance(row.get("system"), dict) else {}
        details = system.get("details") if isinstance(system.get("details"), dict) else {}
        bio = details.get("biography") or {}
        actor_refs[row["_id"]] = builder.resource("npc", row["_id"], str(row.get("name") or row["_id"]), prose(bio.get("value") if isinstance(bio, dict) else bio), row)
    for row in collections["items"]:
        system = row.get("system") if isinstance(row.get("system"), dict) else {}
        description = system.get("description") or {}
        builder.resource("item", row["_id"], str(row.get("name") or row["_id"]), prose(description.get("value") if isinstance(description, dict) else description), row)
    for row in collections["journal"]:
        pages = row.get("pages", [])
        if not isinstance(pages, list) or len(pages) > 128 or not all(isinstance(page, dict) for page in pages):
            raise ValueError("Foundry journal pages must be a bounded array")
        content = []
        for page in pages:
            text = page.get("text") or {}
            if page.get("type") == "text" and isinstance(text, dict):
                content.append(str(text.get("content") or ""))
        if not pages and isinstance(row.get("content"), str):
            content.append(row["content"])  # Older journal document exports.
        html = "\n".join(content)
        node = builder.scene("journal:" + row["_id"], str(row.get("name") or row["_id"]), prose(html))
        node["npc_refs"] = list(dict.fromkeys(actor_refs[key] for key in re.findall(r'@UUID\[Actor\.([^\].]+)\]', html) if key in actor_refs))
    adventure_version = detect_scene_version(data)
    for row in collections["scenes"]:
        name = str(row.get("name") or row["_id"])
        builder.resource("map_location", row["_id"], name, name, row)
        node = builder.scene("scene:" + row["_id"], name, "", reference=True)
        try:
            document, report = portable_scene(
                row, source_version=detect_scene_version(row) or adventure_version,
            )
        except ValueError:
            # No declared version or a malformed row: never guess a schema, and
            # keep the raw document as inert reference evidence.
            builder.draft["warnings"].append("externalImportSceneUnconverted")
            node["description"] = json.dumps(row, ensure_ascii=False, sort_keys=True)
            continue
        entity_id = local_id("scene", row["_id"])
        entity = scene_entity(
            document,
            entity_id=entity_id,
            source_ref=builder.graph["source_ref"],
            name=name,
        )
        if report.contains_script or _has_bundle_forbidden_key(entity):
            # Adventure bundles refuse executable content keys outright; keep
            # the scene as reference prose rather than failing the package.
            builder.draft["warnings"].append("externalImportSceneScriptsOmitted")
            node["description"] = json.dumps(row, ensure_ascii=False, sort_keys=True)
            continue
        node["scene_ref"] = "scene:" + entity_id
        builder.files[f"content/scene/{entity_id}.json"] = entity
    if data.get("macros") or data.get("playlists"):
        builder.draft["warnings"].append("externalImportScriptsOmitted")
    if collections["scenes"] or data.get("img"):
        builder.draft["warnings"].append("externalImportMedia")
    return builder.finish(data["name"])
