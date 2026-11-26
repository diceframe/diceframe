"""Canonical HP writes must refresh the legacy sheet's health display.

The frontend reads ``sheet.resources.hp`` before the canonical character, so a
combat or exploration writeback that only moved ``ruleset_character`` left the
Health bar showing full HP while rest saw zero, or vice versa.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.commands.state_update_applier import StateUpdateApplier
from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.exploration import Dnd2024ExplorationEngine
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import stable_batch_id

from tests.rulesets.test_dnd2024_combat import _character, _instance, _start


def _batch(engine, instance, events: list[dict], tag: str) -> dict:
    state = engine.initialize_state(instance)
    version = int(state.get("version", 0) or 0)
    intent = {"intent_id": f"{tag}:{version}", "type": f"test.{tag}"}
    return {
        "batch_id": stable_batch_id(intent, version),
        "intent_id": intent["intent_id"],
        "intent_type": intent["type"],
        "expected_version": version,
        "result_version": version + 1,
        "events": events,
    }


def _damage_to_zero(engine, instance) -> None:
    applied = engine.apply_batch(instance, _batch(engine, instance, [{
        "type": "resource.changed", "resource": "hp",
        "target_id": "player:gm", "delta": -999, "healing": False,
    }], "damage"))
    assert applied["applied"] is True


def _death_save(engine, instance, **outcome) -> None:
    events = [{
        "type": "dnd2024.death_save.resolved", "actor_id": "player:gm",
        "roll": outcome.get("roll", 10),
        "successes": outcome.get("successes", 0),
        "failures": outcome.get("failures", 0),
        "stable": outcome.get("stable", False),
        "dead": outcome.get("dead", False),
        "hp": outcome.get("hp", 0),
    }]
    applied = engine.apply_batch(instance, _batch(engine, instance, events, "death-save"))
    assert applied["applied"] is True


def applier(runtime: Dnd2024Runtime) -> StateUpdateApplier:
    rules = Path(__file__).resolve().parents[2] / "templates/rules"
    return StateUpdateApplier(
        rules, None, lambda *_: {"default_rule": "dnd2024_srd"},
        ruleset_registry=SimpleNamespace(resolve=lambda _: runtime),
    )


def test_combat_damage_to_zero_refreshes_sheet_health_display() -> None:
    engine, instance = _instance()
    sheet = instance.get_character_sheet("gm")
    max_hp = int(sheet["ruleset_character"]["resources"]["max_hp"])
    sheet["resources"] = {
        "hp": {"label": "Life", "min": 0, "current": max_hp, "max": max_hp},
        "stamina": {"label": "Stamina", "min": 0, "current": 3, "max": 3},
    }
    sheet["inventory"] = [{"name": "Gift", "qty": 1}]
    sheet["gold"] = 321
    instance.set_character_sheet("gm", sheet)
    _start(engine, instance)

    _damage_to_zero(engine, instance)

    updated = instance.get_character_sheet("gm")
    canonical = updated["ruleset_character"]
    assert canonical["resources"]["hp"] == 0
    assert updated["hp"] == updated["resources"]["hp"]["current"] == 0
    assert updated["resources"]["hp"]["label"] == "Life"
    assert updated["resources"]["stamina"] == {
        "label": "Stamina", "min": 0, "current": 3, "max": 3,
    }
    assert updated["inventory"] == [{"name": "Gift", "qty": 1}]
    assert updated["gold"] == 321
    assert updated["status"] == "downed"
    assert updated["deceased"] is False
    assert updated["death_saves"] == canonical["conditions"]["death_saves"]


def test_stabilized_death_save_keeps_zero_display_and_syncs_the_tally() -> None:
    engine, instance = _instance()
    _start(engine, instance)
    _damage_to_zero(engine, instance)

    _death_save(engine, instance, successes=3, failures=0, stable=True)

    updated = instance.get_character_sheet("gm")
    canonical = updated["ruleset_character"]
    assert canonical["resources"]["hp"] == 0
    assert updated["hp"] == updated["resources"]["hp"]["current"] == 0
    assert updated["status"] == "downed"
    assert updated["death_saves"] == canonical["conditions"]["death_saves"] == {
        "successes": 3, "failures": 0,
    }


def test_natural_twenty_death_save_restores_one_hp_in_every_view() -> None:
    engine, instance = _instance()
    _start(engine, instance)
    _damage_to_zero(engine, instance)

    _death_save(engine, instance, roll=20, hp=1)

    updated = instance.get_character_sheet("gm")
    canonical = updated["ruleset_character"]
    assert canonical["resources"]["hp"] == 1
    assert "unconscious" not in canonical["conditions"]
    assert updated["hp"] == updated["resources"]["hp"]["current"] == 1
    assert updated["status"] == "active"
    assert updated["deceased"] is False


def test_narrative_heal_after_zero_agrees_with_canonical_and_unlocks_long_rest() -> None:
    engine, instance = _instance()
    _damage_to_zero(engine, instance)

    runtime = Dnd2024Runtime()
    applier(runtime).apply_state_update(instance, {"players": {"gm": {"hp_change": 5}}})

    healed = instance.get_character_sheet("gm")
    canonical = healed["ruleset_character"]
    assert canonical["resources"]["hp"] == 5
    assert healed["hp"] == healed["resources"]["hp"]["current"] == 5
    assert healed["status"] == "active"
    assert healed["deceased"] is False
    assert "unconscious" not in canonical["conditions"]
    assert healed["death_saves"] == canonical["conditions"]["death_saves"]
    rested = Dnd2024RestEngine(runtime.load_bundle("en")).complete_long_rest(canonical)
    assert rested["character"]["resources"]["hp"] == rested["character"]["resources"]["max_hp"]


class NoRollRng:
    def randint(self, minimum: int, maximum: int) -> int:
        raise AssertionError(f"no dice expected, got randint({minimum}, {maximum})")


def _exploration_instance() -> tuple[Dnd2024Runtime, GameInstance, Dnd2024ExplorationEngine]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-health-display", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    caster = _character(runtime, "kindly_bulwark", "Caster")
    instance.players["gm"] = {"character_name": "Caster", "character_sheet": caster}
    class_magic = caster["ruleset_character"]["spellcasting"]["class"]
    class_magic.setdefault("prepared_spell_refs", []).append("spell:revivify")
    class_magic["slots_current"] = {"3": 1}
    ally = _character(runtime, "stalwart_guardian", "Fallen Ally")
    ally["ruleset_character"]["resources"]["hp"] = 0
    ally["ruleset_character"]["conditions"] = {
        "dead": {"source": "death_saves"},
        "death_saves": {"successes": 0, "failures": 3},
    }
    instance.players["ally"] = {"character_name": "Fallen Ally", "character_sheet": ally}
    assert instance.bind_ruleset_runtime(caster["rule_binding"])
    return runtime, instance, Dnd2024ExplorationEngine(runtime.load_bundle("en"), locale="en")


def test_exploration_revive_refreshes_the_sheet_health_display() -> None:
    _, instance, engine = _exploration_instance()
    stale = instance.get_character_sheet("ally")
    assert stale["hp"] > 0
    assert stale["ruleset_character"]["resources"]["hp"] == 0

    resolved = engine.resolve_intent(instance, {
        "intent_id": "revive-ally", "type": "exploration.cast_spell",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:revivify", "slot_level": 3, "target_ids": ["player:ally"],
    }, NoRollRng())
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True

    revived = instance.get_character_sheet("ally")
    canonical = revived["ruleset_character"]
    assert canonical["resources"]["hp"] == 1
    assert revived["hp"] == revived["resources"]["hp"]["current"] == 1
    assert revived["status"] == "active"
    assert revived["deceased"] is False
    assert "death_saves" not in revived