"""回合处理中的纯辅助判断。"""

from __future__ import annotations

import re

from src.engine.constants import COMBAT_INTENT_KEYWORDS
from src.engine.game_instance import GameInstance


def should_multi_step(instance: GameInstance, actions_text: str) -> bool:
    """判断是否需要多步推理（仅 WebUI 模式）。"""
    if instance.entry_point != "web":
        return False
    decision_keywords = ("是否", "选择", "赌上", "决定", "要么", "还是")
    if instance.puzzle_manager and instance.puzzle_manager.get_active_puzzles():
        return True
    if any(kw in actions_text for kw in COMBAT_INTENT_KEYWORDS):
        return True
    if len(instance.npcs) >= 3:
        return True
    if any(kw in actions_text for kw in decision_keywords):
        return True
    return False


def validate_dice_constraint(dice_block: str, narration: str) -> bool:
    """校验 LLM 叙事是否与骰子结果矛盾。返回 True=合规, False=矛盾。

    只在骰子为大成功/大失败时校验，且仅检测以玩家为主语的描述，
    避免 NPC 的失败/成功被误判为矛盾。
    """
    if not dice_block or not narration:
        return True
    is_critical = "大成功" in dice_block
    is_fumble = "大失败" in dice_block
    if not is_critical and not is_fumble:
        return True

    player_prefixes = ("你", "你们")
    failure_words = ("失败", "没能", "无法", "落空", "失误", "错过", "打偏")
    success_words = ("成功", "做到", "完成", "命中", "击倒", "漂亮")

    sentences = re.split(r'[。！？\n]', narration)

    if is_critical:
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            is_player_subject = any(sentence.startswith(p) for p in player_prefixes) or "你" in sentence[:8]
            if not is_player_subject:
                continue
            if any(word in sentence for word in failure_words):
                return False

    if is_fumble:
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            is_player_subject = any(sentence.startswith(p) for p in player_prefixes) or "你" in sentence[:8]
            if not is_player_subject:
                continue
            if any(word in sentence for word in success_words):
                return False

    return True


def default_quick_actions_by_class(class_name: str) -> list[str]:
    class_text = (class_name or "").lower()
    if any(k in class_text for k in ("战", "war", "fighter", "paladin", "蛮")):
        return ["攻击", "防御", "冲锋", "威吓"]
    if any(k in class_text for k in ("法", "mage", "wizard", "sorcerer", "术")):
        return ["施法", "吟唱", "元素掌控", "法术反制"]
    if any(k in class_text for k in ("盗", "rogue", "thief", "贼", "刺客")):
        return ["潜行", "侦查", "偷袭", "开锁"]
    if any(k in class_text for k in ("牧", "cleric", "priest", "医", "德鲁伊")):
        return ["治疗", "祝福", "驱散", "祈祷"]
    if any(k in class_text for k in ("游", "ranger", "猎人", "弓")):
        return ["射击", "追踪", "设陷阱", "侦查"]
    return ["观察", "交谈", "探索", "戒备"]
