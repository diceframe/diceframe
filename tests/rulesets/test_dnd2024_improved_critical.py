"""Improved Critical / Superior Critical (Champion): ownership + crit threshold.

Owner-scoped feature identity and choice-aware subclass grants are tested
together with the ``crit_threshold`` parameter consumed by weapon and Unarmed
Strike attacks.  Every combat assertion goes through ``resolve_intent`` ->
``apply_batch`` like the web layer does.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_instance,
    with_equipment,
)


def _fighter_sheet(
    runtime: Dnd2024Runtime, level: int = 1, *, fighting_style: str = "feat:archery",
) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    draft = {**preset["draft"], "locale": "en", "name": "Fighter"}
    # 1 级 Fighting Style 是 7 级 Additional Fighting Style 的父级选择。默认
    # 取 Archery 而不是预设的 Defense：已经拥有 Defense 的冠军在 7 级既不能
    # 重复选它、其余三项的效果又尚未实现，那条路径上无法构造任何合法高等级
    # 角色。需要"已拥有 Defense"的场景由测试显式传 defense。
    draft["class_feature_feat_choices"] = {"fighting_style": fighting_style}
    sheet = runtime.finalize_character(None, draft)
    # 高等级必须走真实升级链；默认 helper 总把 ASI 加在 STR 上会撞 20 上限，
    # 因此这里按"仍有空间"的确定性规则分配。
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        asi = next(
            (
                requirement for requirement in preview.get("requirements") or []
                if requirement["id"] == "ability_score_increases"
            ),
            None,
        )
        if asi is not None:
            total = int(asi.get("total", 2))
            abilities = sheet["ruleset_character"]["abilities"]
            allowed = asi.get("allowed", "any")
            pool = list(abilities) if allowed == "any" else list(allowed)
            ability = next(
                key for key in pool if abilities[key] + total <= 20
            )
            picks["ability_score_increases"] = {ability: total}
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _direct_fighter(
    level: int, *, subclass_ref: object = None, include_features: bool = True,
) -> dict:
    character: dict = {
        "build": {"class_levels": [{"class_ref": "class:fighter", "level": level}]},
    }
    if include_features:
        features = {} if subclass_ref is None else {"subclass_ref": subclass_ref}
        character["features"] = features
    return character


def _bundle_with(runtime: Dnd2024Runtime, mutate) -> object:
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    mutate(entities["class_feature_catalog"]["srd_class_features"])
    return replace(bundle, entities=entities)


def _improved_critical(raw: dict) -> dict:
    return next(
        feature for feature in raw["classes"]["fighter"]["features"]
        if feature["id"] == "improved_critical"
    )


# --- 1. catalog identity and labels --------------------------------------


def test_champion_declarations_have_owner_keys_and_both_locales() -> None:
    runtime = Dnd2024Runtime()
    expected_labels = {"en": "Improved Critical", "zh-CN": "精通重击"}
    for locale, label in expected_labels.items():
        catalog = Dnd2024ClassFeatureResolver(
            runtime.load_bundle(locale)
        ).catalog
        improved = catalog.features_by_key[("subclass:champion", "improved_critical")]
        superior = catalog.features_by_key[("subclass:champion", "superior_critical")]

        assert improved.owner == "subclass:champion"
        assert superior.owner == "subclass:champion"
        assert (improved.id, improved.minimum_level) == ("improved_critical", 3)
        assert (superior.id, superior.minimum_level) == ("superior_critical", 15)
        assert catalog.label("improved_critical") == label
    assert Dnd2024ClassFeatureResolver(
        runtime.load_bundle("zh-CN")
    ).catalog.label("superior_critical") == "高效重击"


# --- 2. same local id in two owners ---------------------------------------


def test_same_local_id_can_coexist_under_two_owners() -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        raw["classes"]["rogue"]["features"].append({
            "id": "weapon_mastery",
            "minimum_level": 1,
            "source_ref": "fixture:rogue-weapon-mastery",
            "summary": "Rogue variant used to prove owner-scoped identity.",
            "parameters": {"fixture_owner": "rogue"},
        })

    resolver = Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))
    catalog = resolver.catalog
    fighter = catalog.features_by_key[("class:fighter", "weapon_mastery")]
    rogue = catalog.features_by_key[("class:rogue", "weapon_mastery")]

    assert fighter.parameters != rogue.parameters
    assert len(catalog.features.definitions_for("weapon_mastery")) == 2
    assert resolver.feature_value(
        _direct_fighter(1), "weapon_mastery", "fixture_owner",
    ) is None
    assert resolver.feature_value(
        {
            "build": {"class_levels": [{"class_ref": "class:rogue", "level": 1}]},
            "features": {},
        },
        "weapon_mastery",
        "fixture_owner",
    ) == "rogue"


# --- 3. invalid ownership declarations ------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", "subclass:evoker"),
        ("owner", "champion"),
        ("id", "phantom_critical"),
    ],
)
def test_invalid_or_ungranted_owner_declarations_fail_closed(field: str, value: str) -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _improved_critical(raw)[field] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


# --- 4. declaration level must match the first grant ----------------------


def test_catalog_rejects_a_minimum_level_that_drifts_from_the_grant() -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        # 子职表在 3 级授予 improved_critical；声明成 7 级必须 fail closed。
        _improved_critical(raw)["minimum_level"] = 7

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


# --- 5. crit_threshold range and type --------------------------------------


@pytest.mark.parametrize("value", [1, 20, "19", True])
def test_catalog_rejects_an_invalid_crit_threshold(value) -> None:
    runtime = Dnd2024Runtime()

    def mutate(raw: dict) -> None:
        _improved_critical(raw)["parameters"]["crit_threshold"] = value

    with pytest.raises(ClassFeatureError):
        Dnd2024ClassFeatureResolver(_bundle_with(runtime, mutate))


# --- 6. Monk options keep the same owner ----------------------------------


def test_monk_focus_options_still_require_and_share_the_parent_owner() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    character = {
        "build": {"class_levels": [{"class_ref": "class:monk", "level": 2}]},
        "features": {},
        "resources": {
            "class": {"focus_points": {"current": 2, "maximum": 2}},
        },
    }

    owned = resolver.features_for(character)

    assert [feature.id for feature in owned] == [
        "martial_arts", "monks_focus", "flurry_of_blows", "patient_defense",
        "step_of_the_wind",
    ]
    assert {feature.owner for feature in owned} == {"class:monk"}
    assert resolver.has_feature(
        character, "flurry_of_blows", owner="class:monk",
    ) is True


# --- 7. level gates --------------------------------------------------------


def test_champion_features_follow_level_and_owner() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    champion = {"subclass_ref": "subclass:champion"}

    assert resolver.has_feature(_direct_fighter(2, **champion), "improved_critical") is False
    assert resolver.has_feature(_direct_fighter(3, **champion), "improved_critical") is True
    assert resolver.has_feature(_direct_fighter(14, **champion), "superior_critical") is False
    assert resolver.has_feature(_direct_fighter(15, **champion), "superior_critical") is True


# --- 8. advancement persists the real subclass choice --------------------


def test_advancement_to_three_exposes_improved_critical_with_its_owner() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    character = _fighter_sheet(runtime, 3)["ruleset_character"]

    assert character["features"]["subclass_ref"] == "subclass:champion"
    assert resolver.has_feature(
        character, "improved_critical", owner="subclass:champion",
    ) is True
    view = next(
        item for item in resolver.feature_views(character)
        if item.id == "improved_critical"
    )
    assert view.owner == "subclass:champion"
    assert view.to_dict()["owner"] == "subclass:champion"


# --- 9. missing subclass state never falls back to Champion --------------


def test_level_three_fighter_without_subclass_ref_has_no_champion_feature() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    assert resolver.has_feature(_direct_fighter(3), "improved_critical") is False


# --- 10. empty, foreign, and wrongly typed refs ---------------------------


@pytest.mark.parametrize("subclass_ref", ["subclass:evoker", "", ["subclass:champion"]])
def test_invalid_subclass_ref_grants_no_champion_feature(subclass_ref) -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = _direct_fighter(3, subclass_ref=subclass_ref)

    assert resolver.has_feature(character, "improved_critical") is False


# --- 11. forged refs on other classes -------------------------------------


@pytest.mark.parametrize("class_ref", ["class:rogue", "class:wizard"])
def test_other_classes_cannot_forge_a_champion_subclass(class_ref: str) -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    character = {
        "build": {"class_levels": [{"class_ref": class_ref, "level": 3}]},
        "features": {"subclass_ref": "subclass:champion"},
    }

    assert resolver.has_feature(character, "improved_critical") is False
    assert resolver.has_feature(character, "superior_critical") is False


# --- 12. two thresholds resolve to the most generous one ------------------


def test_level_fifteen_uses_eighteen_not_seventeen() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    character = _direct_fighter(15, subclass_ref="subclass:champion")

    assert resolver.has_feature(character, "improved_critical") is True
    assert resolver.has_feature(character, "superior_critical") is True
    assert resolver.min_feature_value(character, "crit_threshold", 20) == 18


# --- 13. existing class resources and capabilities stay intact -----------


def test_existing_class_resources_and_capabilities_remain_intact() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    monk = {
        "build": {"class_levels": [{"class_ref": "class:monk", "level": 2}]},
        "features": {},
        "resources": {
            "class": {"focus_points": {"current": 2, "maximum": 2}},
        },
    }

    assert resolver.class_resource_definition(monk, "focus_points").maximum == 2
    assert {
        item.id for item in resolver.combat_capabilities(
            monk, {"action": 1, "bonus_action": 1},
        )
    } >= {"bonus_unarmed_strike", "flurry_of_blows"}
    assert [
        feature.id for feature in resolver.features_for(_direct_fighter(1))
    ] == ["second_wind", "weapon_mastery"]


# --- combat helpers ------------------------------------------------------


def _combat(
    runtime: Dnd2024Runtime, sheet: dict, *, enemy_first: bool = False,
) -> tuple[object, object]:
    engine, instance = monk_instance(runtime, sheet)
    rolls = [1, 20] if enemy_first else [20, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [{
            "id": "goblin-1", "name": "Goblin", "hp": 40, "armor_class": 12,
            "speed": 30, "position": 5, "initiative_modifier": 2,
            "abilities": {
                "str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8,
            },
            "saving_throws": {"dex": 2, "wis": -1, "con": 0},
            "attacks": [{
                "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            }],
        }],
    }, SequenceRng(rolls))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return engine, instance


def _attack(engine, instance, weapon_ref: str, rolls: list[int]) -> list[dict]:
    intent = {
        "intent_id": f"attack:{weapon_ref}", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": weapon_ref,
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _cast(engine, instance, rolls: list[int]) -> list[dict]:
    intent = {
        "intent_id": "cast:fire-bolt", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:fire_bolt", "target_ids": ["enemy:goblin-1"],
        "slot_level": 0,
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _check(events: list[dict], kind: str = "attack") -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def _damage(events: list[dict], target_id: str = "enemy:goblin-1") -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed"
        and event.get("resource") == "hp"
        and event.get("target_id") == target_id
    )


def _enemy_state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]


def _paralyze(instance) -> None:
    _enemy_state(instance).setdefault("conditions", {})["paralyzed"] = {"source": "test"}


# --- 14 / 15. weapon threshold -------------------------------------------


def test_level_three_fighter_crits_on_nineteen_with_a_weapon() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [19, 2, 3])

    check = _check(events)
    assert check["natural"] == 19
    assert check["critical"] is True
    assert check["success"] is True
    assert check["crit_threshold"] == 19
    damage = _damage(events)
    assert damage["critical"] is True
    assert len(damage["rolls"]) == 2  # 1d4 doubled


@pytest.mark.parametrize("natural", [18, 19])
def test_level_fifteen_threshold_is_eighteen_not_seventeen(natural: int) -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 15), "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [natural, 2, 3])

    check = _check(events)
    assert check["natural"] == natural
    assert check["critical"] is True
    assert check["crit_threshold"] == 18
    assert len(_damage(events)["rolls"]) == 2


# --- 16. absent class/subclass selection keeps threshold 20 ---------------


@pytest.mark.parametrize("level", [2, 3])
def test_nineteen_does_not_crit_without_champion(level: int) -> None:
    runtime = Dnd2024Runtime()
    sheet = _fighter_sheet(runtime, level)
    if level == 3:
        sheet["ruleset_character"]["features"].pop("subclass_ref", None)
    sheet = with_equipment(runtime, sheet, "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [19, 2])

    check = _check(events)
    assert check["critical"] is False
    assert check["success"] is True
    assert "crit_threshold" not in check
    damage = _damage(events)
    assert damage["critical"] is False
    assert len(damage["rolls"]) == 1


# --- 17. natural 1 and natural 20 ----------------------------------------


@pytest.mark.parametrize(
    "natural,success,critical",
    [(1, False, False), (20, True, True)],
)
def test_natural_one_and_twenty_keep_their_rule(
    natural: int, success: bool, critical: bool,
) -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "item:dagger", [natural, 2, 3])

    check = _check(events)
    assert check["success"] is success
    assert check["critical"] is critical
    if natural == 1:
        assert not [
            event for event in events
            if event["type"] == "resource.changed" and event.get("resource") == "hp"
        ]
    else:
        assert len(_damage(events)["rolls"]) == 2


# --- 18. Unarmed Strike shares the consumption point ----------------------


def test_level_three_unarmed_strike_crits_on_nineteen() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3))
    engine, instance = _combat(runtime, sheet)

    events = _attack(engine, instance, "unarmed_strike", [19])

    check = _check(events)
    assert check["natural"] == 19
    assert check["critical"] is True
    assert check["success"] is True
    assert check["crit_threshold"] == 19
    assert _damage(events)["critical"] is True


# --- 19. opportunity attacks share the consumption point -----------------


@pytest.mark.parametrize("natural", [18, 19])
def test_level_fifteen_opportunity_attack_uses_the_same_threshold(natural: int) -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 15), "item:dagger")
    engine, instance = _combat(runtime, sheet, enemy_first=True)
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "enemy:goblin-1"

    moved = engine.resolve_intent(instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": -15,
    }, SequenceRng([]))
    assert moved["ok"] is True, moved
    assert engine.apply_batch(instance, moved["event_batch"])["applied"] is True
    decision = moved["pending_decision"]
    assert decision["kind"] == "opportunity_attack"

    decided = engine.resolve_intent(instance, {
        "intent_id": "decision-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "resolve",
    }, SequenceRng([natural, 2, 3]))
    assert decided["ok"] is True, decided
    assert engine.apply_batch(instance, decided["event_batch"])["applied"] is True

    check = _check(decided["event_batch"]["events"])
    assert check["natural"] == natural
    assert check["critical"] is True
    assert check["crit_threshold"] == 18


# --- 20. a critical hit always hits --------------------------------------


def test_nineteen_crits_against_a_target_above_the_total() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)
    _enemy_state(instance)["armor_class"] = 30

    events = _attack(engine, instance, "item:dagger", [19, 2, 3])

    check = _check(events)
    assert check["total"] < check["target"]
    assert check["success"] is True
    assert check["critical"] is True


# --- 21. the existing crit_on_hit_within_5 path is unchanged --------------


def test_paralyzed_target_still_upgrades_a_hit_but_not_a_natural_one() -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    engine, instance = _combat(runtime, sheet)
    _paralyze(instance)

    events = _attack(engine, instance, "item:dagger", [14, 2, 3])

    check = _check(events)
    assert check["natural"] == 14
    assert check["success"] is True
    assert check["critical"] is True
    assert len(_damage(events)["rolls"]) == 2

    engine, instance = _combat(runtime, with_equipment(
        runtime, _fighter_sheet(runtime, 3), "item:dagger",
    ))
    _paralyze(instance)
    events = _attack(engine, instance, "item:dagger", [1])
    check = _check(events)
    assert check["success"] is False
    assert check["critical"] is False


# --- 22. spell attacks stay on natural 20 --------------------------------


@pytest.mark.parametrize("natural,critical", [(19, False), (20, True)])
def test_spell_attack_ignores_the_weapon_crit_threshold(
    natural: int, critical: bool,
) -> None:
    runtime = Dnd2024Runtime()
    sheet = with_equipment(runtime, _fighter_sheet(runtime, 3), "item:dagger")
    character = sheet["ruleset_character"]
    character.setdefault("spellcasting", {})["class"] = {
        "ability": "int", "slots_current": {}, "concentration": None,
        "prepared_spell_refs": [], "cantrip_refs": ["spell:fire_bolt"],
    }
    character["derived"]["spell_attack_bonus"] = 5
    engine, instance = _combat(runtime, sheet)

    events = _cast(engine, instance, [natural, 2, 3])

    check = _check(events, "spell_attack")
    assert check["natural"] == natural
    assert check["critical"] is critical
    assert "crit_threshold" not in check


# --- 23. the existing critical-damage pipeline still doubles dice --------


def test_critical_hit_still_doubles_sneak_attack_dice() -> None:
    from test_dnd2024_sneak_attack import (  # local import keeps this regression focused
        _attack as sneak_attack,
        _damage_to,
        _grant_advantage,
        _instance,
    )

    _runtime, engine, instance = _instance(level=1, equipment=["item:dagger"])
    _grant_advantage(instance, "enemy:goblin-1")

    events = sneak_attack(engine, instance, rolls=[20, 1, 2, 3, 4, 5])

    check = _check(events)
    damage = _damage_to(events, "enemy:goblin-1")
    assert check["critical"] is True
    assert damage["critical"] is True
    # 2d4 weapon (critical) + 2d6 sneak (critical) = four damage dice.
    assert len(damage["rolls"]) == 4
