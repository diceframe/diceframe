"""Canonical equipment eligibility for D&D 2024 class features.

A class feature may declare *when* it works.  Martial Arts is the first one:
a Monk benefits from it only while wearing no armor, wielding no Shield, and
wielding only Monk Weapons.

That question is answered here, and only here, from the bundle's own canonical
metadata:

* the ``item`` definitions say what an item *is* (``weapon`` / ``armor`` /
  ``shield`` / ``focus``), which is what "wearing armor" and "wielding shield"
  mean for a character's ``equipment.item_refs``;
* the combat catalog's weapon profiles say what kind of weapon it is
  (``simple`` or ``martial``, melee or ``ranged``, and whether it has the
  ``light`` property).

No file re-states a weapon table, no state is persisted, and nothing here is
combat resolution: eligibility is recomputed from the character's current
canonical equipment every time it is asked.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.attunement import validate_item_declaration

from .models import EquipmentRequirement


def canonical_item_id(ref: Any) -> str:
    """``item:quarterstaff`` -> ``quarterstaff`` (the canonical bundle id)."""

    return str(ref or "").removeprefix("item:")


def _is_melee(weapon: Mapping[str, Any]) -> bool:
    # 战斗目录把远程武器标成 ``ranged``；带 ``thrown_range`` 的仍然是被投掷的
    # 近战武器（匕首、手斧、矛），因此不能据此排除。
    return not bool(weapon.get("ranged"))


def weapon_matches_kind(weapon: Mapping[str, Any] | None, kind: str) -> bool:
    """Whether one canonical weapon profile is of the declared kind.

    ``simple_melee`` is the SRD "Simple Melee weapon" column.  A Monk Weapon is
    that, or a Martial Melee weapon with the Light property; an item the bundle
    does not classify is never a Monk Weapon (fail closed, never guessed from a
    display name).
    """

    if not isinstance(weapon, Mapping):
        return False
    category = str(weapon.get("category") or "")
    melee = _is_melee(weapon)
    if kind == "simple_melee":
        return melee and category == "simple"
    if kind == "martial_melee_light":
        return melee and category == "martial" and bool(weapon.get("light"))
    return False


@dataclass(frozen=True, slots=True)
class Dnd2024EquipmentCatalog:
    """Read-only canonical item / weapon metadata the feature boundary reads."""

    item_types: Mapping[str, str]
    weapons: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_bundle(cls, bundle: LoadedRulesetBundle) -> Dnd2024EquipmentCatalog:
        item_types: dict[str, str] = {}
        for item in bundle.list("item"):
            # 同调声明是物品 schema 的一部分；非 bool 必须在这里就炸掉，
            # 而不是被下游当作 truthy 使用。
            validate_item_declaration(item)
            item_id = str(item.get("id") or "")
            if item_id:
                item_types[item_id] = str(item.get("item_type") or "")
        combat = bundle.get("combat_catalog", "srd_combat_core") or {}
        raw_weapons = combat.get("weapons")
        weapons = raw_weapons if isinstance(raw_weapons, Mapping) else {}
        return cls(
            item_types=item_types,
            weapons=deepcopy(dict(weapons)),
        )

    def equipped_refs(self, character: Mapping[str, Any] | None) -> tuple[str, ...]:
        """The character's canonical ``equipment.item_refs`` (what is worn/wielded)."""

        equipment = character.get("equipment") if isinstance(character, Mapping) else None
        equipment = equipment if isinstance(equipment, Mapping) else {}
        refs = equipment.get("item_refs")
        if not isinstance(refs, list):
            return ()
        return tuple(str(ref) for ref in refs if str(ref))

    def item_type(self, ref: str) -> str:
        return str(self.item_types.get(canonical_item_id(ref), ""))

    def is_monk_weapon(self, ref: str) -> bool:
        """The SRD 5.2.1 Monk Weapon test for one equipped ref."""

        weapon = self.weapons.get(canonical_item_id(ref))
        return weapon_matches_kind(weapon, "simple_melee") or weapon_matches_kind(
            weapon, "martial_melee_light",
        )

    def weapon_refs_of_kinds(
        self, character: Mapping[str, Any] | None, kinds: Iterable[str],
    ) -> tuple[str, ...]:
        """The equipped weapon refs covered by ``kinds``, in equipment order."""

        wanted = tuple(str(kind) for kind in kinds)
        covered: list[str] = []
        for ref in self.equipped_refs(character):
            if self.item_type(ref) != "weapon":
                continue
            weapon = self.weapons.get(canonical_item_id(ref))
            if any(weapon_matches_kind(weapon, kind) for kind in wanted):
                covered.append(ref)
        return tuple(covered)

    def requirement_met(
        self, character: Mapping[str, Any] | None, requirement: EquipmentRequirement,
    ) -> bool:
        """Whether the character's current equipment satisfies a declaration."""

        refs = self.equipped_refs(character)
        types = [self.item_type(ref) for ref in refs]
        if requirement.unarmored and "armor" in types:
            return False
        if requirement.no_shield and "shield" in types:
            return False
        if requirement.weapon_kinds and len(
            self.weapon_refs_of_kinds(character, requirement.weapon_kinds)
        ) != types.count("weapon"):
            # 只要手里有一件不属于声明类别的武器，前提就不成立。
            return False
        return True
