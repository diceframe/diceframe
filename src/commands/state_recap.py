"""玩家可见状态变化摘要。

这个模块只负责把一轮结算前后的公开状态差异整理成可读文案。
它不修改游戏状态，因此可以作为 GameHandler 的纯辅助层独立测试。
"""

from __future__ import annotations

from src.engine.game_instance import GameInstance
from src.engine.language import localized_text, normalize_language


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
        resources = cs.get("resources") or {}
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
            "rule_resources": {
                key: int(res.get("current", 0) or 0)
                for key, res in resources.items()
                if isinstance(res, dict)
            },
        }
    return snapshot


def signed_delta(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def format_counter_diff(before: dict[str, int], after: dict[str, int], language: str = "zh-CN") -> list[str]:
    changes: list[str] = []
    names = sorted(set(before) | set(after))
    for name in names:
        delta = after.get(name, 0) - before.get(name, 0)
        if delta > 0:
            changes.append(localized_text(language, {
                "en": f"Gained {name} x{delta}",
                "zh-CN": f"获得 {name} x{delta}",
                "ja": f"{name} x{delta} を獲得",
            }))
        elif delta < 0:
            changes.append(localized_text(language, {
                "en": f"Lost {name} x{abs(delta)}",
                "zh-CN": f"失去 {name} x{abs(delta)}",
                "ja": f"{name} x{abs(delta)} を喪失",
            }))
    return changes


def quest_status_label(status: str, language: str = "zh-CN") -> str:
    labels = {
        "en": {
            "active": "Active",
            "completed": "Completed",
            "failed": "Failed",
            "cancelled": "Cancelled",
            "hidden": "Hidden",
        },
        "zh-CN": {
            "active": "进行中",
            "completed": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
            "hidden": "隐藏",
        },
        "ja": {
            "active": "進行中",
            "completed": "完了",
            "failed": "失敗",
            "cancelled": "キャンセル",
            "hidden": "非表示",
        },
    }
    table = labels.get(normalize_language(language)) or labels["zh-CN"]
    return table.get(status, status or localized_text(language, {
        "en": "Updated",
        "zh-CN": "更新",
        "ja": "更新",
    }))


_SANITY_VERDICT_LABELS = {
    "en": {
        "大成功": "critical success", "极难成功": "extreme success", "困难成功": "hard success",
        "普通成功": "regular success", "失败": "failure", "大失败": "fumble",
    },
    "ja": {
        "大成功": "クリティカル", "极难成功": "イクストリーム成功", "困难成功": "ハード成功",
        "普通成功": "レギュラー成功", "失败": "失敗", "大失败": "ファンブル",
    },
}


def sanity_check_message(language: str, name: str, outcome: dict) -> str:
    """公开一次理智检定的骰值、阈值、等级与损失；GM 下一轮从历史里读到它来写反应。"""
    spec = str(outcome.get("loss_spec") or "")
    if outcome.get("invalid"):
        return localized_text(language, {
            "en": f"[Sanity Check] {name}: the loss notation is not recognized; not resolved",
            "zh-CN": f"【理智检定】{name}：损失写法无法识别，未结算",
            "ja": f"【正気度ロール】{name}：喪失の表記を認識できないため、処理しませんでした",
        })
    verdict = str(outcome.get("verdict") or "")
    roll_value = int(outcome.get("roll") or 0)
    threshold = int(outcome.get("threshold") or 0)
    loss = int(outcome.get("loss") or 0)
    labels = _SANITY_VERDICT_LABELS.get(normalize_language(language), {})
    return localized_text(language, {
        "en": f"[Sanity Check] {name}: d100={roll_value} vs SAN {threshold} → "
              f"{_SANITY_VERDICT_LABELS['en'].get(verdict, verdict)}, lost {loss} ({spec})",
        "zh-CN": f"【理智检定】{name}：d100={roll_value} / 理智 {threshold} → {verdict}，损失 {loss}（{spec}）",
        "ja": f"【正気度ロール】{name}：d100={roll_value} / 正気度 {threshold} → "
              f"{labels.get(verdict, verdict)}、喪失 {loss}（{spec}）",
    })


def build_state_change_messages(instance: GameInstance, before: dict[str, dict], data: dict) -> list[str]:
    """生成玩家可见的状态变动摘要，避免 HP/物品/任务变化只藏在处理日志里。"""
    messages: list[str] = []
    language = instance.language
    state_update = data.get("state_update", {})
    players_update = state_update.get("players", {})
    loot_players = {item.get("player", "") for item in state_update.get("loot", [])}
    touched_uids = sorted(uid for uid in set(players_update) | loot_players if uid in instance.players)

    numeric_fields = (
        ("hp", "HP"),
        ("gold", localized_text(language, {"en": "Gold", "zh-CN": "金币", "ja": "金貨"})),
        ("mana", localized_text(language, {"en": "Mana", "zh-CN": "法力", "ja": "マナ"})),
        ("sanity", localized_text(language, {"en": "Sanity", "zh-CN": "理智", "ja": "正気度"})),
        ("luck", localized_text(language, {"en": "Luck", "zh-CN": "幸运", "ja": "幸運"})),
    )
    for uid in touched_uids:
        old = before.get(uid, {})
        player = instance.players.get(uid, {})
        cs = instance.get_character_sheet(uid)
        name = old.get("name") or player.get("character_name") or cs.get("character_name") or uid
        parts: list[str] = []
        player_update = players_update.get(uid, {})
        sanity_outcome = player_update.get("san_check_result") if isinstance(player_update, dict) else None
        if isinstance(sanity_outcome, dict):
            messages.append(sanity_check_message(language, name, sanity_outcome))

        for key, label in numeric_fields:
            old_value = old.get(key)
            new_value = cs.get(key)
            if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)) and int(old_value) != int(new_value):
                delta = int(new_value) - int(old_value)
                parts.append(localized_text(language, {
                    "en": f"{label} {int(old_value)} -> {int(new_value)} ({signed_delta(delta)})",
                    "zh-CN": f"{label} {int(old_value)} → {int(new_value)}（{signed_delta(delta)}）",
                    "ja": f"{label} {int(old_value)} → {int(new_value)}（{signed_delta(delta)}）",
                }))

        # 规则自定义资源（STAT 标签/GM 指令改动）；专属字段已在上面播报，此处跳过
        exclusive = {"hp", "gold", "mana", "sanity", "luck"}
        resources = cs.get("resources") or {}
        old_resources = old.get("rule_resources") or {}
        for key, res in resources.items():
            if key in exclusive or not isinstance(res, dict):
                continue
            old_value = old_resources.get(key)
            new_value = int(res.get("current", 0) or 0)
            if isinstance(old_value, (int, float)) and int(old_value) != new_value:
                label = str(res.get("label") or key)
                delta = new_value - int(old_value)
                parts.append(localized_text(language, {
                    "en": f"{label} {int(old_value)} -> {new_value} ({signed_delta(delta)})",
                    "zh-CN": f"{label} {int(old_value)} -> {new_value}（{signed_delta(delta)}）",
                    "ja": f"{label} {int(old_value)} -> {new_value}（{signed_delta(delta)}）",
                }))

        if old.get("status") != cs.get("status") and cs.get("status"):
            parts.append(localized_text(language, {
                "en": f"Status -> {cs.get('status')}",
                "zh-CN": f"状态 → {cs.get('status')}",
                "ja": f"状態 → {cs.get('status')}",
            }))
        if not old.get("deceased") and cs.get("deceased"):
            parts.append(localized_text(language, {"en": "Life state -> Dead", "zh-CN": "生死状态 → 死亡", "ja": "生死状態 → 死亡"}))
        elif old.get("deceased") and not cs.get("deceased"):
            parts.append(localized_text(language, {"en": "Life state -> Revived", "zh-CN": "生死状态 → 复活", "ja": "生死状態 → 復活"}))

        parts.extend(format_counter_diff(old.get("inventory", {}), item_counts(cs.get("inventory", [])), language))
        parts.extend(format_counter_diff(old.get("key_items", {}), item_counts(cs.get("key_items", [])), language))
        parts.extend(format_counter_diff(old.get("equipment", {}), item_counts(cs.get("equipment", [])), language))

        if parts:
            messages.append(localized_text(language, {
                "en": f"[Status Change] {name}: " + "; ".join(parts),
                "zh-CN": f"【状态变动】{name}：" + "；".join(parts),
                "ja": f"【ステータス変更】{name}：" + "；".join(parts),
            }))

    plot_update = data.get("plot_update", {})
    for quest in plot_update.get("quests", []):
        title = str(quest.get("title", "")).strip()
        status = str(quest.get("status", "")).strip()
        if title:
            messages.append(localized_text(language, {
                "en": f"[Quest Update] {title}: {quest_status_label(status, 'en')}",
                "zh-CN": f"【任务更新】{title}：{quest_status_label(status, 'zh-CN')}",
                "ja": f"【クエスト更新】{title}：{quest_status_label(status, 'ja')}",
            }))

    return messages
