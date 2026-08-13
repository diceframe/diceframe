from __future__ import annotations

from typing import Any

import pytest

from src.commands.story_recap import StoryRecapGenerator
from src.engine.game_instance import GameInstance
from src.llm.client import LLMResponse
from src.webui.routes.sse import _play_public_signature


class RecapLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
        self.calls.append({
            "system_prompt": system_prompt,
            "user_message": user_message,
            "kwargs": kwargs,
        })
        content = f"Recap number {len(self.calls)}"
        return LLMResponse(
            content=content,
            narration=content,
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=17,
            is_narration_only=True,
            provider_used="recap-test",
        )


def _entry(round_number: int) -> dict[str, Any]:
    return {
        "round": round_number,
        "actions": [
            {"user_id": "system", "text": f"hidden command {round_number}"},
            {"user_id": "p1", "text": f"public action {round_number}"},
        ],
        "gm_response": f"public narration {round_number}",
        "private_notes": f"secret {round_number}",
    }


@pytest.mark.asyncio
async def test_story_recap_uses_recent_public_rounds_and_does_not_add_fake_round():
    llm = RecapLLM()
    generator = StoryRecapGenerator(llm)
    instance = GameInstance(
        game_key=("web", "recap", "bot"),
        language="en",
        players={"p1": {"character_name": "Avery"}},
        round_number=12,
    )
    instance.log = [_entry(round_number) for round_number in range(1, 13)]
    public_signature_before = _play_public_signature(instance, "p1")

    result = await generator.generate(instance)

    assert result["ok"] is True
    assert len(instance.log) == 12
    assert instance.round_number == 12
    assert instance.log[-1]["story_recaps"][0]["from_round"] == 3
    assert instance.log[-1]["story_recaps"][0]["to_round"] == 12
    assert instance.total_llm_calls == 1
    assert instance.total_tokens == 17
    assert _play_public_signature(instance, "p1") != public_signature_before
    prompt = llm.calls[0]["user_message"]
    assert "Round 3" in prompt
    assert "Round 2" not in prompt
    assert "Avery: public action 12" in prompt
    assert "hidden command" not in prompt
    assert "secret 12" not in prompt


@pytest.mark.asyncio
async def test_story_recap_only_uses_rounds_after_previous_recap():
    llm = RecapLLM()
    generator = StoryRecapGenerator(llm)
    instance = GameInstance(
        game_key=("web", "recap-next", "bot"),
        language="en",
        players={"p1": {"character_name": "Avery"}},
    )
    instance.log = [_entry(1), _entry(2)]

    assert (await generator.generate(instance))["ok"] is True
    no_change = await generator.generate(instance)
    assert no_change["ok"] is False
    assert len(llm.calls) == 1

    instance.log.extend(_entry(round_number) for round_number in range(3, 38))
    second = await generator.generate(instance)

    assert second["ok"] is True
    assert len(llm.calls) == 2
    assert "Round 3" in llm.calls[1]["user_message"]
    assert "Round 37" in llm.calls[1]["user_message"]
    assert "GM: public narration 2\n" not in llm.calls[1]["user_message"]
    assert instance.log[1]["story_recaps"][0]["text"] == "Recap number 1"
    assert instance.log[-1]["story_recaps"][0]["text"] == "Recap number 2"
    assert instance.log[-1]["story_recaps"][0]["from_round"] == 3
    assert instance.log[-1]["story_recaps"][0]["to_round"] == 37
    restored = GameInstance.from_dict(instance.to_dict())
    assert restored.log[-1]["story_recaps"][0]["text"] == "Recap number 2"
