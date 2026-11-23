from __future__ import annotations

import json
from pathlib import Path

from src.commands.narrative_quality_eval import evaluate_quality_trace


def test_long_horizon_fixture_has_no_baseline_decay() -> None:
    fixture = Path(__file__).parent / "fixtures" / "narrative_quality" / "long_horizon_smoke.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    report = evaluate_quality_trace(payload["turns"])

    assert report["turns"] == 20
    assert report["adjacent_duplicates"] == 0
    assert report["rejection_count"] == 0
    assert report["quality_decay"] == 0


def test_quality_trace_reports_late_repetition_and_repair() -> None:
    turns = [
        {"narration": "线索推进。"},
        {"narration": "新的门打开了。"},
        {"narration": "新的门打开了。", "quality_issues": ["semantic:repeated_previous_turn"]},
        {"narration": "新的门打开了。", "repair_attempted": True, "repair_succeeded": False},
    ]

    report = evaluate_quality_trace(turns, window_size=2)

    assert report["adjacent_duplicates"] == 2
    assert report["rejection_count"] == 1
    assert report["repair_attempts"] == 1
    assert report["last_window"]["adjacent_duplicate_rate"] == 1.0
