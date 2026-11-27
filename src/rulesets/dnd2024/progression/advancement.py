"""Pure next-level planning and application for single-class D&D 2024 characters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.attunement import attuned_refs
from src.rulesets.dnd2024.character.builder import ABILITY_IDS, ability_modifier
from src.rulesets.dnd2024.forms import (
    cr_cap, eligible_forms, known_form_count, validate_selection,
)
from src.rulesets.dnd2024.character.derivation import (
    apply_feature_ability_bonuses,
    derive_armor_class,
    derive_armor_training_state,
    derive_movement_profile,
    derive_skill_check,
    derive_speed,
)
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression.catalog import (
    Dnd2024ProgressionCatalog,
    ProgressionCatalogError,
)
from src.rulesets.dnd2024.spells.metamagic import (
    MetamagicError,
    known_after_picks,
    resolve_replacement,
)
from src.rulesets.dnd2024.spells.preparation import PreparationPolicy
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from .choices import (
    AdvancementChoicesMixin,
    PLAIN_ABILITY_FEAT_REF as PLAIN_ABILITY_FEAT_REF,
    _canonical as _canonical,
    _class_id as _class_id,
)

# 专长机械参数的封闭键集：新参数随其归属切片一起扩这里，未知键 fail closed。
ADVANCEMENT_FEAT_PARAMETER_KEYS = frozenset({
    "ranged_attack_bonus",
    "light_extra_attack_ability_damage",
})
# 数值型参数与开关型参数必须分开校验：把布尔当成 1 来接受，会让
# "5" 和 "true" 在同一个字段里都能通过，语义就没法审了。
ADVANCEMENT_FEAT_BOOLEAN_PARAMETERS = frozenset({
    "light_extra_attack_ability_damage",
})
# 被动效果的封闭 kind 集：本切片只消费 armor_class_bonus，其余必须加载期拒绝。
ADVANCEMENT_FEAT_PASSIVE_EFFECT_KINDS = frozenset({"armor_class_bonus"})

# 成长账本：每级一条，记录该级实际做出的选择。缺失项检测只读它，不从
# 数值反推选择（`abilities` 已经把来源合并掉了，反推不出「这一级选了什么」）。


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


def _applied_ability_bonuses(character: dict[str, Any]) -> dict[str, int]:
    """What the ability-raising features have already given this character.

    Stored beside the other derived facts, because it *is* one: without it the
    next level-up could only add the bonus again, and a card that lost the
    feature could never give the points back.
    """

    derived = character.get("derived")
    if not isinstance(derived, dict):
        return {}
    applied = derived.get("ability_bonus_applied")
    if not isinstance(applied, dict):
        return {}
    return {
        ability: int(points)
        for ability, points in applied.items()
        if ability in ABILITY_IDS
        and isinstance(points, int)
        and not isinstance(points, bool)
        and points > 0
    }


#: 十选项的 canonical id（运行时权威在 `spells/metamagic.OPTIONS`）。
METAMAGIC_OPTION_IDS = (
    "careful_spell", "distant_spell", "empowered_spell", "extended_spell",
    "heightened_spell", "quickened_spell", "seeking_spell", "subtle_spell",
    "transmuted_spell", "twinned_spell",
)

METAMAGIC_OPTION_NAMES = {
    "careful_spell": "Careful Spell",
    "distant_spell": "Distant Spell",
    "empowered_spell": "Empowered Spell",
    "extended_spell": "Extended Spell",
    "heightened_spell": "Heightened Spell",
    "quickened_spell": "Quickened Spell",
    "seeking_spell": "Seeking Spell",
    "subtle_spell": "Subtle Spell",
    "transmuted_spell": "Transmuted Spell",
    "twinned_spell": "Twinned Spell",
}


#: 哪些 requirement 的 `id` 对应 `choices` 里的哪个键。同一形状也用于判定必填。
CHOICE_KEYS = {
    "known_form_refs": "known_form_refs",
    "metamagic_option_ids": "metamagic_option_ids",
    "feature_choice": "feature_choices",
}


def _choice_satisfied(requirement: dict[str, Any], choices: dict[str, Any]) -> bool:
    """Whether a choice slot was answered at all.

    The engine only answers "did you fill it", not "is it valid" -- validity
    belongs to each slot's own validator, which runs next to where the option
    list is built.
    """

    key = CHOICE_KEYS.get(str(requirement.get("id"))) or CHOICE_KEYS.get(
        str(requirement.get("kind"))
    )
    if key is None:
        return True
    value = choices.get(key)
    if isinstance(value, list):
        return len(value) >= int(requirement.get("count", 1) or 1)
    return value is not None


def metamagic_choices_at(class_ref: str, target_level: int) -> int:
    """How many new Metamagic options this level grants (2 at levels 2/10/17)."""

    if class_ref != "class:sorcerer":
        return 0
    return 2 if target_level in {2, 10, 17} else 0


@dataclass(slots=True)
class Dnd2024AdvancementEngine(AdvancementChoicesMixin):
    bundle: LoadedRulesetBundle
    catalog: Dnd2024ProgressionCatalog = field(init=False)
    advancement_feats: dict[str, dict[str, Any]] = field(init=False)
    advancement_feat_labels: dict[str, str] = field(init=False)
    # 同一次升级里特性解析被问多次（选项槽、生命值增量、AC 重算），共用一份。
    _feature_resolver: Dnd2024ClassFeatureResolver = field(init=False)

    def __post_init__(self) -> None:
        self._feature_resolver = Dnd2024ClassFeatureResolver(self.bundle)
        self.catalog = Dnd2024ProgressionCatalog.from_bundle(self.bundle)
        feat_catalog = self.bundle.get("advancement_feat_catalog", "srd_advancement_feats")
        if feat_catalog is None or not isinstance(feat_catalog.get("feats"), dict):
            raise ProgressionCatalogError("D&D 2024 advancement feat catalog is missing")
        labels = feat_catalog.get("labels")
        if not isinstance(labels, dict) or set(labels) != set(feat_catalog["feats"]):
            raise ProgressionCatalogError("advancement feat labels must cover the catalog")
        for feat_id, feat in feat_catalog["feats"].items():
            self._validate_feat_parameters(str(feat_id), feat)
            self._validate_feat_passive_effects(str(feat_id), feat)
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
            if key in ADVANCEMENT_FEAT_BOOLEAN_PARAMETERS:
                if not isinstance(value, bool):
                    raise ProgressionCatalogError(
                        f"advancement feat {feat_id} parameter {key} must be a boolean"
                    )
                continue
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

        The audit reads what each level answered, and the only place that is
        written down is the card's ``progression.history``: a level that chose
        nothing has an empty entry, and a card that never went through this
        application has no history at all.  There is no second ledger to
        disagree with, and one derived entry per level is the whole of it.

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
        records = self._ledger_entries(character)
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
                    "enforced": True,
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
                    "enforced": True,
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
        # 有法术书的职业才审计法术书数量；"有没有法术书"是内容说的，不是职业名字。
        policy = PreparationPolicy.for_class(self.bundle, class_ref)
        if policy is not None and policy.source == "spellbook":
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
                "enforced": True,
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
    def _ledger_entries(character: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The growth ledger, derived from the audit history.

        ``None`` and ``[]`` mean different things: ``None`` is "this card keeps
        no history, so it cannot be judged", while ``[]`` is "this card has
        history and every level of it chose nothing".  Detection must not
        confuse the two -- a legacy card reported as incomplete would be a
        false accusation, and a card that skipped a level must not hide behind
        the legacy exemption.

        The derivation is the only definition of the ledger.  Every field a
        level can answer is already inside its history entry, so a second
        stored copy could only ever disagree with this one.
        """

        progression = character.get("progression")
        if not isinstance(progression, dict):
            return None
        history = progression.get("history")
        if not isinstance(history, list):
            if "history" in progression:
                # Present and not a list is corrupt, not legacy: guessing what
                # it meant is how a broken card stays broken.
                return None
            level = progression.get("level")
            level = level if isinstance(level, int) and not isinstance(level, bool) else 0
            # A level-1 card owes nothing with or without history: there is no
            # earlier level it could have answered for.  A card above level 1
            # with no history is a legacy save, and accusing every one of them
            # of skipping a choice would be worse than not judging at all.
            return [] if level <= 1 else None
        class_ref = str(progression.get("class_ref") or "")

        entries: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            to_level = item.get("to_level")
            if isinstance(to_level, bool) or not isinstance(to_level, int):
                continue
            entry: dict[str, Any] = {"level": to_level, "class_ref": class_ref}
            choices = item.get("choices")
            choices = choices if isinstance(choices, dict) else {}
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
                    entry["ability_score_increase"] = parsed
            diff = item.get("diff")
            diff = diff if isinstance(diff, dict) else {}
            feat_ref = str(diff.get("advancement_feat_ref") or "")
            if feat_ref and feat_ref != PLAIN_ABILITY_FEAT_REF:
                gained = diff.get("gained_feature_ids") or []
                key = "epic_boon_ref" if "epic_boon" in gained else "feat_ref"
                entry[key] = feat_ref
            subclass_ref = str(choices.get("subclass_ref") or "")
            if subclass_ref:
                entry["subclass_ref"] = subclass_ref
            entries.append(entry)
        return entries

    @staticmethod
    def _recorded_a_choice(record: dict[str, Any], epic: bool) -> bool:
        key = "epic_boon_ref" if epic else "feat_ref"
        return bool(record.get("ability_score_increase") or record.get(key))
    @staticmethod
    def _validate_feat_passive_effects(feat_id: str, feat: Any) -> None:
        """Validate a feat's closed passive-effect declarations.

        The armor class formula reads these declarations instead of embedding
        the numbers, so an unknown kind or a non-integer value has to fail at
        load -- silently contributing nothing would look like a working feat.
        """

        if not isinstance(feat, dict):
            return
        effects = feat.get("passive_effects")
        if effects is None:
            return
        if not isinstance(effects, list) or not effects:
            raise ProgressionCatalogError(
                f"advancement feat {feat_id} passive_effects must be a non-empty array"
            )
        for effect in effects:
            if not isinstance(effect, dict):
                raise ProgressionCatalogError(
                    f"advancement feat {feat_id} passive effect must be an object"
                )
            kind = str(effect.get("kind") or "")
            if kind not in ADVANCEMENT_FEAT_PASSIVE_EFFECT_KINDS:
                raise ProgressionCatalogError(
                    f"advancement feat {feat_id} has an unknown passive effect: {kind!r}"
                )
            unknown = sorted(set(effect) - {"kind", "value", "requires_armor"})
            if unknown:
                raise ProgressionCatalogError(
                    f"advancement feat {feat_id} passive effect has an unknown "
                    f"field: {unknown[0]}"
                )
            value = effect.get("value")
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 10
            ):
                raise ProgressionCatalogError(
                    f"advancement feat {feat_id} passive effect value must be an "
                    "integer from 1 to 10"
                )
            if not isinstance(effect.get("requires_armor"), bool):
                raise ProgressionCatalogError(
                    f"advancement feat {feat_id} passive effect requires_armor "
                    "must be a boolean"
                )

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
                "enforced": True,
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
                    "enforced": True,
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
            # 施法选择按"升级后"的角色状态解析特性归属：Bard 9→10 的那一级
            # Magical Secrets 才算拥有，用升级前的 9 级读会永远看不到四列表。
            spell_state = deepcopy(character)
            spell_state["build"] = {
                **(character.get("build") or {}),
                "class_levels": [{"class_ref": class_ref, "level": target_level}],
            }
            spell_feature_state = spell_state.get("features")
            spell_feature_state = (
                spell_feature_state if isinstance(spell_feature_state, dict) else {}
            )
            spell_feature_state["subclass_ref"] = (
                selected_subclass_ref
                if selected_subclass_ref == expected_subclass_ref
                else str(spell_feature_state.get("subclass_ref") or "")
            )
            spell_state["features"] = spell_feature_state
            target_requirements = spell_selection.requirements(
                class_ref, target_level, spell_state,
            )
            previous_lists = {
                "cantrip_refs": current_cantrips,
                "prepared_spell_refs": current_prepared,
                "spellbook_refs": current_spellbook,
            }
            policy = PreparationPolicy.for_class(self.bundle, class_ref)
            # 法术书每级固定加 N 个。只要策略声明了增量，这一级就必须提交
            # 一份书目：旧书恰好等于下一级的下限，只能说明以前的账平了，
            # 不能说明本级的 N 个已经选过。宁可多要一次输入，也不放过一条
            # 靠"总数刚好对得上"通过的空升级。
            spellbook_growth = (
                int(policy.spellbook_gain_per_level or 0)
                if policy is not None and policy.source == "spellbook"
                else 0
            )
            # 免费入书的授予（Evocation Savant）走同一套授予槽位：书目的正常
            # 增量仍是 N，多出来的那几个由 `spell_grants` 单独提交，校验侧用
            # 同一份声明判断"多出来的必须正好是授予项"。
            open_slots = [
                slot for slot in spell_selection.grant_slots(target_level, spell_state)
                if len(slot["selected_refs"]) < int(slot["count"])
            ]
            # 免费施法槽同样"拥有但未选"时要把整块标成 required：11 级的
            # 秘奥与 18 级的法术精通都必须在这一级做出选择，但它们未提交
            # 时不算非法（见 `_validate_free_cast`）。
            open_free = [
                slot
                for slot in spell_selection.free_cast_slots(target_level, spell_state)
                if slot["source"] != "fixed" and not slot["selected"]
            ]
            spell_choice_required = (
                len(current_cantrips) != target_requirements["cantrip_count"]
                or len(current_prepared) != target_requirements["prepared_spell_count"]
                or spellbook_growth > 0
                or bool(open_slots)
                or bool(open_free)
            )
            raw_spell_choices = choices.get("class_spell_choices")
            if spell_choice_required or raw_spell_choices is not None:
                spell_options = spell_selection.options(
                    class_ref, target_level, spell_state,
                )
                requirements.append({
                    "id": "class_spell_choices",
                    "kind": "spell_selection",
                    "enforced": True,
                    "required": spell_choice_required,
                    **target_requirements,
                    # 三个上限交给客户端做即时提示与禁用，出提交前就能看出
                    # 超额；服务端仍然逐条校验，二者用同一份策略数据。
                    "additions": max(
                        0,
                        int(target_requirements["prepared_spell_count"])
                        - len(current_prepared),
                    ),
                    "replace_limit": policy.level_up_replace if policy else 0,
                    "cantrip_replace_limit": (
                        1 if policy and policy.cantrip_swap_on == "level_up" else 0
                    ),
                    "spellbook_additions": spellbook_growth,
                    "cantrips": spell_options["cantrips"],
                    "leveled_spells": spell_options["leveled_spells"],
                    "grant_slots": spell_options["grant_slots"],
                    "free_cast_slots": spell_options["free_cast_slots"],
                })
                spell_choices, spell_errors = spell_selection.validate(
                    class_ref, target_level, raw_spell_choices,
                    previous=previous_lists,
                    character=spell_state,
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
        # 属性提升型特性（Primal Champion / Body and Mind）在这一级发放：
        # 用"目标等级"的特性状态取授予，再把上一次已发放的点数换成这一次的，
        # 所以同一张卡反复升级不会叠加 +4，ASI 自己的增长也不会被吞掉。
        # 必须在 Con 参与 HP 之前完成——否则 +4 体质会漏掉回溯的生命值。
        current_subclass_ref = str(feature_state.get("subclass_ref") or "")
        next_subclass_ref = (
            selected_subclass_ref
            if selected_subclass_ref == expected_subclass_ref
            else current_subclass_ref
        )
        next_feature_state = _feature_state(class_ref, target_level, next_subclass_ref)
        feature_ability_grants = self._feature_resolver.ability_bonuses(next_feature_state)
        applied_bonuses = _applied_ability_bonuses(character)
        if feature_ability_grants or applied_bonuses:
            new_abilities, ability_bonus_applied = apply_feature_ability_bonuses(
                new_abilities, feature_ability_grants, applied_bonuses,
            )
        else:
            ability_bonus_applied = {}
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
        features = self._feature_resolver
        feature_hp_before = features.feature_max_hp_bonus(
            _feature_state(class_ref, current_level, current_subclass_ref)
        )
        feature_hp_after = features.feature_max_hp_bonus(
            _feature_state(class_ref, target_level, next_subclass_ref)
        )
        feature_hp_gain = feature_hp_after - feature_hp_before
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
        choice_requirements, choice_records, choice_errors = self._plan_feature_choices(
            character, class_ref, target_level, gained_feature_ids, after, choices,
        )
        errors.extend(choice_errors)
        requirements.extend(choice_requirements)
        # S0-F25：Wild Shape 在 2 级开始，同一级必须选满已知形态。选择本身走
        # 通用 `choices`，不新开一条升级通道。
        form_requirements: dict[str, Any] = {}
        if class_ref == "class:druid" and target_level >= 2:
            entries = [
                entry for catalog in self.bundle.list("monster_catalog")
                for entry in catalog.get("entries") or []
            ]
            # 选择是**可选的一步**：没提交就不拦升级（角色会走推荐形态兜底），
            # 提交了就必须恰好 N 个且合法——否则"选了 3 个"会被静默接受。
            if "known_form_refs" in choices:
                errors.extend(validate_selection(
                    choices.get("known_form_refs"),
                    target_level, entries,
                ))
            count = known_form_count(target_level)
            options = eligible_forms(entries, target_level)
            form_requirements = {
                "known_form_count": count,
                "cr_cap": cr_cap(target_level),
                "known_form_options": options,
            }
            if count:
                # 与其它槽位同构：前端不需要为形态单开一套渲染。
                requirements.append({
                    "id": "known_form_refs",
                    "kind": "known_forms",
                    "count": count,
                    "cr_cap": cr_cap(target_level),
                    # 与其它槽位同构：{value, name}，前端不必解释两种形状。
                    "options": [
                        {"value": row["ref"], "name": row["name"], "cr": row["cr"]}
                        for row in options
                    ],
                })
        # 通用收口：带 `count` 的选择槽默认**必填**。特性只需声明"有哪些槽"，
        # 不必各自再回答一次"不选怎么办"——漏选就让特性永久丢失，这个判断属于
        # 升级本身，而不是每一条特性的实现细节。
        for requirement in requirements:
            if not requirement.get("count") or requirement.get("optional"):
                continue
            if requirement.get("enforced"):
                # 已经有自己的 planner 在判（专长、技能、法术换选等），通用规则
                # 不再重复一遍，避免同一件事出现两种错误文案。
                continue
            if not _choice_satisfied(requirement, choices):
                message = f"{requirement['id']} is required at this level"
                if message not in errors:
                    errors.append(message)
        # S0-F28：超魔选项在 2/10/17 级各选两个；任意升级可换掉一个。
        metamagic_count = metamagic_choices_at(class_ref, target_level)
        metamagic_block = (character.get("features") or {}).get("metamagic", {}) or {}
        metamagic_known = [str(item) for item in metamagic_block.get("known") or []]
        metamagic_picked: list[str] = []
        if metamagic_count:
            picked = choices.get("metamagic_option_ids")
            if picked is None:
                # 与仓库既有原则一致：可选槽位是升级的**必要条件**，不选就
                # 不能确认升级。放过它等于让这个特性永远丢失——2/10/17 每级
                # 只给一次，错过不会补。
                errors.append("metamagic_option_ids is required at this level")
            else:
                metamagic_picked = [str(item) for item in picked]
                if len(metamagic_picked) != metamagic_count:
                    errors.append(
                        f"metamagic_option_ids must list exactly {metamagic_count} options"
                    )
                unknown = [
                    item for item in metamagic_picked
                    if item not in METAMAGIC_OPTION_IDS
                ]
                if unknown:
                    errors.append(f"unknown metamagic option: {unknown[0]}")
                duplicated = [
                    item for item in set(metamagic_picked)
                    if metamagic_picked.count(item) > 1
                ]
                if duplicated:
                    errors.append(f"metamagic option chosen twice: {duplicated[0]}")
                repeated = [item for item in metamagic_picked if item in metamagic_known]
                if repeated:
                    errors.append(f"metamagic option already known: {repeated[0]}")
            requirements.append({
                "id": "metamagic_option_ids",
                "kind": "metamagic",
                "count": metamagic_count,
                # 这个槽自己判"少选"：通用必填只看"填没填"，玩家选了 1 个
                # 会得到"required"而不是"必须恰好 2 个"，文案会指错地方。
                "enforced": True,
                "options": [
                    {"value": option_id,
                     "name": METAMAGIC_OPTION_NAMES.get(option_id, option_id)}
                    for option_id in METAMAGIC_OPTION_IDS
                    if option_id not in metamagic_known
                ],
            })
        if class_ref == "class:sorcerer":
            replacement = choices.get("metamagic_replace")
            if isinstance(replacement, dict):
                try:
                    resolve_replacement(
                        known=metamagic_known,
                        picked=metamagic_picked,
                        drop=str(replacement.get("drop") or ""),
                        add=str(replacement.get("add") or ""),
                    )
                except MetamagicError as error:
                    errors.append(str(error))
            # 替换槽按"本批次已写入"后的已知集合算候选：先选后换与先换后
            # 选必须给出同一组下拉，否则预览刷新一次换一批选项。
            metamagic_known_after = known_after_picks(
                metamagic_known, metamagic_picked,
            )
            if metamagic_known:
                requirements.append({
                    "id": "metamagic_replace",
                    "kind": "metamagic_replace",
                    "optional": True,
                    "known": metamagic_known_after,
                    "options": [
                        {"value": option_id,
                         "name": METAMAGIC_OPTION_NAMES.get(option_id, option_id)}
                        for option_id in METAMAGIC_OPTION_IDS
                        if option_id not in metamagic_known_after
                    ],
                })
        return {
            "ok": not errors,
            "form_requirements": form_requirements,
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
                "feature_choices": choice_records,
                "feature_choice_refs": [
                    ref for record in choice_records for ref in record["selected_refs"]
                ],
                "abilities": new_abilities,
                "ability_bonus_applied": ability_bonus_applied,
                "class_spell_choices": spell_choices,
                "hp": {
                    "method": hp_method,
                    "base": hp_base,
                    "constitution_modifier": new_con,
                    "retrospective_constitution": retrospective_con_hp,
                    # 特性生命上限加成分三段记：升级前的累计、升级后的累计、本次
                    # 只加多少。审计靠它区分「+3 首次发放」与「每级 +1」。
                    "feature_bonus_before": feature_hp_before,
                    "feature_bonus_after": feature_hp_after,
                    "feature_bonus_gain": feature_hp_gain,
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
        # 记录"这几点是特性给的"，下一次升级据此净替换而不是再加一次。
        derived = character.setdefault("derived", {})
        if isinstance(derived, dict):
            applied = preview["diff"].get("ability_bonus_applied") or {}
            if applied:
                derived["ability_bonus_applied"] = deepcopy(applied)
            else:
                derived.pop("ability_bonus_applied", None)

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

        # S0-F25：升级时选好的已知形态写进角色级状态（跨变形、跨战斗存在）。
        known_forms = preview.get("choices", {}).get("known_form_refs") or []
        if known_forms:
            character.setdefault("forms", {}).setdefault("known", {})["wild_shape"] = [
                str(ref) for ref in known_forms
            ]

        # S0-F28：超魔选项是角色级选择，跨战斗与长休存在。
        picked_metamagic = preview.get("choices", {}).get("metamagic_option_ids")
        if picked_metamagic:
            block = character.setdefault("features", {}).setdefault("metamagic", {})
            block["known"] = known_after_picks(
                block.get("known") or [], [str(item) for item in picked_metamagic],
            )
        replacement = preview.get("choices", {}).get("metamagic_replace")
        if isinstance(replacement, dict):
            block = character.setdefault("features", {}).setdefault("metamagic", {})
            # 与预览走同一个函数：预览允许的替换，写入必须得到同一结果。
            block["known"] = resolve_replacement(
                known=block.get("known") or [],
                picked=(),
                drop=str(replacement.get("drop") or ""),
                add=str(replacement.get("add") or ""),
            )

        features = character.setdefault("features", {})
        subclass_ref = str(preview["choices"].get("subclass_ref") or "")
        if subclass_ref:
            features["subclass_ref"] = subclass_ref
        advancement_feat_ref = str(preview["diff"].get("advancement_feat_ref") or "")
        if advancement_feat_ref:
            features.setdefault("feat_refs", []).append(advancement_feat_ref)
        choice_records = preview["diff"].get("feature_choices") or []
        if choice_records:
            # 选项槽与专长所有权分开保存：前者记录玩家选了什么、哪一级选的，
            # 后者才是既有专长前提与被动效果读取的所有权来源。技能选择只写
            # 槽，不写 feat_refs——否则 "skill:stealth" 会被当成一个专长。
            features.setdefault("feature_choices", []).extend(deepcopy(choice_records))
            owned_refs = features.setdefault("feat_refs", [])
            for record in choice_records:
                for ref in record["selected_refs"]:
                    if not str(ref).startswith("feat:"):
                        continue
                    if ref not in owned_refs:
                        owned_refs.append(ref)
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
        history = progression.setdefault("history", [])
        if not isinstance(history, list):
            raise ProgressionCatalogError("progression.history must be a list")
        previous_level = 0
        if history:
            last = history[-1]
            if not isinstance(last, dict):
                raise ProgressionCatalogError(
                    "progression.history entries must be objects"
                )
            previous_level = int(last.get("to_level", 0) or 0)
        if previous_level >= target_level:
            # Levels advance one at a time, so a non-advancing append means two
            # writers disagreed about the current level.  Replay enforces the
            # sequence by construction; this defends a direct caller.
            raise ProgressionCatalogError(
                "progression.history must advance one level at a time: "
                f"{previous_level} is not below {target_level}"
            )
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
        next_magic = {
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
        grant_choices = preview["diff"].get("class_spell_choices", {}).get(
            "spell_grant_choices"
        )
        if isinstance(grant_choices, dict) and grant_choices:
            # 授予槽位与地形选择都是玩家选择：升级只合并本次提交的槽位，
            # 不能把 Land 已经选好的地形写丢。
            merged = deepcopy(previous.get("spell_grant_choices") or {})
            merged.update(deepcopy(grant_choices))
            next_magic["spell_grant_choices"] = merged
        free_cast = preview["diff"].get("class_spell_choices", {}).get(
            "free_cast_slots"
        )
        if isinstance(free_cast, dict) and free_cast:
            # 免费施法槽的记录同时装着"选了哪个法术"和"还剩几次"：这里的
            # 合并只覆盖本次提交的槽位，替换 arcanum 不会顺手把已消耗的
            # 次数刷回满值（`_free_cast_record` 保留原计数）。
            merged_free = deepcopy(previous.get("free_cast_slots") or {})
            merged_free.update(deepcopy(free_cast))
            next_magic["free_cast_slots"] = merged_free
        spellcasting["class"] = next_magic

    def _update_derived(self, character: dict[str, Any]) -> None:
        level = int(character["build"]["level"])
        proficiency = 2 + (level - 1) // 4
        abilities = character["abilities"]
        proficiencies = character["proficiencies"]
        saving_refs = set(proficiencies.get("saving_throw_refs") or [])
        if self._feature_resolver.saving_throw_proficiency_all(character):
            # 全豁免熟练是升级时重建的派生事实：写回 ref 集合让同一张卡在
            # 升级前后只走同一个加值公式，集合语义顺带保证了不重复叠加。
            saving_refs |= {f"ability:{ability}" for ability in ABILITY_IDS}
            proficiencies["saving_throw_refs"] = sorted(saving_refs)
        # 单项豁免熟练（Slippery Mind）合并进同一个集合：已经有这条 ref 时
        # 什么都不会变，所以职业自带熟练与特性授予的熟练都只加一次 PB。
        specific_saves = self._feature_resolver.saving_throw_proficiency_sources(
            character
        )
        if specific_saves:
            saving_refs |= {f"ability:{ability}" for ability in specific_saves}
            proficiencies["saving_throw_refs"] = sorted(saving_refs)
            derived = character.setdefault("derived", {})
            if isinstance(derived, dict):
                # 来源单独记一笔：集合本身只说明"有这个熟练"，看不出谁给的。
                derived["saving_throw_proficiency_sources"] = dict(specific_saves)
        skill_refs = set(proficiencies.get("skill_refs") or [])
        # `grants: "proficiency"` 的槽选中的技能必须真的变成熟练，否则只是
        # 一个记下来的名字、加值是 0。（建卡路径在 derivation 里做同一件事。）
        for record in (character.get("features") or {}).get("feature_choices") or []:
            if record.get("grants") != "proficiency":
                continue
            for ref in record.get("selected_refs") or []:
                if str(ref).startswith("skill:"):
                    skill_refs.add(str(ref))
        saving_throws = {
            ability: ability_modifier(abilities[ability])
            + (proficiency if f"ability:{ability}" in saving_refs else 0)
            for ability in ABILITY_IDS
        }
        skill_values: dict[str, int] = {}
        # 技能加值只有一个聚合入口：升级、建卡与 live 重算都走它。Expertise
        # 与半熟练若在这里另写一套，同一张卡在升级前后会出现两种加值。
        expertise = {
            row["skill_ref"] for row in self._feature_resolver.expertise_skills(character)
        }
        half_proficiency = self._feature_resolver.half_proficiency_bonus(character)
        for skill in self.bundle.list("skill"):
            ability = str(skill.get("ability_ref") or "").removeprefix("ability:")
            ref = f"skill:{skill['id']}"
            skill_values[str(skill["id"])] = derive_skill_check(
                int(abilities[ability]),
                proficiency_bonus=proficiency,
                proficient=ref in skill_refs,
                expertise=ref in expertise,
                half_proficiency=half_proficiency,
            )["total"]
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
        # 升级当次就要生效：Defense 这类被动 AC 加值不能等下一次换装。
        # 复用建卡与 live 协调共用的唯一公式，装备取 canonical 已装备列表。
        equipment = character.get("equipment")
        equipment = equipment if isinstance(equipment, dict) else {}
        equipped_refs = [str(ref) for ref in (equipment.get("item_refs") or [])]
        armor_category_refs = [
            str(ref) for ref in (proficiencies.get("armor_category_refs") or [])
        ]
        feat_state = character.get("features")
        feat_state = feat_state if isinstance(feat_state, dict) else {}
        owned_refs = feat_state.get("feat_refs")
        derived["armor_class"] = derive_armor_class(
            abilities, equipped_refs, self.bundle,
            armor_category_refs=armor_category_refs,
            unarmored_ability_bonuses=self._feature_resolver.unarmored_ac_bonus_abilities(
                character
            ),
            attuned_refs=attuned_refs(character),
            feat_refs=owned_refs if isinstance(owned_refs, list) else (),
        )
        # 速度与 AC 同源：升级当次就按当前装备与等级重算，不等下一次换装。
        # 力量不足的 -10 也在这里重新判，因为 abilities 可能刚被 ASI 改过。
        armor_training = derive_armor_training_state(
            abilities, equipped_refs, armor_category_refs, self.bundle,
        )
        derived["armor_training"] = armor_training
        species_ref = str(
            (character.get("sources") or {}).get("species_ref") or ""
        ).removeprefix("species:")
        species = self.bundle.get("species", species_ref) if species_ref else None
        base_speed = int((species or {}).get("speed", 0) or 0)
        if base_speed > 0:
            derived["speed"] = derive_speed(
                base_speed,
                feature_bonus=self._feature_resolver.speed_bonus(character),
                armor_training=armor_training,
            )
            # 攀爬/游泳速度与跳跃距离都挂在当前 Speed 上，升级当次就要跟着变，
            # 否则升到 Ranger 6 拿到 Roving 之后，爬速要等下一次换装才出现。
            derived.update(derive_movement_profile(
                derived["speed"], abilities, self._feature_resolver, character,
            ))
        class_ref = str(character["build"]["class_levels"][0]["class_ref"])
        class_entity = self.bundle.get("class", _class_id(class_ref)) or {}
        spell_ability = str(class_entity.get("spellcasting_ability") or "")
        if spell_ability:
            derived["spell_attack_bonus"] = proficiency + ability_modifier(abilities[spell_ability])
            derived["spell_save_dc"] = 8 + derived["spell_attack_bonus"]
