"""Save upgrades and imported runs must retain one-use recovery authority."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.engine import module_hooks
from src.engine.game_instance import GameInstance, GameRegistry
from src.engine.module_state import ModuleStateError
from src.engine.modules import resource_recovery as recovery
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION, migrate_game_state_payload, rebind_imported_game_state_payload,
)
from tests.test_resource_recovery_domain import fixture, source


def test_schema_47_fences_old_readers_without_materializing_historical_recovery():
    instance, _ = fixture()
    old = deepcopy(instance.to_dict())
    old["instance_schema_version"] = 46
    old["modules"].pop("resource_recovery", None)
    before = deepcopy(old)
    upgraded = migrate_game_state_payload(old)
    assert upgraded["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION >= 47
    assert "resource_recovery" not in upgraded["modules"]
    assert upgraded["players"] == old["players"] and old == before
    assert migrate_game_state_payload(upgraded) == upgraded
    with patch("src.migrations.instance.CURRENT_INSTANCE_SCHEMA_VERSION", 46):
        with pytest.raises(ValueError, match="unsupported game instance schema"):
            migrate_game_state_payload(upgraded)


@pytest.mark.parametrize("slot", [None, [], "damaged", True, {"schema_version": 99},
    {**recovery.fresh(), "revision": True}, {**recovery.fresh(), "consumed": []}])
def test_decode_refuses_damaged_or_future_slot_before_defaults_can_erase_it(slot):
    instance, _ = fixture()
    payload = deepcopy(instance.to_dict())
    payload["modules"]["resource_recovery"] = slot
    before = deepcopy(payload)
    with pytest.raises(ValueError):
        GameInstance.from_dict(payload)
    assert payload == before


@pytest.mark.parametrize("approved", [False, True])
def test_import_cannot_transfer_pending_or_approved_gm_qualifications(approved):
    instance, rule = fixture()
    from src.rulesets.resource_recovery import policy
    item = recovery.new_source(instance, "reward", "alice", resource_key="sanity", resource_delta=3,
                               policy_snapshot=policy(rule))
    if approved:
        recovery.approve_source(instance, item["id"], "gm")
    payload = deepcopy(instance.to_dict())
    before = deepcopy(payload)
    with pytest.raises(ValueError, match="recovery"):
        rebind_imported_game_state_payload(payload, game_key=("web", "copy", "bot"), run_id="another-run")
    assert payload == before


def test_import_preserves_settled_and_rejected_audit_and_never_reissues_old_source():
    instance, rule = fixture()
    settled = source(instance, resource_key="sanity", resource_delta=3)
    recovery.settle(instance, rule, settled, "settled")
    rejected = source(instance, resource_key="luck", resource_delta=1)
    recovery.reject_source(instance, rejected, "gm")
    original = recovery.view(instance)
    payload = rebind_imported_game_state_payload(instance.to_dict(), game_key=("web", "copy", "bot"), run_id="another-run")
    restored = GameInstance.from_dict(payload)
    assert restored.run_id == "another-run"
    assert recovery.view(restored) == original
    assert restored.get_character_sheet("alice")["sanity"] == 43
    with pytest.raises(ValueError, match="another run"):
        recovery.settle(restored, rule, settled, "again")


@pytest.mark.asyncio
async def test_imported_history_cannot_reactivate_a_rejected_source_from_another_run():
    instance, _ = fixture()
    identity = source(instance, resource_key="sanity", resource_delta=3)
    old_pending = recovery.view(instance)
    recovery.reject_source(instance, identity, "gm")
    instance.log.append({"round": 0, "gm_response": "Public", "pre_resource_recovery": old_pending})
    payload = rebind_imported_game_state_payload(instance.to_dict(), game_key=("web", "copy", "bot"), run_id="another-run")
    restored = GameInstance.from_dict(payload)
    await restored.rollback_last_round()
    assert recovery.view(restored)["sources"][identity]["status"] == "rejected"
    assert GameInstance.from_dict(restored.to_dict()).run_id == "another-run"


@pytest.mark.asyncio
async def test_real_save_package_export_and_import_retains_consumption_without_rewards(tmp_path):
    from tests.test_game_packages import _service
    instance, rule = fixture()
    instance.game_key = ("web", "recovery-export", "bot")
    identity = source(instance, resource_key="sanity", resource_delta=3)
    receipt = recovery.settle(instance, rule, identity, "once")
    registry = GameRegistry(tmp_path / "saves")
    registry.register(instance)
    await registry.save(instance)
    packages, _ = _service(registry)
    exported = packages.export_game_package("|".join(instance.game_key))
    assert exported["ok"]
    imported = await packages.import_game_package(exported["payload"])
    assert imported["ok"]
    restored = registry.get(tuple(imported["game_key"].split("|")))
    assert restored.run_id != instance.run_id
    assert recovery.view(restored)["receipts"][identity] == receipt
    assert restored.get_character_sheet("alice")["sanity"] == 43
    assert recovery.view(restored)["consumed"] == recovery.view(instance)["consumed"]


@pytest.mark.asyncio
async def test_rollback_refuses_before_log_or_players_change_even_with_optional_hooks_detached():
    instance, rule = fixture()
    identity = source(instance, resource_key="sanity", resource_delta=3)
    recovery.settle(instance, rule, identity, "settled")
    instance.log.append({"round": 0, "gm_response": "Public", "pre_state_snapshot": {"alice": {"sanity": 40}}})
    before = deepcopy(instance.to_dict())
    removed = module_hooks.unregister_owner("resource_recovery")
    try:
        with pytest.raises(ValueError, match="recovery"):
            await instance.rollback_last_round()
        assert instance.to_dict() == before
    finally:
        for point, callback in removed.items():
            module_hooks.register(point, "resource_recovery", callback)


@pytest.mark.asyncio
async def test_swipe_generation_refuses_before_mutation_or_llm_call():
    from src.commands.swipe_generator import SwipeGenerator
    instance, rule = fixture()
    identity = source(instance, resource_key="sanity", resource_delta=3)
    recovery.settle(instance, rule, identity, "settled")
    instance.log.append({"round": 0, "gm_response": "Public", "pre_state_snapshot": {"alice": {"sanity": 40}}})
    before = deepcopy(instance.to_dict())
    generator = object.__new__(SwipeGenerator)
    with pytest.raises(ValueError, match="recovery"):
        await generator._generate_locked(instance, 0)
    assert instance.to_dict() == before


@pytest.mark.asyncio
async def test_reset_rotates_run_and_clears_qualifications_and_consumption():
    instance, rule = fixture()
    identity = source(instance, resource_key="sanity", resource_delta=3)
    recovery.settle(instance, rule, identity, "settled")
    previous_run = instance.run_id
    await instance.reset()
    assert instance.run_id != previous_run
    assert recovery.view(instance) == recovery.fresh()


def test_history_cannot_smuggle_a_future_recovery_snapshot_into_rewind():
    instance, _ = fixture()
    payload = deepcopy(instance.to_dict())
    payload["log"] = [{"round": 1, "gm_response": "Public", "pre_resource_recovery": {"schema_version": 99}}]
    with pytest.raises(ModuleStateError):
        GameInstance.from_dict(payload)


def test_unknown_recovery_authority_freezes_writers_even_with_optional_hooks_detached():
    instance, _ = fixture()
    instance.modules['resource_recovery'] = {'schema_version': 99}
    removed = module_hooks.unregister_owner('resource_recovery')
    try:
        with pytest.raises(ModuleStateError):
            instance.run_writable()
    finally:
        for point, callback in removed.items():
            module_hooks.register(point, 'resource_recovery', callback)
