"""Issue #254：装备/背包状态协议（获得/装备/卸下/使用）回归测试。

覆盖：
- 解析器：同轮多事件不覆盖、新标签 WEAPON_GAIN/EQUIP_ITEM/UNEQUIP_ITEM、
  legacy WEAPON 的兼容标记与 damage 钳制；
- applier：固定顺序（获得 -> 装备/卸下 -> 使用）、装备/背包往返不丢
  metadata、qty 永不为负、legacy 单值字段兼容、规则集 canonical 定义优先；
- economy：未确认购买的商品不能通过 item_gains/equipment_ops 绕过购买确认；
- prompts：zh/en/ja 三语言标签协议同步。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.commands.madness_tracker import MadnessTracker
from src.commands.player_state_applier import PlayerStateApplier
from src.commands.tag_parser import parse_tag_state
from src.commands.tag_summary import summarize_tags
from src.engine.constants import WEAPON_DAMAGE
from src.engine.economy import filter_unconfirmed_purchase_grants, queue_purchase_offer
from src.engine.game_instance import GameInstance, GameState
from src.rules.rule_system import RuleSystem

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def _parse(tags: str) -> dict:
    return parse_tag_state(f"叙事\n---\n{tags}\nNONE")


def _instance() -> GameInstance:
    instance = GameInstance(("web", "item", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.players["p1"] = {"character_name": "小林", "character_sheet": {
        "hp": 30, "max_hp": 30,
        "inventory": [], "equipment": [], "key_items": [],
    }}
    return instance


def _apply(pud: dict, *, inventory: list | None = None, rule=None) -> dict:
    instance = _instance()
    if inventory is not None:
        instance.players["p1"]["character_sheet"]["inventory"] = inventory
    PlayerStateApplier(MadnessTracker()).apply_players(instance, {"p1": pud}, rule=rule)
    return instance.players["p1"]["character_sheet"]


def _equip_slot(cs: dict, slot: str) -> dict | None:
    return next((item for item in cs["equipment"] if item.get("slot") == slot), None)


def _inv(cs: dict, name: str) -> dict | None:
    return next((item for item in cs["inventory"] if item.get("name") == name), None)


# ---------------------------------------------------------------- parser


def test_parser_two_equips_do_not_overwrite():
    pud = _parse("EQUIP:web_user:皮甲\nEQUIP:web_user:护身符")["state_update"]["players"]["web_user"]
    assert [gain["name"] for gain in pud["item_gains"]] == ["皮甲", "护身符"]


def test_parser_two_uses_do_not_overwrite():
    pud = _parse("USE:web_user:药水\nUSE:web_user:解毒剂")["state_update"]["players"]["web_user"]
    assert [use["name"] for use in pud["item_uses"]] == ["药水", "解毒剂"]


def test_parser_two_weapons_keep_order():
    pud = _parse("WEAPON:web_user:长剑\nWEAPON:web_user:长弓")["state_update"]["players"]["web_user"]
    assert [op["name"] for op in pud["equipment_ops"]] == ["长剑", "长弓"]
    assert all(
        op["op"] == "equip" and op["slot"] == "main_hand" and op.get("legacy")
        for op in pud["equipment_ops"]
    )


def test_parser_new_item_tags():
    pud = _parse(
        "WEAPON_GAIN:web_user:匕首\nEQUIP_ITEM:web_user:皮甲:body\nUNEQUIP_ITEM:web_user:旧盾",
    )["state_update"]["players"]["web_user"]
    assert pud["item_gains"] == [{"name": "匕首", "category": "weapon", "qty": 1}]
    assert pud["equipment_ops"][0] == {"op": "equip", "name": "皮甲", "slot": "body"}
    assert pud["equipment_ops"][1] == {"op": "unequip", "name": "旧盾"}


def test_parser_equip_item_unknown_slot_is_dropped():
    pud = _parse("EQUIP_ITEM:web_user:护符:toe_ring")["state_update"]["players"]["web_user"]
    assert pud["equipment_ops"] == [{"op": "equip", "name": "护符"}]


def test_parser_legacy_weapon_damage_is_bounded():
    pud = _parse("WEAPON:web_user:血色魔剑:999")["state_update"]["players"]["web_user"]
    assert pud["equipment_ops"][0]["damage"] == 15  # hp_based combat model 武器上限


# ---------------------------------------------------------------- applier


def test_equip_tag_only_gains_into_inventory():
    cs = _apply({"item_gains": [{"name": "硬牛皮甲", "category": "equipment", "qty": 1}]})
    assert _inv(cs, "硬牛皮甲") is not None
    assert cs["equipment"] == []


def test_equip_item_moves_inventory_to_equipment():
    cs = _apply({
        "item_gains": [{"name": "硬牛皮甲", "category": "equipment", "qty": 1}],
        "equipment_ops": [{"op": "equip", "name": "硬牛皮甲", "slot": "body"}],
    })
    assert _inv(cs, "硬牛皮甲") is None
    body = _equip_slot(cs, "body")
    assert body is not None and body["name"] == "硬牛皮甲"


def test_unequip_returns_to_inventory():
    cs = _apply({
        "equipment_ops": [
            {"op": "equip", "name": "硬牛皮甲", "slot": "body", "legacy": True},
            {"op": "unequip", "name": "硬牛皮甲"},
        ],
    })
    assert _equip_slot(cs, "body") is None
    assert _inv(cs, "硬牛皮甲") is not None


def test_weapon_switch_returns_old_main_hand_to_inventory():
    instance = _instance()
    cs = instance.players["p1"]["character_sheet"]
    cs["equipment"].append({"name": "铁剑", "type": "weapon", "damage": 6, "slot": "main_hand", "quality": "common"})
    cs["inventory"].append({"name": "长弓", "qty": 1, "effect": "", "quality": "common"})
    PlayerStateApplier(MadnessTracker()).apply_players(instance, {
        "p1": {"equipment_ops": [{"op": "equip", "name": "长弓", "slot": "main_hand", "legacy": True}]},
    })
    main_hand = _equip_slot(cs, "main_hand")
    assert main_hand is not None and main_hand["name"] == "长弓"
    assert main_hand["damage"] == WEAPON_DAMAGE["长弓"]  # 服务端武器表是权威
    old = _inv(cs, "铁剑")
    assert old is not None and old["qty"] == 1 and old.get("damage") == 6


def test_same_round_gain_then_equip_longsword():
    cs = _apply({
        "item_gains": [{"name": "长剑", "category": "weapon", "qty": 1}],
        "equipment_ops": [{"op": "equip", "name": "长剑", "slot": "main_hand", "legacy": True}],
    })
    main_hand = _equip_slot(cs, "main_hand")
    assert main_hand is not None and main_hand["name"] == "长剑"
    assert main_hand["damage"] == WEAPON_DAMAGE["长剑"]
    assert _inv(cs, "长剑") is None


def test_legacy_weapon_not_owned_gains_then_equips():
    cs = _apply({"equipment_ops": [{"op": "equip", "name": "弯刀", "slot": "main_hand", "legacy": True}]})
    main_hand = _equip_slot(cs, "main_hand")
    assert main_hand is not None and main_hand["name"] == "弯刀"


def test_equip_item_not_owned_is_noop():
    cs = _apply({"equipment_ops": [{"op": "equip", "name": "圣剑", "slot": "main_hand"}]})
    assert cs["equipment"] == []


def test_unequip_not_equipped_is_noop():
    cs = _apply({"equipment_ops": [{"op": "unequip", "name": "幻影甲"}]})
    assert cs["equipment"] == [] and cs["inventory"] == []


def test_same_round_multiple_events_all_execute():
    instance = _instance()
    cs = instance.players["p1"]["character_sheet"]
    cs["hp"] = 22  # 留出治疗空间（apply_hp_delta 始终封顶 max_hp），验证使用效果生效
    cs["inventory"].append({"name": "医疗包", "qty": 1, "effect": "恢复5HP"})
    PlayerStateApplier(MadnessTracker()).apply_players(instance, {"p1": {
        "item_gains": [
            {"name": "皮甲", "category": "equipment", "qty": 1},
            {"name": "护身符", "category": "equipment", "qty": 1},
        ],
        "equipment_ops": [{"op": "equip", "name": "皮甲", "slot": "body"}],
        "item_uses": [{"name": "医疗包"}],
    }})
    assert _equip_slot(cs, "body") is not None
    assert _inv(cs, "皮甲") is None  # 已穿上
    assert _inv(cs, "护身符") is not None
    assert cs["hp"] == 27  # 22 + 恢复5HP，效果生效
    assert _inv(cs, "医疗包")["qty"] == 0


def test_using_more_than_owned_clamps_at_zero():
    cs = _apply(
        {"item_uses": [{"name": "药水"}, {"name": "药水"}]},
        inventory=[{"name": "药水", "qty": 1, "effect": ""}],
    )
    assert _inv(cs, "药水")["qty"] == 0  # 永不为负


def test_equipment_metadata_survives_round_trip():
    instance = _instance()
    cs = instance.players["p1"]["character_sheet"]
    cs["equipment"].append({
        "name": "符文剑", "type": "weapon", "damage": 9, "slot": "main_hand",
        "quality": "rare", "item_key": "rune_sword", "effect": "吸血",
    })
    applier = PlayerStateApplier(MadnessTracker())
    applier.apply_players(instance, {"p1": {"equipment_ops": [{"op": "unequip", "name": "符文剑"}]}})
    row = _inv(cs, "符文剑")
    assert row["damage"] == 9 and row["item_key"] == "rune_sword"
    assert row["quality"] == "rare" and row["effect"] == "吸血"
    applier.apply_players(instance, {"p1": {"equipment_ops": [{"op": "equip", "name": "符文剑", "slot": "main_hand"}]}})
    main_hand = _equip_slot(cs, "main_hand")
    assert main_hand["damage"] == 9 and main_hand["item_key"] == "rune_sword"
    assert main_hand["quality"] == "rare" and main_hand["effect"] == "吸血"


def test_rule_canonical_item_definition_beats_tag_damage():
    rule = RuleSystem({
        "rule_id": "item_rule", "rule_name": "物品测试", "dice_system": "d20",
        "items": {
            "sun_blade": {"name": "圣辉之刃", "type": "weapon", "damage": 12, "damage_dice": "1d12"},
            "watch_armor": {"name": "卫兵板甲", "type": "armor", "armor_category": "heavy", "ac_base": 16},
        },
    })
    cs = _apply({
        "item_gains": [{"name": "圣辉之刃", "category": "weapon", "qty": 1}],
        "equipment_ops": [{"op": "equip", "name": "圣辉之刃", "slot": "main_hand", "damage": 999}],
    }, rule=rule)
    main_hand = _equip_slot(cs, "main_hand")
    assert main_hand["damage"] == 12  # 规则集 canonical 定义优先，LLM 数值被忽略
    assert main_hand["item_key"] == "sun_blade" and main_hand.get("damage_dice") == "1d12"

    cs_armor = _apply({
        "equipment_ops": [{"op": "equip", "name": "卫兵板甲", "slot": "body", "legacy": True}],
    }, rule=rule)
    armor = _equip_slot(cs_armor, "body")
    assert armor["ac_base"] == 16 and armor["type"] == "armor"
    assert armor["item_key"] == "watch_armor"


def test_freeform_weapon_uses_legacy_damage_when_no_authority():
    cs = _apply({"equipment_ops": [{"op": "equip", "name": "血色魔剑", "slot": "main_hand", "legacy": True, "damage": 15}]})
    main_hand = _equip_slot(cs, "main_hand")
    assert main_hand is not None and main_hand["damage"] == 15  # 无权威定义时 legacy 数值生效，不再是死字段


def test_legacy_scalar_fields_still_settle():
    cs = _apply(
        {"equip_gain": "皮甲", "weapon_change": "弯刀", "use_item": "药水"},
        inventory=[{"name": "药水", "qty": 1, "effect": ""}],
    )
    assert _inv(cs, "皮甲") is not None  # equip_gain 只获得
    main_hand = _equip_slot(cs, "main_hand")
    assert main_hand is not None and main_hand["name"] == "弯刀"  # legacy WEAPON 兼容：获得+装备
    assert _inv(cs, "药水")["qty"] == 0


def test_legacy_scalars_skipped_when_list_events_present():
    cs = _apply({
        "item_gains": [{"name": "护身符", "category": "equipment", "qty": 1}],
        "equip_gain": "皮甲",
    })
    assert [item["name"] for item in cs["inventory"]] == ["护身符"]  # 不重复结算


# ---------------------------------------------------------------- summary


def test_summary_prefers_list_events():
    data = _parse(
        "EQUIP:web_user:皮甲\nEQUIP:web_user:护身符\nWEAPON_GAIN:web_user:匕首\n"
        "WEAPON:web_user:长剑\nUNEQUIP_ITEM:web_user:旧盾\nUSE:web_user:药水",
    )
    tags = summarize_tags(data)["tags"]
    assert "EQUIP:web_user:皮甲" in tags
    assert "EQUIP:web_user:护身符" in tags
    assert "WEAPON_GAIN:web_user:匕首" in tags
    assert "WEAPON:web_user:长剑" in tags
    assert "UNEQUIP_ITEM:web_user:旧盾" in tags
    assert "USE:web_user:药水" in tags


# ---------------------------------------------------------------- economy


def test_pending_purchase_blocks_item_gains_and_equips():
    instance = _instance()
    queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["长剑"],
        source="gm_manual", source_ref="gm_manual:test",
    )
    data = {"state_update": {"players": {"p1": {
        "item_gains": [
            {"name": "长剑", "category": "weapon", "qty": 1},
            {"name": "无关护符", "category": "equipment", "qty": 1},
        ],
        "equipment_ops": [
            {"op": "equip", "name": "长剑", "slot": "main_hand", "legacy": True},
            {"op": "unequip", "name": "旧盾"},
        ],
    }}}}
    removed = filter_unconfirmed_purchase_grants(instance, data)
    assert removed == 2
    pud = data["state_update"]["players"]["p1"]
    assert [gain["name"] for gain in pud["item_gains"]] == ["无关护符"]
    assert [op["op"] for op in pud["equipment_ops"]] == ["unequip"]  # 卸下不授予物品，放行

    # 购买结算（committed）后同名叙事获得不再被拦截。
    instance.economy["proposals"][0]["status"] = "committed"
    settled = {"state_update": {"players": {"p1": {
        "item_gains": [{"name": "长剑", "category": "weapon", "qty": 1}],
    }}}}
    assert filter_unconfirmed_purchase_grants(instance, settled) == 0


def test_pending_purchase_blocks_legacy_scalar_fields_too():
    instance = _instance()
    queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["皮甲"],
        source="gm_manual", source_ref="gm_manual:test",
    )
    data = {"state_update": {"players": {"p1": {"equip_gain": "皮甲"}}}}
    assert filter_unconfirmed_purchase_grants(instance, data) == 1
    assert "equip_gain" not in data["state_update"]["players"]["p1"]


# ---------------------------------------------------------------- prompts


@pytest.mark.parametrize("lang", ["zh", "en", "ja"])
def test_prompt_tag_contract_synced(lang: str):
    text = (PROMPTS / f"gm_system_{lang}.md").read_text(encoding="utf-8")
    # 新标签存在
    assert "WEAPON_GAIN:" in text
    assert "EQUIP_ITEM:" in text
    assert "UNEQUIP_ITEM:" in text
    # WEAPON 不再描述为"获得或切换"
    assert "获得或切换" not in text
    assert "gained or switched" not in text
    assert "獲得または持ち替え" not in text
    # 购买防绕过说明覆盖新标签
    assert "LOOT/KEY_ITEM/WEAPON_GAIN" in text
