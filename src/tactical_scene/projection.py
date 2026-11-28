"""Server-side Scene read projections (B2).

Frozen whitelist: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.5.
The functions are pure: they take a validated ``TacticalSceneDocument`` dict, return a
fresh dict and never read an instance, resolve a reference or execute content.
Any field outside the whitelist stays out; a future field must be added here
with an explicit contract change.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Final

#: Top-level fields a seat may receive. ``placeables`` is added separately
#: because only the typed ``tokens``, ``walls`` and ``lights`` collections are
#: projected to a seat.
TACTICAL_SCENE_PLAYER_FIELDS: Final = (
    "schema_version", "kind", "canonical_id", "display_name", "navigation_name",
    "sort", "background", "foreground", "thumbnail", "dimensions", "grid",
    "environment", "levels", "revision",
)

#: GM/private scene fields that never reach a seat payload.
TACTICAL_SCENE_PLAYER_EXCLUDED_FIELDS: Final = (
    "ownership", "flags", "extensions", "provenance", "source_ref", "external_ids",
)

TOKEN_PLAYER_FIELDS: Final = (
    "canonical_id", "actor_ref", "position", "size", "rotation", "texture",
    "alpha", "display", "disposition", "bars", "locked",
)

TOKEN_PLAYER_EXCLUDED_FIELDS: Final = (
    "sight", "detection_modes", "light", "delta", "ring", "flags",
    "extensions", "provenance", "external_ids",
)

#: Typed non-token placeables a seat may receive (workplan §6.5). Walls and
#: lights are visible to every member. Notes link to journal entries whose
#: visibility has to be judged per entry, so they stay GM-only in this phase.
WALL_PLAYER_FIELDS: Final = (
    "canonical_id", "endpoints", "direction", "door", "door_state", "restrictions",
)
LIGHT_PLAYER_FIELDS: Final = (
    "canonical_id", "position", "rotation", "elevation", "radius", "color",
    "intensity", "angle",
)

#: FVTT token dispositions that are public to the table. ``secret`` (-2) is not
#: public; its actor reference is only kept for a viewer who owns the actor.
PUBLIC_TOKEN_DISPOSITIONS: Final = frozenset({-1, 0, 1})


def actor_ref_key(ref: Mapping[str, Any] | None) -> str:
    """Stable key for one ContentRef; display names are never matched."""

    if not isinstance(ref, Mapping):
        return ""
    return ":".join(
        str(ref.get(field, "")) for field in ("source_kind", "source_id", "kind", "id")
    )


def project_tactical_scene_gm(document: Mapping[str, Any]) -> dict[str, Any]:
    """GM projection: the complete validated TacticalSceneDocument."""

    return deepcopy(dict(document))


def _project_token(
    token: Mapping[str, Any],
    owned_actor_refs: frozenset[str],
    public_dispositions: frozenset[int],
) -> dict[str, Any]:
    projected = {
        field: deepcopy(token[field]) for field in TOKEN_PLAYER_FIELDS if field in token
    }
    actor_ref = token.get("actor_ref")
    if actor_ref is not None and (
        actor_ref_key(actor_ref) in owned_actor_refs
        or token.get("disposition") in public_dispositions
    ):
        projected["actor_ref"] = deepcopy(actor_ref)
    else:
        projected["actor_ref"] = None
    return projected


def _project_collection(
    placeables: Any,
    collection: str,
    fields: tuple[str, ...],
    *,
    skip_hidden: bool = False,
) -> dict[str, Any]:
    """Copy the whitelisted fields of every typed placeable in one collection."""

    raw = placeables.get(collection) if isinstance(placeables, Mapping) else None
    projected: dict[str, Any] = {}
    if isinstance(raw, Mapping):
        for key, item in raw.items():
            if not isinstance(item, Mapping):
                continue
            if skip_hidden and item.get("hidden") is True:
                continue
            projected[str(key)] = {
                field: deepcopy(item[field]) for field in fields if field in item
            }
    return projected


def project_tactical_scene_player(
    document: Mapping[str, Any],
    *,
    uid: str,
    owned_actor_refs: set[str],
    public_dispositions: frozenset[int] = PUBLIC_TOKEN_DISPOSITIONS,
) -> dict[str, Any]:
    """Seat projection: explicit whitelist, fail-closed for everything else.

    ``owned_actor_refs`` holds ``actor_ref_key()`` values for actors this viewer
    owns; ownership is resolved before the projection, never from names.
    """

    del uid  # reserved for future per-viewer rules; ownership arrives resolved
    owned = frozenset(owned_actor_refs)
    projection: dict[str, Any] = {
        field: deepcopy(document[field]) for field in TACTICAL_SCENE_PLAYER_FIELDS if field in document
    }
    tokens: dict[str, Any] = {}
    placeables = document.get("placeables")
    raw_tokens = placeables.get("tokens") if isinstance(placeables, Mapping) else None
    if isinstance(raw_tokens, Mapping):
        for token_id, token in raw_tokens.items():
            if not isinstance(token, Mapping) or token.get("hidden") is True:
                continue
            tokens[str(token_id)] = _project_token(token, owned, public_dispositions)
    # The three keys are always present so the seat shape stays stable.
    projection["placeables"] = {
        "tokens": tokens,
        "walls": _project_collection(placeables, "walls", WALL_PLAYER_FIELDS),
        "lights": _project_collection(
            placeables, "lights", LIGHT_PLAYER_FIELDS, skip_hidden=True,
        ),
    }
    return projection


__all__ = [
    "LIGHT_PLAYER_FIELDS",
    "PUBLIC_TOKEN_DISPOSITIONS",
    "TACTICAL_SCENE_PLAYER_EXCLUDED_FIELDS",
    "TACTICAL_SCENE_PLAYER_FIELDS",
    "TOKEN_PLAYER_EXCLUDED_FIELDS",
    "TOKEN_PLAYER_FIELDS",
    "WALL_PLAYER_FIELDS",
    "actor_ref_key",
    "project_tactical_scene_gm",
    "project_tactical_scene_player",
]