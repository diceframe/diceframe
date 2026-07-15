"""掷骰检定解析器。

从 game_handler 拆出的回合检定逻辑：按规则模板掷骰，返回给 GM 的硬约束文本，
并写入 instance.last_check 供前端判定卡片展示。
"""

from __future__ import annotations

import re

from src.engine.dice import check_coc, check_d20_advantage, check_d100
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem


class DiceResolver:
    """回合检定：按规则掷骰，返回 GM 硬约束文本，并写入 instance.last_check。"""

    def roll_d100_check(self, instance: GameInstance, actions_text: str) -> str:
        """通用 d100 检定：优先匹配技能值，否则用最高相关属性×5 作为阈值。"""
        first_action = next(
            (a for a in instance.action_queue if a.get("selected_skill")),
            instance.action_queue[0] if instance.action_queue else {},
        )
        uid = first_action.get("user_id", "")
        cs = instance.get_character_sheet(uid)
        attrs = cs.get("attributes", {})
        raw_skills = cs.get("skills", [])

        sel_skill = first_action.get("selected_skill", "")
        skill_value = 0
        matched_skill = ""
        if sel_skill:
            for skill in raw_skills:
                name = skill.get("name", "") if isinstance(skill, dict) else str(skill)
                if name == sel_skill:
                    skill_value = int(skill.get("value", 20) if isinstance(skill, dict) else 20)
                    matched_skill = name
                    break
        else:
            for skill in raw_skills:
                name = skill.get("name", "") if isinstance(skill, dict) else str(skill)
                if name and name in actions_text:
                    value = int(skill.get("value", 20) if isinstance(skill, dict) else 20)
                    if value > skill_value:
                        skill_value = value
                        matched_skill = name

        if skill_value > 0:
            threshold = skill_value
            label = f"技能「{matched_skill}」{threshold}%"
        else:
            threshold = max(
                int(attrs.get("dex", 10) or 10),
                int(attrs.get("int", 10) or 10),
                int(attrs.get("str", 10) or 10),
                int(attrs.get("pow", 10) or 10),
            ) * 5
            label = f"属性阈值 {threshold}"

        result, verdict = check_d100(threshold)
        instance.last_check = {
            "actor_uid": uid,
            "actor_name": instance.players.get(uid, {}).get("character_name", ""),
            "dice": "d100",
            "attribute": None,
            "skill": matched_skill,
            "roll": result.natural,
            "threshold": threshold,
            "verdict": verdict,
            "is_critical": "大成功" in verdict,
            "is_fumble": "大失败" in verdict,
        }
        return (
            f"\n【系统检定·必须遵循】\n"
            f"检定: d100={result.natural} vs {label}\n"
            f"结果: {verdict}\n"
            f"要求: GM叙事必须严格体现此掷骰结果。大成功(≤5)=额外叙事奖励，"
            f"大失败(≥96)=严重后果，成功/失败由 GM 按 DC 具体判定。\n"
        )

    def roll_rule_check(self, instance: GameInstance, actions_text: str, rule: RuleSystem) -> str:
        """按规则模板进行一次核心检定，并输出给 GM 的硬约束文本。"""
        first_action = next(
            (a for a in instance.action_queue if a.get("selected_skill") or a.get("selected_attribute")),
            instance.action_queue[0] if instance.action_queue else {},
        )
        uid = first_action.get("user_id", "")
        player = instance.players.get(uid, {})
        cs = instance.get_character_sheet(uid)
        attrs = cs.get("attributes", {})
        skills = cs.get("skills", [])

        selected_skill = first_action.get("selected_skill", "")
        matched_skill = None
        if selected_skill:
            matched_skill = next(
                (
                    skill
                    for skill in skills
                    if (skill.get("name", "") if isinstance(skill, dict) else str(skill)) == selected_skill
                ),
                None,
            )
        if not matched_skill:
            matched_skill = self._match_skill(skills, actions_text)

        if rule.dice_system == "d100":
            return self.roll_coc_check(instance, uid, player, attrs, matched_skill)

        selected_attribute = first_action.get("selected_attribute", "")
        attr_key = selected_attribute if selected_attribute else self._guess_attribute_key(actions_text, rule)
        attr_value = int(attrs.get(attr_key, 10) or 10)
        attr_mod = rule.attribute_modifier(attr_value)
        skill_name = matched_skill.get("name", "") if matched_skill else ""
        skill_value = int(matched_skill.get("value", 0) or 0) if matched_skill else 0
        level = int(cs.get("level", 1) or 1)

        skill_bonus = 0
        bonus_label = ""
        if rule.skill_mode == "proficiency" and matched_skill:
            skill_bonus = rule.proficiency_bonus(level)
            bonus_label = f"熟练加值 +{skill_bonus}"
        elif matched_skill:
            skill_bonus = rule.skill_bonus(skill_value)
            bonus_label = f"技能「{skill_name}」{skill_value} → 加值 +{skill_bonus}"

        dc = rule.dc_for_difficulty(instance.difficulty, "normal")
        advantage_mode, advantage_note = self._dnd5e_advantage_mode(actions_text, first_action, rule)
        result, verdict = check_d20_advantage(
            modifier=attr_mod + skill_bonus,
            dc=dc,
            advantage=advantage_mode == "advantage",
            disadvantage=advantage_mode == "disadvantage",
        )
        attr_label = self._attribute_label(rule, attr_key)
        roll_label = self._d20_roll_label(result, advantage_mode)
        if advantage_note:
            roll_label = f"{roll_label}（{advantage_note}）"
        instance.last_check = {
            "actor_uid": uid,
            "actor_name": instance.players.get(uid, {}).get("character_name", ""),
            "dice": "d20",
            "attribute": attr_label,
            "skill": skill_name,
            "roll": result.natural,
            "rolls": result.rolls,
            "advantage_mode": advantage_mode,
            "advantage_note": advantage_note or None,
            "modifier": attr_mod + skill_bonus,
            "modifier_breakdown": bonus_label or None,
            "total": result.total,
            "dc": dc,
            "difficulty": instance.difficulty,
            "verdict": verdict,
            "is_critical": "大成功" in verdict,
            "is_fumble": "大失败" in verdict,
        }
        return (
            f"\n【系统检定·必须遵循】\n"
            f"机制: {rule.mechanics} / {rule.ruleset_level}\n"
            f"检定: {roll_label} + 属性「{attr_label}」修正 {attr_mod:+d}"
            f"{(' + ' + bonus_label) if bonus_label else ''} = {result.total} vs DC {dc}\n"
            f"结果: {verdict}\n"
            f"要求: GM叙事必须严格体现此检定结果。大成功=额外收益，大失败=严重后果；"
            f"普通成功/失败按上述总值与 DC 裁定。\n"
        )

    def roll_coc_check(
        self,
        instance: GameInstance,
        uid: str,
        player: dict,
        attrs: dict,
        matched_skill: dict | None,
    ) -> str:
        """CoC d100 检定：优先使用技能阈值，否则使用智力×5。"""
        cs = instance.get_character_sheet(uid)
        if matched_skill:
            threshold = int(matched_skill.get("value", 20) or 20)
            label = f"技能「{matched_skill.get('name', '')}」{threshold}%"
        else:
            threshold = int(attrs.get("int", 10) or 10) * 5
            label = f"属性「智力」×5 = {threshold}%"

        result, verdict = check_coc(threshold)
        instance.last_check = {
            "actor_uid": uid,
            "actor_name": instance.players.get(uid, {}).get("character_name", ""),
            "dice": "d100",
            "attribute": None,
            "skill": matched_skill.get("name", "") if matched_skill else "",
            "roll": result.natural,
            "threshold": threshold,
            "verdict": verdict,
            "is_critical": "大成功" in verdict,
            "is_fumble": "大失败" in verdict,
        }

        hard = threshold // 2
        extreme = threshold // 5
        luck = int(cs.get("luck", 0) or 0)
        luck_hint = ""
        if verdict == "失败" and result.natural > threshold:
            gap = result.natural - threshold
            if 0 < gap <= luck:
                luck_hint = f"\n幸运提示: 可消耗 {gap} 点幸运把本次失败补成普通成功。"

        return (
            f"\n【系统检定·必须遵循】\n"
            f"机制: coc7e_core / {getattr(instance, 'difficulty', '标准')}\n"
            f"检定: d100={result.natural} vs {label}\n"
            f"成功等级阈值: 普通≤{threshold}，困难≤{hard}，极难≤{extreme}，"
            f"大成功=1，大失败=96-100（技能低于50时）\n"
            f"结果: {verdict}{luck_hint}\n"
            f"要求: GM叙事必须严格体现 CoC 成功等级。失败不应写成成功，"
            f"大失败必须带来明确恶化。\n"
        )

    @staticmethod
    def _match_skill(skills: list, actions_text: str) -> dict | None:
        best: dict | None = None
        for skill in skills:
            if isinstance(skill, str):
                name, value = skill, 20
            elif isinstance(skill, dict):
                name, value = skill.get("name", ""), int(skill.get("value", 20) or 20)
            else:
                continue
            if name and name in actions_text:
                if best is None or value > int(best.get("value", 0) or 0):
                    best = {"name": name, "value": value}
        return best

    @staticmethod
    def _attribute_label(rule: RuleSystem, key: str) -> str:
        for attr in rule.attributes:
            if attr.get("key") == key:
                return attr.get("name", key)
        return key

    @staticmethod
    def _guess_attribute_key(actions_text: str, rule: RuleSystem) -> str:
        hints = [
            ("dex", ("敏捷", "潜行", "躲", "闪", "射击", "弓", "跳", "攀", "快")),
            ("str", ("力量", "攻击", "砍", "劈", "推", "撬", "举", "格斗")),
            ("con", ("体质", "忍耐", "抗", "承受", "耐力")),
            ("int", ("智力", "调查", "破解", "分析", "知识", "研究", "黑入", "维修")),
            ("wis", ("感知", "观察", "侦查", "聆听", "察觉", "追踪")),
            ("cha", ("魅力", "说服", "欺骗", "威胁", "交涉", "表演")),
        ]
        keys = set(rule.attribute_keys)
        for key, words in hints:
            if key in keys and any(word in actions_text for word in words):
                return key
        return "dex" if "dex" in keys else (rule.attribute_keys[0] if rule.attribute_keys else "dex")

    @staticmethod
    def _dnd5e_advantage_mode(actions_text: str, action: dict, rule: RuleSystem) -> tuple[str, str]:
        """从行动文本/元数据里识别 D&D 5e 优势/劣势。"""
        if rule.mechanics != "dnd5e_core":
            return "", ""
        raw_mode = str(action.get("advantage_mode") or action.get("advantage") or "").strip().lower()
        normalized = re.sub(r"\s+", "", actions_text.lower())
        has_advantage = raw_mode in {"advantage", "优势", "有利", "bonus"} or any(
            word in normalized
            for word in ("优势", "有利", "占优", "奖励骰", "帮忙", "协助", "偷袭", "高地")
        )
        has_disadvantage = raw_mode in {"disadvantage", "劣势", "不利", "penalty"} or any(
            word in normalized
            for word in ("劣势", "不利", "受阻", "惩罚骰", "黑暗", "负伤", "疲惫", "干扰")
        )
        if has_advantage and has_disadvantage:
            return "", "优势与劣势同时存在，已按 D&D 5e 抵消"
        if has_advantage:
            return "advantage", "优势：2d20 取高"
        if has_disadvantage:
            return "disadvantage", "劣势：2d20 取低"
        return "", ""

    @staticmethod
    def _d20_roll_label(result, advantage_mode: str) -> str:
        if advantage_mode == "advantage":
            return f"d20优势={result.rolls} 取 {result.natural}"
        if advantage_mode == "disadvantage":
            return f"d20劣势={result.rolls} 取 {result.natural}"
        return f"d20={result.natural}"
