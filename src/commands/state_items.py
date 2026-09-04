"""角色物品、装备、关键物品写入辅助。"""

from __future__ import annotations

import re
from typing import Iterable

# GM 常把数量直接写进物品串（"回复药水x5"、"地图×2"）。识别并剥离这个后缀，
# 让数量进入 qty 字段而不是污染物品名。只认结尾的 x/X/× + 数字，避免误伤
# 名字里本就含 x 的条目。
_ITEM_QTY_SUFFIX = re.compile(r"^(.+?)\s*[xX×]\s*(\d{1,2})$")

_MAX_GRANT_QTY = 99


def split_item_quantity(item_name: str) -> tuple[str, int]:
    """Split a trailing 'xN' quantity suffix from a loot item string."""
    name = str(item_name or "").strip()
    match = _ITEM_QTY_SUFFIX.match(name)
    if match:
        base = match.group(1).strip()
        quantity = int(match.group(2))
        if base and 1 <= quantity <= _MAX_GRANT_QTY:
            return base, quantity
    return name, 1


def normalized_reward_entries(
    item_names: Iterable[str],
    categories: dict[str, list[str]],
) -> list[dict[str, str]]:
    """Normalize offer item names into classified proposal rewards.

    Every transport that converts a persisted offer into a proposal must go
    through this helper so the delivery destination never depends on how the
    offer was confirmed.
    """

    rewards: list[dict[str, str]] = []
    for item_name in item_names:
        name = str(item_name or "").strip()[:120]
        if not name:
            continue
        rewards.append({"name": name, "category": classify_item(name, categories)})
    return rewards[:8]


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
    qty: int = 1,
) -> None:
    qty = max(1, int(qty or 1))
    inventory = character_sheet.setdefault("inventory", [])
    # 同名同类的行优先精确匹配（name+effect+category），其次放宽 effect：
    # 角色卡初始行常带 effect 文案而 loot/购买授予不带，recap 的公开视图
    # （item_counts）本来就按名字聚合，入库不应因 effect 差异拆出碎片行。
    for item in inventory:
        if (
            item.get("name") == item_name
            and item.get("effect", "") == effect
            and item.get("category", "") == category
        ):
            item["qty"] = int(item.get("qty", 1)) + qty
            return
    for item in inventory:
        if item.get("name") == item_name and item.get("category", "") == category:
            if not item.get("effect") and effect:
                item["effect"] = effect
            item["qty"] = int(item.get("qty", 1)) + qty
            return
    new_item = {"name": item_name, "qty": qty, "effect": effect, "quality": quality}
    if category:
        new_item["category"] = category
    inventory.append(new_item)


def add_owned_equipment_to_inventory(
    character_sheet: dict,
    item_name: str,
    *,
    quality: str = "common",
    qty: int = 1,
) -> None:
    """Record an equipment item as owned but not currently equipped.

    Loot and purchases must not silently replace the character's active
    weapon.  Keeping the category on the inventory row lets an explicit
    equip action move it to the equipment list later.
    """

    append_inventory_item(
        character_sheet,
        item_name,
        quality=quality,
        category="equipment",
        qty=qty,
    )


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


def grant_classified_item(
    character_sheet: dict,
    item_name: str,
    category: str = "",
    qty: int = 1,
) -> None:
    """Grant one already-classified item to a character sheet."""
    item_name = str(item_name or "").strip()
    if not item_name:
        return
    if category == "equipment":
        # Obtaining equipment is not the same action as equipping it.  Keep
        # the item owned in the backpack; only an explicit WEAPON/equip action
        # may move it into an active slot.  This prevents a new loot/purchase
        # from silently replacing the weapon currently in hand.
        add_owned_equipment_to_inventory(character_sheet, item_name, qty=qty)
    elif category in ("key_item", "quest", "clue", "credential", "artifact"):
        append_key_item(character_sheet, item_name, category=category)
    elif category == "cyberware":
        cyberware = character_sheet.setdefault("cyberware", [])
        if not any(item.get("name") == item_name for item in cyberware):
            cyberware.append({"name": item_name, "effect": ""})
    elif category == "pills":
        append_inventory_item(character_sheet, item_name, category="丹药", qty=qty)
    else:
        append_inventory_item(character_sheet, item_name, qty=qty)
