"""W2「规则与玩法」看板：玩法模式只读投影的接线状态契约。"""

from __future__ import annotations

from typing import Any
from types import SimpleNamespace

from src.gameplays.contracts import (
    BOT_SURFACE_NONE,
    GameplayMode,
    GameplayProvider,
    GameplayProviderRegistry,
)
from src.webui.api import WebAPI


def _provider(mode_id: str) -> GameplayProvider:
    async def _driver(
        instance: Any, *, on_delta: Any = None, on_reset: Any = None,
    ) -> tuple[str, dict | None]:
        return "narration", None

    return GameplayProvider(
        mode=GameplayMode(
            mode_id=mode_id,
            bot_surface=BOT_SURFACE_NONE,
            validate_create=lambda request: None,
            turn_driver=f"{mode_id}-driver",
        ),
        turn_driver=_driver,
        project_view=lambda instance, viewer_id, viewer_is_gm: {"public": True},
    )


def test_list_gameplays_reports_wiring_status_honestly() -> None:
    """provider/driver 都接线的玩法标已接线；只有模式没实现的如实标未接线。"""

    registry = GameplayProviderRegistry()
    registry.register(_provider("sample_mode"))
    # 只注册模式、没有 provider：fail closed 可见性用例。
    registry.gameplays.register(GameplayMode(
        mode_id="wip_mode",
        bot_surface=BOT_SURFACE_NONE,
        validate_create=lambda request: None,
        turn_driver="wip-driver",
    ))

    result = WebAPI.list_gameplays(SimpleNamespace(_gameplay_providers=registry))
    assert result["ok"] is True
    by_id = {row["mode_id"]: row for row in result["gameplays"]}
    assert by_id["sample_mode"]["provider_registered"] is True
    assert by_id["sample_mode"]["turn_driver_bound"] is True
    assert by_id["wip_mode"]["provider_registered"] is False
    assert by_id["wip_mode"]["turn_driver_bound"] is False
