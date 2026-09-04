"""购买意图同轮闭环与 loot 数量回归（消融实验转正）。

覆盖三层契约：
1. loot 物品串的 "xN" 数量后缀解析与同名堆叠（x5 x1 脏数据不再产生）；
2. 无价购买意图只在回合内存中存在：叙事后复检报价 + 同轮 LOOT 拦截，
   不复活 ADR 0002 已删除的 purchase_request 持久化实体；
3. 端到端：玩家说"买五瓶回复药水"时，同轮出现支付窗口、白拿货被拦、
   付款后数量正确并入原有物品行。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.commands.check_planner import (
    normalize_economy_actions,
    plan_round_checks,
    price_unpriced_purchase_intents,
)
from src.commands.state_items import (
    append_inventory_item,
    classify_item,
    grant_classified_item,
    split_item_quantity,
)
from src.commands.tag_parser import parse_tag_state
from src.engine.economy import (
    filter_unconfirmed_purchase_grants,
    has_pending_identical_purchase,
    queue_purchase_offer,
    resolve_proposal,
)
from src.engine.game_instance import GameInstance
from src.llm.client import LLMResponse

from webapi_harness import web_api  # noqa: F401


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "ablation", "bot"), gm_uid="gm")
    instance.players = {
        "p1": {"character_name": "尤落", "character_sheet": {}},
        "p2": {"character_name": "同伴", "character_sheet": {}},
    }
    return instance


# ---------- 1. 数量后缀解析与同名堆叠 ----------


def test_split_item_quantity_variants() -> None:
    assert split_item_quantity("回复药水x5") == ("回复药水", 5)
    assert split_item_quantity("回复药水×5") == ("回复药水", 5)
    assert split_item_quantity("地图 X2") == ("地图", 2)
    assert split_item_quantity("回复药水") == ("回复药水", 1)
    # 名字本身含 x 但结尾不是数量：不动。
    assert split_item_quantity("X战警手办") == ("X战警手办", 1)
    assert split_item_quantity("石头x") == ("石头x", 1)
    # 数量越界不剥离，保持原文。
    assert split_item_quantity("沙子x999") == ("沙子x999", 1)


def test_loot_tag_parses_qty_and_key_item_strips_suffix() -> None:
    data = parse_tag_state(
        "---\nLOOT:p1:回复药水x5\nKEY_ITEM:p1:下水道地图x1\nLOOT:p1:磨刀石"
    )
    loot = data["state_update"]["loot"]
    assert loot[0] == {"player": "p1", "item": "回复药水", "qty": 5}
    # 关键物品无数量语义，但后缀必须剥掉。
    assert loot[1] == {"player": "p1", "item": "下水道地图", "category": "key_item"}
    assert loot[2] == {"player": "p1", "item": "磨刀石", "qty": 1}


def test_append_inventory_item_merges_by_name_and_adopts_effect() -> None:
    sheet: dict = {"inventory": [{"name": "回复药水", "qty": 2, "effect": "恢复10点HP"}]}
    # effect 为空的授予并入原行，且不覆盖已有 effect 文案。
    append_inventory_item(sheet, "回复药水", qty=5)
    assert sheet["inventory"] == [{"name": "回复药水", "qty": 7, "effect": "恢复10点HP"}]
    # 原行 effect 为空时，授予方提供的 effect 被采纳而不是丢失。
    sheet2: dict = {"inventory": [{"name": "符纸", "qty": 1, "effect": ""}]}
    append_inventory_item(sheet2, "符纸", effect="绘制符箓消耗", qty=2)
    assert sheet2["inventory"] == [{"name": "符纸", "qty": 3, "effect": "绘制符箓消耗"}]
    # 不同品类不跨行合并。
    sheet3: dict = {"inventory": [{"name": "精钢剑", "qty": 1, "category": "equipment"}]}
    append_inventory_item(sheet3, "精钢剑", qty=1)
    assert len(sheet3["inventory"]) == 2


def test_grant_classified_item_qty_stacks_into_existing_row() -> None:
    sheet: dict = {"inventory": [{"name": "回复药水", "qty": 2, "effect": "恢复10点HP"}]}
    grant_classified_item(sheet, "回复药水", classify_item("回复药水", {}), qty=5)
    assert sheet["inventory"] == [{"name": "回复药水", "qty": 7, "effect": "恢复10点HP"}]


# ---------- 2. 无价意图：内存态、复检、拦截 ----------


def test_normalize_economy_actions_keeps_unpriced_intents() -> None:
    instance = _instance()
    offers, unpriced, errors = normalize_economy_actions(instance, [
        {"type": "purchase", "player": "p1", "target": "回复药水",
         "quantity": 5, "price_source": "none"},
        {"type": "purchase", "player": "p1", "target": "长剑"},
        # 与第一条同 payer+target+quantity：去重。
        {"type": "purchase", "player": "尤落", "target": "回复药水",
         "quantity": 5, "price_source": "none"},
        {"type": "purchase", "player": "p2", "target": "口粮",
         "amount": 3, "price_source": "player_stated"},
        {"type": "purchase", "player": "p2", "target": "弓", "amount": 5},
    ])
    assert offers == [{
        "payer_uid": "p2", "amount": 3, "quantity": 1,
        "amount_scope": "total", "target": "口粮", "note": "",
    }]
    assert unpriced == [
        {"payer_uid": "p1", "target": "回复药水", "quantity": 5},
        {"payer_uid": "p1", "target": "长剑", "quantity": 1},
    ]
    # 有 amount 却没有 price_source 的拒绝行为保持不变。
    assert errors == ["economy_actions[4] price_source='' 无效"]


@pytest.mark.asyncio
async def test_plan_round_checks_reports_unpriced_intents() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "p1", "text": "买五瓶回复药水"}]

    class _Client:
        async def call_tools(self, system_prompt, user_message, **kwargs):
            return SimpleNamespace(
                tool_calls=[{"name": "dice_checks", "arguments": {
                    "checks": [],
                    "economy_actions": [{
                        "type": "purchase", "player": "p1", "target": "回复药水",
                        "quantity": 5, "price_source": "none",
                    }],
                }}],
                total_tokens=1,
                provider_used="fake",
                native_tools=True,
            )

    _, metadata = await plan_round_checks(instance, None, _Client())
    assert metadata["economy_offers"] == []
    assert metadata["unpriced_purchase_intents"] == [
        {"payer_uid": "p1", "target": "回复药水", "quantity": 5},
    ]


def _tool_response(economy_actions: list) -> SimpleNamespace:
    return SimpleNamespace(
        tool_calls=[{"name": "dice_checks", "arguments": {
            "checks": [], "economy_actions": economy_actions,
        }}],
        total_tokens=1,
        provider_used="fake",
        native_tools=True,
    )


@pytest.mark.asyncio
async def test_price_pass_prices_intent_from_narration_only() -> None:
    instance = _instance()
    intents = [{"payer_uid": "p1", "target": "回复药水", "quantity": 5}]

    class _Client:
        async def call_tools(self, system_prompt, user_message, **kwargs):
            assert "购买价格复检" in system_prompt
            return _tool_response([
                # 复检只允许逐字转述叙述文本中的价格。
                {"type": "purchase", "player": "p1", "target": "回复药水",
                 "quantity": 5, "amount": 10, "amount_scope": "unit",
                 "price_source": "gm_narrated"},
                # 意图之外的编造记录必须被丢弃。
                {"type": "purchase", "player": "p1", "target": "马车",
                 "amount": 20, "price_source": "gm_narrated"},
            ])

    offers, remaining = await price_unpriced_purchase_intents(
        instance, _Client(), "商贩报价。", intents,
    )
    assert offers == [{
        "payer_uid": "p1", "amount": 50, "quantity": 5,
        "amount_scope": "unit", "target": "回复药水", "note": "",
    }]
    assert remaining == []


@pytest.mark.asyncio
async def test_price_pass_without_price_keeps_intent_for_interception() -> None:
    instance = _instance()
    intents = [{"payer_uid": "p1", "target": "回复药水", "quantity": 5}]

    class _Client:
        async def call_tools(self, system_prompt, user_message, **kwargs):
            return _tool_response([])

    offers, remaining = await price_unpriced_purchase_intents(
        instance, _Client(), "商贩吆喝，但没提价格。", intents,
    )
    assert offers == []
    assert remaining == intents


def test_filter_strips_unpriced_intent_items_only_with_kwarg() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [
        {"player": "p1", "item": "回复药水", "qty": 5},
        {"player": "p1", "item": "磨刀石", "qty": 1},
        {"player": "p2", "item": "回复药水", "qty": 1},
    ]}}
    intents = [{"payer_uid": "p1", "target": "回复药水", "quantity": 5}]
    # 其他调用方（swipe / rollback）不传意图：行为与旧契约完全一致。
    assert filter_unconfirmed_purchase_grants(instance, data) == 0
    assert len(data["state_update"]["loot"]) == 3
    # 同轮无价意图：只拦付款人本人的同名授予。
    removed = filter_unconfirmed_purchase_grants(
        instance, data, unpriced_purchase_intents=intents,
    )
    assert removed == 1
    assert [entry["item"] for entry in data["state_update"]["loot"]] == ["磨刀石", "回复药水"]


def test_unpriced_intents_are_never_persisted() -> None:
    instance = _instance()
    instance.round_unpriced_purchase_intents = [{
        "payer_uid": "p1", "target": "绝密目标物品", "quantity": 5,
    }]
    encoded = json.dumps(instance.to_dict(), ensure_ascii=False, default=str)
    assert "绝密目标物品" not in encoded
    assert "purchase_requests" not in encoded
    recovered = GameInstance.from_dict(instance.to_dict())
    assert recovered.round_unpriced_purchase_intents == []
    assert "purchase_requests" not in recovered.economy


def test_reset_round_checks_clears_unpriced_intents() -> None:
    instance = _instance()
    instance.round_unpriced_purchase_intents = [
        {"payer_uid": "p1", "target": "回复药水", "quantity": 5},
    ]
    instance.reset_round_checks()
    assert instance.round_unpriced_purchase_intents == []


# ---------- 3. 端到端：事故复现（web 全链路，仅 LLM 为替身） ----------


class PurchaseFlowLLMStub:
    """在 harness FakeLLMClient 上挂 call_tools 与叙事注入的最小桩。"""

    def __init__(self, base) -> None:
        self._base = base
        # 在外部覆盖 base.call 之前快照原始绑定方法，避免回退路径递归。
        self._base_call = base.call
        self.buyer_uid = ""
        self.planner_economy: list = []
        self.price_pass_economy: list = []
        self.narration = ""

    async def call_tools(self, system_prompt, user_message, **kwargs):
        if "购买价格复检" in system_prompt:
            return _tool_response(self.price_pass_economy)
        if "回合检定规划器" in system_prompt:
            return _tool_response(self.planner_economy)
        return _tool_response([])

    async def call(self, system_prompt, user_message, **kwargs):
        if "买五瓶回复药水" in user_message:
            return LLMResponse(
                content=f"{self.narration}\n---\nLOOT:{self.buyer_uid}:回复药水x5",
                narration=self.narration,
                state_update=None,
                memory_delta=None,
                info_asymmetry=None,
                plot_update=None,
                total_tokens=12,
                is_narration_only=False,
                provider_used="fake",
            )
        return await self._base_call(system_prompt, user_message, **kwargs)


def _potion_row(sheet: dict) -> dict | None:
    return next(
        (item for item in sheet["inventory"] if item.get("name") == "回复药水"), None,
    )


@pytest.mark.asyncio
async def test_same_round_purchase_shows_window_and_blocks_free_loot(web_api) -> None:
    """事故复现：无价意图 → 叙事报价 → 同轮弹出支付窗口，LOOT 白拿被拦。"""
    api, _lorebook, registry, fake_llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Same-Round Purchase",
        players=[{"character_name": "尤落", "attributes": {"str": 10}, "gold": 5000}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    sheet = instance.get_character_sheet(uid)
    sheet["inventory"] = [{"name": "回复药水", "qty": 2, "effect": "恢复10点HP"}]
    instance.set_character_sheet(uid, sheet)

    stub = PurchaseFlowLLMStub(fake_llm)
    stub.buyer_uid = uid
    stub.planner_economy = [{
        "type": "purchase", "player": uid, "target": "回复药水",
        "quantity": 5, "price_source": "none",
    }]
    stub.price_pass_economy = [{
        "type": "purchase", "player": uid, "target": "回复药水",
        "quantity": 5, "amount": 10, "amount_scope": "unit",
        "price_source": "gm_narrated",
    }]
    stub.narration = "商贩麻利地将五瓶淡红色药水用麻布包好，递到你面前。"
    fake_llm.call_tools = stub.call_tools
    fake_llm.call = stub.call

    await instance.activate()
    await instance.start_round()
    await instance.add_action(uid, "买五瓶回复药水")
    assert await instance.try_advance() is True
    narration, _private = await api._handler.process_round(instance)
    assert "五瓶" in narration

    # 同轮弹出支付窗口：50 金（10 金/瓶 ×5）。
    pending = [p for p in instance.economy["proposals"] if p["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["kind"] == "purchase"
    assert pending[0]["amount"] == 50
    assert pending[0]["payer_uid"] == uid
    # GM 的 LOOT 白拿被拦：无脏行、无多出来的数量、分文未扣。
    sheet = instance.get_character_sheet(uid)
    assert sheet["inventory"] == [{"name": "回复药水", "qty": 2, "effect": "恢复10点HP"}]
    assert sheet["gold"] == 5000
    # 意图已被报价消费。
    assert instance.round_unpriced_purchase_intents == []

    settled = await api.resolve_payment(created["game_key"], pending[0]["id"], True, uid)
    assert settled["ok"] is True
    sheet = instance.get_character_sheet(uid)
    assert sheet["gold"] == 4950
    # 数量并入原有行（2+5=7），不再出现碎片行。
    assert _potion_row(sheet) == {"name": "回复药水", "qty": 7, "effect": "恢复10点HP"}


@pytest.mark.asyncio
async def test_unpriced_purchase_never_delivers_free_items(web_api) -> None:
    """叙述里没人报过价：不弹窗、不扣款，也绝不允许 LOOT 白拿。"""
    api, _lorebook, registry, fake_llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Priceless Purchase",
        players=[{"character_name": "尤落", "attributes": {"str": 10}, "gold": 5000}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    sheet = instance.get_character_sheet(uid)
    sheet["inventory"] = [{"name": "回复药水", "qty": 2, "effect": "恢复10点HP"}]
    instance.set_character_sheet(uid, sheet)

    stub = PurchaseFlowLLMStub(fake_llm)
    stub.buyer_uid = uid
    stub.planner_economy = [{
        "type": "purchase", "player": uid, "target": "回复药水",
        "quantity": 5, "price_source": "none",
    }]
    stub.price_pass_economy = []
    stub.narration = "商贩嘿嘿一笑，说好货不愁卖。"
    fake_llm.call_tools = stub.call_tools
    fake_llm.call = stub.call

    await instance.activate()
    await instance.start_round()
    await instance.add_action(uid, "买五瓶回复药水")
    assert await instance.try_advance() is True
    await api._handler.process_round(instance)

    assert instance.economy["proposals"] == []
    sheet = instance.get_character_sheet(uid)
    assert sheet["inventory"] == [{"name": "回复药水", "qty": 2, "effect": "恢复10点HP"}]
    assert sheet["gold"] == 5000
    # 意图保持待复检状态（仅本轮内存），玩家下轮确认时 planner 可从
    # recent_narration 拾取口述价格并正常弹窗。
    assert instance.round_unpriced_purchase_intents == [
        {"payer_uid": uid, "target": "回复药水", "quantity": 5},
    ]


def test_queue_purchase_offer_idempotent_for_pass_offers() -> None:
    """复检报价与规划报价共用同一幂等入口，重放不产生重复提案。"""
    instance = _instance()
    source_ref = "ai:run:3:p1:回复药水:5:unit:50"
    first = queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["回复药水"] * 5,
        source="table_offer", source_ref=source_ref,
    )
    second = queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["回复药水"] * 5,
        source="table_offer", source_ref=source_ref,
    )
    assert first["id"] == second["id"]
    assert len(instance.economy["proposals"]) == 1


# ---------- 4. 成交后不再每回合重复弹窗 ----------


@pytest.mark.asyncio
async def test_planner_context_lists_recent_purchases() -> None:
    """planner 必须能看到已成交/已拒绝的购买，才能避免把追问当新意图。"""
    instance = _instance()
    instance.action_queue = [{"user_id": "p1", "text": "我在大厅坐着休息"}]
    instance.get_character_sheet("p1").update({
        "gold": 5000, "currency": {"amount": 5000, "base_unit": "unit", "label": "金币"},
    })
    proposal = queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["治疗药水"] * 5,
        source="gm_manual", source_ref="gm_manual:test",
    )
    resolve_proposal(instance, proposal["id"], actor_uid="p1", accepted=True,
                     grant_reward=lambda sheet, reward: None)
    captured: dict[str, str] = {}

    class _Client:
        async def call_tools(self, system_prompt, user_message, **kwargs):
            captured["user"] = user_message
            return SimpleNamespace(
                tool_calls=[], total_tokens=1, provider_used="fake", native_tools=True,
            )

    await plan_round_checks(instance, None, _Client())
    payload = json.loads(captured["user"])
    assert payload["recent_purchases"] == [{
        "round": proposal["round"], "status": "committed", "payer_id": "p1",
        "items": ["治疗药水"], "quantity": 5, "amount": 50,
    }]


def test_has_pending_identical_purchase_scope() -> None:
    instance = _instance()
    queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["治疗药水"] * 5,
        source="gm_manual", source_ref="gm_manual:dup",
    )
    # 同付款人同商品（含包含匹配）→ 待确认重复。
    assert has_pending_identical_purchase(instance, "p1", "治疗药水")
    assert has_pending_identical_purchase(instance, "p1", "治疗药水x5")
    # 其他付款人 / 已拒绝 / 已成交不算待确认重复。
    assert not has_pending_identical_purchase(instance, "p2", "治疗药水")
    assert not has_pending_identical_purchase(instance, "p1", "长剑")
    instance.economy["proposals"][0]["status"] = "committed"
    assert not has_pending_identical_purchase(instance, "p1", "治疗药水")


@pytest.mark.asyncio
async def test_duplicate_offer_not_queued_while_first_pending(web_api) -> None:
    """同一商品已有待确认提案时，下一轮不再叠第二份报价。"""
    api, _lorebook, registry, fake_llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Duplicate Offer",
        players=[{"character_name": "尤落", "attributes": {"str": 10}, "gold": 500}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    sheet = instance.get_character_sheet(uid)
    sheet["inventory"] = [{"name": "治疗药水", "qty": 1, "effect": "恢复10点HP"}]
    instance.set_character_sheet(uid, sheet)

    stub = PurchaseFlowLLMStub(fake_llm)
    stub.buyer_uid = uid
    stub.planner_economy = [{
        "type": "purchase", "player": uid, "target": "治疗药水",
        "quantity": 5, "amount": 10, "amount_scope": "unit",
        "price_source": "gm_narrated",
    }]
    stub.price_pass_economy = []
    stub.narration = "店主擦着柜台，等你拿主意。"
    fake_llm.call_tools = stub.call_tools
    fake_llm.call = stub.call

    async def play_round(action: str) -> None:
        await instance.add_action(uid, action)
        assert await instance.try_advance() is True
        await api._handler.process_round(instance)

    await instance.activate()
    await instance.start_round()
    await play_round("买5瓶治疗药水")
    pending = [p for p in instance.economy["proposals"] if p["status"] == "pending"]
    assert len(pending) == 1
    first_offer = pending[0]

    # 第二轮（process_round 结束时已自动进入下一轮）玩家只是继续话题；
    # planner 仍输出同样的报价意图，但不得叠窗。
    await play_round("再看看货架")
    pending = [p for p in instance.economy["proposals"] if p["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["id"] == first_offer["id"]


# ---------- 5. 结算文案与奖励自动结算上限 ----------


@pytest.mark.asyncio
async def test_settlement_card_avoids_double_full_stop(web_api) -> None:
    """模型书写的 reason 自带句号时，结算卡不得出现「。。」。"""
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Settlement Card Punctuation",
        players=[{"character_name": "尤落", "attributes": {"str": 10}, "gold": 500}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    instance.append_log_entry({
        "round": instance.round_number,
        "actions": [],
        "gm_response": "交易完成。",
        "state_changes": [],
    })
    from src.webui.services import characters

    characters._record_economy_outcome_in_round(instance, {
        "kind": "purchase",
        "status": "committed",
        "effects_status": "committed",
        "amount": 2,
        "reason": "玩家明确要买两根火把，GM叙事中已标明火把1金币。",
        "round": instance.round_number,
        "id": "punct-card",
    })
    card = next(
        item for item in instance.log[-1]["state_changes"] if "结算已确认" in item
    )
    assert "。。" not in card
    assert "已标明火把1金币。关联结果现已生效。" in card


def test_auto_reward_default_cap_is_relaxed(web_api) -> None:
    """奖励自动结算默认上限放宽到 10000（未显式配置的存量局直接生效）。"""
    api, _lorebook, _registry, _llm, _worlds = web_api
    enabled, cap = api.economy_auto_reward_settings()
    assert enabled is True
    assert cap == 10000
