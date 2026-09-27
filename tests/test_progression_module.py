"""Progression persistence and refusal-before-mutation integration contracts."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import fields, replace
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import zipfile

import pytest

from src.engine import progression
from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import progression_state
from webapi_harness import web_api  # noqa: F401  # pytest fixture
from tests.test_round_failure_recovery import _new_game
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v20_to_v21,
    migrate_game_state_payload,
    rebind_imported_game_state_payload,
)


UNKNOWN_SLOTS = [
    {"schema_version": 1, "mode": "phase_table", "round": 7, "extra": {"phase": [1, 2]}},
    {"schema_version": 1, "mode": "phase_table", "round": {"future": [3]}, "extra": [4]},
    {"schema_version": 9, "mode": "narrative_round", "round": 7, "extra": {"future": [5]}},
]


def instance_with_state(slot=None):
    instance = GameInstance(game_key=("web", "progression", "bot"), gm_uid="gm")
    instance.solo_mode = True
    instance.round_number = 7
    instance.state = GameState.ACTIVE_ACTION
    instance.players = {"gm": {"character_name": "Hero", "character_sheet": {"hp": 12, "gold": 20}}}
    instance.npcs = {"guide": {"hp": 9}}
    instance.action_queue = [{"user_id": "gm", "text": "Open the door"}]
    instance.pending_actions = [{"user_id": "gm", "text": "Follow"}]
    instance.ready_players = {"gm"}
    instance.log = [{"round": 6, "gm_response": "Before", "pre_state_snapshot": {"gm": {"hp": 15}}}]
    instance.round_start_snapshot = {"gm": {"hp": 15}}
    instance.round_entity_snapshot = {"npcs": {"guide": {"hp": 10}}}
    instance.combat_extension_round_snapshots["7"] = {"opaque": [1]}
    instance.adventure_progress = {"active_nodes": ["gate"]}
    instance.ruleset_state = {"version": 4}
    instance.event_ledger = [{"id": "old"}]
    instance.game_time = "Third Age, dusk"
    if slot is not None:
        instance.modules["progression"] = deepcopy(slot)
    return instance


def frozen(instance):
    # to_dict exposes live module children: always freeze the entire aggregate.
    return deepcopy(instance.to_dict())


def transaction_snapshot(instance, **extra):
    return deepcopy({
        key: getattr(instance, key) for key in (
            "ruleset_state", "event_ledger", "players", "combat_state", "combat_active",
            "initiative_order", "initiative_current", "scene", "last_activity", "log",
        )
    } | extra)


def test_live_property_and_roundtrip_have_one_storage_owner():
    instance = instance_with_state()
    assert "round_number" not in {item.name for item in fields(instance)}
    assert "round_number" not in instance.__dict__
    assert len(fields(instance)) <= 90  # R5-b removed round_number; later steps may shrink further
    instance.round_number = 4
    assert instance.modules["progression"] == {"schema_version": 1, "mode": "narrative_round", "round": 4}
    instance.modules["progression"]["round"] = 8
    assert instance.round_number == progression.current_era(instance) == 8
    encoded = frozen(instance)
    assert "round_number" not in encoded
    restored = GameInstance.from_dict(encoded)
    assert restored.round_number == 8
    assert restored.game_time == "Third Age, dusk"
    instance.replace_persisted_state_from(restored)
    assert instance.round_number == 8


def _game_time(payload):
    """Where game_time lives after the latest migration (top level before R7-d)."""
    notes = (payload.get("modules") or {}).get("narrative_notes")
    if isinstance(notes, dict) and "game_time" in notes:
        return notes["game_time"]
    return payload.get("game_time")


@pytest.mark.parametrize("legacy, expected", [(7, 7), (0, 0), (None, 0), (-3, 0), (True, 0), ("5", 0), (2.5, 0), ({}, 0)])
def test_v20_migration_is_deepcopied_sequential_and_idempotent(legacy, expected):
    original = {"instance_schema_version": 20, "round_number": legacy, "game_time": "dusk", "log": [{"round": 12}], "other": {"values": [1]}}
    before = deepcopy(original)
    result = migrate_game_state_payload(original)
    assert original == before
    assert result["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION >= 21
    assert result["modules"]["progression"]["round"] == expected
    assert "round_number" not in result
    assert result["log"] == original["log"] and _game_time(result) == "dusk"
    assert migrate_game_state_payload(result) == result
    step = _migrate_v20_to_v21(deepcopy(original))
    assert _migrate_v20_to_v21(deepcopy(step)) == step
    result["other"]["values"].append(2)
    assert original == before


@pytest.mark.parametrize("version", range(1, 21))
def test_historical_chain_preserves_round_and_history(version):
    raw = {"instance_schema_version": version, "round_number": 5, "game_time": "legacy time", "log": [{"round": 2}]}
    before = deepcopy(raw)
    migrated = migrate_game_state_payload(raw)
    assert raw == before
    assert migrated["modules"]["progression"]["round"] == 5
    assert migrated["log"] == [{"round": 2}]
    assert _game_time(migrated) == "legacy time"
    assert migrate_game_state_payload(migrated) == migrated


@pytest.mark.parametrize("slot", [*UNKNOWN_SLOTS, {}, {"schema_version": 1, "mode": "narrative_round", "round": 3}])
def test_existing_slots_win_even_when_empty_or_unknown(slot):
    raw = {"instance_schema_version": 20, "round_number": 99, "modules": {"progression": slot}}
    before = deepcopy(raw)
    result = migrate_game_state_payload(raw)
    assert result["modules"]["progression"] == slot
    assert "round_number" not in result
    assert raw == before


def test_missing_and_malformed_current_slot_materialize_fresh():
    for raw in (None, 3, "bad", []):
        instance = GameInstance(game_key=("test", "fresh", "bot"), modules={"progression": raw})
        assert instance.modules["progression"] == progression_state.fresh()
        assert instance.round_number == 0
    instance = GameInstance(game_key=("test", "absent", "bot"), modules={})
    assert instance.modules["progression"] == progression_state.fresh()
    assert instance.to_dict()["modules"]["progression"] == progression_state.fresh()


def test_missing_counter_and_future_instance_version():
    assert migrate_game_state_payload({"instance_schema_version": 20})["modules"]["progression"]["round"] == 0
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})


@pytest.mark.parametrize("raw", [None, [], "bad", 2])
def test_non_dict_slot_defaults(raw):
    assert progression_state.ensure(raw) == progression_state.fresh()


@pytest.mark.parametrize("value", [None, -3, True, "5", 2.5, {}, []])
@pytest.mark.parametrize("mode", [None, "narrative_round"])
def test_known_mode_repairs_malformed_round(value, mode):
    slot = {"schema_version": 1, "mode": mode, "round": value, "extra": [1]}
    result = progression_state.ensure(slot)
    assert result == {"schema_version": 1, "mode": "narrative_round", "round": 0, "extra": [1]}
    assert progression_state.ensure(result) is result


@pytest.mark.parametrize("mode", ["phase_table", "", False, 0, [], {}])
def test_unknown_mode_never_rewrites_even_malformed_round(mode):
    slot = {"schema_version": 1, "mode": mode, "round": {"future": [1]}, "extra": [2]}
    before = deepcopy(slot)
    assert progression_state.ensure(slot) is slot
    assert slot == before
    instance = GameInstance(game_key=("test", "unknown", "bot"), modules={"progression": slot})
    assert instance.modules["progression"] == before
    with pytest.raises(ModuleStateError):
        instance.round_number = 2
    assert instance.modules["progression"] == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
def test_unknown_slot_encode_decode_and_rebind_preserve_opaque_data(slot):
    instance = instance_with_state(slot)
    before = frozen(instance)
    restored = GameInstance.from_dict(before)
    assert restored.modules["progression"] == slot
    assert restored.to_dict()["modules"]["progression"] == slot
    rebound = rebind_imported_game_state_payload(before, game_key=("web", "imported", "bot"), run_id="new-run")
    assert rebound["modules"]["progression"] == slot
    assert _game_time(rebound) == "Third Age, dusk"
    assert instance.to_dict() == before
    if slot["schema_version"] == 1 and isinstance(slot["round"], int):
        assert restored.round_number == progression.current_round(restored) == 7
    else:
        with pytest.raises(ModuleStateError):
            _ = restored.round_number
    assert restored.modules["progression"] == slot


WRITERS = [
    lambda i: progression.open_next_round(i),
    lambda i: progression.rewind_after_rollback(i, 3),
    lambda i: progression.rewind_for_replay(i, 3),
    lambda i: progression.advance_for_public_timeline(i, i.log),
    lambda i: progression.restore_from_snapshot(i, 3),
    lambda i: progression.reset(i),
    lambda i: setattr(i, "round_number", 3),
]


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("writer", WRITERS)
def test_six_writers_and_property_refuse_without_any_mutation(slot, writer):
    instance = instance_with_state(slot)
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        writer(instance)
    assert instance.to_dict() == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("operation", ["start_round", "finish_judgment", "rollback_last_round", "reset", "try_advance", "advance_round", "activate", "resume", "add_action", "begin_round_processing", "restore_ruleset_transaction"])
@pytest.mark.asyncio
async def test_aggregate_entry_preflight_protects_entire_snapshot(slot, operation):
    instance = instance_with_state(slot)
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        if operation == "begin_round_processing":
            instance.begin_round_processing()
        elif operation == "restore_ruleset_transaction":
            snapshot = transaction_snapshot(instance, round_number=2)
            snapshot["ruleset_state"] = {"replaced": True}
            snapshot["players"] = {}
            instance.restore_ruleset_transaction(snapshot)
        else:
            args = ("new narration",) if operation == "finish_judgment" else (("gm", "Look") if operation == "add_action" else ())
            await getattr(instance, operation)(*args)
    assert instance.to_dict() == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.asyncio
async def test_abort_unknown_progression_precedes_player_restore(slot):
    instance = instance_with_state(slot)
    instance.state = GameState.ACTIVE_JUDGMENT
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        await instance.abort_round_processing()
    assert frozen(instance) == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.asyncio
async def test_abort_outside_judgment_is_noop_for_unknown_progression(slot):
    instance = instance_with_state(slot)
    before = frozen(instance)
    assert await instance.abort_round_processing() is False
    assert frozen(instance) == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("entry", ["manual", "timeout", "force_decline"])
@pytest.mark.asyncio
async def test_saved_pending_luck_rejects_before_mutation(web_api, monkeypatch, slot, entry):
    from src.webui.services import turns

    drain = AsyncMock(side_effect=AssertionError("must not drain external effects"))
    monkeypatch.setattr(turns, "_retry_external_economy_effects", drain)
    api, _, registry, _, _ = web_api
    key, instance, uid = await _new_game(api, registry)
    sheet = instance.players[uid]["character_sheet"]
    sheet["luck"] = 50
    sheet.setdefault("resources", {})["luck"] = {"current": 50, "max": 50}
    await instance.add_action(uid, "Look around")
    await instance.try_advance()
    instance.round_checks_prepared = True
    instance.last_checks = [{
        "check_id": "progression-luck", "actor_uid": uid, "dice": "d100",
        "roll": 55, "threshold": 50, "verdict": "失败",
        "luck_decision": "pending", "luck_spend_available": True,
    }]
    instance.modules["progression"] = deepcopy(slot)
    await registry.save(instance)
    instance = await registry.load(instance.game_key)
    before = frozen(instance)
    save_path = registry.save_package_state_path(instance.game_key)
    disk_before = save_path.read_bytes()

    if entry == "manual":
        with pytest.raises(ModuleStateError):
            await api.resolve_luck_and_continue(key, "progression-luck", uid, True)
    elif entry == "timeout":
        timer = Mock()
        instance._luck_timers["progression-luck"] = timer
        await api._handler._round_processor._luck_timeout(instance.game_key, "progression-luck", 0)
        assert instance._luck_timers["progression-luck"] is timer
        timer.cancel.assert_not_called()
    else:
        with pytest.raises(ModuleStateError):
            await api.decline_pending_luck(key)

    drain.assert_not_called()
    assert frozen(instance) == before
    assert save_path.read_bytes() == disk_before
    assert frozen(await registry.load(instance.game_key)) == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.asyncio
async def test_luck_noops_keep_existing_result_for_unknown_progression(slot):
    instance = instance_with_state(slot)
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.round_checks_prepared = True
    instance.last_checks = [{"check_id": "done", "actor_uid": "gm", "luck_decision": "declined"}]
    before = frozen(instance)
    assert (await instance.resolve_luck_decision("missing", "gm", False))["code"] == "CHECK_NOT_FOUND"
    assert (await instance.resolve_luck_decision("done", "gm", False))["already_resolved"] is True
    assert (await instance.system_decline_luck("missing"))["code"] == "CHECK_NOT_FOUND"
    assert (await instance.system_decline_luck("done"))["code"] == "LUCK_ALREADY_RESOLVED"
    assert await instance.decline_pending_luck() == []
    assert frozen(instance) == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.asyncio
async def test_no_log_rollback_and_stale_start_remain_noops(slot):
    instance = instance_with_state(slot)
    instance.log = []
    before = frozen(instance)
    assert await instance.rollback_last_round() is None
    await instance.start_round(expected_run_id="stale")
    assert instance.to_dict() == before


@pytest.mark.parametrize("round_number", ["invalid", None, {}])
def test_transaction_counter_conversion_precedes_other_assignments(round_number):
    instance = instance_with_state()
    before = frozen(instance)
    snapshot = transaction_snapshot(instance, round_number=round_number)
    snapshot["ruleset_state"] = {"replacement": True}
    with pytest.raises((TypeError, ValueError)):
        instance.restore_ruleset_transaction(snapshot)
    assert instance.to_dict() == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
def test_bounded_director_restore_without_round_remains_supported(slot):
    instance = instance_with_state(slot)
    snapshot = transaction_snapshot(instance)
    instance.ruleset_state = {"changed": True}
    instance.restore_ruleset_transaction(snapshot)
    assert instance.ruleset_state == snapshot["ruleset_state"]
    assert instance.modules["progression"] == slot


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.asyncio
async def test_replay_rejects_before_staged_mutation_or_llm(slot, staged):
    from src.commands.swipe_generator import SwipeGenerator

    instance = instance_with_state(slot)
    retrieve = AsyncMock(side_effect=AssertionError("must not retrieve or call LLM"))
    generator = SwipeGenerator(
        llm_client=None, matcher=None, prompt=None, state_applier=None,
        load_world_template=Mock(), ensure_matcher_for_world=Mock(),
        narrative_max_tokens=100, lore_retriever=SimpleNamespace(retrieve=retrieve),
    )
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        if staged:
            await generator._generate_locked(instance, 6)
        else:
            await generator.generate(instance, 6)
    assert instance.to_dict() == before
    retrieve.assert_not_called()
    assert not instance._rewrite_in_progress
    assert not instance._process_lock.locked()
    assert await generator.generate(instance, 100) is None
    instance.log[0]["swipes"] = ["old"] * 5
    before = frozen(instance)
    assert await generator.generate(instance, 6) is None
    assert instance.to_dict() == before


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("operation", ["start_game", "resume_game", "reset_game", "restart_game"])
@pytest.mark.asyncio
async def test_command_lifecycle_rejects_before_publish_save_or_llm(tmp_path, slot, operation):
    from src.commands.game_lifecycle import GameLifecycle

    instance = instance_with_state(slot)
    instance.state = GameState.PAUSED
    registry = GameRegistry(tmp_path)
    registry.register(instance)
    # The guard must run before any dependency is needed; use actual entry methods.
    lifecycle = object.__new__(GameLifecycle)
    lifecycle.registry = registry
    lifecycle._run_transition_locks = {}
    lifecycle.llm_client = SimpleNamespace(call=AsyncMock())
    lifecycle._new_run_candidate = AsyncMock(side_effect=AssertionError("must not build candidate"))
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        await getattr(lifecycle, operation)(instance)
    assert instance.to_dict() == before
    assert registry.get(instance.game_key) is instance
    lifecycle.llm_client.call.assert_not_called()
    lifecycle._new_run_candidate.assert_not_called()


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("operation", ["prepare_round_checks", "prepare_round_checks_ai", "process_round", "process_round_impl"])
@pytest.mark.asyncio
async def test_processor_preflight_precedes_checks_resources_or_generation(tmp_path, slot, operation):
    from src.commands.round_processor import RoundProcessor

    instance = instance_with_state(slot)
    instance.state = GameState.ACTIVE_JUDGMENT
    registry = GameRegistry(tmp_path)
    registry.register(instance)
    processor = object.__new__(RoundProcessor)
    processor.registry = registry
    processor.llm_client = SimpleNamespace(call=AsyncMock())
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        if operation == "prepare_round_checks":
            processor.prepare_round_checks(instance)
        else:
            await getattr(processor, operation)(instance)
    assert instance.to_dict() == before
    processor.llm_client.call.assert_not_called()


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.asyncio
async def test_registry_load_and_import_keep_opaque_future_slots(tmp_path, slot):
    registry = GameRegistry(tmp_path / "saves")
    instance = instance_with_state(slot)
    await registry.save(instance)
    restored = await registry.load(instance.game_key)
    assert restored.modules["progression"] == slot
    state_path = registry.save_package_state_path(instance.game_key)
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("state.json", state_path.read_bytes())
    result = await registry.import_save_zip(payload.getvalue())
    assert result["ok"]
    imported = registry.get(tuple(result["game_key"]))
    assert imported.modules["progression"] == slot
    assert imported.game_time == instance.game_time


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("operation", ["intent", "resume"])
@pytest.mark.asyncio
async def test_real_ruleset_transactions_reject_before_binding_reducer_or_save(tmp_path, monkeypatch, slot, operation):
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
    dependencies = _M5Api(registry, runtime)._gameplay_dependencies
    save = AsyncMock()
    dependencies = replace(dependencies, save_instance=save)
    if operation == "resume":
        instance.ruleset_state["combat"] = {"status": "active"}
    instance.modules["progression"] = deepcopy(slot)
    before = frozen(instance)
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
    assert instance.to_dict() == before
    binding.assert_not_called()
    reducer.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
@pytest.mark.parametrize("operation", ["submit", "advance", "resume"])
@pytest.mark.asyncio
async def test_turn_services_reject_before_outbox_ai_or_resource_callbacks(tmp_path, monkeypatch, slot, operation):
    from src.webui.services import turns

    instance = instance_with_state(slot)
    registry = GameRegistry(tmp_path)
    registry.register(instance)
    dependencies = SimpleNamespace(
        get_instance=registry.get, parse_game_key=lambda key: tuple(key.split("|")),
        load_rule_for_game=lambda instance: None,
        resume_authoritative_combat=None,
    )
    retry = AsyncMock(side_effect=AssertionError("must not drain outbox"))
    fill = AsyncMock(side_effect=AssertionError("must not call AI"))
    monkeypatch.setattr(turns, "_retry_external_economy_effects", retry)
    monkeypatch.setattr(turns, "_fill_ai_player_actions", fill)
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        if operation == "submit":
            await turns.submit_action(dependencies, "web|progression|bot", "gm", "Look")
        elif operation == "advance":
            await turns.advance_round(dependencies, "web|progression|bot", "gm", force=True)
        else:
            await turns.resume_after_control_change(dependencies, "web|progression|bot")
    assert instance.to_dict() == before
    retry.assert_not_called()
    fill.assert_not_called()


@pytest.mark.parametrize("slot", UNKNOWN_SLOTS)
def test_public_timeline_preflight_does_not_call_projection(slot):
    from src.rulesets.automation import append_public_timeline_entry

    instance = instance_with_state(slot)
    runtime = SimpleNamespace(public_timeline_projection=Mock())
    before = frozen(instance)
    with pytest.raises(ModuleStateError):
        append_public_timeline_entry(runtime, instance, {})
    assert instance.to_dict() == before
    runtime.public_timeline_projection.assert_not_called()


@pytest.mark.asyncio
async def test_current_package_export_import_and_unknown_mode_sse(tmp_path):
    from src.webui.services.game_packages import GamePackageDependencies, GamePackageService
    from src.webui.routes.sse import _event_cursor, _parse_event_cursor, _play_action_signature, _play_public_signature

    instance = instance_with_state(UNKNOWN_SLOTS[0])
    registry = GameRegistry(tmp_path)
    registry.register(instance)
    await registry.save(instance)
    service = GamePackageService(GamePackageDependencies(
        parse_game_key=lambda key: tuple(key.split("|")), get_instance=registry.get,
        state_path_for=registry.save_package_state_path, import_save_zip=registry.import_save_zip,
        resolve_scene_image_file=lambda _: None, resolve_map_background_file=lambda _: None,
        save_scene_image_upload=Mock(), save_map_background_upload=Mock(),
    ))
    exported = service.export_game_package("web|progression|bot")
    assert exported["ok"]
    with zipfile.ZipFile(BytesIO(exported["payload"])) as archive:
        payload = json.loads(archive.read("state.json"))
    assert "round_number" not in payload
    assert payload["modules"]["progression"] == UNKNOWN_SLOTS[0]
    imported = await service.import_game_package(exported["payload"])
    assert imported["ok"]
    loaded = registry.get(tuple(imported["game_key"].split("|")))
    before = frozen(loaded)
    cursor = _event_cursor(loaded.round_number, 0, _play_action_signature(loaded), _play_public_signature(loaded, "gm"))
    assert _parse_event_cursor(cursor)[0] == 7
    assert loaded.to_dict() == before
    assert loaded.game_time == "Third Age, dusk"


def test_e2e_seed_script_constructs_and_encodes_all_fixtures(tmp_path):
    from scripts.prepare_e2e_data import prepare_e2e_data

    prepare_e2e_data(tmp_path)
    saves = list((tmp_path / "saves").rglob("state.json"))
    assert len(saves) == 4
    for path in saves:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "round_number" not in payload
        assert payload["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
        restored = GameInstance.from_dict(payload)
        assert restored.round_number == payload["modules"]["progression"]["round"]


def test_all_python_constructor_calls_use_postconstruction_round_assignment():
    root = Path(__file__).resolve().parents[1]
    violations = []
    for directory in ("src", "tests", "scripts"):
        for path in (root / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                if name == "GameInstance" and any(key.arg == "round_number" for key in node.keywords):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert violations == []
