"""#2 后台摘要延后：summarize 不应阻塞 process_round 返回。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.commands.round_processor import RoundProcessor


class _FakeLLM:
    def __init__(self, content: str = '{"narrative":"摘要内容","key_facts":[]}'):
        self.content = content
        self.called = False

    async def call(self, system_prompt="", user_message="", *, temperature=0.7, max_tokens=1024, **kwargs):
        self.called = True
        return SimpleNamespace(content=self.content, narration="摘要内容")


def _make_processor(content: str = '{"narrative":"摘要内容","key_facts":[]}') -> RoundProcessor:
    proc = RoundProcessor.__new__(RoundProcessor)
    proc.llm_client = _FakeLLM(content)
    proc.summary_max_tokens = 400
    proc._pending_summary_tasks = set()
    return proc


def _make_instance(round_number: int) -> SimpleNamespace:
    instance = SimpleNamespace(
        game_key=("web", "test"),
        round_number=round_number,
        language="zh",
        log=[{"round": 1, "actions": [{"user_id": "p1", "text": "环顾四周"}], "gm_response": "一个房间"}],
        summary={},
        key_facts=[],
    )
    instance.set_summary_narrative = lambda narrative: instance.summary.__setitem__("narrative", narrative)
    instance.set_key_facts = lambda facts: setattr(instance, "key_facts", list(facts))
    return instance


@pytest.mark.asyncio
async def test_summary_deferred_when_due():
    proc = _make_processor()
    inst = _make_instance(10)

    task = proc._maybe_schedule_summary(inst, "gm_prompt")
    assert task is not None
    assert task in proc._pending_summary_tasks
    # 任务已调度但尚未执行：_maybe_schedule_summary 同步返回，未 await LLM
    assert proc.llm_client.called is False

    await task
    assert proc.llm_client.called is True
    assert inst.summary["narrative"] == "摘要内容"
    # done 回调已从集合中清理
    assert task not in proc._pending_summary_tasks


@pytest.mark.asyncio
async def test_no_summary_when_not_due():
    proc = _make_processor()
    inst = _make_instance(5)

    task = proc._maybe_schedule_summary(inst, "gm_prompt")
    assert task is None
    assert len(proc._pending_summary_tasks) == 0
    assert proc.llm_client.called is False


@pytest.mark.asyncio
async def test_summarize_background_swallows_exception():
    # summarize 在自身 try 之前就抛（build_summary_input 拿到非可迭代 log），
    # _summarize_background 必须兜住，避免后台任务 crash 事件循环。
    proc = _make_processor()
    inst = _make_instance(10)
    inst.log = None

    await proc._summarize_background(inst, "gm_prompt", 10)  # 不应抛
