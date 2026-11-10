"""S0-F6c: Font of Magic inside the action economy.

The conversion itself is a pure rule (``spells/font_of_magic.py``); what these
tests pin is everything around it:

* the two directions are two capability declarations, so "no action required"
  and "as a Bonus Action" are spent by the declaration and never twice;
* a created slot is a *real* slot at cast time -- one spell consumes exactly
  one of them, never a temporary and an ordinary slot at once;
* a refusal leaves the sheet exactly as it was, in the combat economy and out
  of it.

The item numbers in the docstrings are the task spec's matrix.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, goblin, monk_instance
from test_dnd2024_spell_grants import _sheet as _class_sheet

LOCALE = "en"
GOBLIN = "enemy:goblin-1"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _sorcerer(runtime: Dnd2024Runtime, level: int, *, points: int | None = None) -> dict:
    sheet = _class_sheet(runtime, "class:sorcerer", level)
    if points is not None:
        canonical = sheet["ruleset_character"]
        canonical["resources"]["class"]["sorcery_points"]["current"] = points
    return sheet


def _state(instance) -> dict:
    return ruleset_runtime.state(instance)


def _apply(engine, instance, intent: dict, rng=None) -> list[dict]:
    return _commit(
        engine, instance, engine.resolve_intent(instance, intent, rng or SequenceRng([])),
    )


def _commit(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _combat(runtime: Dnd2024Runtime, sheet: dict):
    engine, instance = monk_instance(runtime, deepcopy(sheet), uid="gm")
    _apply(engine, instance, {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(position=5, hp=60)],
    }, SequenceRng([20, 1]))
    return engine, instance


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _points(instance, uid: str = "gm") -> int:
    return int(_canonical(instance, uid)["resources"]["class"]["sorcery_points"]["current"])


def _font(instance, uid: str = "gm") -> dict[str, int]:
    return dict(_canonical(instance, uid)["spellcasting"]["class"].get("font_slots") or {})


def _slots(instance, uid: str = "gm") -> dict[str, int]:
    return dict(_canonical(instance, uid)["spellcasting"]["class"]["slots_current"])


def _capabilities(engine, instance, uid: str = "gm") -> dict[str, dict]:
    return {
        action["capability_id"]: action
        for action in engine.available_intents(instance, uid)
        if action["type"] == "class_capability"
    }


def _capability(engine, instance, capability_id: str, slot_level: int, **extra) -> dict:
    state = _state(instance)
    intent = {
        "intent_id": f"{capability_id}-{slot_level}-{state['version']}",
        "type": "class_capability", "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "capability_id": capability_id, "slot_level": slot_level,
    }
    intent.update(extra)
    return engine.resolve_intent(instance, intent, SequenceRng([]))


def _cast(engine, instance, *, spell_ref: str, slot_level: int, target: str = GOBLIN) -> dict:
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"cast-{spell_ref}-{slot_level}-{state['version']}",
        "type": "cast_spell", "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": spell_ref, "slot_level": slot_level, "target_ids": [target],
    }, SequenceRng([10, 3, 4, 5]))


def _economy(instance) -> dict:
    return dict(_state(instance)["combat"]["economy"])


# --------------------------------------------------------------------------
# matrix 1 / 23: the declarations arrive where the class table says they do
# --------------------------------------------------------------------------


def test_the_two_directions_arrive_with_the_feature(
    runtime: Dnd2024Runtime,
) -> None:
    early, early_instance = _combat(runtime, _sorcerer(runtime, 1))
    assert _capabilities(early, early_instance) == {}

    later, later_instance = _combat(runtime, _sorcerer(runtime, 2))
    offered = _capabilities(later, later_instance)
    assert set(offered) == {"font_slot_to_points", "font_create_slot"}
    # 槽换点不花动作、也不要敌对目标；点换槽要附赠动作。
    assert not offered["font_slot_to_points"]["costs"]
    assert [cost["kind"] for cost in offered["font_create_slot"]["costs"]] == [
        "bonus_action"
    ]


# --------------------------------------------------------------------------
# matrix 14-15: the slot -> points direction
# --------------------------------------------------------------------------


def test_matrix_14_exchanging_a_slot_costs_no_action_and_is_not_a_cast(
    runtime: Dnd2024Runtime,
) -> None:
    sheet = _sorcerer(runtime, 5, points=2)
    engine, instance = _combat(runtime, sheet)
    before = int(_slots(instance)["3"])
    assert before >= 1

    _commit(engine, instance, _capability(engine, instance, "font_slot_to_points", 3))

    assert _points(instance) == 5
    assert _slots(instance)["3"] == before - 1
    # 一次"不用动作"的转换不能顺手把附赠动作或本回合的施法位用掉。
    assert _economy(instance)["bonus_action"] == 1
    assert not _economy(instance).get("slot_spell_cast")


def test_matrix_15_an_overflowing_exchange_is_refused_before_anything_moves(
    runtime: Dnd2024Runtime,
) -> None:
    sheet = _sorcerer(runtime, 5, points=5)  # 上限 5，此刻满
    engine, instance = _combat(runtime, sheet)

    refused = _capability(engine, instance, "font_slot_to_points", 1)

    assert refused["ok"] is False
    assert "exceed your Sorcery Points maximum" in refused["error"]
    assert _points(instance) == 5
    assert _slots(instance)["1"] == 4


# --------------------------------------------------------------------------
# matrix 16-17: the points -> slot direction
# --------------------------------------------------------------------------


def test_matrix_16_one_bonus_action_per_created_slot(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=5))

    events = _commit(
        engine, instance, _capability(engine, instance, "font_create_slot", 1),
    )

    assert [event["type"] for event in events] == [
        "intent.submitted", "dnd2024.action.spent",
        "dnd2024.class_resource.spent", "dnd2024.spell.font_slot_created",
    ]
    assert sum(1 for event in events if event["type"] == "dnd2024.action.spent") == 1
    assert _font(instance) == {"1": 1}
    assert _points(instance) == 3
    assert _economy(instance)["bonus_action"] == 0

    again = _capability(engine, instance, "font_create_slot", 1)
    assert again["ok"] is False
    assert "bonus action has already been spent" in again["error"]
    # 第二次被拒后仍然只有一个临时槽。
    assert _font(instance) == {"1": 1}


def test_matrix_17_an_empty_purse_creates_nothing(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=0))

    refused = _capability(engine, instance, "font_create_slot", 1)

    assert refused["ok"] is False
    assert "not enough Sorcery Points" in refused["error"]
    assert _font(instance) == {}
    # 附赠动作没有被这次失败的尝试吃掉。
    assert _economy(instance)["bonus_action"] == 1


def test_matrix_17_a_level_too_low_for_that_slot_is_refused(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=9))

    refused = _capability(engine, instance, "font_create_slot", 4)

    assert refused["ok"] is False
    assert "requires level 7" in refused["error"]
    assert _font(instance) == {}
    assert _points(instance) == 9


def test_matrix_17_no_slot_above_level_five_exists(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat(runtime, _sorcerer(runtime, 9, points=9))

    refused = _capability(engine, instance, "font_create_slot", 6)

    assert refused["ok"] is False
    assert "cannot create a spell slot of level 6" in refused["error"]


# --------------------------------------------------------------------------
# matrix 18-19: a created slot is spent like a real one
# --------------------------------------------------------------------------


def test_matrix_18_the_temporary_slot_pays_for_a_real_spell(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=5))
    _commit(engine, instance, _capability(engine, instance, "font_create_slot", 3))
    ordinary_before = _slots(instance)

    events = _apply(engine, instance, {
        "intent_id": "cast-after-create", "type": "cast_spell",
        "expected_version": _state(instance)["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "spell_ref": "spell:burning_hands",
        "slot_level": 3, "target_ids": [GOBLIN],
    }, SequenceRng([10, 3, 4, 5]))

    assert "dnd2024.spell.font_slot_spent" in [event["type"] for event in events]
    assert _font(instance) == {}
    # 普通槽一个都没动：临时槽是真的付了这次施法。
    assert _slots(instance) == ordinary_before
    assert _economy(instance).get("slot_spell_cast") is True


def test_matrix_19_one_spell_never_pays_twice(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=5))
    _commit(engine, instance, _capability(engine, instance, "font_create_slot", 3))
    ordinary_before = int(_slots(instance)["3"])
    assert ordinary_before >= 1  # 同级普通槽此刻也在：这正是"先花临时槽"的场景

    events = _apply(engine, instance, {
        "intent_id": "cast-once", "type": "cast_spell",
        "expected_version": _state(instance)["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "spell_ref": "spell:burning_hands",
        "slot_level": 3, "target_ids": [GOBLIN],
    }, SequenceRng([10, 3, 4, 5]))

    slot_events = [
        event for event in events
        if event["type"] in {
            "dnd2024.spell.slot_spent", "dnd2024.spell.font_slot_spent",
        }
    ]
    assert len(slot_events) == 1
    assert _slots(instance)["3"] == ordinary_before
    assert _font(instance) == {}


def test_matrix_19_a_slot_level_that_does_not_exist_is_refused(
    runtime: Dnd2024Runtime,
) -> None:
    """A forged level must not be answered out of the ordinary table either."""

    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=5))

    refused = _cast(engine, instance, spell_ref="spell:burning_hands", slot_level=5)

    assert refused["ok"] is False
    assert "not available" in refused["error"]


def test_matrix_19_the_reducer_refuses_a_temporary_slot_it_does_not_hold(
    runtime: Dnd2024Runtime,
) -> None:
    """The event itself fails closed, so a forged batch cannot invent a slot."""

    from src.rulesets.events import EventBatchError

    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=5))
    version = _state(instance)["version"]
    batch = {
        "batch_id": "batch_forged_font_slot", "intent_id": "forged",
        "expected_version": version, "result_version": version + 1,
        "events": [{
            "type": "dnd2024.spell.font_slot_spent", "actor_id": "player:gm",
            "slot_level": 3, "amount": 1, "source": "font_of_magic",
        }],
    }

    with pytest.raises(EventBatchError, match="not initialized"):
        engine.apply_batch(instance, batch)


# --------------------------------------------------------------------------
# matrix 24: the slot survives storage because it lives on the sheet
# --------------------------------------------------------------------------


def test_a_created_slot_survives_a_storage_round_trip(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat(runtime, _sorcerer(runtime, 5, points=5))
    _commit(engine, instance, _capability(engine, instance, "font_create_slot", 2))

    stored = json.loads(json.dumps(instance.get_character_sheet("gm")))
    restored = _sorcerer(runtime, 5)
    restored["ruleset_character"] = stored["ruleset_character"]

    engine2, instance2 = _combat(runtime, restored)

    assert _font(instance2) == {"2": 1}
    offered = next(
        action for action in engine2.available_intents(instance2, "gm")
        if action["type"] == "cast_spell"
    )
    levels = {
        row["spell_ref"]: row["available_slot_levels"] for row in offered["spells"]
    }
    # 临时槽也要出现在可施法环阶里，否则它存在却永远选不到。
    assert 2 in levels["spell:burning_hands"]


# --------------------------------------------------------------------------
# matrix 18, out of combat: the temporary slot is still a slot
# --------------------------------------------------------------------------


class _FixedDie:
    def randint(self, minimum: int, maximum: int) -> int:
        return min(max(3, minimum), maximum)


def _exploration(runtime: Dnd2024Runtime, sheet: dict):
    from src.engine.game_instance import GameInstance
    from src.rulesets.dnd2024.exploration import Dnd2024ExplorationEngine

    instance = GameInstance(
        game_key=("test", "dnd2024-font", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language=LOCALE,
    )
    instance.players["gm"] = {"character_name": "Caster", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["ruleset_character"]["rule_binding"])
    return Dnd2024ExplorationEngine(runtime.load_bundle(LOCALE), locale=LOCALE), instance


def test_matrix_18_the_temporary_slot_also_pays_outside_combat(
    runtime: Dnd2024Runtime,
) -> None:
    """The Bonus Action is a combat fact; being a real slot is not."""

    sheet = _sorcerer(runtime, 5)
    canonical = sheet["ruleset_character"]
    # 探索态只结算治疗/增益；给术士一张治疗法术，考的是"哪个槽被扣了"。
    canonical["spellcasting"]["class"]["prepared_spell_refs"].append("spell:cure_wounds")
    canonical["spellcasting"]["class"]["font_slots"] = {"2": 1}
    canonical["resources"]["hp"] = 5
    engine, instance = _exploration(runtime, sheet)
    ordinary_before = _slots(instance)

    offered = next(
        action for action in engine.available_intents(instance, "player:gm")
        if action["type"] == "exploration.cast_spell"
    )
    levels = {row["spell_ref"]: row["available_slot_levels"] for row in offered["spells"]}
    assert 2 in levels["spell:cure_wounds"]

    resolved = engine.resolve_intent(instance, {
        "intent_id": "explore-font", "type": "exploration.cast_spell",
        "expected_version": int(_state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:cure_wounds", "slot_level": 2,
        "target_ids": ["player:gm"],
    }, _FixedDie())
    assert resolved["ok"] is True, resolved
    assert "dnd2024.spell.font_slot_spent" in [
        event["type"] for event in resolved["events"]
    ]
    engine.apply_batch(instance, resolved["event_batch"])

    assert _font(instance) == {}
    assert _slots(instance) == ordinary_before
    assert int(_canonical(instance)["resources"]["hp"]) > 5
