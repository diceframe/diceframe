"""Cunning Strike (Rogue 5): the Sneak Attack dice budget and its six effects.

Everything runs through the authoritative chain (``resolve_intent`` ->
``apply_batch``) so the assertions see the same events the web layer does.  A
hit that declares nothing and a hit that declares an effect are compared on the
damage actually rolled, never on a view field: the view reports the base pool,
so a broken budget would still look right there.
"""

from __future__ import annotations

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features.attack_declarations import cunning_strike_options
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, advance, monk_instance, with_equipment


def _rogue_sheet(runtime: Dnd2024Runtime, *, level: int = 5) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "lucky_scout")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Rogue"},
    )
    return advance(runtime, sheet, level) if level > 1 else sheet


def _goblin(
    *, hp: int = 200, position: int = 5, size: str = "medium",
) -> dict:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 2,
        "size": size,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _instance(
    *, level: int = 5, equipment: tuple[str, ...] = ("item:dagger",),
    enemies: list[dict] | None = None,
) -> tuple[Dnd2024Runtime, Dnd2024CombatEngine, object]:
    runtime = Dnd2024Runtime()
    sheet = _rogue_sheet(runtime, level=level)
    if equipment:
        sheet = with_equipment(runtime, sheet, *equipment)
    engine, instance = monk_instance(runtime, sheet)
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies or [_goblin()],
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return runtime, engine, instance


def _apply(engine: Dnd2024CombatEngine, instance, intent: dict, rolls=()) -> list[dict]:
    batch = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert batch["ok"] is True, batch
    assert engine.apply_batch(instance, batch["event_batch"])["applied"] is True
    return batch["event_batch"]["events"]


def _attack(
    engine: Dnd2024CombatEngine, instance, *, rolls, effects: list[str] | None = None,
    declared: bool = True, target_id: str = "enemy:goblin-1",
    weapon: str = "item:dagger", intent_id: str | None = None,
) -> list[dict]:
    intent = {
        "intent_id": intent_id or f"attack-{ruleset_runtime.state(instance)['version']}",
        "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": target_id, "weapon_ref": weapon,
    }
    if effects:
        if declared:
            intent["attack_declarations"] = ["cunning_strike"]
        intent["cunning_strike_effects"] = effects
    return _apply(engine, instance, intent, rolls)


def _rejected(engine: Dnd2024CombatEngine, instance, intent: dict) -> str:
    batch = engine.resolve_intent(instance, intent, SequenceRng([15] + [3] * 30))
    assert batch["ok"] is False, batch
    return str(batch.get("error") or "")


def _end_turn(
    engine: Dnd2024CombatEngine, instance, actor_id: str, rolls=(),
) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": f"end-{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id,
    }, rolls)


def _combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _damage(events: list[dict], target_id: str = "enemy:goblin-1") -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("resource") == "hp"
        and event.get("target_id") == target_id
    )


def _enemy_conditions(instance, enemy_id: str = "goblin-1") -> dict:
    return _combat(instance)["enemies"][enemy_id].setdefault("conditions", {})


def _grant_advantage(instance, enemy_id: str = "goblin-1") -> None:
    """Advantage against the target: Sneak Attack's first predicate."""

    _enemy_conditions(instance, enemy_id)["next_attack_advantage"] = {}


def _sneak_uses(instance) -> int:
    usage = _combat(instance).get("feature_usage") or {}
    return int(usage.get("sneak_attack") or 0)


def _sneak_events(events: list[dict]) -> list[dict]:
    return [
        event for event in events
        if event["type"] == "dnd2024.feature.used"
        and event.get("feature_id") == "sneak_attack"
    ]


def _skipped(events: list[dict]) -> dict | None:
    return next(
        (event for event in events if event["type"] == "dnd2024.cunning_strike.skipped"),
        None,
    )


#: 15 hits AC 12 without critting, so the pool is doubled only when a test
#: asks for it; 3 is a legal face on every die this file rolls.
HIT = [15] + [3] * 40


def test_trip_spends_one_die_and_knocks_the_target_prone() -> None:
    """The paid die leaves the damage roll; a failed Dex save knocks it down."""

    _runtime, engine, instance = _instance()
    _grant_advantage(instance)
    plain = _damage(_attack(engine, instance, rolls=list(HIT)))
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")

    _grant_advantage(instance)
    events = _attack(engine, instance, rolls=list(HIT), effects=["trip"])

    tripped = _damage(events)
    assert sum(tripped["rolls"]) == sum(plain["rolls"]) - 3
    assert len(tripped["rolls"]) == len(plain["rolls"]) - 1
    assert "prone" in _enemy_conditions(instance)
    assert _skipped(events) is None


def test_trip_is_skipped_when_sneak_attack_does_not_apply() -> None:
    """No advantage, no ally: the declaration costs nothing and does nothing."""

    _runtime, engine, instance = _instance()
    events = _attack(engine, instance, rolls=list(HIT), effects=["trip"])

    damage = _damage(events)
    # A dagger hit with no Sneak Attack at all: one die, none spent, no prereq.
    assert len(damage["rolls"]) == 1
    assert "prone" not in _enemy_conditions(instance)
    skipped = _skipped(events)
    assert skipped is not None
    assert skipped["reason"] == "no_sneak_attack"


def test_trip_is_refused_against_a_huge_target() -> None:
    """Prerequisite fails at submit: nothing is spent, nothing is rolled."""

    _runtime, engine, instance = _instance(enemies=[_goblin(size="huge")])
    _grant_advantage(instance)
    error = _rejected(engine, instance, {
        "intent_id": "attack-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
        "attack_declarations": ["cunning_strike"],
        "cunning_strike_effects": ["trip"],
    })
    assert "trip" in error


def test_effects_without_the_declaration_are_refused() -> None:
    _runtime, engine, instance = _instance()
    _grant_advantage(instance)
    intent = {
        "intent_id": "attack-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
        "cunning_strike_effects": ["trip"],
    }
    assert "declaration" in _rejected(engine, instance, intent)


def test_unknown_and_repeated_effects_are_refused() -> None:
    _runtime, engine, instance = _instance()
    _grant_advantage(instance)
    base = {
        "intent_id": "attack-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
        "attack_declarations": ["cunning_strike"],
    }
    assert "option" in _rejected(
        engine, instance, {**base, "cunning_strike_effects": ["slip"]},
    )
    assert "repeat" in _rejected(
        engine, instance, {**base, "cunning_strike_effects": ["trip", "trip"]},
    )


def test_a_second_effect_needs_improved_cunning_strike() -> None:
    """Two effects pay two dice, but only an 11th-level rogue may pick two."""

    _runtime, engine, instance = _instance(level=10)
    _grant_advantage(instance)
    intent = {
        "intent_id": "attack-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
        "attack_declarations": ["cunning_strike"],
        "cunning_strike_effects": ["trip", "withdraw"],
    }
    assert "too many" in _rejected(engine, instance, intent)


def test_an_eleventh_level_rogue_may_use_two_effects() -> None:
    _runtime, engine, instance = _instance(level=11)
    _grant_advantage(instance)
    events = _attack(
        engine, instance, rolls=list(HIT), effects=["trip", "withdraw"],
    )
    damage = _damage(events)
    # 6d6 pool at 11th level, two dice spent: 1d4 dagger + 4d6 Sneak Attack.
    assert len(damage["rolls"]) == 5
    assert "prone" in _enemy_conditions(instance)


def test_the_save_dc_uses_dexterity_not_wisdom() -> None:
    """DC 8 + PB + Dex, read from content -- a Wisdom-based DC would be 11."""

    _runtime, engine, instance = _instance()
    character = instance.get_character_sheet("gm")["ruleset_character"]
    effects = {effect.id: effect for effect in cunning_strike_options(engine.features, character)}
    assert effects["trip"].save_dc_ability == "dex"
    attacker = {"proficiency_bonus": 3, "abilities": {"str": 8, "dex": 20, "wis": 8}}
    assert engine._declared_save_dc(attacker, effects["trip"].plan_row(), 0) == 16
    assert engine._declared_save_dc(attacker, {"save_dc_ability": "wis"}, 0) == 10


def test_attack_state_options_project_the_cunning_strike_menu() -> None:
    _runtime, engine, instance = _instance(level=5)
    actor = engine._actor_view(instance, _combat(instance), "player:gm")
    options = engine._attack_state_options(actor, _combat(instance))
    cunning = options["cunning_strike"]
    assert cunning["effects"] == ["poison", "trip", "withdraw"]
    assert cunning["count"] == 1
    assert cunning["costs"] == {"poison": 1, "trip": 1, "withdraw": 1}
    assert cunning["dice"] == 3


def test_a_fourteenth_level_rogue_sees_the_devious_strikes() -> None:
    _runtime, engine, instance = _instance(level=14)
    actor = engine._actor_view(instance, _combat(instance), "player:gm")
    cunning = engine._attack_state_options(actor, _combat(instance))["cunning_strike"]
    assert cunning["effects"][-3:] == ["daze", "knock_out", "obscure"]
    assert cunning["count"] == 2
    assert cunning["dice"] == 7


# --- Poison and Obscure: the condition family -------------------------------


def test_poison_needs_a_poisoners_kit() -> None:
    _runtime, engine, instance = _instance()
    _grant_advantage(instance)
    intent = {
        "intent_id": "attack-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
        "attack_declarations": ["cunning_strike"],
        "cunning_strike_effects": ["poison"],
    }
    assert "poisoners" in _rejected(engine, instance, intent)


def test_poison_applies_for_one_minute_with_a_repeat_save() -> None:
    _runtime, engine, instance = _instance(
        equipment=("item:dagger", "item:poisoners_kit"),
    )
    _grant_advantage(instance)
    _attack(engine, instance, rolls=[15] + [3] * 12, effects=["poison"])

    poisoned = _enemy_conditions(instance)["poisoned"]
    assert poisoned["duration"] == "one_minute"
    assert poisoned["turns_remaining"] == 10
    assert poisoned["save_repeat"] == {"ability": "con", "dc": 14}


def test_the_repeat_save_ends_poison_on_a_success() -> None:
    _runtime, engine, instance = _instance(
        equipment=("item:dagger", "item:poisoners_kit"),
    )
    _grant_advantage(instance)
    _attack(engine, instance, rolls=[15] + [3] * 12, effects=["poison"])
    assert "poisoned" in _enemy_conditions(instance)

    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1", rolls=[20])

    assert "poisoned" not in _enemy_conditions(instance)


def test_poison_expires_after_ten_failed_saves() -> None:
    _runtime, engine, instance = _instance(
        equipment=("item:dagger", "item:poisoners_kit"),
    )
    _grant_advantage(instance)
    _attack(engine, instance, rolls=[15] + [3] * 12, effects=["poison"])

    for _ in range(10):
        _end_turn(engine, instance, "player:gm")
        _end_turn(engine, instance, "enemy:goblin-1", rolls=[1])

    assert "poisoned" not in _enemy_conditions(instance)


def test_obscure_blinds_until_the_end_of_the_targets_next_turn() -> None:
    _runtime, engine, instance = _instance(level=14)
    _grant_advantage(instance)
    _attack(engine, instance, rolls=[15] + [3] * 20, effects=["obscure"])

    blinded = _enemy_conditions(instance)["blinded"]
    assert blinded["duration"] == "target_turn_end"


# --- Withdraw: one provoke-free move ----------------------------------------


def test_withdraw_grants_half_speed_and_no_opportunity_attack() -> None:
    _runtime, engine, instance = _instance()
    _grant_advantage(instance)
    events = _attack(engine, instance, rolls=list(HIT), effects=["withdraw"])

    granted = next(
        event for event in events if event["type"] == "dnd2024.movement.granted"
    )
    assert granted["amount"] == 15
    assert granted["provokes_opportunity"] is False
    allowance = _combat(instance)["economy"]["move_allowance"]
    assert allowance["amount"] == 15
    assert allowance["reason"] == "cunning_strike_withdraw"


def test_the_withdraw_allowance_covers_one_move_only() -> None:
    _runtime, engine, instance = _instance()
    _grant_advantage(instance)
    _attack(engine, instance, rolls=list(HIT), effects=["withdraw"])
    _grant_advantage(instance)

    first = _apply(engine, instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "distance": 10,
    }, [])
    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in first
    )
    moved = next(
        event for event in first if event["type"] == "dnd2024.position.changed"
    )
    assert moved["move_allowance"] is True

    second = _apply(engine, instance, {
        "intent_id": "move-2", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "distance": 5,
    }, [])
    assert any(
        event["type"] == "dnd2024.decision.pending" for event in second
    )


# --- Knock Out and Daze: the devious strikes --------------------------------


def test_knock_out_empties_the_pool_and_still_uses_sneak_attack_once() -> None:
    """The 7d6 pool can be spent to zero; the per-turn ledger must survive it."""

    _runtime, engine, instance = _instance(level=14)
    _grant_advantage(instance)
    events = _attack(
        engine, instance, rolls=[15] + [3] * 30,
        effects=["knock_out", "withdraw"],
    )

    damage = _damage(events)
    assert len(damage["rolls"]) == 1
    assert len(_sneak_events(events)) == 1
    assert "unconscious" in _enemy_conditions(instance)


def test_knock_out_ends_when_the_target_takes_damage() -> None:
    _runtime, engine, instance = _instance(level=14)
    _grant_advantage(instance)
    _attack(engine, instance, rolls=[15] + [3] * 30, effects=["knock_out"])
    assert _enemy_conditions(instance)["unconscious"]["ends_on_damage"] is True


def test_a_dazed_target_can_only_do_one_thing_on_its_turn() -> None:
    _runtime, engine, instance = _instance(level=14)
    _grant_advantage(instance)
    _attack(engine, instance, rolls=[15] + [3] * 20, effects=["daze"])
    assert "dazed" in _enemy_conditions(instance), _enemy_conditions(instance)
    _end_turn(engine, instance, "player:gm")
    assert "dazed" in _enemy_conditions(instance), _enemy_conditions(instance)

    # Toward the player, not away: leaving a threatened square would open an
    # Opportunity Attack window and the move would not settle.
    _apply(engine, instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1", "distance": -5,
    }, [])
    assert _combat(instance)["economy"]["moved"] is True
    assert _enemy_conditions(instance)["dazed"].get("turn_choice_limit") == [
        "move", "action", "bonus_action",
    ], _enemy_conditions(instance)["dazed"]
    batch = engine.resolve_intent(instance, {
        "intent_id": "goblin-attack", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1",
        "target_id": "player:gm", "attack_id": "scimitar",
    }, SequenceRng([15] + [3] * 10))
    assert batch["ok"] is False, batch
    assert "already chose" in str(batch.get("error") or "")
