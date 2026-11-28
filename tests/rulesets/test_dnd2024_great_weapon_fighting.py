"""FD7: Great Weapon Fighting — 1s and 2s on the weapon's dice count as 3.

SRD 5.2.1 p88 says "you can treat any 1 or 2 on a damage die as a 3" when the
attack uses a melee weapon you hold with two hands and the weapon has the
Two-Handed or Versatile property.

The scope is the weapon's own damage dice: Sneak Attack, Hunter's Mark, Divine
Smite and hit riders are separate damage sources and stay untouched.  The
decision and its reason live in ``FD7_GREAT_WEAPON_FIGHTING_TASK_SPEC`` §1;
the house-rule reading (all damage of the attack) is registered as GAP-58.
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.combat.action_adapter import (
    attack_damage_node,
    roll_damage_node_with_trace,
)
from src.rulesets.dnd2024.features.parameter_registry import FeatParameterError
from src.rulesets.dnd2024.features.feat_models import FeatDefinitionError, parse_feat
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from src.engine.modules import ruleset_runtime

from dnd2024_monk_common import SequenceRng
from test_dnd2024_fighting_style import (
    RecordingRng,
    enemy,
    events_of,
    fighter_sheet,
    new_combat,
    resolve,
    set_turn,
)

GWF = "feat:great_weapon_fighting"
ARCHERY = "feat:archery"
GREATSWORD = "item:greatsword"
LONGSWORD = "item:longsword"
LONGBOW = "item:longbow"
JAVELIN = "item:javelin"
SCIMITAR = "item:scimitar"


def _combat(
    *, fighting_style: str = GWF, package: str = "equipment_package:fighter_a",
    extra: tuple[str, ...] = (), only: tuple[str, ...] | None = None,
):
    runtime = Dnd2024Runtime()
    sheet = fighter_sheet(runtime, fighting_style=fighting_style, package=package)
    refs = sheet["ruleset_character"]["equipment"]["item_refs"]
    if only is not None:
        refs[:] = list(only)
    for ref in extra:
        if ref not in refs:
            refs.append(ref)
    # Soldier also grants Savage Attacker, whose hit window would hold the
    # damage this file reads straight off the attack.  FD10 covers it; here it
    # would only be noise.
    feats = sheet["ruleset_character"]["features"]["feat_refs"]
    if "feat:savage_attacker" in feats:
        feats.remove("feat:savage_attacker")
    return new_combat(runtime, sheet, enemies=[enemy(position=5)])


def _damage(result: dict) -> dict:
    return next(
        event for event in events_of(result)
        if event.get("type") == "resource.changed"
        and event.get("resource") == "hp"
        and int(event.get("delta") or 0) < 0
    )


def _hit(engine, instance, *, weapon: str, rolls: list[int], **extra):
    """One attack that is guaranteed to hit: the first roll is the d20."""

    set_turn(engine, instance, "player:gm")
    version = ruleset_runtime.state(instance)["version"]
    intent = {
        "intent_id": f"attack:{version}:{weapon}:{','.join(str(v) for v in rolls)}",
        "type": "attack", "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": weapon, **extra,
    }
    return _damage(resolve(engine, instance, intent, rolls))


# --------------------------------------------------------------------------
# the effect itself
# --------------------------------------------------------------------------


def test_one_and_two_become_three_on_a_two_handed_weapon() -> None:
    engine, instance = _combat()

    event = _hit(engine, instance, weapon=GREATSWORD, rolls=[15, 1, 2])

    assert event["rolls"] == [3, 3]
    assert event["damage_die_floor"] == 3
    assert event["floored_rolls"] == [[1, 3], [2, 3]]
    # amount 仍与 rolls 自洽：下限只抬高骰值，不另加一份加值。
    assert event["amount"] > sum(event["rolls"])


def test_high_dice_are_left_alone_and_the_event_stays_plain() -> None:
    engine, instance = _combat()

    event = _hit(engine, instance, weapon=GREATSWORD, rolls=[15, 5, 6])

    assert event["rolls"] == [5, 6]
    assert "damage_die_floor" not in event
    assert "floored_rolls" not in event


def test_a_critical_hit_floors_every_doubled_die() -> None:
    engine, instance = _combat()

    # 自然 20 = 暴击：2d6 变 4d6，四颗骰都应各自取下限。
    event = _hit(engine, instance, weapon=GREATSWORD, rolls=[20, 1, 2, 1, 2])

    assert event["critical"] is True
    assert event["rolls"] == [3, 3, 3, 3]
    assert event["floored_rolls"] == [[1, 3], [2, 3], [1, 3], [2, 3]]


# --------------------------------------------------------------------------
# applicability: the three predicates
# --------------------------------------------------------------------------


def test_a_versatile_weapon_floors_dice_only_when_held_with_two_hands() -> None:
    engine, instance = _combat(only=(LONGSWORD,))

    one_handed = _hit(engine, instance, weapon=LONGSWORD, rolls=[15, 1])
    assert one_handed["rolls"] == [1]
    assert "damage_die_floor" not in one_handed

    two_handed = _hit(
        engine, instance, weapon=LONGSWORD, rolls=[15, 1], two_handed=True,
    )
    assert two_handed["rolls"] == [3]
    assert two_handed["damage_die_floor"] == 3


def test_a_one_handed_weapon_never_qualifies() -> None:
    engine, instance = _combat(only=(SCIMITAR,))

    event = _hit(engine, instance, weapon=SCIMITAR, rolls=[15, 1])

    assert event["rolls"] == [1]
    assert "damage_die_floor" not in event


def test_a_thrown_one_handed_weapon_does_not_qualify() -> None:
    engine, instance = _combat(only=(JAVELIN,))

    event = _hit(engine, instance, weapon=JAVELIN, rolls=[15, 1])

    assert event["rolls"] == [1]
    assert "damage_die_floor" not in event


def test_a_ranged_two_handed_weapon_does_not_qualify() -> None:
    # Longbow is both ranged and two-handed; the ranged predicate must win.
    # fighter_b carries arrows, and ``only`` keeps the bow in both hands.
    engine, instance = _combat(
        package="equipment_package:fighter_b", only=(LONGBOW,),
    )

    event = _hit(engine, instance, weapon=LONGBOW, rolls=[15, 1])

    assert event["rolls"] == [1]
    assert "damage_die_floor" not in event


def test_without_the_style_nothing_changes() -> None:
    engine, instance = _combat(fighting_style=ARCHERY)

    event = _hit(engine, instance, weapon=GREATSWORD, rolls=[15, 1, 2])

    assert event["rolls"] == [1, 2]
    assert "damage_die_floor" not in event


# --------------------------------------------------------------------------
# scope: only the weapon's own dice, never the extra sources
# --------------------------------------------------------------------------


def test_extra_dice_are_not_floored() -> None:
    """A Hunter's Mark die shares the AST but is not the weapon's damage die.

    This is the boundary the Sage Advice ruling draws, and the reason the
    implementation counts dice instead of touching every die node.
    """

    node = attack_damage_node(
        damage_formula="2d6", modifier=0, critical=False,
        extra_dice_formulas=["1d6"],
    )
    amount, rolls, floored = roll_damage_node_with_trace(
        node, SequenceRng([1, 2, 1]), die_floor=3, floored_dice=2,
    )

    assert rolls == [3, 3, 1]
    assert floored == [(1, 3), (2, 3)]
    assert amount == 7


def test_the_rng_contract_is_unchanged() -> None:
    """A floor is not a reroll: the same number of dice is requested."""

    with_style = RecordingRng([1, 2])
    roll_damage_node_with_trace(
        attack_damage_node(damage_formula="2d6", modifier=0, critical=False),
        with_style, die_floor=3, floored_dice=2,
    )
    without = RecordingRng([1, 2])
    roll_damage_node_with_trace(
        attack_damage_node(damage_formula="2d6", modifier=0, critical=False),
        without,
    )

    assert with_style.requests == without.requests == [(1, 6), (1, 6)]


# --------------------------------------------------------------------------
# declaration shape
# --------------------------------------------------------------------------


def test_the_declared_floor_is_validated() -> None:
    def feat(value: object) -> dict:
        return {
            "id": "great_weapon_fighting",
            "category": "fighting_style",
            "source_ref": "test",
            "automation_level": "deterministic",
            "repeatable": False,
            "parameters": {"two_handed_melee_damage_die_minimum": value},
        }

    assert parse_feat(feat(3)).parameters["two_handed_melee_damage_die_minimum"] == 3
    for bad in (1, 13, True, "3"):
        with pytest.raises((FeatDefinitionError, FeatParameterError)):
            parse_feat(feat(bad))
