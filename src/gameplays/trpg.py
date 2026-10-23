"""The tabletop narrative game: free play and Adventure-bound play."""

from __future__ import annotations

from src.gameplays.contracts import (
    BOT_SURFACE_FULL, TURN_DRIVER_NARRATIVE_ROUND, GameplayCreateRejection, GameplayCreateRequest, GameplayMode,
)


def _validate_free(request: GameplayCreateRequest) -> GameplayCreateRejection | None:
    if request.adventure_id:
        return GameplayCreateRejection("标准自由对局不能绑定冒险包")
    return None


def _validate_adventure(request: GameplayCreateRequest) -> GameplayCreateRejection | None:
    if not request.adventure_id:
        return GameplayCreateRejection("冒险包剧情模式必须选择冒险包")
    return None


FREE = GameplayMode(
    mode_id="free", bot_surface=BOT_SURFACE_FULL, validate_create=_validate_free,
    turn_driver=TURN_DRIVER_NARRATIVE_ROUND,
)
ADVENTURE = GameplayMode(
    mode_id="adventure", bot_surface=BOT_SURFACE_FULL, validate_create=_validate_adventure,
    turn_driver=TURN_DRIVER_NARRATIVE_ROUND,
)
