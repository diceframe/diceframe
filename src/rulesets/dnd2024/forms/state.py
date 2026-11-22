"""Shape-shifting state: what is active, and what it does to the panel.

This is the seam between "a rule exists" and "the engine uses it".  An effect
declares *which* form and *which* policy; this module holds that declaration and
answers the two questions the rest of the engine actually asks:

* what panel do I play from right now (``active_panel``)?
* may I cast a spell / use my equipment (``restrictions``)?

Keeping these two answers here means the combat code never branches on "am I
shaped" — it asks the state, and the state is a plain value that survives being
written to a save.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .panel import (
    POLYMORPH_POLICY,
    WILD_SHAPE_POLICY,
    StatPanel,
    compose,
    panel_from_statblock,
)

#: Effect id -> policy table.  A new shape-shift adds a row here, not a branch.
EFFECT_POLICIES: dict[str, dict[str, str]] = {
    "wild_shape": WILD_SHAPE_POLICY,
    "polymorph": POLYMORPH_POLICY,
}

#: 哪些效果禁止施法。Wild Shape 在 18 级 Beast Spells 之后不在此列，由
#: ``beast_spells`` 字段单独放宽，而不是把整个效果从表里删掉。
NO_SPELLCASTING = frozenset({"wild_shape", "polymorph"})


class FormStateError(ValueError):
    """Raised when a form cannot be assumed or reverted."""


@dataclass(frozen=True)
class FormState:
    """One active shape-shift, as stored on the character."""

    effect_id: str
    form_ref: str
    form_name: str = ""
    duration_hours: int = 0
    assumed_at_turn: int = 0
    beast_spells: bool = False
    equipment_mode: str = "merge"
    #: 形态面板的来源快照不存——它由 ``form_ref`` 重新读出来，避免存档里
    #: 出现第二份可能过期的数值。
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def policy(self) -> dict[str, str]:
        try:
            return EFFECT_POLICIES[self.effect_id]
        except KeyError as exc:
            raise FormStateError(f"unknown shape-shift effect: {self.effect_id}") from exc

    def as_payload(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "form_ref": self.form_ref,
            "form_name": self.form_name,
            "duration_hours": self.duration_hours,
            "assumed_at_turn": self.assumed_at_turn,
            "beast_spells": self.beast_spells,
            "equipment_mode": self.equipment_mode,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FormState":
        effect_id = str(payload.get("effect_id") or "")
        form_ref = str(payload.get("form_ref") or "")
        if effect_id not in EFFECT_POLICIES:
            raise FormStateError(f"unknown shape-shift effect: {effect_id!r}")
        if not form_ref:
            raise FormStateError("shape-shift state has no form_ref")
        mode = str(payload.get("equipment_mode") or "merge")
        if mode not in {"drop", "merge", "wear"}:
            raise FormStateError(f"unknown equipment mode: {mode!r}")
        try:
            duration_hours = payload.get("duration_hours") or 0
            assumed_at_turn = payload.get("assumed_at_turn") or 0
            if isinstance(duration_hours, bool) or isinstance(assumed_at_turn, bool):
                raise ValueError
            duration_hours = int(duration_hours)
            assumed_at_turn = int(assumed_at_turn)
        except (TypeError, ValueError, OverflowError) as exc:
            raise FormStateError("shape-shift turn metadata is invalid") from exc
        if duration_hours < 0 or assumed_at_turn < 0:
            raise FormStateError("shape-shift turn metadata is invalid")
        return cls(
            effect_id=effect_id,
            form_ref=form_ref,
            form_name=str(payload.get("form_name") or ""),
            duration_hours=duration_hours,
            assumed_at_turn=assumed_at_turn,
            beast_spells=bool(payload.get("beast_spells")),
            equipment_mode=mode,
        )


def assume_form(
    character_panel: StatPanel, entry: dict[str, Any], state: FormState,
) -> StatPanel:
    """The panel the creature plays from while this state is active."""

    form_panel = panel_from_statblock(entry)
    merged = compose(character_panel, form_panel, state.policy)
    return StatPanel(**{**merged.__dict__, "sources": dict(merged.sources)})


def restrictions(state: FormState) -> dict[str, bool]:
    """What the shape costs you while it lasts.

    ``spellcasting`` is the only hard one in the SRD; equipment follows the
    declared mode (merged equipment has no effect, it is not merely hidden).
    """

    allow_spells = state.beast_spells or state.effect_id not in NO_SPELLCASTING
    return {
        "spellcasting": allow_spells,
        "equipment_effects": state.equipment_mode != "merge",
        "equipment_visible": state.equipment_mode != "merge",
    }


def wild_shape_state(
    form_entry: dict[str, Any], *, druid_level: int, beast_spells: bool,
    equipment_mode: str = "merge", assumed_at_turn: int = 0,
) -> FormState:
    """Build the state a Wild Shape use produces.

    Duration is half the druid level in hours (SRD p42); the caller passes the
    level rather than this module looking it up, so the level question stays with
    the class that owns it.
    """

    creature_type = str(form_entry.get("creature_type") or "")
    if creature_type != "beast":
        raise FormStateError("Wild Shape needs a beast stat block")
    return FormState(
        effect_id="wild_shape",
        form_ref=str(form_entry.get("id") or ""),
        form_name=str(form_entry.get("name") or ""),
        duration_hours=max(1, int(druid_level) // 2),
        assumed_at_turn=int(assumed_at_turn),
        beast_spells=bool(beast_spells),
        equipment_mode=equipment_mode,
    )


__all__ = [
    "EFFECT_POLICIES",
    "NO_SPELLCASTING",
    "FormState",
    "FormStateError",
    "assume_form",
    "restrictions",
    "wild_shape_state",
]
