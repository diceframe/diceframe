from __future__ import annotations

import random
from types import SimpleNamespace
import pytest

from src.commands.round_effects import apply_ruleset_combat_signal
from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.play import resolve_story_encounter_access
from src.rulesets.automation import apply_director_automation


def _character(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def _instance(
    *, multiplayer: bool = False, adventure: bool = False,
) -> tuple[Dnd2024Runtime, GameInstance]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-campaign", "bot"),
        world_id="greymoor" if adventure else "default_fantasy",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    gm = _character(runtime, "stalwart_guardian", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": gm}
    if multiplayer:
        ally = _character(runtime, "curious_arcanist", "Mira")
        instance.players["ally"] = {"character_name": "Mira", "character_sheet": ally}
    assert instance.bind_ruleset_runtime(gm["rule_binding"])
    if adventure:
        package = runtime._adventure_loader.resolve("core:lanterns_of_greymoor", "en")
        assert instance.bind_adventure(package.binding("greymoor"))
    return runtime, instance


def _submit(
    runtime: Dnd2024Runtime, instance: GameInstance, intent_type: str,
    submitted_by: str = "gm", **fields,
) -> dict:
    version = int(instance.ruleset_state.get("version", 0) or 0)
    intent = {
        "intent_id": f"intent-{intent_type}-{version}",
        "type": intent_type,
        "expected_version": version,
        "submitted_by": submitted_by,
        **fields,
    }
    resolved = runtime.resolve_intent(instance, intent, random.Random(7))
    assert resolved["ok"] is True, resolved
    applied = runtime.apply_event_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    return resolved


def _agreement(runtime: Dnd2024Runtime, instance: GameInstance) -> None:
    defaults = runtime.gameplay_view(instance, "gm", True)["campaign"][
        "session_zero_defaults"
    ]
    _submit(runtime, instance, "session_zero.propose", agreement=defaults)
    for uid in instance.players:
        _submit(runtime, instance, "session_zero.respond", uid, response="accept")
    _submit(runtime, instance, "session_zero.lock")


def test_new_run_initialization_clears_runtime_state_and_rebinds_campaign() -> None:
    runtime, instance = _instance(adventure=True)
    instance.ruleset_state = {
        "state_schema_version": 1,
        "combat": {"status": "active"},
        "campaign": {"schema_version": 1, "tutorial": {"status": "completed"}},
    }

    runtime.initialize_new_run(instance, preserve_characters=True)

    assert "combat" not in instance.ruleset_state
    campaign = instance.ruleset_state["campaign"]
    assert campaign["session_zero"]["status"] == "not_started"
    assert campaign["tutorial"]["status"] == "not_started"
    assert campaign["adventure_binding"]["adventure_id"] == "core:lanterns_of_greymoor"


def test_session_zero_requires_every_player_and_revisions_reset_consent() -> None:
    runtime, instance = _instance(multiplayer=True)
    defaults = runtime.gameplay_view(instance, "gm", True)["campaign"][
        "session_zero_defaults"
    ]
    _submit(runtime, instance, "session_zero.propose", agreement=defaults)
    _submit(runtime, instance, "session_zero.respond", "gm", response="accept")

    denied = runtime.validate_intent(instance, {
        "intent_id": "lock-too-soon", "type": "session_zero.lock",
        "expected_version": 2, "submitted_by": "gm",
    })
    assert denied["ok"] is False
    assert "every player" in denied["error"]

    _submit(runtime, instance, "session_zero.respond", "ally", response="accept")
    changed = {**defaults, "tone": "Hopeful mystery"}
    _submit(runtime, instance, "session_zero.propose", agreement=changed)
    state = runtime.gameplay_view(instance, "gm", True)["campaign"]["session_zero"]
    assert state["revision"] == 2
    assert state["responses"] == {}
    for uid in instance.players:
        _submit(runtime, instance, "session_zero.respond", uid, response="accept")
    _submit(runtime, instance, "session_zero.lock")
    locked = runtime.gameplay_view(instance, "ally", False)["campaign"]["session_zero"]
    assert locked["status"] == "locked"
    assert locked["agreement"]["tone"] == "Hopeful mystery"


def test_solo_quick_start_uses_safe_defaults_and_enters_first_tutorial_step() -> None:
    runtime, instance = _instance(adventure=True)
    instance.solo_mode = True
    actions = runtime.available_intents(instance, "gm")

    assert "session_zero.quick_start" in {item["type"] for item in actions}
    assert runtime.gameplay_view(instance, "gm", True)["campaign"]["automation"]["mode"] == "auto"
    _submit(runtime, instance, "session_zero.quick_start")

    campaign = runtime.gameplay_view(instance, "gm", True)["campaign"]
    assert campaign["session_zero"]["status"] == "locked"
    assert campaign["session_zero"]["agreement"]["difficulty"] == "standard"
    assert campaign["session_zero"]["agreement"]["pvp_policy"] == "consent"
    assert campaign["tutorial"]["status"] == "active"
    assert campaign["tutorial"]["current_step"] is not None
    assert "session_zero.quick_start" not in {
        item["type"] for item in runtime.available_intents(instance, "gm")
    }


def test_automation_mode_is_persisted_and_only_the_gm_can_change_it() -> None:
    runtime, instance = _instance(multiplayer=True)
    instance.solo_mode = False
    _agreement(runtime, instance)

    campaign = runtime.gameplay_view(instance, "ally", False)["campaign"]
    assert campaign["automation"]["mode"] == "assist"
    action = next(
        item for item in runtime.available_intents(instance, "gm")
        if item["type"] == "automation.set"
    )
    assert action["options"] == ["assist", "auto", "manual"]

    denied = runtime.validate_intent(instance, {
        "intent_id": "player-automation", "type": "automation.set",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "ally", "mode": "manual",
    })
    assert denied["ok"] is False
    assert "only the GM" in denied["error"]

    changed = _submit(runtime, instance, "automation.set", mode="manual")
    assert any(
        event["type"] == "dnd2024.automation.configured"
        for event in changed["event_batch"]["events"]
    )
    assert runtime.gameplay_view(instance, "ally", False)["campaign"]["automation"] == {
        "mode": "manual", "configured_by": "gm",
    }
    assert runtime.director_proposal(instance)["proposal"]["mode"] == "manual"


def test_auto_mode_starts_only_the_canonical_story_encounter() -> None:
    runtime, instance = _instance(adventure=True)
    instance.solo_mode = True
    _agreement(runtime, instance)
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")
    _submit(runtime, instance, "tutorial.choose", choice_id="inspect_cold_ash")
    _submit(runtime, instance, "tutorial.choose", choice_id="reassure_mira")
    _submit(runtime, instance, "tutorial.choose", choice_id="follow_small_tracks")
    instance.action_queue = [{"user_id": "gm", "text": "I attack the goblin."}]

    proposal = runtime.director_proposal(instance)["proposal"]
    assert proposal["mode"] == "auto"
    assert proposal["encounter_preset_id"] == "first_skirmish"
    assert runtime.apply_narrative_combat_signal(instance, "start") is True
    batches = apply_director_automation(runtime, instance, proposal, random.Random(7))

    assert batches[0]["intent_type"] == "combat.start"
    combat = runtime.gameplay_view(instance, "gm", True)["combat"]
    assert combat["status"] == "active"
    assert combat["encounter_preset_id"] == "first_skirmish"
    assert set(instance.ruleset_state["combat"]["enemies"]) == {"goblin-minion-1"}


@pytest.mark.asyncio
async def test_auto_mode_maps_free_text_to_a_valid_adventure_choice() -> None:
    runtime, instance = _instance(adventure=True)
    instance.solo_mode = True
    _submit(runtime, instance, "session_zero.quick_start")
    instance.action_queue = [{"user_id": "gm", "text": "I inspect the cold ash closely."}]

    class _Planner:
        async def call_tools(self, *args, **kwargs):
            assert kwargs["tools"][0]["function"]["name"] == "dnd2024_adventure_choice"
            return SimpleNamespace(
                tool_calls=[{"name": "dnd2024_adventure_choice", "arguments": {
                    "selections": [{"player_id": "gm", "choice_id": "inspect_cold_ash", "confidence": 0.96,
                    "reason": "The action directly inspects the offered ash clue."}],
                }}],
                total_tokens=42, provider_used="test", native_tools=True,
            )

    proposal = await runtime.plan_director_turn(instance, _Planner())
    assert proposal is not None and proposal["kind"] == "adventure_choice"
    batches = apply_director_automation(runtime, instance, proposal, random.Random(7))
    assert batches[0]["intent_type"] == "tutorial.choose"
    campaign = runtime.gameplay_view(instance, "gm", True)["campaign"]
    assert campaign["tutorial"]["current_step"]["id"] == "keepers_plea"


def test_active_adventure_step_owns_scene_while_free_play_keeps_legacy_scene_tags() -> None:
    runtime, adventure = _instance(adventure=True)
    adventure.solo_mode = True
    _submit(runtime, adventure, "session_zero.quick_start")

    filtered = runtime.filter_narrative_state_update(
        adventure, {"scene_change": "Unrelated tavern", "npcs": {"Guide": {"tier": "known"}}},
    )
    assert filtered["scene_change"] == ""
    assert "Guide" in filtered["npcs"]

    runtime, free_play = _instance()
    unchanged = runtime.filter_narrative_state_update(
        free_play, {"scene_change": "North Gate"},
    )
    assert unchanged["scene_change"] == "North Gate"


def test_free_play_narrative_signal_wakes_and_can_start_authoritative_combat() -> None:
    runtime, instance = _instance()

    assert apply_ruleset_combat_signal(
        instance, {"combat_command": "start"}, runtime,
    ) is True
    gameplay = runtime.gameplay_view(instance, "gm", True)
    assert gameplay["encounter_request"] == {
        "status": "pending", "source": "narrative", "round": 0,
        "ready_player_ids": [],
        "readiness": {
            "ready_player_ids": [], "required_player_ids": [],
            "ready_count": 0, "required_count": 0, "all_ready": True,
            "players": [],
        },
    }
    start = next(
        item for item in runtime.available_intents(instance, "gm")
        if item["type"] == "combat.start"
    )
    preset = gameplay["encounter_presets"][0]
    _submit(
        runtime, instance, "combat.start",
        encounter_preset_id=preset["id"], enemies=preset["enemies"],
    )

    started = runtime.gameplay_view(instance, "gm", True)
    assert started["combat"]["status"] == "active"
    assert started["encounter_request"] is None
    assert start["requires"] == ["enemies"]


def test_free_play_request_persists_only_a_valid_catalog_recommendation() -> None:
    runtime, instance = _instance()
    proposal = {
        "kind": "combat", "mode": "assist", "confidence": 0.92,
        "encounter_preset_id": "goblin_patrol",
    }

    assert runtime.apply_narrative_combat_signal(instance, "start", proposal) is True
    request = runtime.gameplay_view(instance, "gm", True)["encounter_request"]
    assert request == {
        "status": "pending", "source": "narrative", "round": 0,
        "encounter_preset_id": "goblin_patrol", "confidence": 0.92,
        "ready_player_ids": [],
        "readiness": {
            "ready_player_ids": [], "required_player_ids": [],
            "ready_count": 0, "required_count": 0, "all_ready": True,
            "players": [],
        },
    }

    runtime, instance = _instance()
    assert runtime.apply_narrative_combat_signal(instance, "start", {
        **proposal, "encounter_preset_id": "invented_enemy",
    }) is True
    assert "encounter_preset_id" not in instance.ruleset_state["encounter_request"]


def test_multiplayer_encounter_readiness_is_authoritative_and_visible_to_gm() -> None:
    runtime, instance = _instance(multiplayer=True)
    assert runtime.apply_narrative_combat_signal(instance, "start", {
        "kind": "combat", "mode": "assist", "confidence": 0.92,
        "encounter_preset_id": "goblin_patrol",
    }) is True

    ally_actions = runtime.available_intents(instance, "ally")
    assert [item["type"] for item in ally_actions] == ["encounter.ready"]
    assert any(item["type"] == "combat.start" for item in runtime.available_intents(instance, "gm"))

    _submit(runtime, instance, "encounter.ready", "ally")
    request = runtime.gameplay_view(instance, "gm", True)["encounter_request"]
    assert request["readiness"] == {
        "ready_player_ids": ["ally"], "required_player_ids": ["ally"],
        "ready_count": 1, "required_count": 1, "all_ready": True,
        "players": [{"player_id": "ally", "name": "Mira", "ready": True}],
    }
    assert [item["type"] for item in runtime.available_intents(instance, "ally")] == [
        "encounter.unready",
    ]

    _submit(runtime, instance, "encounter.unready", "ally")
    request = runtime.gameplay_view(instance, "ally", False)["encounter_request"]
    assert request["readiness"]["ready_count"] == 0
    assert request["readiness"]["players"][0]["ready"] is False


def test_combat_communication_is_shared_without_spending_turn_resources() -> None:
    runtime, instance = _instance(multiplayer=True)
    gameplay = runtime.gameplay_view(instance, "gm", True)
    preset = next(item for item in gameplay["encounter_presets"] if item["id"] == "goblin_patrol")
    _submit(
        runtime, instance, "combat.start",
        encounter_preset_id=preset["id"], enemies=preset["enemies"],
    )
    before = dict(instance.ruleset_state["combat"]["economy"])

    _submit(runtime, instance, "combat.message", "ally", text="Fall back behind me!")

    assert instance.ruleset_state["combat"]["economy"] == before
    view = runtime.gameplay_view(instance, "gm", True)
    message = view["recent_combat_events"][-1]
    assert message == {
        "type": "dnd2024.combat.message",
        "actor_id": "player:ally",
        "text": "Fall back behind me!",
        "event_id": message["event_id"],
        "batch_id": message["batch_id"],
        "intent_type": "combat.message",
        "state_version": 2,
        "round": 1,
        "turn_index": 0,
        "actor_name": "Mira",
    }
    assert "combat.message" in {
        item["type"] for item in runtime.available_intents(instance, "ally")
    }


def test_public_combat_feed_inherits_round_and_source_actor_for_damage() -> None:
    runtime, instance = _instance()
    instance.ruleset_state["combat"] = {
        "enemies": {"goblin": {"name": "Goblin"}},
    }
    instance.event_ledger = [{
        "batch_id": "start", "intent_type": "combat.start", "result_version": 1,
        "events": [{"type": "dnd2024.combat.started", "round": 1}],
    }, {
        "batch_id": "attack", "intent_type": "attack", "result_version": 2,
        "events": [
            {"type": "intent.submitted", "actor_id": "enemy:goblin"},
            {
                "type": "resource.changed", "resource": "hp",
                "target_id": "player:gm", "delta": -6, "amount": 6,
            },
        ],
    }]

    damage = runtime._recent_combat_events(instance)[-1]

    assert damage["round"] == 1
    assert damage["turn_index"] == 0
    assert damage["actor_id"] == "enemy:goblin"
    assert damage["actor_name"] == "Goblin"
    assert damage["target_name"] == "Arden"


def test_public_combat_feed_keeps_death_save_hp_and_combat_end_reason() -> None:
    runtime, instance = _instance()
    instance.event_ledger = [{
        "batch_id": "death-save", "intent_type": "death_save", "result_version": 3,
        "events": [{
            "type": "dnd2024.death_save.resolved", "actor_id": "player:gm",
            "roll": 20, "successes": 0, "failures": 0, "hp": 1,
        }],
    }, {
        "batch_id": "incapacitated", "intent_type": "end_turn", "result_version": 4,
        "events": [{
            "type": "dnd2024.combat.ended", "reason": "party_incapacitated",
        }],
    }]

    events = runtime._recent_combat_events(instance)

    assert events[0]["hp"] == 1
    assert events[1]["reason"] == "party_incapacitated"


def test_auto_mode_can_start_a_valid_free_play_catalog_encounter() -> None:
    runtime, instance = _instance()
    instance.solo_mode = True
    _submit(runtime, instance, "session_zero.quick_start")
    proposal = {
        "kind": "combat", "mode": "auto", "confidence": 0.95,
        "encounter_preset_id": "goblin_patrol",
    }

    assert runtime.apply_narrative_combat_signal(instance, "start", proposal) is True
    batches = apply_director_automation(runtime, instance, proposal, random.Random(7))

    assert batches[0]["intent_type"] == "combat.start"
    combat = runtime.gameplay_view(instance, "gm", True)["combat"]
    assert combat["status"] == "active"
    assert combat["encounter_preset_id"] == "goblin_patrol"


def test_loaded_but_inactive_adventure_does_not_replace_free_play_encounter_catalog() -> None:
    runtime, instance = _instance(adventure=True)
    gameplay = runtime.gameplay_view(instance, "gm", True)

    assert gameplay["campaign"]["tutorial"]["status"] == "not_started"
    assert {item["id"] for item in gameplay["encounter_presets"]} == {
        "goblin_patrol", "crypt_pair",
    }


def test_campaign_step_grants_one_shot_authoritative_encounter_access() -> None:
    runtime, instance = _instance(adventure=True)
    instance.solo_mode = True
    _submit(runtime, instance, "session_zero.quick_start")
    _submit(runtime, instance, "tutorial.choose", choice_id="inspect_cold_ash")
    _submit(runtime, instance, "tutorial.choose", choice_id="reassure_mira")
    _submit(runtime, instance, "tutorial.choose", choice_id="follow_small_tracks")

    gameplay = runtime.gameplay_view(instance, "gm", True)
    access = resolve_story_encounter_access(instance, gameplay["campaign"])
    assert access.status == "pending"
    assert access.can_start is True
    assert access.encounter_instance_id == "tutorial:lanterns_of_greymoor:thorn_ambush"
    assert access.encounter_preset_id == "first_skirmish"
    start = next(
        action for action in runtime.available_intents(instance, "gm")
        if action["type"] == "combat.start"
    )
    assert start["encounter_instance_id"] == access.encounter_instance_id

    _submit(
        runtime,
        instance,
        "combat.start",
        encounter_preset_id="first_skirmish",
        encounter_instance_id=start["encounter_instance_id"],
    )
    active = resolve_story_encounter_access(
        instance, runtime.gameplay_view(instance, "gm", True)["campaign"],
    )
    assert active.status == "active"

    _submit(runtime, instance, "combat.end")
    resolved = resolve_story_encounter_access(
        instance, runtime.gameplay_view(instance, "gm", True)["campaign"],
    )
    assert resolved.status == "resolved"
    assert not any(
        action["type"] == "combat.start"
        for action in runtime.available_intents(instance, "gm")
    )
    replay = runtime.validate_intent(instance, {
        "intent_id": "replay-story-encounter",
        "type": "combat.start",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "gm",
        "encounter_preset_id": "first_skirmish",
        "encounter_instance_id": start["encounter_instance_id"],
    })
    assert replay["ok"] is False
    assert "does not allow" in replay["error"]


def test_campaign_record_needs_explicit_confirmation_and_respects_gm_visibility() -> None:
    runtime, instance = _instance(multiplayer=True)
    _agreement(runtime, instance)
    proposed = _submit(
        runtime, instance, "campaign.propose", kind="fact",
        title="Hidden cause", summary="The bell was sabotaged.", visibility="gm",
    )
    proposal_id = next(
        event["proposal_id"] for event in proposed["event_batch"]["events"]
        if event["type"] == "dnd2024.campaign.proposal_created"
    )

    player_view = runtime.gameplay_view(instance, "ally", False)["campaign"]
    gm_view = runtime.gameplay_view(instance, "gm", True)["campaign"]
    assert player_view["proposals"] == []
    assert gm_view["proposals"][0]["status"] == "pending"
    assert player_view["entities"]["fact"] == []

    _submit(
        runtime, instance, "campaign.proposal.resolve",
        proposal_id=proposal_id, option="confirm",
    )
    gm_view = runtime.gameplay_view(instance, "gm", True)["campaign"]
    player_view = runtime.gameplay_view(instance, "ally", False)["campaign"]
    assert gm_view["entities"]["fact"][0]["title"] == "Hidden cause"
    assert player_view["entities"]["fact"] == []


def test_portable_adventure_binding_allows_a_different_world_with_review_status() -> None:
    runtime, instance = _instance()
    instance.world_id = "custom_world"
    package = runtime._adventure_loader.resolve("core:lanterns_of_greymoor", "en")
    assert instance.bind_adventure(package.binding("custom_world"))
    gameplay = runtime.gameplay_view(instance, "gm", True)
    assert gameplay["campaign"]["adventure_binding"]["compatibility"] == "review_required"


def test_multiplayer_branch_collects_party_intents_before_advancing() -> None:
    runtime, instance = _instance(multiplayer=True, adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")

    player_actions = runtime.available_intents(instance, "ally")
    assert any(item["type"] == "party_decision.submit" for item in player_actions)
    assert not any(item["type"] == "tutorial.choose" for item in player_actions)
    gm_actions = runtime.available_intents(instance, "gm")
    assert any(item["type"] == "party_decision.resolve" for item in gm_actions)
    assert any(item["type"] == "tutorial.choose" for item in gm_actions)

    denied = runtime.validate_intent(instance, {
        "intent_id": "player-direct-choice", "type": "tutorial.choose",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "ally", "choice_id": "inspect_cold_ash",
    })
    assert denied["ok"] is False
    assert "party decision flow" in denied["error"]

    empty_resolve = runtime.validate_intent(instance, {
        "intent_id": "empty-party-resolve", "type": "party_decision.resolve",
        "expected_version": instance.ruleset_state["version"], "submitted_by": "gm",
    })
    assert empty_resolve["ok"] is False
    assert "choice is required" in empty_resolve["error"]

    _submit(runtime, instance, "party_decision.submit", "ally", choice_id="inspect_cold_ash")
    view = runtime.gameplay_view(instance, "ally", False)["campaign"]
    party = view["party_decision"]
    assert party["status"] == "open"
    assert party["submitted"] == {"ally": "inspect_cold_ash"}
    assert party["submitted_count"] == 1
    assert party["total_players"] == 2
    assert {item["id"] for item in party["choices"]} == {
        "inspect_cold_ash", "check_wagon_tracks",
    }

    duplicate = runtime.validate_intent(instance, {
        "intent_id": "duplicate-party-submit", "type": "party_decision.submit",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "ally", "choice_id": "check_wagon_tracks",
    })
    assert duplicate["ok"] is False
    assert "already submitted" in duplicate["error"]

    _submit(runtime, instance, "party_decision.submit", "gm", choice_id="check_wagon_tracks")
    resolved = _submit(runtime, instance, "party_decision.resolve", "gm")
    final_events = [event["type"] for event in resolved["event_batch"]["events"]]
    assert "dnd2024.party_decision.resolved" in final_events
    assert "dnd2024.tutorial.choice_applied" in final_events
    campaign = runtime.gameplay_view(instance, "gm", True)["campaign"]
    assert "party_decision" not in campaign
    assert campaign["tutorial"]["current_step"]["id"] == "keepers_plea"
    assert any(
        event["type"] == "dnd2024.party_decision.resolved"
        for batch in instance.event_ledger for event in batch["events"]
    )


def test_multiplayer_party_decision_tie_break_is_deterministic_and_gm_can_force() -> None:
    runtime, instance = _instance(multiplayer=True, adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")
    _submit(runtime, instance, "party_decision.submit", "gm", choice_id="inspect_cold_ash")
    _submit(runtime, instance, "party_decision.submit", "ally", choice_id="check_wagon_tracks")
    _submit(runtime, instance, "party_decision.resolve", "gm")
    history = runtime.gameplay_view(instance, "gm", True)["campaign"]["tutorial"]["history"]
    assert history[-1]["choice_id"] == "check_wagon_tracks"

    runtime, instance = _instance(multiplayer=True, adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")
    _submit(runtime, instance, "party_decision.submit", "ally", choice_id="inspect_cold_ash")
    _submit(runtime, instance, "party_decision.resolve", "gm", choice_id="check_wagon_tracks")
    history = runtime.gameplay_view(instance, "gm", True)["campaign"]["tutorial"]["history"]
    assert history[-1]["choice_id"] == "check_wagon_tracks"


def test_standard_professional_game_has_no_tutorial_actions() -> None:
    runtime, instance = _instance()
    instance.solo_mode = True
    _submit(runtime, instance, "session_zero.quick_start")
    gameplay = runtime.gameplay_view(instance, "gm", True)
    assert gameplay["campaign"]["tutorial"]["status"] == "unavailable"
    assert not any(
        item["type"].startswith("tutorial.")
        for item in runtime.available_intents(instance, "gm")
    )

def test_guided_adventure_runs_to_completion_with_combat_gate_and_summaries() -> None:
    runtime, instance = _instance(adventure=True)
    _agreement(runtime, instance)
    _submit(
        runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor",
    )
    _submit(runtime, instance, "tutorial.choose", choice_id="inspect_cold_ash")
    _submit(runtime, instance, "tutorial.choose", choice_id="reassure_mira")
    _submit(runtime, instance, "tutorial.choose", choice_id="follow_small_tracks")

    blocked = runtime.validate_intent(instance, {
        "intent_id": "skip-combat", "type": "tutorial.choose",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "gm", "choice_id": "secure_the_glade",
    })
    assert blocked["ok"] is False
    assert "objective" in blocked["error"]

    gameplay = runtime.gameplay_view(instance, "gm", True)
    assert gameplay["campaign"]["world_binding"]["world_id"] == "greymoor"
    binding = gameplay["campaign"]["adventure_binding"]
    assert binding["adventure_id"] == "core:lanterns_of_greymoor"
    assert binding["world_id"] == "greymoor"
    assert binding["recommended_world_id"] == "greymoor"
    assert binding["compatibility"] == "compatible"
    assert binding["scene_source"] == "adventure"
    assert binding["version"] == "1.0.0"
    assert binding["content_digest"].startswith("sha256:")
    preset = next(
        item for item in gameplay["encounter_presets"] if item["id"] == "first_skirmish"
    )
    _submit(
        runtime, instance, "combat.start",
        encounter_preset_id="first_skirmish", enemies=preset["enemies"],
    )
    _submit(runtime, instance, "combat.end")
    _submit(runtime, instance, "tutorial.choose", choice_id="secure_the_glade")
    final = _submit(runtime, instance, "tutorial.choose", choice_id="share_the_truth")

    campaign = runtime.gameplay_view(instance, "gm", True)["campaign"]
    assert campaign["tutorial"]["status"] == "completed"
    assert len(campaign["chapter_summaries"]) == 3
    assert any(item["id"] == "fact:shrine_truth_shared" for item in campaign["entities"]["fact"])
    task = next(item for item in campaign["entities"]["task"] if item["id"] == "task:recover_way_lantern")
    assert task["status"] == "completed"
    assert sum(
        event["type"] == "dnd2024.chapter.summarized"
        for batch in instance.event_ledger for event in batch["events"]
    ) == 3
    assert any(
        event["type"] == "dnd2024.tutorial.completed"
        for event in final["event_batch"]["events"]
    )
    llm_view = runtime.build_llm_view(instance)
    assert llm_view["ruleset_authority"]["campaign"]["tutorial"]["status"] == "completed"


def test_guided_combat_uses_story_preset_and_ignores_forged_enemy_stats() -> None:
    runtime, instance = _instance(adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")
    _submit(runtime, instance, "tutorial.choose", choice_id="inspect_cold_ash")
    _submit(runtime, instance, "tutorial.choose", choice_id="reassure_mira")
    _submit(runtime, instance, "tutorial.choose", choice_id="follow_small_tracks")

    available = runtime.available_intents(instance, "gm")
    start_action = next(item for item in available if item["type"] == "combat.start")
    assert start_action["requires"] == ["encounter_preset_id"]
    assert start_action["encounter_preset_id"] == "first_skirmish"

    wrong = runtime.validate_intent(instance, {
        "intent_id": "wrong-story-encounter", "type": "combat.start",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "gm", "encounter_preset_id": "cave_duo",
        "enemies": [],
    })
    assert wrong["ok"] is False
    assert "assigned encounter preset" in wrong["error"]

    version = int(instance.ruleset_state["version"])
    resolved = runtime.resolve_intent(instance, {
        "intent_id": "forged-story-enemy", "type": "combat.start",
        "expected_version": version, "submitted_by": "gm",
        "encounter_preset_id": "first_skirmish",
        "enemies": [{"id": "forged", "hp": 9999, "armor_class": 1, "attacks": []}],
    }, random.Random(7))
    assert resolved["ok"] is True
    started = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.combat.started"
    )
    assert set(started["enemies"]) == {"goblin-minion-1"}
    assert started["enemies"]["goblin-minion-1"]["hp"] == 7


def test_active_adventure_without_binding_reports_unprepared_instead_of_generic_presets() -> None:
    """活动冒险包内没有绑定遭遇时，必须可解释地停在“尚未准备”，不得回退通用目录。"""

    runtime, instance = _instance(adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")

    gameplay = runtime.gameplay_view(instance, "gm", True)
    access = gameplay["encounter_access"]
    assert gameplay["campaign"]["tutorial"]["status"] == "active"
    assert access["mode"] == "blocked"
    assert access["unprepared"] is True
    assert access["can_start"] is False
    assert access["catalog"] == "bundle"
    assert access["encounter_preset_id"] == ""

    # 没有 combat.start 可用动作：前端不会（也不该）拿到通用目录的开始入口。
    assert not any(
        item["type"] == "combat.start"
        for item in runtime.available_intents(instance, "gm")
    )

    # 叙事提出「开战」，即使模型硬塞一个通用 preset，也不会被写进请求。
    assert runtime.apply_narrative_combat_signal(instance, "start", {
        "kind": "combat", "mode": "assist", "confidence": 0.92,
        "encounter_preset_id": "goblin_patrol",
    }) is True
    request = runtime.gameplay_view(instance, "gm", True)["encounter_request"]
    assert request["status"] == "pending"
    assert "encounter_preset_id" not in request

    # 不带显式 sandbox 声明的开战请求被拒绝，并给出可解释原因。
    denied = runtime.validate_intent(instance, {
        "intent_id": "silent-fallback-start", "type": "combat.start",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "gm", "encounter_preset_id": "goblin_patrol",
    })
    assert denied["ok"] is False
    assert "no bound encounter" in denied["error"]

    # GM 显式声明自由遭遇后才允许使用通用目录，并且模式被持久化为 sandbox。
    _submit(
        runtime, instance, "combat.start", mode="sandbox",
        encounter_preset_id="goblin_patrol",
    )
    combat = runtime.gameplay_view(instance, "gm", True)["combat"]
    assert combat["status"] == "active"
    assert combat["mode"] == "sandbox"
    assert combat["adventure_binding"] is None
    assert combat["encounter_preset_id"] == "goblin_patrol"
    assert {actor["actor_id"] for actor in combat["actors"] if actor["kind"] == "enemy"}


def test_story_encounter_binding_is_recorded_and_cannot_be_downgraded_to_sandbox() -> None:
    runtime, instance = _instance(adventure=True)
    _agreement(runtime, instance)
    _submit(runtime, instance, "tutorial.start", adventure_id="lanterns_of_greymoor")
    _submit(runtime, instance, "tutorial.choose", choice_id="inspect_cold_ash")
    _submit(runtime, instance, "tutorial.choose", choice_id="reassure_mira")
    _submit(runtime, instance, "tutorial.choose", choice_id="follow_small_tracks")

    gameplay = runtime.gameplay_view(instance, "gm", True)
    access = gameplay["encounter_access"]
    assert access["mode"] == "story"
    assert access["catalog"] == "adventure"
    assert access["encounter_preset_id"] == "first_skirmish"

    # 剧情已绑定遭遇：既不能在开始页改用通用预设，也不能声明成自由遭遇。
    for fields in (
        {"mode": "sandbox", "encounter_preset_id": "goblin_patrol"},
        {"mode": "sandbox", "encounter_preset_id": "first_skirmish",
         "encounter_instance_id": access["encounter_instance_id"]},
    ):
        denied = runtime.validate_intent(instance, {
            "intent_id": f"downgrade-{fields['encounter_preset_id']}",
            "type": "combat.start",
            "expected_version": instance.ruleset_state["version"],
            "submitted_by": "gm", **fields,
        })
        assert denied["ok"] is False
        assert "assigned encounter preset" in denied["error"]

    _submit(
        runtime, instance, "combat.start",
        encounter_preset_id="first_skirmish",
        encounter_instance_id=access["encounter_instance_id"],
    )
    combat = runtime.gameplay_view(instance, "gm", True)["combat"]
    assert combat["mode"] == "story"
    assert combat["encounter_preset_id"] == "first_skirmish"
    assert combat["encounter_instance_id"] == "tutorial:lanterns_of_greymoor:thorn_ambush"
    assert combat["adventure_binding"] == {
        "adventure_id": "lanterns_of_greymoor",
        "step_id": "thorn_ambush",
        "encounter_preset_id": "first_skirmish",
        "encounter_instance_id": "tutorial:lanterns_of_greymoor:thorn_ambush",
    }
    # actor / initiative / available actions 都来自同一个 encounter instance。
    assert {actor["actor_id"] for actor in combat["actors"] if actor["kind"] == "enemy"} == {
        "enemy:goblin-minion-1",
    }
    assert combat["initiative"] == [actor["actor_id"] for actor in combat["actors"]]
    assert combat["current_actor_id"] in combat["initiative"]


def test_free_play_start_persists_sandbox_mode_without_an_adventure_binding() -> None:
    runtime, instance = _instance()
    gameplay = runtime.gameplay_view(instance, "gm", True)
    assert gameplay["encounter_access"]["mode"] == "sandbox"
    assert gameplay["encounter_access"]["unprepared"] is False
    assert gameplay["encounter_access"]["catalog"] == "bundle"

    _submit(runtime, instance, "combat.start", encounter_preset_id="goblin_patrol")

    combat = runtime.gameplay_view(instance, "gm", True)["combat"]
    assert combat["status"] == "active"
    assert combat["mode"] == "sandbox"
    assert combat["adventure_binding"] is None
    assert combat["encounter_instance_id"] == ""
    # 战斗进行中时不再暴露 start 入口。
    assert runtime.gameplay_view(instance, "gm", True)["encounter_access"]["can_start"] is False


def test_campaign_intent_replay_is_idempotent() -> None:
    runtime, instance = _instance()
    defaults = runtime.gameplay_view(instance, "gm", True)["campaign"][
        "session_zero_defaults"
    ]
    intent = {
        "intent_id": "session-replay", "type": "session_zero.propose",
        "expected_version": 0, "submitted_by": "gm", "agreement": defaults,
    }
    resolved = runtime.resolve_intent(instance, intent, random.Random(1))
    runtime.apply_event_batch(instance, resolved["event_batch"])
    replay = runtime.resolve_intent(instance, intent, random.Random(2))
    reapplied = runtime.apply_event_batch(instance, replay["event_batch"])

    assert replay["replayed"] is True
    assert reapplied["duplicate"] is True
    assert instance.ruleset_state["version"] == 1
