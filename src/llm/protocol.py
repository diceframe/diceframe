"""Shared model state-tag protocol definitions and tolerant line parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass


KNOWN_PROTOCOL_TAGS = frozenset({
    "HP", "GOLD", "PAY", "SCENE", "SCENE_IMAGE", "NPC", "LOOT", "KEY_ITEM", "DECISION",
    "QUEST", "USE", "WEAPON", "EQUIP", "PRIVATE", "XP", "SAN", "SAN_CHECK",
    "LUCK", "SKILL_GROWTH", "PUSH", "PUZZLE", "MANA", "SPELL", "QUICK_ACTIONS",
    "COMBAT", "REVIVE", "CONFIRMED", "MEMORY", "STAT",
})
PROTOCOL_SENTINELS = frozenset({"NONE"})

# 曾在规则模板或模型输出中出现、但核心从未支持执行的伪标签。
# 只用于玩家可见文本的泄漏识别，绝不进入状态执行路径。
NON_EXECUTABLE_PROTOCOL_NAMES = frozenset({"STATE"})


def _name_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


_CANONICAL_BY_KEY = {
    _name_key(tag): tag
    for tag in KNOWN_PROTOCOL_TAGS | PROTOCOL_SENTINELS
}
_NON_EXECUTABLE_KEYS = {_name_key(tag) for tag in NON_EXECUTABLE_PROTOCOL_NAMES}


@dataclass(frozen=True)
class ProtocolLine:
    tag: str
    value: str
    trailing_text: str = ""
    executable: bool = True


_PROTOCOL_LINE_RE = re.compile(
    r"^\s*(?:(?:[-+*]\s+)|(?:>\s*))?"
    r"(?P<open>\*\*|__|`)?\s*"
    r"(?P<tag>[A-Za-z][A-Za-z0-9_-]{1,31})\s*[:：]\s*"
    r"(?P<body>.*)$",
    re.IGNORECASE,
)
_SENTINEL_LINE_RE = re.compile(
    r"^\s*(?:(?:[-+*]\s+)|(?:>\s*))?(?:\*\*|__|`)?\s*NONE\s*(?:\*\*|__|`)?\s*$",
    re.IGNORECASE,
)
_INLINE_MARKDOWN_TAG_RE = re.compile(
    r"(?P<open>\*\*|__|`)\s*"
    r"(?P<tag>[A-Za-z][A-Za-z0-9_-]{1,31})\s*[:：]"
    r"(?P<value>[^\n]*?)\s*(?P=open)",
    re.IGNORECASE,
)


def canonical_protocol_tag(value: str) -> str | None:
    """Return the canonical executable tag name, accepting underscore/case variants."""
    return _CANONICAL_BY_KEY.get(_name_key(value))


def parse_protocol_line(
    line: str,
    *,
    include_non_executable: bool = False,
) -> ProtocolLine | None:
    """Parse one tag-shaped line, tolerating bullets and Markdown wrappers.

    `SANCheck`, `san_check`, and `SAN_CHECK` all canonicalize to `SAN_CHECK`.
    Unsupported historical names such as `STATE` are returned only when explicitly
    requested for display filtering and are never executable.
    """
    source = str(line or "")
    if _SENTINEL_LINE_RE.match(source):
        return ProtocolLine("NONE", "")
    match = _PROTOCOL_LINE_RE.match(source)
    if not match:
        return None
    raw_tag = match.group("tag")
    tag = canonical_protocol_tag(raw_tag)
    executable = tag is not None
    if tag is None:
        if not include_non_executable or _name_key(raw_tag) not in _NON_EXECUTABLE_KEYS:
            return None
        tag = raw_tag.upper()

    body = match.group("body").strip()
    trailing = ""
    opener = match.group("open") or ""
    if opener:
        closing_at = body.find(opener)
        if closing_at >= 0:
            trailing = body[closing_at + len(opener):].strip()
            body = body[:closing_at].strip()
    if tag != "NONE" and not body:
        return None
    return ProtocolLine(tag, body, trailing, executable)


def normalize_protocol_line(line: str) -> str | None:
    """Return a canonical executable protocol line, or None for ordinary text."""
    parsed = parse_protocol_line(line)
    if not parsed:
        return None
    if parsed.tag == "NONE":
        return "NONE"
    return f"{parsed.tag}:{parsed.value}"


def leaked_protocol_line_start(text: str) -> int | None:
    """Locate the first line that begins with an executable or historical protocol tag."""
    source = str(text or "")
    offset = 0
    for line in source.splitlines(keepends=True):
        if parse_protocol_line(line.rstrip("\r\n"), include_non_executable=True):
            return offset
        offset += len(line)
    return None


def strip_protocol_markup_from_public_line(line: str) -> str:
    """Remove a leaked protocol token while preserving any explicit prose after it."""
    source = str(line or "")
    parsed = parse_protocol_line(source, include_non_executable=True)
    if parsed:
        trailing = parsed.trailing_text
        if not trailing and parsed.tag == "SAN_CHECK" and "|" in parsed.value:
            _value, trailing = parsed.value.split("|", 1)
        return trailing.lstrip(" \t|｜:：—–-").strip()

    def replace_inline(match: re.Match[str]) -> str:
        tag = match.group("tag")
        if canonical_protocol_tag(tag) or _name_key(tag) in _NON_EXECUTABLE_KEYS:
            return ""
        return match.group(0)

    cleaned = _INLINE_MARKDOWN_TAG_RE.sub(replace_inline, source)
    return cleaned.strip() if cleaned != source else source


def contains_protocol_markup(text: str) -> bool:
    """Whether player-facing text still contains a known/internal protocol marker."""
    source = str(text or "")
    if leaked_protocol_line_start(source) is not None:
        return True
    for match in _INLINE_MARKDOWN_TAG_RE.finditer(source):
        tag = match.group("tag")
        if canonical_protocol_tag(tag) or _name_key(tag) in _NON_EXECUTABLE_KEYS:
            return True
    return False
