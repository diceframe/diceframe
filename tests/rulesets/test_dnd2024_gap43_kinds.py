"""Activation kinds extend catalog loading, validation, projection and settlement."""

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import ClassFeatureError, Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.kinds import KIND_HANDLERS, KindHandler
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from tests.rulesets.test_dnd2024_channel_control import _apply, _capability, _start, _two_enemies
from tests.rulesets.test_dnd2024_combat import _instance_with_sheet
from tests.rulesets.test_dnd2024_d20_skills import _rogue


KIND = "test_grant_action"


def _custom_bundle(runtime):
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    catalog = entities["class_feature_catalog"]["srd_class_features"]
    feature = next(
        row for row in catalog["classes"]["rogue"]["features"] if row["id"] == "steady_aim"
    )
    capability = feature["capabilities"][0]
    capability["id"] = "test_activation"
    capability["actions"] = [{"kind": KIND, "count": 1}]
    return replace(bundle, entities=entities)


def test_unregistered_kind_is_rejected_on_catalog_load() -> None:
    with pytest.raises(ClassFeatureError, match=f"test_activation.*unknown action kind.*{KIND}"):
        Dnd2024ClassFeatureResolver(_custom_bundle(Dnd2024Runtime()))


def test_registered_kind_runs_all_three_phases_without_central_changes(monkeypatch) -> None:
    def validate(capability, action, context) -> str:
        return "" if context.intent.get("confirm") is True else "confirm the test activation"

    def resolve(capability, action, context, rng) -> list[dict]:
        return [{
            "type": "dnd2024.economy.granted", "actor_id": context.actor_id,
            "grants": {"action": action.count}, "flags": {},
        }]

    def project(capability, action, context, view) -> dict:
        return {"requires_confirmation": True, "actions_granted": action.count}

    monkeypatch.setitem(KIND_HANDLERS, KIND, KindHandler(
        validate=validate, resolve=resolve, project=project,
    ))
    runtime = Dnd2024Runtime()
    engine, instance = _instance_with_sheet(_rogue(runtime, 3), _custom_bundle(runtime))
    _start(engine, instance, enemies=_two_enemies())
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
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

    events = _apply(engine, instance, _capability(engine, instance, "test_activation", confirm=True))
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
