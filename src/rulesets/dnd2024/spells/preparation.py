"""Class spell-preparation policy, read from content.

The SRD separates "how a class changes its prepared spells" into three
independent axes, and the eight spellcasting classes differ on all three:

* **when** -- on gaining a level, or when finishing a Long Rest;
* **how many** -- any number (a whole-list reshuffle) or exactly one;
* **from where** -- the class list, or your own spellbook.

Hard-coding eight class branches for that would be the same mistake as
hard-coding ASI levels: these are content numbers.  The class entity carries a
``preparation_policy`` block; this module is its only reader, so the rules stay
data and the code stays one shape.

A class without the block is simply not a preparing class (the four
non-casters), and ``for_class`` returns ``None`` rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.spells.catalog import SpellCatalogError

_SOURCES = frozenset({"class_list", "spellbook"})
_CANTRIP_TIMINGS = frozenset({"level_up", "long_rest", "none"})


def _non_negative_int(raw: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpellCatalogError(
            f"preparation_policy.{key} must be a non-negative integer"
        )
    return value


@dataclass(frozen=True, slots=True)
class PreparationPolicy:
    """One class's preparation rules, as declared by content.

    ``long_rest_replace`` is ``None`` when the class may reshuffle any number of
    prepared spells on a Long Rest, and an integer cap otherwise; ``0`` means
    the class cannot change preparation during a rest at all.
    """

    source: str
    level_up_replace: int
    long_rest_replace: int | None
    cantrip_swap_on: str
    spellbook_initial: int
    spellbook_per_level: int
    spellbook_gain_per_level: int

    @classmethod
    def for_class(
        cls, bundle: LoadedRulesetBundle, class_ref: str,
    ) -> "PreparationPolicy | None":
        class_id = str(class_ref or "").removeprefix("class:")
        if not class_id:
            return None
        entity = bundle.get("class", class_id) or {}
        raw = entity.get("preparation_policy")
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise SpellCatalogError(
                f"class {class_id} preparation_policy must be an object"
            )
        source = str(raw.get("source") or "")
        if source not in _SOURCES:
            raise SpellCatalogError(
                f"class {class_id} preparation source must be class_list or spellbook"
            )
        timing = str(raw.get("cantrip_swap_on") or "none")
        if timing not in _CANTRIP_TIMINGS:
            raise SpellCatalogError(
                f"class {class_id} cantrip_swap_on must be level_up, long_rest, or none"
            )
        rest = raw.get("long_rest_replace", 0)
        if rest == "any":
            rest_cap: int | None = None
        elif isinstance(rest, bool) or not isinstance(rest, int) or rest < 0:
            raise SpellCatalogError(
                f"class {class_id} long_rest_replace must be \"any\" or a "
                "non-negative integer"
            )
        else:
            rest_cap = rest
        book = raw.get("spellbook")
        book = book if isinstance(book, Mapping) else {}
        return cls(
            source=source,
            level_up_replace=_non_negative_int(raw, "level_up_replace"),
            long_rest_replace=rest_cap,
            cantrip_swap_on=timing,
            spellbook_initial=_non_negative_int(book, "initial"),
            spellbook_per_level=_non_negative_int(book, "per_level"),
            spellbook_gain_per_level=_non_negative_int(raw, "spellbook_gain_per_level"),
        )

    def spellbook_minimum(self, level: int) -> int:
        """The floor a spellbook must hold; copying spells may exceed it."""

        if self.source != "spellbook":
            return 0
        return self.spellbook_initial + self.spellbook_per_level * max(0, level - 1)

    def can_change_on_long_rest(self) -> bool:
        return self.long_rest_replace is None or self.long_rest_replace > 0
