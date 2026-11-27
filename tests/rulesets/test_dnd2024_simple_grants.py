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
