"""Derivation stage for deterministic D&D 2024 characters."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.binding import rule_binding
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver

from .primitives import ABILITY_IDS, ability_modifier, proficiency_bonus, ref_id as _ref_id


def _feature_proxy(class_ref: str, level: int) -> dict[str, Any]:
    """The minimal canonical shape ``features_for`` needs to resolve ownership."""

    return {"build": {"class_levels": [{"class_ref": class_ref, "level": level}]}}


def derive_armor_class(
    abilities: Mapping[str, int],
    equipped_item_refs: Iterable[str],
    bundle: Any,
    *,
    armor_category_refs: Collection[str],
    warnings: list[str] | None = None,
    unarmored_ability_bonuses: Iterable[str] = (),
) -> int:
    """The one and only D&D 2024 armor class formula.

    Character creation and live equipment reconciliation both call this, so the
    rule exists exactly once.  Every number comes from the ruleset bundle's own
    item definitions -- never from an LLM, the frontend, the generic layer, or a
    localized display name.

    The result is independent of the order ``equipped_item_refs`` arrives in.
    Abnormal live state is contained rather than amplified: several armors
    resolve to the single best one instead of stacking their bases, only one
    shield bonus is ever applied, and an item the bundle does not define is
    skipped with a warning instead of guessing at a lookalike.

    ``unarmored_ability_bonuses`` names the abilities that a feature may add to
    an *unarmored* base AC.  "While you aren't wearing armor" is a hard
    precondition: with any armor present the plain ``10 + Dexterity`` baseline
    is used, so a feature formula never competes with the armor's own AC.  Each
    named ability is an alternative candidate, and the best candidate wins --
    ability modifiers are never summed.
    """

    dex_modifier = ability_modifier(int(abilities["dex"]))
    unarmored = 10 + dex_modifier
    best_armor: int | None = None
    shield_bonus = 0
    for raw_ref in equipped_item_refs:
        ref = str(raw_ref or "")
        item = bundle.get("item", _ref_id(ref, "item"))
        if item is None:
            if warnings is not None:
                warnings.append(f"unknown item reference ignored by armor class: {ref}")
            continue
        item_type = item.get("item_type")
        if item_type == "armor":
            dex_bonus = 0
            if item.get("use_dex", True):
                dex_bonus = dex_modifier
                dex_cap = item.get("dex_cap")
                if dex_cap is not None:
                    dex_bonus = min(dex_bonus, int(dex_cap))
            candidate = int(item.get("ac_base", 10)) + dex_bonus
            if best_armor is None:
                best_armor = candidate
            else:
                # 同时穿着多件盔甲是非法 live state：取一件合法的，绝不相加。
                if warnings is not None:
                    warnings.append(f"extra armor ignored by armor class: {ref}")
                best_armor = max(best_armor, candidate)
        elif item_type == "shield":
            # 盾牌必须先受训，否则它不给护甲等级加值（Armor Training 条款）。
            if "armor_category:shield" not in armor_category_refs:
                continue
            bonus = int(item.get("ac_bonus", 0) or 0)
            if shield_bonus:
                # 同理：两面盾不能 +2 +2。
                if warnings is not None:
                    warnings.append(f"extra shield ignored by armor class: {ref}")
                continue
            shield_bonus = bonus
    if best_armor is None:
        for ability in unarmored_ability_bonuses:
            unarmored = max(
                unarmored,
                10 + dex_modifier + ability_modifier(int(abilities[str(ability)])),
            )
    return max(unarmored, best_armor if best_armor is not None else unarmored) + shield_bonus


_ARMOR_CATEGORIES = frozenset({"light", "medium", "heavy"})


def derive_armor_training_state(
    abilities: Mapping[str, int],
    equipped_item_refs: Iterable[str],
    armor_category_refs: Collection[str],
    bundle: Any,
) -> dict[str, Any]:
    """Armor-training consequences of the armor the character is wearing.

    The 2024 rules gate three separate facts on worn armor: an untrained light,
    medium, or heavy armor imposes disadvantage on Strength/Dexterity D20 tests
    and blocks spellcasting; a worn armor whose Strength requirement the
    character does not meet reduces Speed by 10 feet; and an armor with a
    Stealth penalty imposes disadvantage on Dexterity (Stealth) checks.  The
    numbers all come from the item definitions, never from a guessed category.

    A missing or unknown ``category`` on an armor item is unusable directory
    data: the function raises instead of silently treating the armor as trained.
    Shields carry no category and never participate; an item the bundle does
    not define is skipped the same way the armor-class formula skips it.
    """

    strength = int(abilities["str"])
    untrained = False
    speed_penalty = 0
    stealth_disadvantage = False
    for raw_ref in equipped_item_refs:
        ref = str(raw_ref or "")
        item = bundle.get("item", _ref_id(ref, "item"))
        if item is None or item.get("item_type") != "armor":
            continue
        category = item.get("category")
        if not isinstance(category, str) or category not in _ARMOR_CATEGORIES:
            raise ValueError(
                f"armor item {ref!r} has an invalid category: {category!r}"
            )
        if f"armor_category:{category}" not in armor_category_refs:
            untrained = True
        minimum = item.get("strength_minimum")
        if minimum is not None and int(minimum) > strength:
            speed_penalty = 10
        if bool(item.get("stealth_disadvantage")):
            stealth_disadvantage = True
    return {
        "untrained_armor_worn": untrained,
        "speed_penalty": speed_penalty,
        "stealth_disadvantage": stealth_disadvantage,
    }


class CharacterDerivationMixin:
    """Derive canonical mechanics only after the validation stage succeeds."""

    __slots__ = ()

    def derive(self, draft: dict[str, Any]) -> dict[str, Any]:
        errors = self.validate(draft)
        if errors:
            raise ValueError("; ".join(errors))

        level = int(draft.get("level", 1))
        prof = proficiency_bonus(level)
        class_entity = self._required_entity(draft["class_ref"], "class")
        species = self._required_entity(draft["species_ref"], "species")
        background = self._required_entity(draft["background_ref"], "background")
        package = self._required_entity(draft["equipment_package_ref"], "equipment_package")
        background_package_ref = str(draft.get("background_equipment_package_ref") or "")
        background_package = (
            self._required_entity(background_package_ref, "equipment_package")
            if background_package_ref else None
        )
        base = draft["base_abilities"]
        bonuses = draft["background_ability_bonuses"]
        abilities = {key: int(base[key]) + int(bonuses.get(key, 0) or 0) for key in ABILITY_IDS}

        background_feat_refs = [str(background["feat_ref"])] if background.get("feat_ref") else []
        species_feat_refs = list(draft.get("species_feat_refs") or [])
        class_feature_feat_choices = deepcopy(draft.get("class_feature_feat_choices") or {})
        class_feature_feat_refs = (
            [str(class_feature_feat_choices["fighting_style"])]
            if "fighting_style" in set(class_entity.get("level_1_feature_ids") or [])
            and isinstance(class_feature_feat_choices.get("fighting_style"), str)
            else []
        )
        feat_refs = list(dict.fromkeys([
            *background_feat_refs, *species_feat_refs, *class_feature_feat_refs,
        ]))
        feat_choice_answers = deepcopy(draft.get("feat_choice_answers") or {})
        skilled_refs = list(
            (feat_choice_answers.get("feat:skilled") or {}).get("proficiencies") or []
        )
        skilled_skill_refs = [ref for ref in skilled_refs if str(ref).startswith("skill:")]
        skilled_tool_refs = [ref for ref in skilled_refs if str(ref).startswith("tool:")]

        class_skills = list(draft["class_skill_refs"])
        background_skills = list(background.get("skill_refs") or [])
        species_skills = list(draft.get("species_skill_refs") or [])
        skill_refs = list(dict.fromkeys([
            *background_skills, *class_skills, *species_skills, *skilled_skill_refs,
        ]))
        skill_values: dict[str, int] = {}
        all_skills = self.bundle.list("skill")
        for skill in all_skills:
            ref = f"skill:{skill['id']}"
            ability_id = _ref_id(str(skill["ability_ref"]), "ability")
            skill_values[str(skill["id"])] = (
                ability_modifier(abilities[ability_id]) + (prof if ref in skill_refs else 0)
            )

        save_refs = list(class_entity.get("saving_throw_refs") or [])
        saving_throws = {
            ability_id: ability_modifier(abilities[ability_id])
            + (prof if f"ability:{ability_id}" in save_refs else 0)
            for ability_id in ABILITY_IDS
        }
        selected_packages = [package, *([background_package] if background_package else [])]
        item_refs = list(dict.fromkeys(
            ref
            for selected_package in selected_packages
            for ref in (selected_package.get("item_refs") or [])
        ))
        item_grants: list[dict[str, Any]] = []
        for selected_package in selected_packages:
            grants = selected_package.get("item_grants")
            if isinstance(grants, list):
                item_grants.extend(deepcopy(grants))
            else:
                item_grants.extend(
                    {"item_ref": ref, "quantity": 1}
                    for ref in (selected_package.get("item_refs") or [])
                )
        for ref in item_refs:
            # 建卡阶段仍要求装备包里每个引用都真实存在（validation 已保证）。
            self._required_entity(ref, "item")
        features = Dnd2024ClassFeatureResolver(self.bundle)
        feature_proxy = _feature_proxy(draft["class_ref"], level)
        armor_category_refs = [
            f"armor_category:{item}"
            for item in class_entity.get("armor_proficiencies", [])
        ]
        armor_class = derive_armor_class(
            abilities, item_refs, self.bundle,
            armor_category_refs=armor_category_refs,
            unarmored_ability_bonuses=features.unarmored_ac_bonus_abilities(
                feature_proxy
            ),
        )
        armor_training = derive_armor_training_state(
            abilities, item_refs, armor_category_refs, self.bundle,
        )

        hit_die = int(class_entity["hit_die"])
        con_modifier = ability_modifier(abilities["con"])
        fixed_hp = int(class_entity.get("fixed_hp_per_level", max(1, hit_die // 2 + 1)))
        species_hp_bonus = int(species.get("hp_per_level_bonus", 0) or 0)
        max_hp = max(1, hit_die + con_modifier)
        max_hp += max(0, level - 1) * max(1, fixed_hp + con_modifier)
        max_hp += species_hp_bonus * level
        max_hp += features.feature_max_hp_bonus(feature_proxy)
        passive_perception = 10 + ability_modifier(abilities["wis"])
        if "skill:perception" in skill_refs:
            passive_perception += prof

        initiative = ability_modifier(abilities["dex"])
        if "feat:alert" in feat_refs:
            initiative += prof
        size_options = list(species.get("size_options") or [])
        selected_size = str(draft.get("species_size") or (size_options[0] if size_options else "medium"))
        language_refs = list(draft.get("language_refs") or [])
        background_tool_refs = list(background.get("tool_proficiency_refs") or [])
        class_tool_refs = [
            *list(class_entity.get("tool_proficiency_refs") or []),
            *list(draft.get("class_tool_refs") or []),
        ]
        tool_refs = list(dict.fromkeys([
            *background_tool_refs, *class_tool_refs, *skilled_tool_refs,
        ]))

        magic_initiate = []
        for feat_ref in feat_refs:
            feat = self.bundle.get("feat", _ref_id(feat_ref, "feat")) or {}
            if not feat.get("spell_list"):
                continue
            answers = feat_choice_answers.get(feat_ref) or {}
            magic_initiate.append({
                "feat_ref": feat_ref,
                "spell_list": str(feat["spell_list"]),
                "spellcasting_ability": (answers.get("spellcasting_ability") or [""])[0],
                "cantrip_ids": list(answers.get("cantrips") or []),
                "level_1_spell_id": (answers.get("level_1_spell") or [""])[0],
                "source_ref": str(feat.get("source_ref") or ""),
            })

        grants_with_sources = [
            *(
                {"kind": "skill_proficiency", "ref": ref, "source_ref": draft["background_ref"]}
                for ref in background_skills
            ),
            *(
                {"kind": "skill_proficiency", "ref": ref, "source_ref": draft["class_ref"]}
                for ref in class_skills
            ),
            *(
                {"kind": "skill_proficiency", "ref": ref, "source_ref": draft["species_ref"]}
                for ref in species_skills
            ),
            *(
                {"kind": "saving_throw_proficiency", "ref": ref, "source_ref": draft["class_ref"]}
                for ref in save_refs
            ),
            *(
                {"kind": "tool_proficiency", "ref": ref, "source_ref": draft["background_ref"]}
                for ref in background_tool_refs
            ),
            *(
                {"kind": "tool_proficiency", "ref": ref, "source_ref": draft["class_ref"]}
                for ref in class_tool_refs
            ),
            *(
                {"kind": "feat", "ref": ref, "source_ref": draft["background_ref"]}
                for ref in background_feat_refs
            ),
            *(
                {"kind": "feat", "ref": ref, "source_ref": draft["species_ref"]}
                for ref in species_feat_refs
            ),
            *(
                {
                    "kind": "skill_proficiency" if ref.startswith("skill:") else "tool_proficiency",
                    "ref": ref,
                    "source_ref": "feat:skilled",
                }
                for ref in skilled_refs
            ),
        ]

        return {
            "rule_binding": rule_binding(self.bundle),
            "locale": self.bundle.locale,
            "identity": {
                "name": str(draft["name"]).strip(),
                "species_ref": draft["species_ref"],
                "background_ref": draft["background_ref"],
                "size": selected_size,
                "alignment": str(draft.get("alignment") or "neutral"),
            },
            "build": {
                "level": level,
                "class_levels": [{"class_ref": draft["class_ref"], "level": level}],
                "ability_method": draft.get("ability_method", "standard_array"),
                "base_abilities": deepcopy(base),
                "background_ability_bonuses": deepcopy(bonuses),
                "class_skill_refs": class_skills,
                "equipment_package_ref": draft["equipment_package_ref"],
                "background_equipment_package_ref": background_package_ref,
                "species_choice_answers": deepcopy(draft.get("species_choice_answers") or {}),
                "species_skill_refs": species_skills,
                "species_feat_refs": species_feat_refs,
                "class_feature_feat_choices": class_feature_feat_choices,
                "feat_choice_answers": feat_choice_answers,
                "class_tool_refs": list(draft.get("class_tool_refs") or []),
                "language_refs": language_refs,
                "grants_with_sources": grants_with_sources,
            },
            "abilities": abilities,
            "proficiencies": {
                "saving_throw_refs": save_refs,
                "armor_category_refs": armor_category_refs,
                "weapon_category_refs": [
                    f"weapon_category:{item}"
                    for item in class_entity.get("weapon_proficiencies", [])
                ],
                "skill_refs": skill_refs,
                "skill_values": skill_values,
                "tool_refs": tool_refs,
                "language_refs": language_refs,
            },
            "resources": {
                "hp": max_hp,
                "max_hp": max_hp,
                "hit_dice": {f"d{hit_die}": level},
            },
            "equipment": {
                "item_refs": item_refs,
                "item_grants": item_grants,
                "coins_gp": sum(int(item.get("coins_gp", 0) or 0) for item in selected_packages),
            },
            "features": {
                "feat_refs": feat_refs,
                "feat_choice_answers": feat_choice_answers,
                "species_traits": deepcopy(species.get("traits") or []),
            },
            "spellcasting": {"magic_initiate": magic_initiate},
            "derived": {
                "proficiency_bonus": prof,
                "armor_class": armor_class,
                "initiative": initiative,
                "speed": int(species["speed"]),
                "armor_training": armor_training,
                "passive_perception": passive_perception,
                "saving_throws": saving_throws,
            },
            "sources": {
                "class_ref": draft["class_ref"],
                "species_ref": draft["species_ref"],
                "background_ref": draft["background_ref"],
                "content_entities": [
                    draft["class_ref"], draft["species_ref"], draft["background_ref"],
                    *skill_refs, *tool_refs, *feat_refs, *item_refs,
                ],
            },
        }
