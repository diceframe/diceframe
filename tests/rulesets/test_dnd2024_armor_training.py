"""Armor Training (2024): item data, derivation, reconciliation and view projection.

The slice has one authority for the AC formula (:func:`derive_armor_class`) and
one for the training consequences (:func:`derive_armor_training_state`).  Tests
here exercise the real SRD bundle and the real live-equipment reconciliation
path, never a hand-built shortcut, so a data gap in the item catalog fails here
instead of silently treating untrained armor as trained.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.derivation import (
    derive_armor_class,
    derive_armor_training_state,
)
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import EventBatchError

from dnd2024_monk_common import SequenceRng


LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _bundle(runtime: Dnd2024Runtime):
    return runtime.load_bundle(LOCALE)


def _sheet(runtime: Dnd2024Runtime, preset_id: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": LOCALE, "name": "Armor Hero"},
    )


def _canonical(runtime: Dnd2024Runtime, preset_id: str) -> dict:
    return _sheet(runtime, preset_id)["ruleset_character"]


def _armor_refs(canonical: dict) -> list[str]:
    return list((canonical.get("proficiencies") or {}).get("armor_category_refs") or [])


def _reconciler(runtime: Dnd2024Runtime) -> Dnd2024CharacterStateReconciler:
    bundle = _bundle(runtime)
    return Dnd2024CharacterStateReconciler(bundle, locale_bundles=(bundle,))


def _wear(runtime: Dnd2024Runtime, canonical: dict, names: list[str]) -> dict:
    """Put exactly ``names`` on the live sheet and run the real reconciliation."""

    live = deepcopy(canonical)
    live["equipment"] = [
        {"name": name, "qty": 1, "category": "equipment"} for name in names
    ]
    _reconciler(runtime).reconcile(live)
    return live


def _abilities(str_value: int, dex_value: int) -> dict[str, int]:
    return {
        "str": str_value, "dex": dex_value, "con": 12,
        "int": 10, "wis": 10, "cha": 10,
    }


# --------------------------------------------------------------------------
# 1. item data
# --------------------------------------------------------------------------

def test_item_catalog_declares_armor_training_fields(runtime) -> None:
    bundle = _bundle(runtime)

    expected = {
        "leather_armor": "light",
        "studded_leather_armor": "light",
        "chain_shirt": "medium",
        "chain_mail": "heavy",
    }
    for item_id, category in expected.items():
        item = bundle.get("item", item_id)
        assert item is not None, item_id
        assert item["item_type"] == "armor"
        assert item["category"] == category

    chain_mail = bundle.get("item", "chain_mail")
    assert int(chain_mail["strength_minimum"]) == 13
    assert chain_mail["stealth_disadvantage"] is True

    shield = bundle.get("item", "shield")
    assert shield is not None
    assert shield["item_type"] == "shield"
    # 盾牌没有轻/中/重类别：受训按 armor_category:shield 单独匹配。
    assert "category" not in shield
    assert int(shield["ac_bonus"]) == 2


# --------------------------------------------------------------------------
# 2. shield training in the AC formula
# --------------------------------------------------------------------------

def test_shield_bonus_requires_training(runtime) -> None:
    bundle = _bundle(runtime)
    fighter = _canonical(runtime, "stalwart_guardian")
    wizard = _canonical(runtime, "curious_arcanist")
    abilities = fighter["abilities"]

    without_shield = derive_armor_class(
        abilities, [], bundle, armor_category_refs=_armor_refs(fighter),
    )
    trained = derive_armor_class(
        abilities, ["item:shield"], bundle,
        armor_category_refs=_armor_refs(fighter),
    )
    untrained = derive_armor_class(
        abilities, ["item:shield"], bundle,
        armor_category_refs=_armor_refs(wizard),
    )

    assert trained == without_shield + 2
    assert untrained == without_shield


# --------------------------------------------------------------------------
# 3. untrained armor still yields its AC
# --------------------------------------------------------------------------

def test_untrained_armor_keeps_its_armor_class(runtime) -> None:
    bundle = _bundle(runtime)
    abilities = _abilities(8, 12)

    assert derive_armor_class(
        abilities, ["item:leather_armor"], bundle, armor_category_refs=[],
    ) == 11 + 1  # 11 + Dex modifier; RAW never revokes untrained armor AC.


# --------------------------------------------------------------------------
# 4. fully trained gear opens every gate
# --------------------------------------------------------------------------

def test_trained_medium_armor_and_shield_have_no_training_gate(runtime) -> None:
    bundle = _bundle(runtime)
    fighter = _canonical(runtime, "stalwart_guardian")

    state = derive_armor_training_state(
        fighter["abilities"], ["item:chain_shirt", "item:shield"],
        _armor_refs(fighter), bundle,
    )

    assert state == {
        "untrained_armor_worn": False,
        "speed_penalty": 0,
        "stealth_disadvantage": False,
    }
    assert "armor_category:shield" in _armor_refs(fighter)


# --------------------------------------------------------------------------
# 5-6. Strength requirement and Stealth penalty
# --------------------------------------------------------------------------

def test_untrained_heavy_armor_reports_every_consequence(runtime) -> None:
    bundle = _bundle(runtime)

    state = derive_armor_training_state(
        _abilities(12, 10), ["item:chain_mail"], [], bundle,
    )

    assert state == {
        "untrained_armor_worn": True,
        "speed_penalty": 10,
        "stealth_disadvantage": True,
    }


def test_meeting_the_strength_requirement_removes_the_speed_penalty(runtime) -> None:
    bundle = _bundle(runtime)

    state = derive_armor_training_state(
        _abilities(13, 10), ["item:chain_mail"], [], bundle,
    )

    assert state["speed_penalty"] == 0
    # 力量达标不改变其它两项：重甲仍可未受训且仍隐匿劣势。
    assert state["untrained_armor_worn"] is True
    assert state["stealth_disadvantage"] is True


# --------------------------------------------------------------------------
# 7. fail closed on missing directory data
# --------------------------------------------------------------------------

def test_armor_without_a_category_fails_closed(runtime) -> None:
    bundle = _bundle(runtime)
    entities = deepcopy(bundle.entities)
    mutated = entities["item"]["chain_mail"]
    mutated.pop("category")
    broken = replace(bundle, entities=entities)

    with pytest.raises(ValueError, match="chain_mail"):
        derive_armor_training_state(
            _abilities(16, 10), ["item:chain_mail"], [], broken,
        )


# --------------------------------------------------------------------------
# 8. creation-time landing
# --------------------------------------------------------------------------

def test_creation_lands_armor_training_and_keeps_base_speed(runtime) -> None:
    canonical = _canonical(runtime, "stalwart_guardian")
    species = _bundle(runtime).get("species", "human")

    assert canonical["derived"]["armor_training"] == {
        "untrained_armor_worn": False,
        "speed_penalty": 0,
        "stealth_disadvantage": True,
    }
    # 罚值不在 canonical 基础速度里：基础速度始终是种族速度。
    assert canonical["derived"]["speed"] == int(species["speed"]) == 30


# --------------------------------------------------------------------------
# 9. live reconciliation
# --------------------------------------------------------------------------

def test_reconciliation_recomputes_training_when_armor_changes(runtime) -> None:
    rogue = _sheet(runtime, "lucky_scout")
    live = deepcopy(rogue)
    live["equipment"] = [{"name": "Chain Mail", "qty": 1, "category": "equipment"}]
    reconciler = _reconciler(runtime)

    result = reconciler.reconcile(live)
    canonical = live["ruleset_character"]

    assert result is not None
    assert canonical["derived"]["armor_class"] == 16
    assert canonical["derived"]["armor_training"] == {
        "untrained_armor_worn": True,
        "speed_penalty": 10,
        "stealth_disadvantage": True,
    }
    assert reconciler.reconcile(live) is None


def test_training_change_penetrates_an_equal_armor_class_shortcut(runtime) -> None:
    rogue = _sheet(runtime, "lucky_scout")
    live = deepcopy(rogue)
    live["equipment"] = [
        {"name": "Studded Leather Armor", "qty": 1, "category": "equipment"},
    ]
    reconciler = _reconciler(runtime)

    first = reconciler.reconcile(live)
    canonical = live["ruleset_character"]

    assert first is not None
    assert canonical["derived"]["armor_class"] == 15  # 12 + Dex 3
    assert canonical["derived"]["armor_training"]["untrained_armor_worn"] is False
    assert reconciler.reconcile(live) is None

    # 链甲衫同样是 15（13 + 敏捷上限 2），但游荡者未受训中甲：AC 不变而
    # 训练段改变，幂等短路必须被穿透。
    live["equipment"] = [{"name": "Chain Shirt", "qty": 1, "category": "equipment"}]
    second = reconciler.reconcile(live)

    assert second is not None
    assert second["revision"] == first["revision"] + 1
    assert canonical["derived"]["armor_class"] == 15
    assert canonical["derived"]["armor_training"]["untrained_armor_worn"] is True


# --------------------------------------------------------------------------
# 10. combat actor projection
# --------------------------------------------------------------------------

def test_actor_speed_is_net_and_training_is_copied(runtime) -> None:
    engine = Dnd2024CombatEngine(_bundle(runtime))
    canonical = deepcopy(_canonical(runtime, "stalwart_guardian"))
    canonical["derived"]["armor_training"] = {
        "untrained_armor_worn": True,
        "speed_penalty": 10,
        "stealth_disadvantage": True,
    }
    # A canonical sheet stores the already-net Speed: ``derive_speed`` applied
    # the -10 ft when the armour was reconciled.  The actor view only moves it.
    canonical["derived"]["speed"] = 20

    view = engine._player_view("gm", canonical)

    assert view["speed"] == 20
    assert view["armor_training"] == canonical["derived"]["armor_training"]
    # Projection must copy, not alias: mutating the actor never edits canonical.
    view["armor_training"]["speed_penalty"] = 99
    assert canonical["derived"]["armor_training"]["speed_penalty"] == 10

    # ...and the projection never recomputes Speed from the training block.
    # Subtracting the penalty here as well was how the sheet and combat ended
    # up reporting two different numbers for one character.
    canonical["derived"]["armor_training"]["speed_penalty"] = 100
    assert engine._player_view("gm", canonical)["speed"] == 20


# --------------------------------------------------------------------------
# 17. companion projection inherits the player view
# --------------------------------------------------------------------------

def test_companion_projection_inherits_armor_training(runtime) -> None:
    bundle = _bundle(runtime)
    sheet = _sheet(runtime, "stalwart_guardian")
    instance = GameInstance(
        game_key=("test", "dnd2024-armor-training", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language=LOCALE,
    )
    instance.players["gm"] = {"character_name": "Armor Hero", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])

    companion = {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": {
            "resources": {"hp": 20, "max_hp": 20},
            "conditions": {},
            "abilities": _abilities(12, 10),
            "derived": {
                "armor_class": 16, "speed": 20, "initiative": 1,
                "proficiency_bonus": 2, "saving_throws": {"str": 1, "con": 3},
                "armor_training": {
                    "untrained_armor_worn": True,
                    "speed_penalty": 10,
                    "stealth_disadvantage": True,
                },
            },
            "proficiencies": {"skill_values": {}, "weapon_category_refs": []},
            "equipment": {"item_refs": ["item:chain_mail"]},
            "build": {"class_levels": [{"class_ref": "class:wizard", "level": 1}]},
        },
    }
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})["mira"] = companion

    engine = Dnd2024CombatEngine(bundle)
    view = engine._actor_view(instance, {"enemies": {}}, "companion:mira")

    assert view["kind"] == "companion"
    assert view["speed"] == 20
    assert view["armor_training"]["untrained_armor_worn"] is True
    assert view["armor_training"]["speed_penalty"] == 10


# --------------------------------------------------------------------------
# 18. legacy saves repaired at the combat.start boundary (R5)
#
# A save written before the Armor Training slice has no ``derived.armor_training``
# and keeps the old unconditional shield bonus.  The repair happens on the
# production ``resolve_intent`` -> ``apply_event_batch`` chain, before the first
# combat starts, so the party never depends on resting, levelling or re-equipping
# to get the current rules.
# --------------------------------------------------------------------------

_LEGACY_ITEM_REFS = {
    "Chain Mail": "item:chain_mail",
    "Chain Shirt": "item:chain_shirt",
    "Shield": "item:shield",
}


def _legacy_sheet(
    runtime: Dnd2024Runtime, preset_id: str, names: list[str], old_armor_class: int,
) -> dict:
    """A pre-Armor-Training save: no training state and the old shield bonus."""

    sheet = _sheet(runtime, preset_id)
    canonical = sheet["ruleset_character"]
    canonical["equipment"]["item_refs"] = [
        _LEGACY_ITEM_REFS[name] for name in names
    ]
    canonical["derived"]["armor_class"] = old_armor_class
    canonical["derived"].pop("armor_training", None)
    sheet["equipment"] = [
        {"name": name, "qty": 1, "category": "equipment"} for name in names
    ]
    sheet["armor_class"] = old_armor_class
    return sheet


def _loaded_instance(sheet: dict, *, companions: tuple[dict, ...] = ()) -> GameInstance:
    """Persist and reload the aggregate, the real load path for an old save."""

    instance = GameInstance(
        game_key=("test", "dnd2024-legacy-armor", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language=LOCALE,
    )
    instance.players["gm"] = {
        "character_name": "Legacy Hero", "character_sheet": deepcopy(sheet),
    }
    assert instance.bind_ruleset_runtime(sheet["ruleset_character"]["rule_binding"])
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})
    for companion in companions:
        party["companions"][companion["id"]] = deepcopy(companion)
    return GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))


def _goblin(enemy_id: str = "goblin-1") -> dict:
    return {
        "id": enemy_id, "name": enemy_id, "hp": 40, "armor_class": 12, "speed": 30,
        "position": 5, "initiative_modifier": 2, "creature_type": "humanoid",
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _start_through_runtime(
    runtime: Dnd2024Runtime, instance: GameInstance, enemy_id: str = "goblin-1",
) -> dict:
    """Start combat the way the web layer does: runtime, not engine directly."""

    state = ruleset_runtime.state(instance)
    version = int(state.get("version", 0) or 0) if isinstance(state, dict) else 0
    resolved = runtime.resolve_intent(instance, {
        "intent_id": f"legacy-start-{version}", "type": "combat.start",
        "expected_version": version, "submitted_by": "gm",
        "enemies": [_goblin(enemy_id)],
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True, resolved
    applied = runtime.apply_event_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return ruleset_runtime.state(instance)["combat"]


def _check_event(result: dict) -> dict:
    return next(
        event for event in result["event_batch"]["events"]
        if event["type"] == "check.resolved"
    )


def _attack(runtime: Dnd2024Runtime, instance: GameInstance, rolls: list[int]) -> dict:
    version = int(ruleset_runtime.state(instance)["version"])
    result = runtime.resolve_intent(instance, {
        "intent_id": "legacy-attack", "type": "attack", "expected_version": version,
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "unarmed_strike",
    }, SequenceRng(list(rolls)))
    assert result["ok"] is True, result
    return result


def _cast(
    runtime: Dnd2024Runtime, instance: GameInstance, spell_ref: str,
    *, slot_level: int = 1, target_id: str = "enemy:goblin-1",
) -> dict:
    version = int(ruleset_runtime.state(instance)["version"])
    return runtime.resolve_intent(instance, {
        "intent_id": "legacy-cast", "type": "cast_spell",
        "expected_version": version, "submitted_by": "gm",
        "actor_id": "player:gm", "target_id": target_id,
        "spell_ref": spell_ref, "slot_level": slot_level,
    }, SequenceRng([1]))


def _repeat_save(runtime: Dnd2024Runtime, instance: GameInstance) -> dict:
    """End the current turn and inspect the actor's own repeat saving throw."""

    sheet = instance.get_character_sheet("gm")
    sheet["ruleset_character"].setdefault("conditions", {})["legacy_save"] = {
        "save_repeat": {"ability": "dex", "dc": 14},
        "source_actor_id": "companion:mira",
    }
    instance.set_character_sheet("gm", sheet)
    version = int(ruleset_runtime.state(instance)["version"])
    result = runtime.resolve_intent(instance, {
        "intent_id": "legacy-end", "type": "end_turn", "expected_version": version,
        "submitted_by": "gm", "actor_id": "player:gm",
    }, SequenceRng([7, 5]))
    assert result["ok"] is True, result
    return next(
        event for event in result["event_batch"]["events"]
        if event["type"] == "check.resolved" and event["kind"] == "saving_throw"
    )


def test_legacy_untrained_armor_is_repaired_through_the_production_chain(runtime) -> None:
    legacy = _legacy_sheet(runtime, "curious_arcanist", ["Chain Mail", "Shield"], 18)
    assert "armor_training" not in legacy["ruleset_character"]["derived"]

    instance = _loaded_instance(legacy)
    combat = _start_through_runtime(runtime, instance)
    sheet = instance.get_character_sheet("gm")
    derived = sheet["ruleset_character"]["derived"]

    # Old AC 18 (chain mail 16 + unconditional shield +2) becomes 16: the shield
    # bonus is lost because the wizard has no shield training, while untrained
    # armor still keeps its own AC.
    assert derived["armor_class"] == 16
    assert derived["armor_training"] == {
        "untrained_armor_worn": True,
        "speed_penalty": 10,
        "stealth_disadvantage": True,
    }
    assert sheet["ruleset_character"]["equipment"]["item_refs"] == [
        "item:chain_mail", "item:shield",
    ]

    view = Dnd2024CombatEngine(_bundle(runtime))._actor_view(
        instance, combat, "player:gm",
    )
    assert view["armor_class"] == 16
    # Species 30 尺，穿 Chain Mail 而 Strength 不足，派生速度扣 10：协调写回
    # ``derived.speed``（唯一权威）就是 20，投影照搬，不再二次扣减。
    assert view["speed"] == 20
    assert view["speed"] == derived["speed"]
    assert view["armor_training"] == derived["armor_training"]

    blocked = _cast(runtime, instance, "spell:magic_missile")
    assert blocked == {
        "ok": False, "code": "INVALID_INTENT",
        "error": "armor you are not trained in blocks spellcasting",
    }

    attack = _attack(runtime, instance, [15, 3])
    check = _check_event(attack)
    assert check["advantage_mode"] == "disadvantage"
    assert [source["code"] for source in check["advantage_sources"]] == ["untrained_armor"]
    assert check["rolls"] == [15, 3]
    runtime.apply_event_batch(instance, attack["event_batch"])

    save = _repeat_save(runtime, instance)
    assert save["advantage_mode"] == "disadvantage"
    assert [source["code"] for source in save["advantage_sources"]] == ["untrained_armor"]
    assert save["rolls"] == [7, 5]


def test_legacy_trained_armor_keeps_its_armor_class_and_gates(runtime) -> None:
    fighter = _legacy_sheet(runtime, "stalwart_guardian", ["Chain Mail", "Shield"], 18)
    instance = _loaded_instance(fighter)
    combat = _start_through_runtime(runtime, instance)
    derived = instance.get_character_sheet("gm")["ruleset_character"]["derived"]

    # 链甲 16 + 受训盾牌 2 + Defense 1 = 19。存档里的旧值 18 是 Defense 生效
    # 之前的算法，重新推导后按现行公式修正。
    assert derived["armor_class"] == 19
    assert derived["armor_training"] == {
        "untrained_armor_worn": False,
        "speed_penalty": 0,
        "stealth_disadvantage": True,
    }
    view = Dnd2024CombatEngine(_bundle(runtime))._actor_view(
        instance, combat, "player:gm",
    )
    assert view["armor_class"] == 19
    assert view["speed"] == 30

    check = _check_event(_attack(runtime, instance, [15]))
    assert check["advantage_mode"] == ""
    assert check["rolls"] == [15]

    # A trained caster keeps casting: chain shirt (medium) + shield, old AC 14.
    cleric = _legacy_sheet(runtime, "kindly_bulwark", ["Chain Shirt", "Shield"], 14)
    caster = _loaded_instance(cleric)
    _start_through_runtime(runtime, caster)
    caster_derived = caster.get_character_sheet("gm")["ruleset_character"]["derived"]
    assert caster_derived["armor_class"] == 14
    assert caster_derived["armor_training"]["untrained_armor_worn"] is False

    assert _cast(
        runtime, caster, "spell:cure_wounds", target_id="player:gm",
    )["ok"] is True


def test_repeated_load_and_combat_start_do_not_append_state(runtime) -> None:
    legacy = _legacy_sheet(runtime, "curious_arcanist", ["Chain Mail", "Shield"], 18)
    instance = _loaded_instance(legacy)
    _start_through_runtime(runtime, instance)
    repaired = instance.get_character_sheet("gm")
    revision = repaired["ruleset_revision"]
    log = repaired["ruleset_operation_log"]
    assert revision == 1
    assert [entry["kind"] for entry in log] == ["character_reconcile"]

    # Close the encounter, persist and reload: the repaired sheet is stable.
    version = int(ruleset_runtime.state(instance)["version"])
    ended = runtime.resolve_intent(instance, {
        "intent_id": "legacy-combat-end", "type": "combat.end",
        "expected_version": version, "submitted_by": "gm",
    }, SequenceRng([]))
    assert ended["ok"] is True, ended
    runtime.apply_event_batch(instance, ended["event_batch"])

    reloaded = GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))
    before = reloaded.get_character_sheet("gm")
    assert before["ruleset_revision"] == revision
    assert before["ruleset_operation_log"] == log

    _start_through_runtime(runtime, reloaded, enemy_id="goblin-2")
    after = reloaded.get_character_sheet("gm")
    assert after["ruleset_revision"] == revision
    assert after["ruleset_operation_log"] == log

    # Rest/advancement and live equipment use these two boundaries; neither may
    # rewrite the already-correct record.
    bundle = _bundle(runtime)
    assert runtime.reconcile_character_state(
        reloaded, "gm", frozenset({"equipment"}),
    ) is None
    projected = runtime._live_projection(
        after, after["ruleset_character"], bundle,
    )
    assert projected["ruleset_revision"] == revision
    assert projected["ruleset_operation_log"] == log
    assert (
        projected["ruleset_character"]["derived"]["armor_training"]
        == after["ruleset_character"]["derived"]["armor_training"]
    )


def test_stale_combat_start_does_not_publish_legacy_repair(runtime) -> None:
    legacy = _legacy_sheet(runtime, "stalwart_guardian", ["Chain Mail", "Shield"], 18)
    instance = _loaded_instance(legacy)
    before_sheet = deepcopy(instance.get_character_sheet("gm"))

    resolved = runtime.resolve_intent(instance, {
        "intent_id": "stale-legacy-start", "type": "combat.start",
        "expected_version": 0, "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True, resolved

    # Simulate another authoritative write landing between resolve and apply.
    ruleset_runtime.state(instance)["version"] = 1
    with pytest.raises(EventBatchError, match="state version conflict"):
        runtime.apply_event_batch(instance, resolved["event_batch"])

    assert instance.get_character_sheet("gm") == before_sheet
    assert ruleset_runtime.state(instance)["combat"]["status"] == "none"


def test_legacy_repair_keeps_the_content_version(runtime) -> None:
    bundle = _bundle(runtime)
    # The repair changes derived state only; no content shape moved, so the
    # manifest must not be bumped merely to trigger a migration.
    assert bundle.manifest.content_version == "srd-5.2.1+r5"

    legacy = _legacy_sheet(runtime, "curious_arcanist", ["Chain Mail", "Shield"], 18)
    binding = legacy["ruleset_character"]["rule_binding"]
    assert binding["content_version"] == bundle.manifest.content_version

    instance = _loaded_instance(legacy)
    _start_through_runtime(runtime, instance)
    repaired = instance.get_character_sheet("gm")["ruleset_character"]["rule_binding"]
    assert repaired["content_version"] == bundle.manifest.content_version


def _companion(
    companion_id: str, *, abilities: bool = True, armor_proficiencies: bool = True,
) -> dict:
    canonical: dict = {
        "resources": {"hp": 20, "max_hp": 20},
        "conditions": {},
        "derived": {
            "armor_class": 18, "speed": 30, "initiative": 1,
            "proficiency_bonus": 2, "saving_throws": {"str": -1, "dex": 1, "con": 1},
        },
        "equipment": {"item_refs": ["item:chain_mail", "item:shield"]},
        "build": {"class_levels": [{"class_ref": "class:wizard", "level": 1}]},
    }
    if abilities:
        canonical["abilities"] = _abilities(8, 12)
    if armor_proficiencies:
        canonical["proficiencies"] = {
            "skill_values": {}, "weapon_category_refs": [], "armor_category_refs": [],
        }
    return {
        "id": companion_id, "name": companion_id.title(),
        "controller": "ai", "active": True, "ruleset_character": canonical,
    }


def test_legacy_companions_are_reconciled_or_left_untouched(runtime) -> None:
    player = _legacy_sheet(runtime, "curious_arcanist", ["Chain Mail", "Shield"], 18)
    instance = _loaded_instance(
        player,
        companions=(
            _companion("mira"),
            _companion("broken_abilities", abilities=False),
            _companion("broken_proficiencies", armor_proficiencies=False),
        ),
    )
    combat = _start_through_runtime(runtime, instance)
    companions = ruleset_runtime.state(instance)["party"]["companions"]

    mira = companions["mira"]["ruleset_character"]
    assert mira["derived"]["armor_class"] == 16
    assert mira["derived"]["armor_training"] == {
        "untrained_armor_worn": True,
        "speed_penalty": 10,
        "stealth_disadvantage": True,
    }
    view = Dnd2024CombatEngine(_bundle(runtime))._actor_view(
        instance, combat, "companion:mira",
    )
    assert view["speed"] == 20

    # Missing canonical facts are never guessed into "trained" or "wearing
    # nothing": the record is left exactly as the old save wrote it.
    for companion_id in ("broken_abilities", "broken_proficiencies"):
        broken = companions[companion_id]["ruleset_character"]
        assert broken["derived"]["armor_class"] == 18
        assert "armor_training" not in broken["derived"]
