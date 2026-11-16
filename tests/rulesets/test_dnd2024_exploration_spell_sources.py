"""Shared spell-source authority through live rest and exploration (F6/F7)."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

import test_dnd2024_failed_d20_windows as f
import test_dnd2024_free_casting as fc
import test_dnd2024_spell_grants as sg
import test_dnd2024_paladin_auras as pa
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.casting import free_cast_resource
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from src.webui.services import ruleset_rest


@pytest.fixture(scope="module")
def runtime():
    return Dnd2024Runtime()


def _mastery(runtime):
    return fc._sheet(
        runtime, "class:wizard", 18, free_cast={"spell_mastery_1": "spell:mage_armor"}
    )


def _intent(instance, spell_ref, slot_level=0, **extra):
    return {
        "intent_id": f"cast-{f._state(instance).get('version', 0)}",
        "type": "exploration.cast_spell",
        "submitted_by": "gm",
        "actor_id": "player:gm",
        "expected_version": f._state(instance).get("version", 0),
        "spell_ref": spell_ref,
        "slot_level": slot_level,
        "target_ids": ["player:gm"],
        **extra,
    }


def _cast(engine, instance, spell_ref, slot_level=0, rolls=()):
    result = engine.resolve_intent(
        instance, _intent(instance, spell_ref, slot_level), f.SequenceRng(list(rolls))
    )
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"]
    return result


def _offered(engine, instance, ref):
    action = next(
        row
        for row in engine.available_intents(instance, "gm")
        if row["type"] == "exploration.cast_spell"
    )
    return next((row for row in action["spells"] if row["spell_ref"] == ref), None)


@pytest.mark.asyncio
async def test_live_rest_service_rebinds_mastery_once_and_persists(runtime):
    _, instance = f.monk_instance(runtime, _mastery(runtime))
    saved = []

    async def save(current):
        saved.append(deepcopy(current.get_character_sheet("gm")))

    deps = ruleset_rest.LiveRulesetRestDependencies(
        get_instance=lambda key: instance,
        parse_game_key=lambda key: instance.game_key,
        save_instance=save,
        load_rule_for_game=lambda _: SimpleNamespace(
            rule_id="dnd2024_srd", template={}
        ),
        ruleset_registry=SimpleNamespace(resolve=lambda _: runtime),
    )
    body = {
        "rest": "long",
        "confirm_elapsed_time": True,
        "operation_id": "study",
        "expected_revision": instance.get_character_sheet("gm").get(
            "ruleset_revision", 0
        ),
        "free_cast": {"spell_mastery_1": "spell:magic_missile"},
    }
    before = deepcopy(instance.get_character_sheet("gm"))
    runtime.validate_rest_options(
        instance, "gm", "long", {"free_cast": body["free_cast"]}
    )
    assert instance.get_character_sheet("gm") == before
    result = await ruleset_rest.resolve_live(deps, "game", "gm", body)
    assert result["ok"], result
    assert any(row["type"] == "rebind_free_cast" for row in result["events"])
    assert (
        fc._block(f._canonical(instance))["free_cast_slots"]["spell_mastery_1"][
            "spell_ref"
        ]
        == "spell:magic_missile"
    )
    assert len(saved) == 1
    duplicate = await ruleset_rest.resolve_live(deps, "game", "gm", body)
    assert duplicate["ok"] and duplicate["duplicate"]
    assert len(saved) == 1


@pytest.mark.parametrize(
    ("rest", "requested"),
    [
        ("short", {"spell_mastery_1": "spell:magic_missile"}),
        ("long", []),
        ("long", {"spell_mastery_1": "spell:scorching_ray"}),
        ("long", {"forged_slot": "spell:magic_missile"}),
    ],
)
def test_study_validation_rejects_before_state_changes(runtime, rest, requested):
    _, instance = f.monk_instance(runtime, _mastery(runtime))
    before = deepcopy(instance.get_character_sheet("gm"))
    with pytest.raises(ValueError):
        runtime.validate_rest_options(instance, "gm", rest, {"free_cast": requested})
    with pytest.raises(ValueError):
        runtime.complete_rest(None, before, rest, free_cast=requested)
    assert instance.get_character_sheet("gm") == before


def test_domain_grant_can_heal_without_duplicate_preparation(runtime):
    sheet = sg._sheet(runtime, "class:cleric", 3)
    character = sheet["ruleset_character"]
    prepared = fc._block(character)["prepared_spell_refs"]
    if "spell:cure_wounds" in prepared:
        options = Dnd2024SpellSelection(runtime.load_bundle("en")).options(
            "class:cleric", 3, character
        )
        replacement = next(
            row["ref"]
            for row in options["leveled_spells"]
            if row["ref"] not in prepared
        )
        prepared[prepared.index("spell:cure_wounds")] = replacement
    _, instance = f.monk_instance(runtime, sheet)
    canonical = f._canonical(instance)
    canonical["resources"]["hp"] = 1
    before = fc._block(canonical)["slots_current"]["1"]
    engine = runtime._exploration_engine("en")
    assert 1 in _offered(engine, instance, "spell:cure_wounds")["available_slot_levels"]
    result = _cast(engine, instance, "spell:cure_wounds", 1, rolls=[4, 4])
    assert fc._block(f._canonical(instance))["slots_current"]["1"] == before - 1
    assert f._canonical(instance)["resources"]["hp"] > 1
    assert (
        len(
            [
                row
                for row in result["events"]
                if row["type"] == "dnd2024.spell.slot_spent"
            ]
        )
        == 1
    )


def test_unlimited_mastery_free_cast_preserves_slots_and_other_sources(runtime):
    _, instance = f.monk_instance(runtime, _mastery(runtime))
    engine = runtime._exploration_engine("en")
    before = deepcopy(fc._block(f._canonical(instance)))
    assert 0 in _offered(engine, instance, "spell:mage_armor")["available_slot_levels"]
    for _ in range(2):
        result = _cast(engine, instance, "spell:mage_armor")
        assert not any(row["type"].endswith("spent") for row in result["events"])
    assert fc._block(f._canonical(instance)) == before
    # Having a free source must not silently redirect an explicitly paid cast.
    _cast(engine, instance, "spell:mage_armor", 1)
    assert (
        fc._block(f._canonical(instance))["slots_current"]["1"]
        == before["slots_current"]["1"] - 1
    )


def test_signature_use_is_shared_with_combat_replay_and_short_rest(runtime):
    sheet = fc._sheet(runtime, "class:wizard", 20, free_cast=fc.SIGNATURE)
    combat_engine, instance = f.monk_instance(runtime, sheet)
    engine = runtime._exploration_engine("en")
    before_slots = deepcopy(fc._block(f._canonical(instance))["slots_current"])
    spell_ref = "spell:protection_from_energy"
    assert 0 in _offered(engine, instance, spell_ref)["available_slot_levels"]
    assert not engine.validate_intent(
        instance, _intent(instance, spell_ref, target_ids=["enemy:fake"])
    )["ok"]
    result = _cast(engine, instance, spell_ref)
    assert [
        row["free_slot_id"]
        for row in result["events"]
        if row["type"] == "dnd2024.free_cast.spent"
    ] == ["signature_2"]
    assert fc._block(f._canonical(instance))["slots_current"] == before_slots
    view = combat_engine._player_view("gm", f._canonical(instance))
    assert free_cast_resource(view, spell_ref, 3) is None
    assert 0 not in _offered(engine, instance, spell_ref)["available_slot_levels"]
    rejected = engine.resolve_intent(
        instance, _intent(instance, spell_ref), f.SequenceRng([])
    )
    assert not rejected["ok"]
    assert engine.apply_batch(instance, result["event_batch"])["duplicate"]
    assert (
        fc._block(f._canonical(instance))["free_cast_slots"]["signature_2"][
            "uses_remaining"
        ]
        == 0
    )
    restored = runtime.complete_rest(None, instance.get_character_sheet("gm"), "short")[
        "character"
    ]
    instance.set_character_sheet("gm", restored)
    assert free_cast_resource(
        combat_engine._player_view("gm", f._canonical(instance)), spell_ref, 3
    )
    assert 0 in _offered(engine, instance, spell_ref)["available_slot_levels"]


@pytest.mark.parametrize("spell", ["bless", "cure_wounds"])
def test_feat_only_free_source_keeps_ability_and_concentration_authority(
    runtime, spell
):
    draft = f.monk_draft(runtime)
    draft["feat_choice_answers"]["feat:magic_initiate_cleric"] = {
        "spellcasting_ability": ["wis"],
        "cantrips": ["sacred_flame", "spare_the_dying"],
        "level_1_spell": [spell],
    }
    sheet = runtime.finalize_character(None, draft)
    combat, instance = f.monk_instance(runtime, sheet)
    canonical = f._canonical(instance)
    canonical["resources"]["hp"] = 1
    assert "class" not in canonical["spellcasting"]
    engine = runtime._exploration_engine("en")
    assert _offered(engine, instance, f"spell:{spell}")["available_slot_levels"] == [0]
    result = _cast(engine, instance, f"spell:{spell}", rolls=[1, 1])
    canonical = f._canonical(instance)
    assert canonical["spellcasting"]["magic_initiate"][0]["free_cast_used"]
    assert (
        free_cast_resource(combat._player_view("gm", canonical), f"spell:{spell}", 1)
        is None
    )
    if spell == "bless":
        assert canonical["spellcasting"]["concentration"]["spell_ref"] == "spell:bless"
        assert canonical["conditions"]["blessed"]["concentration_owner"] == "player:gm"
    else:
        healed = next(
            row for row in result["events"] if row["type"] == "resource.changed"
        )
        assert healed["amount"] == 2 + (canonical["abilities"]["wis"] - 10) // 2
    assert engine.apply_batch(instance, result["event_batch"])["duplicate"]


def test_free_cast_still_rejects_forged_actor_and_invalid_slot_levels(runtime):
    _, instance = f.monk_instance(runtime, _mastery(runtime))
    engine = runtime._exploration_engine("en")
    for extra in ({"submitted_by": "other"}, {"slot_level": -1}, {"slot_level": 10}):
        before = deepcopy(instance.get_character_sheet("gm"))
        assert not engine.validate_intent(
            instance, _intent(instance, "spell:mage_armor", **extra)
        )["ok"]
        assert instance.get_character_sheet("gm") == before


@pytest.mark.parametrize(
    ("klass", "code"), [("fighter", "indomitable"), ("monk", "disciplined_survivor")]
)
@pytest.mark.parametrize("reroll", [1, 5])
def test_natural_one_with_large_total_opens_and_retains_death_reroll_modifiers(
    runtime, klass, code, reroll
):
    paladin = pa.paladin_sheet(runtime, level=6)
    paladin["ruleset_character"]["abilities"]["cha"] = 20
    sheet = f._fighter(runtime, 9) if klass == "fighter" else f._monk(runtime, 14)
    engine, instance = f._combat(
        runtime, {"gm": sheet, "paladin": paladin}, rolls=[20, 1, 1]
    )
    character = f._canonical(instance)
    character["resources"]["hp"] = 0
    character.setdefault("conditions", {}).update(
        {"unconscious": {"source": "zero_hp"}, "blessed": {"duration": "combat"}}
    )
    result = engine.resolve_intent(
        instance,
        {
            "intent_id": "death",
            "type": "death_save",
            "submitted_by": "gm",
            "actor_id": "player:gm",
            "expected_version": f._state(instance)["version"],
        },
        f.SequenceRng([1, 4]),
    )
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    decision = f._pending(instance)
    assert decision["window_code"] == code
    assert decision["check"]["total"] >= 10 and not decision["check"]["success"]
    result = f._decide(engine, instance, decision, "spend", [reroll])
    assert result["ok"], result
    check = f._checked(f._events(result), "death_save")
    assert check["modifier"] == (5 if klass == "monk" else 0)
    assert check["check_bonus"] == 4 and check["aura_bonus"] == 5
    assert check["success"] == (reroll != 1)  # Bonuses cannot rescue a new natural 1.
    engine.apply_batch(instance, result["event_batch"])
    tally = f._canonical(instance)["conditions"]["death_saves"]
    assert (tally["successes"], tally["failures"]) == (
        (0, 2) if reroll == 1 else (1, 0)
    )
