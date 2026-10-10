from types import SimpleNamespace
import random

import pytest

from src.rulesets.automation import apply_director_automation
from src.rulesets.dnd2024.director.planner import (
    plan_adventure_choice,
    plan_encounter_preset,
)
from src.engine.modules import narrative_notes, ruleset_runtime


class _AdventureChoiceClient:
    def __init__(self, selections):
        self.selections = selections

    async def call_tools(self, *args, **kwargs):
        return SimpleNamespace(
            tool_calls=[{"name": "dnd2024_adventure_choice", "arguments": {
                "selections": self.selections,
            }}], total_tokens=1, provider_used="test", native_tools=True,
        )


def _instance():
    return SimpleNamespace(
        players={"p1": {}}, action_queue=[{"user_id": "p1", "text": "I inspect the ash."}],
        modules={"narrative_notes": {**narrative_notes.fresh(), "scene": "Road"}},
        language="en", log=[], total_llm_calls=0, total_tokens=0,
        record_llm_usage=lambda tokens: None,
    )


def _campaign():
    return {
        "automation": {"mode": "auto"},
        "tutorial": {
            "status": "active", "requirement_met": True,
            "current_step": {
                "id": "road", "title": "Cold ash", "objective": "Investigate",
                "choices": [{"id": "inspect_ash", "label": "Inspect ash", "description": "Look closely"}],
            },
        },
    }


@pytest.mark.asyncio
async def test_planner_rejects_choice_ids_not_offered_by_the_current_step():
    class _Client:
        async def call_tools(self, *args, **kwargs):
            return SimpleNamespace(
                tool_calls=[{"name": "dnd2024_adventure_choice", "arguments": {
                    "selections": [{"player_id": "p1", "choice_id": "invented_choice", "confidence": 1, "reason": "invented"}],
                }}], total_tokens=1, provider_used="test", native_tools=True,
            )

    assert await plan_adventure_choice(_instance(), _campaign(), _Client()) is None


@pytest.mark.asyncio
async def test_planner_skips_multiplayer_and_manual_mode_without_calling_model():
    class _Client:
        async def call_tools(self, *args, **kwargs):
            raise AssertionError("model should not be called")

    instance = _instance()
    instance.players["p2"] = {}
    assert await plan_adventure_choice(instance, _campaign(), _Client()) is None
    instance.players.pop("p2")
    campaign = _campaign()
    campaign["automation"]["mode"] = "manual"
    assert await plan_adventure_choice(instance, campaign, _Client()) is None


@pytest.mark.asyncio
async def test_multiplayer_auto_planner_generates_submits_and_resolve_that_advance_campaign():
    from tests.rulesets.test_dnd2024_campaign import _agreement, _instance, _submit

    runtime, instance = _instance(multiplayer=True, adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "automation.set", mode="auto")
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")
    before_step = runtime.gameplay_view(instance, "gm", True)["campaign"]["tutorial"]["current_step"]["id"]
    instance.action_queue = [
        {"user_id": "gm", "text": "I inspect the cold ash."},
        {"user_id": "ally", "text": "I inspect the cold ash too."},
    ]
    client = _AdventureChoiceClient([
        {"player_id": "gm", "choice_id": "inspect_cold_ash", "confidence": 0.96, "reason": "clear"},
        {"player_id": "ally", "choice_id": "inspect_cold_ash", "confidence": 0.91, "reason": "clear"},
    ])

    proposal = await runtime.plan_director_turn(instance, client)
    assert proposal is not None
    assert proposal["kind"] == "party_decision"
    assert len(proposal["selections"]) == 2
    batches = apply_director_automation(runtime, instance, proposal, random.Random(7))

    intent_types = [batch["intent_type"] for batch in batches]
    assert intent_types == [
        "party_decision.submit", "party_decision.submit", "party_decision.resolve",
    ]
    assert any(
        event["type"] == "dnd2024.tutorial.choice_applied"
        for event in batches[-1]["events"]
    )
    after_step = runtime.gameplay_view(instance, "gm", True)["campaign"]["tutorial"]["current_step"]["id"]
    assert after_step != before_step


@pytest.mark.asyncio
async def test_multiplayer_auto_planner_waits_when_one_action_is_ambiguous():
    from tests.rulesets.test_dnd2024_campaign import _agreement, _instance, _submit

    runtime, instance = _instance(multiplayer=True, adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "automation.set", mode="auto")
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")
    instance.action_queue = [
        {"user_id": "gm", "text": "I inspect the cold ash."},
        {"user_id": "ally", "text": "I wait and see what happens."},
    ]
    client = _AdventureChoiceClient([
        {"player_id": "gm", "choice_id": "inspect_cold_ash", "confidence": 0.96, "reason": "clear"},
        {"player_id": "ally", "choice_id": "inspect_cold_ash", "confidence": 0.60, "reason": "uncertain"},
    ])

    before_version = ruleset_runtime.state(instance)["version"]
    proposal = await runtime.plan_director_turn(instance, client)
    assert proposal is None
    assert ruleset_runtime.state(instance)["version"] == before_version


@pytest.mark.asyncio
async def test_encounter_planner_accepts_only_an_offered_canonical_preset():
    instance = _instance()
    instance.action_queue[0]["text"] = "I attack the goblin blocking the road."
    proposal = {
        "kind": "combat", "mode": "assist", "confidence": 0.9,
        "action_ids": ["action:0"], "requires_gm_confirmation": True,
    }
    presets = [{
        "id": "goblin_patrol", "name": "Goblin Patrol", "difficulty": "standard",
        "description": "A goblin warrior patrol.", "enemies": [{"name": "Goblin Warrior"}],
    }]

    class _Client:
        async def call_tools(self, *args, **kwargs):
            assert kwargs["tools"][0]["function"]["name"] == "dnd2024_encounter_preset"
            return SimpleNamespace(
                tool_calls=[{"name": "dnd2024_encounter_preset", "arguments": {
                    "encounter_preset_id": "goblin_patrol", "confidence": 0.94,
                    "reason": "The established opponent is a goblin.",
                }}],
                total_tokens=3, provider_used="test", native_tools=True,
            )

    planned = await plan_encounter_preset(instance, proposal, presets, _Client())
    assert planned is not None
    assert planned["encounter_preset_id"] == "goblin_patrol"
    assert planned["requires_gm_confirmation"] is True


@pytest.mark.asyncio
async def test_encounter_planner_rejects_invented_or_low_confidence_presets():
    instance = _instance()
    proposal = {"kind": "combat", "mode": "auto", "confidence": 0.9}
    presets = [{
        "id": "goblin_patrol", "name": "Goblin Patrol", "difficulty": "standard",
        "description": "A goblin warrior patrol.", "enemies": [{"name": "Goblin Warrior"}],
    }]

    class _Client:
        def __init__(self, preset_id, confidence):
            self.preset_id = preset_id
            self.confidence = confidence

        async def call_tools(self, *args, **kwargs):
            return SimpleNamespace(
                tool_calls=[{"name": "dnd2024_encounter_preset", "arguments": {
                    "encounter_preset_id": self.preset_id,
                    "confidence": self.confidence,
                    "reason": "uncertain",
                }}],
                total_tokens=1, provider_used="test", native_tools=True,
            )

    assert await plan_encounter_preset(
        instance, proposal, presets, _Client("invented", 1.0),
    ) is None
    assert await plan_encounter_preset(
        instance, proposal, presets, _Client("goblin_patrol", 0.4),
    ) is None
