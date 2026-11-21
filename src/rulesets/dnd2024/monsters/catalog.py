"""Read-only access to the SRD stat-block catalog (S0-MM2).

The catalog is *content*: Wild Shape's known forms, Polymorph's target forms and
any future summon all read the same entries instead of hand-writing stat blocks
into tests.  Loading validates every entry through the combat contract, so a bad
entry fails at load time rather than mid-combat.

Deliberately narrow: lookup and filtering only.  Nothing here decides what a form
is allowed to become — that rule belongs to the feature that consumes it.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..combat.validation import validate_enemy_profiles

CR_ORDER = ("0", "1/8", "1/4", "1/2") + tuple(str(value) for value in range(1, 31))


class MonsterCatalogError(ValueError):
    """Raised when catalog content cannot be trusted."""


def cr_value(label: str) -> float:
    text = str(label or "0")
    if "/" in text:
        numerator, denominator = text.split("/")[:2]
        return float(numerator) / float(denominator)
    return float(text)


class MonsterCatalog:
    """Validated stat blocks, indexed by id."""

    def __init__(self, payload: dict[str, Any]) -> None:
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries:
            raise MonsterCatalogError("monster catalog has no entries")
        indexed: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise MonsterCatalogError("monster entry must be an object")
            entry_id = str(entry.get("id") or "")
            if not entry_id:
                raise MonsterCatalogError("monster entry has no id")
            if entry_id in indexed:
                raise MonsterCatalogError(f"duplicate monster id: {entry_id}")
            indexed[entry_id] = dict(entry)
        # 与 combat.start 的敌人同一个校验器：目录不能有第二份 schema。
        # 逐条传（而不是整表）：该函数顶部还有「一场战斗 1–50 个敌人」的
        # 战斗规模上限，那是战斗边界，不是内容边界。
        for entry in indexed.values():
            validate_enemy_profiles([entry])
        for entry in indexed.values():
            if not str(entry.get("source_ref") or ""):
                raise MonsterCatalogError(
                    f"monster {entry['id']} has no source_ref"
                )
        self._entries = indexed

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, monster_id: str) -> dict[str, Any] | None:
        entry = self._entries.get(str(monster_id))
        return dict(entry) if entry is not None else None

    def all(self) -> Iterable[dict[str, Any]]:
        return (dict(entry) for entry in self._entries.values())

    def find(
        self,
        *,
        creature_type: str | None = None,
        cr_max: str = "1",
        allow_fly: bool = True,
        allow_swim: bool = True,
    ) -> list[dict[str, Any]]:
        """Filter the catalog. ``creature_type=None`` means "any creature".

        The catalog holds every CR 0-1 creature, not just beasts, so the type
        filter is a *caller* decision: Wild Shape passes ``"beast"``, an encounter
        builder passes nothing.
        """

        ceiling = cr_value(cr_max)
        chosen: list[dict[str, Any]] = []
        for entry in self._entries.values():
            if creature_type is not None and str(
                entry.get("creature_type") or ""
            ) != creature_type:
                continue
            if cr_value(str(entry.get("cr") or "0")) > ceiling:
                continue
            speeds = entry.get("speeds") or {}
            if not allow_fly and int(speeds.get("fly", 0) or 0) > 0:
                continue
            if not allow_swim and int(speeds.get("swim", 0) or 0) > 0:
                continue
            chosen.append(dict(entry))
        chosen.sort(key=lambda row: (cr_value(str(row.get("cr") or "0")), str(row["name"])))
        return chosen
