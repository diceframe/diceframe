"""Party rest approval: the DM verdict is the only thing that moves game time."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import game_config, ruleset_runtime
from src.engine.participant_view import Viewer
from src.engine.world_state import clock_to_minutes
from src.webui.api import WebAPI
from src.webui.ruleset_rest_projection import public_rest_session
from src.webui.services.assistant_gameplay_tools import (
    GameplayToolDependencies,
    gameplay_tools,
)
from tests.rulesets.test_dnd2024_character_lifecycle import professional_context  # noqa: F401

KEY = "web|character-lifecycle|web_bot"


def _party(context):
    """A two-player party that is awake, conscious, and not in solo mode."""

    api, instance, character = context
    ally = deepcopy(character)
    ally["character_name"] = "Second Hero"
    ally["ruleset_character"]["identity"]["name"] = "Second Hero"
    instance.players["ally"] = {"character_name": "Second Hero", "character_sheet": ally}
    for uid in ("gm", "ally"):
        sheet = instance.get_character_sheet(uid)
        sheet["hp"] = 1
        sheet["ruleset_character"]["resources"]["hp"] = 1
    game_config.replace_solo_mode(instance, False)
    return api, instance


def _submit_body(rest: str, operation: str, revision: int = 0) -> dict:
    return {
        "rest": rest,
        "hit_dice": {"d10": 1} if rest == "short" else {},
        "confirm_elapsed_time": True,
        "expected_revision": revision,
        "operation_id": operation,
    }


async def _both_submit(api, instance, rest: str, *, tag: str) -> dict:
    await WebAPI.ruleset_rest_submit_live(api, KEY, "gm", _submit_body(rest, f"{tag}-gm"))
    return await WebAPI.ruleset_rest_submit_live(
        api, KEY, "ally", _submit_body(rest, f"{tag}-ally"),
    )


def _clock(instance) -> int:
    return clock_to_minutes(instance.world_state["clock"])


def _revision(instance, uid: str) -> int:
    return int(instance.get_character_sheet(uid).get("ruleset_revision", 0) or 0)


@pytest.mark.asyncio
async def test_a_full_party_waits_for_the_dm_instead_of_settling(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    before_clock = _clock(instance)
    result = await _both_submit(api, instance, "short", tag="wait")

    assert result["ok"] is True
    assert result["resolved"] is False
    assert result["awaiting_approval"] is True
    assert result["rest_session"]["status"] == "awaiting_approval"
    assert result["rest_session"]["duration_minutes"] == 60
    # 申报不碰角色也不碰时钟：裁决之前什么都不能发生。
    assert _revision(instance, "gm") == 0 and _revision(instance, "ally") == 0
    assert _clock(instance) == before_clock


@pytest.mark.asyncio
async def test_approval_settles_everyone_and_advances_the_short_rest(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    before_clock = _clock(instance)
    await _both_submit(api, instance, "short", tag="approve")

    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "decision-1"},
    )

    assert result["ok"] is True and result["resolved"] is True
    assert result["rest_session"]["status"] == "completed"
    assert result["duration_minutes"] == 60
    assert _revision(instance, "gm") == 1 and _revision(instance, "ally") == 1
    assert _clock(instance) == before_clock + 60


@pytest.mark.asyncio
async def test_approval_advances_a_long_rest_by_eight_hours(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    before_clock = _clock(instance)
    await _both_submit(api, instance, "long", tag="long")

    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "decision-long"},
    )

    assert result["ok"] is True
    assert result["rest_session"]["status"] == "completed"
    assert _clock(instance) == before_clock + 480


@pytest.mark.asyncio
async def test_the_clock_crosses_midnight_instead_of_wrapping(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    instance.world_state["clock"] = {"day": 1, "minute": 20 * 60}
    await _both_submit(api, instance, "long", tag="midnight")

    await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "decision-midnight"},
    )

    assert instance.world_state["clock"] == {"day": 2, "minute": 4 * 60}


@pytest.mark.asyncio
async def test_rejection_touches_neither_characters_nor_the_clock(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    before_clock = _clock(instance)
    await _both_submit(api, instance, "short", tag="reject")

    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": False, "operation_id": "decision-reject"},
    )

    assert result["ok"] is True and result["rejected"] is True
    assert result["rest_session"]["status"] == "rejected"
    assert _revision(instance, "gm") == 0 and _revision(instance, "ally") == 0
    assert _clock(instance) == before_clock
    # rejected 是终止态：拒绝之后还能重新发起一次休息。
    again = await WebAPI.ruleset_rest_submit_live(
        api, KEY, "gm", _submit_body("short", "after-reject-gm"),
    )
    assert again["ok"] is True
    assert again["rest_session"]["status"] == "collecting"


@pytest.mark.asyncio
async def test_a_verdict_needs_a_rest_that_is_waiting(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "nothing-here"},
    )
    assert result["ok"] is False
    assert result["code"] == "REST_NOT_AWAITING_APPROVAL"


@pytest.mark.asyncio
async def test_only_the_gm_may_decide(professional_context) -> None:  # noqa: F811
    api, instance = _party(professional_context)
    await _both_submit(api, instance, "short", tag="not-gm")
    before_clock = _clock(instance)

    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "ally", False, {"approve": True, "operation_id": "sneaky"},
    )

    assert result["ok"] is False
    assert result["code"] == "REST_DECISION_FORBIDDEN"
    assert _revision(instance, "gm") == 0
    assert _clock(instance) == before_clock


@pytest.mark.asyncio
async def test_replaying_one_verdict_never_advances_time_twice(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    before_clock = _clock(instance)
    await _both_submit(api, instance, "short", tag="replay")
    body = {"approve": True, "operation_id": "decision-replay"}

    first = await WebAPI.ruleset_rest_decide(api, KEY, "gm", True, body)
    second = await WebAPI.ruleset_rest_decide(api, KEY, "gm", True, body)

    assert first["ok"] is True
    assert second["ok"] is True and second["duplicate"] is True
    assert _clock(instance) == before_clock + 60


@pytest.mark.asyncio
async def test_the_session_records_logical_ticks_not_wall_clock(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    instance.world_state["clock"] = {"day": 3, "minute": 8 * 60}
    await _both_submit(api, instance, "short", tag="ticks")
    await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "decision-ticks"},
    )

    session = ruleset_runtime.state(instance)["rest_session"]
    assert session["started_tick"] == 2 * 1440 + 8 * 60
    assert isinstance(session["resolved_tick"], int)
    assert session["duration_minutes"] == 60
    for legacy in ("started_at", "submitted_at", "resolved_at"):
        assert legacy not in session
    assert isinstance(session["participants"]["gm"]["submitted_tick"], int)

    public = public_rest_session(instance)
    assert public["started_clock"] == {"day": 3, "minute": 8 * 60}
    assert public["resolved_clock"] == {"day": 3, "minute": 9 * 60}
    assert "resolved_at" not in public


@pytest.mark.asyncio
async def test_legacy_wall_clock_sessions_degrade_without_raising(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    state = ruleset_runtime.state(instance)
    state["rest_session"] = {
        "status": "completed",
        "rest": "long",
        "required_uids": ["gm"],
        "participants": {"gm": {"operation_id": "old", "hit_dice": {}}},
        "started_at": "2026-10-18T01:03:03+00:00",
        "resolved_at": "2026-10-18T09:03:03+00:00",
    }
    public = public_rest_session(instance)
    assert public["status"] == "completed"
    assert public["started_clock"] is None
    assert public["resolved_clock"] is None
    assert "resolved_at" not in public


@pytest.mark.asyncio
async def test_a_solo_rest_advances_time_without_an_extra_click(
    professional_context,  # noqa: F811
) -> None:
    api, instance, _character = professional_context
    game_config.replace_solo_mode(instance, True)
    before_clock = _clock(instance)

    result = await WebAPI.ruleset_rest_submit_live(
        api, KEY, "gm", _submit_body("short", "solo-rest"),
    )

    assert result["ok"] is True
    assert result.get("pending") is not True
    assert result["duration_minutes"] == 60
    assert _clock(instance) == before_clock + 60


@pytest.mark.asyncio
async def test_a_ruleset_without_a_duration_leaves_the_clock_alone(
    professional_context,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, instance = _party(professional_context)
    runtime = api._ruleset_registry.resolve(api._load_rule_for_game(instance).template)
    monkeypatch.delattr(type(runtime), "rest_duration_minutes")
    before_clock = _clock(instance)
    await _both_submit(api, instance, "short", tag="noduration")

    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "decision-noduration"},
    )

    assert result["ok"] is True
    assert _revision(instance, "gm") == 1
    assert _clock(instance) == before_clock


@pytest.mark.asyncio
async def test_an_invalid_duration_is_rejected_before_anything_moves(
    professional_context,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, instance = _party(professional_context)
    runtime = api._ruleset_registry.resolve(api._load_rule_for_game(instance).template)
    monkeypatch.setattr(type(runtime), "rest_duration_minutes", lambda self, rest: -5, raising=False)
    before_clock = _clock(instance)

    # 时长只有全员齐了才需要（也是那时才写进会话），所以由最后一个申报者触发。
    result = await _both_submit(api, instance, "short", tag="bad-duration")

    assert result["ok"] is False
    assert result["code"] == "REST_DURATION_INVALID"
    assert _revision(instance, "gm") == 0
    assert _revision(instance, "ally") == 0
    assert _clock(instance) == before_clock


@pytest.mark.asyncio
async def test_a_failed_time_advance_rolls_the_whole_party_back(
    professional_context,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.webui.services import ruleset_rest

    api, instance = _party(professional_context)
    before_clock = _clock(instance)
    await _both_submit(api, instance, "short", tag="rollback")

    def _boom(*_args, **_kwargs):
        raise ValueError("clock refused to move")

    monkeypatch.setattr(ruleset_rest, "advance_world_time", _boom)
    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "decision-rollback"},
    )

    assert result["ok"] is False
    assert result["code"] == "PARTY_REST_FAILED"
    assert _revision(instance, "gm") == 0 and _revision(instance, "ally") == 0
    assert _clock(instance) == before_clock
    assert result["rest_session"]["status"] == "error"


@pytest.mark.asyncio
async def test_the_ai_gm_can_approve_but_only_with_gm_authority(
    professional_context,  # noqa: F811
) -> None:
    api, instance = _party(professional_context)
    await _both_submit(api, instance, "short", tag="agent")
    before_clock = _clock(instance)

    async def decide(key, uid, is_gm, body):
        return await WebAPI.ruleset_rest_decide(api, key, uid, is_gm, body)

    tools = gameplay_tools(GameplayToolDependencies(
        get_game=lambda _key: instance,
        viewer_for=lambda _game, owner: Viewer("gm" if owner == "gm" else "seat", owner),
        rest=lambda key, uid, body: WebAPI.ruleset_rest_submit_live(api, key, uid, body),
        rest_decide=decide,
    ))
    tool = next(candidate for candidate in tools if candidate.name == "decide_party_rest")

    with pytest.raises(PermissionError):
        tool.guard({"game_key": KEY, "run_id": instance.run_id, "approve": True}, "ally")

    result = await tool.handler(
        {"game_key": KEY, "run_id": instance.run_id, "approve": True}, "gm",
    )
    assert result["ok"] is True
    assert result["rest_session"]["status"] == "completed"
    assert _clock(instance) == before_clock + 60
