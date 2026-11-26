"""S0-F15: what a creature declares before the d20, and what that leaves behind.

Every assertion runs the chain the web layer runs -- ``available_intents`` ->
``resolve_intent`` -> ``apply_batch`` -> ``end_turn`` -- so a declaration is
never trusted from the panel and a condition is never written by the test.

The slice has one shape and four consequences:

* **a pre-roll declaration** (Reckless, Brutal) that must be made before the
  dice and is remembered per turn, not per attack;
* **a forgo** (Brutal) that removes Advantage without removing its sources, so
  the event still shows what was given up;
* **target-bound state** (the four Brutal options, Studied Attacks) whose
  lifetime is expressed as a condition or a mark, never as a flag on the
  attacker;
* **a per-turn budget** (Frenzy, Brutal) that must survive a miss.

The item numbers in the docstrings are the task spec's matrix.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import json

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.check_modes import effective_speed
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    capability_intent,
    goblin,
    monk_draft,
    monk_instance,
    with_equipment,
)

LOCALE = "en"
BARBARIAN_PRESET = "relentless_vanguard"

FIGHTER_ABILITIES = {"str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8}
CLERIC_ABILITIES = {"str": 13, "dex": 12, "con": 14, "int": 8, "wis": 15, "cha": 10}

# A Barbarian that has not trained in heavy armour: the one Disadvantage source
# in this file that is not a condition, which makes it the honest way to ask
# "does a second, unrelated source cancel Reckless?" without also testing the
# condition registry.  The derivation itself is contract of the armour slice.
_UNTRAINED = {
    "untrained_armor_worn": True, "speed_penalty": 10, "stealth_disadvantage": True,
}


# --------------------------------------------------------------------------
# sheets: the real builder and the real advancement path
# --------------------------------------------------------------------------

_RUNTIME = Dnd2024Runtime()
_SHEETS: dict[tuple, dict] = {}


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    """One runtime for the module: every sheet helper caches against it."""

    return _RUNTIME


def _spell_choices(runtime: Dnd2024Runtime, class_ref: str, level: int) -> dict:
    options = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).options(class_ref, level)
    requirements = options["requirements"]
    pool = [str(row["ref"]) for row in options["cantrips"]]
    wanted = [ref for ref in ("spell:sacred_flame",) if ref in pool]
    cantrips = (wanted + [ref for ref in pool if ref not in wanted])[
        : int(requirements["cantrip_count"])
    ]
    leveled = [str(row["ref"]) for row in options["leveled_spells"]]
    return {
        "cantrip_refs": cantrips,
        "prepared_spell_refs": leveled[: int(requirements["prepared_spell_count"])],
        "spellbook_refs": [],
    }


def _picks(preview: dict, character: dict) -> dict:
    """First legal answer per requirement, keeping ability scores under 20."""

    picks = advancement_choices(preview)
    for requirement in preview.get("requirements") or []:
        if requirement.get("id") != "ability_score_increases":
            continue
        if requirement.get("allowed", "any") != "any":
            continue
        abilities = character.get("abilities") or {}
        picks["ability_score_increases"] = {
            min(abilities, key=lambda key: int(abilities[key])): int(
                requirement.get("total", 2)
            )
        }
    return picks


def _advance(runtime: Dnd2024Runtime, sheet: dict, level: int, class_ref: str) -> dict:
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = _picks(preview, sheet["ruleset_character"])
        picks["class_spell_choices"] = _spell_choices(runtime, class_ref, next_level)
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, (preview["errors"], picks)
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _sheet(
    runtime: Dnd2024Runtime, class_ref: str, level: int,
    abilities: dict[str, int], package: str,
) -> dict:
    draft = monk_draft(runtime, LOCALE)
    draft.update(
        class_ref=class_ref,
        class_tool_refs=[],
        equipment_package_ref=package,
        base_abilities=dict(abilities),
    )
    # Skills are read back from the builder so a class whose list does not
    # contain the Monk's choices cannot invalidate this fixture.
    choices = runtime.builder_choices(None, draft)
    class_skills = [
        str(item["ref"]) for item in choices["class_skills"]
    ][: int(choices["class_skill_count"])]
    draft["class_skill_refs"] = class_skills
    draft["species_skill_refs"] = [
        str(item["ref"]) for item in choices["species_skills"]
        if str(item["ref"]) not in class_skills
    ][: int(choices["species_skill_count"])]
    draft["class_tool_refs"] = [item["ref"] for item in choices["class_tools"]][
        : choices["class_tool_count"]
    ]
    draft["class_spell_choices"] = _spell_choices(runtime, class_ref, 1)
    sheet = runtime.finalize_character(None, draft)
    return _advance(runtime, sheet, level, class_ref)


def barbarian(level: int) -> dict:
    """A real Berserker: the preset answers the subclass question."""

    key = ("barbarian", level)
    if key not in _SHEETS:
        choices = _RUNTIME.builder_choices(None, {"locale": LOCALE})
        preset = next(
            item for item in choices["quick_presets"] if item["id"] == BARBARIAN_PRESET
        )
        sheet = _RUNTIME.finalize_character(
            None, {**preset["draft"], "locale": LOCALE, "name": "Berserker"},
        )
        _SHEETS[key] = _advance(_RUNTIME, sheet, level, "class:barbarian")
    return deepcopy(_SHEETS[key])


def fighter(level: int) -> dict:
    key = ("fighter", level)
    if key not in _SHEETS:
        _SHEETS[key] = _sheet(
            _RUNTIME, "class:fighter", level, FIGHTER_ABILITIES,
            "equipment_package:fighter_a",
        )
    return deepcopy(_SHEETS[key])


def cleric(level: int = 1) -> dict:
    key = ("cleric", level)
    if key not in _SHEETS:
        _SHEETS[key] = _sheet(
            _RUNTIME, "class:cleric", level, CLERIC_ABILITIES,
            "equipment_package:cleric_a",
        )
    return deepcopy(_SHEETS[key])


def without_heavy_armour_training(sheet: dict) -> dict:
    sheet["ruleset_character"]["derived"]["armor_training"] = dict(_UNTRAINED)
    return sheet


# --------------------------------------------------------------------------
# combat plumbing
# --------------------------------------------------------------------------

def _apply(engine, instance, intent: dict, rolls=()) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _rejected(engine, instance, intent: dict) -> str:
    resolved = engine.resolve_intent(instance, intent, SequenceRng([]))
    assert resolved["ok"] is False, resolved
    return str(resolved.get("error") or "")


def _combat(
    runtime: Dnd2024Runtime, players: dict[str, dict], *, initiative: dict[str, int],
    enemies: list[dict] | None = None, positions: dict[str, int] | None = None,
) -> tuple[Dnd2024CombatEngine, Any]:
    """Start a fight with a chosen turn order.

    ``initiative`` is keyed by uid because ``combat.start`` rolls in sorted uid
    order; a positional list would silently put the wrong creature first.  The
    enemy always rolls last and therefore always acts last.
    """

    first_uid = next(iter(players))
    engine, instance = monk_instance(runtime, deepcopy(players[first_uid]), uid=first_uid)
    for uid, sheet in list(players.items())[1:]:
        instance.players[uid] = {
            "character_name": uid.title(), "character_sheet": deepcopy(sheet),
        }
    order = sorted(instance.players)
    intent: dict[str, Any] = {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": first_uid,
        "enemies": enemies if enemies is not None else [goblin(position=5, hp=200)],
    }
    if positions:
        intent["player_positions"] = positions
    _apply(engine, instance, intent, [initiative[uid] for uid in order] + [1])
    return engine, instance


def _combat_state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _current(instance) -> str:
    combat = _combat_state(instance)
    return str(combat["initiative"][combat["turn_index"]])


def _end_turn(engine, instance, actor_id: str = "") -> list[dict]:
    actor_id = actor_id or _current(instance)
    version = ruleset_runtime.state(instance)["version"]
    uid = "gm" if actor_id.startswith("enemy:") else actor_id.split(":", 1)[1]
    return _apply(engine, instance, {
        "intent_id": f"end-{version}-{actor_id}", "type": "end_turn",
        "expected_version": version, "submitted_by": uid, "actor_id": actor_id,
    })


def _to_actor(engine, instance, actor_id: str) -> None:
    for _ in range(12):
        if _current(instance) == actor_id:
            return
        _end_turn(engine, instance)
    raise AssertionError("the requested actor never got a turn")


def _next_turn(engine, instance, actor_id: str) -> None:
    """Close this turn and come back round to ``actor_id``.

    A new turn is what restores the action economy and ages conditions, so
    "the same creature tries again next turn" must never be faked with a
    second intent inside one turn.
    """

    _end_turn(engine, instance)
    _to_actor(engine, instance, actor_id)


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _attack_intent(instance, *, uid: str = "gm", **extra) -> dict:
    intent: dict[str, Any] = {
        "intent_id": f"attack-{ruleset_runtime.state(instance)['version']}",
        "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": uid, "actor_id": f"player:{uid}",
        "target_id": "enemy:goblin-1",
    }
    intent.update(extra)
    return intent


def _attack(engine, instance, *, uid: str = "gm", rolls=(), **extra) -> list[dict]:
    return _apply(engine, instance, _attack_intent(instance, uid=uid, **extra), rolls)


def _enemy_attack(engine, instance, *, actor_id: str, target_id: str, rolls=()) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": f"enemy-attack-{ruleset_runtime.state(instance)['version']}",
        "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id, "target_id": target_id,
        "attack_id": "scimitar",
    }, rolls)


def _check(events: list[dict], kind: str = "attack") -> dict:
    return next(
        event for event in events
        if event["type"] == "check.resolved" and event.get("kind") == kind
    )


def _decline_windows(engine, instance, events: list[dict]) -> list[dict]:
    """Answer every pending decision with "decline".

    A hit on a party member opens the defender's reaction-spell window, and a
    pending window blocks the next intent.  A test about what the *attacker*
    declared has to close it rather than pretend it is not there.
    """

    events = list(events)
    pending = [event for event in events if event["type"] == "dnd2024.decision.pending"]
    while pending:
        decision = pending.pop(0)
        events.extend(_apply(engine, instance, {
            "intent_id": f"decline-{decision['decision_id']}",
            "type": "decision.resolve",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": str(decision.get("assigned_to") or "gm"),
            "decision_id": decision["decision_id"], "option": "decline",
        }))
    return events


def _damage(events: list[dict], feature_id: str | None = None) -> list[dict]:
    return [
        event for event in events
        if event["type"] == "resource.changed" and event.get("resource") == "hp"
        and (feature_id is None or event.get("feature_id") == feature_id)
    ]


def _conditions(instance, actor_id: str = "enemy:goblin-1") -> dict:
    kind = actor_id.split(":", 1)[0]
    if kind == "enemy":
        return _combat_state(instance)["enemies"][actor_id.split(":", 1)[1]]["conditions"]
    return _canonical(instance, actor_id.split(":", 1)[1]).get("conditions") or {}


def _codes(condition_map: dict) -> set[str]:
    return {str(code) for code in condition_map}


def _attack_options(engine, instance, uid: str = "gm") -> dict:
    row = next(
        item for item in engine.available_intents(instance, uid)
        if item["type"] == "attack"
    )
    return row


def _enter_rage(engine, instance, uid: str = "gm") -> list[dict]:
    return _apply(
        engine, instance,
        capability_intent(engine, instance, "rage", intent_id="rage-1", uid=uid, target_id=""),
    )


# --------------------------------------------------------------------------
# 1. the five declarations, their levels and their names
# --------------------------------------------------------------------------

def test_the_five_declarations_are_granted_where_the_srd_puts_them(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 1: level gates, the 13/17 upgrade and both locales."""

    english = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    chinese = Dnd2024ClassFeatureResolver(runtime.load_bundle("zh-CN"))

    def views(sheet: dict) -> dict[str, Any]:
        return {
            view.id: view
            for view in english.feature_views(sheet["ruleset_character"])
        }

    low = views(barbarian(1))
    assert "reckless_attack" not in low and "brutal_strike" not in low

    second = views(barbarian(2))["reckless_attack"]
    assert second.minimum_level == 2
    assert second.owner == "class:barbarian"
    assert second.source_ref == "srd-5.2.1:p29:reckless-attack"
    assert second.values["ability"] == "str"
    assert second.values["self_condition"] == "reckless"

    third = views(barbarian(3))["frenzy"]
    assert third.owner == "subclass:path_of_the_berserker"
    assert third.minimum_level == 3
    assert third.source_ref == "srd-5.2.1:p30:frenzy"
    # Frenzy rolls one d6 per point of Rage Damage, read from the Rage track:
    # the projection resolves the track into a count at this level.
    assert third.values["damage_die_sides"] == 6
    assert third.values["dice_count"] == 2
    assert third.values["damage_type_from_attack"] is True
    assert third.values["requires_declaration"] == "reckless_attack"

    assert "brutal_strike" not in views(barbarian(8))
    ninth = views(barbarian(9))["brutal_strike"]
    assert ninth.minimum_level == 9
    assert ninth.source_ref == "srd-5.2.1:p30:brutal-strike"
    assert ninth.values["damage_dice"] == "1d10"
    assert ninth.values["effect_options"] == ["forceful", "hamstring"]

    # Improved Brutal Strike is one declaration with two level upgrades: the
    # progression table grants it at 13 and again at 17, but a second node
    # would collide on (owner, id), so 17 is a field, not a second feature.
    assert "improved_brutal_strike" not in views(barbarian(12))
    thirteenth = views(barbarian(13))
    improved = thirteenth["improved_brutal_strike"]
    assert improved.minimum_level == 13
    assert improved.source_ref == "srd-5.2.1:p30:improved-brutal-strike"
    assert improved.values["additional_effect_options"] == ["staggering", "sundering"]
    assert thirteenth["brutal_strike"].values["damage_dice"] == "1d10"
    assert thirteenth["brutal_strike"].values["upgrade_level"] == 17
    assert thirteenth["brutal_strike"].values["damage_dice_upgrade"] == "2d10"
    assert thirteenth["brutal_strike"].values["effects_per_use"] == 1
    assert thirteenth["brutal_strike"].values["effects_per_use_upgrade"] == 2

    assert "studied_attacks" not in views(fighter(12))
    studied = views(fighter(13))["studied_attacks"]
    assert studied.owner == "class:fighter"
    assert studied.source_ref == "srd-5.2.1:p48:studied-attacks"
    assert studied.values["mark_kind"] == "studied"
    assert studied.values["expiry"] == "attacker_next_turn_end"

    assert english.catalog.label("reckless_attack") == "Reckless Attack"
    assert english.catalog.label("staggering_blow") == "Staggering Blow"
    assert chinese.catalog.label("reckless_attack") == "鲁莽攻击"
    assert chinese.catalog.label("frenzy") == "狂暴"
    assert chinese.catalog.label("brutal_strike") == "残暴打击"
    assert chinese.catalog.label("improved_brutal_strike") == "精通残暴打击"
    assert chinese.catalog.label("studied_attacks") == "研习攻击"
    assert chinese.catalog.label("forceful_blow") == "强猛一击"
    assert chinese.catalog.label("hamstring_blow") == "断筋一击"
    assert chinese.catalog.label("staggering_blow") == "震慑一击"
    assert chinese.catalog.label("sundering_blow") == "破甲一击"


# --------------------------------------------------------------------------
# 2. the Reckless window
# --------------------------------------------------------------------------

def test_reckless_is_only_declared_before_the_first_attack_roll_of_the_turn(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 2: before the first roll of your own turn, and only then."""

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    panel = _attack_options(engine, instance)
    assert panel["reckless_attack"] == {
        "available": True, "condition": "reckless", "ability": "str",
    }

    events = _attack(engine, instance, weapon_ref="item:greataxe",
                     reckless_attack=True, rolls=[12, 7, 3])
    assert [event["type"] for event in events].count("condition.applied") == 1
    assert _check(events)["advantage_mode"] == "advantage"

    # Declaring it a second time in the same turn would only re-stamp the
    # expiry, and the roll ledger already says the window is gone.
    assert _attack_options(engine, instance)["reckless_attack"]["available"] is False
    assert "already attacking recklessly" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True),
    )

    # A plain first attack closes the window even though nothing is on the
    # character: the ledger, not the condition, is what "first roll" means.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    _attack(engine, instance, weapon_ref="item:greataxe", rolls=[12, 3])
    assert "before the first attack roll" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True),
    )

    # Someone else's turn is not a declaration window at all.  The generic turn
    # gate answers first, which is exactly why the "own turn" half of the
    # declaration guard is only reachable from a reaction-granted attack.
    engine, instance = _combat(
        runtime, {"gm": barbarian(17), "ally": cleric()}, initiative={"gm": 20, "ally": 15},
    )
    _to_actor(engine, instance, "player:ally")
    assert "not this actor's turn" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True),
    )


# --------------------------------------------------------------------------
# 3. what Reckless grants, in both directions
# --------------------------------------------------------------------------

def test_reckless_grants_advantage_to_strength_rolls_and_against_itself(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 3: own Strength rolls, incoming rolls, and not a Dex roll."""

    engine, instance = _combat(
        runtime, {"gm": with_equipment(_RUNTIME, barbarian(17), "item:greataxe", "item:shortbow")},
        initiative={"gm": 20},
    )
    events = _attack(engine, instance, weapon_ref="item:greataxe",
                     reckless_attack=True, rolls=[12, 7, 3])
    check = _check(events)
    assert check["advantage_mode"] == "advantage"
    assert {"code": "reckless", "mode": "advantage"} in check["advantage_sources"]
    assert check["rolls"] == [12, 7] and check["natural"] == 12

    # The declaration is not limited to Strength attacks; the *Advantage* is.
    dex = _check(_attack(engine, instance, weapon_ref="item:shortbow", rolls=[13, 3]))
    assert dex["advantage_mode"] == ""
    assert "reckless" not in {row["code"] for row in dex["advantage_sources"]}

    # Attacks against the reckless creature have Advantage too: the condition
    # names the side it applies to, so the enemy's roll reads the same data.
    _to_actor(engine, instance, "enemy:goblin-1")
    incoming = _check(_decline_windows(engine, instance, _enemy_attack(
        engine, instance, actor_id="enemy:goblin-1", target_id="player:gm", rolls=[11, 4],
    )))
    assert incoming["advantage_mode"] == "advantage"
    assert {"code": "reckless", "mode": "advantage"} in incoming["advantage_sources"]

    # ... until the start of the barbarian's next turn, and not a moment later.
    _to_actor(engine, instance, "player:gm")
    assert "reckless" not in _codes(_conditions(instance, "player:gm"))


def test_reckless_and_a_second_disadvantage_cancel_into_one_d20(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 4: sources cancel without a third die, and the mode is empty."""

    sheet = without_heavy_armour_training(
        with_equipment(_RUNTIME, barbarian(17), "item:greataxe", "item:chain_mail")
    )
    engine, instance = _combat(runtime, {"gm": sheet}, initiative={"gm": 20})

    events = _attack(engine, instance, weapon_ref="item:greataxe",
                     reckless_attack=True, rolls=[12, 3])
    check = _check(events)
    assert check["advantage_mode"] == ""
    assert len(check["rolls"]) == 1
    assert {row["code"]: row["mode"] for row in check["advantage_sources"]} == {
        "reckless": "advantage", "untrained_armor": "disadvantage",
    }
    assert check["natural"] == 12

    # Expiry is one-sided by design: the armour is still worn, so only the
    # Reckless half disappears and the mode flips to Disadvantage.
    _next_turn(engine, instance, "player:gm")
    assert "reckless" not in _codes(_conditions(instance, "player:gm"))
    later = _check(_attack(engine, instance, weapon_ref="item:greataxe", rolls=[9, 3]))
    assert later["advantage_mode"] == "disadvantage"
    assert {row["code"] for row in later["advantage_sources"]} == {"untrained_armor"}


# --------------------------------------------------------------------------
# 5-6. Frenzy
# --------------------------------------------------------------------------

def test_frenzy_waits_for_the_first_strength_hit_after_reckless(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 5: a miss does not spend the turn's Frenzy."""

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    _enter_rage(engine, instance)

    # Advantage, both dice 1: still a miss, and no extra damage is rolled.
    missed = _attack(engine, instance, weapon_ref="item:greataxe",
                     reckless_attack=True, rolls=[1, 1])
    assert _check(missed)["success"] is False
    assert _damage(missed, "frenzy") == []

    hit = _attack(engine, instance, weapon_ref="item:greataxe", rolls=[12, 7, 3, 5, 5, 4, 4])
    frenzy = _damage(hit, "frenzy")
    assert len(frenzy) == 1
    assert frenzy[0]["rolls"] == [5, 5, 4, 4]
    assert frenzy[0]["amount"] == 18
    assert frenzy[0]["damage_type"] == "slashing"


def test_frenzy_pays_only_once_per_turn(runtime: Dnd2024Runtime) -> None:
    """Matrix 5: the second hit of the same turn gains nothing."""

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    _enter_rage(engine, instance)

    first = _attack(engine, instance, weapon_ref="item:greataxe",
                    reckless_attack=True, rolls=[12, 7, 3, 5, 5, 4, 4])
    assert len(_damage(first, "frenzy")) == 1
    second = _attack(engine, instance, weapon_ref="item:greataxe", rolls=[13, 8, 6])
    assert _damage(second, "frenzy") == []


def test_frenzy_needs_rage_a_strength_attack_and_the_declaration(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 6: the dice count is the current Rage Damage, on a Strength hit."""

    # No Rage: Reckless alone pays nothing here.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    no_rage = _attack(engine, instance, weapon_ref="item:greataxe",
                      reckless_attack=True, rolls=[12, 7, 3])
    assert _damage(no_rage, "frenzy") == []

    # Rage, but a Dex attack: the extra damage is tied to Strength.
    engine, instance = _combat(
        runtime,
        {"gm": with_equipment(_RUNTIME, barbarian(17), "item:greataxe", "item:shortbow")},
        initiative={"gm": 20},
    )
    _enter_rage(engine, instance)
    dex = _attack(engine, instance, weapon_ref="item:shortbow",
                  reckless_attack=True, rolls=[12, 3])
    assert _damage(dex, "frenzy") == []

    # Rage Damage is read live: a 9th-level Berserker rolls 3d6, a 17th rolls 4d6.
    for level, count in ((9, 3), (17, 4)):
        engine, instance = _combat(runtime, {"gm": barbarian(level)}, initiative={"gm": 20})
        _enter_rage(engine, instance)
        events = _attack(engine, instance, weapon_ref="item:greataxe",
                         reckless_attack=True, rolls=[12, 7, 3, *([4] * count)])
        frenzy = _damage(events, "frenzy")
        assert len(frenzy) == 1
        assert len(frenzy[0]["rolls"]) == count
        assert frenzy[0]["damage_type"] == "slashing"


# --------------------------------------------------------------------------
# 7-9. Brutal Strike: who may declare it, what it forgoes, what it pays
# --------------------------------------------------------------------------

@pytest.mark.parametrize("selection", [
    {"brutal_strike": True}, {"attack_declarations": ["brutal_strike"]},
])
def test_brutal_strike_needs_reckless_a_strength_attack_and_a_free_budget(
    runtime: Dnd2024Runtime, selection,
) -> None:
    """Matrix 7: every refusal is a rejection, never a silent plain attack."""

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    assert "requires reckless attack" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", **selection),
    )
    # The panel still offers Brutal at the top of the turn, because Reckless may
    # be declared in the same intent; it is the *pair* that is legal, never the
    # Brutal alone.
    assert _attack_options(engine, instance)["brutal_strike"]["available"] is True

    engine, instance = _combat(
        runtime,
        {"gm": with_equipment(_RUNTIME, barbarian(17), "item:greataxe", "item:shortbow")},
        initiative={"gm": 20},
    )
    assert "Strength-based attack" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:shortbow", **selection),
    )

    # Once used -- hit or miss -- the turn's Brutal is gone.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            rolls=[12, 3, 5, 5], **selection)
    assert "already used this turn" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", **selection),
    )
    assert _attack_options(engine, instance)["brutal_strike"]["available"] is False


def test_brutal_strike_refuses_any_disadvantage_and_rolls_one_die(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 8: "mustn't have Disadvantage" is about sources, not the mode."""

    sheet = without_heavy_armour_training(
        with_equipment(_RUNTIME, barbarian(17), "item:greataxe", "item:chain_mail")
    )
    engine, instance = _combat(runtime, {"gm": sheet}, initiative={"gm": 20})
    # Reckless Advantage and armour Disadvantage cancel, so the mode is empty --
    # and Brutal is still refused, because a source of Disadvantage exists.
    assert "attack has disadvantage" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe",
                       reckless_attack=True, brutal_strike=True),
    )

    # Without it: all Advantage is forgone, the sources stay on the record and
    # exactly one d20 is rolled.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    check = _check(_attack(
        engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
        brutal_strike=True, rolls=[12, 3, 4, 4],
    ))
    assert check["brutal_strike"] is True
    assert check["advantage_mode"] == ""
    assert check["rolls"] == [12]
    assert {"code": "reckless", "mode": "advantage"} in check["advantage_sources"]


def test_brutal_strike_pays_one_d10_until_seventeen_and_two_after(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 9: 1d10 at 9-16, 2d10 at 17, and a miss pays nothing."""

    engine, instance = _combat(runtime, {"gm": barbarian(13)}, initiative={"gm": 20})
    thirteen = _attack(engine, instance, weapon_ref="item:greataxe",
                       reckless_attack=True, brutal_strike=True, rolls=[12, 6, 9])
    damage = _damage(thirteen, "brutal_strike")
    assert len(damage) == 1
    assert damage[0]["rolls"] == [9] and damage[0]["amount"] == 9
    assert damage[0]["damage_type"] == "slashing"

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    seventeen = _attack(engine, instance, weapon_ref="item:greataxe",
                        reckless_attack=True, brutal_strike=True,
                        brutal_strike_effects=["hamstring"],
                        rolls=[12, 6, 7, 8])
    damage = _damage(seventeen, "brutal_strike")
    assert damage[0]["rolls"] == [7, 8] and damage[0]["amount"] == 15

    # A miss: no extra damage, no effect, and the turn's Brutal is spent.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    missed = _attack(engine, instance, weapon_ref="item:greataxe",
                     reckless_attack=True, brutal_strike=True,
                     brutal_strike_effects=["forceful"], rolls=[1, 1])
    assert _check(missed)["success"] is False
    assert _damage(missed, "brutal_strike") == []
    assert "hamstrung" not in _codes(_conditions(instance))
    assert _combat_state(instance)["positions"]["enemy:goblin-1"] == 5
    assert "already used this turn" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", brutal_strike=True),
    )


def test_frenzy_and_brutal_strike_settle_as_separate_instances(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 10: one hit, three damage instances, one shared crit rule."""

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    _enter_rage(engine, instance)
    events = _attack(engine, instance, weapon_ref="item:greataxe",
                     reckless_attack=True, brutal_strike=True,
                     rolls=[12, 8, 5, 5, 4, 4, 3, 3])

    # Declaration, then the roll, then the damage: the pre-roll events are
    # ordered so a replay reconstructs the same turn.
    types = [event["type"] for event in events]
    assert types.index("condition.applied") < types.index("dnd2024.attack_roll.recorded")
    assert types.index("dnd2024.attack_roll.recorded") < types.index("dnd2024.feature.used")
    assert types.index("dnd2024.feature.used") < types.index("check.resolved")

    weapon = _damage(events, None)[0]
    frenzy = _damage(events, "frenzy")[0]
    brutal = _damage(events, "brutal_strike")[0]
    # Dice are drawn in the order they are declared: weapon, Frenzy, Brutal.
    assert weapon["rolls"] == [8]
    assert frenzy["rolls"] == [5, 5, 4, 4]
    assert brutal["rolls"] == [3, 3]
    assert {row["damage_type"] for row in (weapon, frenzy, brutal)} == {"slashing"}
    assert len({row["delta"] for row in (weapon, frenzy, brutal)}) == 3

    # A critical hit doubles every one of them, each as its own instance.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    _enter_rage(engine, instance)
    critical = _attack(engine, instance, weapon_ref="item:greataxe",
                       reckless_attack=True, brutal_strike=True,
                       rolls=[20, 7, 1, 1, *([2] * 8), 3, 3, 3, 3])
    assert _check(critical)["critical"] is True
    assert len(_damage(critical, "frenzy")[0]["rolls"]) == 8
    assert len(_damage(critical, "brutal_strike")[0]["rolls"]) == 4


# --------------------------------------------------------------------------
# 11-12. the four options and their budget
# --------------------------------------------------------------------------

def test_brutal_options_unlock_at_thirteen_and_double_at_seventeen(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 11: 9 -> two options, 13 -> four, 17 -> two effects per use."""

    ninth_engine, ninth_instance = _combat(runtime, {"gm": barbarian(9)}, initiative={"gm": 20})
    ninth = _attack_options(ninth_engine, ninth_instance)
    assert ninth["brutal_strike"]["effects"] == ["forceful", "hamstring"]
    assert ninth["brutal_strike"]["count"] == 1

    thirteen_engine, thirteen_instance = _combat(
        runtime, {"gm": barbarian(13)}, initiative={"gm": 20},
    )
    thirteenth = _attack_options(thirteen_engine, thirteen_instance)
    assert thirteenth["brutal_strike"]["effects"] == [
        "forceful", "hamstring", "staggering", "sundering",
    ]
    assert thirteenth["brutal_strike"]["count"] == 1

    seventeen_engine, seventeen_instance = _combat(
        runtime, {"gm": barbarian(17)}, initiative={"gm": 20},
    )
    seventeenth = _attack_options(seventeen_engine, seventeen_instance)
    assert seventeenth["brutal_strike"]["count"] == 2

    # Declining every option still pays the extra damage.
    engine, instance = _combat(runtime, {"gm": barbarian(13)}, initiative={"gm": 20})
    declined = _attack(engine, instance, weapon_ref="item:greataxe",
                       reckless_attack=True, brutal_strike=True, rolls=[12, 3, 6])
    assert _damage(declined, "brutal_strike")[0]["amount"] == 6
    assert _codes(_conditions(instance)) == set()

    # Two different options at 17 land as two independent conditions/moves.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    both = _attack(engine, instance, weapon_ref="item:greataxe",
                   reckless_attack=True, brutal_strike=True,
                   brutal_strike_effects=["hamstring", "staggering"],
                   rolls=[12, 3, 5, 5])
    assert {"hamstrung", "next_save_disadvantage", "opportunity_attack_blocked"} <= _codes(
        _conditions(instance)
    )
    assert _damage(both, "brutal_strike")[0]["amount"] == 10


def test_illegal_brutal_option_selections_leave_no_partial_state(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 12: the refusals name the reason and record nothing."""

    engine, instance = _combat(runtime, {"gm": barbarian(9)}, initiative={"gm": 20})
    assert "not available: staggering" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True,
                       brutal_strike=True, brutal_strike_effects=["staggering"]),
    )

    engine, instance = _combat(runtime, {"gm": barbarian(13)}, initiative={"gm": 20})
    assert "allows at most 1 effect" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True,
                       brutal_strike=True,
                       brutal_strike_effects=["forceful", "hamstring"]),
    )

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    assert "allows at most 2 effect" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True,
                       brutal_strike=True,
                       brutal_strike_effects=["forceful", "hamstring", "sundering"]),
    )
    assert "cannot apply the same effect twice" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True,
                       brutal_strike=True, brutal_strike_effects=["forceful", "forceful"]),
    )
    # Nothing landed: the goblin is untouched and the turn is not spent.
    assert _codes(_conditions(instance)) == set()
    assert _combat_state(instance)["economy"]["actor_id"] == "player:gm"
    assert _attack_options(engine, instance)["brutal_strike"]["available"] is True


# --------------------------------------------------------------------------
# 13. Forceful Blow
# --------------------------------------------------------------------------

def _two_goblins() -> list[dict]:
    second = {**goblin(position=-5, hp=200), "id": "goblin-2", "name": "Goblin 2"}
    return [goblin(position=5, hp=200), second]


def test_forceful_blow_pushes_fifteen_feet_and_licenses_the_follow_move(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 13: a straight 15-foot push, then half a Speed toward it."""

    engine, instance = _combat(
        runtime, {"gm": barbarian(17)}, initiative={"gm": 20, "ally": 15}, enemies=_two_goblins(),
    )
    events = _attack(engine, instance, weapon_ref="item:greataxe",
                     reckless_attack=True, brutal_strike=True,
                     brutal_strike_effects=["forceful"], rolls=[12, 3, 5, 5])
    pushed = next(
        event for event in events if event["type"] == "dnd2024.position.changed"
    )
    assert (pushed["actor_id"], pushed["distance"], pushed["forced"]) == (
        "enemy:goblin-1", 15, True,
    )
    granted = next(
        event for event in events if event["type"] == "dnd2024.movement.granted"
        and event.get("reason") == "brutal_forceful"
    )
    assert granted["target_id"] == "enemy:goblin-1"
    # Half of the barbarian's 40-foot Speed.
    assert granted["amount"] == 20
    assert _combat_state(instance)["economy"]["move_allowance"] == {
        "amount": 20, "reason": "brutal_forceful", "target_id": "enemy:goblin-1",
    }

    # Moving back toward the pushed creature is inside the allowance, so the
    # opportunity attack the second Goblin would otherwise threaten is
    # suppressed and the allowance is consumed.
    moved = _apply(engine, instance, {
        "intent_id": "move-1", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "distance": 15,
    })
    step = next(event for event in moved if event["type"] == "dnd2024.position.changed")
    assert step["move_allowance"] is True
    assert not any(event["type"] == "dnd2024.decision.pending" for event in moved)
    assert "move_allowance" not in _combat_state(instance)["economy"]


def test_forceful_follow_move_refuses_the_wrong_direction_and_extra_distance(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 13: the allowance is a direction *and* a budget."""

    def pushed_then(distance: int) -> list[dict]:
        engine, instance = _combat(
            runtime, {"gm": barbarian(17)}, initiative={"gm": 20, "ally": 15}, enemies=_two_goblins(),
        )
        _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
                brutal_strike=True, brutal_strike_effects=["forceful"],
                rolls=[12, 3, 5, 5])
        return _apply(engine, instance, {
            "intent_id": "move-1", "type": "move",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm", "actor_id": "player:gm", "distance": distance,
        })

    # Away from the pushed creature: ordinary movement, so the second Goblin
    # gets its opportunity attack window and the allowance survives.
    away = pushed_then(-15)
    assert any(event["type"] == "dnd2024.decision.pending" for event in away)

    # Toward it, but further than half a Speed: the same window opens.
    too_far = pushed_then(25)
    assert any(event["type"] == "dnd2024.decision.pending" for event in too_far)


# --------------------------------------------------------------------------
# 14. Hamstring Blow
# --------------------------------------------------------------------------

def test_hamstring_reduces_speed_by_fifteen_and_keeps_only_the_latest(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 14: -15 until the attacker's next turn, never stacking to -30."""

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    goblin_state = _combat_state(instance)["enemies"]["goblin-1"]
    before = int(goblin_state["speed"])
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, brutal_strike_effects=["hamstring"],
            rolls=[12, 3, 5, 5])
    stored = _conditions(instance)["hamstrung"]
    assert stored["source_feature"] == "brutal_strike"
    assert stored["source_actor_id"] == "player:gm"
    after = effective_speed(
        engine.catalog.conditions, _conditions(instance), before,
    )
    assert (before, after) == (30, 15)

    # A second application replaces the first rather than adding another -15.
    _next_turn(engine, instance, "player:gm")
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, brutal_strike_effects=["hamstring"],
            rolls=[12, 3, 5, 5])
    assert list(_conditions(instance)) == ["hamstrung"]
    assert effective_speed(
        engine.catalog.conditions, _conditions(instance), before,
    ) == 15

    # ... and it expires at the start of the attacker's next turn.
    _next_turn(engine, instance, "player:gm")
    assert "hamstrung" not in _codes(_conditions(instance))


# --------------------------------------------------------------------------
# 15. Staggering Blow
# --------------------------------------------------------------------------

def test_staggering_consumes_the_next_save_and_blocks_reactions_separately(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 15: two halves, two lifetimes."""

    engine, instance = _combat(
        runtime, {"gm": barbarian(17), "ally": cleric()}, initiative={"gm": 20, "ally": 15},
    )
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, brutal_strike_effects=["staggering"],
            rolls=[12, 3, 5, 5])
    applied = _conditions(instance)
    assert {"next_save_disadvantage", "opportunity_attack_blocked"} <= _codes(applied)
    # The saving-throw half carries no duration: only a real save consumes it.
    assert "duration" not in applied["next_save_disadvantage"]
    assert applied["opportunity_attack_blocked"]["duration"] == "attacker_next_turn_start"

    # Another creature forces the save; it is rolled with Disadvantage.
    _to_actor(engine, instance, "player:ally")
    saved = _check(_apply(engine, instance, {
        "intent_id": "cast-1", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "ally", "actor_id": "player:ally",
        "spell_ref": "spell:sacred_flame", "target_id": "enemy:goblin-1",
        "slot_level": 0,
    }, rolls=[18, 4, 3]), kind="saving_throw")
    assert saved["advantage_mode"] == "disadvantage"
    assert "next_save_disadvantage" in {
        row["code"] for row in saved["advantage_sources"]
    }
    assert saved["natural"] == 4

    # The save is spent; the reaction block is not its business.
    after_save = _codes(_conditions(instance))
    assert "next_save_disadvantage" not in after_save
    assert "opportunity_attack_blocked" in after_save

    _to_actor(engine, instance, "player:gm")
    assert "opportunity_attack_blocked" not in _codes(_conditions(instance))


def test_staggering_also_denies_opportunity_attacks(runtime: Dnd2024Runtime) -> None:
    """Matrix 15: the reaction half is behaviour, not just a condition on a card."""

    def walk_away(*, staggering: bool) -> list[dict]:
        engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
        if staggering:
            _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
                    brutal_strike=True, brutal_strike_effects=["staggering"],
                    rolls=[12, 6, 5, 5])
            assert "opportunity_attack_blocked" in _codes(_conditions(instance))
        else:
            _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
                    rolls=[12, 7, 3])
            assert "opportunity_attack_blocked" not in _codes(_conditions(instance))
        return _apply(engine, instance, {
            "intent_id": "move-1", "type": "move",
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm", "actor_id": "player:gm", "distance": -15,
        })

    # The control: an adjacent, unblocked Goblin threatens the leaving attack.
    assert any(
        event["type"] == "dnd2024.decision.pending" for event in walk_away(staggering=False)
    )
    # Staggered, it cannot take that reaction at all.
    assert not any(
        event["type"] == "dnd2024.decision.pending" for event in walk_away(staggering=True)
    )


# --------------------------------------------------------------------------
# 16-17. Sundering Blow
# --------------------------------------------------------------------------

def test_sundering_helps_only_another_creatures_next_attack(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 16: +5, once, for someone else."""

    engine, instance = _combat(
        runtime,
        {"gm": with_equipment(_RUNTIME, barbarian(17), "item:greataxe"),
         "ally": with_equipment(_RUNTIME, cleric(), "item:mace")},
        initiative={"gm": 20, "ally": 15},
    )
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, brutal_strike_effects=["sundering"],
            rolls=[12, 3, 5, 5])
    assert "sundered" in _codes(_conditions(instance))

    # The Barbarian's own next swing is not "another creature": no bonus, and
    # the condition survives to be spent by whoever earns it.
    own = _check(_attack(engine, instance, weapon_ref="item:greataxe", rolls=[13, 3]))
    assert "sundering_bonus" not in own
    assert "sundered" in _codes(_conditions(instance))

    _to_actor(engine, instance, "player:ally")
    ally = _check(_attack(engine, instance, uid="ally", weapon_ref="item:mace",
                          rolls=[11, 4]))
    assert ally["sundering_bonus"] == 5
    # The +5 is its own field, not folded into the attacker's own bonus.
    assert ally["total"] == ally["natural"] + ally["modifier"] + 5
    assert "sundered" not in _codes(_conditions(instance))


def test_one_sundering_bonus_per_attack_and_the_mark_expires(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 17: no +10, and the state is a condition, not a counter."""

    engine, instance = _combat(
        runtime,
        {"gm": with_equipment(_RUNTIME, barbarian(17), "item:greataxe"),
         "ally": with_equipment(_RUNTIME, cleric(), "item:mace")},
        initiative={"gm": 20, "ally": 15},
    )
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, brutal_strike_effects=["sundering"],
            rolls=[12, 3, 5, 5])
    stored = _conditions(instance)["sundered"]
    assert stored["attack_bonus"] == 5
    assert stored["duration"] == "attacker_next_turn_start"

    # A condition is keyed by id: two applications cannot become +10, and the
    # next beneficiary still reads a single +5.
    _next_turn(engine, instance, "player:gm")
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, brutal_strike_effects=["sundering"],
            rolls=[12, 3, 5, 5])
    assert list(_conditions(instance)) == ["sundered"]

    _to_actor(engine, instance, "player:ally")
    ally = _check(_attack(engine, instance, uid="ally", weapon_ref="item:mace",
                          rolls=[11, 4]))
    assert ally["sundering_bonus"] == 5

    # The mark is gone after that one roll, so the next attack is plain.
    _next_turn(engine, instance, "player:ally")
    plain = _check(_attack(engine, instance, uid="ally", weapon_ref="item:mace",
                           rolls=[11, 4]))
    assert "sundering_bonus" not in plain


# --------------------------------------------------------------------------
# 18-20. Studied Attacks
# --------------------------------------------------------------------------

def test_studied_attacks_only_helps_against_the_creature_it_studied(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 18: the mark is per target, not a mode on the Fighter."""

    engine, instance = _combat(
        runtime, {"gm": with_equipment(_RUNTIME, fighter(13), "item:longsword")},
        initiative={"gm": 20, "ally": 15}, enemies=_two_goblins(),
    )
    missed = _attack(engine, instance, weapon_ref="item:longsword", rolls=[1])
    assert _check(missed)["success"] is False
    marks = _combat_state(instance)["attack_marks"]["studied"]
    assert marks["player:gm"]["enemy:goblin-1"]["expiry"] == "attacker_next_turn_end"

    other = _check(_attack(engine, instance, weapon_ref="item:longsword",
                           target_id="enemy:goblin-2", rolls=[13]))
    assert other["advantage_mode"] == ""
    assert "studied_attacks" not in {row["code"] for row in other["advantage_sources"]}

    studied = _check(_attack(engine, instance, weapon_ref="item:longsword", rolls=[14, 6]))
    assert studied["advantage_mode"] == "advantage"
    assert {"code": "studied_attacks", "mode": "advantage"} in studied["advantage_sources"]
    assert studied["natural"] == 14


def test_a_studied_mark_is_spent_by_the_roll_and_never_by_its_own_creation(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 19: consume before the roll, renew only after a second miss."""

    engine, instance = _combat(
        runtime, {"gm": with_equipment(_RUNTIME, fighter(13), "item:longsword")},
        initiative={"gm": 20},
    )
    _attack(engine, instance, weapon_ref="item:longsword", rolls=[1])

    events = _attack(engine, instance, weapon_ref="item:longsword", rolls=[1, 1])
    types = [event["type"] for event in events]
    # Order is the whole rule: the old mark is consumed *before* the roll, so
    # the new one cannot pay for the attack that creates it.
    assert types.index("dnd2024.attack_mark.cleared") < types.index("check.resolved")
    assert types.index("check.resolved") < types.index("dnd2024.attack_mark.set")
    assert _check(events)["success"] is False
    marks = _combat_state(instance)["attack_marks"]["studied"]["player:gm"]
    assert list(marks) == ["enemy:goblin-1"]
    assert marks["enemy:goblin-1"]["turns_remaining"] == 1


def test_studied_marks_age_out_after_the_following_turn(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 20: one turn of grace, and only an attack roll spends it."""

    engine, instance = _combat(
        runtime, {"gm": with_equipment(_RUNTIME, fighter(13), "item:longsword")},
        initiative={"gm": 20},
    )
    _attack(engine, instance, weapon_ref="item:longsword", rolls=[1])
    marks = lambda: _combat_state(instance)["attack_marks"]["studied"]["player:gm"]

    # A Bonus Action that is not an attack roll does not touch the mark.
    _apply(engine, instance, capability_intent(
        engine, instance, "second_wind", intent_id="heal-1", target_id="",
    ))
    assert list(marks()) == ["enemy:goblin-1"]

    # The next turn starts: the counter drops to 0 and the mark still works.
    _next_turn(engine, instance, "player:gm")
    assert marks()["enemy:goblin-1"]["turns_remaining"] == 0
    check = _check(_attack(engine, instance, weapon_ref="item:longsword", rolls=[11, 7]))
    assert {"code": "studied_attacks", "mode": "advantage"} in check["advantage_sources"]
    assert marks() == {}

    # A fresh miss starts a new mark, and that one dies at the second turn
    # start without ever being spent.
    _attack(engine, instance, weapon_ref="item:longsword", rolls=[2])
    assert marks()["enemy:goblin-1"]["turns_remaining"] == 1
    _next_turn(engine, instance, "player:gm")
    assert marks()["enemy:goblin-1"]["turns_remaining"] == 0
    _next_turn(engine, instance, "player:gm")
    assert marks() == {}


# --------------------------------------------------------------------------
# 21-22. fail closed, and no collateral damage to the existing chain
# --------------------------------------------------------------------------

def test_the_declared_state_survives_a_save_and_a_replay(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 17/21: a reloaded turn is the same turn, not a second one."""

    engine, instance = _combat(
        runtime,
        {"gm": with_equipment(_RUNTIME, barbarian(17), "item:greataxe"),
         "ally": with_equipment(_RUNTIME, cleric(), "item:mace")},
        initiative={"gm": 20, "ally": 15},
    )
    declared = _attack_intent(
        instance, weapon_ref="item:greataxe", reckless_attack=True,
        brutal_strike=True, brutal_strike_effects=["hamstring", "sundering"],
    )
    _apply(engine, instance, declared, [12, 6, 5, 5])

    restored = GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))
    goblin_conditions = _conditions(restored)
    assert {"hamstrung", "sundered"} <= _codes(goblin_conditions)
    assert goblin_conditions["sundered"]["attack_bonus"] == 5
    assert effective_speed(
        engine.catalog.conditions, goblin_conditions, 30,
    ) == 15
    state = ruleset_runtime.state(restored)["combat"]
    assert state["attack_rolls"]["player:gm"]["count"] == 1

    # The reloaded instance still knows the turn's Brutal is spent ...
    assert "already used this turn" in _rejected(
        engine, restored,
        _attack_intent(restored, weapon_ref="item:greataxe", brutal_strike=True),
    )
    # ... and the +5 is still one bonus for one other creature.
    _to_actor(engine, restored, "player:ally")
    ally = _check(_attack(engine, restored, uid="ally", weapon_ref="item:mace",
                          rolls=[11, 4]))
    assert ally["sundering_bonus"] == 5

    # Replaying the recorded intent against the reloaded state is a duplicate,
    # so nothing is applied twice.
    replay = engine.resolve_intent(restored, declared, SequenceRng([]))
    assert replay["ok"] is True and replay.get("replayed") is True, replay
    applied = engine.apply_batch(restored, replay["event_batch"])
    assert applied["duplicate"] is True
    assert ruleset_runtime.state(restored)["combat"]["attack_rolls"]["player:gm"] == {
        "round": 1, "turn_index": 0, "count": 1,
    }


def test_stale_versions_forged_fields_and_replays_fail_closed(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 21: the ledger, not the client, decides what happened."""

    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    intent = _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True)

    stale = dict(intent, expected_version=int(intent["expected_version"]) - 1)
    assert "version" in _rejected(engine, instance, stale).lower()

    # A client cannot hand itself Advantage, damage or a Brutal option.
    forged = _attack_intent(
        instance, weapon_ref="item:greataxe",
        advantage_mode="advantage", brutal_strike_effects=["forceful"],
        damage="99", brutal_strike=True,
    )
    rejected = _rejected(engine, instance, forged)
    assert "requires reckless attack" in rejected

    plain = _attack(engine, instance, weapon_ref="item:greataxe",
                    advantage_mode="advantage", rolls=[9, 3])
    assert _check(plain)["advantage_mode"] == ""
    assert _damage(plain)[0]["rolls"] == [3]

    # Options without the declaration are rejected, not quietly dropped.
    assert "require a brutal strike declaration" in _rejected(
        engine, instance,
        _attack_intent(instance, weapon_ref="item:greataxe",
                       brutal_strike_effects=["forceful"]),
    )

    # Replaying the recorded batch does not re-run the declaration.
    engine, instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    declared = _attack_intent(instance, weapon_ref="item:greataxe", reckless_attack=True)
    first = _apply(engine, instance, declared, [12, 3])
    assert [event["type"] for event in first].count("condition.applied") == 1
    replay = engine.resolve_intent(instance, declared, SequenceRng([]))
    assert replay["ok"] is True and replay.get("replayed") is True, replay
    applied = engine.apply_batch(instance, replay["event_batch"])
    assert applied["duplicate"] is True
    assert _combat_state(instance)["attack_rolls"]["player:gm"]["count"] == 1
    assert [code for code in _conditions(instance, "player:gm")] == ["reckless"]


def test_a_plain_attack_records_nothing_this_slice_owns(
    runtime: Dnd2024Runtime,
) -> None:
    """Matrix 22: the existing attack chain is not rewritten for this slice."""

    engine, instance = _combat(
        runtime, {"gm": with_equipment(_RUNTIME, fighter(13), "item:longsword")},
        initiative={"gm": 20},
    )
    events = _attack(engine, instance, weapon_ref="item:longsword", rolls=[12, 3])
    types = [event["type"] for event in events]
    assert "dnd2024.attack_mark.set" not in types
    assert "dnd2024.attack_mark.cleared" not in types
    # No Reckless means nothing to remember: the roll ledger stays empty, so an
    # actor without the declaration owns no per-turn attack state at all.
    assert "dnd2024.attack_roll.recorded" not in types
    check = _check(events)
    assert check["advantage_mode"] == ""
    assert "brutal_strike" not in check
    assert "sundering_bonus" not in check
    assert _combat_state(instance).get("attack_marks") == {}
    assert _combat_state(instance).get("attack_rolls") == {}


# --------------------------------------------------------------------------
# the panel contract the client actually reads
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("level", "expected"), [(9, ["forceful", "hamstring"]), (13, ["forceful", "hamstring",
    "staggering", "sundering"])],
)
def test_the_panel_reports_the_options_the_server_enforces(
    runtime: Dnd2024Runtime, level: int, expected: list[str],
) -> None:
    """Every declaration the panel offers is one validation also accepts."""

    engine, instance = _combat(runtime, {"gm": barbarian(level)}, initiative={"gm": 20})
    options = _attack_options(engine, instance)["brutal_strike"]
    assert options["effects"] == expected
    assert options["count"] == 1
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, rolls=[12, 3, 5, 5])
    # The choice is spent whether or not it landed, and the panel says so.
    assert _attack_options(engine, instance)["brutal_strike"]["available"] is False


# --------------------------------------------------------------------------
# Review round (2026-11-18): the +5 has to survive a recomputed total
# --------------------------------------------------------------------------

def test_a_recomputed_total_keeps_the_sundering_bonus(runtime) -> None:
    """重掷与改骰用 ``_check_total`` 重算总额，破甲一击的 +5 必须跟着走。

    ``resolution`` 把 +5 记成独立字段（不并进 ``modifier``），而共享的总额
    公式当初只列了 modifier/check_bonus/aura/window/penalty/reroll —— 于是
    Stroke of Luck 或重掷之后，同一份事件的总值会比原来少 5，日志与判定互相
    矛盾（S0-F15 §4.3.4）。
    """

    engine, _instance = _combat(
        runtime, {"gm": barbarian(9)}, initiative={"gm": 20},
    )
    base = {"modifier": 3, "check_bonus": 1, "sundering_bonus": 5}
    assert engine._check_total(base, 10) == 10 + 3 + 1 + 5
    # 没有破甲时不多加：字段缺省即 0。
    assert engine._check_total({"modifier": 3}, 10) == 13


# --------------------------------------------------------------------------
# Review round (2026-11-18)
# --------------------------------------------------------------------------

def test_the_narrow_block_leaves_other_reactions_alone(runtime: Dnd2024Runtime) -> None:
    """§4.3.3：Staggering 只禁 Opportunity Attacks，不禁 Shield/Counterspell。

    两者共用 ``_reaction_eligible``，用同一个 ``reaction_blocked`` 会把目标的
    全部反应一起关掉——比原文宽。窄口径有自己的条件码，且只有借机攻击那条
    路径读它。
    """

    engine, _instance = _combat(runtime, {"gm": barbarian(17)}, initiative={"gm": 20})
    combat = {"reactions": {"enemy:goblin-1": 1}}

    def eligible(conditions: dict, *, opportunity: bool) -> bool:
        return engine._reaction_eligible(
            combat,
            {"actor_id": "enemy:goblin-1", "hp": 10, "conditions": conditions},
            opportunity=opportunity,
        )

    triggered = {"opportunity_attack_blocked": {"duration": "attacker_next_turn_start"}}
    assert eligible(triggered, opportunity=True) is False
    assert eligible(triggered, opportunity=False) is True

    # 真正的"不能反应"仍然关掉一切，两个口径都要拦。
    blocked = {"reaction_blocked": {"duration": "target_turn_start"}}
    assert eligible(blocked, opportunity=True) is False
    assert eligible(blocked, opportunity=False) is False


def test_a_death_save_consumes_the_next_save_marker(runtime: Dnd2024Runtime) -> None:
    """§4.3.3：死亡豁免也是豁免，"下一次豁免劣势"必须在这里用掉。

    三条真实豁免里只有法术/专注/重复豁免接过消费点；死亡豁免漏了，于是标记
    会跟着角色走到"复活后的第一次豁免"才被用掉。
    """

    engine, instance = _combat(runtime, {"gm": barbarian(9)}, initiative={"gm": 20})
    character = _canonical(instance)
    character.setdefault("conditions", {})["next_save_disadvantage"] = {
        "duration": "attacker_next_turn_start",
    }
    character["resources"]["hp"] = 0

    # 倒地者就是当前行动者：直接掷死亡豁免（回合结束意图会被"昏迷只能掷
    # 死亡豁免"的守卫拒绝，那是另一条规则）。
    _apply(engine, instance, {
        "intent_id": "death-1", "type": "death_save",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, rolls=[12])
    assert "next_save_disadvantage" not in (_canonical(instance).get("conditions") or {})


def test_a_spell_attack_also_spends_the_sundering_bonus(runtime: Dnd2024Runtime) -> None:
    """§4.3.4：破甲一击说的是"另一次攻击掷骰"，不区分武器与法术。

    只给武器路径读 +5，会让友军的法术攻击白打一次，而条件挂到自然过期。
    """

    engine, instance = _combat(
        runtime, {"gm": barbarian(17), "ally": cleric(5)},
        initiative={"gm": 20, "ally": 15},
    )
    _attack(engine, instance, weapon_ref="item:greataxe", reckless_attack=True,
            brutal_strike=True, brutal_strike_effects=["sundering"],
            rolls=[12, 6, 5, 5])
    assert "sundered" in _codes(_conditions(instance))

    _to_actor(engine, instance, "player:ally")
    events = _apply(engine, instance, {
        "intent_id": "bolt-1", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "ally", "actor_id": "player:ally",
        "spell_ref": "spell:guiding_bolt", "target_id": "enemy:goblin-1",
        "slot_level": 1,
    }, rolls=[13, 3, 3, 3, 3])
    check = next(
        row for row in events
        if row["type"] == "check.resolved" and row["kind"] == "spell_attack"
    )
    assert check["sundering_bonus"] == 5
    assert any(
        row["type"] == "condition.removed" and row.get("reason") == "sundering_consumed"
        for row in events
    )
    assert "sundered" not in _codes(_conditions(instance))
