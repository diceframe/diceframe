"""Selection rules for contributed map definitions."""

from __future__ import annotations

from typing import Any


def map_definition_ref(definition: dict[str, Any]) -> str:
    return f"plugin:{definition.get('plugin_id', '')}:map:{definition.get('id', '')}"


def select_plugin_map(
    definitions: list[dict[str, Any]],
    map_id: str,
) -> dict[str, Any] | None:
    target = str(map_id or "")
    return next(
        (
            definition for definition in definitions
            if target in {str(definition.get("id") or ""), map_definition_ref(definition)}
        ),
        None,
    )


def select_map_definition(
    world_id: str,
    definitions: list[dict[str, Any]],
    preferred: str = "",
) -> dict[str, Any] | None:
    if not definitions:
        return None
    preferred = str(preferred or "").strip()
    if preferred:
        selected = next(
            (
                item for item in definitions
                if preferred in {str(item.get("id") or ""), map_definition_ref(item)}
            ),
            None,
        )
        if selected:
            return selected

    def score(item: dict[str, Any]) -> tuple[int, str, str]:
        declared = str(item.get("world_id") or "")
        worlds = (
            {str(value) for value in item.get("worlds", []) if str(value)}
            if isinstance(item.get("worlds"), list)
            else set()
        )
        specificity = 2 if declared == world_id else 1 if world_id in worlds else 0
        is_default = 1 if item.get("default") is True else 0
        return (
            specificity * 10 + is_default,
            str(item.get("plugin_id") or ""),
            str(item.get("id") or ""),
        )

    eligible = [
        item for item in definitions
        if item.get("default") is True
        or str(item.get("world_id") or "") == world_id
        or (
            isinstance(item.get("worlds"), list)
            and world_id in {str(value) for value in item["worlds"]}
        )
    ]
    return max(eligible, key=score, default=None)
