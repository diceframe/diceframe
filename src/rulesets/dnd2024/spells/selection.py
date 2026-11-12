"""Class spell preparation and spellbook selection rules."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.progression.catalog import Dnd2024ProgressionCatalog
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog, SpellCatalogError
from src.rulesets.dnd2024.spells.preparation import PreparationPolicy


def _class_id(class_ref: str) -> str:
    return str(class_ref or "").removeprefix("class:")


@dataclass(slots=True)
class Dnd2024SpellSelection:
    bundle: LoadedRulesetBundle
    catalog: Dnd2024SpellCatalog = field(init=False)
    progression: Dnd2024ProgressionCatalog = field(init=False)

    def __post_init__(self) -> None:
        self.catalog = Dnd2024SpellCatalog.from_bundle(self.bundle)
        self.progression = Dnd2024ProgressionCatalog.from_bundle(self.bundle)

    def requirements(self, class_ref: str, level: int) -> dict[str, Any]:
        snapshot = self.progression.snapshot(class_ref, level)
        tracks = snapshot["tracks"]
        maximum_spell_level = max((int(key) for key in snapshot["spell_slots"]), default=0)
        # 法术书下限是内容数字（6 本起、每级 +2），不是法师的名字；策略缺省
        # 即"没有法术书"，非施法职业自然得 0。
        policy = PreparationPolicy.for_class(self.bundle, class_ref)
        return {
            "class_ref": class_ref,
            "level": level,
            "cantrip_count": int(tracks.get("cantrips", 0) or 0),
            "prepared_spell_count": int(tracks.get("prepared_spells", 0) or 0),
            "spellbook_minimum": policy.spellbook_minimum(level) if policy else 0,
            "maximum_spell_level": maximum_spell_level,
            "slot_profile": snapshot["slot_profile"],
            "spell_slots": deepcopy(snapshot["spell_slots"]),
        }

    def options(self, class_ref: str, level: int) -> dict[str, Any]:
        requirements = self.requirements(class_ref, level)
        spells = self.catalog.list_for_class(
            class_ref, maximum_level=requirements["maximum_spell_level"],
        )
        return {
            "requirements": requirements,
            "cantrips": [spell for spell in spells if spell["level"] == 0],
            "leveled_spells": [spell for spell in spells if spell["level"] > 0],
        }

    def validate(
        self,
        class_ref: str,
        level: int,
        choices: Any,
        *,
        previous: Mapping[str, Sequence[str]] | None = None,
    ) -> tuple[dict[str, list[str]], list[str]]:
        requirements = self.requirements(class_ref, level)
        if not requirements["slot_profile"]:
            if choices in (None, {}, {"cantrip_refs": [], "prepared_spell_refs": []}):
                return {"cantrip_refs": [], "prepared_spell_refs": [], "spellbook_refs": []}, []
            return {}, ["this class does not have class spellcasting"]
        if not isinstance(choices, dict):
            return {}, ["class_spell_choices are required for this class"]

        def refs(ref_key: str, id_key: str) -> Any:
            raw_refs = choices.get(ref_key)
            if raw_refs is not None:
                return raw_refs
            raw_ids = choices.get(id_key)
            if not isinstance(raw_ids, list):
                return raw_ids
            return [f"spell:{item}" for item in raw_ids]

        cantrips, errors = self.catalog.require(
            refs("cantrip_refs", "cantrip_ids"),
            class_ref=class_ref,
            level=0,
            count=requirements["cantrip_count"],
            label="cantrip_refs",
        )
        prepared, prepared_errors = self.catalog.require(
            refs("prepared_spell_refs", "prepared_spell_ids"),
            class_ref=class_ref,
            level=requirements["maximum_spell_level"],
            count=requirements["prepared_spell_count"],
            label="prepared_spell_refs",
        )
        errors.extend(prepared_errors)
        spellbook: list[str] = []
        policy = PreparationPolicy.for_class(self.bundle, class_ref)
        if policy is not None and policy.source == "spellbook":
            raw_spellbook = refs("spellbook_refs", "spellbook_ids")
            if not isinstance(raw_spellbook, list):
                errors.append("spellbook_refs are required for this class")
            else:
                spellbook, spellbook_errors = self._require_spellbook(
                    raw_spellbook,
                    class_ref=class_ref,
                    requirements=requirements,
                )
                errors.extend(spellbook_errors)
                if not set(prepared).issubset(spellbook):
                    errors.append("prepared_spell_refs must be in spellbook_refs")
        if previous is not None and policy is not None:
            errors.extend(
                self._replacement_errors(policy, previous, cantrips, prepared, spellbook)
            )
        return {
            "cantrip_refs": cantrips,
            "prepared_spell_refs": prepared,
            "spellbook_refs": spellbook,
        }, errors

    def _require_spellbook(
        self,
        spell_refs: list[Any],
        *,
        class_ref: str,
        requirements: Mapping[str, Any],
    ) -> tuple[list[str], list[str]]:
        """A spellbook's floor, not its size.

        SRD prints "six 1st-level spells" at level 1 and +2 per later level, but
        copying spells from scrolls or other books pushes the total above that
        floor.  An equality check makes every copied spell an invalid book, so
        the printed number is a minimum and the gain rule is enforced where a
        gain actually happens (level-up, see ``_replacement_errors``).
        """

        if any(not isinstance(item, str) for item in spell_refs):
            return [], ["spellbook_refs must be an array of spell refs"]
        book = [str(item) for item in spell_refs]
        errors: list[str] = []
        minimum = int(requirements["spellbook_minimum"])
        maximum = int(requirements["maximum_spell_level"])
        if len(book) < minimum:
            errors.append(f"spellbook_refs must contain at least {minimum} spells")
        if len(set(book)) != len(book):
            errors.append("spellbook_refs must not contain duplicate spells")
        for ref in book:
            spell = self.catalog.get(ref)
            if spell is None:
                errors.append(f"spellbook_refs contains unknown spell {ref}")
                continue
            if class_ref not in spell["class_refs"]:
                errors.append(f"{ref} is not on the {class_ref} spell list")
            spell_level = int(spell["level"])
            if not 1 <= spell_level <= maximum:
                errors.append(f"{ref} is not eligible at spell level {maximum}")
        return book, errors

    @staticmethod
    def _previous_refs(
        previous: Mapping[str, Sequence[str]], key: str,
    ) -> set[str]:
        raw = previous.get(key) or ()
        return {str(item) for item in raw if isinstance(item, str)}

    def _replacement_errors(
        self,
        policy: PreparationPolicy,
        previous: Mapping[str, Sequence[str]],
        cantrips: Sequence[str],
        prepared: Sequence[str],
        spellbook: Sequence[str],
    ) -> list[str]:
        """The swap budget at level-up, per the class's declared policy.

        Only the *dropped* spells count: gaining capacity is the other rule
        ("choose additional spells until the list matches the table") and it is
        already enforced by the exact-count check above.
        """

        errors: list[str] = []
        dropped = self._previous_refs(previous, "prepared_spell_refs") - set(prepared)
        if len(dropped) > policy.level_up_replace:
            errors.append(
                "prepared_spell_refs may replace at most "
                f"{policy.level_up_replace} spell(s) at this level"
            )
        cantrip_limit = 1 if policy.cantrip_swap_on == "level_up" else 0
        dropped_cantrips = self._previous_refs(previous, "cantrip_refs") - set(cantrips)
        if len(dropped_cantrips) > cantrip_limit:
            errors.append(
                f"cantrip_refs may replace at most {cantrip_limit} cantrip(s) "
                "at this level"
            )
        if policy.source == "spellbook" and policy.spellbook_gain_per_level:
            added = set(spellbook) - self._previous_refs(previous, "spellbook_refs")
            if len(added) != policy.spellbook_gain_per_level:
                errors.append(
                    "spellbook_refs must add exactly "
                    f"{policy.spellbook_gain_per_level} spell(s) at this level"
                )
        return errors

    def configure(
        self, character: dict[str, Any], choices: Any,
    ) -> dict[str, Any]:
        result = deepcopy(character)
        build = result.get("build")
        class_levels = build.get("class_levels") if isinstance(build, dict) else None
        if not isinstance(class_levels, list) or len(class_levels) != 1:
            raise SpellCatalogError("spell selection requires one class")
        class_ref = str(class_levels[0].get("class_ref") or "")
        level = int(class_levels[0].get("level", 0) or 0)
        parsed, errors = self.validate(class_ref, level, choices)
        if errors:
            raise SpellCatalogError("; ".join(errors))
        requirements = self.requirements(class_ref, level)
        if not requirements["slot_profile"]:
            return result
        class_entity = self.bundle.get("class", _class_id(class_ref)) or {}
        spell_ability = str(class_entity.get("spellcasting_ability") or "")
        result.setdefault("build", {})["class_spell_choices"] = deepcopy(parsed)
        result.setdefault("spellcasting", {})["class"] = {
            "class_ref": class_ref,
            "ability": spell_ability,
            "slot_profile": requirements["slot_profile"],
            "slots_current": deepcopy(requirements["spell_slots"]),
            "slots_max": deepcopy(requirements["spell_slots"]),
            "cantrip_capacity": requirements["cantrip_count"],
            "prepared_capacity": requirements["prepared_spell_count"],
            **parsed,
            "concentration": None,
        }
        abilities = result.get("abilities") or {}
        proficiency = int(result.get("derived", {}).get("proficiency_bonus", 2) or 2)
        score = int(abilities.get(spell_ability, 10) or 10)
        spell_attack_bonus = proficiency + (score - 10) // 2
        result.setdefault("derived", {})["spell_attack_bonus"] = spell_attack_bonus
        result["derived"]["spell_save_dc"] = 8 + spell_attack_bonus
        return result
