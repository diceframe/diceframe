"""Validation stage for deterministic D&D 2024 character drafts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.features import load_feat_definitions
from src.rulesets.dnd2024.features.feat_requirements import (
    FeatContext,
    feat_unavailable_reasons,
)

from .primitives import (
    ABILITY_IDS,
    ALIGNMENTS,
    POINT_BUY_COSTS,
    STANDARD_ARRAY,
    ref_id as _ref_id,
)


class CharacterValidationMixin:
    """Validate user choices without deriving or persisting a character."""

    __slots__ = ()

    def validate(self, draft: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        name = str(draft.get("name") or "").strip()
        if not name:
            errors.append("character name is required")
        elif len(name) > 100:
            errors.append("character name must not exceed 100 characters")

        level = draft.get("level", 1)
        if isinstance(level, bool) or not isinstance(level, int) or level != 1:
            errors.append("character creation currently supports level 1; use level-up for progression")

        base = draft.get("base_abilities")
        if not isinstance(base, dict):
            errors.append("base_abilities must be an object")
            base = {}
        base_values_valid = all(
            not isinstance(value, bool) and isinstance(value, int) and 3 <= value <= 20
            for value in base.values()
        )
        if set(base) != set(ABILITY_IDS):
            errors.append("base_abilities must contain exactly str, dex, con, int, wis, cha")
        ability_method = str(draft.get("ability_method") or "standard_array")
        if ability_method not in {"standard_array", "point_buy", "rolled"}:
            errors.append("ability_method must be standard_array, point_buy, or rolled")
        elif base_values_valid and set(base) == set(ABILITY_IDS):
            values = tuple(sorted((base[key] for key in ABILITY_IDS), reverse=True))
            if ability_method == "standard_array" and values != STANDARD_ARRAY:
                errors.append("standard_array abilities must use 15, 14, 13, 12, 10, 8 once each")
            elif ability_method == "point_buy":
                if any(value not in POINT_BUY_COSTS for value in values):
                    errors.append("point_buy abilities must each be from 8 to 15")
                elif sum(POINT_BUY_COSTS[value] for value in values) != 27:
                    errors.append("point_buy abilities must spend exactly 27 points")
            elif ability_method == "rolled" and any(not 3 <= value <= 18 for value in values):
                errors.append("rolled abilities must each be from 3 to 18")
        for key, value in base.items():
            if isinstance(value, bool) or not isinstance(value, int) or not 3 <= value <= 20:
                errors.append(f"base ability {key} must be an integer from 3 to 20")

        class_entity = self._entity_from_ref(draft.get("class_ref"), "class", errors)
        species = self._entity_from_ref(draft.get("species_ref"), "species", errors)
        background = self._entity_from_ref(draft.get("background_ref"), "background", errors)

        alignment = str(draft.get("alignment") or "neutral")
        if alignment not in ALIGNMENTS:
            errors.append("alignment is not a recognized D&D alignment")

        bonuses = draft.get("background_ability_bonuses")
        if not isinstance(bonuses, dict):
            errors.append("background_ability_bonuses must be an object")
            bonuses = {}
        allowed_bonus_ids = {
            _ref_id(str(ref), "ability")
            for ref in (background or {}).get("ability_refs", [])
        }
        if any(key not in allowed_bonus_ids for key in bonuses):
            errors.append("background ability bonuses contain an ability not allowed by the background")
        bonus_values = list(bonuses.values())
        if (
            any(isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2
                for value in bonus_values)
            or sum(bonus_values) != 3
            or sorted((value for value in bonus_values if value), reverse=True) not in ([2, 1], [1, 1, 1])
        ):
            errors.append("background ability bonuses must be +2/+1 or +1/+1/+1")
        for key in ABILITY_IDS:
            if isinstance(base.get(key), int) and base.get(key, 0) + int(bonuses.get(key, 0) or 0) > 20:
                errors.append(f"final ability {key} must not exceed 20")

        selected_skills = draft.get("class_skill_refs")
        if not isinstance(selected_skills, list):
            errors.append("class_skill_refs must be an array")
            selected_skills = []
        if any(not isinstance(ref, str) for ref in selected_skills):
            errors.append("class skill choices must be string references")
        elif len(selected_skills) != len(set(selected_skills)):
            errors.append("class skill choices must not contain duplicates")
        skill_choice = (class_entity or {}).get("skill_choice") or {}
        allowed_skills = set(skill_choice.get("allowed_refs") or [])
        required_count = int(skill_choice.get("count", 0) or 0)
        if len(selected_skills) != required_count:
            errors.append(f"class requires exactly {required_count} skill choices")
        if any(ref not in allowed_skills for ref in selected_skills):
            errors.append("class skill choice is not allowed")
        background_skills = set((background or {}).get("skill_refs") or [])
        if any(ref in background_skills for ref in selected_skills):
            errors.append("class skill choices must not duplicate background skills")
        for ref in selected_skills:
            self._entity_from_ref(ref, "skill", errors)

        package_ref = draft.get("equipment_package_ref")
        package = self._entity_from_ref(package_ref, "equipment_package", errors)
        allowed_packages = set((class_entity or {}).get("equipment_package_refs") or [])
        if package_ref and package_ref not in allowed_packages:
            errors.append("equipment package is not allowed by the class")
        for item_ref in (package or {}).get("item_refs", []):
            self._entity_from_ref(item_ref, "item", errors)

        background_package_refs = set((background or {}).get("equipment_package_refs") or [])
        background_package_ref = draft.get("background_equipment_package_ref")
        background_package = None
        if background_package_refs:
            background_package = self._entity_from_ref(
                background_package_ref, "equipment_package", errors,
            )
            if background_package_ref not in background_package_refs:
                errors.append("equipment package is not allowed by the background")
            for item_ref in (background_package or {}).get("item_refs", []):
                self._entity_from_ref(item_ref, "item", errors)

        size_options = list((species or {}).get("size_options") or [])
        selected_size = str(draft.get("species_size") or "")
        if len(size_options) > 1 and selected_size not in size_options:
            errors.append("species_size must be one of the selected species size options")
        elif selected_size and size_options and selected_size not in size_options:
            errors.append("species_size is not allowed by the selected species")

        choice_answers = draft.get("species_choice_answers") or {}
        if not isinstance(choice_answers, dict):
            errors.append("species_choice_answers must be an object")
            choice_answers = {}
        choice_specs = (species or {}).get("choice_specs") or []
        expected_choice_ids = {str(spec.get("id") or "") for spec in choice_specs}
        if any(key not in expected_choice_ids for key in choice_answers):
            errors.append("species_choice_answers contains an unknown choice")
        for spec in choice_specs:
            choice_id = str(spec.get("id") or "")
            answer = choice_answers.get(choice_id)
            allowed = set(spec.get("option_ids") or spec.get("option_refs") or [])
            if answer not in allowed:
                errors.append(f"species choice {choice_id} requires one allowed option")
            if spec.get("option_refs") and isinstance(answer, str):
                kind = answer.split(":", 1)[0] if ":" in answer else ""
                if kind:
                    self._entity_from_ref(answer, kind, errors)

        species_skill_choice = (species or {}).get("skill_choice") or {}
        species_skill_refs = self._validate_choice_refs(
            draft.get("species_skill_refs"),
            choice=species_skill_choice,
            kind="skill",
            label="species skill",
            errors=errors,
        )
        occupied_skills = background_skills | set(selected_skills)
        if any(ref in occupied_skills for ref in species_skill_refs):
            errors.append("species skill choices must not duplicate another skill proficiency")

        species_feat_choice = (species or {}).get("feat_choice") or {}
        self._validate_choice_refs(
            draft.get("species_feat_refs"),
            choice=species_feat_choice,
            kind="feat",
            label="species feat",
            errors=errors,
        )

        class_feat_choices = draft.get("class_feature_feat_choices")
        if class_feat_choices is not None and not isinstance(class_feat_choices, dict):
            errors.append("class_feature_feat_choices must be an object")
            class_feat_choices = {}
        class_feat_choices = class_feat_choices or {}
        if "fighting_style" in set((class_entity or {}).get("level_1_feature_ids") or []):
            # 建卡期战斗风格可选：仅当客户端显式给出选择时才校验其合法性。
            # 引导/专家建卡尚无该控件，缺失时保持特性为未选择状态，不阻断建卡。
            if "fighting_style" in class_feat_choices:
                self._validate_fighting_style_choice(
                    class_feat_choices, draft, class_entity, background, errors,
                )
        elif class_feat_choices:
            errors.append("class_feature_feat_choices is not available to this class")

        feat_choice_specs = self._feat_choice_specs(
            draft,
            class_entity=class_entity,
            species=species,
            background=background,
        )
        feat_choice_answers = draft.get("feat_choice_answers") or {}
        if not isinstance(feat_choice_answers, dict):
            errors.append("feat_choice_answers must be an object")
            feat_choice_answers = {}
        if any(feat_ref not in feat_choice_specs for feat_ref in feat_choice_answers):
            errors.append("feat_choice_answers contains a feat that the character does not have")
        for feat_ref, specs in feat_choice_specs.items():
            answers = feat_choice_answers.get(feat_ref)
            if not isinstance(answers, dict):
                errors.append(f"feat {feat_ref} requires its guided choices")
                continue
            expected_ids = {str(spec.get("id") or "") for spec in specs}
            if any(choice_id not in expected_ids for choice_id in answers):
                errors.append(f"feat {feat_ref} contains an unknown choice")
            for spec in specs:
                choice_id = str(spec.get("id") or "")
                selected = answers.get(choice_id)
                count = int(spec.get("count", 0) or 0)
                allowed = set(spec.get("option_ids") or spec.get("option_refs") or [])
                if not isinstance(selected, list):
                    errors.append(f"feat {feat_ref} choice {choice_id} must be an array")
                    continue
                if len(selected) != count or len(set(selected)) != count:
                    errors.append(f"feat {feat_ref} choice {choice_id} requires exactly {count} unique options")
                if any(not isinstance(value, str) or value not in allowed for value in selected):
                    errors.append(f"feat {feat_ref} choice {choice_id} contains an option that is not allowed")

        class_tool_choice = deepcopy((class_entity or {}).get("tool_choice") or {})
        tool_categories = set(class_tool_choice.get("allowed_categories") or [])
        background_tool_refs = set((background or {}).get("tool_proficiency_refs") or [])
        class_tool_choice["allowed_refs"] = [
            f"tool:{item['id']}"
            for item in self.bundle.list("tool")
            if item.get("category") in tool_categories
            and f"tool:{item['id']}" not in background_tool_refs
        ]
        self._validate_choice_refs(
            draft.get("class_tool_refs"),
            choice=class_tool_choice,
            kind="tool",
            label="class tool",
            errors=errors,
        )

        languages = self.bundle.list("language")
        language_refs = draft.get("language_refs")
        if languages:
            if not isinstance(language_refs, list):
                errors.append("language_refs must be an array")
                language_refs = []
            standard_refs = {f"language:{item['id']}" for item in languages}
            if len(language_refs) != 3 or len(set(language_refs)) != 3:
                errors.append("choose exactly three different languages")
            if "language:common" not in language_refs:
                errors.append("all characters must know Common")
            if any(ref not in standard_refs for ref in language_refs):
                errors.append("language choice is not a Standard Language")
            for ref in language_refs:
                self._entity_from_ref(ref, "language", errors)

        if species is not None:
            try:
                speed = int(species.get("speed", 0) or 0)
            except (TypeError, ValueError):
                speed = 0
            if speed <= 0:
                errors.append("species speed must be positive")
        return list(dict.fromkeys(errors))

    def _validate_fighting_style_choice(
        self,
        choices: dict[str, Any],
        draft: dict[str, Any],
        class_entity: dict[str, Any] | None,
        background: dict[str, Any] | None,
        errors: list[str],
    ) -> None:
        """Validate a supplied level-1 fighting style feat choice.

        The parsed declaration is the authority, not the category: a feat is
        accepted only if its own ``requires`` hold for *this* draft.  The
        hand-written ``requires_feature`` test that used to live here was a
        second implementation of the same predicate, and it disagreed with the
        level-up one -- the slot could accept what the level-up path refused.
        Omitting the choice is allowed; the level-1 feature then stays unselected.
        """

        ref = choices.get("fighting_style")
        if not isinstance(ref, str) or not ref:
            errors.append("fighting style feat choice must be a feat reference")
            return
        slot = _fighting_style_slot(self.bundle, class_entity)
        definition = load_feat_definitions(self.bundle).get(_ref_id(ref, "feat"))
        if (
            slot is None
            or slot.choice is None
            or definition is None
            or definition.category != "fighting_style"
            # 白名单是"可实现"而不是"属于该类别"：GWF 声明了类别却没有任何效果，
            # 让它在建卡期通过，就会得到一个"选了什么都不会发生"的角色。
            or ref not in slot.choice.enabled_options
        ):
            errors.append(f"fighting style feat choice is not an available fighting style: {ref}")
            return
        # 槽位只收战斗风格，而战斗风格不声明施法前置；真出现了再解析职业表，
        # 免得每次建卡都为一条不会用到的谓词构造进度目录。
        needs_slot_profile = any(
            requirement.kind == "spellcasting" for requirement in definition.requires
        )
        context = FeatContext(
            level=1,
            abilities=_draft_abilities(draft),
            owned_refs=_draft_feat_refs(draft, background),
            slot_profile=(
                _slot_profile_at(self.bundle, draft.get("class_ref"), 1)
                if needs_slot_profile else ""
            ),
            feature_ids=frozenset((class_entity or {}).get("level_1_feature_ids") or []),
            proficiency_refs=_draft_proficiencies(draft, class_entity, background),
        )
        if feat_unavailable_reasons(definition, context):
            # 文案保持原样：这一串是跨层契约（前端按它做友好提示），
            # 具体理由由升级路径的 ``unavailable_reasons`` 提供。
            errors.append(f"fighting style feat choice is not an available fighting style: {ref}")


def _draft_abilities(draft: dict[str, Any]) -> dict[str, int]:
    """The draft's ability scores before anything is derived.

    Same arithmetic derivation uses: base array plus the background's bonus.
    Coercion is defensive because a malformed draft is reported by its own
    errors; this helper must not raise while collecting them.
    """

    base = draft.get("base_abilities") or {}
    bonuses = draft.get("background_ability_bonuses") or {}
    abilities: dict[str, int] = {}
    for ability in ABILITY_IDS:
        try:
            abilities[ability] = int(base.get(ability, 0) or 0) + int(
                bonuses.get(ability, 0) or 0
            )
        except (TypeError, ValueError):
            abilities[ability] = 0
    return abilities


def _draft_feat_refs(
    draft: dict[str, Any], background: dict[str, Any] | None,
) -> list[str]:
    """Feats the draft already owns before the slot under validation.

    The slot's own choice is excluded on purpose: ``already_owned`` is about a
    duplicate acquisition, and the feat being chosen is not acquired yet.
    """

    refs: list[str] = []
    background_ref = (background or {}).get("feat_ref")
    if background_ref:
        refs.append(str(background_ref))
    refs.extend(str(ref) for ref in draft.get("species_feat_refs") or [])
    return refs


def _draft_proficiencies(
    draft: dict[str, Any],
    class_entity: dict[str, Any] | None,
    background: dict[str, Any] | None,
) -> frozenset[str]:
    """Trained refs the finished character will carry, as far as the draft says.

    This mirrors derivation's construction (armor and weapon refs from the
    class, skills and tools from wherever they were chosen).  A test compares it
    against the derived sheet for a real character, so the mirror cannot drift
    quietly -- which is the failure mode of every "compute it again here" helper.
    """

    refs = {
        *(f"armor_category:{item}" for item in (class_entity or {}).get("armor_proficiencies") or []),
        *(f"weapon_category:{item}" for item in (class_entity or {}).get("weapon_proficiencies") or []),
        *((background or {}).get("skill_refs") or []),
        *(draft.get("class_skill_refs") or []),
        *(draft.get("species_skill_refs") or []),
        *((background or {}).get("tool_proficiency_refs") or []),
        *((class_entity or {}).get("tool_proficiency_refs") or []),
        *(draft.get("class_tool_refs") or []),
    }
    return frozenset(str(ref) for ref in refs)


def _slot_profile_at(bundle: Any, class_ref: Any, level: int) -> str:
    """The class's spellcasting profile at one level, or ``""``."""

    from src.rulesets.dnd2024.progression.catalog import Dnd2024ProgressionCatalog

    if not isinstance(class_ref, str) or not class_ref:
        return ""
    try:
        snapshot = Dnd2024ProgressionCatalog.from_bundle(bundle).snapshot(class_ref, level)
    except Exception:  # noqa: BLE001 - an unknown class is already a draft error
        return ""
    return str(snapshot.get("slot_profile") or "")


def _fighting_style_slot(bundle: Any, class_entity: dict[str, Any] | None):
    """The class's level-1 Fighting Style slot declaration, or ``None``.

    The slot is a real ``ClassFeatureDefinition`` now, so the build path reads the
    same declaration the level-up path does.  Before this, the gate was "is
    ``fighting_style`` in ``level_1_feature_ids``", which could not express the
    whitelist and could not see the two classes that gain it at level 2.
    """

    from src.rulesets.dnd2024.features.models import Dnd2024ClassFeatureCatalog

    class_id = str((class_entity or {}).get("id") or "")
    if not class_id:
        return None
    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    return catalog.features_by_key.get((f"class:{class_id}", "fighting_style"))
