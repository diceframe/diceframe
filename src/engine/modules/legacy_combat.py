"""Read-only combat turn view derived from the bound ruleset's authority.

``combat_state`` / ``combat_active`` / ``initiative_order`` /
``initiative_current`` are NOT stored anywhere. They are derived on every read
from the bound ruleset's own encounter authority through a projector the
ruleset registers with :func:`register_combat_projector` (D&D 2024 projects
``ruleset_state["combat"]``). A game whose runtime has no registered projector
(unbound, generic rulesets, unknown runtime ids) reads the neutral view:
``"none"`` / ``False`` / ``[]`` / ``0``.

``src/engine`` must not import ``src/rulesets``; the registry is the seam.
Projectors are resolved through :func:`ruleset_runtime.bound_runtime_id`, a
read that never materializes or repairs a module slot.

This module owns no persisted state. The former ``modules["legacy_combat"]``
slot (and its generic ``combat_enemies`` list) was retired; instance
migration v42 -> v43 drops it from old saves. Generic hp_based /
lethal_narrative combat targets players by uid and NPCs by ``npc:<id>``. A
future generic encounter authority is a separate design, not this module.

The core flow reads the view only through ``module_hooks.combat_view``; without
this module every game reads the neutral view.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from src.engine import module_hooks
from src.engine.modules import ruleset_runtime

# Hook owner name (not MODULE_NAME: this module owns no slot).  Derived rather
# than spelled out: the retired slot name must
# not reappear as a literal (architecture guard).
HOOK_OWNER = __name__.rsplit(".", 1)[-1]

CombatProjector = Callable[[Any], "Mapping[str, Any] | None"]

_PROJECTORS: dict[str, CombatProjector] = {}


def register_combat_projector(runtime_id: str, projector: CombatProjector) -> None:
    """Register the combat view projector for one ruleset runtime id.

    ``projector(instance)`` returns a mapping with combat view fields, or
    ``None`` for the neutral view. A runtime may also supply the canonical
    ``current_actor_id``. Re-registering a runtime id replaces it.
    """
    normalized = str(runtime_id or "").strip()
    if not normalized:
        raise ValueError("combat projector requires a runtime id")
    if not callable(projector):
        raise TypeError("combat projector must be callable")
    _PROJECTORS[normalized] = projector


def combat_projector_for(runtime_id: str) -> CombatProjector | None:
    return _PROJECTORS.get(str(runtime_id or "").strip())


def neutral_view() -> dict[str, Any]:
    return module_hooks.neutral_combat_view()


def view(instance: Any) -> dict[str, Any]:
    """Return a fresh copy of the derived combat view (never live storage)."""
    projector = combat_projector_for(ruleset_runtime.bound_runtime_id(instance))
    raw = projector(instance) if projector is not None else None
    result = neutral_view()
    if not isinstance(raw, Mapping):
        return result
    state = raw.get("combat_state")
    result["combat_state"] = state if isinstance(state, str) and state else "none"
    result["combat_active"] = bool(raw.get("combat_active"))
    order = raw.get("initiative_order")
    result["initiative_order"] = deepcopy(list(order)) if isinstance(order, (list, tuple)) else []
    current = raw.get("initiative_current")
    result["initiative_current"] = current if type(current) is int else 0
    actor_id = raw.get("current_actor_id")
    if isinstance(actor_id, str):
        result["current_actor_id"] = actor_id
    return result


def combat_state(instance: Any) -> str:
    return view(instance)["combat_state"]


def combat_active(instance: Any) -> bool:
    return view(instance)["combat_active"]


def initiative_order(instance: Any) -> list[Any]:
    return view(instance)["initiative_order"]


def initiative_current(instance: Any) -> int:
    return view(instance)["initiative_current"]


def read_state(instance: Any, kind: str) -> Any:
    return view(instance) if kind == module_hooks.COMBAT_VIEW else None


module_hooks.register(module_hooks.STATE_READ, HOOK_OWNER, read_state)
