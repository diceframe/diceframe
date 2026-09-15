"""角色工具 —— 属性随机生成、默认角色卡、酒馆角色卡导入等跨模块共享逻辑。"""

from __future__ import annotations

import json
import logging
import random
import struct
from pathlib import Path
from typing import Any

from src.rules.rule_system import RuleSystem
from src.rules.loader import RuleBundleLoader
from src.compat.characters import (
    migrate_legacy_character_sheet,
    normalize_character_sheet as normalize_legacy_character_sheet,
)

logger = logging.getLogger("trpg")


def roll_special_stat_value(max_val: int) -> int:
    """随机生成特殊属性初始值（CoC 理智值等），d3*5 取小。"""
    return min(random.randint(3, 18) * 5, max_val)


def initial_special_stat_value(stat: dict, attributes: dict) -> int:
    """Calculate initial rule special stats such as CoC SAN/Luck.

    CoC 7e uses percentile POW for initial SAN. Legacy/homebrew templates may
    still use D&D-like 3-18 POW, so those are converted with ×5 for backwards
    compatibility.
    """
    max_val = int(stat.get("max", 99) or 99)
    if "initial" in stat:
        return max(0, min(int(stat.get("initial") or 0), max_val))
    key = stat.get("key")
    if key == "sanity":
        pow_value = int(attributes.get("pow", 10) or 10)
        return min(pow_value if pow_value > 20 else pow_value * 5, max_val)
    if key == "luck":
        return roll_special_stat_value(max_val)
    return max_val


# 属性生成最大重试次数，防止死循环
_MAX_ROLL_ATTEMPTS = 1000


def format_skills(skills: list) -> list[dict]:
    """兼容处理技能列表：旧格式字符串转为新格式对象，新格式直接返回。"""
    result: list[dict] = []
    for s in skills:
        if isinstance(s, str):
            result.append({"name": s, "value": 20})
        elif isinstance(s, dict):
            result.append({"name": s.get("name", ""), "value": s.get("value", 20)})
    return result


def format_currency(currency: dict | int | float | None, rule: RuleSystem | None = None) -> str:
    """Format generic currency data through the unified CurrencyCodec."""
    from src.engine.currency import format_currency_amount, legacy_currency_spec

    if rule is not None:
        spec = rule.currency_spec
    else:
        spec = legacy_currency_spec("金币")
    amount = currency.get("amount", 0) if isinstance(currency, dict) else int(currency or 0)
    return format_currency_amount(int(amount), spec)


def get_resource(character_sheet: dict, key: str) -> dict | None:
    resources = character_sheet.get("resources", {})
    if isinstance(resources, dict) and key in resources:
        return resources[key]
    if key == "hp" and ("hp" in character_sheet or "max_hp" in character_sheet):
        return {
            "label": "生命",
            "current": int(character_sheet.get("hp", 0) or 0),
            "max": int(character_sheet.get("max_hp", character_sheet.get("hp", 0)) or 0),
        }
    if key in character_sheet:
        max_key = f"max_{key}"
        result = {"label": key, "current": int(character_sheet.get(key, 0) or 0)}
        if max_key in character_sheet:
            result["max"] = int(character_sheet.get(max_key, 0) or 0)
        return result
    return None


def apply_resource_delta(character_sheet: dict, key: str, delta: int, rule: RuleSystem | None = None) -> int:
    """Apply a delta to a generic resource and sync legacy fields."""
    normalize_character_sheet(character_sheet, rule)
    if key == "currency":
        currency = character_sheet.setdefault("currency", {"amount": int(character_sheet.get("gold", 0) or 0)})
        currency["amount"] = max(0, int(currency.get("amount", 0) or 0) + int(delta))
        character_sheet["gold"] = currency["amount"]
        return currency["amount"]

    resources = character_sheet.setdefault("resources", {})
    resource = resources.setdefault(key, {"label": key, "current": int(character_sheet.get(key, 0) or 0)})
    current = int(resource.get("current", 0) or 0) + int(delta)
    min_value = int(resource.get("min", 0) or 0)
    if "max" in resource:
        current = min(current, int(resource.get("max", current) or current))
    current = max(min_value, current)
    resource["current"] = current
    if key == "hp":
        character_sheet["hp"] = current
    else:
        character_sheet[key] = current
    return current


def set_hp(character_sheet: dict, hp: int | float, max_hp: int | float | None = None) -> int:
    """Set HP and keep legacy hp/max_hp in sync with resources.hp."""
    if max_hp is None:
        raw_max_hp = character_sheet.get("max_hp", hp)
        max_value = int(raw_max_hp if raw_max_hp is not None else hp)
    else:
        max_value = int(max_hp)
    max_value = max(0, max_value)
    current = max(0, min(max_value, int(hp)))
    character_sheet["hp"] = current
    character_sheet["max_hp"] = max_value
    resources = character_sheet.setdefault("resources", {})
    resource = resources.setdefault("hp", {})
    resource.setdefault("label", "生命")
    resource.setdefault("min", 0)
    resource["current"] = current
    resource["max"] = max_value
    return current


def armor_ac_components(character_sheet: dict) -> dict[str, Any]:
    """category_lite 护甲模型的 AC 组件：最高甲基础值、DEX 封顶、盾加值。

    无已知护甲时 base=0，调用方按 10 + 完整 DEX 处理（无甲）。
    """
    from src.engine.constants import ARMOR_LITE

    base = 0
    dex_cap: int | None = None
    category = ""
    shield = 0
    equipment = character_sheet.get("equipment")
    if not isinstance(equipment, list):
        return {"base": base, "dex_cap": dex_cap, "shield": shield, "category": category}
    for item in equipment:
        if not isinstance(item, dict):
            continue
        # Content V2 equipment carries its AC mechanics with the item.  Never
        # re-identify such an item through the legacy name/key table: locale
        # names are display data and the canonical fields are authoritative.
        item_type = str(item.get("type") or "").strip()
        if item_type == "shield" and "ac_bonus" in item:
            shield = max(shield, int(item.get("ac_bonus") or 0))
            continue
        if item_type == "armor" and "ac_base" in item:
            item_base = int(item.get("ac_base") or 0)
            if item_base > base:
                base = item_base
                dex_cap = item.get("dex_cap")
                category = str(item.get("armor_category") or "")
            continue

        # Old saves and V1 content only contain names/item keys, so retain the
        # historical lookup exclusively as a compatibility boundary.
        key = str(item.get("item_key") or item.get("name") or "").strip().casefold()
        spec = ARMOR_LITE.get(key)
        if not spec:
            continue
        if spec.get("category") == "shield":
            shield = max(shield, int(spec.get("ac_bonus", 2)))
            continue
        item_base = int(spec.get("ac_base", 0))
        if item_base > base:
            base = item_base
            dex_cap = spec.get("dex_cap")
            category = str(spec.get("category") or "")
    return {"base": base, "dex_cap": dex_cap, "shield": shield, "category": category}


def armor_value(character_sheet: dict) -> int:
    """Return trusted armor from explicit state or equipped items.

    Runtime NPC/enemy records commonly expose ``armor`` directly, while player
    sheets usually derive it from equipment.  Keep both attack DC and damage
    reduction on this single interpretation instead of duplicating the rule.
    """
    for key in ("armor", "_armor"):
        if key not in character_sheet or character_sheet.get(key) is None:
            continue
        try:
            return max(0, int(character_sheet[key]))
        except (TypeError, ValueError):
            break

    total = 0
    equipment = character_sheet.get("equipment")
    if not isinstance(equipment, list):
        return 0
    for item in equipment:
        if not isinstance(item, dict):
            continue
        raw = item.get("armor")
        if raw is None and item.get("type") in {"armor", "clothing"}:
            raw = 1
        try:
            total += int(raw or 0)
        except (TypeError, ValueError):
            continue
    return max(0, total)


def bounded_hp_delta(character_sheet: dict, hp_change: int | float) -> int:
    """按角色 max_hp 限制单次 HP 变更：伤害≤max_hp，治疗≤max_hp//2。"""
    raw_max_hp = character_sheet.get("max_hp", 100)
    max_hp = int(raw_max_hp if raw_max_hp is not None else 100)
    change = int(hp_change)
    if change < 0 and abs(change) > max_hp:
        return -max_hp
    if change > 0 and change > max_hp // 2:
        return max_hp // 2
    return change


def apply_hp_delta(character_sheet: dict, hp_change: int | float, *, bounded: bool = True) -> int:
    """应用 HP 变化并同步 resources.hp，返回变更后的 HP。"""
    raw_max_hp = character_sheet.get("max_hp", 100)
    max_hp = int(raw_max_hp if raw_max_hp is not None else 100)
    change = bounded_hp_delta(character_sheet, hp_change) if bounded else int(hp_change)
    current = max(0, min(max_hp, int(character_sheet.get("hp", 0) or 0) + change))
    return set_hp(character_sheet, current, max_hp)


def apply_currency_delta(character_sheet: dict, delta: int | float) -> int:
    """Apply canonical currency and mirror the legacy ``gold`` projection."""
    currency = character_sheet.setdefault("currency", {})
    raw_current = currency.get("amount", character_sheet.get("gold", 0))
    current = max(0, int(raw_current or 0) + int(delta))
    currency["amount"] = current
    character_sheet["gold"] = current
    return current


def apply_bounded_stat_delta(
    character_sheet: dict,
    key: str,
    delta: int | float,
    *,
    default_current: int = 0,
    max_key: str = "",
    default_max: int | None = None,
) -> int:
    """应用非 HP 的数值资源变化，按 0..max 上下限裁剪并同步 resources。"""
    raw_current = character_sheet.get(key, default_current)
    current = int(raw_current if raw_current is not None else default_current) + int(delta)
    if max_key:
        raw_max = character_sheet.get(max_key, default_max)
        if raw_max is not None:
            current = min(current, int(raw_max))
    elif default_max is not None:
        current = min(current, default_max)
    current = max(0, current)
    character_sheet[key] = current

    resources = character_sheet.setdefault("resources", {})
    resource = resources.setdefault(key, {"label": key, "min": 0})
    resource["current"] = current
    if max_key and max_key in character_sheet:
        resource["max"] = int(character_sheet.get(max_key, current) or current)
    elif default_max is not None:
        resource.setdefault("max", default_max)
    return current


def mark_character_dead(character_sheet: dict, round_number: int | None = None) -> bool:
    """标记角色死亡；本次调用新标记死亡时返回 True。"""
    if character_sheet.get("deceased"):
        return False
    character_sheet["deceased"] = True
    if round_number is not None:
        character_sheet["death_round"] = round_number
    return True


def sync_death_from_hp(character_sheet: dict, round_number: int | None = None, rule: Any | None = None) -> bool:
    """当 HP 归零时落死亡/昏迷状态；本次新死亡返回 True。

    规则声明 ``death_mechanic.hp_zero=downed_death_saves`` 时进入昏迷并
    初始化死亡豁免计数（D&D 5e 式）；否则保持旧版即死语义。
    """
    if int(character_sheet.get("hp", 0) or 0) > 0:
        return False
    if (
        rule is not None
        and rule.death_mechanic["hp_zero"] == "downed_death_saves"
        and not character_sheet.get("deceased")
    ):
        if str(character_sheet.get("status") or "") not in {"downed", "stable"}:
            enter_downed_state(character_sheet)
        return False
    return mark_character_dead(character_sheet, round_number)


def enter_downed_state(character_sheet: dict) -> None:
    """进入昏迷状态并重置死亡豁免计数。"""
    character_sheet["status"] = "downed"
    character_sheet["death_saves"] = {"success": 0, "failure": 0}


def wake_character(character_sheet: dict) -> bool:
    """HP>0 时清除昏迷/稳定状态与豁免计数；实际清除时返回 True。"""
    if int(character_sheet.get("hp", 0) or 0) <= 0:
        return False
    changed = False
    if str(character_sheet.get("status") or "") in {"downed", "stable"}:
        character_sheet.pop("status", None)
        changed = True
    if "death_saves" in character_sheet:
        character_sheet.pop("death_saves", None)
        changed = True
    return changed


def record_death_save_failures(
    character_sheet: dict, count: int, round_number: int | None = None
) -> str | None:
    """昏迷角色受击累加失败（5e：任何伤害 1 次、暴击 2 次）；满 3 次死亡返回 'dead'。"""
    if str(character_sheet.get("status") or "") not in {"downed", "stable"} or int(count) <= 0:
        return None
    # A stable character is still at 0 HP. Old saves may retain a stale
    # death_saves object, so renewed damage always starts a fresh tracker.
    if str(character_sheet.get("status") or "") == "stable":
        character_sheet["status"] = "downed"
        character_sheet["death_saves"] = {"success": 0, "failure": 0}
    saves = character_sheet.get("death_saves")
    if not isinstance(saves, dict):
        saves = {"success": 0, "failure": 0}
        character_sheet["death_saves"] = saves
    saves["failure"] = int(saves.get("failure", 0) or 0) + int(count)
    if int(saves["failure"]) >= 3:
        character_sheet.pop("status", None)
        character_sheet.pop("death_saves", None)
        mark_character_dead(character_sheet, round_number)
        return "dead"
    return "recorded"


def is_conscious(character_sheet: dict) -> bool:
    """角色是否能行动：未死亡且未昏迷（稳定者仍昏迷，不能行动）。"""
    return not character_sheet.get("deceased") and str(
        character_sheet.get("status") or ""
    ) not in {"downed", "stable"}


def apply_death_save(character_sheet: dict, roll_value: int, round_number: int | None = None) -> str:
    """应用一次死亡豁免并返回事件：wake/dead/stable/success/failure/failure_double。"""
    saves = character_sheet.get("death_saves")
    if not isinstance(saves, dict):
        saves = {"success": 0, "failure": 0}
        character_sheet["death_saves"] = saves
    value = int(roll_value)
    if value >= 20:
        set_hp(character_sheet, 1, character_sheet.get("max_hp"))
        character_sheet.pop("status", None)
        character_sheet.pop("death_saves", None)
        return "wake"
    if value <= 1:
        saves["failure"] = int(saves.get("failure", 0) or 0) + 2
        event = "failure_double"
    elif value >= 10:
        saves["success"] = int(saves.get("success", 0) or 0) + 1
        event = "success"
    else:
        saves["failure"] = int(saves.get("failure", 0) or 0) + 1
        event = "failure"
    if int(saves.get("failure", 0) or 0) >= 3:
        character_sheet.pop("status", None)
        character_sheet.pop("death_saves", None)
        mark_character_dead(character_sheet, round_number)
        return "dead"
    if int(saves.get("success", 0) or 0) >= 3:
        character_sheet["status"] = "stable"
        # Stable is a terminal state for automatic saves; do not leave three
        # active successes that can be mistaken for an in-progress tracker.
        character_sheet.pop("death_saves", None)
        return "stable"
    return event


def revive_character(character_sheet: dict, method: str = "法术") -> bool:
    """按复活方式恢复角色；成功复活返回 True。"""
    if not character_sheet.get("deceased"):
        return False
    raw_max_hp = character_sheet.get("max_hp", 100)
    max_hp = int(raw_max_hp if raw_max_hp is not None else 100)
    if method == "法术":
        set_hp(character_sheet, max(1, max_hp // 2), max_hp)
    elif method == "NPC":
        set_hp(character_sheet, max(1, int(max_hp * 0.3)), max_hp)
    elif method == "自然":
        set_hp(character_sheet, max(1, int(max_hp * 0.1)), max_hp)
        xp = int(character_sheet.get("xp", 0) or 0)
        character_sheet["xp"] = max(0, xp - int(xp * 0.2))
    else:
        set_hp(character_sheet, max(1, int(max_hp * 0.1)), max_hp)
    character_sheet["deceased"] = False
    character_sheet.pop("death_round", None)
    normalize_character_sheet(character_sheet)
    return True


def reset_character_for_restart(character_sheet: dict) -> dict:
    """重开世界时恢复角色的可复用运行状态。"""
    canonical = character_sheet.get("ruleset_character")
    canonical_resources = canonical.get("resources") if isinstance(canonical, dict) else None
    canonical_hp = canonical_resources.get("hp") if isinstance(canonical_resources, dict) else None
    canonical_max_hp = (
        canonical_resources.get("max_hp")
        if isinstance(canonical_resources, dict) and canonical_resources.get("max_hp") is not None
        else canonical_hp.get("max") if isinstance(canonical_hp, dict) else None
    )
    raw_max_hp = canonical_max_hp if canonical_max_hp is not None else character_sheet.get("max_hp", 100)
    max_hp = int(raw_max_hp if raw_max_hp is not None else 100)
    raw_gold = character_sheet.get("gold", 30)

    if isinstance(canonical, dict):
        resources = canonical.setdefault("resources", {})
        if isinstance(resources, dict):
            canonical_hp = resources.get("hp")
            if isinstance(canonical_hp, dict):
                canonical_hp["current"] = max_hp
                canonical_hp.setdefault("max", max_hp)
            else:
                resources["hp"] = max_hp
        # A restart begins a fresh run. Death/downed/stable and combat-only
        # conditions must not leak from any canonical character state.
        canonical["conditions"] = {}
        canonical["deceased"] = False

    set_hp(character_sheet, max_hp, max_hp)
    character_sheet["gold"] = int(raw_gold if raw_gold is not None else 30)
    character_sheet["deceased"] = False
    character_sheet.pop("status", None)
    character_sheet.pop("death_round", None)
    character_sheet.pop("death_saves", None)
    normalize_character_sheet(character_sheet)
    return character_sheet


def normalize_character_sheet(character_sheet: dict, rule: RuleSystem | None = None) -> dict:
    """Ensure new generic fields exist while keeping legacy fields in sync."""
    return normalize_legacy_character_sheet(character_sheet, rule)


def roll_attributes(
    keys: list[str] | None = None,
    total: int = 60,
    lo: int = 6,
    hi: int = 16,
    specs: list[dict] | None = None,
) -> dict[str, int]:
    """随机生成一组属性值，总和尽可能接近 total，并尊重每个属性的上下限。"""
    if specs:
        keys = [str(a.get("key")) for a in specs if a.get("key")]
        lows = [int(a.get("min", lo)) for a in specs if a.get("key")]
        highs = [int(a.get("max", hi)) for a in specs if a.get("key")]
    else:
        if not keys:
            keys = ["str", "dex", "con", "int", "wis", "cha"]
        lows = [lo for _ in keys]
        highs = [hi for _ in keys]
    n = len(keys)
    min_total = sum(lows)
    max_total = sum(highs)
    target = max(min_total, min(total, max_total))

    for _attempt in range(_MAX_ROLL_ATTEMPTS):
        values = [random.randint(lows[i], highs[i]) for i in range(n)]
        diff = target - sum(values)
        if diff == 0:
            return dict(zip(keys, values))
        for _ in range(max(10, n * 40)):
            idx = random.randrange(n)
            step = 1 if diff > 0 else -1
            new_val = values[idx] + step
            if lows[idx] <= new_val <= highs[idx]:
                values[idx] = new_val
                diff -= step
                if diff == 0:
                    return dict(zip(keys, values))

    raise RuntimeError(
        f"无法在 {_MAX_ROLL_ATTEMPTS} 次尝试内生成有效属性"
        f"(keys={keys}, total={target}, min_total={min_total}, max_total={max_total})"
    )

def find_item_category(item_categories: dict, name: str) -> str:
    for category, names in (item_categories or {}).items():
        if name in names:
            return category
    return "misc"


def build_starter_items(rule, class_name: str) -> tuple[list[dict], list[dict]]:
    """从规则职业的 starter_equipment 构造初始装备/背包。

    字符串道具名查 item_categories + WEAPON_DAMAGE 判武器；对象 {name, slot} 指定部位。
    """
    if not rule or not getattr(rule, "classes", None):
        return [], []
    cls = next((c for c in rule.classes if c.get("name") == class_name or c.get("id") == class_name), rule.classes[0])
    use_canonical = bool(getattr(rule, "template", {}).get("active_locale")) if hasattr(rule, "template") else False
    starter = (cls.get("starter_equipment_ids") if use_canonical else None) or cls.get("starter_equipment", [])
    # Historical templates and third-party rules may use a single item object.
    # Normalize at this boundary so the normal item construction path handles it.
    if isinstance(starter, dict):
        starter = [starter]
    elif not isinstance(starter, list):
        starter = []
    if not starter:
        return [], []
    from src.engine.constants import (
        ARMOR_LITE,
        WEAPON_DAMAGE,
        WEAPON_DAMAGE_DICE,
        canonical_item_key,
    )
    equip: list[dict] = []
    inv: list[dict] = []
    category_lite = getattr(rule, "armor_model", "sum") == "category_lite"
    item_defs = getattr(rule, "template", {}).get("items", {}) if hasattr(rule, "template") else {}
    if not isinstance(item_defs, dict):
        item_defs = {}
    equipped_weapons = 0

    def resolve_item(iname: str) -> tuple[str, dict | None]:
        display_key = str(iname).strip().casefold()
        item_key = canonical_item_key(iname)
        if display_key in item_defs:
            item_key = display_key
        item_def = item_defs.get(item_key) if item_key else None
        if not isinstance(item_def, dict):
            item_def = None
        if item_def is None:
            # V2 rules normally use canonical IDs; this keeps a localized
            # legacy starter name usable when its item definition has a name.
            for candidate_key, candidate in item_defs.items():
                if (
                    isinstance(candidate, dict)
                    and str(candidate.get("name") or "").strip().casefold() == display_key
                ):
                    item_key = str(candidate_key)
                    item_def = candidate
                    break
        return item_key, item_def

    def display_name_for(iname: str) -> str:
        _, item_def = resolve_item(iname)
        return str((item_def or {}).get("name") or iname)

    def declared_item_type(iname: str) -> str:
        _, item_def = resolve_item(iname)
        return str((item_def or {}).get("type") or "").strip().casefold()

    def make_inventory_item(iname: str) -> dict:
        item_key, item_def = resolve_item(iname)
        result = {
            "name": str((item_def or {}).get("name") or iname),
            "qty": max(1, int((item_def or {}).get("quantity", 1) or 1)),
            "effect": str((item_def or {}).get("effect") or ""),
        }
        if item_key and item_def is not None:
            result["item_key"] = item_key
            if item_def.get("type"):
                result["type"] = str(item_def["type"])
        return result

    def make_item(iname: str, slot: str = "") -> dict | None:
        display_key = str(iname).strip().casefold()
        item_key, item_def = resolve_item(iname)
        display_name = str((item_def or {}).get("name") or iname)
        if isinstance(item_def, dict) and item_def.get("type"):
            item_type = str(item_def["type"])
            if item_type == "focus":
                return {"name": display_name, "type": "focus", "slot": slot, "quality": "common", "item_key": item_key}
            if item_type in {"shield", "armor"}:
                canonical_armor = {
                    "name": display_name, "type": item_type, "slot": slot or ("off_hand" if item_type == "shield" else "armor"),
                    "quality": "common", **({"item_key": item_key} if item_key else {}),
                }
                if item_type == "shield":
                    canonical_armor["ac_bonus"] = int(item_def.get("ac_bonus", 0) or 0)
                else:
                    canonical_armor.update({
                        "armor_category": str(item_def.get("armor_category") or ""),
                        "ac_base": int(item_def.get("ac_base", 0) or 0),
                    })
                    if "armor" in item_def:
                        canonical_armor["armor"] = int(item_def.get("armor", 0) or 0)
                    if "dex_cap" in item_def:
                        canonical_armor["dex_cap"] = item_def["dex_cap"]
                return canonical_armor
            if item_type == "weapon":
                return {
                    "name": display_name, "type": "weapon", "damage": int(item_def.get("damage", 0) or 0),
                    "slot": slot or ("main_hand" if equipped_weapons == 0 else "off_hand"), "quality": "common",
                    **({"item_key": item_key} if item_key else {}), **({"damage_dice": item_def["damage_dice"]} if item_def.get("damage_dice") else {}),
                }
        key = item_key or display_key
        if category_lite and key == "arcane_focus":
            return {
                "name": display_name,
                "type": "focus",
                "slot": slot,
                "quality": "common",
                "item_key": item_key,
            }
        armor = ARMOR_LITE.get(key)
        if category_lite and armor:
            if armor.get("category") == "shield":
                return {
                    "name": display_name, "type": "shield", "slot": slot or "off_hand", "quality": "common",
                    **({"item_key": item_key} if item_key else {}),
                }
            return {
                "name": display_name,
                "type": "armor",
                "slot": slot or "armor",
                "armor": int(armor.get("ac_base", 0)),
                "quality": "common",
                **({"item_key": item_key} if item_key else {}),
            }
        damage = WEAPON_DAMAGE.get(key, WEAPON_DAMAGE.get(display_key, 0))
        damage_dice = WEAPON_DAMAGE_DICE.get(key, WEAPON_DAMAGE_DICE.get(display_key, ""))
        if damage_dice or damage:
            return {
                "name": display_name,
                "type": "weapon",
                "damage": int(damage or 0),
                "slot": slot or ("main_hand" if equipped_weapons == 0 else "off_hand"),
                "quality": "common",
                **({"item_key": item_key} if item_key else {}),
                **({"damage_dice": damage_dice} if damage_dice else {}),
            }
        return None

    for st_item in starter:
        if isinstance(st_item, dict):
            iname = st_item.get("name", "")
            islot = st_item.get("slot", "")
            item = make_item(str(iname), str(islot))
            if item and (islot or item.get("type") == "focus"):
                equip.append(item)
                equipped_weapons += int(item.get("type") == "weapon")
            else:
                inv.append(make_inventory_item(str(iname)))
        else:
            iname = str(st_item)
            cat = find_item_category(rule.item_categories, iname)
            item = make_item(iname)
            known_rule_item = item is not None and (
                category_lite
                or declared_item_type(iname) in {"weapon", "armor", "shield", "focus"}
            )
            legacy_weapon = (
                item is not None
                and item.get("type") == "weapon"
                and cat == "equipment"
                and equipped_weapons == 0
            )
            if known_rule_item or legacy_weapon:
                assert item is not None
                equip.append(item)
                equipped_weapons += int(item.get("type") == "weapon")
            else:
                inv.append(make_inventory_item(iname))
    return equip, inv


def make_default_character(
    name: str,
    rule_id: str = "freeform_fantasy",
    templates_base: Path | None = None,
    language: str = "",
) -> dict:
    """生成默认角色卡，自动从规则模板读取属性/HP 配置。

    Args:
        name: 角色名称
        rule_id: 规则 ID（对应 templates/rules/{rule_id}.json）
        templates_base: templates 目录路径（用于定位 rules/ 子目录）
    """
    keys = ["str", "dex", "con", "int", "wis", "cha"]
    pts = 60
    lo = 6
    hi = 16
    hp = 35
    default_class_name = "冒险者"
    default_equipment: list[dict] = [{"name": "铁剑", "type": "weapon", "damage": 6, "slot": "main_hand", "quality": "common"}]
    default_inventory: list[dict] = [{"name": "医疗包", "qty": 2, "effect": "回复20HP"}]
    default_skills = ["基础攻击"]
    gold = 30

    rules_dir = (
        templates_base / "rules"
        if templates_base
        else Path(__file__).parent.parent.parent / "templates" / "rules"
    )
    rule_path = RuleSystem.path_for(rules_dir, rule_id, language)
    core_path = rules_dir / f"{rule_id}.json"
    rule: RuleSystem | None = None

    skill_base_values: dict[str, int] = {}
    try:
        if core_path.exists():
            rule = RuleSystem(RuleBundleLoader().load_rule(rules_dir, rule_id, language))
        elif rule_path.exists():
            rule = RuleSystem.load(rule_path)
        if rule is not None:
            keys = rule.attribute_keys
            pts = rule.attribute_points
            lo = min(a.get("min", 3) for a in rule.attributes) if rule.attributes else 3
            hi = max(a.get("max", 18) for a in rule.attributes) if rule.attributes else 16
            skill_base_values = rule.skill_base_values
            if rule.classes:
                first_class = rule.classes[0]
                default_class_name = first_class["name"]
                default_skills_raw = rule.get_skill_pool(default_class_name) or ["基础攻击"]
                default_skills = [
                    {"name": sn, "value": skill_base_values.get(sn, 20)}
                    for sn in default_skills_raw
                ]
                se, si = build_starter_items(rule, default_class_name)
                if se:
                    default_equipment = se
                if si:
                    default_inventory = si
    except Exception:
        logger.exception("读取默认角色规则失败: %s", rule_path)

    attrs = roll_attributes(keys, pts, lo, hi, rule.attributes if rule else None)

    try:
        if rule:
            hp = rule.calculate_hp(attrs, default_class_name)
    except Exception:
        logger.exception("计算默认角色 HP 失败: %s", rule_path)

    if rule_id == "freeform_coc":
        gold = 0

    cs = {
        "race": "人类", "class": default_class_name, "level": 1, "xp": 0,
        "attributes": attrs,
        "hp": hp, "max_hp": hp,
        "equipment": default_equipment,
        "inventory": default_inventory,
        "key_items": [],
        "skills": default_skills, "background": "", "deceased": False,
        "gold": gold,
        "attr_points_max": pts,
    }

    try:
        if rule:
            for ss in rule.special_stats:
                max_val = ss.get("max", 99)
                init_val = initial_special_stat_value(ss, attrs)
                cs[ss["key"]] = init_val
                cs[f"max_{ss['key']}"] = max_val
    except Exception:
        logger.exception("初始化默认角色特殊属性失败: %s", rule_path)

    return normalize_character_sheet(cs, rule)


def calc_hp_from_rule(attrs: dict[str, int], rule_id: str = "freeform_fantasy",
                       rules_dir: Path | None = None, class_name: str = "",
                       language: str = "") -> int:
    """根据规则计算 HP。class_name 用于 dnd5e 等需 class_hp_die 的公式。"""
    if rules_dir is None:
        rules_dir = Path(__file__).parent.parent.parent / "templates" / "rules"
    try:
        core_path = rules_dir / f"{rule_id}.json"
        rule_path = RuleSystem.path_for(rules_dir, rule_id, language)
        if core_path.exists():
            rule = RuleSystem(RuleBundleLoader().load_rule(rules_dir, rule_id, language))
            return rule.calculate_hp(attrs, class_name)
        if rule_path.exists():
            rule = RuleSystem.load(rule_path)
            return rule.calculate_hp(attrs, class_name)
    except Exception:
        logger.exception("按规则计算 HP 失败: %s", rule_id)
    return 35


def get_rule_attr_config(rule_id: str = "freeform_fantasy",
                          rules_dir: Path | None = None,
                          language: str = "") -> tuple[list[str], int, int, int]:
    """读取规则属性配置，返回 (keys, total_points, min_val, max_val)。"""
    if rules_dir is None:
        rules_dir = Path(__file__).parent.parent.parent / "templates" / "rules"
    try:
        core_path = rules_dir / f"{rule_id}.json"
        rule_path = RuleSystem.path_for(rules_dir, rule_id, language)
        if core_path.exists():
            rule = RuleSystem(RuleBundleLoader().load_rule(rules_dir, rule_id, language))
            keys = rule.attribute_keys
            pts = rule.attribute_points
            mins = [a.get("min", 3) for a in rule.attributes] if rule.attributes else [3]
            maxs = [a.get("max", 18) for a in rule.attributes] if rule.attributes else [18]
            return keys, pts, min(mins), min(maxs, 16)
        if rule_path.exists():
            rule = RuleSystem.load(rule_path)
            keys = rule.attribute_keys
            pts = rule.attribute_points
            mins = [a.get("min", 3) for a in rule.attributes] if rule.attributes else [3]
            maxs = [a.get("max", 18) for a in rule.attributes] if rule.attributes else [18]
            return keys, pts, min(mins), min(maxs, 16)
    except Exception:
        logger.exception("读取规则属性配置失败: %s", rule_id)
    return ["str", "dex", "con", "int", "wis", "cha"], 60, 6, 16


def parse_tavern_card(file_path: str | Path) -> dict:
    """解析酒馆(SillyTavern)角色卡文件，支持 PNG 内嵌 JSON 和纯 JSON 两种格式。

    返回格式:
        {name, description, personality, scenario, first_mes,
         tags[], character_book[], creator}
    解析失败返回 {"error": "原因"}。
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": "文件不存在"}

    raw_data = path.read_bytes()

    # 先尝试 PNG 内嵌格式
    if raw_data[:8] == b'\x89PNG\r\n\x1a\n':
        result = _parse_tavern_png(raw_data)
        if result:
            return result
        return {"error": "PNG 中未找到角色卡数据"}

    # 纯 JSON 格式
    try:
        raw_text = raw_data.decode("utf-8")
    except UnicodeDecodeError:
        raw_text = raw_data.decode("utf-8-sig")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {"error": f"JSON 解析失败: {exc}"}

    return _extract_tavern_fields(data)


def _parse_tavern_png(raw_data: bytes) -> dict | None:
    """从 PNG 文件的 tEXt chunk 中提取角色卡 JSON。"""
    try:
        # 跳过 8 字节 PNG 签名
        pos = 8
        max_chunks = 2000
        max_chunk_size = 50_000_000
        chunk_count = 0
        while pos + 8 <= len(raw_data) and chunk_count < max_chunks:
            length = struct.unpack(">I", raw_data[pos:pos + 4])[0]
            if length > max_chunk_size:
                break
            pos += 4
            chunk_type = raw_data[pos:pos + 4].decode("ascii", errors="ignore")
            pos += 4
            chunk_data = raw_data[pos:pos + length]
            pos += length + 4  # 数据 + CRC
            chunk_count += 1

            if chunk_type == "tEXt":
                null_pos = chunk_data.find(b'\x00')
                if null_pos == -1:
                    continue
                keyword = chunk_data[:null_pos].decode("latin-1")
                if keyword.lower() == "chara":
                    text = chunk_data[null_pos + 1:].decode("utf-8", errors="replace")
                    data = json.loads(text)
                    return _extract_tavern_fields(data)
            elif chunk_type == "IEND":
                break
    except (IndexError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        logger.debug("酒馆 PNG 角色元数据解析失败", exc_info=True)
    return None


def _extract_tavern_fields(data: dict) -> dict:
    """从酒馆角色卡 JSON 中提取 TRPG NPC 可用字段。"""
    if "data" in data:
        inner = data["data"]
    else:
        inner = data

    result: dict = {
        "name": str(inner.get("name", "") or "").strip() or "未命名",
        "description": str(inner.get("description", "") or "").strip(),
        "personality": str(inner.get("personality", "") or "").strip(),
        "scenario": str(inner.get("scenario", "") or "").strip(),
        "first_mes": str(inner.get("first_mes", "") or "").strip(),
        "system_prompt": str(inner.get("system_prompt", "") or "").strip(),
        "post_history_instructions": str(inner.get("post_history_instructions", "") or "").strip(),
        "tags": [],
        "character_book": [],
        "creator": str(inner.get("creator", "") or "").strip(),
    }

    tags = inner.get("tags")
    if isinstance(tags, list):
        result["tags"] = [str(t).strip() for t in tags if str(t).strip()]

    book = inner.get("character_book")
    if isinstance(book, dict):
        entries = book.get("entries", [])
        if isinstance(entries, list):
            result["character_book"] = entries

    return result
