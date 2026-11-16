"""Production adapter/service/save contracts for the remote character-sheet bridge."""
from copy import deepcopy
import random

import pytest

from src.engine.game_instance import GameInstance
from src.engine.game_registry import GameRegistry
from src.engine.modules import ruleset_runtime
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter
from src.rulesets.remote_event_receipts import RECEIPT_KEY
from src.rulesets.runtime_probe import RuntimeProbeError
from src.webui.services import ruleset_gameplay
from src.webui.services._common import _parse_game_key


def instance_with_sheet():
    instance = GameInstance(game_key=("test", "sheet", "remote"), run_id="sheet-run")
    ruleset_runtime.bind(instance, {
        "id": "external:sheet", "version": 1, "rule_id": "sheet-rule",
        "content_version": "1", "state_schema_version": 1,
    })
    instance.put_player("alice", {"user_id": "alice", "character_sheet": {"hp": 10}})
    return instance


def adapter(transport, **kwargs):
    return RemoteRulesetRuntimeAdapter(
        "external:sheet", 1, RulesetCapabilities(authoritative_intents=True, versioned_state=True),
        transport, **kwargs,
    )


def sheet_transport(calls, full_bridge=False):
    def transport(request):
        calls.append(deepcopy(request))
        payload = request["payload"]
        if request["operation"] == "apply_event_batch":
            batch = payload["batch"]
            result = {"ok": True, "applied": True, "character_sheets": {"alice": {"hp": batch["hp"]}}}
            if full_bridge:
                slot = payload["instance"]["modules"]["ruleset_runtime"]
                result.update(state={"version": len(slot["event_ledger"]) + 1},
                              event_ledger=[*slot["event_ledger"], batch])
        elif request["operation"] == "prepare_intent_submission":
            result = payload["intent"]
        elif request["operation"] == "resolve_intent":
            intent = payload["intent"]
            result = {"ok": True, "event_batch": {"batch_id": intent["intent_id"], "hp": intent["hp"]}}
        elif request["operation"] in {"available_intents", "memory_deltas_from_event_batch"}:
            result = []
        elif request["operation"] == "gameplay_view":
            result = {}
        else:
            raise AssertionError(request["operation"])
        return {"request_id": request["request_id"], "ok": True, "result": result}
    return transport


@pytest.mark.asyncio
@pytest.mark.parametrize("full_bridge", [False, True], ids=["sheet-only", "state-ledger-sheets"])
async def test_sheet_replay_survives_real_save_reload_and_adapter_reconstruction(tmp_path, full_bridge):
    instance = instance_with_sheet()
    calls = []
    runtime = adapter(sheet_transport(calls, full_bridge), ledger_path=tmp_path / "rpc.json")
    b1, b2 = {"batch_id": "b1", "hp": 9}, {"batch_id": "b2", "hp": 3}
    runtime.apply_event_batch(instance, b1)
    runtime.apply_event_batch(instance, b2)
    before = deepcopy(instance.to_dict())
    assert runtime.apply_event_batch(instance, b1) == {"ok": True, "applied": False, "replayed": True}
    assert instance.to_dict() == before

    saves = GameRegistry(save_dir=tmp_path / "saves")
    await saves.save(instance)
    restored = await GameRegistry(save_dir=tmp_path / "saves").load(instance.game_key)
    assert restored is not None
    # No RPC receipt cache at all: the save's host application ledger is authoritative.
    fresh = adapter(lambda _request: pytest.fail("host replay must not contact child"))
    before = deepcopy(restored.to_dict())
    assert fresh.apply_event_batch(restored, b1)["replayed"] is True
    assert fresh.apply_event_batch(restored, b2)["applied"] is False
    assert restored.to_dict() == before
    assert restored.get_character_sheet("alice") == {"hp": 3}
    with pytest.raises(RuntimeProbeError, match="different payload"):
        fresh.apply_event_batch(restored, {**b1, "hp": 100})
    assert restored.to_dict() == before
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_sheet_only_original_intent_replay_through_submission_service(tmp_path):
    instance = instance_with_sheet()
    calls, saves = [], []
    runtime = adapter(sheet_transport(calls), ledger_path=tmp_path / "rpc.json")
    rule = RuleSystem({"rule_id": "sheet-rule", "runtime": {"id": "external:sheet", "minimum_version": 1}})

    async def save(value):
        saves.append(deepcopy(value.to_dict()))

    dependencies = ruleset_gameplay.RulesetGameplayDependencies(
        get_instance=lambda key: instance if key == instance.game_key else None,
        parse_game_key=_parse_game_key, load_rule_for_game=lambda _instance: rule,
        ruleset_registry=RulesetRuntimeRegistry([runtime]),
        resolve_adventure_binding=lambda *args: {}, save_instance=save,
        apply_memory_delta=None, rng_factory=random.Random,
    )
    for intent_id, hp in [("b1", 9), ("b2", 3), ("b1", 9)]:
        result = await ruleset_gameplay.submit_intent(
            dependencies, "|".join(instance.game_key), "alice", False,
            {"type": "sheet.action", "intent_id": intent_id, "hp": hp},
        )
        assert result["ok"], result
    assert instance.get_character_sheet("alice") == {"hp": 3}
    assert [r["payload"]["batch"]["batch_id"] for r in calls if r["operation"] == "apply_event_batch"] == ["b1", "b2"]
    assert len(saves) == 3
    assert len(ruleset_runtime.event_ledger(instance)) == 2


@pytest.mark.parametrize("bad_result", [
    {"ok": True, "character_sheets": {"alice": {"hp": 3}, "unknown": {"hp": 0}}},
    {"ok": True, "state": {"version": 2}, "event_ledger": [], "character_sheets": {"alice": {"hp": 3}}},
    {"ok": True, "state": {"version": 2}, "event_ledger": [{"batch_id": "b2"}, {"batch_id": "b3"}], "character_sheets": {"alice": {"hp": 3}}},
    {"ok": True, "applied": False, "character_sheets": {"alice": {"hp": 3}}},
    {"ok": False, "character_sheets": {"alice": {"hp": 3}}},
    {"ok": True, "receipt_details": float("nan"), "character_sheets": {"alice": {"hp": 3}}},
    {"ok": True, "state": {}, "event_ledger": [{"batch_id": "b3", RECEIPT_KEY: {}}], "character_sheets": {"alice": {"hp": 3}}},
])
def test_invalid_bridge_never_half_writes_state_ledger_or_sheets(bad_result):
    instance = instance_with_sheet()
    calls = []
    adapter(sheet_transport(calls)).apply_event_batch(instance, {"batch_id": "b1", "hp": 9})
    runtime = adapter(lambda req: {"request_id": req["request_id"], "ok": True, "result": bad_result})
    before = deepcopy(instance.to_dict())
    with pytest.raises(RuntimeProbeError):
        runtime.apply_event_batch(instance, {"batch_id": "b3"})
    assert instance.to_dict() == before


def test_child_history_is_isolated_from_host_receipts_across_mixed_bridges():
    instance = instance_with_sheet()
    calls = []
    transport = sheet_transport(calls, full_bridge=True)
    runtime = adapter(transport)
    runtime.apply_event_batch(instance, {"batch_id": "b1", "hp": 9})
    adapter(sheet_transport(calls)).apply_event_batch(instance, {"batch_id": "b2", "hp": 7})
    runtime.apply_event_batch(instance, {"batch_id": "b3", "hp": 3})
    sent = calls[-1]["payload"]["instance"]["modules"]["ruleset_runtime"]["event_ledger"]
    assert sent == [{"batch_id": "b1", "hp": 9}]
    assert [r["batch_id"] for r in ruleset_runtime.event_ledger(instance)] == ["b1", "b2", "b3"]
    runtime.apply_event_batch(instance, {"batch_id": "b1", "hp": 9})
    assert instance.get_character_sheet("alice") == {"hp": 3}
    assert len(calls) == 3


def test_existing_child_version_cannot_overwrite_sheet_or_state():
    instance = instance_with_sheet()
    ruleset_runtime.replace_state(instance, {"version": 4})
    ruleset_runtime.replace_event_ledger(instance, [{"batch_id": "old"}])
    response = {"ok": True, "state": {"version": 4}, "event_ledger": [{"batch_id": "old"}],
                "character_sheets": {"alice": {"hp": 100}}}
    runtime = adapter(lambda req: {"request_id": req["request_id"], "ok": True, "result": response})
    before = deepcopy(instance.to_dict())
    with pytest.raises(RuntimeProbeError, match="exactly one"):
        runtime.apply_event_batch(instance, {"batch_id": "new"})
    with pytest.raises(RuntimeProbeError, match="without a host apply receipt"):
        runtime.apply_event_batch(instance, {"batch_id": "old"})
    assert instance.to_dict() == before


def test_transaction_rollback_restores_receipts_and_fences_cached_postimage(tmp_path):
    instance = instance_with_sheet()
    calls = []
    runtime = adapter(sheet_transport(calls), ledger_path=tmp_path / "rpc.json")
    before = {"ruleset_state": deepcopy(ruleset_runtime.state(instance)),
              "event_ledger": deepcopy(ruleset_runtime.event_ledger(instance)),
              "players": deepcopy(instance.players)}
    batch = {"batch_id": "b1", "hp": 9}
    runtime.apply_event_batch(instance, batch)
    instance.restore_ruleset_transaction(before)
    assert ruleset_runtime.event_ledger(instance) == []
    assert instance.get_character_sheet("alice") == {"hp": 10}
    # Retry against the original baseline can reuse the transport success.
    runtime.apply_event_batch(instance, batch)
    assert len(calls) == 1
    instance.restore_ruleset_transaction(before)
    instance.set_character_sheet("alice", {"hp": 2})
    changed = deepcopy(instance.to_dict())
    # The transport receipt outlives rollback, but is not valid at another base.
    reconstructed = adapter(sheet_transport(calls), ledger_path=tmp_path / "rpc.json")
    with pytest.raises(RuntimeProbeError, match="different payload"):
        reconstructed.apply_event_batch(instance, batch)
    assert instance.to_dict() == changed
    assert len(calls) == 1


def test_round_ledger_truncation_rewinds_host_receipt_and_preserves_child_prefix():
    instance = instance_with_sheet()
    calls = []
    runtime = adapter(sheet_transport(calls, full_bridge=True))
    runtime.apply_event_batch(instance, {"batch_id": "b1", "hp": 9})
    entry = ruleset_runtime.round_entry_snapshot(instance)
    sheet_at_entry = deepcopy(instance.get_character_sheet("alice"))
    b2 = {"batch_id": "b2", "hp": 3}
    runtime.apply_event_batch(instance, b2)
    assert ruleset_runtime.restore_round_entry(instance, entry) is True
    # Character before-images are restored by the existing round owner.
    instance.set_character_sheet("alice", sheet_at_entry)
    assert [r["batch_id"] for r in ruleset_runtime.event_ledger(instance)] == ["b1"]
    assert runtime.apply_event_batch(instance, b2)["applied"] is True
    assert instance.get_character_sheet("alice") == {"hp": 3}
    assert len(calls) == 2  # same base, cached response, fresh application receipt


@pytest.mark.parametrize("batch", [{}, {"batch_id": " b1"}, {"batch_id": 1}, {"batch_id": "b1", "value": float("nan")}])
def test_invalid_batch_identity_is_rejected_before_transport(batch):
    instance = instance_with_sheet()
    runtime = adapter(lambda _request: pytest.fail("invalid identity reached child"))
    before = deepcopy(instance.to_dict())
    with pytest.raises(RuntimeProbeError):
        runtime.apply_event_batch(instance, batch)
    assert instance.to_dict() == before


def test_host_ledger_limit_rejects_new_write_without_evicting_history():
    instance = instance_with_sheet()
    ruleset_runtime.replace_event_ledger(instance, [{"batch_id": f"old-{index}"} for index in range(4096)])
    runtime = adapter(sheet_transport([]))
    before = deepcopy(instance.to_dict())
    with pytest.raises(RuntimeProbeError, match="too large"):
        runtime.apply_event_batch(instance, {"batch_id": "new", "hp": 3})
    assert instance.to_dict() == before


def test_empty_round_marker_keeps_ambiguous_receipts_fail_closed():
    instance = instance_with_sheet()
    entry = ruleset_runtime.round_entry_snapshot(instance)
    runtime = adapter(sheet_transport([]))
    batch = {"batch_id": "b1", "hp": 9}
    runtime.apply_event_batch(instance, batch)
    assert ruleset_runtime.restore_round_entry(instance, entry) is False
    instance.set_character_sheet("alice", {"hp": 10})
    assert runtime.apply_event_batch(instance, batch)["applied"] is False
    assert instance.get_character_sheet("alice") == {"hp": 10}
