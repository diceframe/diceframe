"""Languages: the catalog, who may pick what, and where it surfaces.

Covers S0-F36 §10.1 L1/L2/L3 (the language rows of §10.5).  Feature-granted
languages (Druidic / Thieves' Cant / Deft Explorer) are covered by the same file
once those features land.
"""

from __future__ import annotations

from src.rulesets.dnd2024.character.builder import Dnd2024CharacterBuilder
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
