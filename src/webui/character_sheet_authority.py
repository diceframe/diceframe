"""Who may change which field of a classic (non rules-aware) character sheet.

The browser is never the authority over mechanics (ARCHITECTURE §server
authority): HP, balance, resources, progression, inventory and the numbers
checks are rolled against are owned by the engine (state changes, progression
resolver, economy) and by the GM.  A seated player, a bot acting for a seat
and a P2P guest relayed by the host may only edit how the character is
presented.  The one mechanical change a player makes on a classic sheet is
spending level-up points the engine granted, which is validated here.

Rules-aware rulesets (``character_lifecycle == "rules_aware"``) never reach
this policy: their generic PUT is refused and they use their own profile /
advancement / rest endpoints.  What both share is the seat-reset rule: once
a seat has acted (``seat_activity``), applying a library card to it
(``ADOPT_REQUIRES_GM``) or deleting it (``DELETE_REQUIRES_GM``) is GM-only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

FIELD_REQUIRES_GM = "FIELD_REQUIRES_GM"
# Adopting a library card replaces the whole sheet (stats, gold, equipment).
# Once the seat has acted that is a free refill / re-roll, so only the GM may
# do it -- for classic and rules-aware rulesets alike.  Deleting the seat and
# joining again would be the same reset, so it is GM-only at the same point.
ADOPT_REQUIRES_GM = "ADOPT_REQUIRES_GM"
DELETE_REQUIRES_GM = "DELETE_REQUIRES_GM"

# Presentation only: no engine path reads these as numbers or rule identity.
# GAL P8 的立绘 / 表情差分 / 音色绑定同为呈现字段（引用经资产边界校验，
# 不参与任何机制读取）。
PLAYER_PROFILE_FIELDS = frozenset({
    "character_name", "race", "background", "identity", "portrait",
    "standing_portrait", "standing_portrait_expressions", "voice_profile",
})

# Engine / GM owned.  ``class`` is included because classic rules key hit
# dice, skill pools and starter kits on it.
GM_SHEET_FIELDS = frozenset({
    "class", "attributes", "skills", "equipment", "inventory", "key_items",
    "hp", "max_hp", "resources", "gold", "currency", "level", "xp", "progression",
})

EDITABLE_SHEET_FIELDS = PLAYER_PROFILE_FIELDS | GM_SHEET_FIELDS


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def level_up_allocation(
    sheet: Mapping[str, Any],
    new_attributes: Any,
    rule_attrs: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """The merged attributes if ``new_attributes`` only spends level-up points.

    Every attribute may only go up, by integers, within the rule maximum, and
    the total increase may not exceed ``level_up_points``.  Attributes the
    request leaves out keep their value.  ``None`` means it is not a valid
    allocation (and therefore a GM-only change).
    """
    if not isinstance(new_attributes, dict):
        return None
    old = sheet.get("attributes")
    if not isinstance(old, dict):
        return None
    pool = _as_int(sheet.get("level_up_points")) or 0
    maxima = {
        str(attr.get("key")): _as_int(attr.get("max"))
        for attr in rule_attrs
        if isinstance(attr, Mapping) and attr.get("key")
    }
    merged: dict[str, Any] = dict(old)
    spent = 0
    for key, raw in new_attributes.items():
        if key not in old:
            return None
        new_value, old_value = _as_int(raw), _as_int(old.get(key))
        if new_value is None or old_value is None or new_value < old_value:
            return None
        maximum = maxima.get(str(key))
        if new_value > old_value and maximum is not None and new_value > maximum:
            return None
        spent += new_value - old_value
        merged[key] = new_value if new_value != old_value else old.get(key)
    if spent > max(0, pool):
        return None
    return merged


def player_field_violations(
    sheet: Mapping[str, Any],
    updates: dict[str, Any],
    *,
    rule_attrs: Iterable[Mapping[str, Any]],
) -> list[str]:
    """GM-only fields a player-side request tries to change.

    A field resent with its current value is not a change.  A valid level-up
    allocation rewrites ``updates["attributes"]`` to the merged attribute map
    so attributes the request omitted are kept (and drops it when nothing
    was spent).
    """
    denied: list[str] = []
    for key, value in list(updates.items()):
        if key in PLAYER_PROFILE_FIELDS:
            continue
        if key == "attributes":
            merged = level_up_allocation(sheet, value, rule_attrs)
            if merged is not None:
                if merged == sheet.get("attributes"):
                    # Nothing spent: do not let a resend trigger the
                    # attribute-driven HP recalculation.
                    del updates[key]
                else:
                    updates[key] = merged
                continue
        if key in sheet and sheet.get(key) == value:
            continue
        denied.append(key)
    return sorted(denied)


def field_requires_gm_failure(fields: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": FIELD_REQUIRES_GM,
        "fields": list(fields),
        "error": "这些角色卡字段只能由 GM 修改：" + "、".join(fields),
    }


def adopt_requires_gm_failure() -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": ADOPT_REQUIRES_GM,
        "error": "角色行动后只能由 GM 为其套用卡片",
    }


def delete_requires_gm_failure() -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": DELETE_REQUIRES_GM,
        "error": "角色行动后只能由 GM 删除",
    }
