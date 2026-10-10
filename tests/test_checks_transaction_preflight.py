"""Unsupported checks slots reject transactions before any live-state mutation."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.engine import instance_lifecycle, round_recovery, turn_state
from src.engine.game_instance import GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import checks
from src.webui.services.manual_rolls import ManualRollDependencies, ManualRollService
from src.engine.modules import combat_extension_state
from tests.test_game_instance_reset_characterization import _make_populated_instance


def live_state(instance):
    # Do not use getters or codec normalization to observe partial writes.
    return {key: value for key, value in vars(instance).items() if not isinstance(value, asyncio.Lock)}


def unsupported_instance():
    instance = _make_populated_instance()
    instance.modules["checks"] = {"schema_version": 99, "opaque": [1]}
    # A wrapper must reject before even materializing another writable slot.
    instance.modules.pop("economy", None)
    return instance


@pytest.mark.parametrize("raw", [None, [], "corrupt", {"schema_version": 1, "last_checks": None}])
def test_preflight_does_not_repair_slot(raw):
    instance = _make_populated_instance()
    instance.modules["checks"] = deepcopy(raw)
    before = deepcopy(live_state(instance))
    checks.require_writable(instance)
    assert live_state(instance) == before


def test_preflight_does_not_materialize_missing_slot():
    instance = _make_populated_instance()
    del instance.modules["checks"]
    before = deepcopy(live_state(instance))
    checks.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("schema", [99, None, "1"])
def test_preflight_rejects_unknown_schema_without_mutation(schema):
    instance = unsupported_instance()
    instance.modules["checks"]["schema_version"] = schema
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        checks.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("direct", [False, True], ids=["public", "locked"])
@pytest.mark.parametrize("operation", [
    "reset", "start_round", "finish_judgment", "rollback_last_round", "abort_round_processing",
])
@pytest.mark.asyncio
async def test_transaction_rejection_preserves_all_live_state(operation, direct):
    instance = unsupported_instance()
    combat_extension_state.current(instance)["pending_summaries"] = ["must remain pending"]
    args = ("new narration",) if operation == "finish_judgment" else ()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        if direct:
            owner = (
                instance_lifecycle if operation == "reset"
                else turn_state if operation == "start_round" else round_recovery
            )
            async with instance._lock:
                getattr(owner, operation + "_locked")(instance, *args)
        else:
            await getattr(instance, operation)(*args)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["advance_round", "try_advance", "aggregate_locked", "owner_locked"])
@pytest.mark.asyncio
async def test_advance_rejects_before_readiness_snapshots_or_economy_materialization(operation):
    instance = unsupported_instance()
    instance.state = GameState.ACTIVE_ACTION
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        if operation == "aggregate_locked":
            async with instance._lock:
                instance._do_advance_locked()
        elif operation == "owner_locked":
            async with instance._lock:
                turn_state.do_advance_locked(instance)
        else:
            await getattr(instance, operation)()
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", [
    "record_check", "sync_last_check", "complete_round_check_preparation", "reset_round_checks",
    "record", "sync_last", "mark_prepared", "clear_round", "invalidate_prepared", "add_manual_roll_request",
])
def test_check_owner_entries_reject_before_modifying_any_field(operation):
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    argument = ({"check_id": "new"},) if operation in {
        "record_check", "sync_last_check", "record", "sync_last", "add_manual_roll_request",
    } else ()
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        if hasattr(instance, operation):
            getattr(instance, operation)(*argument)
        else:
            getattr(checks, operation)(instance, *argument)
    assert live_state(instance) == before


@pytest.mark.parametrize("key", ["last_check", "last_checks", "round_checks_prepared", "manual_roll_requests"])
def test_ruleset_restore_preflights_check_snapshot_keys_before_other_restoration(key):
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    # Deliberately omit the remaining required restore keys: rejection must
    # precede assignment of ruleset_state, even without last_activity present.
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        instance.restore_ruleset_transaction({key: None, "ruleset_state": {"changed": True}})
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["create", "resolve", "cancel"])
@pytest.mark.parametrize("attribute_double", [False, True], ids=["aggregate", "attribute-double"])
@pytest.mark.asyncio
async def test_manual_roll_rejection_preserves_requests_and_status(operation, attribute_double):
    instance = _make_populated_instance()
    checks.manual_roll_requests(instance).clear()
    if attribute_double:
        # Non-aggregate doubles carry only module state; the service reads the
        # checks slot directly, so rejection must not depend on GameInstance.
        instance = SimpleNamespace(
            modules={}, gm_uid=instance.gm_uid,
            game_key=instance.game_key, run_id=instance.run_id,
            round_number=instance.round_number, players=instance.players,
        )
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
    request_before = deepcopy(request)
    save.reset_mock()
    instance.modules["checks"] = {"schema_version": 99, "opaque": [1]}
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        if operation == "create":
            await service.create("game", "gm1", {
                "run_id": instance.run_id, "operation_id": "new", "target_uids": ["u1"],
            })
        else:
            await getattr(service, operation)("game", "gm1", request["id"], {
                "run_id": instance.run_id, "target_uid": "u1", "reason": "cancel",
            })
    assert live_state(instance) == before
    assert request == request_before
    save.assert_not_called()


@pytest.mark.parametrize("operation", ["spend", "decline", "decline_all", "timeout"])
@pytest.mark.asyncio
async def test_luck_rejection_preserves_resources_checks_and_timers(operation):
    instance = _make_populated_instance()
    instance.players["u1"]["character_sheet"]["luck"] = 50
    check = {
        "check_id": "luck", "actor_uid": "u1", "dice": "d100", "roll": 60,
        "threshold": 50, "verdict": "失败", "luck_decision": "pending",
        "luck_spend_available": True,
    }
    checks.replace_last_checks(instance, [check])
    checks.replace_last_check(instance, dict(check))
    # Retain the data inside the unsupported slot to detect accidental writes.
    instance.modules["checks"]["schema_version"] = 99
    timer = Mock()
    instance._luck_timers["luck"] = timer
    before = deepcopy({**live_state(instance), "_luck_timers": {}})
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        if operation in {"spend", "decline"}:
            await instance.resolve_luck_decision("luck", "u1", operation == "spend")
        elif operation == "decline_all":
            await instance.decline_pending_luck()
        else:
            await instance.system_decline_luck("luck")
    assert {**live_state(instance), "_luck_timers": {}} == before
    assert instance._luck_timers == {"luck": timer}
    timer.cancel.assert_not_called()


@pytest.mark.parametrize("operation", [
    "prepare_round_checks", "prepare_round_checks_ai", "process_round", "process_round_impl",
])
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
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        if operation == "prepare_round_checks":
            processor.prepare_round_checks(instance)
        else:
            await getattr(processor, operation)(instance)
    assert live_state(instance) == before
    processor.llm_client.call.assert_not_called()


@pytest.mark.asyncio
async def test_start_game_rejects_before_activation():
    from src.commands.game_lifecycle import GameLifecycle

    instance = unsupported_instance()
    lifecycle = object.__new__(GameLifecycle)
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        await lifecycle.start_game(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["submit", "advance", "resume", "progression"])
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
    instance.players["u1"]["control"] = {
        "mode": "human", "revision": 3, "temporary": False, "resume_mode": "human",
    }
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported checks module schema"):
        if operation == "submit":
            await turns.submit_action(dependencies, "game", "u1", "Look")
        elif operation == "advance":
            await turns.advance_round(dependencies, "game", "u1", force=True)
        elif operation == "resume":
            await turns.resume_after_control_change(dependencies, "game")
        else:
            await turns._advance_progression(dependencies, instance, game_key="game")
    assert live_state(instance) == before
    retry.assert_not_called()
    fill.assert_not_called()
