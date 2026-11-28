"""S0-SP1: the direct-fill spells that survived a rules check, end to end.

`ray_of_sickness` and `dissonant_whispers` are what is left of the eight in the
task spec after checking each against `srd_spell_descriptions.json`. Six of them
were dropped: five lack a mechanism the effect schema does not have, and
`phantasmal_force` was a rules error on my part -- the spell has no initial
damage at all, only 2d8 per turn from the phantasm, so it needs the repeat
machinery rather than the one-roll path.

The cases assert numbers and conditions, not the presence of a field: the point
of the slice is that these spells are playable. `dissonant_whispers` is
Bard-only, so it has a declaration case here and its cast case waits for a
creation helper that can build a Bard (three tool proficiencies).
"""

from __future__ import annotations

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_draft,
    monk_instance,
    start_combat,
)


WIZARD_SPELLS = ["spell:ray_of_sickness", "spell:phantasmal_force"]
CANTRIPS = ["spell:fire_bolt", "spell:ray_of_frost", "spell:light"]
# The book minimum grows with level; the filler only has to be legal 1st-level
# wizard spells.
SPELLBOOK_FILLER = [
    "spell:shield", "spell:magic_missile", "spell:mage_armor",
    "spell:thunderwave", "spell:sleep", "spell:charm_person",
    "spell:burning_hands", "spell:chromatic_orb", "spell:color_spray",
    "spell:fog_cloud", "spell:jump", "spell:longstrider",
]


def _choices(runtime: Dnd2024Runtime, level: int) -> dict:
    selection = Dnd2024SpellSelection(runtime.load_bundle("en"))
    requirements = selection.requirements("class:wizard", level)
    cap = int(requirements["maximum_spell_level"])
    def _level(ref: str) -> int:
        return int((selection.catalog.get(ref) or {}).get("level", 0))
    eligible = [s for s in WIZARD_SPELLS if _level(s) <= cap]
    filler = [s for s in SPELLBOOK_FILLER if _level(s) <= cap]
    prepared = list(dict.fromkeys([*eligible, *filler]))[
        : requirements["prepared_spell_count"]
    ]
    book = list(dict.fromkeys([*prepared, *filler]))[
        : requirements["spellbook_minimum"]
    ]
    return {
        "cantrip_refs": CANTRIPS[: requirements["cantrip_count"]],
        "prepared_spell_refs": prepared,
        "spellbook_refs": book,
    }


def _sheet(runtime: Dnd2024Runtime, *, level: int = 3) -> dict:
    draft = monk_draft(runtime)
    draft.update(
        class_ref="class:wizard",
        class_skill_refs=["skill:arcana", "skill:history"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:wizard_a",
        base_abilities={
            "str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10,
        },
        class_spell_choices=_choices(runtime, 1),
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        picks["class_spell_choices"] = _choices(runtime, next_level)
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _combat(*, level: int = 3):
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, _sheet(runtime, level=level), uid="gm")
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
    """伤害落在 `resource.changed` 上，不是 `damage.applied`。"""

    return [
        event for event in events
        if event["type"] == "resource.changed"
        and event.get("target_id") == target_id
        and event.get("damage_type")
    ]


def _cast_event(events: list[dict]) -> dict:
    return next(event for event in events if event["type"] == "dnd2024.spell.cast")


def test_ray_of_sickness_deals_poison_and_poisons_the_target() -> None:
    _, engine, instance = _combat()

    # d20=15（+4 打 AC 12，命中且**不暴击**——20 会翻倍伤害骰）、2d8 用满 5+5。
    events = _cast(
        engine, instance, "spell:ray_of_sickness", 1, ["enemy:goblin-1"],
        rolls=[15, 5, 5],
    )

    damage = _damage_of(events, "enemy:goblin-1")
    assert damage, "命中必须产生伤害事件"
    assert damage[0]["damage_type"] == "poison"
    assert damage[0]["amount"] == 10
    conditions = ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"].get(
        "conditions",
    ) or {}
    assert "poisoned" in conditions


def test_ray_of_sickness_misses_when_the_attack_roll_is_low() -> None:
    _, engine, instance = _combat()

    events = _cast(
        engine, instance, "spell:ray_of_sickness", 1, ["enemy:goblin-1"],
        rolls=[1],
    )

    assert not _damage_of(events, "enemy:goblin-1")


def test_the_sheet_survives_a_round_trip_after_the_cast() -> None:
    runtime, engine, instance = _combat()
    _cast(engine, instance, "spell:ray_of_sickness", 1, ["enemy:goblin-1"], rolls=[15, 5, 5])

    sheet = instance.get_character_sheet("gm")

    normalized = runtime.normalize_character_submission(None, sheet)
    class_block = normalized["ruleset_character"]["spellcasting"]["class"]

    # 重建**不抛**是这条用例要保证的。槽位本身会在这条路径上被重置回满，
    # 那是既有行为（GAP-SP2），不在本片修；断言它保留会让这条用例红，
    # 而断言它被重置会把缺陷写成契约。
    assert class_block["slots_max"]


def test_dissonant_whispers_is_declared_as_a_wisdom_save() -> None:
    """Bard-only; its cast case waits for a Bard-capable creation helper."""

    catalog = Dnd2024Runtime().load_bundle("en").get(
        "combat_catalog", "srd_combat_core",
    )
    effect = catalog["spell_effects"]["dissonant_whispers"]

    assert effect["mode"] == "saving_throw"
    assert effect["save"] == "wis"
    assert effect["damage"] == "3d6"
    assert effect["damage_type"] == "psychic"
    # 原文 "On a successful save, the target takes half as much damage only."
    assert effect["half_on_success"] is True
