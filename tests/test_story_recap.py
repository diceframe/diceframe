from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_story_recap_does_not_hold_process_lock_during_llm_call():
    """回归：概览的模型调用不得持有回合锁，否则会顶掉并发回合推进。"""
    instance = GameInstance(
        game_key=("web", "recap-lock", "bot"),
        language="en",
        players={"p1": {"character_name": "Avery"}},
        round_number=3,
    )
    instance.log = [_entry(round_number) for round_number in range(1, 4)]

    class LockProbeLLM(RecapLLM):
        def __init__(self, target: GameInstance) -> None:
            super().__init__()
            self._target = target

        async def call(self, system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
            assert not self._target._process_lock.locked()
            # 修复前这里全程持锁，回合推进在锁上等待会一直被跳过。
            await asyncio.wait_for(self._target._process_lock.acquire(), 1)
            self._target._process_lock.release()
            return await super().call(system_prompt, user_message, **kwargs)

    generator = StoryRecapGenerator(LockProbeLLM(instance))

    result = await generator.generate(instance)

    assert result["ok"] is True
    assert not instance._process_lock.locked()


@pytest.mark.asyncio
async def test_story_recap_failure_releases_process_lock():
    instance = GameInstance(
        game_key=("web", "recap-fail", "bot"),
        language="en",
        players={"p1": {"character_name": "Avery"}},
        round_number=2,
    )
    instance.log = [_entry(1), _entry(2)]

    class BoomLLM:
        async def call(self, **_kwargs) -> LLMResponse:
            raise RuntimeError("boom")

    generator = StoryRecapGenerator(BoomLLM())

    result = await generator.generate(instance)

    assert result["ok"] is False
    assert not instance._process_lock.locked()


@pytest.mark.asyncio
async def test_story_recap_attaches_to_live_entry_after_new_round_completes():
    """生成期间有新回合完成：概览仍挂到快照时的目标回合，不误报变化。"""
    instance = GameInstance(
        game_key=("web", "recap-race", "bot"),
        language="en",
        players={"p1": {"character_name": "Avery"}},
        round_number=2,
    )
    instance.log = [_entry(1), _entry(2)]

    class SlowThenGrowLLM(RecapLLM):
        def __init__(self, target: GameInstance) -> None:
            super().__init__()
            self._target = target

        async def call(self, system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
            # 模拟概览生成期间回合处理完成并追加了新日志。
            self._target.log.append(_entry(3))
            self._target.round_number = 3
            return await super().call(system_prompt, user_message, **kwargs)

    class _Registry:
        def __init__(self, target) -> None:
            self._target = target

        def get(self, _key):
            return self._target

    generator = StoryRecapGenerator(SlowThenGrowLLM(instance), registry=_Registry(instance))

    result = await generator.generate(instance)

    assert result["ok"] is True
    assert result["recap"]["to_round"] == 2
    assert instance.log[1]["story_recaps"][0]["text"] == "Recap number 1"
    assert "story_recaps" not in instance.log[2]
