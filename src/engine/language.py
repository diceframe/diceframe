"""Language helpers shared by game creation and prompt composition."""

from __future__ import annotations


DEFAULT_LANGUAGE = "zh-CN"
SUPPORTED_LANGUAGES = {"zh-CN", "en"}


def normalize_language(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in {"en", "en-us", "en-gb", "english"}:
        return "en"
    if text in {"zh", "zh-cn", "cn", "chinese", "简体中文", "中文"}:
        return "zh-CN"
    return DEFAULT_LANGUAGE


def is_english(value: object) -> bool:
    return normalize_language(value) == "en"


def language_name(value: object) -> str:
    return "English" if is_english(value) else "简体中文"


def gm_language_instruction(value: object) -> str:
    """Prompt suffix that controls player-facing GM language only.

    The protocol tags stay uppercase and unchanged so existing parsers remain
    stable across languages.
    """
    if is_english(value):
        return (
            "## Output Language\n"
            "- Player-facing GM narration, scene descriptions, private messages, "
            "and QUICK_ACTIONS options must be written in natural English.\n"
            "- Keep the structural protocol unchanged: the `---` separator and "
            "tags such as HP, GOLD, LOOT, SCENE, PRIVATE, QUICK_ACTIONS, NONE "
            "must remain exactly in the required uppercase format.\n"
            "- Do not translate character IDs, tag names, JSON keys, or dice "
            "notation. Translate only prose meant for players."
        )
    return (
        "## 输出语言\n"
        "- 面向玩家的 GM 正文、场景描述、私密信息和 QUICK_ACTIONS 选项必须使用简体中文。\n"
        "- 保持结构化协议不变：`---` 分隔符以及 HP、GOLD、LOOT、SCENE、PRIVATE、"
        "QUICK_ACTIONS、NONE 等标签必须按既有大写格式输出。"
    )
