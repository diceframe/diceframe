"""Owner-scoped, lossless Creator reads at the real Agent/model boundary."""
from copy import deepcopy
import hashlib
import json

import pytest

from src.creator.projects import CreatorProjectStore
from src.llm.client import LLMToolResponse
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime
from src.webui.services.assistant_agent_framework import _bound_history
from tests.test_assistant_agent_application import deps, tool


def project(tmp_path, files):
    store = CreatorProjectStore(tmp_path / 'projects.json')
    project_id = store.create('alice', 'An adventure')['project']['id']
    store.update('alice', project_id, expected_revision=0, files=files)
    return store, project_id


def test_index_stays_small_and_revision_pins_pagination(tmp_path):
    source = 'not-in-file-index' * 5000
    files = {'adventures/core.json': source, **{f'docs/{n:02}.md': 'docs' for n in range(22)}}
    store, project_id = project(tmp_path, files)
    index = store.inspect('alice', project_id)['project']
    assert index['revision'] == 1
    assert index['digest'] == store.read('alice', project_id)['project']['digest']
    assert index['files'][0] == {'path': 'adventures/core.json', 'chars': len(source), 'bytes': len(source),
                                'file_hash': 'sha256:' + hashlib.sha256(source.encode()).hexdigest()}
    for _ in range(6):
        page = store.inspect('alice', project_id)['project']
        assert page == index and 'not-in-file-index' not in json.dumps(page)
        assert len(json.dumps(page)) < 2500
    paths = []
    while True:
        paths.extend(row['path'] for row in index['files'])
        if not index['has_more']:
            break
        index = store.inspect('alice', project_id, offset=index['next_offset'], expected_revision=1)['project']
    assert paths == sorted(files)
    with pytest.raises(ValueError):
        store.inspect('alice', project_id, offset=1)
    with pytest.raises(KeyError):
        store.inspect('bob', project_id)
    store.update('alice', project_id, expected_revision=1, files={'adventures/core.json': 'new'})
    with pytest.raises(ValueError, match='版本已变化'):
        store.inspect('alice', project_id, offset=8, expected_revision=1)


def test_long_file_round_trips_exact_unicode_offsets_and_revision(tmp_path):
    source = ('汉字🐉\\\n\t\r\x01"' * 2000) + 'END'
    store, project_id = project(tmp_path, {'adventures/core.json': source, 'notes.md': 'OTHER-FILE'})
    offset, chunks = 0, []
    while True:
        response = store.read_file('alice', project_id, expected_revision=1, path='adventures/core.json', offset=offset)
        assert response['offset'] == offset
        assert response['end_offset'] > offset
        assert response['total_chars'] == len(source)
        assert 'OTHER-FILE' not in json.dumps(response)
        assert len(json.dumps(response, ensure_ascii=False)) < 3500
        chunks.append(response['content'])
        if not response['has_more']:
            assert response['next_offset'] is None
            break
        offset = response['next_offset']
    assert ''.join(chunks) == source
    with pytest.raises(KeyError):
        store.read_file('bob', project_id, expected_revision=1, path='adventures/core.json')
    with pytest.raises(ValueError):
        store.read_file('alice', project_id, expected_revision=1, path='../etc')
    assert store.read('alice', project_id)['project']['files']['adventures/core.json'] == source
    store.update('alice', project_id, expected_revision=1, files={'adventures/core.json': 'new'})
    with pytest.raises(ValueError, match='版本已变化'):
        store.read_file('alice', project_id, expected_revision=1, path='adventures/core.json', offset=2)


@pytest.mark.asyncio
async def test_credential_like_source_is_not_exposed_even_in_short_range(tmp_path):
    value = 'Bearer ' + 'private-test-value-12456789'
    store, project_id = project(tmp_path, {'doc.md': 'prefix\n' + value, 'tokens.txt': 'plain-value'})
    dependencies = deps(creator_projects=store)
    index = await tool(dependencies, 'creator_project_inspect').handler({'project_id': project_id}, 'alice')
    assert index['project']['redacted_file_count'] == 1 and 'tokens.txt' not in str(index)
    adapter = tool(dependencies, 'creator_project_read_file')
    with pytest.raises(PermissionError):
        await adapter.handler({'project_id': project_id, 'expected_revision': 1, 'path': 'tokens.txt'}, 'alice')
    with pytest.raises(PermissionError, match='敏感') as exc:
        await adapter.handler({'project_id': project_id, 'expected_revision': 1,
                               'path': 'doc.md', 'offset': 9, 'max_chars': 5}, 'alice')
    assert 'private-test-value' not in str(exc.value)
    assert store.read('alice', project_id)['project']['files']['doc.md'].endswith(value)


def test_history_deduplicates_exact_ranges_and_retains_retrievable_identity(monkeypatch):
    def request(parts):
        return {'kind': 'request', 'parts': parts}
    chunk = {'ok': True, 'project_id': 'project-1234_abc', 'revision': 1, 'path': 'chapter.json',
             'file_hash': 'sha256:abc', 'offset': 0, 'end_offset': 2700, 'next_offset': 2700,
             'has_more': True, 'content': '细节🐉' * 900}
    older = dict(chunk, offset=2700, end_offset=5400, next_offset=5400)
    def returned(data, call_id):
        return request([{'part_kind': 'tool-return', 'tool_name': 'creator_project_read_file',
                         'tool_call_id': call_id, 'content': data}])
    spacer = [request([{'part_kind': 'user-prompt', 'content': 'gap'}]) for _ in range(6)]
    history = [request([{'part_kind': 'user-prompt', 'content': 'edit goal'}]),
               returned(older, 'old'), returned(chunk, 'first'), *spacer, returned(chunk, 'again')]
    parts = [m['parts'][0] for m in _bound_history(history)]
    by_id = {part.get('tool_call_id'): part['content'] for part in parts}
    assert by_id['again'] == chunk and by_id['old'] == older
    first = by_id['first']
    assert first['content_omitted'] and 'content' not in first
    assert {key: first[key] for key in ('revision', 'path', 'offset', 'end_offset', 'file_hash')} == {
        key: chunk[key] for key in ('revision', 'path', 'offset', 'end_offset', 'file_hash')}
    monkeypatch.setattr('src.webui.services.assistant_agent_framework.HISTORY_TOTAL_CHARS', 4700)
    packed = {m['parts'][0].get('tool_call_id'): m['parts'][0]['content'] for m in _bound_history(history)}
    assert packed['old']['content_omitted'] and packed['old']['offset'] == 2700
    assert packed['again'] == chunk


class Planner:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.requests = []

    async def call_tools(self, system, user, **kwargs):
        self.requests.append((system, user))
        entry = next(self.calls)
        if isinstance(entry, str):
            return LLMToolResponse([], 10, 'test', content=entry)
        name, args = entry
        return LLMToolResponse([{'name': name, 'arguments': args}], 10, 'test')


async def run(tmp_path, store, planner):
    from src.webui.services.assistant_agent_application import application_tools
    runtime = AgentRuntime(AgentDependencies(lambda: planner, lambda _: None,
                           application_tools(deps(creator_projects=store)), tmp_path / 'agent'))
    events = []
    async def emit(name, payload):
        events.append((name, deepcopy(payload)))
    await runtime.run('alice', [{'role': 'user', 'content': 'Edit the selected project'}],
                      'en', {'route': 'creator'}, '', emit)
    sid = next(payload['session_id'] for name, payload in events if name == 'session')
    return runtime, sid, events


@pytest.mark.asyncio
async def test_agent_reads_target_only_and_proposes_revision_checked_edit(tmp_path):
    title = 'Scene: Harbor\n'
    files = {'adventures/core.json': title, 'unrelated.md': 'UNRELATED-TEXT' * 3500}
    store, project_id = project(tmp_path, files)
    selection = {'project_id': project_id, 'path': 'adventures/core.json', 'expected_revision': 1}
    update = {'project_id': project_id, 'expected_revision': 1,
              'files': {'adventures/core.json': title.replace('Harbor', 'Lighthouse')}}
    planner = Planner([('creator_project_inspect', {'project_id': project_id}),
                       ('creator_project_read_file', selection), ('creator_project_update', update)])
    runtime, sid, events = await run(tmp_path, store, planner)
    assert 'creator_project_inspect' in planner.requests[0][0]
    assert all('UNRELATED-TEXT' not in payload for _, payload in planner.requests)
    history = json.loads(planner.requests[-1][1])['agent_history']
    observed = next(part['content'] for msg in history for part in msg['parts']
                    if part.get('tool_name') == 'creator_project_read_file' and part['part_kind'] == 'tool-return')
    assert observed['content'] == title and observed['revision'] == 1 and observed['has_more'] is False
    approval = next(payload for name, payload in events if name == 'approval')
    assert store.read('alice', project_id)['project']['files'] == files
    receipt = await runtime.confirm('alice', sid, approval['approval_id'], approval['plan_digest'], True)
    assert receipt['ok']
    assert store.read('alice', project_id)['project']['files'] == {**files, **update['files']}
    assert receipt['result']['project']['revision'] == 2
    planner.calls = iter(['Done'])
    async def no_events(*args):
        pass
    await runtime.run('alice', [{'role': 'user', 'content': 'What changed?'}], 'en', {'route': 'creator'}, sid, no_events)
    assert 'UNRELATED-TEXT' not in planner.requests[-1][1]


@pytest.mark.asyncio
async def test_partial_read_cannot_propose_whole_file_replacement(tmp_path):
    path = 'adventures/core.json'
    files = {path: 'scene details\n' * 300}
    store, project_id = project(tmp_path, files)
    identity = {'project_id': project_id, 'expected_revision': 1}
    planner = Planner([('creator_project_read_file', {**identity, 'path': path, 'max_chars': 15}),
                       ('creator_project_update', {**identity, 'files': {path: 'partial replacement'}}),
                       'Stopped without writing.'])
    runtime, sid, events = await run(tmp_path, store, planner)
    assert not any(name == 'approval' for name, _ in events)
    assert runtime.snapshot('alice', sid)['pending'] is None
    assert 'Only part of the source was read' in planner.requests[-1][1]
    assert store.read('alice', project_id)['project']['files'] == files


@pytest.mark.asyncio
async def test_complete_multi_page_source_can_reach_approval(tmp_path):
    path = 'chapter.md'
    files = {path: 'abcdefghij'}
    store, project_id = project(tmp_path, files)
    identity = {'project_id': project_id, 'expected_revision': 1}
    selected = {**identity, 'path': path, 'max_chars': 5}
    replacement = 'abcdefghij and one new sentence'
    planner = Planner([('creator_project_read_file', selected),
                       ('creator_project_read_file', {**selected, 'offset': 5}),
                       ('creator_project_update', {**identity, 'files': {path: replacement}})])
    runtime, sid, events = await run(tmp_path, store, planner)
    approval = next(payload for name, payload in events if name == 'approval')
    store.update('alice', project_id, expected_revision=1, files={path: 'concurrent update'})
    with pytest.raises(ValueError, match='state changed'):
        await runtime.confirm('alice', sid, approval['approval_id'], approval['plan_digest'], True)
    assert store.read('alice', project_id)['project']['files'][path] == 'concurrent update'


@pytest.mark.asyncio
async def test_redundant_retrieval_pauses_but_a_new_range_resets_the_streak(tmp_path):
    store, project_id = project(tmp_path, {'chapter.json': 'story' * 1000})
    index = ('creator_project_inspect', {'project_id': project_id})
    legacy = ('creator_project_read', {'project_id': project_id})
    read = ('creator_project_read_file', {'project_id': project_id, 'expected_revision': 1,
                                         'path': 'chapter.json', 'max_chars': 89})
    planner = Planner([index, legacy, index, read, index, legacy, index])
    runtime, sid, events = await run(tmp_path, store, planner)
    assert len(planner.requests) == 7
    assert all('storystory' not in payload for _, payload in planner.requests[:4])
    task = runtime.snapshot('alice', sid)['task']
    assert task['status'] == 'paused' and task['pause_reason'] == 'repeated_creator_read'
    assert task['completed_steps'] == 0
    assert not any(name == 'approval' for name, _ in events)
    assert any(name == 'delta' and 'repeated reads' in payload['delta'] for name, payload in events)
