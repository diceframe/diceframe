"""Languages: the catalog, who may pick what, and where it surfaces.

Covers S0-F36 §10.1 L1/L2/L3 (the language rows of §10.5).  Feature-granted
languages (Druidic / Thieves' Cant / Deft Explorer) are covered by the same file
once those features land.
"""

from __future__ import annotations

from src.rulesets.dnd2024.character.builder import Dnd2024CharacterBuilder
from src.rulesets.dnd2024.features.resolver import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


def _builder(locale: str = "en") -> Dnd2024CharacterBuilder:
    return Dnd2024CharacterBuilder(Dnd2024Runtime().load_bundle(locale))


def _draft(**overrides) -> dict:
    draft = {
        "name": "Language Test",
        "class_ref": "class:fighter",
        "species_ref": "species:human",
        "background_ref": "background:soldier",
        "species_size": "medium",
        "class_skill_refs": [],
        "class_tool_refs": [],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
        "equipment_package_ref": "equipment_package:standard",
        "background_equipment_package_ref": "equipment_package:standard",
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8,
        },
    }
    draft.update(overrides)
    return draft


# --- L1: the catalog and rarity -------------------------------------------


def test_the_catalog_holds_standard_rare_and_secret_languages() -> None:
    builder = _builder()
    rarity = {
        item["id"]: item["rarity"] for item in builder.bundle.list("language")
    }

    assert rarity["common"] == "standard"
    assert rarity["abyssal"] == "rare"
    assert rarity["druidic"] == "secret"
    assert rarity["thieves_cant"] == "secret"
    # SRD p20 的两张表都要在：7 条罕见 + 2 条秘密。
    assert sum(1 for value in rarity.values() if value == "rare") == 7
    assert sum(1 for value in rarity.values() if value == "secret") == 2


def test_creation_offers_only_standard_languages() -> None:
    choices = _builder().choices(_draft())
    offered = {item["id"] for item in choices["languages"]}

    assert "common" in offered
    assert "abyssal" not in offered
    assert "druidic" not in offered


def test_a_rare_language_cannot_be_taken_at_creation() -> None:
    errors = _builder().validate(_draft(
        language_refs=["language:common", "language:dwarvish", "language:abyssal"],
    ))

    assert any("Standard Language" in line for line in errors)


def test_a_secret_language_cannot_be_taken_at_creation() -> None:
    errors = _builder().validate(_draft(
        language_refs=["language:common", "language:dwarvish", "language:druidic"],
    ))

    assert any("Standard Language" in line for line in errors)


def test_every_new_language_has_both_locales() -> None:
    for locale, expected in (("en", "Abyssal"), ("zh-CN", "深渊语")):
        bundle = Dnd2024Runtime().load_bundle(locale)
        names = {
            item["id"]: item.get("name") for item in bundle.list("language")
        }
        assert names["abyssal"] == expected
        assert names["thieves_cant"] in {"Thieves' Cant", "盗贼黑话"}


# --- L2: monster stat blocks use refs -------------------------------------


def test_every_monster_language_is_a_catalog_ref() -> None:
    bundle = Dnd2024Runtime().load_bundle()
    known = {f"language:{item['id']}" for item in bundle.list("language")}
    catalog = bundle.get("monster_catalog", "srd_monsters")
    assert catalog is not None

    checked = 0
    for monster in catalog["entries"]:
        for ref in monster.get("languages") or []:
            assert ref in known, f"{monster['id']} names an unknown language {ref}"
            checked += 1
    assert checked > 0, "the catalog should still declare languages"


def test_a_monster_language_resolves_to_its_display_name() -> None:
    bundle = Dnd2024Runtime().load_bundle("zh-CN")
    names = {item["id"]: item.get("name") for item in bundle.list("language")}

    # ref 稳定、显示名随 locale：这正是 L2 要统一的东西。
    assert names["common"] == "通用语"
    assert names["goblin"] == "地精语"


# --- L3: the player projection stays out of the combat view ---------------


def test_the_combat_actor_view_does_not_carry_languages() -> None:
    """Language drives no combat rule; the character centre is where it shows.

    Asserting the absence here is deliberate: adding it to the actor view would
    bloat every combat frame for a field nothing reads, and `tool_refs` -- the
    other proficiency that no rule consults -- is already absent for the same
    reason.
    """

    import inspect

    from src.rulesets.dnd2024.combat.view import CombatViewMixin

    source = inspect.getsource(CombatViewMixin._player_view)
    assert "language" not in source


# --- Feature-granted languages (Druidic / Thieves' Cant) -------------------

def _draft_for(runtime: Dnd2024Runtime, class_ref: str, skills: list[str], **over) -> dict:
    draft = {
        "locale": "en",
        "name": "Language Test",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 12, "dex": 14, "con": 13, "int": 10, "wis": 15, "cha": 8,
        },
        "class_ref": class_ref,
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": skills,
        "alignment": "neutral",
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
    }
    choices = runtime.builder_choices(None, draft)
    ability_refs = choices["background_ability_refs"]
    draft["background_ability_bonuses"] = {
        ability_refs[0].split(":", 1)[1]: 2,
        ability_refs[1].split(":", 1)[1]: 1,
    }
    draft["species_size"] = (choices["species_sizes"] or [""])[0]
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"]
    ][: choices["species_skill_count"]]
    draft["species_feat_refs"] = [
        item["ref"] for item in choices["species_feats"]
    ][: choices["species_feat_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    spells = choices.get("class_spells") or {}
    if spells.get("requirements", {}).get("cantrip_count") or spells.get("requirements"):
        rec = choices.get("recommended_class_spells") or {}
        draft["class_spell_choices"] = {
            "cantrip_ids": list(rec.get("cantrip_ids") or []),
            "prepared_spell_ids": list(rec.get("prepared_spell_ids") or []),
        }
    draft["equipment_package_ref"] = choices["equipment_packages"][0]["ref"]
    draft["background_equipment_package_ref"] = (
        choices["background_equipment_packages"][0]["ref"]
    )
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [o["value"] for o in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    draft.update(over)
    return draft


def _canonical(runtime: Dnd2024Runtime, draft: dict) -> dict:
    return runtime.finalize_character(None, draft)["ruleset_character"]


def test_a_druid_knows_druidic_and_always_prepares_speak_with_animals() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(runtime, "class:druid", ["skill:nature", "skill:perception"])

    canonical = _canonical(runtime, draft)

    assert "language:druidic" in canonical["proficiencies"]["language_refs"]
    # 常备法术是特性边界的投影，不是 canonical 的一个子树。
    rows = Dnd2024ClassFeatureResolver(runtime.load_bundle()).spell_grant_rows(
        canonical,
    )
    prepared = [str(row.get("spell_ref") or "") for row in rows]
    assert any("speak_with_animals" in ref for ref in prepared), prepared


def test_a_rogue_knows_thieves_cant_without_spending_its_three_picks() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(
        runtime, "class:rogue",
        ["skill:acrobatics", "skill:stealth", "skill:deception", "skill:persuasion"],
        feature_choices=[{
            "owner": "class:rogue", "feature_id": "thieves_cant",
            "choice_id": "thieves_cant_language", "selected_refs": ["language:gnomish"],
        }],
    )

    canonical = _canonical(runtime, draft)
    refs = canonical["proficiencies"]["language_refs"]

    # 固定授予 + 槽内选择都落在同一个 canonical 字段。
    assert "language:thieves_cant" in refs
    assert "language:gnomish" in refs
    assert refs.count("language:gnomish") == 1


def test_the_thieves_cant_slot_rejects_a_rare_language() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(
        runtime, "class:rogue",
        ["skill:acrobatics", "skill:stealth", "skill:deception", "skill:persuasion"],
        feature_choices=[{
            "owner": "class:rogue", "feature_id": "thieves_cant",
            "choice_id": "thieves_cant_language", "selected_refs": ["language:abyssal"],
        }],
    )

    errors = runtime.validate_character(None, draft)

    assert any("selectable language" in line for line in errors)


def test_a_fighter_learns_neither_secret_language() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(runtime, "class:fighter", ["skill:acrobatics", "skill:athletics"])

    refs = _canonical(runtime, draft)["proficiencies"]["language_refs"]

    assert "language:druidic" not in refs
    assert "language:thieves_cant" not in refs


# --- The check-modifier layer (§10.3) -------------------------------------

def test_a_replacement_reads_the_new_ability_and_keeps_the_proficiency() -> None:
    from src.rulesets.dnd2024.combat.check_modifiers import check_total

    parts = {"ability": "dex", "ability_modifier": 2, "contribution": 2}
    abilities = {"str": 18, "dex": 14, "wis": 10}
    rows = [{"replace_ability": "str", "feature_id": "primal_knowledge"}]

    total, effective, modifier, sources = check_total(
        parts, abilities, default_ability="dex", fallback_total=4, rows=rows,
    )

    # 熟练部分（+2）保留，属性部分换成力量（+4）。
    assert (total, effective, modifier) == (6, "str", 4)
    assert sources == []


def test_an_added_bonus_honours_its_own_floor() -> None:
    from src.rulesets.dnd2024.combat.check_modifiers import check_total

    parts = {"ability": "int", "ability_modifier": 1, "contribution": 2}
    abilities = {"int": 12, "wis": 8}
    rows = [{
        "add": {"source": "ability_modifier", "ability": "wis", "minimum": 1},
        "feature_id": "thaumaturge",
    }]

    total, _effective, _modifier, sources = check_total(
        parts, abilities, default_ability="int", fallback_total=3, rows=rows,
    )

    # 感知 8 -> -1，但特性写了至少 +1。
    assert total == 1 + 2 + 1
    assert sources == ["thaumaturge"]


def test_an_added_bonus_without_a_floor_can_be_negative() -> None:
    from src.rulesets.dnd2024.combat.check_modifiers import check_total

    parts = {"ability": "cha", "ability_modifier": 3, "contribution": 0}
    abilities = {"cha": 16, "wis": 8}
    rows = [{
        "add": {"source": "ability_modifier", "ability": "wis"},
        "feature_id": "elegant_courtier",
    }]

    total, *_ = check_total(
        parts, abilities, default_ability="cha", fallback_total=3, rows=rows,
    )

    # Elegant Courtier 的原文没有 "at least +1"，所以 -1 就是 -1。
    assert total == 3 - 1


def test_a_sheet_without_the_split_falls_back_to_the_total() -> None:
    from src.rulesets.dnd2024.combat.check_modifiers import check_total

    abilities = {"str": 18, "dex": 14}
    rows = [{"replace_ability": "str", "feature_id": "primal_knowledge"}]

    # 旧卡没有 skill_parts：用总量减去默认属性的调整值得出熟练部分。
    total, effective, _modifier, _sources = check_total(
        None, abilities, default_ability="dex", fallback_total=6, rows=rows,
    )

    assert effective == "str"
    assert total == (6 - 2) + 4


def test_a_condition_gated_modifier_stays_off_without_the_condition() -> None:
    from src.rulesets.dnd2024.combat.check_modifiers import active_modifiers

    rows = [{
        "skills": ["skill:acrobatics"],
        "when_condition": "rage",
        "replace_ability": "str",
        "feature_id": "primal_knowledge",
    }]

    assert active_modifiers(rows, skill="acrobatics", ability="dex") == []
    assert active_modifiers(
        rows, skill="acrobatics", ability="dex", conditions={"rage": {"source": "test"}},
    )
    # 作用域之外一个都不生效。
    assert active_modifiers(
        rows, skill="arcana", ability="int", conditions={"rage": {"source": "test"}},
    ) == []


def test_the_declaration_reaches_the_resolver_with_its_source() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle())
    # 3 级才拥有 Primal Knowledge；这里直接给 canonical 的等级结构，不建整张卡。
    character = {
        "build": {"class_levels": [{"class_ref": "class:barbarian", "level": 3}]},
    }

    rows = resolver.check_modifiers(character, "skill_check")

    assert [row["feature_id"] for row in rows] == ["primal_knowledge"]
    assert rows[0]["replace_ability"] == "str"


def test_the_resolver_does_not_hand_out_another_surface_s_modifiers() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle())
    character = {
        "build": {"class_levels": [{"class_ref": "class:barbarian", "level": 3}]},
    }

    # surface 不串：技能修正不会跑到豁免或裸属性检定上。
    assert resolver.check_modifiers(character, "saving_throw") == []
    assert resolver.check_modifiers(character, "ability_check") == []


def test_primal_knowledge_only_offers_barbarian_skills() -> None:
    bundle = Dnd2024Runtime().load_bundle()
    catalog = bundle.get("class_feature_catalog", "srd_class_features")
    feature = next(
        row for row in catalog["classes"]["barbarian"]["features"]
        if row["id"] == "primal_knowledge"
    )

    offered = set(feature["choice"]["enabled_options"])

    assert offered == {
        "skill:animal_handling", "skill:athletics", "skill:intimidation",
        "skill:nature", "skill:perception", "skill:survival",
    }
    assert feature["choice"]["grants"] == "proficiency"
    assert feature["choice"]["requires_proficiency"] is False


def test_bonus_proficiencies_is_three_skills_from_anywhere() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(runtime, "class:bard", ["skill:persuasion", "skill:deception", "skill:performance"])
    draft["features"] = {"subclass_ref": "subclass:college_of_lore"}
    draft["level"] = 3

    slot = next(
        row for row in runtime.builder_choices(None, draft)["skill_choices"]
        if row["feature_id"] == "bonus_proficiencies"
    )

    assert slot["count"] == 3
    offered = [option["value"] for option in slot["options"]]
    assert "skill:arcana" in offered
    assert "skill:medicine" in offered


# --- The two Orders --------------------------------------------------------

def _cleric(level: int = 1, option: str = "") -> dict:
    # 选择记录挂在 canonical 的 features.feature_choices 下，与存档一致。
    features: dict = {}
    if option:
        features["feature_choices"] = [{
            "owner": "class:cleric", "feature_id": "divine_order",
            "choice_id": "divine_order_option",
            "selected_refs": [f"feature:{option}"],
        }]
    return {
        "build": {"class_levels": [{"class_ref": "class:cleric", "level": level}]},
        "features": features,
    }


def test_divine_order_protector_grants_heavy_and_martial_training() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle())

    protector = _cleric(option="divine_order_protector")
    thaumaturge = _cleric(option="divine_order_thaumaturge")

    assert resolver.category_sources(protector, "armor_category_grants") == {"heavy": "divine_order_protector"}
    assert resolver.category_sources(protector, "weapon_category_grants") == {"martial": "divine_order_protector"}
    # 二选一：选了 Thaumaturge 就一无训练。
    assert resolver.category_sources(thaumaturge, "armor_category_grants") == {}
    assert resolver.category_sources(thaumaturge, "weapon_category_grants") == {}


def test_divine_order_thaumaturge_grants_a_cantrip_and_an_intelligence_bonus() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle())

    thaumaturge = _cleric(option="divine_order_thaumaturge")
    protector = _cleric(option="divine_order_protector")

    rows = resolver.check_modifiers(thaumaturge, "skill_check")
    assert [row["feature_id"] for row in rows] == ["divine_order_thaumaturge"]
    assert rows[0]["add"] == {"source": "ability_modifier", "ability": "wis", "minimum": 1}
    # 选了 Protector 就没有这条加值。
    assert resolver.check_modifiers(protector, "skill_check") == []


def test_an_unchosen_order_grants_nothing() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle())

    undecided = _cleric()

    assert resolver.category_sources(undecided, "armor_category_grants") == {}
    assert resolver.category_sources(undecided, "weapon_category_grants") == {}
    assert resolver.check_modifiers(undecided, "skill_check") == []


def test_primal_order_warden_and_magician_are_mutually_exclusive_too() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle())

    def druid(option: str) -> dict:
        return {
            "build": {"class_levels": [{"class_ref": "class:druid", "level": 1}]},
            "features": {"feature_choices": [{
                "owner": "class:druid", "feature_id": "primal_order",
                "choice_id": "primal_order_option",
                "selected_refs": [f"feature:{option}"],
            }]},
        }

    warden = druid("primal_order_warden")
    magician = druid("primal_order_magician")

    assert resolver.category_sources(warden, "armor_category_grants") == {"medium": "primal_order_warden"}
    assert resolver.category_sources(warden, "weapon_category_grants") == {"martial": "primal_order_warden"}
    assert resolver.check_modifiers(warden, "skill_check") == []

    assert resolver.category_sources(magician, "armor_category_grants") == {}
    rows = resolver.check_modifiers(magician, "skill_check")
    assert [row["feature_id"] for row in rows] == ["primal_order_magician"]


def test_an_order_adds_one_cantrip_to_the_capacity() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle()
    from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

    selection = Dnd2024SpellSelection(bundle)
    plain = selection.requirements("class:cleric", 1)
    with_order = selection.requirements(
        "class:cleric", 1, _cleric(option="divine_order_thaumaturge"),
    )

    assert with_order["cantrip_count"] == plain["cantrip_count"] + 1
    # 只加戏法位：准备法术数与法术位不变。
    assert with_order["prepared_spell_count"] == plain["prepared_spell_count"]
    assert with_order["spell_slots"] == plain["spell_slots"]


# --- The paths that used to be wrong --------------------------------------

def test_a_druid_survives_the_wire_format_round_trip() -> None:
    """`build.language_refs` is the creation three, not the merged list.

    Writing the merged list there made `normalize_character_submission` reject
    every druid (it insists on exactly three picks including Common), so a
    druid could not be saved at all.
    """

    runtime = Dnd2024Runtime()
    draft = _draft_for(runtime, "class:druid", ["skill:nature", "skill:perception"])
    sheet = runtime.finalize_character(None, draft)

    normalized = runtime.normalize_character_submission(None, sheet)
    canonical = normalized["ruleset_character"]

    assert canonical["build"]["language_refs"] == [
        "language:common", "language:dwarvish", "language:elvish",
    ]
    assert "language:druidic" in canonical["proficiencies"]["language_refs"]


def test_the_wire_format_keeps_three_picks_even_with_two_extra_languages() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(
        runtime, "class:rogue",
        ["skill:acrobatics", "skill:stealth", "skill:deception", "skill:persuasion"],
        feature_choices=[{
            "owner": "class:rogue", "feature_id": "thieves_cant",
            "choice_id": "thieves_cant_language", "selected_refs": ["language:gnomish"],
        }],
    )
    sheet = runtime.finalize_character(None, draft)

    normalized = runtime.normalize_character_submission(None, sheet)
    canonical = normalized["ruleset_character"]

    assert len(canonical["build"]["language_refs"]) == 3
    assert len(canonical["proficiencies"]["language_refs"]) == 5


def test_a_new_proficiency_from_a_level_up_really_raises_the_skill() -> None:
    """`grants: "proficiency"` must land in `skill_refs`, or it grants nothing."""

    runtime = Dnd2024Runtime()
    draft = _draft_for(runtime, "class:barbarian", ["skill:athletics", "skill:survival"])
    sheet = runtime.finalize_character(None, draft)
    before = sheet["ruleset_character"]["proficiencies"]["skill_values"]["intimidation"]

    while int(sheet["ruleset_character"]["build"]["level"]) < 3:
        answers: dict = {}
        preview = runtime.preview_advancement(None, sheet, {})
        picks = []
        for requirement in preview.get("requirements") or []:
            if requirement.get("id") == "subclass_ref":
                answers["subclass_ref"] = str(requirement["options"][0]["value"])
                continue
            if requirement.get("kind") != "feature_choice":
                continue
            picks.append({
                "owner": str(requirement["owner"]),
                "feature_id": str(requirement["feature_id"]),
                "choice_id": str(requirement["choice_id"]),
                "selected_refs": ["skill:intimidation"],
            })
        if picks:
            answers["feature_choices"] = picks
        preview = runtime.preview_advancement(None, sheet, answers)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, answers)

    after = sheet["ruleset_character"]["proficiencies"]["skill_values"]["intimidation"]

    # 熟练值 = 力量调整值 + PB；力量 12 (+1) 时从 +1 涨到 +1+2。
    assert after == before + int(
        sheet["ruleset_character"]["derived"]["proficiency_bonus"],
    )


def test_creation_offers_the_divine_order_branches() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(runtime, "class:cleric", ["skill:medicine", "skill:history"])

    choices = runtime.builder_choices(None, draft)

    slot = next(
        row for row in choices["feature_option_choices"]
        if row["feature_id"] == "divine_order"
    )
    assert [option["value"] for option in slot["options"]] == [
        "feature:divine_order_protector",
        "feature:divine_order_thaumaturge",
    ]


def test_creation_accepts_a_committed_divine_order() -> None:
    runtime = Dnd2024Runtime()
    draft = _draft_for(runtime, "class:cleric", ["skill:medicine", "skill:history"])

    draft["feature_choices"] = [{
        "owner": "class:cleric", "feature_id": "divine_order",
        "choice_id": "divine_order_option",
        "selected_refs": ["feature:divine_order_protector"],
    }]

    assert runtime.validate_character(None, draft) == []
    canonical = _canonical(runtime, draft)
    assert "armor_category:heavy" in canonical["proficiencies"]["armor_category_refs"]
