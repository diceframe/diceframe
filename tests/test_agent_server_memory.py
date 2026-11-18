"""服务端会话记忆与 Creator 状态卡（agent 失忆根治，P1）。"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.creator.projects import CreatorProjectStore
from src.llm.client import LLMToolResponse
from src.webui.services.assistant_agent import (
    MEMORY_RECENT,
    AgentDependencies,
    AgentRuntime,
    _creator_project_card,
    _merge_memory,
    _memory_conversation,
)
from src.webui.services.assistant_agent_application import application_tools
from tests.test_assistant_agent_application import deps


class Planner:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests: list[str] = []

    async def call_tools(self, system, user, **kwargs):
        self.requests.append(user)
        return LLMToolResponse([], 10, 'test', content=next(self.replies))


def runtime_with(tmp_path, planner, store=None):
    return AgentRuntime(AgentDependencies(
        lambda: planner, lambda _: None,
        application_tools(deps(creator_projects=store)), tmp_path / 'agent',
        creator_projects=store))


async def drive(runtime, text, sid='', context=None):
    events = []

    async def emit(name, payload):
        events.append((name, deepcopy(payload)))

    await runtime.run('alice', [{'role': 'user', 'content': text}], 'en',
                      context or {'route': 'creator'}, sid, emit)
    if not sid:
        sid = next(payload['session_id'] for name, payload in events if name == 'session')
    return sid, events


def seeded_store(tmp_path):
    store = CreatorProjectStore(tmp_path / 'projects.json')
    pid = store.create('alice', 'Well')['project']['id']
    store.update('alice', pid, expected_revision=0, files={
        'adventures/p/adventure.json': json.dumps({
            'chapters': [{'id': 'c1'}],
            'nodes': [{'id': 's', 'type': 'scene'}, {'id': 'g', 'type': 'encounter'}],
        }, ensure_ascii=False),
        'content/npcs/mara.json': '{}',
        'README.md': 'x',
    })
    return store, pid


# ── 纯函数 ────────────────────────────────────────────────

def test_merge_memory_appends_dedupes_and_bootstraps():
    memory: list = []
    _merge_memory(memory, [{'role': 'user', 'content': 'a'},
                           {'role': 'assistant', 'content': 'A'},
                           {'role': 'user', 'content': 'b'}])
    # bootstrap：新会话用客户端历史补种 + 追加本轮 b
    assert [turn['content'] for turn in memory] == ['a', 'A', 'b']
    _merge_memory(memory, [{'role': 'user', 'content': 'b'}])   # 重试同一条
    assert memory[-1]['content'] == 'b' and len(memory) == 3
    _merge_memory(memory, [{'role': 'user', 'content': ''}])    # 空消息不入账
    assert len(memory) == 3


def test_memory_conversation_folds_older_turns_into_card(monkeypatch):
    import src.webui.services.assistant_agent as module
    monkeypatch.setattr(module, 'MEMORY_RECENT', 2)
    memory = []
    for i in range(3):
        memory.append({'role': 'user', 'content': f'请求{i}'})
        memory.append({'role': 'assistant', 'content': f'回答{i}\n细节'})
    conversation, card = _memory_conversation(memory)
    assert card is not None
    assert card['user_requests'] == ['请求0', '请求1']
    assert card['assistant_outcomes'] == ['回答0', '回答1']
    assert len(conversation) == 2 and conversation[0]['content'].startswith('请求2')


def test_creator_project_card_counts_match_overview_semantics(tmp_path):
    store, pid = seeded_store(tmp_path)
    card = _creator_project_card(store, 'alice', pid)
    assert card['id'] == pid and card['revision'] == 1 and card['file_count'] == 3
    assert card['counts'] == {'chapters': 1, 'scenes': 2, 'npcs': 1}  # encounter 算场景；content/npcs 算 NPC
    assert {row['path'] for row in card['files']} == {
        'adventures/p/adventure.json', 'content/npcs/mara.json', 'README.md'}
    # 列表形态：不带 project_id 时给最近工程
    listing = _creator_project_card(store, 'alice', '')
    assert listing['projects'][0]['name'] == 'Well'
    # 他人工程不可见：fail-soft
    assert _creator_project_card(store, 'bob', pid)['error']


# ── runtime 集成 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_survives_when_client_sends_only_new_message(tmp_path):
    planner = Planner(['第一轮的回答，关于水井', '第二轮回答'])
    runtime = runtime_with(tmp_path, planner)
    sid, _ = await drive(runtime, '描述那口井')
    # 客户端最坏情况：localStorage 丢了，只发新消息
    sid, _ = await drive(runtime, '继续', sid)
    payload = planner.requests[-1]
    assert '描述那口井' in payload and '第一轮的回答' in payload
    snap = runtime.snapshot('alice', sid)
    roles = [turn['role'] for turn in snap['memory']]
    assert roles == ['user', 'assistant', 'user', 'assistant']


@pytest.mark.asyncio
async def test_long_history_folds_into_card_in_payload(tmp_path, monkeypatch):
    import src.webui.services.assistant_agent as module
    monkeypatch.setattr(module, 'MEMORY_RECENT', 4)
    planner = Planner([f'回答{i}' for i in range(4)])
    runtime = runtime_with(tmp_path, planner)
    sid = ''
    for i in range(4):
        sid, _ = await drive(runtime, f'需求{i}', sid)
    payload = planner.requests[-1]
    assert 'memory_card' in payload and '需求0' in payload
    assert 'user_requests' in payload and 'assistant_outcomes' in payload


@pytest.mark.asyncio
async def test_creator_route_injects_project_and_list_cards(tmp_path):
    store, pid = seeded_store(tmp_path)
    planner = Planner(['好的', '好的'])
    runtime = runtime_with(tmp_path, planner, store)
    # 工程上下文：状态卡随首轮注入（payload 是 JSON-in-JSON，键名带转义引号）
    sid, _ = await drive(runtime, '加点内容', context={'route': 'creator', 'project_id': pid})
    payload = planner.requests[-1]
    assert '\\"counts\\"' in payload and 'adventures/p/adventure.json' in payload
    # 列表上下文：最近工程卡注入
    await drive(runtime, '有哪些工程', sid=sid, context={'route': 'creator'})
    payload = planner.requests[-1]
    assert '\\"projects\\"' in payload and 'Well' in payload


def test_route_context_project_id_validation():
    from src.webui.routes.assistant_agent import _context
    ctx = _context({'route': 'creator', 'project_id': 'project-abc_123'})
    assert ctx['project_id'] == 'project-abc_123'
    with pytest.raises(ValueError):
        _context({'route': 'creator', 'project_id': '../evil'})
    with pytest.raises(ValueError):
        _context({'route': 'creator', 'project_id': 'nope'})


@pytest.mark.asyncio
async def test_latest_user_request_is_pinned_to_every_model_request(tmp_path):
    """工具往返变长后，最新用户指令仍钉在每次请求末尾（模型不得称没看到请求）。"""
    from src.webui.services.assistant_agent_framework import execute_agent

    class Capturing:
        def __init__(self):
            self.users = []

        async def call_tools(self, system, user, **kwargs):
            self.users.append(user)
            from src.llm.client import LLMToolResponse
            return LLMToolResponse([], 5, 'test', content='final: done')

    payload = {"conversation": [
        {"role": "user", "content": "第一轮的旧指令"},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "给我加一章 ch9 两个场景，改完直接汇报"},
    ], "page_context": {"route": "creator"}}
    import asyncio
    capturing = Capturing()
    async def emit(*_args):
        return None

    out = await execute_agent(capturing, [], "sys", payload, emit, 'zh-CN',
                              1024, 8, task_max_tokens=4096)
    assert 'final' in str(out)
    assert capturing.users, "模型请求未发出"
    for user in capturing.users:
        assert '给我加一章 ch9' in user, "最新用户指令未随请求注入"
