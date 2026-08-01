from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameState
from src.webui.services.turns import advance_round, resolve_luck_and_continue, submit_action


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
        self.pending_payments: list[dict] = []
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
    missing = await submit_action(FakeApi(None), "game", "gm", "行动")
    assert missing == {"payload": {"error": "游戏不存在，请刷新页面重新开始"}, "status": 404}

    instance = FakeInstance()
    api = FakeApi(instance)
    stranger = await submit_action(api, "game", "other", "行动")
    assert stranger["status"] == 403

    instance.dead.add("gm")
    dead = await submit_action(api, "game", "gm", "行动")
    assert dead["status"] == 403

    instance.dead.clear()
    instance.state = GameState.ACTIVE_JUDGMENT
    busy = await submit_action(api, "game", "gm", "行动")
    assert busy["status"] == 409
    assert busy["payload"]["phase"] == "processing"


@pytest.mark.asyncio
async def test_submit_action_pauses_for_dice_then_returns_waiting_state() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    api.check_request = {"label": "潜行检定", "dice_system": "d100"}

    pending = await submit_action(api, "game", "gm", "悄悄前进")
    assert pending["payload"]["phase"] == "dice"
    assert instance.action_queue[0]["dice_pending"] is True

    resolved = await submit_action(api, "game", "gm", "悄悄前进", confirm=True)
    assert resolved["payload"]["advanced"] is False
    assert resolved["payload"]["roll"] == {"value": 17}
    assert "调查员" in resolved["payload"]["narration"]


@pytest.mark.asyncio
async def test_submit_action_pauses_for_luck_or_processes_round() -> None:
    instance = FakeInstance()
    instance.try_advance_result = True
    api = FakeApi(instance)
    api._handler.luck_after_prepare = [{"check_id": "luck-1"}]

    pending = await submit_action(api, "game", "gm", "调查石门")
    assert pending["payload"]["phase"] == "luck"
    assert api._reg.saved == 1
    assert api._handler.processed == 0

    api._handler.luck_after_prepare = []
    completed = await submit_action(api, "game", "p2", "观察门缝", server_roll=True, confirm=True)
    assert completed["payload"]["advanced"] is True
    assert completed["payload"]["narration"] == "叙事完成"
    assert completed["payload"]["recap"] == {"scene": "门厅"}


@pytest.mark.asyncio
async def test_luck_decision_maps_errors_and_continues_when_ready() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    api.luck_result = {"ok": False, "code": "LUCK_ALREADY_RESOLVED", "error": "done"}
    conflict = await resolve_luck_and_continue(api, "game", "check", "gm", True)
    assert conflict["status"] == 409

    api.luck_result = {"ok": True, "round_already_resolved": True}
    already = await resolve_luck_and_continue(api, "game", "check", "gm", True)
    assert already["payload"]["advanced"] is True

    api.luck_result = {"ok": True, "ready_to_resolve": True, "check_result": {"verdict": "成功"}}
    completed = await resolve_luck_and_continue(api, "game", "check", "gm", True)
    assert completed["payload"]["phase"] == "done"
    assert completed["payload"]["narration"] == "叙事完成"
    assert api._handler.processed == 1


@pytest.mark.asyncio
async def test_advance_round_enforces_gm_and_pending_dice() -> None:
    instance = FakeInstance()
    api = FakeApi(instance)
    denied = await advance_round(api, "game", "p2")
    assert denied["status"] == 403

    instance.pending_dice = True
    blocked = await advance_round(api, "game", "gm")
    assert blocked["payload"]["ok"] is False
    assert "等待掷骰" in blocked["payload"]["narration"]

    api.pending_dice_result = {"ok": True, "resolved": [{"user_id": "gm", "value": 17}]}
    instance.advance_result = True
    forced = await advance_round(api, "game", "gm", force=True)
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

    blocked = await advance_round(api, "game", "gm")
    assert blocked["status"] == 409
    recovered = await advance_round(api, "game", "gm", force=True)
    assert recovered["payload"]["narration"] == "叙事完成"
    assert recovered["payload"]["declined_luck_decisions"] == [{"check_id": "luck-1"}]
