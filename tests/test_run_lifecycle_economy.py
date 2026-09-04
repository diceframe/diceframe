from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from src.engine.economy import (
    blocking_economy_proposals,
    cancel_proposals_for_player,
    has_blocking_economy_decision,
    is_nonblocking_personal_purchase,
    pending_memory_deliveries,
    pending_memory_reversals,
    pending_proposals,
    proposal_transition_allowed,
    queue_effect_group,
    queue_purchase_offer,
    queue_proposal,
    reconcile_rollback_snapshot,
    reverse_round_economy,
    resolve_proposal,
    set_proposal_status,
    _remove_reward_delta,
)
from src.commands.economy_effects import (
    discard_unearned_reward_proposals,
    guard_unbacked_payment_narration,
    defer_narrative_effects,
)
from src.commands.state_items import append_key_item
from src.commands.state_update_applier import StateUpdateApplier
from src.engine.game_instance import GameInstance, GameRegistry, restore_players, _snapshot_players
from src.llm.client import LLMResponse
from src.llm.context_builder import build_context
from src.memory.delta import MemoryStore
from src.migrations.instance import CURRENT_INSTANCE_SCHEMA_VERSION, migrate_game_state_payload
from src.webui.services import characters

from webapi_harness import web_api  # noqa: F401


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "economy", "bot"), gm_uid="gm")
    instance.players = {
        "gm": {"character_name": "GM", "character_sheet": {"gold": 30, "currency": {"amount": 30}}},
        "p2": {"character_name": "P2", "character_sheet": {"gold": 20, "currency": {"amount": 20}}},
    }
    return instance


def test_narration_staleness_allows_resolutions_only() -> None:
    """生成叙事期间玩家确认既有提案不判过期；回滚类迁移仍判过期。"""
    from src.engine.economy import (
        economy_changes_are_resolutions_only,
        economy_fingerprint,
    )

    def fp(*statuses: str) -> dict[str, str]:
        return {f"eco_{i}": status for i, status in enumerate(statuses)}

    unchanged = fp("pending", "pending", "committed")
    assert economy_changes_are_resolutions_only(unchanged, unchanged)
    # pending -> 任一终态：本轮已诊断的误杀场景（生成期间确认弹窗）。
    assert economy_changes_are_resolutions_only(
        fp("pending", "pending"), fp("committed", "declined"),
    )
    assert economy_changes_are_resolutions_only(
        fp("pending",), fp("rejected",),
    )
    assert economy_changes_are_resolutions_only(
        fp("pending",), fp("cancelled",),
    )
    # 生成期间新增提案必须判过期，即使 queue_proposal 没有 revision。
    assert not economy_changes_are_resolutions_only(
        fp("pending"), {"eco_0": "pending", "eco_new": "pending"},
    )
    # 回滚类迁移必须判过期。
    assert not economy_changes_are_resolutions_only(
        fp("committed"), fp("reversed"),
    )
    assert not economy_changes_are_resolutions_only(
        fp("committed"), fp("superseded"),
    )
    assert not economy_changes_are_resolutions_only(
        fp("committed"), fp("pending"),
    )
    assert not economy_changes_are_resolutions_only(
        fp("pending"), fp("superseded"),
    )
    assert not economy_changes_are_resolutions_only(
        fp("pending"), {},
    )


@pytest.mark.asyncio
async def test_payer_confirmation_during_generation_keeps_round_output(tmp_path) -> None:
    """回归：round_processor 的两个守卫在“结算既有提案”后必须放行。

    用 economy_fingerprint 直接模拟 process_round_impl 的捕获/比较时序：
    捕获后 resolve 一笔 pending（真实案例中玩家在叙事生成 3.7s 内点确认），
    守卫须放行；而回滚产生的 reversed 迁移须丢弃。
    """
    from src.commands.round_processor import (
        economy_changes_are_resolutions_only,
        economy_fingerprint,
    )
    from src.engine.economy import queue_proposal, resolve_proposal

    instance = _instance()
    instance.round_number = 5
    proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=2,
        rewards=[{"name": "火把", "category": "misc"}],
        source="gm_manual",
        source_ref="gm_manual:stall-test",
    )
    before = economy_fingerprint(instance)
    resolved = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=True)
    assert resolved["ok"] is True
    assert economy_changes_are_resolutions_only(before, economy_fingerprint(instance))

    rollback_proposal = queue_proposal(
        instance,
        kind="purchase",
        payer_uid="gm",
        recipient_uid="gm",
        amount=3,
        rewards=[{"name": "绳索", "category": "misc"}],
        source="gm_manual",
        source_ref="gm_manual:stall-test-2",
    )
    resolve_proposal(instance, rollback_proposal["id"], actor_uid="gm", accepted=True)
    before_rollback = economy_fingerprint(instance)
    reverse_round_economy(instance, instance.round_number)
    assert not economy_changes_are_resolutions_only(
        before_rollback, economy_fingerprint(instance),
    )


def test_unconfirmed_purchase_grants_are_stripped() -> None:
    """验收 #7：有待确认收费时，模型输出同名物品 grant 必须被剥离。"""
    from src.engine.economy import filter_unconfirmed_purchase_grants, queue_purchase_offer

    instance = _instance()
    proposal = queue_purchase_offer(
        instance, payer_uid="gm", amount=50, items=["长剑"],
        source="gm_manual", source_ref="gm_manual:test",
    )
    assert proposal["visibility"] == "party"
    data = {"state_update": {
        "loot": [
            {"player": "gm", "item": "长剑"},
            {"player": "gm", "item": "无关护符"},
            {"player": "p2", "item": "长剑"},
        ],
        "players": {"gm": {"equip_gain": "皮甲", "hp_change": -1}},
    }}
    removed = filter_unconfirmed_purchase_grants(instance, data)
    assert removed == 1
    assert data["state_update"]["loot"] == [
        {"player": "gm", "item": "无关护符"},
        {"player": "p2", "item": "长剑"},
    ]
    # 无 pending 提案时不剥离任何东西。
    assert filter_unconfirmed_purchase_grants(instance, {"state_update": {
        "loot": [{"player": "gm", "item": "另一把长剑"}],
    }}) == 1
    instance.economy["proposals"][0]["status"] = "committed"
    assert filter_unconfirmed_purchase_grants(instance, {"state_update": {
        "loot": [{"player": "gm", "item": "长剑"}],
    }}) == 0


def test_only_plain_personal_purchase_is_nonblocking() -> None:
    instance = _instance()
    purchase = queue_proposal(
        instance,
        kind="purchase",
                source="gm_manual",
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
        source="gm_manual",
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
    assert "本次未扣款" in guarded


def test_narrated_payment_with_proposal_keeps_pending_notice_path() -> None:
    narration = "你支付了五枚金币。"
    data = {"state_update": {"economy_proposals": [{"kind": "payment", "uid": "gm", "amount": 5}]}}
    assert guard_unbacked_payment_narration(narration, data, "zh-CN") == narration


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
        instance, kind="payment", payer_uid="gm", recipient_uid="gm", amount=1, source="gm_manual",
    )
    declined = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=False)
    assert declined["ok"] is True
    for accepted in (True, False):
        repeat = resolve_proposal(instance, proposal["id"], actor_uid="gm", accepted=accepted)
        assert repeat["code"] == "ALREADY_RESOLVED"


def test_queue_proposal_rejects_payer_outside_game() -> None:
    instance = _instance()
    with pytest.raises(ValueError):
        queue_proposal(instance, source="gm_manual", kind="purchase", payer_uid="ghost", amount=5)
    with pytest.raises(ValueError):
        queue_proposal(instance, source="gm_manual", kind="transfer", payer_uid="ghost", amount=5)
    # 奖励类提案没有付款人；收款人资格在结算时校验。
    reward = queue_proposal(
        instance, kind="reward", recipient_uid="p2", amount=5, approval_policy="gm",
    )
    assert reward["status"] == "pending"


def test_personal_purchase_with_effect_group_remains_blocking() -> None:
    instance = _instance()
    purchase = queue_proposal(
        instance,
        kind="purchase",
                source="gm_manual",
        payer_uid="gm",
        recipient_uid="gm",
        amount=5,
        rewards=[{"name": "通行证", "category": "key_item"}],
    )
    queue_effect_group(instance, [purchase], {"state_update": {"scene_change": "城门内"}})
    assert not is_nonblocking_personal_purchase(instance, purchase)
    assert has_blocking_economy_decision(instance)


def test_save_migration_assigns_stable_run_and_drops_legacy_pending_payments() -> None:
    legacy = {
        "game_key": ["web", "legacy", "bot"],
        "state": "paused",
        "started_at": "2025-01-01T00:00:00+00:00",
        "pending_payments": [{"id": "pay_old", "uid": "p1", "amount": 3, "status": "pending"}],
    }
    first = migrate_game_state_payload(legacy)
    second = migrate_game_state_payload(first)

    assert first == second
    assert first["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert first["economy"]["external_effects_outbox"] == []
    assert first["run_id"].startswith("run_")
    assert first["memory_namespace"] == "('web', 'legacy', 'bot')"
    # schema 6+ drops legacy pending payments instead of guessing them
    # into the proposal model (attribution was not authoritative).
    assert first["economy"]["proposals"] == []


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


def test_late_personal_purchase_reopens_when_settlement_round_is_rolled_back() -> None:
    instance = _instance()
    instance.round_number = 5
    purchase = queue_proposal(
        instance,
        kind="purchase",
                source="gm_manual",
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
    assert purchase in pending_proposals(instance)
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
                source="gm_manual",
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
    assert purchase not in pending_proposals(instance)
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
                source="gm_manual",
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
    assert purchase in pending_proposals(instance)

    registry = GameRegistry(tmp_path / "saves")
    registry.register(instance)
    await registry.save(instance)
    registry._instances.clear()
    recovered = await registry.load(instance.game_key)
    assert recovered is not None
    assert recovered.get_character_sheet("gm")["currency"]["amount"] == pre_payment["currency"]["amount"]
    assert recovered.get_character_sheet("gm")["inventory"] == pre_payment.get("inventory", [])
    assert pending_proposals(recovered)[0]["status"] == "pending"


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
                source="gm_manual",
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
    assert pending_proposals(recovered)[0]["status"] == "pending"


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
                source="gm_manual",
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
    assert all(item["id"] != proposal["id"] for item in pending_proposals(instance))
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
        source="gm_manual",
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
        instance, kind="purchase", payer_uid=uid, recipient_uid=uid, amount=5, source="gm_manual",
        rewards=[{"name": "药水", "category": "consumable"}],
    )
    second = queue_proposal(
        instance, kind="purchase", payer_uid=uid, recipient_uid=uid, amount=5, source="gm_manual",
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
    assert {item["id"] for item in pending_proposals(instance)} == {first["id"], second["id"]}
    assert all(tx["status"] == "reversed" for tx in instance.economy["transactions"])

    registry = GameRegistry(tmp_path / "saves")
    registry.register(instance)
    await registry.save(instance)
    registry._instances.clear()
    recovered = await registry.load(instance.game_key)
    assert recovered is not None
    assert recovered.get_character_sheet(uid)["currency"]["amount"] == 30
    assert recovered.get_character_sheet(uid)["inventory"] == []
    assert {item["id"] for item in pending_proposals(recovered)} == {first["id"], second["id"]}

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
        source="gm_manual",
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
        source="gm_manual",
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
    assert proposal not in pending_proposals(instance)


def test_team_split_rejects_incomplete_or_duplicate_contributor_plan() -> None:
    instance = _instance()

    with pytest.raises(ValueError):
        queue_proposal(
            instance,
            kind="fee",
            source="gm_manual",
            amount=10,
            approval_policy="all_contributors",
            contributors=[{"uid": "gm", "amount": 4}, {"uid": "gm", "amount": 4}],
        )


def test_removing_party_member_cancels_their_unresolved_group_proposal() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="fee",
        source="gm_manual",
        amount=10,
        approval_policy="all_contributors",
        contributors=[{"uid": "gm", "amount": 5}, {"uid": "p2", "amount": 5}],
    )
    effect_group = queue_effect_group(
        instance,
        [proposal],
        {"state_update": {"scene_change": "付费区域"}},
    )

    cancel_proposals_for_player(instance, "p2")

    assert proposal["status"] == "cancelled"
    assert proposal["resolution_code"] == "PLAYER_REMOVED"
    assert proposal not in pending_proposals(instance)
    assert effect_group is not None
    assert effect_group["status"] == "discarded"
    assert "effects" not in effect_group
    assert instance.economy["outcomes"][-1]["status"] == "cancelled"


def test_declining_payment_discards_deferred_narrative_effects() -> None:
    instance = _instance()
    proposal = queue_proposal(
        instance,
        kind="payment",
        source="gm_manual",
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
        source="gm_manual",
        payer_uid="gm",
        recipient_uid="gm",
        amount=3,
    )
    second = queue_proposal(
        instance,
        kind="payment",
        source="gm_manual",
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
        source="gm_manual",
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
        source="gm_manual",
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
async def test_purchase_settlement_is_party_visible_even_for_legacy_private_outcome(web_api) -> None:
    """购买结算不能落入角色感知；旧的 private 标记也要升级为公开事件。"""
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Public Purchase Settlement",
        players=[{"character_name": "Buyer", "attributes": {"str": 10}, "gold": 20}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    instance.append_log_entry({
        "round": instance.round_number,
        "actions": [],
        "gm_response": "商人提出交易。",
        "state_changes": [],
    })

    characters._record_economy_outcome_in_round(instance, {
        "kind": "purchase",
        "visibility": "private",
        "status": "committed",
        "effects_status": "committed",
        "amount": 5,
        "reason": "购买通行证",
        "round": instance.round_number,
        "id": "legacy-purchase-outcome",
    })

    assert any("购买通行证" in item for item in instance.log[-1]["state_changes"])
    assert not instance.private_log


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
                source="gm_manual",
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
        source="gm_manual",
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
                source="gm_manual",
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
                source="gm_manual",
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
            source="gm_manual",
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
            source="gm_manual",
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
                        source="gm_manual",
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
        source="gm_manual",
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


async def test_in_flight_narration_keeps_output_when_pending_proposal_resolves(
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
    proposal = queue_purchase_offer(
        instance,
        payer_uid=uid,
        amount=3,
        items=["商人的旧报价"],
        source="gm_manual",
    )
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
    declined = await api.resolve_payment(
        created["game_key"], proposal["id"], False, uid,
    )
    assert declined["ok"] is True
    release.set()

    narration, private = await processing

    # 结算既有提案（玩家在生成期间确认/拒绝自己的弹窗）不使叙事过期。
    assert narration == "这条叙事已经过期。"
    assert private == {}
    assert any(entry.get("gm_response") == "这条叙事已经过期。" for entry in instance.log)


@pytest.mark.asyncio
async def test_in_flight_narration_is_discarded_after_rollback(
    web_api,
    monkeypatch,
) -> None:
    """回滚类迁移（结算回滚 reopen / reverse）仍必须在生成后丢弃叙事。"""
    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Rollback Race",
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
    settled = queue_proposal(
        instance,
        kind="payment",
        source="gm_manual",
        payer_uid=uid,
        recipient_uid=uid,
        amount=3,
        reason="已结算的旧付款",
    )
    committed = await api.resolve_payment(created["game_key"], settled["id"], True, uid)
    assert committed["ok"] is True

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
    # 生成期间回滚本轮：committed -> pending（结算回滚 reopen），必须判过期。
    reverse_round_economy(instance, instance.round_number)
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
    proposal = queue_purchase_offer(
        instance,
        payer_uid=uid,
        amount=1,
        items=["旧报价"],
        source="gm_manual",
    )
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
    declined = await api.resolve_payment(
        created["game_key"], proposal["id"], False, uid,
    )
    assert declined["ok"] is True
    release.set()

    checks = await planning

    # 结算既有提案不使规划过期：检定计划与经济提案（economy_offers）都保留。
    assert len(checks) == 1
    assert instance.round_checks_prepared is True
    assert "check_request" in instance.action_queue[0]
    assert instance.total_llm_calls > calls_before


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
        source="gm_manual",
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
        instance, kind="payment", payer_uid=uid, recipient_uid=uid, amount=2, source="gm_manual",
    )
    queue_effect_group(
        instance,
        [pending],
        {"state_update": {"scene_change": "不应进入的场景"}},
    )
    declined = queue_proposal(
        instance, kind="payment", payer_uid=uid, recipient_uid=uid, amount=1, source="gm_manual",
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
    assert pending_proposals(restarted) == []
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
        source="gm_manual",
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
    assert pending_proposals(reset_instance) == []
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
    proposal = queue_proposal(instance, source="gm_manual", kind="payment", payer_uid=uid, recipient_uid=uid, amount=5)
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
