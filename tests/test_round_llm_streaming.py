from __future__ import annotations

from typing import Any

import pytest

from src.commands.round_llm import _NarrationDeltaFilter, call_llm_with_tag_retry
from src.engine.game_instance import GameInstance
from src.llm.client import LLMResponse, OutputTruncatedError


def _make_response(content: str, provider: str = "stream-test") -> LLMResponse:
    return LLMResponse(
        content=content,
        narration=content.split("---", 1)[0].strip(),
        state_update=None,
        memory_delta=None,
        info_asymmetry=None,
        plot_update=None,
        total_tokens=10,
        is_narration_only=False,
        provider_used=provider,
    )


class StreamingLLM:
    """按顺序返回预设内容的流式 fake；on_delta 收到原始 delta（--- 过滤由上层负责）。"""

    default = "stream-test"

    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self._i = 0
        self.stream_calls = 0

    async def call_stream(self, system_prompt: str = "", user_message: str = "", *,
                          temperature: float = 0.7, max_tokens: int = 1024,
                          on_delta=None, **kwargs) -> LLMResponse:
        self.stream_calls += 1
        content = self._contents[min(self._i, len(self._contents) - 1)]
        self._i += 1
        if on_delta:
            # 拆成两段发，验证 --- 跨 chunk 也能被过滤
            mid = len(content) // 2
            await on_delta(content[:mid])
            await on_delta(content[mid:])
        return _make_response(content)


class LengthRetryStreamingLLM:
    """None 表示本次流式输出因 token 上限截断，其余值表示成功响应。"""

    default = "stream-test"

    def __init__(self, outcomes: list[str | None]) -> None:
        self._outcomes = list(outcomes)
        self._i = 0
        self.max_tokens: list[int] = []

    async def call_stream(self, system_prompt: str = "", user_message: str = "", *,
                          temperature: float = 0.7, max_tokens: int = 1024,
                          on_delta=None, **kwargs) -> LLMResponse:
        self.max_tokens.append(max_tokens)
        outcome = self._outcomes[min(self._i, len(self._outcomes) - 1)]
        self._i += 1
        if outcome is None:
            if on_delta:
                await on_delta("尚未完成的叙事。" * 20)
            raise OutputTruncatedError("max_tokens")
        if on_delta:
            await on_delta(outcome)
        return _make_response(outcome)


@pytest.mark.asyncio
async def test_narration_delta_filter_stops_at_separator():
    received: list[str] = []

    async def on_delta(text: str) -> None:
        received.append(text)

    filt = _NarrationDeltaFilter(on_delta)
    await filt.feed("叙事开头")
    await filt.feed("，继续")
    await filt.feed("。---\nKEY_ITEM:u1:银针")
    await filt.flush()

    streamed = "".join(received)
    assert streamed == "叙事开头，继续。"
    assert "KEY_ITEM" not in streamed
    assert "---" not in streamed


@pytest.mark.asyncio
async def test_narration_delta_filter_handles_split_separator():
    received: list[str] = []

    async def on_delta(text: str) -> None:
        received.append(text)

    filt = _NarrationDeltaFilter(on_delta)
    await filt.feed("叙事")
    await filt.feed("--")
    await filt.feed("-\nKEY_ITEM:u1:银针")
    await filt.flush()

    assert "".join(received) == "叙事"


@pytest.mark.asyncio
async def test_narration_delta_filter_flushes_when_no_separator():
    received: list[str] = []

    async def on_delta(text: str) -> None:
        received.append(text)

    filt = _NarrationDeltaFilter(on_delta)
    await filt.feed("纯叙事文本")
    await filt.flush()

    assert "".join(received) == "纯叙事文本"


@pytest.mark.asyncio
async def test_narration_delta_filter_hides_nonstandard_state_heading():
    received: list[str] = []

    async def on_delta(text: str) -> None:
        received.append(text)

    filt = _NarrationDeltaFilter(on_delta)
    await filt.feed("玛尔塔把药草推到柜台上。\n【**状态**")
    await filt.feed("变更】\nPAY:u1:15\nLOOT:u1:解毒草")
    await filt.flush()

    streamed = "".join(received)
    assert streamed.strip() == "玛尔塔把药草推到柜台上。"
    assert "PAY:" not in streamed
    assert "LOOT:" not in streamed
    assert "状态" not in streamed


@pytest.mark.asyncio
async def test_narration_delta_filter_hides_single_markdown_protocol_line():
    received: list[str] = []

    async def on_delta(text: str) -> None:
        received.append(text)

    filt = _NarrationDeltaFilter(on_delta)
    await filt.feed("**SANCheck:web_79cf963c:")
    await filt.feed("1d6** | 目睹荧绿眼睛，进行理智检定。\n\n后续叙事。")
    await filt.flush()

    streamed = "".join(received)
    assert "SANCheck" not in streamed
    assert "web_79cf963c" not in streamed
    assert "目睹荧绿眼睛" in streamed
    assert "后续叙事" in streamed


@pytest.mark.asyncio
async def test_call_llm_with_tag_retry_streams_narration_only():
    content = "古墓深处传来低语。\n---\nKEY_ITEM:u1:青铜钥匙"
    llm = StreamingLLM([content])
    instance = GameInstance(game_key=("web", "stream", "bot"))

    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    resets: list[int] = []

    async def on_reset() -> None:
        resets.append(1)

    response, _data = await call_llm_with_tag_retry(
        llm, instance, "GM", "ctx", "hp_based", "", 1024, "actions",
        on_delta=on_delta, on_reset=on_reset,
    )

    streamed = "".join(deltas)
    assert "古墓深处传来低语。" in streamed
    assert "KEY_ITEM" not in streamed
    assert "---" not in streamed
    assert resets == []
    assert llm.stream_calls == 1
    assert "青铜钥匙" in response.content


@pytest.mark.asyncio
async def test_call_llm_with_tag_retry_repairs_malformed_protocol_once():
    malformed = "**SANCheck:web_legacy:1d6** | 你看见了不可名状之物。"
    repaired = "你看见了不可名状之物。\n---\nSAN_CHECK:web_legacy:1d6"
    llm = StreamingLLM([malformed, repaired])
    instance = GameInstance(game_key=("web", "stream", "bot"))
    deltas: list[str] = []
    resets: list[int] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    async def on_reset() -> None:
        resets.append(1)
        deltas.clear()

    response, data = await call_llm_with_tag_retry(
        llm, instance, "GM", "ctx", "hp_based", "", 1024, "actions",
        on_delta=on_delta, on_reset=on_reset,
    )

    assert llm.stream_calls == 2
    assert resets == [1]
    assert "SANCheck" not in "".join(deltas)
    assert "web_legacy" not in "".join(deltas)
    assert response.narration == "你看见了不可名状之物。"
    assert data["state_update"]["players"]["web_legacy"]["san_check_loss"] == "1d6"


@pytest.mark.asyncio
async def test_call_llm_with_tag_retry_stream_length_budgets_reach_four_times():
    llm = LengthRetryStreamingLLM([None, None, "最终叙事。"])
    instance = GameInstance(game_key=("web", "stream", "bot"))
    deltas: list[str] = []
    resets: list[int] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    async def on_reset() -> None:
        resets.append(1)
        deltas.clear()

    response, _data = await call_llm_with_tag_retry(
        llm, instance, "GM", "ctx", "hp_based", "", 1024, "actions",
        on_delta=on_delta, on_reset=on_reset,
    )

    assert llm.max_tokens == [1024, 2048, 4096]
    assert len(resets) == 2
    assert "".join(deltas) == "最终叙事。"
    assert response.narration == "最终叙事。"
    assert response.token_budget_initial == 1024
    assert response.token_budget_used == 4096


@pytest.mark.asyncio
async def test_call_llm_with_tag_retry_stream_length_stops_after_four_times():
    llm = LengthRetryStreamingLLM([None])
    instance = GameInstance(game_key=("web", "stream", "bot"))
    resets: list[int] = []

    async def on_delta(_text: str) -> None:
        pass

    async def on_reset() -> None:
        resets.append(1)

    with pytest.raises(OutputTruncatedError):
        await call_llm_with_tag_retry(
            llm, instance, "GM", "ctx", "hp_based", "", 1024, "actions",
            on_delta=on_delta, on_reset=on_reset,
        )

    assert llm.max_tokens == [1024, 2048, 4096]
    assert len(resets) == 2


@pytest.mark.asyncio
async def test_stream_length_retry_does_not_consume_dice_rewrite():
    bad = "你没能打开门。"
    good = "你成功打开了门。"
    llm = LengthRetryStreamingLLM([None, bad, good])
    instance = GameInstance(game_key=("web", "stream", "bot"))
    resets: list[int] = []

    async def on_delta(_text: str) -> None:
        pass

    async def on_reset() -> None:
        resets.append(1)

    response, _data = await call_llm_with_tag_retry(
        llm, instance, "GM", "ctx", "hp_based", "大成功", 1024, "actions",
        on_delta=on_delta, on_reset=on_reset,
    )

    assert llm.max_tokens == [1024, 2048, 1024]
    assert len(resets) == 2
    assert response.narration == good
    assert response.token_budget_initial == 1024
    assert response.token_budget_used == 2048


@pytest.mark.asyncio
async def test_call_llm_with_tag_retry_resets_on_dice_contradiction():
    bad = "你没能打开门。"   # 大成功 + 失败词 -> 矛盾
    good = "你成功打开了门。"
    llm = StreamingLLM([bad, good])
    instance = GameInstance(game_key=("web", "stream", "bot"))

    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    resets: list[int] = []

    async def on_reset() -> None:
        resets.append(1)

    response, _data = await call_llm_with_tag_retry(
        llm, instance, "GM", "ctx", "hp_based", "大成功", 1024, "actions",
        on_delta=on_delta, on_reset=on_reset,
    )

    assert llm.stream_calls == 2
    assert len(resets) == 1  # 第二次尝试前应清空前端流式缓冲
    assert "成功" in response.narration


@pytest.mark.asyncio
async def test_call_llm_with_tag_retry_caps_at_one_retry():
    # 骰子持续矛盾时，最多 2 次尝试（1 次重试），不会无限重试
    bad = "你没能打开门。"  # 大成功 + 失败词 -> 持续矛盾
    llm = StreamingLLM([bad])
    instance = GameInstance(game_key=("web", "stream", "bot"))

    async def on_delta(_text: str) -> None:
        pass

    resets: list[int] = []

    async def on_reset() -> None:
        resets.append(1)

    response, _data = await call_llm_with_tag_retry(
        llm, instance, "GM", "ctx", "hp_based", "大成功", 1024, "actions",
        on_delta=on_delta, on_reset=on_reset,
    )

    assert llm.stream_calls == 2  # range(2): 初始 + 1 次重试
    assert len(resets) == 1
    # 重试用尽后接受最后输出（仍是矛盾叙事，但不再重试）
    assert "没能" in response.narration
