"""S0-F4: always-prepared spell grants, cross-list sources, land terrain.

The five subclass tables and the two Bard choice features are declared in the
class feature catalog; this module checks what the runtime does with them:

* the tables are exact and cumulative, and only the owning subclass at or above
  the listed level grants anything;
* always-prepared spells never consume ordinary preparation capacity, but they
  are still the granting class's spells;
* Circle of the Land commits one terrain per Long Rest and *replaces* the
  previous list instead of stacking four;
* Magical Discoveries is two always-prepared picks, Magical Secrets only widens
  the lists a normal pick may come from;
* every grant reports ``automated`` / ``not_automated`` from whether a
  resolvable effect exists, and a ``not_automated`` cast still fails closed.

The item numbers in the docstrings are the task spec's matrix.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.catalog import SpellCatalogError
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from src.engine.modules import ruleset_runtime

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_instance,
    start_combat,
)

LOCALE = "en"

ABILITIES = {
    "class:bard": {"str": 8, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 15},
    "class:cleric": {"str": 12, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 13},
    "class:druid": {"str": 10, "dex": 13, "con": 14, "int": 8, "wis": 15, "cha": 12},
    "class:paladin": {"str": 15, "dex": 10, "con": 13, "int": 8, "wis": 12, "cha": 14},
    "class:sorcerer": {"str": 8, "dex": 13, "con": 14, "int": 12, "wis": 10, "cha": 15},
    "class:warlock": {"str": 8, "dex": 13, "con": 14, "int": 12, "wis": 10, "cha": 15},
}

# The exact SRD tables, in the order the book prints them.  Kept as literals so
# a content edit that drops a spell fails here even though the declaration and
# the assertion would otherwise be the same file.
LIFE = {
    3: ["spell:aid", "spell:bless", "spell:cure_wounds", "spell:lesser_restoration"],
    5: ["spell:mass_healing_word", "spell:revivify"],
    7: ["spell:aura_of_life", "spell:death_ward"],
    9: ["spell:greater_restoration", "spell:mass_cure_wounds"],
}
OATH = {
    3: ["spell:protection_from_evil_and_good", "spell:shield_of_faith"],
    5: ["spell:aid", "spell:zone_of_truth"],
    9: ["spell:beacon_of_hope", "spell:dispel_magic"],
    13: ["spell:freedom_of_movement", "spell:guardian_of_faith"],
    17: ["spell:commune", "spell:flame_strike"],
}
DRACONIC = {
    3: [
        "spell:alter_self", "spell:chromatic_orb", "spell:command",
        "spell:dragon_s_breath",
    ],
    5: ["spell:fear", "spell:fly"],
    7: ["spell:arcane_eye", "spell:charm_monster"],
    9: ["spell:legend_lore", "spell:summon_dragon"],
}
FIEND = {
    3: ["spell:burning_hands", "spell:command", "spell:scorching_ray", "spell:suggestion"],
    5: ["spell:fireball", "spell:stinking_cloud"],
    7: ["spell:fire_shield", "spell:wall_of_fire"],
    9: ["spell:geas", "spell:insect_plague"],
}
LAND = {
    "arid": {
        3: ["spell:blur", "spell:burning_hands", "spell:fire_bolt"],
        5: ["spell:fireball"],
        7: ["spell:blight"],
        9: ["spell:wall_of_stone"],
    },
    "polar": {
        3: ["spell:fog_cloud", "spell:hold_person", "spell:ray_of_frost"],
        5: ["spell:sleet_storm"],
        7: ["spell:ice_storm"],
        9: ["spell:cone_of_cold"],
    },
    "temperate": {
        3: ["spell:misty_step", "spell:shocking_grasp", "spell:sleep"],
        5: ["spell:lightning_bolt"],
        7: ["spell:freedom_of_movement"],
        9: ["spell:tree_stride"],
    },
    "tropical": {
        3: ["spell:acid_splash", "spell:ray_of_sickness", "spell:web"],
        5: ["spell:stinking_cloud"],
        7: ["spell:polymorph"],
        9: ["spell:insect_plague"],
    },
}
LAND_TERRAINS = ("arid", "polar", "temperate", "tropical")

TABLES = {
    "class:cleric": ("subclass:life_domain", LIFE),
    "class:paladin": ("subclass:oath_of_devotion", OATH),
    "class:sorcerer": ("subclass:draconic_sorcery", DRACONIC),
    "class:warlock": ("subclass:fiend_patron", FIEND),
}


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def selection(runtime: Dnd2024Runtime) -> Dnd2024SpellSelection:
    return Dnd2024SpellSelection(runtime.load_bundle(LOCALE))


@pytest.fixture(scope="module")
def rest(runtime: Dnd2024Runtime) -> Dnd2024RestEngine:
    return Dnd2024RestEngine(runtime.load_bundle(LOCALE))


# --------------------------------------------------------------------------
# sheets through the real creation and advancement paths
# --------------------------------------------------------------------------


def _class_id(class_ref: str) -> str:
    return class_ref.removeprefix("class:")


def _probe(
    canonical: dict, class_ref: str, level: int, subclass_ref: str = "",
) -> dict:
    """The canonical sheet as it will look at ``level``.

    Spell selection is resolved against the level being advanced *to*: Bard
    9 -> 10 only owns Magical Secrets after the level is gained, so reading
    ownership from the pre-advance sheet would never offer the four lists.
    """

    probe = deepcopy(canonical)
    probe["build"] = {
        **(probe.get("build") or {}),
        "class_levels": [{"class_ref": class_ref, "level": level}],
    }
    features = probe.get("features")
    features = dict(features) if isinstance(features, dict) else {}
    features["subclass_ref"] = subclass_ref
    probe["features"] = features
    return probe


def _spell_choices(
    selection: Dnd2024SpellSelection,
    class_ref: str,
    level: int,
    character: dict,
    grant_refs: dict[str, list[str]] | None = None,
) -> dict:
    options = selection.options(class_ref, level, character)
    requirements = options["requirements"]
    cantrips = [row["ref"] for row in options["cantrips"]]
    leveled = [row["ref"] for row in options["leveled_spells"]]
    block = (character.get("spellcasting") or {}).get("class") or {}

    def keep_then_fill(current: list[str], pool: list[str], count: int) -> list[str]:
        """升级只加不减：先保住已选，再按容量补足。

        从头重选会让"同一份列表再提交一次"看起来像整批替换，而 SRD 的
        替换预算是按*丢掉的法术*计的。
        """

        kept = [ref for ref in current if ref in pool]
        return (kept + [ref for ref in pool if ref not in kept])[:count]

    cantrips = keep_then_fill(
        list(block.get("cantrip_refs") or []), cantrips, int(requirements["cantrip_count"]),
    )
    spellbook: list[str] = []
    minimum = int(requirements.get("spellbook_minimum") or 0)
    if minimum:
        # 法术书是已付过代价的资产：只往里加，不重排。
        book = [ref for ref in (block.get("spellbook_refs") or []) if ref in leveled]
        spellbook = keep_then_fill(book, leveled, max(minimum, len(book)))
    prepared_pool = spellbook or leveled
    prepared = keep_then_fill(
        list(block.get("prepared_spell_refs") or []),
        prepared_pool,
        int(requirements["prepared_spell_count"]),
    )
    choices: dict = {
        "cantrip_refs": cantrips[: int(requirements["cantrip_count"])],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }
    slots = options.get("grant_slots") or []
    if slots:
        wanted = grant_refs or {}
        choices["spell_grants"] = {
            slot["feature_id"]: list(
                wanted.get(slot["feature_id"])
                or [option["ref"] for option in slot["options"]][: int(slot["count"])]
            )
            for slot in slots
        }
    return choices


def _draft(
    runtime: Dnd2024Runtime, class_ref: str, abilities: dict[str, int],
) -> dict:
    class_id = _class_id(class_ref)
    draft: dict = {
        "locale": LOCALE,
        "name": "Hero",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": dict(abilities),
        "class_ref": class_ref,
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "equipment_package_ref": f"equipment_package:{class_id}_a",
        "background_equipment_package_ref": "equipment_package:acolyte_b",
        "alignment": "neutral",
    }
    choices = runtime.builder_choices(None, draft)
    ability_refs = choices["background_ability_refs"]
    draft["background_ability_bonuses"] = {
        ability_refs[0].split(":", 1)[1]: 2,
        ability_refs[1].split(":", 1)[1]: 1,
    }
    draft["species_size"] = (choices["species_sizes"] or [""])[0]
    draft["class_skill_refs"] = [
        item["ref"] for item in choices["class_skills"]
    ][: choices["class_skill_count"]]
    # 物种技能不能与职业技能重复，所以先定职业再挑物种。
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"]
        if item["ref"] not in draft["class_skill_refs"]
    ][: choices["species_skill_count"]]
    draft["species_feat_refs"] = [
        item["ref"] for item in choices["species_feats"]
    ][: choices["species_feat_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    draft["language_refs"] = ["language:common", "language:dwarvish", "language:elvish"]
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [
                option["value"] for option in spec["options"]
            ][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    draft["class_spell_choices"] = _spell_choices(
        Dnd2024SpellSelection(runtime.load_bundle(LOCALE)),
        class_ref,
        1,
        {"build": {"class_levels": [{"class_ref": class_ref, "level": 1}]}},
    )
    errors = runtime.validate_character(None, draft)
    assert errors == [], errors
    return draft


def _picks(preview: dict, canonical: dict) -> dict:
    """First legal answer per requirement, keeping ability scores under 20."""

    picks = advancement_choices(preview)
    for requirement in preview.get("requirements") or []:
        if requirement.get("id") != "ability_score_increases":
            continue
        allowed = requirement.get("allowed", "any")
        if allowed == "any":
            abilities = canonical.get("abilities") or {}
            picks["ability_score_increases"] = {
                min(abilities, key=lambda key: int(abilities[key])): int(
                    requirement.get("total", 2)
                )
            }
    return picks


_SHEET_CACHE: dict[tuple, dict] = {}


def _sheet(
    runtime: Dnd2024Runtime,
    class_ref: str,
    level: int = 1,
    *,
    grant_refs: dict[str, list[str]] | None = None,
) -> dict:
    """A character built and advanced through the authoritative paths.

    Building one runs every level-up from 1, so identical fixtures are memoised
    and handed out as copies: several tests edit the prepared list in place to
    probe one rule and must not disturb the next test.
    """

    key = (
        class_ref,
        level,
        tuple(sorted(
            (name, tuple(refs)) for name, refs in (grant_refs or {}).items()
        )),
    )
    cached = _SHEET_CACHE.get(key)
    if cached is not None:
        return deepcopy(cached)
    selection = Dnd2024SpellSelection(runtime.load_bundle(LOCALE))
    draft = _draft(runtime, class_ref, ABILITIES[class_ref])
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        canonical = sheet["ruleset_character"]
        next_level = int(canonical["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = _picks(preview, canonical)
        subclass = str(
            picks.get("subclass_ref")
            or (canonical.get("features") or {}).get("subclass_ref")
            or ""
        )
        probe = _probe(canonical, class_ref, next_level, subclass)
        picks["class_spell_choices"] = _spell_choices(
            selection, class_ref, next_level, probe, grant_refs,
        )
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    _SHEET_CACHE[key] = deepcopy(sheet)
    return sheet


def _canonical(sheet: dict) -> dict:
    return sheet["ruleset_character"]


_RESOLVER: Dnd2024ClassFeatureResolver | None = None


def _grant_rows(canonical: dict) -> list[dict]:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle(LOCALE))
    return _RESOLVER.spell_grant_rows(canonical)


def _grant_refs(canonical: dict) -> list[str]:
    return [row["spell_ref"] for row in _grant_rows(canonical)]


def _with_terrain(canonical: dict, terrain: str) -> dict:
    updated = deepcopy(canonical)
    block = updated.setdefault("spellcasting", {}).setdefault("class", {})
    block.setdefault("spell_grant_choices", {})["circle_of_the_land_spells"] = {
        "terrain": terrain
    }
    return updated


def _druid(runtime: Dnd2024Runtime, level: int = 3, terrain: str | None = None) -> dict:
    canonical = _canonical(_sheet(runtime, "class:druid", level))
    return _with_terrain(canonical, terrain) if terrain else canonical


# --------------------------------------------------------------------------
# matrix 1-6: the tables themselves
# --------------------------------------------------------------------------


def test_matrix_1_tables_grant_only_to_their_own_subclass_at_level(
    runtime: Dnd2024Runtime,
) -> None:
    for class_ref, (subclass, table) in TABLES.items():
        low = min(table)
        below = _canonical(_sheet(runtime, class_ref, low - 1)) if low > 1 else None
        if below is not None:
            assert _grant_rows(below) == [], (class_ref, low)
        wrong = _probe(
            _canonical(_sheet(runtime, class_ref, low)),
            class_ref, low, "subclass:college_of_lore",
        )
        assert _grant_rows(wrong) == [], class_ref
        right = _probe(
            _canonical(_sheet(runtime, class_ref, low)), class_ref, low, subclass,
        )
        assert _grant_refs(right) == table[low], class_ref


def test_matrix_2_life_domain_rows_and_level_nine_accumulation(
    runtime: Dnd2024Runtime,
) -> None:
    for level in (3, 5, 7, 9):
        canonical = _canonical(_sheet(runtime, "class:cleric", level))
        expected = [ref for row, refs in LIFE.items() if row <= level for ref in refs]
        assert _grant_refs(canonical) == expected, level
    assert len(_grant_refs(_canonical(_sheet(runtime, "class:cleric", 9)))) == 10


def test_matrix_3_oath_rows_have_no_seventh_level_node(
    runtime: Dnd2024Runtime,
) -> None:
    for level in (3, 5, 9, 13, 17):
        canonical = _canonical(_sheet(runtime, "class:paladin", level))
        expected = [ref for row, refs in OATH.items() if row <= level for ref in refs]
        assert _grant_refs(canonical) == expected, level
    seven = _canonical(_sheet(runtime, "class:paladin", 7))
    assert _grant_refs(seven) == [ref for refs in OATH.values() for ref in refs][:4]


def test_matrix_4_draconic_uses_the_page_seventy_table(
    runtime: Dnd2024Runtime,
) -> None:
    three = _canonical(_sheet(runtime, "class:sorcerer", 3))
    assert _grant_refs(three) == DRACONIC[3]
    assert "spell:dragon_s_breath" in _grant_refs(three)
    nine = _canonical(_sheet(runtime, "class:sorcerer", 9))
    assert _grant_refs(nine) == [ref for refs in DRACONIC.values() for ref in refs]


def test_matrix_5_fiend_table_reuses_the_canonical_spells(
    runtime: Dnd2024Runtime,
) -> None:
    nine = _canonical(_sheet(runtime, "class:warlock", 9))
    assert _grant_refs(nine) == [ref for refs in FIEND.values() for ref in refs]
    refs = _grant_refs(nine)
    assert refs.count("spell:command") == 1
    catalog = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).catalog
    assert catalog.get("spell:command")["class_refs"] == [
        "class:bard", "class:cleric", "class:paladin"
    ]


@pytest.mark.parametrize("terrain", LAND_TERRAINS)
def test_matrix_6_land_tables_match_each_terrain(
    runtime: Dnd2024Runtime, terrain: str,
) -> None:
    for level in (3, 5, 7, 9):
        canonical = _druid(runtime, level, terrain)
        expected = [
            ref for row, refs in LAND[terrain].items() if row <= level for ref in refs
        ]
        assert _grant_refs(canonical) == expected, (terrain, level)


# --------------------------------------------------------------------------
# matrix 7-10: capacity, ability, and duplicate sources
# --------------------------------------------------------------------------


@pytest.mark.parametrize("terrain", LAND_TERRAINS)
def test_matrix_7_land_cantrips_are_granted_and_cost_no_capacity(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection, terrain: str,
) -> None:
    canonical = _druid(runtime, 3, terrain)
    view = selection.prepared_view(canonical)
    cantrip_refs = {row["spell_ref"] for row in view["cantrips"]}
    granted_cantrips = {
        ref for ref in LAND[terrain][3]
        if (selection.catalog.get(ref) or {}).get("level") == 0
    }
    assert granted_cantrips
    assert granted_cantrips <= cantrip_refs
    capacity = view["capacity"]
    assert capacity["cantrip_used"] == len(
        canonical["spellcasting"]["class"]["cantrip_refs"]
    )
    assert capacity["cantrip_used"] <= capacity["cantrip"]
    assert capacity["granted"] == len(_grant_refs(canonical))


def test_matrix_8_grants_never_consume_prepared_capacity(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _canonical(_sheet(runtime, "class:cleric", 9))
    bare = _probe(canonical, "class:cleric", 9, "")
    requirements = selection.requirements("class:cleric", 9, canonical)
    view = selection.prepared_view(canonical)
    without_grants = selection.prepared_view(bare)
    assert view["capacity"]["prepared"] == requirements["prepared_spell_count"]
    assert view["capacity"]["prepared"] == without_grants["capacity"]["prepared"]
    assert view["capacity"]["prepared_used"] == without_grants["capacity"]["prepared_used"]
    assert view["capacity"]["prepared_used"] == requirements["prepared_spell_count"]
    assert view["capacity"]["granted"] == 10
    over = {
        "cantrip_refs": list(canonical["spellcasting"]["class"]["cantrip_refs"]),
        "prepared_spell_refs": list(
            canonical["spellcasting"]["class"]["prepared_spell_refs"]
        ) + ["spell:guidance"],
    }
    _parsed, errors = selection.validate(
        "class:cleric", 9, over, character=canonical,
    )
    assert any("must contain exactly" in error for error in errors)


def test_matrix_9_grants_cast_with_the_granting_class_ability(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _druid(runtime, 3, "arid")
    rows = {row["spell_ref"]: row for row in _grant_rows(canonical)}
    assert rows["spell:fire_bolt"]["spell_ability"] == "wis"
    # Fire Bolt is printed on the Sorcerer/Wizard list; a Land Druid still casts
    # it with Wisdom, so the spell's own list must not leak into the ability.
    assert "class:druid" not in selection.catalog.get("spell:fire_bolt")["class_refs"]
    engine, instance = monk_instance(runtime, {"ruleset_character": canonical})
    actor = engine._player_view("gm", canonical)
    proficiency = int(actor["proficiency_bonus"])
    wisdom = (int(actor["abilities"]["wis"]) - 10) // 2
    intelligence = (int(actor["abilities"]["int"]) - 10) // 2
    assert actor["spell_attack_bonus"] == proficiency + wisdom
    assert actor["spell_attack_bonus"] != proficiency + intelligence
    assert instance is not None


def test_matrix_10_one_effective_entry_per_spell_and_sources_stay_separate(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _druid(runtime, 7, "temperate")
    block = canonical["spellcasting"]["class"]
    prepared = list(block["prepared_spell_refs"])
    assert "spell:freedom_of_movement" not in prepared
    prepared[0] = "spell:freedom_of_movement"
    _parsed, errors = selection.validate(
        "class:druid", 7,
        {"cantrip_refs": list(block["cantrip_refs"]), "prepared_spell_refs": prepared},
        character=canonical,
    )
    assert errors == []
    block["prepared_spell_refs"] = prepared
    view = selection.prepared_view(canonical)
    entries = [row for row in view["prepared"] if row["spell_ref"] == "spell:freedom_of_movement"]
    assert len(entries) == 1
    kinds = sorted(source["kind"] for source in entries[0]["sources"])
    assert kinds == ["class_feature", "class_list"]


# --------------------------------------------------------------------------
# matrix 11-14: the Long Rest transaction
# --------------------------------------------------------------------------


def test_matrix_11_each_terrain_switch_revokes_the_previous_list(
    runtime: Dnd2024Runtime, rest: Dnd2024RestEngine,
) -> None:
    canonical = _druid(runtime, 9, "arid")
    for terrain in ("polar", "temperate", "tropical", "arid"):
        result = rest.complete_long_rest(deepcopy(canonical), land_terrain=terrain)
        canonical = result["character"]
        assert _grant_refs(canonical) == [
            ref for row, refs in LAND[terrain].items() for ref in refs
        ]
        event = next(
            row for row in result["events"] if row["type"] == "spell_grant_terrain"
        )
        assert event["after"] == terrain
        assert event["changed"] is True


def test_matrix_12_keeping_the_terrain_is_idempotent_and_missing_never_switches(
    runtime: Dnd2024Runtime, rest: Dnd2024RestEngine,
) -> None:
    canonical = _druid(runtime, 5, "polar")
    same = rest.complete_long_rest(deepcopy(canonical), land_terrain="polar")
    event = next(
        row for row in same["events"] if row["type"] == "spell_grant_terrain"
    )
    assert event["changed"] is False
    assert _grant_refs(same["character"]) == _grant_refs(canonical)

    kept = rest.complete_long_rest(deepcopy(canonical))
    assert _grant_refs(kept["character"]) == _grant_refs(canonical)
    assert not [row for row in kept["events"] if row["type"] == "spell_grant_terrain"]

    unset = _druid(runtime, 5)
    missing = rest.complete_long_rest(deepcopy(unset))
    assert _grant_refs(missing["character"]) == []
    assert [row["type"] for row in missing["events"]].count(
        "spell_grant_terrain_missing"
    ) == 1
    assert "spell_grant_choices" not in missing["character"]["spellcasting"]["class"]


def test_matrix_13_a_second_source_survives_the_terrain_switch(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection, rest: Dnd2024RestEngine,
) -> None:
    canonical = _druid(runtime, 7, "temperate")
    block = canonical["spellcasting"]["class"]
    prepared = list(block["prepared_spell_refs"])
    prepared[0] = "spell:freedom_of_movement"
    block["prepared_spell_refs"] = prepared
    switched = rest.complete_long_rest(deepcopy(canonical), land_terrain="arid")
    block = switched["character"]["spellcasting"]["class"]
    assert "spell:freedom_of_movement" in block["prepared_spell_refs"]
    assert "spell:freedom_of_movement" not in _grant_refs(switched["character"])
    view = selection.prepared_view(switched["character"])
    entry = next(
        row for row in view["prepared"] if row["spell_ref"] == "spell:freedom_of_movement"
    )
    assert [source["kind"] for source in entry["sources"]] == ["class_list"]


def test_matrix_14_an_illegal_terrain_leaves_the_whole_rest_untouched(
    runtime: Dnd2024Runtime, rest: Dnd2024RestEngine,
) -> None:
    canonical = _druid(runtime, 5, "arid")
    canonical["resources"]["hp"] = 1
    canonical["spellcasting"]["class"]["slots_current"] = {"1": 0, "2": 0, "3": 0}
    with pytest.raises(RestError):
        rest.complete_long_rest(deepcopy(canonical), land_terrain="swamp")
    assert canonical["resources"]["hp"] == 1
    assert canonical["spellcasting"]["class"]["spell_grant_choices"] == {
        "circle_of_the_land_spells": {"terrain": "arid"}
    }
    healed = rest.complete_long_rest(deepcopy(canonical), land_terrain="polar")
    assert healed["character"]["resources"]["hp"] == canonical["resources"]["max_hp"]
    assert healed["character"]["spellcasting"]["class"]["slots_current"] == (
        healed["character"]["spellcasting"]["class"]["slots_max"]
    )
    assert "spell:fireball" not in _grant_refs(healed["character"])


# --------------------------------------------------------------------------
# matrix 15-18: Magical Discoveries and Magical Secrets
# --------------------------------------------------------------------------


def _lore_bard(
    runtime: Dnd2024Runtime, level: int = 6, picks: tuple[str, str] | None = None,
) -> dict:
    grants = {"magical_discoveries": list(picks)} if picks else None
    return _canonical(_sheet(runtime, "class:bard", level, grant_refs=grants))


def test_matrix_15_magical_discoveries_is_exactly_two_always_prepared_picks(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    slots = selection.options("class:bard", 6, _lore_bard(runtime, 6))["grant_slots"]
    assert [slot["feature_id"] for slot in slots] == ["magical_discoveries"]
    assert slots[0]["count"] == 2
    assert set(slots[0]["spell_lists"]) == {
        "class:cleric", "class:druid", "class:wizard"
    }
    for picks in (
        ("spell:bless", "spell:guidance"),
        ("spell:entangle", "spell:shield"),
        ("spell:mage_hand", "spell:cure_wounds"),
    ):
        canonical = _lore_bard(runtime, 6, picks)
        rows = _grant_rows(canonical)
        assert [row["spell_ref"] for row in rows] == list(picks)
        assert [row["granted_level"] for row in rows] == [6, 6]
        assert {row["feature_id"] for row in rows} == {"magical_discoveries"}
        block = canonical["spellcasting"]["class"]
        view = selection.prepared_view(canonical)
        assert view["capacity"]["granted"] == 2
        assert view["capacity"]["prepared_used"] == block["prepared_capacity"]
        _parsed, errors = selection.validate(
            "class:bard", 6,
            {
                "cantrip_refs": list(block["cantrip_refs"]),
                "prepared_spell_refs": list(block["prepared_spell_refs"]),
                "spell_grants": {"magical_discoveries": list(picks)},
            },
            character=canonical,
        )
        assert errors == [], (picks, errors)


def test_matrix_16_magical_discoveries_level_and_replacement_limits(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _lore_bard(runtime, 6, ("spell:bless", "spell:guidance"))
    block = canonical["spellcasting"]["class"]

    def check(grants: dict, base: dict | None = None) -> list[str]:
        source = base or canonical
        _parsed, errors = selection.validate(
            "class:bard", 6,
            {
                "cantrip_refs": list(block["cantrip_refs"]),
                "prepared_spell_refs": list(block["prepared_spell_refs"]),
                "spell_grants": grants,
            },
            character=source,
        )
        return errors

    assert check({"magical_discoveries": ["spell:bless"]}) != []
    assert check({"magical_discoveries": ["spell:bless", "spell:bless"]}) != []
    assert check({"magical_discoveries": ["spell:bless", "spell:hex"]}) != []
    assert check({"magical_discoveries": ["spell:bless", "spell:polymorph"]}) != []
    assert check({"magical_discoveries": ["spell:bless", "spell:not_a_spell"]}) != []
    # 一个戏法与一个当前有槽环阶都合法；换掉其中一项仍合法。
    assert check({"magical_discoveries": ["spell:bless", "spell:shield"]}) == []
    errors = check({"magical_discoveries": ["spell:shield", "spell:mage_armor"]})
    assert any("replace at most 1" in error for error in errors)
    # 同一槽位不重提交时保持原样，不能被当成"清空"。
    parsed, errors = selection.validate(
        "class:bard", 6,
        {
            "cantrip_refs": list(block["cantrip_refs"]),
            "prepared_spell_refs": list(block["prepared_spell_refs"]),
        },
        character=canonical,
    )
    assert errors == []
    assert parsed["spell_grant_choices"]["magical_discoveries"]["spells"] == [
        "spell:bless", "spell:guidance",
    ]
    unset = deepcopy(canonical)
    unset["spellcasting"]["class"].pop("spell_grant_choices")
    parsed, errors = selection.validate(
        "class:bard", 6,
        {
            "cantrip_refs": list(block["cantrip_refs"]),
            "prepared_spell_refs": list(block["prepared_spell_refs"]),
        },
        character=unset,
    )
    # 还没选不是"非法提交"：槽位保持开放并照实报告，不能被当作错误，否则
    # 任何只想提交普通准备的调用方都无法通过 6 级。
    assert errors == []
    assert "magical_discoveries" not in parsed["spell_grant_choices"]
    open_slots = selection.grant_slots(6, unset)
    assert [(slot["feature_id"], slot["count"], slot["selected_refs"])
            for slot in open_slots] == [("magical_discoveries", 2, [])]
    # 但"提交了内容却不完整"仍然硬失败。
    _parsed, errors = selection.validate(
        "class:bard", 6,
        {
            "cantrip_refs": list(block["cantrip_refs"]),
            "prepared_spell_refs": list(block["prepared_spell_refs"]),
            "spell_grants": {"magical_discoveries": ["spell:shield"]},
        },
        character=unset,
    )
    assert any("must contain exactly 2" in error for error in errors)


def test_matrix_17_magical_secrets_widens_lists_without_widening_capacity(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    nine = _lore_bard(runtime, 9)
    ten = _lore_bard(runtime, 10)
    nine_requirements = selection.requirements("class:bard", 9, nine)
    ten_requirements = selection.requirements("class:bard", 10, ten)
    assert nine_requirements["prepared_list_expansion"] == []
    assert ten_requirements["prepared_list_expansion"] == [
        "class:bard", "class:cleric", "class:druid", "class:wizard",
    ]
    assert (
        ten_requirements["prepared_spell_count"]
        - nine_requirements["prepared_spell_count"]
    ) == 1
    nine_options = selection.options("class:bard", 9, nine)
    ten_options = selection.options("class:bard", 10, ten)
    assert not any(
        row["ref"] == "spell:fireball" for row in nine_options["leveled_spells"]
    )
    assert any(row["ref"] == "spell:fireball" for row in ten_options["leveled_spells"])

    block = ten["spellcasting"]["class"]
    prepared = list(block["prepared_spell_refs"])
    if "spell:fireball" not in prepared:
        prepared[0] = "spell:fireball"
    parsed, errors = selection.validate(
        "class:bard", 10,
        {"cantrip_refs": list(block["cantrip_refs"]), "prepared_spell_refs": prepared},
        character=ten,
    )
    assert errors == [], errors
    assert "spell:fireball" in parsed["prepared_spell_refs"]
    nine_block = nine["spellcasting"]["class"]
    prepared = list(nine_block["prepared_spell_refs"])
    prepared[0] = "spell:fireball"
    _parsed, errors = selection.validate(
        "class:bard", 9,
        {"cantrip_refs": list(nine_block["cantrip_refs"]), "prepared_spell_refs": prepared},
        character=nine,
    )
    assert any("not on the class:bard spell list" in error for error in errors)


def test_matrix_18_secrets_are_ordinary_bard_spells_not_always_prepared(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    ten = _lore_bard(runtime, 10)
    block = ten["spellcasting"]["class"]
    picks = tuple(
        ref for ref in (
            "spell:entangle", "spell:mage_armor", "spell:guidance", "spell:shield",
        )
        if ref not in block["prepared_spell_refs"]
    )[:2]
    assert len(picks) == 2
    ten = _lore_bard(runtime, 10, picks)
    block = ten["spellcasting"]["class"]
    prepared = list(block["prepared_spell_refs"])
    if "spell:fireball" not in prepared:
        prepared[0] = "spell:fireball"
    configured = selection.configure(
        ten,
        {
            "cantrip_refs": list(block["cantrip_refs"]),
            "prepared_spell_refs": prepared,
            "spell_grants": {"magical_discoveries": list(picks)},
        },
    )
    block = configured["spellcasting"]["class"]
    assert block["prepared_capacity"] == ten["spellcasting"]["class"]["prepared_capacity"]
    assert "spell:fireball" in block["prepared_spell_refs"]
    view = selection.prepared_view(configured)
    fireball = next(
        row for row in view["prepared"] if row["spell_ref"] == "spell:fireball"
    )
    assert [source["kind"] for source in fireball["sources"]] == ["class_list"]
    bless = next(row for row in view["prepared"] if row["spell_ref"] == picks[0])
    assert [source["kind"] for source in bless["sources"]] == ["class_feature"]
    assert view["capacity"]["prepared_used"] == block["prepared_capacity"]


# --------------------------------------------------------------------------
# matrix 19-21: automation status and fail closed
# --------------------------------------------------------------------------


def _actor(runtime: Dnd2024Runtime, canonical: dict):
    engine, instance = monk_instance(runtime, {"ruleset_character": canonical})
    start_combat(engine, instance)
    return engine, instance, engine._player_view("gm", canonical)


def test_matrix_19_grants_report_automation_from_the_real_effects(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _canonical(_sheet(runtime, "class:cleric", 3))
    engine, _instance, actor = _actor(runtime, canonical)
    statuses = {row["spell_ref"]: row["automation_status"] for row in actor["spell_grants"]}
    assert statuses["spell:bless"] == "automated"
    assert statuses["spell:cure_wounds"] == "automated"
    assert statuses["spell:aid"] == "not_automated"
    assert statuses["spell:lesser_restoration"] == "not_automated"
    # 施法投影与角色表投影必须同源：两处都从 spell_effects 派生，不能一处
    # 说 automated 而另一处说 not_automated。
    view = {
        row["spell_ref"]: row["automation_status"]
        for row in selection.prepared_view(canonical)["prepared"]
    }
    assert view["spell:aid"] == "not_automated"
    assert view["spell:bless"] == "automated"
    assert view["spell:lesser_restoration"] == "not_automated"


def test_matrix_20_an_unautomated_grant_cannot_be_cast_and_costs_nothing(
    runtime: Dnd2024Runtime,
) -> None:
    canonical = _canonical(_sheet(runtime, "class:cleric", 3))
    engine, instance, actor = _actor(runtime, canonical)
    state = ruleset_runtime.state(instance)
    slots_before = deepcopy(canonical["spellcasting"]["class"]["slots_current"])
    result = engine.resolve_intent(
        instance,
        {
            "intent_id": "cast-aid",
            "type": "cast_spell",
            "expected_version": state["version"],
            "submitted_by": "gm",
            "actor_id": "player:gm",
            "spell_ref": "spell:aid",
            "slot_level": 1,
            "target_id": "player:gm",
        },
        SequenceRng([3, 4, 5]),
    )
    assert result["ok"] is False
    assert "not deterministically automated" in str(result)
    after = ruleset_runtime.state(instance)
    assert after["version"] == state["version"]
    assert instance.get_character_sheet("gm")["ruleset_character"][
        "spellcasting"
    ]["class"]["slots_current"] == slots_before
    statuses = {
        row["spell_ref"]: row["blocked_reason"]
        for row in engine._spell_statuses(instance, actor, state["combat"]["economy"])
        if row["spell_ref"] == "spell:aid"
    }
    assert statuses == {"spell:aid": "not_automated"}


def test_matrix_21_a_granted_automated_spell_still_resolves(
    runtime: Dnd2024Runtime,
) -> None:
    canonical = _druid(runtime, 3, "arid")
    engine, instance, actor = _actor(runtime, canonical)
    assert "spell:fire_bolt" in actor["spell_refs"]
    available = {
        row["spell_ref"] for row in engine._available_spells(
            instance, actor, ruleset_runtime.state(instance)["combat"]["economy"],
        )
    }
    assert "spell:fire_bolt" in available
    result = engine.resolve_intent(
        instance,
        {
            "intent_id": "cast-fire-bolt",
            "type": "cast_spell",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm",
            "actor_id": "player:gm",
            "spell_ref": "spell:fire_bolt",
            "slot_level": 0,
            "target_id": "enemy:goblin-1",
        },
        SequenceRng([15, 6]),
    )
    assert result["ok"] is True, result
    assert engine.apply_batch(instance, result["event_batch"])["applied"] is True
    assert any(
        event["type"] == "dnd2024.spell.cast"
        for event in result["event_batch"]["events"]
    )


# --------------------------------------------------------------------------
# matrix 22-24: persistence, fail closed, and existing contracts
# --------------------------------------------------------------------------


def test_matrix_22_rebuild_and_round_trip_keep_sources_and_terrain(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _druid(runtime, 9, "tropical")
    restored = json.loads(json.dumps(canonical))
    assert _grant_rows(restored) == _grant_rows(canonical)
    assert restored == canonical
    advanced = _sheet(runtime, "class:druid", 10)
    assert _grant_refs(advanced) == []
    carried = deepcopy(restored)
    carried["build"]["class_levels"][0]["level"] = 10
    assert _grant_refs(carried) == _grant_refs(canonical)
    view = selection.prepared_view(restored)
    again = selection.prepared_view(restored)
    assert view == again

    # 走真实升级路径时地形选择也必须活下来：授予是按地形重算的，
    # 存档里的选择不能被升级重建成"未选"。
    sheet = {"ruleset_character": deepcopy(restored)}
    preview = runtime.preview_advancement(None, sheet, {})
    picks = _picks(preview, restored)
    probe = _probe(restored, "class:druid", 10, "subclass:circle_of_the_land")
    picks["class_spell_choices"] = _spell_choices(
        selection, "class:druid", 10, probe,
    )
    preview = runtime.preview_advancement(None, sheet, picks)
    assert preview["ok"] is True, preview["errors"]
    advanced = runtime.apply_advancement(None, sheet, picks)["ruleset_character"]
    assert advanced["spellcasting"]["class"]["spell_grant_choices"] == {
        "circle_of_the_land_spells": {"terrain": "tropical"}
    }
    assert _grant_refs(advanced) == [
        ref for row, refs in LAND["tropical"].items() for ref in refs
    ]

    block = restored["spellcasting"]["class"]
    reconfigured = selection.configure(
        restored,
        {
            "cantrip_refs": list(block["cantrip_refs"]),
            "prepared_spell_refs": list(block["prepared_spell_refs"]),
        },
    )
    assert reconfigured["spellcasting"]["class"]["spell_grant_choices"] == {
        "circle_of_the_land_spells": {"terrain": "tropical"}
    }


def test_matrix_23_illegal_grant_choices_are_rejected_without_state_change(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _lore_bard(runtime, 6)
    block = canonical["spellcasting"]["class"]
    base = {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": list(block["prepared_spell_refs"]),
    }
    cases = [
        {"spell_grants": {"magical_discoveries": ["spell:bless"]}},
        {"spell_grants": {"magical_discoveries": ["spell:bless", "spell:not_a_spell"]}},
        {"spell_grants": {"magical_discoveries": ["spell:bless", "spell:hex"]}},
        {"spell_grants": {"magical_secrets": ["spell:bless", "spell:guidance"]}},
        {"spell_grants": ["spell:bless", "spell:guidance"]},
    ]
    for extra in cases:
        _parsed, errors = selection.validate(
            "class:bard", 6, {**base, **extra}, character=canonical,
        )
        assert errors, extra
    with pytest.raises(SpellCatalogError):
        selection.configure(
            canonical,
            {**base, "spell_grants": {"magical_discoveries": ["spell:bless"]}},
        )
    assert canonical == _lore_bard(runtime, 6)
    forged = deepcopy(canonical)
    forged["spellcasting"]["class"]["spell_grant_choices"] = {
        "magical_discoveries": {"spells": ["spell:bless", "spell:guidance"], "granted_level": 3}
    }
    rows = _grant_rows(forged)
    assert [row["granted_level"] for row in rows] == [3, 3]


def test_matrix_17b_a_bard_without_magical_secrets_keeps_the_bard_list(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    canonical = _canonical(_sheet(runtime, "class:bard", 9))
    block = canonical["spellcasting"]["class"]
    wizard_only = next(
        row["ref"]
        for row in selection.catalog.list_for_class("class:wizard", maximum_level=5)
        if "class:bard" not in row["class_refs"]
    )
    prepared = list(block["prepared_spell_refs"])
    prepared[0] = wizard_only
    _parsed, errors = selection.validate(
        "class:bard", 9,
        {"cantrip_refs": list(block["cantrip_refs"]), "prepared_spell_refs": prepared},
        character=canonical,
    )
    assert any("not on the class:bard spell list" in error for error in errors)


def test_matrix_24_an_unanswered_grant_slot_never_blocks_a_level_up(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    """新特性只能被"报告"，不能被自己的槽位挡住整条升级路径。

    第一版把"还没选"当成非法提交，于是任何只提交普通准备的升级调用方
    （包括本切片之前的所有升级测试）在 Bard 6 级全部失败。这里把两条
    边界都钉住：不回答仍然可以升级、槽位照实开放；回答错了仍然硬失败。
    """

    sheet = _sheet(runtime, "class:bard", 5)
    canonical = _canonical(sheet)
    preview = runtime.preview_advancement(None, sheet, {})
    picks = _picks(preview, canonical)
    probe = _probe(
        canonical, "class:bard", 6, str(canonical["features"]["subclass_ref"]),
    )
    picks["class_spell_choices"] = _spell_choices(
        selection, "class:bard", 6, probe,
    )
    # 故意不回答这个槽：旧调用方只知道 cantrip/prepared 两个字段。
    assert "magical_discoveries" in picks["class_spell_choices"]["spell_grants"]
    picks["class_spell_choices"].pop("spell_grants")
    preview = runtime.preview_advancement(None, sheet, picks)
    assert preview["ok"] is True, preview["errors"]
    requirement = next(
        row for row in preview["requirements"] if row["id"] == "class_spell_choices"
    )
    # 客户端仍然被明确告知"这里有两项要选"。
    assert requirement["required"] is True
    assert [
        (slot["feature_id"], slot["count"], slot["selected_refs"])
        for slot in requirement["grant_slots"]
        if not slot["selected_refs"]
    ] == [("magical_discoveries", 2, [])]
    advanced = runtime.apply_advancement(None, sheet, picks)
    level_six = advanced["ruleset_character"]
    assert level_six["build"]["level"] == 6
    # 没有凭空授予，也没有留下半套选择。
    assert "spell_grant_choices" not in level_six["spellcasting"]["class"]
    assert _grant_rows(level_six) == []


def test_matrix_25_the_runtime_forwards_the_long_rest_terrain(
    runtime: Dnd2024Runtime,
) -> None:
    """后续切片：客户端真的能提交地形了。

    本切片之前引擎已经接受 ``land_terrain``，但 ``runtime.complete_rest()``
    把它丢掉了（``runtime.py`` 当时不在白名单），所以每一次客户端驱动的长休
    都只会产 ``spell_grant_terrain_missing``。通道就是 runtime 自己声明的
    ``rest_option_names``，授予规则一个字没改。
    """

    sheet = _sheet(runtime, "class:druid", 3)
    assert _grant_rows(_canonical(sheet)) == []
    assert "land_terrain" in runtime.rest_option_names

    result = runtime.complete_rest(None, deepcopy(sheet), "long", land_terrain="polar")
    rested = result["character"]["ruleset_character"]
    stored = rested["spellcasting"]["class"]["spell_grant_choices"][
        "circle_of_the_land_spells"
    ]["terrain"]
    assert stored == "polar"
    assert {row["terrain"] for row in _grant_rows(rested)} == {"polar"}
    assert [event["type"] for event in result["events"]].count(
        "spell_grant_terrain"
    ) == 1

    # 短休不接受地形；非法地形整休报错，且连传进去的那份状态都没动。
    with pytest.raises(ValueError):
        runtime.complete_rest(None, deepcopy(sheet), "short", land_terrain="polar")
    probe = deepcopy(sheet)
    snapshot = deepcopy(probe)
    with pytest.raises(RestError):
        runtime.complete_rest(None, probe, "long", land_terrain="swamp")
    assert probe == snapshot
    with pytest.raises(ValueError):
        runtime.complete_rest(None, deepcopy(sheet), "long", land_terrain=7)
