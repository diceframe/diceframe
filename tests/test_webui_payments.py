"""WebUI 支付与金币结算测试（自 test_webui_create_flow 拆分）。"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest


from src.commands.game_handler import GameHandler
from src.commands.tag_parser import parse_tag_state
from src.engine.economy import pending_proposals, queue_effect_group, queue_proposal
from src.engine.game_instance import GameRegistry
from src.engine.health import record_health_event
from src.llm.client import LLMResponse
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.webui.api import WebAPI, can_modify_character
from src.webui.session import SessionManager

from webapi_harness import FakeLLMClient, web_api, write_world

async def _make_game_with_pending(
    web_api,
    *,
    gold=30,
    amount=12,
    payment_id="pay_test1",
    rewards=None,
):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "艾琳",
            "race": "精灵",
            "class": "游侠",
            "attributes": {"str": 12},
            "gold": gold,
        }],
    )
    gk = result["game_key"]
    inst = registry.get(api._parse_key(gk))
    uid = next(iter(inst.players))
    inst.players[uid]["character_sheet"]["gold"] = gold
    inst.gm_uid = uid
    payment = queue_proposal(
        inst,
        kind="payment",
        payer_uid=uid,
        recipient_uid=uid,
        amount=amount,
        rewards=list(rewards or []),
        reason="GM 建议支付",
        source="gm_manual",
        source_ref=f"gm_manual:test:{payment_id}",
    )
    assert payment["id"].startswith("eco_")
    return api, gk, inst, uid, payment


@pytest.mark.asyncio
async def test_raw_gold_change_cannot_bypass_economy(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "艾琳",
            "race": "精灵",
            "class": "游侠",
            "attributes": {"str": 12},
            "gold": 30,
        }],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    assert inst is not None
    uid = next(iter(inst.players))
    cs = inst.players[uid]["character_sheet"]
    cs["gold"] = 30

    # Raw state injection is not an economic authority.
    api._handler._apply_state_update(inst, {
        "players": {uid: {"gold_change": -12}},
    })

    assert inst.players[uid]["character_sheet"]["gold"] == 30
    assert pending_proposals(inst) == []


@pytest.mark.asyncio
async def test_negative_raw_gold_change_is_ignored(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "洛恩",
            "attributes": {"str": 10},
            "gold": 20,
        }],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    assert inst is not None
    uid = next(iter(inst.players))
    inst.players[uid]["character_sheet"]["gold"] = 20

    # Even an oversized injected delta cannot mutate the wallet.
    api._handler._apply_state_update(inst, {
        "players": {uid: {"gold_change": -50}},
    })

    assert inst.players[uid]["character_sheet"]["gold"] == 20
    assert pending_proposals(inst) == []


@pytest.mark.asyncio
async def test_resolve_payment_accepted_deducts_gold(web_api):
    api, gk, inst, uid, payment = await _make_game_with_pending(web_api, gold=30, amount=12)
    res = await api.resolve_payment(gk, payment["id"], True, uid)
    assert res["ok"] is True
    assert res["accepted"] is True
    assert inst.players[uid]["character_sheet"]["gold"] == 18
    assert res["proposal"]["status"] == "committed"
    assert pending_proposals(inst) == []


@pytest.mark.asyncio
async def test_resolve_payment_rejected_adds_health_event(web_api):
    api, gk, inst, uid, payment = await _make_game_with_pending(web_api, gold=30, amount=12)
    res = await api.resolve_payment(gk, payment["id"], False, uid)
    assert res["ok"] is True
    assert res["accepted"] is False
    # 拒绝不扣金币
    assert inst.players[uid]["character_sheet"]["gold"] == 30
    # 通知 GM：健康事件
    assert any(e.get("code") == "economy_declined" for e in inst.health_events)
    assert res["proposal"]["status"] == "declined"
    assert pending_proposals(inst) == []


@pytest.mark.asyncio
async def test_resolve_payment_permission_non_owner_blocked(web_api):
    api, gk, inst, uid, payment = await _make_game_with_pending(web_api, gold=30, amount=12)
    # 非当事玩家、非 GM 不能处理
    res = await api.resolve_payment(gk, payment["id"], True, "other_user")
    assert res["ok"] is False
    assert res["code"] == "FORBIDDEN"
    # 状态未变
    assert next(p for p in pending_proposals(inst) if p["id"] == payment["id"])["status"] == "pending"
    assert inst.players[uid]["character_sheet"]["gold"] == 30


@pytest.mark.asyncio
async def test_resolve_payment_insufficient_gold(web_api):
    api, gk, inst, uid, payment = await _make_game_with_pending(
        web_api,
        gold=5,
        amount=12,
        rewards=[{"name": "解毒草", "category": ""}],
    )
    res = await api.resolve_payment(gk, payment["id"], True, uid)
    assert res["ok"] is False
    assert res["code"] == "INSUFFICIENT_FUNDS"
    assert inst.players[uid]["character_sheet"]["gold"] == 5
    assert not any(
        item.get("name") == "解毒草"
        for item in inst.players[uid]["character_sheet"].get("inventory", [])
    )
    # 余额不足：交易不成立，pending 被自动取消，避免弹窗反复出现
    assert not any(
        p["id"] == payment["id"] and p["status"] == "pending"
        for p in pending_proposals(inst)
    )


@pytest.mark.asyncio
async def test_multiplayer_payment_grants_items_to_recipient(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "多人交易",
        players=[
            {"character_name": "付款者", "attributes": {"str": 12}, "gold": 30},
            {"character_name": "接收者", "attributes": {"str": 12}, "gold": 5},
        ],
    )
    gk = result["game_key"]
    inst = registry.get(api._parse_key(gk))
    payer_uid, recipient_uid = list(inst.players)
    inst.gm_uid = payer_uid
    inst.players[payer_uid]["character_sheet"]["gold"] = 30
    payment = queue_proposal(
        inst,
        kind="payment",
        payer_uid=payer_uid,
        recipient_uid=recipient_uid,
        amount=15,
        rewards=[
            {"name": "解毒草", "category": ""},
            {"name": "止血苔", "category": ""},
        ],
        reason="替队友购买药草",
        source="gm_manual",
        source_ref="gm_manual:test:pay_multi",
    )

    resolved = await api.resolve_payment(
        gk, payment["id"], True, payer_uid
    )
    assert resolved["ok"] is True
    assert inst.players[payer_uid]["character_sheet"]["gold"] == 15
    recipient_inventory = inst.players[recipient_uid]["character_sheet"]["inventory"]
    assert {item["name"] for item in recipient_inventory} >= {"解毒草", "止血苔"}

@pytest.mark.asyncio

async def test_gm_can_create_payment_proposal_without_deduction(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world", "GM 创建提案",
        players=[{"character_name": "艾琳", "attributes": {"str": 12}, "gold": 30}],
    )
    gk = result["game_key"]
    inst = registry.get(api._parse_key(gk))
    uid = next(iter(inst.players))
    inst.gm_uid = uid

    created = await api.create_payment_proposal(
        gk,
        payer_uid=uid,
        amount=5,
        recipient_uid=uid,
        items=["通行证"],
        reason="购买通行证",
    )
    assert created["ok"] is True
    proposal = created["proposal"]
    assert proposal["approval_policy"] == "payer"
    assert proposal["kind"] == "purchase"
    assert proposal["visibility"] == "party"
    assert proposal["rewards"][0]["name"] == "通行证"
    assert inst.get_character_sheet(uid).get("gold") == 30
    assert proposal in pending_proposals(inst)


@pytest.mark.asyncio
async def test_same_reward_emission_retry_is_idempotent(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world", "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 12}, "gold": 30}],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    uid = next(iter(inst.players))
    update = parse_tag_state(
        f"艾琳完成悬赏。\n---\nGOLD:{uid}:15:完成黑石镇悬赏",
        "hp_based",
    )["state_update"]

    api._handler._apply_state_update(inst, deepcopy(update))
    api._handler._apply_state_update(inst, deepcopy(update))

    rewards = [item for item in inst.economy["proposals"] if item["kind"] == "reward"]
    assert len(rewards) == 1
    assert rewards[0]["reason"] == "完成黑石镇悬赏"


@pytest.mark.asyncio
async def test_same_reward_reason_in_later_round_creates_new_proposal(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world", "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 12}, "gold": 30}],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    uid = next(iter(inst.players))
    update = parse_tag_state(
        f"艾琳完成悬赏。\n---\nGOLD:{uid}:15:完成黑石镇悬赏",
        "hp_based",
    )["state_update"]

    api._handler._apply_state_update(inst, deepcopy(update))
    inst.round_number += 1
    api._handler._apply_state_update(inst, deepcopy(update))

    rewards = [item for item in inst.economy["proposals"] if item["kind"] == "reward"]
    assert len(rewards) == 2
    assert rewards[0]["source_ref"] != rewards[1]["source_ref"]


@pytest.mark.asyncio
async def test_later_identical_reward_does_not_drop_deferred_effects(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world", "重复任务奖励",
        players=[{"character_name": "艾琳", "attributes": {"str": 12}, "gold": 30}],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    uid = next(iter(inst.players))
    inst.gm_uid = uid
    update = parse_tag_state(
        f"艾琳完成每日委托。\n---\nGOLD:{uid}:5:完成每日委托",
        "hp_based",
    )["state_update"]

    first = api._handler._state_applier.apply_state_update(inst, deepcopy(update))
    first_group = queue_effect_group(
        inst, first, {"state_update": {"scene_change": "第一天营地"}},
    )
    assert first_group is not None
    assert (await api.resolve_payment(
        result["game_key"], first[0]["id"], True, uid,
    ))["effects_committed"] is True
    assert inst.scene == "第一天营地"

    inst.round_number += 1
    second = api._handler._state_applier.apply_state_update(inst, deepcopy(update))
    second_group = queue_effect_group(
        inst, second, {"state_update": {"scene_change": "第二天营地"}},
    )
    assert second_group is not None
    assert second[0]["id"] != first[0]["id"]
    assert (await api.resolve_payment(
        result["game_key"], second[0]["id"], True, uid,
    ))["effects_committed"] is True
    assert inst.scene == "第二天营地"

@pytest.mark.asyncio

async def test_apply_state_update_caps_loot_per_round(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world", "模板世界",
        players=[{"character_name": "艾琳"}],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    uid = next(iter(inst.players))

    api._handler._apply_state_update(inst, {
        "loot": [{"player": uid, "item": f"物品{i}"} for i in range(25)],
    })

    inventory = inst.players[uid]["character_sheet"]["inventory"]
    names = {item["name"] for item in inventory}
    assert {f"物品{i}" for i in range(20)} <= names
    assert not names & {f"物品{i}" for i in range(20, 25)}

