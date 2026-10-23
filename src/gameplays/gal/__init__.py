"""Gal gameplay mode (ADR-0010, ADR-0012).

Everything gal-specific lives in this package.  It may use the shared
platform (engine core, content, LLM, memory, TTS) but not the tabletop
game's own mechanics; ``tests/architecture/test_gameplay_packages.py``
enforces both directions.
"""

from __future__ import annotations

from src.gameplays.contracts import (
    BOT_SURFACE_READ_ONLY, GameplayCreateRejection, GameplayCreateRequest, GameplayMode,
)

GAL_RULE_ID = "gal_narrative"


def _validate_create(request: GameplayCreateRequest) -> GameplayCreateRejection | None:
    if not request.solo:
        return GameplayCreateRejection("GAL 玩法只能创建单人局", "GAL_REQUIRES_SOLO")
    if request.rule_id != GAL_RULE_ID:
        return GameplayCreateRejection("GAL 玩法必须使用 gal_narrative 叙事规则", "GAL_REQUIRES_NARRATIVE_RULE")
    if request.adventure_id:
        return GameplayCreateRejection("GAL 玩法不能绑定冒险包", "GAL_CANNOT_BIND_ADVENTURE")
    return None


GAL = GameplayMode(mode_id="gal", bot_surface=BOT_SURFACE_READ_ONLY, validate_create=_validate_create)
