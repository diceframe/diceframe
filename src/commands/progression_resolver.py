"""角色成长与升级结算。"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from src.engine.character_utils import set_hp
from src.engine.game_instance import GameInstance
from src.engine.world_template import world_template_path
from src.rules.rule_system import RuleSystem

logger = logging.getLogger("trpg")


class ProgressionResolver:
    """处理经验、升级、CoC 技能成长等规则相关成长逻辑。"""

    def __init__(self, rules_dir: Path, worlds_dir: Path):
        self.rules_dir = rules_dir
        self.worlds_dir = worlds_dir

    def skill_growth_checks(self, instance: GameInstance, growth_skills: list[dict]) -> None:
        """CoC 技能成长检定：检定成功使用的技能有概率成长。

        掷 d100 > 当前技能值 → 技能 +1d10。
        growth_skills: LLM 输出的 SKILL_GROWTH 标签解析结果。
        """
        seen: set[tuple[str, str]] = set()
        for gs in growth_skills:
            uid = gs.get("uid", "")
            skill_name = gs.get("skill", "")
            if not uid or not skill_name:
                continue
            key = (uid, skill_name)
            if key in seen:
                continue
            seen.add(key)
            cs = instance.get_character_sheet(uid)
            skills: list[dict] = cs.get("skills", [])
            for skill in skills:
                if skill.get("name") == skill_name:
                    current_val = skill.get("value", 20)
                    if random.randint(1, 100) > current_val:
                        growth = random.randint(1, 10)
                        skill["value"] = min(current_val + growth, 99)
                        msg = (f"技能成长: {instance.players[uid].get('character_name', uid)} "
                               f"「{skill_name}」{current_val}% → {skill['value']}%")
                        logger.info(msg)
                    break

    @staticmethod
    def calc_xp_to_level(level: int) -> int:
        """计算升到下一级需要的总 XP（简单二次增长）。"""
        return level * (level + 1) * 50

    def try_level_up(self, instance: GameInstance, uid: str) -> list[str]:
        """检查角色是否达到升级条件，返回本轮升级信息列表。"""
        msgs: list[str] = []
        if uid not in instance.players:
            return msgs
        cs = instance.get_character_sheet(uid)
        level = cs.get("level", 1)
        xp = cs.get("xp", 0)
        xp_needed = self.calc_xp_to_level(level)
        while xp >= xp_needed:
            xp -= xp_needed
            level += 1
            atk = instance.players[uid]
            old_hp = cs.get("max_hp", 0)
            old_level = cs.get("level", 1)
            cs["level"] = level
            cs["xp"] = xp
            cs["level_up_points"] = cs.get("level_up_points", 0) + 2
            cs["attr_points_max"] = cs.get("attr_points_max", 60) + 2
            try:
                if instance.world_id and self.worlds_dir:
                    world_path = world_template_path(self.worlds_dir, instance.world_id)
                    rule = RuleSystem.load_for_world_path(world_path, self.rules_dir)
                    if rule:
                        base_hp = rule.calculate_hp(cs.get("attributes", {}), cs.get("class", ""))
                        new_hp = max(base_hp, cs.get("max_hp", 0)) + 5
                        set_hp(cs, new_hp, new_hp)
                    else:
                        new_hp = cs.get("max_hp", 0) + 10
                        set_hp(cs, new_hp, new_hp)
                else:
                    new_hp = cs.get("max_hp", 0) + 10
                    set_hp(cs, new_hp, new_hp)
            except Exception:
                logger.exception("升级 HP 计算失败，回退 +10: uid=%s", uid)
                new_hp = cs.get("max_hp", 0) + 10
                set_hp(cs, new_hp, new_hp)
            atk["character_sheet"] = cs
            instance.players[uid] = atk
            msgs.append(
                f"🎉 {atk['character_name']} 升到 Lv.{level}！"
                f"HP {old_hp}→{cs['max_hp']}，获得2点自由属性"
            )
            logger.info(
                "角色升级: %s Lv.%d→%d HP %d→%d XP剩余=%d",
                uid, old_level, level, old_hp, cs["max_hp"], xp,
            )
            xp_needed = self.calc_xp_to_level(level)
        return msgs
