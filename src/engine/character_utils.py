"""角色工具 —— 属性随机生成、默认角色卡、酒馆角色卡导入等跨模块共享逻辑。"""

from __future__ import annotations

import json
import logging
import random
import struct
from pathlib import Path

from src.rules.rule_system import RuleSystem

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
    """Format generic currency data using a rule currency_system."""
    system = rule.currency_system if rule else {
        "base_unit": "unit",
        "units": [{"id": "unit", "name": "金币", "rate": 1}],
    }
    amount = currency.get("amount", 0) if isinstance(currency, dict) else int(currency or 0)
    units = sorted(system.get("units", []), key=lambda u: int(u.get("rate", 1) or 1), reverse=True)
    if not units:
        return str(amount)
    parts: list[str] = []
    remaining = int(amount)
    for unit in units:
        rate = max(1, int(unit.get("rate", 1) or 1))
        count, remaining = divmod(remaining, rate)
        if count:
            parts.append(f"{count} {unit.get('name', unit.get('id', ''))}".strip())
    if not parts:
        parts.append(f"0 {units[-1].get('name', units[-1].get('id', ''))}".strip())
    return " ".join(parts)


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
    """应用金币/通用货币变化并保持 legacy gold 与 currency.amount 同步。"""
    current = max(0, int(character_sheet.get("gold", 0) or 0) + int(delta))
    character_sheet["gold"] = current
    currency = character_sheet.setdefault("currency", {})
    currency["amount"] = current
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


def sync_death_from_hp(character_sheet: dict, round_number: int | None = None) -> bool:
    """当 HP 归零时统一落死亡状态；本次新死亡返回 True。"""
    if int(character_sheet.get("hp", 0) or 0) > 0:
        return False
    return mark_character_dead(character_sheet, round_number)


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
    raw_max_hp = character_sheet.get("max_hp", 100)
    max_hp = int(raw_max_hp if raw_max_hp is not None else 100)
    raw_gold = character_sheet.get("gold", 30)
    set_hp(character_sheet, max_hp, max_hp)
    character_sheet["gold"] = int(raw_gold if raw_gold is not None else 30)
    character_sheet["deceased"] = False
    character_sheet.pop("status", None)
    character_sheet.pop("death_round", None)
    normalize_character_sheet(character_sheet)
    return character_sheet


def migrate_legacy_character_sheet(character_sheet: dict, rule: RuleSystem | None = None) -> dict:
    """Populate generic identity/resources/progression/currency from legacy fields."""
    identity = character_sheet.setdefault("identity", {})
    identity.setdefault("origin", character_sheet.get("race", ""))
    identity.setdefault("archetype", character_sheet.get("class", ""))
    identity.setdefault("background", character_sheet.get("background", ""))

    progression = character_sheet.setdefault("progression", {})
    progression.setdefault("type", rule.progression_schema.get("type", rule.growth_system) if rule else "xp_level")
    progression.setdefault("level", int(character_sheet.get("level", 1) or 1))
    progression.setdefault("xp", int(character_sheet.get("xp", 0) or 0))

    currency = character_sheet.setdefault("currency", {})
    currency.setdefault("amount", int(character_sheet.get("gold", 0) or 0))
    if rule:
        currency.setdefault("base_unit", rule.currency_system.get("base_unit", "unit"))
        currency.setdefault("label", rule.ui_schema.get("currency_label", rule.currency))

    resources = character_sheet.setdefault("resources", {})
    hp = resources.setdefault("hp", {})
    hp.setdefault("label", "生命")
    hp.setdefault("current", int(character_sheet.get("hp", 0) or 0))
    hp.setdefault("max", int(character_sheet.get("max_hp", hp.get("current", 0)) or 0))
    hp.setdefault("min", 0)
    if rule:
        for spec in rule.resource_schema:
            key = spec.get("key")
            if not key or key == "hp":
                continue
            res = resources.setdefault(key, {})
            res.setdefault("label", spec.get("label", key))
            if key in character_sheet:
                res.setdefault("current", int(character_sheet.get(key, 0) or 0))
            if f"max_{key}" in character_sheet:
                res.setdefault("max", int(character_sheet.get(f"max_{key}", 0) or 0))
            elif "max" in spec:
                res.setdefault("max", spec.get("max"))
            res.setdefault("min", spec.get("min", 0))
    return character_sheet


def normalize_character_sheet(character_sheet: dict, rule: RuleSystem | None = None) -> dict:
    """Ensure new generic fields exist while keeping legacy fields in sync."""
    migrate_legacy_character_sheet(character_sheet, rule)
    identity = character_sheet.setdefault("identity", {})
    if character_sheet.get("race"):
        identity["origin"] = character_sheet.get("race", "")
    else:
        character_sheet["race"] = identity.get("origin", "人类") or "人类"
    if character_sheet.get("class"):
        identity["archetype"] = character_sheet.get("class", "")
    else:
        character_sheet["class"] = identity.get("archetype", "冒险者") or "冒险者"
    if "background" in character_sheet:
        identity["background"] = character_sheet.get("background", "")
    else:
        character_sheet["background"] = identity.get("background", "")

    progression = character_sheet.setdefault("progression", {})
    if "level" in character_sheet:
        progression["level"] = int(character_sheet.get("level", 1) or 1)
    else:
        character_sheet["level"] = int(progression.get("level", 1) or 1)
    if "xp" in character_sheet:
        progression["xp"] = int(character_sheet.get("xp", 0) or 0)
    else:
        character_sheet["xp"] = int(progression.get("xp", 0) or 0)

    resources = character_sheet.setdefault("resources", {})
    hp = resources.setdefault("hp", {})
    if "hp" in character_sheet:
        hp["current"] = int(character_sheet.get("hp", 0) or 0)
    else:
        character_sheet["hp"] = int(hp.get("current", 0) or 0)
    if "max_hp" in character_sheet:
        hp["max"] = int(character_sheet.get("max_hp", 0) or 0)
    else:
        character_sheet["max_hp"] = int(hp.get("max", character_sheet.get("hp", 0)) or 0)

    currency = character_sheet.setdefault("currency", {})
    if "gold" in character_sheet:
        currency["amount"] = int(character_sheet.get("gold", 0) or 0)
    else:
        character_sheet["gold"] = int(currency.get("amount", 0) or 0)
    character_sheet["skills"] = format_skills(character_sheet.get("skills", []))
    return character_sheet


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

def make_default_character(
    name: str,
    rule_id: str = "freeform_fantasy",
    templates_base: Path | None = None,
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
    default_weapon = {"name": "铁剑", "type": "weapon", "damage": 6, "slot": "main_hand", "quality": "common"}
    default_skills = ["基础攻击"]
    gold = 30

    rules_dir = (
        templates_base / "rules"
        if templates_base
        else Path(__file__).parent.parent.parent / "templates" / "rules"
    )
    rule_path = RuleSystem.path_for(rules_dir, rule_id)
    rule: RuleSystem | None = None

    skill_base_values: dict[str, int] = {}
    try:
        if rule_path.exists():
            rule = RuleSystem.load(rule_path)
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
                starter_equip = first_class.get("starter_equipment", [])
                if starter_equip:
                    from src.engine.constants import WEAPON_DAMAGE
                    equip_name = starter_equip[0]
                    dmg = WEAPON_DAMAGE.get(equip_name, 6)
                    default_weapon = {"name": equip_name, "type": "weapon", "damage": dmg, "slot": "main_hand", "quality": "common"}
    except Exception:
        logger.exception("读取默认角色规则失败: %s", rule_path)

    attrs = roll_attributes(keys, pts, lo, hi, rule.attributes if rule else None)

    try:
        if rule:
            hp = rule.calculate_hp(attrs, default_class_name)
    except Exception:
        logger.exception("计算默认角色 HP 失败: %s", rule_path)

    if rule_id == "freeform_coc":
        default_weapon = {"name": "手电筒", "type": "weapon", "damage": 1, "slot": "off_hand", "quality": "common"}
        gold = 0

    cs = {
        "race": "人类", "class": default_class_name, "level": 1, "xp": 0,
        "attributes": attrs,
        "hp": hp, "max_hp": hp,
        "equipment": [default_weapon],
        "inventory": [{"name": "医疗包", "qty": 2, "effect": "回复20HP"}],
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
                       rules_dir: Path | None = None, class_name: str = "") -> int:
    """根据规则计算 HP。class_name 用于 dnd5e 等需 class_hp_die 的公式。"""
    if rules_dir is None:
        rules_dir = Path(__file__).parent.parent.parent / "templates" / "rules"
    try:
        rule_path = RuleSystem.path_for(rules_dir, rule_id)
        if rule_path.exists():
            rule = RuleSystem.load(rule_path)
            return rule.calculate_hp(attrs, class_name)
    except Exception:
        logger.exception("按规则计算 HP 失败: %s", rule_id)
    return 35


def get_rule_attr_config(rule_id: str = "freeform_fantasy",
                          rules_dir: Path | None = None) -> tuple[list[str], int, int, int]:
    """读取规则属性配置，返回 (keys, total_points, min_val, max_val)。"""
    if rules_dir is None:
        rules_dir = Path(__file__).parent.parent.parent / "templates" / "rules"
    try:
        rule_path = RuleSystem.path_for(rules_dir, rule_id)
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
    except Exception:
        pass
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
