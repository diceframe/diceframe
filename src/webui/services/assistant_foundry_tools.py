"""Agent actions for FVTT Scene import (foundry-02).

Both actions are GM-only. ``foundry_import_preview`` is read-only and never
writes; ``foundry_import_commit`` delegates to the foundry import service, which
owns digest re-validation, the scene-03 authority gate and persistence. The
commit broadcasts through the connection pool attached by the composition root
(``WebAPI.attach_connection_pool``); without that wiring it still persists but
stays silent, exactly like before the attachment existed.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from src.application_actions import ApplicationAction
from src.webui.errors import error_payload

_ACTION_DOMAIN = "foundry"
_GAME_KEY = {"type": "string", "minLength": 1, "maxLength": 240}
_PREVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "game_key": _GAME_KEY,
        "source_version": {"type": "string", "minLength": 1, "maxLength": 16},
        "scene": {"type": "object"},
    },
    "required": ["game_key", "source_version", "scene"],
    "additionalProperties": False,
}
_COMMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "game_key": _GAME_KEY,
        "operation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "plan_digest": {"type": "string", "minLength": 1, "maxLength": 128},
        "plan": {"type": "object"},
        "policies": {"type": "object"},
    },
    "required": ["game_key", "operation_id", "plan_digest", "plan", "policies"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class FoundryToolDependencies:
    """Owner seams a foundry tool needs; never the WebAPI facade."""

    get_game: Callable[[str], Any]
    viewer_for: Callable[[Any, str], Any]
    preview: Callable[..., dict[str, Any]]
    commit: Callable[..., Any]


def _not_found() -> dict[str, Any]:
    return error_payload("webui.services.foundry_import.tactical_scene_not_found", code="tactical_scene_not_found")


def foundry_tools(dependencies: FoundryToolDependencies) -> tuple[ApplicationAction, ...]:
    def is_gm(args: dict[str, Any], owner: str) -> bool:
        game = dependencies.get_game(str(args.get("game_key", "")))
        return bool(game is not None and dependencies.viewer_for(game, owner).is_gm)

    def preview(args: dict[str, Any], owner: str) -> dict[str, Any]:
        game = dependencies.get_game(str(args["game_key"]))
        if game is None:
            return _not_found()
        viewer = dependencies.viewer_for(game, owner)
        return dependencies.preview(
            str(args["game_key"]), viewer, args.get("scene"),
            source_version=args.get("source_version"),
        )

    async def commit(args: dict[str, Any], owner: str) -> dict[str, Any]:
        game = dependencies.get_game(str(args["game_key"]))
        if game is None:
            return _not_found()
        viewer = dependencies.viewer_for(game, owner)
        result = dependencies.commit(
            str(args["game_key"]), viewer, args.get("plan"),
            plan_digest=args.get("plan_digest"),
            policies=args.get("policies"),
            operation_id=args.get("operation_id"),
        )
        return await result if inspect.isawaitable(result) else result

    return (
        ApplicationAction(
            "foundry_import_preview",
            "Preview a Foundry VTT Scene import as the GM",
            _PREVIEW_SCHEMA,
            preview,
            domain=_ACTION_DOMAIN,
            effect="read",
            mutation=False,
            authorize=is_gm,
            instructions=(
                "GM only. Convert one FVTT scene JSON for a declared source_version "
                "(v13/v14) and return the import plan plus plan_digest. Read-only: "
                "nothing is written. Unknown versions and unsafe payloads fail closed."
            ),
        ),
        ApplicationAction(
            "foundry_import_commit",
            "Commit a reviewed Foundry VTT Scene import plan as the GM",
            _COMMIT_SCHEMA,
            commit,
            domain=_ACTION_DOMAIN,
            effect="write",
            mutation=True,
            authorize=is_gm,
            instructions=(
                "GM only. Submit the exact previewed plan, its plan_digest, one "
                "create/update/duplicate/skip policy per item and a stable "
                "operation_id. A stale digest is rejected; repeating the same "
                "digest and operation_id returns the existing receipt."
            ),
        ),
    )


__all__ = ["FoundryToolDependencies", "foundry_tools"]