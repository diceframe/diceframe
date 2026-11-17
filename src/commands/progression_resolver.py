"""角色成长与升级结算。"""

from __future__ import annotations

import logging
from pathlib import Path

from src.engine.character_utils import set_hp
from src.engine.game_instance import GameInstance
from src.engine.world_template import world_template_path
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem

logger = logging.getLogger("trpg")


class ProgressionResolver:
    """处理经验、升级、CoC 技能成长等规则相关成长逻辑。"""

    def __init__(self, rules_dir: Path, worlds_dir: Path):
        self.rules_dir = rules_dir
        self.worlds_dir = worlds_dir

    def _active_rule(self, instance: GameInstance) -> RuleSystem | None:
        """成长必须跟随存档选择的规则，而不是世界创建页的默认规则。"""
        rule_id = str(getattr(instance, "rule_id", "") or "").strip()
        if rule_id:
            core = self.rules_dir / f"{rule_id}.json"
            if core.exists():
                return RuleSystem(RuleBundleLoader().load_rule(self.rules_dir, rule_id, getattr(instance, "language", "")))
            rule_path = RuleSystem.path_for(
                self.rules_dir, rule_id, getattr(instance, "language", "")
            )
            if rule_path.exists():
                return RuleSystem.load(rule_path)
        if instance.world_id and self.worlds_dir:
            world_path = world_template_path(self.worlds_dir, instance.world_id)
            return RuleSystem.load_for_world_path(world_path, self.rules_dir)
        return None

    @staticmethod
    def _level_hp_gain(rule: RuleSystem, character_sheet: dict) -> int:
        """计算本级 HP 增量；D&D 5e 使用职业生命骰固定平均值。"""
        if rule.mechanics != "dnd5e_core":
            return 5
        class_name = str(character_sheet.get("class") or "")
        hp_die = next(
            (int(item.get("hp_die", 8) or 8) for item in rule.classes if item.get("name") == class_name),
            8,
        )
        con = int((character_sheet.get("attributes") or {}).get("con", 10) or 10)
        con_mod = (con - 10) // 2
        return max(1, hp_die // 2 + 1 + con_mod)

    def skill_growth_checks(self, instance: GameInstance, growth_skills: list[dict]) -> None:
        """Retired narrator-tag entry point; real checks mark deferred growth."""
        # Legacy protocol tags are accepted as narration only. They cannot
        # authorize development marks or consume RNG (#541).
        return
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
                rule = self._active_rule(instance)
                if rule:
                    if rule.mechanics == "dnd5e_core":
                        new_hp = int(cs.get("max_hp", 0) or 0) + self._level_hp_gain(rule, cs)
                        set_hp(cs, new_hp, new_hp)
                    else:
                        base_hp = rule.calculate_hp(cs.get("attributes", {}), cs.get("class", ""))
                        new_hp = max(base_hp, cs.get("max_hp", 0)) + 5
                        set_hp(cs, new_hp, new_hp)
                else:
                    new_hp = cs.get("max_hp", 0) + 10
                    set_hp(cs, new_hp, new_hp)
            except Exception:
                logger.exception("升级 HP 计算失败，回退 +10: uid=%s", uid)
                new_hp = cs.get("max_hp", 0) + 10
                set_hp(cs, new_hp, new_hp)
            instance.set_character_sheet(uid, cs)
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
