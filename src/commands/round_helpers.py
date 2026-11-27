"""回合处理中的纯辅助判断。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.engine.modules import game_config

from src.engine import module_hooks

import re

from src.engine import check_verdict
from src.engine.constants import COMBAT_INTENT_KEYWORDS
from src.engine.game_instance import GameInstance
from src.engine.intent.lexicon import narration_patterns


_DECISION_KEYWORDS = ("是否", "选择", "赌上", "决定", "要么")


def log_round(item: Any, default: int = -1) -> int:
    """Read a persisted log round without turning valid round 0 into -1.

    Shared by the round processor and the scene-media pipeline, which both scan
    ``instance.log`` for the entry a completed round left behind.
    """
    try:
        value = item.get("round") if isinstance(item, dict) else None
        return default if value is None or str(value).strip() == "" else int(value)
    except (TypeError, ValueError):
        return default


def should_multi_step(instance: GameInstance, actions_text: str) -> bool:
    """判断是否需要多步推理（仅 WebUI 模式）。

    多步分析只允许由“当前”确实存在的复杂条件触发；累计的历史 NPC 数
    （instance.npcs 包含全部历史 NPC）不代表当前局势复杂，不能据此增加模型调用。
    """
    if game_config.entry_point(instance) != "web":
        return False
    return should_semantic_review(instance, actions_text)


def should_semantic_review(instance: GameInstance, actions_text: str) -> bool:
    """Return whether this turn has enough active complexity for semantic review."""

    if instance.puzzle_manager and instance.puzzle_manager.get_active_puzzles():
        return True
    if any(kw in actions_text for kw in COMBAT_INTENT_KEYWORDS):
        return True
    if any(kw in actions_text for kw in _DECISION_KEYWORDS):
        return True
    return _current_scene_has_multiple_npcs(instance)


def _current_scene_has_multiple_npcs(instance: GameInstance) -> bool:
    """当前场景描述中出现 ≥2 名已注册 NPC 的名字时，视为当前场景多名活跃 NPC。

    用当前场景文本而非累计 NPC 总数，避免历史 NPC 堆积触发多余的局势分析。
    名字长度不足 2 的单字名称容易与场景文本误匹配，不予计数。
    """
    scene_text = str(module_hooks.narrative_view(instance)["scene"] or "")
    if not scene_text:
        return False
    present = 0
    for key, npc in instance.npcs.items():
        name = str(npc.get("character_name") or npc.get("name") or key or "").strip()
        if len(name) < 2:
            continue
        if name in scene_text:
            present += 1
            if present >= 2:
                return True
    return False


def validate_dice_constraint(
    check_results: Sequence[Mapping[str, Any]],
    narration: str,
    *,
    language: str = "zh-CN",
) -> bool:
    """Check that the narration does not contradict a critical outcome.

    Returns ``True`` when the narration is acceptable, when nothing has to be
    checked, or when the game language ships no narration patterns: this is a
    soft quality gate, so an unsupported language degrades to "no opinion"
    instead of blocking the round.

    Attribution is structural. A sentence belongs to an actor when it names the
    character; pronouns are only used when a single player acted this round,
    because in a party the same "you" would otherwise validate every seat
    against one outcome. Verdicts come from :mod:`src.engine.check_verdict`
    codes, never from rendered text, so the check behaves the same in every
    language.
    """

    if not narration:
        return True
    patterns = narration_patterns(language)
    if not patterns:
        return True
    critical: list[tuple[str, str, str]] = []
    player_checks = 0
    for check in check_results or ():
        if not isinstance(check, Mapping):
            continue
        if str(check.get("actor_ref") or "").startswith("player:"):
            player_checks += 1
        code = check_verdict.code_of(check)
        if not (check_verdict.is_critical(code) or check_verdict.is_fumble(code)):
            continue
        actor = str(check.get("actor_name") or "").strip()
        if not actor:
            continue
        critical.append((actor, code, str(check.get("actor_uid") or "")))
    if not critical:
        return True

    pronouns = patterns.get("second_person", ())
    failure_words = patterns.get("failure_words", ())
    success_words = patterns.get("success_words", ())
    sentences = [part.strip() for part in re.split(r"[。！？!?\n]|(?<=\.)\s+", narration)]

    for actor, code, _uid in critical:
        words = failure_words if check_verdict.is_critical(code) else success_words
        use_pronoun = player_checks <= 1
        for sentence in sentences:
            if not sentence:
                continue
            addressed = actor in sentence
            if not addressed and use_pronoun:
                addressed = any(_mentions(sentence, token) for token in pronouns)
            if not addressed:
                continue
            if any(_mentions(sentence, word) for word in words):
                return False
    return True


def _mentions(text: str, token: str) -> bool:
    """Literal match for scripts without spaces; word match for Latin/Cyrillic.

    A substring test would let "miss" match "mission"; an always-bounded test
    would never match inside Chinese or Japanese prose.
    """

    needle = token.casefold()
    if not needle:
        return False
    haystack = text.casefold()
    if needle.isascii():
        return re.search(rf"(?<![0-9a-z]){re.escape(needle)}(?![0-9a-z])", haystack) is not None
    return needle in haystack


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
