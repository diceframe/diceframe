"""Project canonical D&D health into the legacy sheet display fields.

Canonical ``ruleset_character.resources.hp`` is the only authority.  The legacy
sheet carries a parallel display copy (top-level ``hp``/``max_hp``,
``resources.hp``, ``status``, ``deceased``, ``death_saves``); the frontend reads
that copy first, so every writeback that moves canonical HP must refresh it or
the health bar and the rest guard disagree with the rules engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from src.engine.character_utils import set_hp


def sync_health_display(sheet: dict[str, Any], canonical: Mapping[str, Any]) -> None:
    resources = canonical.get("resources") or {}
    hp = int(resources.get("hp", 0) or 0)
    max_hp = int(resources.get("max_hp", 0) or 0)
    set_hp(sheet, hp, max_hp)
    conditions = canonical.get("conditions") or {}
    deceased = "dead" in conditions
    sheet["deceased"] = deceased
    if "death_saves" in conditions:
        sheet["death_saves"] = deepcopy(conditions["death_saves"])
    else:
        sheet.pop("death_saves", None)
    sheet["status"] = "dead" if deceased else "downed" if hp == 0 else "active"
    if not deceased:
        sheet.pop("death_round", None)