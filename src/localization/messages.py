"""后端文案的统一多语言入口（python-i18n）。

所有面向用户 / 模型的后端文字都放在 ``locales/<命名空间>.<语言>.yml``，代码里
按 ``命名空间.键`` 取用，不再把某一种语言写死在业务逻辑里::

    from src.localization.messages import t

    t("errors.game_not_found")                       # 接口提示：按请求语言
    t("fu.merchant.gold_short", game.language, price=30)   # 对局内容：按对局语言

语言来源
--------
- **接口提示**（返回给当前请求方的错误 / 提示）：不传语言，取请求语言——按标准的
  ``Accept-Language`` 协商，由 ``request_language_middleware`` 写入上下文（前端把它
  显式设为界面语言）。没带这个头或没有受支持语言时按 zh-CN 处理，机器人、脚本拿到
  的仍是原来的中文。
- **对局内容**（写进存档、时间线，或发给模型的文字）：显式传对局语言。

回退链与 ``src.engine.language.localized_text`` 一致：目标语言 → en → zh-CN；
暂停维护的社区语言按 ``src.localization.policy`` 回退英文。

文案文件约定
------------
- 顶层键是语言标签；占位符写成 ``%{name}``；``count`` 参数触发复数形式
  （值写成 ``one`` / ``many`` 的映射）；
- 新文案只要求 zh-CN 与 en 两种，其它语言由社区补充；
- 键是稳定标识：不要拿渲染后的文字做判断或持久化 ID；
- ``locale`` / ``default`` 是库的保留参数名，不能当占位符用。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

import i18n as _i18n
from i18n import resource_loader as _resource_loader
from i18n import translations as _translations

from src.engine.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, normalize_language
from src.localization.policy import display_language

logger = logging.getLogger("trpg")

LOCALES_DIR = Path(__file__).with_name("locales")
FILE_FORMAT = "yml"
#: 新文案必须同时提供的语言。
REQUIRED_LOCALES = ("zh-CN", "en")
#: 目标语言缺失时依次尝试的语言。
FALLBACK_CHAIN = ("en", DEFAULT_LANGUAGE)

_request_language: ContextVar[str] = ContextVar("diceframe_request_language", default="")
_reported_missing: set[tuple[str, str]] = set()
_loaded = False


def _load_catalogs() -> None:
    """Configure python-i18n once and load every catalog file eagerly.

    The library searches the load path again on every missing key; loading up
    front keeps lookups in memory and makes a missing key a plain dict miss.
    """

    global _loaded
    _i18n.set("load_path", [str(LOCALES_DIR)])
    _i18n.set("file_format", FILE_FORMAT)
    _i18n.set("filename_format", "{namespace}.{locale}.{format}")
    _i18n.set("fallback", FALLBACK_CHAIN[0])
    _i18n.set("skip_locale_root_data", False)
    _i18n.set("enable_memoization", True)
    _i18n.set("error_on_missing_translation", False)
    _i18n.set("error_on_missing_placeholder", False)
    _translations.container.clear()
    for path in sorted(LOCALES_DIR.glob(f"*.{FILE_FORMAT}")):
        parts = path.name.split(".")
        if len(parts) != 3:
            raise ValueError(f"文案文件名必须是 <命名空间>.<语言>.{FILE_FORMAT}：{path.name}")
        _resource_loader.load_translation_file(path.name, str(LOCALES_DIR), parts[1])
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load_catalogs()


def reload() -> None:
    """Re-read every catalog file (tests, or after editing files in development)."""

    for loader in _resource_loader.loaders.values():
        getattr(loader, "memoization_dict", {}).clear()
    _reported_missing.clear()
    _load_catalogs()


def recognized_language(value: object) -> str:
    """Map a client-supplied tag to a supported language, or ``""`` when unknown.

    Unlike ``normalize_language`` this never guesses: an unknown tag must not
    silently become Chinese (or anything else) on a request boundary.
    """

    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return ""
    primary = text.split("-", 1)[0]
    for language in SUPPORTED_LANGUAGES:
        if text == language.lower() or primary == language.lower().split("-", 1)[0]:
            return language
    return ""


def set_request_language(value: object) -> Token[str]:
    return _request_language.set(recognized_language(value))


def reset_request_language(token: Token[str]) -> None:
    _request_language.reset(token)


def request_language() -> str:
    """The language of the request being handled, or ``""`` when it sent none."""

    return _request_language.get()


def _chain(language: object | None) -> list[str]:
    requested = request_language() if language is None else str(language or "")
    target = display_language(normalize_language(requested)) if requested else DEFAULT_LANGUAGE
    chain = [target]
    for fallback in FALLBACK_CHAIN:
        if fallback not in chain:
            chain.append(fallback)
    return chain


def has(key: str, language: object | None = None) -> bool:
    """Whether ``key`` resolves for ``language`` (including its fallbacks)."""

    _ensure_loaded()
    return any(_translations.has(key, locale) for locale in _chain(language))


def t(key: str, language: object | None = None, /, **params: Any) -> str:
    """Render one message.

    ``language`` omitted → the request language; pass the game language for
    text that becomes part of a game (saved state, timeline, model prompts).
    A key missing from every catalog is returned as-is and logged once, so a
    typo is visible instead of silently rendering nothing.
    """

    _ensure_loaded()
    chain = _chain(language)
    for locale in chain:
        if _translations.has(key, locale):
            return str(_i18n.t(key, locale=locale, **params))
    marker = (key, chain[0])
    if marker not in _reported_missing:
        _reported_missing.add(marker)
        logger.warning("缺少文案：%s（语言 %s）", key, chain[0])
    return key


def catalog_keys(locale: str) -> set[str]:
    """Every key defined for one locale (catalog completeness checks)."""

    _ensure_loaded()
    return set(_translations.container.get(locale, {}))


__all__ = [
    "FALLBACK_CHAIN",
    "LOCALES_DIR",
    "REQUIRED_LOCALES",
    "catalog_keys",
    "has",
    "recognized_language",
    "reload",
    "request_language",
    "reset_request_language",
    "set_request_language",
    "t",
]
