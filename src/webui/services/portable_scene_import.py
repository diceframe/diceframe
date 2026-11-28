"""Explicit per-game creation from a portable Tactical Scene definition (m3-03).

Frozen contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.17 and §12.2.

A content package or adventure carries the portable scene form: the
``tactical-scene-v1`` payload is exactly the ``TacticalSceneDocument`` produced
by the Foundry scene adapter. This GM action creates it in the current game
through the same authority path as the map-background conversion (GM gate,
instance lock, save rollback, SSE projection) — one ``tactical_scene_create``
applied by ``src.webui.services.tactical_scene_ops``.

Imports never overwrite a local scene: the same identity with identical content
is an idempotent no-op, the same identity with different content is refused,
and a same-named local scene requires an explicit ``confirm_name_collision``
before another scene is created. Nothing here guesses tokens, walls or routes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.compat.foundry.import_plan import FoundryImportPlanError, sanitize_scene_document
from src.content_modules.plan import content_digest
from src.content_modules.refs import PORTABLE_SCENE_SCHEMA
from src.engine.module_state import ModuleStateError
from src.engine.modules import tactical_scene
from src.engine.participant_view import Viewer
from src.tactical_scene.codec import decode_tactical_scene_document
from src.tactical_scene.contract import TacticalSceneContractError
from src.tactical_scene.projection import project_tactical_scene_gm
from src.webui.services.tactical_scene_ops import (
    Broadcast,
    TacticalSceneOpDependencies,
    apply_tactical_scene_op,
)

CODE_FORBIDDEN = "scene_import_forbidden"
CODE_NOT_FOUND = "scene_import_not_found"
CODE_UNCONFIRMED = "scene_import_unconfirmed"
CODE_INVALID = "tactical_scene_invalid_payload"
CODE_CONFLICT = "scene_import_conflict"
CODE_NAME_CONFLICT = "scene_import_name_conflict"


def _error(code: str, message: str) -> dict[str, Any]:
    # ASCII messages: no locale catalog owns this service yet, so ``t`` call
    # sites here would fail the message-key audit.
    return {"ok": False, "error_code": code, "error": message}


def scene_payload(definition: Any) -> Any:
    """Unwrap a ``tactical-scene-v1`` payload; a bare document is accepted too."""

    if isinstance(definition, Mapping) and definition.get("spec") == PORTABLE_SCENE_SCHEMA:
        return definition.get("tactical_scene")
    return definition


def _comparable_digest(document: Mapping[str, Any]) -> str:
    body = dict(document)
    body.pop("revision", None)
    return content_digest(body)


def _name_collision(
    scenes: Mapping[str, Any], document: Mapping[str, Any], target_id: str,
) -> str | None:
    name = str(document.get("display_name") or "")
    if not name:
        return None
    for scene_id, raw in scenes.items():
        if scene_id == target_id or not isinstance(raw, Mapping):
            continue
        if str(raw.get("display_name") or "") == name:
            return str(scene_id)
    return None


def _inspect_import(
    scene_dependencies: TacticalSceneOpDependencies,
    game_key: str,
    viewer: Viewer,
    *,
    definition: Any,
    intent: Any,
    require_confirm: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Shared read-only checks: exactly one of ``(error, target)`` is set."""

    if not viewer.is_gm:
        return _error(CODE_FORBIDDEN, "Only the GM can import a scene definition into this game."), None
    instance = scene_dependencies.get_instance(game_key)
    if instance is None:
        return _error(CODE_NOT_FOUND, "The game does not exist."), None
    if require_confirm and (
        not isinstance(intent, Mapping) or intent.get("confirm") is not True
    ):
        return _error(CODE_UNCONFIRMED, "Confirm the scene import before creating a tactical scene."), None
    try:
        document, _scrubbed = sanitize_scene_document(scene_payload(definition))
    except (FoundryImportPlanError, ValueError):
        return _error(CODE_INVALID, "The scene definition cannot be read safely."), None
    scene_id = document["canonical_id"]
    try:
        scenes = tactical_scene.scenes(instance)
        slot_revision = tactical_scene.revision(instance)
    except ModuleStateError:
        return _error(CODE_INVALID, "The scene state cannot be read safely."), None

    existing = scenes.get(scene_id)
    if isinstance(existing, Mapping):
        try:
            current = decode_tactical_scene_document(existing)
        except TacticalSceneContractError:
            return _error(CODE_INVALID, "The existing scene cannot be read safely."), None
        if _comparable_digest(current) != _comparable_digest(document):
            return _error(
                CODE_CONFLICT,
                "A different local scene already uses this identity; imports never overwrite it.",
            ), None
        return None, {
            "instance": instance,
            "document": document,
            "scene_id": scene_id,
            "revision": slot_revision,
            "existing": current,
            "name_collision": False,
        }
    return None, {
        "instance": instance,
        "document": document,
        "scene_id": scene_id,
        "revision": slot_revision,
        "existing": None,
        "name_collision": _name_collision(scenes, document, scene_id) is not None,
    }


def preview_scene_import(
    scene_dependencies: TacticalSceneOpDependencies,
    game_key: str,
    viewer: Viewer,
    *,
    definition: Any,
) -> dict[str, Any]:
    """Report what an import would do without writing anything."""

    error, target = _inspect_import(
        scene_dependencies, game_key, viewer,
        definition=definition, intent=None, require_confirm=False,
    )
    if error is not None:
        return error
    assert target is not None
    existing = target["existing"]
    report: dict[str, Any] = {
        "ok": True,
        "action": "noop" if existing is not None else "create",
        "scene_id": target["scene_id"],
        "revision": target["revision"],
        "name_collision": target["name_collision"],
    }
    if existing is not None:
        report["active_scene_ref"] = tactical_scene.active_scene_ref(target["instance"])
        report["scene"] = project_tactical_scene_gm(existing)
    return report


async def import_scene_definition(
    scene_dependencies: TacticalSceneOpDependencies,
    game_key: str,
    viewer: Viewer,
    *,
    definition: Any,
    intent: Any,
    broadcast: Broadcast | None = None,
) -> dict[str, Any]:
    """Create one game scene from a portable definition; never overwrite."""

    error, target = _inspect_import(
        scene_dependencies, game_key, viewer,
        definition=definition, intent=intent, require_confirm=True,
    )
    if error is not None:
        return error
    assert target is not None
    existing = target["existing"]
    if existing is not None:
        return {
            "ok": True,
            "created": False,
            "scene_id": target["scene_id"],
            "revision": target["revision"],
            "active_scene_ref": tactical_scene.active_scene_ref(target["instance"]),
            "scene": project_tactical_scene_gm(existing),
        }

    if target["name_collision"] and intent.get("confirm_name_collision") is not True:
        return _error(
            CODE_NAME_CONFLICT,
            "A local scene already has this name; confirm creating another scene.",
        )

    expected_revision = intent.get("expected_revision", target["revision"])
    result = await apply_tactical_scene_op(
        scene_dependencies,
        game_key,
        viewer,
        {"kind": "tactical_scene_create", "scene": target["document"]},
        expected_revision=expected_revision,
        broadcast=broadcast,
    )
    if result.get("ok"):
        result["created"] = True
    return result


__all__ = [
    "CODE_CONFLICT",
    "CODE_FORBIDDEN",
    "CODE_INVALID",
    "CODE_NAME_CONFLICT",
    "CODE_NOT_FOUND",
    "CODE_UNCONFIRMED",
    "import_scene_definition",
    "preview_scene_import",
    "scene_payload",
]