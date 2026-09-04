from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.commands.state_items import grant_classified_item
from src.commands.state_update_applier import StateUpdateApplier
from src.engine.economy import queue_proposal, resolve_proposal
from src.engine.game_instance import GameInstance
from src.engine.purchase_orders import (
    create_purchase_order,
    deliver_purchase_order,
    filter_unordered_purchase_grants,
    pending_purchase_requests,
    record_purchase_requests,
)
from src.webui.routes.game_gameplay_routes import (
    api_payment_create,
    api_payment_resolve,
    api_purchase_order_deliver,
)

from webapi_harness import web_api  # noqa: F401


def _purchase_app(api: object, registry: object) -> web.Application:
    @web.middleware
    async def test_identity(request: web.Request, handler):
        request["user_id"] = request.query.get("user", "")
        request["owner_authenticated"] = False
        request["player_preview"] = False
        request["player_delegate"] = False
        return await handler(request)

    app = web.Application(middlewares=[test_identity])
    app["api"] = api
    app["subsystems"] = SimpleNamespace(registry=registry)
    app["plugin_host"] = None
    app.router.add_post("/api/games/{game_key}/payments", api_payment_create)
    app.router.add_post(
        "/api/games/{game_key}/payments/{payment_id}", api_payment_resolve,
    )
    app.router.add_post(
        "/api/games/{game_key}/purchase-orders/{order_id}/deliver",
        api_purchase_order_deliver,
    )
    return app


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "orders", "bot"), gm_uid="gm")
    instance.players = {
        "a": {"character_name": "A", "character_sheet": {"gold": 50, "currency": {"amount": 50}, "inventory": []}},
        "b": {"character_name": "B", "character_sheet": {"gold": 50, "currency": {"amount": 50}, "inventory": []}},
    }
    return instance


def _grant(sheet: dict, reward: dict) -> None:
    grant_classified_item(sheet, str(reward.get("name") or ""), "consumable")


def test_multiplayer_requests_and_orders_stay_actor_scoped() -> None:
    instance = _instance()
    requests = record_purchase_requests(
        instance,
        [
            {"user_id": "a", "text": "我买钢剑"},
            {"user_id": "b", "text": "我买盔甲"},
        ],
        language="zh-CN",
    )
    assert [item["actor_uid"] for item in requests] == ["a", "b"]
    assert "钢剑" in requests[0]["item_hint"]
    assert "盔甲" in requests[1]["item_hint"]

    order_a, proposal_a = create_purchase_order(
        instance,
        payer_uid="a",
        amount=15,
        items=["钢剑"],
        request_id=requests[0]["id"],
    )
    order_b, proposal_b = create_purchase_order(
        instance,
        payer_uid="b",
        amount=15,
        items=["盔甲"],
        request_id=requests[1]["id"],
    )
    assert proposal_a["sequence"] < proposal_b["sequence"]

    assert resolve_proposal(instance, proposal_b["id"], actor_uid="b", accepted=True, grant_reward=_grant)["ok"]
    assert resolve_proposal(instance, proposal_a["id"], actor_uid="a", accepted=True, grant_reward=_grant)["ok"]
    assert instance.get_character_sheet("a")["currency"]["amount"] == 35
    assert instance.get_character_sheet("b")["currency"]["amount"] == 35
    assert instance.economy["purchase_orders"][0]["id"] == order_a["id"]
    assert instance.economy["purchase_orders"][1]["id"] == order_b["id"]


def test_repeated_order_creation_for_same_request_is_idempotent() -> None:
    instance = _instance()
    request = record_purchase_requests(
        instance,
        [{"user_id": "a", "text": "我买钢剑"}],
        language="zh-CN",
    )[0]
    first_order, first_proposal = create_purchase_order(
        instance,
        payer_uid="a",
        amount=15,
        items=["钢剑"],
        request_id=request["id"],
    )
    repeated_order, repeated_proposal = create_purchase_order(
        instance,
        payer_uid="a",
        amount=999,
        items=["错误商品"],
        request_id=request["id"],
    )
    assert repeated_order["id"] == first_order["id"]
    assert repeated_proposal["id"] == first_proposal["id"]
    assert repeated_order["amount"] == 15
    assert repeated_order["items"] == ["钢剑"]
    assert len(instance.economy["purchase_orders"]) == 1
    assert len(instance.economy["proposals"]) == 1


def test_unrelated_narration_amount_cannot_price_or_grant_purchase() -> None:
    instance = _instance()
    record_purchase_requests(
        instance,
        [{"user_id": "a", "text": "我买钢剑"}],
        language="zh-CN",
    )
    data = {
        "state_update": {
            "loot": [{"player": "a", "item": "钢剑"}],
            "economy_proposals": [],
        },
    }
    assert filter_unordered_purchase_grants(instance, data) == 1
    assert data["state_update"]["loot"] == []
    assert instance.economy["proposals"] == []
    assert "钢剑" in pending_purchase_requests(instance)[0]["item_hint"]


def test_declined_or_insufficient_order_reopens_original_request() -> None:
    instance = _instance()
    request = record_purchase_requests(
        instance,
        [{"user_id": "a", "text": "我买钢剑"}],
        language="zh-CN",
    )[0]
    _order, proposal = create_purchase_order(
        instance,
        payer_uid="a",
        amount=15,
        items=["钢剑"],
        request_id=request["id"],
    )
    result = resolve_proposal(instance, proposal["id"], actor_uid="a", accepted=False, grant_reward=_grant)
    assert result["ok"] and result["accepted"] is False
    assert pending_purchase_requests(instance)[0]["id"] == request["id"]

    _order2, proposal2 = create_purchase_order(
        instance,
        payer_uid="a",
        amount=100,
        items=["精钢剑"],
        request_id=request["id"],
    )
    result = resolve_proposal(instance, proposal2["id"], actor_uid="a", accepted=True, grant_reward=_grant)
    assert result["code"] == "INSUFFICIENT_FUNDS"
    assert pending_purchase_requests(instance)[0]["id"] == request["id"]


def test_deferred_delivery_charges_once_and_delivers_once() -> None:
    instance = _instance()
    request = record_purchase_requests(
        instance,
        [{"user_id": "a", "text": "我买铁剑，付钱后明天交付"}],
        language="zh-CN",
    )[0]
    order, proposal = create_purchase_order(
        instance,
        payer_uid="a",
        amount=20,
        items=["铁剑"],
        request_id=request["id"],
        delivery_mode="deferred",
        delivery_condition="铁匠明天打好后交付",
    )
    settled = resolve_proposal(
        instance, proposal["id"], actor_uid="a", accepted=True, grant_reward=_grant,
    )
    assert settled["ok"]
    assert instance.get_character_sheet("a")["currency"]["amount"] == 30
    assert instance.get_character_sheet("a")["inventory"] == []
    assert order["status"] == "paid"

    delivered = deliver_purchase_order(instance, order["id"], grant_reward=_grant)
    assert delivered["ok"]
    assert instance.get_character_sheet("a")["currency"]["amount"] == 30
    assert len(instance.get_character_sheet("a")["inventory"]) == 1
    repeated = deliver_purchase_order(instance, order["id"], grant_reward=_grant)
    assert repeated["ok"] and repeated["already_delivered"]
    assert instance.get_character_sheet("a")["currency"]["amount"] == 30
    assert len(instance.get_character_sheet("a")["inventory"]) == 1


def test_model_payment_proposals_are_ignored_but_rewards_remain_pending() -> None:
    instance = _instance()
    applier = StateUpdateApplier(__import__("pathlib").Path("templates/rules"), None, lambda *_: {})
    result = applier.apply_state_update(instance, {
        "economy_proposals": [
            {"kind": "payment", "uid": "a", "amount": 15, "reason": "模型乱收费"},
            {"kind": "purchase", "uid": "a", "amount": 20, "rewards": [{"name": "钢剑"}]},
            {"kind": "reward", "uid": "a", "amount": 5, "reason": "完成任务"},
        ],
    })
    assert len(result) == 1
    assert result[0]["kind"] == "reward"
    assert instance.economy["proposals"] == result
    assert instance.get_character_sheet("a")["currency"]["amount"] == 50


def test_chargeable_proposals_require_explicit_gm_order() -> None:
    instance = _instance()
    with pytest.raises(ValueError, match="explicit GM order"):
        queue_proposal(
            instance,
            kind="payment",
            payer_uid="a",
            amount=1,
            source="narrative",
        )
    with pytest.raises(ValueError, match="explicit order"):
        queue_proposal(
            instance,
            kind="purchase",
            payer_uid="a",
            amount=1,
            source="gm_manual",
        )


def test_equipment_acquisition_does_not_replace_active_weapon() -> None:
    instance = _instance()
    sheet = instance.get_character_sheet("a")
    sheet["equipment"] = [{"name": "铁剑", "slot": "main_hand", "type": "weapon", "damage": 3}]
    applier = StateUpdateApplier(__import__("pathlib").Path("templates/rules"), None, lambda *_: {})
    applier.apply_state_update(instance, {
        "loot": [{"player": "a", "item": "二手战斧", "category": "equipment"}],
    })
    updated = instance.get_character_sheet("a")
    assert updated["equipment"][0]["name"] == "铁剑"
    assert any(item["name"] == "二手战斧" for item in updated["inventory"])


@pytest.mark.asyncio
async def test_purchase_order_http_flow_and_gm_only_delivery(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "订单 HTTP 流程",
        players=[
            {"character_name": "付款人", "attributes": {"str": 10}, "gold": 50},
            {"character_name": "旁观者", "attributes": {"str": 10}, "gold": 50},
        ],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    payer_uid, other_uid = list(instance.players)
    instance.gm_uid = payer_uid
    app = _purchase_app(api, registry)

    async with TestClient(TestServer(app)) as client:
        gm_query = {"user": payer_uid, "share": "1"}
        created_order = await client.post(
            f"/api/games/{game_key}/payments",
            params=gm_query,
            json={
                "payer_uid": payer_uid,
                "recipient_uid": payer_uid,
                "amount": 20,
                "items": ["铁剑"],
                "delivery_mode": "deferred",
                "delivery_condition": "明天打好",
            },
        )
        assert created_order.status == 200
        payload = await created_order.json()
        order = payload["order"]
        proposal = payload["proposal"]
        assert order["status"] == "pending"
        assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 50

        confirmed = await client.post(
            f"/api/games/{game_key}/payments/{proposal['id']}",
            params=gm_query,
            json={"accepted": True},
        )
        assert confirmed.status == 200
        assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 30
        assert instance.get_character_sheet(payer_uid)["inventory"] == []

        denied = await client.post(
            f"/api/games/{game_key}/purchase-orders/{order['id']}/deliver",
            params={"user": other_uid, "share": "1"},
        )
        assert denied.status == 403

        delivered = await client.post(
            f"/api/games/{game_key}/purchase-orders/{order['id']}/deliver",
            params=gm_query,
        )
        assert delivered.status == 200
        assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 30
        sheet = instance.get_character_sheet(payer_uid)
        assert sum(
            1
            for field in ("inventory", "equipment", "key_items")
            for item in sheet.get(field, [])
            if item.get("name") == "铁剑"
        ) == 1

        repeated = await client.post(
            f"/api/games/{game_key}/purchase-orders/{order['id']}/deliver",
            params=gm_query,
        )
        assert repeated.status == 200
        assert (await repeated.json()).get("already_delivered") is True
        sheet = instance.get_character_sheet(payer_uid)
        assert sum(
            1
            for field in ("inventory", "equipment", "key_items")
            for item in sheet.get(field, [])
            if item.get("name") == "铁剑"
        ) == 1
