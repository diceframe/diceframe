"""D1：SRD 5.2.1 装备数据与两用（Versatile）词条消费者回归测试。

覆盖 T7–T14：38 件武器装载、8 件护甲派生、力量/隐匿后果、两用骰式切换、
非两用武器的数据失败关闭、locale 覆盖，以及"战利品 → 装备 → 攻击"闭环。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.commands.state_items import equip_owned_item, grant_classified_item
from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.character.derivation import (
    derive_armor_class,
    derive_armor_training_state,
)
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.action_adapter import (
    attack_damage_node,
    maximize_dice_nodes,
    roll_damage_node,
)
from src.rulesets.dnd2024.combat.catalog import (
    DICE_RE,
    FLAT_DAMAGE_RE,
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.combat.weapon_masteries import WEAPON_MASTERY_EFFECTS
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


TEMPLATES = Path("templates/rulesets/dnd2024_srd")

NEW_WEAPONS = (
    "club", "greatclub", "light_hammer", "crossbow_light", "dart", "sling",
    "battleaxe", "glaive", "halberd", "lance", "maul", "morningstar", "pike",
    "rapier", "trident", "war_pick", "warhammer", "whip", "blowgun",
    "crossbow_hand", "crossbow_heavy", "musket", "pistol",
)

VERSATILE = {
    "battleaxe": ("1d8", "1d10"),
    "longsword": ("1d8", "1d10"),
    "quarterstaff": ("1d6", "1d8"),
    "spear": ("1d6", "1d8"),
    "trident": ("1d8", "1d10"),
    "war_pick": ("1d8", "1d10"),
    "warhammer": ("1d8", "1d10"),
}

# id -> (category, ac_base, dex_cap, use_dex)
NEW_ARMORS = {
    "padded": ("light", 11, None, True),
    "hide": ("medium", 12, 2, True),
    "scale_mail": ("medium", 14, 2, True),
    "breastplate": ("medium", 14, 2, True),
    "half_plate": ("medium", 15, 2, True),
    "ring_mail": ("heavy", 14, None, False),
    "splint": ("heavy", 17, None, False),
    "plate": ("heavy", 18, None, False),
}

ALL_ARMOR_CATEGORIES = [
    "armor_category:light", "armor_category:medium",
    "armor_category:heavy", "armor_category:shield",
]


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum, f"{value} not in [{minimum}, {maximum}]"
        return value


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def bundle(runtime):
    return runtime.load_bundle("en")


@pytest.fixture(scope="module")
def catalog(bundle) -> Dnd2024CombatCatalog:
    return Dnd2024CombatCatalog.from_bundle(bundle)


class _BundleProxy:
    """Serve patched entities while keeping the rest of the bundle intact."""

    def __init__(self, bundle, overrides: dict[tuple[str, str], dict]) -> None:
        self._bundle = bundle
        self._overrides = overrides

    def get(self, kind: str, entity_id: str = ""):
        patched = self._overrides.get((kind, entity_id))
        if patched is not None:
            return patched
        return self._bundle.get(kind, entity_id)

    def list(self, kind: str):
        return self._bundle.list(kind)


def _sheet(runtime: Dnd2024Runtime, name: str = "Equipment Hero") -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


# --------------------------------------------------------------------------
# T7 23 件新武器逐件装载
# --------------------------------------------------------------------------

@pytest.mark.parametrize("weapon_id", NEW_WEAPONS)
def test_new_weapon_is_a_usable_catalog_entry(catalog, bundle, weapon_id: str) -> None:
    weapon = catalog.weapons[weapon_id]
    item = bundle.get("item", weapon_id)

    assert item is not None and item.get("item_type") == "weapon"
    damage = str(weapon["damage"])
    assert DICE_RE.fullmatch(damage) or FLAT_DAMAGE_RE.fullmatch(damage)
    assert 5 <= int(weapon["range"]) <= 600
    assert weapon["category"] in {"simple", "martial"}
    assert weapon["mastery"] in WEAPON_MASTERY_EFFECTS
    assert item["damage"] == damage


def test_the_srd_weapon_table_is_complete(catalog) -> None:
    assert len(catalog.weapons) == 38
    for weapon_id in NEW_WEAPONS:
        assert weapon_id in catalog.weapons


# D1-D4 review B1：已确认错误的武器行必须有逐字段精确期望；只验证"字段存在"
# 无法阻止 1d12 / topple 这类数值错误重新混入。
REVIEWED_WEAPON_ROWS = {
    "crossbow_heavy": {
        "damage": "1d10", "damage_type": "piercing", "mastery": "push",
        "range": 100, "long_range": 400, "heavy": True, "ranged": True,
        "ammunition_item_ref": "item:bolts",
    },
    "spear": {
        "damage": "1d6", "damage_type": "piercing", "mastery": "sap",
        "range": 5, "long_range": 60, "thrown_range": 20,
        "damage_two_handed": "1d8",
    },
    "mace": {
        "damage": "1d6", "damage_type": "bludgeoning", "mastery": "sap",
        "range": 5,
    },
}


@pytest.mark.parametrize("weapon_id", sorted(REVIEWED_WEAPON_ROWS))
def test_reviewed_weapon_rows_match_the_srd_exactly(catalog, bundle, weapon_id: str) -> None:
    expected = REVIEWED_WEAPON_ROWS[weapon_id]
    weapon = catalog.weapons[weapon_id]
    item = bundle.get("item", weapon_id)

    assert item is not None and item["damage"] == expected["damage"]
    for field, value in expected.items():
        assert weapon[field] == value, f"{weapon_id}.{field}"
    if weapon_id == "crossbow_heavy":
        # 重弩不是两用武器：不能出现双手伤害骰改写。
        assert "damage_two_handed" not in weapon


# --------------------------------------------------------------------------
# T8/T9 8 件新护甲：派生 AC、力量要求、隐匿劣势
# --------------------------------------------------------------------------

@pytest.mark.parametrize("armor_id", sorted(NEW_ARMORS))
def test_new_armor_derives_the_srd_armor_class(bundle, armor_id: str) -> None:
    _category, ac_base, dex_cap, use_dex = NEW_ARMORS[armor_id]
    abilities = {"str": 16, "dex": 16}
    dex_bonus = 0 if not use_dex else min(3, dex_cap) if dex_cap else 3

    assert derive_armor_class(
        abilities, [f"item:{armor_id}"], bundle,
        armor_category_refs=ALL_ARMOR_CATEGORIES,
    ) == ac_base + dex_bonus


def test_armor_without_category_still_fails_closed(bundle) -> None:
    patched = json.loads(json.dumps(bundle.get("item", "plate")))
    patched.pop("category")
    proxy = _BundleProxy(bundle, {("item", "plate"): patched})

    with pytest.raises(ValueError):
        derive_armor_training_state(
            {"str": 16, "dex": 12}, ["item:plate"],
            ALL_ARMOR_CATEGORIES, proxy,
        )


def test_strength_requirement_reduces_speed_and_stealth_is_projected(bundle) -> None:
    weak = derive_armor_training_state(
        {"str": 12, "dex": 12}, ["item:plate"], ALL_ARMOR_CATEGORIES, bundle,
    )
    strong = derive_armor_training_state(
        {"str": 15, "dex": 12}, ["item:plate"], ALL_ARMOR_CATEGORIES, bundle,
    )

    assert weak["speed_penalty"] == 10
    assert weak["stealth_disadvantage"] is True
    assert strong["speed_penalty"] == 0
    assert strong["untrained_armor_worn"] is False


# --------------------------------------------------------------------------
# T11 数据失败关闭：两用声明必须在数据上成立
# --------------------------------------------------------------------------

@pytest.mark.parametrize("weapon_id,patch", [
    ("longbow", {"damage_two_handed": "1d10"}),      # 远程武器没有两用
    ("blowgun", {"damage_two_handed": "1d4"}),       # 固定伤害武器没有两用
    ("longsword", {"damage_two_handed": "1d8"}),     # 双手骰并不优于单手骰
    ("longsword", {"damage_two_handed": "d10"}),     # 非法骰式
])
def test_impossible_two_handed_declarations_fail_closed(bundle, weapon_id, patch) -> None:
    patched = json.loads(json.dumps(bundle.get("combat_catalog", "srd_combat_core")))
    patched["weapons"][weapon_id].update(patch)
    proxy = _BundleProxy(bundle, {("combat_catalog", "srd_combat_core"): patched})

    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(proxy)


def test_two_handed_dice_that_really_grow_are_accepted(bundle) -> None:
    """``club`` 1d4 声明双手 1d6 是"更大的骰子"，属于合法数据。"""

    patched = json.loads(json.dumps(bundle.get("combat_catalog", "srd_combat_core")))
    patched["weapons"]["club"]["damage_two_handed"] = "1d6"
    proxy = _BundleProxy(bundle, {("combat_catalog", "srd_combat_core"): patched})
    catalog = Dnd2024CombatCatalog.from_bundle(proxy)
    assert catalog.weapons["club"]["damage_two_handed"] == "1d6"


# --------------------------------------------------------------------------
# T10/T12 两用消费者：骰式切换、重击翻倍、动作经济
# --------------------------------------------------------------------------

def _combat_instance(runtime, equipment: list[dict]) -> tuple[Dnd2024CombatEngine, GameInstance]:
    sheet = _sheet(runtime)
    sheet["equipment"] = equipment
    sheet["inventory"] = []
    Dnd2024CharacterStateReconciler(runtime.load_bundle("en")).reconcile(
        sheet, frozenset({"equipment"}),
    )
    instance = GameInstance(
        game_key=("test", "dnd2024-two-handed", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Equipment Hero", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    start = {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [{
            "id": "goblin-1", "name": "Goblin", "hp": 60, "armor_class": 5,
            "speed": 30, "position": 5, "initiative_modifier": 2,
            "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
            "saving_throws": {"dex": 2, "wis": -1, "con": 0},
            "attacks": [{
                "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            }],
        }],
    }
    resolved = engine.resolve_intent(instance, start, SequenceRng([20, 1]))
    assert resolved["ok"] is True
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return engine, instance


def _attack(engine, instance, intent_id: str, rng, *, two_handed=None) -> dict:
    intent = {
        "intent_id": intent_id, "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:longsword",
    }
    if two_handed is not None:
        intent["two_handed"] = two_handed
    return engine.resolve_intent(instance, intent, rng)


def _damage_event(resolved: dict) -> dict:
    return next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "resource.changed" and event["resource"] == "hp"
    )


def test_two_handed_swaps_to_the_bigger_die(runtime) -> None:
    engine, instance = _combat_instance(
        runtime, [{"name": "Longsword", "type": "weapon", "slot": "main_hand",
                   "item_ref": "item:longsword"}],
    )

    one_handed = _damage_event(_attack(engine, instance, "a1", SequenceRng([15, 8])))
    two_handed = _damage_event(
        _attack(engine, instance, "a2", SequenceRng([15, 10]), two_handed=True),
    )

    # 声明值本身证明骰面：8 只能在 d8 里出现，10 只能在 d10 里出现。
    assert one_handed["rolls"] == [8]
    assert two_handed["rolls"] == [10]


def test_two_handed_critical_doubles_the_swapped_dice(runtime) -> None:
    engine, instance = _combat_instance(
        runtime, [{"name": "Longsword", "type": "weapon", "slot": "main_hand",
                   "item_ref": "item:longsword"}],
    )

    crit = _damage_event(
        _attack(engine, instance, "crit", SequenceRng([20, 10, 10]), two_handed=True),
    )

    assert crit["critical"] is True
    assert crit["rolls"] == [10, 10]


def test_two_handed_die_feeds_the_maximize_pipeline(catalog) -> None:
    """S-P2：骰式切换后，最大化作用于双手骰而不是单手骰。"""

    weapon = catalog.weapons["longsword"]
    one_handed = attack_damage_node(
        damage_formula=weapon["damage"], modifier=0, critical=False,
    )
    two_handed = attack_damage_node(
        damage_formula=weapon["damage_two_handed"], modifier=0, critical=False,
    )

    # 最大化不消耗 RNG（空 RNG 足证），结果只可能是对应骰面。
    assert roll_damage_node(maximize_dice_nodes(one_handed), SequenceRng([])) == (8, [])
    assert roll_damage_node(maximize_dice_nodes(two_handed), SequenceRng([])) == (10, [])

    # 重击先翻倍、最大化后：2d10 → 20，而不是单手 2d8 → 16。
    crit_two_handed = attack_damage_node(
        damage_formula=weapon["damage_two_handed"], modifier=0, critical=True,
    )
    assert roll_damage_node(maximize_dice_nodes(crit_two_handed), SequenceRng([])) == (20, [])


def test_two_handed_attack_keeps_the_conditional_check_bonus(runtime) -> None:
    """S-P10：祝福的 d4 加值仍作用于双手攻击检定，伤害仍用双手骰。"""

    engine, instance = _combat_instance(
        runtime, [{"name": "Longsword", "type": "weapon", "slot": "main_hand",
                   "item_ref": "item:longsword"}],
    )
    conditions = instance.get_character_sheet("gm")["ruleset_character"].setdefault(
        "conditions", {},
    )
    conditions["blessed"] = {"source": "test"}

    resolved = _attack(
        engine, instance, "blessed-two-handed", SequenceRng([15, 3, 10]), two_handed=True,
    )

    assert resolved["ok"] is True, resolved
    check = next(
        event for event in resolved["event_batch"]["events"]
        if event.get("type") == "check.resolved" and event.get("kind") == "attack"
    )
    assert check["check_bonus"] == 3
    assert check["check_bonus_rolls"] == [
        {"condition": "blessed", "die": 4, "roll": 3},
    ]
    assert _damage_event(resolved)["rolls"] == [10]


@pytest.mark.parametrize("extra", [
    {"name": "Shield", "type": "shield", "slot": "off_hand", "item_ref": "item:shield"},
])
def test_two_handed_is_rejected_when_a_shield_occupies_the_other_hand(runtime, extra) -> None:
    engine, instance = _combat_instance(
        runtime, [
            {"name": "Longsword", "type": "weapon", "slot": "main_hand",
             "item_ref": "item:longsword"},
            extra,
        ],
    )

    resolved = _attack(engine, instance, "blocked", SequenceRng([15, 4]), two_handed=True)

    assert resolved["ok"] is False
    assert "two-handed" in str(resolved.get("error") or "")


def test_two_handed_requires_the_weapon_to_declare_it(runtime) -> None:
    engine, instance = _combat_instance(
        runtime, [{"name": "Greatsword", "type": "weapon", "slot": "main_hand",
                   "item_ref": "item:greatsword"}],
    )
    intent = {
        "intent_id": "gs", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword", "two_handed": True,
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4]))

    assert resolved["ok"] is False
    assert "two-handed" in str(resolved.get("error") or "")


def test_carrying_a_backup_weapon_does_not_block_two_handed(runtime) -> None:
    engine, instance = _combat_instance(runtime, [
        {"name": "Spear", "type": "weapon", "slot": "main_hand", "item_ref": "item:spear"},
        {"name": "Longsword", "type": "weapon", "slot": "main_hand", "item_ref": "item:longsword"},
    ])
    intent = {
        "intent_id": "spear", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:spear", "two_handed": True,
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4]))

    assert resolved["ok"] is True


def test_a_shield_in_the_other_hand_blocks_two_handed(runtime) -> None:
    engine, instance = _combat_instance(runtime, [
        {"name": "Spear", "type": "weapon", "slot": "main_hand", "item_ref": "item:spear"},
        {"name": "Shield", "type": "shield", "slot": "off_hand", "item_ref": "item:shield"},
    ])
    intent = {
        "intent_id": "spear", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:spear", "two_handed": True,
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4]))

    assert resolved["ok"] is False
    assert "shield" in str(resolved.get("error") or "")


# --------------------------------------------------------------------------
# T13 locale 覆盖
# --------------------------------------------------------------------------

@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_every_new_record_has_a_display_only_locale(locale: str) -> None:
    for item_id in (*NEW_WEAPONS, *NEW_ARMORS):
        path = TEMPLATES / "locales" / locale / "items" / f"{item_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["target"] == {"kind": "item", "id": item_id}
        assert set(payload["fields"]) == {"name"}
        assert payload["fields"]["name"].strip()


# --------------------------------------------------------------------------
# T14 战利品 → 装备 → 一次攻击与 AC 的端到端
# --------------------------------------------------------------------------

def test_loot_to_equipment_to_combat_numbers_end_to_end(runtime) -> None:
    sheet = _sheet(runtime)
    sheet["equipment"] = []
    sheet["inventory"] = []
    reconciler = Dnd2024CharacterStateReconciler(runtime.load_bundle("en"))
    summary = reconciler.reconcile(sheet, frozenset({"equipment"}))
    unarmored_ac = (
        summary["armor_class"] if summary
        else sheet["ruleset_character"]["derived"]["armor_class"]
    )

    # 战利品只进背包，不改变当前装备。
    grant_classified_item(sheet, "Plate Armor", "equipment", item_ref="item:plate")
    reconciler.reconcile(sheet, frozenset({"inventory"}))
    row = sheet["inventory"][0]
    assert row["item_ref"] == "item:plate"
    assert row["type"] == "armor"
    assert row["slot"] == "body"
    assert sheet["ruleset_character"]["derived"]["armor_class"] == unarmored_ac

    # 装备后才改变 AC，且 AC 完全来自规则集物品定义。
    assert equip_owned_item(sheet, "Plate Armor", item_ref="item:plate") is True
    summary = reconciler.reconcile(sheet, frozenset({"equipment", "inventory"}))
    assert summary["armor_class"] == 18
    assert sheet["ruleset_character"]["equipment"]["item_refs"] == ["item:plate"]
    # 该角色力量 17 满足板甲的力量要求：不减速，但隐匿劣势照常投影。
    assert sheet["ruleset_character"]["derived"]["armor_training"] == {
        "untrained_armor_worn": False,
        "speed_penalty": 0,
        "stealth_disadvantage": True,
    }

    # 同一份 canonical 直接进战斗：伤害骰来自刚装备的武器。
    grant_classified_item(sheet, "Longsword", "equipment", item_ref="item:longsword")
    assert equip_owned_item(sheet, "Longsword", item_ref="item:longsword") is True
    reconciler.reconcile(sheet, frozenset({"equipment", "inventory"}))

    instance = GameInstance(
        game_key=("test", "dnd2024-e2e", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.players["gm"] = {"character_name": "Equipment Hero", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    start = {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [{
            "id": "goblin-1", "name": "Goblin", "hp": 60, "armor_class": 5,
            "speed": 30, "position": 5, "initiative_modifier": 2,
            "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
            "saving_throws": {"dex": 2, "wis": -1, "con": 0},
            "attacks": [{
                "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            }],
        }],
    }
    started = engine.resolve_intent(instance, start, SequenceRng([20, 1]))
    assert started["ok"] is True
    engine.apply_batch(instance, started["event_batch"])

    resolved = engine.resolve_intent(instance, {
        "intent_id": "e2e-attack", "type": "attack", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": "item:longsword",
    }, SequenceRng([15, 7]))

    assert resolved["ok"] is True
    assert _damage_event(resolved)["rolls"] == [7]
