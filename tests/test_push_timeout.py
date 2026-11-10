"""#714：无人处理的孤注一掷按原结果超时关闭，并与幸运共用超时调度。"""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from src.engine.game_state import GameState
from src.engine.modules import check_presentation, checks
from tests.test_push_decisions import change, prepared


def _processor(inst, resumed: list):
    from src.commands.round_processor import RoundProcessor

    class FakeRegistry:
        def get(self, _key):
            return inst

        async def save(self, _instance):
            pass

    processor = RoundProcessor(
        FakeRegistry(), None, None, None, None, None, None, None, None,
        None, None, None, None, 1, 1, 1,
    )

    async def fake_process_round(instance, **_kwargs):
        resumed.append(instance.game_key)
        return "narration", None

    processor.process_round = fake_process_round  # type: ignore[method-assign]
    return processor


def _push_only():
    inst, module = prepared()
    checks.last_checks(inst)[0].update(luck_decision="declined", luck_spend_available=False)
    return inst, module


@pytest.mark.parametrize("phase", ["pending", "requested", "offered"])
def test_timeout_closes_unanswered_push_with_its_original_result(phase):
    inst, module = _push_only()
    if phase != "pending":
        change(inst, module, "a", "request", reason="Use a lamp and search the seams")
    if phase == "offered":
        change(inst, module, "gm", "offer", consequence="Alarm the guards")
    before = deepcopy(checks.last_checks(inst)[0])

    assert module.close_timed_out(inst, "c1") is True

    current = checks.last_checks(inst)[0]
    assert (current["roll"], current["verdict"]) == (before["roll"], before["verdict"])
    assert current["push"]["status"] == "declined"
    assert current["push"]["decision_reason"] == "push_timeout"
    assert current["push"]["revision"] == before["push"]["revision"] + 1
    assert not module.pending(inst)
    assert module.close_timed_out(inst, "c1") is False  # 已关闭的选择不会再动


def test_luck_timeout_owns_a_check_whose_luck_is_still_open():
    inst, module = prepared()  # 幸运与孤注一掷同时待决
    assert module.close_timed_out(inst, "c1") is False
    assert module.pending(inst)


@pytest.mark.parametrize("drift", ["round", "state", "unknown_check"])
def test_timeout_never_closes_a_choice_outside_the_current_judgment(drift):
    inst, module = _push_only()
    check_id = "c1"
    if drift == "round":
        inst.round_number += 1
    elif drift == "state":
        inst.state = GameState.ACTIVE_ACTION
    else:
        check_id = "missing"
    before = deepcopy(checks.last_checks(inst)[0]["push"])

    assert module.close_timed_out(inst, check_id) is False
    assert checks.last_checks(inst)[0]["push"] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("luck_open,expected", [(True, [("c1", False)]), (False, [("c1", True)])])
async def test_scheduler_arms_one_timer_per_open_choice(luck_open, expected):
    inst, _module = prepared() if luck_open else _push_only()
    check_presentation.replace_luck_timeout_seconds(inst, 5)
    processor = _processor(inst, [])
    armed: list[tuple[str, bool]] = []

    async def record(_game_key, check_id, _timeout, *, push=False):
        armed.append((check_id, push))

    processor._luck_timeout = record  # type: ignore[method-assign]
    processor.schedule_decision_timeouts(inst)
    processor.schedule_decision_timeouts(inst)  # 已在计时的不重复挂
    await asyncio.sleep(0)

    assert armed == expected


@pytest.mark.asyncio
async def test_push_timeout_closes_the_choice_and_resumes_the_round():
    inst, _module = _push_only()
    check_presentation.replace_luck_timeout_seconds(inst, 5)
    resumed: list = []
    processor = _processor(inst, resumed)

    await processor._luck_timeout(inst.game_key, "c1", 0, push=True)

    push = checks.last_checks(inst)[0]["push"]
    assert (push["status"], push["decision_reason"]) == ("declined", "push_timeout")
    assert resumed == [inst.game_key]


@pytest.mark.asyncio
async def test_push_timeout_after_a_manual_decision_is_a_no_op():
    inst, module = _push_only()
    change(inst, module, "a", "decline")
    resumed: list = []
    processor = _processor(inst, resumed)

    await processor._luck_timeout(inst.game_key, "c1", 0, push=True)

    assert "decision_reason" not in checks.last_checks(inst)[0]["push"]
    assert resumed == []


@pytest.mark.asyncio
async def test_finished_timer_does_not_drop_a_newer_rearmed_timer():
    inst, _module = _push_only()
    processor = _processor(inst, [])
    newer = asyncio.create_task(asyncio.sleep(30))
    inst._luck_timers["c1"] = newer
    try:
        await processor._luck_timeout(inst.game_key, "c1", 0, push=True)
        assert inst._luck_timers.get("c1") is newer
    finally:
        newer.cancel()
