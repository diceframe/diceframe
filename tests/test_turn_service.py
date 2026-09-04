from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameState
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services.turns import (
    TurnDependencies,
    advance_round,
    resolve_luck_and_continue,
    submit_action,
)


class FakeInstance:
    def __init__(self) -> None:
        self.players = {
            "gm": {"user_id": "gm", "character_name": "守密人"},
            "p2": {"user_id": "p2", "character_name": "调查员"},
        }
        self.gm_uid = "gm"
        self.state = GameState.ACTIVE_ACTION
        self.round_number = 1
        self.action_queue: list[dict] = []
        self.run_id = "run-test"
        self.economy = {
            "proposals": [],
            "effect_groups": [],
        }
        self.last_check = None
        self.last_checks: list[dict] = []
        self.quick_actions = ["观察"]
        self.last_state_update = {"scene": "门厅"}
        self.solo_mode = False
        self.dead: set[str] = set()
        self.pending_luck: list[dict] = []
        self.pending_dice = False
        self.try_advance_result = False
        self.advance_result = False
        self.accept_actions = True
        self.should_advance_result = True
        self.added: list[tuple[str, str, dict]] = []

    def is_dead(self, uid: str) -> bool:
        return uid in self.dead

    async def start_round(self) -> None:
        self.state = GameState.ACTIVE_ACTION

    async def resume(self) -> None:
        self.state = GameState.ACTIVE_ACTION

    async def add_action(self, uid: str, text: str, *_args, **kwargs) -> None:
        action = {"user_id": uid, "text": text, **kwargs}
        self.action_queue.append(action)
        self.added.append((uid, text, kwargs))

    async def try_advance(self) -> bool:
        return self.try_advance_result

    async def advance_round(self) -> bool:
        return self.advance_result

    def pending_luck_checks(self) -> list[dict]:
        return list(self.pending_luck)

    def multiplayer_status(self) -> dict:
        acted = {action.get("user_id") for action in self.action_queue}
        waiting = [player for uid, player in self.players.items() if uid not in acted]
        return {"waiting_players": waiting, "player_count": 2, "max_players": 6}

    def can_accept_actions(self) -> bool:
        return self.accept_actions

    def has_pending_dice(self) -> bool:
        return self.pending_dice

    def should_advance(self) -> bool:
        return self.should_advance_result


class FakeRegistry:
    def __init__(self, instance: FakeInstance | None) -> None:
        self.instance = instance
        self.saved = 0

    def get(self, _key):
        return self.instance

    async def save(self, _instance) -> None:
        self.saved += 1


class FakeHandler:
    def __init__(self, instance: FakeInstance | None) -> None:
        self.instance = instance
        self.processed = 0
        self.luck_after_prepare: list[dict] = []

    def prepare_round_checks(self, instance: FakeInstance) -> None:
        instance.pending_luck = list(self.luck_after_prepare)

    async def process_round(self, _instance, **_kwargs):
        self.processed += 1
        return "叙事完成", None


class FakeApi:
    def __init__(self, instance: FakeInstance | None = None) -> None:
        self._reg = FakeRegistry(instance)
        self._handler = FakeHandler(instance)
        self.check_request: dict | None = None
        self.pending_dice_result: dict = {"ok": True, "roll": {"value": 17}}
        self.roll_result: dict = {"ok": True, "value": 13}
        self.luck_result: dict = {"ok": True, "ready_to_resolve": False}
        self.declined_result: dict = {"declined_luck_decisions": []}
        self.auto_reward_settings: tuple[bool, int] = (True, 50)
        self.resolved_rewards: list[tuple[str, str, str]] = []
        self.dependencies = TurnDependencies(
            get_instance=self._reg.get,
            parse_game_key=self._parse_key,
            ruleset_registry=RulesetRuntimeRegistry(),
            load_rule_for_game=lambda _instance: None,
            prepare_round_checks_ai=None,
            prepare_round_checks=self._handler.prepare_round_checks,
            resolve_pending_dice=self.resolve_pending_dice_for_game,
            roll_for_game=self.roll_for_game,
            save_instance=self._reg.save,
            process_round=self._handler.process_round,
            resolve_luck_decision=self.resolve_luck_decision,
            decline_pending_luck=self.decline_pending_luck,
            economy_auto_reward_settings=lambda: self.auto_reward_settings,
            resolve_reward=self.resolve_reward,
        )

    async def resolve_reward(self, game_key: str, payment_id: str, session_uid: str) -> dict:
        self.resolved_rewards.append((game_key, payment_id, session_uid))
        return {"ok": True}

    @staticmethod
    def _parse_key(_key: str):
        return ("web", "room", "bot")

    def check_request_for_action(self, *_args):
        return self.check_request

    async def resolve_pending_dice_for_game(self, *_args, **_kwargs):
        return self.pending_dice_result

    def roll_for_game(self, _game_key: str):
        return self.roll_result

    async def resolve_luck_decision(self, *_args):
        return self.luck_result

    async def decline_pending_luck(self, *_args):
        return self.declined_result


@pytest.mark.asyncio
async def test_submit_action_rejects_invalid_actor_and_busy_round() -> None:
    missing_api = FakeApi(None)
    missing = await submit_action(
        missing_api.dependencies, "game", "gm", "行动",
    )
    assert missing == {"payload": {"error": "游戏不存在，请刷新页面重新开始"}, "status": 404}

    instance = FakeInstance()
    api = FakeApi(instance)
    stranger = await submit_action(api.dependencies, "game", "other", "行动")
    assert stranger["status"] == 403

    instance.dead.add("gm")
    dead = await submit_action(api.dependencies, "game", "gm", "行动")
    assert dead["status"] == 403

    instance.dead.clear()
    instance.state = GameState.ACTIVE_JUDGMENT
    busy = await submit_action(api.dependencies, "game", "gm", "行动")
    assert busy["status"] == 409
    assert busy["payload"]["phase"] == "processing"


@pytest.mark.asyncio
async def test_pending_economy_blocks_next_round_before_recording_action() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    proposal = {
        "id": "eco-pending",
        "run_id": instance.run_id,
        "status": "pending",
        "kind": "payment",
        "payer_uid": "gm",
        "visibility": "private",
    }
    instance.economy["proposals"].append(proposal)

    blocked = await submit_action(api.dependencies, "game", "gm", "继续赶路")

    assert blocked["status"] == 409
    assert blocked["payload"]["error_code"] == "ECONOMY_DECISION_PENDING"
    assert blocked["payload"]["pending_count"] == 1
    assert instance.added == []

    proposal["status"] = "declined"
    continued = await submit_action(api.dependencies, "game", "gm", "继续赶路")
    assert continued["status"] == 200
    assert instance.added


@pytest.mark.asyncio
async def test_personal_purchase_can_remain_pending_without_blocking_other_player() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    purchase = {
        "id": "purchase-pending",
        "run_id": instance.run_id,
        "status": "pending",
        "kind": "purchase",
        "approval_policy": "payer",
        "payer_uid": "gm",
        "recipient_uid": "gm",
        "rewards": [{"name": "药水", "category": "consumable"}],
        "contributors": [],
        "visibility": "private",
    }
    instance.economy["proposals"].append(purchase)

    result = await submit_action(api.dependencies, "game", "p2", "调查房门")

    assert result["status"] == 200
    assert instance.added and instance.added[0][0] == "p2"
    assert purchase["status"] == "pending"


@pytest.mark.asyncio
async def test_submit_action_records_natural_language_without_player_dice_gate() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    api.check_request = {"label": "潜行检定", "dice_system": "d100"}  # 旧启发式不再参与提交主链

    pending = await submit_action(
        api.dependencies, "game", "gm", "悄悄前进",
    )
    assert pending["payload"]["phase"] == "done"
    assert pending["payload"]["advanced"] is False
    assert "dice_pending" not in instance.action_queue[0]
    assert "调查员" in pending["payload"]["narration"]


@pytest.mark.asyncio
async def test_submit_action_pauses_for_luck_or_processes_round() -> None:
    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    api._handler.luck_after_prepare = [{"check_id": "luck-1"}]

    pending = await submit_action(api.dependencies, "game", "gm", "调查石门")
    assert pending["payload"]["phase"] == "luck"
    assert api._reg.saved == 1
    assert api._handler.processed == 0

    api._handler.luck_after_prepare = []
    completed = await submit_action(api.dependencies, "game", "p2", "观察门缝")
    assert completed["payload"]["advanced"] is True
    assert completed["payload"]["narration"] == "叙事完成"
    assert completed["payload"]["recap"] == {"scene": "门厅"}


@pytest.mark.asyncio
async def test_luck_decision_maps_errors_and_continues_when_ready() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    api.luck_result = {"ok": False, "code": "LUCK_ALREADY_RESOLVED", "error": "done"}
    conflict = await resolve_luck_and_continue(
        api.dependencies, "game", "check", "gm", True,
    )
    assert conflict["status"] == 409

    api.luck_result = {"ok": True, "round_already_resolved": True}
    already = await resolve_luck_and_continue(
        api.dependencies, "game", "check", "gm", True,
    )
    assert already["payload"]["advanced"] is True

    api.luck_result = {"ok": True, "ready_to_resolve": True, "check_result": {"verdict": "成功"}}
    completed = await resolve_luck_and_continue(
        api.dependencies, "game", "check", "gm", True,
    )
    assert completed["payload"]["phase"] == "done"
    assert completed["payload"]["narration"] == "叙事完成"
    assert api._handler.processed == 1


@pytest.mark.asyncio
async def test_advance_round_enforces_gm_and_pending_dice() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    denied = await advance_round(api.dependencies, "game", "p2")
    assert denied["status"] == 403

    instance.pending_dice = True
    blocked = await advance_round(api.dependencies, "game", "gm")
    assert blocked["payload"]["ok"] is False
    assert "等待掷骰" in blocked["payload"]["narration"]

    api.pending_dice_result = {"ok": True, "resolved": [{"user_id": "gm", "value": 17}]}
    instance.advance_result = True
    forced = await advance_round(api.dependencies, "game", "gm", force=True)
    assert forced["payload"]["ok"] is True
    assert forced["payload"]["auto_rolls"] == [{"user_id": "gm", "value": 17}]


@pytest.mark.asyncio
async def test_force_advance_recovers_judgment_and_declines_luck() -> None:
    instance = FakeInstance()
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.action_queue = [{"user_id": "gm", "text": "行动"}]
    api = FakeApi(instance)
    api._handler.luck_after_prepare = [{"check_id": "luck-1"}]
    api.declined_result = {"declined_luck_decisions": [{"check_id": "luck-1"}]}

    blocked = await advance_round(api.dependencies, "game", "gm")
    assert blocked["status"] == 409
    recovered = await advance_round(api.dependencies, "game", "gm", force=True)
    assert recovered["payload"]["narration"] == "叙事完成"
    assert recovered["payload"]["declined_luck_decisions"] == [{"check_id": "luck-1"}]


def _reward_proposal(instance, *, proposal_id: str, amount: int, uid: str = "gm") -> dict:
    proposal = {
        "id": proposal_id,
        "run_id": instance.run_id,
        "status": "pending",
        "kind": "reward",
        "approval_policy": "gm",
        "payer_uid": "",
        "recipient_uid": uid,
        "amount": amount,
        "contributors": [],
        "visibility": "private",
    }
    instance.economy["proposals"].append(proposal)
    return proposal


@pytest.mark.asyncio
async def test_qualifying_reward_auto_settles_after_round() -> None:
    """普通小额奖励在回合完成后自动结算，不需要 GM 点击。"""

    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    _reward_proposal(instance, proposal_id="reward-small", amount=12)

    result = await submit_action(api.dependencies, "game", "gm", "继续前进")

    assert result["status"] == 200
    assert api.resolved_rewards == [("game", "reward-small", "gm")]


@pytest.mark.asyncio
async def test_reward_over_cap_stays_pending_for_gm() -> None:
    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    _reward_proposal(instance, proposal_id="reward-big", amount=500)

    result = await submit_action(api.dependencies, "game", "gm", "继续前进")

    # 超上限的奖励仍是叙事屏障：行动被拦下，等 GM 处理。
    assert result["status"] == 409
    assert result["payload"]["error_code"] == "ECONOMY_DECISION_PENDING"
    assert api.resolved_rewards == []


@pytest.mark.asyncio
async def test_auto_reward_switch_off_keeps_gm_confirmation() -> None:
    """GM 总开关关闭后回到全部确认模式。"""

    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    api.auto_reward_settings = (False, 50)
    _reward_proposal(instance, proposal_id="reward-off", amount=12)

    result = await submit_action(api.dependencies, "game", "gm", "继续前进")

    # 总开关关闭：回到全部确认模式，行动被 pending 奖励拦下。
    assert result["status"] == 409
    assert result["payload"]["error_code"] == "ECONOMY_DECISION_PENDING"
    assert api.resolved_rewards == []


@pytest.mark.asyncio
async def test_auto_settle_skips_purchases_and_team_rewards() -> None:
    """购买与团队分摊不进自动结算，付款人/GM 确认语义保持不变。"""

    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    instance.economy["proposals"].append({
        "id": "purchase-1", "run_id": instance.run_id, "status": "pending",
        "kind": "purchase", "approval_policy": "payer", "payer_uid": "gm",
        "recipient_uid": "gm", "amount": 30, "contributors": [],
        "rewards": [{"name": "药水", "category": "consumable"}],
    })
    _reward_proposal(instance, proposal_id="team-reward", amount=10)
    instance.economy["proposals"][-1]["contributors"] = [
        {"uid": "gm", "amount": 5}, {"uid": "p2", "amount": 5},
    ]

    result = await submit_action(api.dependencies, "game", "gm", "继续前进")

    # 团队分摊奖励是 blocker；个人购买保持非阻塞，二者都不进自动结算。
    assert result["status"] == 409
    assert result["payload"]["error_code"] == "ECONOMY_DECISION_PENDING"
    assert api.resolved_rewards == []


@pytest.mark.asyncio
async def test_auto_reward_duplicate_settlement_is_idempotent() -> None:
    """已结算提案不会被再次自动结算；重复推进不重复入账。"""

    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    _reward_proposal(instance, proposal_id="reward-once", amount=30)

    first = await submit_action(api.dependencies, "game", "gm", "继续前进")
    assert first["status"] == 200
    assert api.resolved_rewards == [("game", "reward-once", "gm")]

    # 真实服务结算后提案变 committed；再次推进不得重复结算。
    instance.economy["proposals"][0]["status"] = "committed"
    second = await submit_action(api.dependencies, "game", "gm", "继续前进")
    assert second["status"] == 200
    assert api.resolved_rewards == [("game", "reward-once", "gm")]


@pytest.mark.asyncio
async def test_multiple_rewards_settle_independently() -> None:
    """同轮多个奖励逐条独立结算：单个失败保持 pending，不中断其余。"""

    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    _reward_proposal(instance, proposal_id="reward-a", amount=10)
    _reward_proposal(instance, proposal_id="reward-b", amount=20)

    async def flaky_resolve(game_key: str, payment_id: str, session_uid: str) -> dict:
        api.resolved_rewards.append((game_key, payment_id, session_uid))
        if payment_id == "reward-b":
            return {"ok": False, "code": "FORBIDDEN"}
        return {"ok": True}

    dependencies = replace(api.dependencies, resolve_reward=flaky_resolve)
    result = await submit_action(dependencies, "game", "gm", "继续前进")

    assert result["status"] == 200
    assert [r[1] for r in api.resolved_rewards] == ["reward-a", "reward-b"]
    by_id = {p["id"]: p for p in instance.economy["proposals"]}
    assert by_id["reward-b"]["status"] == "pending"
