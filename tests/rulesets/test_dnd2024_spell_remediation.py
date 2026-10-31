"""Post-review remediation tests for P1–P9 / R1.

Package A: reaction eligibility and the Counterspell decision chain.
Package B: declarative Self targets and the spell projection/UI/P2P contract.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.game_instance import GameState
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, monk_instance
from test_dnd2024_counterspell import (
    cast,
    combat_of,
    enemy,
    new_combat,
    pending_of,
    resolve_decision,
    set_turn,
    slots,
    state,
    wizard_sheet,
)
from test_dnd2024_false_life import (
    catalog_bundle,
    start_combat,
    wizard_sheet as false_life_sheet,
)
from test_dnd2024_revivify import ally_sheet


def _combat_with_separate_gm(gm_uid: str = "keeper", dc: int = 15):
    """A real combat where the GM seat is not the acting player."""

    runtime = Dnd2024Runtime()
    sheet = wizard_sheet(runtime, level=5)
    engine, instance = monk_instance(runtime, sheet, uid="p1")
    instance.gm_uid = gm_uid
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": gm_uid, "enemies": [enemy(dc=dc)],
    }, SequenceRng([20, 1]))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return runtime, engine, instance


def _paralyze(instance, *, actor_id: str = "enemy:goblin-1", code: str = "paralyzed") -> None:
    combat_of(instance)["enemies"]["goblin-1"].setdefault("conditions", {})[code] = {
        "source": "test",
    }


def _attack_first(instance) -> None:
    """Put the party actor first in initiative."""

    combat = combat_of(instance)
    combat["turn_index"] = combat["initiative"].index("player:gm")
    combat["economy"] = {
        "actor_id": "player:gm", "action": 1, "bonus_action": 1,
        "movement": 30, "reaction": 1,
    }


# --- A1 the automatic ladder must not consume a human GM's window --------

def test_automatic_ladder_does_not_decline_a_counter_window() -> None:
    # 独立 GM（自己没有队伍角色）亲手决定反制；GM 自己在队伍里时由服务器
    # 替敌人反制，见 test_dnd2024_counterspell。
    _runtime, engine, instance = new_combat()
    instance.gm_uid = "host"

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])

    assert pending_of(result)[0]["kind"] == "counter_spell"
    assert engine.next_automatic_intent(instance) is None
    assert combat_of(instance)["pending_decisions"][0]["kind"] == "counter_spell"


@pytest.mark.asyncio
async def test_service_keeps_the_counter_decision_pending(tmp_path) -> None:
    from src.engine.game_instance import GameRegistry
    from dnd2024_http_common import GameplayApiShim

    runtime = Dnd2024Runtime()
    registry = GameRegistry(tmp_path / "saves")
    sheet = wizard_sheet(runtime, level=5)
    engine, instance = monk_instance(runtime, sheet, uid="gm")
    instance.game_key = ("web", "counter-service", "web_bot")
    instance.world_id = "greymoor"
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.gm_uid = "host"  # 独立 GM：反制窗口留给他亲手决定
    # 确定性地进入先攻，避免服务层随机地先生成一次被命中反应窗口。
    started = engine.resolve_intent(instance, {
        "intent_id": "service-start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "host", "enemies": [enemy()],
    }, SequenceRng([20, 1]))
    assert started["ok"] is True, started
    assert engine.apply_batch(instance, started["event_batch"])["applied"]
    registry.register(instance)
    api = GameplayApiShim(registry, runtime)
    game_key = "|".join(instance.game_key)

    casted = await api.ruleset_submit_intent(game_key, "gm", False, {
        "intent_id": "service-cast", "type": "cast_spell",
        "expected_version": int(state(instance)["version"]),
        "actor_id": "player:gm", "spell_ref": "spell:fire_bolt",
        "slot_level": 0, "target_ids": ["enemy:goblin-1"],
    })
    assert casted["ok"] is True, casted

    pending = ruleset_runtime.state(instance)["combat"]["pending_decisions"]
    assert [row["kind"] for row in pending] == ["counter_spell"]
    applied_types = [
        event["type"]
        for batch in casted["result"].get("automatic_event_batches") or []
        for event in batch.get("events") or []
    ]
    assert "dnd2024.decision.cleared" not in applied_types


# --- A1 the assigned GM must be able to discover the decision ------------

def test_assigned_gm_finds_the_counter_decision_outside_the_player_turn() -> None:
    _runtime, engine, instance = _combat_with_separate_gm()

    cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], uid="p1")

    actions = engine.available_intents(instance, "keeper")
    decisions = [
        row for row in actions
        if row.get("type") == "decision.resolve"
        and row.get("decisions", [{}])[0].get("kind") == "counter_spell"
    ]
    assert len(decisions) == 1, actions
    assert decisions[0]["decisions"][0]["assigned_to"] == "keeper"


def test_non_assigned_player_cannot_resolve_the_counter_decision() -> None:
    _runtime, engine, instance = _combat_with_separate_gm()
    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], uid="p1")
    decision = pending_of(result)[0]

    rejected = engine.validate_intent(instance, {
        "intent_id": "p1-resolves", "type": "decision.resolve",
        "expected_version": int(state(instance)["version"]),
        "submitted_by": "p1", "decision_id": decision["decision_id"],
        "option": "counter",
    })
    assert rejected["ok"] is False
    assert "assigned GM" in rejected["error"]


# --- A2 incapacitated or reaction-blocked enemies cannot react -----------

@pytest.mark.parametrize("condition", ["paralyzed", "stunned", "unconscious"])
def test_incapacitated_enemy_cannot_open_a_counter_window(condition: str) -> None:
    _runtime, engine, instance = new_combat()
    _paralyze(instance, code=condition)

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])

    assert pending_of(result) == []
    assert combat_of(instance)["pending_decisions"] == []


def test_reaction_blocked_enemy_cannot_open_a_counter_window() -> None:
    _runtime, engine, instance = new_combat()
    _paralyze(instance, code="reaction_blocked")

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])

    assert pending_of(result) == []


@pytest.mark.parametrize("condition", ["paralyzed", "reaction_blocked"])
def test_ineligible_enemy_gets_no_opportunity_attack(condition: str) -> None:
    _runtime, engine, instance = new_combat()
    _attack_first(instance)
    _paralyze(instance, code=condition)

    result = engine.resolve_intent(instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": int(state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "player:gm", "distance": -15,
    }, SequenceRng([]))
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])

    assert result.get("pending_decision") is None
    assert combat_of(instance)["positions"]["player:gm"] == -15
    assert combat_of(instance)["reactions"]["enemy:goblin-1"] == 1


def test_opportunity_attack_is_rechecked_when_the_threat_becomes_incapacitated() -> None:
    _runtime, engine, instance = new_combat()
    _attack_first(instance)

    moved = engine.resolve_intent(instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": int(state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "player:gm", "distance": -15,
    }, SequenceRng([]))
    assert moved["ok"], moved
    engine.apply_batch(instance, moved["event_batch"])
    decision = moved["pending_decision"]
    assert decision["kind"] == "opportunity_attack"

    _paralyze(instance)
    resolved = resolve_decision(engine, instance, decision, "resolve", rolls=[15, 4])

    assert "dnd2024.reaction.spent" not in {
        event["type"] for event in resolved["event_batch"]["events"]
    }
    assert combat_of(instance)["positions"]["player:gm"] == -15
    assert combat_of(instance)["reactions"]["enemy:goblin-1"] == 1


def test_opportunity_attack_is_skipped_when_the_threat_has_no_reaction_left() -> None:
    _runtime, engine, instance = new_combat()
    _attack_first(instance)

    moved = engine.resolve_intent(instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": int(state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "player:gm", "distance": -15,
    }, SequenceRng([]))
    assert moved["ok"], moved
    assert engine.apply_batch(instance, moved["event_batch"])["applied"]
    decision = moved["pending_decision"]
    assert decision["kind"] == "opportunity_attack"

    # 开窗后威胁者耗尽反应：apply_batch 已替换 canonical combat，必须重新
    # 取值再修改，否则改到的是旧快照。
    combat_of(instance)["reactions"]["enemy:goblin-1"] = 0
    resolved = resolve_decision(engine, instance, decision, "resolve", rolls=[15, 4])

    assert "dnd2024.reaction.spent" not in {
        event["type"] for event in resolved["event_batch"]["events"]
    }
    assert combat_of(instance)["pending_decisions"] == []
    assert combat_of(instance)["positions"]["player:gm"] == -15
    assert combat_of(instance)["reactions"]["enemy:goblin-1"] == 0


def test_counter_is_rechecked_when_the_threat_becomes_incapacitated() -> None:
    _runtime, engine, instance = new_combat()

    result = cast(engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"])
    decision = pending_of(result)[0]
    assert decision["kind"] == "counter_spell"

    _paralyze(instance)
    resolved = resolve_decision(engine, instance, decision, "counter", rolls=[15, 5])

    event_types = [event["type"] for event in resolved["event_batch"]["events"]]
    assert "dnd2024.spell.countered" not in event_types
    assert "dnd2024.reaction.spent" not in event_types
    assert "dnd2024.spell.cast" in event_types
    assert combat_of(instance)["reactions"]["enemy:goblin-1"] == 1


# --- B1 declarative Self targets -----------------------------------------

def _false_life_combat() -> tuple[Dnd2024Runtime, object, object]:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, false_life_sheet(runtime, level=1), uid="gm")
    start_combat(engine, instance)
    return runtime, engine, instance


def _add_ally(instance, runtime, uid: str = "p2") -> None:
    instance.players[uid] = {
        "character_name": "Ally",
        "character_sheet": false_life_sheet(runtime, level=1),
    }


def _validate_cast(
    engine, instance, *, spell_ref: str, slot_level: int, targets: list[str],
    **extra,
) -> dict:
    return engine.validate_intent(instance, {
        "intent_id": f"validate:{spell_ref}:{','.join(targets)}",
        "type": "cast_spell",
        "expected_version": int(state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": spell_ref, "slot_level": slot_level,
        "target_ids": list(targets), **extra,
    })


def test_false_life_accepts_only_the_caster() -> None:
    runtime, engine, instance = _false_life_combat()
    _add_ally(instance, runtime)

    accepted = _validate_cast(
        engine, instance, spell_ref="spell:false_life", slot_level=1,
        targets=["player:gm"],
    )
    assert accepted["ok"] is True, accepted

    for target in ("player:p2", "enemy:goblin-1"):
        rejected = _validate_cast(
            engine, instance, spell_ref="spell:false_life", slot_level=1,
            targets=[target],
        )
        assert rejected["ok"] is False, rejected
        assert "allied" in rejected["error"] or "caster" in rejected["error"]

    result = cast(engine, instance, "spell:false_life", 1, ["player:gm"], [2, 3])
    event = next(
        row for row in result["event_batch"]["events"]
        if row["type"] == "resource.changed" and row.get("resource") == "temp_hp"
    )
    assert event["target_id"] == "player:gm"
    assert event["amount"] == 9


@pytest.mark.parametrize("scope", ["ally", "enemy", "any", "", 1, True, None])
def test_catalog_rejects_an_unsupported_target_scope(scope) -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    entry = {
        "mode": "temp_hp", "range": 5, "target_count": 1,
        "temp_hp": "2d4+4", "target_scope": scope,
    }
    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(
            catalog_bundle(bundle, spell_effects={"false_life": entry}),
        )


def test_catalog_accepts_the_declared_self_scope() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    entry = deepcopy(
        bundle.get("combat_catalog", "srd_combat_core")["spell_effects"]["false_life"]
    )
    assert entry["target_scope"] == "self"
    Dnd2024CombatCatalog.from_bundle(
        catalog_bundle(bundle, spell_effects={"false_life": entry}),
    )


def test_misty_step_keeps_the_self_contract() -> None:
    runtime, engine, instance = new_combat(level=5)
    instance.players["p2"] = {
        "character_name": "Ally",
        "character_sheet": wizard_sheet(runtime, level=5),
    }

    accepted = _validate_cast(
        engine, instance, spell_ref="spell:misty_step", slot_level=2,
        targets=["player:gm"], teleport_distance=25,
    )
    assert accepted["ok"] is True, accepted

    for target in ("player:p2", "enemy:goblin-1"):
        rejected = _validate_cast(
            engine, instance, spell_ref="spell:misty_step", slot_level=2,
            targets=[target], teleport_distance=25,
        )
        assert rejected["ok"] is False, rejected

    missing_distance = _validate_cast(
        engine, instance, spell_ref="spell:misty_step", slot_level=2,
        targets=["player:gm"],
    )
    assert missing_distance["ok"] is False
    assert "teleport" in missing_distance["error"]


# --- B2 the projection carries the server-owned target contract ----------

def test_spell_projection_declares_target_requirements() -> None:
    runtime = Dnd2024Runtime()
    sheet = wizard_sheet(runtime, level=5)
    prepared = sheet["ruleset_character"]["spellcasting"]["class"]["prepared_spell_refs"]
    for ref in (
        "spell:false_life", "spell:hold_person", "spell:scorching_ray",
        "spell:protection_from_energy", "spell:dispel_magic",
    ):
        if ref not in prepared:
            prepared.append(ref)
    _runtime, engine, instance = new_combat(level=5, sheet=sheet)

    action = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "cast_spell"
    )
    spells = {row["spell_ref"]: row for row in action["spells"]}

    assert spells["spell:false_life"]["target_scope"] == "self"
    assert spells["spell:false_life"]["target_side"] == "ally"
    assert spells["spell:false_life"]["target_count"] == 1
    assert spells["spell:misty_step"]["target_scope"] == "self"
    assert spells["spell:hold_person"]["target_side"] == "enemy"
    assert spells["spell:hold_person"]["upcast_target_count"] == 1
    assert spells["spell:scorching_ray"]["target_side"] == "enemy"
    assert spells["spell:scorching_ray"]["target_count"] == 3
    assert spells["spell:scorching_ray"]["repeat_targets"] is True
    assert spells["spell:dispel_magic"]["target_side"] == "any"
    assert spells["spell:protection_from_energy"]["resist_choice"] == [
        "acid", "cold", "fire", "lightning", "thunder",
    ]


def test_target_projection_distinguishes_dead_from_zero_hp() -> None:
    runtime, engine, instance = new_combat(level=5)
    dead = ally_sheet(runtime)
    dying = ally_sheet(runtime)
    dying["ruleset_character"]["conditions"].pop("dead")
    instance.players["dead"] = {"character_name": "Fallen", "character_sheet": dead}
    instance.players["dying"] = {"character_name": "Dying", "character_sheet": dying}

    action = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "cast_spell"
    )
    targets = {row["actor_id"]: row for row in action["targets"]}

    assert targets["player:dead"]["hp"] == 0
    assert targets["player:dead"]["is_dead"] is True
    assert targets["player:dying"]["hp"] == 0
    assert targets["player:dying"]["is_dead"] is False
    assert targets["player:dead"]["side"] == "party"
    assert targets["enemy:goblin-1"]["is_dead"] is False

