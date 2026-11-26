"""The Hunter's Defensive Tactics: attack-roll facts, not conditions.

Both options answer a question about a roll rather than about a creature's
state, so neither belongs in the condition registry:

* Escape the Horde only reaches Opportunity Attacks against the holder;
* Multiattack Defense is "this attacker already hit me this turn".

The second rides the shared ``attack_marks`` store (kind -> actor -> target)
that Studied Attacks already uses.  The row keeps the ``round`` / ``turn_index``
it was created in, so "this turn" is enforced by the read gate rather than by a
sweep; ``turns_remaining: 0`` only tells the next turn-start sweep to drop it.

The options are passives of the *defender*, so every reader asks the defender's
canonical sheet.  An un-answered choice grants nothing -- the resolver returns
"" and neither a source nor a mark is produced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..features.resolver import Dnd2024ClassFeatureResolver

HUNTER_SUBCLASS = "subclass:hunter"
DEFENSIVE_TACTICS = "defensive_tactics"
ESCAPE_THE_HORDE = "escape_the_horde"
MULTIATTACK_DEFENSE = "multiattack_defense"


class CombatDefensiveTacticsMixin:
    """Hunter 7's Defensive Tactics options, shared by both attack surfaces."""

    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by the combat engine and its other mixins; no runtime state.
        features: Dnd2024ClassFeatureResolver
        _feature_character: Callable[[dict[str, Any]], dict[str, Any]]

    def _defensive_tactics_option(self, actor: dict[str, Any]) -> str:
        """The chosen option id, or "" when none is committed."""

        if actor.get("kind") not in {"player", "companion"}:
            return ""
        return self.features.feature_option_choice(
            self._feature_character(actor),
            DEFENSIVE_TACTICS,
            owner=HUNTER_SUBCLASS,
        )

    def _multiattack_mark_active(
        self, combat: dict[str, Any], actor: dict[str, Any], target: dict[str, Any],
    ) -> bool:
        """Whether this attacker already hit this target *this turn*."""

        marks = combat.get("attack_marks")
        per_kind = (
            marks.get(MULTIATTACK_DEFENSE) if isinstance(marks, dict) else None
        )
        per_actor = (
            per_kind.get(str(actor["actor_id"])) if isinstance(per_kind, dict) else None
        )
        row = (
            per_actor.get(str(target["actor_id"])) if isinstance(per_actor, dict) else None
        )
        if not isinstance(row, dict):
            return False
        return (
            int(row.get("round", -1)) == int(combat.get("round", 0) or 0)
            and int(row.get("turn_index", -1)) == int(combat.get("turn_index", 0) or 0)
        )

    def _defensive_tactics_sources(
        self, combat: dict[str, Any], actor: dict[str, Any], target: dict[str, Any],
        *, opportunity: bool,
    ) -> list[dict[str, str]]:
        """Attack-mode sources this defender's chosen option contributes."""

        sources: list[dict[str, str]] = []
        if opportunity and self._defensive_tactics_option(target) == ESCAPE_THE_HORDE:
            sources.append({"code": ESCAPE_THE_HORDE, "mode": "disadvantage"})
        if self._multiattack_mark_active(combat, actor, target):
            sources.append({"code": MULTIATTACK_DEFENSE, "mode": "disadvantage"})
        return sources

    def _multiattack_mark_events(
        self, actor: dict[str, Any], target: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Mark an attacker that just hit a Hunter who chose Multiattack Defense.

        The first hit is not reached by this mark -- it is written after the
        roll resolves -- which is exactly the SRD's "all *other* attack rolls".
        """

        if self._defensive_tactics_option(target) != MULTIATTACK_DEFENSE:
            return []
        return [{
            "type": "dnd2024.attack_mark.set",
            "actor_id": str(actor["actor_id"]),
            "kind": MULTIATTACK_DEFENSE,
            "target_id": str(target["actor_id"]),
            "turns_remaining": 0,
            "expiry": "turn_end",
        }]
