"""S0-MM3c: a save-based action, its two outcomes, and its usage budget."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


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
        game_key=("test", "dnd2024-save-action", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "stalwart_guardian", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox()), instance


def _action(*, dc: int = 11, on_success: list[dict] | None = None,
            usage: dict | None = None, ability: str = "dex") -> dict:
    action = {
        "id": "save-0", "name": "Fire Breath", "kind": "save_action",
        "ability": ability, "dc": dc,
        "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}],
    }
    if on_success is not None:
        action["on_success"] = on_success
    if usage is not None:
        action["usage_limits"] = usage
    return action


def _enemy(action: dict) -> dict:
    return {
        "id": "wyrmling-1", "name": "Wyrmling", "hp": 30, "armor_class": 16,
        "speed": 30, "position": 30, "initiative_modifier": 10, "size": "medium",
        "abilities": {"str": 15, "dex": 10, "con": 13, "int": 10, "wis": 11, "cha": 13},
        "saving_throws": {},
        "attacks": [],
        "actions": [action],
    }


def _events(engine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _start(engine, instance, enemy: dict) -> list[dict]:
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [enemy],
    }
    return _events(engine, instance, intent, SequenceRng([1, 20]))


def _breathe(engine, instance, *, rolls: list[int], intent_id: str = "breath-1",
             expect_ok: bool = True) -> dict:
    intent = {
        "intent_id": intent_id, "type": "save_action",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:wyrmling-1",
        "target_id": "player:gm", "action_id": "save-0",
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(rolls))
    if not expect_ok:
        return resolved
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _hits(events: list[dict]) -> list[dict]:
    return [
        event for event in _of_type(events, "resource.changed")
        if event.get("resource") == "hp" and event.get("target_id") == "player:gm"
    ]


def _player_hp(instance) -> int:
    sheet = instance.players["gm"]["character_sheet"]["ruleset_character"]
    return int(sheet["resources"]["hp"])


# 1. 失败：全额 --------------------------------------------------------------


def test_a_failed_save_takes_the_whole_damage() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(_action()))

    batch = _breathe(engine, instance, rolls=[1, 3, 3, 3, 3])

    check = next(row for row in _of_type(batch["events"], "check.resolved")
                 if row["kind"] == "saving_throw")
    assert check["success"] is False
    hits = _hits(batch["events"])
    assert len(hits) == 1
    assert hits[0]["amount"] == 12
    assert len(hits[0]["rolls"]) == 4


# 2. 成功：独立骰池，不是把失败值除以 2 --------------------------------------


def test_a_successful_save_rolls_the_declared_half_pool() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(_action(
        on_success=[{"damage": [{"dice": "2d6", "type": "fire"}]}],
    )))

    batch = _breathe(engine, instance, rolls=[20, 3, 3])

    check = next(row for row in _of_type(batch["events"], "check.resolved")
                 if row["kind"] == "saving_throw")
    assert check["success"] is True
    hits = _hits(batch["events"])
    # 2 颗骰而不是 4 颗：成功分支掷的是自己的骰池。
    assert (len(hits[0]["rolls"]), hits[0]["amount"]) == (2, 6)


def test_an_odd_pool_can_declare_a_halved_roll() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(_action(
        on_success=[{"damage": [{"dice": "3d4", "type": "cold", "half": True}]}],
    )))

    batch = _breathe(engine, instance, rolls=[20, 3, 3, 4])

    hits = _hits(batch["events"])
    # 3+3+4 = 10，折半向下取整 5：独立掷、独立折半，不复用失败分支的骰值。
    assert (hits[0]["damage_type"], hits[0]["amount"]) == ("cold", 5)


def test_a_halved_flag_inside_the_failed_branch_does_not_halve_a_failed_save() -> None:
    """``half`` means "half on a success"; a failed save is always the whole roll."""
    engine, instance = _instance()
    _start(engine, instance, _enemy({
        **_action(),
        "on_fail": [{"damage": [{"dice": "4d6", "type": "fire", "half": True}]}],
    }))

    batch = _breathe(engine, instance, rolls=[1, 3, 3, 3, 3])

    check = next(row for row in _of_type(batch["events"], "check.resolved")
                 if row["kind"] == "saving_throw")
    assert check["success"] is False
    hits = _hits(batch["events"])
    assert (len(hits[0]["rolls"]), hits[0]["amount"]) == (4, 12)


# 3. 契约 --------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        {"id": "save-0", "name": "X", "kind": "save_action", "dc": 11,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}]},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "luck", "dc": 11,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}]},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 31,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}]},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 0,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}]},
        # 失败分支只允许伤害：多一个条件就是本片表达不了的形状。
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 11,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}],
                      "condition": "prone"}]},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 11,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "screaming"}]}]},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 11,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}],
         "usage_limits": {"recharge": {"from": 1, "to": 2}}},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 11,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}],
         "usage_limits": {"per_day": 0}},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 11,
         "on_fail": [{"damage": [{"dice": "4d6", "type": "fire"}]}],
         "usage_limits": {"recharge": {"from": 5, "to": 6}, "per_day": 2}},
        {"id": "save-0", "name": "X", "kind": "save_action", "ability": "dex", "dc": 11},
    ],
)
def test_a_malformed_save_action_is_refused(action: dict) -> None:
    engine, instance = _instance()
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_enemy(action)],
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([1, 20]))

    assert resolved["ok"] is False


# 4. recharge ----------------------------------------------------------------


def test_a_recharge_action_is_unavailable_until_it_comes_back() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(_action(usage={"recharge": {"from": 5, "to": 6}})))

    _breathe(engine, instance, rolls=[20])
    usage = ruleset_runtime.state(instance)["combat"]["enemies"]["wyrmling-1"]["usage"]
    assert usage["save-0"] == {"used": 1, "available": False}
    before = _player_hp(instance)
    _give_action_back(instance)

    refused = _breathe(engine, instance, rolls=[1], intent_id="breath-2", expect_ok=False)

    assert refused["ok"] is False
    assert "not available" in refused["error"]
    # 不可用时不消耗动作，也不掷任何骰子。
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 1
    assert _player_hp(instance) == before


def test_the_recharge_roll_happens_at_the_owners_turn_start() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(_action(usage={"recharge": {"from": 5, "to": 6}})))
    _breathe(engine, instance, rolls=[20])

    # 龙的回合结束 → 玩家 → 玩家结束 → 又轮到龙，此时掷回骰（6 点 ≥ 5）。
    _end_turn(engine, instance, "enemy:wyrmling-1", rolls=[])
    _end_turn(engine, instance, "player:gm", rolls=[6])

    events = ruleset_runtime.event_ledger(instance)
    rolls = [
        event for batch in events for event in batch.get("events") or []
        if event.get("type") == "dnd2024.action.recharge_rolled"
    ]
    assert [(row["roll"], row["from"]) for row in rolls] == [(6, 5)]
    usage = ruleset_runtime.state(instance)["combat"]["enemies"]["wyrmling-1"]["usage"]
    assert usage["save-0"]["available"] is True


def _end_turn(engine, instance, actor_id: str, *, rolls: list[int]) -> list[dict]:
    intent = {
        "intent_id": f"end-{actor_id}-{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": actor_id,
    }
    return _events(engine, instance, intent, SequenceRng(list(rolls)))


# 5. per_day -----------------------------------------------------------------


def test_a_per_day_action_runs_out() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(_action(usage={"per_day": 2})))

    _breathe(engine, instance, rolls=[20], intent_id="breath-1")
    # 恢复动作位：这里只测使用次数，不测经济，直接把回合还给龙。
    _give_action_back(instance)
    _breathe(engine, instance, rolls=[20], intent_id="breath-2")
    _give_action_back(instance)

    refused = _breathe(engine, instance, rolls=[1], intent_id="breath-3", expect_ok=False)

    assert refused["ok"] is False
    assert "not available" in refused["error"]
    usage = ruleset_runtime.state(instance)["combat"]["enemies"]["wyrmling-1"]["usage"]
    assert usage["save-0"] == {"used": 2, "available": False}


def _give_action_back(instance) -> None:
    ruleset_runtime.state(instance)["combat"]["economy"]["action"] = 1


# 存档往返：新的 usage / actions / traits 都要能读回来 ----------------------


def _reloaded(instance):
    return GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))


def test_usage_survives_a_save_and_load_round_trip() -> None:
    engine, instance = _instance()
    enemy = _enemy(_action(usage={"recharge": {"from": 5, "to": 6}}))
    enemy["traits"] = [{"id": "flyby"}]
    _start(engine, instance, enemy)
    _breathe(engine, instance, rolls=[20])

    loaded = _reloaded(instance)

    alive = ruleset_runtime.state(loaded)["combat"]["enemies"]["wyrmling-1"]
    assert alive["usage"]["save-0"] == {"used": 1, "available": False}
    assert alive["actions"][0]["kind"] == "save_action"
    assert alive["traits"] == [{"id": "flyby"}]
    # 读档后仍然不可用：可用性不是现算的，它来自 reducer 记下的账。
    refused = _breathe(engine, loaded, rolls=[1], intent_id="after-load", expect_ok=False)
    assert refused["ok"] is False


def test_a_save_outcome_may_apply_a_condition_not_just_damage() -> None:
    """S0-EFFECT1 P3：结果行走同一套效果项——条件也是结果。

    Constrictor Snake 的 "Failure: 7 (3d4) Bludgeoning damage, and the target has
    the Grappled condition (escape DC 12)" 就是这种形状；它不需要新原语，
    只需要结果行能装下条件。
    """

    engine, instance = _instance()
    action = {
        "id": "save-0", "name": "Constrict", "kind": "save_action",
        "ability": "str", "dc": 12,
        "on_fail": [
            {"damage": [{"dice": "3d4", "type": "bludgeoning"}]},
            {"condition": "grappled", "escape_dc": 12},
        ],
    }
    _start(engine, instance, _enemy(action))

    batch = _breathe(engine, instance, rolls=[1, 3, 1, 1, 1])

    conditions = [row for row in _of_type(batch["events"], "condition.applied")]
    assert [row["condition"] for row in conditions] == ["grappled"]
    assert conditions[0]["escape_dc"] == 12
    assert len(_hits(batch["events"])) == 1
