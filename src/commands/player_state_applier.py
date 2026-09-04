"""玩家状态更新：HP/金币/SAN/LUCK/MANA/装备/道具/施法/死亡等字段写入。

从 state_update_applier 拆出的玩家角色字段应用逻辑。
"""

from __future__ import annotations

import logging
import re

from src.engine.dice import check_d100, roll as dice_roll
from src.engine.character_utils import (
    apply_bounded_stat_delta,
    apply_hp_delta,
    apply_resource_delta,
    bounded_hp_delta,
    get_resource,
    sync_death_from_hp,
    wake_character,
)
from src.engine.game_instance import GameInstance
from src.commands.madness_tracker import MadnessTracker
from src.commands.resource_triggers import check_resource_triggers
from src.commands.state_items import (
    add_owned_equipment_to_inventory,
    append_inventory_item,
    equipment_entry,
)

logger = logging.getLogger("trpg")


class PlayerStateApplier:
    """将 LLM 输出的 state_update.players 部分应用到玩家角色。"""

    def __init__(self, madness: MadnessTracker):
        self._madness = madness

    def apply_players(
        self,
        instance: GameInstance,
        players_update: dict,
        rule=None,
        allowed_player_uids: set | None = None,
    ) -> None:
        for uid, pud in players_update.items():
            if uid not in instance.players:
                continue
            # 多人局权威白名单：状态变更只允许作用于本轮行动者/参战者，
            # 挡住“玩家诱导 GM 修改他人状态”；None 表示不限制（单人局/离线路径）。
            if allowed_player_uids is not None and uid not in allowed_player_uids:
                logger.warning(
                    "多人局状态标签目标越权，已丢弃: uid=%s round=%d", uid, instance.round_number,
                )
                continue
            cs = instance.get_character_sheet(uid)
            hp_change = pud.get("hp_change")
            if isinstance(hp_change, (int, float)):
                max_hp = cs.get("max_hp", 100)
                bounded_change = bounded_hp_delta(cs, hp_change)
                # D8: 按 max_hp 限制单次变更（伤害≤max_hp，治疗≤max_hp//2）
                if hp_change < 0 and bounded_change != int(hp_change):
                    logger.warning("HP 伤害 %.0f 超 max_hp %d，截断", hp_change, max_hp)
                elif hp_change > 0 and bounded_change != int(hp_change):
                    logger.warning("HP 治疗 %.0f 超 max_hp//2 %d，截断", hp_change, max_hp // 2)
                apply_hp_delta(cs, bounded_change, bounded=False)
            gold_change = pud.get("gold_change")
            if isinstance(gold_change, (int, float)):
                # Narrative output is not an economic authority. Legacy GOLD
                # tags are converted to economy proposals by the parser; an
                # injected raw field is ignored rather than bypassing approval.
                logger.warning(
                    "忽略未经经济事务授权的 gold_change: uid=%s round=%d",
                    uid,
                    instance.round_number,
                )
            if "status" in pud:
                cs["status"] = pud["status"]
            # 使用道具
            use_item = pud.get("use_item")
            if use_item:
                inv = cs.get("inventory", [])
                for item in inv:
                    if item.get("name") == use_item and item.get("qty", 0) > 0:
                        item["qty"] -= 1
                        effect = item.get("effect", "")
                        if "HP" in effect:
                            m = re.search(r"\d+", effect)
                            if m:
                                heal = int(m.group())
                                apply_hp_delta(cs, heal, bounded=False)
                        logger.info("道具已使用: %s x %s, HP=%d", use_item, effect, cs["hp"])
                        break
            # 切换武器
            weapon_name = pud.get("weapon_change")
            if weapon_name:
                requested = str(weapon_name).strip()
                inventory = cs.setdefault("inventory", [])
                owned_index = next(
                    (
                        index for index, item in enumerate(inventory)
                        if isinstance(item, dict)
                        and str(item.get("name") or "").strip().casefold()
                        == requested.casefold()
                        and int(item.get("qty", 1) or 1) > 0
                    ),
                    None,
                )
                if owned_index is None:
                    logger.warning(
                        "忽略未拥有武器的装备切换: uid=%s weapon=%s round=%d",
                        uid, requested, instance.round_number,
                    )
                else:
                    owned = inventory[owned_index]
                    owned["qty"] = int(owned.get("qty", 1) or 1) - 1
                    if owned["qty"] <= 0:
                        inventory.pop(owned_index)
                    eq = cs.setdefault("equipment", [])
                    previous = next(
                        (item for item in eq if isinstance(item, dict) and item.get("slot") == "main_hand"),
                        None,
                    )
                    if previous is not None:
                        append_inventory_item(
                            cs,
                            str(previous.get("name") or "").strip(),
                            quality=str(previous.get("quality") or "common"),
                            category="equipment",
                        )
                    eq[:] = [item for item in eq if item is not previous]
                    equipped = equipment_entry(requested)
                    equipped["slot"] = "main_hand"
                    eq.append(equipped)
            equip_gain = pud.get("equip_gain")
            if equip_gain:
                # EQUIP is an acquisition marker in the narrative protocol,
                # not an instruction to replace the active loadout.  Explicit
                # WEAPON/equip actions are the only path that changes slots.
                add_owned_equipment_to_inventory(cs, str(equip_gain))
            # 法力变化
            mana_change = pud.get("mana_change")
            if isinstance(mana_change, (int, float)):
                apply_bounded_stat_delta(cs, "mana", mana_change)
                logger.info("法力变化: %s %+d -> %d", uid, int(mana_change), cs["mana"])
            # 理智值变化
            san_change = pud.get("san_change")
            if isinstance(san_change, (int, float)):
                prev = cs.get("sanity", 99)
                apply_bounded_stat_delta(
                    cs, "sanity", san_change,
                    default_current=99, max_key="max_sanity", default_max=99,
                )
                logger.info("理智值变化: %s %+d -> %d", uid, int(san_change), cs["sanity"])
                self._madness.apply_madness(instance, uid, cs, prev - cs["sanity"])
            # 理智检定
            san_check_loss = pud.get("san_check_loss")
            if san_check_loss:
                current_san = cs.get("sanity", 50)
                san_res, san_verdict = check_d100(current_san)
                try:
                    loss_dice = dice_roll(san_check_loss)
                    full_loss = abs(loss_dice.total)
                except Exception:
                    full_loss = 6
                actual_loss = (full_loss + 1) // 2 if san_verdict in ("成功", "大成功") else full_loss
                prev = cs.get("sanity", 99)
                apply_bounded_stat_delta(
                    cs, "sanity", -actual_loss,
                    default_current=current_san, max_key="max_sanity", default_max=99,
                )
                logger.info("理智检定: %s d100=%d ≤ san=%d? %s, 损失=%d",
                            uid, san_res.natural, current_san, san_verdict, actual_loss)
                self._madness.apply_madness(instance, uid, cs, prev - cs["sanity"])
            # 幸运值变化
            luck_change = pud.get("luck_change")
            if isinstance(luck_change, (int, float)):
                apply_bounded_stat_delta(
                    cs, "luck", luck_change,
                    default_current=99, max_key="max_luck", default_max=99,
                )
                logger.info("幸运值变化: %s %+d -> %d", uid, int(luck_change), cs["luck"])
            # 推动检定
            push_skill = pud.get("push_skill")
            if push_skill:
                skills: list[dict] = cs.get("skills", [])
                for s in skills:
                    if s.get("name") == push_skill:
                        sv = s.get("value", 20)
                        push_res, push_verdict = check_d100(sv)
                        pushed_key = f"_pushed_{push_skill}"
                        pushed_rounds = cs.get(pushed_key, 0)
                        if pushed_rounds >= instance.round_number:
                            break  # 本轮已推动过此技能
                        cs[pushed_key] = instance.round_number
                        # 推动失败：后果加倍
                        fail_suffix = ""
                        if push_verdict in ("失败", "大失败"):
                            fail_suffix = " (推动失败，后果加倍！)"
                        logger.info("推动检定: %s 推动技能 %s d100=%d vs %d -> %s%s",
                                    instance.players[uid].get("character_name", uid),
                                    push_skill, push_res.natural, sv, push_verdict, fail_suffix)
                        break
            # 施法
            cast_spell = pud.get("cast_spell")
            if cast_spell:
                spells = cs.setdefault("spells_known", [])
                if cast_spell not in spells:
                    spells.append(cast_spell)
                # 默认施法消耗 5 点法力
                if "mana" not in cs:
                    cs["mana"] = cs.get("int", 10) * 3
                apply_bounded_stat_delta(cs, "mana", -5)
                logger.info("施法: %s cast %s, mana=%d", uid, cast_spell, cs["mana"])
            # 规则自定义资源（STAT 标签）：只结算角色卡上已存在的资源，钳制上下限
            stat_changes = pud.get("stat_changes")
            if isinstance(stat_changes, dict) and stat_changes:
                for stat_key, delta in stat_changes.items():
                    if not isinstance(delta, (int, float)) or int(delta) == 0:
                        continue
                    stat_key = str(stat_key)
                    if get_resource(cs, stat_key) is None and stat_key not in cs:
                        logger.warning("STAT 资源不在角色卡上，已忽略: %s %s", uid, stat_key)
                        continue
                    after = apply_resource_delta(cs, stat_key, int(delta), rule)
                    logger.info("规则资源变化: %s %s %+d -> %d", uid, stat_key, int(delta), after)
                check_resource_triggers(instance, uid, rule)
            # 死亡检测（治疗先苏醒，HP 归零再按规则落昏迷/死亡）
            wake_character(cs)
            if sync_death_from_hp(cs, instance.round_number, rule):
                logger.info("%s 已死亡 (round=%d, hp=%d)",
                            instance.players[uid].get("character_name", uid),
                            instance.round_number, cs.get("hp", 0))
            instance.set_character_sheet(uid, cs)
