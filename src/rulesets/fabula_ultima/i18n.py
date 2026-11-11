"""FU 后端文案的多语言渲染。

所有面向玩家 / GM / 模型的文字都按**对局语言**（``instance.language``）渲染，
不在业务代码里写死某一种语言。每个模块维护自己的文案表：

```python
_MESSAGES = {
    "merchant_opened": {"zh-CN": "商人已开始营业", "en": "The merchant is open"},
    "gold_short": {"zh-CN": "金币不足（需要 {price}）", "en": "Not enough gold ({price} needed)"},
}
text = render(instance.language, _MESSAGES, "gold_short", price=30)
```

约定：

- 新文案只写 ``zh-CN`` 与 ``en`` 两种；其它语言由社区补充，缺失时按
  ``localized_text`` 的回退链取英文；
- 文案表的 key 是稳定标识，**不要**把渲染后的文字当成判断条件或持久化 ID；
- 判定结论（大成功 / 成功 / 失败 / 大失败）等引擎内的 canonical 值不在这里翻译。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.engine.language import localized_text

Messages = Mapping[str, Mapping[str, str]]

#: 新文案必须同时提供的语言。
REQUIRED_LANGUAGES = ("zh-CN", "en")


def language_of(instance: Any) -> str:
    """对局语言；实例缺失或未设置时由 ``localized_text`` 的回退链兜底。"""

    return str(getattr(instance, "language", "") or "")


def render(language: object, messages: Messages, key: str, /, **params: Any) -> str:
    """按语言取出一条文案并填入参数。"""

    template = localized_text(language, dict(messages[key]))
    return template.format(**params) if params else template


def missing_translations(messages: Messages) -> list[str]:
    """文案表里缺少必需语言的条目（供测试守卫使用）。"""

    return [
        f"{key}:{language}"
        for key, variants in messages.items()
        for language in REQUIRED_LANGUAGES
        if not str(variants.get(language) or "").strip()
    ]
