"""Monk combat capabilities: Martial Arts, Bonus Unarmed Strike, Focus actions.

Every assertion here goes through the same authoritative chain the web layer
uses: ``available_intents`` -> ``validate_intent`` -> ``resolve_intent`` ->
``apply_batch``.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import EventBatchError

from dnd2024_monk_common import (
    SequenceRng,
    advance,
    capability_action,
    capability_actions,
    capability_intent,
    focus_state,
    monk_instance,
    monk_sheet,
    start_combat,
)
from src.engine.modules import ruleset_runtime


def _setup(level: int = 2, **kwargs):
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=level)
    locale = str(kwargs.pop("locale", "en"))
    engine, instance = monk_instance(runtime, sheet, locale=locale)
    start_combat(engine, instance, **kwargs)
    return engine, instance


def _attack_action(engine, instance, uid: str = "gm") -> dict:
    return next(
        item for item in engine.available_intents(instance, uid) if item["type"] == "attack"
    )


def _apply(engine, instance, intent, rng):
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    return resolved, applied


def test_martial_arts_reaches_the_canonical_attack_profile() -> None:
    engine, instance = _setup(level=2)

    unarmed = _attack_action(engine, instance)["weapons"][0]

    assert unarmed["weapon_ref"] == "unarmed_strike"
    assert unarmed["damage"] == "1d6"
    assert unarmed["martial_arts"] is True
    assert unarmed["damage_type"] == "bludgeoning"


def test_bonus_unarmed_strike_costs_the_bonus_action_and_reuses_canonical_resolution() -> None:
    engine, instance = _setup(level=2)
    action = capability_action(engine, instance, "bonus_unarmed_strike")

    assert action is not None
    assert [cost["kind"] for cost in action["costs"]] == ["bonus_action"]
    assert action["requires_target"] is True
    assert [target["actor_id"] for target in action["targets"]] == ["enemy:goblin-1"]

    resolved, _applied = _apply(
        engine, instance,
        capability_intent(engine, instance, "bonus_unarmed_strike", intent_id="bonus-1"),
        SequenceRng([15, 4]),
    )
    events = resolved["event_batch"]["events"]
    spent = [event for event in events if event["type"] == "dnd2024.action.spent"]
    checks = [event for event in events if event["type"] == "check.resolved"]

    assert [(event["resource"], event["amount"]) for event in spent] == [("bonus_action", 1)]
    assert len(checks) == 1 and checks[0]["kind"] == "attack"
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["bonus_action"] == 0
    # 附赠徒手打击不是 Attack action：动作与专注点都不受影响。
    assert economy["action"] == 1
    assert focus_state(instance)["current"] == 2
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 40 - (4 + 2)


def test_bonus_unarmed_strike_disappears_once_the_bonus_action_is_spent() -> None:
    engine, instance = _setup(level=2)
    _apply(
        engine, instance,
        capability_intent(engine, instance, "bonus_unarmed_strike", intent_id="bonus-1"),
        SequenceRng([15, 4]),
    )

    assert capability_action(engine, instance, "bonus_unarmed_strike") is None
    forged = engine.validate_intent(
        instance,
        capability_intent(engine, instance, "bonus_unarmed_strike", intent_id="bonus-2"),
    )

    assert forged["ok"] is False
    assert "bonus action" in forged["error"]
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0


def test_flurry_spends_one_focus_and_one_bonus_action_for_two_strikes() -> None:
    engine, instance = _setup(level=2)
    action = capability_action(engine, instance, "flurry_of_blows")

    assert action is not None
    assert [(cost["kind"], cost["name"], cost["amount"]) for cost in action["costs"]] == [
        ("bonus_action", "Bonus Action", 1),
        ("resource", "Focus Points", 1),
    ]

    resolved, _applied = _apply(
        engine, instance,
        capability_intent(engine, instance, "flurry_of_blows", intent_id="flurry-1"),
        SequenceRng([15, 4, 15, 5]),
    )
    events = resolved["event_batch"]["events"]
    checks = [event for event in events if event["type"] == "check.resolved"]
    damage = [event for event in events if event["type"] == "resource.changed"]
    focus_events = [
        event for event in events if event["type"] == "dnd2024.class_resource.spent"
    ]

    assert len(checks) == 2 and all(event["kind"] == "attack" for event in checks)
    assert [event["amount"] for event in damage] == [6, 7]
    assert [(event["resource_id"], event["amount"]) for event in focus_events] == [
        ("focus_points", 1),
    ]
    # 资源扣减与两次攻击在同一个 EventBatch 里一起生效。
    assert focus_state(instance)["current"] == 1
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 1
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 40 - 13


def test_flurry_replay_is_idempotent() -> None:
    engine, instance = _setup(level=2)
    intent = capability_intent(engine, instance, "flurry_of_blows", intent_id="flurry-1")
    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4, 15, 5]))
    first = engine.apply_batch(instance, resolved["event_batch"])
    second = engine.apply_batch(instance, resolved["event_batch"])

    assert first["applied"] is True and second["duplicate"] is True
    assert focus_state(instance)["current"] == 1
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0


def test_flurry_is_rejected_without_focus() -> None:
    engine, instance = _setup(level=2)
    sheet = deepcopy(instance.get_character_sheet("gm"))
    sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = 0
    instance.set_character_sheet("gm", sheet)

    result = engine.validate_intent(
        instance, capability_intent(engine, instance, "flurry_of_blows", intent_id="f-0"),
    )

    assert result["ok"] is False
    assert "not enough Focus Points" in result["error"]
    assert focus_state(instance)["current"] == 0
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 1


def test_flurry_is_rejected_without_a_bonus_action() -> None:
    engine, instance = _setup(level=2)
    ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] = 0

    result = engine.validate_intent(
        instance, capability_intent(engine, instance, "flurry_of_blows", intent_id="f-1"),
    )

    assert result["ok"] is False
    assert "bonus action" in result["error"]
    assert focus_state(instance)["current"] == 2


def test_flurry_is_rejected_for_a_forged_target() -> None:
    engine, instance = _setup(level=2, position=1000)

    result = engine.validate_intent(
        instance, capability_intent(engine, instance, "flurry_of_blows", intent_id="f-2"),
    )

    assert result["ok"] is False
    assert "out of range" in result["error"]


def test_forged_capability_id_is_rejected() -> None:
    engine, instance = _setup(level=2)

    result = engine.validate_intent(
        instance,
        capability_intent(engine, instance, "wish", intent_id="forged", target_id=""),
    )

    assert result["ok"] is False
    assert "not available to this actor" in result["error"]


def test_forged_payload_cannot_change_the_authoritative_cost() -> None:
    engine, instance = _setup(level=2)

    _apply(
        engine, instance,
        capability_intent(
            engine, instance, "flurry_of_blows", intent_id="flurry-1",
            focus_points=99, amount=0, costs=[],
        ),
        SequenceRng([15, 4, 15, 5]),
    )

    assert focus_state(instance)["current"] == 1
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0


def test_a_rejected_resource_spend_rolls_the_whole_batch_back() -> None:
    engine, instance = _setup(level=2)
    version = ruleset_runtime.state(instance)["version"]
    ledger_length = len(ruleset_runtime.event_ledger(instance))
    batch = {
        "batch_id": "batch_forged_class_resource",
        "intent_id": "forged-spend",
        "intent_type": "class_capability",
        "expected_version": version,
        "result_version": version + 1,
        "events": [
            {"type": "intent.submitted", "intent_type": "class_capability",
             "actor_id": "player:gm", "submitted_by": "gm"},
            {"type": "dnd2024.class_resource.spent", "actor_id": "player:gm",
             "resource_id": "focus_points", "amount": 3},
        ],
        "source_ref": "test",
    }

    with pytest.raises(EventBatchError):
        engine.apply_batch(instance, batch)

    assert focus_state(instance)["current"] == 2
    assert ruleset_runtime.state(instance)["version"] == version
    assert len(ruleset_runtime.event_ledger(instance)) == ledger_length


def test_patient_defense_disengages_as_a_bonus_action() -> None:
    engine, instance = _setup(level=2)
    action = capability_action(engine, instance, "patient_defense")

    assert action is not None and action["requires_target"] is False

    resolved, _applied = _apply(
        engine, instance,
        capability_intent(engine, instance, "patient_defense", intent_id="pd-1", target_id=""),
        SequenceRng([]),
    )
    events = resolved["event_batch"]["events"]

    assert [event["condition"] for event in events if event["type"] == "condition.applied"] == [
        "disengaged",
    ]
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0
    assert focus_state(instance)["current"] == 2


def test_step_of_the_wind_grants_dash_movement_as_a_bonus_action() -> None:
    engine, instance = _setup(level=2)

    resolved, _applied = _apply(
        engine, instance,
        capability_intent(engine, instance, "step_of_the_wind", intent_id="sw-1", target_id=""),
        SequenceRng([]),
    )
    granted = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.movement.granted"
    )

    assert granted["amount"] == 30
    assert ruleset_runtime.state(instance)["combat"]["economy"]["movement"] == 60
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0


def test_focus_variants_spend_focus_and_resolve_both_actions() -> None:
    engine, instance = _setup(level=2)

    resolved, _applied = _apply(
        engine, instance,
        capability_intent(
            engine, instance, "step_of_the_wind_focus", intent_id="swf-1", target_id="",
        ),
        SequenceRng([]),
    )
    kinds = [event["type"] for event in resolved["event_batch"]["events"]]

    assert "dnd2024.class_resource.spent" in kinds
    assert "dnd2024.movement.granted" in kinds
    assert [
        event["condition"] for event in resolved["event_batch"]["events"]
        if event["type"] == "condition.applied"
    ] == ["disengaged"]
    assert focus_state(instance)["current"] == 1
    assert ruleset_runtime.state(instance)["combat"]["economy"]["movement"] == 60


def test_patient_defense_focus_also_dodges() -> None:
    engine, instance = _setup(level=2)

    resolved, _applied = _apply(
        engine, instance,
        capability_intent(
            engine, instance, "patient_defense_focus", intent_id="pdf-1", target_id="",
        ),
        SequenceRng([]),
    )
    conditions = [
        event["condition"] for event in resolved["event_batch"]["events"]
        if event["type"] == "condition.applied"
    ]

    assert conditions == ["disengaged", "dodging"]
    assert focus_state(instance)["current"] == 1


def test_non_monk_never_gets_monk_capabilities() -> None:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Fighter"},
    )
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)

    assert {
        item["capability_id"] for item in capability_actions(engine, instance)
    }.isdisjoint({
        "bonus_unarmed_strike", "flurry_of_blows", "patient_defense",
        "patient_defense_focus", "step_of_the_wind", "step_of_the_wind_focus",
    })
    unarmed = _attack_action(engine, instance)["weapons"][-1]
    assert unarmed["weapon_ref"] == "unarmed_strike"
    assert unarmed["damage"] == "1"
    # 原有动作经济不变：攻击、疾走、闪避、脱离接战照旧可用。
    assert {item["type"] for item in engine.available_intents(instance, "gm")} >= {
        "attack", "dash", "dodge", "disengage",
    }


def test_level_one_monk_has_bonus_unarmed_strike_but_no_focus_actions() -> None:
    # 1 级武僧有武艺但还没有专注点：只有附赠徒手打击出现。
    engine, instance = _setup(level=1)

    available = {item["capability_id"] for item in capability_actions(engine, instance)}

    assert available == {"bonus_unarmed_strike"}
    assert focus_state(instance) == {}


def test_ai_hosted_monk_uses_the_martial_arts_profile_and_ends_its_turn() -> None:
    engine, instance = _setup(level=2)
    instance.players["gm"]["control"] = {
        "mode": "ai", "revision": 1, "temporary": False, "resume_mode": None,
    }

    intents: list[dict] = []
    for _ in range(10):
        intent = engine.next_automatic_intent(instance)
        assert intent is not None
        intents.append(intent)
        resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4, 15, 4]))
        assert resolved["ok"] is True, resolved
        assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
        if intent["type"] == "end_turn":
            break
    else:  # pragma: no cover - the bounded ladder always terminates
        raise AssertionError("AI-hosted monk never ended its turn")

    # AI v1 只走既有确定性阶梯，不主动使用专注战术；但普通徒手打击必须使用
    # 武艺后的档案。
    attack = next(item for item in intents if item["type"] == "attack")
    assert attack["weapon_ref"] == "unarmed_strike"
    assert {item["type"] for item in intents} <= {"attack", "move", "dodge", "end_turn"}
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] < 40
    # AI 回合没有绕过任何权威链：专注点仍然完整。
    assert focus_state(instance)["current"] == 2


def test_monk_companion_keeps_the_automatic_ladder_alive() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=2))
    companion = deepcopy(instance.get_character_sheet("gm")["ruleset_character"])
    companion["resources"] = {"hp": 20, "max_hp": 20, "class": companion["resources"]["class"]}
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})["mira"] = {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": companion,
    }
    start_combat(engine, instance, hp=200)
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index("companion:mira")
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, "companion:mira"),
    )

    intent = engine.next_automatic_intent(instance)

    assert intent is not None and intent["type"] == "attack"
    assert intent["weapon_ref"] == "unarmed_strike"
    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] == 200 - 6


def test_weaponless_monk_still_owns_the_plain_attack_action() -> None:
    engine, instance = _setup(level=2)

    weapons = _attack_action(engine, instance)["weapons"]

    assert [item["weapon_ref"] for item in weapons] == ["unarmed_strike"]
    # 普通 Attack 与附赠徒手打击 / 疾风连击共用同一个 canonical 攻击身份。
    assert {
        item["capability_id"] for item in capability_actions(engine, instance)
    } == {
        "bonus_unarmed_strike", "flurry_of_blows", "patient_defense",
        "patient_defense_focus", "step_of_the_wind", "step_of_the_wind_focus",
    }


def test_class_capability_requires_the_current_actor() -> None:
    engine, instance = _setup(level=2)
    instance.players["other"] = {
        "character_name": "Other",
        "character_sheet": deepcopy(instance.get_character_sheet("gm")),
    }
    ruleset_runtime.state(instance)["combat"]["economy"] = engine._fresh_economy(
        engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], "player:gm"),
    )

    result = engine.validate_intent(
        instance,
        capability_intent(
            engine, instance, "flurry_of_blows", intent_id="not-my-turn", uid="other",
        ),
    )

    assert result["ok"] is False
    assert "not this actor's turn" in result["error"]


def test_capability_targets_only_list_hostiles() -> None:
    engine, instance = _setup(level=2, position=5)

    flurry = capability_action(engine, instance, "flurry_of_blows")

    assert flurry is not None
    assert [target["actor_id"] for target in flurry["targets"]] == ["enemy:goblin-1"]
    assert flurry["targets"][0]["hp"] == 40
    # 不需要目标的能力不会附带 targets 字段，前端据此决定是否进入选靶流程。
    step = capability_action(engine, instance, "step_of_the_wind")
    assert step is not None and "targets" not in step



def test_every_combat_actor_view_projects_class_resources_as_a_list() -> None:
    """同一个字段只能有一种形状：客户端把它当数组渲染。

    敌人没有职业资源，投影必须是空列表；``{}`` 之类的第二种形状会让前端在渲染
    战斗面板时抛错，把整块面板（以及整页）带下去（Browser smoke 的实际故障）。
    """

    engine, instance = _setup(level=2)

    actors = engine.gameplay_view(instance)["combat"]["actors"]
    by_id = {actor["actor_id"]: actor for actor in actors}

    assert isinstance(by_id["player:gm"]["class_resources"], list)
    assert [row["id"] for row in by_id["player:gm"]["class_resources"]] == ["focus_points"]
    # 敌人没有职业资源，但字段仍然存在，并且仍然是同一张列表。
    assert by_id["enemy:goblin-1"]["class_resources"] == []


# --------------------------------------------------------------------------
# Second Wind (Slice 1): the first ``heal`` capability action
# --------------------------------------------------------------------------


def _fighter_sheet(runtime: Dnd2024Runtime, *, level: int = 1) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Fighter"},
    )
    return advance(runtime, sheet, level) if level > 1 else sheet


def _fighter_instance(
    *, level: int = 1, second_wind_current: int | None = None,
    conditions: dict | None = None,
):
    runtime = Dnd2024Runtime()
    sheet = _fighter_sheet(runtime, level=level)
    canonical = sheet["ruleset_character"]
    if second_wind_current is not None:
        canonical["resources"]["class"]["second_wind"]["current"] = second_wind_current
    if conditions is not None:
        canonical["conditions"] = conditions
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)
    return engine, instance


def _second_wind_events(engine, instance, rolls: list[int]) -> list[dict]:
    intent = capability_intent(
        engine, instance, "second_wind", intent_id="wind-1", target_id="",
    )
    resolved, _applied = _apply(engine, instance, intent, SequenceRng(rolls))
    return resolved["event_batch"]["events"]


def _heal_event(events: list[dict]) -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("healing") is True
    )


def test_second_wind_spends_its_costs_then_heals_the_actor() -> None:
    engine, instance = _fighter_instance()
    action = capability_action(engine, instance, "second_wind")

    assert action is not None
    assert action["requires_target"] is False
    assert [cost["kind"] for cost in action["costs"]] == ["bonus_action", "resource"]

    events = _second_wind_events(engine, instance, [7])
    ordered = [
        event for event in events
        if event["type"] in {
            "dnd2024.action.spent", "dnd2024.class_resource.spent", "resource.changed",
        }
    ]

    assert [event["type"] for event in ordered] == [
        "dnd2024.action.spent", "dnd2024.class_resource.spent", "resource.changed",
    ]
    action_event, resource_event, heal_event = ordered
    assert (action_event["resource"], action_event["amount"]) == ("bonus_action", 1)
    assert (resource_event["resource_id"], resource_event["amount"]) == ("second_wind", 1)
    assert heal_event["target_id"] == "player:gm"
    assert heal_event["rolls"] == [7]

    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    assert canonical["resources"]["class"]["second_wind"]["current"] == 1


@pytest.mark.parametrize("level,level_bonus", [(1, 1), (5, 5)])
def test_second_wind_healing_is_one_d10_plus_the_fighter_level(
    level: int, level_bonus: int,
) -> None:
    engine, instance = _fighter_instance(level=level)

    heal_event = _heal_event(_second_wind_events(engine, instance, [9]))

    assert heal_event["rolls"] == [9]
    assert heal_event["delta"] == 9 + level_bonus


def test_second_wind_is_rejected_without_a_use_and_emits_nothing() -> None:
    engine, instance = _fighter_instance(second_wind_current=0)
    intent = capability_intent(
        engine, instance, "second_wind", intent_id="wind-0", target_id="",
    )

    validation = engine.validate_intent(instance, intent)
    assert validation["ok"] is False
    assert "Second Wind" in validation["error"]

    resolved = engine.resolve_intent(instance, intent, SequenceRng([7]))
    assert resolved["ok"] is False
    assert "event_batch" not in resolved
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 1
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    assert canonical["resources"]["class"]["second_wind"]["current"] == 0


def test_second_wind_restores_no_hp_under_cannot_regain_hp() -> None:
    engine, instance = _fighter_instance(conditions={"cannot_regain_hp": True})

    events = _second_wind_events(engine, instance, [9])
    heal_event = _heal_event(events)

    assert heal_event["healing"] is True
    assert heal_event["delta"] == 0
    assert heal_event["amount"] == 0
    assert heal_event["blocked_by"] == "cannot_regain_hp"
    assert any(
        event["type"] == "dnd2024.class_resource.spent" for event in events
    )
