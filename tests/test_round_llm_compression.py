from __future__ import annotations

from typing import Any

import pytest

from src.commands.round_llm import call_llm_with_tag_retry
from src.engine.game_instance import GameInstance
from src.llm.client import LLMResponse


class CompressionLLM:
    default = "compression-test"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
        self.calls.append({"system_prompt": system_prompt, "user_message": user_message, "kwargs": kwargs})
        if "请压缩以下 TRPG GM 正文" in user_message:
            content = "李玄清压低声音，指出银针与祭品名录都指向白马寺藏经阁。夜风骤停，绢帛上新名字正在浮现，若不立刻潜入查明阵法根源，下一具无面尸很快就会出现。"
        else:
            long_text = "李玄清展开绢帛。" * 100
            content = (
                f"{long_text}\n---\n"
                "KEY_ITEM:u1:摄魂银针\n"
                "QUICK_ACTIONS:潜入藏经阁|追踪银针黑气\n"
                "MEMORY:摄魂银针可反向追踪施术者"
            )
        return LLMResponse(
            content=content,
            narration=content.split("---", 1)[0].strip(),
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=10,
            is_narration_only=False,
            provider_used="compression-test",
        )


@pytest.mark.asyncio
async def test_long_narration_is_compressed_without_reparsing_tags():
    llm = CompressionLLM()
    instance = GameInstance(game_key=("web", "compression", "bot"))

    response, data = await call_llm_with_tag_retry(
        llm,
        instance,
        "你是测试 GM。",
        "上下文",
        "hp_based",
        "",
        1024,
    )

    assert len(llm.calls) == 2
    assert llm.calls[1]["kwargs"]["max_tokens"] == 1024
    assert "白马寺藏经阁" in response.narration
    assert len(response.narration) < 260
    assert data["state_update"]["loot"][0]["item"] == "摄魂银针"
    assert data["quick_actions"] == ["潜入藏经阁", "追踪银针黑气"]
    assert "KEY_ITEM:u1:摄魂银针" in response.content
