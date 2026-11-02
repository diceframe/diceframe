"""S-P8: Hide action, Cunning Action bonus-action routing, Fast Hands ownership.

Hide is one canonical action with one resolution (a Dexterity (Stealth) check)
regardless of whether it is paid with an action or a bonus action.  Cunning
Action is pure catalog data: three capabilities whose declared cost routes the
existing ``dash`` / ``disengage`` / ``hide`` kinds into the bonus-action pool.
Fast Hands is ownership + label only in this slice; its capabilities belong to
the 1D item pipeline.

Every state change here goes through ``resolve_intent`` -> ``apply_batch``, the
same authoritative chain the web layer uses.
"""

from __future__ import annotations

from src.engine.modules import ruleset_runtime
from src.engine.player_control import set_control
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.models import (
    ClassFeatureDefinition,
    Dnd2024ClassFeatureCatalog,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, advance, monk_instance

LOCALE = "en"
CUNNING_CAPABILITIES = ("cunning_dash", "cunning_disengage", "cunning_hide")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _rogue_sheet(runtime: Dnd2024Runtime, *, level: int = 1) -> dict:
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "lucky_scout")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": LOCALE, "name": "Rogue"},
    )
    if level > 1:
        sheet = advance(runtime, sheet, level)
    # 测试直接构造权威数据：隐匿加值由角色卡投影而来，这里固定为 +7 以便断言
    # 骰值可读（不为测试改动生产投影）。
    canonical = sheet["ruleset_character"]
    canonical["proficiencies"]["skill_values"]["stealth"] = 7
    return sheet


def _goblin(
    enemy_id: str = "goblin-1", *, wis: int = -1, hp: int = 60, position: int = 5,
) -> dict:
    return {
        "id": enemy_id, "name": enemy_id, "hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 2,
        "creature_type": "humanoid",
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": wis, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _instance(
    *, level: int = 1, enemies: list[dict] | None = None,
    spellcasting: dict | None = None, untrained_armor: bool = False,
) -> tuple[Dnd2024Runtime, Dnd2024CombatEngine, object]:
    runtime = Dnd2024Runtime()
    sheet = _rogue_sheet(runtime, level=level)
    canonical = sheet["ruleset_character"]
    if spellcasting is not None:
        canonical.setdefault("spellcasting", {})["class"] = spellcasting
    if untrained_armor:
        # S-P12 迁移债 D2：权威派生状态由真实数据写入（S-P12 测试同款构造）。
        canonical["derived"]["armor_training"] = {
            "untrained_armor_worn": True, "speed_penalty": 10,
            "stealth_disadvantage": True,
        }
    engine, instance = monk_instance(runtime, sheet)
    _start(engine, instance, enemies or [_goblin()])
    return runtime, engine, instance


def _start(engine: Dnd2024CombatEngine, instance, enemies: list[dict]) -> dict:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies,
    }, SequenceRng([20] + [1] * len(enemies)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"
    return ruleset_runtime.state(instance)["combat"]


def _set_turn(engine: Dnd2024CombatEngine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def _resolve(instance, engine: Dnd2024CombatEngine, intent: dict, rolls=()) -> dict:
    intent.setdefault("expected_version", ruleset_runtime.state(instance)["version"])
    result = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert result["ok"] is True, result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"] is True, applied
    return result


def _hide(instance, engine, rolls, *, intent_id: str = "hide-1") -> dict:
    return _resolve(instance, engine, {
        "intent_id": intent_id, "type": "hide", "submitted_by": "gm",
        "actor_id": "player:gm",
    }, rolls)


def _capability(instance, engine, capability_id: str, *, intent_id: str,
                target_id: str = "") -> dict:
    intent = {
        "intent_id": intent_id, "type": "class_capability", "submitted_by": "gm",
        "actor_id": "player:gm", "capability_id": capability_id,
    }
    if target_id:
        intent["target_id"] = target_id
    resolved = engine.resolve_intent(
        instance, {**intent, "expected_version": ruleset_runtime.state(instance)["version"]},
        SequenceRng([13]),
    )
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved


def _events(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def _check(result: dict) -> dict:
    return next(
        event for event in _events(result)
        if event["type"] == "check.resolved" and event.get("kind") == "hide"
    )


def _applied(result: dict) -> list[dict]:
    return [event for event in _events(result) if event["type"] == "condition.applied"]


def _economy(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]["economy"]


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _resolver() -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle(LOCALE))


def _feature_character(level: int, *, class_ref: str = "class:rogue") -> dict:
    return {"build": {"class_levels": [{"class_ref": class_ref, "level": level}]}}


# --------------------------------------------------------------------------
# #1 catalog declarations
# --------------------------------------------------------------------------

def test_catalog_declares_cunning_action_and_fast_hands_in_both_locales() -> None:
    runtime = Dnd2024Runtime()
    expected = {
        "en": ("Cunning Action", "Fast Hands"),
        "zh-CN": ("灵巧动作", "快手"),
    }
    for locale, (cunning, fast) in expected.items():
        catalog = Dnd2024ClassFeatureCatalog.from_bundle(runtime.load_bundle(locale))
        cunning_definition: ClassFeatureDefinition = catalog.features["cunning_action"]
        fast_definition: ClassFeatureDefinition = catalog.features["fast_hands"]

        assert catalog.labels["cunning_action"] == cunning
        assert catalog.labels["fast_hands"] == fast
        assert cunning_definition.minimum_level == 2
        assert fast_definition.minimum_level == 3
        assert [cap.id for cap in cunning_definition.capabilities] == list(
            CUNNING_CAPABILITIES
        )
        assert [cap.cost.action for cap in cunning_definition.capabilities] == [
            "bonus_action", "bonus_action", "bonus_action",
        ]
        assert [
            [action.kind for action in cap.actions]
            for cap in cunning_definition.capabilities
        ] == [["dash"], ["disengage"], ["hide"]]
        # Fast Hands 只声明所有权：capability 属于 1D 物品管线（迁移债 §8）。
        assert fast_definition.capabilities == ()


# --------------------------------------------------------------------------
# #2 / #3 / #4 hide action resolution and the DC proxy
# --------------------------------------------------------------------------

def test_hide_action_spends_the_action_and_applies_invisible() -> None:
    _runtime, engine, instance = _instance()
    result = _hide(instance, engine, [13])

    spent = next(
        event for event in _events(result) if event["type"] == "dnd2024.action.spent"
    )
    assert spent["resource"] == "action" and spent["amount"] == 1

    check = _check(result)
    assert check["ability"] == "stealth"
    assert check["rolls"] == [13]
    assert check["natural"] == 13
    assert check["modifier"] == 7
    assert check["total"] == 20
    assert check["target"] == 9  # 10 + goblin's -1 Wis save
    assert check["success"] is True

    applied = _applied(result)
    assert len(applied) == 1
    assert applied[0]["condition"] == "invisible"
    assert applied[0]["target_id"] == "player:gm"
    assert applied[0]["duration"] == "combat"
    assert applied[0]["ends_on"] == ["attack_roll", "spell_cast"]
    assert applied[0]["source_actor_id"] == "player:gm"

    assert _canonical(instance)["conditions"]["invisible"]["ends_on"] == [
        "attack_roll", "spell_cast",
    ]
    assert _economy(instance)["action"] == 0
    assert _economy(instance)["bonus_action"] == 1


def test_failed_hide_resolves_the_check_without_a_condition() -> None:
    _runtime, engine, instance = _instance(enemies=[_goblin(wis=10)])
    result = _hide(instance, engine, [1])

    check = _check(result)
    assert check["target"] == 20
    assert check["total"] == 8
    assert check["success"] is False
    assert _applied(result) == []
    assert "invisible" not in (_canonical(instance).get("conditions") or {})


def test_hide_dc_is_ten_plus_the_highest_living_enemy_wisdom() -> None:
    enemies = [_goblin("goblin-1", wis=0), _goblin("goblin-2", wis=3, position=10)]
    _runtime, engine, instance = _instance(enemies=enemies)
    result = _hide(instance, engine, [5])

    assert _check(result)["target"] == 13
    # 规则边界：没有存活敌人时 DC = 10。
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] = 0
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-2"]["hp"] = 0
    _set_turn(engine, instance, "player:gm")
    result = _hide(instance, engine, [5], intent_id="hide-2")
    assert _check(result)["target"] == 10


# --------------------------------------------------------------------------
# #5 / #6 / #7 P1 invisibility integration
# --------------------------------------------------------------------------

def test_invisible_attacker_has_advantage_and_loses_invisibility() -> None:
    _runtime, engine, instance = _instance(level=2)
    _capability(instance, engine, "cunning_hide", intent_id="cunning-hide")

    result = _resolve(instance, engine, {
        "intent_id": "attack-1", "type": "attack", "submitted_by": "gm",
        "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:shortsword",
    }, [8, 15, 4])

    check = next(
        event for event in _events(result)
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    )
    assert check["advantage_mode"] == "advantage"
    assert {"code": "invisible", "mode": "advantage"} in check["advantage_sources"]
    assert check["natural"] == 15

    types = [event["type"] for event in _events(result)]
    assert types.index("condition.removed") < types.index("dnd2024.attack.spent")
    removed = _events(result)[types.index("condition.removed")]
    assert removed["target_id"] == "player:gm" and removed["condition"] == "invisible"
    assert "invisible" not in _canonical(instance)["conditions"]


def test_invisible_caster_casting_a_spell_ends_hide_invisibility() -> None:
    _runtime, engine, instance = _instance(
        level=2,
        spellcasting={
            "ability": "int", "cantrip_refs": ["spell:fire_bolt"],
            "prepared_spell_refs": [], "slots_current": {}, "slots_max": {},
        },
    )
    _capability(instance, engine, "cunning_hide", intent_id="cunning-hide")

    result = _resolve(instance, engine, {
        "intent_id": "cast-1", "type": "cast_spell", "submitted_by": "gm",
        "actor_id": "player:gm", "spell_ref": "spell:fire_bolt",
        "slot_level": 0, "target_ids": ["enemy:goblin-1"],
    }, [15, 8, 4])

    types = [event["type"] for event in _events(result)]
    assert "condition.removed" in types
    removed = _events(result)[types.index("condition.removed")]
    assert removed["condition"] == "invisible"
    assert "invisible" not in _canonical(instance)["conditions"]


def test_enemy_attacks_the_invisible_hider_with_disadvantage() -> None:
    _runtime, engine, instance = _instance(level=2)
    _capability(instance, engine, "cunning_hide", intent_id="cunning-hide")
    _set_turn(engine, instance, "enemy:goblin-1")

    result = _resolve(instance, engine, {
        "intent_id": "enemy-attack", "type": "attack", "submitted_by": "gm",
        "actor_id": "enemy:goblin-1", "target_id": "player:gm",
        "attack_id": "scimitar",
    }, [15, 8, 4])

    check = next(
        event for event in _events(result)
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    )
    assert check["advantage_mode"] == "disadvantage"
    assert {"code": "invisible", "mode": "disadvantage"} in check["advantage_sources"]
    assert check["natural"] == 8
    # 敌人的攻击不消耗被攻击者的隐形：ends_on 只读攻击者自己的条件。
    assert "invisible" in _canonical(instance)["conditions"]


# --------------------------------------------------------------------------
# #8 / #9 / #10 bonus-action routing
# --------------------------------------------------------------------------

def test_cunning_dash_spends_the_bonus_action_and_grants_movement() -> None:
    _runtime, engine, instance = _instance(level=2)
    speed = int(_economy(instance)["speed"])
    result = _capability(instance, engine, "cunning_dash", intent_id="cunning-dash")

    events = _events(result)
    spent = next(
        event for event in events if event["type"] == "dnd2024.action.spent"
    )
    assert spent["resource"] == "bonus_action"
    granted = next(
        event for event in events if event["type"] == "dnd2024.movement.granted"
    )
    assert granted["actor_id"] == "player:gm" and granted["amount"] == speed
    assert _economy(instance)["bonus_action"] == 0
    assert _economy(instance)["action"] == 1


def test_cunning_hide_spends_the_bonus_action_and_leaves_the_action_free() -> None:
    _runtime, engine, instance = _instance(level=2)
    result = _capability(instance, engine, "cunning_hide", intent_id="cunning-hide")

    check = _check(result)
    assert check["success"] is True
    assert [event["condition"] for event in _applied(result)] == ["invisible"]
    assert _economy(instance)["bonus_action"] == 0
    assert _economy(instance)["action"] == 1


def test_a_second_bonus_action_capability_is_rejected() -> None:
    _runtime, engine, instance = _instance(level=2)
    _capability(instance, engine, "cunning_dash", intent_id="cunning-dash")

    validation = engine.validate_intent(instance, {
        "intent_id": "cunning-disengage", "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "capability_id": "cunning_disengage",
    })
    assert validation["ok"] is False
    assert "bonus action has already been spent" in validation["error"]


# --------------------------------------------------------------------------
# #11 / #12 / #15 ownership and level gates
# --------------------------------------------------------------------------

def test_cunning_action_appears_at_level_two_only() -> None:
    resolver = _resolver()
    level_one = resolver.combat_capabilities(
        _feature_character(1), {"action": 1, "bonus_action": 1},
    )
    level_two = resolver.combat_capabilities(
        _feature_character(2), {"action": 1, "bonus_action": 1},
    )

    assert resolver.has_feature(_feature_character(1), "cunning_action") is False
    assert [view.id for view in level_one] == []
    assert resolver.has_feature(_feature_character(2), "cunning_action") is True
    assert [view.id for view in level_two] == list(CUNNING_CAPABILITIES)


def test_fast_hands_is_owned_at_three_but_declares_no_capability() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    sheet = _rogue_sheet(runtime, level=3)
    canonical = sheet["ruleset_character"]

    assert resolver.has_feature(canonical, "fast_hands") is True
    assert resolver.has_feature(canonical, "cunning_action") is True
    capabilities = resolver.combat_capabilities(canonical, {"action": 1, "bonus_action": 1})
    assert [view.id for view in capabilities] == list(CUNNING_CAPABILITIES)
    assert [view.feature_id for view in capabilities] == ["cunning_action"] * 3


def test_a_fighter_never_owns_cunning_capabilities() -> None:
    resolver = _resolver()
    capabilities = resolver.combat_capabilities(
        _feature_character(18, class_ref="class:fighter"),
        {"action": 1, "bonus_action": 1, "reaction": 1},
    )

    assert [view.id for view in capabilities if view.id.startswith("cunning_")] == []


# --------------------------------------------------------------------------
# #13 / #16 / #17 intent gates
# --------------------------------------------------------------------------

def test_an_incapacitated_actor_cannot_hide() -> None:
    _runtime, engine, instance = _instance()
    _canonical(instance).setdefault("conditions", {})["paralyzed"] = {"source": "test"}

    validation = engine.validate_intent(instance, {
        "intent_id": "hide-paralyzed", "type": "hide", "submitted_by": "gm",
        "actor_id": "player:gm",
        "expected_version": ruleset_runtime.state(instance)["version"],
    })
    assert validation["ok"] is False
    assert "incapacitated" in validation["error"]


def test_hosted_choices_include_a_valid_hide_option() -> None:
    runtime, engine, instance = _instance(level=2)
    set_control(instance, "gm", "ai")

    available = engine.available_intents(instance, "gm")
    assert any(action["type"] == "hide" for action in available)

    choices = runtime.hosted_intent_choices(instance, "gm")
    hide = [choice for choice in choices if choice["type"] == "hide"]
    assert hide, choices
    assert all(engine.validate_intent(instance, choice)["ok"] for choice in hide)


def test_hide_is_rejected_once_the_action_is_spent() -> None:
    _runtime, engine, instance = _instance()
    _resolve(instance, engine, {
        "intent_id": "attack-1", "type": "attack", "submitted_by": "gm",
        "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:shortsword",
    }, [15, 4])

    validation = engine.validate_intent(instance, {
        "intent_id": "hide-spent", "type": "hide", "submitted_by": "gm",
        "actor_id": "player:gm",
        "expected_version": ruleset_runtime.state(instance)["version"],
    })
    assert validation["ok"] is False
    assert "action has already been spent" in validation["error"]


# --------------------------------------------------------------------------
# #14 duplicate hide refreshes one condition
# --------------------------------------------------------------------------

def test_hiding_again_refreshes_the_single_invisible_condition() -> None:
    _runtime, engine, instance = _instance(level=2)
    first = _hide(instance, engine, [13], intent_id="hide-1")
    second = _capability(instance, engine, "cunning_hide", intent_id="cunning-hide")

    assert [event["condition"] for event in _applied(first)] == ["invisible"]
    assert [event["condition"] for event in _applied(second)] == ["invisible"]
    conditions = _canonical(instance)["conditions"]
    assert list(conditions).count("invisible") == 1
    assert conditions["invisible"]["ends_on"] == ["attack_roll", "spell_cast"]


# --------------------------------------------------------------------------
# S-P12 migration debt D2: untrained armor's Stealth disadvantage
# --------------------------------------------------------------------------

def test_untrained_armor_rolls_hide_with_disadvantage() -> None:
    _runtime, engine, instance = _instance(untrained_armor=True)
    result = _hide(instance, engine, [18, 4])

    check = _check(result)
    assert check["advantage_mode"] == "disadvantage"
    assert {"code": "stealth_disadvantage", "mode": "disadvantage"} in (
        check["advantage_sources"]
    )
    assert check["rolls"] == [18, 4]
    assert check["natural"] == 4
    assert check["total"] == 11
    assert check["success"] is True
