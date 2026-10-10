"""Future adventure_runtime slots reject transactions before any live mutation."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.engine import round_recovery, round_snapshots, turn_state
from src.engine.game_instance import GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import adventure_runtime_state, round_safety, table_settings
from tests.test_game_instance_reset_characterization import _make_populated_instance
from test_golden_e2e_54pr import golden  # noqa: F401  (fixture re-export)

MATCH = "unsupported adventure_runtime module schema"


def live_state(instance):
    return {key: value for key, value in vars(instance).items() if not isinstance(value, asyncio.Lock)}


def make_unsupported(instance):
    instance.modules["adventure_runtime"] = {
        "schema_version": 99, "progress": {"active_nodes": ["gate"]}, "play_mode": "adventure",
    }
    # Reject before materializing unrelated slots, not only before gameplay writes.
    instance.modules.pop("economy", None)
    return instance


def unsupported_instance():
    return make_unsupported(_make_populated_instance())


@pytest.mark.parametrize("raw", [None, [], "corrupt", {"schema_version": 1, "progress": None}])
def test_preflight_does_not_repair_slot(raw):
    instance = _make_populated_instance()
    instance.modules["adventure_runtime"] = deepcopy(raw)
    before = deepcopy(live_state(instance))
    adventure_runtime_state.require_writable(instance)
    assert live_state(instance) == before


def test_preflight_does_not_materialize_missing_slot():
    instance = _make_populated_instance()
    del instance.modules["adventure_runtime"]
    before = deepcopy(live_state(instance))
    adventure_runtime_state.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("schema", [99, None, "1"])
def test_preflight_rejects_unknown_schema_without_mutation(schema):
    instance = unsupported_instance()
    instance.modules["adventure_runtime"]["schema_version"] = schema
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        adventure_runtime_state.require_writable(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation,value", [("replace_progress", {}), ("replace_play_mode", "free")])
def test_owner_entries_reject_before_mutation(operation, value):
    instance = unsupported_instance()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        getattr(adventure_runtime_state, operation)(instance, value)
    assert live_state(instance) == before


@pytest.mark.parametrize("direct", [False, True], ids=["public", "locked"])
@pytest.mark.parametrize("operation", ["rollback_last_round", "abort_round_processing", "finish_judgment"])
@pytest.mark.asyncio
async def test_round_history_transactions_reject_before_mutation(operation, direct):
    instance = unsupported_instance()
    args = ("new narration",) if operation == "finish_judgment" else ()
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        if direct:
            async with instance._lock:
                getattr(round_recovery, operation + "_locked")(instance, *args)
        else:
            await getattr(instance, operation)(*args)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["advance_round", "try_advance", "aggregate_locked", "owner_locked"])
@pytest.mark.asyncio
async def test_advance_rejects_before_judgment_snapshots(operation):
    instance = unsupported_instance()
    instance.state = GameState.ACTIVE_ACTION
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        if operation == "aggregate_locked":
            async with instance._lock:
                instance._do_advance_locked()
        elif operation == "owner_locked":
            async with instance._lock:
                turn_state.do_advance_locked(instance)
        else:
            await getattr(instance, operation)()
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["capture", "restore"])
def test_entity_snapshot_capture_and_restore_reject_without_mutation(operation):
    instance = unsupported_instance()
    round_safety.replace_entity_snapshot(instance, {"npcs": {}, "adventure_progress": {"active_nodes": ["x"]}})
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        if operation == "capture":
            round_snapshots.capture_round_entity_snapshot(instance)
        else:
            round_snapshots.restore_round_entity_snapshot(instance)
    assert live_state(instance) == before


@pytest.mark.parametrize("operation", ["process_round", "process_round_impl"])
@pytest.mark.asyncio
async def test_round_processor_rejects_before_generation(tmp_path, operation):
    from src.commands.round_processor import RoundProcessor

    instance = unsupported_instance()
    registry = GameRegistry(tmp_path)
    registry.register(instance)
    processor = object.__new__(RoundProcessor)
    processor.registry = registry
    processor.llm_client = SimpleNamespace(call=AsyncMock())
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        await getattr(processor, operation)(instance)
    assert live_state(instance) == before
    processor.llm_client.call.assert_not_called()


@pytest.mark.asyncio
async def test_swipe_rejects_before_staging_or_generation():
    from src.commands.swipe_generator import SwipeGenerator

    instance = unsupported_instance()
    generator = object.__new__(SwipeGenerator)
    generator._generate_locked = AsyncMock(side_effect=AssertionError("must not generate"))
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        await generator.generate(instance, 1)
    assert live_state(instance) == before
    generator._generate_locked.assert_not_called()


@pytest.mark.parametrize("operation", ["submit", "advance", "progression"])
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
    with pytest.raises(ModuleStateError, match=MATCH):
        if operation == "submit":
            await turns.submit_action(dependencies, "game", "u1", "Look")
        elif operation == "advance":
            await turns.advance_round(dependencies, "game", "u1", force=True)
        else:
            await turns._advance_progression(dependencies, instance, game_key="game")
    assert live_state(instance) == before
    retry.assert_not_called()
    fill.assert_not_called()


# ---- Adventure application runtime ---------------------------------------------


def _adventure_fixture(tmp_path):
    from tests.test_adventure_v2_runtime import _deps, _graph, _install, _instance

    resolver = _install(tmp_path, _graph())
    return _deps(resolver), _instance(resolver)


def test_initialize_rejects_before_world_seed(tmp_path):
    from src.webui.services import adventure_runtime

    deps, instance = _adventure_fixture(tmp_path)
    seed = Mock(side_effect=AssertionError("must not materialize the world seed"))
    deps = replace(deps, materialize_world_seed=seed)
    make_unsupported(instance)
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        adventure_runtime.initialize_adventure_run(deps, instance)
    assert live_state(instance) == before
    seed.assert_not_called()


@pytest.mark.parametrize("operation", ["complete", "advance"])
def test_node_completion_and_world_advance_reject_before_mutation(tmp_path, operation):
    from src.webui.services import adventure_runtime

    deps, instance = _adventure_fixture(tmp_path)
    adventure_runtime.initialize_adventure_run(deps, instance)
    make_unsupported(instance)
    before = deepcopy(live_state(instance))
    with pytest.raises(ModuleStateError, match=MATCH):
        if operation == "complete":
            adventure_runtime.complete_adventure_node(deps, instance, "gate")
        else:
            adventure_runtime.advance_adventure_world(deps, instance)
    assert live_state(instance) == before


@pytest.mark.asyncio
async def test_seed_creation_rejects_unsupported_source_slot_before_registering(golden):
    from test_golden_e2e_54pr import _created_golden, _dnd_characters

    created = await _created_golden(golden)
    source = golden.instance(created["game_key"])
    make_unsupported(source)
    games_before = [game["game_key"] for game in golden.api.list_games()["games"]]
    before = deepcopy(source.to_dict())
    with pytest.raises(ModuleStateError, match=MATCH):
        await golden.api.create_from_seed(
            table_settings.seed_code(source), players=_dnd_characters(1), gm_uid="seed_gm", language="zh-CN",
        )
    assert [game["game_key"] for game in golden.api.list_games()["games"]] == games_before
    assert source.to_dict() == before
