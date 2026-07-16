"""Platform-neutral DiceFrame command predicates."""

from __future__ import annotations

import re


def is_help(text: str) -> bool:
    return text.strip().lower() in {"帮助", "help", "?", "？", "怎么玩", "新手", "开始", "指令"}


def is_character_create(text: str) -> bool:
    return text.strip().lower() in {"新建角色", "创建角色", "车卡", "建卡", "角色创建", "我要建卡"}


def is_ai_character_create(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return normalized in {
        "ai车卡", "ai建卡", "ai新建角色", "ai创建角色",
        "私聊车卡", "私聊建卡", "智能车卡", "辅助车卡",
    }


def is_invite(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return normalized in {
        "邀请", "邀请我", "私聊邀请我", "发我邀请",
        "邀请链接", "链接", "网页入口", "加入链接", "分享",
        "share", "invite", "inviteme",
    }


def is_private_character_creation_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").strip().lower())
    if not compact:
        return False
    return any(word in compact for word in {
        "车卡", "建卡", "新建角色", "创建角色", "角色创建",
        "ai车卡", "ai建卡", "角色草稿", "生成角色",
    })


def is_private_log(text: str) -> bool:
    return text.strip().lower() in {"感知", "我的感知", "私密感知", "悄悄话", "私密信息", "私聊记录"}


def is_recap(text: str) -> bool:
    return text.strip().lower() in {"前情", "前情提要", "提要", "剧情回顾", "剧情摘要", "剧情", "回顾", "recap", "summary"}


def is_map(text: str) -> bool:
    return text.strip().lower() in {"地图", "场景地图", "查看地图", "当前地图", "地点", "地点列表", "map"}


def is_away(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return any(word in normalized for word in {"暂离", "离开", "下线", "挂机", "afk", "away"})


def is_return(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return any(word in normalized for word in {"回来", "归队", "上线", "return", "back"})


def away_target_query(text: str) -> str:
    raw = str(text or "").strip()
    raw = re.sub(r"^(让|标记)?\s*", "", raw)
    raw = re.sub(r"^(暂离|离开|下线|挂机|回来|归队|上线)\s*", "", raw)
    raw = re.sub(r"\s*(暂离|离开|下线|挂机|回来|归队|上线)$", "", raw)
    return raw.strip()


def is_advance(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return normalized in {
        "推进", "强制推进", "下一轮", "下回合", "进入下一轮", "进入下回合",
        "推进剧情", "继续推进", "继续", "advance", "next", "nextround", "forceadvance",
    }


def advance_force(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    return normalized not in {"尝试推进", "普通推进", "advance"}


def is_payment_list(text: str) -> bool:
    return text.strip().lower() in {"支付", "付款", "待支付", "待付款", "支付列表", "付款列表"}


def payment_decision(text: str) -> bool | None:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    if normalized in {"确认支付", "同意支付", "支付确认", "确认付款", "同意付款", "付款确认"} or normalized.startswith(("确认支付", "同意支付", "确认付款", "同意付款")):
        return True
    if normalized in {"拒绝支付", "取消支付", "支付拒绝", "拒绝付款", "取消付款", "付款拒绝"} or normalized.startswith(("拒绝支付", "取消支付", "拒绝付款", "取消付款")):
        return False
    return None


def payment_index(text: str) -> int:
    match = re.search(r"(\d+)", text)
    return max(1, int(match.group(1))) if match else 1
