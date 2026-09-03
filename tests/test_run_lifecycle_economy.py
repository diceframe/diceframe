from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from src.engine.economy import (
    blocking_economy_proposals,
    has_blocking_economy_decision,
    is_nonblocking_personal_purchase,
    pending_memory_deliveries,
    pending_memory_reversals,
    proposal_transition_allowed,
    queue_effect_group,
    queue_proposal,
    reconcile_rollback_snapshot,
    reverse_round_economy,
    resolve_proposal,
    set_proposal_status,
    _remove_reward_delta,
)
from src.commands.economy_effects import (
    close_purchase_quote,
    discard_unearned_reward_proposals,
    discard_unbacked_purchase_items,
    guard_unbacked_payment_narration,
    link_purchase_quote_proposal,
    match_open_merchant_offers,
    record_merchant_offer,
    record_purchase_clarification,
    repair_unbacked_purchase,
    defer_narrative_effects,
    record_purchase_quote,
    settle_purchase_quote,
)
from src.commands.state_items import append_key_item
from src.commands.state_update_applier import StateUpdateApplier
from src.engine.intent.evidence import record_evidence
from src.engine.game_instance import GameInstance, GameRegistry, restore_players, _snapshot_players
from src.llm.client import LLMResponse
from src.llm.context_builder import build_context
from src.memory.delta import MemoryStore
from src.migrations.instance import migrate_game_state_payload
from src.webui.services import characters

from webapi_harness import web_api  # noqa: F401


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "economy", "bot"), gm_uid="gm")
    instance.players = {
        "gm": {"character_name": "GM", "character_sheet": {"gold": 30, "currency": {"amount": 30}}},
        "p2": {"character_name": "P2", "character_sheet": {"gold": 20, "currency": {"amount": 20}}},
    }
    return instance


def test_only_plain_personal_purchase_is_nonblocking() -> None:
    instance = _instance()
    purchase = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "药水", "category": "consumable"}],
    )
    assert is_nonblocking_personal_purchase(instance, purchase)
    assert blocking_economy_proposals(instance) == []


def test_conditional_narrative_reward_is_not_queued_before_task_completion() -> None:
    instance = _instance()
    data = {
        "state_update": {
            "economy_proposals": [{
                "kind": "reward", "uid": "gm", "amount": 15,
                "reason": "完成药剂师委托的报酬",
            }],
        },
    }
    dropped = discard_unearned_reward_proposals(
        instance, data, "你要是能帮我清干净，15 金币一个子儿不少。",
    )
    assert dropped == 1
    assert data["state_update"]["economy_proposals"] == []


def test_reward_without_completion_evidence_is_not_queued() -> None:
    instance = _instance()
    data = {
        "state_update": {
            "economy_proposals": [{
                "kind": "reward", "uid": "gm", "amount": 15,
                "reason": "任务报酬",
            }],
        },
    }
    assert discard_unearned_reward_proposals(instance, data, "药剂师向你说明报酬安排。") == 1
    assert data["state_update"]["economy_proposals"] == []


def test_reward_is_eligible_when_same_turn_marks_quest_completed() -> None:
    instance = _instance()
    data = {
        "state_update": {
            "economy_proposals": [{
                "kind": "reward", "uid": "gm", "amount": 15,
                "reason": "完成药剂师委托的报酬",
            }],
        },
        "plot_update": {"quests": [{"title": "药剂师委托", "status": "completed"}]},
    }
    assert discard_unearned_reward_proposals(
        instance, data, "如果你完成了委托，药剂师会支付报酬；但本轮已确认任务完成。",
    ) == 0
    assert len(data["state_update"]["economy_proposals"]) == 1
    assert not has_blocking_economy_decision(instance)

    team = queue_proposal(
        instance,
        kind="fee",
        amount=10,
        approval_policy="all_contributors",
        contributors=[{"uid": "gm", "amount": 5}, {"uid": "p2", "amount": 5}],
    )
    assert not is_nonblocking_personal_purchase(instance, team)
    assert team in blocking_economy_proposals(instance)
    assert has_blocking_economy_decision(instance)


def test_narrated_payment_without_protocol_is_explicitly_not_charged() -> None:
    narration = "你从怀里掏出五枚金币，放在柜台上。"
    guarded = guard_unbacked_payment_narration(narration, {}, "zh-CN")
    assert narration in guarded
    assert "未扣除金币" in guarded


def test_narrated_payment_with_proposal_keeps_pending_notice_path() -> None:
    narration = "你支付了五枚金币。"
    data = {"state_update": {"pending_payments": [{"uid": "gm", "amount": 5}]}}
    assert guard_unbacked_payment_narration(narration, data, "zh-CN") == narration


def test_unbacked_shop_price_does_not_grant_loot() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    dropped = discard_unbacked_purchase_items(
        instance, data, "城门卫兵说通行证需要支付5金币。"
    )
    assert dropped == 1
    assert data["state_update"]["loot"] == []
    # fail-closed 的结构保留：澄清记录包含候选商品与叙事价格。
    clarification = instance.economy["clarifications"][0]
    assert clarification["status"] == "open"
    assert clarification["item_candidates"] == ["通行证"]
    assert clarification["amount_candidates"] == [5]
    assert clarification["reason"] == "MISSING_SELLER_PRICE_CONFIRMATION"


def test_purchase_loot_is_kept_when_proposal_exists() -> None:
    instance = _instance()
    data = {
        "state_update": {
            "loot": [{"player": "gm", "item": "通行证"}],
            "economy_proposals": [{"kind": "purchase", "uid": "gm", "amount": 5}],
        },
    }
    assert discard_unbacked_purchase_items(instance, data, "通行证需要支付5金币。") == 0
    assert data["state_update"]["loot"]
    assert not instance.economy.get("clarifications")


def test_explicit_purchase_with_omitted_pay_tag_becomes_pending_proposal() -> None:
    """商品与价格同句：缺 PAY 也合成待确认提案。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下硬皮甲"}]
    data = {
        "state_update": {
            "players": {"gm": {"equip_gain": "硬皮甲"}},
        },
    }
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根把硬皮甲递给你，硬皮甲二十五枚金币，钱货两讫。"
    )
    assert dropped == 0
    assert ambiguous is False
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["kind"] == "purchase"
    assert proposal["amount"] == 25
    assert proposal["amount_source"] == "narration"
    assert proposal["items"] == ["硬皮甲"]


def test_purchase_price_in_separate_sentence_clarifies() -> None:
    """商品与价格不同句且行动无金额：价格归属不成立 → 澄清而非猜测。

    真实案例：叙事"数出二十五枚金币"+"授予硬皮甲"分处两句，全局唯一金额
    曾被错误绑给商品（25），而相邻轮次的任务悬赏（40金）也会被同样误绑。
    """

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下硬皮甲"}]
    data = {
        "state_update": {
            "players": {"gm": {"equip_gain": "硬皮甲"}},
        },
    }
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "你从钱袋里数出二十五枚金币，放在柜台上。"
    )
    assert (dropped, ambiguous) == (1, True)
    assert not data["state_update"].get("economy_proposals")
    clarification = instance.economy["clarifications"][0]
    assert clarification["reason"] == "AMBIGUOUS_PRICE"
    assert 25 in clarification["amount_candidates"]


def test_repaired_purchase_consumes_bound_grant_for_single_delivery() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下治疗药水"}]
    data = {
        "state_update": {
            "loot": [
                {"player": "gm", "item": "治疗药水"},
                {"player": "gm", "item": "任务赠品"},
            ],
        },
    }
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "治疗药水需要5金币。", actions=instance.action_queue,
    )
    assert dropped == 0 and ambiguous is False
    assert data["state_update"]["economy_proposals"][0]["items"] == ["治疗药水"]
    assert data["state_update"]["loot"] == [{"player": "gm", "item": "任务赠品"}]


def test_repaired_purchase_defers_dependent_player_state_but_keeps_item_grants() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下治疗药水并喝掉"}]
    data = {
        "state_update": {
            "economy_proposals": [{
                "kind": "purchase", "uid": "gm", "amount": 5,
                "source": "server_purchase_guard",
            }],
            "loot": [{"player": "gm", "item": "任务赠品"}],
            "players": {"gm": {"hp_change": 10, "equip_gain": "无关护符"}},
            "scene_change": "不应立即进入",
        },
    }

    class Response:
        state_update = data["state_update"]
        memory_delta = {}
        info_asymmetry = {}
        plot_update = {}

    deferred = defer_narrative_effects(data, Response(), defer_state_update=False)

    assert data["state_update"]["loot"] == [{"player": "gm", "item": "任务赠品"}]
    assert data["state_update"]["players"] == {"gm": {"equip_gain": "无关护符"}}
    assert "scene_change" not in data["state_update"]
    assert deferred["state_update"]["players"] == {"gm": {"hp_change": 10}}
    assert deferred["state_update"]["scene_change"] == "不应立即进入"


def test_purchase_repair_uses_explicit_historical_actions() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "p2", "text": "买下另一件物品"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "治疗药水"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance,
        data,
        "治疗药水需要5金币。",
        actions=[{"user_id": "gm", "text": "买下治疗药水"}],
    )
    assert dropped == 0 and ambiguous is False
    assert data["state_update"]["economy_proposals"][0]["uid"] == "gm"


def test_ambiguous_purchase_without_pay_tag_drops_item_fail_closed() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下硬皮甲"}]
    data = {"state_update": {"players": {"gm": {"equip_gain": "硬皮甲"}}}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "店里有五金币的药水和二十五金币的硬皮甲。"
    )
    assert dropped == 1
    assert ambiguous is True
    assert "equip_gain" not in data["state_update"]["players"]["gm"]


def test_purchase_guard_uses_ruleset_currency_labels() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "购买通行证"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance,
        data,
        "柜台收取三枚灵石。",
        currency_labels=("spirit_shard", "灵石"),
    )
    # 跨句 + 无行动金额：澄清的价格候选证明灵石被当作货币解析。
    assert (dropped, ambiguous) == (1, True)
    clarification = instance.economy["clarifications"][0]
    assert clarification["amount_candidates"] == [3]
    assert not data["state_update"].get("economy_proposals")


def test_purchase_quote_can_be_confirmed_on_next_turn() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [{"player": "gm", "item": "硬皮甲"}]}}
    assert record_purchase_quote(instance, data, "硬皮甲售价260金币。")
    instance.action_queue = [{"user_id": "gm", "text": "行成交"}]
    confirm_data = {"state_update": {}}
    assert settle_purchase_quote(instance, confirm_data)
    proposal = confirm_data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 260
    assert proposal["items"] == ["硬皮甲"]
    assert confirm_data["state_update"].get("loot", []) == []
    # 确认后报价保留为审计条目，只有 open 报价可再次结算。
    assert instance.economy["purchase_quotes"][0]["status"] == "confirmed"


def test_purchase_quote_requires_purchase_semantics_not_bare_currency() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [{"player": "gm", "item": "短剑"}]}}
    assert not record_purchase_quote(instance, data, "你找到短剑。你还剩50金币。")
    assert instance.economy.get("purchase_quotes", []) == []


def test_persisted_purchase_quote_wins_over_later_narration() -> None:
    instance = _instance()
    first = {"state_update": {"loot": [{"player": "gm", "item": "短剑"}]}}
    assert record_purchase_quote(instance, first, "短剑售价260金币。")
    instance.action_queue = [{"user_id": "gm", "text": "成交"}]
    confirmation = {"state_update": {}}
    assert settle_purchase_quote(instance, confirmation)
    proposal = confirmation["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 260
    assert proposal["items"] == ["短剑"]
    assert confirmation["state_update"].get("loot", []) == []


def test_confirmed_quote_overrides_conflicting_llm_purchase() -> None:
    instance = _instance()
    first = {"state_update": {"loot": [{"player": "gm", "item": "护甲"}]}}
    assert record_purchase_quote(instance, first, "护甲售价260金币。")
    instance.action_queue = [{"user_id": "gm", "text": "成交"}]
    confirmation = {"state_update": {
        "economy_proposals": [
            {"kind": "purchase", "uid": "gm", "amount": 300, "items": ["护甲"]},
            {"kind": "purchase", "uid": "gm", "amount": 2, "items": ["药水"]},
        ],
        "loot": [{"player": "gm", "item": "护甲"}],
    }}
    assert settle_purchase_quote(instance, confirmation)
    purchases = confirmation["state_update"]["economy_proposals"]
    assert len(purchases) == 2
    quoted = next(item for item in purchases if item["items"] == ["护甲"])
    assert quoted["amount"] == 260
    assert any(item["items"] == ["药水"] for item in purchases)
    assert confirmation["state_update"]["loot"] == []


def test_unrelated_shop_price_does_not_quote_other_loot() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [{"player": "gm", "item": "短剑"}]}}
    assert not record_purchase_quote(instance, data, "你找到短剑。药水售价5金币。")
    assert data["state_update"]["loot"] == [{"player": "gm", "item": "短剑"}]


def test_purchase_intent_for_other_item_does_not_quote_unrelated_loot() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "我想买药水"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "短剑"}]}}
    assert not record_purchase_quote(instance, data, "你找到一把短剑。你还有50金币。")
    assert data["state_update"]["loot"] == [{"player": "gm", "item": "短剑"}]


def test_item_and_price_in_same_offer_records_quote() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [{"player": "gm", "item": "硬皮甲"}]}}
    assert record_purchase_quote(instance, data, "商人把硬皮甲推到你面前：硬皮甲售价260金币。")
    quote = instance.economy["purchase_quotes"][0]
    assert quote["amount"] == 260
    assert quote["items"] == ["硬皮甲"]
    assert quote["payer_uid"] == "gm"
    assert quote["run_id"] == instance.run_id


def test_quote_confirmation_removes_model_repeated_purchase_item() -> None:
    instance = _instance()
    first = {"state_update": {"loot": [{"player": "gm", "item": "短剑"}]}}
    assert record_purchase_quote(instance, first, "短剑售价260金币。")
    instance.action_queue = [{"user_id": "gm", "text": "成交"}]
    confirmation = {
        "state_update": {
            "loot": [{"player": "gm", "item": "短剑"}],
            "players": {"gm": {"equip_gain": "短剑"}},
        },
    }
    assert settle_purchase_quote(instance, confirmation)
    assert confirmation["state_update"]["loot"] == []
    assert confirmation["state_update"]["players"]["gm"] == {}


def test_origin_round_rollback_discards_open_purchase_quote() -> None:
    instance = _instance()
    instance.round_number = 5
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    assert record_purchase_quote(instance, data, "通行证售价5金币。")
    assert instance.economy["purchase_quotes"]
    reverse_round_economy(instance, 5)
    quote = instance.economy["purchase_quotes"][0]
    assert quote["status"] == "cancelled"
    assert quote["resolution_code"] == "ORIGIN_ROLLED_BACK"


def test_round_zero_rollback_discards_open_purchase_quote() -> None:
    instance = _instance()
    instance.round_number = 0
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    assert record_purchase_quote(instance, data, "通行证售价5金币。")
    reverse_round_economy(instance, 0)
    assert instance.economy["purchase_quotes"][0]["status"] == "cancelled"


def test_purchase_quote_keeps_single_open_offer_over_history() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    assert record_purchase_quote(instance, data, "通行证售价5金币。")
    quote = instance.economy["purchase_quotes"][0]
    assert quote["id"].startswith("quote_")
    # 历史条目（已确认/已取消）不会阻止新报价，但同一时刻仍最多一个 open。
    instance.action_queue = [{"user_id": "gm", "text": "行，成交"}]
    assert settle_purchase_quote(instance, {"state_update": {}})
    assert quote["status"] == "confirmed"
    assert not settle_purchase_quote(instance, {"state_update": {}})
    assert record_purchase_quote(instance, {"state_update": {"loot": [{"player": "gm", "item": "硬皮甲"}]}}, "硬皮甲售价260金币。")
    open_quotes = [
        item for item in instance.economy["purchase_quotes"]
        if item.get("status", "open") == "open"
    ]
    assert len(open_quotes) == 1


def test_proposal_status_transitions_are_whitelisted() -> None:
    instance = _instance()
    assert proposal_transition_allowed("pending", "committed")
    assert proposal_transition_allowed("committed", "reversed")
    assert proposal_transition_allowed("committed", "pending")
    for terminal in ("declined", "cancelled", "rejected", "reversed", "superseded"):
        assert not proposal_transition_allowed(terminal, "committed")
        assert not proposal_transition_allowed(terminal, "pending")
        assert not proposal_transition_allowed(terminal, "cancelled")
    with pytest.raises(ValueError):
        set_proposal_status({"status": "declined"}, "committed")

    proposal = queue_proposal(
        instance, kind="payment", payer_uid="gm", recipient_uid="gm", amount=1,
    )
    declined = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=False)
    assert declined["ok"] is True
    for accepted in (True, False):
        repeat = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=accepted)
        assert repeat["code"] == "ALREADY_RESOLVED"


def test_queue_proposal_rejects_payer_outside_game() -> None:
    instance = _instance()
    with pytest.raises(ValueError):
        queue_proposal(instance, kind="purchase", payer_uid="ghost", amount=5)
    with pytest.raises(ValueError):
        queue_proposal(instance, kind="transfer", payer_uid="ghost", amount=5)
    # 奖励类提案没有付款人；收款人资格在结算时校验。
    reward = queue_proposal(
        instance, kind="reward", recipient_uid="p2", amount=5, approval_policy="gm",
    )
    assert reward["status"] == "pending"


def test_typed_merchant_offer_persists_without_grant() -> None:
    instance = _instance()
    applier = StateUpdateApplier(Path("nonexistent-rules"), None, lambda world_id, language: {})
    applier.apply_state_update(instance, {
        "merchant_offers": [
            {
                "item_display": "矮人精钢剑", "amount": 30,
                "seller_id": "npc_hogen", "currency_id": "gold",
            },
            {"item_display": "", "amount": 5},
            {"item_display": "药膏", "amount": -1},
        ],
    })
    offers = instance.economy["merchant_offers"]
    assert len(offers) == 1
    offer = offers[0]
    assert offer["id"].startswith("offer_")
    assert offer["item_display"] == "矮人精钢剑"
    assert offer["amount"] == 30
    assert offer["run_id"] == instance.run_id
    assert offer["status"] == "open"
    # 已持久化的卖家报价不会被重新叙述覆盖价格。
    applier.apply_state_update(instance, {
        "merchant_offers": [{"item_display": "矮人精钢剑", "amount": 99}],
    })
    assert len(instance.economy["merchant_offers"]) == 1
    assert instance.economy["merchant_offers"][0]["amount"] == 30


def test_player_action_price_synthesizes_pending_proposal() -> None:
    """叙事没复述价格、玩家行动自带唯一金额：出待确认提案，确认前不动钱。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "掏30金币买下精钢剑"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根接过金币，转身取下那柄矮人精钢剑，递到你面前。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 30
    assert proposal["amount_source"] == "player_action"
    assert proposal["items"] == ["矮人精钢剑"]
    assert data["state_update"]["loot"] == []
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 30


def test_narration_price_outranks_action_price() -> None:
    """商家叙事价（点名商品）优先于玩家自报价。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "掏20金币买下精钢剑"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根咧嘴：“矮人精钢剑三十金币。”你把钱放下了。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 30
    assert proposal["amount_source"] == "narration"


def test_pronoun_price_falls_back_to_player_action() -> None:
    """叙事用代词指代商品（"这剑三十金币"）时无法按句绑定：
    回落玩家行动自报金额并经其确认，而不是把无关数字绑给商品。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "掏20金币买下精钢剑"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根咧嘴：“这剑三十金币。”你把钱放下了。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 20
    assert proposal["amount_source"] == "player_action"


def test_merchant_offer_price_is_authoritative_when_it_matches() -> None:
    instance = _instance()
    record_merchant_offer(instance, item_display="矮人精钢剑", amount=30)
    instance.action_queue = [{"user_id": "gm", "text": "掏30金币买下精钢剑"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根收了钱，把剑递到你面前。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 30
    assert proposal["amount_source"] == "merchant_offer"


def test_player_amount_conflicting_with_offer_clarifies() -> None:
    """stored offer=30，玩家喊 20：不出 20 的提案，进澄清（还价语义）。"""

    instance = _instance()
    record_merchant_offer(instance, item_display="矮人精钢剑", amount=30)
    instance.action_queue = [{"user_id": "gm", "text": "掏20金币买下精钢剑"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根皱起了眉头。",
    )
    assert (dropped, ambiguous) == (1, True)
    assert not data["state_update"].get("economy_proposals")
    assert data["state_update"]["loot"] == []
    clarification = instance.economy["clarifications"][0]
    assert clarification["reason"] == "OFFER_PRICE_CONFLICT"
    assert clarification["payer_uid"] == "gm"
    assert clarification["item_candidates"] == ["矮人精钢剑"]
    assert clarification["amount_candidates"] == [20, 30]


def test_prose_sale_without_grants_persists_clarification() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "我买下这把剑"}]
    data = {"state_update": {}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "你掏出三十金币递了过去，商家点头把剑包好。",
    )
    # 无 grant 的意图按 actor 记为澄清，repair 已接管（ambiguous=True 跳过 discard）。
    assert (dropped, ambiguous) == (0, True)
    assert not data["state_update"].get("economy_proposals")
    clarification = instance.economy["clarifications"][0]
    assert clarification["reason"] == "MISSING_SELLER_PRICE_CONFIRMATION"
    assert clarification["payer_uid"] == "gm"
    assert clarification["amount_candidates"] == [30]
    assert clarification["status"] == "open"


def test_clarification_dedupes_identical_open_entries() -> None:
    instance = _instance()
    first = record_purchase_clarification(
        instance, reason="AMBIGUOUS_PRICE", payer_uid="gm",
        item_candidates=["矮人精钢剑"], amount_candidates=[20],
    )
    second = record_purchase_clarification(
        instance, reason="AMBIGUOUS_PRICE", payer_uid="gm",
        item_candidates=["矮人精钢剑"], amount_candidates=[20],
    )
    assert first["id"] == second["id"]
    assert len(instance.economy["clarifications"]) == 1


def test_merchant_offer_matching_requires_exact_or_suffix() -> None:
    """绑定只认精确匹配或后缀变体；包含关系不得误继承商家价格。"""

    instance = _instance()
    variant = record_merchant_offer(instance, item_display="精钢剑", amount=30)
    sword_sheath = record_merchant_offer(instance, item_display="长剑鞘", amount=5)
    assert record_merchant_offer(instance, item_display="铁剑", amount=25) is not None

    # 精确匹配与后缀变体（修饰语在前、中心语在后）可绑定。
    assert [offer["id"] for offer in match_open_merchant_offers(instance, ["精钢剑"])] == [variant["id"]]
    assert [offer["id"] for offer in match_open_merchant_offers(instance, ["矮人精钢剑"])] == [variant["id"]]
    # "长剑鞘" 精确命中自己的报价，且不得因包含关系再命中 "长剑"。
    assert [offer["id"] for offer in match_open_merchant_offers(instance, ["长剑鞘"])] == [sword_sheath["id"]]
    # 典型误绑定陷阱：碎片/鞘类 purchase 不得继承武器报价。
    assert match_open_merchant_offers(instance, ["铁剑碎片"]) == []
    assert match_open_merchant_offers(instance, ["长剑"]) == []
    assert match_open_merchant_offers(instance, ["圣剑"]) == []


def test_queue_proposal_rejects_unknown_amount_source() -> None:
    instance = _instance()
    with pytest.raises(ValueError):
        queue_proposal(
            instance, kind="payment", payer_uid="gm", amount=1,
            amount_source="fabricated",
        )
    proposal = queue_proposal(
        instance, kind="payment", payer_uid="gm", amount=1,
        amount_source="merchant_offer",
    )
    assert proposal["amount_source"] == "merchant_offer"


def test_rollback_supersedes_open_offers_and_clarifications() -> None:
    instance = _instance()
    instance.round_number = 5
    record_merchant_offer(instance, item_display="矮人精钢剑", amount=30)
    record_purchase_clarification(
        instance, reason="AMBIGUOUS_PRICE", payer_uid="gm",
        item_candidates=["矮人精钢剑"], amount_candidates=[20],
    )
    reverse_round_economy(instance, 5)
    assert instance.economy["merchant_offers"][0]["status"] == "superseded"
    assert instance.economy["merchant_offers"][0]["resolution_code"] == "ORIGIN_ROLLED_BACK"
    assert instance.economy["clarifications"][0]["status"] == "superseded"
    # 其他轮次的 open 条目不受影响。
    instance.round_number = 6
    later_offer = record_merchant_offer(instance, item_display="硬皮甲", amount=260)
    assert later_offer["status"] == "open"
    reverse_round_economy(instance, 6)
    assert later_offer["status"] == "superseded"
    assert instance.economy["merchant_offers"][0]["status"] == "superseded"


def test_purchase_quote_confirmation_requires_payer_and_current_run() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [{"player": "gm", "item": "硬皮甲"}]}}
    assert record_purchase_quote(instance, data, "硬皮甲售价260金币。")
    instance.action_queue = [{"user_id": "p2", "text": "行成交"}]
    assert not settle_purchase_quote(instance, {"state_update": {}})
    assert instance.economy["purchase_quotes"]
    instance.action_queue = [{"user_id": "gm", "text": "行成交"}]
    instance.run_id = "new-run"
    assert not settle_purchase_quote(instance, {"state_update": {}})


def test_purchase_quote_does_not_bundle_multiple_players() -> None:
    instance = _instance()
    data = {"state_update": {"loot": [
        {"player": "gm", "item": "药水"},
        {"player": "p2", "item": "卷轴"},
    ]}}
    assert not record_purchase_quote(instance, data, "商品共需10金币。")
    assert instance.economy.get("purchase_quotes", []) == []


def test_personal_purchase_with_effect_group_remains_blocking() -> None:
    instance = _instance()
    purchase = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
    )
    queue_effect_group(instance, [purchase], {"state_update": {"scene_change": "城门内"}})
    assert not is_nonblocking_personal_purchase(instance, purchase)
    assert has_blocking_economy_decision(instance)


def test_save_migration_assigns_stable_run_and_imports_pending_payment() -> None:
    legacy = {
        "game_key": ["web", "legacy", "bot"],
        "state": "paused",
        "started_at": "2025-01-01T00:00:00+00:00",
        "pending_payments": [{"id": "pay_old", "uid": "p1", "amount": 3, "status": "pending"}],
    }
    first = migrate_game_state_payload(legacy)
    second = migrate_game_state_payload(first)

    assert first == second
    assert first["instance_schema_version"] == 4
    assert first["economy"]["external_effects_outbox"] == []
    assert first["run_id"].startswith("run_")
    assert first["memory_namespace"] == "('web', 'legacy', 'bot')"
    assert first["economy"]["proposals"][0]["id"] == "pay_old"


def test_narrative_reward_requires_gm_and_commits_once() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="reward",
        recipient_uid="p2",
        amount=5,
        approval_policy="gm",
        source="narrative",
        source_ref="round:1:reward:p2:5",
    )

    forbidden = resolve_proposal(instance, proposal["id"], actor_uid="p2", accepted=True)
    accepted = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=True)
    duplicate = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=True)

    assert forbidden["code"] == "FORBIDDEN"
    assert accepted["ok"] is True
    assert instance.get_character_sheet("p2")["currency"]["amount"] == 25
    assert len(instance.economy["transactions"]) == 1
    assert sum(
        entry["delta"] for entry in accepted["transaction"]["entries"]
    ) == 0
    assert duplicate["code"] == "ALREADY_RESOLVED"


def _grant_inventory_reward(sheet: dict, reward: dict) -> None:
    inventory = sheet.setdefault("inventory", [])
    inventory.append({"name": str(reward.get("name") or ""), "qty": 1})


def _grant_key_item_reward(sheet: dict, reward: dict) -> None:
    append_key_item(sheet, str(reward.get("name") or ""))


def test_quote_origin_rollback_reverses_late_confirmed_purchase() -> None:
    """R5 报价 → R6 显式确认并支付；回滚 R5 必须级联撤销 R6 结算。"""

    instance = _instance()
    instance.round_number = 5
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    assert record_purchase_quote(instance, data, "通行证售价5金币。")
    quote = instance.economy["purchase_quotes"][0]
    instance.round_number = 6
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
        source="server_purchase_quote",
        source_ref=f"purchase_quote:{quote['id']}",
        approval_policy="payer",
        quote_id=quote["id"],
    )
    link_purchase_quote_proposal(instance, quote["id"], proposal["id"])
    assert close_purchase_quote(
        instance, quote["id"], status="confirmed", resolution_code="CONFIRMED_BY_PAYER",
    ) is not None
    settled = resolve_proposal(
        instance,
        proposal["id"],
        actor_uid="gm",
        accepted=True,
        grant_reward=_grant_key_item_reward,
    )
    assert settled["ok"] is True
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 25
    assert any(
        item.get("name") == "通行证"
        for item in instance.get_character_sheet("gm").get("key_items", [])
    )

    reverse_round_economy(instance, 5)
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 30
    assert not any(
        item.get("name") == "通行证"
        for item in instance.get_character_sheet("gm").get("key_items", [])
    )
    assert all(
        transaction["status"] == "reversed"
        for transaction in instance.economy["transactions"]
    )
    assert proposal["status"] == "reversed"
    assert quote["status"] == "superseded"
    assert quote["resolution_code"] == "ORIGIN_ROLLED_BACK"
    assert quote["id"]


def test_quote_origin_rollback_preserves_later_unrelated_currency_change() -> None:
    """选择性回滚只撤销目标交易：30 → 买-5 → 无关-2 → 回滚 R5 应为 28。

    反向 delta 叠加到当前余额，而不是写绝对 before 快照——否则后发生的
    合法交易效果会被一并抹掉。
    """

    instance = _instance()
    instance.round_number = 5
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    assert record_purchase_quote(instance, data, "通行证售价5金币。")
    quote = instance.economy["purchase_quotes"][0]
    instance.round_number = 6
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
        source="server_purchase_quote",
        source_ref=f"purchase_quote:{quote['id']}",
        approval_policy="payer",
        quote_id=quote["id"],
    )
    link_purchase_quote_proposal(instance, quote["id"], proposal["id"])
    assert close_purchase_quote(
        instance, quote["id"], status="confirmed", resolution_code="CONFIRMED_BY_PAYER",
    ) is not None
    settled = resolve_proposal(
        instance,
        proposal["id"],
        actor_uid="gm",
        accepted=True,
        grant_reward=_grant_key_item_reward,
    )
    assert settled["ok"] is True
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 25

    # R7：与报价无关的合法支出。
    instance.round_number = 7
    unrelated = queue_proposal(
        instance,
        kind="fee",
        payer_uid="gm",
        amount=2,
        reason="客栈住宿",
    )
    unrelated_settled = resolve_proposal(
        instance, unrelated["id"], actor_uid="gm", accepted=True,
    )
    assert unrelated_settled["ok"] is True
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 23

    reverse_round_economy(instance, 5)

    # 只撤销目标购买：23 + 5 = 28，不是绝对 before 的 30。
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 28
    assert proposal["status"] == "reversed"
    assert quote["status"] == "superseded"
    assert quote["resolution_code"] == "ORIGIN_ROLLED_BACK"
    assert not any(
        item.get("name") == "通行证"
        for item in instance.get_character_sheet("gm").get("key_items", [])
    )
    # R7 无关交易保持已结算，未受波及。
    assert unrelated["status"] == "committed"
    statuses = {
        transaction["id"]: transaction["status"]
        for transaction in instance.economy["transactions"]
    }
    assert statuses[settled["transaction"]["id"]] == "reversed"
    assert statuses[unrelated_settled["transaction"]["id"]] == "committed"


def test_quote_origin_rollback_reverses_narration_confirmed_purchase() -> None:
    """叙事确认路径必须与显式路径共享同一 origin 回滚语义。"""

    instance = _instance()
    instance.round_number = 5
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    assert record_purchase_quote(instance, data, "通行证售价5金币。")
    quote = instance.economy["purchase_quotes"][0]
    instance.round_number = 6
    instance.action_queue = [{"user_id": "gm", "text": "行，成交"}]
    confirm_payload = {"state_update": {}}
    assert settle_purchase_quote(instance, confirm_payload)
    applier = StateUpdateApplier(Path("nonexistent-rules"), None, lambda world_id, language: {})
    queued = applier.apply_state_update(instance, confirm_payload["state_update"])
    assert len(queued) == 1
    assert queued[0]["quote_id"] == quote["id"]
    assert quote["proposal_id"] == queued[0]["id"]
    settled = resolve_proposal(
        instance,
        queued[0]["id"],
        actor_uid="gm",
        accepted=True,
        grant_reward=_grant_key_item_reward,
    )
    assert settled["ok"] is True
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 25

    reverse_round_economy(instance, 5)
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 30
    assert not any(
        item.get("name") == "通行证"
        for item in instance.get_character_sheet("gm").get("key_items", [])
    )
    assert queued[0]["status"] == "reversed"
    assert quote["status"] == "superseded"
    assert quote["resolution_code"] == "ORIGIN_ROLLED_BACK"


def test_legacy_open_purchase_quote_gets_stable_id_on_load() -> None:
    legacy = {
        "game_key": ["web", "legacy", "quote"],
        "instance_schema_version": 3,
        "run_id": "run_legacy",
        "state": "paused",
        "started_at": "2025-01-01T00:00:00+00:00",
        "players": {
            "p1": {
                "character_name": "付款人",
                "character_sheet": {"gold": 30, "currency": {"amount": 30}},
            },
        },
        "economy": {
            "schema_version": 2,
            "run_id": "run_legacy",
            "purchase_quotes": [{
                "run_id": "run_legacy",
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
    migrated = migrate_game_state_payload(legacy)
    quote = migrated["economy"]["purchase_quotes"][0]
    assert quote["id"].startswith("quote_")
    # 同一存档重复迁移得到同一身份，save/reload 后不变。
    assert migrate_game_state_payload(migrated)["economy"]["purchase_quotes"][0]["id"] == quote["id"]
    instance = GameInstance.from_dict(migrated)
    assert instance.economy["purchase_quotes"][0]["id"] == quote["id"]
    reloaded = GameInstance.from_dict(instance.to_dict())
    assert reloaded.economy["purchase_quotes"][0]["id"] == quote["id"]
    # 迁移后的报价可被显式确认契约寻址并转换。
    proposal = queue_proposal(
        reloaded,
        kind="purchase",
        payer_uid="p1",
        recipient_uid="p1",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
        source="server_purchase_quote",
        source_ref=f"purchase_quote:{quote['id']}",
        approval_policy="payer",
        quote_id=quote["id"],
    )
    assert close_purchase_quote(
        reloaded, quote["id"], status="confirmed", resolution_code="CONFIRMED_BY_PAYER",
    ) is not None
    assert proposal["status"] == "pending"


def test_late_personal_purchase_reopens_when_settlement_round_is_rolled_back() -> None:
    instance = _instance()
    instance.round_number = 5
    purchase = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
    )
    assert purchase["round"] == 5
    instance.round_number = 7
    before = deepcopy(instance.get_character_sheet("gm"))
    committed = resolve_proposal(
        instance,
        purchase["id"],
        actor_uid="gm",
        accepted=True,
        grant_reward=_grant_inventory_reward,
    )
    assert committed["transaction"]["round"] == 7
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 25
    assert instance.get_character_sheet("gm")["inventory"]
    assert purchase["status"] == "committed"

    # The real round-start snapshot may be captured after a late settlement.
    post_settlement_snapshot = _snapshot_players(instance)

    reverse_round_economy(instance, 7)
    # The offer originated in R5, so a R7 settlement rollback reopens it.
    assert purchase["status"] == "pending"
    assert purchase in instance.pending_payments
    assert not has_blocking_economy_decision(instance)
    assert all(tx["status"] == "reversed" for tx in instance.economy["transactions"])
    assert not any(
        outcome.get("status") == "committed"
        and outcome.get("proposal_id") == purchase["id"]
        for outcome in instance.economy["outcomes"]
    )

    # Reconcile the post-settlement snapshot before restoring it, as the real
    # GM rollback and swipe paths do.
    restore_players(instance, reconcile_rollback_snapshot(instance, post_settlement_snapshot, 7))
    assert instance.get_character_sheet("gm")["currency"]["amount"] == before["currency"]["amount"]
    assert instance.get_character_sheet("gm")["inventory"] == before.get("inventory", [])

    # Verify the reopened proposal can settle exactly once after restoration.
    retry = resolve_proposal(
        instance,
        purchase["id"],
        actor_uid="gm",
        accepted=True,
        grant_reward=_grant_inventory_reward,
    )
    assert retry["ok"] is True
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 25
    assert len(instance.economy["transactions"]) == 2
    assert sum(tx["status"] == "committed" for tx in instance.economy["transactions"]) == 1


def test_origin_round_rollback_invalidates_purchase_paid_in_later_round() -> None:
    instance = _instance()
    instance.round_number = 5
    purchase = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
    )
    instance.round_number = 7
    resolve_proposal(
        instance,
        purchase["id"],
        actor_uid="gm",
        accepted=True,
        grant_reward=_grant_inventory_reward,
    )

    reverse_round_economy(instance, 5)
    assert purchase["status"] == "reversed"
    assert all(tx["status"] == "reversed" for tx in instance.economy["transactions"])
    assert purchase not in instance.pending_payments
    assert not any(
        outcome.get("proposal_id") == purchase["id"]
        and outcome.get("status") == "committed"
        for outcome in instance.economy["outcomes"]
    )


@pytest.mark.asyncio
async def test_gm_rollback_restores_late_purchase_character_and_ledger_consistently(tmp_path) -> None:
    """Exercise the real GameInstance rollback path after a late settlement."""
    instance = _instance()
    instance.round_number = 5
    purchase = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
    )
    instance.round_number = 7
    pre_payment = deepcopy(instance.get_character_sheet("gm"))
    settled = resolve_proposal(
        instance,
        purchase["id"],
        actor_uid="gm",
        accepted=True,
        grant_reward=_grant_inventory_reward,
    )
    assert settled["transaction"]["round"] == 7
    # This deliberately captures the edge-case snapshot after payment.
    instance.log.append({
        "round": 7,
        "actions": [],
        "gm_response": "late purchase",
        "round_start_snapshot": _snapshot_players(instance),
    })
    instance.round_number = 8

    assert await instance.rollback_last_round() == 7
    assert purchase["status"] == "pending"
    assert instance.get_character_sheet("gm")["currency"]["amount"] == pre_payment["currency"]["amount"]
    assert instance.get_character_sheet("gm")["inventory"] == pre_payment.get("inventory", [])
    assert all(tx["status"] == "reversed" for tx in instance.economy["transactions"])
    assert purchase in instance.pending_payments

    registry = GameRegistry(tmp_path / "saves")
    registry.register(instance)
    await registry.save(instance)
    registry._instances.clear()
    recovered = await registry.load(instance.game_key)
    assert recovered is not None
    assert recovered.get_character_sheet("gm")["currency"]["amount"] == pre_payment["currency"]["amount"]
    assert recovered.get_character_sheet("gm")["inventory"] == pre_payment.get("inventory", [])
    assert recovered.pending_payments[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_real_swipe_reconciles_purchase_settled_after_target_round(
    web_api, monkeypatch,
) -> None:
    """Swipe must undo a purchase settled after the target round snapshot."""
    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Swipe late settlement",
        gm_uid="gm",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 20}],
    )
    assert created["ok"] is True
    instance = registry.get(api._parse_key(created["game_key"]))
    assert instance is not None
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    # The offer predates the target round; only its later settlement belongs
    # to the round that will be rewritten, so rollback should reopen it.
    instance.round_number = 0
    before = _snapshot_players(instance)
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid=uid,
        recipient_uid=uid,
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
        reason="购买通行证",
    )

    # Enter the target round and capture its pre-settlement state, then settle
    # while the round is in progress (the late-payment boundary).
    instance.round_number = 1
    target_snapshot = _snapshot_players(instance)
    settled = await api.resolve_payment(created["game_key"], proposal["id"], True, uid)
    assert settled["ok"] is True
    assert instance.get_character_sheet(uid)["currency"]["amount"] == 15
    assert any(item.get("name") == "通行证" for item in instance.get_character_sheet(uid)["key_items"])

    # This mirrors the historical edge case: the persisted target entry carries
    # a post-settlement snapshot even though its proposal originated in round 1.
    instance.log.append({
        "round": 1,
        "actions": [],
        "gm_response": "旧分支",
        "pre_state_snapshot": target_snapshot,
        "swipes": ["旧分支"],
        "current_swipe": 0,
    })

    async def replacement_swipe(*, system_prompt, user_message, **kwargs):
        del system_prompt, user_message, kwargs
        return LLMResponse(
            content="新的侧路分支。\n---\nNONE",
            narration="新的侧路分支。",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=6,
            is_narration_only=True,
            provider_used="fake",
        )

    monkeypatch.setattr(llm, "call", replacement_swipe)
    swipe_text = await api._handler.generate_swipe(instance, 1)

    assert swipe_text == "新的侧路分支。"
    assert instance.get_character_sheet(uid)["currency"]["amount"] == before[uid]["currency"]["amount"]
    assert instance.get_character_sheet(uid)["key_items"] == before[uid]["key_items"]
    assert proposal["status"] == "pending"
    assert any(tx["status"] == "reversed" for tx in instance.economy["transactions"])
    assert instance.log[-1]["current_swipe"] == 1

    registry._instances.clear()
    recovered = await registry.load(instance.game_key)
    assert recovered is not None
    assert recovered.get_character_sheet(uid)["currency"]["amount"] == before[uid]["currency"]["amount"]
    assert recovered.get_character_sheet(uid)["key_items"] == before[uid]["key_items"]
    assert recovered.pending_payments[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_origin_round_swipe_does_not_project_later_settlement_before_state(
    web_api, monkeypatch,
) -> None:
    """Origin-round Swipe restores its own snapshot, not a later tx.before."""
    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Origin round snapshot",
        gm_uid="gm",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 30}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    assert instance is not None
    uid = next(iter(instance.players))
    instance.gm_uid = uid

    # R5 snapshot starts at 30 and contains the offer.
    instance.round_number = 5
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid=uid,
        recipient_uid=uid,
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
        reason="购买通行证",
    )
    target_snapshot = _snapshot_players(instance)

    # An unrelated later-round change raises the balance to 40 before the old
    # R5 offer is settled in R7.  Its transaction.before is therefore 40.
    instance.round_number = 6
    sheet = instance.get_character_sheet(uid)
    sheet["gold"] = 40
    sheet["currency"]["amount"] = 40
    instance.set_character_sheet(uid, sheet)
    instance.round_number = 7
    settled = await api.resolve_payment(created["game_key"], proposal["id"], True, uid)
    assert settled["transaction"]["round"] == 7
    assert settled["transaction"]["entries"][0]["before"] == 40

    instance.log.append({
        "round": 5,
        "actions": [],
        "gm_response": "旧 R5 分支",
        "pre_state_snapshot": target_snapshot,
        "swipes": ["旧 R5 分支"],
        "current_swipe": 0,
    })

    async def replacement_swipe(*, system_prompt, user_message, **kwargs):
        del system_prompt, user_message, kwargs
        return LLMResponse(
            content="新的 R5 分支。\n---\nNONE",
            narration="新的 R5 分支。",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=6,
            is_narration_only=True,
            provider_used="fake",
        )

    monkeypatch.setattr(llm, "call", replacement_swipe)
    assert await api._handler.generate_swipe(instance, 5) == "新的 R5 分支。"

    assert instance.get_character_sheet(uid)["currency"]["amount"] == 30
    assert instance.get_character_sheet(uid)["gold"] == 30
    assert instance.get_character_sheet(uid)["key_items"] == []
    current_proposal = next(
        item for item in instance.economy["proposals"] if item["id"] == proposal["id"]
    )
    assert current_proposal["status"] == "reversed"
    assert all(item["id"] != proposal["id"] for item in instance.pending_payments)
    assert instance.economy["transactions"][0]["status"] == "reversed"
    assert not any(
        outcome.get("proposal_id") == proposal["id"]
        and outcome.get("status") == "committed"
        for outcome in instance.economy["outcomes"]
    )


def test_transfer_moves_currency_between_players_with_balanced_ledger() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="transfer",
        payer_uid="gm",
        recipient_uid="p2",
        amount=7,
        approval_policy="payer",
        source_ref="player-transfer:1",
    )

    result = resolve_proposal(
        instance, proposal["id"], actor_uid="gm", accepted=True,
    )

    assert result["ok"] is True
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 23
    assert instance.get_character_sheet("p2")["currency"]["amount"] == 27
    assert sum(entry["delta"] for entry in result["transaction"]["entries"]) == 0


@pytest.mark.asyncio
async def test_rollback_reconciles_multiple_late_settlements_in_reverse_commit_order(tmp_path) -> None:
    """Two purchases by one player restore the earliest balance and inventory."""
    instance = _instance()
    uid = "gm"
    instance.round_number = 6
    first = queue_proposal(
        instance, kind="purchase", payer_uid=uid, recipient_uid=uid, amount=5,
        rewards=[{"name": "药水", "category": "consumable"}],
    )
    second = queue_proposal(
        instance, kind="purchase", payer_uid=uid, recipient_uid=uid, amount=5,
        rewards=[{"name": "绳索", "category": "misc"}],
    )
    instance.round_number = 7
    resolve_proposal(instance, first["id"], actor_uid=uid, accepted=True, grant_reward=_grant_inventory_reward)
    resolve_proposal(instance, second["id"], actor_uid=uid, accepted=True, grant_reward=_grant_inventory_reward)
    late_snapshot = _snapshot_players(instance)
    assert late_snapshot[uid]["currency"]["amount"] == 20

    # Exercise the aggregate's real rollback path, not only the reconciliation
    # helper.  The persisted snapshot intentionally contains post-settlement
    # state, matching the late-payment edge case.
    instance.log.append({
        "round": 7,
        "actions": [],
        "gm_response": "late settlements",
        "round_start_snapshot": late_snapshot,
    })
    instance.round_number = 8
    assert await instance.rollback_last_round() == 7

    assert instance.get_character_sheet(uid)["currency"]["amount"] == 30
    assert instance.get_character_sheet(uid)["gold"] == 30
    assert instance.get_character_sheet(uid)["inventory"] == []
    assert first["status"] == second["status"] == "pending"
    assert {item["id"] for item in instance.pending_payments} == {first["id"], second["id"]}
    assert all(tx["status"] == "reversed" for tx in instance.economy["transactions"])

    registry = GameRegistry(tmp_path / "saves")
    registry.register(instance)
    await registry.save(instance)
    registry._instances.clear()
    recovered = await registry.load(instance.game_key)
    assert recovered is not None
    assert recovered.get_character_sheet(uid)["currency"]["amount"] == 30
    assert recovered.get_character_sheet(uid)["inventory"] == []
    assert {item["id"] for item in recovered.pending_payments} == {first["id"], second["id"]}

    # Retrying both reopened proposals charges and grants exactly once.
    resolve_proposal(recovered, first["id"], actor_uid=uid, accepted=True, grant_reward=_grant_inventory_reward)
    resolve_proposal(recovered, second["id"], actor_uid=uid, accepted=True, grant_reward=_grant_inventory_reward)
    assert recovered.get_character_sheet(uid)["currency"]["amount"] == 20
    assert [item["name"] for item in recovered.get_character_sheet(uid)["inventory"]] == ["药水", "绳索"]
    assert sum(tx["status"] == "committed" for tx in recovered.economy["transactions"]) == 2


def test_stacked_reward_delta_preserves_original_quantity() -> None:
    before = [{"name": "Potion", "qty": 2}]
    after_first = [{"name": "Potion", "qty": 3}]
    after_second = [{"name": "Potion", "qty": 5}]
    current = _remove_reward_delta(after_second, after_first, after_second)
    current = _remove_reward_delta(current, before, after_first)
    assert current == before


def test_team_split_is_all_or_nothing() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="fee",
        amount=15,
        approval_policy="all_contributors",
        contributors=[{"uid": "gm", "amount": 5}, {"uid": "p2", "amount": 10}],
        source_ref="gate:city:fee",
    )

    first = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=True)
    assert first["committed"] is False
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 30
    second = resolve_proposal(instance, proposal["id"], actor_uid="p2", accepted=True)

    assert second["ok"] is True
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 25
    assert instance.get_character_sheet("p2")["currency"]["amount"] == 10


def test_team_split_insufficient_funds_rejects_without_partial_debit() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="fee",
        amount=45,
        approval_policy="all_contributors",
        contributors=[{"uid": "gm", "amount": 5}, {"uid": "p2", "amount": 40}],
        source_ref="gate:city:expensive-fee",
    )

    assert resolve_proposal(
        instance, proposal["id"], actor_uid="gm", accepted=True,
    )["committed"] is False
    rejected = resolve_proposal(
        instance, proposal["id"], actor_uid="p2", accepted=True,
    )

    assert rejected["code"] == "INSUFFICIENT_FUNDS"
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 30
    assert instance.get_character_sheet("p2")["currency"]["amount"] == 20
    assert proposal["status"] == "rejected"
    assert proposal not in instance.pending_payments


def test_team_split_rejects_incomplete_or_duplicate_contributor_plan() -> None:
    instance = _instance()

    with pytest.raises(ValueError):
        queue_proposal(
            instance,
            kind="fee",
            amount=10,
            approval_policy="all_contributors",
            contributors=[{"uid": "gm", "amount": 4}, {"uid": "gm", "amount": 4}],
        )


def test_removing_party_member_cancels_their_unresolved_group_proposal() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="fee",
        amount=10,
        approval_policy="all_contributors",
        contributors=[{"uid": "gm", "amount": 5}, {"uid": "p2", "amount": 5}],
    )
    effect_group = queue_effect_group(
        instance,
        [proposal],
        {"state_update": {"scene_change": "付费区域"}},
    )

    instance.remove_payments_for_player("p2")

    assert proposal["status"] == "cancelled"
    assert proposal["resolution_code"] == "PLAYER_REMOVED"
    assert proposal not in instance.pending_payments
    assert effect_group is not None
    assert effect_group["status"] == "discarded"
    assert "effects" not in effect_group
    assert instance.economy["outcomes"][-1]["status"] == "cancelled"


def test_declining_payment_discards_deferred_narrative_effects() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="payment",
        payer_uid="gm",
        recipient_uid="gm",
        amount=10,
        reason="购买通行许可",
    )
    effect_group = queue_effect_group(
        instance,
        [proposal],
        {
            "state_update": {
                "scene_change": "城门内",
                "loot": [{"player": "gm", "item": "通行许可"}],
            },
            "confirmed": ["已经进入城内"],
        },
    )

    result = resolve_proposal(
        instance,
        proposal["id"],
        actor_uid="gm",
        accepted=False,
    )

    assert result["ok"] is True
    assert result["outcome"]["status"] == "declined"
    assert result["outcome"]["effects_status"] == "discarded"
    assert instance.get_character_sheet("gm")["currency"]["amount"] == 30
    assert effect_group is not None
    assert effect_group["status"] == "discarded"
    assert "effects" not in effect_group


def test_multiple_proposals_form_one_all_or_nothing_effect_barrier() -> None:
    instance = _instance()
    first = queue_proposal(
        instance,
        kind="payment",
        payer_uid="gm",
        recipient_uid="gm",
        amount=3,
    )
    second = queue_proposal(
        instance,
        kind="payment",
        payer_uid="p2",
        recipient_uid="p2",
        amount=4,
    )
    group = queue_effect_group(
        instance,
        [first, second],
        {"state_update": {"scene_change": "队伍共同进入的区域"}},
    )

    first_result = resolve_proposal(
        instance, first["id"], actor_uid="gm", accepted=True,
    )
    second_result = resolve_proposal(
        instance, second["id"], actor_uid="p2", accepted=True,
    )

    assert group is not None
    assert first_result.get("effect_group") is None
    assert first_result["outcome"]["effects_status"] == "pending"
    assert second_result["effect_group"]["id"] == group["id"]
    assert group["status"] == "ready"


@pytest.mark.asyncio
async def test_team_split_is_visible_to_each_contributor_and_waits_for_all(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Party Economy",
        players=[
            {"character_name": "One", "attributes": {"str": 10}, "gold": 20},
            {"character_name": "Two", "attributes": {"str": 10}, "gold": 20},
        ],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    first_uid, second_uid = list(instance.players)
    instance.gm_uid = first_uid
    proposal = queue_proposal(
        instance,
        kind="fee",
        amount=10,
        approval_policy="all_contributors",
        contributors=[
            {"uid": first_uid, "amount": 4},
            {"uid": second_uid, "amount": 6},
        ],
    )
    effect_group = queue_effect_group(
        instance,
        [proposal],
        {"state_update": {"scene_change": "队伍包下的房间"}},
    )

    second_view = api.game_detail(created["game_key"], second_uid)
    assert [item["id"] for item in second_view["economy_proposals"]] == [proposal["id"]]

    first = await api.resolve_payment(
        created["game_key"], proposal["id"], True, first_uid,
    )
    assert first["committed"] is False
    assert instance.get_character_sheet(first_uid)["currency"]["amount"] == 20
    assert instance.scene != "队伍包下的房间"
    assert effect_group is not None and effect_group["status"] == "pending"
    assert any(event.get("code") == "economy_approved" for event in instance.health_events)

    second = await api.resolve_payment(
        created["game_key"], proposal["id"], True, second_uid,
    )
    assert second["ok"] is True
    assert instance.get_character_sheet(first_uid)["currency"]["amount"] == 16
    assert instance.get_character_sheet(second_uid)["currency"]["amount"] == 14
    assert instance.scene == "队伍包下的房间"
    committed_group = next(
        item for item in instance.economy["effect_groups"]
        if item["id"] == effect_group["id"]
    )
    assert committed_group["status"] == "committed"
    assert "effects" not in committed_group
    assert instance.economy["outcomes"][-1]["effects_status"] == "committed"


@pytest.mark.asyncio
async def test_private_payment_outcome_does_not_leak_into_party_log(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Private Economy",
        players=[
            {"character_name": "One", "attributes": {"str": 10}, "gold": 20},
            {"character_name": "Two", "attributes": {"str": 10}, "gold": 20},
        ],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    payer_uid, other_uid = list(instance.players)
    instance.gm_uid = payer_uid
    instance.append_log_entry({
        "round": instance.round_number,
        "actions": [],
        "gm_response": "一次私下报价。",
        "state_changes": [],
    })
    proposal = queue_proposal(
        instance,
        kind="payment",
        payer_uid=payer_uid,
        recipient_uid=payer_uid,
        amount=2,
        reason="不应公开的私下报价",
        visibility="private",
    )

    result = await api.resolve_payment(
        created["game_key"], proposal["id"], False, payer_uid,
    )

    assert result["ok"] is True
    assert "economy_resolutions" not in instance.log[-1]
    assert not any("私下报价" in item for item in instance.log[-1]["state_changes"])
    assert instance.private_log[payer_uid][-1]["kind"] == "economy_resolution"
    assert other_uid not in instance.private_log


@pytest.mark.asyncio
async def test_payment_decision_commits_or_discards_linked_effects(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Decision Barrier",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 20}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    instance.append_log_entry({
        "round": instance.round_number,
        "actions": [],
        "gm_response": "商人提出交易。",
        "state_changes": [],
    })
    accepted = queue_proposal(
        instance,
        kind="purchase",
        payer_uid=uid,
        recipient_uid=uid,
        amount=5,
        reason="购买城门通行证",
        visibility="party",
    )
    accepted_group = queue_effect_group(
        instance,
        [accepted],
        {
            "state_update": {
                "scene_change": "城门内",
                "loot": [{"player": uid, "item": "城门通行证"}],
            },
            "confirmed": ["已经取得城门通行证"],
            "xp_rewards": {uid: 7},
        },
    )

    committed = await api.resolve_payment(
        created["game_key"], accepted["id"], True, uid,
    )

    assert committed["effects_committed"] is True
    assert instance.get_character_sheet(uid)["currency"]["amount"] == 15
    assert instance.scene == "城门内"
    sheet = instance.get_character_sheet(uid)
    owned_items = [
        item
        for field in ("inventory", "key_items", "equipment")
        for item in sheet.get(field, [])
        if isinstance(item, dict)
    ]
    assert any(item.get("name") == "城门通行证" for item in owned_items)
    assert "已经取得城门通行证" in instance.confirmed_items
    assert instance.get_character_sheet(uid)["xp"] == 7
    assert accepted_group is not None
    assert next(
        item for item in instance.economy["effect_groups"]
        if item["id"] == accepted_group["id"]
    )["status"] == "committed"
    assert instance.log[-1]["economy_resolutions"][-1]["status"] == "committed"

    declined = queue_proposal(
        instance,
        kind="payment",
        payer_uid=uid,
        recipient_uid=uid,
        amount=4,
        reason="乘坐马车",
        visibility="party",
    )
    declined_group = queue_effect_group(
        instance,
        [declined],
        {"state_update": {"scene_change": "远方驿站"}},
    )

    rejected = await api.resolve_payment(
        created["game_key"], declined["id"], False, uid,
    )

    assert rejected["accepted"] is False
    assert instance.get_character_sheet(uid)["currency"]["amount"] == 15
    assert instance.scene == "城门内"
    assert declined_group is not None
    discarded_group = next(
        item for item in instance.economy["effect_groups"]
        if item["id"] == declined_group["id"]
    )
    assert discarded_group["status"] == "discarded"
    assert "effects" not in discarded_group
    assert instance.log[-1]["economy_resolutions"][-1]["status"] == "declined"


@pytest.mark.asyncio
async def test_effect_failure_rolls_back_the_whole_economy_decision(
    web_api,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Atomic Economy",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 20}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid=uid,
        recipient_uid=uid,
        amount=5,
        reason="购买通行证",
        visibility="party",
    )
    group = queue_effect_group(
        instance,
        [proposal],
        {"state_update": {"scene_change": "收费区内"}},
    )
    original_apply = api._character_dependencies.apply_economy_effects

    async def fail_after_partial_mutation(staged, effects):
        staged.set_scene("不应提交的半成品场景")
        raise RuntimeError("fault injection after partial effect")

    api._character_dependencies = replace(
        api._character_dependencies,
        apply_economy_effects=fail_after_partial_mutation,
    )
    failed = await api.resolve_payment(
        created["game_key"], proposal["id"], True, uid,
    )

    assert failed["code"] == "EFFECT_COMMIT_FAILED"
    assert instance.get_character_sheet(uid)["currency"]["amount"] == 20
    assert instance.scene != "不应提交的半成品场景"
    assert proposal["status"] == "pending"
    assert instance.economy["transactions"] == []
    assert instance.economy["outcomes"] == []
    assert group is not None and group["status"] == "pending"

    api._character_dependencies = replace(
        api._character_dependencies,
        apply_economy_effects=original_apply,
    )
    retried = await api.resolve_payment(
        created["game_key"], proposal["id"], True, uid,
    )

    assert retried["ok"] is True
    assert instance.get_character_sheet(uid)["currency"]["amount"] == 15
    assert instance.scene == "收费区内"
    assert len(instance.economy["transactions"]) == 1
    assert next(
        item for item in instance.economy["effect_groups"]
        if item["id"] == group["id"]
    )["status"] == "committed"


@pytest.mark.asyncio
async def test_economy_scene_image_starts_only_after_authoritative_save(
    web_api,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Atomic Economy Scene Image",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 20}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    instance.scene_image = {"kind": "upload", "asset_id": "old-scene"}
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid=uid,
        recipient_uid=uid,
        amount=4,
        reason="进入雾港",
    )
    queue_effect_group(instance, [proposal], {
        "state_update": {"scene_change": "雾港码头"},
        "scene_image_prompt": "misty harbor at dusk",
    })
    schedule_calls: list[dict] = []

    def schedule_scene(_instance, payload):
        schedule_calls.append(dict(payload))
        return object()

    original = api._character_dependencies

    async def fail_authoritative_save(_instance):
        raise OSError("fault before scene image scheduling")

    api._character_dependencies = replace(
        original,
        schedule_economy_scene_image=schedule_scene,
        games=replace(original.games, save_instance=fail_authoritative_save),
    )
    with pytest.raises(OSError, match="before scene image"):
        await api.resolve_payment(created["game_key"], proposal["id"], True, uid)

    assert schedule_calls == []
    assert instance.scene_image == {"kind": "upload", "asset_id": "old-scene"}
    assert instance.get_character_sheet(uid)["currency"]["amount"] == 20

    api._character_dependencies = replace(
        original,
        schedule_economy_scene_image=schedule_scene,
    )
    committed = await api.resolve_payment(
        created["game_key"], proposal["id"], True, uid,
    )
    duplicate = await api.resolve_payment(
        created["game_key"], proposal["id"], True, uid,
    )

    assert committed["ok"] is True
    assert committed["scene_image_scheduled"] is True
    assert duplicate["code"] == "ALREADY_RESOLVED"
    assert schedule_calls == [{
        "scene_image_prompt": "misty harbor at dusk",
        "state_update": {"scene_change": "雾港码头"},
    }]


@pytest.mark.asyncio
async def test_economy_memory_outbox_closes_save_crash_window(
    web_api,
    tmp_path,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    memory = MemoryStore(tmp_path / "economy-memory.db")
    memory.open()
    try:
        created = await api.create_game(
            "template_world",
            "Economy Memory Outbox",
            players=[{
                "character_name": "Hero",
                "attributes": {"str": 10},
                "gold": 20,
            }],
        )
        instance = registry.get(api._parse_key(created["game_key"]))
        uid = next(iter(instance.players))
        instance.gm_uid = uid
        original_dependencies = api._character_dependencies
        memory_dependencies = replace(
            original_dependencies,
            apply_economy_memory=memory.apply_economy_delta,
            reverse_economy_memory=memory.reverse_economy_delta,
        )
        api._character_dependencies = memory_dependencies

        proposal = queue_proposal(
            instance,
            kind="payment",
            payer_uid=uid,
            recipient_uid=uid,
            amount=3,
            reason="读取密函",
        )
        group = queue_effect_group(instance, [proposal], {
            "memory_delta": {
                "add": [{
                    "entity": "密函",
                    "relation": "内容",
                    "value": "北门午夜开启",
                    "confidence": 1.0,
                }],
                "update": [],
                "forget": [],
            },
        })
        assert group is not None

        async def fail_authoritative_save(_instance):
            raise OSError("fault before authoritative game save")

        api._character_dependencies = replace(
            memory_dependencies,
            games=replace(
                memory_dependencies.games,
                save_instance=fail_authoritative_save,
            ),
        )
        with pytest.raises(OSError, match="authoritative game save"):
            await api.resolve_payment(
                created["game_key"], proposal["id"], True, uid,
            )

        live_proposal = next(
            item for item in instance.economy["proposals"]
            if item["id"] == proposal["id"]
        )
        assert live_proposal["status"] == "pending"
        assert instance.get_character_sheet(uid)["currency"]["amount"] == 20
        assert memory.list_entries(instance.memory_namespace) == []

        api._character_dependencies = memory_dependencies
        committed = await api.resolve_payment(
            created["game_key"], proposal["id"], True, uid,
        )
        assert committed["external_effects_committed"] is True
        assert len(memory.list_entries(instance.memory_namespace)) == 1
        assert pending_memory_deliveries(instance) == []

        second = queue_proposal(
            instance,
            kind="payment",
            payer_uid=uid,
            recipient_uid=uid,
            amount=2,
            reason="读取第二封密函",
        )
        second_group = queue_effect_group(instance, [second], {
            "memory_delta": {
                "add": [{
                    "entity": "第二封密函",
                    "relation": "内容",
                    "value": "钟响后撤离",
                    "confidence": 1.0,
                }],
                "update": [],
                "forget": [],
            },
        })
        assert second_group is not None
        save_calls = 0

        async def fail_delivery_receipt_save(target):
            nonlocal save_calls
            save_calls += 1
            if save_calls == 1:
                await registry.save(target)
                return
            raise OSError("fault after external memory delivery")

        api._character_dependencies = replace(
            memory_dependencies,
            games=replace(
                memory_dependencies.games,
                save_instance=fail_delivery_receipt_save,
            ),
        )
        with pytest.raises(OSError, match="after external memory delivery"):
            await api.resolve_payment(
                created["game_key"], second["id"], True, uid,
            )
        assert len(memory.list_entries(instance.memory_namespace)) == 2

        recovered_registry = GameRegistry(registry.save_dir)
        recovered = await recovered_registry.load(instance.game_key)
        assert recovered is not None
        assert len(pending_memory_deliveries(recovered)) == 1
        recovery_dependencies = replace(
            memory_dependencies,
            games=replace(
                memory_dependencies.games,
                get_instance=recovered_registry.get,
                save_instance=recovered_registry.save,
            ),
        )
        assert await characters.drain_economy_outbox(
            recovery_dependencies, recovered,
        ) is True
        assert pending_memory_deliveries(recovered) == []
        assert len(memory.list_entries(recovered.memory_namespace)) == 2
    finally:
        memory.close()


@pytest.mark.asyncio
async def test_delivered_economy_memory_is_reversed_with_round(
    web_api,
    tmp_path,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    memory = MemoryStore(tmp_path / "economy-memory-reversal.db")
    memory.open()
    try:
        created = await api.create_game(
            "template_world",
            "Economy Memory Reversal",
            players=[{
                "character_name": "Hero",
                "attributes": {"str": 10},
                "gold": 20,
            }],
        )
        instance = registry.get(api._parse_key(created["game_key"]))
        uid = next(iter(instance.players))
        instance.gm_uid = uid
        await memory.apply_delta(instance.memory_namespace, {
            "add": [{
                "entity": "通行证",
                "relation": "持有状态",
                "value": "尚未取得",
                "confidence": 1.0,
            }],
            "update": [],
            "forget": [],
        }, 0)
        dependencies = replace(
            api._character_dependencies,
            apply_economy_memory=memory.apply_economy_delta,
            reverse_economy_memory=memory.reverse_economy_delta,
        )
        api._character_dependencies = dependencies
        proposal = queue_proposal(
            instance,
            kind="purchase",
            payer_uid=uid,
            recipient_uid=uid,
            amount=3,
            reason="购买通行证",
        )
        queue_effect_group(instance, [proposal], {
            "memory_delta": {
                "add": [],
                "update": [{
                    "entity": "通行证",
                    "relation": "持有状态",
                    "value": "已经取得",
                    "confidence": 1.0,
                }],
                "forget": [],
            },
        })
        committed = await api.resolve_payment(
            created["game_key"], proposal["id"], True, uid,
        )

        assert committed["external_effects_committed"] is True
        assert memory.list_entries(instance.memory_namespace)[0]["value"] == "已经取得"
        delivery = instance.economy["external_effects_outbox"][0]
        assert delivery["status"] == "delivered"

        async def fail_reversal_receipt_save(_instance):
            raise OSError("fault after external memory reversal")

        failing_dependencies = replace(
            dependencies,
            games=replace(
                dependencies.games,
                save_instance=fail_reversal_receipt_save,
            ),
        )
        api._character_dependencies = failing_dependencies
        instance.log.append({"round": instance.round_number})
        with pytest.raises(OSError, match="after external memory reversal"):
            await api.rollback_round(created["game_key"])

        restored = memory.list_entries(instance.memory_namespace)
        assert len(restored) == 1
        assert restored[0]["value"] == "尚未取得"

        recovered_registry = GameRegistry(registry.save_dir)
        recovered = await recovered_registry.load(instance.game_key)
        assert recovered is not None
        assert len(pending_memory_reversals(recovered)) == 1
        recovery_dependencies = replace(
            dependencies,
            games=replace(
                dependencies.games,
                get_instance=recovered_registry.get,
                save_instance=recovered_registry.save,
            ),
        )
        assert await characters.drain_economy_outbox(
            recovery_dependencies, recovered,
        ) is True
        recovered_delivery = recovered.economy["external_effects_outbox"][0]
        assert recovered_delivery["status"] == "reversed"
        assert pending_memory_deliveries(recovered) == []
        assert pending_memory_reversals(recovered) == []
        assert await characters.drain_economy_outbox(
            recovery_dependencies, recovered,
        ) is True
    finally:
        memory.close()


@pytest.mark.asyncio
async def test_private_reward_and_transfer_stay_out_of_public_gm_context(
    web_api,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Private Economy Projection",
        players=[
            {"character_name": "GM", "attributes": {"str": 10}, "gold": 20},
            {"character_name": "Agent", "attributes": {"str": 10}, "gold": 20},
        ],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    gm_uid, agent_uid = list(instance.players)
    instance.gm_uid = gm_uid
    reward = queue_proposal(
        instance,
        kind="reward",
        recipient_uid=agent_uid,
        amount=3,
        approval_policy="gm",
        reason="秘密线人奖励-不可公开",
        visibility="private",
    )
    transfer = queue_proposal(
        instance,
        kind="transfer",
        payer_uid=gm_uid,
        recipient_uid=agent_uid,
        amount=2,
        reason="秘密转账-不可公开",
        visibility="private",
    )

    assert (await api.resolve_payment(
        created["game_key"], reward["id"], True, gm_uid,
    ))["ok"] is True
    assert (await api.resolve_payment(
        created["game_key"], transfer["id"], True, gm_uid,
    ))["ok"] is True
    context = await build_context(instance, "SYSTEM", [], "继续")

    assert "秘密线人奖励-不可公开" not in context
    assert "秘密转账-不可公开" not in context
    assert any(
        "秘密线人奖励" in item.get("text", "")
        for item in instance.private_log[agent_uid]
    )
    assert any(
        "秘密转账" in item.get("text", "")
        for item in instance.private_log[agent_uid]
    )


@pytest.mark.asyncio
async def test_in_flight_narration_is_discarded_after_economy_decision(
    web_api,
    monkeypatch,
) -> None:
    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Decision Race",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 20}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    await instance.activate()
    await instance.start_round()
    await instance.add_action(uid, "我等待商人的答复")
    assert await instance.try_advance() is True
    instance.complete_round_check_preparation()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_call(*, system_prompt, user_message, **kwargs):
        entered.set()
        await release.wait()
        return LLMResponse(
            content="这条叙事已经过期。\n---\nSCENE:错误场景",
            narration="这条叙事已经过期。",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=8,
            is_narration_only=False,
            provider_used="fake",
        )

    monkeypatch.setattr(llm, "call", delayed_call)
    processing = asyncio.create_task(api._handler.process_round(instance))
    await entered.wait()
    proposal = queue_proposal(
        instance,
        kind="payment",
        payer_uid=uid,
        recipient_uid=uid,
        amount=3,
        reason="商人的旧报价",
    )
    declined = await api.resolve_payment(
        created["game_key"], proposal["id"], False, uid,
    )
    assert declined["ok"] is True
    release.set()

    narration, private = await processing

    assert narration == ""
    assert private is None
    assert instance.scene != "错误场景"
    assert not any(entry.get("gm_response") == "这条叙事已经过期。" for entry in instance.log)


@pytest.mark.asyncio
async def test_in_flight_check_plan_is_discarded_after_economy_decision(
    web_api,
    monkeypatch,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Planner Decision Race",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 20}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    await instance.activate()
    await instance.start_round()
    await instance.add_action(uid, "我用力量推开石门", selected_attribute="str")
    assert await instance.try_advance() is True
    entered = asyncio.Event()
    release = asyncio.Event()
    calls_before = instance.total_llm_calls

    async def delayed_plan(target, rule, client):
        entered.set()
        await release.wait()
        return [(
            target.action_queue[0],
            {
                "check_id": "stale-check",
                "required": True,
                "actor_uid": uid,
                "dice_system": "d20",
                "attribute": "str",
                "target": 10,
            },
        )], {"available": True, "skipped": False, "total_tokens": 7}

    monkeypatch.setattr("src.commands.round_processor.plan_round_checks", delayed_plan)
    planning = asyncio.create_task(api._handler.prepare_round_checks_ai(instance))
    await entered.wait()
    proposal = queue_proposal(
        instance,
        kind="payment",
        payer_uid=uid,
        recipient_uid=uid,
        amount=1,
        reason="旧报价",
    )
    declined = await api.resolve_payment(
        created["game_key"], proposal["id"], False, uid,
    )
    assert declined["ok"] is True
    release.set()

    checks = await planning

    assert checks == []
    assert instance.round_checks_prepared is False
    assert "check_request" not in instance.action_queue[0]
    assert instance.total_llm_calls == calls_before


@pytest.mark.asyncio
async def test_restart_rejects_old_payment_waiting_during_candidate_generation(
    web_api,
    monkeypatch,
) -> None:
    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Restart Payment Race",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 20}],
    )
    old_instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(old_instance.players))
    old_instance.gm_uid = uid
    proposal = queue_proposal(
        old_instance,
        kind="payment",
        payer_uid=uid,
        recipient_uid=uid,
        amount=5,
        reason="重开期间支付",
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_opening(*, system_prompt, user_message, **kwargs):
        entered.set()
        await release.wait()
        return LLMResponse(
            content="新开场。",
            narration="新开场。",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=5,
            is_narration_only=True,
            provider_used="fake",
        )

    monkeypatch.setattr(llm, "call", delayed_opening)
    restarting = asyncio.create_task(api.restart_game(created["game_key"]))
    await entered.wait()
    payment = asyncio.create_task(
        api.resolve_payment(created["game_key"], proposal["id"], True, uid)
    )
    await asyncio.sleep(0)
    assert payment.done() is False
    release.set()
    assert (await restarting)["ok"] is True
    paid = await payment
    assert paid["ok"] is False
    assert paid["code"] == "STALE_RUN"

    restarted = registry.get(api._parse_key(created["game_key"]))
    assert restarted is not old_instance
    assert restarted.get_character_sheet(uid)["currency"]["amount"] == 20
    with pytest.raises(RuntimeError, match="stale game instance"):
        await registry.save(old_instance)
    recovered = await GameRegistry(registry.save_dir).load(restarted.game_key)
    assert recovered is not None
    assert recovered.run_id == restarted.run_id
    assert recovered.get_character_sheet(uid)["currency"]["amount"] == 20


@pytest.mark.asyncio
async def test_restart_opening_character_effect_survives(web_api, monkeypatch) -> None:
    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Restart Opening Effect",
        players=[{
            "character_name": "Hero",
            "attributes": {"str": 10},
            "gold": 20,
            "mana": 12,
            "max_mana": 12,
        }],
    )
    old_instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(old_instance.players))
    old_instance.get_character_sheet(uid).update({"mana": 12, "max_mana": 12})

    async def opening_with_character_effects(*, system_prompt, user_message, **kwargs):
        return LLMResponse(
            content=(
                "Hero 在风暴中醒来。\n---\n"
                f"HP:{uid}:-3\n"
                f"MANA:{uid}:-2\n"
                "SCENE:风暴海岸"
            ),
            narration="Hero 在风暴中醒来。",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=8,
            is_narration_only=False,
            provider_used="fake",
        )

    monkeypatch.setattr(llm, "call", opening_with_character_effects)
    result = await api.restart_game(created["game_key"])
    restarted = registry.get(api._parse_key(created["game_key"]))
    sheet = restarted.get_character_sheet(uid)

    assert result["ok"] is True
    assert sheet["hp"] == sheet["max_hp"] - 3
    assert sheet["mana"] == 10
    assert restarted.scene == "风暴海岸"


@pytest.mark.asyncio
async def test_restart_rotates_run_and_memory_but_preserves_character_assets(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Lifecycle",
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": 23}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    sheet = instance.get_character_sheet(uid)
    sheet.update({"hp": 0, "deceased": True, "status": "downed", "death_saves": {"failure": 2}})
    old_run = instance.run_id
    old_namespace = instance.memory_namespace
    pending = queue_proposal(
        instance, kind="payment", payer_uid=uid, recipient_uid=uid, amount=2,
    )
    queue_effect_group(
        instance,
        [pending],
        {"state_update": {"scene_change": "不应进入的场景"}},
    )
    declined = queue_proposal(
        instance, kind="payment", payer_uid=uid, recipient_uid=uid, amount=1,
    )
    resolve_proposal(
        instance, declined["id"], actor_uid=uid, accepted=False,
    )
    instance.append_log_entry({
        "round": 99,
        "actions": [],
        "gm_response": "previous-run-only",
    })
    await registry.save(instance)

    result = await api.restart_game(created["game_key"])
    restarted = registry.get(api._parse_key(created["game_key"]))

    assert result["ok"] is True
    assert restarted.run_id != old_run
    assert restarted.memory_namespace != old_namespace
    assert restarted.get_character_sheet(uid)["currency"]["amount"] == 23
    assert restarted.get_character_sheet(uid)["hp"] == restarted.get_character_sheet(uid)["max_hp"]
    assert restarted.get_character_sheet(uid).get("deceased") is False
    assert "death_saves" not in restarted.get_character_sheet(uid)
    assert restarted.pending_payments == []
    assert restarted.economy["transactions"] == []
    assert restarted.economy["effect_groups"] == []
    assert restarted.economy["outcomes"] == []
    assert restarted.economy["decision_revision"] == 0
    recovered = await GameRegistry(registry.save_dir).load(restarted.game_key)
    assert recovered is not None
    assert recovered.run_id == restarted.run_id
    assert all(
        entry.get("gm_response") != "previous-run-only"
        for entry in recovered.log
    )

    reset_pending = queue_proposal(
        restarted,
        kind="payment",
        payer_uid=uid,
        recipient_uid=uid,
        amount=2,
    )
    queue_effect_group(
        restarted,
        [reset_pending],
        {"state_update": {"scene_change": "重置后不应出现"}},
    )
    reset_run = restarted.run_id
    reset_result = await api.reset_game(created["game_key"])
    reset_instance = registry.get(api._parse_key(created["game_key"]))

    assert reset_result["ok"] is True
    assert reset_instance.run_id != reset_run
    assert reset_instance.players == {}
    assert reset_instance.pending_payments == []
    assert reset_instance.economy["proposals"] == []
    assert reset_instance.economy["transactions"] == []
    assert reset_instance.economy["effect_groups"] == []
    assert reset_instance.economy["outcomes"] == []
    assert reset_instance.economy["decision_revision"] == 0


async def _hold_historical_rewrite(instance, entered, release) -> None:
    async with instance.historical_rewrite() as acquired:
        assert acquired is True
        async with instance._process_lock:
            entered.set()
            await release.wait()


async def _gated_game(web_api, name: str, *, gold: int = 20):
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world", name,
        players=[{"character_name": "Hero", "attributes": {"str": 10}, "gold": gold}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    return api, registry, created, instance, next(iter(instance.players))


@pytest.mark.asyncio
async def test_payment_cannot_interleave_with_swipe_rewrite(web_api) -> None:
    api, _registry, created, instance, uid = await _gated_game(web_api, "Payment Gate")
    proposal = queue_proposal(instance, kind="payment", payer_uid=uid, recipient_uid=uid, amount=5)
    before = deepcopy(instance.economy)
    entered, release = asyncio.Event(), asyncio.Event()
    rewrite = asyncio.create_task(_hold_historical_rewrite(instance, entered, release))
    await entered.wait()
    result = await asyncio.wait_for(api.resolve_payment(created["game_key"], proposal["id"], True, uid), 1)
    assert result["code"] == "REWRITE_IN_PROGRESS"
    assert instance.economy == before
    assert instance.get_character_sheet(uid)["currency"]["amount"] == 20
    release.set()
    await asyncio.wait_for(rewrite, 1)


@pytest.mark.asyncio
async def test_pending_dice_confirmation_is_rejected_during_swipe(web_api) -> None:
    api, _registry, created, instance, uid = await _gated_game(web_api, "Dice Gate")
    await instance.activate()
    await instance.start_round()
    await instance.add_action(uid, "尝试开锁", dice_pending=True, dice_system="d20", check_request={"dice_system": "d20", "dc": 10})
    before_action, before_ready = deepcopy(instance.action_queue[0]), set(instance.ready_players)
    entered, release = asyncio.Event(), asyncio.Event()
    rewrite = asyncio.create_task(_hold_historical_rewrite(instance, entered, release))
    await entered.wait()
    result = await asyncio.wait_for(api.resolve_pending_dice_for_game(created["game_key"], uid, "player"), 1)
    assert result["code"] == "REWRITE_IN_PROGRESS"
    assert instance.action_queue[0] == before_action
    assert instance.ready_players == before_ready
    release.set()
    await asyncio.wait_for(rewrite, 1)


@pytest.mark.asyncio
async def test_gm_resource_change_is_rejected_during_swipe(web_api) -> None:
    api, _registry, created, instance, _uid = await _gated_game(web_api, "GM Gate")
    before = deepcopy(instance.players)
    entered, release = asyncio.Event(), asyncio.Event()
    rewrite = asyncio.create_task(_hold_historical_rewrite(instance, entered, release))
    await entered.wait()
    result = await asyncio.wait_for(api.gm_command(created["game_key"], "给Hero加金币5"), 1)
    assert result["code"] == "REWRITE_IN_PROGRESS"
    assert instance.players == before
    release.set()
    await asyncio.wait_for(rewrite, 1)


@pytest.mark.asyncio
async def test_character_update_is_rejected_during_swipe(web_api) -> None:
    api, _registry, created, instance, uid = await _gated_game(web_api, "Character Gate")
    before = deepcopy(instance.players[uid])
    entered, release = asyncio.Event(), asyncio.Event()
    rewrite = asyncio.create_task(_hold_historical_rewrite(instance, entered, release))
    await entered.wait()
    result = await asyncio.wait_for(api.update_character(created["game_key"], uid, {"background": "changed"}), 1)
    assert result["error_code"] == "REWRITE_IN_PROGRESS"
    assert instance.players[uid] == before
    release.set()
    await asyncio.wait_for(rewrite, 1)


def test_two_actors_same_round_repair_splits_per_payer() -> None:
    """复刻 round-12：双人同轮各自购买，按 actor 拆成独立待确认提案。"""

    instance = _instance()
    instance.action_queue = [
        {"user_id": "gm", "text": "买下《王都周边地城简录》（5金币）"},
        {"user_id": "p2", "text": "买下那卷结构图（3金币）"},
    ]
    data = {"state_update": {"loot": [
        {"player": "gm", "item": "王都周边地城简录"},
        {"player": "p2", "item": "结构图"},
    ]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data,
        "“结构图三金币，简录五金币，都是旧货，不还价。”老头把两样东西并排摆在柜台上。"
        "“我测试”掏出三枚金币搁在柜台上，抓起卷轴塞进怀里。你也数出五枚金币推过去，"
        "老头把册子递到你手里。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposals = data["state_update"]["economy_proposals"]
    assert len(proposals) == 2
    by_payer = {p["uid"]: p for p in proposals}
    assert by_payer["gm"]["amount"] == 5
    assert by_payer["gm"]["items"] == ["王都周边地城简录"]
    assert by_payer["p2"]["amount"] == 3
    assert by_payer["p2"]["items"] == ["结构图"]
    for proposal in proposals:
        assert proposal["status"] if False else proposal["amount_source"] == "player_action"
    # 双人的 grant 都被各自提案消费，loot 不再残留。
    assert data["state_update"]["loot"] == []
    assert not instance.economy.get("clarifications")


def test_intent_without_grant_becomes_per_actor_clarification() -> None:
    """AI 没发 grant 的那半购买：按 actor 记结构化澄清，不再无痕丢失。"""

    instance = _instance()
    instance.action_queue = [
        {"user_id": "p2", "text": "买下那卷结构图（3金币）"},
    ]
    data = {"state_update": {}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data,
        "“结构图三金币。”老头把发黑的卷轴推了过去，“我测试”把钱递了过去。",
    )
    assert (dropped, ambiguous) == (0, True)
    assert not data["state_update"].get("economy_proposals")
    clarifications = instance.economy["clarifications"]
    assert len(clarifications) == 1
    clarification = clarifications[0]
    assert clarification["payer_uid"] == "p2"
    assert clarification["reason"] == "MISSING_SELLER_PRICE_CONFIRMATION"
    assert clarification["amount_candidates"] == [3]
    assert any("结构图" in item for item in clarification["item_candidates"])


def test_repair_binds_narration_price_per_item_sentence() -> None:
    """叙事里商品与金额同句唯一绑定时，优先于行动自报金额。"""

    instance = _instance()
    instance.action_queue = [
        {"user_id": "gm", "text": "掏9金币买下精钢剑"},
    ]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data,
        "霍根咧嘴：“矮人精钢剑卖三十金币，不讲价。”你把钱数给了他，剑归了你。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 30
    assert proposal["amount_source"] == "narration"


def test_repair_persists_evidence_and_links_to_proposal() -> None:
    """恢复层为每个意图留下证据链，proposal 挂 evidence_ids；证据无权威性。"""

    instance = _instance()
    instance.action_queue = [
        {"user_id": "gm", "text": "掏30金币买下精钢剑"},
    ]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根接过金币，转身取下那柄矮人精钢剑，递到你面前。",
    )
    assert (dropped, ambiguous) == (0, False)
    evidence = instance.economy["evidence"]
    types = {item["type"] for item in evidence}
    assert "purchase_intent" in types
    assert "seller_grant" in types
    for item in evidence:
        assert item["authority"] is False
        assert item["id"].startswith("ev_")
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["evidence_ids"]
    assert set(proposal["evidence_ids"]) <= {item["id"] for item in evidence}


def test_repair_twice_does_not_duplicate_proposals() -> None:
    """重复 repair 不会重复创建提案（幂等）。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下治疗药水"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "治疗药水"}]}}
    first = repair_unbacked_purchase(instance, data, "霍根收了2枚金币，把治疗药水递给你。")
    second = repair_unbacked_purchase(instance, data, "霍根收了2枚金币，把治疗药水递给你。")
    assert first == second == (0, False)
    proposals = data["state_update"]["economy_proposals"]
    assert len(proposals) == 1


def test_deferred_payment_intent_goes_to_clarification() -> None:
    """先拿货后付款：不合成立即结算的提案，进澄清由 GM/玩家安排。"""

    instance = _instance()
    instance.action_queue = [
        {"user_id": "gm", "text": "这剑我先拿走，明天付款"},
    ]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "霍根眯眼打量了你一番，还是把剑递了过来。",
    )
    assert (dropped, ambiguous) == (1, True)
    assert not data["state_update"].get("economy_proposals")
    clarification = instance.economy["clarifications"][0]
    assert clarification["reason"] == "DEFERRED_PAYMENT"
    assert clarification["payer_uid"] == "gm"
    assert clarification["evidence_ids"]


def test_referenced_evidence_survives_trim() -> None:
    """被 proposal/clarification 引用的证据不参与 trim 淘汰（审计链不断）。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "掏30金币买下精钢剑"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "矮人精钢剑"}]}}
    repair_unbacked_purchase(
        instance, data, "霍根接过金币，转身取下那柄矮人精钢剑，递到你面前。",
    )
    proposal = data["state_update"]["economy_proposals"][0]
    referenced_ids = set(proposal["evidence_ids"])
    assert referenced_ids
    # 模拟 applier 入队：引用关系进入 economy["proposals"] 后才受 trim 保护。
    instance.economy.setdefault("proposals", []).append({
        "id": "eco_placeholder", "evidence_ids": list(referenced_ids),
    })
    for _ in range(150):
        record_evidence(instance, evidence_type="noise", source="narration")
    surviving = {item["id"] for item in instance.economy["evidence"]}
    assert referenced_ids <= surviving
    assert len(instance.economy["evidence"]) <= 160


def test_npc_dialogue_does_not_trigger_deferred_payment() -> None:
    """NPC 台词里的 pay later 不触发赊账：只有玩家行动才产生意图。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下龙牙匕首"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "龙牙匕首"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data,
        "商人笑道：“you can pay tomorrow, no problem。”"
        "龙牙匕首卖30金币，说罢把匕首递到你手里。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 30
    assert not [
        c for c in instance.economy.get("clarifications", [])
        if c.get("reason") == "DEFERRED_PAYMENT"
    ]


def test_multi_quote_price_ambiguity_stays_fail_closed() -> None:
    """单行动混入两个报价：按句绑定无法唯一 → AMBIGUOUS_PRICE 澄清。"""

    instance = _instance()
    instance.action_queue = [
        {"user_id": "gm", "text": "我买那个500金币的龙牙匕首，旁边那个200金币的戒指也拿了"},
    ]
    data = {"state_update": {"loot": [{"player": "gm", "item": "龙牙匕首"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "龙牙匕首500金，戒指200金。老板把两样都包了起来。",
    )
    assert (dropped, ambiguous) == (1, True)
    assert not data["state_update"].get("economy_proposals")
    clarification = instance.economy["clarifications"][0]
    assert clarification["reason"] == "AMBIGUOUS_PRICE"
    assert clarification["amount_candidates"] == [200, 500]


def test_currency_labels_regression_custom_rule() -> None:
    """自定义规则货币（灵石）经 label 投影参与证据与绑定。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "掏30灵石买下丹药"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "丹药"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "丹药30灵石，掌柜收了钱把药递给你。",
        currency_labels=["灵石"],
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 30
    assert proposal["amount_source"] == "narration"


def test_japanese_game_language_parses_purchase_intent() -> None:
    """日文局：剣を買います 经 ja 资源产生意图（多语言分离目标）。"""

    instance = _instance()
    instance.language = "ja"
    instance.action_queue = [{"user_id": "gm", "text": "剣を買います"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "剣"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "商家は剣を渡した。金貨はまだ支払われていない。",
        currency_labels=["金貨"],
    )
    # 有 grant 但全无价格证据：物品不得白送 → 丢弃 + AMBIGUOUS_PRICE 澄清。
    assert (dropped, ambiguous) == (1, True)
    clarification = instance.economy["clarifications"][0]
    assert clarification["payer_uid"] == "gm"
    assert not data["state_update"].get("economy_proposals")


def test_unknown_language_falls_back_to_union() -> None:
    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买下精钢剑"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "精钢剑"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "精钢剑30金币，一手交钱一手交货。", currency_labels=["金币"],
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 30


def test_bounty_amount_not_attributed_to_purchase() -> None:
    """round-5 真实案例回归：叙事里无关的任务悬赏（40金）不得绑给商品。

    双人同轮各自购买（行动自带正确价格 15/20），叙事唯一金额是悬赏 40 金——
    全局唯一叙事金额曾把 40 绑给两件商品；删除该层级后应回落行动自报金额。
    """

    instance = _instance()
    instance.action_queue = [
        {"user_id": "gm", "text": "确认购买精钢长剑（15金）"},
        {"user_id": "p2", "text": "确认购买硬皮甲（20金）"},
    ]
    data = {"state_update": {"loot": [
        {"player": "gm", "item": "精钢长剑"},
        {"player": "p2", "item": "硬皮甲"},
    ]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data,
        "艾琳将精钢长剑推到尤落面前，又将硬皮甲叠好递给小林。"
        "矿道那单影狼的悬赏，公会现在提价到40金，视完成情况还有追加。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposals = data["state_update"]["economy_proposals"]
    by_payer = {p["uid"]: p for p in proposals}
    assert by_payer["gm"]["amount"] == 15
    assert by_payer["gm"]["amount_source"] == "player_action"
    assert by_payer["p2"]["amount"] == 20
    assert by_payer["p2"]["amount_source"] == "player_action"


def test_reward_noise_not_attributed_to_purchase() -> None:
    """叙事里的任务奖励（500金）不得成为购买药水的价格。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "买治疗药水（5金）"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "治疗药水"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data,
        "你完成了任务，获得500金币奖励。掌柜把治疗药水递给你，药水5金。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 5
    assert proposal["amount_source"] == "narration"


def test_question_actions_do_not_create_intents() -> None:
    """询价/疑问行动不产生购买意图，也不产生澄清噪音。"""

    instance = _instance()
    instance.action_queue = [
        {"user_id": "gm", "text": "还有其他买的吗我们一起看看"},
    ]
    data = {"state_update": {"loot": [{"player": "gm", "item": "通行证"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "城门卫兵说通行证需要支付5金币。",
    )
    # 疑问行动无意图：repair 层不产出提案/澄清。
    assert (dropped, ambiguous) == (0, False)
    assert not data["state_update"].get("economy_proposals")
    # round 管线后续由 discard 层接管：有价叙事 + 无提案 → 掉落 + 无主澄清。
    dropped_by_discard = discard_unbacked_purchase_items(
        instance, data, "城门卫兵说通行证需要支付5金币。",
    )
    assert dropped_by_discard == 1
    assert data["state_update"]["loot"] == []
    clarification = instance.economy["clarifications"][0]
    assert clarification["reason"] == "MISSING_SELLER_PRICE_CONFIRMATION"
    # 玩家只是询价：澄清不归属到任何 payer（无购买承诺）。
    assert clarification["payer_uid"] == ""


def test_intent_question_filter_keeps_real_intent() -> None:
    """过滤只针对疑问句："我要买这个"等陈述句不受影响。"""

    instance = _instance()
    instance.action_queue = [{"user_id": "gm", "text": "我要买这个"}]
    data = {"state_update": {"loot": [{"player": "gm", "item": "硬皮甲"}]}}
    dropped, ambiguous = repair_unbacked_purchase(
        instance, data, "掌柜把硬皮甲递给你，硬皮甲二十枚金币。",
    )
    assert (dropped, ambiguous) == (0, False)
    proposal = data["state_update"]["economy_proposals"][0]
    assert proposal["amount"] == 20
