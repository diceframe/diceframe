"""currency_system 校验与 legacy 归一化。

V2（schema_version == 2）必须严格校验、fail-fast；旧规则（只有 ``currency``
标签或旧单单位 ``currency_system``）归一化为 rate=1 的 legacy spec，
绝不根据货币名称猜测单位。
"""

from __future__ import annotations

from typing import Any

from src.engine.currency.models import SCHEMA_VERSION, CurrencySpec, CurrencyUnit
from src.localization.messages import t

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


def validate_declared_currency_system(raw: Any) -> None:
    """Fail fast on any explicitly declared but unsupported/invalid version.

    原则：无版本键 → legacy（直接放行）；显式声明 → 必须合法且受支持。
    ``schema_version: 1`` 是受支持的 legacy 声明（结构校验保持宽松），
    ``2`` 走严格 V2 校验，其余（0 / 3 / "abc" / bool / float）一律拒绝，
    绝不静默当 legacy。
    """

    if not declares_currency_schema(raw):
        return
    assert isinstance(raw, dict)
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise CurrencySystemError(t("engine.currency.validation.schema_version_invalid", version=repr(version)))
    if version == SCHEMA_VERSION:
        validate_currency_system(raw)
        return
    if version != 1:
        raise CurrencySystemError(
            t("engine.currency.validation.declared_schema_version_unsupported", schema_version=SCHEMA_VERSION, version=version),
        )


def declares_currency_schema(raw: Any) -> bool:
    """Whether ``raw`` carries a ``schema_version`` key (any value, any version).

    完全没有该 key 的旧规则继续走 legacy；只要 key 存在（哪怕值为 null /
    非法 / 未支持版本），调用方必须走 ``validate_declared_currency_system``
    fail-fast，不得静默当 legacy。
    """

    return isinstance(raw, dict) and "schema_version" in raw


def validate_currency_system(raw: Any) -> CurrencySpec:
    """Validate one explicit V2 ``currency_system`` declaration.

    Raises :class:`CurrencySystemError` on any structural problem; the caller
    must fail closed instead of repairing user/AI/plugin content.
    """

    if not isinstance(raw, dict):
        raise CurrencySystemError(t("engine.currency.validation.not_object"))
    version = raw.get("schema_version")
    if version is None:
        raise CurrencySystemError(t("engine.currency.validation.schema_version_required"))
    # 显式声明必须受支持且为整型：bool/float/字符串等一切歧义类型 fail-fast，
    # 不做 int() 截断或解析猜测。
    if isinstance(version, bool) or not isinstance(version, int):
        raise CurrencySystemError(t("engine.currency.validation.schema_version_invalid", version=repr(version)))
    version_int = version
    if version_int != SCHEMA_VERSION:
        raise CurrencySystemError(
            t("engine.currency.validation.schema_version_unsupported", schema_version=SCHEMA_VERSION, version_int=version_int),
        )

    raw_units = raw.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raise CurrencySystemError(t("engine.currency.validation.units_empty"))
    units: list[CurrencyUnit] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_units):
        if not isinstance(item, dict):
            raise CurrencySystemError(t("engine.currency.validation.unit_not_object", index=index))
        unit_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not unit_id:
            raise CurrencySystemError(t("engine.currency.validation.unit_id_required", index=index))
        if not name:
            raise CurrencySystemError(t("engine.currency.validation.unit_name_required", index=index))
        if unit_id in seen_ids:
            raise CurrencySystemError(t("engine.currency.validation.unit_id_duplicate", index=index, unit_id=unit_id))
        seen_ids.add(unit_id)
        rate_raw = item.get("rate")
        if isinstance(rate_raw, float) or not isinstance(rate_raw, int):
            raise CurrencySystemError(t("engine.currency.validation.unit_rate_not_integer", unit_id=unit_id, rate_raw=repr(rate_raw)))
        if rate_raw <= 0:
            raise CurrencySystemError(t("engine.currency.validation.unit_rate_not_positive", unit_id=unit_id, rate_raw=rate_raw))
        symbol = str(item.get("symbol") or "").strip()
        units.append(CurrencyUnit(id=unit_id, name=name, rate=rate_raw, symbol=symbol))

    base_unit = str(raw.get("base_unit") or "").strip()
    if not base_unit:
        raise CurrencySystemError(t("engine.currency.validation.base_unit_required"))
    if base_unit not in seen_ids:
        raise CurrencySystemError(t("engine.currency.validation.base_unit_missing", base_unit=base_unit))
    base = next(u for u in units if u.id == base_unit)
    if base.rate != 1:
        raise CurrencySystemError(t("engine.currency.validation.base_unit_rate_not_one", base_unit=base_unit, rate=base.rate))

    display_unit = str(raw.get("display_unit") or "").strip() or base_unit
    if display_unit not in seen_ids:
        raise CurrencySystemError(t("engine.currency.validation.display_unit_missing", display_unit=display_unit))

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
