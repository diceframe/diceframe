"""Scene slot round snapshots: commit / abort / rollback / swipe (workplan §6.13)."""

from copy import deepcopy

import pytest

from src.engine import module_hooks
from src.engine.game_instance import GameInstance, GameState
from src.engine.module_state import ModuleStateError
from src.engine.modules import tactical_scene


def game() -> GameInstance:
    return GameInstance(("test", "tactical-scene-rollback"))


def _scene(scene_id: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": scene_id,
        "display_name": scene_id,
        "revision": 0,
    }


def _slot(scene_ids: tuple[str, ...], active: str | None, revision: int) -> dict:
    return {
        "schema_version": 1,
        "scenes": {scene_id: _scene(scene_id) for scene_id in scene_ids},
        "active_scene_ref": active,
        "revision": revision,
    }


def _write(instance: GameInstance, scene_ids: tuple[str, ...], active: str | None, revision: int) -> None:
    # A GM write copies the whole live slot (apply_tactical_scene_op deep-copies
    # the draft), so the judgment-entry capture inside it survives the edit.
    slot = tactical_scene.slot(instance)
    slot["scenes"] = {scene_id: _scene(scene_id) for scene_id in scene_ids}
    slot["active_scene_ref"] = active
    slot["revision"] = revision


def test_commit_records_the_judgment_entry_scene_and_rollback_restores_it():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, instance)
    # A GM edit after judgment entry must be undone with the round.
    _write(instance, ("scene_a", "scene_b"), "scene_b", 2)

    entry: dict = {"round": 1}
    module_hooks.run(module_hooks.ROUND_COMMIT, instance, entry, None)

    assert entry[tactical_scene.LOG_FIELD] == _slot(("scene_a",), "scene_a", 1)
    assert "_before_round" not in tactical_scene.slot(instance)
    # Live play keeps the committed (post-judgment) scene.
    assert tactical_scene.active_scene_ref(instance) == "scene_b"

    # The entry snapshot is a deep copy, not a view of the live slot.
    tactical_scene.scenes(instance)["scene_a"]["display_name"] = "tampered"
    assert entry[tactical_scene.LOG_FIELD]["scenes"]["scene_a"]["display_name"] == "scene_a"

    _write(instance, ("scene_a", "scene_b", "scene_c"), "scene_c", 3)
    module_hooks.run(
        module_hooks.ROUND_REWIND_RESTORE, instance, entry, 1,
        module_hooks.REWIND_SOURCE_ROLLBACK,
    )
    assert tactical_scene.slot(instance) == _slot(("scene_a",), "scene_a", 1)


def test_swipe_rewind_restores_the_entry_snapshot_and_drops_the_live_capture():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, instance)
    _write(instance, ("scene_a", "scene_b"), "scene_b", 2)
    entry: dict = {"round": 1}
    module_hooks.run(module_hooks.ROUND_COMMIT, instance, entry, None)

    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, instance)
    _write(instance, ("scene_x",), "scene_x", 9)
    module_hooks.run(module_hooks.ROUND_REWIND_BEGIN, instance, 1)
    assert "_before_round" not in tactical_scene.slot(instance)

    module_hooks.run(
        module_hooks.ROUND_REWIND_RESTORE, instance, entry, 1,
        module_hooks.REWIND_SOURCE_SWIPE,
    )
    assert tactical_scene.slot(instance) == _slot(("scene_a",), "scene_a", 1)


def test_abort_restores_the_scene_as_of_judgment_entry():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, instance)
    _write(instance, ("scene_a", "scene_b"), "scene_b", 2)

    assert module_hooks.run_any(module_hooks.ROUND_ABORT, instance) is True
    assert tactical_scene.slot(instance) == _slot(("scene_a",), "scene_a", 1)
    assert "_before_round" not in tactical_scene.slot(instance)


def test_abort_without_a_captured_entry_keeps_the_live_scene():
    instance = game()
    _write(instance, ("scene_a", "scene_b"), "scene_b", 2)

    assert module_hooks.run_any(module_hooks.ROUND_ABORT, instance) is False
    assert tactical_scene.slot(instance) == _slot(("scene_a", "scene_b"), "scene_b", 2)


def test_fresh_empty_round_commit_records_no_scene_field():
    instance = game()
    entry: dict = {"state_changes": []}

    module_hooks.run(module_hooks.ROUND_COMMIT, instance, entry, None)

    assert tactical_scene.LOG_FIELD not in entry


def test_commit_without_a_judgment_entry_capture_records_the_live_slot():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    entry: dict = {"round": 1}

    module_hooks.run(module_hooks.ROUND_COMMIT, instance, entry, None)

    assert entry[tactical_scene.LOG_FIELD] == _slot(("scene_a",), "scene_a", 1)


def test_unknown_slot_schema_preflight_fails_closed_without_mutation():
    instance = game()
    instance.modules[tactical_scene.MODULE_NAME] = {"schema_version": 2, "scenes": {"future": {}}}
    before = deepcopy(instance.modules[tactical_scene.MODULE_NAME])

    with pytest.raises(ModuleStateError):
        module_hooks.run(module_hooks.ROUND_RECOVERY_PREFLIGHT, instance)
    with pytest.raises(ModuleStateError):
        module_hooks.run(module_hooks.INSTANCE_RESET_PREFLIGHT, instance)
    with pytest.raises(ModuleStateError):
        tactical_scene.active_scene_ref(instance)

    assert instance.modules[tactical_scene.MODULE_NAME] == before


def test_rewind_without_an_entry_snapshot_keeps_the_live_scene():
    instance = game()
    _write(instance, ("scene_a", "scene_b"), "scene_b", 2)
    entry = {"round": 1}  # historical log written before scene snapshots existed

    for source in (module_hooks.REWIND_SOURCE_ROLLBACK, module_hooks.REWIND_SOURCE_SWIPE):
        module_hooks.run(module_hooks.ROUND_REWIND_RESTORE, instance, entry, 1, source)
        assert tactical_scene.slot(instance) == _slot(("scene_a", "scene_b"), "scene_b", 2)


def test_rewind_refuses_an_unknown_entry_snapshot_schema():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    entry = {
        "round": 1,
        tactical_scene.LOG_FIELD: {"schema_version": 2, "scenes": {"future": {}}},
    }

    module_hooks.run(
        module_hooks.ROUND_REWIND_RESTORE, instance, entry, 1,
        module_hooks.REWIND_SOURCE_ROLLBACK,
    )
    assert tactical_scene.slot(instance) == _slot(("scene_a",), "scene_a", 1)


def test_instance_reset_starts_a_fresh_scene_workspace_and_new_runs_carry_none():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 4)

    candidate = game()
    module_hooks.run(module_hooks.INSTANCE_NEW_RUN, candidate, instance)
    assert tactical_scene.slot(candidate) == tactical_scene.fresh()

    module_hooks.run(module_hooks.INSTANCE_RESET_PREFLIGHT, instance)
    module_hooks.run(module_hooks.INSTANCE_RESET, instance)
    assert tactical_scene.slot(instance) == tactical_scene.fresh()


def test_restore_goes_through_the_owning_module_set_module_state(monkeypatch):
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    module_hooks.run(module_hooks.ROUND_PROCESSING_BEGIN, instance)
    _write(instance, ("scene_b",), "scene_b", 2)
    entry: dict = {"round": 1}
    module_hooks.run(module_hooks.ROUND_COMMIT, instance, entry, None)
    _write(instance, ("scene_c",), "scene_c", 3)

    recorded: list[tuple[str, dict]] = []
    real = tactical_scene.set_module_state

    def spy(target, name, value):
        recorded.append((name, deepcopy(value)))
        return real(target, name, value)

    monkeypatch.setattr(tactical_scene, "set_module_state", spy)

    module_hooks.run(
        module_hooks.ROUND_REWIND_RESTORE, instance, entry, 1,
        module_hooks.REWIND_SOURCE_ROLLBACK,
    )
    assert [name for name, _ in recorded] == [tactical_scene.MODULE_NAME]
    assert recorded[0][1] == _slot(("scene_a",), "scene_a", 1)
    assert tactical_scene.active_scene_ref(instance) == "scene_a"

    module_hooks.run(module_hooks.INSTANCE_RESET_PREFLIGHT, instance)
    module_hooks.run(module_hooks.INSTANCE_RESET, instance)
    assert tactical_scene.slot(instance) == tactical_scene.fresh()
    assert [name for name, _ in recorded[-1:]] == [tactical_scene.MODULE_NAME]


@pytest.mark.asyncio
async def test_rollback_of_a_committed_round_restores_the_scene_slot():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.begin_round_processing()
    _write(instance, ("scene_a", "scene_b"), "scene_b", 2)

    await instance.finish_judgment("The doors open.")
    assert instance.log[-1][tactical_scene.LOG_FIELD] == _slot(("scene_a",), "scene_a", 1)
    assert tactical_scene.active_scene_ref(instance) == "scene_b"

    await instance.rollback_last_round()
    assert tactical_scene.slot(instance) == _slot(("scene_a",), "scene_a", 1)


@pytest.mark.asyncio
async def test_failed_judgment_abort_restores_the_scene_slot():
    instance = game()
    _write(instance, ("scene_a",), "scene_a", 1)
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.begin_round_processing()
    _write(instance, ("scene_a", "scene_b"), "scene_b", 2)

    assert await instance.abort_round_processing() is True
    assert tactical_scene.slot(instance) == _slot(("scene_a",), "scene_a", 1)