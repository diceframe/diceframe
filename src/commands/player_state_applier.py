"""玩家状态更新：HP/金币/SAN/LUCK/MANA/装备/道具/施法/死亡等字段写入。

从 state_update_applier 拆出的玩家角色字段应用逻辑。
"""

from __future__ import annotations

import logging
import re

from src.engine.dice import check_d100, roll as dice_roll
from src.engine.character_utils import (
    apply_bounded_stat_delta,
    apply_currency_delta,
    apply_hp_delta,
    bounded_hp_delta,
    sync_death_from_hp,
)
from src.engine.game_instance import GameInstance
from src.commands.madness_tracker import MadnessTracker
from src.commands.state_items import append_unique_equipment

logger = logging.getLogger("trpg")


class PlayerStateApplier:
    """将 LLM 输出的 state_update.players 部分应用到玩家角色。"""

    def __init__(self, madness: MadnessTracker):
        self._madness = madness

    def apply_players(self, instance: GameInstance, players_update: dict) -> None:
        for uid, pud in players_update.items():
            if uid in instance.players:
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
                    apply_currency_delta(cs, gold_change)
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
                    dmg = pud.get("weapon_damage", 3)
                    eq = cs.get("equipment", [])
                    replaced = False
                    for e in eq:
                        if e.get("slot") == "main_hand":
                            e["name"] = weapon_name
                            e["damage"] = dmg
                            replaced = True
                            break
                    if not replaced:
                        eq.append({"name": weapon_name, "type": "weapon", "damage": dmg, "slot": "main_hand", "quality": "common"})
                equip_gain = pud.get("equip_gain")
                if equip_gain:
                    append_unique_equipment(cs, equip_gain)
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
                    actual_loss = (full_loss + 1) // 2 if san_verdict == "成功" else full_loss
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
                # 死亡检测
                if sync_death_from_hp(cs, instance.round_number):
                    logger.info("%s 已死亡 (round=%d, hp=%d)",
                                instance.players[uid].get("character_name", uid),
                                instance.round_number, cs.get("hp", 0))
                instance.set_character_sheet(uid, cs)
