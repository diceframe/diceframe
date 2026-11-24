"""Offline long-horizon metrics for captured narrative traces."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any


def _normalized(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().casefold())


def _rate(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0.0


def evaluate_quality_trace(
    turns: Iterable[Mapping[str, Any]],
    *,
    window_size: int = 5,
) -> dict[str, Any]:
    """Summarize a captured multi-round trace without calling an LLM.

    A trace may include ``quality_issues``, ``repair_attempted``,
    ``repair_succeeded`` and ``reviewer_unavailable`` fields emitted by a test
    harness or production log adapter. Unknown fields are ignored.
    """

    rows = [dict(turn) for turn in turns if isinstance(turn, Mapping)]
    normalized_narrations = [_normalized(row.get("narration")) for row in rows]
    adjacent_duplicates = sum(
        bool(current) and current == previous
        for previous, current in zip(normalized_narrations, normalized_narrations[1:])
    )
    repeated_narrations = sum(
        bool(text) and text in set(normalized_narrations[:index])
        for index, text in enumerate(normalized_narrations)
    )
    rejection_count = sum(bool(row.get("quality_issues")) for row in rows)
    repair_attempts = sum(bool(row.get("repair_attempted")) for row in rows)
    repair_successes = sum(bool(row.get("repair_succeeded")) for row in rows)
    reviewer_unavailable = sum(bool(row.get("reviewer_unavailable")) for row in rows)
    window = max(1, int(window_size or 5))

    def window_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "turns": len(items),
            "adjacent_duplicate_rate": _rate(
                sum(
                    bool(_normalized(current.get("narration")))
                    and _normalized(current.get("narration"))
                    == _normalized(previous.get("narration"))
                    for previous, current in zip(items, items[1:])
                ),
                max(0, len(items) - 1),
            ),
            "rejection_rate": _rate(
                sum(bool(item.get("quality_issues")) for item in items),
                len(items),
            ),
        }

    first_window = window_metrics(rows[:window])
    last_window = window_metrics(rows[-window:])
    quality_decay = round(
        max(
            0.0,
            (last_window["adjacent_duplicate_rate"] - first_window["adjacent_duplicate_rate"])
            + (last_window["rejection_rate"] - first_window["rejection_rate"]),
        ),
        4,
    )
    return {
        "turns": len(rows),
        "adjacent_duplicates": adjacent_duplicates,
        "adjacent_duplicate_rate": _rate(adjacent_duplicates, max(0, len(rows) - 1)),
        "repeated_narrations": repeated_narrations,
        "rejection_count": rejection_count,
        "rejection_rate": _rate(rejection_count, len(rows)),
        "repair_attempts": repair_attempts,
        "repair_successes": repair_successes,
        "reviewer_unavailable": reviewer_unavailable,
        "first_window": first_window,
        "last_window": last_window,
        "quality_decay": quality_decay,
    }


__all__ = ["evaluate_quality_trace"]
