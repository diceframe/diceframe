from __future__ import annotations

from src.engine.game_instance import GameInstance
from src.engine.health import health_payload
from src.engine.modules import narrative_quality_metrics


def test_quality_metrics_are_per_run_and_visible_in_health_payload() -> None:
    instance = GameInstance(game_key=("web", "metrics", "bot"))

    narrative_quality_metrics.record(
        instance,
        turn=True,
        complex_turn=True,
        rejection=True,
        repair_attempt=True,
        repair_success=True,
    )

    assert narrative_quality_metrics.metrics(instance) == {
        "turns": 1,
        "complex_turns": 1,
        "gate_rejections": 1,
        "repair_attempts": 1,
        "repair_successes": 1,
        "reviewer_unavailable": 0,
    }
    assert health_payload(instance)["narrative_quality"] == narrative_quality_metrics.metrics(instance)


def test_quality_metrics_reset_with_new_run() -> None:
    instance = GameInstance(game_key=("web", "metrics-reset", "bot"))
    narrative_quality_metrics.record(instance, turn=True, reviewer_unavailable=True)

    narrative_quality_metrics.reset(instance)

    assert narrative_quality_metrics.metrics(instance)["turns"] == 0
    assert narrative_quality_metrics.metrics(instance)["reviewer_unavailable"] == 0
