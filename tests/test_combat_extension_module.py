"""R3 persistence, live identity, and combat recovery contracts."""

from copy import deepcopy
from dataclasses import fields
import json
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import combat_extension_state as combat
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v19_to_v20,
    migrate_game_state_payload,
)


ATTRS = [
    ("combat_extension", "current"),
    ("combat_extension_round_snapshots", "round_snapshots"),
]


def make_instance():
    return GameInstance(game_key=("web", "r3", "bot"))


def populate(instance):
    instance.combat_extension = {"schema_version": 1, "opaque": {"values": [1]}}
    instance.combat_extension_round_snapshots = {
        "2": {"schema_version": 1, "combat_extension": {"opaque": [2]}, "entity_fields": {}},
    }


def malformed_snapshot():
    return {
        "schema_version": 1,
        "combat_extension": {"opaque": "must not commit"},
        "entity_fields": {
            "player:p": {"values": {"hp": 1}, "missing": []},
            "npc:broken": "invalid",
        },
    }


def test_fresh_slots_and_instances_have_independent_mutable_children():
    first, second = make_instance(), make_instance()
    slots = [combat.fresh(), combat.fresh(), first.modules[combat.MODULE_NAME], second.modules[combat.MODULE_NAME]]
    children = [slot[key] for slot in slots for key in ("current", "round_snapshots")]
    assert all(slot == {"schema_version": 1, "current": {}, "round_snapshots": {}} for slot in slots)
    assert len({id(child) for child in children}) == len(children)
    assert not {name for name, _ in ATTRS}.intersection(field.name for field in fields(GameInstance))


@pytest.mark.parametrize(("attribute", "child"), ATTRS)
@pytest.mark.parametrize("value", [{}, {"opaque": {"items": [1]}}])
def test_properties_retain_assigned_dict_identity_and_detach_old_value(attribute, child, value):
    instance = make_instance()
    slot = instance.modules[combat.MODULE_NAME]
    slot["extra"] = {"keep": True}
    sibling_key = "round_snapshots" if child == "current" else "current"
    sibling = slot[sibling_key]
    old = getattr(instance, attribute)
    old["old"] = True
    assigned = deepcopy(value)
    setattr(instance, attribute, assigned)
    assert getattr(instance, attribute) is assigned is slot[child]
    assert old == {"old": True}
    assigned.setdefault("opaque", {"items": []})["items"].append(2)
    assert getattr(instance, attribute)["opaque"]["items"][-1] == 2
    assert slot[sibling_key] is sibling
    assert slot["extra"] == {"keep": True}


@pytest.mark.parametrize(("attribute", "child"), ATTRS)
def test_dict_subclass_assignment_keeps_identity(attribute, child):
    class Payload(dict):
        pass

    instance = make_instance()
    assigned = Payload()
    setattr(instance, attribute, assigned)
    assert getattr(instance, attribute) is assigned
    assert instance.modules[combat.MODULE_NAME][child] is assigned


@pytest.mark.parametrize(("attribute", "child"), ATTRS)
@pytest.mark.parametrize("value", [None, [], "bad", 0])
def test_non_dict_assignment_repairs_only_the_assigned_container(attribute, child, value):
    instance = make_instance()
    populate(instance)
    slot = instance.modules[combat.MODULE_NAME]
    sibling_key = "round_snapshots" if child == "current" else "current"
    sibling = slot[sibling_key]
    setattr(instance, attribute, value)
    assert getattr(instance, attribute) == {}
    assert slot[sibling_key] is sibling


def test_legacy_migration_preserves_data_input_and_both_idempotency_boundaries():
    payload = {
        "instance_schema_version": 19,
        "combat_extension": {"schema_version": 99, "opaque": {"x": [1]}},
        "combat_extension_round_snapshots": {2: {"combat_extension": {"old": True}}, "bad": [], "3": {}},
        "modules": {"other": {"schema_version": 55, "opaque": [2]}},
        "log": [{"pre_combat_extension_snapshot": {"combat_extension": {"keep": True}}}],
    }
    original = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == original
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["modules"][combat.MODULE_NAME] == {
        "schema_version": 1,
        "current": original["combat_extension"],
        "round_snapshots": {"2": {"combat_extension": {"old": True}}, "3": {}},
    }
    assert migrated["modules"]["other"] == original["modules"]["other"]
    assert migrated["log"] == original["log"]
    assert all(name not in migrated for name, _ in ATTRS)
    step = _migrate_v19_to_v20(deepcopy(payload))
    assert _migrate_v19_to_v20(deepcopy(step)) == step
    assert migrate_game_state_payload(migrated) == migrated


@pytest.mark.parametrize("modules", [None, [], {}, {"combat_extension": "bad"}])
@pytest.mark.parametrize("legacy", [None, [], "bad"])
def test_legacy_corrupt_containers_load_empty(modules, legacy):
    payload = dict(make_instance().to_dict())
    payload.update(instance_schema_version=19, modules=modules,
                   combat_extension=legacy, combat_extension_round_snapshots=legacy)
    recovered = GameInstance.from_dict(payload)
    assert recovered.combat_extension == {}
    assert recovered.combat_extension_round_snapshots == {}


@pytest.mark.parametrize("slot", [
    {},
    {"schema_version": 99, "current": "opaque", "round_snapshots": [3]},
    {"schema_version": 1, "current": {}, "round_snapshots": {4: "preserved"}, "extra": [1]},
])
def test_existing_slots_win_over_legacy_including_empty_and_unknown(slot):
    payload = {"instance_schema_version": 19, "modules": {combat.MODULE_NAME: slot},
               "combat_extension": {"stale": True}, "combat_extension_round_snapshots": {"1": {}}}
    migrated = migrate_game_state_payload(payload)
    assert migrated["modules"][combat.MODULE_NAME] == slot
    assert all(name not in migrated for name, _ in ATTRS)


@pytest.mark.parametrize("version", [16, 17, 18, 19])
def test_full_prior_migration_chain_loads_combat(version):
    payload = dict(make_instance().to_dict())
    payload.update(instance_schema_version=version, modules={},
                   combat_extension={"opaque": [1]}, combat_extension_round_snapshots={2: {"opaque": [3]}})
    restored = GameInstance.from_dict(payload)
    assert restored.instance_schema_version == CURRENT_INSTANCE_SCHEMA_VERSION
    assert restored.combat_extension == {"opaque": [1]}
    assert restored.combat_extension_round_snapshots == {"2": {"opaque": [3]}}
    assert restored.away_control_policy == "pause"
    assert restored.economy["run_id"] == restored.run_id
    assert restored.lorebook_timed_state == {}


def test_future_instance_schema_rejected():
    payload = dict(make_instance().to_dict())
    payload["instance_schema_version"] = CURRENT_INSTANCE_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        GameInstance.from_dict(payload)


@pytest.mark.parametrize("raw", [None, [], {"schema_version": 1},
                                     {"schema_version": 1, "current": [], "round_snapshots": "bad"}])
def test_supported_container_repair_is_idempotent(raw):
    slot = combat.ensure(raw)
    assert slot == {"schema_version": 1, "current": {}, "round_snapshots": {}}
    current, snapshots = slot["current"], slot["round_snapshots"]
    assert combat.ensure(slot) is slot
    assert slot["current"] is current
    assert slot["round_snapshots"] is snapshots


def test_current_schema_is_opaque_and_never_uses_stale_legacy_fallback():
    instance = make_instance()
    slot = instance.modules[combat.MODULE_NAME]
    slot.update(current={"schema_version": 99, "pools": "opaque"},
                round_snapshots={3: "opaque", "4": {"schema_version": True}}, extra=[1])
    current, snapshots = slot["current"], slot["round_snapshots"]
    assert combat.ensure(slot) is slot
    assert slot["current"] is current
    assert slot["round_snapshots"] is snapshots
    payload = dict(instance.to_dict())
    payload.update(combat_extension={"stale": True}, combat_extension_round_snapshots={"old": {}})
    restored = GameInstance.from_dict(payload)
    assert restored.modules[combat.MODULE_NAME] == slot
    payload["modules"].pop(combat.MODULE_NAME)
    restored = GameInstance.from_dict(payload)
    assert restored.combat_extension == restored.combat_extension_round_snapshots == {}


@pytest.mark.parametrize("version", [99, None, "1"])
def test_unknown_outer_schema_roundtrips_and_rejects_all_access_without_mutation(version):
    raw = {"schema_version": version, "current": "untouched", "round_snapshots": [1], "extra": {"x": 1}}
    assert combat.ensure(raw) is raw
    instance = GameInstance(game_key=("web", "unknown", "bot"), modules={combat.MODULE_NAME: raw})
    encoded_slot = json.dumps(raw, sort_keys=True)
    restored = GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))
    assert json.dumps(restored.to_dict()["modules"][combat.MODULE_NAME], sort_keys=True) == encoded_slot
    for attribute, _ in ATTRS:
        with pytest.raises(ModuleStateError):
            getattr(restored, attribute)
        with pytest.raises(ModuleStateError):
            setattr(restored, attribute, {})
    with pytest.raises(ModuleStateError):
        combat.reset(restored)
    assert json.dumps(restored.modules[combat.MODULE_NAME], sort_keys=True) == encoded_slot


def lifecycle_instance():
    instance = make_instance()
    populate(instance)
    instance.round_number = 2
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.seed_code = "keep-seed"
    instance.players = {"p": {"character_sheet": {"hp": 3, "max_hp": 20, "gold": 7}}}
    instance.npcs = {"guard": {"hp": 4}}
    instance.scene = "gate"
    instance.action_queue = [{"user_id": "p", "text": "attack"}]
    instance.pending_actions = [{"user_id": "p", "text": "wait"}]
    instance.ready_players = {"p"}
    instance.round_start_snapshot = {"p": {"hp": 20, "gold": 10}}
    instance.capture_round_entity_snapshot()
    instance.log = [{
        "round": 1, "gm_response": "previous round", "swipes": [],
        "round_start_snapshot": deepcopy(instance.round_start_snapshot),
    }]
    instance.last_checks = [{"id": "check"}]
    instance.death_save_outcomes = {"2": {"p": {"outcome": "stable"}}}
    instance.economy["next_sequence"] = 7
    instance.lorebook_timed_state = {"entry": {"sticky": 2}}
    return instance


def serialized_state(instance):
    # to_dict exposes live module children; freeze the entire persisted aggregate.
    return json.dumps(instance.to_dict(), sort_keys=True)


def unsupported_lifecycle_instance(version, module="combat_extension"):
    payload = lifecycle_instance().to_dict()
    payload["modules"][module]["schema_version"] = version
    return GameInstance.from_dict(json.loads(json.dumps(payload)))


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [99, None, "1"])
@pytest.mark.parametrize("operation, args", [
    ("rollback_last_round", ()),
    ("abort_round_processing", ()),
    ("finish_judgment", ("new response",)),
    ("reset", (True,)),
    ("reset", (False,)),
])
async def test_unknown_combat_lifecycle_rejection_preserves_entire_save(version, operation, args):
    instance = unsupported_lifecycle_instance(version)
    before = serialized_state(instance)
    with pytest.raises(ModuleStateError, match="unsupported combat_extension module schema"):
        await getattr(instance, operation)(*args)
    assert serialized_state(instance) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [99, None, "1"])
@pytest.mark.parametrize("keep_seed", [False, True])
async def test_unknown_lorebook_reset_rejection_preserves_entire_save(version, keep_seed):
    instance = unsupported_lifecycle_instance(version, "lorebook_runtime")
    before = serialized_state(instance)
    with pytest.raises(ModuleStateError, match="unsupported lorebook_runtime module schema"):
        await instance.reset(keep_seed=keep_seed)
    assert serialized_state(instance) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [99, None, "1"])
async def test_unknown_combat_empty_rollback_remains_a_noop(version):
    instance = unsupported_lifecycle_instance(version)
    instance.log.clear()
    before = serialized_state(instance)
    assert await instance.rollback_last_round() is None
    assert serialized_state(instance) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [state for state in GameState if state != GameState.ACTIVE_JUDGMENT])
async def test_unknown_combat_abort_outside_judgment_remains_a_noop(state):
    instance = unsupported_lifecycle_instance(99)
    instance.state = state
    before = serialized_state(instance)
    assert await instance.abort_round_processing() is False
    assert serialized_state(instance) == before


@pytest.mark.parametrize("snapshot", [None, [], {"schema_version": 99, "combat_extension": {}}])
def test_unknown_combat_invalid_snapshot_restore_remains_a_noop(snapshot):
    instance = unsupported_lifecycle_instance(99)
    before = serialized_state(instance)
    assert instance.restore_combat_extension_snapshot(snapshot) is False
    assert serialized_state(instance) == before


@pytest.mark.parametrize("json_roundtrip", [False, True])
def test_roundtrip_projects_only_module_storage_and_clones_nested_data(json_roundtrip):
    instance = make_instance()
    populate(instance)
    payload = instance.to_dict()
    assert all(name not in payload for name, _ in ATTRS)
    # The module projection is live, not a detached snapshot (unlike the old
    # shallow combat-field copies). Reconstruction is the isolation boundary.
    assert payload["modules"][combat.MODULE_NAME]["current"] is instance.combat_extension
    assert payload["modules"][combat.MODULE_NAME]["round_snapshots"] is instance.combat_extension_round_snapshots
    saved = json.loads(json.dumps(payload)) if json_roundtrip else payload
    restored = GameInstance.from_dict(saved)
    assert restored.modules == instance.modules
    restored.combat_extension["opaque"]["values"].append(9)
    restored.combat_extension_round_snapshots["2"]["combat_extension"]["opaque"].append(9)
    assert instance.combat_extension["opaque"]["values"] == [1]
    assert instance.combat_extension_round_snapshots["2"]["combat_extension"]["opaque"] == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_lifecycle", [False, True])
async def test_reset_replaces_current_clears_snapshots_in_place_and_retains_policy(use_lifecycle):
    instance = make_instance()
    populate(instance)
    instance.away_control_policy = "ai_takeover"
    instance.modules["other"] = {"schema_version": 77, "keep": [1]}
    slot = instance.modules[combat.MODULE_NAME]
    slot["extra"] = {"keep": True}
    current, snapshots = instance.combat_extension, instance.combat_extension_round_snapshots
    original_current = deepcopy(current)
    other = instance.modules["other"]
    run_id = instance.run_id
    if use_lifecycle:
        await instance.reset()
        assert instance.run_id != run_id
    else:
        combat.reset(instance)
        assert instance.run_id == run_id
    assert instance.modules[combat.MODULE_NAME] is slot
    assert instance.combat_extension == {} and instance.combat_extension is not current
    assert current == original_current
    assert instance.combat_extension_round_snapshots is snapshots and snapshots == {}
    assert slot["extra"] == {"keep": True}
    assert instance.modules["other"] is other
    assert instance.away_control_policy == "ai_takeover"


def test_staged_aggregate_commit_deepcopies_combat_and_retains_runtime_identity():
    instance = make_instance()
    populate(instance)
    locks = (instance._lock, instance._process_lock, instance._authority_lock)
    run_id = instance.run_id
    staged = GameInstance.from_dict(deepcopy(instance.to_dict()))
    staged.combat_extension["opaque"]["values"].append(3)
    staged.combat_extension_round_snapshots["2"]["combat_extension"]["opaque"].append(4)
    assert instance.combat_extension["opaque"]["values"] == [1]
    assert instance.combat_extension_round_snapshots["2"]["combat_extension"]["opaque"] == [2]
    instance.replace_persisted_state_from(staged)
    assert instance.modules == staged.modules
    staged.combat_extension["opaque"]["values"].append(5)
    staged.combat_extension_round_snapshots.clear()
    assert instance.combat_extension["opaque"]["values"] == [1, 3]
    assert instance.combat_extension_round_snapshots["2"]["combat_extension"]["opaque"] == [2, 4]
    assert (instance._lock, instance._process_lock, instance._authority_lock) == locks
    assert instance.run_id == run_id
    assert all(name not in instance.__dict__ for name, _ in ATTRS)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_luck", [False, True])
async def test_disk_startup_recovery_preserves_both_combat_children(tmp_path, pending_luck):
    registry = GameRegistry(tmp_path / "saves")
    instance = make_instance()
    populate(instance)
    instance.state = GameState.ACTIVE_JUDGMENT
    if pending_luck:
        instance.round_checks_prepared = True
        instance.last_checks = [{"check_id": "c1", "actor_uid": "p", "luck_decision": "pending"}]
    registry.register(instance)
    await registry.save(instance)
    restored, = await GameRegistry(tmp_path / "saves").recover_all()
    assert restored.state == (GameState.ACTIVE_JUDGMENT if pending_luck else GameState.PAUSED)
    assert restored.modules[combat.MODULE_NAME] == instance.modules[combat.MODULE_NAME]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["abort", "rollback"])
@pytest.mark.parametrize("invalid", [False, True])
async def test_round_recovery_restores_or_clears_current_without_partial_entity_edits(operation, invalid):
    instance = make_instance()
    populate(instance)
    instance.round_number = 2
    instance.players["p"] = {"character_sheet": {"hp": 20}}
    snapshot = malformed_snapshot()
    if not invalid:
        snapshot["entity_fields"].pop("npc:broken")
    instance.combat_extension_round_snapshots["2"] = snapshot
    slot = instance.modules[combat.MODULE_NAME]
    old_current = instance.combat_extension
    if operation == "abort":
        instance.state = GameState.ACTIVE_JUDGMENT
        assert await instance.abort_round_processing()
    else:
        instance.log = [{"round": 2, "combat_extension_round_start": snapshot}]
        assert await instance.rollback_last_round() == 2
        assert instance.combat_extension_round_snapshots == {}
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.modules[combat.MODULE_NAME] is slot
    assert instance.combat_extension == ({} if invalid else snapshot["combat_extension"])
    assert instance.combat_extension is not old_current
    assert old_current["opaque"]["values"] == [1]
    assert instance.get_character_sheet("p")["hp"] == (20 if invalid else 1)


def test_snapshot_first_touch_late_fields_and_summary_copy_survive_module_storage():
    instance = make_instance()
    populate(instance)
    instance.round_number = 7
    instance.players["p"] = {"character_sheet": {"hp": 20, "inventory": ["old"]}}
    instance.capture_combat_extension_snapshot({"player:p": ("hp", "missing")})
    first = instance.combat_extension_round_snapshots["7"]
    instance.get_character_sheet("p").update(hp=10, missing="added", qi=4)
    instance.capture_combat_extension_snapshot({"player:p": ("hp", "missing", "qi", "inventory")})
    assert instance.combat_extension_round_snapshots["7"] is first
    tracked = first["entity_fields"]["player:p"]
    assert tracked == {"values": {"hp": 20, "qi": 4, "inventory": ["old"]}, "missing": ["missing"]}
    instance.get_character_sheet("p")["inventory"].append("new")
    instance.combat_extension["pending_summaries"] = ["queued"]
    now = instance.current_combat_extension_snapshot()
    assert "pending_summaries" not in now["combat_extension"]
    assert instance.combat_extension["pending_summaries"] == ["queued"]
    now["combat_extension"]["opaque"]["values"].append(9)
    assert instance.combat_extension["opaque"]["values"] == [1]
    assert instance.restore_combat_extension_snapshot(first)
    assert instance.get_character_sheet("p") == {"hp": 20, "qi": 4, "inventory": ["old"]}
    assert instance.restore_combat_extension_snapshot({"schema_version": 1, "opaque": [8]})
    assert instance.combat_extension == {"schema_version": 1, "opaque": [8]}


@pytest.mark.asyncio
async def test_bounded_snapshots_and_finished_log_copies_remain_independent():
    instance = make_instance()
    populate(instance)
    for round_number in range(1, 103):
        instance.round_number = round_number
        instance.capture_combat_extension_snapshot()
    assert set(instance.combat_extension_round_snapshots) == {str(n) for n in range(3, 103)}
    first = instance.combat_extension_round_snapshots["102"]
    current = instance.current_combat_extension_snapshot()
    await instance.finish_judgment("done", pre_combat_extension_snapshot=current)
    entry = instance.log[-1]
    assert entry["combat_extension_round_start"] == first
    assert entry["pre_combat_extension_snapshot"] == current
    first["combat_extension"]["opaque"]["values"].append(8)
    current["combat_extension"]["opaque"]["values"].append(9)
    assert entry["combat_extension_round_start"]["combat_extension"]["opaque"]["values"] == [1]
    assert entry["pre_combat_extension_snapshot"]["combat_extension"]["opaque"]["values"] == [1]
    assert "102" not in instance.combat_extension_round_snapshots


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_at", ["current", "target"])
async def test_swipe_invalid_snapshot_clears_staged_current_without_leaking_on_failure(invalid_at):
    from src.commands.swipe_generator import SwipeGenerator

    instance = make_instance()
    populate(instance)
    instance.round_number = 3
    instance.players["p"] = {"character_sheet": {"hp": 20}}
    instance.log = [{"round": 2, "gm_response": "old", "pre_state_snapshot": {"p": {"hp": 20}}}]
    if invalid_at == "current":
        instance.combat_extension_round_snapshots["3"] = malformed_snapshot()
    else:
        instance.log[0]["pre_combat_extension_snapshot"] = malformed_snapshot()
    before = deepcopy(instance.to_dict())

    class GenerationStopped(Exception):
        pass

    async def check_restored_stage(staged, *args, **kwargs):
        assert staged is not instance
        assert staged.combat_extension == {}
        assert staged.combat_extension_round_snapshots == {}
        assert staged.get_character_sheet("p")["hp"] == 20
        assert instance.to_dict() == before
        raise GenerationStopped

    generator = SwipeGenerator(
        llm_client=None, matcher=None, prompt=None, state_applier=None,
        load_world_template=lambda *args: None, ensure_matcher_for_world=lambda *args: None,
        narrative_max_tokens=100, lore_retriever=SimpleNamespace(retrieve=check_restored_stage),
    )
    with pytest.raises(GenerationStopped):
        await generator.generate(instance, 2)
    assert instance.to_dict() == before
