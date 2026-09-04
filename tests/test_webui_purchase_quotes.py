"""购买报价（purchase quote）显式确认契约测试。

覆盖 PR1 的核心差距：持久化报价必须有服务端 offer id，确认/取消是显式的
带身份端点，且结算仍只经由标准支付确认路径（一笔交易一次发货）。
"""

from __future__ import annotations

import pytest

pytest.skip(
    "Retired PR202 purchase-quote contract; covered by test_purchase_orders",
    allow_module_level=True,
)

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import web_server
from src.commands.economy_effects import (
    record_merchant_offer,
    record_purchase_clarification,
    record_purchase_quote,
    settle_purchase_quote,
)
from src.webui.routes.game_gameplay_routes import (
    api_payment_resolve,
    api_purchase_quote_cancel,
    api_purchase_quote_confirm,
)
from src.webui.routes.game_query_routes import api_detail

from webapi_harness import web_api  # noqa: F401


def _quote_app(api, registry) -> web.Application:
    app = web.Application(middlewares=[web_server.auth_middleware])
    app["api"] = api
    app["subsystems"] = SimpleNamespace(registry=registry)
    app["plugin_host"] = None
    app.router.add_get("/api/games/{game_key}", api_detail)
    app.router.add_post(
        "/api/games/{game_key}/payments/{payment_id}",
        api_payment_resolve,
    )
    app.router.add_post(
        "/api/games/{game_key}/purchase-quotes/{quote_id}/confirm",
        api_purchase_quote_confirm,
    )
    app.router.add_post(
        "/api/games/{game_key}/purchase-quotes/{quote_id}/cancel",
        api_purchase_quote_cancel,
    )
    return app


def _record_quote(instance, payer_uid: str, *, item: str = "通行证", amount: int = 5):
    instance.action_queue = [{"user_id": payer_uid, "text": f"我想买{item}"}]
    data = {"state_update": {"loot": [{"player": payer_uid, "item": item}]}}
    assert record_purchase_quote(instance, data, f"{item}售价{amount}金币。")
    return instance.economy["purchase_quotes"][-1]


@pytest.mark.asyncio
async def test_quote_confirm_creates_single_pending_proposal_for_payer(web_api):
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "购买报价确认",
        players=[
            {"character_name": "付款人", "attributes": {"str": 10}, "gold": 30},
            {"character_name": "旁观者", "attributes": {"str": 10}, "gold": 30},
        ],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    payer_uid, other_uid = list(instance.players)
    quote = _record_quote(instance, payer_uid)
    assert quote["id"].startswith("quote_")
    assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 30

    app = _quote_app(api, registry)
    async with TestClient(TestServer(app)) as client:
        payer_query = {"user": payer_uid, "share": "1"}

        detail = await client.get(f"/api/games/{game_key}", params=payer_query)
        assert detail.status == 200
        projected = (await detail.json()).get("purchase_quotes", [])
        assert [item["id"] for item in projected] == [quote["id"]]
        assert projected[0]["amount"] == 5

        # 旁观者带玩家身份也不能确认他人的报价。
        denied = await client.post(
            f"/api/games/{game_key}/purchase-quotes/{quote['id']}/confirm",
            params={"user": other_uid, "share": "1"},
        )
        assert denied.status == 403
        assert not instance.economy.get("proposals")

        confirmed = await client.post(
            f"/api/games/{game_key}/purchase-quotes/{quote['id']}/confirm",
            params=payer_query,
        )
        assert confirmed.status == 200
        proposal = (await confirmed.json())["proposal"]
        assert proposal["status"] == "pending"
        assert proposal["amount"] == 5
        assert quote["status"] == "confirmed"
        assert quote["proposal_id"] == proposal["id"]
        # 确认只创建待确认提案，不扣款、不发货。
        assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 30

        repeat = await client.post(
            f"/api/games/{game_key}/purchase-quotes/{quote['id']}/confirm",
            params=payer_query,
        )
        assert repeat.status == 200
        assert (await repeat.json())["already_resolved"] is True
        assert len(instance.economy["proposals"]) == 1

        settled = await client.post(
            f"/api/games/{game_key}/payments/{proposal['id']}",
            params=payer_query,
            json={"accepted": True},
        )
        assert settled.status == 200
        sheet = instance.get_character_sheet(payer_uid)
        assert sheet["currency"]["amount"] == 25
        # 分类与叙事路径一致：通行证是关键物品，不落普通背包。
        granted = [
            item for item in sheet.get("key_items", [])
            if item.get("name") == "通行证"
        ]
        assert len(granted) == 1
        assert not [
            item for item in sheet.get("inventory", [])
            if item.get("name") == "通行证"
        ]
        assert len(instance.economy["transactions"]) == 1


@pytest.mark.asyncio
async def test_explicit_confirm_matches_narration_confirm_delivery(web_api):
    """同一报价，显式确认与叙事确认的权威角色状态必须一致。"""

    api, _lorebook, registry, _llm, _worlds = web_api

    async def _make_game(name: str):
        created = await api.create_game(
            "template_world",
            name,
            players=[
                {"character_name": "付款人", "attributes": {"str": 10}, "gold": 30},
            ],
        )
        instance = registry.get(api._parse_key(created["game_key"]))
        uid = next(iter(instance.players))
        return created["game_key"], instance, uid

    def _record(instance, uid: str):
        instance.action_queue = [{"user_id": uid, "text": "我想买通行证"}]
        data = {"state_update": {"loot": [{"player": uid, "item": "通行证"}]}}
        assert record_purchase_quote(instance, data, "通行证售价5金币。")
        return instance.economy["purchase_quotes"][-1]

    # 叙事路径：下一轮文本确认 → state_update 提案 → 支付结算。
    narration_key, narration_inst, narration_uid = await _make_game("叙事确认")
    _record(narration_inst, narration_uid)
    narration_inst.action_queue = [{"user_id": narration_uid, "text": "行，成交"}]
    confirm_payload = {"state_update": {}}
    assert settle_purchase_quote(narration_inst, confirm_payload)
    api._handler._apply_state_update(narration_inst, confirm_payload["state_update"])
    narration_proposal = narration_inst.economy["proposals"][-1]
    assert narration_proposal["quote_id"]
    settled = await api.resolve_payment(
        narration_key, narration_proposal["id"], True, narration_uid,
    )
    assert settled["ok"] is True

    # 显式路径：HTTP 确认 → 支付结算。
    explicit_key, explicit_inst, explicit_uid = await _make_game("显式确认")
    quote = _record(explicit_inst, explicit_uid)
    confirmed = await api.confirm_purchase_quote(explicit_key, quote["id"], explicit_uid)
    assert confirmed["ok"] is True
    explicit_proposal = confirmed["proposal"]
    settled = await api.resolve_payment(
        explicit_key, explicit_proposal["id"], True, explicit_uid,
    )
    assert settled["ok"] is True

    narration_sheet = narration_inst.get_character_sheet(narration_uid)
    explicit_sheet = explicit_inst.get_character_sheet(explicit_uid)
    assert narration_sheet["currency"]["amount"] == 25
    assert explicit_sheet["currency"]["amount"] == narration_sheet["currency"]["amount"]
    assert narration_sheet.get("key_items") == explicit_sheet.get("key_items")
    assert narration_sheet.get("inventory") == explicit_sheet.get("inventory")


@pytest.mark.asyncio
async def test_quote_cancel_is_explicit_and_blocks_later_confirm(web_api):
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "购买报价取消",
        players=[
            {"character_name": "付款人", "attributes": {"str": 10}, "gold": 30},
            {"character_name": "GM 主持", "attributes": {"str": 10}, "gold": 30},
        ],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    payer_uid, gm_uid = list(instance.players)
    instance.gm_uid = gm_uid
    quote = _record_quote(instance, payer_uid)

    app = _quote_app(api, registry)
    async with TestClient(TestServer(app)) as client:
        payer_query = {"user": payer_uid, "share": "1"}
        cancelled = await client.post(
            f"/api/games/{game_key}/purchase-quotes/{quote['id']}/cancel",
            params=payer_query,
        )
        assert cancelled.status == 200
        assert quote["status"] == "cancelled"
        assert quote["resolution_code"] == "CANCELLED_BY_PAYER"
        assert instance.get_character_sheet(payer_uid)["currency"]["amount"] == 30

        late = await client.post(
            f"/api/games/{game_key}/purchase-quotes/{quote['id']}/confirm",
            params=payer_query,
        )
        assert late.status == 409
        assert not instance.economy.get("proposals")

        # GM 也能取消 open 报价。
        second = _record_quote(instance, payer_uid, item="硬皮甲", amount=260)
        gm_cancel = await client.post(
            f"/api/games/{game_key}/purchase-quotes/{second['id']}/cancel",
            params={"user": gm_uid, "share": "1"},
        )
        assert gm_cancel.status == 200
        assert second["resolution_code"] == "CANCELLED_BY_GM"


@pytest.mark.asyncio
async def test_quote_confirm_rejects_stale_run(web_api):
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "购买报价跨局拒绝",
        players=[
            {"character_name": "付款人", "attributes": {"str": 10}, "gold": 30},
        ],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    payer_uid = next(iter(instance.players))
    quote = _record_quote(instance, payer_uid)

    app = _quote_app(api, registry)
    async with TestClient(TestServer(app)) as client:
        instance.run_id = "run_new_run"
        stale = await client.post(
            f"/api/games/{game_key}/purchase-quotes/{quote['id']}/confirm",
            params={"user": payer_uid, "share": "1"},
        )
        assert stale.status == 409
        assert (await stale.json())["code"] == "STALE_RUN"
        assert not instance.economy.get("proposals")


@pytest.mark.asyncio
async def test_game_detail_projects_offers_and_clarifications(web_api):
    """商家报价全桌可见；澄清只对 GM 和对应付款人可见。"""

    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "报价与澄清投影",
        players=[
            {"character_name": "付款人", "attributes": {"str": 10}, "gold": 30},
            {"character_name": "旁观者", "attributes": {"str": 10}, "gold": 30},
        ],
    )
    game_key = created["game_key"]
    instance = registry.get(api._parse_key(game_key))
    payer_uid, other_uid = list(instance.players)
    record_merchant_offer(instance, item_display="矮人精钢剑", amount=30)
    record_purchase_clarification(
        instance, reason="OFFER_PRICE_CONFLICT", payer_uid=payer_uid,
        item_candidates=["矮人精钢剑"], amount_candidates=[20, 30],
    )

    app = _quote_app(api, registry)
    async with TestClient(TestServer(app)) as client:
        payer_view = await client.get(
            f"/api/games/{game_key}", params={"user": payer_uid, "share": "1"},
        )
        body = await payer_view.json()
        assert [offer["item_display"] for offer in body["merchant_offers"]] == ["矮人精钢剑"]
        assert body["merchant_offers"][0]["amount"] == 30
        assert len(body["clarifications"]) == 1
        assert body["clarifications"][0]["reason"] == "OFFER_PRICE_CONFLICT"

        other_view = await client.get(
            f"/api/games/{game_key}", params={"user": other_uid, "share": "1"},
        )
        other_body = await other_view.json()
        # 报价是世界事实，全桌可见；他人澄清不可见。
        assert len(other_body["merchant_offers"]) == 1
        assert other_body["clarifications"] == []


@pytest.mark.asyncio
async def test_imported_legacy_open_quote_keeps_usable_id(web_api, tmp_path):
    """导入的无 id 旧报价经迁移获得稳定 id，且在导入后仍可显式确认。"""

    import io
    import json as _json
    import zipfile

    api, _lorebook, registry, _llm, _worlds = web_api
    state = {
        "game_key": ["web", "legacy", "quote"],
        "instance_schema_version": 3,
        "run_id": "run_exported",
        "memory_namespace": "source-memory",
        "world_id": "template_world",
        "state": "paused",
        "started_at": "2025-01-01T00:00:00+00:00",
        "round_number": 5,
        "log": [],
        "players": {
            "p1": {
                "character_name": "付款人",
                "character_sheet": {"gold": 30, "currency": {"amount": 30}},
            },
        },
        "economy": {
            "schema_version": 2,
            "run_id": "run_exported",
            "purchase_quotes": [{
                "run_id": "run_exported",
                "round": 5,
                "payer_uid": "p1",
                "recipient_uid": "p1",
                "amount": 5,
                "items": ["通行证"],
                "reason": "购买商品",
                "status": "open",
            }],
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("state.json", _json.dumps(state))
        zf.writestr("chatlog.jsonl", _json.dumps({"round": 1, "content": "a"}) + "\n")
    result = await registry.import_save_zip(buffer.getvalue())
    assert result["ok"] is True
    game_key = result["game_key"]
    game_key = game_key if isinstance(game_key, str) else "|".join(game_key)
    instance = registry.get(api._parse_key(game_key))
    quote = instance.economy["purchase_quotes"][0]
    assert quote["id"].startswith("quote_")
    assert quote["run_id"] == instance.run_id != "run_exported"

    confirmed = await api.confirm_purchase_quote(game_key, quote["id"], "p1")
    assert confirmed["ok"] is True
    proposal = confirmed["proposal"]
    assert proposal["status"] == "pending"
    assert proposal["amount"] == 5
    assert quote["status"] == "confirmed"
    assert not instance.economy["transactions"]

    settled = await api.resolve_payment(game_key, proposal["id"], True, "p1")
    assert settled["ok"] is True
    sheet = instance.get_character_sheet("p1")
    assert sheet["currency"]["amount"] == 25
    assert any(
        item.get("name") == "通行证" for item in sheet.get("key_items", [])
    )
