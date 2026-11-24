"""Contract tests for the writer → quality gate → bounded repair workflow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.commands.narrative_quality import (
    BufferedNarrativeStream,
    NarrativeQualityError,
    build_quality_gate,
    inspect_narrative_output,
    quality_repair_instruction,
    review_narrative_semantics,
)
from src.commands.round_llm import call_llm_with_tag_retry
from src.engine.game_instance import GameInstance
from src.engine.modules import narrative_quality_metrics
from src.llm.client import LLMResponse


def _response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        narration=content.split("---", 1)[0].strip(),
        state_update=None,
        memory_delta=None,
        info_asymmetry=None,
        plot_update=None,
        total_tokens=8,
        is_narration_only=False,
        provider_used="quality-test",
    )


class _Writer:
    default = "quality-test"

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls: list[str] = []

    async def call(self, *, user_message: str, **_kwargs):
        self.calls.append(user_message)
        return _response(self.outputs.pop(0))


def test_quality_gate_rejects_unknown_player_and_bad_shapes() -> None:
    response = SimpleNamespace(narration="门后的灯亮了")
    data = {
        "state_update": {
            "players": {"outsider": "overwrite everything"},
            "loot": {},
        },
        "plot_update": [],
    }

    issues = inspect_narrative_output(
        response,
        data,
        allowed_player_uids={"actor"},
    )

    assert issues == [
        "unauthorized_player_uid:outsider",
        "invalid_player_update:outsider",
        "invalid_loot",
        "invalid_plot_update",
    ]


def test_quality_gate_accepts_narration_only_with_protocol_separator() -> None:
    response = SimpleNamespace(narration="门后的灯亮了")
    data = {
        "state_update": {"players": {}, "loot": []},
        "plot_update": {"quests": []},
    }

    assert inspect_narrative_output(response, data) == []


@pytest.mark.asyncio
async def test_quality_gate_repairs_once_and_accepts_rewritten_response() -> None:
    writer = _Writer([
        "",
        "门后的灯亮了。\n---\nNONE",
    ])
    instance = GameInstance(game_key=("web", "quality", "bot"))
    resets: list[int] = []

    async def on_reset() -> None:
        resets.append(1)

    response, data = await call_llm_with_tag_retry(
        writer,
        instance,
        "GM",
        "context",
        "hp_based",
        "",
        512,
        quality_gate=lambda candidate, parsed: inspect_narrative_output(candidate, parsed),
        quality_repair_instruction=lambda issues: quality_repair_instruction(
            "zh-CN", issues,
        ),
        on_reset=on_reset,
    )

    assert len(writer.calls) == 2
    assert "质量门修复" in writer.calls[1]
    assert resets == [1]
    assert response.narration == "门后的灯亮了。"
    assert data["state_update"]["loot"] == []


@pytest.mark.asyncio
async def test_quality_gate_fails_closed_after_repair_attempt() -> None:
    writer = _Writer(["", ""])
    instance = GameInstance(game_key=("web", "quality-fail", "bot"))

    with pytest.raises(NarrativeQualityError) as rejected:
        await call_llm_with_tag_retry(
            writer,
            instance,
            "GM",
            "context",
            "hp_based",
            "",
            512,
            quality_gate=lambda candidate, parsed: inspect_narrative_output(candidate, parsed),
            quality_repair_instruction=lambda issues: quality_repair_instruction(
                "zh-CN", issues,
            ),
        )

    assert rejected.value.issues == ("empty_narration",)
    assert len(writer.calls) == 2


@pytest.mark.asyncio
async def test_semantic_reviewer_returns_bounded_issues() -> None:
    reviewer = _Writer([
        '{"pass":false,"issues":["ignored_current_action",'
        '"repeated_previous_turn","prompt_injection"]}',
    ])
    instance = GameInstance(game_key=("web", "quality-review", "bot"))

    issues = await review_narrative_semantics(
        reviewer,
        instance,
        actions_text="调查门锁",
        narration="门还是在那里。",
        state_update={"players": {}},
        recent_public_narration="上一轮：门还是在那里。",
    )

    assert issues == ["semantic:ignored_current_action", "semantic:repeated_previous_turn"]


@pytest.mark.asyncio
async def test_semantic_reviewer_failure_is_fail_open() -> None:
    class BrokenReviewer:
        async def call(self, **_kwargs):
            raise RuntimeError("reviewer unavailable")

    instance = GameInstance(game_key=("web", "quality-review-fail", "bot"))

    assert await review_narrative_semantics(
        BrokenReviewer(),
        instance,
        actions_text="调查门锁",
        narration="门开了。",
        state_update={},
    ) == []


@pytest.mark.asyncio
async def test_complex_turn_stream_is_flushed_only_after_review() -> None:
    received: list[str] = []

    async def sink(text: str) -> None:
        received.append(text)

    buffered = BufferedNarrativeStream(sink)
    await buffered("第一段")
    await buffered("第二段")
    assert received == []

    await buffered.flush()

    assert received == ["第一段第二段"]


@pytest.mark.asyncio
async def test_quality_gate_still_runs_when_a_dice_conflict_is_accepted() -> None:
    """接受骰子矛盾的输出不等于豁免质量门。

    第二次仍与骰子约束矛盾时输出会被接受，但规则集的状态过滤、玩家白名单和
    结构校验都必须在这份输出上照常执行。
    """
    writer = _Writer([
        "你没能在门上打出洞。\n---\nNONE",
        "你依然无法打开门。\n---\nNONE",
    ])
    instance = GameInstance(game_key=("web", "quality-dice", "bot"))
    gate_calls: list[dict] = []

    async def gate(candidate, parsed):
        gate_calls.append(dict(parsed))
        return []

    response, _data = await call_llm_with_tag_retry(
        writer,
        instance,
        "GM",
        "context",
        "hp_based",
        "【系统检定·必须遵循】大成功",
        512,
        quality_gate=gate,
        quality_repair_instruction=lambda issues: "rewrite",
    )

    assert len(writer.calls) == 2
    assert len(gate_calls) == 1


@pytest.mark.asyncio
async def test_the_gate_applies_the_ruleset_policy_and_counts_the_turn() -> None:
    class _PolicyRuntime:
        def filter_narrative_state_update(self, instance, update):
            return {**update, "scene_change": ""}

    instance = GameInstance(game_key=("web", "quality-policy", "bot"))
    gate = build_quality_gate(
        instance,
        None,
        runtime=_PolicyRuntime(),
        actions_text="调查门锁",
        semantic_review=False,
    )
    candidate = {"state_update": {"scene_change": "酒馆", "players": {}}}

    assert await gate(SimpleNamespace(narration="门后的灯亮了"), candidate) == []

    assert candidate["state_update"]["scene_change"] == ""
    assert narrative_quality_metrics.metrics(instance) == {
        "turns": 1, "complex_turns": 0, "gate_rejections": 0, "repair_attempts": 0,
        "repair_successes": 0, "reviewer_unavailable": 0,
    }


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("zh-CN", "质量门修复"),
        ("en", "QUALITY GATE REPAIR"),
        ("ja", "品質ゲート修正"),
        ("de", "QUALITÄTSGATE-KORREKTUR"),
    ],
)
def test_repair_instruction_reaches_every_supported_language(language: str, expected: str) -> None:
    instruction = quality_repair_instruction(language, ["empty_narration"])

    assert expected in instruction
    assert "empty_narration" in instruction


def test_semantic_issue_labels_are_only_added_for_chinese() -> None:
    issue = "semantic:ignored_current_action"

    assert "当前行动没有被回应" in quality_repair_instruction("zh-CN", [issue])
    assert issue in quality_repair_instruction("en", [issue])
