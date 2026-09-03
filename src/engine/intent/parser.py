"""Narrative/intent text parsing primitives, driven by language resources.

所有触发模式来自 lexicon 语言资源（稳定 ID → 模式列表），本模块只负责
把资源编译成正则并封装 parsing 函数。任何函数都不硬编码具体语言文本；
自定义规则货币经 ``extra_labels`` 投影。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.engine.intent.lexicon import (
    currency_label_defaults,
    instance_language,
    intent_regex,
)

_PURCHASE_INTENT_ID = "purchase_intent"
_PURCHASE_CONFIRM_ID = "purchase_confirm"
_PURCHASE_OFFER_ID = "purchase_offer"
_FREE_PURCHASE_ID = "free_purchase"
_DEFERRED_PAYMENT_ID = "deferred_payment"


def purchase_intent_pattern(language: str) -> re.Pattern[str]:
    return intent_regex(language, _PURCHASE_INTENT_ID)


def purchase_confirm_pattern(language: str) -> re.Pattern[str]:
    return intent_regex(language, _PURCHASE_CONFIRM_ID)


def purchase_offer_pattern(language: str) -> re.Pattern[str]:
    return intent_regex(language, _PURCHASE_OFFER_ID)


def free_purchase_pattern(language: str) -> re.Pattern[str]:
    return intent_regex(language, _FREE_PURCHASE_ID)


def deferred_payment_pattern(language: str) -> re.Pattern[str]:
    return intent_regex(language, _DEFERRED_PAYMENT_ID)


def currency_labels(language: str, extra_labels: Iterable[str] | None = None) -> tuple[str, ...]:
    """Default currency labels for the language union plus rule projections."""

    return currency_label_defaults(language, extra_labels)


def currency_labels_for_rule(rule: Any, language: str = "") -> tuple[str, ...]:
    """Project declared rule currency IDs/names into the generic text guard."""

    system = getattr(rule, "currency_system", None)
    if not isinstance(system, dict) and isinstance(rule, dict):
        system = rule.get("currency_system")
    units = system.get("units", []) if isinstance(system, dict) else []
    labels: list[str] = []
    if isinstance(units, list):
        for unit in units:
            if isinstance(unit, dict):
                labels.extend(
                    str(unit.get(key) or "").strip()
                    for key in ("id", "name", "label")
                    if str(unit.get(key) or "").strip()
                )
    return currency_labels(language, labels)


def currency_amount_pattern(
    language: str,
    extra_labels: Iterable[str] | None = None,
) -> re.Pattern[str]:
    labels = "|".join(re.escape(label) for label in currency_labels(language, extra_labels))
    return re.compile(
        rf"(?P<amount>[0-9]+|[零〇一二三四五六七八九十百千万两]+)"
        rf"\s*(?:枚|个)?\s*(?:{labels})",
        re.IGNORECASE,
    )


def charge_pattern(
    language: str,
    extra_labels: Iterable[str] | None = None,
) -> re.Pattern[str]:
    labels = "|".join(re.escape(label) for label in currency_labels(language, extra_labels))
    offer_head = intent_regex(language, _PURCHASE_OFFER_ID).pattern
    return re.compile(
        rf"(?:{offer_head}|"
        rf"支付|付费|买下|花费|缴纳|pay|purchase|spend)[^。！？\n]{{0,24}}?"
        rf"(?:[一二三四五六七八九十百千万两\d]+)\s*(?:枚|个)?\s*(?:{labels})"
        rf"|(?:[一二三四五六七八九十百千万两\d]+)\s*(?:{labels})"
        rf"[^。！？\n]{{0,16}}?"
        rf"(?:支付|付费|买下|花费|缴纳|pay|purchase|spend)",
        re.IGNORECASE,
    )


def completed_payment_pattern(
    language: str,
    extra_labels: Iterable[str] | None = None,
) -> re.Pattern[str]:
    labels = "|".join(re.escape(label) for label in currency_labels(language, extra_labels))
    return re.compile(
        rf"(?:掏出|拿出|数出|数了|递出|放下|交出|付出|支付了?|缴纳了?|付清|花费了?)\s*"
        rf"(?:[一二三四五六七八九十百千万两\d]+)\s*(?:枚|个)?\s*(?:{labels})"
        rf"|(?:paid|spent|handed over|paid out)\s+"
        rf"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:{labels})",
        re.IGNORECASE,
    )


def _chinese_amount(value: str) -> int | None:
    """Parse the small Chinese numerals commonly used in narrative prices."""

    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
        elif char in units:
            unit = units[char]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


def currency_amounts(
    language: str,
    narration: str,
    extra_labels: Iterable[str] | None = None,
) -> list[int]:
    amounts: list[int] = []
    for match in currency_amount_pattern(language, extra_labels).finditer(str(narration or "")):
        amount = _chinese_amount(match.group("amount"))
        if amount is not None and amount > 0:
            amounts.append(amount)
    return amounts


def item_context_from_action(
    language: str,
    text: str,
    extra_labels: Iterable[str] | None = None,
) -> str:
    """Derive a loose item hint from one player action.

    去掉金额、货币与购买动词后剩下的片段作为商品指代；只用于澄清展示与
    宽松绑定，永远不作为价格或身份的权威来源。
    """

    cleaned = currency_amount_pattern(language, extra_labels).sub(" ", str(text or ""))
    cleaned = re.sub(r"[（(][^）)]*[)）]", " ", cleaned)
    cleaned = purchase_intent_pattern(language).sub(" ", cleaned)
    cleaned = re.sub(
        r"(?:掏|拿出|递给?|付|支付|付钱|结账|给钱|花费|spend|pay|buy|purchase|bought|paid)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"^[\s，,。.、：:；;'-]+|[\s，,。.、：:；;'-]+$", "", cleaned)


_INTENT_QUESTION_RE = re.compile(
    r"(?:[买购付][^。！？\n]{0,6}(?:吗|呢))"
    r"|(?:多少钱|多少金币|多少灵石)"
    r"|(?:可以买了吗|买的话)",
    re.IGNORECASE,
)


def parse_purchase_intents(
    action_records: Iterable[Any],
    players: Any,
    language: str = "",
    extra_labels: Iterable[str] | None = None,
) -> list[Any]:
    """Parse each player's own action into one purchase intent.

    每个 actor 独立解析：意图只来自玩家自己的行动文本，AI 输出不参与。
    疑问/询价句（"还有其他买的吗""可以买了吗""多少钱"）不产生意图——
    询问价格不是购买承诺；"我要买这个"等陈述句不受影响。
    """

    from src.engine.intent.models import PurchaseIntent

    intents: list[PurchaseIntent] = []
    intent_pattern = purchase_intent_pattern(language)
    for action in action_records:
        if not isinstance(action, dict):
            continue
        actor_uid = str(action.get("user_id") or "")
        text = str(action.get("text") or "")
        if not actor_uid or actor_uid not in players:
            continue
        verb_sentences = [
            sentence
            for sentence in re.split(r"[。！？.!?]+", text)
            if intent_pattern.search(sentence)
        ]
        if not verb_sentences:
            continue
        # 疑问句过滤：购买动词所在句是询价/疑问时不产生意图。
        verb_sentences = [
            sentence for sentence in verb_sentences
            if not _INTENT_QUESTION_RE.search(sentence)
        ]
        if not verb_sentences:
            continue
        evidence_text = "。".join(verb_sentences)
        amounts = currency_amounts(language, evidence_text, extra_labels)
        intents.append(PurchaseIntent(
            actor_uid=actor_uid,
            action_text=text,
            item_context=item_context_from_action(language, evidence_text, extra_labels),
            amount_candidates=tuple(sorted(set(amounts))),
        ))
    return intents


def instance_language_hint(instance: Any) -> str:
    """Language of an instance for lexicon lookups（供调用方统一取语言）。"""

    return instance_language(instance)
