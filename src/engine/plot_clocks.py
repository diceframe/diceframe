"""命刻（进度钟）的手动编辑：新增、改名、推进/回退、删除。

只做剧情追踪器上的纯状态变更与格数钳制；谁可以调用、何时保存与广播由
调用方（规则运行时的意图）负责。校验失败抛 ``ValueError``（文案按传入语言），
找不到命刻抛 ``KeyError``。
"""

from __future__ import annotations

from typing import Any

from src.engine.language import localized_text
from src.engine.plot_tracker import ClockEntry

#: 单次允许的推进幅度上限（防止误传 999 直接把威胁点满）。
MAX_CLOCK_STEP = 12
MIN_CLOCK_SEGMENTS = 4
MAX_CLOCK_SEGMENTS = 12
MAX_CLOCK_TITLE_LENGTH = 80

_MESSAGES: dict[str, dict[str, str]] = {
    "step_out_of_range": {
        "zh-CN": "命刻推进幅度必须是 ±1..±{limit} 之间的整数",
        "en": "A clock can only be changed by a whole number between ±1 and ±{limit}",
    },
    "title_required": {"zh-CN": "命刻标题不能为空", "en": "A clock needs a title"},
    "segments_out_of_range": {
        "zh-CN": "命刻格数必须是 4–12 的整数",
        "en": "A clock must have between 4 and 12 sections",
    },
}


def _text(language: object, key: str, **params: Any) -> str:
    return localized_text(language, _MESSAGES[key]).format(**params)


def _clean_title(title: object, language: object) -> str:
    clean = str(title or "").strip()[:MAX_CLOCK_TITLE_LENGTH]
    if not clean:
        raise ValueError(_text(language, "title_required"))
    return clean


def _clock(tracker: Any, clock_id: str) -> ClockEntry:
    clocks = getattr(tracker, "clocks", None)
    clock = clocks.get(clock_id) if isinstance(clocks, dict) else None
    if clock is None:
        raise KeyError(clock_id)
    return clock


def create_clock(
    tracker: Any, title: object, segments: object, round_number: int = 0, *, language: object = "",
) -> dict[str, Any]:
    """新增命刻；格数 4–12，已填从 0 开始。总格创建后不能改，要改删除重建。"""

    clean_title = _clean_title(title, language)
    if isinstance(segments, bool) or not isinstance(segments, int) or not (
        MIN_CLOCK_SEGMENTS <= segments <= MAX_CLOCK_SEGMENTS
    ):
        raise ValueError(_text(language, "segments_out_of_range"))
    tracker._clock_counter = int(getattr(tracker, "_clock_counter", 0)) + 1
    clock_id = f"clock_{tracker._clock_counter}"
    tracker.clocks[clock_id] = ClockEntry(
        clock_id=clock_id,
        title=clean_title,
        segments=segments,
        filled=0,
        kind="threat",
        round_updated=int(round_number or 0),
    )
    return tracker.clocks[clock_id].to_dict()


def apply_clock_delta(
    tracker: Any, clock_id: str, delta: object, round_number: int = 0, *, language: object = "",
) -> dict[str, Any]:
    """把命刻推进/回退 ``delta`` 格（钳制在 0..总格），返回更新后的快照。"""

    if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0 or abs(delta) > MAX_CLOCK_STEP:
        raise ValueError(_text(language, "step_out_of_range", limit=MAX_CLOCK_STEP))
    clock = _clock(tracker, clock_id)
    clock.filled = max(0, min(int(clock.segments), int(clock.filled) + delta))
    if round_number:
        clock.round_updated = int(round_number)
    return clock.to_dict()


def rename_clock(
    tracker: Any, clock_id: str, title: object, round_number: int = 0, *, language: object = "",
) -> dict[str, Any]:
    clean_title = _clean_title(title, language)
    clock = _clock(tracker, clock_id)
    clock.title = clean_title
    if round_number:
        clock.round_updated = int(round_number)
    return clock.to_dict()


def delete_clock(tracker: Any, clock_id: str) -> None:
    _clock(tracker, clock_id)
    del tracker.clocks[clock_id]
