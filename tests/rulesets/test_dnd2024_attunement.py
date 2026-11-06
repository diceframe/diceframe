"""D2 attunement matrix: binding, capacity, rest, death and projection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.attunement import (
    AttunementError,
    attuned_refs,
    attunement_capacity,
    validate_item_declaration,
)
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.equipment import Dnd2024EquipmentCatalog
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.resting.engine import RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _runtime() -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Arden"},
    )
    return runtime, sheet


def _install_item(runtime: Dnd2024Runtime, item_id: str, *, ac_bonus: int = 1) -> str:
    runtime.load_bundle("en").entities["item"][item_id] = {
        "schema_version": 1,
        "kind": "item",
        "id": item_id,
        "name": item_id.replace("_", " ").title(),
        "source_ref": f"test:item:{item_id}",
        "automation_level": "deterministic",
        "item_type": "armor",
        "category": "medium",
        "ac_base": 13,
        "use_dex": True,
        "dex_cap": 2,
        "ac_bonus": ac_bonus,
        "requires_attunement": True,
    }
    return f"item:{item_id}"


def _install_magic_shield(
    runtime: Dnd2024Runtime, item_id: str, *, ac_bonus: int = 3,
) -> str:
    """A magic shield in the legacy single-field form: no ``ac_base``.

    ``ac_bonus`` therefore mixes the ordinary shield bonus with the magic one,
    which is exactly the shape that must still respect the attunement gate.
    """

    runtime.load_bundle("en").entities["item"][item_id] = {
        "schema_version": 1,
        "kind": "item",
        "id": item_id,
        "name": item_id.replace("_", " ").title(),
        "source_ref": f"test:item:{item_id}",
        "automation_level": "deterministic",
        "item_type": "shield",
        "ac_bonus": ac_bonus,
        "requires_attunement": True,
    }
    return f"item:{item_id}"


def _set_equipment(sheet: dict, equipped: list[str], carried: list[str] = ()) -> None:
    sheet["equipment"] = [
        {"name": ref, "item_ref": ref, "type": "armor", "slot": "body", "qty": 1}
        for ref in equipped
    ]
    sheet["inventory"] = [
        {"name": ref, "item_ref": ref, "type": "gear", "qty": 1}
        for ref in carried
    ]


def _reconciler(runtime: Dnd2024Runtime):
    return runtime._reconciler(runtime.load_bundle("en"))


def test_unattuned_magic_armor_contributes_no_magic_bonus_and_is_marked_pending() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "chain_shirt_attunement")
    _set_equipment(sheet, [ref])

    reconciled = _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))

    canonical = sheet["ruleset_character"]
    assert reconciled is not None
    assert sheet["armor_class"] == 15  # 13 + DEX 2, no +1 while unattuned
    assert canonical["derived"]["attunement_slots"] == 3
    assert canonical["derived"]["attuned_refs"] == []
    assert canonical["derived"]["attunement_pending"] == [ref]
    assert sheet["attunement"] == {"refs": [], "capacity": 3, "pending": [ref]}


def test_bound_magic_armor_applies_its_bonus_and_projects_state() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "chain_shirt_attunement")
    _set_equipment(sheet, [ref])
    canonical = sheet["ruleset_character"]
    canonical["attunement"] = {"refs": [ref]}

    _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))

    assert sheet["armor_class"] == 16
    assert canonical["derived"]["attuned_refs"] == [ref]
    assert canonical["derived"]["attunement_pending"] == []
    assert sheet["attunement"]["refs"] == [ref]


def test_normal_shield_keeps_its_bonus_while_attunement_gates_only_magic_bonus() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "chain_shirt_attunement")
    _set_equipment(sheet, [ref])
    sheet["equipment"].append({
        "name": "Shield", "item_ref": "item:shield", "type": "shield",
        "slot": "hand", "qty": 1,
    })

    _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    assert sheet["armor_class"] == 17  # 13 + DEX 2 + ordinary shield 2
    sheet["ruleset_character"]["attunement"] = {"refs": [ref]}
    _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    assert sheet["armor_class"] == 18  # +1 magic armor bonus


def test_magic_shield_without_ac_base_still_obeys_the_attunement_gate() -> None:
    """A single-field magic shield must not hand out its bonus while unbound.

    ``ac_bonus`` alone cannot be split into base and magic parts, so an unbound
    one may only count as an ordinary shield (+2); otherwise the gate would be
    decorative for every shield that declares attunement.
    """

    runtime, sheet = _runtime()
    armor_ref = _install_item(runtime, "chain_shirt_attunement")
    shield_ref = _install_magic_shield(runtime, "spellguard_shield", ac_bonus=3)
    _set_equipment(sheet, [armor_ref])
    sheet["equipment"].append({
        "name": "Spellguard Shield", "item_ref": shield_ref, "type": "shield",
        "slot": "hand", "qty": 1,
    })

    _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    assert sheet["armor_class"] == 17  # 13 + DEX 2 + ordinary shield 2
    assert shield_ref in sheet["ruleset_character"]["derived"]["attunement_pending"]

    sheet["ruleset_character"]["attunement"] = {"refs": [shield_ref]}
    _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    assert sheet["armor_class"] == 18  # +3 magic shield bonus once bound


def test_short_rest_binds_the_item_and_rest_projection_recomputes_armor_class() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "chain_shirt_attunement")
    _set_equipment(sheet, [ref])

    result = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": ref},
    )

    updated = result["character"]
    canonical = updated["ruleset_character"]
    assert canonical["attunement"] == {"refs": [ref]}
    assert updated["armor_class"] == 16
    assert [event["type"] for event in result["events"]] == ["attune_item"]
    assert result["events"][0]["item_ref"] == ref


def test_swap_is_atomic_at_full_capacity_and_requires_unbinding_first() -> None:
    runtime, sheet = _runtime()
    first = _install_item(runtime, "item_a")
    second = _install_item(runtime, "item_b")
    third = _install_item(runtime, "item_c")
    fourth = _install_item(runtime, "item_d")
    _set_equipment(sheet, [first, second, third], [fourth])
    canonical = sheet["ruleset_character"]
    canonical["attunement"] = {"refs": [first, second, third]}

    rejected = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": fourth},
    )
    assert [event["type"] for event in rejected["events"]] == ["attunement_rejected"]
    assert attuned_refs(rejected["character"]["ruleset_character"]) == {
        first, second, third,
    }

    swapped = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": fourth, "unattune": first},
    )
    assert [event["type"] for event in swapped["events"]] == [
        "unattune_item", "attune_item",
    ]
    assert swapped["events"][0]["reason"] == "swap"
    assert attuned_refs(swapped["character"]["ruleset_character"]) == {
        second, third, fourth,
    }
    assert set(
        swapped["character"]["ruleset_character"]["derived"]["attuned_refs"],
    ) == {second, third, fourth}


def test_capacity_is_base_three_plus_declared_feature_bonus_and_fails_closed() -> None:
    runtime, sheet = _runtime()
    refs = [_install_item(runtime, f"slot_{index}") for index in range(4)]
    _set_equipment(sheet, refs[:3], refs[3:])
    canonical = sheet["ruleset_character"]
    canonical["attunement"] = {"refs": refs[:3]}
    features = runtime.load_bundle("en").entities[
        "class_feature_catalog"
    ]["srd_class_features"]
    second_wind = features["classes"]["fighter"]["features"][0]
    second_wind["parameters"]["attunement_slot_bonus"] = 3
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))
    assert attunement_capacity(canonical, resolver) == 6

    result = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": refs[3]},
    )
    assert attuned_refs(result["character"]["ruleset_character"]) == set(refs)

    second_wind["parameters"]["attunement_slot_bonus"] = "3"
    with pytest.raises(AttunementError, match="integer"):
        attunement_capacity(
            canonical, Dnd2024ClassFeatureResolver(runtime.load_bundle("en")),
        )
    second_wind["parameters"]["attunement_slot_bonus"] = 9
    with pytest.raises(AttunementError, match="outside"):
        attunement_capacity(
            canonical, Dnd2024ClassFeatureResolver(runtime.load_bundle("en")),
        )


def test_unbinding_is_allowed_even_when_existing_refs_exceed_capacity() -> None:
    runtime, sheet = _runtime()
    refs = [_install_item(runtime, f"legacy_slot_{index}") for index in range(5)]
    _set_equipment(sheet, refs)
    sheet["ruleset_character"]["attunement"] = {"refs": refs}

    result = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"unattune": refs[0]},
    )

    assert [event["type"] for event in result["events"]] == ["unattune_item"]
    assert attuned_refs(result["character"]["ruleset_character"]) == set(refs[1:])


def test_two_copies_reject_binding_but_unbinding_is_allowed() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "duplicated_amulet")
    _set_equipment(sheet, [ref], [ref])

    rejected = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": ref},
    )
    assert [event["type"] for event in rejected["events"]] == ["attunement_rejected"]
    assert "copies" in rejected["events"][0]["reason"]

    sheet["ruleset_character"]["attunement"] = {"refs": [ref]}
    released = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"unattune": ref},
    )
    assert [event["type"] for event in released["events"]] == ["unattune_item"]
    assert attuned_refs(released["character"]["ruleset_character"]) == set()


def test_one_row_with_quantity_two_still_counts_as_two_copies() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "stacked_amulet")
    _set_equipment(sheet, [], [ref])
    sheet["inventory"] = [
        {"name": ref, "item_ref": ref, "type": "gear", "qty": 2},
    ]

    rejected = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": ref},
    )
    assert [event["type"] for event in rejected["events"]] == ["attunement_rejected"]
    assert "copies" in rejected["events"][0]["reason"]

    sheet["ruleset_character"]["attunement"] = {"refs": [ref]}
    released = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"unattune": ref},
    )
    assert [event["type"] for event in released["events"]] == ["unattune_item"]
    assert attuned_refs(released["character"]["ruleset_character"]) == set()


def test_long_rest_keeps_attunement_and_does_not_change_its_state() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "long_rest_item")
    _set_equipment(sheet, [ref])
    sheet["ruleset_character"]["attunement"] = {"refs": [ref]}

    result = runtime.complete_rest(None, sheet, "long")

    assert result["character"]["ruleset_character"]["attunement"] == {"refs": [ref]}
    assert result["character"]["armor_class"] == 16
    assert [event["type"] for event in result["events"]] == ["restore_all_hit_dice"]


def test_unconscious_character_cannot_attune_and_active_combat_blocks_rest() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "sleeping_item")
    _set_equipment(sheet, [ref])
    canonical = sheet["ruleset_character"]
    canonical["resources"]["hp"] = 0

    with pytest.raises(RestError, match="at least 1 HP"):
        runtime.complete_rest(None, sheet, "short", {}, attunement={"attune": ref})
    assert canonical["attunement"] == {"refs": []}

    engine, instance = _combat_instance()
    del engine
    state = ruleset_runtime.state(instance)
    state["combat"] = {"active": True}
    with pytest.raises(ValueError, match="战斗进行中"):
        Dnd2024Runtime.validate_rest_context(instance, "player:gm", "short")


def test_long_rest_rejects_attunement_and_short_rest_payload_type_is_strict() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "strict_item")
    _set_equipment(sheet, [ref])

    with pytest.raises(ValueError, match="short rest"):
        runtime.complete_rest(None, sheet, "long", attunement={"attune": ref})
    with pytest.raises(ValueError, match="short rest"):
        runtime.complete_rest(None, sheet, "long", attunement={})
    with pytest.raises(ValueError, match="object"):
        runtime.complete_rest(None, sheet, "short", {}, attunement=[ref])


def test_invalid_short_rest_payload_records_rejection_without_losing_the_rest() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "declared_item")
    _set_equipment(sheet, [ref])

    unknown = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"bind": ref},
    )
    assert unknown["events"][0]["type"] == "attunement_rejected"
    assert "unknown" in unknown["events"][0]["reason"]

    not_attunable = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": "item:chain_mail"},
    )
    assert "does not require" in not_attunable["events"][0]["reason"]

    _set_equipment(sheet, ["item:chain_mail"])
    sheet["ruleset_character"]["attunement"] = {"refs": ["item:chain_mail"]}
    non_magical_unbind = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"unattune": "item:chain_mail"},
    )
    assert [event["type"] for event in non_magical_unbind["events"]] == ["unattune_item"]
    assert attuned_refs(non_magical_unbind["character"]["ruleset_character"]) == set()

    _set_equipment(sheet, [], [])
    unowned = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": ref},
    )
    assert "does not own" in unowned["events"][0]["reason"]

    sheet["ruleset_character"]["attunement"] = {"refs": [ref]}
    _set_equipment(sheet, [ref])
    already = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": ref},
    )
    assert "already attuned" in already["events"][0]["reason"]

    not_bound = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"unattune": ref},
    )
    sheet["ruleset_character"]["attunement"] = {"refs": []}
    not_bound = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"unattune": ref},
    )
    assert "not attuned" in not_bound["events"][0]["reason"]


def test_duplicate_binding_is_rejected_without_bumping_revision() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "stable_amulet")
    _set_equipment(sheet, [ref])
    canonical = sheet["ruleset_character"]
    canonical["attunement"] = {"refs": [ref]}
    _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    revision = sheet["ruleset_revision"]

    rejected = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"attune": ref},
    )

    assert [event["type"] for event in rejected["events"]] == ["attunement_rejected"]
    assert rejected["character"]["ruleset_revision"] == revision
    assert canonical["attunement"] == {"refs": [ref]}


def test_unbinding_releases_slots_for_items_no_longer_owned_or_deleted() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "lost_item")
    _set_equipment(sheet, [ref])
    sheet["ruleset_character"]["attunement"] = {"refs": [ref]}
    # The item was dropped/sold, or its content entry was removed: the ref
    # still occupies a slot and must be releasable.
    _set_equipment(sheet, [])

    result = runtime.complete_rest(
        None, sheet, "short", {}, attunement={"unattune": ref},
    )

    assert [event["type"] for event in result["events"]] == ["unattune_item"]
    assert attuned_refs(result["character"]["ruleset_character"]) == set()


def test_old_malformed_and_current_saves_migrate_idempotently() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "legacy_item")
    _set_equipment(sheet, [ref])
    canonical = sheet["ruleset_character"]
    canonical.pop("attunement", None)
    sheet.pop("attunement", None)

    first = _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    assert first is not None
    assert canonical["attunement"] == {"refs": []}
    assert sheet["attunement"]["pending"] == [ref]
    assert _reconciler(runtime).reconcile(sheet, frozenset({"equipment"})) is None

    canonical["attunement"] = {"refs": "not-a-list"}
    migrated = _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    assert migrated is not None
    assert canonical["attunement"] == {"refs": []}
    assert _reconciler(runtime).reconcile(sheet, frozenset({"equipment"})) is None


def test_rest_projection_tolerates_a_malformed_attunement_segment() -> None:
    runtime, sheet = _runtime()
    ref = _install_item(runtime, "malformed_segment_item")
    _set_equipment(sheet, [ref])
    sheet["ruleset_character"]["attunement"] = "broken"

    result = runtime.complete_rest(None, sheet, "short", {})

    assert result["character"]["ruleset_character"]["attunement"] == {"refs": []}
    assert result["character"]["attunement"]["refs"] == []


def test_item_declaration_requires_a_boolean_and_bundle_validation_fails_closed() -> None:
    assert validate_item_declaration({}) is False
    assert validate_item_declaration({"requires_attunement": True}) is True
    with pytest.raises(AttunementError, match="boolean"):
        validate_item_declaration({"requires_attunement": "yes"})

    runtime, _sheet = _runtime()
    runtime.load_bundle("en").entities["item"]["bad_item"] = {
        "id": "bad_item", "item_type": "armor", "requires_attunement": "yes",
    }
    with pytest.raises(AttunementError, match="boolean"):
        Dnd2024EquipmentCatalog.from_bundle(runtime.load_bundle("en"))


def _combat_instance() -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Arden"},
    )
    ref = _install_item(runtime, "combat_attuned_armor")
    _set_equipment(sheet, [ref])
    sheet["ruleset_character"]["attunement"] = {"refs": [ref]}
    _reconciler(runtime).reconcile(sheet, frozenset({"equipment"}))
    assert sheet["armor_class"] == 16
    instance = GameInstance(
        game_key=("test", "dnd2024-attunement", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    start = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm",
        "enemies": [{
            "id": "goblin-1", "name": "Goblin", "hp": 18, "armor_class": 12,
            "speed": 30, "position": 5, "initiative_modifier": 2,
            "abilities": {
                "str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8,
            },
            "saving_throws": {"dex": 2, "wis": -1, "con": 0},
            "attacks": [{
                "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            }],
        }],
    }, SequenceRng([20, 1]))
    engine.apply_batch(instance, start["event_batch"])
    return engine, instance


def test_damage_at_zero_hp_death_clears_attunement_and_emits_events() -> None:
    engine, instance = _combat_instance()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 2},
    }
    batch = {
        "batch_id": "batch_death_damage", "intent_id": "death-damage",
        "expected_version": 1, "result_version": 2,
        "events": [{
            "type": "resource.changed", "target_id": "player:gm",
            "resource": "hp", "delta": -5, "amount": 5,
        }],
    }

    applied = engine.apply_batch(instance, batch)

    sheet = instance.get_character_sheet("gm")
    updated = sheet["ruleset_character"]
    assert updated["conditions"]["dead"] == {"source": "damage_at_zero_hp"}
    assert attuned_refs(updated) == frozenset()
    assert sheet["armor_class"] == 15  # 死亡后立刻回落到未同调 AC
    assert applied["event_batch"]["events"][-1] == {
        "type": "unattune_item", "item_ref": "item:combat_attuned_armor",
        "reason": "death",
    }
    assert ruleset_runtime.event_ledger(instance)[-1]["events"][-1]["reason"] == "death"


def test_revival_does_not_restore_cleared_attunements() -> None:
    engine, instance = _combat_instance()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 2},
    }
    engine.apply_batch(instance, {
        "batch_id": "batch_death_revive", "intent_id": "death-revive",
        "expected_version": 1, "result_version": 2,
        "events": [{
            "type": "dnd2024.death_save.resolved", "actor_id": "player:gm",
            "successes": 0, "failures": 3, "dead": True,
        }],
    })
    engine.apply_batch(instance, {
        "batch_id": "batch_revive", "intent_id": "revive",
        "expected_version": 2, "result_version": 3,
        "events": [{
            "type": "dnd2024.character.revived", "actor_id": "player:gm", "hp": 5,
        }],
    })

    sheet = instance.get_character_sheet("gm")
    updated = sheet["ruleset_character"]
    assert "dead" not in updated["conditions"]
    assert attuned_refs(updated) == frozenset()
    assert sheet["armor_class"] == 15


def test_death_save_triple_failure_clears_attunement() -> None:
    engine, instance = _combat_instance()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["resources"]["hp"] = 0
    canonical["conditions"] = {
        "unconscious": {"source": "zero_hp"},
        "death_saves": {"successes": 0, "failures": 2},
    }
    batch = {
        "batch_id": "batch_death_save", "intent_id": "death-save",
        "expected_version": 1, "result_version": 2,
        "events": [{
            "type": "dnd2024.death_save.resolved", "actor_id": "player:gm",
            "successes": 0, "failures": 3, "dead": True,
        }],
    }

    applied = engine.apply_batch(instance, batch)

    updated = instance.get_character_sheet("gm")["ruleset_character"]
    assert attuned_refs(updated) == frozenset()
    assert [event["type"] for event in applied["event_batch"]["events"]] == [
        "dnd2024.death_save.resolved", "unattune_item",
    ]
