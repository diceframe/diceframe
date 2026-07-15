"""Lorebook 模块 —— SQLite 存储 + 关键词匹配。"""

from .matcher import KeywordMatcher, MAX_RECURSIVE_DEPTH
from .store import LorebookStore

__all__ = ["KeywordMatcher", "LorebookStore", "MAX_RECURSIVE_DEPTH"]
