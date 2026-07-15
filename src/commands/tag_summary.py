"""标签解析结果摘要。"""

from __future__ import annotations


def summarize_tags(data: dict) -> dict:
    """从解析后的标签数据中提取标签摘要，供 WebUI 处理日志显示。"""
    tags: list[str] = []
    state_update = data.get("state_update", {})
    players = state_update.get("players", {})
    for uid, player_update in players.items():
        if player_update.get("hp_change"):
            tags.append(f"HP:{uid}:{player_update['hp_change']}")
        if player_update.get("gold_change"):
            tags.append(f"GOLD:{uid}:{player_update['gold_change']}")
        if player_update.get("weapon_change"):
            tags.append(f"WEAPON:{uid}:{player_update['weapon_change']}")
        if player_update.get("equip_gain"):
            tags.append(f"EQUIP:{uid}:{player_update['equip_gain']}")
        if player_update.get("use_item"):
            tags.append(f"USE:{uid}:{player_update['use_item']}")
    for pay in state_update.get("pending_payments", []):
        tags.append(f"PAY:{pay.get('uid', '')}:{pay.get('amount', 0)}")
    if state_update.get("scene_change"):
        tags.append(f"SCENE:{state_update['scene_change']}")
    for name in state_update.get("npcs", {}):
        tags.append(f"NPC:{name}")
    for loot in state_update.get("loot", []):
        tag_name = "KEY_ITEM" if loot.get("category") == "key_item" else "LOOT"
        tags.append(f"{tag_name}:{loot.get('player','')}:{loot.get('item','')}")
    for quest in data.get("plot_update", {}).get("quests", []):
        tags.append(f"QUEST:{quest.get('title','')}:{quest.get('status','')}")
    for decision in data.get("plot_update", {}).get("decisions", []):
        tags.append(f"DECISION:{decision}")
    if data.get("xp_rewards"):
        for uid, xp in data["xp_rewards"].items():
            tags.append(f"XP:{uid}:{xp}")
    return {"tags": tags, "count": len(tags), "has_tags": len(tags) > 0}
