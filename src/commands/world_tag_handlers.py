"""世界、战利品与行动标签处理。"""

from __future__ import annotations

import logging

from src.commands.state_items import split_item_quantity

logger = logging.getLogger("trpg")

_PERSON_SUFFIXES = (
    "年轻人", "中年人", "老年人", "老人", "小孩", "孩子", "少年", "少女", "青年",
    "男子", "女子", "男人", "女人", "老头", "老太", "小伙子", "姑娘", "婴儿",
    "先生", "小姐", "女士", "太太", "夫人", "少爷", "大人", "同志",
    "博士", "教授", "医生", "护士", "律师", "侦探", "警官", "警察", "探员", "特工",
    "士兵", "军官", "记者", "学者", "作家", "画家", "诗人", "师傅", "师父", "老板",
    "掌柜", "管家", "神父", "牧师", "修女", "僧人", "道士", "和尚", "渔夫", "农夫",
    "铁匠", "商人", "仆人", "佣人", "侍女", "侍从", "证人", "嫌疑人", "嫌犯", "罪犯",
    "凶手", "受害者", "死者", "当事人", "目击者", "知情者", "参与者", "幸存者",
    "失踪者", "门徒", "弟子", "信徒", "追随者",
)


def _looks_like_person(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _PERSON_SUFFIXES)


def parse_world_tag(tag: str, value: str, result: dict) -> None:
    if tag == "CONFIRMED":
        result.setdefault("confirmed", []).append(value)
    elif tag == "MEMORY":
        if value.strip():
            result["memory_delta"]["add"].append(value)
        else:
            logger.warning("MEMORY tag empty, skipped")
    elif tag == "SCENE":
        result["state_update"]["scene_change"] = value[:200]
    elif tag == "SCENE_IMAGE":
        result["scene_image_prompt"] = value[:300]
    elif tag == "NPC":
        parts = value.split(":", 1)
        if len(parts) == 2:
            name, relation = parts[0].strip()[:80], parts[1].strip()[:40]
            result["state_update"]["npcs"][name] = {"name": name, "tier": relation}
    elif tag == "DECISION":
        result["plot_update"]["decisions"].append(value[:300])
    elif tag == "QUEST":
        parts = value.rsplit(":", 1)
        if len(parts) == 2:
            result["plot_update"]["quests"].append({
                "title": parts[0].strip(),
                "status": parts[1].strip(),
            })
    elif tag == "PRIVATE":
        parts = value.split(":", 1)
        if len(parts) == 2:
            result["info_asymmetry"][parts[0].strip()] = parts[1].strip()


def parse_loot_tag(tag: str, value: str, result: dict) -> None:
    if tag == "LOOT":
        parts = value.split(":", 1)
        if parts:
            uid = parts[0].strip()
            item = parts[1].strip() if len(parts) > 1 else ""
            name, quantity = split_item_quantity(item)
            entry: dict = {"player": uid, "item": name[:120], "qty": quantity}
            result["state_update"]["loot"].append(entry)
    elif tag == "KEY_ITEM":
        parts = value.split(":", 1)
        if len(parts) == 2:
            uid, item = parts[0].strip(), parts[1].strip()
            # 关键物品无数量语义，但同样要剥掉"xN"后缀，避免脏名字入库。
            item, _qty = split_item_quantity(item)
            if _looks_like_person(item):
                logger.warning("KEY_ITEM 疑似人物而非物品（已照常写入，请检查 GM prompt）: %s", item)
            result["state_update"]["loot"].append({
                "player": uid,
                "item": item[:120],
                "category": "key_item",
            })


def parse_action_tag(tag: str, value: str, result: dict) -> None:
    if tag == "PUZZLE":
        parts = value.split(":", 1)
        if len(parts) == 2:
            result.setdefault("puzzle_updates", {})[parts[0].strip()] = parts[1].strip()
    elif tag == "SPELL":
        parts = value.split(":", 1)
        if len(parts) == 2:
            uid, spell_name = parts[0].strip(), parts[1].strip()
            result["state_update"]["players"].setdefault(uid, {})["cast_spell"] = spell_name
            logger.info("施法: %s 使用了 %s", uid, spell_name)
    elif tag == "QUICK_ACTIONS":
        result["quick_actions"] = [action.strip() for action in value.split("|") if action.strip()][:4]
    elif tag == "COMBAT":
        result["combat_command"] = value.strip()
