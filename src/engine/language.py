"""Language helpers shared by game creation and prompt composition."""

from __future__ import annotations


DEFAULT_LANGUAGE = "zh-CN"
# 四语支持：zh-CN / en / ja / de。ja 已全链路实现（localized_text n-way 分叉 +
# *_ja 规则模板/词表/prompt + 前端 ja 消息，见 P3-A）。de（German）按同一模式
# 追加：核心 UI/GM 输出与规则附录已支持，内置世界模板/SRD 等大体量内容仍随
# localized_text 的回退链落回英文，尚未逐条翻译（与 ja 现状一致）。
# ru (Russian) follows the same pattern as de: the language is registered here
# so it can be selected, the GM output instruction and the front-end messages
# are translated, and everything else rides the localized_text fallback chain
# down to English until it is translated entry by entry.
SUPPORTED_LANGUAGES = {"zh-CN", "en", "ja", "de", "ru"}

# 本地化字段后缀登记：中文（zh-*）无后缀（直接用原字段）；
# 新增语言在此登记后缀后，{key}_{suffix} 式字段即可被 localized_field 查到。
# 字段可选，缺失时回退原字段，不强制维护。
_LANG_FIELD_SUFFIXES = {"en": "en", "ja": "ja", "de": "de", "ru": "ru"}


def normalize_language(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in {"en", "en-us", "en-gb", "english"}:
        return "en"
    if text in {"ja", "jp", "japanese", "日本語"}:
        return "ja"
    if text in {"de", "de-de", "de-at", "de-ch", "german", "deutsch"}:
        return "de"
    if text in {"ru", "ru-ru", "ru-by", "ru-kz", "russian", "русский"}:
        return "ru"
    if text in {"zh", "zh-cn", "cn", "chinese", "简体中文", "中文"}:
        return "zh-CN"
    return DEFAULT_LANGUAGE


def is_english(value: object) -> bool:
    return normalize_language(value) == "en"


def localized_text(language: object, texts: dict[str, str], fallback: str = "") -> str:
    """按语言查表取文案（P3-A n-way 重构的核心 helper）。

    texts = {"zh-CN": "...", "en": "...", "ja": "...", "de": "..."}；未命中
    当前语言时回退 en，再回退 zh-CN，最后回退 fallback。逐步替代
    `if english: A else B` 的二元分叉。第三/四语言（ja/de）缺失时优先回退英文。
    """
    lang = normalize_language(language)
    return texts.get(lang) or texts.get("en") or texts.get("zh-CN") or fallback


def content_locale_candidates(locale: object) -> list[str]:
    """Ordered locale tags to try when resolving localized content on disk.

    Mirrors the `localized_text` chain: the requested tag, its bare language
    subtag, then English. Chinese is deliberately left out of the chain — the
    core template file already carries the Chinese text, so a Chinese request
    must fall through to it instead of picking up an English overlay.

    Without the English step, every locale that ships no content of its own
    (ru, de today; ja for worlds) resolved straight to the Chinese core, so a
    Russian game listed Chinese worlds and rules.
    """
    requested = str(locale or "").strip().replace("_", "-")
    if not requested:
        return []
    candidates: list[str] = []
    for value in (requested, requested.split("-", 1)[0]):
        if value and value not in candidates:
            candidates.append(value)
    if not requested.lower().startswith("zh") and "en" not in candidates:
        candidates.append("en")
    return candidates


def lang_suffix(language: object) -> str:
    """本地化字段后缀：中文（zh-*）无后缀（用原字段），其他语言返回登记的后缀。

    未登记语言返回空（回退原字段）。
    """
    lang = normalize_language(language)
    if lang.startswith("zh"):
        return ""
    return _LANG_FIELD_SUFFIXES.get(lang, "")


def localized_field(template: dict, key: str, language: object = DEFAULT_LANGUAGE):
    """按语言取本地化字段：优先 {key}_{suffix}，第三/四语言（ja/de）缺失时回退
    {key}_en，再无则回退 {key}（zh 原文）。字段可选，不强制维护。"""
    suffix = lang_suffix(language)
    if suffix:
        v = template.get(f"{key}_{suffix}")
        if v is not None:
            return v
        # ja/de 等非 en 语言缺失时先回退英文字段，保持与 localized_text 的回退链一致。
        if suffix != "en":
            en_v = template.get(f"{key}_en")
            if en_v is not None:
                return en_v
    return template.get(key)


def field_suffixes() -> set[str]:
    """所有已登记的本地化字段后缀（如 {'en'}）。"""
    return set(_LANG_FIELD_SUFFIXES.values())


def language_name(value: object) -> str:
    lang = normalize_language(value)
    if lang == "en":
        return "English"
    if lang == "ja":
        return "日本語"
    if lang == "de":
        return "Deutsch"
    if lang == "ru":
        return "Русский"
    return "简体中文"


def gm_language_instruction(value: object) -> str:
    """Prompt suffix that controls player-facing GM language only.

    The protocol tags stay uppercase and unchanged so existing parsers remain
    stable across languages.
    """
    lang = normalize_language(value)
    if lang == "de":
        return (
            "## Ausgabesprache\n"
            "- GM-Erzähltext, Szenenbeschreibungen, private Nachrichten und "
            "QUICK_ACTIONS-Optionen für Spieler müssen in natürlichem Deutsch "
            "verfasst werden.\n"
            "- Das strukturelle Protokoll bleibt unverändert: der `---`-Trenner "
            "und Tags wie HP, GOLD, LOOT, SCENE, PRIVATE, QUICK_ACTIONS, NONE "
            "müssen exakt im vorgegebenen Großschreibungsformat bleiben.\n"
            "- Charakter-IDs, Tag-Namen, JSON-Schlüssel und Würfelnotation nicht "
            "übersetzen. Nur den für Spieler bestimmten Fließtext übersetzen.\n"
            "- Eine Gebühr oder Belohnung nie als abgeschlossen erzählen ohne "
            "den ausdrücklichen serverseitigen Vorschlag; Käufe laufen über den "
            "GM-Bestellablauf."
        )
    if lang == "en":
        return (
            "## Output Language\n"
            "- Player-facing GM narration, scene descriptions, private messages, "
            "and QUICK_ACTIONS options must be written in natural English.\n"
            "- Keep the structural protocol unchanged: the `---` separator and "
            "tags such as HP, GOLD, LOOT, SCENE, PRIVATE, QUICK_ACTIONS, NONE "
            "must remain exactly in the required uppercase format.\n"
            "- Do not translate character IDs, tag names, JSON keys, or dice "
            "notation. Translate only prose meant for players.\n"
            "- Never narrate a fee or reward as completed without its explicit "
            "server-side proposal; purchases are issued through the GM order flow."
        )
    if lang == "ja":
        return (
            "## 出力言語\n"
            "- プレイヤー向けの GM 本文・シーン描写・プライベートメッセージ・"
            "QUICK_ACTIONS の選択肢は自然な日本語で書くこと。\n"
            "- 構造プロトコルは変えない：`---` 区切りと HP、GOLD、LOOT、SCENE、"
            "PRIVATE、QUICK_ACTIONS、NONE などのタグは既存の大文字形式のまま。\n"
            "- キャラクターID・タグ名・JSONキー・ダイス表記は翻訳しない。"
            "プレイヤー向けの散文だけ翻訳する。\n"
            "- 料金や報酬はサーバー側の明示的な提案なしに完了として描写しないこと。"
        )
    if lang == "ru":
        return (
            "## Язык вывода\n"
            "- Повествование ведущего, описания сцен, приватные сообщения и "
            "варианты QUICK_ACTIONS, обращённые к игроку, должны быть написаны "
            "на естественном русском языке.\n"
            "- Структурный протокол остаётся без изменений: разделитель `---` и "
            "теги HP, GOLD, LOOT, SCENE, PRIVATE, QUICK_ACTIONS, NONE должны "
            "выводиться ровно в требуемом формате заглавными буквами.\n"
            "- Не переводить идентификаторы персонажей, имена тегов, ключи JSON "
            "и запись бросков кубов. Переводить только текст, предназначенный "
            "игрокам.\n"
            "- Никогда не описывать списание платы или выдачу награды как "
            "совершившиеся без явного серверного предложения; покупки проводятся "
            "через оформление заказа у ведущего."
        )
    return (
        "## 输出语言\n"
        "- 面向玩家的 GM 正文、场景描述、私密信息和 QUICK_ACTIONS 选项必须使用简体中文。\n"
        "- 保持结构化协议不变：`---` 分隔符以及 HP、GOLD、LOOT、SCENE、PRIVATE、"
        "QUICK_ACTIONS、NONE 等标签必须按既有大写格式输出。\n"
        "- 没有服务端明确提案，不得把费用或奖励叙述为已完成；购买必须由 GM 下单。"
    )
