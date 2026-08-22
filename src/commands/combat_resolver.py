"""战斗结算解析器。

攻击命中只消费本轮统一检定产生的 ``CheckResult``；本模块
不存在 d20/d100 命中掷骰的兼容回退。
"""

from __future__ import annotations

import logging
from typing import Any

from src.engine.combat import AttackResult, resolve_attack
from src.engine.constants import WEAPON_DAMAGE
from src.engine.dice import roll_initiative
from src.engine.game_instance import GameInstance
from src.engine.language import localized_text

logger = logging.getLogger("trpg")


class CombatResolver:
    """处理战斗目标、权威检定消费、伤害和先攻顺序。"""

    @staticmethod
    def _authoritative_attack_check(
        instance: GameInstance,
        action: dict[str, Any],
        actor_uid: str,
    ) -> dict[str, Any] | None:
        """严格按 ``check_id + actor_uid + kind=attack`` 定位唯一结果。"""
        request = action.get("check_request")
        if not isinstance(request, dict):
            return None
        check_id = str(request.get("check_id") or "")
        if (
            not check_id
            or str(request.get("actor_uid") or "") != actor_uid
            or str(request.get("kind") or "") != "attack"
        ):
            return None
        matches = [
            check
            for check in instance.last_checks
            if str(check.get("check_id") or "") == check_id
            and str(check.get("actor_uid") or "") == actor_uid
            and str(check.get("kind") or "") == "attack"
        ]
        if len(matches) != 1:
            logger.warning(
                "攻击检定无法唯一匹配，已拒绝伤害结算: actor=%s check=%s matches=%d",
                actor_uid,
                check_id,
                len(matches),
            )
            return None
        check = matches[0]
        request_opponent = str(request.get("opponent") or "")
        result_opponent = str(check.get("opponent") or "")
        if request_opponent != result_opponent:
            logger.warning(
                "攻击检定目标引用不一致，已拒绝伤害结算: check=%s request=%s result=%s",
                check_id,
                request_opponent,
                result_opponent,
            )
            return None
        return check

    @staticmethod
    def _target_from_ref(
        instance: GameInstance,
        target_ref: str,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """返回 ``(target state, display name, player uid)``。"""
        if target_ref in instance.players:
            sheet = instance.get_character_sheet(target_ref)
            name = str(instance.players[target_ref].get("character_name") or target_ref)
            return sheet, name, target_ref
        if target_ref.startswith("npc:"):
            npc_id = target_ref.split(":", 1)[1]
            npc = instance.npcs.get(npc_id)
            if isinstance(npc, dict):
                name = str(npc.get("character_name") or npc.get("name") or npc_id)
                return npc, name, ""
            return None, "", ""
        if target_ref.startswith("enemy:"):
            try:
                index = int(target_ref.split(":", 1)[1])
                if index < 0:
                    return None, "", ""
                enemy = instance.combat_enemies[index]
            except (ValueError, IndexError):
                return None, "", ""
            name = str(enemy.get("character_name") or enemy.get("name") or f"敌人{index + 1}")
            state = (
                enemy.get("character_sheet")
                if "hp" not in enemy and isinstance(enemy.get("character_sheet"), dict)
                else enemy
            )
            return state, name, ""
        return None, "", ""

    @staticmethod
    def _weapon(character_sheet: dict[str, Any], action_text: str) -> tuple[dict[str, Any] | None, str]:
        for equipment in character_sheet.get("equipment", []) or []:
            if equipment.get("slot") == "main_hand":
                name = str(equipment.get("name") or "徒手")
                return {"name": name, "damage": equipment.get("damage", 2)}, name
        for name in sorted(WEAPON_DAMAGE, key=lambda value: -len(value)):
            if name in action_text:
                return {"name": name, "damage": WEAPON_DAMAGE[name]}, name
        return None, "徒手"

    @staticmethod
    def _record_result(instance: GameInstance, result: AttackResult) -> None:
        """保持旧字段并补齐多人结算引用；同 check_id 不重复追加。"""
        if any(
            str(item.get("check_id") or "") == result.check_id
            for item in instance.pending_combat_results
        ):
            return
        instance.record_combat_result({
            "attacker": result.attacker,
            "attacker_uid": result.attacker_uid,
            "target": result.target,
            "target_ref": result.target_ref,
            "weapon": result.weapon,
            "check_id": result.check_id,
            "verdict": result.verdict,
            "is_critical": result.is_critical,
            "is_fumble": result.is_fumble,
            "damage": result.actual_damage,
            "actual_damage": result.actual_damage,
            "target_hp_before": result.target_hp_before,
            "target_hp_after": result.target_hp_after,
            "description": result.description,
            "round": instance.round_number,
        })

    def resolve_combat(self, instance: GameInstance, actions_text: str, combat_model: str) -> str:
        """只结算有唯一权威攻击 ``CheckResult`` 的行动。"""
        del actions_text  # 禁止从多人汇总文本猜攻击者、武器或目标。
        results: list[tuple[AttackResult, dict[str, Any]]] = []

        for action in list(instance.action_queue):
            actor_uid = str(action.get("user_id") or "")
            if actor_uid not in instance.players or not instance.is_alive(actor_uid):
                continue
            check = self._authoritative_attack_check(instance, action, actor_uid)
            if check is None:
                continue

            target_ref = str(check.get("opponent") or "")
            target, target_name, target_uid = self._target_from_ref(instance, target_ref)
            if target is None:
                logger.warning(
                    "攻击检定缺少可解析目标，已拒绝伤害结算: actor=%s check=%s opponent=%s",
                    actor_uid,
                    check.get("check_id"),
                    target_ref,
                )
                continue

            player = instance.players[actor_uid]
            character_sheet = instance.get_character_sheet(actor_uid)
            attacker_name = str(player.get("character_name") or actor_uid)
            weapon, weapon_name = self._weapon(character_sheet, str(action.get("text") or ""))
            attr_value = int(character_sheet.get("attributes", {}).get("str", 10) or 10)

            attacker_faction = str(character_sheet.get("faction") or "party")
            target_faction = ""
            if target_uid:
                target_faction = str(instance.get_character_sheet(target_uid).get("faction") or "party")
            same_faction = bool(attacker_faction and target_faction and attacker_faction == target_faction)

            cached = action.get("combat_outcome")
            if isinstance(cached, dict):
                try:
                    result = AttackResult.from_record(cached)
                except (TypeError, ValueError):
                    logger.warning(
                        "攻击缓存格式无效，已拒绝重放: actor=%s check=%s",
                        actor_uid,
                        check.get("check_id"),
                    )
                    continue
                if (
                    result.check_id != str(check.get("check_id") or "")
                    or result.attacker_uid != actor_uid
                    or result.target_ref != target_ref
                ):
                    logger.warning(
                        "攻击缓存引用不一致，已拒绝重放: actor=%s check=%s",
                        actor_uid,
                        check.get("check_id"),
                    )
                    continue
            else:
                result = resolve_attack(
                    attacker_name=attacker_name,
                    target=target,
                    weapon=weapon,
                    attr_value=attr_value,
                    combat_model=combat_model,
                    difficulty=instance.difficulty,
                    check_result=check,
                    same_faction=same_faction,
                    attacker_uid=actor_uid,
                    target_ref=target_ref,
                    target_name=target_name,
                )
                # 战斗 outcome 属于行动的下游状态，不回写或改动
                # 已形成的 CheckResult。进程重试时复用它，既不重掷
                # 命中骰，也不重复扣 HP。
                action["combat_outcome"] = result.to_record()

            # 旧记录中可能没有 weapon；仅在内存恢复时填上显示值。
            if not result.weapon:
                result.weapon = weapon_name
            self._record_result(instance, result)
            results.append((result, check))

        if not results:
            return ""

        lines = [localized_text(instance.language, {
            "zh-CN": "【系统战斗结算·必须遵循】",
            "en": "[System Combat Resolution - Must Follow]",
            "ja": "【システム戦闘結算・必ず従うこと】",
        })]
        for result, check in results:
            lines.append(localized_text(instance.language, {
                "zh-CN": f"{result.attacker}持{result.weapon}攻击{result.target}",
                "en": f"{result.attacker} attacks {result.target} with {result.weapon}",
                "ja": f"{result.attacker}は{result.weapon}で{result.target}を攻撃した",
            }))
            dice_name = str(check.get("dice") or "")
            roll_value = int(check.get("roll", 0) or 0)
            lines.append(localized_text(instance.language, {
                "zh-CN": (
                    f"  {dice_name}={roll_value} → {result.verdict}，"
                    f"最终伤害={result.actual_damage}"
                ),
                "en": (
                    f"  {dice_name}={roll_value} → {result.verdict}, "
                    f"final damage={result.actual_damage}"
                ),
                "ja": (
                    f"  {dice_name}={roll_value} → {result.verdict}、"
                    f"最終ダメージ={result.actual_damage}"
                ),
            }))
            if result.is_critical:
                lines.append(localized_text(instance.language, {
                    "zh-CN": "  ⚡ 大成功！",
                    "en": "  ⚡ Critical!",
                    "ja": "  ⚡ 大成功！",
                }))
            if result.target_hp_after <= 0 and result.target_hp_before > 0:
                lines.append(localized_text(instance.language, {
                    "zh-CN": f"  💀 {result.target} 倒地！",
                    "en": f"  💀 {result.target} is down!",
                    "ja": f"  💀 {result.target} は倒れた！",
                }))

        logger.info(
            "多人战斗结算: %d attacks, checks=%s",
            len(results),
            [result.check_id for result, _ in results],
        )
        return "\n".join(lines)

    def initiate_combat(self, instance: GameInstance) -> str:
        """初始化战斗先攻顺序。返回战斗开始公告文本。"""
        combatants: list[tuple[str, int]] = []

        for uid in instance.alive_players:
            character_sheet = instance.get_character_sheet(uid)
            dexterity = character_sheet.get("attributes", {}).get("dex", 10)
            initiative = roll_initiative((dexterity - 10) // 2)
            combatants.append((uid, initiative.total))
            logger.info(
                "先攻: %s dex=%d roll=%d",
                instance.players[uid].get("character_name", uid),
                dexterity,
                initiative.total,
            )

        for enemy in instance.combat_enemies:
            enemy_id = enemy.get("name", enemy.get("character_name", "敌人"))
            dexterity = enemy.get("character_sheet", {}).get("attributes", {}).get("dex", 10)
            initiative = roll_initiative((dexterity - 10) // 2)
            combatants.append((enemy_id, initiative.total))

        combatants.sort(key=lambda item: -item[1])
        instance.begin_combat([combatant[0] for combatant in combatants])

        order_text = " → ".join(
            f"{instance.players[uid].get('character_name', uid)}({score})"
            if uid in instance.players else f"{uid}({score})"
            for uid, score in combatants
        )
        logger.info("战斗开始: order=%s", order_text)
        return f"⚔ 战斗开始！先攻顺序: {order_text}"
