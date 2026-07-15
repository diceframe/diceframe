"""玩家可见状态变化摘要。

这个模块只负责把一轮结算前后的公开状态差异整理成可读文案。
它不修改游戏状态，因此可以作为 GameHandler 的纯辅助层独立测试。
"""

from __future__ import annotations

from src.engine.game_instance import GameInstance


def item_counts(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items or []:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + int(item.get("qty", 1) or 1)
    return counts


def snapshot_public_player_state(instance: GameInstance) -> dict[str, dict]:
    snapshot: dict[str, dict] = {}
    for uid, player, cs in instance.iter_player_sheets():
        snapshot[uid] = {
            "name": player.get("character_name") or cs.get("character_name") or uid,
            "hp": cs.get("hp"),
            "max_hp": cs.get("max_hp"),
            "gold": cs.get("gold"),
            "mana": cs.get("mana"),
            "sanity": cs.get("sanity"),
            "luck": cs.get("luck"),
            "status": cs.get("status"),
            "deceased": bool(cs.get("deceased")),
            "inventory": item_counts(cs.get("inventory", [])),
            "key_items": item_counts(cs.get("key_items", [])),
            "equipment": item_counts(cs.get("equipment", [])),
        }
    return snapshot


def signed_delta(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def format_counter_diff(before: dict[str, int], after: dict[str, int]) -> list[str]:
    changes: list[str] = []
    names = sorted(set(before) | set(after))
    for name in names:
        delta = after.get(name, 0) - before.get(name, 0)
        if delta > 0:
            changes.append(f"获得 {name} x{delta}")
        elif delta < 0:
            changes.append(f"失去 {name} x{abs(delta)}")
    return changes


def quest_status_label(status: str) -> str:
    labels = {
        "active": "进行中",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
        "hidden": "隐藏",
    }
    return labels.get(status, status or "更新")


def build_state_change_messages(instance: GameInstance, before: dict[str, dict], data: dict) -> list[str]:
    """生成玩家可见的状态变动摘要，避免 HP/物品/任务变化只藏在处理日志里。"""
    messages: list[str] = []
    state_update = data.get("state_update", {})
    players_update = state_update.get("players", {})
    loot_players = {item.get("player", "") for item in state_update.get("loot", [])}
    touched_uids = sorted(uid for uid in set(players_update) | loot_players if uid in instance.players)

    numeric_fields = (
        ("hp", "HP"),
        ("gold", "金币"),
        ("mana", "法力"),
        ("sanity", "理智"),
        ("luck", "幸运"),
    )
    for uid in touched_uids:
        old = before.get(uid, {})
        player = instance.players.get(uid, {})
        cs = instance.get_character_sheet(uid)
        name = old.get("name") or player.get("character_name") or cs.get("character_name") or uid
        parts: list[str] = []
        player_update = players_update.get(uid, {})

        for key, label in numeric_fields:
            old_value = old.get(key)
            new_value = cs.get(key)
            if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)) and int(old_value) != int(new_value):
                delta = int(new_value) - int(old_value)
                parts.append(f"{label} {int(old_value)} → {int(new_value)}（{signed_delta(delta)}）")

        if old.get("status") != cs.get("status") and cs.get("status"):
            parts.append(f"状态 → {cs.get('status')}")
        if not old.get("deceased") and cs.get("deceased"):
            parts.append("生死状态 → 死亡")
        elif old.get("deceased") and not cs.get("deceased"):
            parts.append("生死状态 → 复活")

        parts.extend(format_counter_diff(old.get("inventory", {}), item_counts(cs.get("inventory", []))))
        parts.extend(format_counter_diff(old.get("key_items", {}), item_counts(cs.get("key_items", []))))
        parts.extend(format_counter_diff(old.get("equipment", {}), item_counts(cs.get("equipment", []))))

        if parts:
            messages.append(f"【状态变动】{name}：" + "；".join(parts))

    plot_update = data.get("plot_update", {})
    for quest in plot_update.get("quests", []):
        title = str(quest.get("title", "")).strip()
        status = str(quest.get("status", "")).strip()
        if title:
            messages.append(f"【任务更新】{title}：{quest_status_label(status)}")

    return messages
