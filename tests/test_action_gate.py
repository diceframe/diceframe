"""R4-a admission contracts: order, purity, async retry and authority boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.engine import action_gate as gate
from src.engine.game_instance import GameInstance, GameState
from src.engine.memory_outbox import queue_memory_delivery
from src.engine.player_control import get_control, set_control
from src.webui.services import characters, ruleset_gameplay, turns


def instance_with_seats() -> GameInstance:
    instance = GameInstance(game_key=("web", "action-gate", "bot"), gm_uid="gm")
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 7
    for uid in ("actor", "human"):
        instance.put_player(uid, {
            "user_id": uid,
            "character_name": uid,
            "character_sheet": {"hp": 10, "max_hp": 10},
        })
    return instance


def ai_request(instance) -> gate.GateRequest:
    return gate.GateRequest(
        actor_uid="actor", source=gate.SOURCE_AI_SEAT,
        expected_run_id=instance.run_id,
        expected_round_number=instance.round_number,
        expected_control_revision=get_control(instance, "actor")["revision"],
        action_source="ai_player",
    )


def duplicate_action(instance) -> None:
    instance.action_queue.append({
        "user_id": "actor", "text": "already submitted",
        "metadata": {"source": "ai_player", "generated_for_round": instance.round_number},
    })


def turn_dependencies(instance, **changes) -> turns.TurnDependencies:
    return turns.TurnDependencies(
        **{
            "get_instance": lambda _key: instance,
            "parse_game_key": lambda _key: instance.game_key if instance else ("web", "missing", "bot"),
            "ruleset_registry": Mock(),
            "load_rule_for_game": lambda _instance: None,
            "prepare_round_checks_ai": None,
            "prepare_round_checks": None,
            "resolve_pending_dice": AsyncMock(),
            "roll_for_game": Mock(),
            "save_instance": AsyncMock(),
            "process_round": None,
            "resolve_luck_decision": AsyncMock(),
            "decline_pending_luck": AsyncMock(),
            **changes,
        },
    )


@pytest.mark.parametrize("check, code", [
    (gate.check_seat_exists, "PLAYER_NOT_IN_GAME"),
    (gate.check_human_control, "PLAYER_AI_CONTROLLED"),
    (gate.check_structured_intent, "STRUCTURED_INTENT_REQUIRED"),
    (gate.check_actor_deceased, "ACTOR_DECEASED"),
    (gate.check_economy, "ECONOMY_DECISION_PENDING"),
    (gate.check_not_judging, "ROUND_PROCESSING"),
    (gate.check_run_unchanged, "run_changed"),
    (gate.check_round_unchanged, "round_changed"),
    (gate.check_seat_present, "seat_removed"),
    (gate.check_ai_control_current, "control_changed"),
    (gate.check_phase_active_action, "phase_changed"),
    (gate.check_human_gate_open, "human_gate_changed"),
    (gate.check_not_duplicate_from_source, "duplicate"),
])
def test_each_check_hits_and_passes_without_mutating_instance(check, code) -> None:
    instance = instance_with_seats()
    set_control(instance, "actor", "ai")
    instance.ready_players.add("human")
    req = ai_request(instance)
    if check is gate.check_human_control:
        set_control(instance, "actor", "human")
    before = deepcopy(instance.to_dict())
    assert check(instance, req) == ""
    assert instance.to_dict() == before

    if check in (gate.check_seat_exists, gate.check_seat_present):
        instance.players.pop("actor")
    elif check is gate.check_human_control:
        set_control(instance, "actor", "ai")
    elif check is gate.check_structured_intent:
        req = replace(req, requires_structured_intent=True)
    elif check is gate.check_actor_deceased:
        instance.players["actor"]["character_sheet"]["deceased"] = True
    elif check is gate.check_economy:
        req = replace(req, economy_blocked=lambda: True)
    elif check in (gate.check_not_judging, gate.check_phase_active_action):
        instance.state = GameState.ACTIVE_JUDGMENT
    elif check is gate.check_run_unchanged:
        req = replace(req, expected_run_id="other-run")
    elif check is gate.check_round_unchanged:
        req = replace(req, expected_round_number=6)
    elif check is gate.check_ai_control_current:
        req = replace(req, expected_control_revision=99)
    elif check is gate.check_human_gate_open:
        instance.ready_players.clear()
    elif check is gate.check_not_duplicate_from_source:
        duplicate_action(instance)
    before = deepcopy(instance.to_dict())
    assert check(instance, req) == code
    assert instance.to_dict() == before


@pytest.mark.parametrize("mode, expected", [("human", ""), ("unclaimed", "PLAYER_UNCLAIMED")])
def test_human_control_preserves_other_modes(mode, expected) -> None:
    instance = instance_with_seats()
    set_control(instance, "actor", mode)
    assert gate.check_human_control(instance, gate.GateRequest("actor", gate.SOURCE_HUMAN)) == expected


@pytest.mark.parametrize("raw", [None, {"mode": "bad", "revision": -1}, {"mode": "ai", "revision": "bad"}])
def test_reading_corrupt_control_never_repairs_it(raw) -> None:
    instance = instance_with_seats()
    instance.players["actor"]["control"] = raw
    before = deepcopy(instance.players)
    gate.evaluate(instance, gate.GateRequest("actor", gate.SOURCE_HUMAN), gate.HUMAN_FREE_TEXT_POLICY)
    gate.evaluate(instance, ai_request(instance), gate.AI_SEAT_POLICY)
    assert instance.players == before


@pytest.mark.parametrize("expected", [
    "PLAYER_NOT_IN_GAME", "PLAYER_AI_CONTROLLED", "STRUCTURED_INTENT_REQUIRED",
    "ACTOR_DECEASED", "ECONOMY_DECISION_PENDING", "ROUND_PROCESSING", "",
])
def test_human_policy_first_rejection_wins_and_economy_is_lazy(expected) -> None:
    instance = instance_with_seats()
    order = ["PLAYER_NOT_IN_GAME", "PLAYER_AI_CONTROLLED", "STRUCTURED_INTENT_REQUIRED",
             "ACTOR_DECEASED", "ECONOMY_DECISION_PENDING", "ROUND_PROCESSING", ""]
    index = order.index(expected)
    if index <= 1:
        set_control(instance, "actor", "ai")
    instance.players["actor"]["character_sheet"]["deceased"] = index <= 3
    if index == 0:
        instance.players.pop("actor")
    if index <= 5:
        instance.state = GameState.ACTIVE_JUDGMENT
    economy = Mock(return_value=index <= 4)
    req = gate.GateRequest("actor", gate.SOURCE_HUMAN,
                           requires_structured_intent=index <= 2, economy_blocked=economy)
    before = deepcopy(instance.to_dict())
    assert gate.evaluate(instance, req, gate.HUMAN_FREE_TEXT_POLICY) == expected
    assert instance.to_dict() == before
    assert economy.call_count == (1 if index >= 4 else 0)


AI_REASONS = ["run_changed", "round_changed", "seat_removed", "control_changed",
              "phase_changed", "human_gate_changed", "duplicate", ""]


def ai_rejection_case(reason):
    instance = instance_with_seats()
    set_control(instance, "actor", "ai")
    req = ai_request(instance)
    index = AI_REASONS.index(reason)
    duplicate_action(instance)
    if index <= 3:
        set_control(instance, "actor", "human")
    if index <= 2:
        instance.players.pop("actor")
    if index <= 1:
        instance.round_number += 1
    if index == 0:
        instance.run_id = "new-run"
    if index <= 4:
        instance.state = GameState.PAUSED
    if index >= 6:
        instance.ready_players.add("human")
    if index == 7:
        instance.action_queue.clear()
    return instance, req


@pytest.mark.parametrize("reason", AI_REASONS)
def test_ai_full_and_stale_policies_preserve_reason_priority(reason) -> None:
    instance, req = ai_rejection_case(reason)
    before = deepcopy(instance.to_dict())
    assert gate.evaluate(instance, req, gate.AI_SEAT_POLICY) == reason
    stale_reason = reason if AI_REASONS.index(reason) < 5 else ""
    assert gate.evaluate(instance, req, gate.AI_SEAT_STALE_POLICY) == stale_reason
    assert instance.ai_player_action_stale_reason(
        "actor", expected_run_id=req.expected_run_id,
        expected_round_number=req.expected_round_number,
        expected_control_revision=req.expected_control_revision,
    ) == stale_reason
    assert instance.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", AI_REASONS)
async def test_ai_commit_checks_are_inside_authority_and_state_lock(reason, monkeypatch) -> None:
    instance, req = ai_rejection_case(reason)
    before = deepcopy(instance.to_dict())
    original = gate.evaluate
    calls = []

    def locked_evaluate(target, request, policy):
        assert target._authority_owner is asyncio.current_task()
        assert target._authority_lock.locked() and target._lock.locked()
        calls.append(True)
        return original(target, request, policy)

    monkeypatch.setattr(gate, "evaluate", locked_evaluate)
    monkeypatch.setattr("src.engine.game_instance.evaluate", locked_evaluate)
    result = await instance.commit_ai_player_action(
        "actor", "look around", source=req.action_source,
        expected_run_id=req.expected_run_id, expected_round_number=req.expected_round_number,
        expected_control_revision=req.expected_control_revision,
        action_metadata={"source": req.action_source, "generated_for_round": req.expected_round_number},
    )
    assert calls
    assert result == reason
    if reason:
        assert instance.to_dict() == before
    else:
        assert instance.action_queue[0]["text"] == "look around"


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["process", "rewrite"])
async def test_ai_outer_write_gates_precede_stale_checks(busy, monkeypatch) -> None:
    instance, req = ai_rejection_case("run_changed")
    before = deepcopy(instance.to_dict())
    evaluate = Mock(side_effect=AssertionError("must not evaluate admission"))
    monkeypatch.setattr("src.engine.game_instance.evaluate", evaluate)
    if busy == "process":
        await instance._process_lock.acquire()
    else:
        instance._rewrite_in_progress = True
    try:
        result = await instance.commit_ai_player_action(
            "actor", "look", source=req.action_source, expected_run_id=req.expected_run_id,
            expected_round_number=req.expected_round_number,
            expected_control_revision=req.expected_control_revision,
        )
    finally:
        if busy == "process":
            instance._process_lock.release()
        else:
            instance._rewrite_in_progress = False
    assert result == "rejected"
    evaluate.assert_not_called()
    assert instance.to_dict() == before


@pytest.mark.asyncio
async def test_all_ai_table_commits_once_under_concurrent_submissions() -> None:
    instance = instance_with_seats()
    for uid in instance.players:
        set_control(instance, uid, "ai")
    assert not instance.active_human_players and not instance.human_actions_ready()
    req = ai_request(instance)

    async def commit():
        return await instance.commit_ai_player_action(
            "actor", "look", source=req.action_source, expected_run_id=req.expected_run_id,
            expected_round_number=req.expected_round_number,
            expected_control_revision=req.expected_control_revision,
            action_metadata={"source": req.action_source, "generated_for_round": req.expected_round_number},
        )

    assert sorted(await asyncio.gather(commit(), commit())) == ["", "duplicate"]
    assert len(instance.action_queue) == 1


def test_ai_control_revision_and_duplicate_matching_remain_exact() -> None:
    instance = instance_with_seats()
    set_control(instance, "actor", "ai")
    instance.ready_players.add("human")
    req = ai_request(instance)
    assert gate.check_ai_control_current(instance, replace(req, expected_control_revision=None)) == ""
    assert gate.check_ai_control_current(instance, replace(req, expected_control_revision=99)) == "control_changed"
    set_control(instance, "actor", "unclaimed")
    assert gate.check_ai_control_current(instance, req) == "control_changed"
    duplicate_action(instance)
    assert gate.check_not_duplicate_from_source(instance, req) == "duplicate"
    assert gate.check_not_duplicate_from_source(instance, replace(req, action_source="other")) == ""
    assert gate.check_not_duplicate_from_source(instance, replace(req, expected_round_number=6)) == ""
    assert gate.check_run_unchanged(instance, replace(req, expected_run_id=None)) == ""
    assert gate.check_round_unchanged(instance, replace(req, expected_round_number=None)) == ""


@pytest.mark.parametrize("state", list(GameState))
@pytest.mark.parametrize("member, gm", [(False, False), (False, True), (True, False)])
def test_structured_policy_membership_precedes_judgment_gm_only_bypasses_membership(member, gm, state) -> None:
    # R5-c1 deliberately replaces R4's membership-only contract. Other phases,
    # control, death and economy remain outside structured-intent admission.
    instance = instance_with_seats()
    set_control(instance, "actor", "ai")
    instance.players["actor"]["character_sheet"]["deceased"] = True
    instance.state = state
    expected = (
        "PLAYER_NOT_IN_GAME" if not member and not gm
        else "ROUND_PROCESSING" if state == GameState.ACTIVE_JUDGMENT else ""
    )
    if not member:
        instance.players.pop("actor")
    economy = Mock(side_effect=AssertionError("intent policy must not inspect economy"))
    req = gate.GateRequest("actor", gate.SOURCE_INTENT, requester_is_gm=gm,
                           requires_structured_intent=True, economy_blocked=economy)
    before = deepcopy(instance.to_dict())
    assert gate.evaluate(instance, req, gate.STRUCTURED_INTENT_POLICY) == expected
    assert instance.to_dict() == before
    economy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", [
    "PLAYER_NOT_IN_GAME", "PLAYER_AI_CONTROLLED", "PLAYER_UNCLAIMED",
    "STRUCTURED_INTENT_REQUIRED", "ACTOR_DECEASED",
])
async def test_service_earlier_rejections_never_retry_or_query_economy(rejection) -> None:
    instance = instance_with_seats()
    # Each case also has later reasons to fail.
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.players["actor"]["character_sheet"]["deceased"] = True
    if rejection == "PLAYER_NOT_IN_GAME":
        instance.players.pop("actor")
    elif rejection == "PLAYER_AI_CONTROLLED":
        set_control(instance, "actor", "ai")
    elif rejection == "PLAYER_UNCLAIMED":
        set_control(instance, "actor", "unclaimed")
    runtime = SimpleNamespace(capabilities=SimpleNamespace(
        authoritative_intents=True, narrative_turns=False,
    ))
    drain = AsyncMock(side_effect=AssertionError("retry too early"))
    settings = Mock(side_effect=AssertionError("economy read too early"))
    deps = turn_dependencies(
        instance, drain_economy_outbox=drain, economy_auto_reward_settings=settings,
        load_rule_for_game=lambda _: None if rejection == "ACTOR_DECEASED" else SimpleNamespace(template={}),
        ruleset_registry=SimpleNamespace(resolve=lambda _: runtime),
    )
    before = deepcopy(instance.to_dict())
    result = await turns.submit_action(deps, "game", "actor", "look")
    if rejection == "PLAYER_NOT_IN_GAME":
        assert result == {"status": 403, "payload": {"error": "未加入本局，请先通过邀请链接加入"}}
    else:
        assert result["payload"]["error_code"] == rejection
    assert instance.to_dict() == before
    drain.assert_not_called()
    settings.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "ai", "unclaimed"])
async def test_runtime_unavailable_intentionally_precedes_membership_and_control(mode) -> None:
    instance = instance_with_seats()
    if mode == "missing":
        instance.players.pop("actor")
    else:
        set_control(instance, "actor", mode)
    resolve = Mock(side_effect=ValueError("runtime unavailable: exact detail"))
    drain = AsyncMock()
    deps = turn_dependencies(
        instance, ruleset_registry=SimpleNamespace(resolve=resolve),
        load_rule_for_game=lambda _: SimpleNamespace(template={}), drain_economy_outbox=drain,
    )
    before = deepcopy(instance.to_dict())
    assert await turns.submit_action(deps, "game", "actor", "look") == {
        "status": 409,
        "payload": {"ok": False, "error_code": "RULESET_RUNTIME_UNAVAILABLE",
                    "error": "runtime unavailable: exact detail"},
    }
    assert instance.to_dict() == before
    drain.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("deliver, judging", [(True, False), (True, True), (False, True)])
async def test_real_async_outbox_retry_completes_before_economy_and_phase(deliver, judging) -> None:
    instance = instance_with_seats()
    if judging:
        instance.state = GameState.ACTIVE_JUDGMENT
    delivery = queue_memory_delivery(instance, effect_group_id="eg", memory_delta={"add": ["fact"]}, round_number=7)
    assert delivery is not None
    entered, release = asyncio.Event(), asyncio.Event()
    events = []

    async def apply_memory(*_args):
        assert instance._lock.locked()
        events.append("delivery-start")
        entered.set()
        await release.wait()
        if not deliver:
            raise OSError("memory store unavailable")
        events.append("delivery-done")

    async def save(target):
        events.append("save")
        assert target is instance

    char_deps = SimpleNamespace(
        games=SimpleNamespace(get_instance=lambda _: instance, save_instance=save),
        apply_economy_memory=apply_memory, reverse_economy_memory=None,
    )

    async def drain(target):
        return await characters.drain_economy_outbox(char_deps, target)

    def settings(_instance):
        events.append("economy-query")
        assert release.is_set()
        return False, 50

    deps = turn_dependencies(instance, drain_economy_outbox=drain,
                             economy_auto_reward_settings=settings, save_instance=save)
    task = asyncio.create_task(turns.submit_action(deps, "game", "actor", "look"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        assert not task.done()
        assert events == ["delivery-start"]
        assert instance.action_queue == []
        assert delivery["status"] == "pending"
        release.set()
        result = await asyncio.wait_for(task, timeout=3)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if deliver:
        assert events[:4] == ["delivery-start", "delivery-done", "save", "economy-query"]
        assert delivery["status"] == "delivered"
        assert "payload" not in delivery
        if judging:
            assert result["payload"]["error_code"] == "ROUND_PROCESSING"
            assert instance.action_queue == []
        else:
            assert result["status"] == 200
            assert instance.action_queue[0]["text"] == "look"
    else:
        assert events == ["delivery-start", "economy-query"]
        assert result["payload"]["error_code"] == "ECONOMY_DECISION_PENDING"
        assert delivery["status"] == "pending"
        assert instance.action_queue == []


@pytest.mark.asyncio
async def test_retry_exception_propagates_before_economy_and_action() -> None:
    instance = instance_with_seats()
    drain = AsyncMock(side_effect=OSError("receipt save failed"))
    settings = Mock(side_effect=AssertionError("must not inspect after failed retry"))
    deps = turn_dependencies(instance, drain_economy_outbox=drain, economy_auto_reward_settings=settings)
    before = deepcopy(instance.to_dict())
    with pytest.raises(OSError, match="receipt save failed"):
        await turns.submit_action(deps, "game", "actor", "look")
    drain.assert_awaited_once_with(instance)
    settings.assert_not_called()
    assert instance.to_dict() == before


@pytest.mark.parametrize("code, status, payload", [
    ("PLAYER_AI_CONTROLLED", 409, {"ok": False, "error": "该角色当前由 AI 托管，真人无法提交行动（可先接管该角色）"}),
    ("PLAYER_UNCLAIMED", 409, {"ok": False, "error": "该角色尚未被认领，请先认领角色再提交行动"}),
    ("STRUCTURED_INTENT_REQUIRED", 409, {"ok": False, "error": "当前处于权威战斗，请在专业战斗工具中选择合法动作"}),
    ("ACTOR_DECEASED", 403, {"error": "角色已死亡，无法提交行动"}),
    ("ROUND_PROCESSING", 409, {"error": "本轮正在推进剧情，请等待下一轮开始", "phase": "processing"}),
])
def test_gate_response_mapping_only_adds_error_code(code, status, payload) -> None:
    assert turns._gate_rejection(instance_with_seats(), "actor", code) == {
        "status": status, "payload": {**payload, "error_code": code},
    }


def test_membership_mapping_preserves_existing_error_only_contract() -> None:
    assert turns._gate_rejection(instance_with_seats(), "outsider", "PLAYER_NOT_IN_GAME") == {
        "status": 403, "payload": {"error": "未加入本局，请先通过邀请链接加入"},
    }


def test_economy_mapping_retains_viewer_filtering_and_pending_count() -> None:
    instance = instance_with_seats()
    instance.economy["proposals"].append({
        "id": "private", "run_id": instance.run_id, "status": "pending",
        "kind": "payment", "payer_uid": "human", "visibility": "private",
    })
    assert turns._gate_rejection(instance, "actor", "ECONOMY_DECISION_PENDING") == {
        "status": 409,
        "payload": {"ok": False, "error_code": "ECONOMY_DECISION_PENDING",
                    "error": "请先处理待确认的经济提案，再继续本局叙事",
                    "pending_count": 1, "economy_proposals": []},
    }


@pytest.mark.parametrize("uid, gm, gm_uid, expected", [
    ("", True, "gm", "AUTH_REQUIRED"),
    ("session", True, "", "GM_IDENTITY_MISSING"),
    ("outsider", False, "gm", "PLAYER_NOT_IN_GAME"),
    ("session", True, "gm", ""),
    ("actor", False, "gm", ""),
])
def test_structured_context_preserves_auth_gm_and_effective_requester(uid, gm, gm_uid, expected) -> None:
    instance = instance_with_seats()
    instance.gm_uid = gm_uid
    instance.ruleset_runtime = {"id": "runtime"}
    runtime = SimpleNamespace(runtime_id="runtime", capabilities=SimpleNamespace(authoritative_intents=True))
    load = Mock(return_value=SimpleNamespace(template={}))
    deps = SimpleNamespace(get_instance=lambda _: instance, parse_game_key=lambda _: instance.game_key,
                           load_rule_for_game=load, ruleset_registry=SimpleNamespace(resolve=lambda _: runtime))
    before = deepcopy(instance.to_dict())
    result = ruleset_gameplay._context(deps, "game", uid, gm)
    if expected:
        assert result[-1]["code"] == expected
        load.assert_not_called()
        if expected == "PLAYER_NOT_IN_GAME":
            assert result[-1] == {"ok": False, "code": expected, "error": "当前玩家不在本局中"}
    else:
        assert result[-1] is None
        assert result[-2] == (gm_uid if gm else uid)
    assert instance.to_dict() == before
