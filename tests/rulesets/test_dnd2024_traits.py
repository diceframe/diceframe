"""S0-MM3d: eight passive traits — declared facts, each with one consumer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.monsters.catalog import MonsterCatalog
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

CATALOG = (
    Path(__file__).resolve().parents[2]
    / "templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json"
)


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _character(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def _instance() -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-traits", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "stalwart_guardian", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox()), instance


def _enemy(traits: list[dict], *, ally: bool = False, attacks: bool = True) -> list[dict]:
    enemies = [{
        "id": "fiend-1", "name": "Fiend", "hp": 40, "armor_class": 13,
        "speed": 30, "position": 5, "initiative_modifier": 10, "size": "medium",
        "abilities": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 12},
        "saving_throws": {},
        "attacks": [{
            "id": "claw-0", "name": "Claw", "attack_bonus": 30,
            "damage": [{"dice": "1d6+2", "type": "slashing"}],
            "range": 5, "long_range": 5,
        }] if attacks else [],
        "traits": traits,
    }]
    if ally:
        enemies.append({
            "id": "fiend-2", "name": "Ally Fiend", "hp": 20, "armor_class": 13,
            # 目标在 0，攻击者在 5：同伙必须也在目标 5 尺内才成立。
            "speed": 30, "position": 5, "initiative_modifier": 0, "size": "medium",
            "abilities": {"str": 12, "dex": 12, "con": 12, "int": 8, "wis": 10, "cha": 8},
            "attacks": [],
        })
    return enemies


def _events(engine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _start(engine, instance, enemies: list[dict]) -> list[dict]:
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": enemies,
    }
    return _events(engine, instance, intent, SequenceRng([1, 20, 5]))


def _enemy_view(engine, instance, actor_id: str = "enemy:fiend-1") -> dict:
    return engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], actor_id)


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _bite_player(engine, instance, *, rolls: list[int], attacker: str = "enemy:fiend-1") -> list[dict]:
    intent = {
        "intent_id": f"attack-{attacker}", "type": "attack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": attacker,
        "target_id": "player:gm", "attack_id": "claw-0",
    }
    return _events(engine, instance, intent, SequenceRng(rolls))


# 每个特质一条最小用例：声明 → 消费点生效 ------------------------------------


def test_amphibious_and_water_breathing_are_readable_facts() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([{"id": "amphibious"}, {"id": "water_breathing"}]))

    view = _enemy_view(engine, instance)

    assert [row["id"] for row in view["traits"]] == ["amphibious", "water_breathing"]


def test_spider_climb_gives_a_climb_speed_equal_to_walking() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([{"id": "spider_climb"}]))

    view = _enemy_view(engine, instance)

    assert view["speeds"] == {"walk": 30, "climb": 30}


def test_amorphous_is_a_movement_fact_with_no_number() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([{"id": "amorphous"}]))

    assert "amorphous" in {row["id"] for row in _enemy_view(engine, instance)["traits"]}


def test_pack_tactics_needs_an_ally_beside_the_target() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([{"id": "pack_tactics"}], ally=True))

    events = _bite_player(engine, instance, rolls=[15, 4])

    check = next(row for row in _of_type(events, "check.resolved") if row["kind"] == "attack")
    assert "pack_tactics" in {row["code"] for row in check["advantage_sources"]}
    assert check["advantage_mode"] == "advantage"


def test_pack_tactics_alone_gives_nothing() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([{"id": "pack_tactics"}]))

    events = _bite_player(engine, instance, rolls=[15, 4])

    check = next(row for row in _of_type(events, "check.resolved") if row["kind"] == "attack")
    assert check["advantage_mode"] == ""
    assert check["advantage_sources"] == []


def test_flyby_keeps_the_opportunity_attack_window_closed() -> None:
    """A creature that flies out of reach does not provoke — so no window opens."""

    engine, instance = _instance()
    _start(engine, instance, _enemy([{"id": "flyby"}]))

    events = _move(engine, instance, distance=20, rolls=[])

    assert not _of_type(events, "dnd2024.decision.pending")
    assert _of_type(events, "dnd2024.position.changed")


def test_without_flyby_leaving_reach_does_provoke() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([]))

    events = _move(engine, instance, distance=20, rolls=[])

    pending = _of_type(events, "dnd2024.decision.pending")
    assert [row["kind"] for row in pending] == ["opportunity_attack"]


def _move(engine, instance, *, distance: int, rolls: list[int]) -> list[dict]:
    intent = {
        "intent_id": f"move-{ruleset_runtime.state(instance)['version']}",
        "type": "move",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:fiend-1", "distance": distance,
    }
    return _events(engine, instance, intent, SequenceRng(list(rolls)))


def _end_turn(engine, instance, actor_id: str, *, rolls: list[int]) -> list[dict]:
    intent = {
        "intent_id": f"end-{actor_id}-{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": actor_id,
    }
    return _events(engine, instance, intent, SequenceRng(list(rolls)))


# magic resistance -----------------------------------------------------------


def test_magic_resistance_only_helps_against_spells() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([{"id": "magic_resistance"}]))
    combat = ruleset_runtime.state(instance)["combat"]
    view = _enemy_view(engine, instance)

    spell_save = engine._saving_throw_check(
        instance, combat, view, "enemy:fiend-1", "dex", 15, "player:gm",
        [], SequenceRng([3, 18]), against_spell=True,
    )
    plain_save = engine._saving_throw_check(
        instance, combat, view, "enemy:fiend-1", "dex", 15, "player:gm",
        [], SequenceRng([3]),
    )

    assert spell_save["advantage_mode"] == "advantage"
    assert [row["code"] for row in spell_save["advantage_sources"]] == ["magic_resistance"]
    # 非法术的豁免（这里是一次呼吸武器式豁免）不受影响：只掷一颗骰。
    assert plain_save.get("advantage_mode") is None
    assert plain_save["rolls"] == [3]


# legendary resistance -------------------------------------------------------


def test_legendary_resistance_turns_a_failure_into_a_success() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([
        {"id": "legendary_resistance", "params": {"per_day": 2}},
    ]))
    combat = ruleset_runtime.state(instance)["combat"]
    view = _enemy_view(engine, instance)
    events: list[dict] = []

    check = engine._saving_throw_check(
        instance, combat, view, "enemy:fiend-1", "dex", 25, "player:gm",
        events, SequenceRng([3]),
    )

    assert check["success"] is True
    assert check["legendary_resistance"] == 1
    assert [row["type"] for row in events] == ["dnd2024.legendary_resistance.spent"]


def test_legendary_resistance_runs_out() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy([
        {"id": "legendary_resistance", "params": {"per_day": 1}},
    ]))
    combat = ruleset_runtime.state(instance)["combat"]
    view = _enemy_view(engine, instance)
    first = engine._saving_throw_check(
        instance, combat, view, "enemy:fiend-1", "dex", 25, "player:gm",
        [], SequenceRng([3]),
    )
    assert first["success"] is True
    # 支出记账由 reducer 完成；这里把它落到状态上，模拟真实批次。
    _apply_spend(engine, instance)

    refreshed = _enemy_view(engine, instance)
    second = engine._saving_throw_check(
        instance, ruleset_runtime.state(instance)["combat"], refreshed,
        "enemy:fiend-1", "dex", 25, "player:gm", [], SequenceRng([3]),
    )

    assert second["success"] is False
    assert "legendary_resistance" not in second


def _apply_spend(engine, instance) -> None:
    version = int(ruleset_runtime.state(instance)["version"])
    batch = {
        "batch_id": f"batch_{version}", "intent_id": f"spend:{version}",
        "intent_type": "combat.message", "expected_version": version,
        "result_version": version + 1, "source_ref": "test",
        "events": [
            {"type": "intent.submitted", "intent_type": "combat.message",
             "actor_id": "player:gm", "submitted_by": "gm"},
            {"type": "dnd2024.legendary_resistance.spent",
             "actor_id": "enemy:fiend-1", "per_day": 1},
        ],
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


# 契约 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "traits",
    [
        [{"id": "darkvision_60"}],
        [{"id": "magic_resistance", "params": {"per_day": 3}}],
        [{"id": "legendary_resistance", "params": {"per_day": 0}}],
        [{"id": "legendary_resistance", "params": {"per_day": 9}}],
        [{"id": "amphibious"}, {"id": "amphibious"}],
        [{"id": "pack_tactics", "desc": "free text"}],
        "magic_resistance",
    ],
)
def test_an_unknown_or_malformed_trait_is_refused(traits) -> None:
    engine, instance = _instance()
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": _enemy(traits),
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([1, 20]))

    assert resolved["ok"] is False


def test_the_catalog_traits_all_load_and_are_known() -> None:
    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))

    counts: dict[str, int] = {}
    for entry in catalog.all():
        for trait in entry.get("traits") or []:
            counts[trait["id"]] = counts.get(trait["id"], 0) + 1
    # 八个闭集 id 里，CR 0-1 覆盖到七个（没有传奇生物在这一段）。
    assert counts == {
        "pack_tactics": 14, "water_breathing": 7, "amphibious": 6,
        "magic_resistance": 6, "flyby": 6, "spider_climb": 6, "amorphous": 2,
    }
