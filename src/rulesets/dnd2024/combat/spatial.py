"""Square-grid spatial rules for the D&D 2024 combat slice.

This module owns the *board* and *spatial_position* shapes frozen in the
combat-01 contract, plus the pure geometry the intent path needs: Chebyshev
distance, footprint occupancy, straight-line wall blocking, and move checks.
The ruleset never imports the Tactical Scene package; the application service
takes the grid snapshot from the scene and passes it in as an explicit
combat.start input.

Coordinates here are grid cells, never canvas pixels.  Wall segments are kept
in the same cell space so the ruleset stays independent of the scene's pixel
conversion.

Theater combat is untouched: nothing in this module runs unless
``combat.position_mode == "grid"``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

POSITION_MODE_GRID = "grid"
DEFAULT_CELL_SIZE_FT = 5
DISTANCE_METRIC = "dnd2024"
_MAX_FOOTPRINT = 64
_NEIGHBORS = tuple(
    (dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)
)


class SpatialError(ValueError):
    """A grid move or board input was refused; ``code`` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def is_grid(combat: Mapping[str, Any]) -> bool:
    return str(combat.get("position_mode") or "") == POSITION_MODE_GRID


def _positive_int(value: Any, code: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SpatialError(code, f"{label} must be a positive integer")
    return value


def _cell_int(value: Any, code: str, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SpatialError(code, f"{label} must be an integer >= {minimum}")
    return value


def _finite(value: Any, code: str, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpatialError(code, f"{label} must be a finite number")
    return float(value)


def normalize_board(raw: Any) -> dict[str, Any]:
    """Validate the frozen board snapshot and return a normalized copy."""

    if not isinstance(raw, Mapping):
        raise SpatialError("INVALID_BOARD", "board must be an object")
    scene_ref = str(raw.get("id") or "")
    if not scene_ref:
        raise SpatialError("INVALID_BOARD", "board.id must be a scene reference")
    width = _positive_int(raw.get("width"), "INVALID_BOARD", "board.width")
    height = _positive_int(raw.get("height"), "INVALID_BOARD", "board.height")
    cell_size = raw.get("cell_size", DEFAULT_CELL_SIZE_FT)
    cell_size = _positive_int(cell_size, "INVALID_BOARD", "board.cell_size")
    metric = str(raw.get("distance_metric") or DISTANCE_METRIC)
    if metric != DISTANCE_METRIC:
        raise SpatialError(
            "UNSUPPORTED_DISTANCE_METRIC", f"unknown distance metric: {metric}",
        )
    walls_raw = raw.get("walls") or []
    if not isinstance(walls_raw, list):
        raise SpatialError("INVALID_BOARD", "board.walls must be an array")
    walls: list[dict[str, float]] = []
    for index, wall in enumerate(walls_raw):
        if not isinstance(wall, Mapping):
            raise SpatialError("INVALID_BOARD", f"board.walls[{index}] must be an object")
        walls.append({
            "x0": _finite(wall.get("x0"), "INVALID_BOARD", f"walls[{index}].x0"),
            "y0": _finite(wall.get("y0"), "INVALID_BOARD", f"walls[{index}].y0"),
            "x1": _finite(wall.get("x1"), "INVALID_BOARD", f"walls[{index}].x1"),
            "y1": _finite(wall.get("y1"), "INVALID_BOARD", f"walls[{index}].y1"),
        })
    return {
        "id": scene_ref,
        "width": width,
        "height": height,
        "cell_size": cell_size,
        "distance_metric": metric,
        "walls": walls,
        **_scene_revision(raw),
    }


def _scene_revision(raw: Mapping[str, Any]) -> dict[str, int]:
    """The scene slot revision captured at combat.start, when supplied.

    It is optional so old grid batches (and every theater board) replay
    unchanged; the application service uses it as ``expected_revision`` when it
    writes final positions back.  A missing value is never guessed.
    """

    value = raw.get("scene_revision")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return {"scene_revision": value}
    return {}


def normalize_position(raw: Any, board: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one ``{x, y, footprint}`` entry and prove it fits the board."""

    if not isinstance(raw, Mapping):
        raise SpatialError("INVALID_POSITION", "spatial position must be an object")
    x = _cell_int(raw.get("x"), "INVALID_POSITION", "spatial position x")
    y = _cell_int(raw.get("y"), "INVALID_POSITION", "spatial position y")
    footprint_raw = raw.get("footprint") or {}
    if not isinstance(footprint_raw, Mapping):
        raise SpatialError("INVALID_POSITION", "footprint must be an object")
    w = _positive_int(footprint_raw.get("w", 1), "INVALID_POSITION", "footprint.w")
    h = _positive_int(footprint_raw.get("h", 1), "INVALID_POSITION", "footprint.h")
    if w > _MAX_FOOTPRINT or h > _MAX_FOOTPRINT:
        raise SpatialError("INVALID_POSITION", "footprint is too large")
    position = {"x": x, "y": y, "footprint": {"w": w, "h": h}}
    if not fits_board(board, position):
        raise SpatialError("POSITION_OUT_OF_BOARD", "token does not fit inside the board")
    return position


def normalize_positions(
    raw: Any, board: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping) or not raw:
        raise SpatialError(
            "GRID_START_POSITIONS_REQUIRED",
            "grid combat needs an initial spatial position for every actor",
        )
    result: dict[str, dict[str, Any]] = {}
    for actor_id, value in raw.items():
        actor = str(actor_id or "")
        if not actor:
            raise SpatialError("INVALID_POSITION", "spatial position actor id is empty")
        result[actor] = normalize_position(value, board)
    _reject_overlaps(result, "GRID_START_OVERLAP")
    return result


def fits_board(board: Mapping[str, Any], position: Mapping[str, Any]) -> bool:
    footprint = position.get("footprint") or {}
    width = int(footprint.get("w", 1) or 1)
    height = int(footprint.get("h", 1) or 1)
    x = int(position.get("x", 0) or 0)
    y = int(position.get("y", 0) or 0)
    return (
        x >= 0 and y >= 0
        and x + width <= int(board["width"])
        and y + height <= int(board["height"])
    )


def occupied_cells(position: Mapping[str, Any]) -> set[tuple[int, int]]:
    footprint = position.get("footprint") or {}
    width = int(footprint.get("w", 1) or 1)
    height = int(footprint.get("h", 1) or 1)
    x = int(position.get("x", 0) or 0)
    y = int(position.get("y", 0) or 0)
    return {
        (x + dx, y + dy) for dx in range(width) for dy in range(height)
    }


def _reject_overlaps(positions: Mapping[str, Mapping[str, Any]], code: str) -> None:
    claimed: dict[tuple[int, int], str] = {}
    for actor_id in sorted(positions):
        for cell in occupied_cells(positions[actor_id]):
            other = claimed.get(cell)
            if other is not None:
                raise SpatialError(
                    code, f"{other} and {actor_id} occupy the same cell {cell}",
                )
            claimed[cell] = actor_id


def chebyshev(first: Mapping[str, Any], second: Mapping[str, Any]) -> int:
    return max(
        abs(int(first.get("x", 0) or 0) - int(second.get("x", 0) or 0)),
        abs(int(first.get("y", 0) or 0) - int(second.get("y", 0) or 0)),
    )


def distance_ft(
    board: Mapping[str, Any], first: Mapping[str, Any], second: Mapping[str, Any],
) -> int:
    """D&D 2024 square grid: every cell step, diagonals included, is 5 feet."""

    return chebyshev(first, second) * int(board["cell_size"])


def position_of(combat: Mapping[str, Any], actor_id: str) -> dict[str, Any]:
    positions = combat.get("spatial_position") or {}
    position = positions.get(str(actor_id)) if isinstance(positions, Mapping) else None
    if not isinstance(position, Mapping):
        raise SpatialError(
            "MISSING_SPATIAL_POSITION",
            f"grid actor has no spatial position: {actor_id}",
        )
    return dict(position)


def distance_between(combat: Mapping[str, Any], first: str, second: str) -> int:
    board = combat.get("board") or {}
    if not isinstance(board, Mapping) or "cell_size" not in board:
        raise SpatialError("INVALID_BOARD", "grid combat has no board")
    return distance_ft(board, position_of(combat, first), position_of(combat, second))


def distance_to_cell(combat: Mapping[str, Any], actor_id: str, cell: Mapping[str, Any]) -> int:
    board = combat.get("board") or {}
    return distance_ft(board, position_of(combat, actor_id), cell)


def _anchor(position: Mapping[str, Any]) -> tuple[float, float]:
    footprint = position.get("footprint") or {}
    return (
        float(position.get("x", 0) or 0) + float(footprint.get("w", 1) or 1) / 2.0,
        float(position.get("y", 0) or 0) + float(footprint.get("h", 1) or 1) / 2.0,
    )


def _orientation(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_segment(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> bool:
    return (
        min(ax, bx) <= cx <= max(ax, bx)
        and min(ay, by) <= cy <= max(ay, by)
    )


def _segments_cross(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
) -> bool:
    o1 = _orientation(ax, ay, bx, by, cx, cy)
    o2 = _orientation(ax, ay, bx, by, dx, dy)
    o3 = _orientation(cx, cy, dx, dy, ax, ay)
    o4 = _orientation(cx, cy, dx, dy, bx, by)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
        return True
    if o1 == 0 and _on_segment(ax, ay, bx, by, cx, cy):
        return True
    if o2 == 0 and _on_segment(ax, ay, bx, by, dx, dy):
        return True
    if o3 == 0 and _on_segment(cx, cy, dx, dy, ax, ay):
        return True
    if o4 == 0 and _on_segment(cx, cy, dx, dy, bx, by):
        return True
    return False


def wall_blocks(board: Mapping[str, Any], start: Mapping[str, Any], end: Mapping[str, Any]) -> bool:
    """Whether the straight segment between two anchors crosses a wall."""

    ax, ay = _anchor(start)
    bx, by = _anchor(end)
    for wall in board.get("walls") or []:
        if _segments_cross(
            ax, ay, bx, by,
            float(wall["x0"]), float(wall["y0"]), float(wall["x1"]), float(wall["y1"]),
        ):
            return True
    return False


def move_cost_ft(
    combat: Mapping[str, Any], actor_id: str, target: Mapping[str, Any],
) -> int:
    board = combat.get("board") or {}
    return distance_ft(board, position_of(combat, actor_id), target)


def check_move(
    combat: Mapping[str, Any], actor_id: str, payload: Mapping[str, Any],
    *, budget_ft: int, approach_sources: Iterable[str] = (),
) -> int:
    """Validate one grid move and return its cost in feet.

    ``payload`` is the move intent.  Reading it here (rather than in the
    combat package hotspots) keeps the new ``target_cell`` field in one owner.
    """

    target = require_target_cell(payload)
    board = combat.get("board") or {}
    if not isinstance(board, Mapping) or "cell_size" not in board:
        raise SpatialError("INVALID_BOARD", "grid combat has no board")
    start = position_of(combat, actor_id)
    target = normalize_position({**target, "footprint": start.get("footprint")}, board)
    if chebyshev(start, target) == 0:
        raise SpatialError("GRID_MOVE_NO_DISTANCE", "target cell is the current cell")
    positions = combat.get("spatial_position") or {}
    claimed: dict[tuple[int, int], str] = {}
    for other_id, other in positions.items():
        if str(other_id) == str(actor_id) or not isinstance(other, Mapping):
            continue
        for cell in occupied_cells(other):
            claimed[cell] = str(other_id)
    for cell in occupied_cells(target):
        if cell in claimed:
            raise SpatialError(
                "GRID_MOVE_CELL_OCCUPIED",
                f"target cell {cell} is occupied by {claimed[cell]}",
            )
    if wall_blocks(board, start, target):
        raise SpatialError("GRID_MOVE_BLOCKED_BY_WALL", "a wall blocks this move")
    cost = move_cost_ft(combat, actor_id, target)
    if cost > max(0, int(budget_ft or 0)):
        raise SpatialError(
            "GRID_MOVE_EXCEEDS_SPEED",
            f"movement costs {cost} feet but only {budget_ft} remain",
        )
    for source_id in approach_sources:
        source = str(source_id or "")
        if not source or source not in positions:
            continue
        before = distance_between(combat, actor_id, source)
        after = distance_ft(board, target, positions[source])
        if after < before:
            raise SpatialError(
                "GRID_MOVE_CANNOT_APPROACH",
                "a frightened creature cannot move closer to the source of its fear",
            )
    return cost


def require_target_cell(payload: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(payload, Mapping):
        raise SpatialError("GRID_MOVE_SHAPE", "move intent must be an object")
    if "distance" in payload:
        raise SpatialError(
            "GRID_MOVE_SHAPE", "grid moves use target_cell, not distance",
        )
    raw = payload.get("target_cell")
    if not isinstance(raw, Mapping):
        raise SpatialError("GRID_MOVE_TARGET_REQUIRED", "grid move needs target_cell")
    return {
        "x": _cell_int(raw.get("x"), "GRID_MOVE_SHAPE", "target_cell.x"),
        "y": _cell_int(raw.get("y"), "GRID_MOVE_SHAPE", "target_cell.y"),
    }


def has_start_input(payload: Mapping[str, Any]) -> bool:
    return isinstance(payload, Mapping) and (
        "board" in payload or "spatial_positions" in payload
    )


def start_input(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]] | None:
    """Read the explicit combat.start board input.  No board means theater."""

    if not has_start_input(payload):
        return None
    if "board" not in payload:
        raise SpatialError("INVALID_BOARD", "spatial_positions requires board")
    board = normalize_board(payload.get("board"))
    positions = normalize_positions(payload.get("spatial_positions"), board)
    return board, positions


def validate_start_input(payload: Mapping[str, Any]) -> None:
    start_input(payload)


def view(combat: Mapping[str, Any]) -> dict[str, Any]:
    """The grid portion of one gameplay view (board + one authority per actor)."""

    board = combat.get("board")
    positions = combat.get("spatial_position") or {}
    return {
        "board": dict(board) if isinstance(board, Mapping) else None,
        "spatial_position": {
            str(actor): dict(value)
            for actor, value in positions.items()
            if isinstance(value, Mapping)
        },
    }


def actor_view(combat: Mapping[str, Any], actor_id: str) -> dict[str, Any]:
    positions = combat.get("spatial_position") or {}
    current = positions.get(str(actor_id)) if isinstance(positions, Mapping) else None
    return {
        "spatial_position": dict(current) if isinstance(current, Mapping) else None,
        "position_authority": "combat",
    }


def apply_started(combat: dict[str, Any], event: Mapping[str, Any]) -> None:
    """Land a grid ``combat.started`` event on a freshly built combat dict.

    A grid start without a complete initial position for every participant is
    refused: replay must never invent a two-dimensional coordinate.
    """

    board = normalize_board(event.get("board"))
    positions = normalize_positions(event.get("spatial_position"), board)
    initiative = [str(actor) for actor in combat.get("initiative") or []]
    missing = sorted(actor for actor in initiative if actor not in positions)
    if missing:
        raise SpatialError(
            "GRID_START_MISSING_POSITION",
            "grid combat.started lacks spatial position for: " + ", ".join(missing),
        )
    combat["board"] = board
    combat["spatial_position"] = {actor: positions[actor] for actor in initiative}


def apply_move_event(
    combat: Mapping[str, Any], actor_id: str, event: Mapping[str, Any],
) -> None:
    """Apply one ``dnd2024.position.changed`` spatial delta during replay.

    Cost accounting stays in the reducer's single movement path; this only
    moves the authoritative cell.  A grid event carries either an explicit
    ``target_cell`` (ordinary move and teleport) or the forced-move sentence
    ``feet`` + ``away_from``, which is turned into a cell here.  Anything else
    fails closed so an old event never invents a two-dimensional coordinate.
    """

    cell = event.get("target_cell")
    if not isinstance(cell, Mapping):
        cell = forced_target_cell(combat, actor_id, event)
    if isinstance(cell, Mapping):
        current = (combat.get("spatial_position") or {}).get(actor_id)
        if not isinstance(current, Mapping):
            raise SpatialError(
                "MISSING_SPATIAL_POSITION",
                f"grid actor has no spatial position: {actor_id}",
            )
        combat.setdefault("spatial_position", {})[actor_id] = normalize_position(
            {
                "x": cell.get("x"), "y": cell.get("y"),
                "footprint": current.get("footprint"),
            },
            combat.get("board") or {},
        )
        return
    if int(event.get("distance") or 0) or event.get("feet"):
        raise SpatialError("GRID_MOVE_SHAPE", "grid movement must carry target_cell")


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _occupied_by_other(
    combat: Mapping[str, Any], actor_id: str, candidate: Mapping[str, Any],
) -> bool:
    claimed: set[tuple[int, int]] = set()
    for other_id, other in (combat.get("spatial_position") or {}).items():
        if str(other_id) == str(actor_id) or not isinstance(other, Mapping):
            continue
        for cell in occupied_cells(other):
            claimed.add(cell)
    return bool(claimed & occupied_cells(candidate))


def forced_target_cell(
    combat: Mapping[str, Any], actor_id: str, event: Mapping[str, Any],
) -> dict[str, int] | None:
    """Where a grid forced move lands, described as ``feet`` away from a source.

    Push riders say "N feet away from that creature" and never name a cell; the
    direction is the sign of the anchor delta and diagonal pushes are legal.
    The creature advances one cell at a time and stops before the first step
    that would leave the board, enter an occupied cell or cross a wall, so a
    push never tunnels through an obstacle.  ``None`` means the event is not a
    forced move, and the caller must fail closed.
    """

    feet = event.get("feet")
    away_from = str(event.get("away_from") or "")
    if isinstance(feet, bool) or not isinstance(feet, int) or feet <= 0 or not away_from:
        return None
    board = combat.get("board") or {}
    if not isinstance(board, Mapping) or "cell_size" not in board:
        raise SpatialError("INVALID_BOARD", "grid combat has no board")
    current = position_of(combat, actor_id)
    source = position_of(combat, away_from)
    dx = int(current.get("x", 0) or 0) - int(source.get("x", 0) or 0)
    dy = int(current.get("y", 0) or 0) - int(source.get("y", 0) or 0)
    if dx == 0 and dy == 0:
        raise SpatialError(
            "GRID_FORCED_NO_DIRECTION",
            "a forced move needs a direction away from its source",
        )
    cell_size = int(board["cell_size"])
    steps = int(feet) // cell_size
    if steps <= 0:
        # A push shorter than one cell still displaces one whole cell.
        steps = 1
    sx, sy = _sign(dx), _sign(dy)
    footprint = deepcopy(current.get("footprint") or {"w": 1, "h": 1})
    origin_x = int(current.get("x", 0) or 0)
    origin_y = int(current.get("y", 0) or 0)
    target = {"x": origin_x, "y": origin_y}
    for step in range(1, steps + 1):
        candidate = {
            "x": origin_x + sx * step,
            "y": origin_y + sy * step,
            "footprint": footprint,
        }
        if not fits_board(board, candidate):
            break
        if _occupied_by_other(combat, actor_id, candidate):
            break
        if wall_blocks(board, current, candidate):
            break
        target = {"x": candidate["x"], "y": candidate["y"]}
    return target


def teleport_target_cell(
    combat: Mapping[str, Any], actor_id: str, payload: Mapping[str, Any],
    *, range_ft: int,
) -> dict[str, int]:
    """Validate a grid ``target_cell`` teleport: in range, free, in the board.

    Teleportation is not movement: it ignores walls and spends no movement, but
    it still may not land on another creature or outside the board.
    """

    board = combat.get("board") or {}
    if not isinstance(board, Mapping) or "cell_size" not in board:
        raise SpatialError("INVALID_BOARD", "grid combat has no board")
    raw = require_target_cell(payload)
    start = position_of(combat, actor_id)
    target = normalize_position({**raw, "footprint": start.get("footprint")}, board)
    if chebyshev(start, target) == 0:
        raise SpatialError(
            "GRID_TELEPORT_NO_DISTANCE", "teleport target is the current cell",
        )
    if distance_ft(board, start, target) > int(range_ft or 0):
        raise SpatialError(
            "GRID_TELEPORT_OUT_OF_RANGE",
            f"teleport target is {distance_ft(board, start, target)} feet away, "
            f"range is {range_ft}",
        )
    if _occupied_by_other(combat, actor_id, target):
        raise SpatialError(
            "GRID_TELEPORT_CELL_OCCUPIED", "another creature occupies that cell",
        )
    return {"x": target["x"], "y": target["y"]}


def automatic_move_target(
    combat: Mapping[str, Any], actor_id: str, target_id: str, budget_ft: int,
) -> dict[str, int] | None:
    """A greedy grid step toward ``target_id`` that costs at most ``budget_ft``.

    One step per iteration, always reducing Chebyshev distance, never entering
    an occupied cell or crossing a wall.  ``None`` means no legal step exists
    (or no budget), and the automatic ladder falls back to its next choice.
    """

    board = combat.get("board") or {}
    if not isinstance(board, Mapping) or "cell_size" not in board:
        return None
    cell_size = max(1, int(board.get("cell_size") or 1))
    budget_cells = int(budget_ft or 0) // cell_size
    if budget_cells <= 0:
        return None
    try:
        current = position_of(combat, actor_id)
        goal = position_of(combat, target_id)
    except SpatialError:
        return None
    start = {"x": int(current.get("x", 0) or 0), "y": int(current.get("y", 0) or 0)}
    best = start
    footprint = current.get("footprint") or {"w": 1, "h": 1}
    for _ in range(budget_cells):
        step = _greedy_step(combat, actor_id, board, best, goal, footprint)
        if step is None:
            break
        best = step
        if chebyshev(best, goal) == 0:
            break
    if best == start:
        return None
    if wall_blocks(
        board, {**start, "footprint": footprint}, {**best, "footprint": footprint},
    ):
        # The grid move validator checks the straight start -> target segment;
        # a stepwise path that crosses a wall end-to-end is not a legal move.
        return None
    return {"x": best["x"], "y": best["y"]}


def _greedy_step(
    combat: Mapping[str, Any], actor_id: str, board: Mapping[str, Any],
    current: Mapping[str, Any], goal: Mapping[str, Any], footprint: Any,
) -> dict[str, int] | None:
    here = chebyshev(current, goal)
    options: list[tuple[int, int, int, int, dict[str, int]]] = []
    for dx, dy in _NEIGHBORS:
        cell = {
            "x": int(current.get("x", 0) or 0) + dx,
            "y": int(current.get("y", 0) or 0) + dy,
            "footprint": footprint,
        }
        distance = chebyshev(cell, goal)
        if distance >= here:
            continue
        if not fits_board(board, cell):
            continue
        if _occupied_by_other(combat, actor_id, cell):
            continue
        if wall_blocks(board, current, cell):
            continue
        # Prefer straight over diagonal, then a stable ordering.
        options.append((distance, abs(dx) + abs(dy), dx, dy, {
            "x": cell["x"], "y": cell["y"],
        }))
    if not options:
        return None
    options.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return options[0][4]


def guard_automatic_intent(
    candidate: Mapping[str, Any] | None, combat: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Refuse a grid move that carries only a theater distance.

    The grid automatic ladder emits ``target_cell`` moves through
    :func:`automatic_move_target`; an intent still shaped for the one-dimensional
    map cannot be validated by any grid rule and would roll the whole batch
    back, so it ends its turn instead.
    """

    if (
        isinstance(candidate, Mapping)
        and str(candidate.get("type") or "") == "move"
        and is_grid(combat)
        and not isinstance(candidate.get("target_cell"), Mapping)
    ):
        guarded = {
            key: value for key, value in candidate.items() if key != "distance"
        }
        guarded["type"] = "end_turn"
        guarded["intent_id"] = f"{guarded.get('intent_id')}:grid-end"
        return guarded
    return candidate