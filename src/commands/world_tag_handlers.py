"""世界、战利品与行动标签处理。"""

from __future__ import annotations

import logging
import re

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

# `SCENE_PANEL` is specified as one protocol line per panel.  Some model
# providers nevertheless compact several lines into one value (or repeat the
# tag token inline); keep the parser tolerant at this compatibility boundary.
_SCENE_PANEL_TOKEN_RE = re.compile(r"(?i)(?<![A-Za-z0-9_])SCENE_PANEL\s*[:：]")


def _looks_like_person(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _PERSON_SUFFIXES)


def _scene_panel_triplets(value: str) -> list[tuple[str, str, str]]:
    """Extract one or more ``participants|location|description`` records.

    The public protocol uses one ``SCENE_PANEL`` per line.  For compatibility
    with model output that loses newlines, accept a compact stream of complete
    triplets as well as repeated inline ``SCENE_PANEL:`` tokens.  A single
    description is allowed to contain ``|``; compact multi-panel parsing is
    only enabled when the number of fields is an exact multiple of three.
    """
    source = str(value or "").strip()
    if not source:
        return []

    # A provider may emit ``a|loc|desc SCENE_PANEL:b|loc|desc`` on one line.
    # Split only on an explicit protocol token; ordinary text remains part of
    # the description and is handled by the single-record fallback below.
    if _SCENE_PANEL_TOKEN_RE.search(source):
        chunks = [chunk.strip() for chunk in _SCENE_PANEL_TOKEN_RE.split(source) if chunk.strip()]
    else:
        chunks = [source]

    records: list[tuple[str, str, str]] = []
    for chunk in chunks:
        raw_fields = chunk.split("|")
        fields = [field.strip() for field in raw_fields]
        if len(fields) >= 6 and len(fields) % 3 == 0:
            # The compact form has no field escaping; only treat an exact
            # sequence of triplets as compact panels to avoid truncating a
            # legitimate pipe in a single description.
            records.extend(
                (fields[index], fields[index + 1], fields[index + 2])
                for index in range(0, len(fields), 3)
            )
            continue
        if len(fields) >= 3:
            # Preserve any extra separators in a normal single-panel
            # description rather than silently discarding text.
            records.append((fields[0], fields[1], "|".join(raw_fields[2:]).strip()))
    return records


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
    elif tag == "SCENE_PANEL":
        for participants, location, description in _scene_panel_triplets(value):
            if location and description:
                result.setdefault("scene_panels", []).append({
                    "participants": participants,
                    "location": location[:160],
                    "description": description[:700],
                })
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
    elif tag == "FREE_GRANT":
        # 本轮「明确免费」授权标记：只作为 purchase grant gate 的放行凭据，
        # 自身不发物品、不改余额、不创建提案，也不会被持久化。
        parts = value.split(":", 1)
        if len(parts) == 2:
            uid, item = parts[0].strip(), parts[1].strip()
            name, _quantity = split_item_quantity(item)
            if uid and name:
                result["state_update"].setdefault("free_grants", []).append({
                    "player": uid,
                    "item": name[:120],
                })
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
