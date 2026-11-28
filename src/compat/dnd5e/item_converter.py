"""dnd5e Item -> display DTO + dnd2024 catalog reference (workplan §6.16).

Pure conversion only (§3.2 rule 2): this module never derives dnd5e numbers,
never interprets ``system.activities`` or Active Effects and never executes
anything. An item either maps to an existing dnd2024 catalog entity or is
preserved verbatim and reported as saved-but-not-runnable (§3.2 rule 3).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .mapping import (
    ACTOR_SCOPED_ITEM_TYPES,
    Dnd2024Catalog,
    Dnd5eConversionError,
    Dnd5eConversionReport,
    INVENTORY_ITEM_TYPES,
    default_catalog,
    map_feat_identifier,
    map_item_identifier,
    map_spell_identifier,
)

_ITEM_SYSTEM_HANDLED = frozenset({
    "activities", "activation", "armor", "attunement", "bonuses", "charge",
    "damage", "description", "durability", "duration", "equipped", "identifier",
    "identified", "level", "magicalBonus", "mastery", "properties", "price",
    "proficient", "quantity", "range", "rarity", "school", "source",
    "target", "type", "uses", "weight",
})

_ITEM_HANDLED_KEYS = frozenset({
    "_id", "_stats", "effects", "flags", "folder", "img", "name", "ownership",
    "sort", "system", "type",
})


def _malformed(message: str) -> Dnd5eConversionError:
    return Dnd5eConversionError(message, error_code="dnd5e_item_malformed")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _damage_formula(damage: Any) -> str | None:
    """Format the *declared* damage parts; no scaling or modifiers are applied."""

    if not isinstance(damage, Mapping):
        return None
    base = damage.get("base")
    if not isinstance(base, Mapping):
        return None
    custom = base.get("custom")
    if isinstance(custom, Mapping) and custom.get("enabled") and custom.get("formula"):
        return str(custom["formula"])
    number = _int_or_none(base.get("number")) or 0
    denomination = _int_or_none(base.get("denomination")) or 0
    if not number or not denomination:
        return None
    formula = f"{number}d{denomination}"
    bonus = str(base.get("bonus") or "").strip()
    if bonus:
        formula = f"{formula} + {bonus}"
    return formula


def _damage_types(damage: Any) -> list[str]:
    if not isinstance(damage, Mapping):
        return []
    base = damage.get("base")
    if not isinstance(base, Mapping):
        return []
    types = base.get("types")
    if isinstance(types, (list, tuple, set)):
        return [str(item) for item in types]
    return []


def _identifier(raw: Mapping[str, Any]) -> str:
    system = raw.get("system")
    if isinstance(system, Mapping):
        return str(system.get("identifier") or "")
    return ""


def _display(raw: Mapping[str, Any]) -> dict[str, Any]:
    system = raw.get("system") if isinstance(raw.get("system"), Mapping) else {}
    price = system.get("price") if isinstance(system.get("price"), Mapping) else {}
    weight = system.get("weight") if isinstance(system.get("weight"), Mapping) else {}
    armor = system.get("armor") if isinstance(system.get("armor"), Mapping) else {}
    description = system.get("description")
    text = ""
    if isinstance(description, Mapping):
        text = str(description.get("value") or "")
    return {
        "name": str(raw.get("name") or ""),
        "type": str(raw.get("type") or ""),
        "identifier": _identifier(raw),
        "image": str(raw.get("img") or ""),
        "quantity": _int_or_none(system.get("quantity")),
        "weight": (
            {"value": weight.get("value"), "units": weight.get("units")}
            if weight
            else None
        ),
        "price": (
            {"value": price.get("value"), "denomination": price.get("denomination")}
            if price
            else None
        ),
        "damage": {
            "formula": _damage_formula(system.get("damage")),
            "types": _damage_types(system.get("damage")),
            "declared": True,
        },
        "armor": {
            "value": _int_or_none(armor.get("value")),
            "dex": _int_or_none(armor.get("dex")),
        },
        "properties": sorted(
            str(item) for item in (system.get("properties") or [])
            if isinstance(item, str)
        ),
        "description": text,
        "declared": True,
    }


def _map_reference(
    raw: Mapping[str, Any],
    *,
    catalog: Dnd2024Catalog,
    report: Dnd5eConversionReport,
    path: str,
) -> str | None:
    item_type = str(raw.get("type") or "")
    identifier = _identifier(raw)
    if item_type == "feat":
        feat_id = map_feat_identifier(identifier, catalog)
        if feat_id:
            return f"feat:{feat_id}"
        report.keep(path, f"feat {identifier or '<no identifier>'} has no dnd2024 catalog feat")
        report.missing("feat", path, identifier)
        return None
    if item_type == "spell":
        spell_id = map_spell_identifier(identifier, catalog)
        if spell_id:
            return f"spell:{spell_id}"
        report.keep(path, f"spell {identifier or '<no identifier>'} has no dnd2024 SRD spell")
        report.missing("spell", path, identifier)
        return None
    if item_type in INVENTORY_ITEM_TYPES:
        item_id = map_item_identifier(identifier, catalog)
        if item_id:
            return f"item:{item_id}"
        report.keep(
            path,
            f"{item_type} item {identifier or '<no identifier>'} has no dnd2024 catalog item",
        )
        report.missing("item", path, identifier)
        return None
    if item_type in ACTOR_SCOPED_ITEM_TYPES:
        report.keep(
            path,
            f"{item_type} item is actor-scoped; it is read by the actor converter, "
            "not mapped as an inventory item",
            kind="actor_scoped",
        )
        return None
    report.keep(path, f"item type {item_type!r} is not a supported dnd2024 item kind")
    return None


def _convert(
    raw: Any,
    *,
    catalog: Dnd2024Catalog,
    report: Dnd5eConversionReport,
    path: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise _malformed(f"{path} is not an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise _malformed(f"{path} has no name")
    item_type = str(raw.get("type") or "").strip()
    if not item_type:
        raise _malformed(f"{path} has no item type")
    system = raw.get("system")
    if system is not None and not isinstance(system, Mapping):
        raise _malformed(f"{path}.system is not an object")

    for key in raw:
        if key not in _ITEM_HANDLED_KEYS:
            report.unknown(f"{path}.{key}")
    if isinstance(system, Mapping):
        for key in system:
            if key not in _ITEM_SYSTEM_HANDLED:
                report.unknown(f"{path}.system.{key}")

    catalog_ref = _map_reference(raw, catalog=catalog, report=report, path=path)

    if isinstance(system, Mapping) and system.get("activities"):
        report.keep(
            f"{path}.system.activities",
            "Activity data is preserved but never mapped or executed (Activity 不映射)",
            kind="activity",
        )
    if raw.get("effects"):
        report.keep(
            f"{path}.effects",
            "embedded ActiveEffect data is a modelling reference only; not interpreted",
            kind="active_effect",
        )

    return {
        "kind": "dnd5e_item",
        "source_version": _source_version(raw),
        "display": _display(raw),
        "catalog_ref": catalog_ref,
        "extensions": {"raw": deepcopy(dict(raw))},
        "report": report,
    }


def _source_version(raw: Mapping[str, Any]) -> str:
    stats = raw.get("_stats")
    if isinstance(stats, Mapping):
        return str(stats.get("systemVersion") or "dnd5e")
    return "dnd5e"


def convert_item_into(
    raw_dnd5e: Any,
    *,
    catalog: Dnd2024Catalog,
    report: Dnd5eConversionReport,
    path: str,
) -> dict[str, Any]:
    """Convert an embedded item while appending to an existing actor report."""

    return _convert(raw_dnd5e, catalog=catalog, report=report, path=path)


def convert_item(
    raw_dnd5e: Any,
    *,
    locale: str = "",
    catalog: Dnd2024Catalog | None = None,
) -> dict[str, Any]:
    """Convert one raw dnd5e Item document.

    Returns ``display`` (presentation-safe DTO), ``catalog_ref`` (``item:``,
    ``feat:`` or ``spell:`` ref into the shipped dnd2024 catalog, or ``None``),
    the verbatim ``extensions.raw`` preserved copy and the conversion report.
    """

    resolved = catalog if catalog is not None else default_catalog(locale)
    report = Dnd5eConversionReport(source_version="dnd5e")
    converted = _convert(raw_dnd5e, catalog=resolved, report=report, path="item")
    converted["report"] = report.to_dict()
    return converted


__all__ = ["convert_item", "convert_item_into"]