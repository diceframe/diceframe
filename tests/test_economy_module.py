"""R2 storage, identity and settlement contracts across the economy slot."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine import economy
from src.engine.game_instance import GameInstance, _snapshot_players, restore_players
from src.engine.game_state import GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import economy_state
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v18_to_v19,
    migrate_game_state_payload,
    rebind_imported_game_state_payload,
)


def _ledger(run_id="run_source"):
    ledger = economy_state.fresh_economy_state(run_id)
    ledger.update({
        "next_sequence": 8,
        "decision_revision": 4,
        "proposals": [{"id": "eco_7", "run_id": run_id, "round": 2, "status": "pending", "amount": 13}],
        "transactions": [{"id": "tx_6", "run_id": run_id, "round": 3, "entries": [{"delta": -9}]}],
        "effect_groups": [{"id": "group", "run_id": run_id, "round": 3, "effects": {"scene": "gate"}}],
        "external_effects_outbox": [
            {"id": "delivery", "run_id": run_id, "status": "pending", "payload": {"fact": "gate open"}},
            {"id": "receipt", "run_id": run_id, "status": "delivered"},
            {"id": "reversal", "run_id": run_id, "status": "reversal_pending"},
        ],
        "outcomes": [{"id": "outcome", "run_id": run_id, "round": 2, "resolved_round": 3}],
        "idempotency_records": {"source": "eco_7"},
        "opaque": {"future_record": [1, 2]},
    })
    return ledger


def test_migration_moves_entire_ledger_verbatim_and_is_idempotent():
    source = {
        "instance_schema_version": 18,
        "game_key": ["web", "legacy", "u"],
        "economy": _ledger(),
        "modules": {"other": {"schema_version": 8, "opaque": [1]}},
    }
    original = deepcopy(source)
    migrated = migrate_game_state_payload(source)
    assert source == original
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert "economy" not in migrated
    assert migrated["modules"]["economy"] == {"schema_version": 1, "state": original["economy"]}
    assert migrated["modules"]["other"] == original["modules"]["other"]
    assert migrate_game_state_payload(migrated) == migrated
    step = _migrate_v18_to_v19(deepcopy(source))
    assert step["instance_schema_version"] == 19
    assert _migrate_v18_to_v19(deepcopy(step)) == step


@pytest.mark.parametrize("legacy", [None, [], "corrupt", {}])
@pytest.mark.parametrize("modules", [None, {}, {"economy": "corrupt"}])
def test_missing_or_corrupt_legacy_ledger_has_safe_defaults(legacy, modules):
    payload = {"instance_schema_version": 18, "state": "created", "game_key": ["web", "defaults", "u"], "modules": modules}
    if legacy is not None:
        payload["economy"] = legacy
    migrated = migrate_game_state_payload(payload)
    assert migrated["modules"]["economy"] == {"schema_version": 1, "state": {}}
    instance = GameInstance.from_dict(migrated)
    assert instance.economy == economy_state.fresh_economy_state(instance.run_id)
    assert instance.run_id


@pytest.mark.parametrize("slot", [
    {"schema_version": 1, "state": {"run_id": "existing", "proposals": ["keep"]}, "extra": True},
    {"schema_version": 99, "opaque": [1, 2]},
])
def test_migration_existing_slot_wins_over_legacy_ledger(slot):
    migrated = migrate_game_state_payload({
        "instance_schema_version": 18,
        "economy": _ledger(),
        "modules": {"economy": deepcopy(slot)},
    })
    assert migrated["modules"]["economy"] == slot
    assert "economy" not in migrated


def test_new_instances_have_independent_ledgers_and_live_property():
    first = GameInstance(game_key=("web", "first", "u"))
    second = GameInstance(game_key=("web", "second", "u"))
    assert first.modules["economy"]["schema_version"] == 1
    assert first.economy == economy_state.fresh_economy_state(first.run_id)
    assert first.economy is first.modules["economy"]["state"]
    first.economy["proposals"].append({"id": "local"})
    assert first.modules["economy"]["state"]["proposals"] == [{"id": "local"}]
    assert second.economy["proposals"] == []
    assert first.run_id != second.run_id


@pytest.mark.parametrize("saved_run_id", ["run_original", "", None, 0])
def test_initialization_preserves_explicit_ledger_identity(saved_run_id):
    raw = {"run_id": saved_run_id, "next_sequence": 9, "proposals": None, "extra": [1]}
    instance = GameInstance(
        game_key=("web", "explicit", "u"), run_id="run_current",
        modules={"economy": {"schema_version": 1, "state": raw}},
    )
    assert instance.economy is raw
    assert instance.economy["run_id"] == saved_run_id
    assert instance.economy["next_sequence"] == 9
    assert instance.economy["proposals"] is None
    assert instance.economy["extra"] == [1]
    assert instance.economy["schema_version"] == 2
    assert GameInstance.from_dict(instance.to_dict()).economy == instance.economy


def test_initialization_defaults_only_missing_run_id_after_instance_identity_exists():
    instance = GameInstance(
        game_key=("web", "missing", "u"), run_id="",
        modules={"economy": {"schema_version": 1, "state": {"next_sequence": 7}}},
    )
    assert instance.run_id
    assert instance.economy["run_id"] == instance.run_id
    assert instance.economy["next_sequence"] == 7
    repaired = economy_state.ensure(deepcopy(instance.modules["economy"]))
    assert economy_state.ensure(deepcopy(repaired)) == repaired


@pytest.mark.parametrize("snapshot", [{}, {"proposals": [{"id": "snapshot"}], "run_id": ""}])
def test_setter_restores_exact_snapshot_without_normalization(snapshot):
    instance = GameInstance(game_key=("web", "rollback", "u"))
    instance.economy = snapshot
    assert instance.economy is snapshot
    assert instance.modules["economy"]["state"] is snapshot
    before = deepcopy(snapshot)
    instance.economy = _ledger(instance.run_id)
    instance.economy = snapshot
    assert instance.economy == before
    assert "schema_version" not in snapshot


def test_save_load_preserves_complete_ledger_and_inner_schema():
    instance = GameInstance(game_key=("web", "roundtrip", "u"), run_id="run_source")
    instance.economy = _ledger(instance.run_id)
    payload = instance.to_dict()
    assert "economy" not in payload
    assert payload["modules"]["economy"]["schema_version"] == 1
    restored = GameInstance.from_dict(payload)
    assert restored.economy["schema_version"] == 2
    assert restored.economy == instance.economy
    restored.economy["proposals"][0]["amount"] = 99
    assert instance.economy["proposals"][0]["amount"] == 13


def test_run_rotation_clears_ledger_without_mutating_old_snapshot_or_other_slots():
    instance = GameInstance(game_key=("web", "rotate", "u"))
    instance.economy = _ledger(instance.run_id)
    old_ledger = instance.economy
    before = deepcopy(old_ledger)
    instance.away_control_policy = "ai_takeover"
    old, new = instance.rotate_run_identity()
    assert old == before["run_id"]
    assert old != new == instance.run_id
    assert instance.memory_namespace == f"{instance.game_key!s}::run:{new}"
    assert instance.economy == economy_state.fresh_economy_state(new)
    assert old_ledger == before
    assert instance.economy is not old_ledger
    assert instance.away_control_policy == "ai_takeover"


@pytest.mark.asyncio
async def test_reset_rebinds_fresh_economy_to_new_run():
    instance = GameInstance(game_key=("web", "reset", "u"))
    instance.economy = _ledger(instance.run_id)
    old = instance.run_id
    await instance.reset()
    assert instance.run_id != old
    assert instance.economy == economy_state.fresh_economy_state(instance.run_id)
    assert instance.economy is instance.modules["economy"]["state"]


def test_aggregate_replacement_copies_ledger_without_aliasing():
    instance = GameInstance(game_key=("web", "staged", "u"))
    staged = GameInstance.from_dict(instance.to_dict())
    staged.economy = _ledger(staged.run_id)
    instance.replace_persisted_state_from(staged)
    assert instance.economy == staged.economy
    staged.economy["proposals"].clear()
    assert len(instance.economy["proposals"]) == 1


@pytest.mark.parametrize("version", [None, 0, 99])
def test_unknown_module_schema_round_trips_and_runtime_fails_without_mutation(version):
    slot = {"schema_version": version, "state": _ledger(), "extra": [1]}
    instance = GameInstance(game_key=("web", "future", "u"), modules={"economy": deepcopy(slot)})
    restored = GameInstance.from_dict(instance.to_dict())
    before = deepcopy(restored.to_dict())
    assert restored.modules["economy"] == slot
    with pytest.raises(ModuleStateError, match="unsupported economy module schema"):
        _ = restored.economy
    with pytest.raises(ModuleStateError):
        restored.economy = {}
    with pytest.raises(ModuleStateError):
        economy.pending_proposals(restored)
    with pytest.raises(ModuleStateError):
        restored.rotate_run_identity()
    assert restored.to_dict() == before
    with pytest.raises(ModuleStateError):
        rebind_imported_game_state_payload(before, game_key=("web", "import", "u"), run_id="new")
    assert restored.to_dict() == before


def _unsupported_economy_instance(version):
    instance = GameInstance(game_key=("web", "future-lifecycle", "u"))
    instance.round_number = 2
    instance.players = {"p": {"character_sheet": {"hp": 3, "max_hp": 10, "gold": 7}}}
    instance.npcs = {"guard": {"hp": 4}}
    instance.scene = "gate"
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.action_queue = [{"user_id": "p", "text": "open gate"}]
    instance.pending_actions = [{"user_id": "p", "text": "wait"}]
    instance.ready_players = {"p"}
    instance.round_start_snapshot = {"p": {"hp": 10, "gold": 20}}
    instance.capture_round_entity_snapshot()
    instance.combat_extension = {"schema_version": 1, "pending_summaries": ["guard hit"]}
    instance.combat_extension_round_snapshots = {"2": {"schema_version": 1, "phase": "before"}}
    instance.log = [{
        "round": 1, "gm_response": "previous round", "swipes": [],
        "round_start_snapshot": deepcopy(instance.round_start_snapshot),
    }]
    instance.last_checks = [{"id": "check"}]
    instance.death_save_outcomes = {"2": {"p": {"outcome": "stable"}}}
    instance.economy = _ledger(instance.run_id)
    payload = instance.to_dict()
    payload["modules"]["economy"]["schema_version"] = version
    return GameInstance.from_dict(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0, 99])
@pytest.mark.parametrize("operation, args", [
    ("rollback_last_round", ()),
    ("abort_round_processing", ()),
    ("start_round", ()),
    ("finish_judgment", ("new response",)),
    ("finish_judgment_with_swipe", ("new branch", 1)),
    ("reset", ()),
    ("advance_round", ()),
    ("try_advance", ()),
])
async def test_unknown_economy_lifecycle_rejection_preserves_entire_save(version, operation, args):
    instance = _unsupported_economy_instance(version)
    if operation in {"advance_round", "try_advance"}:
        instance.state = GameState.ACTIVE_ACTION
    before = deepcopy(instance.to_dict())
    with pytest.raises(ModuleStateError, match="unsupported economy module schema"):
        await getattr(instance, operation)(*args)
    assert instance.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0, 99])
async def test_unknown_economy_empty_rollback_remains_a_noop(version):
    instance = _unsupported_economy_instance(version)
    instance.log.clear()
    before = deepcopy(instance.to_dict())
    assert await instance.rollback_last_round() is None
    assert instance.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [state for state in GameState if state != GameState.ACTIVE_JUDGMENT])
async def test_unknown_economy_abort_outside_judgment_remains_a_noop(state):
    instance = _unsupported_economy_instance(99)
    instance.state = state
    before = deepcopy(instance.to_dict())
    assert await instance.abort_round_processing() is False
    assert instance.to_dict() == before


@pytest.mark.parametrize("version", [18, CURRENT_INSTANCE_SCHEMA_VERSION])
def test_import_rebinds_nested_records_and_keeps_only_pending_memory_deliveries(version):
    ledger = _ledger()
    source = {"instance_schema_version": version, "state": "created", "run_id": "run_source", "game_key": ["web", "source", "u"]}
    if version == 18:
        source["economy"] = ledger
    else:
        source["modules"] = {"economy": {"schema_version": 1, "state": ledger, "extra": "keep"}}
    before = deepcopy(source)
    key = ("web", "import", "u")
    imported = rebind_imported_game_state_payload(source, game_key=key, run_id="run_imported")
    assert source == before
    assert imported["game_key"] == list(key)
    assert imported["run_id"] == "run_imported"
    assert imported["memory_namespace"] == f"{key!s}::run:run_imported"
    assert "economy" not in imported
    expected = deepcopy(ledger)
    expected["run_id"] = "run_imported"
    expected["external_effects_outbox"] = expected["external_effects_outbox"][:1]
    for name in ("proposals", "transactions", "effect_groups", "external_effects_outbox", "outcomes"):
        for item in expected[name]:
            item["run_id"] = "run_imported"
    assert imported["modules"]["economy"]["state"] == expected
    if version == CURRENT_INSTANCE_SCHEMA_VERSION:
        assert imported["modules"]["economy"]["extra"] == "keep"
    assert GameInstance.from_dict(imported).economy == expected


@pytest.mark.parametrize("round_number", [0, 7, -2, None, "12"])
def test_era_key_retains_round_coercion(round_number):
    assert economy.era_key(SimpleNamespace(round_number=round_number)) == int(round_number or 0)
    assert economy.era_key(SimpleNamespace()) == 0


def test_all_new_economy_records_use_era_key_and_keep_proposal_origin(monkeypatch):
    instance = GameInstance(game_key=("web", "era", "u"), gm_uid="p")
    instance.round_number = 3
    instance.players = {"p": {"character_sheet": {"gold": 30, "currency": {"amount": 30}}}}
    monkeypatch.setattr(economy, "era_key", lambda _: 11)
    proposal = economy.queue_proposal(instance, kind="payment", source="gm_manual", payer_uid="p", amount=5)
    group = economy.queue_effect_group(instance, [proposal], {"state_update": {"scene_change": "gate"}})
    assert proposal["round"] == 11
    assert group["round"] == 11
    monkeypatch.setattr(economy, "era_key", lambda _: 12)
    result = economy.resolve_proposal(instance, proposal["id"], actor_uid="p", accepted=True)
    assert result["ok"]
    assert instance.economy["transactions"][0]["round"] == 12
    outcome = instance.economy["outcomes"][-1]
    assert outcome["round"] == 11
    assert outcome["resolved_round"] == 12
    fallback = economy.record_economy_outcome(instance, {}, status="rejected", actor_uid="p")
    assert fallback["round"] == fallback["resolved_round"] == 12
    snapshot = _snapshot_players(instance)
    economy.reverse_round_economy(instance, 12)
    restore_players(instance, economy.reconcile_rollback_snapshot(instance, snapshot, 12))
    assert proposal["status"] == "pending"
    assert instance.economy["transactions"][0]["status"] == "reversed"
    assert instance.get_character_sheet("p")["currency"]["amount"] == 30


def test_future_instance_schema_is_rejected():
    with pytest.raises(ValueError, match="unsupported game instance schema version"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})
