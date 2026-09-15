"""统一货币模型：CurrencySpec 定义 → CurrencyCodec → canonical int。

- ``CurrencySpec`` 是规则货币结构的唯一权威；
- ``parse_currency_amount`` / ``format_currency_amount`` 是唯一转换入口；
- economy 引擎只处理 canonical base-unit 整数，不感知货币名称。
"""

from src.engine.currency.codec import format_currency_amount, parse_currency_amount
from src.engine.currency.models import CurrencySpec, CurrencyUnit
from src.engine.currency.validation import (
    CurrencySystemError,
    declares_currency_schema,
    is_v2_currency_system,
    legacy_currency_spec,
    validate_currency_system,
    validate_declared_currency_system,
)

__all__ = [
    "CurrencySpec",
    "CurrencySystemError",
    "CurrencyUnit",
    "declares_currency_schema",
    "validate_declared_currency_system",
    "format_currency_amount",
    "is_v2_currency_system",
    "legacy_currency_spec",
    "parse_currency_amount",
    "validate_currency_system",
]
