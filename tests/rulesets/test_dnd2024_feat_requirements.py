"""A feat's printed prerequisite: one declaration, one verdict, three callers.

"Prerequisite: Level 4+, Medium Armor Training" is a list of AND-ed facts.
Before this module each fact was its own ``if`` in two methods, and the two had
drifted: level-up never read ``requires_feature``, character creation never read
an ability minimum or spellcasting.  These tests pin the *agreement*, not just
the individual predicates -- a second implementation is only ever wrong in the
place nobody compares.
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.character.validation import _draft_proficiencies
from src.rulesets.dnd2024.features.feat_models import (
    LEGACY_FEAT_FIELDS,
    FeatDefinitionError,
    load_feat_definitions,
    parse_feat,
)
from src.rulesets.dnd2024.features.feat_requirements import (
    ARMOR_TRAINING,
    REASON_CODES,
    FeatContext,
    FeatRequirementError,
    feat_unavailable_reasons,
    parse_requirements,
)
from src.rulesets.dnd2024.features.models import Dnd2024ClassFeatureCatalog
from src.rulesets.dnd2024.features.validation import _known_feature_ids, _validate_feat_requirement_references
from src.rulesets.dnd2024.progression.advancement import Dnd2024AdvancementEngine
from src.rulesets.dnd2024.progression.choices import _FEAT_REASON_MESSAGES
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")


def _feat(**overrides):
    base = {
        "schema_version": 1,
        "kind": "feat",
        "id": "probe_feat",
        "category": "general",
        "source_ref": "srd-5.2.1:p87:probe",
        "automation_level": "guided",
        "content_pack": "srd-5.2.1",
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle(LOCALE)


# ---------------------------------------------------------------------------
# 1. 声明：闭集 kind，每一项都要有读者
# ---------------------------------------------------------------------------


def test_a_prerequisite_line_parses_into_predicates() -> None:
    definition = parse_feat(_feat(requires=[
        {"kind": "ability_minimum", "any_of": {"str": 13, "dex": 13}},
        {"kind": "proficiency", "family": "armor", "id": "medium"},
        {"kind": "spellcasting"},
    ]))
    assert [r.kind for r in definition.requires] == [
        "ability_minimum", "proficiency", "spellcasting",
    ]
    assert definition.requires[0].any_of == {"str": 13, "dex": 13}
    assert definition.requires[1].family == "armor"
    assert definition.requires[1].proficiency_id == "medium"


def test_an_unknown_kind_is_rejected() -> None:
    with pytest.raises(FeatRequirementError) as captured:
        parse_requirements("probe_feat", [{"kind": "training"}])
    assert "training" in str(captured.value)


def test_an_empty_ability_list_is_rejected() -> None:
    # "任一属性达标" 少了集合就没有意义，放行等于"永远不满足"。
    with pytest.raises(FeatRequirementError):
        parse_requirements("probe_feat", [{"kind": "ability_minimum", "any_of": {}}])


def test_an_unknown_armor_training_is_rejected() -> None:
    with pytest.raises(FeatRequirementError) as captured:
        parse_requirements(
            "probe_feat",
            [{"kind": "proficiency", "family": "armor", "id": "mithral"}],
        )
    assert "mithral" in str(captured.value)


def test_an_extra_field_is_rejected() -> None:
    with pytest.raises(FeatRequirementError) as captured:
        parse_requirements("probe_feat", [{"kind": "spellcasting", "id": "wizard"}])
    assert "id" in str(captured.value)


def test_the_armor_training_set_is_the_one_the_classes_use(bundle) -> None:
    """The predicate's vocabulary must be the content's vocabulary.

    A class declaring ``armor_proficiencies: ["medium"]`` and a prerequisite
    asking for ``id: "medium"`` only work if both sides mean the same closed set.
    """

    declared: set[str] = set()
    for entity in bundle.list("class"):
        declared.update(str(item) for item in entity.get("armor_proficiencies") or [])
    assert declared == set(ARMOR_TRAINING)


# ---------------------------------------------------------------------------
# 2. 判定：理由码闭集、顺序稳定、OR 语义
# ---------------------------------------------------------------------------


def _context(**overrides) -> FeatContext:
    base = {
        "level": 4,
        "abilities": {ability: 10 for ability in ABILITIES},
        "owned_refs": (),
        "slot_profile": "",
        "feature_ids": (),
        "proficiency_refs": (),
    }
    base.update(overrides)
    return FeatContext(**base)


def test_an_empty_verdict_means_available() -> None:
    definition = parse_feat(_feat())
    assert feat_unavailable_reasons(definition, _context()) == ()


def test_level_comes_before_prerequisites() -> None:
    definition = parse_feat(_feat(minimum_level=19, requires=[
        {"kind": "ability_minimum", "any_of": {"str": 13}},
    ]))
    assert feat_unavailable_reasons(definition, _context()) == (
        "level", "ability_prerequisite",
    )


def test_an_ability_minimum_is_an_or() -> None:
    definition = parse_feat(_feat(requires=[
        {"kind": "ability_minimum", "any_of": {"str": 13, "dex": 13}},
    ]))
    # "Strength or Dexterity 13+" -- 一项达标即满足。
    assert feat_unavailable_reasons(
        definition, _context(abilities={**{a: 8 for a in ABILITIES}, "dex": 13}),
    ) == ()
    assert feat_unavailable_reasons(definition, _context()) == ("ability_prerequisite",)


def test_armor_training_reads_the_trained_refs() -> None:
    # 就是 "Level 4+, Medium Armor Training" 那一行；受训是角色身上已有的
    # ``armor_category:medium``，不是为专长另造的事实。
    definition = parse_feat(_feat(requires=[
        {"kind": "proficiency", "family": "armor", "id": "medium"},
    ]))
    assert feat_unavailable_reasons(definition, _context()) == ("proficiency",)
    assert feat_unavailable_reasons(
        definition, _context(proficiency_refs=("armor_category:medium",)),
    ) == ()


def test_a_feature_prerequisite_reads_owned_features() -> None:
    definition = parse_feat(_feat(requires=[{"kind": "feature", "id": "fighting_style"}]))
    assert feat_unavailable_reasons(definition, _context()) == ("feature",)
    assert feat_unavailable_reasons(
        definition, _context(feature_ids=("fighting_style",)),
    ) == ()


def test_spellcasting_reads_the_slot_profile() -> None:
    definition = parse_feat(_feat(requires=[{"kind": "spellcasting"}]))
    assert feat_unavailable_reasons(definition, _context()) == ("spellcasting",)
    assert feat_unavailable_reasons(definition, _context(slot_profile="full")) == ()


def test_every_reason_code_has_wording() -> None:
    # 判定与措辞分开之后，新理由码最容易的失败方式是"没人给它文案"。
    assert set(_FEAT_REASON_MESSAGES) == set(REASON_CODES)


def test_a_requirement_naming_an_unknown_feature_fails_at_load(bundle) -> None:
    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    known = _known_feature_ids(catalog, bundle, None)
    definitions = {
        "probe_feat": parse_feat(_feat(requires=[{"kind": "feature", "id": "not_a_feature"}])),
    }
    with pytest.raises(Exception) as captured:  # ClassFeatureError
        _validate_feat_requirement_references(definitions, known)
    assert "not_a_feature" in str(captured.value)


def test_a_choice_slot_counts_as_a_feature(bundle) -> None:
    # ``fighting_style`` 只有槽位、没有 class feature 定义；把"特性 id 空间"
    # 限定成目录里的定义会让四个战斗风格的先决全部失效。
    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    known = _known_feature_ids(catalog, bundle, None)
    assert "fighting_style" in known
    assert "second_wind" in known


def test_the_real_catalog_declares_its_prerequisites_in_the_new_field(bundle) -> None:
    definitions = load_feat_definitions(bundle)
    assert [r.kind for r in definitions["archery"].requires] == ["feature"]
    assert definitions["grappler"].requires[0].any_of == {"str": 13, "dex": 13}
    assert [r.kind for r in definitions["boon_of_spell_recall"].requires] == ["spellcasting"]
    # 前置已整体迁出 legacy 袋子。
    assert LEGACY_FEAT_FIELDS == frozenset({"ability_choice", "choice_specs"})


# ---------------------------------------------------------------------------
# 3. 三个调用点给同一个答案
# ---------------------------------------------------------------------------


def _fighter_draft(runtime: Dnd2024Runtime) -> dict:
    """A legal level-1 fighter draft, built from the real bundle."""

    choices = runtime.builder_choices(None, {
        "locale": LOCALE, "class_ref": "class:fighter",
        "background_ref": "background:soldier", "species_ref": "species:human",
    })
    class_entity = runtime.load_bundle(LOCALE).get("class", "fighter")
    draft = {
        "locale": LOCALE,
        "name": "Archer",
        "level": 1,
        "alignment": "neutral_good",
        "ability_method": "standard_array",
        "base_abilities": class_entity["recommended_standard_array"],
        "background_ability_bonuses": {"str": 2, "con": 1},
        "class_ref": "class:fighter",
        "species_ref": "species:human",
        "background_ref": "background:soldier",
        "class_skill_refs": [
            item["ref"] for item in choices["class_skills"][: choices["class_skill_count"]]
        ],
        "equipment_package_ref": "equipment_package:fighter_b",
        "background_equipment_package_ref": choices["background_equipment_packages"][0]["ref"],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
        "class_feature_feat_choices": {"fighting_style": "feat:archery"},
    }
    choices = runtime.builder_choices(None, draft)
    draft["species_size"] = "medium"
    draft["species_choice_answers"] = {
        choice["id"]: (choice.get("option_ids") or choice.get("option_refs") or [""])[0]
        for choice in choices["species_choices"]
    }
    if choices["species_skill_count"]:
        draft["species_skill_refs"] = [choices["species_skills"][0]["ref"]]
    if choices["species_feat_count"]:
        draft["species_feat_refs"] = ["feat:alert"]
    if choices["class_tool_count"]:
        draft["class_tool_refs"] = [
            item["ref"] for item in choices["class_tools"][: choices["class_tool_count"]]
        ]
    return draft


def _bare_character(*, feat_refs=()) -> dict:
    return {
        "abilities": {ability: 10 for ability in ABILITIES},
        "features": {"feat_refs": list(feat_refs)},
        "proficiencies": {"armor_category_refs": [], "weapon_category_refs": []},
    }


def test_the_level_up_path_reports_a_missing_feature(bundle) -> None:
    """The old behaviour was "no reason at all", because nothing read it.

    A class without the Fighting Style slot saw every fighting style as
    available; the only thing that kept it out of reach was that the category
    never showed up in a level-up slot.  Asking for the category directly is
    what turns that coincidence into a verdict.
    """

    engine = Dnd2024AdvancementEngine(bundle)
    snapshot = engine.catalog.snapshot("class:monk", 4)
    options = engine._feat_options("fighting_style", _bare_character(), 4, snapshot)
    assert {option["value"]: option["unavailable_reasons"] for option in options} == {
        f"feat:{feat_id}": ["feature"]
        for feat_id in (
            "archery", "defense", "great_weapon_fighting", "two_weapon_fighting",
        )
    }


def test_the_two_paths_agree_for_a_real_character(bundle) -> None:
    """Build accepts Archery; level-up must not then call it unavailable.

    This is the drift the slice removes: two implementations, each missing a
    predicate the other had.
    """

    runtime = Dnd2024Runtime()
    draft = _fighter_draft(runtime)
    assert runtime.validate_character(None, draft) == []
    character = runtime.derive_character(None, draft)

    engine = Dnd2024AdvancementEngine(runtime.load_bundle(LOCALE))
    snapshot = engine.catalog.snapshot("class:fighter", 4)
    options = engine._feat_options("fighting_style", character, 4, snapshot)
    archery = next(option for option in options if option["value"] == "feat:archery")
    # 已经拥有（不可重复）是唯一理由；如果 ``feature`` 也出现，说明升级路径
    # 认为这个战士没有战斗风格特性——那正是建卡与升级分叉的样子。
    assert archery["unavailable_reasons"] == ["already_owned"]


def test_build_path_training_matches_the_derived_sheet(bundle) -> None:
    """The build-time proficiency set is a mirror; mirrors drift.

    ``_draft_proficiencies`` exists because validation runs before derivation,
    so it cannot read the derived sheet.  This test is what keeps the copy
    honest -- without it the mirror would only be discovered the first time a
    proficiency prerequisite was written.
    """

    runtime = Dnd2024Runtime()
    draft = _fighter_draft(runtime)
    character = runtime.derive_character(None, draft)
    derived = character["proficiencies"]
    expected = {
        *(str(ref) for ref in derived.get("armor_category_refs") or []),
        *(str(ref) for ref in derived.get("weapon_category_refs") or []),
        *(str(ref) for ref in derived.get("skill_refs") or []),
        *(str(ref) for ref in derived.get("tool_refs") or []),
    }
    actual = _draft_proficiencies(
        draft,
        bundle.get("class", "fighter"),
        bundle.get("background", "soldier"),
    )
    assert set(actual) == expected


def test_a_retired_prerequisite_field_is_never_silently_dropped() -> None:
    for retired in ("any_ability_minimum", "requires_feature", "requires_spellcasting"):
        with pytest.raises(FeatDefinitionError):
            parse_feat(_feat(**{retired: True}))
