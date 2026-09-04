from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skip(reason="legacy pending-payment HTTP contract retired in schema 6; covered by purchase-order authority tests")
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import web_server
from src.engine.economy import queue_proposal
from src.webui.access_password import hash_access_password
from src.webui.routes.game_gameplay_routes import api_payment_resolve
from src.webui.routes.game_query_routes import api_detail

from webapi_harness import web_api  # noqa: F401


def _payment_app(api, registry) -> web.Application:
    app = web.Application(middlewares=[web_server.auth_middleware])
    app["api"] = api
    app["subsystems"] = SimpleNamespace(registry=registry)
    app["plugin_host"] = None
    app.router.add_get("/api/games/{game_key}", api_detail)
    app.router.add_post(
        "/api/games/{game_key}/payments/{payment_id}",
        api_payment_resolve,
    )
    return app


@pytest.mark.asyncio
async def test_player_share_payment_authority_with_owner_and_room_password(
    web_api,
    monkeypatch,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "HTTP Economy Authority",
        players=[
            {"character_name": "付款人", "attributes": {"str": 10}, "gold": 20},
            {"character_name": "旁观者", "attributes": {"str": 10}, "gold": 20},
        ],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    payer_uid, other_uid = list(instance.players)
    instance.gm_uid = payer_uid
    payment = queue_proposal(
        instance,
        kind="payment",
        payer_uid=payer_uid,
        recipient_uid=payer_uid,
        amount=3,
        reason="购买车票",
    )
    monkeypatch.setitem(
        web_server.STATE,
        "access_token",
        hash_access_password("owner-secret"),
    )

    app = _payment_app(api, registry)
    async with TestClient(TestServer(app)) as client:
        payer_query = {"user": payer_uid, "share": "1"}
        detail = await client.get(f"/api/games/{game_key}", params=payer_query)
        assert detail.status == 200
        assert any(
            item.get("id") == payment["id"]
            for item in (await detail.json()).get("pending_payments", [])
        )

        denied = await client.post(
            f"/api/games/{game_key}/payments/{payment['id']}",
            params={"user": other_uid, "share": "1"},
            json={"accepted": True},
        )
        assert denied.status == 403
        assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 20

        accepted = await client.post(
            f"/api/games/{game_key}/payments/{payment['id']}",
            params=payer_query,
            json={"accepted": True},
        )
        assert accepted.status == 200
        assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 17

        room_payment = queue_proposal(
            instance,
            kind="payment",
            payer_uid=payer_uid,
            recipient_uid=payer_uid,
            amount=2,
            reason="寄存行李",
        )
        instance.room_password = "configured"
        instance.room_token = "room-token"
        no_room_token = await client.post(
            f"/api/games/{game_key}/payments/{room_payment['id']}",
            params=payer_query,
            json={"accepted": True},
        )
        assert no_room_token.status == 403
        with_room_token = await client.post(
            f"/api/games/{game_key}/payments/{room_payment['id']}",
            params={**payer_query, "room_token": "room-token"},
            json={"accepted": True},
        )
        assert with_room_token.status == 200

        reward = queue_proposal(
            instance,
            kind="reward",
            recipient_uid=other_uid,
            amount=4,
            approval_policy="gm",
            reason="剧情奖励",
        )
        player_reward = await client.post(
            f"/api/games/{game_key}/payments/{reward['id']}",
            params={"user": other_uid, "share": "1", "room_token": "room-token"},
            json={"accepted": True},
        )
        assert player_reward.status == 403
        assert instance.get_character_sheet(other_uid)["currency"]["amount"] == 20

        gm_reward = await client.post(
            f"/api/games/{game_key}/payments/{reward['id']}",
            params={"user": payer_uid, "share": "1", "room_token": "room-token"},
            headers={"Authorization": "Bearer owner-secret"},
            json={"accepted": True},
        )
        assert gm_reward.status == 200
        assert instance.get_character_sheet(other_uid)["currency"]["amount"] == 24
