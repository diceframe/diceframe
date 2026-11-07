
from legacy_save_fixtures import legacy_table_payload
from src.engine.modules import checks
"""Player consent, Keeper risk and server-only re-rolls for CoC Push."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.rules.rule_system import RuleSystem

ROOT = Path(__file__).resolve().parents[1]


def game():
    inst = GameInstance(("web", "push", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.gm_uid = "gm"
    inst.players = {"a": {"character_name": "A", "character_sheet": {"hp": 10, "luck": 50, "skills": [{"name": "Investigate", "value": 40}]}},
                    "b": {"character_sheet": {"luck": 50}}}
    checks.replace_last_checks(inst, [{'check_id': 'c1', 'actor_uid': 'a', 'kind': 'check', 'skill': 'Investigate', 'dice': 'd100', 'dice_mechanic': 'coc_bonus_penalty', 'roll': 60, 'rolls': [60], 'threshold': 40, 'verdict': '失败', 'advantage_mode': '', 'luck_spend_available': True, 'luck_decision': 'pending'}])
    inst.complete_round_check_preparation()
    return inst


def test_llm_push_tag_cannot_roll_or_write_legacy_markers(monkeypatch):
    from src.commands.player_state_applier import PlayerStateApplier

    inst = game()
    inst.round_number = 2
    rng = Mock(side_effect=AssertionError("LLM cannot roll Push"))
    monkeypatch.setattr("src.commands.player_state_applier.resolve_sanity_check", rng)
    before = deepcopy(inst.players)
    PlayerStateApplier(Mock()).apply_players(inst, {"a": {"push_skill": "Investigate"}})
    assert inst.players == before
    rng.assert_not_called()


def prepared():
    from src.engine import check_decisions

    inst = game()
    check_decisions.prepare(inst, RuleSystem.load(ROOT / "templates/rules/freeform_coc.json"))
    return inst, check_decisions


def change(inst, module, uid, operation, **kwargs):
    push = checks.last_checks(inst)[0]["push"]
    return module.change(inst, "c1", uid, operation, run_id=inst.run_id,
                         round_number=inst.round_number, expected_revision=push["revision"], **kwargs)


def test_push_requires_owner_request_keeper_risk_and_owner_confirmation():
    inst, module = prepared()
    rng = Mock(return_value=23)
    assert module.pending(inst)
    before = deepcopy(inst.to_dict())
    with pytest.raises((ValueError, PermissionError)):
        change(inst, module, "b", "request", reason="new approach")
    with pytest.raises((ValueError, PermissionError)):
        change(inst, module, "a", "confirm", rng=rng)
    assert inst.to_dict() == before
    change(inst, module, "a", "request", reason="Use a lamp and search the seams")
    assert checks.last_checks(inst)[0]["luck_decision"] == "declined"
    with pytest.raises((ValueError, PermissionError)):
        change(inst, module, "a", "offer", consequence="Alarm the guards")
    change(inst, module, "gm", "offer", consequence="Alarm the guards")
    with pytest.raises((ValueError, PermissionError)):
        change(inst, module, "gm", "confirm", rng=rng)
    rng.assert_not_called()
    change(inst, module, "a", "confirm", rng=rng)
    assert checks.last_checks(inst)[0]["roll"] == 23
    assert checks.last_checks(inst)[0]["push"]["original"]["roll"] == 60
    assert checks.last_checks(inst)[0]["push"]["consequence"] == "Alarm the guards"
    assert not module.pending(inst)
    assert not checks.last_checks(inst)[0]["luck_spend_available"]
    assert rng.call_count == 1
    with pytest.raises((ValueError, PermissionError)):
        change(inst, module, "a", "confirm", rng=rng)
    assert rng.call_count == 1


@pytest.mark.parametrize("changes", [
    {"kind": "attack"}, {"kind": "sanity"}, {"attribute_key": "luck", "skill": ""},
    {"dice": "d20"}, {"opponent": "enemy:1"}, {"verdict": "普通成功"}, {"actor_ref": "companion:x"},
])
def test_unpushable_checks_never_become_blocking_decisions(changes):
    from src.engine import check_decisions

    inst = game()
    checks.last_checks(inst)[0].update(changes)
    check_decisions.prepare(inst, RuleSystem.load(ROOT / "templates/rules/freeform_coc.json"))
    assert "push" not in checks.last_checks(inst)[0]
    assert not check_decisions.pending(inst)


@pytest.mark.asyncio
async def test_luck_and_push_are_exclusive_and_expired_scope_cannot_roll():
    inst, module = prepared()
    assert (await inst.resolve_luck_decision("c1", "a", True))["ok"]
    assert checks.last_checks(inst)[0]["push"]["status"] == "declined"
    with pytest.raises((ValueError, PermissionError)):
        change(inst, module, "a", "request", reason="too late")
    inst, module = prepared()
    change(inst, module, "a", "request", reason="new approach")
    change(inst, module, "gm", "offer", consequence="Noise")
    inst.round_number += 1
    assert not module.pending(inst)
    with pytest.raises((ValueError, PermissionError)):
        change(inst, module, "a", "confirm", rng=Mock(side_effect=AssertionError("expired")))


def test_pushed_failure_keeps_explicit_consequence_and_never_doubles_hp():
    inst, module = prepared()
    change(inst, module, "a", "request", reason="new approach")
    change(inst, module, "gm", "offer", consequence="Lose the lead")
    before = deepcopy(inst.players)
    change(inst, module, "a", "confirm", rng=Mock(return_value=87))
    assert checks.last_checks(inst)[0]["verdict"] == "失败"
    assert checks.last_checks(inst)[0]["push"]["status"] == "resolved"
    assert checks.last_checks(inst)[0]["push"]["failed"] is True
    assert inst.players == before
    loaded = GameInstance.from_dict(inst.to_dict())
    assert checks.last_checks(loaded) == checks.last_checks(inst)


def test_unknown_push_schema_blocks_processing_and_migration_never_replays_checks():
    from src.migrations.instance import CURRENT_INSTANCE_SCHEMA_VERSION, migrate_game_state_payload
    from src.engine import check_decisions

    inst, _ = prepared()
    checks.last_checks(inst)[0]["push"]["schema_version"] = 99
    with pytest.raises(ValueError):
        check_decisions.pending(inst)
    payload = inst.to_dict()
    payload = legacy_table_payload(payload)
    payload["instance_schema_version"] = 37
    migrated = migrate_game_state_payload(payload)
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["modules"]["checks"] == payload["modules"]["checks"]
    assert migrate_game_state_payload(migrated) == migrated


@pytest.mark.asyncio
async def test_insufficient_luck_cannot_close_push_and_forced_advance_records_it():
    inst, module = prepared()
    inst.get_character_sheet("a")["luck"] = 1
    before = deepcopy(inst.to_dict())
    assert not (await inst.resolve_luck_decision("c1", "a", True))["ok"]
    assert inst.to_dict() == before
    result = await inst.decline_pending_luck()
    assert result and not module.pending(inst)
    assert checks.last_checks(inst)[0]["push"]["decision_reason"] == "gm_forced_advance"
