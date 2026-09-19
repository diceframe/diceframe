"""Content Modules 包：模组内容引用 / 目录 / 校验（母方案 §85）。

MOD 程序的 generic 半区（不依赖具体 ruleset）。D&D 内容目录的实现位于
``src/rulesets/dnd2024/content/``（DNDMOD 组）。
"""

from src.content_modules.refs import (
    CONTENT_KINDS,
    ContentRef,
    ContentRefChain,
    ContentRefError,
    ContentResolution,
    parse_content_ref,
)

__all__ = [
    "CONTENT_KINDS",
    "ContentRef",
    "ContentRefChain",
    "ContentRefError",
    "ContentResolution",
    "parse_content_ref",
]
