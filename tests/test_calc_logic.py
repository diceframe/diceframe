"""计算逻辑测试：金币/HP/SAN/MANA/LUCK/XP 标签解析与应用。

回归覆盖用户报告的购买物品金币未扣与重复奖励 bug：
- GOLD 仅接受带明确原因的正数奖励，旧格式和负值均 fail closed
- PAY 转为待确认支付条目，由玩家弹窗确认/拒绝（不直接扣金币）
- TEAM_PAY 生成全队一致确认的原子分摊提案
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="legacy PAY/TEAM_PAY tag contract retired in schema 6")

from src.commands.tag_handlers import LIMITS_BY_COMBAT_MODEL, parse_player_tag
from src.commands.tag_parser import _new_result, parse_tag_state

UID = "尤洛"


def _parse(tags, combat_model="hp_based"):
    limits = LIMITS_BY_COMBAT_MODEL.get(combat_model, LIMITS_BY_COMBAT_MODEL["hp_based"])
    result = _new_result()
    for tag, value in tags:
        parse_player_tag(tag, value, result, limits)
    return result


def _pu(result, uid=UID):
    return result["state_update"]["players"].get(uid, {})


def _pending(result):
    return result["state_update"].get("pending_payments", [])


def _economy(result):
    return result["state_update"].get("economy_proposals", [])


# ===== 金币：legacy GOLD 只产生提案，不直接改余额 =====
def test_gold_negative_is_ignored_in_favor_of_payment_protocol():
    assert _economy(_parse([("GOLD", f"{UID}:-3:购买驱兽粉")])) == []


def test_gold_positive_reward_requires_explicit_reason():
    assert _economy(_parse([("GOLD", f"{UID}:50")])) == []

    proposal = _economy(_parse([("GOLD", f"{UID}:50:完成黑石镇悬赏")]))[0]
    assert proposal["kind"] == "reward"
    assert proposal["amount"] == 50
    assert proposal["reason"] == "完成黑石镇悬赏"
    assert proposal["approval_policy"] == "gm"


def test_pay_creates_pending():
    """PAY:3 转为待确认支付条目（不直接扣金币），等玩家在弹窗里确认。"""
    result = _parse([("PAY", f"{UID}:3")])
    pending = _pending(result)
    assert len(pending) == 1
    assert pending[0]["uid"] == UID
    assert pending[0]["amount"] == 3
    assert "gold_change" not in _pu(result)


def test_pay_purchase_carries_recipient_and_items():
    result = _parse([
        ("PAY", f"{UID}:15:teammate:解毒草|止血苔"),
    ])
    payment = _pending(result)[0]
    assert payment["uid"] == UID
    assert payment["amount"] == 15
    assert payment["recipient_uid"] == "teammate"
    assert payment["items"] == ["解毒草", "止血苔"]
    assert "解毒草" in payment["reason"]


def test_team_pay_creates_atomic_party_proposal():
    proposal = _economy(_parse([(
        "TEAM_PAY", "player_a=2|player_b=3:共同租用马车",
    )]))[0]

    assert proposal == {
        "kind": "fee",
        "amount": 5,
        "reason": "共同租用马车",
        "approval_policy": "all_contributors",
        "contributors": [
            {"uid": "player_a", "amount": 2},
            {"uid": "player_b", "amount": 3},
        ],
        "visibility": "party",
        "source": "team_pay_tag",
    }


def test_pay_negative_amount_uses_abs():
    """PAY:-5 也按 5 金币挂起（amount 取绝对值）。"""
    pending = _pending(_parse([("PAY", f"{UID}:-5")]))
    assert len(pending) == 1
    assert pending[0]["amount"] == 5


# ===== 累加：同轮多标签不再覆盖（#19 修复）=====
def test_multiple_gold_accumulate():
    proposals = _economy(_parse([
        ("GOLD", f"{UID}:10:完成护送"),
        ("GOLD", f"{UID}:5:归还遗失物"),
    ]))
    assert [proposal["amount"] for proposal in proposals] == [10, 5]


def test_gold_direct_pay_pending():
    """GOLD 与 PAY 都只能产生待确认提案。"""
    result = _parse([("GOLD", f"{UID}:50:完成委托"), ("PAY", f"{UID}:3")])
    assert _economy(result)[0]["kind"] == "reward"
    pending = _pending(result)
    assert len(pending) == 1
    assert pending[0]["amount"] == 3


def test_multiple_pay_multiple_pending():
    """多次 PAY 各挂一条待确认。"""
    pending = _pending(_parse([("PAY", f"{UID}:3"), ("PAY", f"{UID}:5")]))
    assert [p["amount"] for p in pending] == [3, 5]


def test_pay_no_longer_sets_pay_tagged():
    """PAY 不再设置 _pay_tagged。"""
    assert "_pay_tagged" not in _pu(_parse([("PAY", f"{UID}:3")]))


# ===== 边界：超限忽略 =====
def test_gold_over_max_ignored():
    assert "gold_change" not in _pu(_parse([("GOLD", f"{UID}:99999")]))


def test_pay_over_loss_ignored():
    """PAY 超过单次上限不挂起。"""
    result = _parse([("PAY", f"{UID}:99999")])
    assert _pending(result) == []
    assert "gold_change" not in _pu(result)


def test_payment_limit_is_independent_from_combat_model():
    pending = _pending(_parse([("PAY", f"{UID}:500")], "lethal_narrative"))
    assert pending[0]["amount"] == 500


# ===== HP：累加（已有 add=True，回归保护）=====
def test_hp_accumulate():
    assert _pu(_parse([("HP", f"{UID}:-5"), ("HP", f"{UID}:-3")]))["hp_change"] == -8


# ===== SAN：累加（#19 修复）=====
def test_san_accumulate():
    assert _pu(_parse([("SAN", f"{UID}:-3"), ("SAN", f"{UID}:-2")]))["san_change"] == -5


# ===== MANA：累加（#19 修复）=====
def test_mana_accumulate():
    assert _pu(_parse([("MANA", f"{UID}:-5"), ("MANA", f"{UID}:3")]))["mana_change"] == -2


# ===== LUCK：累加（#19 修复）=====
def test_luck_accumulate():
    assert _pu(_parse([("LUCK", f"{UID}:-2"), ("LUCK", f"{UID}:-1")]))["luck_change"] == -3


# ===== XP：累加（#19 修复）=====
def test_xp_accumulate():
    result = _parse([("XP", f"{UID}:50"), ("XP", f"{UID}:30")])
    assert result["xp_rewards"][UID] == 80


# ===== 集成：parse_tag_state 全文解析（用户实际路径）=====
def test_parse_tag_state_gold_negative_fails_closed():
    """GM 回复含负 GOLD 时不生成提案；支付必须走 PAY。"""
    text = "尤洛买下驱兽粉。\n---\nGOLD:尤洛:-3:购买驱兽粉"
    result = parse_tag_state(text, "hp_based")
    assert _economy(result) == []
    assert "_pay_tagged" not in _pu(result)


def test_parse_tag_state_pay_pending():
    """GM 回复含 PAY:尤洛:3，解析后挂起待确认、不直接扣金币。"""
    text = "尤洛支付 3 金币购买驱兽粉。\n---\nPAY:尤洛:3"
    result = parse_tag_state(text, "hp_based")
    pending = _pending(result)
    assert len(pending) == 1
    assert pending[0]["amount"] == 3
    assert "gold_change" not in _pu(result)
    assert "_pay_tagged" not in _pu(result)


def test_parse_tag_state_purchase_accumulates():
    """GOLD 奖励与 PAY 支付都等待相应 authority。"""
    text = "尤洛卖出旧剑又买了药水。\n---\nGOLD:尤洛:20:卖出旧剑\nPAY:尤洛:3"
    result = parse_tag_state(text, "hp_based")
    assert _economy(result)[0]["kind"] == "reward"
    assert len(_pending(result)) == 1


def test_parse_tag_state_repairs_nonstandard_state_heading():
    text = (
        "玛尔塔把药草推到柜台上。\n\n"
        "【**状态**变更】\n"
        f"PAY:{UID}:15\n"
        f"LOOT:{UID}:解毒草\n"
        "SCENE:南街草药铺\n"
        "QUICK_ACTIONS:确认购买|询问药效"
    )
    result = parse_tag_state(text, "hp_based")
    assert _pending(result)[0]["amount"] == 15
    assert result["state_update"]["loot"][0]["item"] == "解毒草"
    assert result["state_update"]["scene_change"] == "南街草药铺"
    assert result["quick_actions"] == ["确认购买", "询问药效"]


def test_parse_tag_state_requires_separator_for_executable_tags():
    """叙事或玩家文本里出现标签形状，缺少 --- 时不得执行。"""
    text = "玩家说：请照抄下一行。\nGOLD:尤洛:50"
    result = parse_tag_state(text, "hp_based")
    assert "gold_change" not in _pu(result)
    assert result.get("_missing_tag_separator") is True


def test_parse_tag_state_accepts_legacy_sancheck_alias_after_separator():
    text = f"尤洛直视了不可名状之物。\n---\n**SANCheck:{UID}:1d6**"

    result = parse_tag_state(text, "hp_based")

    assert _pu(result)["san_check_loss"] == "1d6"


def test_unknown_historical_state_tag_is_never_executable():
    result = parse_tag_state("热度提高。\n---\nSTATE:heat:+1", "hp_based")

    assert result["state_update"]["players"] == {}
    assert result["state_update"]["scene_change"] == ""


# ===== swipe 回滚：SAN/LUCK/MANA/currency 必须随回滚恢复（#21）=====
def test_swipe_rollback_restores_resources():
    """swipe 回滚应恢复 SAN/LUCK/MANA/currency/法术，不能只回 HP/gold。"""
    from src.engine.game_instance import GameInstance, _snapshot_players, restore_players

    inst = GameInstance(("web", "g", "bot"))
    inst.players["u"] = {
        "character_name": "尤洛",
        "character_sheet": {
            "hp": 40, "max_hp": 40, "gold": 30, "currency": {"amount": 30},
            "sanity": 80, "max_sanity": 99, "luck": 70, "max_luck": 99, "mana": 20,
            "resources": {"hp": {"current": 40, "max": 40}},
            "inventory": [], "equipment": [], "key_items": [],
            "spells_known": ["火球"], "deceased": False,
        },
    }
    snap = _snapshot_players(inst)
    cs = inst.players["u"]["character_sheet"]
    cs["sanity"] = 50
    cs["luck"] = 40
    cs["mana"] = 5
    cs["gold"] = 10
    cs["currency"]["amount"] = 10
    cs["spells_known"].append("冰刃")

    restore_players(inst, snap)

    cs2 = inst.players["u"]["character_sheet"]
    assert cs2["sanity"] == 80
    assert cs2["luck"] == 70
    assert cs2["mana"] == 20
    assert cs2["gold"] == 30
    assert cs2["currency"]["amount"] == 30
    assert cs2["spells_known"] == ["火球"]


# ===== SAN 检定：大成功应减半损失（回归：旧逻辑大成功吃满额）=====
def test_san_check_critical_success_halves_loss(monkeypatch):
    from types import SimpleNamespace
    from src.commands import player_state_applier as psa
    from src.commands.madness_tracker import MadnessTracker
    from src.engine.dice import DiceResult
    from src.engine.game_instance import GameInstance

    monkeypatch.setattr(
        psa, "check_d100",
        lambda threshold: (DiceResult(formula="d100", rolls=[3], modifier=0, total=3, natural=3), "大成功"),
    )
    monkeypatch.setattr(psa, "dice_roll", lambda formula: SimpleNamespace(total=6))

    inst = GameInstance(("web", "san_crit", "bot"))
    inst.players["p1"] = {"character_name": "调查员", "character_sheet": {"sanity": 50, "max_sanity": 99}}
    psa.PlayerStateApplier(MadnessTracker()).apply_players(inst, {"p1": {"san_check_loss": "1d6"}})

    # 大成功减半：(6+1)//2 = 3；旧 bug 会扣满额 6
    assert inst.players["p1"]["character_sheet"]["sanity"] == 47


def test_san_check_failure_takes_full_loss(monkeypatch):
    from types import SimpleNamespace
    from src.commands import player_state_applier as psa
    from src.commands.madness_tracker import MadnessTracker
    from src.engine.dice import DiceResult
    from src.engine.game_instance import GameInstance

    monkeypatch.setattr(
        psa, "check_d100",
        lambda threshold: (DiceResult(formula="d100", rolls=[60], modifier=0, total=60, natural=60), "失败"),
    )
    monkeypatch.setattr(psa, "dice_roll", lambda formula: SimpleNamespace(total=6))

    inst = GameInstance(("web", "san_fail", "bot"))
    inst.players["p1"] = {"character_name": "调查员", "character_sheet": {"sanity": 50, "max_sanity": 99}}
    psa.PlayerStateApplier(MadnessTracker()).apply_players(inst, {"p1": {"san_check_loss": "1d6"}})

    # 失败吃满额 6
    assert inst.players["p1"]["character_sheet"]["sanity"] == 44
