"""Real D&D authority with model-selected intents and in-flight isolation."""
from __future__ import annotations
from src.engine.modules import adventure_runtime_state, ruleset_runtime

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameState
from src.engine.player_control import set_control
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services.ai_seats import AISeatRuntime
from src.webui.services.ruleset_gameplay import RulesetGameplayDependencies, resume_authoritative_combat, submit_intent
from tests.test_ai_player_combat import _setup, _start, _set_turn, _goblin
from tests.test_ai_seat_settings import config


def setup(*, choose="dodge", during=None, failure=None, save_failure=False, raw_output=None):
    runtime, engine, inst = _setup()
    inst.state = GameState.ACTIVE_ACTION
    adventure_runtime_state.replace_play_mode(inst, 'free')
    _start(engine, inst, rolls=[18, 10, 1], enemy_hp=200)
    _set_turn(engine, inst, "player:ai1")
    inst.players["gm"]["character_sheet"]["private_note"] = "OTHER-SEAT-SECRET"
    inst.npcs["hidden"] = {"secret": "GM-SECRET"}
    state, calls, clients, saved = config(), [], [], []

    class Client:
        def __init__(self, **_kwargs):
            self.closed = False
            clients.append(self)

        async def call(self, system, context, **_kwargs):
            assert not inst._lock.locked()
            assert not inst._authority_lock.locked()
            assert not inst._process_lock.locked()
            assert "GM-SECRET" not in context
            assert "OTHER-SEAT-SECRET" not in context
            calls.append(context)
            if during:
                await during(inst, state)
            if failure:
                raise failure
            choices = json.loads(context.split("LEGAL_CHOICES\n", 1)[1])
            row = next((row for row in choices if row["type"] == choose), choices[0])
            return SimpleNamespace(narration=raw_output if raw_output is not None else json.dumps({"choice_id": row["choice_id"]}))

        async def close(self):
            self.closed = True

    async def save(instance):
        if save_failure:
            raise OSError("save unavailable")
        saved.append(deepcopy(instance.to_dict()))

    deps = RulesetGameplayDependencies(
        get_instance=lambda _key: inst, parse_game_key=lambda _key: inst.game_key,
        load_rule_for_game=lambda _inst: SimpleNamespace(rule_id="dnd2024_srd", template={
            "runtime": {"id": runtime.runtime_id, "minimum_version": runtime.runtime_version}}),
        ruleset_registry=RulesetRuntimeRegistry([runtime]), resolve_adventure_binding=lambda *_: {},
        save_instance=save, apply_memory_delta=None,
        seat_runtime=AISeatRuntime(config_state=lambda: state, client_factory=Client),
    )
    return runtime, inst, deps, calls, clients, saved


def test_combat_choices_are_validated_own_seat_operations():
    runtime, engine, inst = _setup()
    _start(engine, inst, rolls=[18, 10, 1])
    _set_turn(engine, inst, "player:ai1")
    choices = runtime.hosted_intent_choices(inst, "ai1")
    assert {row["type"] for row in choices} >= {"attack", "dodge", "end_turn"}
    assert all(row["actor_id"] == "player:ai1" for row in choices)
    assert all(row["submitted_by"] == "gm" for row in choices)
    assert all(runtime.validate_intent(inst, row)["ok"] for row in choices)
    assert runtime.hosted_intent_choices(inst, "gm") == []
    set_control(inst, "ai1", "human")
    assert runtime.hosted_intent_choices(inst, "ai1") == []


@pytest.mark.asyncio
async def test_model_can_choose_dodge_and_server_resolves_and_persists():
    _runtime, inst, deps, calls, clients, saved = setup()
    result = await resume_authoritative_combat(deps, "game", "ai1")
    assert result["ok"] and result["resumed"]
    assert result["automatic_event_batches"][0]["intent_type"] == "dodge"
    assert calls and all(c.closed for c in clients)
    assert saved
    assert inst.modules["ai_seats"]["outcomes"]["ai1"]["status"] == "added"
    assert deps.seat_runtime.project(inst)["seats"]["ai1"]["last_outcome"]["round"] == 1


@pytest.mark.asyncio
async def test_model_failure_uses_legal_rules_fallback_with_visible_reason():
    _runtime, inst, deps, calls, clients, saved = setup(failure=TimeoutError("secret URL"))
    result = await resume_authoritative_combat(deps, "game", "ai1")
    assert result["ok"] and result["resumed"]
    assert result["automatic_event_batches"][0]["intent_type"] == "attack"
    outcome = inst.modules["ai_seats"]["outcomes"]["ai1"]
    assert outcome["status"] == "fallback" and outcome["reason"] == "provider_timeout"
    assert "secret URL" not in json.dumps(saved)
    assert calls and all(c.closed for c in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["control", "config", "reset", "join"])
async def test_inflight_change_discards_entire_plan_without_overwriting_live_state(mutation):
    async def during(inst, state):
        if mutation == "control":
            set_control(inst, "ai1", "human")
        elif mutation == "config":
            state["ai_providers"].clear()
        elif mutation == "reset":
            inst.run_id = "new-run"
        else:
            inst.put_player("late", {"character_name": "New arrival"})
    _runtime, inst, deps, _calls, clients, saved = setup(during=during)
    before = deepcopy(ruleset_runtime.state(inst))
    result = await resume_authoritative_combat(deps, "game", "ai1")
    assert not result["ok"] and not result["resumed"]
    assert ruleset_runtime.state(inst) == before
    assert not saved and all(c.closed for c in clients)
    if mutation == "join":
        assert "late" in inst.players


@pytest.mark.asyncio
async def test_failed_save_restores_state_and_outcomes():
    _runtime, inst, deps, _calls, clients, _saved = setup(save_failure=True)
    before = deepcopy(inst.to_dict())
    with pytest.raises(OSError):
        await resume_authoritative_combat(deps, "game", "ai1")
    assert inst.to_dict() == before
    assert all(c.closed for c in clients)


@pytest.mark.asyncio
async def test_cancelled_model_does_not_publish_partial_ladder():
    async def during(_inst, _state):
        raise asyncio.CancelledError()
    _runtime, inst, deps, _calls, clients, saved = setup(during=during)
    before = deepcopy(inst.to_dict())
    with pytest.raises(asyncio.CancelledError):
        await resume_authoritative_combat(deps, "game", "ai1")
    assert inst.to_dict() == before and not saved
    assert all(c.closed for c in clients)


@pytest.mark.asyncio
async def test_human_submission_enters_same_model_automation_boundary():
    runtime, inst, deps, calls, _clients, _saved = setup()
    engine = runtime._combat_engine(inst, locale="en")
    _set_turn(engine, inst, "player:gm")
    # GM -> AI -> enemy -> GM in this controlled initiative order.
    combat = ruleset_runtime.state(inst)["combat"]
    combat["initiative"] = ["player:gm", "player:ai1", "enemy:goblin-1"]
    # 顺序必须同时写进 ``turn_slots``：S0-F20 之后它才是权威（一个行动者可以
    # 有两个槽），只改 initiative 会让 turn_index 指向另一位行动者。
    combat["turn_slots"] = [
        {
            "slot_id": f"{actor}#1", "actor_id": actor,
            "initiative_total": 20 - index, "kind": "normal",
        }
        for index, actor in enumerate(combat["initiative"])
    ]
    _set_turn(engine, inst, "player:gm")
    result = await submit_intent(deps, "game", "gm", True, {
        "type": "end_turn", "intent_id": "human-end", "actor_id": "player:gm",
        "expected_version": ruleset_runtime.state(inst)["version"],
    })
    assert result["ok"] and calls
    assert result["result"]["automatic_event_batches"][0]["intent_type"] == "dodge"


@pytest.mark.asyncio
async def test_no_injected_seat_runtime_keeps_existing_rules_automation():
    _runtime, _inst, deps, calls, _clients, _saved = setup()
    result = await resume_authoritative_combat(replace(deps, seat_runtime=None), "game", "ai1")
    assert result["ok"] and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ['{"choice_id":"invented"}', '{"choice_id":"c0","damage":999}',
                                   'I attack another player.', '```json\n{"choice_id":"c0"}\n```'])
async def test_untrusted_output_cannot_author_mechanics(output):
    _runtime, inst, deps, calls, _clients, _saved = setup(raw_output=output)
    result = await resume_authoritative_combat(deps, "game", "ai1")
    assert result["ok"]
    assert result["automatic_event_batches"][0]["intent_type"] == "attack"
    assert len(calls) == 1
    assert inst.modules["ai_seats"]["outcomes"]["ai1"]["reason"] == "unusable_output"


@pytest.mark.asyncio
async def test_concurrent_hosted_plans_publish_only_one_ladder():
    reached, release = 0, asyncio.Event()

    async def during(_inst, _state):
        nonlocal reached
        reached += 1
        if reached == 2:
            release.set()
        await release.wait()

    _runtime, _inst, deps, _calls, clients, saved = setup(choose="end_turn", during=during)
    results = await asyncio.gather(*(resume_authoritative_combat(deps, "game", "ai1") for _ in range(2)))
    assert sum(result["ok"] for result in results) == 1
    assert len(saved) == 1 and all(client.closed for client in clients)


def test_staging_preserves_new_seats_and_unbounded_live_history_with_independent_locks():
    from src.engine.staged_transaction import StagedGameTransaction
    _runtime, _engine, inst = _setup()
    inst.put_player("new", {"character_name": "Not in historical logs"})
    inst.log = [{"round": i, "gm_response": str(i), "actions": []} for i in range(130)]
    transaction = StagedGameTransaction(inst)
    assert "new" in transaction.staged.players
    assert len(transaction.staged.log) == 130
    assert transaction.staged._lock is not inst._lock
    assert transaction.staged._authority_lock is not inst._authority_lock
    assert transaction.matches(inst)
    inst.log[0]["gm_response"] = "edited old history"
    assert not transaction.matches(inst)


def test_combat_retry_uses_turn_authority_instead_of_narrative_human_gate():
    from src.webui.services.ai_seats import retry_block
    _runtime, inst, _deps, _calls, _clients, _saved = setup()
    assert not inst.human_actions_ready()
    assert retry_block(inst, "ai1") == ""


@pytest.mark.parametrize("preset", ["kindly_bulwark", "stalwart_guardian"])
def test_class_and_spell_choices_remain_real_validated_intents(preset):
    runtime, engine, inst = _setup(ai_preset=preset)
    _start(engine, inst, rolls=[18, 10, 1], enemy_hp=200)
    _set_turn(engine, inst, "player:ai1")
    choices = runtime.hosted_intent_choices(inst, "ai1")
    assert choices
    assert all(runtime.validate_intent(inst, choice)["ok"] for choice in choices)


@pytest.mark.asyncio
async def test_round_completion_scheduler_waits_for_live_locks_before_model_request():
    from src.commands.hosted_combat import HostedCombatScheduler
    _runtime, inst, deps, calls, _clients, saved = setup()
    scheduler = HostedCombatScheduler(
        get_instance=deps.get_instance, seat_for=lambda _inst: "ai1",
        resume=lambda key, uid: resume_authoritative_combat(deps, key, uid),
    )
    async with inst.authoritative_write():
        async with inst._process_lock:
            scheduler.schedule(inst)
            scheduler.schedule(inst)
            await asyncio.sleep(0)
            assert not calls
    await scheduler.wait_idle()
    assert calls and len(saved) == 1
    await scheduler.close()


def test_director_defers_hosted_pc_to_model_boundary():
    from src.rulesets.automation import apply_director_automation
    runtime, engine, inst = _setup()
    original = runtime.director_automatic_intent
    runtime.director_automatic_intent = lambda *_: {
        "intent_id": "director-start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }
    try:
        from tests.test_ai_player_combat import SequenceRng
        # Force AI seat first, so Director must stop after starting combat.
        batches = apply_director_automation(runtime, inst, {"kind": "combat"}, SequenceRng([20, 1, 1]),
                                            defer_hosted_players=True)
    finally:
        runtime.director_automatic_intent = original
    assert len(batches) == 1
    assert runtime.next_automatic_intent(inst)["actor_id"] == "player:ai1"


@pytest.mark.asyncio
async def test_model_cannot_keep_combat_open_by_repeated_movement():
    runtime, inst, deps, calls, _clients, saved = setup(choose="move")
    canonical = inst.players["ai1"]["character_sheet"]["ruleset_character"]
    canonical["derived"]["speed"] = 300
    _set_turn(runtime._combat_engine(inst, locale="en"), inst, "player:ai1")
    result = await resume_authoritative_combat(deps, "game", "ai1")
    assert result["ok"] and result["resumed"]
    assert len(calls) <= 8
    assert saved
    assert ruleset_runtime.state(inst)["combat"]["initiative"][ruleset_runtime.state(inst)["combat"]["turn_index"]] != "player:ai1"


def test_spent_enemy_reaction_does_not_create_an_unresolvable_movement_decision():
    runtime, engine, inst = _setup()
    _start(engine, inst, rolls=[18, 10, 1])
    _set_turn(engine, inst, "player:ai1")
    ruleset_runtime.state(inst)["combat"]["reactions"]["enemy:goblin-1"] = 0
    from tests.test_ai_player_combat import SequenceRng
    result = runtime.resolve_intent(inst, {
        "intent_id": "leave-spent-reaction", "type": "move", "actor_id": "player:ai1",
        "submitted_by": "gm", "expected_version": ruleset_runtime.state(inst)["version"], "distance": -10,
    }, SequenceRng([]))
    assert result["ok"] and result.get("pending_decision") is None
    assert runtime.apply_event_batch(inst, result["event_batch"])["applied"]
