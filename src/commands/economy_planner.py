"""Ruleset-neutral economy planning and quote validation for the round planner.

This module owns purchase intent normalization, evidence checks, and the
compatibility fallback used when the semantic planner is unavailable.
"""

from __future__ import annotations

import re
from typing import Any

from src.engine.currency import legacy_currency_spec, parse_currency_amount
from src.engine.currency.validation import CurrencySystemError
from src.engine.economy import MAX_ECONOMY_AMOUNT
from src.engine.game_instance import GameInstance
from src.engine.intent.locale_messages import locale_message
from src.engine.intent.parser import (
    parse_purchase_intents,
    purchase_balance_pattern,
    purchase_generic_target_terms,
    purchase_intent_pattern,
    purchase_offer_pattern,
    purchase_quantity_classifier_pattern,
    purchase_quantity_words,
    purchase_target_connector_pattern,
    purchase_target_prefix_pattern,
    purchase_target_suffix_pattern,
)
from src.engine.language import localized_text
from src.llm.parser import sanitize_narration
from src.rules.rule_system import RuleSystem


def _match_player(instance: GameInstance, value: object) -> str:
    query = str(value or "").strip().casefold()
    if not query:
        return ""
    for uid, player in instance.players.items():
        if query in {uid.casefold(), str(player.get("character_name") or "").strip().casefold()}:
            return uid
    return ""


def append_unpriced_purchase_intent(
    unpriced: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
    *,
    uid: str,
    target: str,
    quantity: int,
) -> None:
    """Record one deduplicated unpriced purchase intent (round memory only)."""

    key = (uid, target.casefold(), quantity)
    if key in seen:
        return
    seen.add(key)
    unpriced.append({
        "payer_uid": uid,
        "target": target,
        "quantity": quantity,
    })


def _infer_explicit_currency_unit(
    instance: GameInstance,
    uid: str,
    rule: RuleSystem | None,
    *,
    amount: Any,
    price_source: str,
) -> str:
    """Recover a missing unit only from an exact amount+unit phrase in trusted text.

    The planner may omit ``unit`` even when the player/GM clearly wrote
    ``34金币``.  Recovering that label server-side preserves valid purchases
    without defaulting an unknown ``银币`` quote to the display unit.  If the
    amount/unit pair is not explicit or is ambiguous, callers must fail closed.
    """

    currency_spec = rule.currency_spec if rule else legacy_currency_spec("")
    source_texts: list[str] = []
    if price_source == "player_stated":
        source_texts = [
            str(action.get("text") or "")
            for action in instance.action_queue
            if str(action.get("user_id") or "") == str(uid)
        ]
    elif price_source == "gm_narrated":
        source_texts = [
            sanitize_narration(str(entry.get("gm_response") or ""))
            for entry in instance.log[-2:]
            if entry.get("gm_response")
        ]
    text = "\n".join(source_texts)
    raw_amount = str(amount or "").strip()
    if not text or not raw_amount:
        return ""
    matches: set[str] = set()
    for unit in currency_spec.units:
        labels = [str(unit.id or "").strip(), str(unit.name or "").strip()]
        if unit.symbol:
            labels.append(str(unit.symbol).strip())
        for label in labels:
            if not label:
                continue
            pattern = rf"{re.escape(raw_amount)}\s*(?:枚|个)?\s*{re.escape(label)}"
            if re.search(pattern, text, flags=re.IGNORECASE):
                matches.add(unit.id)
                break
    return next(iter(matches)) if len(matches) == 1 else ""


def _quote_evidence_is_trusted(
    instance: GameInstance,
    uid: str,
    price_source: str,
    evidence: Any,
) -> bool:
    """Verify model-provided quote evidence against server-owned text."""

    excerpt = re.sub(r"\s+", " ", str(evidence or "")).strip()
    if not excerpt or len(excerpt) > 300:
        return False
    if price_source == "player_stated":
        sources = [
            str(action.get("text") or "")
            for action in instance.action_queue
            if str(action.get("user_id") or "") == str(uid)
        ]
    elif price_source == "gm_narrated":
        sources = [
            sanitize_narration(str(entry.get("gm_response") or ""))
            for entry in instance.log[-2:]
            if entry.get("gm_response")
        ]
    else:
        return False
    return any(excerpt in re.sub(r"\s+", " ", source) for source in sources)


def normalize_economy_actions(
    instance: GameInstance,
    raw_actions: list[Any],
    rule: RuleSystem | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """校验模型经济报价并归一到付款人。

    新货币协议：``amount`` 是十进制字符串、``unit`` 是规则货币的 canonical
    unit id；服务端经 CurrencyCodec 转成 canonical base-unit 整数，模型绝不
    自行换算。price_source=none 或缺价时安全跳过（没有人说出价格就不产生
    扣款提案），有效的意图以 ``unpriced`` 形式返回，供本轮 LOOT 拦截使用；
    它们只存在于回合内存中，从不入库，也从不产生金额。amount 存在则
    price_source 必须是明确的转述来源。单条无效不影响同批。

    意图与报价分层：player/target/quantity 校验通过后购买意图即成立；
    之后任何「报价不可结算」（unit 无法映射、金额无法 canonicalize、
    amount_scope/price_source 非法、超出上限）都只降级为 unpriced intent
    并记录 error——绝不因为价格解析失败让购买意图消失，否则 LOOT gate
    会放行免费发放。fail closed：可以不收费，但不能免费交货。
    """
    currency_spec = rule.currency_spec if rule else legacy_currency_spec("")
    offers: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_unpriced: set[tuple[str, str, int]] = set()

    def downgrade_to_unpriced(index: int, reason: str) -> None:
        errors.append(f"economy_actions[{index}] {reason}；已降级为 unpriced purchase intent")
        append_unpriced_purchase_intent(
            unpriced, seen_unpriced,
            uid=uid, target=target, quantity=quantity,
        )

    for index, raw in enumerate(raw_actions[:8]):
        if not isinstance(raw, dict):
            errors.append(f"economy_actions[{index}] 不是 object")
            continue
        if str(raw.get("type") or "") != "purchase":
            errors.append(f"economy_actions[{index}] type 仅支持 purchase")
            continue
        uid = _match_player(instance, raw.get("player"))
        if not uid:
            errors.append(f"economy_actions[{index}] player 不存在")
            continue
        target = str(raw.get("target") or "").strip()[:120]
        if not target:
            errors.append(f"economy_actions[{index}] target 为空")
            continue
        quantity = 1
        if raw.get("quantity") is not None:
            try:
                quantity = int(raw.get("quantity") or 1)
            except (TypeError, ValueError):
                errors.append(f"economy_actions[{index}] quantity 无效")
                continue
        if not 1 <= quantity <= 8:
            errors.append(f"economy_actions[{index}] quantity 超出范围")
            continue
        # —— 以上通过后购买意图已可靠成立；以下失败只降级，不丢弃 ——

        price_source = str(raw.get("price_source") or "").strip()
        if price_source != "none" and raw.get("amount") is None:
            # 有目标但没有金额：与 price_source=none 同样处理为无价意图。
            price_source = "none"
        if price_source == "none":
            append_unpriced_purchase_intent(
                unpriced, seen_unpriced,
                uid=uid, target=target, quantity=quantity,
            )
            continue
        amount_scope = str(raw.get("amount_scope") or "total").strip()
        if amount_scope not in {"unit", "total"}:
            downgrade_to_unpriced(index, f"amount_scope={amount_scope!r} 无效")
            continue
        if price_source not in {"player_stated", "gm_narrated"}:
            downgrade_to_unpriced(index, f"price_source={price_source!r} 无效")
            continue
        evidence = str(raw.get("evidence") or "").strip()[:300]
        if evidence and not _quote_evidence_is_trusted(instance, uid, price_source, evidence):
            downgrade_to_unpriced(index, "evidence 与可信文本不匹配")
            continue
        unit_input = str(raw.get("unit") or "").strip()
        if not unit_input:
            unit_input = _infer_explicit_currency_unit(
                instance,
                uid,
                rule,
                amount=raw.get("amount"),
                price_source=price_source,
            )
        # 金额报价必须携带 canonical unit。过去在 unit 缺失时回退到
        # display unit，导致“34 银币”被模型省略 unit 后按“34 金币”扣款。
        # 缺失或未知单位一律降级为无价意图，不能猜测换算。
        if not unit_input:
            downgrade_to_unpriced(index, "unit 缺失")
            continue
        unit = currency_spec.unit(unit_input)
        if unit is None:
            downgrade_to_unpriced(
                index, f"unit={unit_input!r} 不在当前规则货币单位中",
            )
            continue
        try:
            amount = parse_currency_amount(raw.get("amount"), unit.id, currency_spec)
        except CurrencySystemError as exc:
            downgrade_to_unpriced(index, str(exc))
            continue
        if amount > MAX_ECONOMY_AMOUNT:
            downgrade_to_unpriced(index, "amount 超出范围")
            continue
        total_amount = amount * quantity if amount_scope == "unit" else amount
        if not 0 < total_amount <= MAX_ECONOMY_AMOUNT:
            downgrade_to_unpriced(index, "total amount 超出范围")
            continue
        initial_due = None
        if raw.get("deposit_amount") is not None:
            try:
                initial_due = parse_currency_amount(raw.get("deposit_amount"), unit.id, currency_spec)
            except CurrencySystemError as exc:
                downgrade_to_unpriced(index, f"deposit_amount {exc}")
                continue
            if not 0 < initial_due < total_amount:
                downgrade_to_unpriced(index, "deposit_amount 必须小于 total amount")
                continue
        max_total_raw = raw.get("max_total")
        max_total = None
        if max_total_raw is not None:
            try:
                max_total = parse_currency_amount(max_total_raw, unit.id, currency_spec)
            except CurrencySystemError as exc:
                downgrade_to_unpriced(index, f"max_total {exc}")
                continue
            if not 0 <= max_total <= MAX_ECONOMY_AMOUNT:
                downgrade_to_unpriced(index, "max_total 超出范围")
                continue
            if total_amount > max_total:
                downgrade_to_unpriced(index, "报价超过玩家预算上限")
                continue
        offer = {
            "payer_uid": uid,
            # Downstream proposal settlement always receives the total charge.
            "amount": total_amount,
            "quantity": quantity,
            "amount_scope": amount_scope,
            "target": target,
            "note": str(raw.get("note") or "").strip()[:160],
        }
        if evidence:
            offer["evidence"] = evidence
        if max_total is not None:
            offer["max_total"] = max_total
        if initial_due is not None:
            offer["initial_due"] = initial_due
            offer["remaining_due"] = total_amount - initial_due
        offers.append(offer)
    # A repair pass may add a valid quote after the first pass recorded the
    # same intent as unpriced. The chargeable offer is the stronger authority;
    # do not keep a stale unpriced blocker for that same payer/item/quantity.
    priced_keys = {
        (str(offer.get("payer_uid") or ""), str(offer.get("target") or "").casefold(), int(offer.get("quantity", 1) or 1))
        for offer in offers
    }
    unpriced = [
        intent for intent in unpriced
        if (
            str(intent.get("payer_uid") or ""),
            str(intent.get("target") or "").casefold(),
            int(intent.get("quantity", 1) or 1),
        ) not in priced_keys
    ]
    return offers, unpriced, errors


def _clean_deterministic_purchase_target(context: str, language: str = "") -> tuple[str, int]:
    """Clean the parser's item hint without inventing an item identity."""

    target = re.sub(r"\s+", " ", str(context or "")).strip()
    if not target:
        return "", 1
    target = purchase_target_prefix_pattern(language or "zh-CN").sub("", target, count=1)
    target = re.split(
        purchase_offer_pattern(language or "zh-CN").pattern,
        target,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    # A compact player action may put the explicit quote directly after the
    # item (“买护甲，34金币”) without a price marker. Strip only a trailing
    # numeric+label clause; the amount itself is parsed separately from the
    # original action and never inferred from this hint.
    target = re.sub(
        r"[，,]\s*\d+(?:\.\d+)?\s*(?:枚|个)?\s*[\w\u3400-\u9fff]+$",
        "",
        target,
        flags=re.IGNORECASE,
    )
    # The generic intent parser removes the currency token before it removes
    # the purchase verb.  Strip balance/connector residue left by phrases such
    # as “我有34金币再买护甲”, without treating that balance as a quote.
    target = purchase_balance_pattern(language or "zh-CN").sub("", target, count=1)
    target = purchase_target_connector_pattern(language or "zh-CN").sub("", target, count=1)
    target = re.sub(r"^[\s，,。.、：:；;\-]+|[\s，,。.、：:；;\-]+$", "", target)
    target = purchase_target_suffix_pattern(language or "zh-CN").sub("", target)
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[\w\u3400-\u9fff]+", target):
        return "", 1
    quantity = 1
    classifier = purchase_quantity_classifier_pattern(language or "zh-CN")
    numeric = re.match(rf"^(?P<qty>[0-9]+)\s*{classifier.pattern}", target, flags=re.IGNORECASE)
    if numeric:
        quantity = int(numeric.group("qty"))
        target = target[numeric.end():]
    else:
        quantity_words = purchase_quantity_words(language or "zh-CN")
        word_pattern = "|".join(
            re.escape(word) for word in sorted(quantity_words, key=len, reverse=True)
        )
        chinese = re.match(rf"^(?P<qty>{word_pattern})\s*{classifier.pattern}", target, flags=re.IGNORECASE) if word_pattern else None
        if chinese:
            quantity = quantity_words[chinese.group("qty")]
            target = target[chinese.end():]
    target = re.sub(r"\s+", " ", target).strip()
    generic_targets = {
        str(value).casefold()
        for value in purchase_generic_target_terms(language or "zh-CN")
    }
    if not target or target.casefold() in generic_targets:
        return "", quantity
    if not 1 <= quantity <= 8:
        return "", quantity
    return target[:120], quantity


def _purchase_target_from_action(action_text: str, language: str) -> tuple[str, int]:
    """Extract the text immediately following the player's purchase verb."""

    text = str(action_text or "")
    pattern = purchase_intent_pattern(language or "zh-CN")
    tails: list[str] = []
    for sentence in re.split(r"[。！？.!?\n]+", text):
        matches = list(pattern.finditer(sentence))
        if matches:
            tails.append(sentence[matches[-1].end():])
    if not tails:
        return "", 1
    return _clean_deterministic_purchase_target(tails[-1], language)


def _explicit_player_purchase_quote(
    intent: Any,
    rule: RuleSystem | None,
    language: str = "",
) -> tuple[str, str] | None:
    """Return one explicit ``(amount, unit_id)`` from the player's action.

    This is deliberately narrower than the model planner.  It only recovers
    a quote when the same purchase sentence contains exactly one numeric
    amount and declared currency label.  Balance/budget statements are not
    treated as prices, and multiple amounts remain ambiguous.
    """

    if rule is None or not getattr(rule, "currency_spec", None):
        return None
    text = str(getattr(intent, "action_text", "") or "")
    spec = rule.currency_spec
    labels: list[tuple[str, str]] = []
    for unit in spec.units:
        for label in (unit.id, unit.name, unit.symbol):
            if str(label or "").strip():
                labels.append((str(label).strip(), unit.id))
    if not labels:
        return None
    label_pattern = "|".join(
        re.escape(label)
        for label, _unit in sorted(labels, key=lambda item: len(item[0]), reverse=True)
    )
    amount_pattern = re.compile(
        rf"(?P<amount>\d+(?:\.\d+)?)\s*(?:枚|个)?\s*(?P<label>{label_pattern})",
        re.IGNORECASE,
    )
    purchase_pattern = purchase_intent_pattern(language or "zh-CN")
    candidates: list[tuple[str, str]] = []
    for sentence in re.split(r"[。！？.!?\n]+", text):
        if not purchase_pattern.search(sentence):
            continue
        matches = list(amount_pattern.finditer(sentence))
        for match in matches:
            prefix = sentence[:match.start()]
            # “我有34金币再买护甲” describes a balance, not a quote.
            if purchase_balance_pattern(language or "zh-CN").search(prefix):
                continue
            raw_label = match.group("label")
            unit_id = next(
                (unit for label, unit in labels if label.casefold() == raw_label.casefold()),
                "",
            )
            if not unit_id:
                continue
            try:
                parse_currency_amount(match.group("amount"), unit_id, spec)
            except CurrencySystemError:
                continue
            candidates.append((match.group("amount"), unit_id))
    if len(set(candidates)) != 1:
        return None
    return candidates[0]


def _explicit_gm_purchase_quote(
    instance: GameInstance,
    target: str,
    rule: RuleSystem | None,
    language: str = "",
) -> tuple[str, str] | None:
    """Recover one price explicitly narrated alongside the requested item.

    This is intentionally stricter than general narration parsing: the target
    and exactly one currency amount must occur in the same sentence, and the
    sentence must contain a price cue. Balance/status sentences are excluded.
    """

    if rule is None or not getattr(rule, "currency_spec", None):
        return None
    spec = rule.currency_spec
    labels: list[tuple[str, str]] = []
    for unit in spec.units:
        for label in (unit.id, unit.name, unit.symbol):
            if str(label or "").strip():
                labels.append((str(label).strip(), unit.id))
    if not labels or not target:
        return None
    label_pattern = "|".join(
        re.escape(label)
        for label, _unit in sorted(labels, key=lambda item: len(item[0]), reverse=True)
    )
    amount_pattern = re.compile(
        rf"(?P<amount>\d+(?:\.\d+)?)\s*(?:枚|个)?\s*(?P<label>{label_pattern})",
        re.IGNORECASE,
    )
    price_cue = re.compile(
        rf"(?:{purchase_offer_pattern(language or 'zh-CN').pattern}|一共|每(?:个|件|瓶|把|份)?|"
        r"要价|报价|fee|for|each|sell)",
        re.IGNORECASE,
    )
    target_key = str(target).casefold()
    candidates: list[tuple[str, str]] = []
    for entry in list(getattr(instance, "log", None) or [])[-2:]:
        narration = sanitize_narration(str(entry.get("gm_response") or ""))
        for sentence in re.split(r"[。！？.!?\n]+", narration):
            if target_key not in sentence.casefold() or not price_cue.search(sentence):
                continue
            matches = list(amount_pattern.finditer(sentence))
            if len(matches) != 1:
                continue
            match = matches[0]
            prefix = sentence[:match.start()]
            if purchase_balance_pattern(language or "zh-CN").search(prefix):
                continue
            raw_label = match.group("label")
            unit_id = next(
                (unit for label, unit in labels if label.casefold() == raw_label.casefold()),
                "",
            )
            if not unit_id:
                continue
            try:
                parse_currency_amount(match.group("amount"), unit_id, spec)
            except CurrencySystemError:
                continue
            candidates.append((match.group("amount"), unit_id))
    return candidates[0] if len(set(candidates)) == 1 and candidates else None


def _recover_purchase_intents_from_player_actions(
    instance: GameInstance,
    rule: RuleSystem | None,
    offers: list[dict[str, Any]],
    unpriced: list[dict[str, Any]],
) -> None:
    """Backstop a missing model economy field with deterministic player intent.

    This never invents a target or amount.  A clear item/action without an
    explicit price becomes an unpriced blocker; an explicit amount+unit is
    normalized through the same validator as model output.
    """

    extra_labels = [
        label
        for unit in (getattr(getattr(rule, "currency_spec", None), "units", ()) or ())
        for label in (getattr(unit, "id", ""), getattr(unit, "name", ""), getattr(unit, "symbol", ""))
        if str(label or "").strip()
    ]
    intents = parse_purchase_intents(
        instance.action_queue,
        instance.players,
        str(getattr(instance, "language", "") or ""),
        extra_labels,
    )
    known = {
        (str(item.get("payer_uid") or ""), str(item.get("target") or "").casefold(), int(item.get("quantity", 1) or 1))
        for item in [*offers, *unpriced]
        if isinstance(item, dict)
    }
    priced_actors = {
        str(item.get("payer_uid") or "")
        for item in offers
        if isinstance(item, dict) and str(item.get("payer_uid") or "")
    }
    for intent in intents:
        # The parser's item hint can carry an explicit quantity prefix; do not
        # infer anything beyond that hint.
        target, quantity = _purchase_target_from_action(
            intent.action_text,
            str(getattr(instance, "language", "") or ""),
        )
        if not target:
            target, quantity = _clean_deterministic_purchase_target(
                intent.item_context,
                str(getattr(instance, "language", "") or ""),
            )
        if not target:
            continue
        # Model output for this actor already carries an economy decision. Do
        # not append a second heuristic target from the same action; this is
        # especially important when the model's target is more precise than
        # the parser's loose item hint.
        if str(intent.actor_uid) in priced_actors:
            continue
        key = (str(intent.actor_uid), target.casefold(), quantity)
        existing_unpriced = any(
            (
                str(item.get("payer_uid") or ""),
                str(item.get("target") or "").casefold(),
                int(item.get("quantity", 1) or 1),
            ) == key
            for item in unpriced
            if isinstance(item, dict)
        )
        if key in known and not existing_unpriced:
            continue
        quote = _explicit_player_purchase_quote(
            intent, rule, str(getattr(instance, "language", "") or ""),
        )
        quote_source = "player_stated"
        if quote is None:
            quote = _explicit_gm_purchase_quote(
                instance,
                target,
                rule,
                str(getattr(instance, "language", "") or ""),
            )
            quote_source = "gm_narrated"
        if quote is None:
            # A model/repair pass may already have preserved this actor's
            # unpriced intent. Keep that target rather than append a noisier
            # parser hint; only a newly recovered explicit quote supersedes it.
            if any(
                str(item.get("payer_uid") or "") == str(intent.actor_uid)
                for item in unpriced
                if isinstance(item, dict)
            ):
                continue
            unpriced.append({
                "payer_uid": str(intent.actor_uid),
                "target": target,
                "quantity": quantity,
            })
            known.add(key)
            continue
        amount, unit_id = quote
        recovered_offers, recovered_unpriced, _errors = normalize_economy_actions(
            instance,
            [{
                "type": "purchase",
                "player": str(intent.actor_uid),
                "target": target,
                "quantity": quantity,
                "amount": amount,
                "unit": unit_id,
                "amount_scope": "total",
                "price_source": quote_source,
                "note": (
                    "player action quote recovered server-side"
                    if quote_source == "player_stated"
                    else "GM narration quote recovered server-side"
                ),
            }],
            rule,
        )
        if recovered_offers:
            offers.extend(recovered_offers)
            known.add(key)
            priced_actors.add(str(intent.actor_uid))
        elif recovered_unpriced:
            unpriced.extend(recovered_unpriced)
            known.add(key)
    priced_keys = {
        (
            str(item.get("payer_uid") or ""),
            str(item.get("target") or "").casefold(),
            int(item.get("quantity", 1) or 1),
        )
        for item in offers
        if isinstance(item, dict)
    }
    unpriced[:] = [
        item for item in unpriced
        if (
            str(item.get("payer_uid") or ""),
            str(item.get("target") or "").casefold(),
            int(item.get("quantity", 1) or 1),
        ) not in priced_keys
    ]
