"""Shared text and card content presenters for DiceFrame bridge responses."""

from __future__ import annotations

import re
from typing import Any

from src.engine.language import localized_text


def command_example(command: str = "", *, command_prefix: str = "@我") -> str:
    prefix = str(command_prefix or "").strip()
    command = str(command or "").strip()
    return f"{prefix} {command}".strip() if command else prefix


def format_action_result(result: dict[str, Any], language: str = "zh-CN") -> str:
    if str(result.get("error_code") or "") == "ECONOMY_DECISION_PENDING":
        if result.get("economy_proposals"):
            return localized_text(language, {
                "en": "An economy proposal is waiting for your decision. Send “pay” to review it.",
                "zh-CN": "当前有经济提案待确认，请发送“支付”查看。",
                "ja": "経済提案の確認待ちです。「支払い」で確認してください。",
            })
        return localized_text(language, {
            "en": "The game is waiting for the GM or another contributor to resolve an economy proposal.",
            "zh-CN": "当前正在等待 GM 或其他参与者处理经济提案。",
            "ja": "GM またはほかの参加者による経済提案の処理を待っています。",
        })
    lines = []
    roll = result.get("roll") or {}
    if roll:
        lines.append(f"🎲 {str(roll.get('dice_system', '')).upper()} = {roll.get('value')}")
    checks = result.get("check_results") if isinstance(result.get("check_results"), list) else []
    lines.extend(format_check_result(check, language) for check in checks if isinstance(check, dict))
    narration = str(result.get("narration") or "").strip()
    if narration:
        lines.append(narration)
    pending_luck = result.get("pending_luck_decisions") if isinstance(result.get("pending_luck_decisions"), list) else []
    if pending_luck:
        lines.extend(luck_prompt_lines(pending_luck, language))
    return "\n".join(lines) or localized_text(
        language, {"en": "Action recorded.", "zh-CN": "行动已记录。", "ja": "行動が記録されました。"}
    )


def format_check_result(check: dict[str, Any], language: str = "zh-CN") -> str:
    """群聊没有网页卡片，以同一结构化结果渲染紧凑文本。"""
    actor = str(check.get("actor_name") or check.get("actor_uid") or localized_text(
        language, {"en": "Character", "zh-CN": "角色", "ja": "キャラクター"}
    ))
    label = str(check.get("label") or localized_text(
        language, {"en": "Check", "zh-CN": "检定", "ja": "判定"}
    ))
    dice = str(check.get("dice") or "d20")
    roll = check.get("roll")
    verdict_raw = str(check.get("verdict") or "")
    verdict_map = {
        "大成功": "Critical Success",
        "极难成功": "Extreme Success",
        "困难成功": "Hard Success",
        "普通成功": "Regular Success",
        "成功": "Success",
        "失败": "Failure",
        "大失败": "Critical Failure",
    }
    verdict = localized_text(
        language,
        {
            "en": verdict_map.get(verdict_raw, verdict_raw),
            "zh-CN": verdict_raw,
            "ja": verdict_raw,
        },
    )
    if dice == "d100":
        math = f"d100={roll} vs {check.get('threshold')}%"
    else:
        modifier = int(check.get("modifier", 0) or 0)
        if check.get("opponent_name"):
            math = (
                f"d20={roll}({modifier:+d})={check.get('total')} vs {check.get('opponent_name')} "
                f"d20={check.get('opponent_roll')}({int(check.get('opponent_modifier', 0) or 0):+d})="
                f"{check.get('opponent_total')}"
            )
        else:
            math = f"d20={roll}({modifier:+d})={check.get('total')} vs DC {check.get('dc')}"
    return f"🎲 {actor} · {label} {math} → {verdict}"


def luck_prompt_lines(
    checks: list[dict[str, Any]],
    language: str = "zh-CN",
    *,
    command_prefix: str = "@me",
) -> list[str]:
    """格式化跨 Bridge 共用的幸运选择提示。"""
    lines = [localized_text(language, {
        "en": "Luck decision required:",
        "zh-CN": "需要决定是否使用幸运：",
        "ja": "幸運を使用するかどうか決めてください：",
    })]
    multiple = len(checks) > 1
    for index, check in enumerate(checks, 1):
        prefix = f"{index}. " if multiple else ""
        actor = str(check.get("actor_name") or check.get("actor_uid") or localized_text(
            language, {"en": "Character", "zh-CN": "角色", "ja": "キャラクター"}
        ))
        label = str(check.get("label") or localized_text(
            language, {"en": "Check", "zh-CN": "检定", "ja": "判定"}
        ))
        roll = check.get("roll")
        threshold = check.get("threshold")
        cost = int(check.get("luck_cost", 0) or 0)
        lines.append(localized_text(language, {
            "en": f"{prefix}{actor} · {label}: d100={roll}/{threshold}, spend {cost} Luck for a regular success.",
            "zh-CN": f"{prefix}{actor} · {label}：d100={roll}/{threshold}，可消耗 {cost} 点幸运变为普通成功。",
            "ja": f"{prefix}{actor} · {label}：d100={roll}/{threshold}、幸運を{cost}点消費して通常成功にできます。",
        }))
    lines.append(localized_text(language, {
        "en": f"Use: {command_prefix} luck; keep failure: {command_prefix} no luck",
        "zh-CN": f"使用：{command_prefix} 幸运；保留失败：{command_prefix} 不用幸运",
        "ja": f"使用：{command_prefix} luck；失敗のまま残す：{command_prefix} no luck",
    }))
    if multiple:
        lines.append(localized_text(language, {
            "en": "Your character is matched automatically; add a number only if that character has multiple decisions.",
            "zh-CN": "系统会自动匹配你的角色；只有同一角色有多个选择时才需要追加序号。",
            "ja": "あなたのキャラクターは自動的にマッチします；同一キャラクターに複数の選択肢がある場合のみ番号を付けてください。",
        }))
    return lines


def recap_text(detail: dict[str, Any], language: str = "zh-CN") -> str:
    recap = detail.get("recap") if isinstance(detail.get("recap"), dict) else {}
    scene = str(recap.get("current_scene") or detail.get("scene") or localized_text(
        language, {"en": "Unknown scene", "zh-CN": "未知场景", "ja": "不明なシーン"}
    ))
    round_no = recap.get("round_number") or detail.get("round_number") or "?"
    lines = [localized_text(language, {"en": "Recap:", "zh-CN": "前情提要：", "ja": "これまでのあらすじ："})]
    lines.append(localized_text(language, {
        "en": f"  Current: round {round_no}, scene “{scene}”.",
        "zh-CN": f"　　当前：第 {round_no} 轮，场景「{scene}」。",
        "ja": f"　　現在：第 {round_no} ラウンド、シーン「{scene}」。",
    }))
    narrative = str(recap.get("narrative") or "").strip()
    if narrative:
        lines.append(localized_text(language, {
            "en": f"  Overview: {narrative}",
            "zh-CN": f"　　总览：{narrative}",
            "ja": f"　　概要：{narrative}",
        }))
    recent = recap.get("recent_rounds") if isinstance(recap.get("recent_rounds"), list) else []
    if recent:
        lines.append(localized_text(language, {"en": "Recent events:", "zh-CN": "最近发生：", "ja": "最近の出来事："}))
        for item in recent[-3:]:
            if not isinstance(item, dict):
                continue
            gm_text = str(item.get("gm_response") or "").strip()
            action_bits = []
            actions = item.get("actions") if isinstance(item.get("actions"), list) else []
            for action in actions[:3]:
                if isinstance(action, dict):
                    name = str(action.get("character_name") or localized_text(
                        language, {"en": "Adventurer", "zh-CN": "冒险者", "ja": "冒険者"}
                    ))
                    text = str(action.get("text") or "").strip()
                    if text:
                        action_bits.append(localized_text(language, {
                            "en": f"{name}: {text}",
                            "zh-CN": f"{name}：{text}",
                            "ja": f"{name}：{text}",
                        }))
            body = gm_text or localized_text(language, {
                "en": "; ".join(action_bits),
                "zh-CN": "；".join(action_bits),
                "ja": "；".join(action_bits),
            })
            if body:
                lines.append(localized_text(language, {
                    "en": f"  R{item.get('round', '?')}: {body}",
                    "zh-CN": f"　　R{item.get('round', '?')}：{body}",
                    "ja": f"　　R{item.get('round', '?')}：{body}",
                }))
    waiting = (detail.get("multiplayer") or {}).get("waiting_players") if isinstance(detail.get("multiplayer"), dict) else []
    if isinstance(waiting, list) and waiting:
        names = [
            str(item.get("character_name") or item.get("user_id") or "")
            for item in waiting
            if isinstance(item, dict) and str(item.get("character_name") or item.get("user_id") or "")
        ]
        if names:
            lines.append(localized_text(language, {
                "en": "Waiting for actions from: " + ", ".join(names) + ".",
                "zh-CN": "现在等待：" + "、".join(names) + " 行动。",
                "ja": "アクション待ち：" + "、".join(names) + "。",
            }))
    if len(lines) <= 2:
        lines.append(localized_text(language, {
            "en": "  No previous rounds yet; submit an action to begin.",
            "zh-CN": "　　暂无历史回合；可以先发送行动开始冒险。",
            "ja": "　　まだ過去のラウンドはありません；行動を送って冒険を始めてください。",
        }))
    return "\n".join(lines)


def map_lines(data: dict[str, Any], language: str = "zh-CN") -> list[str]:
    locations = data.get("locations") if isinstance(data.get("locations"), list) else []
    current_scene = str(data.get("current_scene") or "").strip()
    if not locations:
        base = [localized_text(language, {
            "en": "No map data yet. Add locations to the lorebook or continue the story.",
            "zh-CN": "暂无地图数据；可以先在世界书补地点，或继续推进剧情。",
            "ja": "まだマップデータがありません。ワールドブックに地点を追加するか、物語を進めてください。",
        })]
        if current_scene:
            base.insert(0, localized_text(language, {
                "en": f"Current scene: {current_scene}",
                "zh-CN": f"当前场景：{current_scene}",
                "ja": f"現在のシーン：{current_scene}",
            }))
        return base

    by_id = {
        str(loc.get("id") or ""): str(loc.get("name") or "").strip()
        for loc in locations
        if isinstance(loc, dict) and str(loc.get("name") or "").strip()
    }
    lines = [localized_text(language, {
        "en": f"Current scene: {current_scene or 'Unknown'}",
        "zh-CN": f"当前场景：{current_scene or '未知'}",
        "ja": f"現在のシーン：{current_scene or '不明'}",
    })]
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
        lines.append(f"{marker} {name}" + (localized_text(language, {
            "en": f": {content}",
            "zh-CN": f"：{content}",
            "ja": f"：{content}",
        }) if content else ""))

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
        lines.append(localized_text(language, {
            "en": "Connections: " + "; ".join(edges),
            "zh-CN": "连接：" + "；".join(edges),
            "ja": "接続：" + "；".join(edges),
        }))
    if len(locations) > 10:
        lines.append(localized_text(language, {
            "en": f"{len(locations) - 10} more locations are available on the web map.",
            "zh-CN": f"另有 {len(locations) - 10} 个地点，可在网页地图查看。",
            "ja": f"あと {len(locations) - 10} 地点はウェブマップで確認できます。",
        }))
    return lines


def is_current_location(loc: dict[str, Any], current_scene: str) -> bool:
    name = str(loc.get("name") or "").strip()
    tier = str(loc.get("tier") or "").strip()
    return bool(
        current_scene
        and name
        and (name == current_scene or name in current_scene or current_scene in name or tier == "current")
    )


def map_text(lines: list[str], language: str = "zh-CN") -> str:
    title = localized_text(language, {"en": "Scene map:\n", "zh-CN": "场景地图：\n", "ja": "シーンマップ：\n"})
    indent = localized_text(language, {"en": "  ", "zh-CN": "　　", "ja": "　　"})
    return title + "\n".join(indent + line for line in lines)


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


def payment_line(payment: dict[str, Any], index: int, language: str = "zh-CN") -> str:
    amount = int(payment.get("amount", 0) or 0)
    reason = str(payment.get("reason") or localized_text(language, {
        "en": "GM-requested payment",
        "zh-CN": "GM 建议支付",
        "ja": "GM からの支払い要請",
    })).strip()
    round_no = payment.get("round", "?")
    kind = str(payment.get("kind") or "payment")
    marker = localized_text(language, {
        "en": "reward" if kind == "reward" else "payment",
        "zh-CN": "奖励" if kind == "reward" else "支付",
        "ja": "報酬" if kind == "reward" else "支払い",
    })
    reference = int(payment.get("sequence", index) or index)
    return localized_text(language, {
        "en": f"#{reference} · R{round_no} {marker} {amount} gold: {reason}",
        "zh-CN": f"#{reference} · R{round_no} {marker} {amount} 金币：{reason}",
        "ja": f"#{reference} · R{round_no} {marker} {amount} ゴールド：{reason}",
    })


def roster_names(group: dict[str, Any], language: str = "zh-CN") -> str:
    names = [
        str(item.get("character_name") or "").strip()
        for item in group.get("roster", [])
        if isinstance(item, dict) and str(item.get("character_name") or "").strip()
    ]
    return localized_text(language, {
        "en": ", ".join(names[:12]) or "No characters yet (create one on the web page first)",
        "zh-CN": "、".join(names[:12]) or "暂无角色（请先在网页创建角色）",
        "ja": "、".join(names[:12]) or "まだキャラクターがいません（先にウェブページで作成してください）",
    })


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


def bind_success_text(
    result: dict[str, Any],
    *,
    command_prefix: str = "@我",
    language: str = "zh-CN",
) -> str:
    world = result.get("world_name") or result["game_key"]
    names = roster_names({"roster": result.get("players", [])}, language)
    return localized_text(language, {
        "en": (
            f"Bound to “{world}”; GM identity confirmed.\n"
            "How to get started:\n"
            f"1. Claim a character: {command_example('join Character Name', command_prefix=command_prefix)}\n"
            f"   Available: {names}\n"
            f"2. Describe an action: {command_example('I inspect the area', command_prefix=command_prefix)}\n"
            "3. Checks are adjudicated and rolled automatically after the table is ready.\n"
            f"4. Catch up with {command_example('recap', command_prefix=command_prefix)} or "
            f"{command_example('map', command_prefix=command_prefix)}; use "
            f"{command_example('help', command_prefix=command_prefix)} if you get stuck."
        ),
        "zh-CN": (
            f"已绑定《{world}》，GM 身份已确认。\n"
            "接下来这样玩：\n"
            f"1. 玩家先认领角色：{command_example('加入 角色名', command_prefix=command_prefix)}\n"
            f"   可认领：{names}\n"
            f"2. 认领后直接描述行动：{command_example('我调查四周', command_prefix=command_prefix)}\n"
            "3. 人齐后系统自动判断并掷骰，不需要发送掷骰命令。\n"
            f"4. 补信息：{command_example('前情', command_prefix=command_prefix)} / {command_example('地图', command_prefix=command_prefix)}；不知道做什么就发：{command_example('帮助', command_prefix=command_prefix)}"
        ),
        "ja": (
            f"《{world}》にバインドしました。GM として確認済みです。\n"
            "遊び方：\n"
            f"1. まずキャラクターを認領する：{command_example('join キャラクター名', command_prefix=command_prefix)}\n"
            f"   認領可能：{names}\n"
            f"2. 認領後は行動を直接記述する：{command_example('部屋の中を調べる', command_prefix=command_prefix)}\n"
            "3. メンバーが揃うと自動で判定とダイスロールが行われます。ダイスコマンドは不要です。\n"
            f"4. 補足：{command_example('recap', command_prefix=command_prefix)} / {command_example('map', command_prefix=command_prefix)}；困ったら {command_example('help', command_prefix=command_prefix)} を送信してください。"
        ),
    })


def unbound_group_text(*, command_prefix: str = "@我", language: str = "zh-CN") -> str:
    return localized_text(language, {
        "en": (
            "This chat is not bound to a game yet.\n"
            "GM setup:\n"
            "1. Open the current game in the web app\n"
            "2. Choose “One-time Bot binding” in GM controls\n"
            f"3. Send the copied command here: {command_example('bind <game_key> <one-time-token>', command_prefix=command_prefix)}\n"
            f"Players can then claim a character with: {command_example('join Character Name', command_prefix=command_prefix)}"
        ),
        "zh-CN": (
            "本群尚未绑定游戏。\n"
            "GM 开团步骤：\n"
            "1. 打开网页里的当前游戏\n"
            "2. 右侧 GM 操作点“一次性 Bot 绑定”\n"
            f"3. 把复制出的命令发到群里：{command_example('绑定 <game_key> <一次性凭证>', command_prefix=command_prefix)}\n"
            f"玩家暂时不用操作，等 GM 绑定后再发送：{command_example('加入 角色名', command_prefix=command_prefix)}"
        ),
        "ja": (
            "このチャットはまだゲームにバインドされていません。\n"
            "GM のセットアップ手順：\n"
            "1. ウェブアプリで現在のゲームを開く\n"
            "2. GM コントロールの「One-time Bot バインド」を選択\n"
            f"3. コピーしたコマンドをここに送信：{command_example('bind <game_key> <one-time-token>', command_prefix=command_prefix)}\n"
            f"プレイヤーはその後 {command_example('join Character Name', command_prefix=command_prefix)} でキャラクターを認領できます。"
        ),
    })


def unclaimed_player_text(
    group: dict[str, Any],
    *,
    command_prefix: str = "@我",
    language: str = "zh-CN",
) -> str:
    return localized_text(language, {
        "en": (
            "You have not claimed a character yet, so you cannot submit actions.\n"
            f"First: {command_example('join Character Name', command_prefix=command_prefix)}\n"
            f"Available: {roster_names(group, language)}\n"
            f"Example: {command_example('join Erin', command_prefix=command_prefix)}\n"
            f"Catch up: {command_example('recap', command_prefix=command_prefix)}\n"
            f"View locations: {command_example('map', command_prefix=command_prefix)}\n"
            f"Then act with: {command_example('I inspect the area', command_prefix=command_prefix)}"
        ),
        "zh-CN": (
            "你还没认领角色，所以暂时不能行动。\n"
            f"第一步：发送 {command_example('加入 角色名', command_prefix=command_prefix)}\n"
            f"可认领：{roster_names(group)}\n"
            f"例子：{command_example('加入 艾琳', command_prefix=command_prefix)}\n"
            f"想先看剧情：{command_example('前情', command_prefix=command_prefix)}\n"
            f"想看地点：{command_example('地图', command_prefix=command_prefix)}\n"
            f"认领后就可以发送：{command_example('我观察四周', command_prefix=command_prefix)}"
        ),
        "ja": (
            "まだキャラクターを認領していないため、行動を送信できません。\n"
            f"まず：{command_example('join Character Name', command_prefix=command_prefix)}\n"
            f"認領可能：{roster_names(group, language)}\n"
            f"例：{command_example('join Erin', command_prefix=command_prefix)}\n"
            f"前情を確認：{command_example('recap', command_prefix=command_prefix)}\n"
            f"地点を確認：{command_example('map', command_prefix=command_prefix)}\n"
            f"認領後は：{command_example('部屋の中を調べる', command_prefix=command_prefix)}"
        ),
    })


def bound_help_text(
    group: dict[str, Any],
    *,
    command_prefix: str = "@我",
    language: str = "zh-CN",
) -> str:
    return localized_text(language, {
        "en": (
            "DiceFrame chat quick start:\n"
            f"1. Claim a character: {command_example('join Character Name', command_prefix=command_prefix)}\n"
            f"   Available: {roster_names(group, language)}\n"
            f"   Need one? {command_example('create character', command_prefix=command_prefix)}; "
            f"AI draft: {command_example('AI character', command_prefix=command_prefix)}\n"
            f"   Invite players: {command_example('invite', command_prefix=command_prefix)}\n"
            f"   Catch up: {command_example('recap', command_prefix=command_prefix)}; "
            f"map: {command_example('map', command_prefix=command_prefix)}\n"
            f"2. Describe an action: {command_example('I inspect the area', command_prefix=command_prefix)}\n"
            f"   Ask the GM without acting: {command_example('ask kp <question>', command_prefix=command_prefix)}\n"
            "3. Checks are adjudicated and rolled automatically when the table is ready.\n"
            f"   If offered: {command_example('luck', command_prefix=command_prefix)} or {command_example('no luck', command_prefix=command_prefix)}\n"
            f"4. Character status: {command_example('status', command_prefix=command_prefix)}\n"
            f"5. Step away: {command_example('away', command_prefix=command_prefix)}; "
            f"return: {command_example('back', command_prefix=command_prefix)}\n"
            f"6. GM progression: {command_example('advance', command_prefix=command_prefix)}\n"
            f"7. Use {command_example('help', command_prefix=command_prefix)} whenever you get stuck."
        ),
        "zh-CN": (
            "DiceFrame 群聊新手指南：\n"
            f"1. 先认领角色：{command_example('加入 角色名', command_prefix=command_prefix)}\n"
            f"   可认领：{roster_names(group)}\n"
            f"   没有角色：{command_example('新建角色', command_prefix=command_prefix)} / {command_example('车卡', command_prefix=command_prefix)}；想 AI 辅助：{command_example('AI车卡', command_prefix=command_prefix)}\n"
            f"   邀请玩家：{command_example('邀请', command_prefix=command_prefix)}\n"
            f"   补前情：{command_example('前情', command_prefix=command_prefix)}\n"
            f"   看地图：{command_example('地图', command_prefix=command_prefix)}\n"
            f"2. 描述行动：{command_example('我观察四周', command_prefix=command_prefix)} / {command_example('我攻击守卫', command_prefix=command_prefix)}\n"
            f"   桌外问 KP（不耗行动）：{command_example('询问 <问题>', command_prefix=command_prefix)}\n"
            "3. 人齐后系统自动判断并掷骰，不需要发送掷骰命令。\n"
            f"   如果可以消耗幸运：{command_example('幸运', command_prefix=command_prefix)} / {command_example('不用幸运', command_prefix=command_prefix)}\n"
            f"4. 查看自己状态：{command_example('状态', command_prefix=command_prefix)}\n"
            f"5. 临时离开：{command_example('暂离', command_prefix=command_prefix)}；回来：{command_example('回来', command_prefix=command_prefix)}\n"
            f"6. GM 推进：{command_example('推进', command_prefix=command_prefix)} / {command_example('下一轮', command_prefix=command_prefix)}\n"
            "7. DND局小抄：优势=2d20取高，劣势=2d20取低，同时出现会抵消\n"
            f"8. 看这份说明：{command_example('帮助', command_prefix=command_prefix)}"
        ),
        "ja": (
            "DiceFrame チャット クイックスタート：\n"
            f"1. キャラクターを認領する：{command_example('join Character Name', command_prefix=command_prefix)}\n"
            f"   認領可能：{roster_names(group, language)}\n"
            f"   まだない？{command_example('create character', command_prefix=command_prefix)}；"
            f"AI による下書き：{command_example('AI character', command_prefix=command_prefix)}\n"
            f"   プレイヤーを招待：{command_example('invite', command_prefix=command_prefix)}\n"
            f"   前情を確認：{command_example('recap', command_prefix=command_prefix)}；"
            f"マップ：{command_example('map', command_prefix=command_prefix)}\n"
            f"2. 行動を記述する：{command_example('部屋の中を調べる', command_prefix=command_prefix)}\n"
            f"   行動せず GM に質問：{command_example('ask kp <question>', command_prefix=command_prefix)}\n"
            "3. メンバーが揃うと自動で判定とダイスロールが行われます。\n"
            f"   選択肢が出たら：{command_example('luck', command_prefix=command_prefix)} または {command_example('no luck', command_prefix=command_prefix)}\n"
            f"4. キャラクターの状態：{command_example('status', command_prefix=command_prefix)}\n"
            f"5. 一時離席：{command_example('away', command_prefix=command_prefix)}；"
            f"復帰：{command_example('back', command_prefix=command_prefix)}\n"
            f"6. GM の進行：{command_example('advance', command_prefix=command_prefix)}\n"
            f"7. 困ったら {command_example('help', command_prefix=command_prefix)} を送ってください。"
        ),
    })


def character_creation_lines(
    data: dict[str, Any],
    *,
    command_prefix: str = "@我",
    language: str = "zh-CN",
) -> list[str]:
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

    attr_line = localized_text(language, {
        "en": "Attributes: " + (", ".join(attr_names[:8]) if attr_names else "follow the web form"),
        "zh-CN": "属性：" + ("、".join(attr_names[:8]) if attr_names else "按网页表单填写"),
        "ja": "属性：" + ("、".join(attr_names[:8]) if attr_names else "ウェブフォームに従って入力"),
    })
    if attr_total:
        attr_line += localized_text(language, {
            "en": f" (suggested total: {attr_total})",
            "zh-CN": f"（建议总点数 {attr_total}）",
            "ja": f"（推奨合計：{attr_total}）",
        })
    skill_bits = []
    if max_skills:
        skill_bits.append(localized_text(language, {
            "en": f"choose about {max_skills}",
            "zh-CN": f"建议选 {max_skills} 个左右",
            "ja": f"約 {max_skills} 個を推奨",
        }))
    if skill_points:
        skill_bits.append(localized_text(language, {
            "en": f"suggested skill points: {skill_points}",
            "zh-CN": f"参考技能点 {skill_points}",
            "ja": f"技能ポイント目安：{skill_points}",
        }))
    if max_skill_value:
        skill_bits.append(localized_text(language, {
            "en": f"suggested per-skill maximum: {max_skill_value}",
            "zh-CN": f"单项参考 {max_skill_value}",
            "ja": f"単一スキル上限の目安：{max_skill_value}",
        }))
    if skill_examples:
        skill_bits.append(localized_text(language, {
            "en": "Examples: " + ", ".join(skill_examples[:6]),
            "zh-CN": "例：" + "、".join(skill_examples[:6]),
            "ja": "例：" + "、".join(skill_examples[:6]),
        }))
    if skill_hint:
        skill_bits.append(skill_hint)
    return localized_text(language, {
        "en": [
            "1. Character name: what should others call you?",
            "2. Species/identity: human, investigator, elf, etc.",
            "3. Class/role: create your own" + (f"; examples: {', '.join(class_names[:6])}" if class_names else " based on the setting"),
            "4. " + attr_line,
            "5. Skills: " + ("; ".join(skill_bits) if skill_bits else "follow the current rules and web form"),
            "6. Background: 1–3 sentences about your origin, goal, or secret",
            f"Want an AI draft? {command_example('AI character', command_prefix=command_prefix)}",
            f"When ready, return here and use: {command_example('join Character Name', command_prefix=command_prefix)}",
        ],
        "zh-CN": [
            "1. 角色名：你想被怎么称呼",
            "2. 种族/身份：如人类、修士、调查员",
            "3. 职业/定位：可自拟" + (f"；参考：{'、'.join(class_names[:6])}" if class_names else "，按世界观填写"),
            "4. " + attr_line,
            "5. 技能：" + ("；".join(skill_bits) if skill_bits else "按网页表单和当前规则填写"),
            "6. 背景：1-3 句话说明来历、目标、秘密",
            f"想 AI 辅助生成草稿：{command_example('AI车卡', command_prefix=command_prefix)}",
            f"填完后回群聊：{command_example('加入 角色名', command_prefix=command_prefix)}",
        ],
        "ja": [
            "1. キャラクター名：どう呼ばれたいか",
            "2. 種族/身分：人間、調査員、エルフなど",
            "3. 職業/ロール：自由に設定" + (f"；参考：{'、'.join(class_names[:6])}" if class_names else "、世界観に合わせて記入"),
            "4. " + attr_line,
            "5. スキル：" + ("；".join(skill_bits) if skill_bits else "現在のルールとウェブフォームに従って記入"),
            "6. 背景：来歴・目標・秘密を 1〜3 文で",
            f"AI による下書き生成：{command_example('AI character', command_prefix=command_prefix)}",
            f"記入が終わったらここで送信：{command_example('join Character Name', command_prefix=command_prefix)}",
        ],
    })


def character_creation_text(lines: list[str], link: str = "", language: str = "zh-CN") -> str:
    text = localized_text(language, {
        "en": "Create a character:\n",
        "zh-CN": "新建角色 / 车卡：\n",
        "ja": "キャラクター作成：\n",
    }) + "\n".join(lines)
    if link:
        text += localized_text(language, {
            "en": f"\nWeb character creator: {link}",
            "zh-CN": f"\n网页建卡入口：{link}",
            "ja": f"\nウェブのキャラクター作成ページ：{link}",
        })
    return text


def character_draft_lines(draft: dict[str, Any], language: str = "zh-CN") -> list[str]:
    name = str(draft.get("character_name") or localized_text(
        language, {"en": "Unnamed character", "zh-CN": "未命名角色", "ja": "名前のないキャラクター"}
    ))
    race = str(draft.get("race") or localized_text(
        language, {"en": "Unspecified identity", "zh-CN": "未定身份", "ja": "未設定の身分"}
    ))
    cls = str(draft.get("class") or localized_text(
        language, {"en": "Unspecified role", "zh-CN": "未定定位", "ja": "未設定のロール"}
    ))
    attrs = format_character_attrs(draft.get("attributes"), language)
    skills = format_character_skills(draft.get("skills"), language)
    bg_lines = background_lines(str(draft.get("background") or ""), language)
    equipment = format_character_items(draft.get("equipment") or draft.get("inventory"), language)
    lines = localized_text(language, {
        "en": [
            f"Character: {name}",
            f"Identity/role: {race} · {cls}",
            f"Attributes: {attrs}",
            f"Skills: {skills}",
            *bg_lines,
        ],
        "zh-CN": [
            f"角色：{name}",
            f"身份/定位：{race} · {cls}",
            f"属性：{attrs}",
            f"技能：{skills}",
            *bg_lines,
        ],
        "ja": [
            f"キャラクター：{name}",
            f"身分/ロール：{race} · {cls}",
            f"属性：{attrs}",
            f"スキル：{skills}",
            *bg_lines,
        ],
    })
    if equipment:
        lines.append(localized_text(language, {
            "en": f"Equipment/items: {equipment}",
            "zh-CN": f"装备/物品：{equipment}",
            "ja": f"装備/所持品：{equipment}",
        }))
    return lines


def background_lines(background: str, language: str = "zh-CN") -> list[str]:
    """把角色背景按段落拆分，段间用空串标记（卡片渲染时段间留空行）。"""
    raw = background.strip()
    if not raw:
        return [localized_text(language, {"en": "Background: none yet", "zh-CN": "背景：暂无背景", "ja": "背景：まだなし"})]
    segments = [re.sub(r"[ \t]+", " ", seg.strip()) for seg in re.split(r"\n\s*\n", raw) if seg.strip()]
    if not segments:
        segments = [re.sub(r"\s+", " ", raw)]
    lines: list[str] = [localized_text(language, {"en": "Background:", "zh-CN": "背景：", "ja": "背景："})]
    for i, seg in enumerate(segments):
        if i:
            lines.append("")
        lines.append(seg[:200])
    return lines


def character_public_lines(draft: dict[str, Any], language: str = "zh-CN") -> list[str]:
    return character_draft_lines(draft, language)


def character_draft_text(title: str, lines: list[str], link: str = "", language: str = "zh-CN") -> str:
    indent = localized_text(language, {"en": "  ", "zh-CN": "　　", "ja": "　　"})
    text = title + localized_text(language, {"en": ":\n", "zh-CN": "：\n", "ja": "：\n"}) + "\n".join(
        "" if line == "" else indent + line for line in lines
    )
    if link:
        text += localized_text(language, {
            "en": f"\nWeb character creator: {link}",
            "zh-CN": f"\n网页建卡入口：{link}",
            "ja": f"\nウェブのキャラクター作成ページ：{link}",
        })
    return text


def format_character_attrs(attrs: Any, language: str = "zh-CN") -> str:
    if not isinstance(attrs, dict) or not attrs:
        return localized_text(language, {"en": "Follow the web rules", "zh-CN": "按网页规则填写", "ja": "ウェブのルールに従って入力"})
    separator = localized_text(language, {"en": ", ", "zh-CN": "、", "ja": "、"})
    return separator.join(f"{key} {value}" for key, value in list(attrs.items())[:8])


def format_character_skills(skills: Any, language: str = "zh-CN") -> str:
    if not isinstance(skills, list) or not skills:
        return localized_text(language, {"en": "Choose based on the character’s role", "zh-CN": "按角色定位选择", "ja": "キャラクターのロールに合わせて選択"})
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
    separator = localized_text(language, {"en": ", ", "zh-CN": "、", "ja": "、"})
    return separator.join(names) or localized_text(language, {
        "en": "Choose based on the character’s role",
        "zh-CN": "按角色定位选择",
        "ja": "キャラクターのロールに合わせて選択",
    })


def format_character_items(items: Any, language: str = "zh-CN") -> str:
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
    return localized_text(language, {"en": ", ", "zh-CN": "、", "ja": "、"}).join(names)


def player_tutorial_lines(*, command_prefix: str = "@我", language: str = "zh-CN") -> list[str]:
    return localized_text(language, {
        "en": [
            f"1. Catch up: {command_example('recap', command_prefix=command_prefix)}",
            f"2. Need a character? {command_example('create character', command_prefix=command_prefix)}",
            f"   Want an AI draft? {command_example('AI character', command_prefix=command_prefix)}",
            f"3. Claim one: {command_example('join Character Name', command_prefix=command_prefix)}",
            f"4. Start playing: {command_example('I inspect the area', command_prefix=command_prefix)}",
            f"5. Checks are adjudicated and rolled automatically; status: "
            f"{command_example('status', command_prefix=command_prefix)}",
            f"Extra: {command_example('map', command_prefix=command_prefix)}, "
            f"{command_example('sense', command_prefix=command_prefix)}, "
            f"{command_example('pay', command_prefix=command_prefix)}, or "
            f"{command_example('help', command_prefix=command_prefix)}",
        ],
        "zh-CN": [
            f"1. 先补剧情：{command_example('前情', command_prefix=command_prefix)}",
            f"2. 没角色：{command_example('新建角色', command_prefix=command_prefix)} / {command_example('车卡', command_prefix=command_prefix)}，然后按提示填角色名、身份、职业、属性、技能、背景",
            f"   想让 AI 辅助起草：{command_example('AI车卡', command_prefix=command_prefix)}",
            f"3. 有角色：{command_example('加入 角色名', command_prefix=command_prefix)}",
            f"4. 开始玩：{command_example('我观察四周', command_prefix=command_prefix)} / {command_example('我向守卫打听消息', command_prefix=command_prefix)}",
            f"5. 检定由系统自动判断并掷骰；想看自己状态：{command_example('状态', command_prefix=command_prefix)}",
            "DND局小抄：优势=2d20取高，劣势=2d20取低；同时出现会抵消",
            f"额外：{command_example('地图', command_prefix=command_prefix)} 看地点；{command_example('感知', command_prefix=command_prefix)} 看私密信息；{command_example('支付', command_prefix=command_prefix)} 处理待确认付款；卡住就发 {command_example('帮助', command_prefix=command_prefix)}",
        ],
        "ja": [
            f"1. 前情を確認：{command_example('recap', command_prefix=command_prefix)}",
            f"2. キャラクターがまだない場合：{command_example('create character', command_prefix=command_prefix)}、表示に従って名前・身分・職業・属性・技能・背景を入力",
            f"   AI による下書き：{command_example('AI character', command_prefix=command_prefix)}",
            f"3. キャラクターがある場合：{command_example('join Character Name', command_prefix=command_prefix)}",
            f"4. プレイ開始：{command_example('部屋の中を調べる', command_prefix=command_prefix)} / {command_example('衛兵に話を聞く', command_prefix=command_prefix)}",
            f"5. 判定は自動で行われます；自分の状態を見る：{command_example('status', command_prefix=command_prefix)}",
            "DND メモ：有利=2d20の高い方、不利=2d20の低い方；同時の場合は相殺",
            f"その他：{command_example('map', command_prefix=command_prefix)} で地点、{command_example('sense', command_prefix=command_prefix)} でプライベート情報、{command_example('pay', command_prefix=command_prefix)} で支払い確認；困ったら {command_example('help', command_prefix=command_prefix)}",
        ],
    })


def player_tutorial_text(lines: list[str], language: str = "zh-CN") -> str:
    title = localized_text(language, {
        "en": "New player quick start:\n",
        "zh-CN": "群聊跑团新玩家一图流：\n",
        "ja": "新規プレイヤー クイックスタート：\n",
    })
    return title + "\n".join(lines)
