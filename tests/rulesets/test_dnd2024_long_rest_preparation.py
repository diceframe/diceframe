"""Changing prepared spells is a Long Rest activity, per class.

SRD gives three shapes and the code had none of them: Cleric, Druid and Wizard
reshuffle freely after a rest; Paladin and Ranger replace exactly one; Bard,
Sorcerer and Warlock cannot do it at all (they swap one spell on level-up
instead).  Cantrips follow their own timing: only the Wizard re-picks one after
a rest.

The rest payload is ``prepared_spells: {prepared_spell_refs, cantrip_refs?}``;
``cantrip_refs`` is optional so a plain reshuffle does not have to echo them.
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog

LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def engine(runtime: Dnd2024Runtime) -> Dnd2024RestEngine:
    return Dnd2024RestEngine(runtime.load_bundle(LOCALE))


def _caster(runtime: Dnd2024Runtime, class_id: str) -> dict:
    """A level-1 character of ``class_id``, reskinned from the arcane preset.

    Only three presets are casters (Wizard, Cleric, Ranger); the rest are built
    from the same base draft with the class's own skills, package, tools and
    recommended spells, which is what the builder does in one click.
    """

    bundle = runtime.load_bundle(LOCALE)
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    base = next(
        item for item in choices["quick_presets"] if item["id"] == "curious_arcanist"
    )["draft"]
    background = bundle.get(
        "background", str(base["background_ref"]).removeprefix("background:")
    )
    background_skills = set(background.get("skill_refs") or [])
    background_tools = set(background.get("tool_proficiency_refs") or [])
    entity = bundle.get("class", class_id) or {}
    skill_choice = entity.get("skill_choice") or {}
    draft = {
        **base,
        "class_ref": f"class:{class_id}",
        "class_skill_refs": [
            ref for ref in skill_choice.get("allowed_refs") or []
            if ref not in background_skills
        ][: int(skill_choice.get("count") or 0)],
        "equipment_package_ref": (entity.get("equipment_package_refs") or [""])[0],
        "name": f"Rest {class_id}",
    }
    recommended = entity.get("recommended_spell_choices") or {}
    if recommended:
        draft["class_spell_choices"] = {
            "cantrip_ids": recommended.get("cantrip_ids") or [],
            "prepared_spell_ids": recommended.get("prepared_spell_ids") or [],
            **(
                {"spellbook_ids": recommended["spellbook_ids"]}
                if recommended.get("spellbook_ids") else {}
            ),
        }
    tool_choice = entity.get("tool_choice") or {}
    if tool_choice:
        categories = set(tool_choice.get("allowed_categories") or [])
        draft["class_tool_refs"] = [
            f"tool:{tool['id']}" for tool in bundle.list("tool")
            if tool.get("category") in categories
            and f"tool:{tool['id']}" not in background_tools
        ][: int(tool_choice.get("count") or 0)]
    else:
        draft.pop("class_tool_refs", None)
    return runtime.finalize_character(None, draft)["ruleset_character"]


def _live(character: dict) -> dict:
    """The payload shape the runtime receives from the service."""

    return {"ruleset_character": character}


def _magic(character: dict) -> dict:
    return character["spellcasting"]["class"]


# ---------------------------------------------------------------------------
# the classes that may reshuffle freely
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("class_id", ["cleric", "druid"])
def test_a_preparing_class_reshuffles_after_a_long_rest(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine, class_id: str,
) -> None:
    character = _caster(runtime, class_id)
    before = list(_magic(character)["prepared_spell_refs"])
    # Keep the first spell, fill the rest of the capacity with other spells.
    replacement = [before[0]]
    available = _other_spells(runtime, class_id, character)
    while len(replacement) < len(before):
        replacement.append(available.pop())

    parsed = engine.parse_prepared_spells(_live(character), {
        "prepared_spell_refs": replacement,
    })
    result = engine.complete_long_rest(_live(character), prepared_spells=parsed)

    applied = _magic(result["character"])["prepared_spell_refs"]
    assert applied == replacement
    event = next(
        item for item in result["events"] if item["type"] == "prepared_spells_changed"
    )
    assert event["before"] == before
    assert event["after"] == replacement


def _catalog(runtime: Dnd2024Runtime) -> Dnd2024SpellCatalog:
    return Dnd2024SpellCatalog.from_bundle(runtime.load_bundle(LOCALE))


def _other_spells(runtime: Dnd2024Runtime, class_id: str, character: dict) -> list[str]:
    """Level-eligible spells on the class list that are not prepared yet."""

    known = set(_magic(character)["prepared_spell_refs"])
    slots = _magic(character).get("slots_max") or {"1": 1}
    maximum = max(int(level) for level in slots)
    return [
        spell["ref"] for spell in _catalog(runtime).list_for_class(
            f"class:{class_id}", maximum_level=maximum,
        )
        if int(spell["level"]) >= 1 and spell["ref"] not in known
    ]


# ---------------------------------------------------------------------------
# the wizard: book-bounded, cantrip on rest
# ---------------------------------------------------------------------------


def test_a_wizard_may_only_prepare_from_the_spellbook(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    character = _caster(runtime, "wizard")
    book = set(_magic(character)["spellbook_refs"])
    outsider = next(
        ref for ref in _other_spells(runtime, "wizard", character) if ref not in book
    )
    prepared = [*_magic(character)["prepared_spell_refs"][1:], outsider]

    with pytest.raises(RestError, match="must be in spellbook_refs"):
        engine.parse_prepared_spells(_live(character), {
            "prepared_spell_refs": prepared,
        })


def test_a_wizard_may_re_pick_one_cantrip_after_a_rest(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    character = _caster(runtime, "wizard")
    cantrips = list(_magic(character)["cantrip_refs"])
    prepared = list(_magic(character)["prepared_spell_refs"])
    new_cantrip = next(
        spell["ref"] for spell in _catalog(runtime).list_for_class("class:wizard")
        if int(spell["level"]) == 0 and spell["ref"] not in cantrips
    )

    parsed = engine.parse_prepared_spells(_live(character), {
        "prepared_spell_refs": prepared,
        "cantrip_refs": [*cantrips[1:], new_cantrip],
    })

    assert parsed["cantrip_refs"] == [*cantrips[1:], new_cantrip]
    assert parsed["prepared_spell_refs"] == prepared


def test_a_wizard_cannot_re_pick_two_cantrips(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    character = _caster(runtime, "wizard")
    cantrips = list(_magic(character)["cantrip_refs"])
    spare = [
        spell["ref"] for spell in _catalog(runtime).list_for_class("class:wizard")
        if int(spell["level"]) == 0 and spell["ref"] not in cantrips
    ][:2]

    with pytest.raises(RestError, match="at most 1 cantrip"):
        engine.parse_prepared_spells(_live(character), {
            "prepared_spell_refs": list(_magic(character)["prepared_spell_refs"]),
            # Capacity stays 3: one old cantrip kept, two swapped out.
            "cantrip_refs": [cantrips[0], *spare],
        })


# ---------------------------------------------------------------------------
# the one-for-one classes
# ---------------------------------------------------------------------------


def test_a_paladin_replaces_one_spell_after_a_rest(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    character = _caster(runtime, "paladin")
    before = list(_magic(character)["prepared_spell_refs"])
    assert len(before) == 2
    fresh = next(
        ref for ref in _other_spells(runtime, "paladin", character)
        if _level_of(runtime, ref) <= 1
    )

    parsed = engine.parse_prepared_spells(_live(character), {
        "prepared_spell_refs": [before[0], fresh],
    })

    assert parsed["prepared_spell_refs"] == [before[0], fresh]


def test_a_paladin_cannot_replace_two_spells_after_a_rest(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    character = _caster(runtime, "paladin")
    fresh = [
        ref for ref in _other_spells(runtime, "paladin", character)
        if _level_of(runtime, ref) <= 1
    ][:2]

    with pytest.raises(RestError, match="at most 1 spell"):
        engine.parse_prepared_spells(_live(character), {
            "prepared_spell_refs": fresh,
        })


def _level_of(runtime: Dnd2024Runtime, ref: str) -> int:
    spell = _catalog(runtime).get(ref)
    return int(spell["level"]) if spell else 0


# ---------------------------------------------------------------------------
# the classes that cannot change anything on a rest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("class_id", ["bard", "sorcerer", "warlock"])
def test_a_level_up_only_class_is_refused_on_a_long_rest(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine, class_id: str,
) -> None:
    character = _caster(runtime, class_id)

    with pytest.raises(RestError, match="cannot change prepared spells on a long rest"):
        engine.parse_prepared_spells(_live(character), {
            "prepared_spell_refs": list(_magic(character)["prepared_spell_refs"]),
        })


def test_a_change_that_changes_nothing_is_refused(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    character = _caster(runtime, "cleric")

    with pytest.raises(RestError, match="would not change anything"):
        engine.parse_prepared_spells(_live(character), {
            "prepared_spell_refs": list(_magic(character)["prepared_spell_refs"]),
        })


# ---------------------------------------------------------------------------
# wiring: timing, and the untouched path
# ---------------------------------------------------------------------------


def test_prepared_spells_are_refused_on_a_short_rest(
    runtime: Dnd2024Runtime,
) -> None:
    character = _caster(runtime, "cleric")

    with pytest.raises(ValueError, match="only be changed on a long rest"):
        runtime.complete_rest(
            None, _live(character), "short",
            prepared_spells={"prepared_spell_refs": []},
        )


def test_a_long_rest_without_the_option_keeps_the_list_and_adds_no_event(
    runtime: Dnd2024Runtime,
) -> None:
    character = _caster(runtime, "cleric")
    before = list(_magic(character)["prepared_spell_refs"])

    result = runtime.complete_rest(None, _live(character), "long")

    projected = result["character"]
    canonical = projected.get("ruleset_character", projected)
    assert _magic(canonical)["prepared_spell_refs"] == before
    assert not [
        item for item in result["events"] if item["type"] == "prepared_spells_changed"
    ]


def test_two_casters_each_keep_their_own_new_list(
    runtime: Dnd2024Runtime, engine: Dnd2024RestEngine,
) -> None:
    """The party path replays each participant's own options, one at a time."""

    cleric = _caster(runtime, "cleric")
    druid = _caster(runtime, "druid")
    cleric_choice = [
        _magic(cleric)["prepared_spell_refs"][0],
        *_other_spells(runtime, "cleric", cleric)[: len(_magic(cleric)["prepared_spell_refs"]) - 1],
    ]
    druid_choice = [
        _magic(druid)["prepared_spell_refs"][0],
        *_other_spells(runtime, "druid", druid)[: len(_magic(druid)["prepared_spell_refs"]) - 1],
    ]

    results = {}
    for name, character, choice in (
        ("cleric", cleric, cleric_choice), ("druid", druid, druid_choice),
    ):
        parsed = engine.parse_prepared_spells(
            _live(character), {"prepared_spell_refs": choice},
        )
        results[name] = engine.complete_long_rest(
            _live(character), prepared_spells=parsed,
        )["character"]

    assert _magic(results["cleric"])["prepared_spell_refs"] == cleric_choice
    assert _magic(results["druid"])["prepared_spell_refs"] == druid_choice
    assert cleric_choice != druid_choice
