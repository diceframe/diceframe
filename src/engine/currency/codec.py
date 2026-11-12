"""货币金额的唯一转换入口：显示金额 ↔ canonical base-unit 整数。

- 解析一律使用 :class:`decimal.Decimal`，禁止 float；
- 结果必须恰好为整数，否则拒绝（不 round / floor / ceil）；
- economy 引擎之外不得散落 ×100 / ÷100 之类的单位换算。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from src.engine.currency.models import CurrencySpec, CurrencyUnit
from src.engine.currency.validation import CurrencySystemError
from src.localization.messages import t

# 纯十进制字面量：拒绝负号、空白、指数（1e2）、NaN/Infinity 等一切歧义输入。
_DECIMAL_RE = re.compile(r"\d+(?:\.\d+)?")
_MAX_FORMAT_DECIMALS = 8


def _as_decimal_text(value: str | int) -> str:
    if isinstance(value, bool):
        raise CurrencySystemError(t("engine.currency.codec.amount_invalid", amount=repr(value)))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if text and _DECIMAL_RE.fullmatch(text):
            return text
    raise CurrencySystemError(t("engine.currency.codec.amount_invalid", amount=repr(value)))


def parse_currency_amount(
    value: str | int,
    unit_id: str,
    spec: CurrencySpec,
) -> int:
    """把「显示金额 + 单位」转换为 canonical base-unit 整数。

    - ``value``：十进制字符串或整数（``"0.25"`` / ``25``）；
    - ``unit_id``：当前规则 ``CurrencySpec`` 里的 canonical unit id；
    - 结果必须恰好为整数，否则拒绝（0.001 dollar → 0.1 cent 会拒绝）。
    """

    if not isinstance(spec, CurrencySpec):
        raise CurrencySystemError(t("engine.currency.codec.currency_system_missing"))
    unit = spec.unit(str(unit_id or "").strip())
    if unit is None:
        raise CurrencySystemError(t("engine.currency.codec.unknown_unit", unit_id=repr(unit_id)))
    text = _as_decimal_text(value)
    try:
        canonical = Decimal(text) * unit.rate
    except (InvalidOperation, ValueError):
        raise CurrencySystemError(t("engine.currency.codec.amount_invalid", amount=repr(value))) from None
    if canonical != canonical.to_integral_value():
        raise CurrencySystemError(
            t("engine.currency.codec.amount_not_exact", text=text, unit=unit.name, base_unit=spec.base.name),
        )
    canonical_int = int(canonical)
    if canonical_int <= 0:
        raise CurrencySystemError(t("engine.currency.codec.amount_not_positive"))
    return canonical_int


def _decimals_for_rate(rate: int) -> int | None:
    """Minimal decimal places that represent ``1/rate`` exactly, else None."""

    for candidate in range(1, _MAX_FORMAT_DECIMALS + 1):
        if (10**candidate) % rate == 0:
            return candidate
    return None


def _format_with_symbol(unit: CurrencyUnit, text: str) -> str:
    if unit.symbol:
        return f"{unit.symbol}{text}"
    return f"{text} {unit.name}"


def format_currency_amount(amount: int, spec: CurrencySpec) -> str:
    """Format one canonical base-unit integer for display.

    例如：25 cent → ``$0.25``；1250 fen → ``¥12.50``；25 灵石 → ``25 灵石``。
    display.rate 无法用有限十进制表示（如 rate=3）时，不做小数近似，
    始终按单位面额贪心分解，剩余部分以 base_unit 显示（1 → ``1 铜币``）。
    """

    if isinstance(amount, bool) or not isinstance(amount, int):
        raise CurrencySystemError(t("engine.currency.codec.canonical_amount_not_integer", amount=repr(amount)))
    sign = "-" if amount < 0 else ""
    value = abs(int(amount))
    display = spec.display
    if display.rate > 1:
        decimals = _decimals_for_rate(display.rate)
        if decimals is not None:
            quantum = Decimal(10) ** -decimals
            text = str((Decimal(value) / Decimal(display.rate)).quantize(quantum))
            return sign + _format_with_symbol(display, text)
    # display 即 base（或 rate 无法精确十进制表示）：按单位面额贪心分解，
    # 剩余（含 value 小于最小高面额的情形）一律落在 base_unit 上。
    larger = sorted(
        (unit for unit in spec.units if unit.rate > 1 and unit.rate <= value),
        key=lambda unit: unit.rate,
        reverse=True,
    )
    if larger:
        parts: list[str] = []
        remaining = value
        for unit in larger:
            count, remaining = divmod(remaining, unit.rate)
            if count:
                parts.append(f"{count} {unit.name}")
        if remaining or not parts:
            parts.append(f"{remaining} {spec.base.name}")
        return sign + " ".join(parts)
    if display.rate == 1:
        return sign + _format_with_symbol(display, f"{value}")
    # value 不足以兑任何高面额单位：以 base_unit 显示，绝不冒充高面额单位。
    return sign + _format_with_symbol(spec.base, f"{value}")
