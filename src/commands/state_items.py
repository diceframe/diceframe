"""角色物品、装备、关键物品写入辅助。"""

from __future__ import annotations


def classify_item(item_name: str, categories: dict[str, list[str]]) -> str:
    if not item_name:
        return ""
    if item_name in {"护甲油", "磨刀石"}:
        return "misc"
    key_item_keywords = (
        "凭证", "通行证", "许可证", "徽章", "令牌", "钥匙", "信件", "信",
        "手稿", "笔记", "日记", "地图", "线索", "契约", "证明", "档案",
        "访问卡", "门禁卡", "身份卡", "邀请函",
    )
    if any(keyword in item_name for keyword in key_item_keywords):
        return "key_item"
    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in item_name:
                return category.removesuffix("_keywords")
    return ""


def equipment_entry(item_name: str) -> dict:
    from src.engine.constants import WEAPON_DAMAGE as weapon_damage

    damage = weapon_damage.get(item_name, 0)
    is_weapon = damage > 0 or any(
        keyword in item_name for keyword in ("剑", "刀", "弓", "弩", "杖", "匕首", "矛", "锤", "斧", "钉头锤")
    )
    return {
        "name": item_name,
        "type": "weapon" if is_weapon else "armor",
        "damage": damage if is_weapon else 0,
        "slot": "main_hand" if is_weapon else "body",
        "quality": "common",
    }


def append_unique_equipment(character_sheet: dict, item_name: str) -> None:
    equipment = character_sheet.setdefault("equipment", [])
    if any(item.get("name") == item_name for item in equipment):
        return
    equipment.append(equipment_entry(item_name))


def append_inventory_item(
    character_sheet: dict,
    item_name: str,
    effect: str = "",
    quality: str = "common",
    category: str = "",
) -> None:
    inventory = character_sheet.setdefault("inventory", [])
    for item in inventory:
        if item.get("name") == item_name and item.get("effect", "") == effect and item.get("category", "") == category:
            item["qty"] = int(item.get("qty", 1)) + 1
            return
    new_item = {"name": item_name, "qty": 1, "effect": effect, "quality": quality}
    if category:
        new_item["category"] = category
    inventory.append(new_item)


def append_key_item(
    character_sheet: dict,
    item_name: str,
    note: str = "",
    category: str = "key_item",
) -> None:
    key_items = character_sheet.setdefault("key_items", [])
    for item in key_items:
        if item.get("name") == item_name:
            if note and not item.get("note"):
                item["note"] = note
            if category and not item.get("category"):
                item["category"] = category
            return
    new_item = {"name": item_name, "category": category}
    if note:
        new_item["note"] = note
    key_items.append(new_item)
