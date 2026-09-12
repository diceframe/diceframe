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
    equip_owned_item,
    unequip_item,
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
            # 物品事件：固定顺序 获得 -> 装备/卸下 -> 使用，再结算其它资源状态。
            self._apply_item_events(instance, uid, cs, pud, rule)
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

    def _apply_item_events(
        self,
        instance: GameInstance,
        uid: str,
        cs: dict,
        pud: dict,
        rule=None,
    ) -> None:
        """结算一轮的物品事件，固定顺序：获得 -> 装备/卸下 -> 使用。

        所有物品事件都是列表，同一轮多条同类标签全部按原始顺序执行，不再互相
        覆盖。旧版单值字段（equip_gain/weapon_change/use_item）作为 compatibility
        input 继续接受；对应列表字段存在时跳过 scalar，避免同轮重复结算。
        """

        item_gains = pud.get("item_gains")
        if isinstance(item_gains, list):
            for gain in item_gains:
                if not isinstance(gain, dict):
                    continue
                name = str(gain.get("name") or "").strip()
                if not name:
                    continue
                try:
                    qty = max(1, min(99, int(gain.get("qty", 1) or 1)))
                except (TypeError, ValueError):
                    qty = 1
                category = str(gain.get("category") or "").strip()
                if category in ("weapon", "equipment"):
                    # 获得武器/装备只进背包：获得 != 装备，绝不自动替换当前装备。
                    add_owned_equipment_to_inventory(cs, name, qty=qty)
                else:
                    append_inventory_item(cs, name, qty=qty)
        equipment_ops = pud.get("equipment_ops")
        if isinstance(equipment_ops, list):
            for op in equipment_ops:
                if not isinstance(op, dict):
                    continue
                name = str(op.get("name") or "").strip()
                if not name:
                    continue
                if op.get("op") == "unequip":
                    unequip_item(cs, name)
                else:
                    custom_damage = op.get("damage")
                    equip_owned_item(
                        cs,
                        name,
                        slot=str(op.get("slot") or ""),
                        custom_damage=custom_damage if isinstance(custom_damage, int) else None,
                        legacy_gain=bool(op.get("legacy")),
                        rule=rule,
                    )
        item_uses = pud.get("item_uses")
        if isinstance(item_uses, list):
            for use in item_uses:
                name = (
                    str(use.get("name") or "").strip()
                    if isinstance(use, dict) else str(use or "").strip()
                )
                if name:
                    self._use_inventory_item(instance, uid, cs, name)
        # ---- legacy 单值字段兼容（旧解析器/外部注入的数据）----
        if "item_gains" not in pud and "equipment_ops" not in pud:
            equip_gain = pud.get("equip_gain")
            if equip_gain:
                # EQUIP 在叙事协议里只代表"获得装备"，不改变当前穿戴。
                add_owned_equipment_to_inventory(cs, str(equip_gain))
            weapon_name = pud.get("weapon_change")
            if weapon_name:
                equip_owned_item(
                    cs, str(weapon_name), slot="main_hand", legacy_gain=True, rule=rule,
                )
        if "item_uses" not in pud:
            use_item = pud.get("use_item")
            if use_item:
                self._use_inventory_item(instance, uid, cs, str(use_item))

    def _use_inventory_item(
        self, instance: GameInstance, uid: str, cs: dict, item_name: str,
    ) -> None:
        """使用一件背包物品：qty -1（永不为负），effect 含 HP 数字时回血。"""

        for item in cs.get("inventory", []):
            if (
                not isinstance(item, dict)
                or str(item.get("name") or "").strip().casefold() != item_name.casefold()
                or int(item.get("qty", 0) or 0) <= 0
            ):
                continue
            item["qty"] = int(item.get("qty", 1) or 1) - 1
            effect = str(item.get("effect") or "")
            if "HP" in effect:
                m = re.search(r"\d+", effect)
                if m:
                    apply_hp_delta(cs, int(m.group()), bounded=False)
            logger.info("道具已使用: %s x %s, HP=%d", item_name, effect, cs.get("hp", 0))
            return
        logger.warning(
            "忽略使用未拥有的物品: uid=%s item=%s round=%d",
            uid, item_name, instance.round_number,
        )
