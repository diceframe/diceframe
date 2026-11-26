"""Activation kinds extend catalog loading, validation, projection and settlement."""

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import CombatIntentError
from src.rulesets.dnd2024.features import ClassFeatureError, Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.kinds import KIND_HANDLERS, KindHandler
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from tests.rulesets.test_dnd2024_channel_control import _apply, _capability, _enemy, _start, _two_enemies
from dnd2024_monk_common import monk_sheet
from tests.rulesets.test_dnd2024_combat import _instance_with_sheet
from tests.rulesets.test_dnd2024_d20_skills import _rogue


KIND = "test_grant_action"


def _custom_bundle(runtime, *, requires=()):
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    catalog = entities["class_feature_catalog"]["srd_class_features"]
    feature = next(
        row for row in catalog["classes"]["rogue"]["features"] if row["id"] == "steady_aim"
    )
    capability = feature["capabilities"][0]
    capability["id"] = "test_activation"
    capability["requires_hostile_target"] = True
    capability["actions"] = [{"kind": KIND, "count": 1}]
    if requires != ():
        capability["requires"] = requires
    return replace(bundle, entities=entities)


def test_unregistered_kind_is_rejected_on_catalog_load() -> None:
    with pytest.raises(ClassFeatureError, match=f"test_activation.*unknown action kind.*{KIND}"):
        Dnd2024ClassFeatureResolver(_custom_bundle(Dnd2024Runtime()))


@pytest.mark.parametrize(("requires", "error"), [
    (None, "must be an array"),
    (["unknown:shadow"], "unknown prerequisite"),
    (["active_long_effect:"], "effect id is not a canonical id"),
    (["active_long_effect:shadow"] * 2, "repeats"),
])
def test_catalog_rejects_invalid_prerequisites(monkeypatch, requires, error) -> None:
    monkeypatch.setitem(KIND_HANDLERS, KIND, KindHandler(resolve=lambda *_: []))
    with pytest.raises(ClassFeatureError, match=rf"test_activation\.requires.*{error}"):
        Dnd2024ClassFeatureResolver(_custom_bundle(Dnd2024Runtime(), requires=requires))


def test_registered_kind_runs_all_three_phases_without_central_changes(monkeypatch) -> None:
    def validate(capability, action, context) -> str:
        return "" if context.intent.get("confirm") is True else "confirm the test activation"

    def validate_available(capability, action, context) -> None:
        if context.intent.get("amount") != action.count:
            raise CombatIntentError("the amount must match the declaration")

    def resolve(capability, action, context, rng) -> list[dict]:
        return [{
            "type": "dnd2024.economy.granted", "actor_id": context.actor_id,
            "grants": {"action": action.count}, "flags": {},
        }]

    def project(capability, action, context, view) -> dict:
        return {"requires_confirmation": True, "actions_granted": action.count}

    monkeypatch.setitem(KIND_HANDLERS, KIND, KindHandler(
        validate=validate, validate_available=validate_available, resolve=resolve, project=project,
    ))
    runtime = Dnd2024Runtime()
    engine, instance = _instance_with_sheet(
        _rogue(runtime, 3), _custom_bundle(runtime, requires=["active_long_effect:shadow"]),
    )
    _start(engine, instance, enemies=_two_enemies())
    economy = ruleset_runtime.state(instance)["combat"]["economy"]

    denied = _capability(engine, instance, "test_activation", confirm=True, amount=1)
    assert denied["ok"] is False
    assert denied["error"] == "no effect to dismiss"
    assert economy["bonus_action"] == 1

    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("long_effects", {})["shadow"] = {"expires_at": 10}

    entry = next(
        row for row in engine.available_intents(instance, "gm")
        if row.get("capability_id") == "test_activation"
    )
    assert entry["kind_details"] == {KIND: {"requires_confirmation": True, "actions_granted": 1}}
    assert entry["costs"][0]["kind"] == "bonus_action"

    denied = _capability(engine, instance, "test_activation")
    assert denied["ok"] is False
    assert denied["error"] == "confirm the test activation"
    assert economy["bonus_action"] == 1

    denied = _capability(engine, instance, "test_activation", confirm=True, amount=2)
    assert denied["ok"] is False
    assert denied["error"] == "the amount must match the declaration"
    assert economy["bonus_action"] == 1

    denied = _capability(
        engine, instance, "test_activation", confirm=True, amount=1, target_id="player:gm",
    )
    assert denied["ok"] is False
    assert denied["error"] == "this capability requires a living hostile target"

    events = _apply(engine, instance, _capability(
        engine, instance, "test_activation", confirm=True, amount=1, target_id="enemy:goblin-1",
    ))
    assert [event["type"] for event in events] == [
        "intent.submitted", "dnd2024.action.spent", "dnd2024.economy.granted",
    ]
    assert events[1]["resource"] == "bonus_action"
    assert events[2]["grants"] == {"action": 1}
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0
    assert not any(
        row.get("capability_id") == "test_activation"
        for row in engine.available_intents(instance, "gm")
    )
    denied = _capability(engine, instance, "test_activation", confirm=True, amount=2)
    assert denied["ok"] is False
    assert denied["error"] == "the bonus action has already been spent"


def test_granted_strike_that_ends_combat_stops_remaining_effects() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    catalog = entities["class_feature_catalog"]["srd_class_features"]
    flurry = next(
        capability
        for feature in catalog["classes"]["monk"]["features"]
        for capability in feature.get("capabilities", [])
        if capability["id"] == "flurry_of_blows"
    )
    flurry["actions"].append({"kind": "dodge", "count": 1})
    engine, instance = _instance_with_sheet(monk_sheet(runtime, level=2), replace(bundle, entities=entities))
    _start(engine, instance, enemies=[_enemy(hp=1)])

    # The first strike wins. No die is available for a second strike, and the
    # following Dodge effect must not be emitted after combat ends.
    events = _apply(engine, instance, _capability(
        engine, instance, "flurry_of_blows", [15, 4], target_id="enemy:goblin-1",
    ))
    assert events[-1]["type"] == "dnd2024.combat.ended"
    assert len([event for event in events if event["type"] == "check.resolved"]) == 1
