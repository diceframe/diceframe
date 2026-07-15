"""Text and card content presenters for QQ bot responses."""

from __future__ import annotations

import re
from typing import Any


def format_action_result(result: dict[str, Any]) -> str:
    lines = []
    roll = result.get("roll") or {}
    if roll:
        lines.append(f"🎲 {str(roll.get('dice_system', '')).upper()} = {roll.get('value')}")
    narration = str(result.get("narration") or "").strip()
    if narration:
        lines.append(narration)
    return "\n".join(lines) or "行动已记录。"


def recap_text(detail: dict[str, Any]) -> str:
    recap = detail.get("recap") if isinstance(detail.get("recap"), dict) else {}
    scene = str(recap.get("current_scene") or detail.get("scene") or "未知场景")
    round_no = recap.get("round_number") or detail.get("round_number") or "?"
    lines = [
        "前情提要：",
        f"　　当前：第 {round_no} 轮，场景「{scene}」。",
    ]
    narrative = str(recap.get("narrative") or "").strip()
    if narrative:
        lines.append(f"　　总览：{narrative}")
    recent = recap.get("recent_rounds") if isinstance(recap.get("recent_rounds"), list) else []
    if recent:
        lines.append("最近发生：")
        for item in recent[-3:]:
            if not isinstance(item, dict):
                continue
            gm_text = str(item.get("gm_response") or "").strip()
            action_bits = []
            actions = item.get("actions") if isinstance(item.get("actions"), list) else []
            for action in actions[:3]:
                if isinstance(action, dict):
                    name = str(action.get("character_name") or "冒险者")
                    text = str(action.get("text") or "").strip()
                    if text:
                        action_bits.append(f"{name}：{text}")
            body = gm_text or "；".join(action_bits)
            if body:
                lines.append(f"　　R{item.get('round', '?')}：{body}")
    waiting = (detail.get("multiplayer") or {}).get("waiting_players") if isinstance(detail.get("multiplayer"), dict) else []
    if isinstance(waiting, list) and waiting:
        names = [
            str(item.get("character_name") or item.get("user_id") or "")
            for item in waiting
            if isinstance(item, dict) and str(item.get("character_name") or item.get("user_id") or "")
        ]
        if names:
            lines.append("现在等待：" + "、".join(names) + " 行动。")
    if len(lines) <= 2:
        lines.append("　　暂无历史回合；可以先发送行动开始冒险。")
    return "\n".join(lines)


def map_lines(data: dict[str, Any]) -> list[str]:
    locations = data.get("locations") if isinstance(data.get("locations"), list) else []
    current_scene = str(data.get("current_scene") or "").strip()
    if not locations:
        base = ["暂无地图数据；可以先在世界书补地点，或继续推进剧情。"]
        if current_scene:
            base.insert(0, f"当前场景：{current_scene}")
        return base

    by_id = {
        str(loc.get("id") or ""): str(loc.get("name") or "").strip()
        for loc in locations
        if isinstance(loc, dict) and str(loc.get("name") or "").strip()
    }
    lines = [f"当前场景：{current_scene or '未知'}"]
    for loc in locations[:10]:
        if not isinstance(loc, dict):
            continue
        name = str(loc.get("name") or "").strip()
        if not name:
            continue
        marker = "★" if is_current_location(loc, current_scene) else "•"
        content = re.sub(r"\s+", " ", str(loc.get("content") or "").strip())
        if len(content) > 42:
            content = content[:42] + "…"
        lines.append(f"{marker} {name}" + (f"：{content}" if content else ""))

    edges: list[str] = []
    seen: set[tuple[str, str]] = set()
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        src = str(loc.get("name") or "").strip()
        if not src:
            continue
        connected = loc.get("connected_to") if isinstance(loc.get("connected_to"), list) else []
        for target in connected:
            dst = by_id.get(str(target), str(target)).strip()
            if not dst or dst == src:
                continue
            pair = tuple(sorted((src, dst)))
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(f"{src} ↔ {dst}")
            if len(edges) >= 8:
                break
        if len(edges) >= 8:
            break
    if edges:
        lines.append("连接：" + "；".join(edges))
    if len(locations) > 10:
        lines.append(f"另有 {len(locations) - 10} 个地点，可在网页地图查看。")
    return lines


def is_current_location(loc: dict[str, Any], current_scene: str) -> bool:
    name = str(loc.get("name") or "").strip()
    tier = str(loc.get("tier") or "").strip()
    return bool(
        current_scene
        and name
        and (name == current_scene or name in current_scene or current_scene in name or tier == "current")
    )


def map_text(lines: list[str]) -> str:
    return "场景地图：\n" + "\n".join("　　" + line for line in lines)


def normalize_summary_line(text: str) -> str:
    return (
        re.sub(r"\s+", "", str(text or ""))
        .replace("（", "(")
        .replace("）", ")")
        .replace("：", ":")
        .replace("；", ";")
        .replace("，", ",")
    )


def text_contains_summary_line(text: str, line: str) -> bool:
    normalized_line = normalize_summary_line(line)
    if not normalized_line:
        return False
    return normalized_line in normalize_summary_line(text)


def payment_line(payment: dict[str, Any], index: int) -> str:
    amount = int(payment.get("amount", 0) or 0)
    reason = str(payment.get("reason") or "GM 建议支付").strip()
    round_no = payment.get("round", "?")
    return f"{index}. R{round_no} {amount} 金币：{reason}"


def roster_names(group: dict[str, Any]) -> str:
    names = [
        str(item.get("character_name") or "").strip()
        for item in group.get("roster", [])
        if isinstance(item, dict) and str(item.get("character_name") or "").strip()
    ]
    return "、".join(names[:12]) or "暂无角色（请先在网页创建角色）"


def roster_name_by_uid(group: dict[str, Any], uid: str) -> str:
    for item in group.get("roster", []):
        if isinstance(item, dict) and str(item.get("user_id") or "") == uid:
            return str(item.get("character_name") or uid)
    return uid


def match_roster_character(roster: list[Any], query: str) -> list[dict[str, Any]]:
    normalized_query = re.sub(r"\s+", "", str(query or ""))
    candidates = [
        item for item in roster
        if isinstance(item, dict) and str(item.get("character_name") or "").strip()
    ]
    exact = [
        item for item in candidates
        if re.sub(r"\s+", "", str(item.get("character_name") or "")) == normalized_query
    ]
    if exact:
        return exact
    return [
        item for item in candidates
        if re.sub(r"\s+", "", str(item.get("character_name") or "")) in normalized_query
    ]


def bind_success_text(result: dict[str, Any]) -> str:
    world = result.get("world_name") or result["game_key"]
    names = roster_names({"roster": result.get("players", [])})
    return (
        f"已绑定《{world}》，GM 身份已确认。\n"
        "接下来这样玩：\n"
        "1. 玩家先认领角色：@我 加入 角色名\n"
        f"   可认领：{names}\n"
        "2. 认领后直接描述行动：@我 我调查四周\n"
        "3. 需要检定时发送：@我 掷骰\n"
        "4. 补信息：@我 前情 / @我 地图；不知道做什么就发：@我 帮助"
    )


def unbound_group_text() -> str:
    return (
        "本群尚未绑定游戏。\n"
        "GM 开团步骤：\n"
        "1. 打开网页里的当前游戏\n"
        "2. 右侧 GM 操作点“一次性 Bot 绑定”\n"
        "3. 把复制出的命令发到群里：@我 绑定 <game_key> <一次性凭证>\n"
        "玩家暂时不用操作，等 GM 绑定后再发送：@我 加入 角色名"
    )


def unclaimed_player_text(group: dict[str, Any]) -> str:
    return (
        "你还没认领角色，所以暂时不能行动。\n"
        "第一步：发送 @我 加入 角色名\n"
        f"可认领：{roster_names(group)}\n"
        "例子：@我 加入 艾琳\n"
        "想先看剧情：@我 前情\n"
        "想看地点：@我 地图\n"
        "认领后就可以发送：@我 我观察四周"
    )


def bound_help_text(group: dict[str, Any]) -> str:
    return (
        "DiceFrame 群聊新手指南：\n"
        "1. 先认领角色：@我 加入 角色名\n"
        f"   可认领：{roster_names(group)}\n"
        "   没有角色：@我 新建角色 / 车卡；想 AI 辅助：@我 AI车卡\n"
        "   邀请玩家：@我 邀请\n"
        "   补前情：@我 前情\n"
        "   看地图：@我 地图\n"
        "2. 描述行动：@我 我观察四周 / @我 我攻击守卫\n"
        "3. 如果提示需要检定：@我 掷骰\n"
        "4. 查看自己状态：@我 状态\n"
        "5. 临时离开：@我 暂离；回来：@我 回来\n"
        "6. GM 推进：@我 推进 / @我 下一轮\n"
        "7. DND局小抄：优势=2d20取高，劣势=2d20取低，同时出现会抵消\n"
        "8. 看这份说明：@我 帮助"
    )


def character_creation_lines(data: dict[str, Any]) -> list[str]:
    attrs = data.get("rule_attrs") if isinstance(data.get("rule_attrs"), list) else []
    meta = data.get("rule_meta") if isinstance(data.get("rule_meta"), dict) else {}
    classes = data.get("rule_classes") if isinstance(data.get("rule_classes"), list) else []

    attr_names = [
        str(attr.get("display_name") or attr.get("name") or attr.get("key"))
        for attr in attrs
        if isinstance(attr, dict)
    ]
    class_names = []
    for item in classes:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("class_name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            class_names.append(name)
    attr_total = meta.get("attribute_points") or data.get("rule_attrs_total") or ""
    max_skills = meta.get("max_skills") or ""
    skill_points = meta.get("skill_point_total") or ""
    max_skill_value = meta.get("max_skill_value") or ""
    skill_hint = str(meta.get("skill_hint") or "").strip()
    skill_pools = meta.get("skill_pools") if isinstance(meta.get("skill_pools"), dict) else {}
    skill_examples: list[str] = []
    for values in skill_pools.values():
        if isinstance(values, list):
            skill_examples.extend(str(value).strip() for value in values if str(value).strip())
    skill_examples = list(dict.fromkeys(skill_examples))

    attr_line = "属性：" + ("、".join(attr_names[:8]) if attr_names else "按网页表单填写")
    if attr_total:
        attr_line += f"（建议总点数 {attr_total}）"
    skill_bits = []
    if max_skills:
        skill_bits.append(f"建议选 {max_skills} 个左右")
    if skill_points:
        skill_bits.append(f"参考技能点 {skill_points}")
    if max_skill_value:
        skill_bits.append(f"单项参考 {max_skill_value}")
    if skill_examples:
        skill_bits.append("例：" + "、".join(skill_examples[:6]))
    if skill_hint:
        skill_bits.append(skill_hint)
    return [
        "1. 角色名：你想被怎么称呼",
        "2. 种族/身份：如人类、修士、调查员",
        "3. 职业/定位：可自拟" + (f"；参考：{'、'.join(class_names[:6])}" if class_names else "，按世界观填写"),
        "4. " + attr_line,
        "5. 技能：" + ("；".join(skill_bits) if skill_bits else "按网页表单和当前规则填写"),
        "6. 背景：1-3 句话说明来历、目标、秘密",
        "想 AI 辅助生成草稿：@我 AI车卡",
        "填完后回群聊：@我 加入 角色名",
    ]


def character_creation_text(lines: list[str], link: str = "") -> str:
    text = "新建角色 / 车卡：\n" + "\n".join(lines)
    if link:
        text += f"\n网页建卡入口：{link}"
    return text


def character_draft_lines(draft: dict[str, Any]) -> list[str]:
    name = str(draft.get("character_name") or "未命名角色")
    race = str(draft.get("race") or "未定身份")
    cls = str(draft.get("class") or "未定定位")
    attrs = format_character_attrs(draft.get("attributes"))
    skills = format_character_skills(draft.get("skills"))
    bg_lines = background_lines(str(draft.get("background") or ""))
    equipment = format_character_items(draft.get("equipment") or draft.get("inventory"))
    lines = [
        f"角色：{name}",
        f"身份/定位：{race} · {cls}",
        f"属性：{attrs}",
        f"技能：{skills}",
        *bg_lines,
    ]
    if equipment:
        lines.append(f"装备/物品：{equipment}")
    return lines


def background_lines(background: str) -> list[str]:
    """把角色背景按段落拆分，段间用空串标记（卡片渲染时段间留空行）。"""
    raw = background.strip()
    if not raw:
        return ["背景：暂无背景"]
    segments = [re.sub(r"[ \t]+", " ", seg.strip()) for seg in re.split(r"\n\s*\n", raw) if seg.strip()]
    if not segments:
        segments = [re.sub(r"\s+", " ", raw)]
    lines: list[str] = ["背景："]
    for i, seg in enumerate(segments):
        if i:
            lines.append("")
        lines.append(seg[:200])
    return lines


def character_public_lines(draft: dict[str, Any]) -> list[str]:
    return character_draft_lines(draft)


def character_draft_text(title: str, lines: list[str], link: str = "") -> str:
    text = title + "：\n" + "\n".join("" if line == "" else "　　" + line for line in lines)
    if link:
        text += f"\n网页建卡入口：{link}"
    return text


def format_character_attrs(attrs: Any) -> str:
    if not isinstance(attrs, dict) or not attrs:
        return "按网页规则填写"
    return "、".join(f"{key} {value}" for key, value in list(attrs.items())[:8])


def format_character_skills(skills: Any) -> str:
    if not isinstance(skills, list) or not skills:
        return "按角色定位选择"
    names: list[str] = []
    for item in skills[:8]:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            value = item.get("value")
            if name:
                names.append(f"{name}{f' {value}' if value not in (None, '') else ''}")
        else:
            value = str(item).strip()
            if value:
                names.append(value)
    return "、".join(names) or "按角色定位选择"


def format_character_items(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return ""
    names: list[str] = []
    for item in items[:6]:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item).strip()
        if name:
            names.append(name)
    return "、".join(names)


def player_tutorial_lines() -> list[str]:
    return [
        "1. 先补剧情：@我 前情",
        "2. 没角色：@我 新建角色 / 车卡，然后按提示填角色名、身份、职业、属性、技能、背景",
        "   想让 AI 辅助起草：@我 AI车卡",
        "3. 有角色：@我 加入 角色名",
        "4. 开始玩：@我 我观察四周 / @我 我向守卫打听消息",
        "5. 被要求检定：@我 掷骰；想看自己状态：@我 状态",
        "DND局小抄：优势=2d20取高，劣势=2d20取低；同时出现会抵消",
        "额外：@我 地图 看地点；@我 感知 看私密信息；@我 支付 处理待确认付款；卡住就发 @我 帮助",
    ]


def player_tutorial_text(lines: list[str]) -> str:
    return "群聊跑团新玩家一图流：\n" + "\n".join(lines)
