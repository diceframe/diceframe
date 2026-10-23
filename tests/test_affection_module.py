"""Affection module slot: bounded scores owned by the run (GAL track P2)."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.engine import module_hooks
from src.engine.game_instance import GameInstance, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import affection
from src.migrations.instance import CURRENT_INSTANCE_SCHEMA_VERSION, migrate_game_state_payload


def game():
    return GameInstance(("test", "affection"))


def test_fresh_game_has_an_empty_table_and_unknown_subjects_have_no_score():
    inst = game()
    assert inst.modules["affection"] == {"schema_version": 1, "scores": {}}
    assert affection.scores(inst) == {}
    assert affection.score(inst, "npc:heroine") is None


def test_adjust_starts_at_zero_clamps_to_range_and_reports_applied_change():
    inst = game()
    assert affection.adjust(inst, "npc:heroine", 30) == 30
    assert affection.adjust(inst, "npc:heroine", 90) == 70
    assert affection.score(inst, "npc:heroine") == affection.MAX_SCORE
    assert affection.adjust(inst, "npc:rival", -500) == affection.MIN_SCORE
    assert affection.scores(inst) == {"npc:heroine": 100, "npc:rival": -100}


@pytest.mark.parametrize("subject, delta", [
    ("", 1), ("bad id", 1), ("-leading", 1), (None, 1), ("x" * 121, 1),
    ("npc:heroine", 1.5), ("npc:heroine", True), ("npc:heroine", "3"),
])
def test_adjust_rejects_malformed_input_without_mutation(subject, delta):
    inst = game()
    before = deepcopy(inst.to_dict())
    with pytest.raises(ValueError):
        affection.adjust(inst, subject, delta)
    assert inst.to_dict() == before


def test_subject_count_is_bounded():
    inst = game()
    for index in range(affection.MAX_SUBJECTS):
        affection.adjust(inst, f"npc:{index}", 1)
    with pytest.raises(ValueError, match="too many"):
        affection.adjust(inst, "npc:one-more", 1)
    assert affection.adjust(inst, "npc:0", 1) == 1


def test_ensure_drops_malformed_entries_instead_of_guessing():
    slot = affection.ensure({"schema_version": 1, "scores": {
        "npc:ok": 5, "npc:float": 2.5, "npc:bool": True, "npc:high": 101, "bad id": 3,
    }, "_before_round": "damaged"})
    assert slot == {"schema_version": 1, "scores": {"npc:ok": 5}}
    assert affection.ensure("damaged") == affection.fresh()
    assert affection.ensure({"schema_version": 1, "scores": []})["scores"] == {}


def test_unknown_schema_is_preserved_and_refused():
    inst = game()
    inst.modules["affection"] = {"schema_version": 99, "opaque": {"npc:heroine": 999}}
    before = deepcopy(inst.modules["affection"])
    assert affection.score(inst, "npc:heroine") is None
    with pytest.raises(ModuleStateError):
        affection.scores(inst)
    with pytest.raises(ModuleStateError):
        affection.adjust(inst, "npc:heroine", 1)
    with pytest.raises(ModuleStateError):
        module_hooks.run(module_hooks.INSTANCE_RESET_PREFLIGHT, inst)
    with pytest.raises(ModuleStateError):
        module_hooks.run(module_hooks.ROUND_RECOVERY_PREFLIGHT, inst)
    assert inst.modules["affection"] == before


def test_round_hooks_snapshot_and_restore_scores():
    inst = game()
    affection.adjust(inst, "npc:heroine", 10)
    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, inst)
    affection.adjust(inst, "npc:heroine", 5)
    entry = {"round": 1}
    module_hooks.run(module_hooks.ROUND_COMMIT, inst, entry, None)
    assert entry["pre_affection"] == {"scores": {"npc:heroine": 10}}
    assert "_before_round" not in inst.modules["affection"]

    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, inst)
    affection.adjust(inst, "npc:heroine", 20)
    assert module_hooks.run_any(module_hooks.ROUND_ABORT, inst)
    assert affection.scores(inst) == {"npc:heroine": 15}

    module_hooks.run(module_hooks.ROUND_REWIND_RESTORE, inst, entry, 1, module_hooks.REWIND_SOURCE_SWIPE)
    assert affection.scores(inst) == {"npc:heroine": 10}


def test_round_that_started_empty_rewinds_to_empty():
    inst = game()
    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, inst)
    affection.adjust(inst, "npc:heroine", 7)
    entry = {"round": 1}
    module_hooks.run(module_hooks.ROUND_COMMIT, inst, entry, None)
    assert "pre_affection" not in entry
    module_hooks.run(module_hooks.ROUND_REWIND_RESTORE, inst, entry, 1, module_hooks.REWIND_SOURCE_ROLLBACK)
    assert affection.scores(inst) == {}


def test_reset_and_new_run_start_without_scores():
    inst = game()
    affection.adjust(inst, "npc:heroine", 40)
    candidate = game()
    module_hooks.run(module_hooks.INSTANCE_NEW_RUN, candidate, inst)
    assert affection.scores(candidate) == {}
    module_hooks.run(module_hooks.INSTANCE_RESET, inst)
    assert affection.scores(inst) == {}


@pytest.mark.asyncio
async def test_rollback_of_a_committed_round_restores_scores():
    inst = game()
    affection.adjust(inst, "npc:heroine", 10)
    inst.state = GameState.ACTIVE_JUDGMENT
    inst.begin_round_processing()
    affection.adjust(inst, "npc:heroine", 25)
    await inst.finish_judgment("The heroine smiles.")
    assert affection.score(inst, "npc:heroine") == 35
    assert inst.log[-1]["pre_affection"] == {"scores": {"npc:heroine": 10}}
    await inst.rollback_last_round()
    assert affection.score(inst, "npc:heroine") == 10


def test_scores_survive_save_and_restore():
    inst = game()
    affection.adjust(inst, "npc:heroine", 42)
    restored = GameInstance.from_dict(deepcopy(inst.to_dict()))
    assert affection.scores(restored) == {"npc:heroine": 42}


def test_schema_48_fences_old_readers_without_inventing_scores():
    old = deepcopy(game().to_dict())
    old["instance_schema_version"] = 47
    old["modules"].pop("affection", None)
    before = deepcopy(old)
    upgraded = migrate_game_state_payload(old)
    assert upgraded["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION >= 48
    assert "affection" not in upgraded["modules"]
    assert old == before
    assert migrate_game_state_payload(upgraded) == upgraded
    assert affection.scores(GameInstance.from_dict(upgraded)) == {}
    with patch("src.migrations.instance.CURRENT_INSTANCE_SCHEMA_VERSION", 47):
        with pytest.raises(ValueError, match="unsupported game instance schema"):
            migrate_game_state_payload(upgraded)
