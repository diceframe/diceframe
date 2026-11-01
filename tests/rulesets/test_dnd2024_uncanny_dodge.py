"""Uncanny Dodge (Rogue 5): reaction-triggered damage halving.

Every assertion goes through the same authoritative chain the web layer uses:
``resolve_intent`` -> ``apply_batch`` for the attack and the defender's
``decision.resolve``.  The decision itself is the generic hit-reaction
envelope, so these tests also pin down that the spell-only path still behaves
exactly as before.
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
    advance,
    monk_instance,
    start_combat,
)


def _rogue_sheet(runtime: Dnd2024Runtime, *, level: int = 5) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "lucky_scout")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Rogue"},
    )
    return advance(runtime, sheet, level) if level > 1 else sheet


def _instance(
    *,
    level: int = 5,
    reactions: int | None = None,
    spellcasting: dict | None = None,
    ai: bool = False,
) -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    sheet = _rogue_sheet(runtime, level=level)
    if spellcasting is not None:
        sheet["ruleset_character"]["spellcasting"] = {"class": spellcasting}
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)
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
    """Advance to the enemy's turn and hit the rogue for 1d6+2 damage."""

    _end_player_turn(engine, instance)
    return _enemy_attack(engine, instance, rolls or [15, 4])


def _pending(instance: GameInstance) -> dict:
    return next(
        item for item in ruleset_runtime.state(instance)["combat"]["pending_decisions"]
        if item["kind"] == HIT_REACTION_DECISION
    )


def _resolve(engine, instance, **extra) -> list[dict]:
    decision = _pending(instance)
    return _apply(engine, instance, {
        "intent_id": "resolve-1", "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "decision_id": decision["decision_id"], **extra,
    })


def _damage(events: list[dict]) -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("target_id") == "player:gm"
    )


def test_a_hit_offers_uncanny_dodge_through_the_generic_reaction_decision() -> None:
    engine, instance = _instance()

    events = _hit_once(engine, instance)

    pending = next(
        event for event in events if event["type"] == "dnd2024.decision.pending"
    )
    assert pending["kind"] == HIT_REACTION_DECISION
    assert pending["options"] == ["feature", "decline"]
    assert pending["spells"] == []
    assert pending["features"] == [{
        "feature_id": "uncanny_dodge",
        "name": "Uncanny Dodge",
        "mode": "reaction_defense",
        "damage_multiplier": 0.5,
    }]
    assert pending["damage_event"]["amount"] == 6
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 1


def test_choosing_uncanny_dodge_spends_the_reaction_and_halves_the_damage() -> None:
    engine, instance = _instance()
    _hit_once(engine, instance, [15, 5])
    hp_before = _player(instance)["resources"]["hp"]

    events = _resolve(engine, instance, option="feature", feature_id="uncanny_dodge")

    damage = _damage(events)
    assert damage["amount"] == 3  # (5 + 2) // 2, rounded down
    assert damage["delta"] == -3
    assert damage["rolls"] == [5]
    assert not any(
        event["type"] == "check.resolved" and event.get("kind") == "reaction_defense"
        for event in events
    )
    assert "dnd2024.decision.cleared" in [event["type"] for event in events]
    assert ("dnd2024.reaction.spent", "reaction_feature") in [
        (event["type"], event.get("reason")) for event in events
    ]
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 0
    assert _player(instance)["resources"]["hp"] == hp_before - 3


def test_declining_keeps_the_reaction_and_the_full_damage() -> None:
    engine, instance = _instance()
    _hit_once(engine, instance, [15, 4])
    hp_before = _player(instance)["resources"]["hp"]

    events = _resolve(engine, instance, option="decline")

    damage = _damage(events)
    assert damage["amount"] == 6
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 1
    assert _player(instance)["resources"]["hp"] == hp_before - 6


def test_a_spent_reaction_removes_the_option_and_applies_full_damage() -> None:
    engine, instance = _instance(reactions=0)

    events = _hit_once(engine, instance, [15, 4])

    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in events
    )
    assert _damage(events)["amount"] == 6
    assert _player(instance)["resources"]["hp"] < 40


def test_ai_hosted_rogue_uses_uncanny_dodge_automatically() -> None:
    engine, instance = _instance(ai=True)
    _hit_once(engine, instance, [15, 4])

    intent = engine.next_automatic_intent(instance)

    assert intent["type"] == "decision.resolve"
    assert intent["option"] == "feature"
    assert intent["feature_id"] == "uncanny_dodge"
    events = _apply(engine, instance, intent)
    assert _damage(events)["amount"] == 3
    assert ruleset_runtime.state(instance)["combat"]["reactions"]["player:gm"] == 0


def test_shield_and_uncanny_dodge_are_both_offered() -> None:
    engine, instance = _instance(spellcasting={
        "ability": "int", "slots_current": {"1": 2},
        "prepared_spell_refs": ["spell:shield"], "cantrip_refs": [],
    })

    events = _hit_once(engine, instance)

    pending = next(
        event for event in events if event["type"] == "dnd2024.decision.pending"
    )
    assert [row["spell_ref"] for row in pending["spells"]] == ["spell:shield"]
    assert [row["feature_id"] for row in pending["features"]] == ["uncanny_dodge"]
    assert pending["options"] == ["cast", "feature", "decline"]


def test_a_level_four_rogue_gets_no_reaction_decision() -> None:
    engine, instance = _instance(level=4)

    events = _hit_once(engine, instance)

    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in events
    )
    assert _damage(events)["amount"] == 6


def test_a_blinded_rogue_is_not_offered_uncanny_dodge() -> None:
    """Uncanny Dodge needs to see the attacker; a blinded rogue just takes it."""

    engine, instance = _instance()
    _player(instance).setdefault("conditions", {})["blinded"] = {}

    events = _hit_once(engine, instance, [15, 4])

    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in events
    )
    assert _damage(events)["amount"] == 6


def test_an_invisible_attacker_cannot_be_dodged() -> None:
    engine, instance = _instance()
    _end_player_turn(engine, instance)
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"].setdefault(
        "conditions", {},
    )["invisible"] = {}

    # P1 起 invisible 进入攻击模式注册表：隐形攻击者的攻击具有优势，
    # 双骰取高后伤害骰继续沿用序列；断言意图不变（无反应决策 + 全额伤害）。
    events = _enemy_attack(engine, instance, [15, 8, 4])

    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in events
    )
    assert _damage(events)["amount"] == 6
