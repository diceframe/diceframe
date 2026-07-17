"""Language helpers shared by game creation and prompt composition."""

from __future__ import annotations


DEFAULT_LANGUAGE = "zh-CN"
SUPPORTED_LANGUAGES = {"zh-CN", "en"}

# 本地化字段后缀登记：中文（zh-*）无后缀（直接用原字段）；
# 新增语言在此登记后缀后，{key}_{suffix} 式字段即可被 localized_field 查到。
# 字段可选，缺失时回退原字段，不强制维护。
_LANG_FIELD_SUFFIXES = {"en": "en"}


def normalize_language(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in {"en", "en-us", "en-gb", "english"}:
        return "en"
    if text in {"zh", "zh-cn", "cn", "chinese", "简体中文", "中文"}:
        return "zh-CN"
    return DEFAULT_LANGUAGE


def is_english(value: object) -> bool:
    return normalize_language(value) == "en"


def lang_suffix(language: object) -> str:
    """本地化字段后缀：中文（zh-*）无后缀（用原字段），其他语言返回登记的后缀。

    未登记语言返回空（回退原字段）。
    """
    lang = normalize_language(language)
    if lang.startswith("zh"):
        return ""
    return _LANG_FIELD_SUFFIXES.get(lang, "")


def localized_field(template: dict, key: str, language: object = DEFAULT_LANGUAGE):
    """按语言取本地化字段：优先 {key}_{suffix}，回退 {key}。字段可选，不强制维护。"""
    suffix = lang_suffix(language)
    if suffix:
        v = template.get(f"{key}_{suffix}")
        if v is not None:
            return v
    return template.get(key)


def field_suffixes() -> set[str]:
    """所有已登记的本地化字段后缀（如 {'en'}）。"""
    return set(_LANG_FIELD_SUFFIXES.values())


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
