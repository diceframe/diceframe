"""Intent 语言资源加载器：稳定 ID → 触发模式。

语言资源（locales/<lang>/intents.json）描述"怎么识别"，逻辑只认稳定 ID
（purchase_intent / purchase_confirm / purchase_decline / purchase_offer /
free_purchase / deferred_payment / currency_labels，以及 purchase_target_*、
purchase_quantity_*、purchase_generic_target、purchase_balance），永不使用文本本身作为判断依据。

加载采用 union 语义：游戏语言资源 + en + zh-CN 基准全部生效——多语言
混写（中文局里说 buy / 日文局里夹汉字）与单一大正则时代行为一致，同时
新语言只需新增资源文件。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Iterable

import json

from src.engine.language import normalize_language

_RESOURCE_DIR = Path(__file__).parent / "locales"
_BASE_LANGUAGES = ("en", "zh-CN")


@lru_cache(maxsize=None)
def _load_resource(language: str) -> dict[str, tuple[str, ...]]:
    path = _RESOURCE_DIR / language / "intents.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return {
        str(intent_id): tuple(
            str(pattern) for pattern in (patterns or []) if str(pattern)
        )
        for intent_id, patterns in data.items()
        if isinstance(patterns, list)
    }


def intent_languages(language: str) -> tuple[str, ...]:
    """给定游戏语言的资源解析序：语言本身 + en + zh-CN 基准（去重）。"""

    normalized = normalize_language(language or "")
    ordered: list[str] = []
    for candidate in (normalized, *_BASE_LANGUAGES):
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return tuple(ordered)


def intent_patterns(
    language: str,
    intent_id: str,
    extra_patterns: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Union 语义下某稳定 ID 的全部触发模式（规则投影经 extra_patterns 追加）。"""

    patterns: list[str] = []
    for resource_language in intent_languages(language):
        for pattern in _load_resource(resource_language).get(intent_id, ()):
            if pattern not in patterns:
                patterns.append(pattern)
    for pattern in extra_patterns or ():
        value = str(pattern)
        if value and value not in patterns:
            patterns.append(value)
    return tuple(patterns)


def intent_regex(
    language: str,
    intent_id: str,
    extra_patterns: Iterable[str] | None = None,
) -> re.Pattern[str]:
    """Compile one intent's resource patterns into a single IGNORECASE regex.

    资源里的模式是正则片段（可含字符类/量词，如 deferred_payment 的
    ``[^。！？\\n]{0,12}``），因此不做 re.escape；资源写坏会在加载后
    compile 时立即报错（fail fast）。
    """

    return re.compile(
        "|".join(intent_patterns(language, intent_id, extra_patterns)),
        re.IGNORECASE,
    )


def currency_label_defaults(
    language: str,
    extra_labels: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Default currency labels for the language union plus rule projections."""

    labels: list[str] = []
    for resource_language in intent_languages(language):
        for label in _load_resource(resource_language).get("currency_labels", ()):
            if label not in labels:
                labels.append(label)
    for label in extra_labels or ():
        value = str(label or "").strip()
        if value and value not in labels:
            labels.append(value)
    return tuple(sorted(labels, key=len, reverse=True))


def instance_language(instance: Any) -> str:
    """Game language of an instance, normalized for the lexicon."""

    return normalize_language(getattr(instance, "language", "") or "")
