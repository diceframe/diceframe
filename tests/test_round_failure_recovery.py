"""判定/叙事失败后的恢复契约（真实 WebAPI + GameHandler，仅 LLM 为替身）。

回归目标（生产事故 2026-09-10 21:42 / 22:10）：
- 生成失败必须回滚到行动阶段，不能把对局永久留在 ACTIVE_JUDGMENT；
- 回滚后行动队列与已掷骰值保留，重试可以直接成功；
- 本轮仍在生成中时，推进必须返回结构化 409，而不是"空正文 + 200"；
- GM 明确强制推进时抢占在飞生成：中止 → 回滚 → 重新处理本回合。
"""

from __future__ import annotations

import asyncio

import pytest

from src.commands.round_processor import RoundProcessingFailure
from src.engine.game_instance import GameInstance, GameState
from webapi_harness import web_api  # noqa: F401  # pytest fixture


async def _new_game(api, registry):
    created = await api.create_game(
        "template_world",
        "Failure Recovery",
        players=[{"character_name": "Hero", "attributes": {"str": 12}, "gold": 20}],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    await instance.activate()
    await instance.start_round()
    return game_key, instance, uid


async def _two_player_game(api, registry):
    created = await api.create_game(
        "template_world",
        "Preemption",
        players=[
            {"character_name": "GM甲", "attributes": {"str": 12}, "gold": 20},
            {"character_name": "玩家乙", "attributes": {"str": 10}, "gold": 20},
        ],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    gm_uid, player_uid = list(instance.players)[:2]
    instance.gm_uid = gm_uid
    await instance.activate()
    await instance.start_round()
    return game_key, instance, gm_uid, player_uid


def _block_first_llm_call(llm, monkeypatch) -> asyncio.Event:
    """让第一次模型调用永远挂起（只能被取消），之后恢复正常。"""
    entered = asyncio.Event()
    original = llm.call
    calls = {"n": 0}

    async def blocking_call(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            await asyncio.Event().wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(llm, "call", blocking_call)
    return entered


@pytest.mark.asyncio
async def test_failed_round_rolls_back_then_retry_succeeds(web_api, monkeypatch) -> None:
    api, _lorebook, registry, llm, _worlds = web_api
    game_key, instance, uid = await _new_game(api, registry)
    await instance.add_action(uid, "我推开石门", selected_attribute="str")
    assert await instance.try_advance() is True

    round_before = instance.round_number
    log_before = len(instance.log)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("所有模型供应商均调用失败: 模型未返回最终正文 (finish_reason=length)")

    original_call = llm.call
    monkeypatch.setattr(llm, "call", boom)

    failed = await api.advance_turn(game_key, uid, force=True)

    assert failed["status"] == 502
    assert failed["payload"]["error_code"] == "ROUND_PROCESSING_FAILED"
    assert failed["payload"]["rolled_back"] is True
    # 关键回归：不能留在判定阶段。
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.round_number == round_before
    assert len(instance.log) == log_before
    # 行动队列保留，重试不需要玩家重新提交。
    assert [action["user_id"] for action in instance.action_queue] == [uid]

    monkeypatch.setattr(llm, "call", original_call)
    retried = await api.advance_turn(game_key, uid, force=True)

    assert retried["status"] == 200
    assert retried["payload"]["narration"]
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.round_number == round_before + 1
    assert len(instance.log) == log_before + 1
    assert instance.action_queue == []


@pytest.mark.asyncio
async def test_advance_while_processing_reports_busy_without_rollback(web_api) -> None:
    """本轮仍在生成中：返回 409 ROUND_PROCESSING_BUSY，且不动实例状态。"""
    api, _lorebook, registry, _llm, _worlds = web_api
    game_key, instance, uid = await _new_game(api, registry)
    await instance.add_action(uid, "我推开石门", selected_attribute="str")
    assert await instance.try_advance() is True
    log_before = len(instance.log)

    async with instance._process_lock:
        busy = await api.advance_turn(game_key, uid, force=True)

    assert busy["status"] == 409
    assert busy["payload"]["error_code"] == "ROUND_PROCESSING_BUSY"
    # 正在生成中的回合不得被回滚或提前提交。
    assert instance.state == GameState.ACTIVE_JUDGMENT
    assert len(instance.log) == log_before
    assert [action["user_id"] for action in instance.action_queue] == [uid]


@pytest.mark.asyncio
async def test_direct_process_round_failure_also_rolls_back(web_api, monkeypatch) -> None:
    """绕过 turns 直接调用底层 process_round 的入口失败后同样退回行动阶段。

    共享恢复边界在 RoundProcessor.process_round 上，因此幸运超时、CLI 等不经过
    Web 回合服务的入口不会把对局永久留在 ACTIVE_JUDGMENT（PR #241 审核 P1）。
    """
    api, _lorebook, registry, llm, _worlds = web_api
    _game_key, instance, uid = await _new_game(api, registry)
    await instance.add_action(uid, "我推开石门", selected_attribute="str")
    assert await instance.try_advance() is True

    async def boom(*_args, **_kwargs):
        raise RuntimeError("所有模型供应商均调用失败: 模型未返回最终正文 (finish_reason=length)")

    monkeypatch.setattr(llm, "call", boom)

    with pytest.raises(RoundProcessingFailure) as rejected:
        await api._handler.process_round(instance)

    assert rejected.value.rolled_back is True
    assert instance.state == GameState.ACTIVE_ACTION
    assert [action["user_id"] for action in instance.action_queue] == [uid]


@pytest.mark.asyncio
async def test_luck_timeout_failure_rolls_back_instead_of_sticking(web_api, monkeypatch) -> None:
    """幸运超时触发的推进失败也必须回退，不能一直卡在"生成中"（审核 P1）。"""
    api, _lorebook, registry, llm, _worlds = web_api
    _game_key, instance, uid = await _new_game(api, registry)
    await instance.add_action(uid, "我推开石门", selected_attribute="str")
    assert await instance.try_advance() is True
    await api._handler.prepare_round_checks_ai(instance)
    assert instance.last_checks, "需要一条真实检定来模拟幸运超时"
    check = instance.last_checks[-1]
    check_id = str(check["check_id"])
    check["luck_spend_available"] = True
    check["luck_cost"] = 1
    check["luck_decision"] = "pending"
    log_before = len(instance.log)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("所有模型供应商均调用失败")

    monkeypatch.setattr(llm, "call", boom)

    # 超时定时器到点：decline 最后一条幸运后自动继续推进，而推进失败。
    await api._handler._round_processor._luck_timeout(instance.game_key, check_id, 0)

    assert instance.state == GameState.ACTIVE_ACTION
    assert len(instance.log) == log_before
    assert [action["user_id"] for action in instance.action_queue] == [uid]


@pytest.mark.asyncio
async def test_force_advance_preempts_in_flight_generation(web_api, monkeypatch) -> None:
    """GM 强制推进抢占在飞生成：中止 → 回滚 → 用同一条队列重新处理本回合。

    被中止的玩家请求拿到结构化 409（preempted），而不是把对局留在"生成中"。
    """
    api, _lorebook, registry, llm, _worlds = web_api
    game_key, instance, gm_uid, player_uid = await _two_player_game(api, registry)
    await instance.add_action(gm_uid, "我警戒四周", "str")
    instance.round_checks_prepared = True  # 跳过检定规划，把挂起点锁进叙事阶段
    round_before = instance.round_number
    log_before = len(instance.log)

    entered = _block_first_llm_call(llm, monkeypatch)
    player_task = asyncio.create_task(
        api.submit_action(game_key, player_uid, "我推开石门", selected_attribute="str"),
    )
    await asyncio.wait_for(entered.wait(), 5)
    assert instance.state == GameState.ACTIVE_JUDGMENT
    assert instance.round_processing_in_flight() is True

    gm_result = await api.advance_turn(game_key, gm_uid, force=True)

    player_result = await asyncio.wait_for(player_task, 5)
    assert player_result["status"] == 409
    assert player_result["payload"]["error_code"] == "ROUND_NOT_PROCESSED"
    assert player_result["payload"]["reason"] == "preempted"

    # GM 的推进继续完成本轮：只多出一条日志，队列已清空。
    assert gm_result["status"] == 200
    assert gm_result["payload"]["narration"]
    assert instance.round_number == round_before + 1
    assert len(instance.log) == log_before + 1
    assert instance.log[-1]["round"] == round_before
    assert instance.action_queue == []
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.round_processing_in_flight() is False


@pytest.mark.asyncio
async def test_external_cancellation_rolls_back_and_reraises(web_api, monkeypatch) -> None:
    """非抢占的取消（关服/断连）同样回滚，但保留 CancelledError 语义。"""
    api, _lorebook, registry, llm, _worlds = web_api
    game_key, instance, gm_uid, player_uid = await _two_player_game(api, registry)
    await instance.add_action(gm_uid, "我警戒四周", "str")
    instance.round_checks_prepared = True

    entered = _block_first_llm_call(llm, monkeypatch)
    player_task = asyncio.create_task(
        api.submit_action(game_key, player_uid, "我推开石门", selected_attribute="str"),
    )
    await asyncio.wait_for(entered.wait(), 5)

    player_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await player_task

    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.round_processing_in_flight() is False
    assert [action["user_id"] for action in instance.action_queue] == [gm_uid, player_uid]


@pytest.mark.asyncio
async def test_cancel_round_processing_needs_an_in_flight_task() -> None:
    """没有在飞 task 时抢占是空操作，不得取消调用者自己。"""
    instance = GameInstance(game_key=("web", "no-inflight", "bot"))

    assert instance.round_processing_in_flight() is False
    assert await instance.cancel_round_processing() is False

    async with instance.track_round_processing():
        # 在飞处理就是当前 task：不能自己取消自己。
        assert instance.round_processing_in_flight() is True
        assert await instance.cancel_round_processing() is False
    assert instance.round_processing_in_flight() is False
