"""战斗结算解析器。

从 game_handler 拆出的多人战斗结算与先攻初始化逻辑。
"""

from __future__ import annotations

import logging

from src.engine.combat import AttackResult, resolve_attack
from src.engine.constants import WEAPON_DAMAGE
from src.engine.dice import roll_initiative
from src.engine.game_instance import GameInstance

logger = logging.getLogger("trpg")


class CombatResolver:
    """处理战斗目标识别、攻击结算和先攻顺序。"""

    def resolve_combat(self, instance: GameInstance, actions_text: str, combat_model: str) -> str:
        """检测战斗意图，为所有攻击者结算，返回结构化结算文本。"""

        # 寻找目标（敌人 > NPC > 其他玩家）
        target = None
        target_name = "目标"
        for enemy in instance.combat_enemies:
            enemy_name = enemy.get("character_name", enemy.get("name", ""))
            if enemy_name and enemy_name in actions_text:
                target = enemy
                target_name = enemy_name
                break
        if target is None:
            for npc_name in instance.npcs:
                if npc_name in actions_text:
                    target = instance.npcs[npc_name]
                    target_name = npc_name
                    break
        if target is None:
            for uid, pdata, cs in instance.iter_player_sheets():
                char_name = pdata.get("character_name", "")
                if char_name and char_name in actions_text and uid != "web_user":
                    target = cs or {"name": char_name, "hp": 10, "armor": 0}
                    target["character_name"] = char_name
                    target_name = char_name
                    break
        if target is None:
            return ""

        # 查找所有攻击者
        results = []
        attacker_uids_seen: set[str] = set()
        for uid, pdata, cs in instance.iter_player_sheets():
            char_name = pdata.get("character_name", "")
            if not char_name or char_name not in actions_text:
                continue
            if uid in attacker_uids_seen:
                continue
            attacker_uids_seen.add(uid)

            # 武器
            weapon = None
            weapon_name = "徒手"
            for eq in cs.get("equipment", []):
                if eq.get("slot") == "main_hand":
                    weapon = {"name": eq.get("name", "徒手"), "damage": eq.get("damage", 2)}
                    weapon_name = eq.get("name", "徒手")
                    break
            if weapon is None:
                for wname in sorted(WEAPON_DAMAGE, key=lambda x: -len(x)):
                    if wname in actions_text:
                        weapon_name = wname
                        weapon = {"name": wname, "damage": WEAPON_DAMAGE[wname]}
                        break

            attr_value = cs.get("attributes", {}).get("str", 10)

            # PvP: 检查友军伤害
            attacker_faction = cs.get("faction", "party")
            target_faction = ""
            if target_name in instance.players:
                target_faction = instance.get_character_sheet(target_name).get("faction", "party")
            same_faction = attacker_faction and attacker_faction == target_faction

            result = resolve_attack(
                attacker_name=char_name,
                target=target,
                weapon=weapon,
                attr_value=attr_value,
                combat_model=combat_model,
                difficulty=instance.difficulty,
            )
            # 友军伤害减半
            if same_faction and result.damage > 0:
                result = AttackResult(
                    attacker=result.attacker,
                    target=result.target,
                    damage=result.damage // 2,
                    actual_damage=result.actual_damage // 2,
                    target_hp_before=result.target_hp_before,
                    target_hp_after=target.get("hp", result.target_hp_after),
                    description=result.description + " (友军伤害减半)",
                    dice=result.dice,
                )

            results.append((char_name, weapon_name, result))
            instance.pending_combat_results.append({
                "attacker": char_name,
                "target": target_name,
                "weapon": weapon_name,
                "damage": result.actual_damage,
                "target_hp_before": result.target_hp_before,
                "target_hp_after": result.target_hp_after,
                "description": result.description,
                "round": instance.round_number,
            })

        if not results:
            return ""

        lines = ["【系统战斗结算·必须遵循】"]
        for attacker_name, weapon_name, result in results:
            lines.append(f"{attacker_name}持{weapon_name}攻击{target_name}")
            if combat_model == "hp_based" and result.dice:
                lines.append(
                    f"  d20={result.dice.natural} → "
                    f"{'命中' if result.damage > 0 else '未命中'}, 伤害={result.damage}"
                )
            else:
                lines.append(f"  伤害={result.damage}")
            if result.dice and result.dice.is_critical:
                lines.append("  ⚡ 大成功！")
            if result.target_hp_after <= 0:
                lines.append(f"  💀 {target_name} 倒地！")

        logger.info("多人战斗结算: %d attackers → %s", len(results), target_name)
        return "\n".join(lines)

    def initiate_combat(self, instance: GameInstance) -> str:
        """初始化战斗先攻顺序。返回战斗开始公告文本。"""
        combatants: list[tuple[str, int]] = []

        for uid in instance.alive_players:
            cs = instance.get_character_sheet(uid)
            dex = cs.get("attributes", {}).get("dex", 10)
            init = roll_initiative((dex - 10) // 2)
            combatants.append((uid, init.total))
            logger.info(
                "先攻: %s dex=%d roll=%d",
                instance.players[uid].get("character_name", uid),
                dex,
                init.total,
            )

        for enemy in instance.combat_enemies:
            eid = enemy.get("name", enemy.get("character_name", "敌人"))
            dex = enemy.get("character_sheet", {}).get("attributes", {}).get("dex", 10)
            init = roll_initiative((dex - 10) // 2)
            combatants.append((eid, init.total))

        combatants.sort(key=lambda x: -x[1])
        instance.initiative_order = [c[0] for c in combatants]
        instance.initiative_current = 0
        instance.combat_state = "active"
        instance.combat_active = True

        order_text = " → ".join(
            f"{instance.players[uid].get('character_name', uid)}({score})"
            if uid in instance.players else f"{uid}({score})"
            for uid, score in combatants
        )
        logger.info("战斗开始: order=%s", order_text)
        return f"⚔ 战斗开始！先攻顺序: {order_text}"
