"""NPC 状态更新：NPC 注册、默认战斗属性推断、数量告警。

从 state_update_applier 拆出的 NPC 字段应用逻辑。
"""

from __future__ import annotations

import logging

from src.engine.game_instance import GameInstance

logger = logging.getLogger("trpg")


class NpcStateApplier:
    """将 LLM 输出的 state_update.npcs 部分应用到 NPC 状态。"""

    def apply_npcs(self, instance: GameInstance, npcs_update: dict) -> None:
        for npc_name, nud in npcs_update.items():
            if npc_name in instance.npcs:
                instance.npcs[npc_name].update(nud)
            else:
                # D10: NPC 默认战斗属性（按 tier 和难度推断，避免一击必杀）
                tier = nud.get("tier", nud.get("relation", "neutral"))
                diff = instance.difficulty
                base_hp = {"friendly": 15, "neutral": 20, "hostile": 30, "boss": 60}.get(tier, 20)
                if diff == "硬核":
                    base_hp = int(base_hp * 1.3)
                elif diff == "轻松":
                    base_hp = int(base_hp * 0.7)
                base_armor = {"friendly": 0, "neutral": 1, "hostile": 2, "boss": 5}.get(tier, 1)
                instance.npcs[npc_name] = {
                    "name": npc_name,
                    "character_name": npc_name,
                    "first_seen_round": instance.round_number,
                    "hp": base_hp,
                    "max_hp": base_hp,
                    "armor": base_armor,
                }
                instance.npcs[npc_name].update(nud)
                logger.info("新 NPC 已注册: %s (hp=%d armor=%d)", npc_name, base_hp, base_armor)
        if len(instance.npcs) >= 500:
            logger.warning(
                "NPC 数量已达 %d，建议 DM 手动清理不再需要的 NPC 数据，"
                "或使用 WebUI 角色管理页面删除无用 NPC",
                len(instance.npcs),
            )
