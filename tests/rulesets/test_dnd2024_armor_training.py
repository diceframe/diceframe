"""Armor Training (2024): item data, derivation, reconciliation and view projection.

The slice has one authority for the AC formula (:func:`derive_armor_class`) and
one for the training consequences (:func:`derive_armor_training_state`).  Tests
here exercise the real SRD bundle and the real live-equipment reconciliation
path, never a hand-built shortcut, so a data gap in the item catalog fails here
instead of silently treating untrained armor as trained.
"""

from __future__ import annotations

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

    view = engine._player_view("gm", canonical)

    assert view["speed"] == 20
    assert view["armor_training"] == canonical["derived"]["armor_training"]
    # Projection must copy, not alias: mutating the actor never edits canonical.
    view["armor_training"]["speed_penalty"] = 99
    assert canonical["derived"]["armor_training"]["speed_penalty"] == 10

    canonical["derived"]["armor_training"]["speed_penalty"] = 100
    assert engine._player_view("gm", canonical)["speed"] == 0


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
                "armor_class": 16, "speed": 30, "initiative": 1,
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
