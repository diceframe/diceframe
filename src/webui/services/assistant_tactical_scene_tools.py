"""Agent actions for Scene projections and GM writes (B2 / scene-03).

The read actions expose the same read service as the HTTP route; they hold no
visibility logic and never write. The GM write actions delegate to the write
service, which owns authority, stale guards and persistence. Whitelist filtering
stays in the service and projection boundary, so an agent cannot receive more
than its viewer role.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from src.application_actions import ApplicationAction
from src.tactical_scene.contract import PLACEABLE_COLLECTIONS
from src.webui.errors import error_payload

_ACTION_DOMAIN = "tactical_scene"
_TACTICAL_SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "game_key": {"type": "string", "minLength": 1, "maxLength": 240},
        "scene_id": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$",
        },
    },
    "required": ["game_key"],
    "additionalProperties": False,
}
_TACTICAL_SCENE_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "game_key": {"type": "string", "minLength": 1, "maxLength": 240},
        "expected_revision": {"type": "integer", "minimum": 0},
        "scene": {"type": "object"},
    },
    "required": ["game_key", "expected_revision", "scene"],
    "additionalProperties": False,
}
_TACTICAL_SCENE_UPDATE_SCHEMA = {
    **_TACTICAL_SCENE_WRITE_SCHEMA,
    "properties": {**_TACTICAL_SCENE_WRITE_SCHEMA["properties"], "scene_id": _TACTICAL_SCENE_SCHEMA["properties"]["scene_id"]},
    "required": ["game_key", "scene_id", "expected_revision", "scene"],
}
_PLACEABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "game_key": {"type": "string", "minLength": 1, "maxLength": 240},
        "scene_id": _TACTICAL_SCENE_SCHEMA["properties"]["scene_id"],
        "collection": {"type": "string", "enum": list(PLACEABLE_COLLECTIONS)},
        "expected_revision": {"type": "integer", "minimum": 0},
        "placeable": {"type": "object"},
    },
    "required": ["game_key", "scene_id", "collection", "expected_revision", "placeable"],
    "additionalProperties": False,
}
_PLACEABLE_IDENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "game_key": {"type": "string", "minLength": 1, "maxLength": 240},
        "scene_id": _TACTICAL_SCENE_SCHEMA["properties"]["scene_id"],
        "collection": {"type": "string", "enum": list(PLACEABLE_COLLECTIONS)},
        "placeable_id": _TACTICAL_SCENE_SCHEMA["properties"]["scene_id"],
        "expected_revision": {"type": "integer", "minimum": 0},
    },
    "required": ["game_key", "scene_id", "collection", "placeable_id", "expected_revision"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TacticalSceneToolDependencies:
    """Owner seams a scene tool needs; never the WebAPI facade."""

    get_game: Callable[[str], Any]
    viewer_for: Callable[[Any, str], Any]
    read_tactical_scene: Callable[[str, Any, str | None], dict[str, Any]]
    apply_op: Callable[..., Any] | None = None


def _not_found() -> dict[str, Any]:
    return error_payload("webui.services.tactical_scene_read.tactical_scene_not_found", code="tactical_scene_not_found")


def tactical_scene_tools(dependencies: TacticalSceneToolDependencies) -> tuple[ApplicationAction, ...]:
    def _read(args: dict[str, Any], owner: str) -> dict[str, Any]:
        game = dependencies.get_game(str(args["game_key"]))
        if game is None:
            return _not_found()
        viewer = dependencies.viewer_for(game, owner)
        scene_id = args.get("scene_id")
        return dependencies.read_tactical_scene(
            str(args["game_key"]), viewer, str(scene_id) if scene_id else None,
        )

    def read_tactical_scene(args: dict[str, Any], owner: str) -> dict[str, Any]:
        return _read(args, owner)

    def read_visibility(args: dict[str, Any], owner: str) -> dict[str, Any]:
        result = _read(args, owner)
        if result.get("ok"):
            game = dependencies.get_game(str(args["game_key"]))
            viewer = dependencies.viewer_for(game, owner)
            result["visibility"] = {
                "viewer_kind": viewer.kind,
                "full_projection": bool(viewer.is_gm),
            }
        return result

    actions = [
        ApplicationAction(
            "tactical_scene_read",
            "Read one Scene through the caller's visibility whitelist",
            _TACTICAL_SCENE_SCHEMA,
            read_tactical_scene,
            domain=_ACTION_DOMAIN,
            effect="read",
            instructions=(
                "Read a scene by scene_id, or the bound active scene when scene_id "
                "is omitted. GM calls receive the full document; seat calls receive "
                "only the public whitelist (hidden tokens and GM fields are absent)."
            ),
        ),
        ApplicationAction(
            "tactical_scene_visibility_read",
            "Read a Scene plus the caller's visibility classification",
            _TACTICAL_SCENE_SCHEMA,
            read_visibility,
            domain=_ACTION_DOMAIN,
            effect="read",
            instructions=(
                "Same payload as tactical_scene_read plus a visibility block stating whether "
                "the caller received the full or player projection. Read-only."
            ),
        ),
    ]

    if dependencies.apply_op is not None:
        def is_gm(args: dict[str, Any], owner: str) -> bool:
            game = dependencies.get_game(str(args["game_key"]))
            return bool(game is not None and dependencies.viewer_for(game, owner).is_gm)

        async def apply(kind: str, args: dict[str, Any], owner: str, **fields: Any) -> dict[str, Any]:
            game = dependencies.get_game(str(args["game_key"]))
            if game is None:
                return _not_found()
            viewer = dependencies.viewer_for(game, owner)
            result = dependencies.apply_op(
                str(args["game_key"]), viewer, {"kind": kind, **fields},
                expected_revision=args["expected_revision"],
            )
            return await result if inspect.isawaitable(result) else result

        async def create_tactical_scene(args, owner):
            return await apply("tactical_scene_create", args, owner, scene=args.get("scene"))

        async def update_tactical_scene(args, owner):
            return await apply(
                "tactical_scene_update", args, owner,
                scene_id=args["scene_id"], scene=args.get("scene"),
            )

        async def create_placeable(args, owner):
            return await apply(
                "tactical_scene_placeable_create", args, owner,
                scene_id=args["scene_id"], collection=args["collection"],
                placeable=args.get("placeable"),
            )

        async def update_placeable(args, owner):
            return await apply(
                "tactical_scene_placeable_update", args, owner,
                scene_id=args["scene_id"], collection=args["collection"],
                placeable_id=args["placeable_id"], placeable=args.get("placeable"),
            )

        async def delete_placeable(args, owner):
            return await apply(
                "tactical_scene_placeable_delete", args, owner,
                scene_id=args["scene_id"], collection=args["collection"],
                placeable_id=args["placeable_id"],
            )

        async def set_active(args, owner):
            return await apply(
                "tactical_scene_set_active", args, owner, scene_id=args["scene_id"],
            )

        async def delete_tactical_scene(args, owner):
            return await apply(
                "tactical_scene_delete", args, owner, scene_id=args["scene_id"],
            )

        actions.extend((
            ApplicationAction(
                "tactical_scene_create", "Create a Scene document as the GM", _TACTICAL_SCENE_WRITE_SCHEMA,
                create_tactical_scene, domain=_ACTION_DOMAIN, effect="write", mutation=True,
                authorize=is_gm,
                instructions=(
                    "GM only. Supply a full scene document with a unique canonical_id and "
                    "the current expected_revision. Duplicate identity is rejected."
                ),
            ),
            ApplicationAction(
                "tactical_scene_update", "Replace an existing Scene document as the GM",
                _TACTICAL_SCENE_UPDATE_SCHEMA, update_tactical_scene, domain=_ACTION_DOMAIN,
                effect="write", mutation=True, authorize=is_gm,
                instructions=(
                    "GM only. Replace the complete document; canonical_id must match the "
                    "target and cannot change. expected_revision guards concurrent writes."
                ),
            ),
            ApplicationAction(
                "tactical_scene_placeable_create", "Add a placeable to a Scene as the GM",
                _PLACEABLE_SCHEMA, create_placeable, domain=_ACTION_DOMAIN,
                effect="write", mutation=True, authorize=is_gm,
                instructions=(
                    "GM only. Tokens are validated by the token contract; other "
                    "placeables use the shared envelope. canonical_id must be unique."
                ),
            ),
            ApplicationAction(
                "tactical_scene_placeable_update", "Update a placeable in a Scene as the GM",
                {**_PLACEABLE_SCHEMA,
                 "properties": {**_PLACEABLE_SCHEMA["properties"],
                                "placeable_id": _TACTICAL_SCENE_SCHEMA["properties"]["scene_id"]},
                 "required": [*_PLACEABLE_SCHEMA["required"], "placeable_id"]},
                update_placeable, domain=_ACTION_DOMAIN, effect="write",
                mutation=True, authorize=is_gm,
                instructions=(
                    "GM only. canonical_id must match placeable_id and cannot change."
                ),
            ),
            ApplicationAction(
                "tactical_scene_placeable_delete", "Delete a placeable from a Scene as the GM",
                _PLACEABLE_IDENTITY_SCHEMA, delete_placeable, domain=_ACTION_DOMAIN,
                effect="destructive", mutation=True, authorize=is_gm,
                instructions="GM only. Deleting a placeable is destructive.",
            ),
            ApplicationAction(
                "tactical_scene_set_active", "Bind the game's active Scene as the GM",
                {**_TACTICAL_SCENE_UPDATE_SCHEMA, "properties": {
                    "game_key": _TACTICAL_SCENE_SCHEMA["properties"]["game_key"],
                    "scene_id": _TACTICAL_SCENE_SCHEMA["properties"]["scene_id"],
                    "expected_revision": _TACTICAL_SCENE_WRITE_SCHEMA["properties"]["expected_revision"],
                }, "required": ["game_key", "scene_id", "expected_revision"]},
                set_active, domain=_ACTION_DOMAIN, effect="write", mutation=True,
                authorize=is_gm,
                instructions=(
                    "GM only. Resolve an explicit canonical scene_id; a missing or "
                    "stale binding is rejected rather than guessed."
                ),
            ),
            ApplicationAction(
                "tactical_scene_delete", "Delete a Scene document as the GM",
                {**_TACTICAL_SCENE_UPDATE_SCHEMA, "properties": {
                    "game_key": _TACTICAL_SCENE_SCHEMA["properties"]["game_key"],
                    "scene_id": _TACTICAL_SCENE_SCHEMA["properties"]["scene_id"],
                    "expected_revision": _TACTICAL_SCENE_WRITE_SCHEMA["properties"]["expected_revision"],
                }, "required": ["game_key", "scene_id", "expected_revision"]},
                delete_tactical_scene, domain=_ACTION_DOMAIN, effect="destructive",
                mutation=True, authorize=is_gm,
                instructions=(
                    "GM only. Deleting a scene removes its document and unbinds the "
                    "active reference when it pointed at the deleted scene. Destructive."
                ),
            ),
        ))

    return tuple(actions)


__all__ = ["TacticalSceneToolDependencies", "tactical_scene_tools"]