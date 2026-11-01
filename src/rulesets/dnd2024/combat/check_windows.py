"""Post-roll d20 check windows for D&D 2024 combat.

A check window opens *after* a d20 check (or a damage roll) has been made and
*before* its consequences land.  The roller's dice are already on the table and
visible in the pending decision; a window owner may spend a resource to change
the result, and everyone else declines.

Two decision kinds share this machinery:

* ``check_window`` — the roll is a d20 check (attack roll, death save, or a
  concentration save). Outcomes: Lucky's disadvantage face rerolls one d20 and
  keeps the lower natural result; Cutting Words subtracts a Bardic Inspiration
  die from the total; Dark One's Own Luck adds a d10.
* ``damage_window`` — the roll is a damage roll. Cutting Words subtracts a
  Bardic Inspiration die from the damage after resistance is applied.

Invariants:

* The straight-through path is untouched: a window is only offered when a
  qualified owner exists, and declining emits exactly the events the original
  path would have emitted (the pending/cleared pair aside).
* Window dice are rolled at *resolve* time, never folded into the pre-existing
  dice sequence (``SP2`` §3.8 RNG order).
* Every consumption goes through ``dnd2024.class_resource.spent`` and, for
  reaction-priced windows, ``dnd2024.reaction.spent``; the reducer needs no
  new branch.
* The registry is the single place that names window codes and their
  resource/feature/reaction requirements. Resolution and validation only look
  up entries; no rule branches on a concrete feature id.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.engine.player_control import is_ai_controlled
from src.rulesets.dnd2024.features.resources import resource_state

from .check_modes import mode_from_sources
from .primitives import (
    CombatIntentError,
    actor_kind,
    canonical as _canonical,
    player_actor as _player_actor,
)


CHECK_WINDOW_DECISION = "check_window"
DAMAGE_WINDOW_DECISION = "damage_window"
LUCKY_RESOURCE = "lucky_points"
BARDIC_RESOURCE = "bardic_inspiration"
DOOL_RESOURCE = "dark_ones_own_luck"


@dataclass(frozen=True, slots=True)
class WindowEntry:
    """One registered window: its surface, resource, feature, and cost."""

    code: str
    surface: str  # "attack_check" | "attack_damage" | "save"
    resource_id: str
    feature_id: str
    uses_reaction: bool


# The only registry of window codes. Adding a consumer means adding an entry
# and, at most, one eligibility predicate -- never a branch in resolution.py.
WINDOW_REGISTRY: dict[str, WindowEntry] = {
    "lucky_disadvantage": WindowEntry(
        code="lucky_disadvantage", surface="attack_check",
        resource_id=LUCKY_RESOURCE, feature_id="", uses_reaction=False,
    ),
    "cutting_words_check": WindowEntry(
        code="cutting_words_check", surface="attack_check",
        resource_id=BARDIC_RESOURCE, feature_id="cutting_words",
        uses_reaction=True,
    ),
    "dark_ones_own_luck": WindowEntry(
        code="dark_ones_own_luck", surface="save",
        resource_id=DOOL_RESOURCE, feature_id="dark_ones_own_luck",
        uses_reaction=False,
    ),
    "cutting_words_damage": WindowEntry(
        code="cutting_words_damage", surface="attack_damage",
        resource_id=BARDIC_RESOURCE, feature_id="cutting_words",
        uses_reaction=True,
    ),
}

# Cutting Words reaches a creature the bard can see within 60 feet.
CUTTING_WORDS_RANGE_FT = 60


class CombatCheckWindowsMixin:
    """Offer, validate, and finish post-roll check/damage windows."""

    __slots__ = ()

    # ------------------------------------------------------------------ pools

    def _canonical_actor_character(self, instance: Any, actor_id: str) -> dict[str, Any]:
        """The canonical character that owns ``actor_id``'s resource pools.

        The combat actor view only projects pools that a feature declares by
        ``resource_id``, so window owners must read the canonical sheet.  Lucky,
        Bardic Inspiration, and Dark One's Own Luck are owned through their
        resource entries, not through a second ownership table.
        """

        kind, raw_id = actor_kind(actor_id)
        if kind == "player":
            if raw_id not in instance.players:
                return {}
            return _canonical(instance.get_character_sheet(raw_id))
        if kind == "companion":
            return self._companion(instance, raw_id).get("ruleset_character") or {}
        return {}

    def _window_pool_state(
        self, instance: Any, actor_id: str, resource_id: str,
    ) -> dict[str, Any]:
        return dict(resource_state(self._canonical_actor_character(instance, actor_id), resource_id))

    def _window_pool_available(
        self, instance: Any, actor_id: str, resource_id: str,
    ) -> bool:
        state = self._window_pool_state(instance, actor_id, resource_id)
        return bool(state) and int(state.get("current", 0) or 0) >= 1

    # -------------------------------------------------------------- eligibility

    def _window_die_sides(self, actor: dict[str, Any], code: str) -> int:
        """The die a window rolls for ``actor``, or 0 when it is unusable."""

        entry = WINDOW_REGISTRY.get(code)
        if entry is None:
            return 0
        if code == "lucky_disadvantage":
            sides = 20
        elif entry.feature_id:
            sides = self.features.feature_value(
                self._feature_character(actor), entry.feature_id, "die_sides", 0,
            )
        else:  # pragma: no cover - every feature window resolves above
            return 0
        if isinstance(sides, bool) or not isinstance(sides, int) or not 2 <= sides <= 100:
            return 0
        return sides

    def _cutting_words_bards(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        exclude: tuple[str, ...] = (),
    ) -> list[str]:
        """Party bards that can see ``attacker`` and still react.

        Ordered by actor id so multi-bard parties pick the same bard every
        time.  ``exclude`` carries bards that already spent on this attack's
        check face (one face per attack).
        """

        if attacker.get("side") != "enemy":
            return []
        if "invisible" in (attacker.get("conditions") or {}):
            return []
        order = [str(item) for item in combat.get("initiative", [])]
        candidates = sorted(
            [_player_actor(uid) for uid in instance.players]
            + [f"companion:{cid}" for cid in self._active_companions(instance)]
        )
        bards: list[str] = []
        for actor_id in candidates:
            if actor_id in exclude or actor_id not in order:
                continue
            actor = self._actor_view(instance, combat, actor_id)
            if actor.get("side") != "party" or int(actor.get("hp", 0) or 0) <= 0:
                continue
            if "blinded" in (actor.get("conditions") or {}):
                continue
            if not self._reaction_eligible(combat, actor):
                continue
            if not self.features.has_feature(
                self._feature_character(actor), "cutting_words",
            ):
                continue
            if not self._window_pool_available(instance, actor_id, BARDIC_RESOURCE):
                continue
            if self._window_die_sides(actor, "cutting_words_check") == 0:
                continue
            if self._distance(combat, actor_id, str(attacker["actor_id"])) > CUTTING_WORDS_RANGE_FT:
                continue
            bards.append(actor_id)
        return bards

    def _attack_check_candidate(
        self, instance: Any, combat: dict[str, Any], code: str,
        attacker: dict[str, Any], defender: dict[str, Any], check: dict[str, Any],
        exclude_bards: tuple[str, ...] = (),
    ) -> str:
        """Return the owner actor id for ``code`` on this attack, or ``""``."""

        if code == "lucky_disadvantage":
            if defender.get("kind") not in {"player", "companion"}:
                return ""
            if not check.get("success"):
                return ""
            # Already disadvantaged: a second disadvantage source adds nothing.
            if str(check.get("advantage_mode") or "") == "disadvantage":
                return ""
            # A natural 1 always misses; lowering it changes nothing.
            if int(check.get("natural", 0) or 0) == 1:
                return ""
            if not self._window_pool_available(instance, str(defender["actor_id"]), LUCKY_RESOURCE):
                return ""
            return str(defender["actor_id"])
        if code == "cutting_words_check":
            if not check.get("success"):
                return ""
            bards = self._cutting_words_bards(instance, combat, attacker, exclude_bards)
            return bards[0] if bards else ""
        return ""

    def _dool_owner(
        self, instance: Any, actor: dict[str, Any], *, failed: bool,
    ) -> str:
        actor_id = str(actor["actor_id"])
        if not failed:
            return ""
        if actor.get("kind") not in {"player", "companion"}:
            return ""
        if not self.features.has_feature(
            self._feature_character(actor), "dark_ones_own_luck",
        ):
            return ""
        if not self._window_pool_available(instance, actor_id, DOOL_RESOURCE):
            return ""
        if self._window_die_sides(actor, "dark_ones_own_luck") == 0:
            return ""
        return actor_id

    def _death_save_check_window(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        check: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Open Dark One's Own Luck on a failed death save, or ``None``.

        Natural 1 already costs two failures regardless of the total, and a
        natural 20 is not part of the failing band, so neither opens a window.
        """

        roll = int(check.get("natural", 0) or 0)
        if roll == 1 or roll >= 10:
            return None
        owner_id = self._dool_owner(instance, actor, failed=True)
        if not owner_id:
            return None
        return self._build_check_window(
            instance, code="dark_ones_own_luck", surface="death_save",
            owner_id=owner_id, check=check, settlement={},
            source=str(actor["actor_id"]), roller_id=str(actor["actor_id"]),
            attacker_id="", target_id="", remaining_windows=[],
            die_sides=self._window_die_sides(actor, "dark_ones_own_luck"),
        )

    def _concentration_with_dool(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        damage: int, rng: Any,
    ) -> list[dict[str, Any]]:
        """Concentration save events, pausing before the failure lands.

        ``_concentration_events`` is untouched: this wrapper only replaces the
        failure tail when Dark One's Own Luck can still change it. A
        successful save (or no DOOL owner) is returned byte-for-byte.
        """

        events = self._concentration_events(target, damage, rng)
        if not events or events[0].get("success"):
            return events
        owner_id = self._dool_owner(instance, target, failed=True)
        if not owner_id:
            return events
        check = deepcopy(events[0])
        window = self._build_check_window(
            instance, code="dark_ones_own_luck", surface="concentration",
            owner_id=owner_id, check=check, settlement={"damage": int(damage)},
            source=str(target["actor_id"]), roller_id=str(target["actor_id"]),
            attacker_id="", target_id=str(target["actor_id"]),
            remaining_windows=[],
            die_sides=self._window_die_sides(target, "dark_ones_own_luck"),
        )
        return [{"type": "dnd2024.decision.pending", **window}]

    # -------------------------------------------------------------- offer shape

    def _window_assigned_to(self, instance: Any, owner_id: str) -> str:
        kind, raw_id = actor_kind(owner_id)
        human = kind == "player" and not is_ai_controlled(instance, raw_id)
        return raw_id if human else str(getattr(instance, "gm_uid", "") or "")

    def _build_check_window(
        self, instance: Any, *, code: str, surface: str, owner_id: str,
        check: dict[str, Any], settlement: dict[str, Any] | None,
        source: str, roller_id: str, attacker_id: str, target_id: str,
        remaining_windows: list[str], die_sides: int,
    ) -> dict[str, Any]:
        entry = WINDOW_REGISTRY[code]
        return {
            "decision_id": f"check-window:{code}:{source}:{roller_id}",
            "kind": CHECK_WINDOW_DECISION,
            "surface": surface,
            "window_code": code,
            "window_owner_id": owner_id,
            "actor_id": roller_id,
            "attacker_id": attacker_id,
            "target_id": target_id,
            "assigned_to": self._window_assigned_to(instance, owner_id),
            "options": ["spend", "decline"],
            "check": deepcopy(check),
            "settlement": deepcopy(settlement),
            "remaining_windows": list(remaining_windows),
            "resource_id": entry.resource_id,
            "die_sides": die_sides,
        }

    def _attack_check_window_offer(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        defender: dict[str, Any], check: dict[str, Any], settlement: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The first check-face window of this attack, or ``None``.

        Only hostile attacks against a party defender are eligible: Lucky's
        disadvantage face protects the target, and Cutting Words protects an
        ally the bard can see. A friendly attack never opens either window.
        """

        if attacker.get("side") == defender.get("side"):
            return None
        if defender.get("side") != "party":
            return None
        codes = [
            code for code in ("lucky_disadvantage", "cutting_words_check")
            if self._attack_check_candidate(instance, combat, code, attacker, defender, check)
        ]
        if not codes:
            return None
        code = codes[0]
        owner_id = self._attack_check_candidate(instance, combat, code, attacker, defender, check)
        settlement["remaining_windows"] = codes[1:]
        return self._build_check_window(
            instance, code=code, surface="attack", owner_id=owner_id, check=check,
            settlement=settlement, source=str(settlement["intent"].get("intent_id") or ""),
            roller_id=str(attacker["actor_id"]), attacker_id=str(attacker["actor_id"]),
            target_id=str(defender["actor_id"]), remaining_windows=codes[1:],
            die_sides=self._window_die_sides(
                defender if code == "lucky_disadvantage" else self._actor_view(instance, combat, owner_id),
                code,
            ),
        )

    def _next_attack_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Re-derive the remaining check-face chain against the new result."""

        attacker = self._actor_view(instance, combat, str(decision["attacker_id"]))
        defender = self._actor_view(instance, combat, str(decision["target_id"]))
        codes = [str(item) for item in decision.get("remaining_windows") or []]
        exclude = tuple(str(item) for item in settlement.get("exclude_bards") or [])
        for code in codes:
            owner_id = self._attack_check_candidate(
                instance, combat, code, attacker, defender, check, exclude,
            )
            if not owner_id:
                continue
            remaining = codes[codes.index(code) + 1:]
            settlement["remaining_windows"] = remaining
            return self._build_check_window(
                instance, code=code, surface="attack", owner_id=owner_id, check=check,
                settlement=settlement,
                source=str(settlement["intent"].get("intent_id") or ""),
                roller_id=str(attacker["actor_id"]), attacker_id=str(attacker["actor_id"]),
                target_id=str(defender["actor_id"]), remaining_windows=remaining,
                die_sides=self._window_die_sides(
                    defender if code == "lucky_disadvantage"
                    else self._actor_view(instance, combat, owner_id),
                    code,
                ),
            )
        return None

    def _damage_window_offer(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        defender: dict[str, Any], damage_event: dict[str, Any],
        intent: dict[str, Any], *, exclude_bards: tuple[str, ...] = (),
        next_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The Cutting Words damage-face window, or ``None``.

        Offered only for hostile attacks against a party defender whose
        damage is positive; the same bard cannot spend on both faces of one
        attack (``exclude_bards`` carries the check-face spender).
        """

        if attacker.get("side") == defender.get("side"):
            return None
        if defender.get("side") != "party":
            return None
        if int(damage_event.get("amount", 0) or 0) <= 0:
            return None
        bards = self._cutting_words_bards(instance, combat, attacker, exclude_bards)
        if not bards:
            return None
        owner_id = bards[0]
        die_sides = self._window_die_sides(
            self._actor_view(instance, combat, owner_id), "cutting_words_damage",
        )
        if die_sides == 0:
            return None
        return {
            "decision_id": f"damage-window:cutting_words_damage:"
                           f"{intent.get('intent_id', '')}:{defender['actor_id']}",
            "kind": DAMAGE_WINDOW_DECISION,
            "surface": "damage",
            "window_code": "cutting_words_damage",
            "window_owner_id": owner_id,
            "actor_id": str(attacker["actor_id"]),
            "attacker_id": str(attacker["actor_id"]),
            "target_id": str(defender["actor_id"]),
            "assigned_to": self._window_assigned_to(instance, owner_id),
            "options": ["spend", "decline"],
            "damage_event": deepcopy(damage_event),
            "damage_sides": die_sides,
            "resource_id": BARDIC_RESOURCE,
            "next_decision": deepcopy(next_decision) if next_decision else None,
        }

    # -------------------------------------------------------------- validation

    def _validate_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any],
    ) -> None:
        """Re-check ownership and cost at resolve time, not just at offer time."""

        if intent.get("option") != "spend":
            return
        entry = WINDOW_REGISTRY.get(str(decision.get("window_code") or ""))
        if entry is None:
            raise CombatIntentError("this check window is not available")
        owner_id = str(decision.get("window_owner_id") or "")
        owner = self._actor_view(instance, combat, owner_id)
        if not self._window_pool_available(instance, owner_id, entry.resource_id):
            raise CombatIntentError("this check window resource is already spent")
        if entry.feature_id and not self.features.has_feature(
            self._feature_character(owner), entry.feature_id,
        ):
            raise CombatIntentError("this check window feature is not owned")
        if entry.uses_reaction and not self._reaction_eligible(combat, owner):
            raise CombatIntentError("the reaction is not available")

    # ----------------------------------------------------------------- finish

    def _finish_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        entry = WINDOW_REGISTRY[str(decision["window_code"])]
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        check = deepcopy(decision["check"])
        spent = intent["option"] == "spend"
        if spent:
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": decision["window_owner_id"],
                "resource_id": entry.resource_id,
            })
            if entry.uses_reaction:
                events.append({
                    "type": "dnd2024.reaction.spent", "actor_id": decision["window_owner_id"],
                    "reason": "reaction_feature",
                })
            die = int(rng.randint(1, int(decision["die_sides"])))
            check = self._spend_check_window(decision, check, die)
        surface = str(decision.get("surface") or "")
        if surface == "attack":
            return self._finish_attack_check_window(
                instance, combat, decision, check, spent, events, rng,
            )
        if surface == "death_save":
            return self._finish_death_save_window(
                instance, combat, decision, check, events, rng,
            )
        if surface == "concentration":
            return self._finish_concentration_window(decision, check, events)
        raise CombatIntentError("this check window surface is not supported")  # pragma: no cover

    def _spend_check_window(
        self, decision: dict[str, Any], check: dict[str, Any], die: int,
    ) -> dict[str, Any]:
        """Apply one spent window die to the held check event."""

        code = str(decision["window_code"])
        window = {
            "window": code,
            "window_resource": str(decision["resource_id"]),
            "window_roll": die,
        }
        if code == "lucky_disadvantage":
            # Lucky imposes disadvantage: append the lucky d20 and keep the
            # lower natural result. Re-normalizing the sources keeps
            # advantage/disadvantage cancellation declarative.
            sources = list(check.get("advantage_sources") or [])
            sources.append({"code": "lucky", "mode": "disadvantage"})
            mode = mode_from_sources(sources)
            natural = min(int(check["natural"]), die)
            rolls = [*list(check.get("rolls") or []), die]
            check.update({
                **window,
                "advantage_mode": mode,
                "advantage_sources": sources,
                "rolls": rolls,
                "natural": natural,
                "total": natural + int(check.get("modifier", 0) or 0)
                + int(check.get("bless_bonus", 0) or 0),
                "critical": natural >= int(check.get("crit_threshold", 20) or 20),
            })
            check["success"] = self._attack_check_success(check)
            return check
        if code == "cutting_words_check":
            check.update({
                **window,
                "total": int(check["total"]) - die,
                "check_penalty": die,
                "check_penalty_rolls": [die],
            })
            check["success"] = self._attack_check_success(check)
            return check
        if code == "dark_ones_own_luck":
            total = int(check.get("total", check.get("natural", 0)) or 0) + die
            check.update({
                **window,
                "window_bonus": die,
                "window_bonus_rolls": [die],
                "total": total,
                "success": total >= int(check["target"]),
            })
            return check
        raise CombatIntentError("this check window code is not supported")  # pragma: no cover

    @staticmethod
    def _attack_check_success(check: dict[str, Any]) -> bool:
        """Attack success after a window: a natural 1 always misses."""

        natural = int(check.get("natural", 0) or 0)
        if natural == 1:
            return False
        if bool(check.get("critical")):
            return True
        return int(check["total"]) >= int(check["target"])

    def _finish_attack_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], spent: bool, events: list[dict[str, Any]], rng: Any,
    ) -> list[dict[str, Any]]:
        settlement = deepcopy(decision.get("settlement") or {})
        actor_id = str(decision["attacker_id"])
        target_id = str(decision["target_id"])
        actor = self._actor_view(instance, combat, actor_id)
        target = self._actor_view(instance, combat, target_id)
        events.append(check)
        if settlement.get("rage_sustain"):
            events.append({
                "type": "dnd2024.condition.sustained", "target_id": actor_id,
                "condition": "rage",
            })
        exclude_bards = tuple(str(item) for item in settlement.get("exclude_bards") or [])
        spent_reactions = tuple(str(item) for item in settlement.get("spent_reactions") or [])
        if spent:
            code = str(decision["window_code"])
            if code == "cutting_words_check":
                exclude_bards = (*exclude_bards, str(decision["window_owner_id"]))
                spent_reactions = (*spent_reactions, str(decision["window_owner_id"]))
                # Persist the cost into the settlement so any remaining window
                # of this same attack cannot re-offer the bard already spent.
                settlement["exclude_bards"] = list(exclude_bards)
                settlement["spent_reactions"] = list(spent_reactions)
            if check.get("success"):
                next_window = self._next_attack_check_window(
                    instance, combat, decision, check, settlement,
                )
                if next_window is not None:
                    events.append({"type": "dnd2024.decision.pending", **next_window})
                    return events
        elif check.get("success"):
            next_window = self._next_attack_check_window(
                instance, combat, decision, check, settlement,
            )
            if next_window is not None:
                events.append({"type": "dnd2024.decision.pending", **next_window})
                return events
        if check.get("success"):
            events.extend(self._hit_settlement_events(
                instance, combat, actor, target,
                deepcopy(settlement.get("weapon") or {}), check,
                deepcopy(settlement.get("intent") or {}), rng,
                ability=str(settlement.get("ability") or ""),
                modifier=int(settlement.get("modifier", 0) or 0),
                damage_formula=str(settlement.get("damage_formula") or ""),
                damage_type=str(settlement.get("damage_type") or ""),
                crit_on_hit=bool(settlement.get("crit_on_hit")),
                exclude_bards=exclude_bards,
                spent_reactions=spent_reactions,
            ))
        events.extend(self._consume_attack_conditions(actor_id, target_id, actor, target))
        return events

    def _finish_death_save_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], events: list[dict[str, Any]], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(decision["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        events.append(check)
        events.extend(self._death_save_outcome_events(instance, combat, actor, check, rng))
        return events

    def _finish_concentration_window(
        self, decision: dict[str, Any], check: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        events.append(check)
        if not check.get("success"):
            events.append({
                "type": "dnd2024.concentration.ended",
                "actor_id": str(decision["actor_id"]),
                "reason": "failed_concentration_save",
            })
        return events

    def _finish_damage_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        entry = WINDOW_REGISTRY["cutting_words_damage"]
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        damage_event = deepcopy(decision["damage_event"])
        spent = intent["option"] == "spend"
        if spent:
            owner_id = str(decision["window_owner_id"])
            die = int(rng.randint(1, int(decision["damage_sides"])))
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": owner_id,
                "resource_id": entry.resource_id,
            })
            events.append({
                "type": "dnd2024.reaction.spent", "actor_id": owner_id,
                "reason": "reaction_feature",
            })
            penalty = min(int(damage_event["amount"]), die)
            amount = max(0, int(damage_event["amount"]) - penalty)
            damage_event["amount"] = amount
            damage_event["delta"] = -amount
            damage_event["penalty"] = die
            damage_event["penalty_rolls"] = [die]
            events.append({
                "type": "check.resolved", "kind": "damage_window",
                "actor_id": owner_id, "target_id": str(decision["target_id"]),
                "rolls": [die], "natural": die, "total": die,
                "penalty": penalty, "success": True,
            })
        next_decision = decision.get("next_decision")
        if isinstance(next_decision, dict):
            next_decision = deepcopy(next_decision)
            if spent:
                held = next_decision.get("damage_event")
                if isinstance(held, dict):
                    # A later hit-triggered spell holds the same base damage;
                    # the reduction must land before it is submitted.
                    held["amount"] = int(damage_event["amount"])
                    held["delta"] = -int(damage_event["amount"])
                    held["penalty"] = int(damage_event.get("penalty", 0) or 0)
                    if damage_event.get("penalty_rolls"):
                        held["penalty_rolls"] = list(damage_event["penalty_rolls"])
            events.append({"type": "dnd2024.decision.pending", **next_decision})
            return events
        target = self._actor_view(instance, combat, str(decision["target_id"]))
        events.extend(self._resolve_committed_hit(
            instance, combat, str(decision["attacker_id"]), target, damage_event,
            int(damage_event["amount"]), rng,
        ))
        return events

    # ------------------------------------------------------------- AI ladders

    def _automatic_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
    ) -> dict[str, Any] | None:
        owner_id = str(decision.get("window_owner_id") or "")
        kind, raw_id = actor_kind(owner_id)
        if kind == "player" and not is_ai_controlled(instance, raw_id):
            return None
        check = decision.get("check") or {}
        code = str(decision.get("window_code") or "")
        option = "decline"
        if code == "lucky_disadvantage" and check.get("success"):
            margin = int(check["total"]) - int(check["target"])
            # The lucky d20 replaces the natural result with the lower one;
            # only a natural high enough above the margin can flip the hit.
            option = "spend" if int(check["natural"]) - margin >= 2 else "decline"
        elif code == "cutting_words_check" and check.get("success"):
            option = (
                "spend" if not check.get("critical")
                and int(check["total"]) - int(decision["die_sides"]) < int(check["target"])
                else "decline"
            )
        elif code == "dark_ones_own_luck" and not check.get("success"):
            surface = str(decision.get("surface") or "")
            current = int(check["total"]) if surface == "concentration" else int(check["natural"])
            option = (
                "spend" if current + int(decision["die_sides"]) >= int(check["target"])
                else "decline"
            )
        return self._window_resolve_intent(instance, decision, version, option)

    def _automatic_damage_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
    ) -> dict[str, Any] | None:
        owner_id = str(decision.get("window_owner_id") or "")
        kind, raw_id = actor_kind(owner_id)
        if kind == "player" and not is_ai_controlled(instance, raw_id):
            return None
        amount = int((decision.get("damage_event") or {}).get("amount", 0) or 0)
        option = "spend" if amount > 1 and int(decision["damage_sides"]) >= 2 else "decline"
        return self._window_resolve_intent(instance, decision, version, option)

    @staticmethod
    def _window_resolve_intent(
        instance: Any, decision: dict[str, Any], version: int, option: str,
    ) -> dict[str, Any]:
        return {
            "intent_id": f"auto:check-window:{version}:{decision['decision_id']}",
            "type": "decision.resolve",
            "expected_version": version,
            "submitted_by": str(getattr(instance, "gm_uid", "") or ""),
            "decision_id": str(decision["decision_id"]),
            "option": option,
        }
