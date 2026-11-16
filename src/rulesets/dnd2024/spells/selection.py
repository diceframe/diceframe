"""Class spell preparation, always-prepared grants, and cross-list sources.

Three things are deliberately separate here, because the SRD separates them:

* **ordinary preparation** is what the class table's ``prepared_spells`` track
  sizes, and it is the only thing that capacity counts;
* **always-prepared grants** (the five subclass tables, Circle of the Land's
  terrain, Magical Discoveries) never enter that number, but are still *that
  class's spells* -- their spellcasting ability and list identity come from the
  granting class, not from the list the spell was printed on;
* **list expansion** (Magical Secrets) widens where a prepared spell may come
  from.  It grants nothing by itself and adds no capacity.

Grants are re-derived from canonical choices on every read, so a downgrade, a
rebuild, a save/load round trip and a replay can never disagree about a terrain
or a duplicate row.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.features.resolver import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression.catalog import Dnd2024ProgressionCatalog
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog, SpellCatalogError
from src.rulesets.dnd2024.spells.preparation import PreparationPolicy


def _class_id(class_ref: str) -> str:
    return str(class_ref or "").removeprefix("class:")


def _class_magic(character: Mapping[str, Any] | None) -> dict[str, Any]:
    """The live ``spellcasting.class`` block, or an empty dict."""

    spellcasting = (character or {}).get("spellcasting")
    if not isinstance(spellcasting, Mapping):
        return {}
    block = spellcasting.get("class")
    return block if isinstance(block, dict) else {}


def _casting_segment(value: Any) -> str:
    """The cast-mode a spell is used with, stripped of ritual and riders.

    "Action or Ritual" casts with an action; "1 minute or Ritual" does not;
    "Bonus Action, which you take ..." is still a Bonus Action.  Spell Mastery
    asks for a casting time of an action, so it needs this normalised form
    rather than the raw catalog string.
    """

    return str(value or "").split(",")[0].split(" or ")[0].strip()


@dataclass(slots=True)
class Dnd2024SpellSelection:
    bundle: LoadedRulesetBundle
    catalog: Dnd2024SpellCatalog = field(init=False)
    progression: Dnd2024ProgressionCatalog = field(init=False)
    features: Dnd2024ClassFeatureResolver = field(init=False)
    _effects: dict[str, dict[str, Any]] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.catalog = Dnd2024SpellCatalog.from_bundle(self.bundle)
        self.progression = Dnd2024ProgressionCatalog.from_bundle(self.bundle)
        self.features = Dnd2024ClassFeatureResolver(self.bundle)

    def requirements(
        self, class_ref: str, level: int, character: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ordinary preparation capacity.  Grants are never part of this number."""

        snapshot = self.progression.snapshot(class_ref, level)
        tracks = snapshot["tracks"]
        maximum_spell_level = max((int(key) for key in snapshot["spell_slots"]), default=0)
        # 法术书下限是内容数字（6 本起、每级 +2），不是法师的名字；策略缺省
        # 即"没有法术书"，非施法职业自然得 0。
        policy = PreparationPolicy.for_class(self.bundle, class_ref)
        expansion = self.prepared_list_expansion(character)
        return {
            "class_ref": class_ref,
            "level": level,
            "cantrip_count": int(tracks.get("cantrips", 0) or 0),
            "prepared_spell_count": int(tracks.get("prepared_spells", 0) or 0),
            "spellbook_minimum": policy.spellbook_minimum(level) if policy else 0,
            "maximum_spell_level": maximum_spell_level,
            "slot_profile": snapshot["slot_profile"],
            "spell_slots": deepcopy(snapshot["spell_slots"]),
            # 跨列表来源只扩大"能选什么"，不改变数量；挂在 requirements 里
            # 是为了让客户端与校验读同一个闭集，而不是各自推断。
            "prepared_list_expansion": list(expansion),
        }

    def options(
        self, class_ref: str, level: int, character: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        requirements = self.requirements(class_ref, level, character)
        spells = self._listable(class_ref, requirements)
        return {
            "requirements": requirements,
            "cantrips": [spell for spell in spells if spell["level"] == 0],
            "leveled_spells": [spell for spell in spells if spell["level"] > 0],
            "grant_slots": self.grant_slots(level, character),
            "free_cast_slots": self.free_cast_slots(level, character),
        }

    def prepared_list_expansion(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        """Class lists a prepared spell may also come from, per owned features.

        Magical Secrets is the only declaration today.  The returned refs are
        the four lists it names; ownership (Bard level 10, class feature) is
        decided by the feature resolver, so no class name is hardcoded here.
        """

        if not isinstance(character, Mapping):
            return ()
        lists: list[str] = []
        for definition in self.features.features_for(character):
            for ref in definition.parameters.get("prepared_list_expansion") or ():
                listed = str(ref)
                if listed and listed not in lists:
                    lists.append(listed)
        return tuple(lists)

    def grant_slots(
        self, level: int, character: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Player-choice grant slots the character currently owns.

        Magical Discoveries is one: two spells from the Cleric, Druid, or Wizard
        list, always prepared, replaceable one per Bard level.  The slot reports
        its options so a client never has to know which lists are legal.
        """

        slots: list[dict[str, Any]] = []
        if not isinstance(character, Mapping):
            return slots
        class_ref = self._class_ref(character)
        if not class_ref:
            return slots
        stored = self.features.spell_grant_choices(character)
        maximum = int(
            self.requirements(class_ref, level, character)["maximum_spell_level"]
        )
        for definition in self.features.features_for(character):
            count = int(definition.parameters.get("spell_choice_count") or 0)
            if count < 1:
                continue
            lists = [str(ref) for ref in definition.parameters.get("spell_choice_lists") or ()]
            slot_state = stored.get(definition.id) or {}
            slots.append({
                "id": f"spell_grant:{definition.owner}:{definition.id}",
                "feature_id": definition.id,
                "owner": definition.owner,
                "count": count,
                "replacement": int(definition.parameters.get("spell_choice_replacement") or 0),
                "granted_level": int(
                    slot_state.get("granted_level") or definition.minimum_level
                ),
                "spell_lists": lists,
                "selected_refs": list(slot_state.get("spells") or ()),
                "options": self._spells_for_lists(lists, maximum),
            })
        return slots

    def free_cast_slots(
        self, level: int, character: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Free-cast slots the character owns at ``level``, with their options.

        Every row is one independent pool: Contact Patron, each of the four
        Mystic Arcana, the two Spell Mastery slots and the two Signature slots
        differ in level, recharge and remaining uses, so nothing here is
        collapsed into a shared "free casting" flag.
        """

        slots: list[dict[str, Any]] = []
        if not isinstance(character, Mapping):
            return slots
        for row in self.features.free_cast_slots(character):
            if row["source"] != "fixed":
                row["options"] = self._free_cast_options(row, character)
            else:
                row["options"] = []
            slots.append(row)
        return slots

    def rest_free_cast_rows(
        self, character: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """The free-cast slots a Long Rest may re-choose, with their candidates.

        Only slots that declare a rest replacement are listed: Signature Spells
        prints no study rule, so showing it in a rest panel would offer a change
        ``rebind_free_cast`` refuses.  ``rest_replace`` travels with the row so
        the client can enforce the same budget the engine enforces, instead of
        hard-coding "one spell per rest" in the view.
        """

        state = self.features.class_state(character)
        if state is None:
            return []
        _class_id, level = state
        rows: list[dict[str, Any]] = []
        for row in self.free_cast_slots(level, character):
            budget = int(row.get("rest_replace") or 0)
            if budget < 1:
                continue
            chosen = str(row.get("spell_ref") or "")
            spell = self.catalog.get(chosen) if chosen else None
            rows.append({
                "slot_id": row["slot_id"],
                "feature_id": row["feature_id"],
                "spell_level": int(row["spell_level"]),
                "recharge": str(row["recharge"]),
                "spell_ref": chosen,
                "spell_name": str((spell or {}).get("name") or ""),
                "rest_replace": budget,
                "valid": bool(row["valid"]),
                "options": [dict(option) for option in row.get("options") or ()],
            })
        return rows

    def _free_cast_options(
        self, row: Mapping[str, Any], character: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """The spells one slot may legally hold, in catalog order."""

        level = int(row["spell_level"])
        if row["source"] == "spellbook":
            block = _class_magic(character)
            known = [
                str(ref) for ref in block.get("spellbook_refs") or ()
                if isinstance(ref, str)
            ]
            spells = [
                spell for spell in (self.catalog.get(ref) for ref in known)
                if spell is not None
            ]
        else:
            spells = self._spells_for_lists(list(row["spell_lists"]), level)
        wanted = str(row.get("casting_time") or "")
        return [
            {"ref": spell["ref"], "name": spell["name"], "level": int(spell["level"])}
            for spell in spells
            if int(spell["level"]) == level
            and (
                not wanted
                or _casting_segment(spell["casting_time"]) == wanted
            )
        ]

    def validate(
        self,
        class_ref: str,
        level: int,
        choices: Any,
        *,
        previous: Mapping[str, Sequence[str]] | None = None,
        context: str = "level_up",
        character: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        requirements = self.requirements(class_ref, level, character)
        if not requirements["slot_profile"]:
            if choices in (None, {}, {"cantrip_refs": [], "prepared_spell_refs": []}):
                return {
                    "cantrip_refs": [], "prepared_spell_refs": [], "spellbook_refs": [],
                }, []
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
        prepared, prepared_errors = self._require_prepared(
            refs("prepared_spell_refs", "prepared_spell_ids"),
            class_ref=class_ref,
            requirements=requirements,
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
                self._replacement_errors(
                    policy, previous, cantrips, prepared, spellbook, context,
                )
            )
        grants, grant_errors = self._validate_grants(
            choices.get("spell_grants"), character, requirements,
        )
        errors.extend(grant_errors)
        free_cast, free_errors = self._validate_free_cast(
            choices.get("free_cast"), character, requirements, spellbook,
        )
        errors.extend(free_errors)
        return {
            "cantrip_refs": cantrips,
            "prepared_spell_refs": prepared,
            "spellbook_refs": spellbook,
            "spell_grant_choices": grants,
            "free_cast_slots": free_cast,
        }, errors

    def preparation_options(self, character: Mapping[str, Any]) -> dict[str, Any]:
        """Everything the rest panel needs to offer a preparation change.

        The candidates are the class's own legal picks, narrowed to the
        spellbook for a ``spellbook`` source -- a Wizard cannot prepare from a
        list, only from what is written down.  A non-caster reports ``policy:
        None`` and zero capacity rather than an error.
        """

        build = character.get("build")
        rows = build.get("class_levels") if isinstance(build, Mapping) else None
        row = rows[0] if isinstance(rows, list) and rows else {}
        class_ref = str(row.get("class_ref") or "")
        level = int(row.get("level") or 0)
        block = _class_magic(character)
        empty = {
            "class_ref": class_ref,
            "level": level,
            "policy": None,
            "prepared_capacity": 0,
            "prepared_spell_refs": [],
            "spellbook_refs": [],
            "cantrip_capacity": 0,
            "cantrip_refs": [],
            "cantrips": [],
            "leveled_spells": [],
        }
        if not class_ref:
            return empty
        requirements = self.requirements(class_ref, level)
        if not requirements["slot_profile"]:
            return empty
        policy = PreparationPolicy.for_class(self.bundle, class_ref)
        if policy is None:
            return empty
        options = self.options(class_ref, level)
        leveled = options["leveled_spells"]
        if policy.source == "spellbook":
            known = {
                str(ref) for ref in block.get("spellbook_refs") or ()
                if isinstance(ref, str)
            }
            leveled = [spell for spell in leveled if spell["ref"] in known]
        return {
            "class_ref": class_ref,
            "level": level,
            "policy": {
                "source": policy.source,
                "level_up_replace": policy.level_up_replace,
                "long_rest_replace": (
                    "any" if policy.long_rest_replace is None
                    else policy.long_rest_replace
                ),
                "cantrip_swap_on": policy.cantrip_swap_on,
            },
            "prepared_capacity": int(requirements["prepared_spell_count"]),
            "prepared_spell_refs": [
                str(ref) for ref in block.get("prepared_spell_refs") or ()
                if isinstance(ref, str)
            ],
            "spellbook_refs": [
                str(ref) for ref in block.get("spellbook_refs") or ()
                if isinstance(ref, str)
            ],
            "cantrip_capacity": int(requirements["cantrip_count"]),
            "cantrip_refs": [
                str(ref) for ref in block.get("cantrip_refs") or ()
                if isinstance(ref, str)
            ],
            "cantrips": options["cantrips"],
            "leveled_spells": leveled,
        }

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
        context: str,
    ) -> list[str]:
        """The swap budget for this context, per the class's declared policy.

        Level-up and Long Rest are two different budgets for the same class:
        a Bard swaps one spell when gaining a level and none on a rest, a Cleric
        the other way round, and a Wizard re-picks cantrips only after a rest.
        Only the *dropped* spells count; gaining capacity is the other SRD rule
        and is already enforced by the exact-count check above.
        """

        errors: list[str] = []
        if context == "level_up":
            budget = policy.level_up_replace
            where = "at this level"
        elif context == "long_rest":
            budget = policy.long_rest_replace
            where = "on a long rest"
        else:
            raise SpellCatalogError(f"unknown spell selection context {context}")
        dropped = self._previous_refs(previous, "prepared_spell_refs") - set(prepared)
        if budget is not None and len(dropped) > budget:
            errors.append(
                f"prepared_spell_refs may replace at most {budget} spell(s) {where}"
            )
        cantrip_timing = "level_up" if context == "level_up" else "long_rest"
        cantrip_limit = 1 if policy.cantrip_swap_on == cantrip_timing else 0
        dropped_cantrips = self._previous_refs(previous, "cantrip_refs") - set(cantrips)
        if len(dropped_cantrips) > cantrip_limit:
            errors.append(
                f"cantrip_refs may replace at most {cantrip_limit} cantrip(s) {where}"
            )
        if policy.source == "spellbook" and context == "level_up":
            if policy.spellbook_gain_per_level:
                # 升级只增不减：旧书目是这张卡已经付过代价的资产，不能因为
                # "新增数量对得上"就被替换掉。抄写与失书各有自己的领域操作。
                lost = self._previous_refs(previous, "spellbook_refs") - set(spellbook)
                if lost:
                    errors.append(
                        "spellbook_refs must keep the spells the book already holds"
                    )
                added = set(spellbook) - self._previous_refs(previous, "spellbook_refs")
                if len(added) != policy.spellbook_gain_per_level:
                    errors.append(
                        "spellbook_refs must add exactly "
                        f"{policy.spellbook_gain_per_level} spell(s) at this level"
                    )
        elif policy.source == "spellbook":
            # 休息只重排准备位，不往法术书里加东西。
            if set(spellbook) != self._previous_refs(previous, "spellbook_refs"):
                errors.append("spellbook_refs cannot change during a rest")
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
        parsed, errors = self.validate(
            class_ref, level, choices, character=result,
        )
        if errors:
            raise SpellCatalogError("; ".join(errors))
        requirements = self.requirements(class_ref, level, result)
        if not requirements["slot_profile"]:
            return result
        class_entity = self.bundle.get("class", _class_id(class_ref)) or {}
        spell_ability = str(class_entity.get("spellcasting_ability") or "")
        grant_choices = parsed.pop("spell_grant_choices", {})
        free_cast = parsed.pop("free_cast_slots", {})
        result.setdefault("build", {})["class_spell_choices"] = deepcopy(parsed)
        block = {
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
        previous = _class_magic(character).get("spell_grant_choices")
        merged = deepcopy(previous) if isinstance(previous, dict) else {}
        merged.update(grant_choices)
        if merged:
            # 地形选择由长休写入；这里只合并本次提交的槽位，不能把 Land
            # 的既有地形顺手清成"未选"。
            block["spell_grant_choices"] = merged
        if free_cast:
            # 免费施法槽的"已消耗"计数与所选法术是同一份记录：重提交时
            # 不重算次数，替换法术也不刷新，见 `_free_cast_record`。
            block["free_cast_slots"] = deepcopy(free_cast)
        result.setdefault("spellcasting", {})["class"] = block
        abilities = result.get("abilities") or {}
        proficiency = int(result.get("derived", {}).get("proficiency_bonus", 2) or 2)
        score = int(abilities.get(spell_ability, 10) or 10)
        spell_attack_bonus = proficiency + (score - 10) // 2
        result.setdefault("derived", {})["spell_attack_bonus"] = spell_attack_bonus
        result["derived"]["spell_save_dc"] = 8 + spell_attack_bonus
        return result

    # ------------------------------------------------------------------
    # projections
    # ------------------------------------------------------------------

    def prepared_view(
        self, character: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Every spell the character can treat as prepared, with its sources.

        One effective entry per spell: a spell that is both ordinary-prepared
        and always-prepared appears once carrying both sources, so revoking one
        still leaves the other visible instead of deleting the spell.
        """

        block = _class_magic(character)
        ordinary_cantrips = [str(ref) for ref in block.get("cantrip_refs") or ()]
        ordinary_prepared = [str(ref) for ref in block.get("prepared_spell_refs") or ()]
        rows = self.features.spell_grant_rows(character)
        entries: dict[str, dict[str, Any]] = {}

        def entry(spell_ref: str) -> dict[str, Any]:
            spell = self.catalog.get(spell_ref) or {}
            return entries.setdefault(spell_ref, {
                "spell_ref": spell_ref,
                "name": spell.get("name", ""),
                "level": int(spell.get("level", 0) or 0),
                "automation_status": self.automation_status(spell_ref),
                "sources": [],
            })

        for spell_ref in ordinary_cantrips:
            entry(spell_ref)["sources"].append({"kind": "class_list", "slot": "cantrip"})
        for spell_ref in ordinary_prepared:
            entry(spell_ref)["sources"].append({"kind": "class_list", "slot": "prepared"})
        for row in rows:
            entry(row["spell_ref"])["sources"].append({
                "kind": "class_feature",
                "owner": row["owner"],
                "feature_id": row["feature_id"],
                "granted_level": row["granted_level"],
                "terrain": row["terrain"],
                "choice_id": row["choice_id"],
                "spell_ability": row["spell_ability"],
            })
        prepared = [row for row in entries.values() if row["level"] > 0]
        cantrips = [row for row in entries.values() if row["level"] == 0]
        granted_refs = {row["spell_ref"] for row in rows}
        return {
            "cantrips": cantrips,
            "prepared": prepared,
            "capacity": {
                "cantrip": int(block.get("cantrip_capacity", 0) or 0),
                "prepared": int(block.get("prepared_capacity", 0) or 0),
                "cantrip_used": len(ordinary_cantrips),
                "prepared_used": len(ordinary_prepared),
                # 常驻授予单独计数：它们既不是"已用容量"，也不是"剩余容量"。
                "granted": len(granted_refs),
            },
        }

    def grant_refs(self, character: Mapping[str, Any] | None) -> list[str]:
        """The always-prepared spell refs, in declaration order."""

        return [row["spell_ref"] for row in self.features.spell_grant_rows(character)]

    def automation_status(self, spell_ref: str) -> str:
        """``automated`` when a resolvable effect exists, else ``not_automated``.

        The status is read from the same ``spell_effects`` table combat executes
        from -- a spell being listed in the catalog is not evidence that it can
        be cast, and no caller may hand-write ``automated`` to hide an empty
        effect.
        """

        if self._effects is None:
            raw = self.bundle.get("combat_catalog", "srd_combat_core") or {}
            effects = raw.get("spell_effects") if isinstance(raw, Mapping) else None
            self._effects = dict(effects) if isinstance(effects, Mapping) else {}
        return (
            "automated"
            if str(spell_ref).removeprefix("spell:") in self._effects
            else "not_automated"
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _require_prepared(
        self,
        spell_refs: Any,
        *,
        class_ref: str,
        requirements: Mapping[str, Any],
        label: str,
    ) -> tuple[list[str], list[str]]:
        lists = tuple(requirements.get("prepared_list_expansion") or ())
        if not lists:
            return self.catalog.require(
                spell_refs,
                class_ref=class_ref,
                level=requirements["maximum_spell_level"],
                count=requirements["prepared_spell_count"],
                label=label,
            )
        # 跨列表开放后，普通 require 的"必须在该职业列表上"不再成立，
        # 但等级、数量、重复三条仍然逐字沿用同一套判据与措辞。
        if not isinstance(spell_refs, list) or any(
            not isinstance(item, str) for item in spell_refs
        ):
            return [], [f"{label} must be an array of spell refs"]
        refs = list(spell_refs)
        errors: list[str] = []
        count = int(requirements["prepared_spell_count"])
        maximum = int(requirements["maximum_spell_level"])
        if len(refs) != count:
            errors.append(f"{label} must contain exactly {count} spells")
        if len(set(refs)) != len(refs):
            errors.append(f"{label} must not contain duplicate spells")
        for ref in refs:
            spell = self.catalog.get(ref)
            if spell is None:
                errors.append(f"{label} contains unknown spell {ref}")
                continue
            families = set(spell["class_refs"])
            if class_ref not in families and not families & set(lists):
                errors.append(
                    f"{ref} is not on the {class_ref} spell list or an expanded list"
                )
            spell_level = int(spell["level"])
            if not 1 <= spell_level <= maximum:
                errors.append(f"{ref} is not eligible at spell level {maximum}")
        return refs, errors

    def _validate_grants(
        self,
        raw_grants: Any,
        character: Mapping[str, Any] | None,
        requirements: Mapping[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Validate the chosen spells of every grant slot the character owns.

        Ownership, count, list membership and level eligibility all come from
        the declaration; only a slot the character actually owns may be filled,
        and an unknown slot is an error rather than a field to ignore.
        """

        slots = {
            slot["feature_id"]: slot
            for slot in self.grant_slots(int(requirements["level"]), character)
        }
        errors: list[str] = []
        if raw_grants is None:
            parsed: dict[str, dict[str, Any]] = {}
        elif not isinstance(raw_grants, Mapping):
            return {}, ["spell_grants must be an object keyed by feature id"]
        else:
            parsed = {}
            for feature_id, raw in raw_grants.items():
                key = str(feature_id)
                slot = slots.get(key)
                if slot is None:
                    errors.append(f"spell grant {key} is not a slot this character owns")
                    continue
                refs = raw.get("spells") if isinstance(raw, Mapping) else raw
                if not isinstance(refs, list) or any(
                    not isinstance(ref, str) for ref in refs
                ):
                    errors.append(f"spell grant {key} must be an array of spell refs")
                    continue
                if len(set(refs)) != len(refs):
                    errors.append(f"spell grant {key} must not repeat a spell")
                    continue
                if len(refs) != int(slot["count"]):
                    errors.append(
                        f"spell grant {key} must contain exactly {slot['count']} spells"
                    )
                    continue
                allowed = set(slot["spell_lists"])
                for ref in refs:
                    spell = self.catalog.get(ref)
                    if spell is None:
                        errors.append(f"spell grant {key} contains unknown spell {ref}")
                        continue
                    if not set(spell["class_refs"]) & allowed:
                        errors.append(
                            f"{ref} is not on a list {key} may choose from"
                        )
                        continue
                    spell_level = int(spell["level"])
                    if spell_level and not (
                        1 <= spell_level <= int(requirements["maximum_spell_level"])
                    ):
                        errors.append(
                            f"{ref} is not eligible at spell level "
                            f"{requirements['maximum_spell_level']}"
                        )
                previous = [
                    ref for ref in slot["selected_refs"] if isinstance(ref, str)
                ]
                if previous and slot["replacement"] >= 0:
                    replaced = len(set(refs) - set(previous))
                    if replaced > int(slot["replacement"]):
                        errors.append(
                            f"spell grant {key} can replace at most "
                            f"{slot['replacement']} of its spells per level"
                        )
                parsed[key] = {
                    "spells": list(refs),
                    "granted_level": int(slot.get("granted_level") or requirements["level"]),
                }
        for feature_id, slot in slots.items():
            # 尚未做出的选择不是非法提交：槽位仍然开放（`grant_slots()` 里
            # `selected_refs` 为空，升级预览据此把 `class_spell_choices` 标成
            # required 并列出 `count`），但"还没选"和"提交了错误内容"必须
            # 分开。否则任何经过该等级的路径——旧客户端、只想提普通准备的
            # 调用方、以及本切片之前的全部升级测试——都会被一个新特性挡住。
            if not slot["selected_refs"]:
                continue
            if feature_id in parsed:
                continue
            # 已选过的槽位不重提交就保持原样，不当作"清空"。
            parsed[feature_id] = {
                "spells": list(slot["selected_refs"]),
                "granted_level": int(slot.get("granted_level") or requirements["level"]),
            }
        return parsed, errors

    def _validate_free_cast(
        self,
        raw_slots: Any,
        character: Mapping[str, Any] | None,
        requirements: Mapping[str, Any],
        spellbook: list[str],
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Validate the spells held in the character's free-cast slots.

        A slot the character owns but has not filled yet is not an error: the
        same rule the grant slots follow, because a client that only wants to
        submit ordinary preparation must never be blocked by a high-level
        feature it has no UI for.  What *is* an error is a slot that does not
        exist, a spell of the wrong level, a spell from outside the declared
        source, and a replacement beyond what the feature allows.
        """

        slots = {
            row["slot_id"]: row
            for row in self.free_cast_slots(int(requirements["level"]), character)
        }
        errors: list[str] = []
        previous = self.features.free_cast_choices(character)
        # 只保留当前仍拥有的槽位：降级或换职后遗留的记录不能继续生效。
        records: dict[str, dict[str, Any]] = {
            slot_id: deepcopy(record)
            for slot_id, record in previous.items()
            if slot_id in slots and str(record.get("spell_ref") or "")
        }
        if raw_slots is not None:
            if not isinstance(raw_slots, Mapping):
                return {}, ["free_cast must be an object keyed by slot id"]
            replaced: dict[str, int] = {}
            for key, value in raw_slots.items():
                slot_id = str(key)
                slot = slots.get(slot_id)
                if slot is None:
                    errors.append(
                        f"free cast slot {slot_id} is not a slot this character owns"
                    )
                    continue
                ref = value.get("spell") if isinstance(value, Mapping) else value
                if not isinstance(ref, str) or not ref:
                    errors.append(f"free cast slot {slot_id} must name one spell")
                    continue
                if slot["source"] == "fixed":
                    if ref != slot["spell_ref"]:
                        errors.append(f"free cast slot {slot_id} is not a player choice")
                    continue
                problem = self._free_cast_ref_error(slot, ref, spellbook)
                if problem:
                    errors.append(problem)
                    continue
                held = str((records.get(slot_id) or {}).get("spell_ref") or "")
                if held and held != ref:
                    group = str(slot["replacement_group"])
                    replaced[group] = replaced.get(group, 0) + 1
                    if replaced[group] > int(slot["replacement"]):
                        errors.append(
                            f"free cast {group} can replace at most "
                            f"{slot['replacement']} of its spells per level"
                        )
                        continue
                records[slot_id] = self._free_cast_record(
                    slot, ref, records.get(slot_id),
                )
        errors.extend(self._free_cast_duplicate_errors(slots, records))
        return records, errors

    def _free_cast_ref_error(
        self,
        slot: Mapping[str, Any],
        ref: str,
        spellbook: list[str],
    ) -> str:
        """Why ``ref`` may not fill ``slot``; empty when it may."""

        slot_id = slot["slot_id"]
        spell = self.catalog.get(ref)
        if spell is None:
            return f"free cast slot {slot_id} contains unknown spell {ref}"
        if int(spell["level"]) != int(slot["spell_level"]):
            return f"{ref} is not a level {slot['spell_level']} spell"
        wanted = str(slot.get("casting_time") or "")
        if wanted and _casting_segment(spell["casting_time"]) != wanted:
            return f"{ref} cannot be held: its casting time must be {wanted}"
        if slot["source"] == "spellbook" and ref not in spellbook:
            return f"{ref} is not in your spellbook"
        if slot["source"] == "class_list":
            allowed = set(slot["spell_lists"])
            if not set(spell["class_refs"]) & allowed:
                return f"{ref} is not on a list {slot_id} may choose from"
        return ""

    @staticmethod
    def _free_cast_record(
        slot: Mapping[str, Any], ref: str, previous: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """The record to persist for one slot.

        Replacing the spell never refreshes the uses: an arcanum swapped at
        level-up is still the one that was already cast today.
        """

        record = {
            "spell_ref": ref,
            "spell_level": int(slot["spell_level"]),
            "recharge": str(slot["recharge"]),
        }
        uses = int(slot["uses_max"])
        if uses:
            kept = (previous or {}).get("uses_remaining")
            record["uses_remaining"] = (
                int(kept) if isinstance(kept, int) and not isinstance(kept, bool)
                else uses
            )
        return record

    @staticmethod
    def _free_cast_duplicate_errors(
        slots: Mapping[str, Mapping[str, Any]],
        records: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        """Signature Spells are two *different* spells, not the same one twice."""

        seen: dict[tuple[str, str], str] = {}
        errors: list[str] = []
        for slot_id, record in records.items():
            slot = slots.get(slot_id)
            if slot is None:
                continue
            ref = str(record.get("spell_ref") or "")
            if not ref:
                continue
            key = (str(slot["feature_id"]), ref)
            first = seen.get(key)
            if first is not None:
                errors.append(f"{ref} cannot fill both {first} and {slot_id}")
                continue
            seen[key] = slot_id
        return errors

    def rebind_free_cast(
        self, character: Mapping[str, Any] | None, changes: Any,
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Re-choose free-cast slots outside level-up: the Long Rest study.

        Spell Mastery is the only declaration that allows this today, and it
        allows exactly one replacement per Long Rest -- never two, never on a
        Short Rest, never automatically.  Signature Spells declares
        ``free_cast_rest_replace == 0``, so the same call refuses it, which is
        how "the book prints no replacement rule" becomes enforced rather than
        merely undocumented.
        """

        block = _class_magic(character)
        build = (character or {}).get("build")
        rows = build.get("class_levels") if isinstance(build, Mapping) else None
        level = int((rows[0] if isinstance(rows, list) and rows else {}).get("level", 0) or 0)
        slots = {
            row["slot_id"]: row for row in self.free_cast_slots(level, character)
        }
        previous = self.features.free_cast_choices(character)
        records: dict[str, dict[str, Any]] = {
            slot_id: deepcopy(record)
            for slot_id, record in previous.items()
            if slot_id in slots and str(record.get("spell_ref") or "")
        }
        if not isinstance(changes, Mapping):
            return {}, ["free cast changes must be an object keyed by slot id"]
        errors: list[str] = []
        requested: dict[str, Any] = {}
        for key, value in changes.items():
            slot_id = str(key)
            slot = slots.get(slot_id)
            if slot is None:
                errors.append(
                    f"free cast slot {slot_id} is not a slot this character owns"
                )
                continue
            if int(slot["rest_replace"]) < 1:
                errors.append(
                    f"free cast slot {slot_id} cannot be re-chosen during a rest"
                )
                continue
            requested[slot_id] = value
        if errors:
            return {}, errors
        if requested:
            budget = min(int(slots[slot_id]["rest_replace"]) for slot_id in requested)
            if len(requested) > budget:
                return {}, [
                    f"at most {budget} free cast spells can be re-chosen during a rest"
                ]
        spellbook = [str(ref) for ref in block.get("spellbook_refs") or ()]
        for slot_id, value in requested.items():
            slot = slots[slot_id]
            ref = value.get("spell") if isinstance(value, Mapping) else value
            if not isinstance(ref, str) or not ref:
                errors.append(f"free cast slot {slot_id} must name one spell")
                continue
            problem = self._free_cast_ref_error(slot, ref, spellbook)
            if problem:
                errors.append(problem)
                continue
            records[slot_id] = self._free_cast_record(
                slot, ref, records.get(slot_id),
            )
        if errors:
            return {}, errors
        errors.extend(self._free_cast_duplicate_errors(slots, records))
        return records, errors

    def _listable(
        self, class_ref: str, requirements: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        spells = self.catalog.list_for_class(
            class_ref, maximum_level=requirements["maximum_spell_level"],
        )
        lists = tuple(requirements.get("prepared_list_expansion") or ())
        if not lists:
            return spells
        # 跨列表只扩大"已准备法术"的来源：戏法不是 prepared spells，Magical
        # Secrets 也没有给戏法开新列表，所以戏法始终只来自本职业列表。
        known = {spell["ref"] for spell in spells}
        for expansion in lists:
            if expansion == class_ref:
                continue
            for spell in self._spells_for_lists(
                [expansion], requirements["maximum_spell_level"],
            ):
                if spell["level"] == 0 or spell["ref"] in known:
                    continue
                known.add(spell["ref"])
                spells.append(spell)
        return sorted(spells, key=lambda item: (item["level"], item["name"], item["id"]))

    def _spells_for_lists(
        self, lists: list[str], maximum_level: int,
    ) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for ref in lists:
            for spell in self.catalog.list_for_class(
                ref, maximum_level=maximum_level,
            ):
                seen.setdefault(spell["ref"], spell)
        return sorted(seen.values(), key=lambda item: (item["level"], item["name"], item["id"]))

    @staticmethod
    def _class_ref(character: Mapping[str, Any]) -> str:
        build = character.get("build")
        rows = build.get("class_levels") if isinstance(build, Mapping) else None
        row = rows[0] if isinstance(rows, list) and rows else {}
        return str(row.get("class_ref") or "")
