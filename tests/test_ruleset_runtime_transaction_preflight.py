"""Future ruleset slots reject before the first live transaction mutation."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine import instance_lifecycle, round_recovery, round_snapshots, turn_state
from src.engine.game_instance import GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import ruleset_runtime
from src.engine.modules import round_safety
from tests.test_game_instance_reset_characterization import _make_populated_instance
from tests.test_legacy_combat_transaction_preflight import live_state


def unsupported_instance():
    instance = _make_populated_instance()
    instance.modules["ruleset_runtime"]["schema_version"] = 99
    instance.modules.pop("economy", None)
    return instance


@pytest.mark.parametrize("raw", [None, [], "corrupt", {"schema_version": 1, "state": None}])
def test_preflight_does_not_repair_slot(raw):
    instance = _make_populated_instance()
    instance.modules["ruleset_runtime"] = deepcopy(raw)
    before = deepcopy(live_state(instance))
    ruleset_runtime.require_writable(instance)
    assert live_state(instance) == before


def test_preflight_does_not_materialize_missing_slot():
    instance = _make_populated_instance()
    del instance.modules["ruleset_runtime"]
    before = deepcopy(live_state(instance))
    ruleset_runtime.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("schema", [99, None, "1"])
def test_preflight_rejects_unknown_schema_without_mutation(schema):
    instance = unsupported_instance()
    instance.modules["ruleset_runtime"]["schema_version"] = schema
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        ruleset_runtime.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("direct", [False, True], ids=["public", "locked"])
@pytest.mark.parametrize("operation", ["reset", "abort_round_processing"])
@pytest.mark.asyncio
async def test_transaction_rejection_preserves_all_live_state(operation, direct):
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
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
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        if operation == "aggregate_locked":
            async with instance._lock:
                instance._do_advance_locked()
        elif operation == "owner_locked":
            async with instance._lock:
                turn_state.do_advance_locked(instance)
        else:
            await getattr(instance, operation)()
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["bind", "restore", "reset", "copy", "aggregate_bind", "aggregate_restore"])
def test_owner_and_aggregate_entries_reject_before_mutation(operation):
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        if operation == "aggregate_bind":
            instance.bind_ruleset_runtime({"runtime_id": "dnd", "runtime_version": 1,
                                          "content_version": "1", "state_schema_version": 1})
        elif operation == "aggregate_restore":
            instance.restore_ruleset_transaction({"ruleset_state": {"changed": True}})
        elif operation == "bind":
            ruleset_runtime.bind(instance, {"state_schema_version": 1})
        elif operation == "restore":
            ruleset_runtime.restore_from_transaction(instance, {})
        elif operation == "reset":
            ruleset_runtime.reset(instance, {})
        else:
            ruleset_runtime.copy_binding_for_new_run(instance, _make_populated_instance())
    assert live_state(instance) == before


@pytest.mark.parametrize("direct", [False, True], ids=["aggregate", "owner"])
@pytest.mark.parametrize("operation", ["capture", "restore"])
def test_entity_snapshot_entries_reject_before_npc_or_snapshot_writes(operation, direct):
    instance = unsupported_instance()
    round_safety.replace_entity_snapshot(instance, {"npcs": {"different": {"hp": 1}}, "combat_enemies": []})
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        if direct:
            getattr(round_snapshots, operation + "_round_entity_snapshot")(instance)
        else:
            getattr(instance, operation + "_round_entity_snapshot")()
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["abort", "advance", "empty_entity_restore", "invalid_bind"])
@pytest.mark.asyncio
async def test_existing_readonly_returns_remain_before_preflight(operation):
    instance = unsupported_instance()
    instance.state = GameState.WAITING
    round_safety.round_entity_snapshot(instance).clear()
    before = deepcopy(live_state(instance))
    if operation == "abort":
        assert await instance.abort_round_processing() is False
    elif operation == "advance":
        async with instance._lock:
            assert turn_state.do_advance_locked(instance) is False
    elif operation == "invalid_bind":
        assert instance.bind_ruleset_runtime({}) is False
    else:
        assert instance.restore_round_entity_snapshot() is False
    assert live_state(instance) == before


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
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
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
    retry, fill = AsyncMock(), AsyncMock()
    monkeypatch.setattr(turns, "_retry_external_economy_effects", retry)
    monkeypatch.setattr(turns, "_fill_ai_player_actions", fill)
    instance.players["u1"]["control"] = {
        "mode": "human", "revision": 3, "temporary": False, "resume_mode": "human",
    }
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
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


@pytest.mark.parametrize("engine_name", ["campaign", "combat", "exploration"])
@pytest.mark.parametrize("operation", ["initialize", "batch"])
def test_dnd_engines_reject_before_defaults_or_reduction(engine_name, operation, monkeypatch):
    from src.rulesets.dnd2024.runtime import Dnd2024Runtime

    runtime = Dnd2024Runtime()
    instance = _make_populated_instance()
    ruleset_runtime.replace_state(instance, {})
    instance.adventure_binding = {}
    if engine_name == "campaign":
        engine = runtime._campaign_engine(instance, "en")
    elif engine_name == "combat":
        engine = runtime._combat_engine(instance, locale="en")
    else:
        engine = runtime._exploration_engine("en")
    instance.modules["ruleset_runtime"]["schema_version"] = 99
    before = deepcopy(live_state(instance))

    def forbidden_reducer(*args):
        raise AssertionError("must reject before reduction")

    monkeypatch.setattr(type(engine), "_reduce_event", forbidden_reducer)
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        if operation == "batch":
            engine.apply_batch(instance, {"batch_id": "future", "events": []})
        elif engine_name == "exploration":
            engine._state(instance)
        else:
            engine.initialize_state(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["advancement_state", "advancement_restore", "rest", "runtime_initialize"])
def test_additional_writers_reject_before_mutation(operation):
    from src.rulesets.dnd2024 import advancement_access
    from src.rulesets.dnd2024.runtime import Dnd2024Runtime
    from src.webui.services import ruleset_rest

    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        if operation == "advancement_state":
            advancement_access.view(instance)
        elif operation == "advancement_restore":
            advancement_access.restore(instance, {})
        elif operation == "rest":
            ruleset_rest._set_rest_session(instance, {"status": "collecting"})
        else:
            Dnd2024Runtime().initialize_new_run(instance, preserve_characters=True)
    assert live_state(instance) == before


@pytest.mark.parametrize("future_target", ["source", "candidate"])
@pytest.mark.asyncio
async def test_new_run_rejects_before_creation_or_candidate_configuration(future_target):
    from src.commands.game_lifecycle import GameLifecycle

    source, candidate = _make_populated_instance(), _make_populated_instance()
    target = source if future_target == "source" else candidate
    target.modules["ruleset_runtime"]["schema_version"] = 99
    owner = object.__new__(GameLifecycle)
    owner.create_game = AsyncMock(return_value=candidate)
    before = deepcopy(live_state(target))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        await owner._new_run_candidate(source, preserve_players=True)
    assert live_state(target) == before
    if future_target == "source":
        owner.create_game.assert_not_called()


@pytest.mark.asyncio
async def test_party_rest_rejects_before_session_or_character_mutation():
    from src.rules.rule_system import RuleSystem
    from src.rulesets.builtin import build_default_ruleset_registry
    from src.webui.services.ruleset_rest import LiveRulesetRestDependencies, resolve_live_party

    instance = unsupported_instance()
    instance.combat_active = False
    instance.combat_state = "none"
    instance.players = {
        uid: {"character_name": uid, "character_sheet": {"hp": 5}}
        for uid in ("hero", "ally")
    }
    rule = RuleSystem({"rule_id": "dnd2024_srd", "runtime": {"id": "core:dnd2024", "minimum_version": 1}})
    save = AsyncMock()
    deps = LiveRulesetRestDependencies(
        get_instance=lambda key: instance, parse_game_key=lambda key: instance.game_key,
        save_instance=save, load_rule_for_game=lambda instance: rule,
        ruleset_registry=build_default_ruleset_registry(),
    )
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        await resolve_live_party(deps, "game", "hero", {
            "rest": "long", "confirm_elapsed_time": True,
            "expected_revision": 0, "operation_id": "future-party-rest",
        })
    assert live_state(instance) == before
    save.assert_not_called()


def test_adventure_migration_rejects_before_binding_or_campaign_mutation():
    from src.rulesets.dnd2024.adventure_migrations import apply_unreleased_adventure_binding_migration
    from tests.test_dnd2024_adventure_binding_compat import _bindings

    instance = unsupported_instance()
    old, current = _bindings()
    instance.adventure_binding = old
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        apply_unreleased_adventure_binding_migration(instance, current)
    assert live_state(instance) == before


def test_replayed_combat_batch_rejects_before_default_repair():
    from tests.rulesets.test_dnd2024_combat import _instance, _start

    engine, instance = _instance()
    batch = _start(engine, instance)
    assert engine.apply_batch(instance, batch)["duplicate"] is True
    del ruleset_runtime.state(instance)["combat_history"]
    instance.modules["ruleset_runtime"]["schema_version"] = 99
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match="unsupported ruleset_runtime module schema"):
        engine.apply_batch(instance, batch)
    assert live_state(instance) == before
