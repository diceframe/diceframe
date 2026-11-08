"""S0-E2: Paladin auras -- saving-throw bonus, expansion range, Charmed immunity.

The evaluator is exercised through the real ``resolve_intent -> apply_batch``
chain wherever a rule is observable in play.  Only the two things a live combat
cannot stage -- an illegal declaration and an equal-bonus tie between two
holders -- are driven directly against the pure helpers.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from src.rulesets.events import stable_batch_id

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
)


PALADIN_SPELLS = [
    "spell:heroism",
    "spell:cure_wounds",
    "spell:bless",
    "spell:divine_smite",
    "spell:shield_of_faith",
    "spell:lesser_restoration",
    "spell:find_steed",
    "spell:prayer_of_healing",
    "spell:revivify",
    "spell:aid",
    "spell:command",
    "spell:detect_magic",
    "spell:protection_from_evil_and_good",
    "spell:zone_of_truth",
]
WIZARD_CANTRIPS = [
    "spell:light", "spell:mage_hand", "spell:ray_of_frost", "spell:fire_bolt",
]
WIZARD_SPELLBOOK = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:shield",
    "spell:color_spray",
    "spell:charm_person",
    "spell:thunderwave",
    "spell:sleep",
    "spell:hold_person",
    "spell:invisibility",
    "spell:blur",
    "spell:misty_step",
    "spell:mirror_image",
    "spell:fireball",
]


# --------------------------------------------------------------------------
# character construction
# --------------------------------------------------------------------------


def paladin_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:paladin", level,
    )
    return {
        "cantrip_refs": [],
        "spellbook_refs": [],
        "prepared_spell_refs": PALADIN_SPELLS[: requirements["prepared_spell_count"]],
    }


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:wizard", level,
    )
    return {
        "cantrip_refs": WIZARD_CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": WIZARD_SPELLBOOK[: requirements["prepared_spell_count"]],
        "spellbook_refs": WIZARD_SPELLBOOK[: requirements["spellbook_minimum"]],
    }


def _advance_with_spells(
    runtime: Dnd2024Runtime, sheet: dict, target_level: int, chooser,
) -> dict:
    while int(sheet["ruleset_character"]["build"]["level"]) < target_level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        if any(
            requirement["id"] == "class_spell_choices"
            for requirement in preview.get("requirements") or []
        ):
            choices["class_spell_choices"] = chooser(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def paladin_sheet(
    runtime: Dnd2024Runtime, *, level: int = 7,
    abilities: dict[str, int] | None = None,
) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:paladin",
        class_skill_refs=["skill:athletics", "skill:intimidation"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:paladin_a",
        class_spell_choices=paladin_choices(runtime, 1),
    )
    if abilities is not None:
        draft["base_abilities"] = abilities
    sheet = runtime.finalize_character(None, draft)
    return _advance_with_spells(runtime, sheet, level, paladin_choices)


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 5) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:wizard",
        class_skill_refs=["skill:arcana", "skill:history"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:wizard_a",
        base_abilities={
            "str": 8, "dex": 12, "con": 13, "int": 15, "wis": 14, "cha": 10,
        },
        class_spell_choices=wizard_choices(runtime, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    return _advance_with_spells(runtime, sheet, level, wizard_choices)


def enemy(*, position: int = 5) -> dict:
    profile = goblin(position=position, hp=100)
    profile["id"] = "goblin-1"
    profile["creature_type"] = "humanoid"
    return profile


def new_combat(
    *, paladin_level: int = 7, allies: dict[str, str] | None = None,
    paladin_abilities: dict[str, int] | None = None,
    ally_sheets: dict[str, dict] | None = None,
) -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    sheets = {"gm": paladin_sheet(
        runtime, level=paladin_level, abilities=paladin_abilities,
    )}
    for uid, kind in (allies or {}).items():
        sheets[uid] = (
            wizard_sheet(runtime) if kind == "wizard" else paladin_sheet(runtime)
        )
    for uid, sheet in (ally_sheets or {}).items():
        sheets[uid] = sheet
    engine, instance = monk_instance(runtime, sheets["gm"], uid="gm")
    for uid, sheet in sheets.items():
        if uid != "gm":
            instance.players[uid] = {
                "character_name": uid.title(), "character_sheet": sheet,
            }
    player_rolls = [1 for _ in sorted(sheets)]
    player_rolls[sorted(sheets).index("gm")] = 20
    rolls = [*player_rolls, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [enemy()],
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return runtime, engine, instance


# --------------------------------------------------------------------------
# state helpers
# --------------------------------------------------------------------------


def combat_state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def place(instance, **positions: int) -> None:
    combat = combat_state(instance)
    for actor, position in positions.items():
        combat["positions"][actor] = position


def direct_cast(
    engine, instance, target_id: str, rolls=(), *,
    actor_id: str = "player:gm", spell_ref: str = "spell:charm_person",
    slot_level: int = 1,
) -> list[dict]:
    """Drive the spell resolver directly; only intent validation is skipped."""

    return engine._spell_events(
        instance,
        combat_state(instance),
        {
            "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": [target_id],
        },
        SequenceRng(list(rolls)),
    )


def apply_events(engine, instance, events: list[dict]) -> dict:
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {"intent_id": f"resolve:{version}", "expected_version": version,
              "type": "cast_spell"}
    batch = {
        "batch_id": stable_batch_id(intent, version),
        "intent_id": intent["intent_id"], "intent_type": "cast_spell",
        "expected_version": version, "result_version": version + 1,
        "events": [
            {"type": "intent.submitted", "intent_type": "cast_spell",
             "actor_id": "player:gm", "submitted_by": "gm"},
            *events,
        ],
        "source_ref": "test",
    }
    result = engine.apply_batch(instance, batch)
    assert result["applied"], result
    return batch


def real_cast(
    engine, instance, uid: str, spell_ref: str, target_id: str, rolls=(),
    *, slot_level: int = 1,
) -> dict:
    combat = combat_state(instance)
    actor_id = f"player:{uid}"
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, actor_id),
    )
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": f"cast:{spell_ref}:{version}", "type": "cast_spell",
        "expected_version": version, "submitted_by": uid, "actor_id": actor_id,
        "spell_ref": spell_ref, "slot_level": slot_level, "target_ids": [target_id],
    }
    result = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    return result


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def check_of(result: dict, kind: str = "saving_throw") -> dict:
    return next(
        event for event in events_of(result)
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def check_in(events: list[dict], kind: str = "saving_throw") -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def conditions_of(engine, instance, actor_id: str) -> dict:
    return engine._actor_view(instance, combat_state(instance), actor_id)["conditions"]


def put_condition(engine, instance, actor_id: str, condition_id: str) -> None:
    """Apply one condition through the authoritative batch path."""

    apply_events(engine, instance, [{
        "type": "condition.applied", "target_id": actor_id,
        "condition": condition_id, "source_actor_id": "player:gm",
    }])


def aura_of(engine, instance, actor_id: str) -> dict:
    return engine._saving_throw_aura_bonus(
        instance, combat_state(instance),
        engine._actor_view(instance, combat_state(instance), actor_id),
    )


# --------------------------------------------------------------------------
# 6.1 grant, declaration and validation
# --------------------------------------------------------------------------


def _feature_ids(engine, instance, actor_id: str) -> set[str]:
    view = engine._actor_view(instance, combat_state(instance), actor_id)
    character = engine._feature_character(view)
    return {definition.id for definition in engine.features.active_features_for(character)}


def test_aura_of_protection_arrives_at_level_six() -> None:
    """1. No aura at 5, one at 6, range 10 until the level-18 expansion."""

    _, engine5, instance5 = new_combat(paladin_level=5)
    assert "aura_of_protection" not in _feature_ids(engine5, instance5, "player:gm")
    assert aura_of(engine5, instance5, "player:gm")["bonus"] == 0

    _, engine6, instance6 = new_combat(paladin_level=6)
    assert "aura_of_protection" in _feature_ids(engine6, instance6, "player:gm")
    assert "aura_expansion" not in _feature_ids(engine6, instance6, "player:gm")
    assert aura_of(engine6, instance6, "player:gm")["sources"][0]["range"] == 10

    _, engine17, instance17 = new_combat(paladin_level=17)
    assert aura_of(engine17, instance17, "player:gm")["sources"][0]["range"] == 10

    _, engine18, instance18 = new_combat(paladin_level=18)
    assert "aura_expansion" in _feature_ids(engine18, instance18, "player:gm")
    assert aura_of(engine18, instance18, "player:gm")["sources"][0]["range"] == 30


def test_aura_of_devotion_arrives_at_subclass_level_seven() -> None:
    """2. The immunity needs both the class aura and the subclass grant."""

    _, engine6, instance6 = new_combat(paladin_level=6)
    assert "aura_of_devotion" not in _feature_ids(engine6, instance6, "player:gm")

    _, engine7, instance7 = new_combat(paladin_level=7)
    assert "aura_of_devotion" in _feature_ids(engine7, instance7, "player:gm")


def test_the_three_declarations_keep_their_owner_and_level() -> None:
    """3. Owner-scoped identity, so a bare short id cannot forge ownership."""

    _, engine, instance = new_combat(paladin_level=18)
    catalog = engine.features.catalog.features_by_key
    protection = catalog[("class:paladin", "aura_of_protection")]
    expansion = catalog[("class:paladin", "aura_expansion")]
    devotion = catalog[("subclass:oath_of_devotion", "aura_of_devotion")]
    assert protection.minimum_level == 6
    assert expansion.minimum_level == 18
    assert devotion.minimum_level == 7
    assert protection.parameters == {
        "aura_range_ft": 10,
        "aura_save_bonus_ability": "cha",
        "aura_save_bonus_minimum": 1,
    }
    assert expansion.parameters == {
        "aura_expands_feature_id": "aura_of_protection",
        "aura_range_override_ft": 30,
    }
    assert devotion.parameters == {
        "aura_source_feature_id": "aura_of_protection",
        "aura_condition_immunities": ["charmed"],
    }


def test_labels_are_localized_but_identity_is_not() -> None:
    """4. Both locales carry the three names; the ids stay canonical."""

    runtime = Dnd2024Runtime()
    for locale, expected in (
        ("en", {
            "aura_of_protection": "Aura of Protection",
            "aura_expansion": "Aura Expansion",
            "aura_of_devotion": "Aura of Devotion",
        }),
        ("zh-CN", {
            "aura_of_protection": "守护灵光",
            "aura_expansion": "灵光扩展",
            "aura_of_devotion": "奉献灵光",
        }),
    ):
        bundle = runtime.load_bundle(locale)
        labels = bundle.get(
            "class_feature_catalog", "srd_class_features",
        )["labels"]
        for feature_id, label in expected.items():
            assert labels[feature_id] == label


def _mutated_bundle(base, feature_id: str, mutate):
    bundle = replace(base, entities={k: dict(v) for k, v in base.entities.items()})
    raw = bundle.entities["class_feature_catalog"]["srd_class_features"]
    raw = {**raw, "classes": {
        cls: {**body, "features": [dict(item) for item in body["features"]]}
        for cls, body in raw["classes"].items()
    }}
    bundle.entities["class_feature_catalog"]["srd_class_features"] = raw
    target = next(
        item for item in raw["classes"]["paladin"]["features"]
        if item["id"] == feature_id
    )
    mutate(target)
    return bundle


@pytest.mark.parametrize(
    "feature_id, mutate",
    [
        ("aura_of_protection", lambda f: f["parameters"].update({"aura_range_ft": 0})),
        ("aura_of_protection", lambda f: f["parameters"].update({"aura_range_ft": True})),
        ("aura_of_protection", lambda f: f["parameters"].update({"aura_save_bonus_ability": "luck"})),
        ("aura_of_protection", lambda f: f["parameters"].pop("aura_save_bonus_minimum")),
        ("aura_of_protection", lambda f: f["parameters"].update({"aura_save_bonus_minimum": 0})),
        ("aura_of_protection", lambda f: f["parameters"].pop("aura_range_ft")),
        ("aura_of_devotion", lambda f: f["parameters"].update({"aura_condition_immunities": []})),
        ("aura_of_devotion", lambda f: f["parameters"].update({"aura_condition_immunities": ["charmed", "charmed"]})),
        ("aura_of_devotion", lambda f: f["parameters"].update({"aura_condition_immunities": ["frightened"]})),
        ("aura_of_devotion", lambda f: f["parameters"].update({"aura_condition_immunities": "charmed"})),
        ("aura_of_devotion", lambda f: f["parameters"].update({"aura_source_feature_id": "no_such_aura"})),
        ("aura_of_devotion", lambda f: f["parameters"].pop("aura_source_feature_id")),
        ("aura_expansion", lambda f: f["parameters"].update({"aura_expands_feature_id": "aura_expansion"})),
        ("aura_expansion", lambda f: f["parameters"].pop("aura_expands_feature_id")),
        ("aura_expansion", lambda f: f["parameters"].update({"aura_expands_feature_id": "aura_of_devotion"})),
    ],
)
def test_malformed_aura_declarations_fail_closed(feature_id: str, mutate) -> None:
    """5-6. Every illegal shape is rejected at load, never at first activation."""

    from src.rulesets.dnd2024.features.models import ClassFeatureError

    runtime = Dnd2024Runtime()
    with pytest.raises(ClassFeatureError):
        engine, _ = _resolver_for(runtime, feature_id, mutate)
        del engine


def _resolver_for(runtime: Dnd2024Runtime, feature_id: str, mutate):
    from src.rulesets.dnd2024.features.resolver import Dnd2024ClassFeatureResolver

    bundle = _mutated_bundle(runtime.load_bundle("en"), feature_id, mutate)
    return Dnd2024ClassFeatureResolver(bundle), None


def test_an_expansion_cycle_is_rejected_at_load() -> None:
    """6 (continued). A→B→A makes "the effective range" order-dependent.

    Two declarations can only form a cycle if both carry a range of their own,
    so this pins the walk rather than the single-hop check: ``aura_expansion``
    is given a range and the pair is made to expand each other.
    """

    from src.rulesets.dnd2024.features.models import ClassFeatureError
    from src.rulesets.dnd2024.features.resolver import Dnd2024ClassFeatureResolver

    runtime = Dnd2024Runtime()
    bundle = _mutated_bundle(
        runtime.load_bundle("en"), "aura_expansion",
        lambda item: item["parameters"].update({"aura_range_ft": 10}),
    )
    raw = bundle.entities["class_feature_catalog"]["srd_class_features"]
    protection = next(
        item for item in raw["classes"]["paladin"]["features"]
        if item["id"] == "aura_of_protection"
    )
    protection["parameters"].update({
        "aura_expands_feature_id": "aura_expansion",
        "aura_range_override_ft": 30,
    })
    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(bundle)


# --------------------------------------------------------------------------
# 6.2 Aura of Protection numbers and range
# --------------------------------------------------------------------------


def _view(engine, instance, actor_id: str) -> dict:
    return engine._actor_view(instance, combat_state(instance), actor_id)


def _charisma_modifier(engine, instance, actor_id: str) -> int:
    from src.rulesets.dnd2024.character.primitives import ability_modifier

    return ability_modifier(int(_view(engine, instance, actor_id)["abilities"]["cha"]))


def test_the_bonus_is_the_charisma_modifier_and_stays_separate() -> None:
    """7. modifier keeps the sheet value; aura_bonus is its own line item."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    expected = max(1, _charisma_modifier(engine, instance, "player:gm"))
    events = direct_cast(engine, instance, "player:gm", [10], actor_id="player:wiz")
    check = check_in(events)
    sheet_wis = int(_view(engine, instance, "player:gm")["saving_throws"]["wis"])
    assert check["modifier"] == sheet_wis
    assert check["aura_bonus"] == expected
    assert check["total"] == (
        int(check["natural"]) + sheet_wis + int(check["check_bonus"]) + expected
    )
    assert check["aura_sources"] == [{
        "feature_id": "aura_of_protection",
        "owner": "class:paladin",
        "source_actor_id": "player:gm",
        "range": 10,
        "ability": "cha",
        "ability_modifier": _charisma_modifier(engine, instance, "player:gm"),
    }]


def test_an_ally_is_covered_at_ten_feet_and_dropped_at_eleven() -> None:
    """8. Boundary is inclusive; level 18 moves the same boundary to 30."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 10})
    assert aura_of(engine, instance, "player:wiz")["bonus"] > 0
    place(instance, **{"player:wiz": 11})
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 0

    _, engine18, instance18 = new_combat(paladin_level=18, allies={"wiz": "wizard"})
    place(instance18, **{"player:gm": 0, "player:wiz": 30})
    assert aura_of(engine18, instance18, "player:wiz")["bonus"] > 0
    place(instance18, **{"player:wiz": 31})
    assert aura_of(engine18, instance18, "player:wiz")["bonus"] == 0


@pytest.mark.parametrize("cha", [8, 10, 15])
def test_negative_and_zero_charisma_fall_back_to_plus_one(cha: int) -> None:
    """9. ``max(1, modifier)`` holds for negative, zero and positive alike."""

    from src.rulesets.dnd2024.character.primitives import ability_modifier

    rest = [value for value in (15, 14, 13, 12, 10, 8) if value != cha]
    abilities = dict(zip(("str", "dex", "con", "int", "wis"), rest))
    abilities["cha"] = cha
    _, engine, instance = new_combat(
        paladin_level=6, paladin_abilities=abilities,
    )
    actual_cha = int(_view(engine, instance, "player:gm")["abilities"]["cha"])
    assert aura_of(engine, instance, "player:gm")["bonus"] == max(
        1, ability_modifier(actual_cha),
    )


def test_enemies_and_absent_auras_grant_nothing() -> None:
    """10. Enemies never benefit, and neither does a party without an aura."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    # 敌方落在光环几何范围内也不得受益：范围判定之外还有阵营判定。
    place(instance, **{"player:gm": 0, "player:wiz": 5, "enemy:goblin-1": 3})
    assert aura_of(engine, instance, "player:wiz")["bonus"] > 0
    assert aura_of(engine, instance, "enemy:goblin-1")["bonus"] == 0

    _, engine5, instance5 = new_combat(paladin_level=5, allies={"wiz": "wizard"})
    assert aura_of(engine5, instance5, "player:wiz")["bonus"] == 0

    _, engine6, instance6 = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance6, **{"player:gm": 0, "player:wiz": 40})
    assert aura_of(engine6, instance6, "player:wiz")["bonus"] == 0


@pytest.mark.parametrize(
    "condition_id", ["incapacitated", "paralyzed", "stunned", "unconscious"],
)
def test_an_incapacitated_holder_projects_no_aura_at_all(condition_id: str) -> None:
    """11. The whole emanation is inactive, not just the holder's own share."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    assert aura_of(engine, instance, "player:wiz")["bonus"] > 0
    put_condition(engine, instance, "player:gm", condition_id)
    assert aura_of(engine, instance, "player:gm")["bonus"] == 0
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 0


def test_a_second_paladin_keeps_its_aura_when_the_first_is_down() -> None:
    """11 (continued). Another valid holder still covers the party."""

    _, engine, instance = new_combat(
        paladin_level=6, allies={"p2": "paladin", "wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:p2": 3, "player:wiz": 5})
    put_condition(engine, instance, "player:gm", "incapacitated")
    source = aura_of(engine, instance, "player:wiz")["sources"]
    assert [row["source_actor_id"] for row in source] == ["player:p2"]


def test_a_dead_holder_stops_projecting_immediately() -> None:
    """11 (continued). Death is not just another condition on the list.

    The reducer removes ``unconscious`` the moment it marks a creature dead,
    and ``dead`` is not a catalog condition, so the incapacitated flag alone
    would let a corpse keep radiating.  0 HP has to switch the emanation off on
    its own, and the death has to come from real death saves, not a hand-set
    flag.
    """

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 1

    down(engine, instance)
    assert "unconscious" in _canonical_of(instance, "gm")["conditions"]
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 0

    # 两个天然 1 就是四次失败：reducer 写 dead，并摘掉 unconscious。
    for _ in range(2):
        _death_save(engine, instance, rolls=[1])
    canonical = _canonical_of(instance, "gm")
    assert "dead" in canonical["conditions"]
    assert "unconscious" not in canonical["conditions"]
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 0


def test_two_auras_never_stack_and_the_higher_one_wins() -> None:
    """12. Highest bonus only; leaving that holder falls back to the other."""

    low = {"str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8}
    high = {"str": 14, "dex": 13, "con": 12, "int": 10, "wis": 8, "cha": 15}
    second_runtime = Dnd2024Runtime()
    _, engine, instance = new_combat(
        paladin_level=6,
        paladin_abilities=low,
        ally_sheets={
            "p2": paladin_sheet(second_runtime, level=6, abilities=high),
        },
        allies={"wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:p2": 4, "player:wiz": 2})
    best = max(
        1, _charisma_modifier(engine, instance, "player:gm"),
        _charisma_modifier(engine, instance, "player:p2"),
    )
    covered = aura_of(engine, instance, "player:wiz")
    assert covered["bonus"] == best
    assert len(covered["sources"]) == 1
    # 把高魅力那位挪出范围后立刻改用另一位，而不是归零。
    place(instance, **{"player:p2": 50})
    fallback = aura_of(engine, instance, "player:wiz")
    assert fallback["bonus"] == max(1, _charisma_modifier(engine, instance, "player:gm"))
    assert fallback["sources"][0]["source_actor_id"] == "player:gm"


def test_equal_bonuses_pick_a_stable_source() -> None:
    """13. Same value breaks on initiative order, and evaluation is repeatable."""

    _, engine, instance = new_combat(
        paladin_level=6,
        allies={"p2": "paladin", "wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:p2": 4, "player:wiz": 2})
    first = aura_of(engine, instance, "player:wiz")
    second = aura_of(engine, instance, "player:wiz")
    assert first == second
    order = [str(item) for item in combat_state(instance)["initiative"]]
    expected = min(
        ("player:gm", "player:p2"), key=lambda actor: order.index(actor),
    )
    assert first["sources"][0]["source_actor_id"] == expected


def test_expansion_widens_only_its_own_holder() -> None:
    """14. A level-18 paladin does not lend 30 feet to a level-6 ally."""

    _, engine, instance = new_combat(
        paladin_level=18, allies={"p2": "paladin", "wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:p2": 5, "player:wiz": 25})
    covered = aura_of(engine, instance, "player:wiz")
    assert [row["range"] for row in covered["sources"]] == [30]
    assert covered["sources"][0]["source_actor_id"] == "player:gm"
    # 6 级队友的光环仍是 10 尺，且不会额外多出一份加值。
    assert aura_of(engine, instance, "player:p2")["sources"][0]["source_actor_id"] == "player:gm"
    place(instance, **{"player:gm": 60})
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 0


# --------------------------------------------------------------------------
# 6.3 every saving-throw surface
# --------------------------------------------------------------------------


def _canonical_of(instance, uid: str) -> dict:
    return instance.players[uid]["character_sheet"]["ruleset_character"]


def down(engine, instance, actor_id: str = "player:gm") -> None:
    """Drop one player to 0 HP the way the reducer does.

    Writing ``hp = 0`` straight onto the sheet would invent a state the engine
    never produces: ``resource.changed`` pairs 0 HP with ``unconscious``
    ("zero_hp"), and a creature at 0 HP is Unconscious or dead.  That pairing is
    exactly what switches its own aura off, so a bare HP write would test a
    corpse-free world that cannot happen in play.
    """

    _, uid = actor_id.split(":", 1)
    hp = int(_canonical_of(instance, uid)["resources"]["hp"])
    apply_events(engine, instance, [{
        "type": "resource.changed", "target_id": actor_id,
        "resource": "hp", "delta": -max(hp, 1),
    }])


def give_turn(instance, actor_id: str) -> None:
    """Hand the turn to one actor so an off-turn intent is legal."""

    combat = combat_state(instance)
    combat["turn_index"] = combat["initiative"].index(actor_id)


def _death_save(engine, instance, actor_id: str = "player:gm", rolls=()) -> list[dict]:
    # 死亡豁免只在本人回合可提交；把回合交给该角色，避免测试偷偷依赖先攻顺序。
    give_turn(instance, actor_id)
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": f"death-save-{version}", "type": "death_save",
        "actor_id": actor_id, "expected_version": version,
        "submitted_by": actor_id.split(":", 1)[1],
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def test_a_dying_ally_can_pass_on_the_aura_alone() -> None:
    """19. natural 9 plus a +1 aura is an ordinary success at DC 10.

    The roll belongs to the dying *ally*, not to the paladin: a creature at
    0 HP is Unconscious, so the paladin's own emanation is switched off, and
    only a living holder can lend a bonus to that save.  Benefiting from an
    aura needs no consciousness, which is why the 0 HP target keeps the +1.
    """

    low = {"str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8}
    _, engine, instance = new_combat(
        paladin_level=6, paladin_abilities=low, allies={"wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 1

    down(engine, instance, "player:wiz")
    events = _death_save(engine, instance, "player:wiz", rolls=[9])
    check = check_in(events, "death_save")
    assert check["natural"] == 9
    assert check["aura_bonus"] == 1
    assert check["modifier"] == 0
    assert check["total"] == 10
    assert check["success"] is True


def test_a_natural_one_still_fails_twice_and_a_natural_twenty_still_heals() -> None:
    """19 (continued). The dedicated faces outrank the ordinary comparison."""

    low = {"str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8}
    _, engine, instance = new_combat(
        paladin_level=6, paladin_abilities=low, allies={"wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    down(engine, instance, "player:wiz")
    events = _death_save(engine, instance, "player:wiz", rolls=[1])
    check = check_in(events, "death_save")
    # 天然 1 仍是两次失败——光环加值不能把 1 变成普通成功。
    assert check["total"] == 2
    assert check["success"] is False
    resolved = next(
        event for event in events if event["type"] == "dnd2024.death_save.resolved"
    )
    assert resolved["failures"] == 2
    assert resolved["successes"] == 0

    _, engine2, instance2 = new_combat(
        paladin_level=6, paladin_abilities=low, allies={"wiz": "wizard"},
    )
    place(instance2, **{"player:gm": 0, "player:wiz": 5})
    down(engine2, instance2, "player:wiz")
    events2 = _death_save(engine2, instance2, "player:wiz", rolls=[20])
    check2 = check_in(events2, "death_save")
    assert check2["natural"] == 20
    assert check2["success"] is True
    healed = next(
        event for event in events2 if event["type"] == "dnd2024.death_save.resolved"
    )
    assert healed["hp"] == 1


def test_bless_counts_on_a_death_save_and_says_so() -> None:
    """19 (continued). A death save is still a saving throw, Bless included.

    The task book sketched ``bless_bonus: 0`` for this surface.  Dropping a
    bonus the baseline already applied is §7.1's non-goal, so the field
    reports what actually happened instead of what the sketch assumed: the
    conditional d4 stays in the total and is named separately.
    """

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    put_condition(engine, instance, "player:wiz", "blessed")
    down(engine, instance, "player:wiz")
    # 骰序：死亡豁免的 d20，然后才是 Bless 的一枚 d4。
    events = _death_save(engine, instance, "player:wiz", rolls=[9, 4])
    check = check_in(events, "death_save")
    assert check["natural"] == 9
    assert check["check_bonus"] == 4
    assert check["bless_bonus"] == 4
    assert check["aura_bonus"] == 1
    assert check["total"] == 14


def test_an_auto_failed_save_rolls_nothing_and_gains_nothing() -> None:
    """20. Paralyzed DEX saves stay empty, consume no die and add no aura."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    put_condition(engine, instance, "player:gm", "paralyzed")
    before = aura_of(engine, instance, "player:gm")
    assert before["bonus"] == 0
    events = direct_cast(
        engine, instance, "player:gm", [], actor_id="player:wiz",
        spell_ref="spell:burning_hands", slot_level=1,
    )
    check = check_in(events)
    assert check["rolls"] == []
    assert check["auto_failed"] is True
    assert check["total"] == 0
    assert check["aura_bonus"] == 0
    assert check["aura_sources"] == []


def test_a_bare_sheet_keeps_its_numbers_when_no_aura_covers_it() -> None:
    """22. Without an aura the event still carries the fields, all zero."""

    _, engine, instance = new_combat(paladin_level=5, allies={"wiz": "wizard"})
    events = direct_cast(engine, instance, "player:gm", [10], actor_id="player:wiz")
    check = check_in(events)
    assert check["aura_bonus"] == 0
    assert check["aura_sources"] == []
    assert check["total"] == int(check["natural"]) + int(check["modifier"]) + int(
        check["check_bonus"]
    )


# --------------------------------------------------------------------------
# 6.4 Aura of Devotion
# --------------------------------------------------------------------------


def test_a_charmed_ally_inside_the_aura_never_gets_the_condition() -> None:
    """23. The save still fails; only the condition fails to land."""

    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 10})
    events = direct_cast(
        engine, instance, "player:wiz", [1], actor_id="player:gm",
        spell_ref="spell:charm_person",
    )
    check = check_in(events)
    assert check["success"] is False
    assert check["blocked_condition"] == "charmed"
    assert check["blocked_by_aura"] == {
        "feature_id": "aura_of_devotion",
        "owner": "subclass:oath_of_devotion",
        "source_actor_id": "player:gm",
    }
    assert not any(
        event["type"] == "condition.applied" and event.get("condition") == "charmed"
        for event in events
    )


def test_outside_the_aura_the_condition_lands_normally() -> None:
    """24. Eleven feet is outside ten; the same cast then sticks."""

    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 11})
    events = direct_cast(
        engine, instance, "player:wiz", [1], actor_id="player:gm",
        spell_ref="spell:charm_person",
    )
    check = check_in(events)
    assert "blocked_condition" not in check
    applied = [
        event for event in events
        if event["type"] == "condition.applied" and event.get("condition") == "charmed"
    ]
    assert len(applied) == 1

    # 18 级的 Aura Expansion 把同一条边界推到 30 尺：30 内拦截，31 外照常落账。
    _, engine18, instance18 = new_combat(paladin_level=18, allies={"wiz": "wizard"})
    place(instance18, **{"player:gm": 0, "player:wiz": 30})
    inside = direct_cast(engine18, instance18, "player:wiz", [1])
    assert check_in(inside)["blocked_condition"] == "charmed"

    place(instance18, **{"player:wiz": 31})
    outside = direct_cast(engine18, instance18, "player:wiz", [1])
    assert "blocked_condition" not in check_in(outside)


def test_the_devotion_paladin_is_immune_too() -> None:
    """25. The holder sits at distance 0, which its own aura covers."""

    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 20})
    events = direct_cast(
        engine, instance, "player:gm", [1], actor_id="player:wiz",
        spell_ref="spell:charm_person",
    )
    check = check_in(events)
    assert check["blocked_condition"] == "charmed"
    assert check["blocked_by_aura"]["source_actor_id"] == "player:gm"


def test_a_level_six_devotion_paladin_does_not_yet_immunize() -> None:
    """25 (continued). The subclass grant arrives at seven, not six."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    events = direct_cast(
        engine, instance, "player:wiz", [1], actor_id="player:gm",
        spell_ref="spell:charm_person",
    )
    check = check_in(events)
    assert "blocked_condition" not in check


def test_an_incapacitated_holder_also_loses_the_immunity() -> None:
    """26. Devotion rides on the base aura, so both die together."""

    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    put_condition(engine, instance, "player:gm", "incapacitated")
    events = direct_cast(
        engine, instance, "player:wiz", [1], actor_id="player:gm",
        spell_ref="spell:charm_person",
    )
    check = check_in(events)
    assert "blocked_condition" not in check
    assert any(
        event["type"] == "condition.applied" and event.get("condition") == "charmed"
        for event in events
    )


def test_only_charmed_is_intercepted() -> None:
    """27. Hold Person still paralyzes; the immunity is not a general shield."""

    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    events = direct_cast(
        engine, instance, "player:wiz", [1], actor_id="player:gm",
        spell_ref="spell:hold_person",
    )
    check = check_in(events)
    assert "blocked_condition" not in check
    applied = [
        event for event in events
        if event["type"] == "condition.applied" and event.get("condition") == "paralyzed"
    ]
    assert len(applied) == 1


def test_a_condition_that_needs_no_save_is_still_intercepted(monkeypatch) -> None:
    """27 (continued). Immunity does not depend on there being a save at all.

    ``condition.applied`` is built in one place shared by buff, debuff,
    automatic damage and spell attacks, so the interception cannot sit behind
    ``mode == "saving_throw"``.  No shipped effect needs that today -- Charmed
    only arrives through Charm Person -- so the shape is pinned by re-pointing
    an existing buff at ``charmed`` and giving it a real cast.
    """

    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    effect = engine.catalog.spell_effects["bless"]
    monkeypatch.setitem(
        engine.catalog.spell_effects, "bless", {**effect, "condition": "charmed"},
    )
    events = events_of(real_cast(engine, instance, "gm", "spell:bless", "player:wiz"))
    assert not any(
        event["type"] == "condition.applied" and event.get("condition") == "charmed"
        for event in events
    )
    # 这条路径没有 check event 可挂，条件被吞掉就必须留一条可见审计。
    message = next(
        event for event in events if event["type"] == "dnd2024.combat.message"
    )
    assert message["blocked_condition"] == "charmed"
    assert message["blocked_by_aura"] == {
        "feature_id": "aura_of_devotion",
        "owner": "subclass:oath_of_devotion",
        "source_actor_id": "player:gm",
    }


def test_an_existing_charmed_is_kept_not_removed_when_entering_the_aura() -> None:
    """28. Walking into the aura suspends the effect; it never deletes it."""

    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 11})
    events = direct_cast(
        engine, instance, "player:wiz", [1], actor_id="player:gm",
        spell_ref="spell:charm_person",
    )
    assert any(
        event["type"] == "condition.applied" and event.get("condition") == "charmed"
        for event in events
    )
    apply_events(engine, instance, events)
    assert "charmed" in conditions_of(engine, instance, "player:wiz")

    # 走进光环：条件保留，不发 removed，也没有任何 suppression 元数据。
    place(instance, **{"player:wiz": 5})
    apply_events(engine, instance, [])
    conditions = conditions_of(engine, instance, "player:wiz")
    assert "charmed" in conditions
    assert "suppressed_by_aura" not in conditions["charmed"]
    source = engine._aura_condition_immunity_source(
        instance, combat_state(instance),
        engine._actor_view(instance, combat_state(instance), "player:wiz"),
        "charmed",
    )
    assert source is not None

    # 走出去：免疫消失，条件仍在。
    place(instance, **{"player:wiz": 11})
    assert engine._aura_condition_immunity_source(
        instance, combat_state(instance),
        engine._actor_view(instance, combat_state(instance), "player:wiz"),
        "charmed",
    ) is None
    assert "charmed" in conditions_of(engine, instance, "player:wiz")


def test_an_interception_ends_no_concentration_and_clears_no_other_charmed() -> None:
    """29. The interception touches the one condition and nothing else.

    Charm Person carries no concentration in SRD 5.2.1 (``1_hour``, no
    ``concentration`` key), so the contract's "does not end the caster's
    concentration early" can only be observed as an absence here: no
    ``concentration.ended``, no ``condition.removed`` for anyone -- including a
    creature that was already Charmed before the cast -- and no invented
    suppression event.  An interception that invented its own bookkeeping would
    fail this test long before the still-hypothetical enemy spellcasting lands.
    """

    _, engine, instance = new_combat(
        paladin_level=7, allies={"wiz": "wizard", "far": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:wiz": 5, "player:far": 40})
    put_condition(engine, instance, "player:far", "charmed")
    assert "charmed" in conditions_of(engine, instance, "player:far")

    events = direct_cast(engine, instance, "player:wiz", [1])
    assert check_in(events)["blocked_condition"] == "charmed"
    assert not any(
        event["type"] in {"dnd2024.concentration.ended", "condition.removed"}
        for event in events
    )
    assert not any("suppress" in str(event["type"]) for event in events)

    apply_events(engine, instance, events)
    assert "charmed" in conditions_of(engine, instance, "player:far")
    assert "charmed" not in conditions_of(engine, instance, "player:wiz")


def test_two_devotion_auras_yield_one_interception_with_one_source() -> None:
    """30. Multiple providers do not stack into multiple settlements."""

    _, engine, instance = new_combat(
        paladin_level=7, allies={"p2": "paladin", "wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:p2": 3, "player:wiz": 5})
    events = direct_cast(
        engine, instance, "player:wiz", [1], actor_id="player:gm",
        spell_ref="spell:charm_person",
    )
    check = check_in(events)
    assert check["blocked_condition"] == "charmed"
    assert isinstance(check["blocked_by_aura"], dict)
    assert len(check["blocked_condition"].split(",")) == 1


def test_bless_and_the_aura_are_reported_separately() -> None:
    """15. One extra d4 for Bless, the aura as its own non-dice line item."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    put_condition(engine, instance, "player:gm", "blessed")
    expected_aura = max(1, _charisma_modifier(engine, instance, "player:gm"))
    # 骰序恰好两枚：一枚 d20 与 Bless 的一枚 d4。多取一枚就会抛错。
    events = direct_cast(engine, instance, "player:gm", [10, 3], actor_id="player:wiz")
    check = check_in(events)
    assert check["rolls"] == [10]
    assert check["check_bonus"] == 3
    assert len(check["check_bonus_rolls"]) == 1
    assert check["aura_bonus"] == expected_aura
    assert check["total"] == 10 + int(check["modifier"]) + 3 + expected_aura


# --------------------------------------------------------------------------
# 6.3 (continued) repeat save, concentration, ability checks
# --------------------------------------------------------------------------


def end_turn(engine, instance, actor_id: str = "player:gm", rolls=()) -> dict:
    combat = combat_state(instance)
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, actor_id),
    )
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": f"end:{actor_id}:{version}", "type": "end_turn",
        "expected_version": version, "submitted_by": "gm", "actor_id": actor_id,
    }
    result = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    return result


def enemy_attack(engine, instance, target_id: str, rolls) -> dict:
    combat = combat_state(instance)
    combat["turn_index"] = combat["initiative"].index("enemy:goblin-1")
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, "enemy:goblin-1"),
    )
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {
        "intent_id": f"attack:{target_id}:{version}", "type": "attack",
        "expected_version": version, "submitted_by": "gm",
        "actor_id": "enemy:goblin-1", "target_id": target_id,
        "attack_id": "scimitar",
    }
    result = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    return result


def test_a_repeat_save_reads_the_aura_where_the_target_stands() -> None:
    """16. The repeat save re-evaluates the aura; DC and ability stay frozen."""

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 10})
    condition = {
        "source": "test", "source_actor_id": "player:gm",
        "save_repeat": {"ability": "wis", "dc": 20},
    }
    _canonical_of(instance, "wiz").setdefault("conditions", {})["charmed"] = condition
    bonus = aura_of(engine, instance, "player:wiz")["bonus"]
    assert bonus > 0

    inside = check_in(engine._save_repeat_events(
        instance, combat_state(instance), "player:wiz", SequenceRng([10]),
    ))
    assert inside["repeat_save"] is True
    # DC 与豁免属性是施放时冻结的快照，位置只影响光环这一项。
    assert inside["target"] == 20
    assert inside["ability"] == "wis"
    assert inside["aura_bonus"] == bonus
    assert inside["total"] == 10 + int(inside["modifier"]) + bonus
    assert inside["success"] is False

    # 同一条件的回合末结算换成 11 尺：结算当时的光环没了，加值随之消失。
    place(instance, **{"player:wiz": 11})
    outside = check_in(engine._save_repeat_events(
        instance, combat_state(instance), "player:wiz", SequenceRng([10]),
    ))
    assert outside["aura_bonus"] == 0
    assert outside["total"] == 10 + int(outside["modifier"])
    assert outside["total"] == int(inside["total"]) - bonus
    assert outside["target"] == 20

    # 成功仍然只移除本条条件。
    condition["save_repeat"]["dc"] = 5
    place(instance, **{"player:wiz": 10})
    events = engine._save_repeat_events(
        instance, combat_state(instance), "player:wiz", SequenceRng([15]),
    )
    assert check_in(events)["success"] is True
    apply_events(engine, instance, events)
    assert "charmed" not in conditions_of(engine, instance, "player:wiz")


def test_a_concentration_save_counts_the_aura_exactly_once() -> None:
    """17-18. Both damage entries add the aura once, and the DC sees one total.

    Shield, Uncanny Dodge and Divine Smite are damage *sources*: each one
    settles its damage before ``_concentration_events`` runs, so all of them
    meet the same chokepoint with a single final number.  This slice builds no
    Rogue and no Shield-capable defender, so the chokepoint is pinned twice --
    once through the real weapon-attack chain, once by handing the helper the
    merged damage such a reaction would have left behind.
    """

    _, engine, instance = new_combat(paladin_level=6)
    place(instance, **{"player:gm": 0, "enemy:goblin-1": 5})
    real_cast(engine, instance, "gm", "spell:bless", "player:gm")
    assert _canonical_of(instance, "gm")["spellcasting"]["class"]["concentration"]
    bonus = aura_of(engine, instance, "player:gm")["bonus"]
    assert bonus > 0

    end_turn(engine, instance, "player:gm")
    # 骰序：命中 d20、1d6 伤害、专注豁免 d20。多取一枚就会抛错。
    events = events_of(enemy_attack(engine, instance, "player:gm", [15, 3, 13]))
    checks = [event for event in events if event["type"] == "check.resolved"]
    assert [event["kind"] for event in checks] == ["attack", "concentration"]
    assert "aura_bonus" not in checks[0]
    weapon = checks[1]
    assert weapon["aura_bonus"] == bonus
    assert len(weapon["aura_sources"]) == 1
    assert weapon["total"] == (
        int(weapon["natural"]) + int(weapon["modifier"])
        + int(weapon["check_bonus"]) + bonus
    )

    view = engine._actor_view(instance, combat_state(instance), "player:gm")
    merged = engine._concentration_events(
        instance, combat_state(instance), view, 22, SequenceRng([13]),
    )
    assert len(merged) == 1
    assert merged[0]["aura_bonus"] == bonus
    assert len(merged[0]["aura_sources"]) == 1
    # 22 点合并伤害只判一次，DC = max(10, 22 // 2)；分项不会各判一次。
    assert merged[0]["target"] == 11


def test_ability_checks_never_carry_the_aura() -> None:
    """21. Attacks, initiative and enemy checks are not saving throws.

    The aura lives in the saving-throw surfaces only.  Initiative is settled at
    ``combat.start`` before any aura exists, a weapon attack is an attack roll,
    and the only ability check the aura could reach -- a Counterspell check --
    belongs to the counter-caster, which is an enemy and therefore excluded by
    the same side filter that keeps test 10 honest.
    """

    order = {}
    for level in (5, 6):
        _, engine, instance = new_combat(paladin_level=level)
        place(instance, **{"player:gm": 0, "enemy:goblin-1": 5})
        order[level] = list(combat_state(instance)["initiative"])
        assert aura_of(engine, instance, "enemy:goblin-1")["bonus"] == 0
    # 同一套固定骰序下，6 级（有光环）与 5 级（无光环）的先攻顺序完全一致。
    assert order[5] == order[6]

    _, engine, instance = new_combat(paladin_level=6, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5, "enemy:goblin-1": 5})
    events = events_of(enemy_attack(engine, instance, "player:wiz", [15, 3]))
    attack = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    )
    assert "aura_bonus" not in attack
    assert "aura_sources" not in attack
    # 攻击检定没有多取骰：命中 + 伤害正好两枚，序列用尽即抛错。
    assert attack["natural"] == 15


# --------------------------------------------------------------------------
# 6.5 persistence and regression
# --------------------------------------------------------------------------


def _settled(instance, uid: str) -> dict:
    canonical = _canonical_of(instance, uid)
    return {
        "hp": dict(canonical.get("resources") or {}),
        "conditions": copy.deepcopy(canonical.get("conditions") or {}),
        "concentration": copy.deepcopy(
            (canonical.get("spellcasting") or {}).get("class", {}).get("concentration"),
        ),
        "death_saves": copy.deepcopy(canonical.get("death_saves")),
    }


def _replay_is_a_no_op(engine, instance, batch: dict, uid: str) -> None:
    """31. Settling the same batch twice writes nothing the second time."""

    before = _settled(instance, uid)
    replay = engine.apply_batch(instance, copy.deepcopy(batch))
    assert replay["duplicate"] is True
    assert replay["applied"] is False
    assert _settled(instance, uid) == before


def test_replaying_an_aura_batch_changes_nothing() -> None:
    """31. The audit fields are inert on replay across all three surfaces."""

    # 法术豁免：被拦下的 Charm Person。
    _, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    batch = apply_events(
        engine, instance, direct_cast(engine, instance, "player:wiz", [1]),
    )
    _replay_is_a_no_op(engine, instance, batch, "wiz")

    # 专注豁免：带光环加值的一次受伤结算。
    _, engine, instance = new_combat(paladin_level=6)
    place(instance, **{"player:gm": 0, "enemy:goblin-1": 5})
    real_cast(engine, instance, "gm", "spell:bless", "player:gm")
    end_turn(engine, instance, "player:gm")
    concentrated = enemy_attack(engine, instance, "player:gm", [15, 3, 13])
    batch = concentrated["event_batch"]
    assert next(
        event for event in batch["events"] if event.get("kind") == "concentration"
    )["aura_bonus"] > 0
    _replay_is_a_no_op(engine, instance, batch, "gm")

    # 死亡豁免：濒死队友天然 9 加 +1 光环刚好 10 的普通成功。
    low = {"str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8}
    _, engine, instance = new_combat(
        paladin_level=6, paladin_abilities=low, allies={"wiz": "wizard"},
    )
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    down(engine, instance, "player:wiz")
    give_turn(instance, "player:wiz")
    resolved = engine.resolve_intent(instance, {
        "intent_id": "death-save-replay", "type": "death_save",
        "actor_id": "player:wiz", "submitted_by": "wiz",
        "expected_version": ruleset_runtime.state(instance)["version"],
    }, SequenceRng([9]))
    assert resolved["ok"] is True, resolved
    batch = resolved["event_batch"]
    assert check_in(batch["events"], "death_save")["aura_bonus"] == 1
    assert engine.apply_batch(instance, batch)["applied"] is True
    _replay_is_a_no_op(engine, instance, batch, "wiz")


def test_the_feed_shows_the_aura_and_leaks_nothing_else() -> None:
    """32. The audit fields survive the feed whitelist; the sheet does not."""

    runtime, engine, instance = new_combat(paladin_level=7, allies={"wiz": "wizard"})
    place(instance, **{"player:gm": 0, "player:wiz": 5})
    apply_events(engine, instance, direct_cast(engine, instance, "player:wiz", [1]))

    blocked = next(
        event for event in runtime._recent_combat_events(instance)
        if event.get("type") == "check.resolved"
        and isinstance(event.get("blocked_by_aura"), dict)
    )
    assert blocked["blocked_condition"] == "charmed"
    assert blocked["blocked_by_aura"] == {
        "feature_id": "aura_of_devotion",
        "owner": "subclass:oath_of_devotion",
        "source_actor_id": "player:gm",
    }
    # 加值与来源同时可见：拦截不是"查不到理由的静默丢弃"。
    assert blocked["aura_bonus"] == 1
    assert blocked["aura_sources"][0]["feature_id"] == "aura_of_protection"
    for leaked in ("character_sheet", "parameters", "conditions", "spells", "features"):
        assert leaked not in blocked

    # 位置来自持久化状态：改回 11 尺后，同一份状态立刻给出 0。
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 1
    combat_state(instance)["positions"]["player:wiz"] = 11
    assert aura_of(engine, instance, "player:wiz")["bonus"] == 0
