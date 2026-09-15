"""currency_system 校验与 legacy 归一化。

V2（schema_version == 2）必须严格校验、fail-fast；旧规则（只有 ``currency``
标签或旧单单位 ``currency_system``）归一化为 rate=1 的 legacy spec，
绝不根据货币名称猜测单位。
"""

from __future__ import annotations

from typing import Any

from src.engine.currency.models import SCHEMA_VERSION, CurrencySpec, CurrencyUnit

_LEGACY_BASE_UNIT_ID = "unit"


class CurrencySystemError(ValueError):
    """currency_system 声明非法；调用方应拒绝保存/加载，不得猜测修复。"""


def _declares_v2(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    version = raw.get("schema_version")
    if version is None or isinstance(version, bool):
        return False
    try:
        return int(version) >= SCHEMA_VERSION
    except (TypeError, ValueError):
        return False


def is_v2_currency_system(raw: Any) -> bool:
    """Whether a raw payload explicitly declares the V2 currency schema."""

    return _declares_v2(raw)


def validate_currency_system(raw: Any) -> CurrencySpec:
    """Validate one explicit V2 ``currency_system`` declaration.

    Raises :class:`CurrencySystemError` on any structural problem; the caller
    must fail closed instead of repairing user/AI/plugin content.
    """

    if not isinstance(raw, dict):
        raise CurrencySystemError("currency_system 必须是对象")
    version = raw.get("schema_version")
    if version is None:
        raise CurrencySystemError("currency_system 缺少 schema_version")
    try:
        version_int = int(version)
    except (TypeError, ValueError):
        raise CurrencySystemError(f"currency_system.schema_version 非法: {version!r}") from None
    if version_int != SCHEMA_VERSION:
        raise CurrencySystemError(
            f"currency_system.schema_version 仅支持 {SCHEMA_VERSION}，收到 {version_int}",
        )

    raw_units = raw.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raise CurrencySystemError("currency_system.units 必须是非空数组")
    units: list[CurrencyUnit] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_units):
        if not isinstance(item, dict):
            raise CurrencySystemError(f"units[{index}] 必须是对象")
        unit_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not unit_id:
            raise CurrencySystemError(f"units[{index}] 缺少 id")
        if not name:
            raise CurrencySystemError(f"units[{index}] 缺少 name")
        if unit_id in seen_ids:
            raise CurrencySystemError(f"units[{index}] unit id 重复: {unit_id}")
        seen_ids.add(unit_id)
        rate_raw = item.get("rate")
        if isinstance(rate_raw, float) or not isinstance(rate_raw, int):
            raise CurrencySystemError(f"unit {unit_id} 的 rate 必须是整数，收到 {rate_raw!r}")
        if rate_raw <= 0:
            raise CurrencySystemError(f"unit {unit_id} 的 rate 必须 > 0，收到 {rate_raw}")
        symbol = str(item.get("symbol") or "").strip()
        units.append(CurrencyUnit(id=unit_id, name=name, rate=rate_raw, symbol=symbol))

    base_unit = str(raw.get("base_unit") or "").strip()
    if not base_unit:
        raise CurrencySystemError("currency_system 缺少 base_unit")
    if base_unit not in seen_ids:
        raise CurrencySystemError(f"base_unit 不存在: {base_unit}")
    base = next(u for u in units if u.id == base_unit)
    if base.rate != 1:
        raise CurrencySystemError(f"base_unit {base_unit} 的 rate 必须为 1，收到 {base.rate}")

    display_unit = str(raw.get("display_unit") or "").strip() or base_unit
    if display_unit not in seen_ids:
        raise CurrencySystemError(f"display_unit 不存在: {display_unit}")

    return CurrencySpec(
        schema_version=SCHEMA_VERSION,
        base_unit=base_unit,
        display_unit=display_unit,
        units=tuple(units),
    )


def _coerce_v1_unit(raw: Any, fallback_label: str) -> CurrencyUnit | None:
    if not isinstance(raw, dict):
        return None
    unit_id = str(raw.get("id") or "").strip()
    if not unit_id:
        return None
    try:
        rate = int(raw.get("rate", 1))
    except (TypeError, ValueError):
        rate = 1
    if rate <= 0:
        rate = 1
    name = str(raw.get("name") or "").strip() or fallback_label or unit_id
    symbol = str(raw.get("symbol") or "").strip()
    return CurrencyUnit(id=unit_id, name=name, rate=rate, symbol=symbol)


def legacy_currency_spec(
    currency_label: str | None,
    legacy_system: dict[str, Any] | None = None,
) -> CurrencySpec:
    """Build the legacy (schema_version=1) spec for a rule.

    旧规则语义：1 canonical amount = 1 个 ``currency`` 标签单位。旧
    ``currency_system`` 若只有单单位/未声明 V2，则沿用其 base/unit 名称，
    不做任何数值语义转换。
    """

    label = str(currency_label or "").strip()
    if isinstance(legacy_system, dict):
        raw_units = legacy_system.get("units")
        units = [
            unit
            for unit in (
                _coerce_v1_unit(item, label) for item in (raw_units or [])
            )
            if unit is not None
        ]
        base_unit = str(legacy_system.get("base_unit") or "").strip()
        display_unit = str(legacy_system.get("display_unit") or "").strip()
        ids = {unit.id for unit in units}
        if units and base_unit in ids:
            if display_unit not in ids:
                display_unit = base_unit
            return CurrencySpec(
                schema_version=1,
                base_unit=base_unit,
                display_unit=display_unit,
                units=tuple(units),
            )
    unit = CurrencyUnit(id=_LEGACY_BASE_UNIT_ID, name=label or _LEGACY_BASE_UNIT_ID, rate=1)
    return CurrencySpec(
        schema_version=1,
        base_unit=_LEGACY_BASE_UNIT_ID,
        display_unit=_LEGACY_BASE_UNIT_ID,
        units=(unit,),
    )
