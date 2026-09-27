"""Unsupported statistics must reject whole transactions before live mutations."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.engine import instance_lifecycle, round_recovery, turn_state
from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import session_stats
from src.webui.services.manual_rolls import ManualRollDependencies, ManualRollService
from tests.test_game_instance_reset_characterization import _make_populated_instance


def live_state(instance):
    # Inspect storage directly: to_dict/property reads can normalize other slots
    # and would hide precisely the partial mutations these tests protect against.
    return {
        key: value for key, value in vars(instance).items()
        if not isinstance(value, asyncio.Lock)
    }


def unsupported_instance():
    instance = _make_populated_instance()
    instance.modules["session_stats"] = {"schema_version": 99, "opaque": [1]}
    # Wrapper preflights must precede even materialization of another module.
    instance.modules.pop("economy", None)
    return instance


@pytest.mark.parametrize("raw", [None, [], "corrupt", {"schema_version": 1, "total_tokens": -1}])
def test_preflight_does_not_repair_slot(raw):
    instance = GameInstance(game_key=("test", "preflight", "bot"))
    instance.modules["session_stats"] = deepcopy(raw)
    before = deepcopy(live_state(instance))
    session_stats.require_writable(instance)
    assert live_state(instance) == before


def test_preflight_does_not_materialize_missing_slot():
    instance = GameInstance(game_key=("test", "preflight", "bot"))
    del instance.modules["session_stats"]
    before = deepcopy(live_state(instance))
    session_stats.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("schema", [99, None, "1"])
def test_preflight_rejects_unknown_schema_without_mutation(schema):
    instance = unsupported_instance()
    instance.modules["session_stats"]["schema_version"] = schema
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        session_stats.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("direct", [False, True], ids=["public", "locked"])
@pytest.mark.parametrize("operation", [
    "activate", "reset", "add_action", "start_round", "apply_action_roll",
    "set_player_away", "finish_judgment", "finish_judgment_with_swipe",
    "rollback_last_round", "abort_round_processing",
])
@pytest.mark.asyncio
async def test_transaction_rejection_preserves_all_live_state(operation, direct):
    instance = unsupported_instance()
    if operation in {"add_action", "apply_action_roll", "set_player_away"}:
        instance.state = GameState.ACTIVE_ACTION
    instance.away_players.add("u1")
    instance.action_queue[0]["dice_pending"] = True
    instance.combat_extension["pending_summaries"] = ["must remain pending"]
    args = {
        "activate": (), "reset": (), "add_action": ("u1", "replacement"),
        "start_round": (), "apply_action_roll": ("u1", "d20", 12),
        "set_player_away": ("u1", False), "finish_judgment": ("new narration",),
        "finish_judgment_with_swipe": ("new swipe", 4),
        "rollback_last_round": (), "abort_round_processing": (),
    }[operation]
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        if direct:
            owner = (
                instance_lifecycle if operation in {"activate", "reset"}
                else turn_state if operation in {
                    "add_action", "start_round", "apply_action_roll", "set_player_away",
                } else round_recovery
            )
            async with instance._lock:
                getattr(owner, operation + "_locked")(instance, *args)
        else:
            await getattr(instance, operation)(*args)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["create", "resolve", "cancel"])
@pytest.mark.asyncio
async def test_manual_roll_rejection_preserves_requests_and_status(operation):
    instance = _make_populated_instance()
    instance.manual_roll_requests.clear()
    save = AsyncMock()
    service = ManualRollService(ManualRollDependencies(
        parse_game_key=lambda key: instance.game_key,
        get_instance=lambda key: instance,
        save_instance=save,
    ))
    result = await service.create("game", "gm1", {
        "run_id": instance.run_id, "operation_id": "existing", "target_uids": ["u1"],
    })
    assert result["ok"]
    request = result["request"]
    save.reset_mock()
    instance.modules["session_stats"] = {"schema_version": 99, "opaque": [1]}
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        if operation == "create":
            await service.create("game", "gm1", {
                "run_id": instance.run_id, "operation_id": "new", "target_uids": ["u1"],
            })
        else:
            await getattr(service, operation)("game", "gm1", request["id"], {
                "run_id": instance.run_id, "target_uid": "u1", "reason": "cancel",
            })
    assert live_state(instance) == before
    assert request == before["manual_roll_requests"][0]
    save.assert_not_called()


@pytest.mark.parametrize("operation", ["spend", "decline", "decline_all", "timeout"])
@pytest.mark.asyncio
async def test_luck_rejection_preserves_resources_checks_and_timers(operation):
    instance = unsupported_instance()
    instance.players["u1"]["character_sheet"]["luck"] = 50
    check = {
        "check_id": "luck", "actor_uid": "u1", "dice": "d100", "roll": 60,
        "threshold": 50, "verdict": "失败", "luck_decision": "pending",
        "luck_spend_available": True,
    }
    instance.last_checks = [check]
    instance.last_check = dict(check)
    timer = Mock()
    instance._luck_timers["luck"] = timer
    before = deepcopy({**live_state(instance), "_luck_timers": {}})
    with pytest.raises(ModuleStateError):
        if operation in {"spend", "decline"}:
            await instance.resolve_luck_decision("luck", "u1", operation == "spend")
        elif operation == "decline_all":
            await instance.decline_pending_luck()
        else:
            await instance.system_decline_luck("luck")
    assert {**live_state(instance), "_luck_timers": {}} == before
    assert instance._luck_timers == {"luck": timer}
    timer.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_story_recap_rejection_preserves_log():
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        await instance.append_story_recap({"text": "recap"}, target_entry=instance.log[0], tokens=12)
    assert live_state(instance) == before


def test_ruleset_restore_rejects_before_restoring_other_fields():
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        instance.restore_ruleset_transaction({"last_activity": "old", "ruleset_state": {"new": True}})
    assert live_state(instance) == before


def test_record_usage_rejects_without_mutation():
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        instance.record_llm_usage(10)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["prepare_round_checks_ai", "process_round", "process_round_impl"])
@pytest.mark.asyncio
async def test_round_processor_rejects_before_checks_or_generation(tmp_path, operation):
    from src.commands.round_processor import RoundProcessor

    instance = unsupported_instance()
    registry = GameRegistry(tmp_path)
    registry.register(instance)
    processor = object.__new__(RoundProcessor)
    processor.registry = registry
    processor.llm_client = SimpleNamespace(call=AsyncMock())
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        await getattr(processor, operation)(instance)
    assert live_state(instance) == before
    processor.llm_client.call.assert_not_called()


@pytest.mark.parametrize("direct", [False, True], ids=["public", "staged"])
@pytest.mark.asyncio
async def test_swipe_generation_rejects_before_history_rewrite(direct):
    from src.commands.swipe_generator import SwipeGenerator

    instance = unsupported_instance()
    generator = SwipeGenerator(
        llm_client=None, matcher=None, prompt=None, state_applier=None,
        load_world_template=Mock(), ensure_matcher_for_world=Mock(), narrative_max_tokens=100,
    )
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        if direct:
            await generator._generate_locked(instance, 4)
        else:
            await generator.generate(instance, 4)
    assert live_state(instance) == before


@pytest.mark.asyncio
async def test_start_game_rejects_before_activation():
    from src.commands.game_lifecycle import GameLifecycle

    instance = unsupported_instance()
    lifecycle = object.__new__(GameLifecycle)
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        await lifecycle.start_game(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["intent", "resume"])
@pytest.mark.asyncio
async def test_ruleset_transactions_reject_before_binding_or_reducer(tmp_path, monkeypatch, operation):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "rulesets"))
    from tests.rulesets.test_dnd2024_m5_http import _EnabledRuntime, _M5Api, _character, _ready_story_encounter
    from src.webui.services import ruleset_gameplay

    runtime = _EnabledRuntime()
    registry = GameRegistry(tmp_path)
    instance = GameInstance(game_key=("web", "intent", "bot"), rule_id="dnd2024_srd", gm_uid="gm")
    character = _character(runtime, "stalwart_guardian", "Hero")
    instance.players["gm"] = {"character_name": "Hero", "character_sheet": character}
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    action = _ready_story_encounter(runtime, instance)
    registry.register(instance)
    save = AsyncMock()
    dependencies = replace(_M5Api(registry, runtime)._gameplay_dependencies, save_instance=save)
    if operation == "resume":
        instance.ruleset_state["combat"] = {"status": "active"}
    instance.modules["session_stats"] = {"schema_version": 99, "opaque": [1]}
    before = deepcopy(live_state(instance))
    binding = AsyncMock(side_effect=AssertionError("must not migrate binding"))
    monkeypatch.setattr(ruleset_gameplay, "_ensure_compatible_adventure_binding", binding)
    reducer = Mock(side_effect=AssertionError("must not reduce"))
    monkeypatch.setattr(runtime, "apply_event_batch", reducer)
    with pytest.raises(ModuleStateError):
        if operation == "intent":
            await ruleset_gameplay.submit_intent(dependencies, "web|intent|bot", "gm", True, {
                **action, "intent_id": "start", "expected_version": instance.ruleset_state["version"],
            })
        else:
            await ruleset_gameplay.resume_authoritative_combat(dependencies, "web|intent|bot", "gm")
    assert live_state(instance) == before
    binding.assert_not_called()
    reducer.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("operation", ["submit", "advance", "resume"])
@pytest.mark.asyncio
async def test_turn_services_reject_before_outbox_or_ai(tmp_path, monkeypatch, operation):
    from src.webui.services import turns

    instance = unsupported_instance()
    instance.state = GameState.ACTIVE_ACTION
    instance.gm_uid = "u1"
    registry = GameRegistry(tmp_path)
    registry.register(instance)
    dependencies = SimpleNamespace(
        get_instance=registry.get, parse_game_key=lambda key: instance.game_key,
        load_rule_for_game=lambda instance: None, resume_authoritative_combat=None,
    )
    retry = AsyncMock(side_effect=AssertionError("must not drain outbox"))
    fill = AsyncMock(side_effect=AssertionError("must not call AI"))
    monkeypatch.setattr(turns, "_retry_external_economy_effects", retry)
    monkeypatch.setattr(turns, "_fill_ai_player_actions", fill)
    # Admission reads player control; establish it before measuring mutations.
    instance.players["u1"]["control"] = {
        "mode": "human", "revision": 3, "temporary": False, "resume_mode": "human",
    }
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError):
        if operation == "submit":
            await turns.submit_action(dependencies, "game", "u1", "Look")
        elif operation == "advance":
            await turns.advance_round(dependencies, "game", "u1", force=True)
        else:
            await turns.resume_after_control_change(dependencies, "game")
    assert live_state(instance) == before
    retry.assert_not_called()
    fill.assert_not_called()
