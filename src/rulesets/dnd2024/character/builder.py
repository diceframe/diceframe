"""Deterministic D&D 2024 character draft validation and derivation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver


from .derivation import CharacterDerivationMixin
from .primitives import (
    ABILITY_IDS,
    POINT_BUY_COSTS,
    STANDARD_ARRAY,
    ability_modifier,
    humanize_id as _humanize_id,
    proficiency_bonus,
    ref_id as _ref_id,
)
from .validation import CharacterValidationMixin


class ProfessionalCharacterBlueprintRequired(ValueError):
    """A professional submission cannot be safely rebuilt from legacy fields."""

    error_code = "PROFESSIONAL_CHARACTER_BLUEPRINT_REQUIRED"


@dataclass(slots=True)
class Dnd2024CharacterBuilder(CharacterValidationMixin, CharacterDerivationMixin):
    bundle: LoadedRulesetBundle

    def choices(self, draft: dict[str, Any]) -> dict[str, Any]:
        """Return presentation-safe choices constrained by the current draft."""

        def present(entity: dict[str, Any], kind: str) -> dict[str, Any]:
            entity_id = str(entity["id"])
            result = {
                "ref": f"{kind}:{entity_id}",
                "id": entity_id,
                "name": entity.get("name", entity_id),
                "summary": entity.get("summary", entity.get("description", "")),
                "automation_level": entity["automation_level"],
                "source_ref": entity["source_ref"],
            }
            if kind == "equipment_package":
                grants = entity.get("item_grants") or []
                details: list[dict[str, Any]] = []
                for grant in grants:
                    if not isinstance(grant, dict):
                        continue
                    item_ref = str(grant.get("item_ref") or "")
                    item_id = item_ref.split(":", 1)[-1]
                    item = self.bundle.get("item", item_id) or {}
                    quantity = int(grant.get("quantity", 1) or 1)
                    details.append({
                        "ref": item_ref,
                        "name": item.get("name", item_id),
                        "quantity": quantity,
                    })
                result["items"] = details
                coins = int(entity.get("coins_gp", 0) or 0)
                if details or coins:
                    result["summary"] = (
                        f"{len(details)} 项装备"
                        + (f" · {coins} GP" if coins else "")
                    )
                    if details:
                        names = [str(item["name"]) for item in details[:2]]
                        suffix = (
                            f" 等 {len(details)} 项" if self.bundle.locale.startswith("zh")
                            else f" + {len(details) - 2} more"
                        ) if len(details) > 2 else ""
                        result["name"] = " · ".join(names) + suffix
                    elif coins:
                        result["name"] = (
                            f"{coins} GP 购买" if self.bundle.locale.startswith("zh")
                            else f"Purchase with {coins} GP"
                        )
            return result

        class_ref = str(draft.get("class_ref") or "")
        class_entity = self.bundle.get("class", _ref_id(class_ref, "class"))
        background_ref = str(draft.get("background_ref") or "")
        background = self.bundle.get("background", _ref_id(background_ref, "background"))
        species_ref = str(draft.get("species_ref") or "")
        species = self.bundle.get("species", _ref_id(species_ref, "species"))
        skill_choice = (class_entity or {}).get("skill_choice") or {}
        background_skill_refs = set((background or {}).get("skill_refs") or [])
        skill_refs = set(skill_choice.get("allowed_refs") or []) - background_skill_refs
        package_refs = set((class_entity or {}).get("equipment_package_refs") or [])
        background_package_refs = set((background or {}).get("equipment_package_refs") or [])
        species_skill_choice = (species or {}).get("skill_choice") or {}
        occupied_skill_refs = background_skill_refs | set(draft.get("class_skill_refs") or [])
        species_skill_refs = set(species_skill_choice.get("allowed_refs") or []) - occupied_skill_refs
        species_feat_choice = (species or {}).get("feat_choice") or {}
        class_tool_choice = (class_entity or {}).get("tool_choice") or {}
        tool_categories = set(class_tool_choice.get("allowed_categories") or [])
        occupied_tool_refs = set((background or {}).get("tool_proficiency_refs") or [])
        class_tools = [
            item for item in self.bundle.list("tool")
            if item.get("category") in tool_categories
            and f"tool:{item['id']}" not in occupied_tool_refs
        ]
        quick_presets = []
        for item in self.bundle.list("quick_character_preset"):
            quick_presets.append({
                **present(item, "quick_character_preset"),
                "difficulty": item.get("difficulty", "intermediate"),
                "fantasy_tags": list(item.get("fantasy_tags") or []),
                "recommendation_reason": item.get("recommendation_reason", ""),
                "draft": deepcopy(item.get("draft") or {}),
            })
        feat_choices = self._present_feat_choices(
            draft, class_entity=class_entity, species=species, background=background,
        )
        return {
            "ability_methods": [
                {
                    "id": "standard_array",
                    "values": list(STANDARD_ARRAY),
                    "automation_level": "deterministic",
                    "source_ref": "srd-5.2.1:p21:standard-array",
                },
                {
                    "id": "point_buy",
                    "budget": 27,
                    "costs": dict(POINT_BUY_COSTS),
                    "automation_level": "deterministic",
                    "source_ref": "srd-5.2.1:p21:point-cost",
                },
                {
                    "id": "rolled",
                    "formula": "4d6kh3",
                    "count": 6,
                    "automation_level": "guided",
                    "source_ref": "srd-5.2.1:p21:random-generation",
                },
            ],
            "classes": [present(item, "class") for item in self.bundle.list("class")],
            "species": [present(item, "species") for item in self.bundle.list("species")],
            "backgrounds": [present(item, "background") for item in self.bundle.list("background")],
            "skills": [present(item, "skill") for item in self.bundle.list("skill")],
            "languages": [present(item, "language") for item in self.bundle.list("language")],
            "origin_feats": [
                present(item, "feat")
                for item in self.bundle.list("feat")
                if item.get("category") == "origin"
            ],
            "class_skills": [
                present(item, "skill")
                for item in self.bundle.list("skill")
                if f"skill:{item['id']}" in skill_refs
            ],
            "class_skill_count": int(skill_choice.get("count", 0) or 0),
            "equipment_packages": [
                present(item, "equipment_package")
                for item in self.bundle.list("equipment_package")
                if f"equipment_package:{item['id']}" in package_refs
            ],
            "background_equipment_packages": [
                present(item, "equipment_package")
                for item in self.bundle.list("equipment_package")
                if f"equipment_package:{item['id']}" in background_package_refs
            ],
            "background_ability_refs": list((background or {}).get("ability_refs") or []),
            "species_sizes": list((species or {}).get("size_options") or []),
            "species_choices": deepcopy((species or {}).get("choice_specs") or []),
            "species_skills": [
                present(item, "skill")
                for item in self.bundle.list("skill")
                if f"skill:{item['id']}" in species_skill_refs
            ],
            "species_skill_count": int(species_skill_choice.get("count", 0) or 0),
            "species_feats": [
                present(item, "feat")
                for item in self.bundle.list("feat")
                if f"feat:{item['id']}" in set(species_feat_choice.get("allowed_refs") or [])
            ],
            "species_feat_count": int(species_feat_choice.get("count", 0) or 0),
            "feat_choices": feat_choices,
            "class_tools": [present(item, "tool") for item in class_tools],
            "class_tool_count": int(class_tool_choice.get("count", 0) or 0),
            "quick_presets": quick_presets,
            "recommended_base_abilities": deepcopy(
                (class_entity or {}).get("recommended_standard_array") or {}
            ),
            "recommended_class_spells": deepcopy(
                (class_entity or {}).get("recommended_spell_choices") or {}
            ),
        }

    def _acquired_feat_refs(
        self,
        draft: dict[str, Any],
        background: dict[str, Any] | None,
    ) -> list[str]:
        background_refs = (
            [str(background["feat_ref"])]
            if background is not None and background.get("feat_ref")
            else []
        )
        species_refs = [
            str(ref) for ref in (draft.get("species_feat_refs") or [])
            if isinstance(ref, str)
        ]
        return list(dict.fromkeys([*background_refs, *species_refs]))

    def _feat_choice_specs(
        self,
        draft: dict[str, Any],
        *,
        class_entity: dict[str, Any] | None,
        species: dict[str, Any] | None,
        background: dict[str, Any] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        del species
        result: dict[str, list[dict[str, Any]]] = {}
        for feat_ref in self._acquired_feat_refs(draft, background):
            feat = self.bundle.get("feat", _ref_id(feat_ref, "feat"))
            if feat is None:
                continue
            specs = deepcopy(feat.get("choice_specs") or [])
            proficiency_choice = feat.get("proficiency_choice") or {}
            if proficiency_choice:
                occupied = {
                    *list((background or {}).get("skill_refs") or []),
                    *list(draft.get("class_skill_refs") or []),
                    *list(draft.get("species_skill_refs") or []),
                    *list((background or {}).get("tool_proficiency_refs") or []),
                    *list((class_entity or {}).get("tool_proficiency_refs") or []),
                    *list(draft.get("class_tool_refs") or []),
                }
                allowed_kinds = set(proficiency_choice.get("allowed_kinds") or [])
                allowed_refs = [
                    f"{kind}:{item['id']}"
                    for kind in ("skill", "tool")
                    if kind in allowed_kinds
                    for item in self.bundle.list(kind)
                    if f"{kind}:{item['id']}" not in occupied
                ]
                specs.append({
                    "id": "proficiencies",
                    "count": int(proficiency_choice.get("count", 0) or 0),
                    "option_refs": allowed_refs,
                })
            if specs:
                result[feat_ref] = specs
        return result

    def _present_feat_choices(
        self,
        draft: dict[str, Any],
        *,
        class_entity: dict[str, Any] | None,
        species: dict[str, Any] | None,
        background: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        raw_specs = self._feat_choice_specs(
            draft, class_entity=class_entity, species=species, background=background,
        )
        result: list[dict[str, Any]] = []
        for feat_ref, specs in raw_specs.items():
            feat = self.bundle.get("feat", _ref_id(feat_ref, "feat")) or {}
            raw_labels = feat.get("labels")
            labels: dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
            presented_specs = []
            for spec in specs:
                options = []
                for value in spec.get("option_ids") or spec.get("option_refs") or []:
                    value = str(value)
                    if ":" in value:
                        kind, entity_id = value.split(":", 1)
                        entity = self.bundle.get(kind, entity_id) or {}
                        name = str(entity.get("name") or _humanize_id(entity_id))
                        source_ref = str(entity.get("source_ref") or feat.get("source_ref") or "")
                    else:
                        name = str(labels.get(value) or _humanize_id(value))
                        source_ref = str(feat.get("source_ref") or "")
                    options.append({"value": value, "name": name, "source_ref": source_ref})
                spec_id = str(spec.get("id") or "")
                presented_specs.append({
                    "id": spec_id,
                    "name": str(labels.get(spec_id) or _humanize_id(spec_id)),
                    "count": int(spec.get("count", 0) or 0),
                    "options": options,
                })
            result.append({
                "feat_ref": feat_ref,
                "name": str(feat.get("name") or _humanize_id(_ref_id(feat_ref, "feat"))),
                "summary": str(feat.get("summary") or ""),
                "automation_level": str(feat.get("automation_level") or "guided"),
                "source_ref": str(feat.get("source_ref") or ""),
                "specs": presented_specs,
            })
        return result

    def finalize(self, draft: dict[str, Any]) -> dict[str, Any]:
        canonical = self.derive(draft)
        return {
            **self.project_legacy(canonical),
            "rule_binding": deepcopy(canonical["rule_binding"]),
            "ruleset_character": canonical,
        }

    def normalize_submission(self, character: dict[str, Any]) -> dict[str, Any]:
        """Rebuild a submitted sheet from canonical choices, never derived client values."""

        canonical = character.get("ruleset_character")
        if not isinstance(canonical, dict):
            raise ProfessionalCharacterBlueprintRequired(
                "professional character submission is missing ruleset_character",
            )
        binding = canonical.get("rule_binding")
        if not isinstance(binding, dict):
            raise ValueError("professional character submission is missing rule_binding")
        expected_binding = {
            "rule_id": "dnd2024_srd",
            "runtime_id": "core:dnd2024",
            "runtime_version": 1,
            "content_version": self.bundle.manifest.content_version,
            "state_schema_version": 1,
        }
        if binding != expected_binding:
            raise ValueError("professional character rule binding is incompatible")
        build = canonical.get("build")
        identity = canonical.get("identity")
        if not isinstance(build, dict) or not isinstance(identity, dict):
            raise ValueError("professional character canonical choices are incomplete")
        class_levels = build.get("class_levels")
        if not isinstance(class_levels, list) or len(class_levels) != 1:
            raise ValueError("professional character must contain one class choice")
        class_level = class_levels[0]
        if not isinstance(class_level, dict):
            raise ValueError("professional character class choice is invalid")
        draft = {
            "locale": str(canonical.get("locale") or self.bundle.locale),
            "name": str(identity.get("name") or character.get("character_name") or ""),
            "level": class_level.get("level", build.get("level", 1)),
            "alignment": identity.get("alignment", "neutral"),
            "ability_method": build.get("ability_method", "standard_array"),
            "base_abilities": deepcopy(build.get("base_abilities")),
            "background_ability_bonuses": deepcopy(build.get("background_ability_bonuses")),
            "class_ref": class_level.get("class_ref"),
            "species_ref": identity.get("species_ref"),
            "background_ref": identity.get("background_ref"),
            "species_size": identity.get("size"),
            "species_choice_answers": deepcopy(build.get("species_choice_answers") or {}),
            "species_skill_refs": deepcopy(build.get("species_skill_refs") or []),
            "species_feat_refs": deepcopy(build.get("species_feat_refs") or []),
            "class_feature_feat_choices": deepcopy(
                build.get("class_feature_feat_choices") or {}
            ),
            "feat_choice_answers": deepcopy(build.get("feat_choice_answers") or {}),
            "class_skill_refs": deepcopy(build.get("class_skill_refs") or []),
            "class_tool_refs": deepcopy(build.get("class_tool_refs") or []),
            "equipment_package_ref": build.get("equipment_package_ref"),
            "background_equipment_package_ref": build.get(
                "background_equipment_package_ref"
            ),
            "language_refs": deepcopy(build.get("language_refs") or []),
            "class_spell_choices": deepcopy(build.get("class_spell_choices")),
        }
        return self.finalize(draft)

    def project_legacy(
        self,
        character: dict[str, Any],
        *,
        features: Dnd2024ClassFeatureResolver | None = None,
    ) -> dict[str, Any]:
        class_ref = character["build"]["class_levels"][0]["class_ref"]
        species_ref = character["identity"]["species_ref"]
        background_ref = character["identity"]["background_ref"]
        resolver = (
            features if features is not None else Dnd2024ClassFeatureResolver(self.bundle)
        )
        class_entity = self._required_entity(class_ref, "class")
        species = self._required_entity(species_ref, "species")
        background = self._required_entity(background_ref, "background")
        skills = []
        proficient_refs = set(character["proficiencies"]["skill_refs"])
        for skill_id, bonus in character["proficiencies"]["skill_values"].items():
            if f"skill:{skill_id}" not in proficient_refs:
                continue
            skill = self._required_entity(f"skill:{skill_id}", "skill")
            skills.append({"name": skill.get("name", skill_id), "value": bonus})
        equipment = []
        inventory = []
        for grant in character["equipment"].get("item_grants", []):
            item_ref = str(grant.get("item_ref") or "")
            item = self._required_entity(item_ref, "item")
            row = {
                "name": item.get("name", _ref_id(item_ref, "item")),
                "type": item.get("item_type", "item"),
                "qty": int(grant.get("quantity", 1) or 1),
                "item_ref": item_ref,
            }
            if item.get("damage"):
                row["damage_dice"] = item["damage"]
            if item.get("item_type") in {"weapon", "armor", "shield", "focus"}:
                equipment.append(row)
            else:
                inventory.append(row)
        return {
            "character_name": character["identity"]["name"],
            "rule_id": "dnd2024_srd",
            "race": species.get("name", _ref_id(species_ref, "species")),
            "class": class_entity.get("name", _ref_id(class_ref, "class")),
            "level": character["build"]["level"],
            "identity": {
                "origin": species.get("name", _ref_id(species_ref, "species")),
                "archetype": class_entity.get("name", _ref_id(class_ref, "class")),
                "background": background.get(
                    "name", _ref_id(background_ref, "background")
                ),
            },
            "background": background.get(
                "name", _ref_id(background_ref, "background")
            ),
            "attributes": deepcopy(character["abilities"]),
            "skills": skills,
            "hp": character["resources"]["hp"],
            "max_hp": character["resources"]["max_hp"],
            "deceased": "dead" in character.get("conditions", {}),
            "armor_class": character["derived"]["armor_class"],
            "equipment": equipment,
            "inventory": inventory,
            "gold": character["equipment"]["coins_gp"],
            "currency": {"amount": character["equipment"]["coins_gp"]},
            # 职业能力与职业资源是规则运行时对用户可见的投影；数值本身仍以
            # ruleset_character.resources.class 为唯一权威，这里只做展示。
            # 投影形状由 feature boundary 定义一次，live equipment reconcile 复用同一份。
            **resolver.projection_fields(character),
        }

    def _validate_choice_refs(
        self,
        raw: Any,
        *,
        choice: dict[str, Any],
        kind: str,
        label: str,
        errors: list[str],
    ) -> list[str]:
        required_count = int(choice.get("count", 0) or 0)
        if not required_count:
            if raw not in (None, []):
                errors.append(f"{label} choices are not available")
            return []
        if not isinstance(raw, list):
            errors.append(f"{label}_refs must be an array")
            return []
        if any(not isinstance(ref, str) for ref in raw):
            errors.append(f"{label} choices must be string references")
            return []
        if len(raw) != required_count or len(set(raw)) != required_count:
            errors.append(f"{label} requires exactly {required_count} different choices")
        allowed = set(choice.get("allowed_refs") or [])
        if any(ref not in allowed for ref in raw):
            errors.append(f"{label} choice is not allowed")
        for ref in raw:
            self._entity_from_ref(ref, kind, errors)
        return raw

    def _entity_from_ref(
        self,
        raw: Any,
        kind: str,
        errors: list[str],
    ) -> dict[str, Any] | None:
        ref = str(raw or "")
        entity_id = _ref_id(ref, kind)
        if not entity_id:
            errors.append(f"{kind}_ref must use {kind}:<id>")
            return None
        entity = self.bundle.get(kind, entity_id)
        if entity is None:
            errors.append(f"unknown {kind} reference: {ref}")
        return entity

    def _required_entity(self, ref: str, kind: str) -> dict[str, Any]:
        entity = self.bundle.get(kind, _ref_id(ref, kind))
        if entity is None:
            raise ValueError(f"unknown {kind} reference: {ref}")
        return entity
