"""S0-SP2: condition depth took three spells from "cannot express" to playable.

`mind_spike`, `blindness_deafness` and `ray_of_enfeeblement` were all deferred in
SP1. None of them needed a spell mechanism; they needed `condition` to say more
than "attack rolls at disadvantage".

What this slice added:

- `condition.check_modes` -- check disadvantage, scoped by surface and ability.
  Enfeebled is "Disadvantage on Strength-based D20 Tests", which covers three
  surfaces and only Strength.
- `spell_effects.condition` may be an object `{choices, picked}` -- Blindness/
  Deafness picks one of two conditions and the schema could only hold one string.

Repeated saves were **not** added: `save_repeat` already existed
(`hold_person` uses it), and `phantasmal_force`'s per-turn damage was dropped
for needing target tracking this slice does not build.
"""

from __future__ import annotations

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.check_modes import check_disadvantage_sources
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_draft,
    monk_instance,
    start_combat,
)


WIZARD_SPELLS = ["spell:mind_spike", "spell:blindness_deafness", "spell:ray_of_enfeeblement"]
CANTRIPS = ["spell:fire_bolt", "spell:ray_of_frost", "spell:light"]
SPELLBOOK_FILLER = [
    "spell:shield", "spell:magic_missile", "spell:mage_armor",
    "spell:thunderwave", "spell:sleep", "spell:charm_person",
    "spell:burning_hands", "spell:chromatic_orb", "spell:color_spray",
    "spell:fog_cloud", "spell:jump", "spell:longstrider",
]


def _choices(runtime: Dnd2024Runtime, level: int, focus: str) -> dict:
    """Spell choices for one 2nd-level spell.

    A level-3 wizard prepares 6 spells and may swap one per level, so exactly
    one 2nd-level spell fits at a time: the last prepared slot is swapped for
    `focus` once the cap allows it. Each case therefore builds its own sheet
    around the spell it is about.
    """

    selection = Dnd2024SpellSelection(runtime.load_bundle("en"))
    requirements = selection.requirements("class:wizard", level)
    count = int(requirements["prepared_spell_count"])
    minimum = int(requirements["spellbook_minimum"])
    cap = int(requirements["maximum_spell_level"])
    focus_level = int((selection.catalog.get(focus) or {}).get("level", 0))

    prepared = list(SPELLBOOK_FILLER[:count])
    book = list(SPELLBOOK_FILLER[:minimum])
    if cap >= focus_level:
        # 替换末位而不是追加：book 的追加位在 [:minimum] 之外会被截掉，
        # 而 prepared 必须是 book 的子集。
        prepared[-1] = focus
        book[-1] = focus
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": prepared,
        "spellbook_refs": book,
    }


def _sheet(runtime: Dnd2024Runtime, *, focus: str, level: int = 3) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:wizard",
        class_skill_refs=["skill:arcana", "skill:history"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:wizard_a",
        base_abilities={
            "str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10,
        },
        class_spell_choices=_choices(runtime, 1, focus),
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        picks["class_spell_choices"] = _choices(runtime, next_level, focus)
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _combat(*, focus: str, level: int = 3):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(
        runtime, _sheet(runtime, focus=focus, level=level), uid="gm",
    )
    start_combat(engine, instance, hp=100)
    _turn_to(engine, instance, "player:gm")
    return runtime, engine, instance


def _turn_to(engine, instance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, actor_id),
    )


def _cast(engine, instance, spell_ref: str, slot: int, targets: list[str], rolls=()):
    _turn_to(engine, instance, "player:gm")
    intent = {
        "intent_id": f"cast:{spell_ref}:{slot}",
        "type": "cast_spell", "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": spell_ref, "slot_level": slot, "target_ids": targets,
        "expected_version": ruleset_runtime.state(instance)["version"],
    }
    result = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert result["ok"], result
    assert engine.apply_batch(instance, result["event_batch"])["applied"]
    return result["event_batch"]["events"]


def _damage_of(events: list[dict], target_id: str) -> list[dict]:
    return [
        event for event in events
        if event["type"] == "resource.changed"
        and event.get("target_id") == target_id
        and event.get("damage_type")
    ]


def _enemy_conditions(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"].get(
        "conditions",
    ) or {}


# --- mind_spike: the one that needed nothing at all -----------------------

def test_mind_spike_halves_the_damage_on_a_successful_save() -> None:
    _, engine, instance = _combat(focus="spell:mind_spike")

    # 感知豁免 d20=20 必成功；3d8 的 5+5+5 半数向下取整是 7。
    events = _cast(
        engine, instance, "spell:mind_spike", 2, ["enemy:goblin-1"],
        rolls=[20, 5, 5, 5],
    )

    damage = _damage_of(events, "enemy:goblin-1")
    assert damage, "成功豁免仍需半伤"
    assert damage[0]["amount"] == 7


def test_mind_spike_takes_full_damage_on_a_failed_save() -> None:
    _, engine, instance = _combat(focus="spell:mind_spike")

    events = _cast(
        engine, instance, "spell:mind_spike", 2, ["enemy:goblin-1"],
        rolls=[1, 5, 5, 5],
    )

    assert _damage_of(events, "enemy:goblin-1")[0]["amount"] == 15


# --- blindness_deafness: one condition, chosen at declaration time --------

def test_blindness_deafness_applies_the_declared_choice() -> None:
    _, engine, instance = _combat(focus="spell:blindness_deafness")

    events = _cast(
        engine, instance, "spell:blindness_deafness", 2, ["enemy:goblin-1"],
        rolls=[1],
    )

    applied = [
        event for event in events
        if event["type"] == "condition.applied"
    ]
    assert applied, "失败豁免必须施加条件"
    assert applied[0]["condition"] == "blinded"


def test_blindness_deafness_applies_nothing_on_a_successful_save() -> None:
    _, engine, instance = _combat(focus="spell:blindness_deafness")

    events = _cast(
        engine, instance, "spell:blindness_deafness", 2, ["enemy:goblin-1"],
        rolls=[20],
    )

    assert not [e for e in events if e["type"] == "condition.applied"]


# --- ray_of_enfeeblement: the condition now actually bends the roll -------

def test_ray_of_enfeeblement_applies_enfeebled() -> None:
    _, engine, instance = _combat(focus="spell:ray_of_enfeeblement")

    _cast(
        engine, instance, "spell:ray_of_enfeeblement", 2, ["enemy:goblin-1"],
        rolls=[1],
    )

    assert "enfeebled" in _enemy_conditions(instance)


def test_enfeebled_only_penalises_strength_checks() -> None:
    """The whole point of `check_modes.abilities`: Strength-based only."""

    catalog = Dnd2024Runtime().load_bundle("en").get(
        "combat_catalog", "srd_combat_core",
    )
    registry = catalog["conditions"]
    conditions = {"enfeebled": {"source": "test"}}

    for surface in ("ability_check", "skill_check", "saving_throw"):
        assert check_disadvantage_sources(
            registry, conditions, surface=surface, ability="str",
        ) == ["enfeebled"], surface
        assert check_disadvantage_sources(
            registry, conditions, surface=surface, ability="dex",
        ) == [], surface
    # 查不到属性时按"不匹配"处理，不能当成"任意属性"。
    assert check_disadvantage_sources(
        registry, conditions, surface="skill_check",
    ) == []


def test_an_enfeebled_actor_rolls_strength_saves_at_disadvantage() -> None:
    runtime, engine, instance = _combat(focus="spell:ray_of_enfeeblement")
    _cast(
        engine, instance, "spell:ray_of_enfeeblement", 2, ["enemy:goblin-1"],
        rolls=[1],
    )
    enemies = ruleset_runtime.state(instance)["combat"]["enemies"]
    goblin = engine._actor_view(
        instance, ruleset_runtime.state(instance)["combat"], "enemy:goblin-1",
    )
    assert "enfeebled" in enemies["goblin-1"]["conditions"]

    events = engine._saving_throw_check(
        instance, ruleset_runtime.state(instance)["combat"], goblin,
        "enemy:goblin-1", "str", 15, "player:gm", [], SequenceRng([10, 3]),
    )

    assert events["rolls"] == [10, 3]
    assert events["natural"] == 3
    assert events["advantage_mode"] == "disadvantage"


# --- the repeated save is a mechanism this slice must not lose -------------

def _repeat_save(engine, instance, rolls):
    combat = ruleset_runtime.state(instance)["combat"]
    return engine._save_repeat_events(
        instance, combat, "enemy:goblin-1", SequenceRng(list(rolls)),
    )


def test_a_failed_repeat_save_keeps_the_condition() -> None:
    _, engine, instance = _combat(focus="spell:blindness_deafness")
    _cast(engine, instance, "spell:blindness_deafness", 2, ["enemy:goblin-1"], rolls=[1])
    assert "blinded" in _enemy_conditions(instance)

    events = _repeat_save(engine, instance, [1])

    assert "blinded" in _enemy_conditions(instance)
    assert any(e["type"] == "check.resolved" and e["kind"] == "saving_throw" for e in events)
    assert not [e for e in events if e["type"] == "condition.removed"]


def test_a_successful_repeat_save_ends_the_condition() -> None:
    _, engine, instance = _combat(focus="spell:blindness_deafness")
    _cast(engine, instance, "spell:blindness_deafness", 2, ["enemy:goblin-1"], rolls=[1])

    events = _repeat_save(engine, instance, [20])

    # `_save_repeat_events` 只产出事件，状态由 reducer 应用——断言事件而不是
    # 状态，否则测的是"我有没有手动 apply"，不是"机制有没有决定移除"。
    check = next(e for e in events if e["type"] == "check.resolved")
    assert check["success"] is True
    removed = next(e for e in events if e["type"] == "condition.removed")
    assert removed["reason"] == "save_repeat"
    assert removed["condition"] == "blinded"
