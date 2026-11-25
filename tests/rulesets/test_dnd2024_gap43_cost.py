# -*- coding: utf-8 -*-
"""GAP-43 step 1: one cost model, three independent prices.

An empty action slot is a rule statement -- "no action required" -- not a
missing declaration.  The truly empty costs remain usable when every action
slot is spent.  Fixed restoration prices are resources, not action slots.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.rulesets.dnd2024.features import ClassFeatureError, Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.combat import CombatContext, combat_capabilities
from src.rulesets.dnd2024.features.models import CapabilityCost
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

#: (feature, capability, action kinds) of every capability with an empty cost.
EMPTY_COST_CAPABILITIES = {
    ("dragon_wings", "dragon_wings_end", ("end_long_effect",)),
    ("font_of_inspiration", "font_slot_exchange", ("inspiration_slot_exchange",)),
    ("font_of_magic", "font_slot_to_points", ("font_slot_to_points",)),
    ("holy_nimbus", "holy_nimbus_end", ("end_long_effect",)),
    ("quivering_palm", "quivering_palm_dismiss", ("end_long_effect",)),
}


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle("en")


def _resolver() -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle("en"))


def test_exactly_these_five_capabilities_declare_no_cost() -> None:
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

    # 结束类（*_end / dismiss）虽然空成本，但要结束的东西必须存在；这条
    # 用例问的是"空成本不需要槽位"，所以先给它一个可结束的效果。
    effects = frozenset({"holy_nimbus", "dragon_wings", "quivering_palm"})
    for feature_id, capability_id, _kinds in sorted(EMPTY_COST_CAPABILITIES):
        views = combat_capabilities(
            resolver.catalog, (definitions[feature_id],),
            CombatContext(active_effects=effects), {},
            include_unavailable=True,
        )
        view = next(view for view in views if view.id == capability_id)

        assert view.available is True, (capability_id, view.blocked_reason)
        assert view.blocked_reason == ""
        assert view.costs == ()


@pytest.mark.parametrize(("resources", "error"), [
    ([{"spell_slot": 0}], "must be a positive integer"),
    ([{"spell_slot": True}], "must be a positive integer"),
    ([{"spell_slot": "5"}], "must be a positive integer"),
    ([{"spell_slot": 10}], "must be at most 9"),
    ([{"spell_slot": 5, "id": "rages", "amount": 1}], "must contain only spell_slot"),
    ([{"spell_slot": 5}, {"spell_slot": 5}], "repeats a spell slot cost level"),
    ([], "must declare its price in cost.resources"),
])
def test_catalog_rejects_invalid_restore_costs(bundle, resources, error) -> None:
    entities = deepcopy(bundle.entities)
    catalog = entities["class_feature_catalog"]["srd_class_features"]
    holy_nimbus = next(
        feature for feature in catalog["classes"]["paladin"]["features"]
        if feature["id"] == "holy_nimbus"
    )
    restore = next(
        capability for capability in holy_nimbus["capabilities"]
        if capability["id"] == "holy_nimbus_restore"
    )
    restore["cost"]["resources"] = resources
    if not resources:
        # An old price in feature parameters cannot substitute for the declaration.
        holy_nimbus["parameters"]["restore_slot_level"] = 5

    with pytest.raises(ClassFeatureError, match=error):
        Dnd2024ClassFeatureResolver(replace(bundle, entities=entities))


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
