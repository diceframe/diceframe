"""FIX-08 验收：纯物品奖励提案的结算语义（施工单 §10 步骤 12）。

授权决策（用户 2026-xx）：允许 ``kind="reward"`` 且 ``amount = 0`` 的提案携带
``rewards`` 物品，并复用既有 proposal 结算/快照/回滚路径。这里冻结该决策的边界：

```text
amount = 0 只在 reward 合法，且必须真的带物品
付款 / 购买仍然要求正金额（不变量不放宽）
物品只在 resolve_proposal 里写，且必须带 before-image（可整轮回滚）
有物品却缺发放通道 → fail closed（不产生"结算了但没发"的半截状态）
```
"""

from __future__ import annotations

import pytest

from src.engine.economy import queue_proposal, resolve_proposal
from src.engine.game_instance import GameInstance


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "fix08", "bot"), gm_uid="gm")
    instance.players["gm"] = {
        "character_name": "守门人",
        "character_sheet": {"character_name": "守门人", "inventory": [], "gold": 5},
    }
    return instance


def _queue_reward(instance: GameInstance, **overrides) -> dict:
    values = {
        "kind": "reward",
        "amount": 0,
        "recipient_uid": "gm",
        "approval_policy": "gm",
        "rewards": [{"name": "Brass Key", "category": "key_item"}],
    }
    values.update(overrides)
    return queue_proposal(instance, **values)


# ---- 金额不变量 -------------------------------------------------------------


def test_zero_amount_is_only_valid_for_item_rewards() -> None:
    instance = _instance()

    proposal = _queue_reward(instance)
    assert proposal["amount"] == 0
    assert proposal["status"] == "pending"

    # 零金额但没有物品 = 什么都不做的空提案 → 拒绝。
    with pytest.raises(ValueError, match="requires item rewards"):
        _queue_reward(instance, rewards=[])
    # 付款/购买仍然要求正金额（既有不变量）。
    for kind in ("payment", "purchase"):
        with pytest.raises(ValueError, match="amount is out of range"):
            queue_proposal(
                instance, kind=kind, amount=0, payer_uid="gm", recipient_uid="gm",
            )
    with pytest.raises(ValueError, match="amount is out of range"):
        _queue_reward(instance, amount=-5)


# ---- 结算：只有 GM 确认后才写 inventory -------------------------------------


def _grant(sheet: dict, reward: dict) -> None:
    sheet.setdefault("inventory", []).append({"name": reward["name"]})


def test_zero_amount_reward_grants_items_with_a_rollback_snapshot() -> None:
    instance = _instance()
    proposal = _queue_reward(instance)

    # 结算前：物品还没有进角色卡。
    assert instance.get_character_sheet("gm")["inventory"] == []

    result = resolve_proposal(
        instance, proposal["id"], actor_uid="gm", accepted=True, grant_reward=_grant,
    )

    assert result["ok"] is True, result
    assert [row["name"] for row in instance.get_character_sheet("gm")["inventory"]] == [
        "Brass Key",
    ]
    transaction = result["transaction"]
    assert transaction["kind"] == "reward"
    # 零金额 → 没有货币流水；物品靠绝对 before-image 还原。
    assert transaction["entries"] == []
    snapshot = transaction["reward_snapshots"][0]
    assert snapshot["recipient_uid"] == "gm"
    assert snapshot["before"]["inventory"] == []
    assert [row["name"] for row in snapshot["after"]["inventory"]] == ["Brass Key"]


def test_item_reward_without_a_grant_channel_fails_closed() -> None:
    instance = _instance()
    proposal = _queue_reward(instance)

    result = resolve_proposal(
        instance, proposal["id"], actor_uid="gm", accepted=True, grant_reward=None,
    )

    assert result["ok"] is False
    assert result["code"] == "REWARD_UNSUPPORTED"
    # 提案没有被判成 committed，物品也没有落地。
    assert proposal["status"] == "pending"
    assert instance.get_character_sheet("gm")["inventory"] == []


def test_only_the_gm_may_settle_a_gm_approved_reward() -> None:
    instance = _instance()
    instance.players["player"] = {
        "character_name": "同伴", "character_sheet": {"character_name": "同伴"},
    }
    proposal = _queue_reward(instance)

    denied = resolve_proposal(
        instance, proposal["id"], actor_uid="player", accepted=True,
        grant_reward=_grant,
    )

    assert denied["ok"] is False
    assert denied["code"] == "FORBIDDEN"
    assert instance.get_character_sheet("gm")["inventory"] == []


def test_currency_reward_still_works_with_a_positive_amount() -> None:
    """回归：原有"纯货币奖励"语义不变（零金额改动不牵连它）。"""

    instance = _instance()
    proposal = queue_proposal(
        instance, kind="reward", amount=3, recipient_uid="gm", approval_policy="gm",
    )

    result = resolve_proposal(
        instance, proposal["id"], actor_uid="gm", accepted=True,
    )

    assert result["ok"] is True
    assert instance.get_character_sheet("gm")["gold"] == 8
    assert result["transaction"]["entries"][0]["delta"] == 3
