"""Complete a dnd5e draft candidate with user choices, then reconcile (m2-02).

Contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §3.2 (six rules), §6.16.
m2-01 deliberately leaves user decisions out of the draft candidate; this
module overlays the choices the caller collected (front end m2-03) and never
invents a missing one. Creation itself stays in the dnd2024 runtime: this
module only composes the draft and compares the runtime's recomputed sheet
with the dnd5e declared values (rule 4, dnd2024 is the only calculator).

The module has no rules engine and no persistence; it depends solely on the
m2-01 conversion result and dnd2024's public creation contract held by the
caller.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .actor_converter import ABILITY_IDS

DND2024_RUNTIME_ID = "core:dnd2024"

#: Fields a caller may collect and overlay onto the m2-01 draft candidate.
#: ``progression`` is not a draft field: it is only inspected to explain the
#: level-1 creation limit. ``subclass_ref`` is carried into ``features`` but a
#: level-1 draft owns no subclass feature, so it grants nothing on its own.
DRAFT_SELECTION_FIELDS: frozenset[str] = frozenset({
    "background_ability_bonuses",
    "equipment_package_ref",
    "background_equipment_package_ref",
    "language_refs",
    "species_size",
    "species_choice_answers",
    "species_skill_refs",
    "species_feat_refs",
    "class_skill_refs",
    "class_tool_refs",
    "feat_choice_answers",
    "feature_choices",
    "class_spell_choices",
    "subclass_ref",
    "name",
    "alignment",
    "locale",
    "progression",
})


class Dnd5eCreationError(ValueError):
    """Raised when a dnd5e draft cannot be completed without guessing."""

    def __init__(self, message: str, *, error_code: str = "dnd5e_creation_invalid") -> None:
        super().__init__(message)
        self.error_code = error_code


def is_dnd2024_binding(binding: Any) -> bool:
    """Whether a game/character binding belongs to the dnd2024 runtime.

    The concrete runtime id belongs to this dnd5e boundary, not to a generic
    service: generic backend paths must stay free of concrete ruleset names.
    """

    return (
        isinstance(binding, Mapping)
        and str(binding.get("id") or "") == DND2024_RUNTIME_ID
    )


def compose_draft(
    conversion: Mapping[str, Any],
    selections: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Overlay collected user choices onto the m2-01 draft candidate.

    Only :data:`DRAFT_SELECTION_FIELDS` are accepted; anything else is rejected
    instead of being silently dropped, so a caller cannot smuggle a mechanical
    field the conversion never produced. Unknown or missing choices are left
    for validation to report; nothing is defaulted here.
    """

    if not isinstance(conversion, Mapping):
        raise Dnd5eCreationError(
            "dnd5e conversion result must be an object",
            error_code="dnd5e_creation_invalid",
        )
    draft = conversion.get("draft")
    if not isinstance(draft, dict):
        raise Dnd5eCreationError(
            "dnd5e actor has no draft candidate (single-class character required)",
            error_code="dnd5e_draft_unavailable",
        )
    if selections is None:
        selections = {}
    if not isinstance(selections, Mapping):
        raise Dnd5eCreationError(
            "dnd5e import selections must be an object",
            error_code="dnd5e_selection_invalid",
        )
    unknown = sorted(set(selections) - DRAFT_SELECTION_FIELDS)
    if unknown:
        raise Dnd5eCreationError(
            "unsupported dnd5e import selection fields: " + ", ".join(unknown),
            error_code="dnd5e_selection_invalid",
        )

    composed = deepcopy(draft)
    features = composed.get("features")
    features = dict(features) if isinstance(features, Mapping) else {}
    for key, value in selections.items():
        if key == "progression":
            continue
        if key == "subclass_ref":
            if value:
                features["subclass_ref"] = str(value)
            continue
        composed[key] = deepcopy(value)
    if features:
        composed["features"] = features
    return composed


def has_progression_history(selections: Mapping[str, Any] | None) -> bool:
    """Whether the caller supplied a progression ledger for a level > 1 actor."""

    if not isinstance(selections, Mapping):
        return False
    progression = selections.get("progression")
    if not isinstance(progression, Mapping):
        return False
    history = progression.get("history")
    return isinstance(history, list) and bool(history)


def reconcile_runtime(
    conversion: Mapping[str, Any],
    finalized: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """List declared-vs-recomputed mismatches after dnd2024 finalize (rule 4).

    ``finalized`` is the dnd2024 legacy projection (it also carries
    ``ruleset_character``). Only fields the dnd5e actor declared are compared;
    a value the source did not persist is never reported as a mismatch.
    """

    display = conversion.get("display")
    display = display if isinstance(display, Mapping) else {}
    canonical = finalized.get("ruleset_character")
    canonical = canonical if isinstance(canonical, Mapping) else {}
    abilities = canonical.get("abilities")
    abilities = abilities if isinstance(abilities, Mapping) else {}
    resources = canonical.get("resources")
    resources = resources if isinstance(resources, Mapping) else {}
    derived = canonical.get("derived")
    derived = derived if isinstance(derived, Mapping) else {}

    entries: list[dict[str, Any]] = []

    def add(field: str, declared: Any, recomputed: Any, note: str) -> None:
        if declared is None or recomputed is None:
            return
        if declared == recomputed:
            return
        entries.append({
            "field": field,
            "declared": declared,
            "recomputed": recomputed,
            "note": note,
        })

    declared_abilities = display.get("abilities")
    declared_abilities = (
        declared_abilities if isinstance(declared_abilities, Mapping) else {}
    )
    for ability in ABILITY_IDS:
        entry = declared_abilities.get(ability)
        declared = entry.get("score") if isinstance(entry, Mapping) else None
        add(
            f"abilities.{ability}",
            declared,
            abilities.get(ability),
            "dnd2024 recomputed from base abilities plus the chosen background bonuses",
        )

    hp = display.get("hp")
    hp = hp if isinstance(hp, Mapping) else {}
    add(
        "hp.max",
        hp.get("max"),
        resources.get("max_hp"),
        "dnd2024 recomputed maximum HP from class, Constitution and species",
    )
    add(
        "hp.value",
        hp.get("value"),
        resources.get("hp"),
        "dnd2024 recomputed current HP from the maximum",
    )
    add(
        "armor_class",
        display.get("armor_class"),
        derived.get("armor_class"),
        "dnd2024 recomputed AC from the created equipment package and abilities",
    )
    add(
        "speed",
        display.get("speed"),
        derived.get("speed"),
        "dnd2024 recomputed Speed from species and features",
    )
    return entries


__all__ = [
    "DND2024_RUNTIME_ID",
    "DRAFT_SELECTION_FIELDS",
    "Dnd5eCreationError",
    "compose_draft",
    "has_progression_history",
    "is_dnd2024_binding",
    "reconcile_runtime",
]