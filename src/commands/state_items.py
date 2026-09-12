"""角色物品、装备、关键物品写入辅助。"""

from __future__ import annotations

import copy
import logging
import re
from typing import Iterable

logger = logging.getLogger("trpg")

# GM 常把数量直接写进物品串（"回复药水x5"、"地图×2"）。识别并剥离这个后缀，
# 让数量进入 qty 字段而不是污染物品名。只认结尾的 x/X/× + 数字，避免误伤
# 名字里本就含 x 的条目。
_ITEM_QTY_SUFFIX = re.compile(r"^(.+?)\s*[xX×]\s*(\d{1,2})$")

_MAX_GRANT_QTY = 99

# narrative 装备操作允许显式指定的槽位。LLM 不是装备规则 authority：不在表内
# 的 slot 会被丢弃，由服务端按物品信息推断。
EQUIPMENT_SLOTS = frozenset({
    "main_hand", "off_hand", "body", "armor", "head", "hands",
    "waist", "feet", "back", "accessory", "pack",
})


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


def canonical_equipment_entry(item_name: str, rule=None) -> dict | None:
    """规则集 template.items 定义了该物品时，按 canonical 定义构造装备条目。

    规则集 canonical 定义是装备数值的 authority（damage/ac_bonus/item_key 等）；
    没有定义时返回 None，由调用方回退到名称推断。
    """

    if rule is None:
        return None
    template = getattr(rule, "template", None)
    item_defs = template.get("items") if isinstance(template, dict) else None
    if not isinstance(item_defs, dict) or not item_defs:
        return None
    from src.engine.constants import canonical_item_key

    display_key = str(item_name or "").strip().casefold()
    if not display_key:
        return None
    item_key = canonical_item_key(item_name) or ""
    item_def = item_defs.get(item_key) if item_key else None
    if not isinstance(item_def, dict):
        item_def = None
        for candidate_key, candidate in item_defs.items():
            if (
                isinstance(candidate, dict)
                and str(candidate.get("name") or "").strip().casefold() == display_key
            ):
                item_key, item_def = str(candidate_key), candidate
                break
    if not isinstance(item_def, dict):
        return None
    item_type = str(item_def.get("type") or "").strip().casefold()
    entry: dict = {
        "name": str(item_def.get("name") or item_name),
        "quality": "common",
        "slot": "",
    }
    if item_key:
        entry["item_key"] = item_key
    if item_type == "weapon":
        entry.update({
            "type": "weapon",
            "damage": int(item_def.get("damage", 0) or 0),
            "slot": "main_hand",
        })
        if item_def.get("damage_dice"):
            entry["damage_dice"] = item_def["damage_dice"]
    elif item_type == "shield":
        entry.update({
            "type": "shield",
            "slot": "off_hand",
            "ac_bonus": int(item_def.get("ac_bonus", 0) or 0),
        })
    elif item_type == "armor":
        entry.update({
            "type": "armor",
            "slot": "armor",
            "armor_category": str(item_def.get("armor_category") or ""),
            "ac_base": int(item_def.get("ac_base", 0) or 0),
        })
        if "armor" in item_def:
            entry["armor"] = int(item_def["armor"] or 0)
        if "dex_cap" in item_def:
            entry["dex_cap"] = item_def["dex_cap"]
    elif item_type == "focus":
        entry["type"] = "focus"
    else:
        return None
    return entry


def _find_inventory_row(
    inventory: list, item_name: str,
) -> tuple[int | None, dict | None]:
    """按 canonical item_key 优先、名称其次找一行 qty>0 的库存。"""

    from src.engine.constants import canonical_item_key

    wanted_key = canonical_item_key(item_name) or ""
    wanted_name = str(item_name or "").strip().casefold()
    fallback: tuple[int, dict] | None = None
    for index, row in enumerate(inventory):
        if not isinstance(row, dict) or int(row.get("qty", 0) or 0) <= 0:
            continue
        row_key = str(row.get("item_key") or "")
        if wanted_key and row_key and row_key == wanted_key:
            return index, row
        if (
            fallback is None
            and str(row.get("name") or "").strip().casefold() == wanted_name
        ):
            fallback = (index, row)
    return fallback if fallback is not None else (None, None)


def take_from_inventory(character_sheet: dict, item_name: str) -> dict | None:
    """从背包取走 1 件物品，返回携带完整 metadata 的条目副本。

    qty 永远不会变成负数；数量减到 0 时移除该行。找不到返回 None。
    """

    inventory = character_sheet.setdefault("inventory", [])
    index, row = _find_inventory_row(inventory, item_name)
    if index is None or row is None:
        return None
    entry = copy.deepcopy(row)
    row["qty"] = int(row.get("qty", 1) or 1) - 1
    if row["qty"] <= 0:
        inventory.pop(index)
    entry["qty"] = 1
    return entry


def return_equipment_to_inventory(character_sheet: dict, entry: dict) -> None:
    """把装备条目放回背包：整条 metadata 随行，不从名字重猜属性。"""

    name = str(entry.get("name") or "").strip()
    if not name:
        return
    inventory = character_sheet.setdefault("inventory", [])
    entry = copy.deepcopy(entry)
    entry["qty"] = 1
    for row in inventory:
        if (
            isinstance(row, dict)
            and str(row.get("name") or "").strip().casefold() == name.casefold()
        ):
            row["qty"] = int(row.get("qty", 0) or 0) + 1
            for key, value in entry.items():
                # 行里已有的字段不动（避免覆盖同名牌的既有属性），
                # 只补齐行缺失的装备 metadata（item_key/damage/type/slot…）。
                if key not in row:
                    row[key] = value
            return
    inventory.append(entry)


def _resolve_weapon_damage(
    entry: dict,
    requested_name: str,
    custom_damage: int | None,
    canonical_damage: int | None,
) -> int:
    """武器伤害 authority 顺序：规则集定义 > 物品自带 > 全局武器表 > legacy 值。"""

    if canonical_damage is not None:
        return max(0, int(canonical_damage))
    existing = int(entry.get("damage", 0) or 0)
    if existing > 0:
        return existing
    from src.engine.constants import WEAPON_DAMAGE

    name = str(entry.get("name") or requested_name)
    table_damage = WEAPON_DAMAGE.get(name, WEAPON_DAMAGE.get(requested_name, 0))
    if table_damage > 0:
        return int(table_damage)
    if isinstance(custom_damage, int) and custom_damage > 0:
        return custom_damage
    return 0


def _finalize_equipment_entry(
    entry: dict,
    requested_name: str,
    slot: str,
    custom_damage: int | None,
    rule=None,
) -> dict:
    """补全 type/slot/damage，使条目成为合法的装备槽条目。

    规则集 canonical 定义存在时整体采用它（item 自带杂项 metadata 保留）；
    slot 优先级：显式指定 > 物品自带 > 按类型推断。
    """

    canonical = canonical_equipment_entry(requested_name, rule)
    canonical_damage: int | None = None
    if canonical is not None:
        if canonical.get("type") == "weapon":
            canonical_damage = int(canonical.get("damage", 0) or 0)
        for key, value in entry.items():
            # canonical 定义的数值字段优先；物品自带的其它 metadata（effect 等）保留。
            if key not in canonical and key not in {"qty", "slot"}:
                canonical[key] = value
        entry = canonical
    else:
        inferred = equipment_entry(requested_name)
        entry.setdefault("type", inferred.get("type", "armor"))
        entry.setdefault("quality", inferred.get("quality", "common"))
        if int(entry.get("damage", 0) or 0) == 0:
            entry.setdefault("damage", int(inferred.get("damage", 0) or 0))
    if slot:
        entry["slot"] = slot
    if not str(entry.get("slot") or ""):
        entry["slot"] = "main_hand" if entry.get("type") == "weapon" else "body"
    if entry.get("type") == "weapon":
        damage = _resolve_weapon_damage(entry, requested_name, custom_damage, canonical_damage)
        if isinstance(custom_damage, int) and custom_damage > 0 and damage != custom_damage:
            logger.info(
                "武器 %s 伤害采用权威值 %d，忽略标签提供的 %d",
                requested_name, damage, custom_damage,
            )
        entry["damage"] = damage
    return entry


def equip_owned_item(
    character_sheet: dict,
    item_name: str,
    *,
    slot: str = "",
    custom_damage: int | None = None,
    legacy_gain: bool = False,
    rule=None,
) -> bool:
    """装备一件背包里的物品：inventory -1，目标槽旧装备返回背包。

    - 未拥有时默认 warning 并忽略，绝不凭空装备；
    - ``legacy_gain=True`` 是 legacy WEAPON 标签的兼容路径：旧 Prompt 把"获得
      武器"与"装备武器"混在同一标签里，未拥有时先获得 1 件再立即装备；
    - 装备条目携带物品自身 metadata 往返（equipment -> inventory -> equipment
      不丢 item_key/damage/slot 等），不从 display name 重猜。
    """

    name = str(item_name or "").strip()
    if not name:
        return False
    entry = take_from_inventory(character_sheet, name)
    if entry is None:
        if not legacy_gain:
            logger.warning("忽略装备未拥有物品: %s", name)
            return False
        logger.info(
            "legacy WEAPON acquisition compatibility: %s 不在背包，先获得再装备", name,
        )
        entry = canonical_equipment_entry(name, rule) or equipment_entry(name)
        entry["qty"] = 1
    slot = str(slot or "").strip().lower()
    if slot and slot not in EQUIPMENT_SLOTS:
        logger.warning("未知装备槽位 %s，已丢弃（由服务端推断）: %s", slot, name)
        slot = ""
    entry = _finalize_equipment_entry(entry, name, slot, custom_damage, rule)
    target_slot = str(entry.get("slot") or "")
    equipment = character_sheet.setdefault("equipment", [])
    if target_slot:
        previous = next(
            (
                item for item in equipment
                if isinstance(item, dict) and str(item.get("slot") or "") == target_slot
            ),
            None,
        )
        if previous is not None:
            equipment.remove(previous)
            return_equipment_to_inventory(character_sheet, previous)
    equipment.append(entry)
    return True


def unequip_item(character_sheet: dict, item_name: str) -> bool:
    """卸下当前装备中的一件并放回背包；未装备时 warning + no-op。"""

    from src.engine.constants import canonical_item_key

    name = str(item_name or "").strip()
    if not name:
        return False
    wanted_key = canonical_item_key(name) or ""
    wanted_name = name.casefold()
    equipment = character_sheet.setdefault("equipment", [])
    entry = next(
        (
            item for item in equipment
            if isinstance(item, dict)
            and (
                (wanted_key and str(item.get("item_key") or "") == wanted_key)
                or str(item.get("name") or "").strip().casefold() == wanted_name
            )
        ),
        None,
    )
    if entry is None:
        logger.warning("忽略卸下未装备的物品: %s", name)
        return False
    equipment.remove(entry)
    return_equipment_to_inventory(character_sheet, entry)
    return True
