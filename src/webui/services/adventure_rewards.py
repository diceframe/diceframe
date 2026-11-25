"""Adapt adventure item reward intents to the existing proposal authority."""

from __future__ import annotations

from typing import Any

from src.engine.economy import queue_proposal


def queue_adventure_reward_intents(
    instance: Any, intents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """FIX-08 §10 步骤 12：adventure item_reward → 既有奖励提案权威。

    Adventure 的 ``item_reward`` 只声明"该给什么"（reward intent）；发放必须走
    既有 proposal/settlement 权威，**绝不直接写 inventory**：
    ``kind="reward"`` 且 ``amount=0``（纯物品、无货币）、``approval_policy="gm"``
    → 提案 pending，GM 用既有的支付确认路径结算，物品才由
    ``characters.grant_reward`` 写入角色卡（带 before-image，整轮回滚可还原）。

    幂等：``source_ref`` 由奖励内容 + 收件人确定（同一批奖励不会被排两次队），
    重放/中止后同一身份会走 ``_existing_by_source`` 的既有语义。
    """

    named = []
    for intent in intents or []:
        if str(intent.get("kind") or "") != "item_grant":
            continue
        name = str(intent.get("name") or "")
        if not name.strip():
            continue
        reward = {
            "name": name,
            "category": str(intent.get("category") or ""),
        }
        # 身份映射是不透明的 metadata：通用 webui 层不解释 D&D 规则，
        # 只保证它随奖励一起到达 grant_reward（缺失时保持旧形状）。
        item_ref = str(intent.get("item_ref") or "")
        if item_ref:
            reward["item_ref"] = item_ref
        named.append(reward)
    if not named:
        return []
    recipient_uid = str((intents or [{}])[0].get("recipient_uid") or "")
    if not recipient_uid:
        raise ValueError("adventure reward intent has no recipient")
    refs = sorted({
        f"{intent.get('ref', {}).get('source', '')}:{intent.get('ref', {}).get('id', '')}"
        for intent in intents or []
        if isinstance(intent.get("ref"), dict)
    } | {reward["name"] for reward in named})
    proposal = queue_proposal(
        instance,
        kind="reward",
        amount=0,
        recipient_uid=recipient_uid,
        reason="冒险奖励",
        source="adventure",
        source_ref=f"adventure_reward:{recipient_uid}:{'|'.join(refs)}",
        approval_policy="gm",
        rewards=named,
        visibility="party",
    )
    return [{
        "proposal_id": str(proposal.get("id") or ""),
        "status": str(proposal.get("status") or ""),
        "kind": "reward",
    }]
