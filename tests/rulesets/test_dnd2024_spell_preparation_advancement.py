"""Level-up swap budget, per the class's own preparation policy.

The SRD lets a class exchange one prepared spell per level (Bard, Sorcerer,
Warlock), none at all (Cleric, Druid — they reshuffle on a Long Rest instead),
and a Wizard adds exactly two spells to the book while replacing nothing.  The
engine used to accept any new list, so a Sorcerer could reshuffle four spells
while gaining one level.  These tests pin the caps and the requirement fields
the panel renders.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.progression import Dnd2024AdvancementEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells import Dnd2024SpellSelection

LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def engine(runtime: Dnd2024Runtime) -> Dnd2024AdvancementEngine:
    return Dnd2024AdvancementEngine(runtime.load_bundle(LOCALE))


@pytest.fixture(scope="module")
def selection(runtime: Dnd2024Runtime) -> Dnd2024SpellSelection:
    return Dnd2024SpellSelection(runtime.load_bundle(LOCALE))


def _preset(runtime: Dnd2024Runtime, preset_id: str, **overrides: object) -> dict:
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    draft = {**preset["draft"], "locale": LOCALE, "name": "Spell Tester", **overrides}
    return runtime.finalize_character(None, draft)["ruleset_character"]


@pytest.fixture(scope="module")
def wizard(runtime: Dnd2024Runtime) -> dict:
    return _preset(runtime, "curious_arcanist")


@pytest.fixture(scope="module")
def cleric(runtime: Dnd2024Runtime) -> dict:
    return _preset(runtime, "kindly_bulwark")


@pytest.fixture(scope="module")
def sorcerer(runtime: Dnd2024Runtime) -> dict:
    """No sorcerer preset exists; the arcane preset is re-skinned to Sorcerer."""

    return _preset(
        runtime,
        "curious_arcanist",
        class_ref="class:sorcerer",
        class_skill_refs=["skill:deception", "skill:persuasion"],
        equipment_package_ref="equipment_package:sorcerer_a",
        class_spell_choices={
            "cantrip_ids": ["light", "prestidigitation", "shocking_grasp", "sorcerous_burst"],
            "prepared_spell_ids": ["burning_hands", "detect_magic"],
        },
    )


def _magic(character: dict) -> dict:
    return character.get("spellcasting", {}).get("class", {})


def _errors(preview: dict) -> str:
    return " | ".join(preview.get("errors") or [])


# ---------------------------------------------------------------------------
# the swap cap itself
# ---------------------------------------------------------------------------


def test_sorcerer_cannot_reshuffle_the_whole_list_at_level_up(
    engine: Dnd2024AdvancementEngine, sorcerer: dict,
) -> None:
    prepared = list(_magic(sorcerer)["prepared_spell_refs"])
    assert len(prepared) == 2

    preview = engine.preview_next_level(sorcerer, {"class_spell_choices": {
        "cantrip_refs": list(_magic(sorcerer)["cantrip_refs"]),
        "prepared_spell_refs": [
            "spell:charm_person", "spell:chromatic_orb",
            "spell:color_spray", "spell:comprehend_languages",
        ],
    }})

    assert preview["ok"] is False
    assert "at most 1" in _errors(preview)


def test_sorcerer_may_swap_exactly_one_while_gaining_capacity(
    engine: Dnd2024AdvancementEngine, sorcerer: dict,
) -> None:
    block = _magic(sorcerer)
    kept = block["prepared_spell_refs"][:1]

    preview = engine.preview_next_level(sorcerer, {"class_spell_choices": {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": [
            *kept, "spell:chromatic_orb", "spell:color_spray", "spell:comprehend_languages",
        ],
    }})

    assert preview["ok"] is True, _errors(preview)
    applied = engine.apply_next_level(sorcerer, {"class_spell_choices": {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": [
            *kept, "spell:chromatic_orb", "spell:color_spray", "spell:comprehend_languages",
        ],
    }})
    assert applied["build"]["level"] == 2
    assert len(_magic(applied)["prepared_spell_refs"]) == 4


def test_cleric_cannot_replace_at_all_on_level_up(
    engine: Dnd2024AdvancementEngine, cleric: dict,
) -> None:
    """Cleric reshuffles on a Long Rest; level-up only adds."""

    prepared = list(_magic(cleric)["prepared_spell_refs"])
    assert len(prepared) == 4
    # Drop guiding_bolt while filling the fifth slot.
    replaced = [prepared[0], prepared[1], prepared[3], "spell:healing_word", "spell:command"]

    preview = engine.preview_next_level(cleric, {"class_spell_choices": {
        "cantrip_refs": list(_magic(cleric)["cantrip_refs"]),
        "prepared_spell_refs": replaced,
    }})

    assert preview["ok"] is False
    assert "at most 0" in _errors(preview)


def test_cleric_keeps_every_old_spell_and_adds_one(
    engine: Dnd2024AdvancementEngine, cleric: dict,
) -> None:
    block = _magic(cleric)

    preview = engine.preview_next_level(cleric, {"class_spell_choices": {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": [*block["prepared_spell_refs"], "spell:healing_word"],
    }})

    assert preview["ok"] is True, _errors(preview)


# ---------------------------------------------------------------------------
# cantrip swaps follow a separate timing axis
# ---------------------------------------------------------------------------


def test_bard_swaps_one_cantrip_on_level_up(selection: Dnd2024SpellSelection) -> None:
    previous = {
        "cantrip_refs": ["spell:dancing_lights", "spell:vicious_mockery"],
        "prepared_spell_refs": [],
        "spellbook_refs": [],
    }
    choices = {
        "cantrip_refs": ["spell:dancing_lights", "spell:mage_hand", "spell:minor_illusion"],
        "prepared_spell_refs": [
            "spell:charm_person", "spell:cure_wounds", "spell:faerie_fire",
            "spell:healing_word", "spell:thunderwave", "spell:detect_magic", "spell:sleep",
        ],
    }

    _, errors = selection.validate(
        "class:bard", 4, choices, previous=previous,
    )

    assert not [error for error in errors if "cantrip" in error], errors


def _wizard_book_at(selection: Dnd2024SpellSelection, level: int) -> list[str]:
    """A book of exactly the printed floor, drawn from eligible spell levels."""

    requirements = selection.requirements("class:wizard", level)
    pool = [
        spell["ref"] for spell in selection.catalog.list_for_class(
            "class:wizard", maximum_level=requirements["maximum_spell_level"],
        )
        # A spellbook holds leveled spells only; cantrips have their own list.
        if int(spell["level"]) >= 1
    ]
    return pool[: int(requirements["spellbook_minimum"])]


def _wizard_level_four_lists(
    selection: Dnd2024SpellSelection, wizard: dict,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Previous (level 3) and next (level 4) lists for a Wizard.

    Level 3 holds a 10-spell book and 6 prepared spells; level 4 asks for 12
    and 7, gains one cantrip, and may not swap the other three.
    """

    block = _magic(wizard)
    book = _wizard_book_at(selection, 4)
    previous = {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": book[:6],
        "spellbook_refs": book[:10],
    }
    next_lists = {
        "prepared_spell_refs": book[:7],
        "spellbook_refs": list(book),
    }
    return previous, next_lists


def test_wizard_cannot_swap_a_cantrip_on_level_up(
    selection: Dnd2024SpellSelection, wizard: dict,
) -> None:
    previous, next_lists = _wizard_level_four_lists(selection, wizard)
    choices = {
        "cantrip_refs": [
            previous["cantrip_refs"][0], previous["cantrip_refs"][1],
            "spell:minor_illusion", "spell:prestidigitation",
        ],
        **next_lists,
    }

    _, errors = selection.validate(
        "class:wizard", 4, choices, previous=previous,
    )

    assert any("cantrip_refs may replace at most 0" in error for error in errors), errors


def test_wizard_only_adds_the_new_cantrip(
    selection: Dnd2024SpellSelection, wizard: dict,
) -> None:
    previous, next_lists = _wizard_level_four_lists(selection, wizard)
    choices = {
        "cantrip_refs": [*previous["cantrip_refs"], "spell:minor_illusion"],
        **next_lists,
    }

    parsed, errors = selection.validate(
        "class:wizard", 4, choices, previous=previous,
    )

    assert errors == []
    assert len(parsed["cantrip_refs"]) == 4


# ---------------------------------------------------------------------------
# the spellbook: a floor, plus an exact gain
# ---------------------------------------------------------------------------


def test_wizard_adds_exactly_two_spells_to_the_book(
    engine: Dnd2024AdvancementEngine, wizard: dict,
) -> None:
    block = _magic(wizard)

    preview = engine.preview_next_level(wizard, {"class_spell_choices": {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": [*block["prepared_spell_refs"], "spell:shield"],
        "spellbook_refs": [*block["spellbook_refs"], "spell:shield", "spell:grease"],
    }})

    assert preview["ok"] is True, _errors(preview)
    applied = engine.apply_next_level(wizard, {"class_spell_choices": {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": [*block["prepared_spell_refs"], "spell:shield"],
        "spellbook_refs": [*block["spellbook_refs"], "spell:shield", "spell:grease"],
    }})
    assert len(_magic(applied)["spellbook_refs"]) == 8


@pytest.mark.parametrize(
    ("added", "message"),
    [(3, "must add exactly 2"), (1, "must add exactly 2")],
)
def test_wizard_book_gain_is_not_negotiable(
    engine: Dnd2024AdvancementEngine, wizard: dict, added: int, message: str,
) -> None:
    block = _magic(wizard)
    extras = ["spell:shield", "spell:grease", "spell:jump"][:added]

    preview = engine.preview_next_level(wizard, {"class_spell_choices": {
        "cantrip_refs": list(block["cantrip_refs"]),
        "prepared_spell_refs": list(block["prepared_spell_refs"]),
        "spellbook_refs": [*block["spellbook_refs"], *extras],
    }})

    assert preview["ok"] is False
    assert message in _errors(preview)


def test_a_spellbook_may_sit_above_its_printed_floor(
    selection: Dnd2024SpellSelection, runtime: Dnd2024Runtime,
) -> None:
    """44 is the floor at level 20, not the ceiling: copying is legal."""

    bundle = runtime.load_bundle(LOCALE)
    catalog = selection.catalog
    wizard_spells = [
        spell["ref"] for spell in catalog.list_for_class("class:wizard", maximum_level=9)
    ]
    previous_book = wizard_spells[:43]
    prepared = [ref for ref in previous_book if catalog.get(ref)["level"] == 1][:25]
    choices = {
        "cantrip_refs": wizard_spells[:5],
        "prepared_spell_refs": prepared,
        "spellbook_refs": [*previous_book, *wizard_spells[43:45]],
    }

    parsed, errors = selection.validate(
        "class:wizard", 20, choices,
        previous={
            "cantrip_refs": wizard_spells[:4],
            "prepared_spell_refs": prepared,
            "spellbook_refs": previous_book,
        },
    )

    assert len(parsed["spellbook_refs"]) == 45
    assert not [error for error in errors if "spellbook_refs" in error], errors
    assert bundle.get("class", "wizard")["preparation_policy"]["spellbook"]["per_level"] == 2


# ---------------------------------------------------------------------------
# the requirement the panel renders
# ---------------------------------------------------------------------------


def test_wizard_requirement_reports_the_three_caps(
    engine: Dnd2024AdvancementEngine, wizard: dict,
) -> None:
    preview = engine.preview_next_level(wizard)
    requirement = next(
        item for item in preview["requirements"] if item["id"] == "class_spell_choices"
    )

    assert requirement["additions"] == 1
    assert requirement["replace_limit"] == 0
    assert requirement["cantrip_replace_limit"] == 0
    assert requirement["spellbook_additions"] == 2


def test_sorcerer_requirement_reports_a_one_spell_budget(
    engine: Dnd2024AdvancementEngine, sorcerer: dict,
) -> None:
    preview = engine.preview_next_level(sorcerer)
    requirement = next(
        item for item in preview["requirements"] if item["id"] == "class_spell_choices"
    )

    assert requirement["additions"] == 2
    assert requirement["replace_limit"] == 1
    assert requirement["cantrip_replace_limit"] == 1
    assert requirement["spellbook_additions"] == 0


# ---------------------------------------------------------------------------
# a level without spell growth must not ask for a selection
# ---------------------------------------------------------------------------


def _fill_spells(block: dict, requirement: dict) -> dict:
    """Keep every old spell, then pad up to the new capacity."""

    cantrips = list(block.get("cantrip_refs") or [])
    for spell in requirement["cantrips"]:
        if len(cantrips) >= int(requirement["cantrip_count"]):
            break
        if spell["ref"] not in cantrips:
            cantrips.append(spell["ref"])
    prepared = list(block.get("prepared_spell_refs") or [])
    for spell in requirement["leveled_spells"]:
        if len(prepared) >= int(requirement["prepared_spell_count"]):
            break
        if spell["ref"] not in prepared:
            prepared.append(spell["ref"])
    lists = {"cantrip_refs": cantrips, "prepared_spell_refs": prepared}
    book = list(block.get("spellbook_refs") or [])
    additions = int(requirement.get("spellbook_additions") or 0)
    if additions:
        target = len(book) + additions
        for spell in requirement["leveled_spells"]:
            if len(book) >= target:
                break
            if spell["ref"] not in book:
                book.append(spell["ref"])
    if book:
        lists["spellbook_refs"] = book
    return lists


def _advance_to(engine: Dnd2024AdvancementEngine, character: dict, level: int) -> dict:
    """Walk a cleric up to ``level``, answering only what each level asks."""

    current = character
    while current["build"]["level"] < level:
        preview = engine.preview_next_level(current)
        assert preview["ok"] is False or not preview["requirements"], preview
        choices: dict = {}
        for requirement in preview["requirements"]:
            kind = requirement["id"]
            if kind == "subclass_ref":
                choices[kind] = requirement["options"][0]["value"]
            elif kind == "ability_score_increases":
                total = int(requirement["total"])
                maximum = int(requirement.get("maximum_score") or 20)
                allowed = requirement.get("allowed") or "any"
                abilities = (
                    allowed if isinstance(allowed, list)
                    else ["str", "dex", "con", "int", "wis", "cha"]
                )
                room = next(
                    ability for ability in abilities
                    if int(current["abilities"].get(ability, 0)) + total <= maximum
                )
                choices[kind] = {room: total}
            elif kind == "feat_ref":
                choices[kind] = requirement["options"][0]["value"]
            elif kind == "class_spell_choices":
                choices[kind] = _fill_spells(_magic(current), requirement)
        current = engine.apply_next_level(current, choices)
    return current


def test_a_level_without_spell_growth_does_not_ask_for_spells(
    engine: Dnd2024AdvancementEngine, cleric: dict,
) -> None:
    eleven = _advance_to(engine, cleric, 11)
    assert eleven["build"]["level"] == 11

    preview = engine.preview_next_level(eleven)

    assert not [
        item for item in preview["requirements"] if item["id"] == "class_spell_choices"
    ]


def test_a_level_with_spell_growth_does_ask_for_spells(
    engine: Dnd2024AdvancementEngine, cleric: dict,
) -> None:
    five = _advance_to(engine, cleric, 5)

    preview = engine.preview_next_level(five)

    requirement = next(
        item for item in preview["requirements"] if item["id"] == "class_spell_choices"
    )
    assert requirement["required"] is True
    assert requirement["additions"] == 1


# ---------------------------------------------------------------------------
# review follow-ups: the book never shrinks, and every level must say its +2
# ---------------------------------------------------------------------------


def test_a_level_up_cannot_drop_a_spell_from_the_book(
    selection: Dnd2024SpellSelection, wizard: dict,
) -> None:
    """升级只加不减：删旧换新会让"新增数量对得上"掩盖书变小的事实。"""

    previous, next_lists = _wizard_level_four_lists(selection, wizard)
    book = list(next_lists["spellbook_refs"])
    book.remove(previous["spellbook_refs"][-1])
    choices = {
        "cantrip_refs": [*previous["cantrip_refs"], "spell:minor_illusion"],
        "prepared_spell_refs": next_lists["prepared_spell_refs"],
        "spellbook_refs": [*book, "spell:jump"],
    }

    _, errors = selection.validate("class:wizard", 4, choices, previous=previous)

    assert any(
        "must keep the spells the book already holds" in error for error in errors
    ), errors


def test_a_book_at_the_next_floor_still_owes_this_levels_two_spells(
    engine: Dnd2024AdvancementEngine,
    selection: Dnd2024SpellSelection,
    wizard: dict,
) -> None:
    """旧书靠抄写恰好等于下一级下限，不等于本级的 +2 已经选过。

    11 级书 26 个（印刷下限），抄写到 28 个正好是 12 级的下限；12 级的
    准备位与戏法位都不增长。旧的判定只看"数量是否等于下限"，于是整段
    新增校验被跳过——不传 class_spell_choices 也能通过。
    """

    eleven = _advance_to(engine, deepcopy(wizard), 11)
    block = _magic(eleven)
    assert len(block["spellbook_refs"]) == 26
    assert selection.requirements("class:wizard", 12)["prepared_spell_count"] == (
        selection.requirements("class:wizard", 11)["prepared_spell_count"]
    )

    copied = deepcopy(eleven)
    book = _magic(copied)["spellbook_refs"]
    pool = [
        spell["ref"] for spell in selection.catalog.list_for_class(
            "class:wizard", maximum_level=5,
        )
        if int(spell["level"]) >= 1 and spell["ref"] not in book
    ]
    book.extend(pool[:2])
    assert len(book) == 28

    preview = engine.preview_next_level(copied, {})

    requirement = next(
        item for item in preview["requirements"]
        if item["id"] == "class_spell_choices"
    )
    assert requirement["required"] is True
    assert requirement["spellbook_additions"] == 2
    assert preview["ok"] is False
    assert "class_spell_choices are required" in _errors(preview)
