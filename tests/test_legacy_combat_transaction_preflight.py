"""Unsupported compatibility combat slots reject before live transaction writes."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine import instance_lifecycle, round_recovery, round_snapshots, turn_state
from src.engine.game_instance import GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import legacy_combat
from tests.test_game_instance_reset_characterization import _make_populated_instance
from src.engine.modules import ruleset_runtime
from src.engine.modules import round_safety


def live_state(instance):
    return {key: value for key, value in vars(instance).items() if not isinstance(value, asyncio.Lock)}


def unsupported_instance():
    instance = _make_populated_instance()
    instance.modules["legacy_combat"]["schema_version"] = 99
    instance.modules.pop("economy", None)
    return instance


@pytest.mark.parametrize("raw", [None, [], "corrupt", {"schema_version": 1, "initiative_order": None}])
def test_preflight_does_not_repair_slot(raw):
    instance = _make_populated_instance()
    instance.modules["legacy_combat"] = deepcopy(raw)
    before = deepcopy(live_state(instance))
    legacy_combat.require_writable(instance)
    assert live_state(instance) == before


def test_preflight_does_not_materialize_missing_slot():
    instance = _make_populated_instance()
    del instance.modules["legacy_combat"]
    before = deepcopy(live_state(instance))
    legacy_combat.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("schema", [99, None, "1"])
def test_preflight_rejects_unknown_schema_without_mutation(schema):
    instance = unsupported_instance()
    instance.modules["legacy_combat"]["schema_version"] = schema
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        legacy_combat.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("direct", [False, True], ids=["public", "locked"])
@pytest.mark.parametrize("operation", ["reset", "abort_round_processing"])
@pytest.mark.asyncio
async def test_transaction_rejection_preserves_all_live_state(operation, direct):
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        if direct:
            owner = instance_lifecycle if operation == "reset" else round_recovery
            async with instance._lock:
                getattr(owner, operation + "_locked")(instance)
        else:
            await getattr(instance, operation)()
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["advance_round", "try_advance", "aggregate_locked", "owner_locked"])
@pytest.mark.asyncio
async def test_advance_rejects_before_readiness_snapshots_or_economy_materialization(operation):
    instance = unsupported_instance()
    instance.state = GameState.ACTIVE_ACTION
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        if operation == "aggregate_locked":
            async with instance._lock:
                instance._do_advance_locked()
        elif operation == "owner_locked":
            async with instance._lock:
                turn_state.do_advance_locked(instance)
        else:
            await getattr(instance, operation)()
    assert live_state(instance) == before


@pytest.mark.parametrize("operation,args", [
    ("begin", (["u1"],)), ("end", ()), ("reset", ()),
    ("project_from_ruleset", ({"status": "active"},)),
    ("restore_from_entity_snapshot", ({},)), ("restore_from_transaction", ({},)),
    ("begin_combat", (["u1"],)), ("end_combat", ()),
])
def test_owner_and_aggregate_combat_entries_reject_before_mutation(operation, args):
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        if operation in {"begin_combat", "end_combat"}:
            getattr(instance, operation)(*args)
        else:
            getattr(legacy_combat, operation)(instance, *args)
    assert live_state(instance) == before


def test_ruleset_restore_rejects_before_any_field_assignment():
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        instance.restore_ruleset_transaction({"ruleset_state": {"changed": True}})
    assert live_state(instance) == before


@pytest.mark.parametrize("direct", [False, True], ids=["aggregate", "owner"])
@pytest.mark.parametrize("operation", ["capture", "restore"])
def test_entity_snapshot_entries_reject_before_npc_or_snapshot_writes(operation, direct):
    instance = unsupported_instance()
    round_safety.replace_entity_snapshot(instance, {"npcs": {"different": {"hp": 1}}, "combat_enemies": []})
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        if direct:
            getattr(round_snapshots, operation + "_round_entity_snapshot")(instance)
        else:
            getattr(instance, operation + "_round_entity_snapshot")()
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["rollback", "abort", "advance", "empty_entity_restore"])
@pytest.mark.asyncio
async def test_existing_noop_guards_remain_before_preflight(operation):
    instance = unsupported_instance()
    if operation == "rollback":
        instance.log.clear()
    if operation in {"abort", "advance"}:
        instance.state = GameState.WAITING
    if operation == "empty_entity_restore":
        round_safety.round_entity_snapshot(instance).clear()
    before = deepcopy(live_state(instance))
    if operation == "rollback":
        assert await instance.rollback_last_round() is None
    elif operation == "abort":
        assert await instance.abort_round_processing() is False
    elif operation == "advance":
        async with instance._lock:
            assert turn_state.do_advance_locked(instance) is False
    else:
        assert instance.restore_round_entity_snapshot() is False
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["start_round", "finish_judgment", "rollback_last_round"])
@pytest.mark.asyncio
async def test_independent_round_transactions_do_not_require_legacy_projection(operation):
    instance = unsupported_instance()
    slot = deepcopy(instance.modules["legacy_combat"])
    args = ("narration",) if operation == "finish_judgment" else ()
    await getattr(instance, operation)(*args)
    assert instance.modules["legacy_combat"] == slot


@pytest.mark.parametrize("operation", ["process_round", "process_round_impl"])
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
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        await getattr(processor, operation)(instance)
    assert live_state(instance) == before
    processor.llm_client.call.assert_not_called()


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
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
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


@pytest.mark.parametrize("initialized", [False, True])
def test_dnd_combat_batch_rejects_before_initialization_or_reducer(initialized, monkeypatch):
    from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
    from src.rulesets.dnd2024.runtime import Dnd2024Runtime

    runtime = Dnd2024Runtime()
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"))
    instance = unsupported_instance()
    ruleset_runtime.replace_state(instance, {})
    if initialized:
        engine.initialize_state(instance)
    before = deepcopy(live_state(instance))

    def forbidden_reducer(*args):
        raise AssertionError("must reject before reduction")

    monkeypatch.setattr(Dnd2024CombatEngine, "_reduce_event", forbidden_reducer)
    batch = {
        "batch_id": "batch_preflight", "intent_id": "preflight", "expected_version": 0,
        "result_version": 1, "events": [{"type": "preflight"}],
    }
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        engine.apply_batch(instance, batch)
    assert live_state(instance) == before


def test_dnd_projection_and_duplicate_batch_preserve_authority():
    from tests.rulesets.test_dnd2024_combat import _instance, _start

    engine, instance = _instance()
    batch = _start(engine, instance)
    authority = deepcopy(ruleset_runtime.state(instance))
    assert instance.combat_state == "active"
    assert instance.combat_active is True
    assert instance.initiative_order == ruleset_runtime.state(instance)["combat"]["initiative"]
    assert instance.initiative_order is not ruleset_runtime.state(instance)["combat"]["initiative"]
    before = deepcopy(live_state(instance))
    result = engine.apply_batch(instance, batch)
    assert result["duplicate"] is True
    assert live_state(instance) == before
    assert ruleset_runtime.state(instance) == authority

    # A replay may still write ruleset defaults, so it fails closed too.
    instance.modules["legacy_combat"]["schema_version"] = 99
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
        engine.apply_batch(instance, batch)
    assert live_state(instance) == before


@pytest.mark.parametrize("future_projection", [False, True])
def test_duplicate_needing_defaults_preserves_repair_semantics(future_projection):
    from tests.rulesets.test_dnd2024_combat import _instance, _start

    engine, instance = _instance()
    batch = _start(engine, instance)
    del ruleset_runtime.state(instance)["combat_history"]
    if future_projection:
        instance.modules["legacy_combat"]["schema_version"] = 99
    before = deepcopy(live_state(instance))
    if future_projection:
        with pytest.raises(ModuleStateError, match="unsupported legacy_combat module schema"):
            engine.apply_batch(instance, batch)
        assert live_state(instance) == before
    else:
        authority = ruleset_runtime.state(instance)
        assert engine.apply_batch(instance, batch)["duplicate"] is True
        assert ruleset_runtime.state(instance) is authority
        assert ruleset_runtime.state(instance)["combat_history"] == []


def test_duplicate_keeps_ruleset_schema_validation():
    from tests.rulesets.test_dnd2024_combat import _instance, _start
    from src.rulesets.dnd2024.combat.primitives import CombatIntentError

    engine, instance = _instance()
    batch = _start(engine, instance)
    ruleset_runtime.state(instance)["state_schema_version"] = 99
    before = deepcopy(live_state(instance))
    with pytest.raises(CombatIntentError, match="unsupported D&D 2024 combat state schema"):
        engine.apply_batch(instance, batch)
    assert live_state(instance) == before
