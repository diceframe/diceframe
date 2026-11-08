"""Dark One's Blessing: defeat-triggered temporary hit points.

The trigger is declared through one closed parameter set, so the engine never
names a feature, class or subclass.  Every scenario drives the same
authoritative chain the web layer uses: ``resolve_intent`` -> ``apply_batch``.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.models import ClassFeatureError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
    with_equipment,
)


PREPARED = [
    "spell:hex", "spell:charm_person", "spell:hellish_rebuke", "spell:bane",
    "spell:expeditious_retreat",
]
CANTRIPS = ["spell:eldritch_blast", "spell:prestidigitation", "spell:chill_touch"]
WARLOCK = "class:warlock"
FEATURE_ID = "dark_ones_blessing"
PAYLOAD_KEYS = {"trigger", "temp_hp_ability", "temp_hp_class_ref", "temp_hp_minimum"}


def spell_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        WARLOCK, level,
    )
    return {
        "cantrip_refs": CANTRIPS[: int(requirements["cantrip_count"])],
        "prepared_spell_refs": PREPARED[: int(requirements["prepared_spell_count"])],
        "spellbook_refs": [],
    }


def warlock_sheet(runtime: Dnd2024Runtime, *, level: int = 3) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref=WARLOCK,
        class_skill_refs=["skill:arcana", "skill:deception"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:warlock_a",
        base_abilities={
            "str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15,
        },
        name="Warlock",
        class_spell_choices=spell_choices(runtime, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        choices["class_spell_choices"] = spell_choices(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def rogue_sheet(runtime: Dnd2024Runtime) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "lucky_scout")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Ally"},
    )
    return with_equipment(runtime, sheet, "item:dagger")


def enemy(enemy_id: str = "goblin-1", *, hp: int = 1, position: int = 5) -> dict:
    profile = goblin(position=position, hp=hp)
    profile["id"] = enemy_id
    return profile


def warlock_instance(*, level: int = 3):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, warlock_sheet(runtime, level=level))
    return runtime, engine, instance


def party_instance(*, positions: dict | None = None, enemy_position: int = 5):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, warlock_sheet(runtime, level=3))
    instance.players["ally"] = {
        "character_name": "Ally", "character_sheet": rogue_sheet(runtime),
    }
    # sorted(players) is ["ally", "gm"]: low roll for the ally, high for the GM.
    start(engine, instance, [enemy(position=enemy_position)], rolls=[1, 20, 1],
          positions=positions)
    return runtime, engine, instance


def start(engine, instance, enemies: list[dict], *, rolls=None, positions=None):
    rolls = list(rolls or [20] + [1] * len(enemies))
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies,
        **({"player_positions": positions} if positions else {}),
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["initiative"][0] == "player:gm"
    return combat


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def submitter(actor_id: str) -> str:
    return actor_id.removeprefix("player:") if actor_id.startswith("player:") else "gm"


def attack(
    engine, instance, *, actor_id: str = "player:gm", target_id: str = "enemy:goblin-1",
    weapon: str = "item:dagger", rolls=(10, 4, 1), intent_id: str = "attack-1",
) -> dict:
    resolved = engine.resolve_intent(instance, {
        "intent_id": intent_id, "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": submitter(actor_id), "actor_id": actor_id,
        "target_id": target_id, "weapon_ref": weapon,
    }, SequenceRng(list(rolls)))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


def enemy_attack(engine, instance, target_id: str, rolls) -> dict:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index("enemy:goblin-1")
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, "enemy:goblin-1"),
    )
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"enemy-attack:{target_id}",
        "type": "attack", "submitted_by": "gm",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "actor_id": "enemy:goblin-1", "target_id": target_id,
        "attack_id": "scimitar",
    }, SequenceRng(list(rolls)))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


def cast(engine, instance, spell_ref: str, slot: int, targets: list[str], *, rolls=()):
    set_turn(engine, instance, "player:gm")
    resolved = engine.resolve_intent(instance, {
        "intent_id": f"cast:{spell_ref}:{slot}",
        "type": "cast_spell", "submitted_by": "gm", "actor_id": "player:gm",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
    }, SequenceRng(list(rolls)))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return resolved


def grant(instance, uid: str, *refs: str) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    for ref in refs:
        if ref not in magic["prepared_spell_refs"]:
            magic["prepared_spell_refs"].append(ref)


def grant_slot(instance, uid: str, level: int, count: int = 1) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    magic["slots_current"][str(level)] = count
    magic["slots_max"][str(level)] = count


def direct_damage(engine, instance, amount: int, *, target_id: str = "player:gm") -> dict:
    version = ruleset_runtime.state(instance)["version"]
    return engine.apply_batch(instance, {
        "batch_id": f"batch_damage_{amount}_{target_id}_{version}",
        "intent_id": f"damage-{amount}", "intent_type": "attack",
        "expected_version": version, "result_version": version + 1,
        "events": [{
            "type": "resource.changed", "target_id": target_id, "resource": "hp",
            "delta": -amount, "amount": amount,
        }],
        "source_ref": "test:dark-ones-blessing-damage",
    })


def events_of(result: dict) -> list[dict]:
    return result["event_batch"]["events"]


def temp_events(result: dict) -> list[dict]:
    return [
        event for event in events_of(result)
        if event["type"] == "resource.changed" and event.get("resource") == "temp_hp"
    ]


def temp_event(result: dict) -> dict:
    return temp_events(result)[0]


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def resources(instance, uid: str = "gm") -> dict:
    return canonical(instance, uid)["resources"]


def catalog_definition(raw: dict, feature_id: str = FEATURE_ID) -> dict:
    for body in raw["classes"].values():
        for definition in body.get("features") or []:
            if definition.get("id") == feature_id:
                return definition
    raise AssertionError(f"{feature_id} is not declared")


def bundle_with_class_features(base, raw: dict):
    class _Bundle:
        def __getattr__(self, name):
            return getattr(base, name)

        def get(self, kind, ident=None):
            if kind == "class_feature_catalog":
                return raw
            return base.get(kind, ident)

        def list(self, kind):
            return base.list(kind)

    return _Bundle()


def resolver_for(bundle, parameters: dict):
    raw = deepcopy(bundle.get("class_feature_catalog", "srd_class_features"))
    catalog_definition(raw)["parameters"] = parameters
    return Dnd2024ClassFeatureResolver(bundle_with_class_features(bundle, raw))


# --- matrix 5-6: the payload is one closed four-key set -------------------

@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"temp_hp_ability": "cha", "temp_hp_class_ref": "class:warlock"},
                     id="missing-trigger-and-minimum"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_class_ref": "class:warlock"}, id="missing-minimum"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_minimum": 1}, id="missing-class-ref"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp",
                      "temp_hp_class_ref": "class:warlock", "temp_hp_minimum": 1},
                     id="missing-ability"),
        pytest.param({"trigger": "ally_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_class_ref": "class:warlock", "temp_hp_minimum": 1},
                     id="unknown-trigger"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "luck",
                      "temp_hp_class_ref": "class:warlock", "temp_hp_minimum": 1},
                     id="unknown-ability"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_class_ref": "warlock", "temp_hp_minimum": 1},
                     id="class-ref-without-prefix"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_class_ref": "class:dragon", "temp_hp_minimum": 1},
                     id="class-ref-without-a-table"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_class_ref": "class:warlock", "temp_hp_minimum": 0},
                     id="minimum-zero"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_class_ref": "class:warlock", "temp_hp_minimum": True},
                     id="minimum-true"),
        pytest.param({"trigger": "enemy_reduced_to_zero_hp", "temp_hp_ability": "cha",
                      "temp_hp_class_ref": "class:warlock", "temp_hp_minimum": 1.0},
                     id="minimum-float"),
        # 已删除的旧载荷：留着就等于把“队友击杀 10 尺共享”放回引擎。
        pytest.param({"defeat_share_range_ft": 10}, id="retired-share-radius"),
        pytest.param({"defeat_temp_hp_ability": "cha",
                      "defeat_temp_hp_add_class_level": True}, id="retired-ability-flag"),
    ],
)
def test_the_trigger_payload_is_one_closed_four_key_set(payload) -> None:
    bundle = Dnd2024Runtime().load_bundle("en")
    with pytest.raises(ClassFeatureError):
        resolver_for(bundle, payload)


def test_the_declared_payload_is_exactly_the_four_keys() -> None:
    bundle = Dnd2024Runtime().load_bundle("en")
    definition = catalog_definition(bundle.get("class_feature_catalog", "srd_class_features"))
    assert set(definition["parameters"]) == PAYLOAD_KEYS
    assert definition["source_ref"] == "srd-5.2.1:p76:dark-ones-blessing"
    assert definition["owner"] == "subclass:fiend_patron"
    assert definition["minimum_level"] == 3
    assert "10 feet" not in definition["summary"]


def test_a_foreign_trigger_key_is_not_claimed_by_this_payload() -> None:
    """A sibling slice may declare its own trigger name on another feature."""

    bundle = Dnd2024Runtime().load_bundle("en")
    raw = deepcopy(bundle.get("class_feature_catalog", "srd_class_features"))
    critical = catalog_definition(raw, "improved_critical")
    critical["parameters"] = {**critical["parameters"], "trigger": "hit_with_weapon"}

    Dnd2024ClassFeatureResolver(bundle_with_class_features(bundle, raw))


# --- matrix 1-4: ownership, labels, source ref ----------------------------

def test_feature_is_owned_from_level_three() -> None:
    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    level_three = warlock_sheet(runtime, level=3)["ruleset_character"]
    level_two = warlock_sheet(runtime, level=2)["ruleset_character"]
    assert resolver.has_feature(level_three, FEATURE_ID) is True
    assert resolver.has_feature(level_two, FEATURE_ID) is False


def test_without_the_subclass_choice_the_kill_grants_nothing() -> None:
    """2. A level-3 Warlock who is not a Fiend owns no blessing."""

    runtime = Dnd2024Runtime()
    sheet = warlock_sheet(runtime, level=3)
    for forged in ("", "subclass:great_old_one"):
        sheet["ruleset_character"]["features"]["subclass_ref"] = forged
        engine, instance = monk_instance(runtime, deepcopy(sheet))
        start(engine, instance, [enemy(hp=1)])
        result = attack(engine, instance, rolls=[10, 4])
        assert temp_events(result) == []
        assert "temp_hp" not in resources(instance)


def test_another_class_cannot_forge_the_subclass_ref() -> None:
    """3. A Rogue holding ``subclass:fiend_patron`` still owns nothing."""

    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    sheet = rogue_sheet(runtime)
    sheet["ruleset_character"].setdefault("features", {})[
        "subclass_ref"
    ] = "subclass:fiend_patron"
    assert resolver.has_feature(sheet["ruleset_character"], FEATURE_ID) is False

    engine, instance = monk_instance(runtime, sheet)
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])
    assert temp_events(result) == []


def test_labels_and_source_ref_match_the_srd() -> None:
    runtime = Dnd2024Runtime()
    for locale, label in (("en", "Dark One's Blessing"), ("zh-CN", "黑暗赐福")):
        catalog = runtime.load_bundle(locale).get(
            "class_feature_catalog", "srd_class_features",
        )
        assert catalog["labels"][FEATURE_ID] == label


# --- matrix 7: the holder's own kill, with the full audit payload ---------

def test_holder_kill_grants_temp_hp_with_complete_audit_fields() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    event = temp_event(result)
    assert event["target_id"] == "player:gm"
    assert event["source_actor_id"] == "player:gm"
    assert event["defeated_actor_id"] == "enemy:goblin-1"
    assert event["amount"] == 5  # Charisma 15 (+2) + Warlock 3
    assert event["delta"] == 5
    assert event["rolls"] == []
    assert event["feature_id"] == FEATURE_ID
    assert event["owner"] == "subclass:fiend_patron"
    assert event["ability"] == "cha"
    assert event["ability_modifier"] == 2
    assert event["class_ref"] == "class:warlock"
    assert event["class_level"] == 3
    assert resources(instance)["temp_hp"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


# --- matrix 8: not every hit is a defeat ---------------------------------

def test_a_non_lethal_hit_grants_nothing() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=20)])
    result = attack(engine, instance, rolls=[10, 4])

    assert temp_events(result) == []
    assert "temp_hp" not in resources(instance)


def test_a_miss_grants_nothing() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[1, 4, 1])

    assert temp_events(result) == []
    assert "temp_hp" not in resources(instance)
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 1


def test_a_condition_only_spell_grants_nothing() -> None:
    """8. Zero HP damage -- a Blinded enemy was not reduced to 0 Hit Points."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    grant(instance, "gm", "spell:color_spray")
    grant_slot(instance, "gm", 1)

    result = cast(engine, instance, "spell:color_spray", 1, ["enemy:goblin-1"], rolls=[1])

    assert temp_events(result) == []
    assert "temp_hp" not in resources(instance)
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 1


# --- matrix 9-10: the formula -------------------------------------------

def test_negative_charisma_still_grants_one() -> None:
    _runtime, engine, instance = warlock_instance()
    canonical(instance)["abilities"]["cha"] = 6  # modifier -2, +3 still floors at 1
    start(engine, instance, [enemy(hp=1)])

    result = attack(engine, instance, rolls=[10, 4])
    assert temp_event(result)["amount"] == 1
    assert resources(instance)["temp_hp"] == 1


def test_the_amount_follows_the_current_level_and_charisma() -> None:
    """10. A fourth Warlock level is read at settlement, not cached at build."""

    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, warlock_sheet(runtime, level=4))
    modifier = ability_modifier(int(canonical(instance)["abilities"]["cha"]))
    start(engine, instance, [enemy(hp=1)])

    event = temp_event(attack(engine, instance, rolls=[10, 4]))
    assert event["class_level"] == 4
    assert event["amount"] == max(1, modifier + 4)
    assert resources(instance)["temp_hp"] == event["amount"]


# --- matrix 11-12: only the source, and no distance ----------------------

@pytest.mark.parametrize("enemy_position", [5, 60])
def test_a_teammates_kill_grants_nothing_at_any_distance(enemy_position) -> None:
    """11. Another creature's kill pays that creature, never a bystander."""

    _runtime, engine, instance = party_instance(
        positions={"gm": 0, "ally": 0}, enemy_position=enemy_position,
    )
    set_turn(engine, instance, "player:ally")
    result = attack(engine, instance, actor_id="player:ally", rolls=[10, 4, 1])

    assert temp_events(result) == []
    assert "temp_hp" not in resources(instance)


def test_a_ranged_kill_needs_no_distance() -> None:
    """12. No visibility, distance or Reaction gate beyond the spell's range."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1, position=60)], positions={"gm": 0})
    grant(instance, "gm", "spell:scorching_ray")

    result = cast(engine, instance, "spell:scorching_ray", 2, ["enemy:goblin-1"],
                  rolls=[10, 1, 1])

    assert temp_event(result)["amount"] == 5
    assert resources(instance)["temp_hp"] == 5


def test_an_opportunity_attack_kill_grants_once() -> None:
    """20. The reaction attack settles through the same final-damage path."""

    _runtime, engine, instance = warlock_instance()
    # 让哥布林先攻：它才有移动预算，才能主动离开术士的触及范围。第二个敌人
    # 只是让这场战斗不因这一击立刻结束（胜利会清空经济，held 移动随即失败，
    # 那是既有引擎行为，与触发器无关）。
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm",
        "enemies": [enemy(hp=1, position=5), enemy("goblin-2", hp=40, position=30)],
        "player_positions": {"gm": 0},
    }, SequenceRng([1, 20, 1, 1]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["initiative"][0] == "enemy:goblin-1"

    moved = engine.resolve_intent(instance, {
        "intent_id": "move-away", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": -15,
    }, SequenceRng([]))
    assert moved["ok"] is True, moved
    assert engine.apply_batch(instance, moved["event_batch"])["applied"] is True
    decision = moved["pending_decision"]
    assert decision["kind"] == "opportunity_attack"
    # 借机攻击窗口打开时还没有任何伤害，因此不能提前发临时 HP。
    assert temp_events(moved) == []

    decided = engine.resolve_intent(instance, {
        "intent_id": "oa-resolve", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "resolve",
    }, SequenceRng([10, 4]))
    assert decided["ok"] is True, decided
    assert engine.apply_batch(instance, decided["event_batch"])["applied"] is True

    assert len(temp_events(decided)) == 1
    assert temp_event(decided)["amount"] == 5
    assert resources(instance)["temp_hp"] == 5


# --- matrix 13: an already-defeated enemy is not a fresh kill ------------

def test_defeated_enemy_cannot_be_farmed() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1), enemy("goblin-2", hp=40)])
    attack(engine, instance, rolls=[10, 4])

    set_turn(engine, instance, "player:gm")
    resolved = engine.resolve_intent(instance, {
        "intent_id": "attack-corpse", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
    }, SequenceRng([10, 4]))

    # 现有校验拒绝攻击已归零目标，触发点因此不会被重复进入。
    assert resolved["ok"] is False
    assert "living hostile" in resolved["error"]
    assert resources(instance)["temp_hp"] == 5


# --- matrix 14: players, companions and featureless sources --------------

def test_a_player_brought_to_zero_grants_nothing() -> None:
    """14. The target side matters: only ``kind == "enemy"`` pays out."""

    _runtime, engine, instance = party_instance()
    direct_damage(
        engine, instance, resources(instance, "ally")["hp"] - 3,
        target_id="player:ally",
    )
    assert resources(instance, "ally")["hp"] == 3

    result = enemy_attack(engine, instance, "player:ally", [15, 3, 3])

    assert resources(instance, "ally")["hp"] == 0
    assert temp_events(result) == []


def test_ordinary_combat_emits_no_temp_hp() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, rogue_sheet(runtime))
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4, 1])

    assert temp_events(result) == []
    assert "temp_hp" not in resources(instance)


# --- matrix 15-19: the spell damage surfaces ----------------------------

def test_two_spell_beams_each_kill_once_and_keep_max() -> None:
    """15 + 19. One cast, two targets, two audit rows, keep-max temp HP."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [
        enemy("goblin-1", hp=1, position=5),
        enemy("goblin-2", hp=1, position=10),
    ], rolls=[20, 1, 1])
    grant(instance, "gm", "spell:scorching_ray")

    result = cast(
        engine, instance, "spell:scorching_ray", 2,
        ["enemy:goblin-1", "enemy:goblin-2"],
        rolls=[10, 1, 1, 10, 1, 1],
    )

    assert len(temp_events(result)) == 2
    assert {event["amount"] for event in temp_events(result)} == {5}
    assert {event["defeated_actor_id"] for event in temp_events(result)} == {
        "enemy:goblin-1", "enemy:goblin-2",
    }
    assert resources(instance)["temp_hp"] == 5
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["enemies"]["goblin-1"]["hp"] == 0
    assert combat["enemies"]["goblin-2"]["hp"] == 0


@pytest.mark.parametrize("beam_count", [2, 3])
def test_repeated_beams_on_one_defeated_target_grant_once(beam_count: int) -> None:
    """18. Only the beam that first crosses 0 HP triggers."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy("goblin-1", hp=1)])
    grant(instance, "gm", "spell:scorching_ray")

    result = cast(
        engine, instance, "spell:scorching_ray", 2,
        ["enemy:goblin-1"] * beam_count,
        rolls=[10, 1, 1] * beam_count,
    )

    damage_events = [
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("target_id") == "enemy:goblin-1"
    ]
    assert len(damage_events) == beam_count
    assert len(temp_events(result)) == 1
    assert resources(instance)["temp_hp"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


def test_a_later_beam_that_first_crosses_the_threshold_grants_once() -> None:
    """18. The accumulated total decides, not any single beam."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy("goblin-1", hp=10)])
    grant(instance, "gm", "spell:scorching_ray")

    result = cast(
        engine, instance, "spell:scorching_ray", 2,
        ["enemy:goblin-1", "enemy:goblin-1"],
        rolls=[10, 1, 1, 10, 6, 6],
    )

    assert len(temp_events(result)) == 1
    assert temp_event(result)["amount"] == 5
    assert resources(instance)["temp_hp"] == 5


def test_saving_throw_kill_grants_temp_hp() -> None:
    """16. A save-halved spell still pays out when the halved total kills."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=5)])
    grant(instance, "gm", "spell:inflict_wounds")
    grant_slot(instance, "gm", 1)

    result = cast(
        engine, instance, "spell:inflict_wounds", 1, ["enemy:goblin-1"],
        rolls=[20, 10, 5],
    )

    # 豁免成功减半：15 点原始伤害减半为 7，仍然跨过 5 HP。
    assert temp_event(result)["amount"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


def test_a_halved_save_that_stops_short_of_zero_grants_nothing() -> None:
    """16. The raw dice would kill; the settled damage does not."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=15)])
    grant(instance, "gm", "spell:inflict_wounds")
    grant_slot(instance, "gm", 1)

    result = cast(
        engine, instance, "spell:inflict_wounds", 1, ["enemy:goblin-1"],
        rolls=[20, 10, 10],
    )

    damage = next(
        event for event in events_of(result)
        if event["type"] == "resource.changed"
        and event.get("target_id") == "enemy:goblin-1"
    )
    assert damage["amount"] == 10
    assert temp_events(result) == []
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 5


def test_automatic_damage_kill_grants_temp_hp() -> None:
    """17. Automatic damage has no attack roll but the same defeat guard."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    grant(instance, "gm", "spell:magic_missile")
    grant_slot(instance, "gm", 1)

    result = cast(engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
                  rolls=[1, 1, 1])

    assert temp_event(result)["amount"] == 5
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 0


def test_automatic_damage_that_does_not_zero_grants_nothing() -> None:
    """17. Automatic is not the same thing as always defeating."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=40)])
    grant(instance, "gm", "spell:magic_missile")
    grant_slot(instance, "gm", 1)

    result = cast(engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
                  rolls=[4, 4, 4])

    assert temp_events(result) == []
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 25


# --- matrix 24-27: temp HP bookkeeping ----------------------------------

@pytest.mark.parametrize("existing", [8, 5])
def test_existing_temp_hp_is_kept_when_the_grant_is_lower_or_equal(existing) -> None:
    _runtime, engine, instance = warlock_instance()
    resources(instance)["temp_hp"] = existing
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    assert temp_event(result)["amount"] == 5
    assert resources(instance)["temp_hp"] == existing


def test_a_higher_grant_replaces_the_old_temp_hp() -> None:
    _runtime, engine, instance = warlock_instance()
    resources(instance)["temp_hp"] = 2
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    assert temp_event(result)["amount"] == 5
    assert resources(instance)["temp_hp"] == 5


def test_temp_hp_absorbs_damage_before_hp() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=40)])
    resources(instance).update({"temp_hp": 6})
    before = resources(instance)["hp"]

    direct_damage(engine, instance, 4)

    assert resources(instance)["temp_hp"] == 2
    assert resources(instance)["hp"] == before


def test_a_dying_source_only_gains_temp_hp() -> None:
    """27. The grant is neither healing nor a revive.

    A creature at 0 HP cannot take the attack action, so the settlement is
    driven directly; what matters is that the event only touches temp HP.
    """

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)], rolls=[20, 1])
    resources(instance)["hp"] = 0
    canonical(instance)["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 0},
    }
    combat = ruleset_runtime.state(instance)["combat"]
    target = engine._actor_view(instance, combat, "enemy:goblin-1")
    events = engine._enemy_zero_hp_feature_events(
        instance, combat, "player:gm", target, 1,
    )
    assert [event["amount"] for event in events] == [5]

    version = ruleset_runtime.state(instance)["version"]
    applied = engine.apply_batch(instance, {
        "batch_id": "batch_dying_grant", "intent_id": "dying-grant",
        "intent_type": "attack", "expected_version": version,
        "result_version": version + 1, "events": events,
        "source_ref": "test:dying-grant",
    })
    assert applied["applied"] is True, applied

    assert resources(instance)["hp"] == 0
    assert resources(instance)["temp_hp"] == 5
    assert "unconscious" in canonical(instance)["conditions"]


# --- matrix 28-30: order, replay, RNG ------------------------------------

def test_temp_hp_event_precedes_victory() -> None:
    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    events = events_of(result)
    damage_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "resource.changed"
        and event.get("target_id") == "enemy:goblin-1"
    )
    temp_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "resource.changed" and event.get("resource") == "temp_hp"
    )
    victory_index = events.index({"type": "dnd2024.combat.ended", "reason": "victory"})
    assert damage_index < temp_index < victory_index
    assert sum(event["type"] == "dnd2024.combat.ended" for event in events) == 1
    # 战斗结束不回滚已授予的临时 HP。
    assert resources(instance)["temp_hp"] == 5


def test_a_replayed_batch_grants_nothing_twice() -> None:
    """29. The audit fields are inert on replay; the ledger stays authoritative."""

    _runtime, engine, instance = warlock_instance()
    start(engine, instance, [enemy(hp=1)])
    result = attack(engine, instance, rolls=[10, 4])

    def settled():
        return (
            resources(instance)["temp_hp"], resources(instance)["hp"],
            ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"],
        )

    before = settled()
    replay = engine.apply_batch(instance, deepcopy(result["event_batch"]))

    assert replay["duplicate"] is True
    assert replay["applied"] is False
    assert settled() == before


class CountingRng(SequenceRng):
    """A deterministic RNG that also records how many dice were drawn."""

    def __init__(self, values):
        super().__init__(list(values))
        self.draws = 0

    def randint(self, minimum, maximum):
        self.draws += 1
        return super().randint(minimum, maximum)


def test_the_trigger_consumes_no_dice() -> None:
    """30. Same attack, with and without the feature: identical RNG draws."""

    draws = []
    for sheet in ("warlock", "rogue"):
        runtime = Dnd2024Runtime()
        built = warlock_sheet(runtime, level=3) if sheet == "warlock" else rogue_sheet(runtime)
        engine, instance = monk_instance(runtime, built)
        start(engine, instance, [enemy(hp=40)])
        rng = CountingRng([10, 4])
        resolved = engine.resolve_intent(instance, {
            "intent_id": f"attack:{sheet}", "type": "attack",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm", "actor_id": "player:gm",
            "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
        }, rng)
        assert resolved["ok"], resolved
        draws.append(rng.draws)

    assert draws[0] == draws[1]
