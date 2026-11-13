from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.rules.rule_system import RuleSystem
from src.rulesets.legacy_adapter import LegacyRulesetAdapter
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.routes.rules import register_rules
from src.webui.services import ruleset_builder


def _write(path: Path, value: dict | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        value if isinstance(value, str) else json.dumps(value),
        encoding="utf-8",
    )


def _entity(kind: str, entity_id: str, **fields) -> dict:
    return {
        "schema_version": 1,
        "kind": kind,
        "id": entity_id,
        "source_ref": f"test:{kind}/{entity_id}",
        "automation_level": "deterministic",
        **fields,
    }


@pytest.fixture
def runtime(tmp_path: Path) -> Dnd2024Runtime:
    root = tmp_path / "dnd2024_srd"
    _write(root / "legal" / "ATTRIBUTION.md", "test")
    _write(root / "manifest.json", {
        "schema_version": 1,
        "bundle_id": "test:dnd2024",
        "runtime_id": "core:dnd2024",
        "ruleset_version": "0.1.0",
        "content_version": "test-content-v1",
        "default_locale": "en",
        "supported_locales": ["en"],
        "license": {"id": "test", "attribution": "legal/ATTRIBUTION.md"},
    })
    for ability_id in ("str", "dex", "con", "int", "wis", "cha"):
        # `con` is a reserved device name on Windows, and `con.json` counts as
        # the device too: the write goes to the console and no file appears, so
        # the fixture silently lost one ability and every reference to it failed
        # as "unresolved".  The loader takes the id from inside the file, which
        # is why the real content can spell the filename out -- so this does the
        # same instead of depending on the platform.
        filename = "constitution" if ability_id == "con" else ability_id
        _write(
            root / "content" / "abilities" / f"{filename}.json",
            _entity("ability", ability_id),
        )
    for skill_id, ability_id in {
        "athletics": "str",
        "intimidation": "cha",
        "perception": "wis",
        "survival": "wis",
    }.items():
        _write(
            root / "content" / "skills" / f"{skill_id}.json",
            _entity("skill", skill_id, ability_ref=f"ability:{ability_id}"),
        )
    _write(root / "content" / "classes" / "fighter.json", _entity(
        "class", "fighter", hit_die=10,
        saving_throw_refs=["ability:str", "ability:con"],
        armor_proficiencies=["heavy", "shield"],
        skill_choice={
            "count": 2,
            "allowed_refs": ["skill:athletics", "skill:perception", "skill:survival"],
        },
        equipment_package_refs=["equipment_package:fighter_a"],
    ))
    _write(root / "content" / "species" / "human.json", _entity(
        "species", "human", speed=30,
    ))
    _write(root / "content" / "backgrounds" / "soldier.json", _entity(
        "background", "soldier",
        ability_refs=["ability:str", "ability:dex", "ability:con"],
        skill_refs=["skill:athletics", "skill:intimidation"],
        feat_ref="feat:savage_attacker",
    ))
    _write(root / "content" / "feats" / "savage_attacker.json", _entity(
        "feat", "savage_attacker", automation_level="guided",
    ))
    _write(root / "content" / "items" / "chain_mail.json", _entity(
        "item", "chain_mail", item_type="armor", category="heavy",
        ac_base=16, dex_cap=0,
    ))
    _write(root / "content" / "items" / "shield.json", _entity(
        "item", "shield", item_type="shield", ac_bonus=2,
    ))
    _write(root / "content" / "items" / "longsword.json", _entity(
        "item", "longsword", item_type="weapon", damage_dice="1d8",
    ))
    _write(root / "content" / "equipment_packages" / "fighter_a.json", _entity(
        "equipment_package", "fighter_a",
        item_refs=["item:chain_mail", "item:shield", "item:longsword"],
    ))
    return Dnd2024Runtime(tmp_path)


@pytest.fixture
def valid_draft() -> dict:
    return {
        "locale": "en",
        "name": "Arden",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 15, "dex": 13, "con": 14,
            "int": 10, "wis": 12, "cha": 8,
        },
        "background_ability_bonuses": {"str": 2, "con": 1},
        "class_ref": "class:fighter",
        "species_ref": "species:human",
        "background_ref": "background:soldier",
        "class_skill_refs": ["skill:perception", "skill:survival"],
        "equipment_package_ref": "equipment_package:fighter_a",
    }


def test_derives_first_level_fighter_deterministically(runtime, valid_draft) -> None:
    character = runtime.derive_character(None, valid_draft)

    assert character["rule_binding"] == {
        "rule_id": "dnd2024_srd",
        "runtime_id": "core:dnd2024",
        "runtime_version": 1,
        "content_version": "test-content-v1",
        "state_schema_version": 1,
    }
    assert character["abilities"]["str"] == 17
    assert character["abilities"]["con"] == 15
    assert character["resources"]["max_hp"] == 12
    assert character["derived"]["armor_class"] == 18
    assert character["derived"]["initiative"] == 1
    assert character["derived"]["passive_perception"] == 13
    assert character["derived"]["saving_throws"]["str"] == 5
    assert character["derived"]["saving_throws"]["con"] == 4
    assert character["proficiencies"]["skill_values"]["perception"] == 3


def test_finalize_adds_legacy_projection_without_replacing_canonical_state(
    runtime, valid_draft,
) -> None:
    character = runtime.finalize_character(None, valid_draft)

    canonical = character["ruleset_character"]
    assert canonical["identity"]["species_ref"] == "species:human"
    assert character["character_name"] == "Arden"
    assert character["class"] == "fighter"
    assert character["race"] == "human"
    assert character["hp"] == canonical["resources"]["hp"] == 12
    assert character["attributes"] == canonical["abilities"]
    assert isinstance(character["equipment"], list)
    assert isinstance(canonical["equipment"], dict)


def test_normalize_submission_rederives_and_rejects_binding_tampering(
    runtime, valid_draft,
) -> None:
    submitted = runtime.finalize_character(None, valid_draft)
    submitted["hp"] = 999
    submitted["armor_class"] = 999
    submitted["attributes"]["str"] = 99

    normalized = runtime.normalize_character_submission(None, submitted, "en")

    assert normalized["hp"] == 12
    assert normalized["armor_class"] == 18
    assert normalized["attributes"]["str"] == 17

    submitted["ruleset_character"]["rule_binding"]["runtime_version"] = 999
    with pytest.raises(ValueError, match="binding is incompatible"):
        runtime.normalize_character_submission(None, submitted, "en")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda draft: draft.update(name=""), "character name is required"),
        (lambda draft: draft["base_abilities"].update(str=14), "standard_array"),
        (lambda draft: draft.update(background_ability_bonuses={"wis": 2, "con": 1}), "not allowed"),
        (lambda draft: draft.update(class_skill_refs=["skill:intimidation", "skill:survival"]), "not allowed"),
        (lambda draft: draft.update(equipment_package_ref="equipment_package:missing"), "unknown equipment_package"),
    ],
)
def test_rejects_illegal_character_choices(runtime, valid_draft, mutate, message) -> None:
    draft = deepcopy(valid_draft)
    mutate(draft)

    errors = runtime.validate_character(None, draft)

    assert any(message in error for error in errors)
    with pytest.raises(ValueError):
        runtime.finalize_character(None, draft)


def test_runtime_capabilities_claim_completed_m6_features(runtime) -> None:
    capabilities = runtime.capabilities

    assert capabilities.character_builder == "professional"
    assert capabilities.versioned_state is True
    assert capabilities.authoritative_intents is True
    assert capabilities.deterministic_combat is True
    assert capabilities.session_zero is True
    assert capabilities.tutorial_coach is True


def test_builder_choices_are_constrained_by_selected_class(runtime, valid_draft) -> None:
    choices = runtime.builder_choices(None, valid_draft)

    assert choices["ability_methods"][0]["values"] == [15, 14, 13, 12, 10, 8]
    assert {item["ref"] for item in choices["class_skills"]} == {
        "skill:perception", "skill:survival",
    }
    assert choices["class_skill_count"] == 2
    assert [item["ref"] for item in choices["equipment_packages"]] == [
        "equipment_package:fighter_a"
    ]
    package = choices["equipment_packages"][0]
    assert package["summary"] == ""
    assert package["items"] == []
    assert all("source_ref" in item for item in choices["class_skills"])


def _real_bundle_draft(runtime: Dnd2024Runtime, class_ref: str, species_ref: str) -> dict:
    choices = runtime.builder_choices(None, {
        "locale": "en",
        "class_ref": class_ref,
        "background_ref": "background:soldier",
        "species_ref": species_ref,
    })
    class_entity = runtime.load_bundle("en").get("class", class_ref.split(":", 1)[1])
    assert class_entity is not None
    draft = {
        "locale": "en",
        "name": "SRD Test Hero",
        "level": 1,
        "alignment": "neutral_good",
        "ability_method": "standard_array",
        "base_abilities": class_entity["recommended_standard_array"],
        "background_ability_bonuses": {"str": 2, "con": 1},
        "class_ref": class_ref,
        "species_ref": species_ref,
        "background_ref": "background:soldier",
        "class_skill_refs": [
            item["ref"] for item in choices["class_skills"][:choices["class_skill_count"]]
        ],
        "equipment_package_ref": choices["equipment_packages"][0]["ref"],
        "background_equipment_package_ref": choices["background_equipment_packages"][0]["ref"],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
    }
    if "fighting_style" in (class_entity.get("level_1_feature_ids") or []):
        # 建卡 AC 现在会消费 Defense 的 +1；这个文件的断言是"无被动加值"的
        # 基线，因此用 Archery（无 AC 效果），Defense 由独立回归覆盖。
        draft["class_feature_feat_choices"] = {"fighting_style": "feat:archery"}
    if class_entity.get("recommended_spell_choices"):
        draft["class_spell_choices"] = class_entity["recommended_spell_choices"]
    choices = runtime.builder_choices(None, draft)
    sizes = choices["species_sizes"]
    if sizes:
        draft["species_size"] = "medium" if "medium" in sizes else sizes[0]
    species_choices = {}
    for choice in choices["species_choices"]:
        options = choice.get("option_ids") or choice.get("option_refs") or []
        species_choices[choice["id"]] = options[0]
    if species_choices:
        draft["species_choice_answers"] = species_choices
    if choices["species_skill_count"]:
        draft["species_skill_refs"] = [choices["species_skills"][0]["ref"]]
    if choices["species_feat_count"]:
        draft["species_feat_refs"] = ["feat:alert"]
    if choices["class_tool_count"]:
        draft["class_tool_refs"] = [
            item["ref"] for item in choices["class_tools"][:choices["class_tool_count"]]
        ]
    return draft


@pytest.mark.parametrize(
    "class_id",
    [
        "barbarian", "bard", "cleric", "druid", "fighter", "monk",
        "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard",
    ],
)
def test_real_srd_bundle_all_classes_form_legal_level_one_characters(class_id) -> None:
    runtime = Dnd2024Runtime()
    draft = _real_bundle_draft(runtime, f"class:{class_id}", "species:human")

    errors = runtime.validate_character(None, draft)
    character = runtime.finalize_character(None, draft)

    assert errors == []
    assert character["rule_binding"]["content_version"] == "srd-5.2.1+r5"
    assert character["ruleset_character"]["build"]["class_levels"] == [
        {"class_ref": f"class:{class_id}", "level": 1}
    ]
    assert character["ruleset_character"]["build"]["grants_with_sources"]


@pytest.mark.parametrize(
    "species_id",
    ["dragonborn", "dwarf", "elf", "gnome", "goliath", "halfling", "human", "orc", "tiefling"],
)
def test_real_srd_bundle_all_species_complete_their_required_choices(species_id) -> None:
    runtime = Dnd2024Runtime()
    draft = _real_bundle_draft(runtime, "class:fighter", f"species:{species_id}")

    errors = runtime.validate_character(None, draft)
    character = runtime.derive_character(None, draft)

    assert errors == []
    assert character["identity"]["species_ref"] == f"species:{species_id}"
    assert character["identity"]["size"] in {"small", "medium"}


def test_all_quick_presets_are_server_validated_and_finalize() -> None:
    runtime = Dnd2024Runtime()
    presets = runtime.builder_choices(None, {"locale": "zh-CN"})["quick_presets"]

    assert len(presets) == 6
    for preset in presets:
        draft = {**preset["draft"], "locale": "zh-CN", "name": preset["name"]}
        assert runtime.validate_character(None, draft) == [], preset["id"]
        character = runtime.finalize_character(None, draft)
        assert character["character_name"] == preset["name"]
        assert character["ruleset_character"]["build"]["grants_with_sources"]


def test_magic_initiate_requires_complete_choices_and_is_preserved_canonically() -> None:
    runtime = Dnd2024Runtime()
    preset = next(
        item for item in runtime.builder_choices(None, {"locale": "en"})["quick_presets"]
        if item["id"] == "kindly_bulwark"
    )
    draft = {**preset["draft"], "locale": "en", "name": "Kind Guide"}
    choices = runtime.builder_choices(None, draft)

    assert choices["feat_choices"][0]["feat_ref"] == "feat:magic_initiate_cleric"
    assert [spec["count"] for spec in choices["feat_choices"][0]["specs"]] == [1, 2, 1]
    character = runtime.derive_character(None, draft)
    initiate = character["spellcasting"]["magic_initiate"][0]
    assert initiate["spellcasting_ability"] == "wis"
    assert initiate["cantrip_ids"] == ["guidance", "sacred_flame"]
    assert initiate["level_1_spell_id"] == "healing_word"

    draft.pop("feat_choice_answers")
    assert any(
        "requires its guided choices" in error
        for error in runtime.validate_character(None, draft)
    )


def test_skilled_choices_grant_three_nonduplicate_proficiencies() -> None:
    runtime = Dnd2024Runtime()
    draft = _real_bundle_draft(runtime, "class:fighter", "species:human")
    draft["species_feat_refs"] = ["feat:skilled"]
    choices = runtime.builder_choices(None, draft)
    feat = next(item for item in choices["feat_choices"] if item["feat_ref"] == "feat:skilled")
    selected = [option["value"] for option in feat["specs"][0]["options"][:3]]
    draft["feat_choice_answers"] = {"feat:skilled": {"proficiencies": selected}}

    character = runtime.derive_character(None, draft)

    assert runtime.validate_character(None, draft) == []
    granted = set(character["proficiencies"]["skill_refs"])
    granted.update(character["proficiencies"]["tool_refs"])
    assert set(selected).issubset(granted)


def test_builder_is_level_one_only_until_progression_runtime_is_available() -> None:
    runtime = Dnd2024Runtime()
    draft = _real_bundle_draft(runtime, "class:fighter", "species:human")
    draft["level"] = 2

    assert runtime.validate_character(None, draft) == [
        "character creation currently supports level 1; use level-up for progression"
    ]


def test_english_bundle_does_not_inherit_chinese_presentation_fields() -> None:
    bundle = Dnd2024Runtime().load_bundle("en")

    assert bundle.get("class", "fighter")["summary"].startswith("Direct, durable")
    assert bundle.get("skill", "perception")["summary"].startswith("Use your senses")
    assert bundle.get("feat", "magic_initiate_wizard")["labels"]["mage_hand"] == "Mage Hand"


class _BuilderApi:
    def __init__(self, runtime: Dnd2024Runtime):
        self._ruleset_registry = RulesetRuntimeRegistry([LegacyRulesetAdapter(), runtime])
        self._professional_rule = RuleSystem({
            "rule_id": "test_dnd2024",
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
        })
        self._legacy_rule = RuleSystem({"rule_id": "legacy"})
        self.builder_dependencies = ruleset_builder.RulesetBuilderDependencies(
            load_rule=self._load_rule_by_id,
            ruleset_registry=self._ruleset_registry,
        )

    def _load_rule_by_id(self, rule_id: str, language: str = ""):
        del language
        return {
            "test_dnd2024": self._professional_rule,
            "legacy": self._legacy_rule,
        }.get(rule_id)

    def ruleset_experience(self, rule_id: str, language: str = ""):
        return ruleset_builder.experience(self.builder_dependencies, rule_id, language)

    def ruleset_builder_choices(self, rule_id: str, draft, language: str = ""):
        return ruleset_builder.choices(
            self.builder_dependencies, rule_id, draft, language
        )

    def ruleset_builder_validate(self, rule_id: str, draft, language: str = ""):
        return ruleset_builder.validate(
            self.builder_dependencies, rule_id, draft, language
        )

    def ruleset_builder_derive(self, rule_id: str, draft, language: str = ""):
        return ruleset_builder.derive(
            self.builder_dependencies, rule_id, draft, language
        )

    def ruleset_builder_finalize(self, rule_id: str, draft, language: str = ""):
        return ruleset_builder.finalize(
            self.builder_dependencies, rule_id, draft, language
        )


def test_stateless_builder_service_validates_before_deriving(runtime, valid_draft) -> None:
    api = _BuilderApi(runtime)

    valid = ruleset_builder.validate(
        api.builder_dependencies, "test_dnd2024", valid_draft, "en"
    )
    finalized = ruleset_builder.finalize(
        api.builder_dependencies, "test_dnd2024", valid_draft, "en"
    )
    invalid_draft = deepcopy(valid_draft)
    invalid_draft["name"] = ""
    rejected = ruleset_builder.derive(
        api.builder_dependencies, "test_dnd2024", invalid_draft, "en"
    )

    assert valid == {"ok": True, "rule_id": "test_dnd2024", "valid": True, "errors": []}
    assert finalized["ok"] is True
    assert finalized["character"]["character_name"] == "Arden"
    assert rejected["ok"] is False
    assert rejected["code"] == "INVALID_CHARACTER_DRAFT"


def test_builder_service_rejects_legacy_rule_and_hostile_shape(runtime) -> None:
    api = _BuilderApi(runtime)
    nested: dict = {}
    cursor = nested
    for _ in range(ruleset_builder.MAX_BUILDER_DRAFT_DEPTH + 1):
        cursor["next"] = {}
        cursor = cursor["next"]

    unavailable = ruleset_builder.validate(
        api.builder_dependencies, "legacy", {}, "en"
    )

    assert unavailable["ok"] is False
    assert unavailable["code"] == "RULESET_BUILDER_UNAVAILABLE"
    with pytest.raises(ValueError, match="nesting is too deep"):
        ruleset_builder.validate_draft_shape(nested)
    with pytest.raises(ValueError, match="JSON object"):
        ruleset_builder.validate_draft_shape([])


@pytest.mark.asyncio
async def test_builder_http_contract_and_request_size_limit(runtime, valid_draft) -> None:
    app = web.Application()
    app["api"] = _BuilderApi(runtime)
    register_rules(app)

    async with TestClient(TestServer(app)) as client:
        experience_response = await client.get(
            "/api/rules/test_dnd2024/experience?language=en"
        )
        experience = await experience_response.json()
        assert experience_response.status == 200
        assert experience["ruleset_runtime"]["id"] == "core:dnd2024"
        assert experience["experience"]["modes"] == ["quick", "guided", "expert"]

        finalized_response = await client.post(
            "/api/rules/test_dnd2024/builder/finalize?language=en",
            json=valid_draft,
        )
        finalized = await finalized_response.json()
        assert finalized_response.status == 200
        assert finalized["character"]["ruleset_character"]["derived"]["armor_class"] == 18

        invalid_response = await client.post(
            "/api/rules/test_dnd2024/builder/validate",
            json=[],
        )
        assert invalid_response.status == 400

        too_large = await client.post(
            "/api/rules/test_dnd2024/builder/validate",
            data=b" " * (ruleset_builder.MAX_BUILDER_DRAFT_BYTES + 1),
        )
        assert too_large.status == 413
