"""S0-F6b: a timed ritual spends its own minute in the same commit.

Magical Cunning only returns slots at the end of a 1-minute rite, so the rite
cannot be resolved by a function that forgets to move the clock -- and it must
not move the clock without returning the slots either.  Both halves are one
authoritative write: any failure restores the character and the world together.

The item numbers in the docstrings are the task spec's matrix.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.world_state import clock_to_minutes
from src.webui.api import WebAPI
from src.webui.services import ruleset_ritual

from tests.rulesets.test_dnd2024_character_lifecycle import professional_context  # noqa: F401
from tests.rulesets.test_dnd2024_spell_grants import _sheet as _class_sheet

KEY = "web|character-lifecycle|web_bot"


def _warlock_sheet(api, level: int = 5) -> dict:
    return _class_sheet(api._runtime, "class:warlock", level)


def _seat(instance, api, *, spent: int = 2, level: int = 5) -> dict:
    """Put a warlock in the GM seat with ``spent`` Pact slots already used."""

    sheet = _warlock_sheet(api, level)
    canonical = sheet["ruleset_character"]
    (key,) = canonical["spellcasting"]["class"]["slots_max"]
    canonical["spellcasting"]["class"]["slots_current"][key] = (
        int(canonical["spellcasting"]["class"]["slots_max"][key]) - spent
    )
    sheet["character_name"] = "Rite Warlock"
    canonical["identity"]["name"] = "Rite Warlock"
    # 版本从 0 起：这些断言只关心这一次仪式自己推进了哪一格。
    sheet["ruleset_revision"] = 0
    canonical["ruleset_revision"] = 0
    # 两点生命值只为过「意识清醒」的门槛，不参与这里的断言。
    sheet["hp"] = 2
    canonical["resources"]["hp"] = 2
    instance.players["gm"] = {"character_name": "Rite Warlock", "character_sheet": sheet}
    return sheet


def _body(operation: str, revision: int = 0) -> dict:
    return {
        "confirm_elapsed_time": True,
        "expected_revision": revision,
        "operation_id": operation,
    }


def _clock(instance) -> int:
    return clock_to_minutes(instance.world_state["clock"])


def _slot_state(instance) -> dict[str, int]:
    sheet = instance.get_character_sheet("gm")
    return dict(sheet["ruleset_character"]["spellcasting"]["class"]["slots_current"])


def _uses(instance) -> int:
    sheet = instance.get_character_sheet("gm")
    return int(sheet["ruleset_character"]["resources"]["class"]["magical_cunning"]["current"])


def _revision(instance) -> int:
    return int(instance.get_character_sheet("gm").get("ruleset_revision", 0) or 0)


@pytest.mark.asyncio
async def test_matrix_10_the_rite_moves_the_clock_by_its_own_minute(
    professional_context,  # noqa: F811
) -> None:
    api, instance, _base = professional_context
    _seat(instance, api)
    before_clock = _clock(instance)

    result = await WebAPI.ruleset_ritual_resolve_live(api, KEY, "gm", _body("rite-1"))

    assert result["ok"] is True, result
    assert result["duration_minutes"] == 1
    assert _clock(instance) == before_clock + 1
    assert _slot_state(instance) == {"3": 1}
    assert _uses(instance) == 0
    assert _revision(instance) == 1


@pytest.mark.asyncio
async def test_matrix_10_a_refused_clock_leaves_no_half_done_rite(
    professional_context,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, instance, _base = professional_context
    _seat(instance, api)
    before_clock = _clock(instance)
    before_slots = _slot_state(instance)

    def _refuse(*_args, **_kwargs):
        raise RuntimeError("clock is frozen")

    monkeypatch.setattr(ruleset_ritual, "advance_game_time", _refuse)
    result = await WebAPI.ruleset_ritual_resolve_live(api, KEY, "gm", _body("rite-2"))

    assert result["ok"] is False
    assert result["code"] == "RITUAL_TIME_ADVANCE_FAILED"
    # 槽、次数、版本、时钟都必须回到仪式之前：不允许「槽回来了但时间没走」。
    assert _slot_state(instance) == before_slots
    assert _uses(instance) == 1
    assert _revision(instance) == 0
    assert _clock(instance) == before_clock


@pytest.mark.asyncio
async def test_matrix_22_replaying_the_same_operation_does_not_perform_twice(
    professional_context,  # noqa: F811
) -> None:
    api, instance, _base = professional_context
    _seat(instance, api)

    first = await WebAPI.ruleset_ritual_resolve_live(api, KEY, "gm", _body("rite-3"))
    after_clock = _clock(instance)
    second = await WebAPI.ruleset_ritual_resolve_live(api, KEY, "gm", _body("rite-3"))

    assert first["ok"] is True, first
    assert second["ok"] is True, second
    assert second["duplicate"] is True
    assert _clock(instance) == after_clock
    assert _uses(instance) == 0
    assert _revision(instance) == 1


@pytest.mark.asyncio
async def test_matrix_22_a_stale_revision_moves_nothing(
    professional_context,  # noqa: F811
) -> None:
    api, instance, _base = professional_context
    _seat(instance, api)
    before_clock = _clock(instance)
    before_slots = _slot_state(instance)

    result = await WebAPI.ruleset_ritual_resolve_live(api, KEY, "gm", _body("rite-4", 7))

    assert result["ok"] is False
    assert result["code"] == "STALE_CHARACTER_REVISION"
    assert _slot_state(instance) == before_slots
    assert _clock(instance) == before_clock


@pytest.mark.asyncio
async def test_matrix_10_the_client_must_confirm_the_minute(
    professional_context,  # noqa: F811
) -> None:
    api, instance, _base = professional_context
    _seat(instance, api)
    body = _body("rite-5")
    body.pop("confirm_elapsed_time")

    result = await WebAPI.ruleset_ritual_resolve_live(api, KEY, "gm", body)

    assert result["ok"] is False
    assert result["code"] == "RITUAL_CONFIRMATION_REQUIRED"
    assert _uses(instance) == 1


def test_the_options_projection_never_mutates_the_sheet(
    professional_context,  # noqa: F811
) -> None:
    api, instance, _base = professional_context
    _seat(instance, api)
    before = deepcopy(instance.get_character_sheet("gm"))

    result = WebAPI.ruleset_ritual_options(api, KEY, "gm")

    assert result["ok"] is True, result
    assert [row["resource_id"] for row in result["ritual_options"]] == ["magical_cunning"]
    assert result["ritual_options"][0]["minutes"] == 1
    assert instance.get_character_sheet("gm") == before
