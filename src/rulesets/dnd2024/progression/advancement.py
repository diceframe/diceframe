"""Pure next-level planning and application for single-class D&D 2024 characters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.builder import ABILITY_IDS, ability_modifier
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression.catalog import (
    Dnd2024ProgressionCatalog,
    ProgressionCatalogError,
)
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

# 专长机械参数的封闭键集：新参数随其归属切片一起扩这里，未知键 fail closed。
ADVANCEMENT_FEAT_PARAMETER_KEYS = frozenset({"ranged_attack_bonus"})

# 纯 ASI 走的是一条与专长相同的代码路径：属性值提升被声明成一条伪专长，
# 让「占专长槽」只有一种实现。它不是角色拥有的专长，账本据此把它记成
# ability_score_increase，而不是 feat_ref——否则审计会去要一个从未选过的专长。
PLAIN_ABILITY_FEAT_REF = "feat:ability_score_improvement"

# 成长账本：每级一条，记录该级实际做出的选择。缺失项检测只读它，不从
# 数值反推选择（`abilities` 已经把来源合并掉了，反推不出「这一级选了什么」）。
ADVANCEMENT_RECORDS_KEY = "advancement_records"


def _canonical(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("ruleset_character")
    return nested if isinstance(nested, dict) else payload


def _class_id(class_ref: str) -> str:
    prefix = "class:"
    value = str(class_ref or "")
    return value[len(prefix):] if value.startswith(prefix) else ""


def _feature_state(
    class_ref: str, level: int, subclass_ref: str = "",
) -> dict[str, Any]:
    """The minimal canonical shape the class feature boundary reads."""

    state: dict[str, Any] = {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
    }
    if subclass_ref:
        # 子职特性只按真实选择授予：升级增量必须带上"升级前/升级后"的选择，
        # 否则在选出子职的那一级会漏掉它的生命值上限加成。
        state["features"] = {"subclass_ref": subclass_ref}
    return state


def _slot_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        level: after.get(level, 0) - before.get(level, 0)
        for level in sorted(set(before) | set(after), key=int)
        if after.get(level, 0) != before.get(level, 0)
    }


@dataclass(slots=True)
class Dnd2024AdvancementEngine:
    bundle: LoadedRulesetBundle
    catalog: Dnd2024ProgressionCatalog = field(init=False)
    advancement_feats: dict[str, dict[str, Any]] = field(init=False)
    advancement_feat_labels: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.catalog = Dnd2024ProgressionCatalog.from_bundle(self.bundle)
        feat_catalog = self.bundle.get("advancement_feat_catalog", "srd_advancement_feats")
        if feat_catalog is None or not isinstance(feat_catalog.get("feats"), dict):
            raise ProgressionCatalogError("D&D 2024 advancement feat catalog is missing")
        labels = feat_catalog.get("labels")
        if not isinstance(labels, dict) or set(labels) != set(feat_catalog["feats"]):
            raise ProgressionCatalogError("advancement feat labels must cover the catalog")
        for feat_id, feat in feat_catalog["feats"].items():
            self._validate_feat_parameters(str(feat_id), feat)
        self.advancement_feats = deepcopy(feat_catalog["feats"])
        self.advancement_feat_labels = {
            str(feat_id): str(label) for feat_id, label in labels.items()
        }

    @staticmethod
    def _validate_feat_parameters(feat_id: str, feat: Any) -> None:
        """Validate the closed parameter vocabulary an advancement feat declares.

        Feat parameters are consumption contracts read by feature projections
        (e.g. Archery's flat ranged attack bonus), so the catalog is the only
        writer and every unknown key fails closed here instead of being
        silently ignored downstream.
        """

        if not isinstance(feat, dict):
            return
        parameters = feat.get("parameters")
        if parameters is None:
            return
        if not isinstance(parameters, dict):
            raise ProgressionCatalogError(
                f"advancement feat parameters must be an object: {feat_id}"
            )
        unknown = sorted(set(parameters) - ADVANCEMENT_FEAT_PARAMETER_KEYS)
        if unknown:
            raise ProgressionCatalogError(
                f"advancement feat {feat_id} has an unknown parameter: {unknown[0]}"
            )
        for key, value in parameters.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 10
            ):
                raise ProgressionCatalogError(
                    f"advancement feat {feat_id} parameter {key} must be an integer from 1 to 10"
                )

    def missing_choices(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Choices this character still owes, in level order.

        This is the other half of "a subclass only comes from an explicit
        selection": a rule that refuses the choice after the granting level
        must also say what happens to a character that never made it.  Without
        this, a card imported without ``features.subclass_ref`` silently loses
        its whole subclass tree and has no way back.

        The audit reads facts, never the audit log.  ``progression.history``
        may be trimmed, and a card that never went through this application has
        no history at all -- only the ledger is authoritative about *which*
        level chose what.

        Coverage is deliberately narrow, because a broad audit would have to
        guess:

        - subclass: one persisted field, and the granting level comes from the
          catalog instead of a hard-coded 3;
        - ability increase / feat: read from the growth ledger;
        - prepared spells: a count comparison, not a legality audit.  Free
          casts and feature-granted always-prepared spells live outside the
          lists being counted, so they are excluded by construction.

        An old save with no ledger is not reported as incomplete.  There is no
        way to tell "the player skipped it" from "this field did not exist
        yet", and a false report would mark every legacy card as broken.
        """

        character = _canonical(payload)
        class_ref, level = self._single_class_state(character)
        feature_state = character.get("features")
        feature_state = feature_state if isinstance(feature_state, dict) else {}
        subclass = self.catalog.subclass_choice(class_ref)
        missing: list[dict[str, Any]] = []

        missing.extend(
            self._missing_subclass(level, feature_state, subclass)
        )
        missing.extend(
            self._missing_advancements(class_ref, level, character)
        )
        missing.extend(
            self._missing_spell_slots(class_ref, level, character)
        )
        return missing

    def _missing_subclass(
        self,
        level: int,
        feature_state: dict[str, Any],
        subclass: dict[str, Any],
    ) -> list[dict[str, Any]]:
        grant_level = self._pending_subclass_level(feature_state, level, subclass)
        if grant_level is None:
            return []
        return [{
            "id": "subclass_ref",
            "kind": "single",
            "required": True,
            "level": grant_level,
            "options": [{
                "value": f"subclass:{subclass['id']}",
                "name": self._subclass_name(str(subclass["id"])),
                "source_ref": str(subclass.get("source_ref") or ""),
            }],
            "reason": (
                f"level {grant_level} granted a subclass and none was selected"
            ),
        }]

    @staticmethod
    def _pending_subclass_level(
        feature_state: dict[str, Any], level: int, subclass: dict[str, Any],
    ) -> int | None:
        """The granting level this character still owes, or ``None``.

        The question is "does this character have a subclass", not "does this
        level grant one".  Those agree for a card grown from level 1, and stop
        agreeing the moment a card arrives from outside -- which is exactly the
        case this exists for.
        """

        if str(feature_state.get("subclass_ref") or ""):
            return None
        by_level = subclass.get("feature_ids_by_level") or {}
        if not by_level:
            return None
        grant_level = min(int(raw_level) for raw_level in by_level)
        return grant_level if level >= grant_level else None

    def _missing_advancements(
        self, class_ref: str, level: int, character: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records = self._advancement_records(character)
        if records is None:
            return []
        by_level: dict[int, dict[str, Any]] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            raw_level = record.get("level")
            if isinstance(raw_level, bool) or not isinstance(raw_level, int):
                continue
            by_level[raw_level] = record

        missing: list[dict[str, Any]] = []
        for row_level in range(1, level + 1):
            snapshot = self.catalog.snapshot(class_ref, row_level)
            gained = snapshot["gained_feature_ids"]
            epic = "epic_boon" in gained
            if not epic and "ability_score_improvement" not in gained:
                continue
            record = by_level.get(row_level)
            if record is not None and self._recorded_a_choice(record, epic):
                continue
            reason = (
                f"level {row_level} granted "
                f"{'an Epic Boon' if epic else 'an ability score improvement'} "
                "and no choice was recorded"
            )
            if epic:
                missing.append({
                    "id": "epic_boon_ref",
                    "kind": "single",
                    "required": True,
                    "level": row_level,
                    "options": self._feat_options(
                        "epic_boon", character, row_level, snapshot,
                    ),
                    "reason": reason,
                })
                missing.append({
                    "id": "ability_score_increases",
                    "kind": "ability_increase",
                    "required": True,
                    "level": row_level,
                    "total": 1,
                    "maximum_score": 30,
                    "allowed": "any",
                    "pattern": "1",
                    "reason": reason,
                })
            else:
                missing.append({
                    "id": "ability_score_increases",
                    "kind": "ability_increase",
                    "required": True,
                    "level": row_level,
                    "total": 2,
                    "maximum_score": 20,
                    "allowed": "any",
                    "pattern": "2_or_1_1",
                    "reason": reason,
                })
                missing.append({
                    "id": "feat_ref",
                    "kind": "single",
                    "required": True,
                    "level": row_level,
                    "options": self._feat_options(
                        "general", character, row_level, snapshot,
                    ),
                    "reason": reason,
                })
        return missing

    def _missing_spell_slots(
        self, class_ref: str, level: int, character: dict[str, Any],
    ) -> list[dict[str, Any]]:
        snapshot = self.catalog.snapshot(class_ref, level)
        if not snapshot["slot_profile"]:
            return []
        required = Dnd2024SpellSelection(self.bundle).requirements(class_ref, level)
        spellcasting = character.get("spellcasting")
        spell_class = (
            spellcasting.get("class") if isinstance(spellcasting, dict) else None
        )
        spell_class = spell_class if isinstance(spell_class, dict) else {}

        checks = (
            ("cantrip_refs", "cantrip_count", "cantrips"),
            ("prepared_spell_refs", "prepared_spell_count", "prepared spells"),
        )
        if class_ref == "class:wizard":
            checks = (*checks, ("spellbook_refs", "spellbook_minimum", "spellbook spells"))

        missing: list[dict[str, Any]] = []
        for ref_key, count_key, label in checks:
            expected = int(required.get(count_key, 0) or 0)
            actual = len([ref for ref in (spell_class.get(ref_key) or []) if ref])
            if actual >= expected:
                continue
            missing.append({
                "id": count_key,
                "kind": "spell_selection",
                "required": True,
                "level": level,
                "expected": expected,
                "actual": actual,
                "reason": (
                    f"level {level} expects {expected} {label}, {actual} selected"
                ),
            })
        return missing

    @staticmethod
    def _advancement_records(character: dict[str, Any]) -> list[Any] | None:
        """The ledger, or ``None`` when this card predates it.

        ``None`` and ``[]`` mean different things: a card with the field but no
        entries has been through at least one audit and can be judged; a card
        without the field cannot.  Detection must not confuse the two.
        """

        progression = character.get("progression")
        if not isinstance(progression, dict):
            return None
        records = progression.get(ADVANCEMENT_RECORDS_KEY)
        return records if isinstance(records, list) else None

    @staticmethod
    def _recorded_a_choice(record: dict[str, Any], epic: bool) -> bool:
        key = "epic_boon_ref" if epic else "feat_ref"
        return bool(record.get("ability_score_increase") or record.get(key))

    def preview_next_level(
        self,
        payload: dict[str, Any],
        choices: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate one next-level choice set and return an exact, non-mutating diff."""

        character = _canonical(payload)
        choices = deepcopy(choices or {})
        class_ref, current_level = self._single_class_state(character)
        if current_level >= 20:
            raise ProgressionCatalogError("a level 20 character cannot gain another level")
        target_level = current_level + 1
        before = self.catalog.snapshot(class_ref, current_level)
        after = self.catalog.snapshot(class_ref, target_level)
        class_id = _class_id(class_ref)
        class_entity = self.bundle.get("class", class_id)
        if class_entity is None:
            raise ProgressionCatalogError(f"class entity is missing: {class_ref}")

        errors: list[str] = []
        requirements: list[dict[str, Any]] = []
        feature_state = character.get("features")
        feature_state = feature_state if isinstance(feature_state, dict) else {}

        selected_subclass_ref = str(
            choices.get("subclass_ref") or feature_state.get("subclass_ref") or ""
        )
        subclass = self.catalog.subclass_choice(class_ref)
        expected_subclass_ref = f"subclass:{subclass['id']}"
        # 判定读"升级后这个角色是否仍缺子职"，而不是"本级是否授予子职"。
        # 两者对从 1 级长起来的卡等价；对导入卡不等价，而导入卡正是缺口来源。
        if self._pending_subclass_level(feature_state, target_level, subclass) is not None:
            requirements.append({
                "id": "subclass_ref",
                "kind": "single",
                "required": True,
                "options": [{
                    "value": expected_subclass_ref,
                    "name": self._subclass_name(str(subclass["id"])),
                    "source_ref": str(subclass["source_ref"]),
                }],
            })
            if not selected_subclass_ref:
                errors.append("subclass_ref is required at this level")
            elif selected_subclass_ref != expected_subclass_ref:
                errors.append("subclass_ref is not an SRD subclass for this class")
        elif choices.get("subclass_ref") and selected_subclass_ref != feature_state.get("subclass_ref"):
            # 已有子职不可改选；"从空到有"走上面的补齐分支，不落在这里。
            errors.append("subclass_ref cannot be changed once selected")

        ability_increases, advancement_feat_ref, ability_errors = self._advancement_feat(
            character, after, choices
        )
        errors.extend(ability_errors)
        if "ability_score_improvement" in after["gained_feature_ids"]:
            ability_spec = self._ability_choice_for(advancement_feat_ref)
            requirements.append({
                "id": "ability_score_increases",
                "kind": "ability_increase",
                "required": True,
                "total": int(ability_spec.get("points", 2) or 2),
                "maximum_score": int(ability_spec.get("maximum", 20) or 20),
                "allowed": deepcopy(ability_spec.get("allowed", "any")),
                "pattern": str(ability_spec.get("pattern") or "2_or_1_1"),
            })
            requirements.append({
                "id": "feat_ref",
                "kind": "single",
                "required": True,
                "options": self._feat_options("general", character, target_level, after),
            })
        if "epic_boon" in after["gained_feature_ids"]:
            requirements.extend([
                {
                    "id": "epic_boon_ref",
                    "kind": "single",
                    "required": True,
                    "options": self._feat_options(
                        "epic_boon", character, target_level, after
                    ),
                },
                {
                    "id": "ability_score_increases",
                    "kind": "ability_increase",
                    "required": True,
                    "total": 1,
                    "maximum_score": 30,
                },
            ])

        spell_choices: dict[str, list[str]] = {}
        if after["slot_profile"]:
            spell_selection = Dnd2024SpellSelection(self.bundle)
            before_spell = character.get("spellcasting", {}).get("class")
            before_spell = before_spell if isinstance(before_spell, dict) else {}
            current_cantrips = list(before_spell.get("cantrip_refs") or [])
            current_prepared = list(before_spell.get("prepared_spell_refs") or [])
            current_spellbook = list(before_spell.get("spellbook_refs") or [])
            target_requirements = spell_selection.requirements(class_ref, target_level)
            spell_choice_required = (
                len(current_cantrips) != target_requirements["cantrip_count"]
                or len(current_prepared) != target_requirements["prepared_spell_count"]
                or (
                    class_ref == "class:wizard"
                    and len(current_spellbook) != target_requirements["spellbook_minimum"]
                )
            )
            raw_spell_choices = choices.get("class_spell_choices")
            if spell_choice_required or raw_spell_choices is not None:
                spell_options = spell_selection.options(class_ref, target_level)
                requirements.append({
                    "id": "class_spell_choices",
                    "kind": "spell_selection",
                    "required": spell_choice_required,
                    **target_requirements,
                    "cantrips": spell_options["cantrips"],
                    "leveled_spells": spell_options["leveled_spells"],
                })
                spell_choices, spell_errors = spell_selection.validate(
                    class_ref, target_level, raw_spell_choices,
                )
                errors.extend(spell_errors)
            else:
                spell_choices = {
                    "cantrip_refs": current_cantrips,
                    "prepared_spell_refs": current_prepared,
                    "spellbook_refs": current_spellbook,
                }

        hp_method = str(choices.get("hp_method") or "fixed")
        fixed_hp = int(class_entity.get("fixed_hp_per_level", 0) or 0)
        hit_die = int(class_entity.get("hit_die", 0) or 0)
        hp_roll = choices.get("hp_roll")
        if hp_method not in {"fixed", "rolled"}:
            errors.append("hp_method must be fixed or rolled")
        if hp_method == "rolled":
            if isinstance(hp_roll, bool) or not isinstance(hp_roll, int) or not 1 <= hp_roll <= hit_die:
                errors.append(f"hp_roll must be an integer from 1 to {hit_die}")
            hp_base = int(hp_roll or 0)
        else:
            if hp_roll is not None:
                errors.append("hp_roll is only valid when hp_method is rolled")
            hp_base = fixed_hp

        old_abilities = character.get("abilities")
        if not isinstance(old_abilities, dict):
            raise ProgressionCatalogError("canonical character abilities are missing")
        new_abilities = {ability: int(old_abilities.get(ability, 0) or 0) for ability in ABILITY_IDS}
        for ability, increase in ability_increases.items():
            new_abilities[ability] += increase
        old_con = ability_modifier(int(old_abilities.get("con", 0) or 0))
        new_con = ability_modifier(new_abilities["con"])
        species = self.bundle.get(
            "species", str(character.get("identity", {}).get("species_ref") or "").removeprefix("species:")
        ) or {}
        species_hp = int(species.get("hp_per_level_bonus", 0) or 0)
        new_level_hp = max(1, hp_base + new_con + species_hp)
        retrospective_con_hp = current_level * (new_con - old_con)
        # 防御类特性的生命值上限加成只按等级增量发放：2→3 得 +3，之后每级 +1。
        # 用与建卡相同的 resolver 公式，不回溯 from_level 之前的等级。
        features = Dnd2024ClassFeatureResolver(self.bundle)
        current_subclass_ref = str(feature_state.get("subclass_ref") or "")
        next_subclass_ref = (
            selected_subclass_ref
            if selected_subclass_ref == expected_subclass_ref
            else current_subclass_ref
        )
        feature_hp_gain = (
            features.feature_max_hp_bonus(
                _feature_state(class_ref, target_level, next_subclass_ref)
            )
            - features.feature_max_hp_bonus(
                _feature_state(class_ref, current_level, current_subclass_ref)
            )
        )
        hp_gain = new_level_hp + retrospective_con_hp + feature_hp_gain

        subclass_feature_ids = self._subclass_features(
            subclass,
            target_level,
            selected_subclass_ref == expected_subclass_ref,
            # 原本没有子职、本次补选：低等级的子职特性对它全是新的。
            catching_up=(
                not str(feature_state.get("subclass_ref") or "")
                and selected_subclass_ref == expected_subclass_ref
            ),
        )
        gained_feature_ids = [*after["gained_feature_ids"], *subclass_feature_ids]
        return {
            "ok": not errors,
            "errors": errors,
            "requirements": requirements,
            "choices": choices,
            "from_level": current_level,
            "to_level": target_level,
            "class_ref": class_ref,
            "source_ref": after["source_ref"],
            "content_version": after["content_version"],
            "diff": {
                "proficiency_bonus": {
                    "before": before["proficiency_bonus"],
                    "after": after["proficiency_bonus"],
                },
                "gained_feature_ids": gained_feature_ids,
                "track_changes": {
                    key: {"before": before["tracks"].get(key, 0), "after": value}
                    for key, value in after["tracks"].items()
                    if before["tracks"].get(key, 0) != value
                },
                "spell_slot_changes": _slot_delta(before["spell_slots"], after["spell_slots"]),
                "spell_slots": after["spell_slots"],
                "ability_increases": ability_increases,
                "advancement_feat_ref": advancement_feat_ref,
                "abilities": new_abilities,
                "class_spell_choices": spell_choices,
                "hp": {
                    "method": hp_method,
                    "base": hp_base,
                    "constitution_modifier": new_con,
                    "retrospective_constitution": retrospective_con_hp,
                    "gain": hp_gain,
                },
            },
            "snapshot": after,
        }

    def apply_next_level(
        self,
        payload: dict[str, Any],
        choices: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a ready preview to a copied canonical character."""

        original = _canonical(payload)
        preview = self.preview_next_level(original, choices)
        if not preview["ok"]:
            raise ProgressionCatalogError("; ".join(preview["errors"]))
        character = deepcopy(original)
        target_level = int(preview["to_level"])
        build = character["build"]
        build["level"] = target_level
        build["class_levels"][0]["level"] = target_level
        character["abilities"] = deepcopy(preview["diff"]["abilities"])

        hp_gain = int(preview["diff"]["hp"]["gain"])
        resources = character.setdefault("resources", {})
        resources["max_hp"] = int(resources.get("max_hp", 0) or 0) + hp_gain
        resources["hp"] = min(
            resources["max_hp"], int(resources.get("hp", 0) or 0) + hp_gain
        )
        class_entity = self.bundle.get("class", _class_id(preview["class_ref"])) or {}
        hit_die_key = f"d{int(class_entity.get('hit_die', 0) or 0)}"
        hit_dice = resources.setdefault("hit_dice", {})
        hit_dice[hit_die_key] = target_level

        features = character.setdefault("features", {})
        subclass_ref = str(preview["choices"].get("subclass_ref") or "")
        if subclass_ref:
            features["subclass_ref"] = subclass_ref
        advancement_feat_ref = str(preview["diff"].get("advancement_feat_ref") or "")
        if advancement_feat_ref:
            features.setdefault("feat_refs", []).append(advancement_feat_ref)
        grants = features.setdefault("class_feature_grants", [])
        if not grants:
            level_one = self.catalog.snapshot(preview["class_ref"], 1)
            grants.extend(
                {"id": feature_id, "class_ref": preview["class_ref"], "level": 1}
                for feature_id in level_one["gained_feature_ids"]
            )
        grants.extend(
            {"id": feature_id, "class_ref": preview["class_ref"], "level": target_level}
            for feature_id in preview["diff"]["gained_feature_ids"]
        )

        progression = character.setdefault("progression", {})
        progression.update({
            "mode": "single_class",
            "class_ref": preview["class_ref"],
            "level": target_level,
            "tracks": deepcopy(preview["snapshot"]["tracks"]),
            "content_version": preview["content_version"],
        })
        self._record_advancement(
            progression,
            level=target_level,
            class_ref=str(preview["class_ref"]),
            choices=preview["choices"],
            diff=preview["diff"],
        )
        history = progression.setdefault("history", [])
        history.append({
            "from_level": preview["from_level"],
            "to_level": target_level,
            "choices": deepcopy(preview["choices"]),
            "diff": deepcopy(preview["diff"]),
            "source_ref": preview["source_ref"],
        })
        self._update_spellcasting(character, preview)
        self._update_derived(character)
        return character

    def _record_advancement(
        self,
        progression: dict[str, Any],
        *,
        level: int,
        class_ref: str,
        choices: dict[str, Any],
        diff: dict[str, Any],
    ) -> None:
        """Append this level's choices to the growth ledger.

        The ledger answers "what did this level choose", which is the only
        question the missing-choice audit may ask -- ``abilities`` has already
        folded every source together, so a skipped ASI cannot be told from a
        small one by reading the number.

        Nothing is written for a choice the level did not make.  An empty record
        would read as "the player answered with nothing" and make a genuinely
        skipped level look completed.

        Order is enforced here rather than trusted from the caller: levels
        advance one at a time, so a non-advancing append means two writers
        disagreed about the current level.
        """

        records = progression.setdefault(ADVANCEMENT_RECORDS_KEY, [])
        if not isinstance(records, list):
            raise ProgressionCatalogError(
                "progression.advancement_records must be a list"
            )
        previous_level = 0
        if records:
            last = records[-1]
            if not isinstance(last, dict):
                raise ProgressionCatalogError(
                    "progression.advancement_records entries must be objects"
                )
            previous_level = int(last.get("level", 0) or 0)
        if previous_level >= level:
            raise ProgressionCatalogError(
                "progression.advancement_records must advance one level at a time: "
                f"{previous_level} is not below {level}"
            )

        record: dict[str, Any] = {"level": level, "class_ref": class_ref}
        increases = choices.get("ability_score_increases")
        if isinstance(increases, dict):
            parsed = {
                str(ability): int(points)
                for ability, points in increases.items()
                if isinstance(points, int)
                and not isinstance(points, bool)
                and points > 0
            }
            if parsed:
                record["ability_score_increase"] = parsed
        feat_ref = str(diff.get("advancement_feat_ref") or "")
        if feat_ref and feat_ref != PLAIN_ABILITY_FEAT_REF:
            gained = diff.get("gained_feature_ids") or []
            key = "epic_boon_ref" if "epic_boon" in gained else "feat_ref"
            record[key] = feat_ref
        subclass_ref = str(choices.get("subclass_ref") or "")
        if subclass_ref:
            record["subclass_ref"] = subclass_ref
        records.append(record)

    def repair_choices(
        self,
        payload: dict[str, Any],
        repairs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fill the choices this character still owes, without changing level.

        Repair is not a second upgrade path.  It never moves the level, and it
        only accepts ids that ``missing_choices`` actually reported -- a stray
        key is an error rather than a field to ignore, the same rule the
        upgrade preview applies to unknown choice keys.

        Hit points are recomputed from the two things that can move them here,
        and neither is "one level's worth":

        - a subclass's declared maximum, which is an absolute value at the
          current level, not a per-level increment;
        - a constitution increase, which pays back over the levels already
          earned, exactly as an upgrade-time increase does.
        """

        character = deepcopy(_canonical(payload))
        repairs = deepcopy(repairs or {})
        class_ref, level = self._single_class_state(character)
        feature_state = character.get("features")
        feature_state = feature_state if isinstance(feature_state, dict) else {}
        features = character.setdefault("features", {})
        subclass = self.catalog.subclass_choice(class_ref)
        expected_subclass_ref = f"subclass:{subclass['id']}"
        pending = self.missing_choices(character)
        pending_ids = {row["id"] for row in pending}
        errors: list[str] = []

        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        hp_before = resolver.feature_max_hp_bonus(character)
        abilities = character.get("abilities")
        abilities = abilities if isinstance(abilities, dict) else {}
        con_before = ability_modifier(int(abilities.get("con", 0) or 0))

        grant_level = self._pending_subclass_level(feature_state, level, subclass)
        records: list[Any] | None = self._advancement_records(character)
        repaired_anything = False

        chosen_subclass = repairs.get("subclass_ref")
        if chosen_subclass is not None:
            value = str(chosen_subclass)
            if "subclass_ref" not in pending_ids:
                errors.append("subclass_ref is not missing from this character")
            elif value != expected_subclass_ref:
                errors.append("subclass_ref is not an SRD subclass for this class")
            else:
                features["subclass_ref"] = value
                repaired_anything = True
                if records is not None and grant_level is not None:
                    self._merge_advancement_record(records, {
                        "level": grant_level,
                        "class_ref": class_ref,
                        "subclass_ref": value,
                    })

        for entry in repairs.get("advancements") or []:
            if not isinstance(entry, dict):
                errors.append("advancement repairs must be objects")
                continue
            raw_level = entry.get("level")
            if isinstance(raw_level, bool) or not isinstance(raw_level, int):
                errors.append("an advancement repair needs an integer level")
                continue
            if not 1 <= raw_level <= level:
                errors.append(
                    f"advancement repair level is out of range: {raw_level}"
                )
                continue
            if not any(
                row["id"] == "ability_score_increases" and row["level"] == raw_level
                for row in pending
            ):
                errors.append(f"level {raw_level} owes no ability increase")
                continue
            snapshot = self.catalog.snapshot(class_ref, raw_level)
            increases, feat_ref, feat_errors = self._advancement_feat(
                character, snapshot, entry
            )
            errors.extend(feat_errors)
            if feat_errors:
                continue
            for ability, points in increases.items():
                abilities[ability] = int(abilities.get(ability, 0) or 0) + int(points)
            repaired_anything = True
            if records is not None:
                record: dict[str, Any] = {
                    "level": raw_level, "class_ref": class_ref,
                }
                if increases:
                    record["ability_score_increase"] = dict(increases)
                if feat_ref and feat_ref != PLAIN_ABILITY_FEAT_REF:
                    epic = "epic_boon" in snapshot["gained_feature_ids"]
                    record["epic_boon_ref" if epic else "feat_ref"] = feat_ref
                    owned = features.setdefault("feat_refs", [])
                    if feat_ref not in owned:
                        owned.append(feat_ref)
                self._merge_advancement_record(records, record)

        if errors:
            raise ProgressionCatalogError("; ".join(errors))

        if records is not None:
            progression = character.setdefault("progression", {})
            progression[ADVANCEMENT_RECORDS_KEY] = records

        if repaired_anything:
            hp_after = resolver.feature_max_hp_bonus(character)
            con_after = ability_modifier(int(abilities.get("con", 0) or 0))
            hp_gain = (hp_after - hp_before) + level * (con_after - con_before)
            if hp_gain:
                resources = character.setdefault("resources", {})
                resources["max_hp"] = (
                    int(resources.get("max_hp", 0) or 0) + hp_gain
                )
                resources["hp"] = min(
                    int(resources["max_hp"]),
                    int(resources.get("hp", 0) or 0) + hp_gain,
                )
            self._update_derived(character)
        return character

    @staticmethod
    def _merge_advancement_record(
        records: list[Any], record: dict[str, Any],
    ) -> None:
        """Insert or update one level's ledger entry, keeping level order.

        Upgrades append; repairs fill in history, which may mean updating a
        level that already has a record (a subclass repair on top of a level-3
        entry that recorded something else) or inserting one that was skipped.
        """

        target_level = int(record["level"])
        for index, existing in enumerate(records):
            if not isinstance(existing, dict):
                continue
            if int(existing.get("level", 0) or 0) == target_level:
                records[index] = {**existing, **record}
                return
        records.append(record)
        records.sort(key=lambda row: int(row.get("level", 0) or 0))

    def _single_class_state(self, character: dict[str, Any]) -> tuple[str, int]:
        binding = character.get("rule_binding")
        if not isinstance(binding, dict) or binding.get("runtime_id") != "core:dnd2024":
            raise ProgressionCatalogError("character is not bound to core:dnd2024")
        if binding.get("content_version") != self.bundle.manifest.content_version:
            raise ProgressionCatalogError("character content version does not match the bundle")
        build = character.get("build")
        class_levels = build.get("class_levels") if isinstance(build, dict) else None
        if not isinstance(class_levels, list) or len(class_levels) != 1:
            raise ProgressionCatalogError("only single-class advancement is currently supported")
        row = class_levels[0]
        if not isinstance(row, dict):
            raise ProgressionCatalogError("class level state is invalid")
        class_ref = str(row.get("class_ref") or "")
        level = row.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 20:
            raise ProgressionCatalogError("current class level must be an integer from 1 to 20")
        self.catalog.snapshot(class_ref, level)
        return class_ref, level

    def _advancement_feat(
        self,
        character: dict[str, Any],
        snapshot: dict[str, Any],
        choices: dict[str, Any],
    ) -> tuple[dict[str, int], str, list[str]]:
        gained_features = snapshot["gained_feature_ids"]
        raw = choices.get("ability_score_increases")
        general = "ability_score_improvement" in gained_features
        epic = "epic_boon" in gained_features
        selected_ref = str(
            choices.get("epic_boon_ref") if epic else choices.get("feat_ref") or ""
        )
        if general and not selected_ref and isinstance(raw, dict):
            selected_ref = "feat:ability_score_improvement"
        errors: list[str] = []
        if not general and not epic:
            if raw:
                errors.append("ability_score_increases is not available at this level")
            if choices.get("feat_ref") or choices.get("epic_boon_ref"):
                errors.append("advancement feat selection is not available at this level")
            return {}, "", errors

        expected_category = "epic_boon" if epic else "general"
        feat_id = selected_ref.removeprefix("feat:") if selected_ref.startswith("feat:") else ""
        feat = self.advancement_feats.get(feat_id)
        if not selected_ref:
            errors.append(f"{'epic_boon_ref' if epic else 'feat_ref'} is required at this level")
            return {}, "", errors
        if feat is None or feat.get("category") != expected_category:
            errors.append(f"{selected_ref} is not an eligible {expected_category} feat")
            return {}, "", errors
        target_level = int(snapshot["level"])
        if target_level < int(feat.get("minimum_level", 1) or 1):
            errors.append(f"{selected_ref} level prerequisite is not met")
        owned = list(character.get("features", {}).get("feat_refs") or [])
        if selected_ref in owned and not bool(feat.get("repeatable")):
            errors.append(f"{selected_ref} cannot be selected more than once")
        minimums = feat.get("any_ability_minimum")
        abilities = character.get("abilities") or {}
        if isinstance(minimums, dict) and not any(
            int(abilities.get(ability, 0) or 0) >= int(minimum)
            for ability, minimum in minimums.items()
        ):
            errors.append(f"{selected_ref} ability prerequisite is not met")
        if feat.get("requires_spellcasting") and not snapshot.get("slot_profile"):
            errors.append(f"{selected_ref} requires Spellcasting")

        ability_choice = feat.get("ability_choice")
        if not isinstance(ability_choice, dict):
            if raw not in (None, {}):
                errors.append(f"{selected_ref} does not grant an ability increase")
            return {}, selected_ref, errors
        if not isinstance(raw, dict):
            errors.append("ability_score_increases is required at this level")
            return {}, selected_ref, errors
        parsed: dict[str, int] = {}
        allowed = ability_choice.get("allowed")
        allowed_abilities = set(ABILITY_IDS if allowed == "any" else allowed or [])
        points = int(ability_choice.get("points", 0) or 0)
        maximum = int(ability_choice.get("maximum", 20) or 20)
        pattern = str(ability_choice.get("pattern") or "")
        for ability, increase in raw.items():
            if (
                ability not in allowed_abilities
                or isinstance(increase, bool)
                or not isinstance(increase, int)
                or increase not in ({1, 2} if pattern == "2_or_1_1" else {1})
            ):
                errors.append(
                    f"ability_score_increases are not valid for {selected_ref}"
                )
                continue
            if int(abilities.get(ability, 0) or 0) + int(increase) > maximum:
                errors.append(
                    f"ability_score_increases would raise {ability} above {maximum}"
                )
            parsed[ability] = int(increase)
        expected_lengths = {1, 2} if pattern == "2_or_1_1" else {1}
        if sum(parsed.values()) != points or len(parsed) not in expected_lengths:
            errors.append(f"ability_score_increases must total exactly {points}")
        return parsed, selected_ref, errors

    def _feat_options(
        self,
        category: str,
        character: dict[str, Any],
        target_level: int,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        abilities = character.get("abilities") or {}
        owned = list(character.get("features", {}).get("feat_refs") or [])
        result: list[dict[str, Any]] = []
        for feat_id, feat in self.advancement_feats.items():
            if feat.get("category") != category:
                continue
            unavailable_reasons: list[str] = []
            if target_level < int(feat.get("minimum_level", 1) or 1):
                unavailable_reasons.append("level")
            if f"feat:{feat_id}" in owned and not bool(feat.get("repeatable")):
                unavailable_reasons.append("already_owned")
            minimums = feat.get("any_ability_minimum")
            if isinstance(minimums, dict) and not any(
                int(abilities.get(ability, 0) or 0) >= int(minimum)
                for ability, minimum in minimums.items()
            ):
                unavailable_reasons.append("ability_prerequisite")
            if feat.get("requires_spellcasting") and not snapshot.get("slot_profile"):
                unavailable_reasons.append("spellcasting")
            result.append({
                "value": f"feat:{feat_id}",
                "name": self.advancement_feat_labels[feat_id],
                "source_ref": str(feat["source_ref"]),
                "available": not unavailable_reasons,
                "unavailable_reasons": unavailable_reasons,
                "ability_choice": deepcopy(feat.get("ability_choice") or {}),
            })
        return result

    def _ability_choice_for(self, feat_ref: str) -> dict[str, Any]:
        feat_id = str(feat_ref or "").removeprefix("feat:")
        feat = self.advancement_feats.get(feat_id) or {}
        choice = feat.get("ability_choice")
        return choice if isinstance(choice, dict) else {}

    def _subclass_name(self, subclass_id: str) -> str:
        catalog = self.bundle.get("subclass_catalog", "srd_subclasses") or {}
        labels = catalog.get("labels")
        return str(labels.get(subclass_id) if isinstance(labels, dict) else subclass_id)

    @staticmethod
    def _subclass_features(
        subclass: dict[str, Any], level: int, selected: bool,
        *, catching_up: bool = False,
    ) -> list[str]:
        """The subclass features this level newly grants.

        ``catching_up`` is a delayed first selection: the character is only now
        receiving a subclass it owed from an earlier level, so every feature at
        or below ``level`` is new to it.  Taking only ``level`` would silently
        skip the option slots the granting level used to ask for.
        """

        if not selected:
            return []
        by_level = subclass.get("feature_ids_by_level") or {}
        if catching_up:
            granted: list[str] = []
            for raw_level in sorted(by_level, key=int):
                if int(raw_level) <= level:
                    granted.extend(by_level[raw_level])
            return granted
        return list(by_level.get(str(level)) or [])

    def _update_spellcasting(self, character: dict[str, Any], preview: dict[str, Any]) -> None:
        class_entity = self.bundle.get("class", _class_id(preview["class_ref"])) or {}
        snapshot = preview["snapshot"]
        spellcasting = character.setdefault("spellcasting", {})
        if not snapshot["slot_profile"]:
            return
        previous = spellcasting.get("class")
        previous = previous if isinstance(previous, dict) else {}
        previous_current = previous.get("slots_current")
        previous_current = previous_current if isinstance(previous_current, dict) else {}
        previous_max = previous.get("slots_max")
        previous_max = previous_max if isinstance(previous_max, dict) else {}
        slot_max = deepcopy(snapshot["spell_slots"])
        slot_current = {
            level: min(
                maximum,
                int(previous_current.get(level, previous_max.get(level, 0)) or 0)
                + max(0, maximum - int(previous_max.get(level, 0) or 0)),
            )
            for level, maximum in slot_max.items()
        }
        tracks = snapshot["tracks"]
        spellcasting["class"] = {
            **deepcopy(previous),
            "class_ref": preview["class_ref"],
            "ability": class_entity.get("spellcasting_ability"),
            "slot_profile": snapshot["slot_profile"],
            "slots_current": slot_current,
            "slots_max": slot_max,
            "cantrip_capacity": tracks.get("cantrips", 0),
            "prepared_capacity": tracks.get("prepared_spells", 0),
            "cantrip_refs": deepcopy(
                preview["diff"].get("class_spell_choices", {}).get(
                    "cantrip_refs", previous.get("cantrip_refs", [])
                )
            ),
            "prepared_spell_refs": deepcopy(
                preview["diff"].get("class_spell_choices", {}).get(
                    "prepared_spell_refs", previous.get("prepared_spell_refs", [])
                )
            ),
            "spellbook_refs": deepcopy(
                preview["diff"].get("class_spell_choices", {}).get(
                    "spellbook_refs", previous.get("spellbook_refs", [])
                )
            ),
            "concentration": previous.get("concentration"),
        }

    def _update_derived(self, character: dict[str, Any]) -> None:
        level = int(character["build"]["level"])
        proficiency = 2 + (level - 1) // 4
        abilities = character["abilities"]
        proficiencies = character["proficiencies"]
        saving_refs = set(proficiencies.get("saving_throw_refs") or [])
        skill_refs = set(proficiencies.get("skill_refs") or [])
        saving_throws = {
            ability: ability_modifier(abilities[ability])
            + (proficiency if f"ability:{ability}" in saving_refs else 0)
            for ability in ABILITY_IDS
        }
        skill_values: dict[str, int] = {}
        for skill in self.bundle.list("skill"):
            ability = str(skill.get("ability_ref") or "").removeprefix("ability:")
            skill_values[str(skill["id"])] = ability_modifier(abilities[ability]) + (
                proficiency if f"skill:{skill['id']}" in skill_refs else 0
            )
        perception = skill_values.get("perception", ability_modifier(abilities["wis"]))
        derived = character.setdefault("derived", {})
        derived.update({
            "proficiency_bonus": proficiency,
            "initiative": ability_modifier(abilities["dex"])
            + (proficiency if "feat:alert" in set(character.get("features", {}).get("feat_refs") or []) else 0),
            "passive_perception": 10 + perception,
            "saving_throws": saving_throws,
        })
        proficiencies["skill_values"] = skill_values
        class_ref = str(character["build"]["class_levels"][0]["class_ref"])
        class_entity = self.bundle.get("class", _class_id(class_ref)) or {}
        spell_ability = str(class_entity.get("spellcasting_ability") or "")
        if spell_ability:
            derived["spell_attack_bonus"] = proficiency + ability_modifier(abilities[spell_ability])
            derived["spell_save_dc"] = 8 + derived["spell_attack_bonus"]
