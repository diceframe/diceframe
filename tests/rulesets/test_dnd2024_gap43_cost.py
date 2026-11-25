# -*- coding: utf-8 -*-
"""GAP-43 step 1: one cost model, three independent prices.

The capabilities that declare no cost are pinned here before anything moves
under them.  An empty slot is a rule statement -- "no action required" -- not a
missing declaration: it must stay usable when every slot is already spent, and
it still obeys the turn every class capability obeys.  How many there are is
part of the contract too: a tenth empty cost is a new decision, and silently
pricing one of these nine is a mechanics change.
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.combat import CombatContext, combat_capabilities
from src.rulesets.dnd2024.features.models import CapabilityCost
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

#: (feature, capability, action kinds) of every capability with an empty cost.
EMPTY_COST_CAPABILITIES = {
    ("dragon_wings", "dragon_wings_end", ("end_long_effect",)),
    ("dragon_wings", "dragon_wings_restore", ("restore_feature_use",)),
    ("font_of_inspiration", "font_slot_exchange", ("inspiration_slot_exchange",)),
    ("font_of_magic", "font_slot_to_points", ("font_slot_to_points",)),
    ("holy_nimbus", "holy_nimbus_end", ("end_long_effect",)),
    ("holy_nimbus", "holy_nimbus_restore", ("restore_feature_use",)),
    ("hurl_through_hell", "hurl_through_hell_restore", ("restore_feature_use",)),
    ("intimidating_presence", "intimidating_presence_restore", ("restore_feature_use",)),
    ("quivering_palm", "quivering_palm_dismiss", ("end_long_effect",)),
}


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle("en")


def _resolver() -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle("en"))


def test_exactly_these_nine_capabilities_declare_no_cost() -> None:
    empty = set()
    for definition in _resolver().catalog.features.values():
        for capability in definition.capabilities:
            if capability.cost == CapabilityCost():
                empty.add((
                    definition.id, capability.id,
                    tuple(action.kind for action in capability.actions),
                ))

    assert empty == EMPTY_COST_CAPABILITIES


def test_an_empty_cost_needs_no_slot_and_reports_nothing_to_pay() -> None:
    """Every slot already spent: the capability is still offered, unblocked."""

    resolver = _resolver()
    definitions = {
        definition.id: definition
        for definition in resolver.catalog.features.values()
    }

    for feature_id, capability_id, _kinds in sorted(EMPTY_COST_CAPABILITIES):
        views = combat_capabilities(
            resolver.catalog, (definitions[feature_id],), CombatContext(), {},
            include_unavailable=True,
        )
        view = next(view for view in views if view.id == capability_id)

        assert view.available is True, (capability_id, view.blocked_reason)
        assert view.blocked_reason == ""
        assert view.costs == ()


def test_an_empty_cost_still_needs_the_actors_own_turn(bundle) -> None:
    """"No action required" is not "at any time": the turn gate still applies."""

    from tests.rulesets.test_dnd2024_channel_control import (
        _intent,
        _setup,
        _start,
        _to_turn,
        _two_enemies,
    )
    from tests.rulesets.test_dnd2024_long_effects import _sorcerer

    engine, instance = _setup(_sorcerer(bundle, 2))
    _start(engine, instance, enemies=_two_enemies())
    _to_turn(engine, instance, "enemy:goblin-1")

    resolved = engine.resolve_intent(
        instance,
        _intent(
            instance, type="class_capability",
            capability_id="font_slot_to_points",
        ),
        None,
    )

    assert resolved["ok"] is False
