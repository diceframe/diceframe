"""Invisibility slice (P1): data-driven attack modes, upcasting, early ends."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.combat.check_modes import mode_from_sources
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
)


END_TRIGGERS = ["attack_roll", "damage_dealt", "spell_cast"]
CANTRIPS = ["spell:light", "spell:mage_hand", "spell:ray_of_frost"]
SPELLBOOK = [
    # Levels 1-2 must stay inside the wizard's maximum spell level at that level.
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:sleep",
    "spell:shield",
    "spell:feather_fall",
    "spell:thunderwave",
    "spell:charm_person",
    "spell:invisibility",
    "spell:blur",
]
PREPARED = [
    "spell:detect_magic",
    "spell:mage_armor",
    "spell:magic_missile",
    "spell:sleep",
    "spell:shield",
    "spell:invisibility",
]


def wizard_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle("en")).requirements(
        "class:wizard", level
    )
    prepared = PREPARED[: requirements["prepared_spell_count"]]
    spellbook = SPELLBOOK[: requirements["spellbook_minimum"]]
    assert set(prepared).issubset(spellbook)
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def wizard_sheet(runtime: Dnd2024Runtime, *, level: int = 3) -> dict:
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
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        choices["class_spell_choices"] = wizard_choices(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def wizard_instance(*, level: int = 3):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, wizard_sheet(runtime, level=level))
    start(engine, instance, allies=())
    return runtime, engine, instance


def start(engine, instance, *, allies) -> None:
    for uid, sheet in allies:
        instance.players[uid] = {"character_name": uid.title(), "character_sheet": sheet}
    # sorted(players) is ["ally", "gm"]: low roll for the ally, high roll for the GM.
    rolls = [1, 20, 1] if allies else [20, 1]
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(position=5, hp=100)],
    }, SequenceRng(rolls))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == "player:gm"


def duo_instance(*, level: int = 3):
    runtime = Dnd2024Runtime()
    gm_sheet = wizard_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, gm_sheet)
    start(engine, instance, allies=[("ally", wizard_sheet(runtime, level=level))])
    return runtime, engine, instance


def canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def grant(instance, uid: str, *refs: str) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    for ref in refs:
        if ref not in magic["prepared_spell_refs"]:
            magic["prepared_spell_refs"].append(ref)


def grant_slot(instance, uid: str, level: int, count: int = 1) -> None:
    magic = canonical(instance, uid)["spellcasting"]["class"]
    magic["slots_current"][str(level)] = count
    magic["slots_max"][str(level)] = count


def add_player_condition(instance, uid: str, condition: str) -> None:
    canonical(instance, uid).setdefault("conditions", {})[condition] = {"source": "test"}


def add_enemy_condition(instance, condition: str) -> None:
    enemies = ruleset_runtime.state(instance)["combat"]["enemies"]
    enemies["goblin-1"].setdefault("conditions", {})[condition] = {"source": "test"}


def set_turn(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def resolve(engine, instance, request: dict, rolls=()):
    request.setdefault("expected_version", ruleset_runtime.state(instance)["version"])
    result = engine.resolve_intent(instance, request, SequenceRng(list(rolls)))
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return result


def submitter(actor_id: str) -> str:
    # A seat submits for its own character; the GM submits enemy intents.
    return actor_id.removeprefix("player:") if actor_id.startswith("player:") else "gm"


def cast(engine, instance, uid: str, spell_ref: str, slot: int, targets: list[str], rolls=()):
    set_turn(engine, instance, f"player:{uid}")
    version = ruleset_runtime.state(instance)["version"]
    request = {
        "intent_id": f"cast:{spell_ref}:{slot}:{len(targets)}:{version}",
        "type": "cast_spell", "submitted_by": submitter(f"player:{uid}"),
        "actor_id": f"player:{uid}",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
    }
    return resolve(engine, instance, request, rolls)


def attack(engine, instance, *, actor_id: str, target_id: str, weapon=None, attack_id=None, rolls=()):
    set_turn(engine, instance, actor_id)
    version = ruleset_runtime.state(instance)["version"]
    request = {
        "intent_id": f"attack:{actor_id}:{target_id}:{weapon or attack_id}:{version}",
        "type": "attack", "submitted_by": submitter(actor_id),
        "actor_id": actor_id, "target_id": target_id,
    }
    if weapon:
        request["weapon_ref"] = weapon
    if attack_id:
        request["attack_id"] = attack_id
    return resolve(engine, instance, request, rolls)


def check(result: dict) -> dict:
    return next(
        event for event in result["event_batch"]["events"]
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    )


def event_types(result: dict) -> list[str]:
    return [event["type"] for event in result["event_batch"]["events"]]


def catalog_bundle(base, *, pop_conditions: bool = False, **override):
    class _Bundle:
        def get(self, kind, ident=None):
            if kind == "combat_catalog":
                raw = deepcopy(base.get(kind, ident))
                if pop_conditions:
                    raw.pop("conditions", None)
                raw.update(deepcopy(override))
                return raw
            return base.get(kind, ident)

        def list(self, kind):
            return base.list(kind)

    return _Bundle()


# --- #1 catalog validation -------------------------------------------------

def test_catalog_accepts_missing_conditions_and_rejects_malicious_entries() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")

    assert Dnd2024CombatCatalog.from_bundle(catalog_bundle(bundle, pop_conditions=True)).conditions == {}

    bad_side = {"blurred": {"attack_modes": [{"side": "somewhere", "mode": "advantage"}]}}
    bad_mode = {"blurred": {"attack_modes": [{"side": "target", "mode": "neutral"}]}}
    unknown_key = {"blurred": {"attack_modes": [{"side": "target", "mode": "advantage", "bonus": 1}]}}
    bad_negated = {"blurred": {"attack_modes": [{"side": "target", "mode": "advantage", "negated_by": [1]}]}}
    bad_consume = {"blurred": {"attack_modes": [{"side": "target", "mode": "advantage", "consume_on": "turn_end"}]}}
    bad_ends_on = {"invisibility": {"mode": "buff", "range": 5, "ends_on": ["turn_end"]}}
    bad_upcast = {"invisibility": {"mode": "buff", "range": 5, "upcast_target_count": 99}}

    for conditions in (bad_side, bad_mode, unknown_key, bad_negated, bad_consume):
        with pytest.raises(CombatCatalogError):
            Dnd2024CombatCatalog.from_bundle(catalog_bundle(bundle, conditions=conditions))
    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(
            catalog_bundle(bundle, spell_effects={"invisibility": bad_ends_on["invisibility"]})
        )
    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(
            catalog_bundle(bundle, spell_effects={"invisibility": bad_upcast["invisibility"]})
        )


# --- #2 casting ------------------------------------------------------------

def test_invisibility_applies_invisible_condition_with_early_end_metadata() -> None:
    _runtime, engine, instance = duo_instance()

    result = cast(engine, instance, "gm", "spell:invisibility", 2, ["player:ally"], rolls=())

    types = event_types(result)
    started = types.index("dnd2024.concentration.started")
    cast_index = types.index("dnd2024.spell.cast")
    applied_index = types.index("condition.applied")
    assert started < cast_index < applied_index
    applied = next(
        event for event in result["event_batch"]["events"]
        if event["type"] == "condition.applied"
    )
    assert applied["target_id"] == "player:ally"
    assert applied["condition"] == "invisible"
    assert applied["concentration_owner"] == "player:gm"
    assert applied["ends_on"] == END_TRIGGERS
    ally = canonical(instance, "ally")
    assert ally["conditions"]["invisible"]["ends_on"] == END_TRIGGERS
    assert ally["conditions"]["invisible"]["concentration_owner"] == "player:gm"


# --- #3 enemy attacks the invisible ally -----------------------------------

def test_enemy_attack_against_invisible_target_has_disadvantage_and_keeps_low_die() -> None:
    _runtime, engine, instance = duo_instance()
    cast(engine, instance, "gm", "spell:invisibility", 2, ["player:ally"])

    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 8, 4],
    )

    check_event = check(result)
    assert check_event["advantage_mode"] == "disadvantage"
    assert {"code": "invisible", "mode": "disadvantage"} in check_event["advantage_sources"]
    assert check_event["natural"] == 8
    assert check_event["rolls"] == [15, 8]


# --- #4 invisible attacker -------------------------------------------------

@pytest.mark.parametrize("rolls,hits", [([8, 15, 4], True), ([2, 1], False)])
def test_invisible_attacker_has_advantage_and_the_whole_spell_ends(rolls, hits) -> None:
    _runtime, engine, instance = duo_instance()
    cast(engine, instance, "gm", "spell:invisibility", 2, ["player:ally"])

    result = attack(
        engine, instance, actor_id="player:ally", target_id="enemy:goblin-1",
        weapon="item:dagger", rolls=rolls,
    )

    check_event = check(result)
    assert check_event["advantage_mode"] == "advantage"
    assert {"code": "invisible", "mode": "advantage"} in check_event["advantage_sources"]
    assert check_event["natural"] == max(rolls[:2])
    assert check_event["success"] is hits
    types = event_types(result)
    ended = result["event_batch"]["events"][types.index("dnd2024.concentration.ended")]
    assert ended["actor_id"] == "player:gm"
    assert ended["reason"] == "spell_ended_early"
    # Early termination is declared before the cost is paid or the roll happens.
    assert types.index("dnd2024.concentration.ended") < types.index("dnd2024.attack.spent")
    assert types.index("dnd2024.concentration.ended") < types.index("check.resolved")
    assert canonical(instance, "ally")["conditions"].get("invisible") is None
    assert canonical(instance, "gm")["spellcasting"]["class"]["concentration"] is None


# --- #4b invisible opportunity attack ---------------------------------------

def test_invisible_opportunity_attacker_ends_the_spell_and_keeps_both_events() -> None:
    _runtime, engine, instance = wizard_instance()
    cast(engine, instance, "gm", "spell:invisibility", 2, ["player:gm"])

    # 敌人移出隐形法师的近战威胁范围，触发法师的借机攻击。
    set_turn(engine, instance, "enemy:goblin-1")
    moved = resolve(engine, instance, {
        "intent_id": "move-away", "type": "move",
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": 10,
    })
    assert moved["pending_decision"]["kind"] == "opportunity_attack"

    decided = resolve(engine, instance, {
        "intent_id": "decision-away", "type": "decision.resolve",
        "decision_id": moved["pending_decision"]["decision_id"],
        "submitted_by": "gm", "option": "resolve",
    }, rolls=[15, 8, 4])

    types = event_types(decided)
    # 提前终止与反应支付两个事件都必须存在，且终止先于支付。
    assert types.index("dnd2024.concentration.ended") < types.index("dnd2024.reaction.spent")
    assert check(decided)["advantage_mode"] == "advantage"
    assert canonical(instance, "gm")["conditions"].get("invisible") is None


# --- #5 invisible caster casts another spell ------------------------------

def test_invisible_caster_casting_another_spell_ends_the_spell_first() -> None:
    _runtime, engine, instance = wizard_instance()
    grant(instance, "gm", "spell:fire_bolt")
    cast(engine, instance, "gm", "spell:invisibility", 2, ["player:gm"])

    result = cast(engine, instance, "gm", "spell:fire_bolt", 0, ["enemy:goblin-1"], rolls=[20, 3, 2, 2])

    types = event_types(result)
    ended = result["event_batch"]["events"][types.index("dnd2024.concentration.ended")]
    assert ended["reason"] == "spell_ended_early"
    assert types.index("dnd2024.concentration.ended") < types.index("dnd2024.action.spent")
    assert types.index("dnd2024.concentration.ended") < types.index("dnd2024.spell.cast")
    assert canonical(instance, "gm")["conditions"].get("invisible") is None


# --- #6 recasting invisibility on yourself --------------------------------

def test_recasting_invisibility_ends_the_old_spell_once_and_keeps_the_new_one() -> None:
    _runtime, engine, instance = wizard_instance()
    cast(engine, instance, "gm", "spell:invisibility", 2, ["player:gm"])

    result = cast(engine, instance, "gm", "spell:invisibility", 2, ["player:gm"])

    ended = [
        event for event in result["event_batch"]["events"]
        if event["type"] == "dnd2024.concentration.ended"
    ]
    assert len(ended) == 1
    assert ended[0]["reason"] == "spell_ended_early"
    assert canonical(instance, "gm")["conditions"]["invisible"]["ends_on"] == END_TRIGGERS
    assert canonical(instance, "gm")["spellcasting"]["class"]["concentration"]["spell_ref"] == "spell:invisibility"


# --- #7 upcast target count ------------------------------------------------

def test_third_level_slot_upcasts_invisibility_to_two_targets() -> None:
    _runtime, engine, instance = duo_instance()
    grant_slot(instance, "gm", 3)

    result = cast(engine, instance, "gm", "spell:invisibility", 3, ["player:gm", "player:ally"])

    applied = [
        event for event in result["event_batch"]["events"]
        if event["type"] == "condition.applied"
    ]
    assert sorted(event["target_id"] for event in applied) == ["player:ally", "player:gm"]


def test_second_level_slot_rejects_two_invisibility_targets() -> None:
    _runtime, engine, instance = duo_instance()

    validation = engine.validate_intent(instance, {
        "intent_id": "upcast-illegal", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:invisibility", "slot_level": 2,
        "target_ids": ["player:gm", "player:ally"],
    })

    assert validation["ok"] is False
    assert "1 to 1 unique targets" in validation["error"]


# --- #8 greater invisibility has no early end -----------------------------

def test_greater_invisibility_survives_the_target_attacking() -> None:
    _runtime, engine, instance = duo_instance()
    grant(instance, "gm", "spell:greater_invisibility")
    grant_slot(instance, "gm", 4)
    cast(engine, instance, "gm", "spell:greater_invisibility", 4, ["player:ally"])

    attack(
        engine, instance, actor_id="player:ally", target_id="enemy:goblin-1",
        weapon="item:dagger", rolls=[15, 4],
    )

    assert canonical(instance, "ally")["conditions"]["invisible"]["concentration_owner"] == "player:gm"
    assert canonical(instance, "gm")["spellcasting"]["class"]["concentration"]["spell_ref"] == "spell:greater_invisibility"


# --- #9 / #10 sensory negation --------------------------------------------

def test_see_invisibility_negates_the_target_side_disadvantage() -> None:
    _runtime, engine, instance = duo_instance()
    cast(engine, instance, "gm", "spell:invisibility", 2, ["player:ally"])
    add_enemy_condition(instance, "see_invisibility")

    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 4],
    )

    check_event = check(result)
    assert check_event["advantage_mode"] == ""
    assert check_event["rolls"] == [15]


def test_see_invisibility_negates_the_attacker_side_advantage() -> None:
    _runtime, engine, instance = wizard_instance()
    add_player_condition(instance, "gm", "see_invisibility")
    add_enemy_condition(instance, "invisible")

    result = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:dagger", rolls=[15, 4],
    )

    check_event = check(result)
    assert check_event["advantage_mode"] == ""
    assert check_event["rolls"] == [15]


# --- #11 blur -------------------------------------------------------------

def test_blur_imposes_disadvantage_unless_the_attacker_has_truesight() -> None:
    _runtime, engine, instance = wizard_instance()
    grant(instance, "gm", "spell:blur")
    cast(engine, instance, "gm", "spell:blur", 2, ["player:gm"])

    blurry = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 8, 4],
    )
    assert check(blurry)["advantage_mode"] == "disadvantage"

    _runtime2, engine2, instance2 = wizard_instance()
    grant(instance2, "gm", "spell:blur")
    cast(engine2, instance2, "gm", "spell:blur", 2, ["player:gm"])
    add_enemy_condition(instance2, "truesight")

    clear = attack(
        engine2, instance2, actor_id="enemy:goblin-1", target_id="player:gm",
        attack_id="scimitar", rolls=[15, 4],
    )
    assert check(clear)["advantage_mode"] == ""
    assert check(clear)["rolls"] == [15]


# --- #12 registry regression ---------------------------------------------

def test_consumed_conditions_still_follow_one_attack_roll() -> None:
    _runtime, engine, instance = wizard_instance()
    add_enemy_condition(instance, "next_attack_advantage")

    result = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:dagger", rolls=[15, 2, 4],
    )
    assert check(result)["advantage_mode"] == "advantage"
    assert "next_attack_advantage" not in ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["conditions"]

    add_enemy_condition(instance, "dodging")
    dodging = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:dagger", rolls=[15, 2, 4],
    )
    assert check(dodging)["advantage_mode"] == "disadvantage"

    add_player_condition(instance, "gm", "next_attack_disadvantage")
    own = attack(
        engine, instance, actor_id="player:gm", target_id="enemy:goblin-1",
        weapon="item:dagger", rolls=[15, 2, 4],
    )
    assert check(own)["advantage_mode"] == "disadvantage"
    assert "next_attack_disadvantage" not in canonical(instance, "gm")["conditions"]


# --- #13 RAW cancellation -------------------------------------------------

def test_advantage_and_disadvantage_cancel_even_when_counts_differ() -> None:
    sources = [
        {"code": "faerie_fire", "mode": "advantage"},
        {"code": "invisible", "mode": "disadvantage"},
        {"code": "dodging", "mode": "disadvantage"},
    ]
    assert mode_from_sources(sources) == ""
    assert mode_from_sources([{"code": "gm_grant", "mode": "advantage"}]) == "advantage"


# --- #14 invisible + faerie fire -----------------------------------------

def test_invisible_and_faerie_fire_cancel_out_for_a_two_source_attack() -> None:
    _runtime, engine, instance = duo_instance()
    cast(engine, instance, "gm", "spell:invisibility", 2, ["player:ally"])
    add_player_condition(instance, "ally", "faerie_fire")

    result = attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:ally",
        attack_id="scimitar", rolls=[15, 4],
    )

    check_event = check(result)
    assert check_event["advantage_mode"] == ""
    codes = {row["code"] for row in check_event["advantage_sources"]}
    assert {"invisible", "faerie_fire"}.issubset(codes)
    assert check_event["rolls"] == [15]
