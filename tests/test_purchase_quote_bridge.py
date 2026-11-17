"""未定价购买的可操作闭环：投影、自助出价来源与路由权限。

「不扣钱也不给东西」的主通道是无价购买意图静默断链（无订单 + LOOT 拦截 +
一行小字）。这里锁定三层修复：待报价意图以只读投影进入对局详情；玩家可在
卡片上以 ``player_self`` 来源自助出价（只能给自己下单）；提示词强调任何
人类报过价不得降级为 none。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.engine import economy
from src.engine.game_instance import GameInstance


def _instance() -> GameInstance:
    inst = GameInstance(game_key=("web", "quotes", "bot"), run_id="run-1")
    inst.players["alice"] = {"user_id": "alice", "character_sheet": {"gold": 99}}
    inst.players["bob"] = {"user_id": "bob", "character_sheet": {"gold": 99}}
    return inst


def test_unpriced_intents_project_into_game_detail_as_readonly_quotes():
    from src.webui.services import game_queries

    inst = _instance()
    inst.round_unpriced_purchase_intents = [
        {"payer_uid": "alice", "target": "精钢长剑", "quantity": 2},
        {"payer_uid": "", "target": "invalid", "quantity": 1},
        "garbage",
    ]
    dependencies = SimpleNamespace(
        get_instance=lambda _key: inst,
        parse_game_key=lambda _raw: inst.game_key,
        load_rule=lambda _inst: None,
    )
    detail = game_queries.game_detail(dependencies, "web|quotes|bot", viewer_uid="alice")
    assert detail is not None
    assert detail["pending_purchase_quotes"] == [
        {"payer_uid": "alice", "target": "精钢长剑", "quantity": 2},
    ]
    # 投影只读：不产生任何扣款提案。
    assert economy.pending_economy_proposals(inst) == [] or not any(
        p.get("source") == "player_self" for p in economy.pending_economy_proposals(inst)
    )


def test_player_self_purchase_offer_is_self_purchase_only():
    inst = _instance()
    proposal = economy.queue_purchase_offer(
        inst,
        payer_uid="alice",
        amount=30,
        items=["面包"],
        reason="self quote",
        source="player_self",
    )
    assert proposal["approval_policy"] == "payer"
    assert proposal["recipient_uid"] == "alice"
    assert proposal["source"] == "player_self"

    with pytest.raises(ValueError, match="unsupported purchase offer source"):
        economy.queue_purchase_offer(
            inst, payer_uid="alice", amount=30, items=["面包"], source="player_self_other",
        )


def test_price_source_prompt_forbids_silent_downgrade():
    from src.llm import tools

    body = tools.__file__
    with open(body, encoding="utf-8") as handle:
        source = handle.read()
    assert "that IS an established" in source
    assert "downgrade to 'none' merely" in source
    assert "stayed" in source


class FakeRegistry:
    def __init__(self, inst: GameInstance):
        self.inst = inst

    def get(self, _key):
        return self.inst


class FakeAPI:
    def __init__(self, registry: FakeRegistry):
        self.registry = registry
        self.created: list[dict] = []

    def get_game_instance(self, _gk):
        return self.registry.inst

    async def create_payment_proposal(self, gk, **kwargs):
        self.created.append(kwargs)
        return {"ok": True, "payment_id": "eco_self"}


def make_request(api: FakeAPI, *, user_id: str, body: dict, owner: bool = False):
    class FakeRequest:
        match_info = {"game_key": "web|quotes|bot"}
        app = {"api": api}
        method = "POST"

        def get(self, key: str, default=None):
            return {
                "user_id": user_id,
                "owner_authenticated": owner,
            }.get(key, default)

        async def json(self):
            return body

    request = FakeRequest()
    request.app["api"] = api
    return request


@pytest.mark.asyncio
async def test_payment_create_route_allows_only_self_quote_for_players():
    from src.webui.routes.game_gameplay_routes import api_payment_create

    inst = _instance()
    inst.gm_uid = "gm"
    api = FakeAPI(FakeRegistry(inst))

    req = make_request(api, user_id="alice", body={
        "payer_uid": "alice", "recipient_uid": "alice", "amount": 5,
        "items": ["面包"], "source": "player_self",
    })
    response = await api_payment_create(req)
    assert response.status == 200
    assert api.created[-1]["source"] == "player_self"

    req = make_request(api, user_id="alice", body={
        "payer_uid": "bob", "recipient_uid": "bob", "amount": 5,
        "items": ["面包"], "source": "player_self",
    })
    response = await api_payment_create(req)
    assert response.status == 403
    assert len(api.created) == 1

    req = make_request(api, user_id="alice", body={
        "payer_uid": "alice", "recipient_uid": "alice", "amount": 5,
        "items": ["面包"], "source": "gm_manual",
    })
    response = await api_payment_create(req)
    assert response.status == 403
    assert len(api.created) == 1

    # GM 仍可用任意合法来源开单。
    req = make_request(api, user_id="gm", body={
        "payer_uid": "alice", "recipient_uid": "alice", "amount": 5,
        "items": ["面包"],
    })
    response = await api_payment_create(req)
    assert response.status == 200
    assert api.created[-1]["source"] == "gm_manual"
