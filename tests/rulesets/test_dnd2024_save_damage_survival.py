"""S0-F12: save advantage, Evasion, Fiendish Resilience, Relentless Rage.

Every assertion goes through the authoritative chain the web layer uses:
``available_intents`` / ``validate_intent`` -> ``resolve_intent`` ->
``apply_batch``.  The two exceptions are named where they appear: a same-side
spell target (validation refuses it, so the resolver is driven directly, the
S0-E2 precedent) and the ending-DC ladder, which is read back from the
canonical character the reducer wrote.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_draft,
    monk_instance,
    start_combat,
    with_equipment,
)

RUNTIME = Dnd2024Runtime()


# ---------------------------------------------------------------------------
# character construction: real presets / drafts, real advancement
# ---------------------------------------------------------------------------


def _draft(class_ref: str, name: str = "Hero") -> dict:
    """A level-1 draft whose guided choices follow the bundle for ``class_ref``."""

    draft = dict(monk_draft(RUNTIME))
    draft["class_ref"] = class_ref
    draft["name"] = name
    choices = RUNTIME.builder_choices(None, draft)
    draft["class_skill_refs"] = [
        item["ref"] for item in choices["class_skills"]
    ][: choices["class_skill_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    draft["equipment_package_ref"] = choices["equipment_packages"][0]["ref"]
    draft["background_equipment_package_ref"] = (
        choices["background_equipment_packages"][0]["ref"]
    )
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    recommended = choices.get("recommended_class_spells") or {}
    if recommended.get("cantrip_ids"):
        draft["class_spell_choices"] = {
            "cantrip_refs": [f"spell:{item}" for item in recommended["cantrip_ids"]],
            "prepared_spell_refs": [
                f"spell:{item}" for item in recommended.get("prepared_spell_ids") or []
            ],
        }
    errors = RUNTIME.validate_character(None, draft)
    assert errors == [], errors
    return draft


def _advance(
    sheet: dict, level: int, *, subclass_ref: str | None = None,
) -> dict:
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        preview = RUNTIME.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        for requirement in preview.get("requirements") or []:
            requirement_id = str(requirement.get("id") or "")
            if requirement_id == "subclass_ref" and subclass_ref:
                values = [option["value"] for option in requirement["options"]]
                assert subclass_ref in values, (subclass_ref, values)
                choices["subclass_ref"] = subclass_ref
            if requirement_id == "ability_score_increases":
                _answer_ability_increase(sheet, requirement, choices)
            if requirement.get("kind") == "spell_selection":
                _answer_spell_slot(sheet, requirement, choices)
        preview = RUNTIME.preview_advancement(None, sheet, choices)
        assert preview.get("ok") is True, preview.get("errors")
        sheet = RUNTIME.apply_advancement(None, sheet, choices)
    return sheet


def _answer_ability_increase(sheet: dict, requirement: dict, choices: dict) -> None:
    """Spend an ASI on the first legal ability that still has headroom."""

    total = int(requirement.get("total", 2))
    allowed = requirement.get("allowed", "any")
    candidates = (
        [str(item) for item in allowed] if isinstance(allowed, list)
        else ["str", "dex", "con", "wis", "int", "cha"]
    )
    scores = sheet["ruleset_character"].get("abilities") or {}
    chosen = next(
        (ability for ability in candidates if int(scores.get(ability, 10)) + total <= 20),
        None,
    )
    if chosen:
        choices["ability_score_increases"] = {chosen: total}


def _answer_spell_slot(sheet: dict, requirement: dict, choices: dict) -> None:
    """Keep the spells already known and fill the new slots from the pool."""

    build = sheet["ruleset_character"]["build"]
    previous = build.get("class_spell_choices") or {}

    def merged(existing: list, options: list, count: int) -> list:
        refs = list(existing)
        for row in options:
            if row["ref"] not in refs:
                refs.append(row["ref"])
        return refs[:count]

    choices["class_spell_choices"] = {
        "cantrip_refs": merged(
            previous.get("cantrip_refs") or [], requirement.get("cantrips") or [],
            int(requirement.get("cantrip_count", 0) or 0),
        ),
        "prepared_spell_refs": merged(
            previous.get("prepared_spell_refs") or [],
            requirement.get("leveled_spells") or [],
            int(requirement.get("prepared_spell_count", 0) or 0),
        ),
    }


def _preset_sheet(preset_id: str, level: int = 1, *, subclass_ref: str | None = None) -> dict:
    choices = RUNTIME.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    sheet = RUNTIME.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": preset_id},
    )
    return _advance(sheet, level, subclass_ref=subclass_ref) if level > 1 else sheet


def _barbarian(level: int = 11) -> dict:
    return _preset_sheet("relentless_vanguard", level)


def _rogue(level: int = 7) -> dict:
    return _preset_sheet("lucky_scout", level)


def _monk(level: int = 7) -> dict:
    sheet = RUNTIME.finalize_character(None, monk_draft(RUNTIME))
    return _advance(sheet, level) if level > 1 else sheet


def _cleric(level: int = 1) -> dict:
    return _preset_sheet("kindly_bulwark", level)


def _warlock(level: int = 10) -> dict:
    sheet = RUNTIME.finalize_character(None, _draft("class:warlock", "Fiend"))
    return _advance(sheet, level, subclass_ref="subclass:fiend_patron")


# ---------------------------------------------------------------------------
# combat plumbing
# ---------------------------------------------------------------------------


def _combat(sheet: dict, *, hp: int | None = None, position: int = 5):
    if hp is not None:
        sheet["ruleset_character"]["resources"]["hp"] = hp
    engine, instance = monk_instance(RUNTIME, sheet)
    start_combat(engine, instance, position=position)
    return engine, instance


def _duo_combat(target_sheet: dict, caster_sheet: dict, *, hp: int | None = None):
    """A party of two: the target under test plus a real caster.

    A same-side spell target has no validated intent path, so the resolver is
    driven directly; the caster still has to be a real character with a real
    spell save DC, otherwise every save would succeed against DC 0.
    """

    if hp is not None:
        target_sheet["ruleset_character"]["resources"]["hp"] = hp
    engine, instance = monk_instance(RUNTIME, target_sheet)
    instance.players["caster"] = {
        "character_name": "Caster", "character_sheet": caster_sheet,
    }
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm",
        "enemies": [{
            "id": "goblin-1", "name": "Goblin", "hp": 40, "armor_class": 12,
            "speed": 30, "position": 30, "initiative_modifier": 2,
            "creature_type": "humanoid",
            "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
            "saving_throws": {"dex": 2, "wis": -1, "con": 0},
            "attacks": [{
                "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            }],
        }],
    }, SequenceRng([20, 10, 1]))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return engine, instance


def _canonical(instance: GameInstance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _combat_state(instance: GameInstance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _apply(engine, instance, intent: dict, rng=None) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng or SequenceRng([]))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _end_turn(engine, instance, *, actor_id: str = "player:gm", rolls=()) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": f"end:{actor_id}", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id,
    }, SequenceRng(list(rolls)))


def _enemy_attack(engine, instance, rolls, *, target_id: str = "player:gm") -> list[dict]:
    combat = _combat_state(instance)
    combat["turn_index"] = combat["initiative"].index("enemy:goblin-1")
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, "enemy:goblin-1"),
    )
    events = _apply(engine, instance, {
        "intent_id": f"attack:{target_id}:{ruleset_runtime.state(instance)['version']}",
        "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "target_id": target_id,
        "attack_id": "scimitar",
    }, SequenceRng(list(rolls)))
    # 3 级起的武僧被命中时会得到拨挡攻击的反应窗口（S0-F11）。这些用例测的是
    # 豁免面，不是反应，所以在这里放弃窗口，让伤害按原来的路径落地。
    events.extend(_decline_hit_reaction(engine, instance))
    return events


def _decline_hit_reaction(engine, instance) -> list[dict]:
    pending = _combat_state(instance).get("pending_decisions") or []
    if not pending or pending[0].get("kind") != "hit_reaction_spell":
        return []
    # 只放弃**拨挡**这一条：别的受击特性（例如狂战士的 Retaliation、
    # 盗贼的 Uncanny Dodge）由各自的用例自己决定，不在这里代为处理。
    if not any(
        row.get("feature_id") == "deflect_attacks"
        for row in pending[0].get("features") or []
    ):
        return []
    version = ruleset_runtime.state(instance)["version"]
    return _apply(engine, instance, {
        "intent_id": f"decline-reaction:{version}", "type": "decision.resolve",
        "expected_version": version, "submitted_by": "gm",
        "decision_id": pending[0]["decision_id"], "option": "decline",
    }, SequenceRng([]))


def _direct_cast(
    engine, instance, target_id: str, spell_ref: str, rolls=(),
    *, actor_id: str = "player:gm", slot_level: int = 1,
) -> list[dict]:
    """Drive the spell resolver directly; only intent validation is skipped.

    Homemade effects (a condition that repeats a save, an ally-targeted spell)
    have no validated intent path today; the S0-E2 suite drives the same
    resolver the same way for the same reason.
    """

    return engine._spell_events(
        instance,
        _combat_state(instance),
        {
            "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": [target_id],
        },
        SequenceRng(list(rolls)),
    )


def _apply_events(engine, instance, events: list[dict]) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    intent = {"intent_id": f"resolve:{version}", "expected_version": version,
              "type": "cast_spell"}
    batch = {
        "batch_id": f"batch_{version}",
        "intent_id": intent["intent_id"], "intent_type": "cast_spell",
        "expected_version": version, "result_version": version + 1,
        "events": [
            {"type": "intent.submitted", "intent_type": "cast_spell",
             "actor_id": "player:gm", "submitted_by": "gm"},
            *events,
        ],
        "source_ref": "test",
    }
    applied = engine.apply_batch(instance, batch)
    assert applied["applied"] is True, applied


def _resolve_decision(engine, instance, decision: dict, option: str, rolls=()) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": (
            f"decision:{decision['decision_id']}:"
            f"{ruleset_runtime.state(instance)['version']}"
        ),
        "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": option,
    }, SequenceRng(list(rolls)))


def _pending(instance: GameInstance, kind: str) -> list[dict]:
    return [
        row for row in _combat_state(instance).get("pending_decisions") or []
        if row.get("kind") == kind
    ]


def _repeat_condition(instance: GameInstance, *, ability: str, dc: int, uid: str = "gm") -> None:
    _canonical(instance, uid).setdefault("conditions", {})["grasped"] = {
        "source": "test", "save_repeat": {"ability": ability, "dc": dc},
    }


def _enter_rage(engine, instance) -> list[dict]:
    from dnd2024_monk_common import capability_intent
    return _apply(
        engine, instance,
        capability_intent(engine, instance, "rage", intent_id="rage-1", target_id=""),
    )


# ---------------------------------------------------------------------------
# 1. declarations
# ---------------------------------------------------------------------------


def test_the_five_declarations_arrive_with_their_owners() -> None:
    resolver = Dnd2024ClassFeatureResolver(RUNTIME.load_bundle("en"))
    barbarian = _barbarian(11)["ruleset_character"]
    monk = _monk(7)["ruleset_character"]
    rogue = _rogue(7)["ruleset_character"]
    warlock = _warlock(10)["ruleset_character"]

    assert resolver.has_feature(barbarian, "danger_sense")
    assert resolver.has_feature(barbarian, "relentless_rage")
    assert resolver.has_feature(monk, "evasion")
    assert resolver.has_feature(rogue, "evasion")
    assert resolver.has_feature(
        warlock, "fiendish_resilience", owner="subclass:fiend_patron",
    )
    assert "class:monk" in {
        definition.owner for definition in resolver.features_for(monk)
        if definition.id == "evasion"
    }
    assert "class:rogue" in {
        definition.owner for definition in resolver.features_for(rogue)
        if definition.id == "evasion"
    }
    # 每条声明都指回官方页码：同名特性不能共用一条来源。
    assert {
        (definition.owner, definition.source_ref)
        for definition in (
            *resolver.features_for(barbarian),
            *resolver.features_for(monk),
            *resolver.features_for(rogue),
            *resolver.features_for(warlock),
        )
        if definition.id in {
            "danger_sense", "evasion", "relentless_rage", "fiendish_resilience",
        }
    } == {
        ("class:barbarian", "srd-5.2.1:p29:danger-sense"),
        ("class:barbarian", "srd-5.2.1:p30:relentless-rage"),
        ("class:monk", "srd-5.2.1:p51:evasion"),
        ("class:rogue", "srd-5.2.1:p63:evasion"),
        ("subclass:fiend_patron", "srd-5.2.1:p76:fiendish-resilience"),
    }


def test_a_level_below_grants_nothing() -> None:
    resolver = Dnd2024ClassFeatureResolver(RUNTIME.load_bundle("en"))

    assert not resolver.has_feature(_barbarian(1)["ruleset_character"], "danger_sense")
    assert not resolver.has_feature(_barbarian(10)["ruleset_character"], "relentless_rage")
    assert not resolver.has_feature(_monk(6)["ruleset_character"], "evasion")
    assert not resolver.has_feature(_rogue(6)["ruleset_character"], "evasion")
    assert not resolver.has_feature(
        _warlock(9)["ruleset_character"], "fiendish_resilience",
    )


# ---------------------------------------------------------------------------
# 2-4. Danger Sense
# ---------------------------------------------------------------------------


def test_danger_sense_rolls_two_dice_on_a_dexterity_save() -> None:
    engine, instance = _combat(_barbarian(2))
    _repeat_condition(instance, ability="dex", dc=15)

    events = _end_turn(engine, instance, rolls=(9, 14))
    check = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("repeat_save")
    )

    assert check["rolls"] == [9, 14]
    assert check["natural"] == 14
    assert check["advantage_mode"] == "advantage"
    assert [row["code"] for row in check["advantage_sources"]] == ["danger_sense"]
    # 两个优势来源仍然只有两骰：来源列表是证据，不是第二次重掷。
    assert check["total"] == 14 + check["modifier"] + check["check_bonus"]


def test_danger_sense_leaves_other_saves_and_attacks_alone() -> None:
    engine, instance = _combat(_barbarian(2))

    assert engine._saving_throw_sources(
        engine._actor_view(instance, _combat_state(instance), "player:gm"), "str",
    ) == []
    assert engine._saving_throw_sources(
        engine._actor_view(instance, _combat_state(instance), "player:gm"), "con",
    ) == []
    assert [
        row["code"] for row in engine._saving_throw_sources(
            engine._actor_view(instance, _combat_state(instance), "player:gm"), "dex",
        )
    ] == ["danger_sense"]

    _repeat_condition(instance, ability="wis", dc=15)
    events = _end_turn(engine, instance, rolls=(9,))
    check = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("repeat_save")
    )
    assert check["rolls"] == [9]
    assert "advantage_sources" not in check


@pytest.mark.parametrize("condition", ["paralyzed", "stunned", "unconscious"])
def test_an_incapacitated_barbarian_gets_no_danger_sense(condition: str) -> None:
    engine, instance = _combat(_barbarian(2))
    canonical = _canonical(instance)
    canonical.setdefault("conditions", {})[condition] = {"source": "test"}
    canonical["resources"]["hp"] = 20
    _repeat_condition(instance, ability="dex", dc=15)

    view = engine._actor_view(instance, _combat_state(instance), "player:gm")
    assert engine._saving_throw_sources(view, "dex") == []


def test_danger_sense_never_reaches_weapon_attacks_or_initiative() -> None:
    sheet = with_equipment(RUNTIME, _barbarian(2), "item:greatsword")
    engine, instance = _combat(sheet)

    # 先攻有自己的一套来源（init_advantage 声明），Danger Sense 不参与。
    resolver = Dnd2024ClassFeatureResolver(RUNTIME.load_bundle("en"))
    initiative_codes = {
        row["code"]
        for row in resolver.initiative_advantage_sources(sheet["ruleset_character"])
    }
    assert "danger_sense" not in initiative_codes

    events = _apply(engine, instance, {
        "intent_id": "swing-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:greatsword",
    }, SequenceRng([15, 4, 5]))
    check = next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    )

    assert check["rolls"] == [15]
    assert [row["code"] for row in check["advantage_sources"]] == []


# ---------------------------------------------------------------------------
# 5-9. Evasion
# ---------------------------------------------------------------------------


def _damage_of(events: list[dict], damage_type: str = "fire") -> dict | None:
    return next(
        (
            event for event in events
            if event["type"] == "resource.changed"
            and event.get("damage_type") == damage_type
        ),
        None,
    )


def _fire_damage(events: list[dict]) -> dict | None:
    return _damage_of(events, "fire")


@pytest.mark.parametrize("sheet_factory", [_monk, _rogue])
def test_evasion_takes_nothing_on_a_success_and_half_on_a_failure(sheet_factory) -> None:
    engine, instance = _duo_combat(sheet_factory(7), _cleric(1), hp=40)

    success = _direct_cast(
        engine, instance, "player:gm", "spell:burning_hands", (20, 4, 4, 4),
        actor_id="player:caster",
    )
    _apply_events(engine, instance, success)
    # 成功 = 0 伤害：没有伤害事件，也没有"0 点治疗"这种假事件。
    assert _fire_damage(success) is None
    assert _canonical(instance)["resources"]["hp"] == 40

    failure = _direct_cast(
        engine, instance, "player:gm", "spell:burning_hands", (1, 4, 4, 4),
        actor_id="player:caster",
    )
    damage = _fire_damage(failure)
    assert damage is not None
    # 12 点伤害失败后半伤 = 6，向下取整；Evasion 只改写一次。
    assert damage["amount"] == 6
    assert damage["delta"] == -6


def test_evasion_ignores_effects_that_do_not_allow_half_damage() -> None:
    engine, instance = _duo_combat(_monk(7), _cleric(1), hp=40)

    events = _direct_cast(
        engine, instance, "player:gm", "spell:sacred_flame", (1, 4),
        actor_id="player:caster",
    )
    damage = _damage_of(events, "radiant")

    # sacred_flame 是 Dex 豁免但成功不减伤：失败吃满 1d8。
    assert damage is not None
    assert damage["amount"] == 4


def test_evasion_ignores_other_abilities_even_when_they_allow_half() -> None:
    engine, instance = _duo_combat(_monk(7), _cleric(1), hp=40)

    # thunderwave 是 Constitution 半伤效果：Evasion 只认 Dex，不能插手。
    events = _direct_cast(
        engine, instance, "player:gm", "spell:thunderwave", (1, 4, 5),
        actor_id="player:caster",
    )
    damage = _damage_of(events, "thunder")

    assert damage is not None
    assert damage["amount"] == 9


def test_evasion_does_not_touch_weapon_attacks() -> None:
    engine, instance = _combat(_monk(7), hp=40)

    # 无豁免的攻击伤害不经过豁免分支，Evasion 无从改写。
    events = _enemy_attack(engine, instance, (19, 5))
    damage = _damage_of(events, "slashing")

    assert damage is not None
    assert damage["amount"] == 7


def test_evasion_is_lost_while_incapacitated() -> None:
    engine, instance = _duo_combat(_monk(7), _cleric(1), hp=40)
    _canonical(instance).setdefault("conditions", {})["stunned"] = {"source": "test"}

    events = _direct_cast(
        engine, instance, "player:gm", "spell:burning_hands", (4, 4, 4),
        actor_id="player:caster",
    )
    check = next(event for event in events if event["type"] == "check.resolved")
    damage = _fire_damage(events)

    # 失能让敏捷豁免自动失败（不掷 d20），Evasion 同时失效：吃满 12 点。
    assert check["auto_failed"] is True
    assert check["rolls"] == []
    assert damage is not None
    assert damage["amount"] == 12


def test_evasion_halves_before_resistance_and_only_once() -> None:
    engine, instance = _duo_combat(_rogue(7), _cleric(1), hp=40)
    _canonical(instance).setdefault("conditions", {})["fire_ward"] = {
        "source": "test", "resist_damage_types": ["fire"],
    }

    events = _direct_cast(
        engine, instance, "player:gm", "spell:burning_hands", (1, 4, 4, 4),
        actor_id="player:caster",
    )
    damage = _fire_damage(events)

    # 12 -> Evasion 半伤 6 -> 抗性 3：顺序固定，且只减半两次（不是三层）。
    assert damage["amount"] == 3
    assert damage["resisted"] is True
    assert damage["resistance"] == "fire_ward"


def test_evasion_rounds_odd_damage_down() -> None:
    engine, instance = _duo_combat(_monk(7), _cleric(1), hp=40)

    # 3d6 = 4+4+5 = 13，失败后半伤向下取整为 6，不是 7 也不是 6.5。
    events = _direct_cast(
        engine, instance, "player:gm", "spell:burning_hands", (1, 4, 4, 5),
        actor_id="player:caster",
    )
    damage = _fire_damage(events)

    assert damage["amount"] == 6


def test_a_successful_evasion_half_save_is_not_a_concentration_hit() -> None:
    sheet = _monk(7)
    sheet["ruleset_character"]["spellcasting"] = {
        "class": {
            "concentration": {"spell_ref": "spell:bless", "target_ids": []},
        },
    }
    engine, instance = _duo_combat(sheet, _cleric(1), hp=40)
    rng = _CountingRng([20, 4, 4, 4])

    events = engine._spell_events(
        instance,
        _combat_state(instance),
        {
            "actor_id": "player:caster", "spell_ref": "spell:burning_hands",
            "slot_level": 1, "target_ids": ["player:gm"],
        },
        rng,
    )

    # 一次 Dex 豁免 = 一个 d20；伤害被 Evasion 归零后不再掷专注检定。
    assert rng.d20 == 1
    assert [event for event in events if event.get("kind") == "concentration"] == []
    assert _fire_damage(events) is None


# ---------------------------------------------------------------------------
# 10-13. Fiendish Resilience
# ---------------------------------------------------------------------------


def test_a_fiend_rechooses_its_resistance_on_a_short_rest() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine

    rested = Dnd2024RestEngine(RUNTIME.load_bundle("en")).complete_short_rest(
        _warlock(10), fiendish_resilience="fire",
    )
    character = rested["character"]

    assert character["features"]["fiendish_resilience"]["damage_type"] == "fire"
    rebind = next(
        event for event in rested["events"]
        if event["type"] == "rebind_fiendish_resilience"
    )
    assert (rebind["before"], rebind["after"]) == (None, "fire")


def test_a_long_rest_can_rechoose_and_force_is_refused() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine, RestError

    engine = Dnd2024RestEngine(RUNTIME.load_bundle("en"))
    sheet = _warlock(10)
    sheet["ruleset_character"].setdefault("features", {})["fiendish_resilience"] = {
        "damage_type": "cold",
    }

    rested = engine.complete_long_rest(sheet, fiendish_resilience="acid")
    assert rested["character"]["features"]["fiendish_resilience"]["damage_type"] == "acid"

    with pytest.raises(RestError):
        engine.complete_long_rest(sheet, fiendish_resilience="force")


@pytest.mark.parametrize("bad", ["radiantish", 7, True, ["fire"], {"type": "fire"}])
def test_an_invalid_choice_is_refused_without_touching_state(bad) -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine, RestError

    engine = Dnd2024RestEngine(RUNTIME.load_bundle("en"))
    sheet = _warlock(10)
    before = deepcopy(sheet["ruleset_character"])

    with pytest.raises(RestError):
        engine.complete_short_rest(sheet, fiendish_resilience=bad)

    assert sheet["ruleset_character"] == before


def test_a_rest_without_a_choice_keeps_the_old_resistance() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine

    engine = Dnd2024RestEngine(RUNTIME.load_bundle("en"))
    sheet = _warlock(10)
    sheet["ruleset_character"].setdefault("features", {})["fiendish_resilience"] = {
        "damage_type": "cold",
    }

    rested = engine.complete_short_rest(sheet)

    assert rested["character"]["features"]["fiendish_resilience"]["damage_type"] == "cold"
    assert not [
        event for event in rested["events"]
        if event["type"] == "rebind_fiendish_resilience"
    ]


def test_a_first_rest_without_a_choice_does_not_default_a_type() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine

    rested = Dnd2024RestEngine(RUNTIME.load_bundle("en")).complete_short_rest(
        _warlock(10),
    )

    assert "damage_type" not in (
        rested["character"].get("features", {}).get("fiendish_resilience") or {}
    )


def test_only_the_chosen_type_is_halved() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine

    rested = Dnd2024RestEngine(RUNTIME.load_bundle("en")).complete_short_rest(
        _warlock(10), fiendish_resilience="fire",
    )
    sheet = {**_warlock(10), "ruleset_character": rested["character"]}
    engine, instance = _combat(sheet, hp=40)

    events = _direct_cast(
        engine, instance, "player:gm", "spell:burning_hands", (1, 4, 4, 4),
    )
    damage = _fire_damage(events)
    assert damage["amount"] == 6
    assert damage["resistance"] == "fiendish_resilience"

    # 另一个伤害类型不受影响：piercing 走同一抗性函数，但不在选择里。
    view = engine._actor_view(instance, _combat_state(instance), "player:gm")
    assert engine._resist_damage(view, 10, "piercing") == (10, "")
    assert engine._resist_damage(view, 10, "force") == (10, "")


def test_a_fiend_choice_survives_a_save_and_load() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine

    rested = Dnd2024RestEngine(RUNTIME.load_bundle("en")).complete_short_rest(
        _warlock(10), fiendish_resilience="acid",
    )
    # 存档只带 canonical 角色：经过一次真正的 JSON 往返，而不是同一个对象。
    saved = json.loads(json.dumps(rested["character"]))
    sheet = {**_warlock(10), "ruleset_character": saved}
    engine, instance = _combat(sheet, hp=40)

    view = engine._actor_view(instance, _combat_state(instance), "player:gm")
    assert engine._resist_damage(view, 10, "acid") == (5, "fiendish_resilience")
    assert engine._resist_damage(view, 10, "fire") == (10, "")


# ---------------------------------------------------------------------------
# 14-21. Relentless Rage
# ---------------------------------------------------------------------------


def _zeroing_hit(engine, instance, *, rolls=(19, 6)) -> list[dict]:
    """One enemy hit that would take a low-HP barbarian to 0.

    A Berserker 10 owns Retaliation, so the hit first offers the reaction
    window; declining it submits the same held damage, which is what the
    survival boundary then sees.
    """

    events = _enemy_attack(engine, instance, rolls)
    reaction = _pending(instance, "hit_reaction_spell")
    if reaction:
        _resolve_decision(engine, instance, reaction[0], "decline")
    return events


def _rage_ready(level: int = 11, hp: int = 3):
    sheet = _barbarian(level)
    engine, instance = _combat(sheet, hp=hp)
    _enter_rage(engine, instance)
    return engine, instance


def test_a_zeroing_hit_opens_the_survival_window_without_changing_hp() -> None:
    engine, instance = _rage_ready()

    events = _zeroing_hit(engine, instance)
    canonical = _canonical(instance)

    assert not [
        event for event in events
        if event["type"] == "resource.changed" and event.get("target_id") == "player:gm"
    ]
    # 伤害被挂起：HP、狂暴与昏迷都还没发生。
    assert canonical["resources"]["hp"] == 3
    assert "rage" in canonical["conditions"]
    assert "unconscious" not in canonical["conditions"]
    decision = _pending(instance, "relentless_rage")[0]
    assert decision["options"] == ["use", "decline"]
    assert decision["dc"] == 10
    assert decision["hp_amount"] == 22


def test_a_successful_save_replaces_the_drop_with_twice_the_barbarian_level() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]

    events = _resolve_decision(engine, instance, decision, "use", (15,))
    canonical = _canonical(instance)
    check = next(event for event in events if event["type"] == "check.resolved")

    assert check["kind"] == "saving_throw"
    assert check["ability"] == "con"
    assert check["target"] == 10
    assert check["success"] is True
    assert canonical["resources"]["hp"] == 22
    assert "rage" in canonical["conditions"]
    assert "unconscious" not in canonical["conditions"]
    assert canonical["features"]["relentless_rage"]["next_dc"] == 15
    # 这次恢复不是一次治疗触发：没有治疗事件，也没有受伤后再反应。
    assert not [
        event for event in events
        if event["type"] == "resource.changed" and event.get("healing") and event.get("amount")
    ]
    # 成功把这次归零整个替换掉了，所以不会补开"受到伤害"的反应窗口。
    assert _pending(instance, "hit_reaction_spell") == []

    # 普通回合边界不动这条特性状态：只有休息才会重置阶梯。
    combat = _combat_state(instance)
    combat["turn_index"] = combat["initiative"].index("player:gm")
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, "player:gm"),
    )
    _end_turn(engine, instance)
    assert canonical["features"]["relentless_rage"]["next_dc"] == 15


def test_a_failed_save_falls_back_to_zero_hp_and_still_raises_the_dc() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]

    events = _resolve_decision(engine, instance, decision, "use", (1,))
    canonical = _canonical(instance)
    check = next(event for event in events if event["type"] == "check.resolved")

    assert check["success"] is False
    assert canonical["resources"]["hp"] == 0
    assert "unconscious" in canonical["conditions"]
    assert "rage" not in canonical["conditions"]
    # 失败也是一次实际使用。
    assert canonical["features"]["relentless_rage"]["next_dc"] == 15


def test_declining_is_not_a_use_and_leaves_the_dc_alone() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]

    _resolve_decision(engine, instance, decision, "decline")
    canonical = _canonical(instance)

    assert canonical["resources"]["hp"] == 0
    assert "unconscious" in canonical["conditions"]
    assert canonical["features"].get("relentless_rage", {}).get("next_dc") in (None, 10)


def test_temp_hp_that_absorbs_the_hit_never_opens_the_window() -> None:
    sheet = _barbarian(11)
    sheet["ruleset_character"]["resources"]["temp_hp"] = 10
    engine, instance = _combat(sheet, hp=3)
    _enter_rage(engine, instance)

    _zeroing_hit(engine, instance)

    canonical = _canonical(instance)
    assert _pending(instance, "relentless_rage") == []
    assert canonical["resources"]["hp"] == 3
    # 临时 HP 被消耗了一部分，但余额仍扛住了这一击。
    assert 0 < canonical["resources"]["temp_hp"] < 10


def test_the_dc_ladder_is_ten_then_fifteen_then_twenty() -> None:
    engine, instance = _rage_ready()

    for expected_dc in (10, 15, 20):
        canonical = _canonical(instance)
        canonical["resources"]["hp"] = 3
        canonical["conditions"].pop("unconscious", None)
        canonical["conditions"]["rage"] = {"duration": "rage", "turns_remaining": 10}
        _zeroing_hit(engine, instance)
        decision = _pending(instance, "relentless_rage")[0]
        assert decision["dc"] == expected_dc
        _resolve_decision(engine, instance, decision, "use", (1,))


def test_a_rest_resets_the_ladder_and_combat_end_does_not() -> None:
    from src.rulesets.dnd2024.resting import Dnd2024RestEngine

    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]
    _resolve_decision(engine, instance, decision, "use", (1,))

    canonical = _canonical(instance)
    assert canonical["features"]["relentless_rage"]["next_dc"] == 15

    # 战斗结束会清掉狂暴与回合级条件，但不该重置这条特性状态。
    _apply(engine, instance, {
        "intent_id": "wrap-up", "type": "combat.end",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
    })
    assert canonical["features"]["relentless_rage"]["next_dc"] == 15

    # 升级同样不重置：DC 阶梯只由"用过的次数"和休息决定。
    canonical["resources"]["hp"] = 10
    canonical["conditions"].pop("unconscious", None)
    advanced = _advance(instance.get_character_sheet("gm"), 12)
    assert (
        advanced["ruleset_character"]["features"]["relentless_rage"]["next_dc"] == 15
    )

    rested = Dnd2024RestEngine(RUNTIME.load_bundle("en")).complete_long_rest(advanced)
    assert rested["character"]["features"]["relentless_rage"]["next_dc"] == 10


def test_relentless_rage_requires_an_active_rage() -> None:
    sheet = _barbarian(11)
    engine, instance = _combat(sheet, hp=3)

    _zeroing_hit(engine, instance)

    assert _pending(instance, "relentless_rage") == []
    assert _canonical(instance)["resources"]["hp"] == 0


def test_a_non_zeroing_hit_never_opens_the_window() -> None:
    engine, instance = _rage_ready()

    _enemy_attack(engine, instance, (19, 1))

    assert _pending(instance, "relentless_rage") == []
    assert _canonical(instance)["resources"]["hp"] > 0


def test_the_window_blocks_other_intents_until_it_is_resolved() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]

    blocked = engine.validate_intent(instance, {
        "intent_id": "end-while-pending", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    })
    assert blocked["ok"] is False
    assert "relentless" in blocked["error"]

    _resolve_decision(engine, instance, decision, "decline")
    assert _pending(instance, "relentless_rage") == []


def test_client_cannot_supply_the_total_dc_level_or_hp() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]

    events = _apply(engine, instance, {
        "intent_id": "forged-decision", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "use",
        # 客户端能声明的只有"用不用"，其余一律按服务端事实结算。
        "total": 30, "dc": 1, "level": 20, "hp": 99,
    }, SequenceRng([1]))
    canonical = _canonical(instance)
    check = next(event for event in events if event["type"] == "check.resolved")

    assert check["target"] == 10
    assert check["total"] == 1 + check["modifier"] + check["check_bonus"]
    assert check["success"] is False
    assert canonical["resources"]["hp"] == 0
    assert canonical["features"]["relentless_rage"]["next_dc"] == 15


class _CountingRng(SequenceRng):
    def __init__(self, values: list[int]) -> None:
        super().__init__(values)
        self.calls = 0
        self.d20 = 0

    def randint(self, minimum: int, maximum: int) -> int:
        self.calls += 1
        if (minimum, maximum) == (1, 20):
            self.d20 += 1
        return super().randint(minimum, maximum)


def test_a_successful_survival_rolls_exactly_one_die() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]
    rng = _CountingRng([15])

    _apply(engine, instance, {
        "intent_id": "one-die",
        "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "use",
    }, rng)

    # 一次 Con 豁免 = 一个 d20；成功恢复既不补骰，也不重开受伤反应。
    assert rng.calls == 1
    assert _canonical(instance)["resources"]["hp"] == 22


def test_replaying_the_survival_batch_does_not_raise_the_dc_twice() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]

    resolved = engine.resolve_intent(instance, {
        "intent_id": "replay-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "use",
    }, SequenceRng([15]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    again = engine.apply_batch(instance, resolved["event_batch"])

    canonical = _canonical(instance)
    assert again["duplicate"] is True
    # 重放不再掷骰、不再改 HP、不再把 DC 从 15 推到 20。
    assert canonical["resources"]["hp"] == 22
    assert canonical["features"]["relentless_rage"]["next_dc"] == 15


def test_the_ladder_survives_a_save_and_load() -> None:
    engine, instance = _rage_ready()
    _zeroing_hit(engine, instance)
    decision = _pending(instance, "relentless_rage")[0]
    _resolve_decision(engine, instance, decision, "use", (1,))
    assert _canonical(instance)["features"]["relentless_rage"]["next_dc"] == 15

    # 存档只带 canonical 角色；读档后的战斗必须读回同一个 DC。
    saved = deepcopy(instance.get_character_sheet("gm"))
    saved["ruleset_character"]["resources"]["hp"] = 3
    saved["ruleset_character"]["conditions"].pop("unconscious", None)
    reloaded_engine, reloaded = _combat(saved, hp=3)
    _enter_rage(reloaded_engine, reloaded)
    _zeroing_hit(reloaded_engine, reloaded)

    decision = _pending(reloaded, "relentless_rage")[0]
    assert decision["dc"] == 15
