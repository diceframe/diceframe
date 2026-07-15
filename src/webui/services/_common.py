"""WebUI services 共享的常量与工具。"""

_GAME_KEY_SEP = "|"
_INVALID_GAME_KEY = ("__invalid_game_key__", "", "")


def _is_safe_game_key_part(s: str) -> bool:
    """校验 game_key 片段不可影响存档路径结构。"""
    return "/" not in s and "\\" not in s and ".." not in s and "#" not in s and ":" not in s


def _parse_game_key(game_key: str) -> tuple[str, str, str]:
    """解析公开 game_key，并拒绝路径穿越片段。"""
    raw = str(game_key or "")
    parts = raw.split(_GAME_KEY_SEP)
    parsed = tuple(parts[:3]) if len(parts) >= 3 else (raw, "", "")
    if not all(_is_safe_game_key_part(part) for part in parsed):
        return _INVALID_GAME_KEY
    return parsed


def _is_safe_world_id(s: str) -> bool:
    """校验 world_id 不含路径遍历字符（允许中文等正常字符）。"""
    return bool(s) and "/" not in s and "\\" not in s and ".." not in s
