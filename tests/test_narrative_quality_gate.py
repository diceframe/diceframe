"""Contract tests for the writer → quality gate → bounded repair workflow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.commands.narrative_quality import (
    NarrativeQualityError,
    inspect_narrative_output,
    quality_repair_instruction,
)
from src.commands.round_llm import call_llm_with_tag_retry
from src.engine.game_instance import GameInstance
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
