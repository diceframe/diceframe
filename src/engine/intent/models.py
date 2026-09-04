"""Intent 层的结构化模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PurchaseIntent:
    """One actor's stated purchase intent, parsed from their own action text.

    ``item_context`` 是去掉金额与购买动词后的商品指代片段，仅用于展示。
    意图只是请求记录，不是交易事实，也不携带可收费金额。
    """

    actor_uid: str
    action_text: str
    item_context: str
    source: str = "keyword"  # keyword = 规则解析（AI 报价经 check_planner 工具调用）
