"""Server-authoritative dispatch for the small U1 declarative action subset."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from src.application_actions import ActionRegistry
from src.webui.services.assistant_agent import validate_arguments


@dataclass(frozen=True)
class UiActionDependencies:
    registry: ActionRegistry


async def dispatch_ui_action(
    dependencies: UiActionDependencies,
    action_name: str,
    owner_id: str,
    arguments: Any,
) -> dict[str, Any]:
    owner = str(owner_id or "").strip()
    if not owner:
        raise PermissionError("UI action requires an authenticated owner")
    action = dependencies.registry.get(str(action_name or "").strip())
    if action is None:
        raise ValueError("UI action is not registered")
    # U1 never creates an approval bypass.  Only actions explicitly designed
    # for guarded automatic writes can be called by a declarative button.
    if not action.automatic or not action.mutation or action.effective_effect != "write":
        raise PermissionError("UI action requires an automatic guarded write")
    validated = validate_arguments(arguments, action.schema)
    if action.authorize is not None:
        allowed = action.authorize(validated, owner)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is False:
            raise PermissionError("UI action is not authorized")
    if action.guard is not None:
        action.guard(validated, owner)
    result = action.handler(validated, owner)
    if inspect.isawaitable(result):
        result = await result
    return {"ok": True, "action": action.name, "result": result}
