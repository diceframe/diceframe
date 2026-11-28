"""combat-02: 2D forced movement, automatic grid pathing and scene write-back.

Every assertion drives the real authoritative chain.  The write-back cases use
the real ``GameInstance`` tactical scene slot and the real placeable-update
operation; a scene changed during combat must never be overwritten.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine.modules import ruleset_runtime, tactical_scene
from src.rules.rule_system import RuleSystem
from src.rulesets.dnd2024.combat import spatial
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import EventBatchError
from src.rulesets.registry import RulesetRuntimeRegistry
from src.tactical_scene.codec import decode_tactical_scene_document
from src.webui.services import ruleset_gameplay

from dnd2024_monk_common import SequenceRng, goblin, monk_draft
from test_dnd2024_grid_combat import (
    BOARD, ENEMY, PLAYER, _combat, _instance, _move_ok, _start,
)
from test_dnd2024_misty_step import wizard_sheet

RUNTIME = Dnd2024Runtime()


@pytest.fixture(scope="module")
def sheet() -> dict:
    return RUNTIME.finalize_character(None, monk_draft(RUNTIME))


def _apply_events(engine, instance, events: list[dict], *, intent_type: str = "test.combat02"):
    version = int(ruleset_runtime.state(instance)["version"])
    batch = {
        "batch_id": f"batch_combat02_{version}",
        "intent_id": f"combat02:{version}",
        "intent_type": intent_type,
        "expected_version": version,
        "result_version": version + 1,
        "events": events,
    }
    return engine.apply_batch(instance, batch)


# --- 2D forced movement ----------------------------------------------------


def test_grid_push_lands_diagonally_away_from_the_attacker(sheet) -> None:
    engine, instance, _ = _start(
        sheet, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 5, "y": 5}},
    )
    before = _combat(instance)

    applied = _apply_events(engine, instance, [{
        "type": "dnd2024.position.changed", "actor_id": ENEMY,
        "feet": 10, "away_from": PLAYER, "distance": 10, "forced": True,
        "source_rider": "push",
    }])

    assert applied["applied"] is True
    combat = _combat(instance)
    assert combat["spatial_position"][ENEMY] == {
        "x": 7, "y": 7, "footprint": {"w": 1, "h": 1},
    }
    # Forced movement never spends movement and never moves the theater field.
    assert combat["economy"] == before["economy"]
    assert combat["positions"] == before["positions"]


def test_grid_push_stops_at_wall_and_occupied_cells() -> None:
    board = spatial.normalize_board(BOARD)
    wall = {"x0": 6.0, "y0": 0.0, "x1": 6.0, "y1": 9.0}
    walled = {
        "board": spatial.normalize_board({**BOARD, "walls": [wall]}),
        "spatial_position": {
            PLAYER: {"x": 1, "y": 1, "footprint": {"w": 1, "h": 1}},
            ENEMY: {"x": 5, "y": 5, "footprint": {"w": 1, "h": 1}},
        },
    }
    assert spatial.forced_target_cell(
        walled, ENEMY, {"feet": 10, "away_from": PLAYER, "forced": True},
    ) == {"x": 5, "y": 5}

    crowded = {
        "board": board,
        "spatial_position": {
            PLAYER: {"x": 1, "y": 1, "footprint": {"w": 1, "h": 1}},
            ENEMY: {"x": 5, "y": 5, "footprint": {"w": 1, "h": 1}},
            "companion:wall": {"x": 6, "y": 6, "footprint": {"w": 1, "h": 1}},
        },
    }
    # The second step is free, but a push never tunnels through an occupied cell.
    assert spatial.forced_target_cell(
        crowded, ENEMY, {"feet": 10, "away_from": PLAYER, "forced": True},
    ) == {"x": 5, "y": 5}


def test_theater_forced_move_keeps_its_one_dimensional_result(sheet) -> None:
    engine, instance, _ = _start(sheet, board=False)
    applied = _apply_events(engine, instance, [{
        "type": "dnd2024.position.changed", "actor_id": ENEMY,
        "feet": 10, "away_from": PLAYER, "distance": 10, "forced": True,
    }])
    assert applied["applied"] is True
    combat = _combat(instance)
    assert combat["positions"][ENEMY] == 15
    assert "spatial_position" not in combat


def test_old_grid_forced_event_without_a_cell_fails_closed(sheet) -> None:
    engine, instance, _ = _start(sheet)
    version = int(ruleset_runtime.state(instance)["version"])
    with pytest.raises(EventBatchError, match="GRID_MOVE_SHAPE"):
        engine.apply_batch(instance, {
            "batch_id": f"batch_legacy_{version}",
            "intent_id": f"legacy:{version}",
            "intent_type": "test.legacy",
            "expected_version": version,
            "result_version": version + 1,
            "events": [{
                "type": "dnd2024.position.changed", "actor_id": ENEMY,
                "distance": 10, "forced": True,
            }],
        })
    assert _combat(instance)["spatial_position"][ENEMY] == {
        "x": 5, "y": 5, "footprint": {"w": 1, "h": 1},
    }


# --- teleport --------------------------------------------------------------


def _misty_step_intent(instance, **extra) -> dict:
    version = int(ruleset_runtime.state(instance)["version"])
    return {
        "intent_id": f"misty:{version}", "type": "cast_spell",
        "expected_version": version, "submitted_by": "gm", "actor_id": PLAYER,
        "spell_ref": "spell:misty_step", "slot_level": 2, "target_ids": [PLAYER],
        **extra,
    }


def test_grid_teleport_uses_the_target_cell_without_economy_or_opportunity(
) -> None:
    wizard = wizard_sheet(RUNTIME, level=3)
    engine, instance, _ = _start(
        wizard, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 5, "y": 5}},
    )
    before = deepcopy(_combat(instance)["economy"])

    resolved = engine.resolve_intent(
        instance, _misty_step_intent(instance, target_cell={"x": 2, "y": 2}),
        SequenceRng([]),
    )
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    events = resolved["event_batch"]["events"]
    moved = next(
        event for event in events if event["type"] == "dnd2024.position.changed"
    )
    assert moved["target_cell"] == {"x": 2, "y": 2}
    assert moved["forced"] is True
    assert "distance" not in moved
    assert "movement_cost" not in moved
    assert not any(event["type"] == "dnd2024.decision.pending" for event in events)

    combat = _combat(instance)
    assert combat["spatial_position"][PLAYER] == {
        "x": 2, "y": 2, "footprint": {"w": 1, "h": 1},
    }
    assert combat["economy"]["movement"] == before["movement"]
    # Grid teleport does not rewrite the theater coordinate either.
    assert combat["positions"] == {PLAYER: 0, ENEMY: 5}


def test_grid_teleport_validation_rejects_missing_out_of_range_and_occupied() -> None:
    wizard = wizard_sheet(RUNTIME, level=3)
    engine, instance, _ = _start(
        wizard, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 5, "y": 5}},
    )
    missing = engine.resolve_intent(instance, _misty_step_intent(instance), SequenceRng([]))
    assert missing["ok"] is False
    assert "GRID_MOVE_TARGET_REQUIRED" in missing["error"]

    far = engine.resolve_intent(
        instance, _misty_step_intent(instance, target_cell={"x": 9, "y": 1}),
        SequenceRng([]),
    )
    assert far["ok"] is False
    assert "GRID_TELEPORT_OUT_OF_RANGE" in far["error"]

    occupied = engine.resolve_intent(
        instance, _misty_step_intent(instance, target_cell={"x": 5, "y": 5}),
        SequenceRng([]),
    )
    assert occupied["ok"] is False
    assert "GRID_TELEPORT_CELL_OCCUPIED" in occupied["error"]


# --- automatic 2D movement -------------------------------------------------


def test_automatic_grid_pathing_respects_occupancy_and_walls() -> None:
    board = spatial.normalize_board(BOARD)
    combat = {
        "board": board,
        "spatial_position": {
            ENEMY: {"x": 5, "y": 5, "footprint": {"w": 1, "h": 1}},
            PLAYER: {"x": 1, "y": 1, "footprint": {"w": 1, "h": 1}},
            "companion:block": {"x": 4, "y": 5, "footprint": {"w": 1, "h": 1}},
        },
    }
    cell = spatial.automatic_move_target(combat, ENEMY, PLAYER, budget_ft=20)
    assert cell is not None
    assert spatial.chebyshev(cell, combat["spatial_position"][PLAYER]) < 4
    assert cell != {"x": 4, "y": 5}

    walled = deepcopy(combat)
    walled["board"] = spatial.normalize_board(
        {**BOARD, "walls": [{"x0": 3.0, "y0": 0.0, "x1": 3.0, "y1": 9.0}]},
    )
    blocked = spatial.automatic_move_target(walled, ENEMY, PLAYER, budget_ft=20)
    # Greedy steps never cross the wall: they stop on the enemy's side of it.
    assert blocked is None or blocked["x"] >= 3

    assert spatial.automatic_move_target(combat, ENEMY, PLAYER, budget_ft=0) is None


def test_guard_still_refuses_a_theater_shaped_grid_move() -> None:
    combat = {"position_mode": "grid"}
    guarded = spatial.guard_automatic_intent(
        {"type": "move", "distance": 15, "intent_id": "auto:x"}, combat,
    )
    assert guarded["type"] == "end_turn"
    assert "distance" not in guarded

    kept = spatial.guard_automatic_intent(
        {"type": "move", "target_cell": {"x": 2, "y": 2}, "intent_id": "auto:y"},
        combat,
    )
    assert kept["type"] == "move"


# --- event replay ----------------------------------------------------------


def test_forced_move_replays_to_the_same_spatial_state(sheet) -> None:
    engine, instance, start_batch = _start(
        sheet, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 5, "y": 5}},
    )
    version = int(ruleset_runtime.state(instance)["version"])
    push = {
        "batch_id": f"batch_push_{version}",
        "intent_id": f"push:{version}",
        "intent_type": "test.push",
        "expected_version": version,
        "result_version": version + 1,
        "events": [{
            "type": "dnd2024.position.changed", "actor_id": ENEMY,
            "feet": 5, "away_from": PLAYER, "distance": 5, "forced": True,
        }],
    }
    assert engine.apply_batch(instance, deepcopy(push))["applied"] is True
    expected = _combat(instance)

    replay_engine, replay_instance = _instance(sheet)
    for batch in (start_batch, push):
        assert replay_engine.apply_batch(replay_instance, deepcopy(batch))["applied"] is True
    replayed = _combat(replay_instance)
    assert replayed["spatial_position"] == expected["spatial_position"]
    assert replayed["positions"] == expected["positions"]


# --- leaving combat: write final positions back to the scene ----------------


def _scene_payload() -> dict:
    def _actor(kind: str, actor_id: str) -> dict:
        return {
            "source_kind": "diceframe", "source_id": "test",
            "kind": kind, "id": actor_id, "digest": "",
        }

    return {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": "scene:chapel",
        "display_name": "Chapel",
        "ownership": {"default": 0, "gm": 3},
        "dimensions": {"width": 1200, "height": 900},
        "grid": {"type": "square", "size": 100, "distance": 5.0, "units": "ft"},
        "placeables": {
            "tokens": {
                "token_hero": {
                    "schema_version": 1, "kind": "token", "canonical_id": "token_hero",
                    "actor_ref": _actor("player", "gm"),
                    "position": {"x": 100.0, "y": 100.0},
                    "size": {"width": 1.0, "height": 1.0},
                },
                "token_goblin": {
                    "schema_version": 1, "kind": "token", "canonical_id": "token_goblin",
                    "actor_ref": _actor("enemy", "goblin-1"),
                    "position": {"x": 500.0, "y": 500.0},
                    "size": {"width": 1.0, "height": 1.0},
                },
            },
        },
    }


async def _noop_save(instance) -> None:
    return None


def _fill_scene(instance) -> None:
    tactical_scene.apply_op(
        instance, {"kind": "tactical_scene_create", "scene": _scene_payload()},
        expected_revision=0,
    )
    tactical_scene.apply_op(
        instance,
        {"kind": "tactical_scene_set_active", "scene_id": "scene:chapel"},
        expected_revision=1,
    )


def _scene_and_combat(sheet) -> tuple[object, object]:
    engine, instance = _instance(sheet)
    _fill_scene(instance)
    board, positions = ruleset_gameplay._scene_grid_start(instance)
    assert board["scene_revision"] == tactical_scene.revision(instance)
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(position=5)],
        "player_positions": {"gm": 0}, "board": board,
        "spatial_positions": positions,
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return engine, instance


def _end_combat(engine, instance) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    resolved = engine.resolve_intent(instance, {
        "intent_id": "end-combat", "type": "combat.end",
        "expected_version": version, "submitted_by": "gm",
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True


def _write_back(instance) -> dict | None:
    return asyncio.run(ruleset_gameplay.write_back_combat_positions(
        instance,
        get_instance=lambda key: instance,
        save_instance=_noop_save,
        requester_is_gm=True,
    ))


def _token(instance, token_id: str) -> dict:
    document = decode_tactical_scene_document(
        tactical_scene.scenes(instance)["scene:chapel"],
    )
    return document["placeables"]["tokens"][token_id]


def test_leaving_combat_writes_final_positions_back(sheet) -> None:
    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    _end_combat(engine, instance)

    sync = _write_back(instance)
    assert sync is not None and sync["ok"] is True, sync
    assert sync["scene_id"] == "scene:chapel"
    assert sync["tokens"] == ["token_hero"]
    assert _token(instance, "token_hero")["position"] == {
        "x": 400.0, "y": 100.0, "elevation": 0.0, "level": 0,
    }
    # Every write went through the placeable-update revision fence.
    assert tactical_scene.revision(instance) >= 3


def test_leaving_combat_does_not_overwrite_a_changed_scene(sheet) -> None:
    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    # Someone else edits the scene while the fight is running.
    current = tactical_scene.revision(instance)
    moved = deepcopy(_token(instance, "token_goblin"))
    moved["position"] = {**moved["position"], "x": 800.0, "y": 800.0}
    tactical_scene.apply_op(instance, {
        "kind": "tactical_scene_placeable_update", "scene_id": "scene:chapel",
        "collection": "tokens", "placeable_id": "token_goblin", "placeable": moved,
    }, expected_revision=current)

    _end_combat(engine, instance)
    sync = _write_back(instance)

    assert sync is not None and sync["ok"] is False
    assert sync["code"] == "tactical_scene_stale_revision"
    # The conflicting scene edit is preserved, never silently overwritten.
    assert _token(instance, "token_hero")["position"]["x"] == 100.0
    assert _token(instance, "token_goblin")["position"]["x"] == 800.0


def test_locked_writeback_hook_never_reacquires_the_instance_lock(sheet) -> None:
    """The submit path already holds the lock; the hook must run inside it."""

    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    _end_combat(engine, instance)
    dependencies = SimpleNamespace(save_instance=_noop_save)
    result: dict = {}

    async def _run() -> None:
        async with instance._lock:
            await asyncio.wait_for(
                ruleset_gameplay._sync_scene_after_combat_end_locked(
                    dependencies, instance,
                    {"type": "combat.end", "submitted_by": "gm"}, result, False,
                ),
                timeout=5,
            )

    asyncio.run(_run())
    assert result["scene_sync"]["ok"] is True, result
    assert _token(instance, "token_hero")["position"]["x"] == 400.0


def test_write_back_is_a_noop_outside_grid_combat(sheet) -> None:
    engine, instance, _ = _start(sheet, board=False)
    _end_combat(engine, instance)
    assert _write_back(instance) is None

    grid_engine, grid_instance = _scene_and_combat(sheet)
    assert _write_back(grid_instance) is None  # combat is still active


# --- leaving combat by victory / party_defeated (combat-02b) ----------------


def _victory(engine, instance) -> list[dict]:
    """Close the fight through the real resolution: the last enemy is dead."""

    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] = 0
    version = int(ruleset_runtime.state(instance)["version"])
    resolved = engine.resolve_intent(instance, {
        "intent_id": "victory-end", "type": "end_turn",
        "expected_version": version, "submitted_by": "gm", "actor_id": PLAYER,
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert _combat(instance)["status"] == "ended"
    return resolved["event_batch"]["events"]


def _gameplay_dependencies(instance, broadcast=None):
    rule = RuleSystem({
        "rule_id": "dnd2024_srd",
        "runtime": {"id": "core:dnd2024", "minimum_version": 1},
    })
    return ruleset_gameplay.RulesetGameplayDependencies(
        get_instance=lambda _key: instance,
        parse_game_key=lambda _key: (),
        load_rule_for_game=lambda _instance: rule,
        ruleset_registry=RulesetRuntimeRegistry([RUNTIME]),
        resolve_adventure_binding=lambda *_args, **_kwargs: {},
        save_instance=_noop_save,
        apply_memory_delta=None,
        resolve_broadcast=(lambda: broadcast) if broadcast is not None else None,
        rng_factory=lambda: SequenceRng([]),
    )


def _sync_locked(instance, intent, requester_is_gm, *, combat_was_active=True):
    dependencies = SimpleNamespace(save_instance=_noop_save)
    result: dict = {}

    async def _run() -> dict | None:
        async with instance._lock:
            return await ruleset_gameplay._sync_scene_after_combat_end_locked(
                dependencies, instance, intent, result, requester_is_gm,
                combat_was_active=combat_was_active,
            )

    return result, asyncio.run(_run())


class _BroadcastRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, game_key, data, exclude_uid="", private_uid="") -> None:
        self.calls.append({
            "game_key": game_key, "data": data,
            "exclude_uid": exclude_uid, "private_uid": private_uid,
        })


def test_victory_auto_end_writes_back_and_broadcasts(sheet) -> None:
    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] = 0
    recorder = _BroadcastRecorder()
    version = int(ruleset_runtime.state(instance)["version"])

    response = asyncio.run(ruleset_gameplay.submit_intent(
        _gameplay_dependencies(instance, broadcast=recorder),
        "test|dnd2024-monk|bot", "gm", True,
        {"intent_id": "victory-end", "type": "end_turn", "expected_version": version,
         "actor_id": PLAYER},
    ))

    assert response["ok"] is True, response
    assert _combat(instance)["status"] == "ended"
    sync = response["result"]["scene_sync"]
    assert sync["ok"] is True and sync["tokens"] == ["token_hero"]
    assert _token(instance, "token_hero")["position"]["x"] == 400.0

    assert [call["data"]["visibility"] for call in recorder.calls] == ["seat", "gm"]
    seat, gm = recorder.calls
    assert seat["game_key"] == "test|dnd2024-monk|bot"
    assert seat["exclude_uid"] == "gm" and seat["private_uid"] == ""
    assert gm["private_uid"] == "gm" and gm["exclude_uid"] == ""
    assert seat["data"]["event_type"] == "tactical_scene_updated"
    assert seat["data"]["scene_id"] == "scene:chapel"
    assert seat["data"]["source_operation"] == "tactical_scene_placeable_update"
    assert seat["data"]["revision"] == gm["data"]["revision"] == sync["revision"]
    # Owner/GM-only fields stay out of the seat projection.
    seat_scene = seat["data"]["projection"]
    gm_scene = gm["data"]["projection"]
    assert "ownership" not in seat_scene and "ownership" in gm_scene
    hero = seat_scene["placeables"]["tokens"]["token_hero"]
    assert hero["position"] == {"x": 400.0, "y": 100.0, "elevation": 0.0, "level": 0}
    gm_hero = gm_scene["placeables"]["tokens"]["token_hero"]
    assert gm_hero["actor_ref"]["id"] == "gm"


def test_automatic_victory_writes_back_without_a_gm_author(sheet) -> None:
    """A seat's killing blow releases the scene; the system authors the write."""

    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    _victory(engine, instance)

    result, event = _sync_locked(instance, {"type": "end_turn"}, False)

    assert result["scene_sync"]["ok"] is True, result
    assert event is not None
    assert _token(instance, "token_hero")["position"]["x"] == 400.0


def test_party_defeated_auto_end_writes_back(sheet) -> None:
    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    applied = _apply_events(engine, instance, [
        {"type": "dnd2024.combat.ended", "reason": "party_defeated"},
    ])
    assert applied["applied"] is True
    assert _combat(instance)["status"] == "ended"

    result, _ = _sync_locked(instance, {"type": "end_turn"}, True)

    assert result["scene_sync"]["ok"] is True, result
    assert _token(instance, "token_hero")["position"]["x"] == 400.0


def test_victory_does_not_overwrite_a_scene_changed_during_combat(sheet) -> None:
    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    _victory(engine, instance)
    current = tactical_scene.revision(instance)
    moved = deepcopy(_token(instance, "token_goblin"))
    moved["position"] = {**moved["position"], "x": 800.0, "y": 800.0}
    tactical_scene.apply_op(instance, {
        "kind": "tactical_scene_placeable_update", "scene_id": "scene:chapel",
        "collection": "tokens", "placeable_id": "token_goblin", "placeable": moved,
    }, expected_revision=current)

    result, event = _sync_locked(instance, {"type": "end_turn"}, True)

    assert result["scene_sync"]["ok"] is False
    assert result["scene_sync"]["code"] == "tactical_scene_stale_revision"
    assert event is None
    assert _token(instance, "token_hero")["position"]["x"] == 100.0
    assert _token(instance, "token_goblin")["position"]["x"] == 800.0


def test_theater_victory_never_writes_back(sheet) -> None:
    engine, instance, _ = _start(sheet, board=False)
    applied = _apply_events(engine, instance, [
        {"type": "dnd2024.combat.ended", "reason": "victory"},
    ])
    assert applied["applied"] is True
    assert _combat(instance)["status"] == "ended"

    result, event = _sync_locked(instance, {"type": "end_turn"}, True)

    assert "scene_sync" not in result
    assert event is None


def test_legacy_grid_combat_without_a_scene_revision_fails_closed(sheet) -> None:
    engine, instance = _scene_and_combat(sheet)
    _move_ok(engine, instance, {"x": 4, "y": 1})
    _victory(engine, instance)
    _combat(instance)["board"].pop("scene_revision")

    result, event = _sync_locked(instance, {"type": "end_turn"}, True)

    assert result["scene_sync"]["ok"] is False
    assert result["scene_sync"]["code"] == "tactical_scene_revision_unknown"
    assert event is None
    assert _token(instance, "token_hero")["position"]["x"] == 100.0