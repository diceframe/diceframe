"""玩家状态更新：HP/金币/SAN/LUCK/MANA/装备/道具/施法/死亡等字段写入。

从 state_update_applier 拆出的玩家角色字段应用逻辑。
"""

from __future__ import annotations

import logging
import re

from src.engine.dice_rules_coc import parse_sanity_loss, resolve_sanity_check
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
from src.engine.modules import progression_state
from src.rulesets import resource_recovery

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
        health_runtime=None,
    ) -> dict[str, frozenset[str]]:
        """应用玩家字段更新，并汇总每个角色本轮真正变化的 live state 域。

        返回值只说明"哪一类角色事实动过"，不含任何规则数值；调用方据此在本轮
        全部 mutation 完成后，对同一角色只触发一次 ruleset reconciliation。
        没有实际变化的角色不会出现在返回值里。
        """

        # Validate before touching any player's sheet. An unknown recovery
        # policy must not silently restore the unrestricted legacy tag path.
        recovery_policy = resource_recovery.policy(rule)
        managed_resources = resource_recovery.managed_resources(rule)

        def accepts_narrative_delta(cs: dict, key: str, delta: int | float) -> bool:
            if recovery_policy is None:
                return True
            if key not in managed_resources or (key not in cs and get_resource(cs, key) is None):
                logger.warning("忽略未声明或角色不具备的恢复资源: %s", key)
                return False
            if delta > 0:
                logger.info("忽略未经恢复事务授权的正向资源标签: %s %+g", key, delta)
                return False
            return True

        changed_domains: dict[str, frozenset[str]] = {}
        for uid, pud in players_update.items():
            if uid not in instance.players:
                continue
            # 多人局权威白名单：状态变更只允许作用于本轮行动者/参战者，
            # 挡住“玩家诱导 GM 修改他人状态”；None 表示不限制（单人局/离线路径）。
            if allowed_player_uids is not None and uid not in allowed_player_uids:
                # fail closed：白名单只包含本轮行动者/参战者。过去对 san_check_loss
                # 额外放行 instance.alive_players，但「活着」不等于「在场目睹」——
                # GM 输出（或被玩家诱导的模型）可以借此替不在场的人掷 SAN。
                # 在拿到可信的在场/目击状态之前，越权目标一律丢弃，包括 SAN；
                # 旁观者 SAN 建模是独立任务，不在这里用宽松条件凑。
                if pud.get("san_check_loss"):
                    logger.warning(
                        "多人局理智检定目标不在本轮参与者内，已按 fail closed 丢弃: uid=%s round=%d",
                        uid, progression_state.round_value(instance),
                    )
                else:
                    logger.warning(
                        "多人局状态标签目标越权，已丢弃: uid=%s round=%d", uid, progression_state.round_value(instance),
                    )
                continue
            cs = instance.get_character_sheet(uid)
            hp_before = int(cs.get("hp", 0) or 0)
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
                    progression_state.round_value(instance),
                )
            if "status" in pud:
                cs["status"] = pud["status"]
            # 物品事件：固定顺序 获得 -> 装备/卸下 -> 使用，再结算其它资源状态。
            domains = self._apply_item_events(instance, uid, cs, pud, rule)
            # 法力变化
            mana_change = pud.get("mana_change")
            if isinstance(mana_change, (int, float)):
                apply_bounded_stat_delta(cs, "mana", mana_change)
                logger.info("法力变化: %s %+d -> %d", uid, int(mana_change), cs["mana"])
            # 理智值变化
            san_change = pud.get("san_change")
            if isinstance(san_change, (int, float)) and accepts_narrative_delta(cs, "sanity", san_change):
                prev = cs.get("sanity", (get_resource(cs, "sanity") or {}).get("current", 99)
                              if recovery_policy is not None else 99)
                apply_bounded_stat_delta(
                    cs, "sanity", san_change,
                    default_current=prev,
                    max_key="max_sanity", default_max=99,
                )
                logger.info("理智值变化: %s %+d -> %d", uid, int(san_change), cs["sanity"])
                self._madness.apply_madness(instance, uid, cs, prev - cs["sanity"])
            # 理智检定：服务端掷 d100 对当前理智值，按成功等级结算损失。
            san_check_loss = pud.get("san_check_loss")
            # 死亡角色不再掷 SAN：白名单只证明「本轮参与者」，不证明「还活着」。
            if san_check_loss and not instance.is_alive(uid):
                logger.warning("已死亡角色不接受理智检定，已丢弃: uid=%s", uid)
                san_check_loss = None
            if san_check_loss and accepts_narrative_delta(cs, "sanity", -1):
                current_san = cs.get("sanity", (get_resource(cs, "sanity") or {}).get("current", 50)
                                     if recovery_policy is not None else 50)
                try:
                    loss_spec = parse_sanity_loss(san_check_loss)
                except ValueError:
                    # 不再静默按 6 点结算：写法无法识别就不结算，并公开说明。
                    logger.warning("理智检定损失写法无法识别，未结算: %s %r", uid, san_check_loss)
                    # 原文只进日志：GM 可能把怪物名之类的秘密写进了标签，不能原样公开。
                    pud["san_check_result"] = {"invalid": True}
                else:
                    outcome = resolve_sanity_check(int(current_san), loss_spec)
                    prev = cs.get("sanity", current_san if recovery_policy is not None else 99)
                    apply_bounded_stat_delta(
                        cs, "sanity", -outcome["loss"],
                        default_current=current_san, max_key="max_sanity", default_max=99,
                    )
                    logger.info("理智检定: %s d100=%d / san=%d %s, 损失=%d (%s)",
                                uid, outcome["roll"], outcome["threshold"], outcome["verdict"],
                                outcome["loss"], outcome["loss_spec"])
                    pud["san_check_result"] = outcome
                    self._madness.apply_madness(instance, uid, cs, prev - cs["sanity"])
            # 幸运值变化
            luck_change = pud.get("luck_change")
            if isinstance(luck_change, (int, float)) and accepts_narrative_delta(cs, "luck", luck_change):
                apply_bounded_stat_delta(
                    cs, "luck", luck_change,
                    default_current=(get_resource(cs, "luck") or {}).get("current", 99)
                    if recovery_policy is not None else 99,
                    max_key="max_luck", default_max=99,
                )
                logger.info("幸运值变化: %s %+d -> %d", uid, int(luck_change), cs["luck"])
            # Legacy LLM PUSH tags are untrusted proposals. Only the linked
            # player/Keeper decision workflow may re-roll a failed check.
            if pud.get("push_skill"):
                logger.info("Ignored unconfirmed LLM Push proposal: uid=%s", uid)
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
                    if stat_key in {"sanity", "luck"} and not accepts_narrative_delta(cs, stat_key, delta):
                        continue
                    if get_resource(cs, stat_key) is None and stat_key not in cs:
                        logger.warning("STAT 资源不在角色卡上，已忽略: %s %s", uid, stat_key)
                        continue
                    if recovery_policy is not None and stat_key in managed_resources:
                        # Match SAN/LUCK's normal sheet authority. A stale
                        # resources projection must not turn a loss into gain.
                        before = cs.get(stat_key, (get_resource(cs, stat_key) or {}).get('current', 0))
                        after = apply_bounded_stat_delta(cs, stat_key, int(delta), default_current=before,
                                                         max_key='max_' + stat_key, default_max=99)
                    else:
                        before = (get_resource(cs, stat_key) or {}).get('current', cs.get(stat_key, 0))
                        after = apply_resource_delta(cs, stat_key, int(delta), rule)
                    if recovery_policy is not None and stat_key == 'sanity' and after < before:
                        self._madness.apply_madness(instance, uid, cs, before - after)
                    logger.info("规则资源变化: %s %s %+d -> %d", uid, stat_key, int(delta), after)
                check_resource_triggers(instance, uid, rule)
            # 死亡检测（治疗先苏醒，HP 归零再按规则落昏迷/死亡）
            wake_character(cs)
            if sync_death_from_hp(cs, progression_state.round_value(instance), rule):
                logger.info("%s 已死亡 (round=%d, hp=%d)",
                            instance.players[uid].get("character_name", uid),
                            progression_state.round_value(instance), cs.get("hp", 0))
            instance.set_character_sheet(uid, cs)
            if health_runtime is not None:
                accepted = health_runtime.apply_narrative_hp_delta(
                    instance, str(uid), int(cs.get("hp", 0) or 0) - hp_before,
                )
                if "hp_change" in pud:
                    if accepted:
                        pud["hp_change"] = accepted
                    else:
                        pud.pop("hp_change", None)
            if domains:
                changed_domains[uid] = frozenset(domains)
        return changed_domains

    def _apply_item_events(
        self,
        instance: GameInstance,
        uid: str,
        cs: dict,
        pud: dict,
        rule=None,
    ) -> set[str]:
        """结算一轮的物品事件，固定顺序：获得 -> 装备/卸下 -> 使用。

        所有物品事件都是列表，同一轮多条同类标签全部按原始顺序执行，不再互相
        覆盖。旧版单值字段（equip_gain/weapon_change/use_item）作为 compatibility
        input 继续接受；对应列表字段存在时跳过 scalar，避免同轮重复结算。

        返回本轮真正发生的 live state 域。被拒绝的操作（装备未拥有的物品、卸下
        未装备的物品、使用不存在的道具）不计入，因此"标签有但没生效"不会触发
        无谓的 reconciliation。装备操作同时记 equipment 与 inventory：物品要么
        离开背包，要么把被替换的旧装备退回背包。
        """

        domains: set[str] = set()
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
                domains.add("inventory")
        equipment_ops = pud.get("equipment_ops")
        if isinstance(equipment_ops, list):
            for op in equipment_ops:
                if not isinstance(op, dict):
                    continue
                name = str(op.get("name") or "").strip()
                if not name:
                    continue
                if op.get("op") == "unequip":
                    changed = unequip_item(cs, name)
                else:
                    custom_damage = op.get("damage")
                    changed = equip_owned_item(
                        cs,
                        name,
                        slot=str(op.get("slot") or ""),
                        custom_damage=custom_damage if isinstance(custom_damage, int) else None,
                        legacy_gain=bool(op.get("legacy")),
                        rule=rule,
                        # 同名多件时用身份取件；未声明时保持旧的按名匹配。
                        item_ref=str(op.get("item_ref") or ""),
                    )
                if changed:
                    domains.update(("equipment", "inventory"))
        item_uses = pud.get("item_uses")
        if isinstance(item_uses, list):
            for use in item_uses:
                name = (
                    str(use.get("name") or "").strip()
                    if isinstance(use, dict) else str(use or "").strip()
                )
                if name and self._use_inventory_item(instance, uid, cs, name):
                    domains.add("inventory")
        # ---- legacy 单值字段兼容（旧解析器/外部注入的数据）----
        if "item_gains" not in pud and "equipment_ops" not in pud:
            equip_gain = pud.get("equip_gain")
            if equip_gain:
                # EQUIP 在叙事协议里只代表"获得装备"，不改变当前穿戴。
                add_owned_equipment_to_inventory(cs, str(equip_gain))
                domains.add("inventory")
            weapon_name = pud.get("weapon_change")
            if weapon_name and equip_owned_item(
                cs, str(weapon_name), slot="main_hand", legacy_gain=True, rule=rule,
            ):
                domains.update(("equipment", "inventory"))
        if "item_uses" not in pud:
            use_item = pud.get("use_item")
            if use_item and self._use_inventory_item(instance, uid, cs, str(use_item)):
                domains.add("inventory")
        return domains

    def _use_inventory_item(
        self, instance: GameInstance, uid: str, cs: dict, item_name: str,
    ) -> bool:
        """使用一件背包物品：qty -1（永不为负），effect 含 HP 数字时回血。

        返回是否真的消耗了一件；未拥有该物品时返回 False，调用方据此不把
        inventory 记为已变化。
        """

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
            return True
        logger.warning(
            "忽略使用未拥有的物品: uid=%s item=%s round=%d",
            uid, item_name, progression_state.round_value(instance),
        )
        return False
