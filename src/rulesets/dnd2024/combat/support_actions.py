"""Bardic Inspiration grants and class capability exchanges of spell slots."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from ..features.models import FeatureCapability
from ..features.resources import resource_state
from ..spells.font_of_magic import (
    FontOfMagicError,
    apply_create_slot,
    apply_slot_to_points,
    create_slot_plan,
    declaring_class_level,
    parse_created_slot_costs,
    slot_to_points_plan,
)
from .primitives import CombatIntentError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..features.resolver import Dnd2024ClassFeatureResolver


class CombatSupportActionsMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        features: Dnd2024ClassFeatureResolver
        _actor_view: Callable[[Any, dict[str, Any], str], dict[str, Any]]
        _active_bardic_instance: Callable[[Any, dict[str, Any], str], dict[str, Any] | None]
        _bardic_die_sides: Callable[[dict[str, Any]], int]
        _canonical_actor: Callable[[Any, dict[str, Any]], dict[str, Any]]
        _canonical_actor_character: Callable[[Any, str], dict[str, Any]]
        _distance: Callable[[dict[str, Any], str, str], int]
        _feature_character: Callable[[dict[str, Any]], dict[str, Any]]
        _logical_minutes: Callable[[Any], int]
        _require_range: Callable[[dict[str, Any], str, str, int], None]

    def _validate_bardic_grant(
        self, instance: Any, combat: dict[str, Any], target_id: str, actor: dict[str, Any],
    ) -> None:
        target = self._actor_view(instance, combat, target_id)
        if target_id == actor["actor_id"]:
            raise CombatIntentError("you cannot grant Bardic Inspiration to yourself")
        if str(target.get("side") or "") != str(actor.get("side") or "") or target["hp"] <= 0:
            raise CombatIntentError("Bardic Inspiration targets a living ally")
        range_ft = self.features.feature_value(
            self._feature_character(actor), "bardic_inspiration", "range_ft", 0,
        )
        if not isinstance(range_ft, int) or range_ft <= 0:
            raise CombatIntentError("no Bardic Inspiration range is declared")
        self._require_range(combat, actor["actor_id"], target_id, range_ft)
        # A blinded target cannot see an invisible bard; deafened is not modeled.
        if (
            "invisible" in (actor.get("conditions") or {})
            and "blinded" in (target.get("conditions") or {})
        ):
            raise CombatIntentError("the target can neither see nor hear you")
        if self._active_bardic_instance(instance, combat, target_id) is not None:
            raise CombatIntentError("the target already holds a Bardic Inspiration die")

    def _bardic_grant_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, holder_id: str, intent_id: Any,
    ) -> list[dict[str, Any]]:
        """Grant one Bardic Inspiration die to a party creature.

        RAW re-checks at settle time what validation already checked: another
        creature within 60 feet that can see or hear the bard, not already
        holding an unexpired die.  The die face is fixed at grant time from
        the bard's progression track, and the hour lives on the authoritative
        world clock — no wall time, no background timer.  The die *instance*
        (not a second resource table) is the state of record; the use was
        already spent by the capability cost, so this handler only validates
        and records.
        """

        if not holder_id:
            raise CombatIntentError("a Bardic Inspiration grant needs a target")
        if holder_id == actor_id:
            raise CombatIntentError("you cannot grant Bardic Inspiration to yourself")
        bard = self._actor_view(instance, combat, actor_id)
        holder = self._actor_view(instance, combat, holder_id)
        if holder.get("side") != bard.get("side"):
            raise CombatIntentError("Bardic Inspiration targets a creature on your side")
        if int(holder.get("hp", 0) or 0) <= 0:
            raise CombatIntentError("Bardic Inspiration targets a living creature")
        die_sides = self._bardic_die_sides(bard)
        if die_sides == 0:
            raise CombatIntentError("no Bardic Inspiration die is available at this level")
        range_ft = self.features.feature_value(
            self._feature_character(bard), "bardic_inspiration", "range_ft", 0,
        )
        if not isinstance(range_ft, int) or range_ft <= 0:
            raise CombatIntentError("no Bardic Inspiration range is declared")
        if self._distance(combat, actor_id, holder_id) > range_ft:
            raise CombatIntentError("the target is out of Bardic Inspiration range")
        # "can see or hear you": with the condition vocabulary the engine has,
        # the only pair that cuts both channels is an invisible bard seen by a
        # blinded holder; deafened is not modeled, so it never blocks.
        bard_conditions = bard.get("conditions") or {}
        holder_conditions = holder.get("conditions") or {}
        if "invisible" in bard_conditions and "blinded" in holder_conditions:
            raise CombatIntentError("the target can neither see nor hear you")
        if self._active_bardic_instance(instance, combat, holder_id) is not None:
            raise CombatIntentError("the target already holds a Bardic Inspiration die")
        granted_at = self._logical_minutes(instance)
        return [{
            "type": "dnd2024.bardic.granted", "actor_id": actor_id,
            "holder_id": holder_id,
            "die_instance_id": f"bardic:{intent_id}:{holder_id}",
            "die_sides": die_sides,
            "granted_at_minute": granted_at,
            "expires_at_minute": granted_at + 60,
            "source_feature": "bardic_inspiration",
        }]

    def _validate_inspiration_slot_exchange(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
    ) -> None:
        character = self._canonical_actor(instance, actor)
        pool = resource_state(character, "bardic_inspiration")
        if int(pool.get("current", 0) or 0) >= int(pool.get("maximum", 0) or 0):
            raise CombatIntentError("no expended Bardic Inspiration use to regain")
        if isinstance(slot_level, bool) or not isinstance(slot_level, int) or slot_level < 1:
            raise CombatIntentError("a spell slot level is required")
        slots = ((character.get("spellcasting") or {}).get("class") or {}).get("slots_current") or {}
        if int(slots.get(str(slot_level), 0) or 0) < 1:
            raise CombatIntentError("the spell slot is already spent")

    def _font_slot_exchange_events(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
    ) -> list[dict[str, Any]]:
        """Spend one real spell slot to regain one Bardic Inspiration use.

        "No action required" means no economy is spent and the exchange does
        not count as casting a spell, so the slot event carries ``exchange``
        and the reducer leaves ``slot_spell_cast`` alone.  Both halves land in
        one batch: the slot only leaves if the use actually comes back, and
        the use can never exceed the Charisma-derived maximum.
        """

        actor_id = str(actor["actor_id"])
        character = self._canonical_actor_character(instance, actor_id)
        pool = dict(resource_state(character, "bardic_inspiration"))
        if not pool:
            raise CombatIntentError("Bardic Inspiration is not initialized")
        maximum = int(pool.get("maximum", 0) or 0)
        current = int(pool.get("current", 0) or 0)
        if current >= maximum:
            raise CombatIntentError("no expended Bardic Inspiration use to regain")
        slots = dict((character.get("spellcasting") or {}).get("class", {}).get("slots_current") or {})
        if isinstance(slot_level, bool) or not isinstance(slot_level, int) or slot_level < 1:
            raise CombatIntentError("a spell slot level is required")
        if int(slots.get(str(slot_level), 0) or 0) < 1:
            raise CombatIntentError("the spell slot is already spent")
        return [
            {
                "type": "dnd2024.spell.slot_spent", "actor_id": actor_id,
                "slot_level": slot_level, "exchange": True,
            },
            {
                "type": "dnd2024.class_resource.refunded", "actor_id": actor_id,
                "resource_id": "bardic_inspiration", "amount": 1,
                "reason": "font_of_inspiration",
            },
        ]

    def _validate_font_slot_to_points(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
    ) -> None:
        try:
            slot_to_points_plan(self._canonical_actor(instance, actor), slot_level)
        except FontOfMagicError as exc:
            raise CombatIntentError(str(exc)) from exc

    def _validate_font_create_slot(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
        capability: FeatureCapability,
    ) -> None:
        character = self._canonical_actor(instance, actor)
        definition = self.features.feature_definition(capability.feature_id)
        class_id = str(getattr(definition, "class_id", "") or "")
        try:
            costs = parse_created_slot_costs(
                self.features.feature_value(character, capability.feature_id, "created_slot_costs", ()),
            )
            create_slot_plan(
                character, slot_level, costs,
                declaring_class_level(character, class_id),
            )
        except FontOfMagicError as exc:
            raise CombatIntentError(str(exc)) from exc

    def _font_of_magic_events(
        self, instance: Any, actor: dict[str, Any], slot_level: Any,
        kind: str, feature_id: str,
    ) -> list[dict[str, Any]]:
        """Sorcery Points <-> spell slots, both directions in one batch.

        The plans are recomputed here rather than trusted from validation: the
        two stages read the same authoritative sheet, and a settle-time refusal
        is what keeps a forged intent from moving points it never owned.  Only
        the *events* survive -- the reducer is the writer, so a copy of the
        sheet is enough to derive the balances the log records.
        """

        actor_id = str(actor["actor_id"])
        character = deepcopy(self._canonical_actor_character(instance, actor_id))
        try:
            if kind == "font_slot_to_points":
                plan = slot_to_points_plan(character, slot_level)
                events = apply_slot_to_points(character, plan)
            else:
                owner = self.features.feature_definition(str(feature_id))
                class_id = str(getattr(owner, "class_id", "") or "")
                costs = parse_created_slot_costs(self.features.feature_value(
                    character, feature_id, "created_slot_costs", (),
                ))
                plan = create_slot_plan(
                    character, slot_level, costs,
                    declaring_class_level(character, class_id),
                )
                events = apply_create_slot(character, plan)
        except FontOfMagicError as exc:
            raise CombatIntentError(str(exc)) from exc
        for event in events:
            event["actor_id"] = actor_id
        return events
