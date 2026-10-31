"""Class feature runtime v1: identity, availability, parameters, i18n.

These tests only exercise the feature boundary.  Combat behavior lives in
``test_dnd2024_monk_combat.py`` and the resource lifecycle in
``test_dnd2024_monk_resources.py``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.resting import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import advance, monk_sheet


MONK_FEATURES_AT_TWO = (
    "martial_arts", "monks_focus", "flurry_of_blows", "patient_defense",
    "step_of_the_wind",
)
MONK_FEATURE_IDS = frozenset({"martial_arts", *MONK_FEATURES_AT_TWO})
MONK_CAPABILITY_IDS = frozenset({
    "bonus_unarmed_strike",
    "flurry_of_blows",
    "patient_defense",
    "patient_defense_focus",
    "step_of_the_wind",
    "step_of_the_wind_focus",
})


def _resolver(locale: str = "en") -> tuple[Dnd2024Runtime, Dnd2024ClassFeatureResolver]:
    runtime = Dnd2024Runtime()
    return runtime, Dnd2024ClassFeatureResolver(runtime.load_bundle(locale))


def _character(level: int, class_ref: str = "class:monk", **extra) -> dict:
    return {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
        **extra,
    }


def _with_focus(current: int, maximum: int, level: int = 2) -> dict:
    return _character(level, resources={
        "class": {"focus_points": {"current": current, "maximum": maximum}},
    })


def _mutated_bundle(runtime: Dnd2024Runtime, mutate) -> object:
    """Return the real bundle with one feature-catalog edit applied."""

    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    mutate(entities["class_feature_catalog"]["srd_class_features"])
    return replace(bundle, entities=entities)


def test_monk_level_one_owns_martial_arts_only() -> None:
    _runtime, resolver = _resolver()

    assert [feature.id for feature in resolver.features_for(_character(1))] == [
        "martial_arts",
    ]
    assert resolver.has_feature(_character(1), "martial_arts") is True
    assert resolver.has_feature(_character(1), "monks_focus") is False
    assert resolver.has_feature(_character(1), "flurry_of_blows") is False
    assert resolver.class_resource_definition(_character(1), "focus_points") is None


def test_monk_level_two_gains_focus_and_the_three_focus_options() -> None:
    _runtime, resolver = _resolver()
    character = _character(2)

    owned = [feature.id for feature in resolver.features_for(character)]

    assert owned == list(MONK_FEATURES_AT_TWO)
    for feature_id in MONK_FEATURES_AT_TWO:
        assert resolver.has_feature(character, feature_id) is True


@pytest.mark.parametrize(
    "preset_id",
    ["stalwart_guardian", "lucky_scout", "curious_arcanist", "woodland_guide"],
)
def test_non_monk_characters_never_own_monk_features(preset_id: str) -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": preset_id},
    )

    owned = [feature.id for feature in resolver.features_for(sheet["ruleset_character"])]
    declared = {
        capability.id for capability in resolver.combat_capabilities(
            sheet["ruleset_character"], {"bonus_action": 1},
        )
    }

    assert set(owned).isdisjoint(MONK_FEATURE_IDS)
    assert declared.isdisjoint(MONK_CAPABILITY_IDS)
    assert sheet["ruleset_character"]["resources"]["class"].get("focus_points") is None


def test_advancing_one_to_two_exposes_the_new_capabilities() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    level_one = monk_sheet(runtime, level=1)

    before = resolver.combat_capabilities(
        level_one["ruleset_character"], {"action": 1, "bonus_action": 1},
    )
    advanced = advance(runtime, level_one, 2)
    after = resolver.combat_capabilities(
        advanced["ruleset_character"], {"action": 1, "bonus_action": 1},
    )

    # 1 级只有武艺带来的附赠徒手打击；升级后才出现专注点与三个专注用法。
    assert [item.id for item in before] == ["bonus_unarmed_strike"]
    assert {item.id for item in after} == {
        "bonus_unarmed_strike", "flurry_of_blows", "patient_defense",
        "patient_defense_focus", "step_of_the_wind", "step_of_the_wind_focus",
    }
    assert advanced["class_resources"][0]["id"] == "focus_points"
    assert advanced["class_resources"][0]["current"] == 2


@pytest.mark.parametrize("level,expected", [(1, "1d6"), (5, "1d8"), (11, "1d10"), (17, "1d12")])
def test_martial_arts_die_comes_from_the_progression_table(level: int, expected: str) -> None:
    _runtime, resolver = _resolver()

    assert resolver.feature_value(
        _character(level), "martial_arts", "unarmed_damage_die",
    ) == expected
    assert resolver.unarmed_strike_die(_character(level)) == expected


def test_martial_arts_die_is_absent_without_the_feature() -> None:
    _runtime, resolver = _resolver()

    assert resolver.feature_value(
        _character(4, "class:fighter"), "martial_arts", "unarmed_damage_die", "",
    ) == ""
    assert resolver.unarmed_strike_die(_character(4, "class:fighter")) == ""


def test_feature_labels_follow_the_bundle_locale() -> None:
    _runtime, english = _resolver("en")
    _runtime_zh, chinese = _resolver("zh-CN")

    assert english.feature_value(_character(2), "monks_focus", "missing", None) is None
    assert [view.name for view in english.feature_views(_character(2))][:2] == [
        "Martial Arts", "Monk's Focus",
    ]
    labels = {view.id: view.name for view in chinese.feature_views(_character(2))}
    assert labels == {
        "martial_arts": "武艺",
        "monks_focus": "武僧专注",
        "flurry_of_blows": "疾风连击",
        "patient_defense": "坚守防御",
        "step_of_the_wind": "疾风步",
    }


def test_feature_views_expose_scalar_parameters_only() -> None:
    _runtime, resolver = _resolver()

    martial_arts = next(
        view for view in resolver.feature_views(_character(3)) if view.id == "martial_arts"
    )

    assert martial_arts.values == {
        "unarmed_damage_die": "1d6",
        "unarmed_ability_choice": ["str", "dex"],
    }
    assert martial_arts.minimum_level == 1
    assert martial_arts.source_ref.startswith("srd-5.2.1:")


def test_class_resource_definition_reflects_canonical_state() -> None:
    _runtime, resolver = _resolver()

    definition = resolver.class_resource_definition(_with_focus(1, 3), "focus_points")

    assert definition is not None
    assert (definition.current, definition.maximum) == (1, 3)
    assert definition.name == "Focus Points"
    assert definition.minimum_level == 2
    assert definition.recovery["short"] == "all" and definition.recovery["long"] == "all"
    assert resolver.class_resource_definition(_with_focus(1, 3), "second_wind") is None


def test_combat_capabilities_require_action_and_resource_budget() -> None:
    _runtime, resolver = _resolver()

    def available(context: dict) -> set[str]:
        return {
            item.id for item in resolver.combat_capabilities(_with_focus(1, 2), context)
        }

    assert available({"action": 1, "bonus_action": 1}) >= {"flurry_of_blows"}
    # 附赠动作已用：需要附赠动作的能力全部不可用。
    assert available({"action": 1, "bonus_action": 0}) == set()
    # 专注点为 0：只有不需要专注的版本仍然可用。
    no_focus = {
        item.id for item in resolver.combat_capabilities(_with_focus(0, 2), {"bonus_action": 1})
    }
    assert no_focus == {"bonus_unarmed_strike", "patient_defense", "step_of_the_wind"}


def test_unavailable_capabilities_report_a_reason() -> None:
    _runtime, resolver = _resolver()

    views = {
        item.id: item
        for item in resolver.combat_capabilities(
            _with_focus(0, 2), {"bonus_action": 1}, include_unavailable=True,
        )
    }

    assert views["flurry_of_blows"].available is False
    assert "not enough Focus Points" in views["flurry_of_blows"].blocked_reason
    assert views["patient_defense"].available is True
    assert views["patient_defense"].blocked_reason == ""
    assert [cost.kind for cost in views["flurry_of_blows"].costs] == [
        "bonus_action", "resource",
    ]


def test_unknown_class_or_partial_sheet_owns_nothing() -> None:
    _runtime, resolver = _resolver()

    assert resolver.features_for({"build": {"class_levels": []}}) == ()
    assert resolver.features_for({"build": {}}) == ()
    assert resolver.features_for({"build": {"class_levels": [
        {"class_ref": "class:monk", "level": 0},
    ]}}) == ()
    assert resolver.features_for(None) == ()
    assert resolver.features_for({
        "build": {"class_levels": [{"class_ref": "class:homebrew", "level": 3}]},
    }) == ()


def test_bundle_without_the_feature_catalog_degrades_to_no_features() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    entities.pop("class_feature_catalog")
    resolver = Dnd2024ClassFeatureResolver(replace(bundle, entities=entities))

    assert resolver.available is False
    assert resolver.features_for(_character(5)) == ()
    assert resolver.class_resource_definition(_with_focus(2, 2), "focus_points") is None
    assert resolver.combat_capabilities(_with_focus(2, 2), {"bonus_action": 1}) == ()


def _drop_actions(catalog: dict) -> None:
    catalog["classes"]["monk"]["features"][0]["capabilities"][0]["actions"] = [
        {"kind": "teleport"},
    ]


def _bad_track(catalog: dict) -> None:
    catalog["classes"]["monk"]["features"][0]["parameters"]["unarmed_damage_die_track"] = (
        "not_a_track"
    )


def _bad_format(catalog: dict) -> None:
    catalog["classes"]["monk"]["features"][0]["parameters"]["unarmed_damage_die_format"] = (
        "{die}{die}"
    )


def _orphan_option(catalog: dict) -> None:
    catalog["classes"]["monk"]["features"][2]["option_of"] = "not_declared"


def _undeclared_resource(catalog: dict) -> None:
    catalog["classes"]["monk"]["features"][1]["resource_id"] = "not_a_resource"


def _bad_cost_amount(catalog: dict) -> None:
    catalog["classes"]["monk"]["features"][2]["capabilities"][0]["cost"]["resources"][0][
        "amount"
    ] = 0


def _bad_once_per_turn(catalog: dict) -> None:
    catalog["classes"]["fighter"]["features"][1]["capabilities"][0]["once_per_turn"] = "yes"


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_actions,
        _bad_track,
        _bad_format,
        _orphan_option,
        _undeclared_resource,
        _bad_cost_amount,
        _bad_once_per_turn,
    ],
)
def test_malformed_feature_declarations_fail_closed(mutate) -> None:
    runtime = Dnd2024Runtime()

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_mutated_bundle(runtime, mutate))


def test_resolver_requires_a_validated_progression_table() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    entities.pop("progression_catalog")
    resolver = Dnd2024ClassFeatureResolver(replace(bundle, entities=entities))

    assert resolver.available is False
    assert resolver.features_for(_character(5)) == ()


def test_feature_and_rest_declarations_agree() -> None:
    """The read-only resource projection must match the lifecycle authority."""

    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    resolver = Dnd2024ClassFeatureResolver(bundle)
    policies = Dnd2024RestEngine(bundle).rules["class_resources"]

    assert set(resolver.resources.by_class) == set(policies)
    for class_id, specs in policies.items():
        declared = resolver.resources.by_class[class_id]
        assert set(declared) == {str(spec["id"]) for spec in specs}
        for spec in specs:
            entry = declared[str(spec["id"])]
            assert entry.minimum_level == int(spec.get("minimum_level", 1) or 1)
            assert entry.source_ref == str(spec["source_ref"])
            assert entry.short_rest == spec.get("short", "none")
            assert entry.long_rest == spec.get("long", "none")


# --------------------------------------------------------------------------
# Second Wind (Slice 1): heal primitive declaration, progression, and rest
# --------------------------------------------------------------------------


def _fighter(level: int, **extra) -> dict:
    return _character(level, "class:fighter", **extra)


def test_fighter_second_wind_declaration_and_capability_load() -> None:
    _runtime, resolver = _resolver()
    character = _fighter(1, resources={
        "class": {"second_wind": {"current": 2, "maximum": 2}},
    })

    assert [feature.id for feature in resolver.features_for(character)] == ["second_wind"]
    capability = resolver.declared_capability(character, "second_wind")

    assert capability is not None
    assert capability.requires_hostile_target is False
    assert capability.cost.action == "bonus_action"
    assert [
        (cost.resource_id, cost.amount) for cost in capability.cost.resources
    ] == [("second_wind", 1)]
    assert [
        (action.kind, action.count) for action in capability.actions
    ] == [("heal", 1)]
    assert resolver.feature_value(character, "second_wind", "heal_dice") == "1d10"
    assert resolver.feature_value(character, "second_wind", "heal_bonus") == 1
    assert resolver.catalog.label("second_wind") == "Second Wind"


def test_second_wind_labels_follow_the_bundle_locale() -> None:
    _runtime, chinese = _resolver("zh-CN")

    assert chinese.catalog.label("fighter") == "战士"
    assert chinese.catalog.label("second_wind") == "回气"


def test_fighter_action_surge_declaration_and_capability_load() -> None:
    _runtime, resolver = _resolver()
    character = _fighter(2, resources={
        "class": {"action_surge": {"current": 1, "maximum": 1}},
    })

    assert [feature.id for feature in resolver.features_for(character)] == [
        "second_wind", "action_surge",
    ]
    capability = resolver.declared_capability(character, "action_surge")

    assert capability is not None
    assert capability.once_per_turn is True
    assert capability.requires_hostile_target is False
    assert capability.cost.action == ""
    assert [
        (cost.resource_id, cost.amount) for cost in capability.cost.resources
    ] == [("action_surge", 1)]
    assert [
        (action.kind, action.count) for action in capability.actions
    ] == [("action_surge", 1)]
    assert resolver.catalog.label("action_surge") == "Action Surge"


def test_action_surge_label_follows_the_bundle_locale() -> None:
    _runtime, chinese = _resolver("zh-CN")

    assert chinese.catalog.label("action_surge") == "动作如潮"


def test_level_one_fighter_does_not_own_action_surge() -> None:
    _runtime, resolver = _resolver()
    character = _fighter(1, resources={
        "class": {"second_wind": {"current": 2, "maximum": 2}},
    })

    assert [feature.id for feature in resolver.features_for(character)] == ["second_wind"]
    assert resolver.declared_capability(character, "action_surge") is None


def _rest_character(level: int) -> dict:
    return {
        "build": {"class_levels": [{"class_ref": "class:fighter", "level": level}]},
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "resources": {"hp": 12, "max_hp": 24, "hit_dice": {"d10": level}},
    }


@pytest.mark.parametrize("level,maximum", [(1, 2), (4, 3), (10, 4)])
def test_second_wind_maximum_follows_the_progression_track(
    level: int, maximum: int,
) -> None:
    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))

    synced = engine.sync_resources(_rest_character(level))
    resource = synced["resources"]["class"]["second_wind"]

    assert resource["maximum"] == maximum
    assert resource["current"] == maximum


def test_second_wind_recovers_one_use_on_a_short_rest_and_all_on_a_long_rest() -> None:
    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    character = engine.sync_resources(_rest_character(4))
    character["resources"]["class"]["second_wind"]["current"] = 0

    short_rested = engine.complete_short_rest(character)["character"]
    assert short_rested["resources"]["class"]["second_wind"]["current"] == 1

    short_rested["resources"]["class"]["second_wind"]["current"] = 0
    long_rested = engine.complete_long_rest(short_rested)["character"]
    assert long_rested["resources"]["class"]["second_wind"]["current"] == 3


# --------------------------------------------------------------------------
# Rage (Slice 2): sustained condition declaration, progression, and rest
# --------------------------------------------------------------------------


def _barbarian(level: int, **extra) -> dict:
    return _character(level, "class:barbarian", **extra)


def test_barbarian_rage_declaration_and_capabilities_load() -> None:
    _runtime, resolver = _resolver()
    character = _barbarian(1, resources={
        "class": {"rages": {"current": 2, "maximum": 2}},
    })

    assert [feature.id for feature in resolver.features_for(character)] == ["rage"]
    enter = resolver.declared_capability(character, "rage")
    sustain = resolver.declared_capability(character, "rage_sustain")

    assert enter is not None and sustain is not None
    assert enter.requires_hostile_target is False
    assert enter.forbidden_condition == "rage" and enter.requires_condition is None
    assert enter.cost.action == "bonus_action"
    assert [(cost.resource_id, cost.amount) for cost in enter.cost.resources] == [
        ("rages", 1),
    ]
    assert [(action.kind, action.count) for action in enter.actions] == [("rage", 1)]
    assert sustain.requires_condition == "rage" and sustain.forbidden_condition is None
    assert sustain.cost.action == "bonus_action"
    assert [(action.kind, action.count) for action in sustain.actions] == [
        ("rage_sustain", 1),
    ]
    assert resolver.catalog.label("rage") == "Rage"
    assert resolver.catalog.label("rage_sustain") == "Sustain Rage"


@pytest.mark.parametrize("level,bonus", [(1, 2), (9, 3), (16, 4)])
def test_rage_damage_follows_the_progression_track(level: int, bonus: int) -> None:
    _runtime, resolver = _resolver()

    assert resolver.feature_value(_barbarian(level), "rage", "rage_damage") == bonus


def test_rage_labels_follow_the_bundle_locale() -> None:
    _runtime, chinese = _resolver("zh-CN")

    assert chinese.catalog.label("barbarian") == "野蛮人"
    assert chinese.catalog.label("rage") == "狂暴"
    assert chinese.catalog.label("rage_sustain") == "延长狂暴"


@pytest.mark.parametrize("level,maximum", [(1, 2), (3, 3), (6, 4), (12, 5), (17, 6)])
def test_rage_maximum_follows_the_progression_track(level: int, maximum: int) -> None:
    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))

    synced = engine.sync_resources({
        "build": {"class_levels": [{"class_ref": "class:barbarian", "level": level}]},
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "resources": {"hp": 12, "max_hp": 24, "hit_dice": {"d12": level}},
    })

    assert synced["resources"]["class"]["rages"]["maximum"] == maximum


def test_rage_recovers_one_use_on_a_short_rest_and_all_on_a_long_rest() -> None:
    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    character = engine.sync_resources({
        "build": {"class_levels": [{"class_ref": "class:barbarian", "level": 1}]},
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "resources": {"hp": 12, "max_hp": 24, "hit_dice": {"d12": 1}},
    })
    assert character["resources"]["class"]["rages"]["maximum"] == 2
    character["resources"]["class"]["rages"]["current"] = 0

    short_rested = engine.complete_short_rest(character)["character"]
    assert short_rested["resources"]["class"]["rages"]["current"] == 1

    short_rested["resources"]["class"]["rages"]["current"] = 0
    long_rested = engine.complete_long_rest(short_rested)["character"]
    assert long_rested["resources"]["class"]["rages"]["current"] == 2


# --------------------------------------------------------------------------
# Uncanny Dodge (Slice 3): reaction-triggered capability declaration
# --------------------------------------------------------------------------


def _rogue(level: int, **extra) -> dict:
    return _character(level, "class:rogue", **extra)


def test_rogue_sneak_attack_declaration_loads_as_a_passive_feature() -> None:
    _runtime, resolver = _resolver()
    character = _rogue(1)

    assert [feature.id for feature in resolver.features_for(character)] == ["sneak_attack"]
    feature = resolver.features_for(character)[0]
    assert feature.capabilities == ()
    assert feature.resource_id == ""
    assert resolver.feature_value(character, "sneak_attack", "damage_dice") == "1d6"
    assert resolver.declared_capability(character, "sneak_attack") is None
    assert resolver.catalog.label("sneak_attack") == "Sneak Attack"


@pytest.mark.parametrize(
    "level, dice", [(1, "1d6"), (3, "2d6"), (5, "3d6"), (19, "10d6")],
)
def test_sneak_attack_dice_follow_the_rogue_progression(level: int, dice: str) -> None:
    _runtime, resolver = _resolver()

    assert resolver.feature_value(_rogue(level), "sneak_attack", "damage_dice") == dice


def test_rogue_uncanny_dodge_declares_no_capability_and_keeps_parameters() -> None:
    """Uncanny Dodge is owned by the reaction path, not the action list.

    It declares no capability on purpose: listing it in ``available_intents``
    would surface a "use" action that the engine cannot resolve (the feature
    reacts to being hit, it is never the player's turn action).  Its catalog
    parameters stay authoritative for the reaction path.
    """

    _runtime, resolver = _resolver()
    character = _rogue(5)

    assert [feature.id for feature in resolver.features_for(character)] == [
        "sneak_attack", "uncanny_dodge",
    ]
    assert resolver.declared_capability(character, "uncanny_dodge") is None
    assert resolver.feature_value(character, "uncanny_dodge", "damage_multiplier") == 0.5
    assert resolver.catalog.label("uncanny_dodge") == "Uncanny Dodge"


def test_uncanny_dodge_is_not_owned_below_level_five() -> None:
    _runtime, resolver = _resolver()

    assert [feature.id for feature in resolver.features_for(_rogue(4))] == ["sneak_attack"]
    assert resolver.declared_capability(_rogue(4), "uncanny_dodge") is None


def test_uncanny_dodge_labels_follow_the_bundle_locale() -> None:
    _runtime, chinese = _resolver("zh-CN")

    assert chinese.catalog.label("rogue") == "游荡者"
    assert chinese.catalog.label("sneak_attack") == "偷袭"
    assert chinese.catalog.label("uncanny_dodge") == "直觉闪避"
