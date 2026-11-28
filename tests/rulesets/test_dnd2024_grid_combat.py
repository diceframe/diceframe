"""combat-01: the square-grid vertical slice.

Every assertion drives the real authoritative chain (``resolve_intent`` ->
``apply_batch``), never a hand-written combat state.  Theater tests guard the
promise that the additive grid contract does not move old saves.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import spatial
from src.rulesets.dnd2024.combat.primitives import CombatIntentError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, goblin, monk_draft, monk_instance

RUNTIME = Dnd2024Runtime()
PLAYER = "player:gm"
ENEMY = "enemy:goblin-1"
BOARD = {
    "id": "scene:chapel",
    "width": 12,
    "height": 9,
    "cell_size": 5,
    "distance_metric": "dnd2024",
    "walls": [],
}


@pytest.fixture(scope="module")
def sheet() -> dict:
    return RUNTIME.finalize_character(None, monk_draft(RUNTIME))


def _instance(sheet: dict):
    return monk_instance(RUNTIME, deepcopy(sheet), uid="gm")


def _start(
    sheet: dict, *, positions: dict | None = None, walls: list | None = None,
    enemies: list | None = None, board: bool = True, rolls: tuple = (20, 1),
):
    engine, instance = _instance(sheet)
    intent = {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm",
        "enemies": enemies if enemies is not None else [goblin(position=5)],
        "player_positions": {"gm": 0},
    }
    if board:
        intent["board"] = {**deepcopy(BOARD), "walls": list(walls or [])}
        intent["spatial_positions"] = deepcopy(
            positions
            if positions is not None
            else {PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 5, "y": 5}}
        )
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return engine, instance, resolved["event_batch"]


def _combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _move(engine, instance, target: dict | None, *, actor: str = PLAYER):
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": f"move-{version}", "type": "move", "expected_version": version,
        "submitted_by": "gm", "actor_id": actor,
    }
    if target is not None:
        intent["target_cell"] = target
    return engine.resolve_intent(instance, intent, SequenceRng([]))


def _move_ok(engine, instance, target: dict) -> dict:
    resolved = _move(engine, instance, target)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]


def test_chebyshev_distance_counts_diagonals_as_five_feet() -> None:
    board = spatial.normalize_board(BOARD)
    origin = {"x": 1, "y": 1, "footprint": {"w": 1, "h": 1}}
    diagonal = {"x": 4, "y": 5, "footprint": {"w": 1, "h": 1}}
    straight = {"x": 4, "y": 1, "footprint": {"w": 1, "h": 1}}

    assert spatial.chebyshev(origin, diagonal) == 4
    assert spatial.distance_ft(board, origin, diagonal) == 20
    assert spatial.distance_ft(board, origin, straight) == 15


def test_grid_start_emits_board_and_projection_has_one_authority(sheet) -> None:
    engine, instance, batch = _start(sheet)
    combat = _combat(instance)
    started = next(
        event for event in batch["events"] if event["type"] == "dnd2024.combat.started"
    )

    assert combat["position_mode"] == "grid"
    assert combat["board"]["id"] == "scene:chapel"
    assert combat["spatial_position"][PLAYER] == {
        "x": 1, "y": 1, "footprint": {"w": 1, "h": 1},
    }
    # Theater positions are still written and keep their integer shape.
    assert combat["positions"] == {PLAYER: 0, ENEMY: 5}
    assert started["position_mode"] == "grid"
    assert started["board"]["width"] == 12

    view = engine.gameplay_view(instance)
    assert view["combat"]["board"]["cell_size"] == 5
    assert view["combat"]["spatial_position"][ENEMY]["x"] == 5
    authorities = {
        actor["actor_id"]: actor["position_authority"]
        for actor in view["combat"]["actors"]
    }
    assert authorities == {PLAYER: "combat", ENEMY: "combat"}
    for actor in view["combat"]["actors"]:
        assert actor["spatial_position"] == combat["spatial_position"][actor["actor_id"]]


def test_theater_start_keeps_old_shape_and_no_grid_keys(sheet) -> None:
    engine, instance, batch = _start(sheet, board=False)
    combat = _combat(instance)

    assert combat["position_mode"] == "theater"
    assert "board" not in combat
    assert "spatial_position" not in combat
    assert combat["positions"] == {PLAYER: 0, ENEMY: 5}
    assert "board" not in engine.gameplay_view(instance)["combat"]


def test_old_save_without_grid_fields_is_not_guessed_into_2d(sheet) -> None:
    engine, instance, _ = _start(sheet, board=False)
    combat = _combat(instance)
    # Simulate a save written before position_mode existed.
    combat.pop("position_mode")

    assert spatial.is_grid(combat) is False
    assert "spatial_position" not in combat
    assert engine._distance(combat, PLAYER, ENEMY) == 5


def test_grid_move_updates_spatial_position_and_spends_single_budget(sheet) -> None:
    engine, instance, _ = _start(sheet)
    before = _combat(instance)

    batch = _move_ok(engine, instance, {"x": 4, "y": 1})
    combat = _combat(instance)
    moved = next(
        event for event in batch["events"] if event["type"] == "dnd2024.position.changed"
    )

    assert moved["target_cell"] == {"x": 4, "y": 1}
    assert moved["movement_cost"] == 15
    assert moved["distance"] == 0
    assert combat["spatial_position"][PLAYER] == {
        "x": 4, "y": 1, "footprint": {"w": 1, "h": 1},
    }
    assert combat["economy"]["movement"] == before["economy"]["movement"] - 15
    # Grid move is not a second theater accounting path.
    assert combat["positions"] == before["positions"]


def test_grid_move_rejects_occupied_cell_and_footprint(sheet) -> None:
    engine, instance, _ = _start(
        sheet,
        positions={
            PLAYER: {"x": 1, "y": 1},
            ENEMY: {"x": 5, "y": 5, "footprint": {"w": 2, "h": 2}},
        },
    )
    resolved = _move(engine, instance, {"x": 5, "y": 5})
    assert resolved["ok"] is False
    assert "GRID_MOVE_CELL_OCCUPIED" in resolved["error"]

    resolved = _move(engine, instance, {"x": 6, "y": 6})
    assert resolved["ok"] is False
    assert "GRID_MOVE_CELL_OCCUPIED" in resolved["error"]


def test_grid_move_rejects_wall(sheet) -> None:
    wall = {"x0": 3.0, "y0": 0.0, "x1": 3.0, "y1": 9.0}
    engine, instance, _ = _start(
        sheet,
        positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 8, "y": 1}},
        walls=[wall],
    )
    blocked = _move(engine, instance, {"x": 4, "y": 1})
    assert blocked["ok"] is False
    assert "GRID_MOVE_BLOCKED_BY_WALL" in blocked["error"]

    _move_ok(engine, instance, {"x": 2, "y": 1})


def test_grid_move_rejects_over_budget(sheet) -> None:
    engine, instance, _ = _start(
        sheet, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 0, "y": 8}},
    )
    resolved = _move(engine, instance, {"x": 8, "y": 8})
    assert resolved["ok"] is False
    assert "GRID_MOVE_EXCEEDS_SPEED" in resolved["error"]


def test_grid_move_requires_target_cell_and_rejects_theater_distance(sheet) -> None:
    engine, instance, _ = _start(sheet)

    missing = _move(engine, instance, None)
    assert missing["ok"] is False
    assert "GRID_MOVE_TARGET_REQUIRED" in missing["error"]

    version = int(ruleset_runtime.state(instance)["version"])
    resolved = engine.resolve_intent(instance, {
        "intent_id": "move-distance", "type": "move", "expected_version": version,
        "submitted_by": "gm", "actor_id": PLAYER, "distance": 5,
    }, SequenceRng([]))
    assert resolved["ok"] is False
    assert "GRID_MOVE_SHAPE" in resolved["error"]


def test_grid_opportunity_attack_uses_2d_threat_range(sheet) -> None:
    engine, instance, _ = _start(
        sheet, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 2, "y": 1}},
    )
    batch = _move_ok(engine, instance, {"x": 4, "y": 1})
    pending = [
        event for event in batch["events"] if event["type"] == "dnd2024.decision.pending"
    ]
    assert pending and pending[0]["kind"] == "opportunity_attack"
    assert pending[0]["movement"]["target_cell"] == {"x": 4, "y": 1}
    assert not any(
        event["type"] == "dnd2024.position.changed" for event in batch["events"]
    )

    # A diagonal step stays within 5 feet of the enemy: no opportunity attack.
    engine, instance, _ = _start(
        sheet, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 2, "y": 1}},
    )
    batch = _move_ok(engine, instance, {"x": 2, "y": 2})
    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in batch["events"]
    )
    assert _combat(instance)["spatial_position"][PLAYER]["x"] == 2


def test_grid_attack_range_uses_chebyshev(sheet) -> None:
    engine, instance, _ = _start(sheet)
    combat = _combat(instance)
    assert engine._distance(combat, PLAYER, ENEMY) == 20

    actions = engine.available_intents(instance, "gm")
    attack = next(action for action in actions if action["type"] == "attack")
    weapon = next(
        item for item in attack["weapons"] if int(item.get("range") or 5) <= 5
    )
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": "attack-far", "type": "attack", "expected_version": version,
        "submitted_by": "gm", "actor_id": PLAYER, "target_id": ENEMY,
        "weapon_ref": weapon["weapon_ref"],
    }
    rejected = engine.validate_intent(instance, intent)
    assert rejected["ok"] is False
    assert "out of range" in rejected["error"]

    engine, instance, _ = _start(
        sheet, positions={PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 2, "y": 2}},
    )
    attack = next(a for a in engine.available_intents(instance, "gm") if a["type"] == "attack")
    weapon = next(item for item in attack["weapons"] if int(item.get("range") or 5) <= 5)
    intent.update(
        intent_id="attack-near",
        expected_version=int(ruleset_runtime.state(instance)["version"]),
        weapon_ref=weapon["weapon_ref"],
    )
    assert engine.validate_intent(instance, intent)["ok"] is True


def test_grid_event_batch_replays_to_the_same_state(sheet) -> None:
    engine, instance, start_batch = _start(sheet)
    move_batch = _move_ok(engine, instance, {"x": 4, "y": 1})
    expected = _combat(instance)

    replay_engine, replay_instance = _instance(sheet)
    for batch in (start_batch, move_batch):
        applied = replay_engine.apply_batch(replay_instance, deepcopy(batch))
        assert applied["applied"] is True, applied
    replayed = _combat(replay_instance)

    assert replayed["board"] == expected["board"]
    assert replayed["spatial_position"] == expected["spatial_position"]
    assert replayed["economy"] == expected["economy"]
    assert replayed["positions"] == expected["positions"]


def test_grid_start_fails_closed_without_complete_positions(sheet) -> None:
    engine, instance = _instance(sheet)
    with pytest.raises(CombatIntentError, match="GRID_START_MISSING_POSITION"):
        engine.resolve_intent(instance, {
            "intent_id": "start", "type": "combat.start", "expected_version": 0,
            "submitted_by": "gm", "enemies": [goblin(position=5)],
            "board": deepcopy(BOARD),
            "spatial_positions": {PLAYER: {"x": 1, "y": 1}},
        }, SequenceRng([20, 1]))


def test_grid_automatic_intent_emits_a_legal_2d_move(sheet) -> None:
    engine, instance, _ = _start(sheet, enemies=[goblin(position=30)])
    version = int(ruleset_runtime.state(instance)["version"])
    ended = engine.resolve_intent(instance, {
        "intent_id": "end-player", "type": "end_turn", "expected_version": version,
        "submitted_by": "gm", "actor_id": PLAYER,
    }, SequenceRng([]))
    assert ended["ok"] is True, ended
    assert engine.apply_batch(instance, ended["event_batch"])["applied"] is True

    automatic = RUNTIME.next_automatic_intent(instance)
    assert automatic is not None
    assert automatic["type"] == "move"
    assert "distance" not in automatic
    assert set(automatic["target_cell"]) == {"x", "y"}

    before = _combat(instance)["spatial_position"][ENEMY]
    resolved = engine.resolve_intent(instance, automatic, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    after = _combat(instance)["spatial_position"][ENEMY]
    assert spatial.chebyshev(before, after) > 0
    assert spatial.chebyshev(after, _combat(instance)["spatial_position"][PLAYER]) < (
        spatial.chebyshev(before, _combat(instance)["spatial_position"][PLAYER])
    )
    assert spatial.fits_board(_combat(instance)["board"], after)


def test_grid_start_rejects_overlap_and_invalid_board(sheet) -> None:
    engine, instance = _instance(sheet)
    base = {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(position=5)],
    }
    overlapped = engine.resolve_intent(instance, {
        **base, "board": deepcopy(BOARD),
        "spatial_positions": {
            PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 1, "y": 1},
        },
    }, SequenceRng([20, 1]))
    assert overlapped["ok"] is False
    assert "GRID_START_OVERLAP" in overlapped["error"]

    bad_metric = engine.resolve_intent(instance, {
        **base, "board": {**deepcopy(BOARD), "distance_metric": "euclidean"},
        "spatial_positions": {
            PLAYER: {"x": 1, "y": 1}, ENEMY: {"x": 5, "y": 5},
        },
    }, SequenceRng([20, 1]))
    assert bad_metric["ok"] is False
    assert "UNSUPPORTED_DISTANCE_METRIC" in bad_metric["error"]