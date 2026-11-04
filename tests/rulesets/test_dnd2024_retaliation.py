"""S-P9: Berserker Retaliation as a hit-reaction attack effect.

Every assertion goes through the same authoritative chain the web layer uses:
``resolve_intent`` -> ``apply_batch`` for the enemy attack and the defender's
``decision.resolve``.  Retaliation reuses the generic hit-reaction envelope;
only the choice's ``effect_kind`` differs from Uncanny Dodge's damage
multiplier, so these tests pin down the counterattack event order (full
damage first, then one complete melee attack paid with the reaction).
"""

from __future__ import annotations

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.engine.player_control import set_control
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.reaction_spells import HIT_REACTION_DECISION
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    capability_intent,
    monk_instance,
    start_combat,
)


def _advance(runtime: Dnd2024Runtime, sheet: dict, target_level: int) -> dict:
    """Advance to ``target_level`` picking a legal ability increase every time.

    The shared helper always raises Strength; this preset already starts at 17
    with the background bonus, so the second ASI would exceed 20.  Constitution
    is the deterministic fallback.
    """

    while int(sheet["ruleset_character"]["build"]["level"]) < target_level:
        preview = runtime.preview_advancement(None, sheet, {})
        answers: dict = {}
        for requirement in preview.get("requirements") or []:
            requirement_id = str(requirement["id"])
            if requirement_id == "subclass_ref":
                answers["subclass_ref"] = str(requirement["options"][0]["value"])
            elif requirement_id in {"feat_ref", "epic_boon_ref"}:
                available = [
                    option for option in requirement["options"] if option.get("available")
                ]
                answers[requirement_id] = str(available[0]["value"])
            elif requirement_id == "ability_score_increases":
                answers["ability_score_increases"] = {
                    "con": int(requirement.get("total", 2)),
                }
        if answers:
            preview = runtime.preview_advancement(None, sheet, answers)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, answers)
    return sheet


def _barbarian_sheet(runtime: Dnd2024Runtime, *, level: int = 10) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "relentless_vanguard"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Barbarian"},
    )
    return _advance(runtime, sheet, level) if level > 1 else sheet


def _instance(
    *,
    level: int = 10,
    reactions: int | None = None,
    ai: bool = False,
    position: int = 5,
) -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    sheet = _barbarian_sheet(runtime, level=level)
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance, position=position)
    if reactions is not None:
        ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] = reactions
    if ai:
        set_control(instance, "gm", "ai")
    return engine, instance


def _player(instance: GameInstance) -> dict:
    return instance.get_character_sheet("gm")["ruleset_character"]


def _apply(engine: Dnd2024CombatEngine, instance: GameInstance, intent: dict, rng=None):
    resolved = engine.resolve_intent(instance, intent, rng or SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _end_player_turn(engine: Dnd2024CombatEngine, instance: GameInstance) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": "end-1", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    })


def _enemy_attack(
    engine: Dnd2024CombatEngine, instance: GameInstance, rolls: list[int],
) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": "goblin-1", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "enemy:goblin-1",
        "target_id": "player:gm", "attack_id": "scimitar",
    }, SequenceRng(list(rolls)))


def _hit_once(engine, instance, rolls: list[int] | None = None) -> list[dict]:
    """Advance to the enemy's turn and hit the barbarian for 1d6+2."""

    _end_player_turn(engine, instance)
    return _enemy_attack(engine, instance, rolls or [15, 4])


def _pending(instance: GameInstance) -> dict:
    return next(
        item for item in ruleset_runtime.state(instance)["combat"]["pending_decisions"]
        if item["kind"] == HIT_REACTION_DECISION
    )


def _resolve(engine, instance, rng=None, **extra) -> list[dict]:
    decision = _pending(instance)
    return _apply(engine, instance, {
        "intent_id": "resolve-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"], **extra,
    }, rng)


def _enemy_hp(instance: GameInstance) -> int:
    return int(ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"])


def _reaction_feature_choices(engine, instance, *, amount: int = 6) -> list[dict]:
    """Ask the registry directly, with the attacker's current position."""

    combat = ruleset_runtime.state(instance)["combat"]
    defender = engine._actor_view(instance, combat, "player:gm")
    attacker = engine._actor_view(instance, combat, "enemy:goblin-1")
    return engine._reaction_feature_choices(
        combat, defender, attacker, {"amount": amount}, None,
    )


def _give_bless(instance: GameInstance, uid: str = "gm") -> None:
    """Concentration-owned Bless on the defender, as the real spell applies it."""

    character = _player(instance)
    character.setdefault("conditions", {})["blessed"] = {
        "concentration_owner": f"player:{uid}",
        "source_spell_ref": "spell:bless",
        "source_actor_id": f"player:{uid}",
    }
    magic = character.setdefault("spellcasting", {}).setdefault("class", {})
    magic["concentration"] = {"spell_ref": "spell:bless"}


def _counter_attack_check(events: list[dict]) -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event["kind"] == "attack"
    )


def test_failed_concentration_from_the_hit_removes_bless_before_the_counterattack() -> None:
    engine, instance = _instance()
    _give_bless(instance)
    # Force a genuinely failing save: the preset's +8 CON save would otherwise
    # reach the DC 10 floor even on a natural 1.
    _player(instance)["derived"].setdefault("saving_throws", {})["con"] = 0
    _hit_once(engine, instance, [15, 4])

    rng = SequenceRng([1, 1, 15, 4])
    events = _resolve(
        engine, instance, rng=rng,
        option="feature", feature_id="retaliation",
    )

    assert rng.values == []
    ended = next(
        event for event in events if event["type"] == "dnd2024.concentration.ended"
    )
    assert ended["actor_id"] == "player:gm"
    check = _counter_attack_check(events)
    assert check["check_bonus"] == 0
    assert check["check_bonus_rolls"] == []
    assert _player(instance)["conditions"].get("blessed") is None


def test_successful_concentration_from_the_hit_keeps_bless_for_the_counterattack() -> None:
    engine, instance = _instance()
    _give_bless(instance)
    _hit_once(engine, instance, [15, 4])

    rng = SequenceRng([20, 2, 15, 3, 4])
    events = _resolve(
        engine, instance, rng=rng,
        option="feature", feature_id="retaliation",
    )

    assert rng.values == []
    assert not any(
        event["type"] == "dnd2024.concentration.ended" for event in events
    )
    check = _counter_attack_check(events)
    assert check["check_bonus"] == 3
    assert check["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]
    assert check["total"] == 15 + int(check["modifier"]) + 3
    assert "blessed" in _player(instance)["conditions"]


def test_retaliation_is_offered_by_the_generic_hit_reaction_decision() -> None:
    engine, instance = _instance()

    events = _hit_once(engine, instance)

    pending = next(
        event for event in events if event["type"] == "dnd2024.decision.pending"
    )
    assert pending["kind"] == HIT_REACTION_DECISION
    assert pending["options"] == ["feature", "decline"]
    assert pending["spells"] == []
    assert pending["features"] == [{
        "feature_id": "retaliation",
        "name": "Retaliation",
        "mode": "reaction_defense",
        "effect_kind": "counterattack",
        "counterattack_range": 5,
    }]
    assert pending["damage_event"]["amount"] == 6
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 1


def test_retaliation_lands_the_full_damage_then_counterattacks() -> None:
    engine, instance = _instance()
    _hit_once(engine, instance, [15, 4])
    hp_before = _player(instance)["resources"]["hp"]
    enemy_before = _enemy_hp(instance)

    events = _resolve(
        engine, instance, rng=SequenceRng([15, 4]),
        option="feature", feature_id="retaliation",
    )

    assert [event["type"] for event in events] == [
        "intent.submitted",
        "dnd2024.decision.cleared",
        "resource.changed",
        "dnd2024.reaction.spent",
        "check.resolved",
        "resource.changed",
    ]
    assert events[2]["target_id"] == "player:gm"
    assert events[2]["amount"] == 6
    assert events[3]["actor_id"] == "player:gm"
    assert events[3]["reason"] == "retaliation"
    check = events[4]
    assert check["kind"] == "attack"
    assert check["actor_id"] == "player:gm"
    assert check["target_id"] == "enemy:goblin-1"
    assert check["target"] == 12
    assert check["success"] is True
    assert events[5]["target_id"] == "enemy:goblin-1"
    assert events[5]["amount"] == enemy_before - _enemy_hp(instance)
    assert _player(instance)["resources"]["hp"] == hp_before - 6
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 0


def test_a_missed_retaliation_still_spends_the_reaction() -> None:
    engine, instance = _instance()
    _hit_once(engine, instance, [15, 4])
    enemy_before = _enemy_hp(instance)

    events = _resolve(
        engine, instance, rng=SequenceRng([1, 4]),
        option="feature", feature_id="retaliation",
    )

    assert any(
        event["type"] == "check.resolved" and event["success"] is False
        for event in events
    )
    assert not any(
        event["type"] == "resource.changed" and event["target_id"] == "enemy:goblin-1"
        for event in events
    )
    assert _enemy_hp(instance) == enemy_before
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 0


def test_lethal_damage_leaves_no_retaliation_and_keeps_the_reaction() -> None:
    engine, instance = _instance()
    _player(instance)["resources"]["hp"] = 4

    _hit_once(engine, instance, [15, 4])

    events = _resolve(
        engine, instance, rng=SequenceRng([15, 4]),
        option="feature", feature_id="retaliation",
    )

    assert not any(event["type"] == "dnd2024.reaction.spent" for event in events)
    assert not any(event["type"] == "check.resolved" for event in events)
    assert _player(instance)["resources"]["hp"] == 0
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 1


def test_a_level_nine_barbarian_gets_no_retaliation_decision() -> None:
    engine, instance = _instance(level=9)
    hp_before = _player(instance)["resources"]["hp"]

    events = _hit_once(engine, instance, [15, 4])

    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in events
    )
    assert _player(instance)["resources"]["hp"] == hp_before - 6


def test_a_spent_reaction_removes_the_retaliation_option() -> None:
    engine, instance = _instance(reactions=0)

    events = _hit_once(engine, instance, [15, 4])

    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in events
    )


def test_an_attacker_ten_feet_away_cannot_be_counterattacked() -> None:
    engine, instance = _instance(position=10)

    assert _reaction_feature_choices(engine, instance) == []


def test_raging_retaliation_adds_rage_damage_without_extending_rage() -> None:
    engine, instance = _instance()
    _apply(
        engine, instance,
        capability_intent(engine, instance, "rage", intent_id="rage-1", target_id=""),
    )
    _hit_once(engine, instance, [15, 4])
    enemy_before = _enemy_hp(instance)

    events = _resolve(
        engine, instance, rng=SequenceRng([15, 4]),
        option="feature", feature_id="retaliation",
    )

    counter_damage = next(
        event for event in events
        if event["type"] == "resource.changed" and event["target_id"] == "enemy:goblin-1"
    )
    # 力量 3 + 狂暴加值 3 + 巨斧掷骰 4：狂暴加值随通用伤害结算自然生效。
    assert counter_damage["amount"] == 4 + 3 + 3
    assert counter_damage["rolls"] == [4]
    assert not any(
        event["type"] == "dnd2024.condition.sustained" for event in events
    )
    assert _enemy_hp(instance) == enemy_before - counter_damage["amount"]


def test_ai_hosted_barbarian_retaliates_automatically() -> None:
    engine, instance = _instance(ai=True)
    _hit_once(engine, instance, [15, 4])

    intent = engine.next_automatic_intent(instance)

    assert intent["type"] == "decision.resolve"
    assert intent["option"] == "feature"
    assert intent["feature_id"] == "retaliation"
    events = _apply(engine, instance, intent, SequenceRng([15, 4]))
    assert any(
        event["type"] == "resource.changed" and event["target_id"] == "enemy:goblin-1"
        for event in events
    )
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 0


def test_zero_damage_never_offers_retaliation() -> None:
    engine, instance = _instance()

    assert _reaction_feature_choices(engine, instance, amount=0) == []


def test_temp_hp_absorbing_the_hit_still_allows_retaliation() -> None:
    engine, instance = _instance()
    player = _player(instance)
    player["resources"]["hp"] = 4
    player["resources"]["temp_hp"] = 10
    enemy_before = _enemy_hp(instance)

    _hit_once(engine, instance, [15, 4])
    events = _resolve(
        engine, instance, rng=SequenceRng([15, 4]),
        option="feature", feature_id="retaliation",
    )

    assert any(
        event["type"] == "dnd2024.reaction.spent"
        and event["reason"] == "retaliation"
        for event in events
    )
    assert any(
        event["type"] == "check.resolved" and event["kind"] == "attack"
        for event in events
    )
    resources = _player(instance)["resources"]
    assert resources["hp"] == 4
    assert resources["temp_hp"] == 4
    assert _enemy_hp(instance) < enemy_before
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 0


def test_insufficient_temp_hp_still_prevents_the_counterattack() -> None:
    engine, instance = _instance()
    player = _player(instance)
    player["resources"]["hp"] = 4
    player["resources"]["temp_hp"] = 2

    _hit_once(engine, instance, [15, 4])
    events = _resolve(
        engine, instance, rng=SequenceRng([15, 4]),
        option="feature", feature_id="retaliation",
    )

    assert not any(
        event["type"] == "dnd2024.reaction.spent" for event in events
    )
    assert not any(
        event["type"] == "check.resolved" and event["kind"] == "attack"
        for event in events
    )
    resources = _player(instance)["resources"]
    assert resources["hp"] == 0
    assert resources["temp_hp"] == 0
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 1
