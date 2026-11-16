"""Hit riders use the same save authority and sequential attack state."""
from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.check_modes import effective_speed
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from dnd2024_monk_common import monk_sheet, monk_instance, start_combat, SequenceRng

TARGET = "enemy:goblin-1"
ACTOR = "player:gm"


@pytest.fixture
def combat():
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=7)
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance, hp=200)
    return engine, instance


def _intent(instance, body):
    version = ruleset_runtime.state(instance)["version"]
    return {
        "intent_id": f"hit-rider-{version}", "submitted_by": "gm",
        "actor_id": ACTOR, "expected_version": version, **body,
    }


def _apply(engine, instance, body, rolls):
    before = deepcopy(instance.to_dict())
    result = engine.resolve_intent(instance, _intent(instance, body), SequenceRng(list(rolls)))
    assert result["ok"], result
    # Planning must not leak the reducer's simulation into the live aggregate.
    assert instance.to_dict() == before
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    return result["event_batch"]["events"]


def _attack(**extra):
    return {"type": "attack", "weapon_ref": "unarmed_strike", "target_id": TARGET, **extra}


def _flurry(**extra):
    return {"type": "class_capability", "capability_id": "flurry_of_blows", "target_id": TARGET, **extra}


def _target(engine, instance):
    return engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], TARGET)


def _focus(instance):
    return instance.get_character_sheet("gm")["ruleset_character"]["resources"]["class"]["focus_points"]["current"]


def test_next_attack_consumes_only_the_stunning_opening_not_the_slow(combat):
    engine, instance = combat
    _apply(engine, instance, _attack(stunning_strike=True), [19, 2, 20])
    assert {"staggered", "staggered_opening"} <= _target(engine, instance)["conditions"].keys()
    events = _apply(engine, instance, _attack(), [19, 18, 2])
    check = next(e for e in events if e.get("kind") == "attack")
    assert check["advantage_mode"] == "advantage"
    target = _target(engine, instance)
    assert "staggered" in target["conditions"] and "staggered_opening" not in target["conditions"]
    assert effective_speed(engine.catalog.conditions, target["conditions"], target["speed"]) == target["speed"] // 2
    # Both outcomes end at the attacker's next turn, not after its next hit.
    for _ in range(2):
        state = ruleset_runtime.state(instance)["combat"]
        _apply(engine, instance, {"type": "end_turn", "actor_id": engine._current_actor(state)}, [])
    assert "staggered" not in _target(engine, instance)["conditions"]


def test_stunned_target_automatically_fails_open_hand_dex_saves(combat):
    engine, instance = combat
    _apply(engine, instance, _attack(stunning_strike=True), [19, 2, 1])
    assert "stunned" in _target(engine, instance)["conditions"]
    events = _apply(engine, instance, _flurry(open_hand_choice="topple"), [19, 18, 2, 19, 18, 2])
    saves = [e for e in events if e.get("kind") == "saving_throw"]
    assert len(saves) == 2
    assert all(not e["success"] and e["auto_failed"] and e["rolls"] == [] for e in saves)
    assert "prone" in _target(engine, instance)["conditions"]


def test_push_stops_followup_attacks_once_the_target_leaves_reach(combat):
    engine, instance = combat
    events = _apply(engine, instance, _flurry(open_hand_choice="push"), [19, 2, 1])
    assert len([e for e in events if e.get("kind") == "attack"]) == 1
    assert len([e for e in events if e["type"] == "dnd2024.position.changed"]) == 1
    state = ruleset_runtime.state(instance)["combat"]
    assert engine._distance(state, ACTOR, TARGET) == 20


def test_first_hit_stun_changes_the_second_hit_and_its_save(combat):
    engine, instance = combat
    events = _apply(engine, instance, _flurry(open_hand_choice="topple", stunning_strike=True),
                    [19, 2, 1, 1, 19, 18, 2])
    attacks = [e for e in events if e.get("kind") == "attack"]
    assert len(attacks[0]["rolls"]) == 1
    assert len(attacks[1]["rolls"]) == 2 and attacks[1]["advantage_mode"] == "advantage"
    saves = [e for e in events if e.get("source_feature") == "open_hand_technique" and e.get("kind") == "saving_throw"]
    assert len(saves) == 2 and saves[1]["auto_failed"]


@pytest.mark.parametrize("capability", ["flurry_of_blows", "bonus_unarmed_strike"])
def test_bonus_attacks_forward_force_and_pay_stunning_only_once(combat, capability):
    engine, instance = combat
    before = _focus(instance)
    body = _flurry(unarmed_damage_type="force", stunning_strike=True)
    body["capability_id"] = capability
    events = _apply(engine, instance, body, [19, 2, 20, 19, 18, 2])
    damage = [e for e in events if e.get("resource") == "hp"]
    expected = 2 if capability == "flurry_of_blows" else 1
    assert len(damage) == expected and all(e["damage_type"] == "force" for e in damage)
    assert len([e for e in events if e.get("source_feature") == "stunning_strike"]) == 1
    assert _focus(instance) == before - (2 if capability == "flurry_of_blows" else 1)


def test_a_missed_first_flurry_hit_preserves_stunning_for_the_second(combat):
    engine, instance = combat
    before = _focus(instance)
    events = _apply(engine, instance, _flurry(stunning_strike=True), [1, 19, 2, 1])
    assert len([e for e in events if e.get("source_feature") == "stunning_strike"]) == 1
    assert _focus(instance) == before - 2


@pytest.mark.parametrize("choice", [
    {"unarmed_damage_type": "fire"}, {"stunning_strike": "yes"},
    {"stunning_strike": True, "focus": 1},
])
def test_bonus_attack_rider_choices_are_validated_before_spending(combat, choice):
    engine, instance = combat
    choice = dict(choice)
    focus = choice.pop("focus", None)
    if focus is not None:
        instance.get_character_sheet("gm")["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = focus
    before = deepcopy(instance.to_dict())
    result = engine.resolve_intent(instance, _intent(instance, _flurry(**choice)), SequenceRng([]))
    assert not result["ok"]
    assert instance.to_dict() == before


def test_open_hand_uses_save_advantage_and_bless(combat):
    engine, instance = combat
    enemy = ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]
    enemy["conditions"].update({"rage": {}, "blessed": {}})
    events = _apply(engine, instance, _flurry(open_hand_choice="push"),
                    [19, 2, 1, 20, 4, 19, 2, 1, 20, 4])
    saves = [e for e in events if e.get("kind") == "saving_throw"]
    assert len(saves) == 2
    assert all(e["rolls"] == [1, 20] and e["advantage_mode"] == "advantage" for e in saves)
    assert all(e["check_bonus"] == 4 and e["success"] for e in saves)
    assert not any(e["type"] == "dnd2024.position.changed" for e in events)


def test_hit_save_reuses_danger_sense_and_the_live_paladin_aura(monkeypatch):
    import test_dnd2024_condition_immunity as immunity
    import test_dnd2024_save_damage_survival as survival
    from test_dnd2024_condition_immunity import _party_combat, _paladin
    from test_dnd2024_save_damage_survival import _barbarian

    runtime = Dnd2024Runtime()
    # Their declaration-rejection cases mutate module-level runtime bundles;
    # do not inherit those intentionally malformed catalogs in a combined run.
    monkeypatch.setattr(immunity, "RUNTIME", runtime)
    monkeypatch.setattr(survival, "RUNTIME", runtime)
    engine, instance = _party_combat({
        "gm": monk_sheet(runtime, level=7), "barbarian": _barbarian(7), "paladin": _paladin(10),
    }, positions={ACTOR: 0, "player:barbarian": 5, "player:paladin": 5})
    canonical = instance.get_character_sheet("barbarian")["ruleset_character"]
    canonical.setdefault("conditions", {})["blessed"] = {}
    state = ruleset_runtime.state(instance)["combat"]
    target = engine._actor_view(instance, state, "player:barbarian")
    # As in the existing spell-save tests, target a real allied canonical
    # character directly at the resolver boundary (PvP intent is forbidden).
    events = engine._hit_condition_events(instance, state, ACTOR, target, {
        "hit_condition_effects": [{
            "kind": "open_hand", "choice": "topple", "topple_save_ability": "dex",
            "topple_condition": "prone",
        }],
    }, SequenceRng([1, 9, 4]))
    save = next(e for e in events if e.get("kind") == "saving_throw")
    assert save["rolls"] == [1, 9] and save["advantage_mode"] == "advantage"
    assert any(e["code"] == "danger_sense" for e in save["advantage_sources"])
    assert save["check_bonus"] == 4 and save["aura_bonus"] >= 1
    assert save["total"] == save["natural"] + save["modifier"] + 4 + save["aura_bonus"]
