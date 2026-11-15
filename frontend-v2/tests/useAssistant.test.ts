import { nextTick, watchEffect } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  agentStream: vi.fn(),
  confirmStream: vi.fn(),
  resumeStream: vi.fn(),
  review: vi.fn(),
  session: vi.fn(),
  authorize: vi.fn(),
  cancel: vi.fn(),
}))

vi.mock('../src/api/assistant', () => ({
  streamAssistantAgent: mocks.agentStream,
  streamAssistantAgentConfirm: mocks.confirmStream,
  streamAssistantAgentResume: mocks.resumeStream,
  reviewAssistantAgent: mocks.review,
  getAssistantAgentSession: mocks.session,
  authorizeAssistantAgent: mocks.authorize,
  cancelAssistantAgent: mocks.cancel,
}))

import { useAssistant } from '../src/composables/useAssistant'

// SSE 迁移：confirm/resume 走流式封装，结果经 onConfirmResult 交付。
function streamConfirmResult(result: Record<string, unknown>) {
  mocks.confirmStream.mockImplementationOnce(async (
    _sessionId: string, _approvalId: string, _digest: string, _approved: boolean,
    handlers: { onConfirmResult?: (value: unknown) => void },
  ) => {
    handlers.onConfirmResult?.(result)
  })
}
function streamResumeResult(result: Record<string, unknown>) {
  mocks.resumeStream.mockImplementationOnce(async (
    _sessionId: string, _approvalId: string, _digest: string,
    handlers: { onConfirmResult?: (value: unknown) => void },
  ) => {
    handlers.onConfirmResult?.(result)
  })
}

describe('useAssistant request history', () => {
  beforeEach(() => {
    mocks.agentStream.mockReset()
    mocks.agentStream.mockResolvedValue(undefined)
    mocks.confirmStream.mockReset()
    mocks.resumeStream.mockReset()
    mocks.review.mockReset()
    mocks.session.mockReset()
    mocks.authorize.mockReset()
    mocks.cancel.mockReset().mockResolvedValue({ ok: true, cancelled: true })
    localStorage.clear()
    useAssistant().clear()
  })

  it('sends a non-empty user message as the final request item', async () => {
    const assistant = useAssistant()
    await assistant.send('  怎么配置模型接口？  ', 'zh-CN')

    const history = mocks.agentStream.mock.calls[0][0]
    expect(history).toEqual([{ role: 'user', content: '怎么配置模型接口？' }])
    expect(history.at(-1)).toMatchObject({ role: 'user' })
  })

  it('keeps the stream active after EOF while a delta is progressively displayed', async () => {
    vi.useFakeTimers()
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onDelta?: (delta: string) => void }) => {
      handlers.onDelta?.('你好🙂世界')
    })
    const assistant = useAssistant()
    const observed: string[] = []
    const stopObserving = watchEffect(() => { observed.push(assistant.messages.value.at(-1)?.content || '') })
    const request = assistant.send('逐字显示', 'zh-CN')
    await vi.runOnlyPendingTimersAsync()
    await Promise.resolve()

    expect(assistant.streaming.value).toBe(true)
    expect(assistant.messages.value.at(-1)?.content.length).toBeGreaterThan(0)
    expect(assistant.messages.value.at(-1)?.content).not.toBe('你好🙂世界')
    await nextTick()
    expect(observed.some(content => content.length > 0 && content.length < '你好🙂世界'.length)).toBe(true)

    await vi.runAllTimersAsync()
    await request
    expect(assistant.messages.value.at(-1)?.content).toBe('你好🙂世界')
    expect(assistant.streaming.value).toBe(false)
    stopObserving()
    vi.useRealTimers()
  })

  it('flushes received text and preserves the error after a failed stream', async () => {
    vi.useFakeTimers()
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onDelta?: (delta: string) => void }) => {
      handlers.onDelta?.('错误前已收到🙂')
      throw new Error('网络中断')
    })
    const assistant = useAssistant()
    await assistant.send('失败', 'zh-CN')
    expect(assistant.messages.value.at(-1)?.content).toBe('错误前已收到🙂')
    expect(assistant.messages.value.at(-1)?.error).toBe('网络中断')
    expect(assistant.streaming.value).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
    vi.useRealTimers()
  })

  it('keeps Unicode order across multiple deltas and completes a long reply within a bounded display window', async () => {
    vi.useFakeTimers()
    const longReply = '🙂世界'.repeat(8_000)
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onDelta?: (delta: string) => void }) => {
      handlers.onDelta?.(longReply.slice(0, 9_000))
      handlers.onDelta?.(longReply.slice(9_000))
    })
    const assistant = useAssistant()
    let completed = false
    const request = assistant.send('长回复', 'zh-CN').then(() => { completed = true })
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(completed).toBe(true)
    await request
    expect(assistant.messages.value.at(-1)?.content).toBe(longReply)
    expect(assistant.streaming.value).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
    vi.useRealTimers()
  })

  it('flushes received text when stopped and marks the assistant message stopped', async () => {
    vi.useFakeTimers()
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onDelta?: (delta: string) => void; onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }, signal: AbortSignal) => {
      handlers.onDelta?.('收到但未完成')
      await new Promise<never>((_resolve, reject) => signal.addEventListener('abort', () => {
        reject(new DOMException('Aborted', 'AbortError'))
        handlers.onDelta?.('晚到的旧文字')
        handlers.onAgentEvent?.('status', { message: '旧状态' })
      }, { once: true }))
    })
    const assistant = useAssistant()
    const request = assistant.send('停止', 'zh-CN')
    await Promise.resolve()
    assistant.stop()
    await vi.runAllTimersAsync()
    await request

    expect(assistant.messages.value.at(-1)?.content).toBe('收到但未完成')
    expect(assistant.messages.value.at(-1)?.stopped).toBe(true)
    expect(assistant.status.value).toBe('')
    expect(assistant.streaming.value).toBe(false)
    vi.useRealTimers()
  })

  it('resets queued text and fences a late delta after clear', async () => {
    vi.useFakeTimers()
    let release!: () => void
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onDelta?: (delta: string) => void; onReset?: () => void }) => {
      handlers.onDelta?.('旧文本')
      handlers.onReset?.()
      handlers.onDelta?.('新文本')
      await new Promise<void>(resolve => { release = resolve })
    })
    const assistant = useAssistant()
    const request = assistant.send('重置', 'zh-CN')
    await Promise.resolve()
    await vi.runOnlyPendingTimersAsync()
    expect(assistant.messages.value.at(-1)?.content).toContain('新')
    expect(assistant.messages.value.at(-1)?.content).not.toContain('旧')
    assistant.clear()
    release()
    await request
    await vi.runAllTimersAsync()
    expect(assistant.messages.value).toEqual([])
    expect(assistant.streaming.value).toBe(false)
    vi.useRealTimers()
  })

  it('drops an empty assistant placeholder left by an earlier failed request', async () => {
    mocks.agentStream.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(undefined)
    const assistant = useAssistant()

    await assistant.send('第一个问题', 'zh-CN')
    await assistant.send('第二个问题', 'zh-CN')

    const secondHistory = mocks.agentStream.mock.calls[1][0]
    expect(secondHistory).toEqual([
      { role: 'user', content: '第一个问题' },
      { role: 'user', content: '第二个问题' },
    ])
    expect(secondHistory.at(-1)).toEqual({ role: 'user', content: '第二个问题' })
  })

  it('uses the agent stream with a sanitized route context and persists the session', async () => {
    window.location.hash = '#/create?game_key=g-1&password=secret&world_id=w-1'
    mocks.agentStream.mockImplementation(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-1' })
      handlers.onAgentEvent?.('status', { message: 'working' })
    })
    const assistant = useAssistant()

    await assistant.send('创建游戏', 'zh-CN')

    expect(mocks.agentStream).toHaveBeenCalledOnce()
    expect(mocks.agentStream.mock.calls[0][4]).toBeUndefined()
    expect(mocks.agentStream.mock.calls[0][5]).toEqual({ route: 'create', game_key: 'g-1', world_id: 'w-1' })
    expect(assistant.sessionId.value).toBe('s-1')
    expect(assistant.status.value).toBe('working')
  })

  it('clears server authorization on a null SSE authorization and successful stop', async () => {
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }, signal: AbortSignal) => {
      handlers.onAgentEvent?.('session', { session_id: 's-auth' })
      handlers.onAgentEvent?.('authorization', { authorization: { mode: 'task', scope: {}, domains: [], expires_at: 1, max_steps: 8, remaining_steps: 8 } })
      handlers.onAgentEvent?.('authorization', { authorization: null })
      await new Promise<never>((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
    })
    const assistant = useAssistant()
    const request = assistant.send('停止任务', 'zh-CN')
    await Promise.resolve()
    expect(assistant.authorization.value).toBeNull()
    assistant.stop()
    await request
    await Promise.resolve()
    expect(mocks.cancel).toHaveBeenCalledWith('s-auth')
    expect(assistant.authorization.value).toBeNull()
  })

  it('releases authorization busy when a reset invalidates an authorization request', async () => {
    let release!: (value: unknown) => void
    mocks.authorize.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
    const assistant = useAssistant()
    const request = assistant.authorize('task')
    await Promise.resolve()
    expect(assistant.authorizationBusy.value).toBe(true)
    assistant.clear()
    release({ ok: true, session_id: 'late', authorization: null })
    await request
    expect(assistant.authorizationBusy.value).toBe(false)
  })

  it('advances approval task state after confirm success or rejection', async () => {
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-task' })
      handlers.onAgentEvent?.('task', { status: 'approval_required', completed_steps: 1, total_steps: 3 })
      handlers.onAgentEvent?.('approval', { approval_id: 'a-1', plan_digest: 'digest-1', tool: 'write_book' })
    })
    streamConfirmResult({ ok: true, result: { changed: true } })
    const assistant = useAssistant()
    await assistant.send('执行一步', 'zh-CN')
    await assistant.confirmApproval(true)
    expect(assistant.task.value).toMatchObject({ status: 'completed', completed_steps: 2 })

    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('task', { status: 'approval_required', completed_steps: 2, total_steps: 3 })
      handlers.onAgentEvent?.('approval', { approval_id: 'a-2', plan_digest: 'digest-2', tool: 'write_book' })
    })
    streamConfirmResult({ ok: true, cancelled: true })
    await assistant.send('拒绝一步', 'zh-CN')
    await assistant.confirmApproval(false)
    expect(assistant.task.value).toMatchObject({ status: 'cancelled', completed_steps: 2 })
  })

  it('marks approval task failed when the server reports a failed execution', async () => {
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-fail' })
      handlers.onAgentEvent?.('task', { status: 'approval_required', completed_steps: 0, total_steps: 2 })
      handlers.onAgentEvent?.('approval', { approval_id: 'a-fail', plan_digest: 'digest-fail', tool: 'write_book' })
    })
    streamConfirmResult({ ok: false, error: 'write failed' })
    const assistant = useAssistant()
    await assistant.send('失败一步', 'zh-CN')
    await assistant.confirmApproval(true)
    expect(assistant.task.value).toMatchObject({ status: 'failed', completed_steps: 0, error: 'write failed' })
  })

  it('keeps a partial batch plan pending and resumes it through the dedicated endpoint', async () => {
    const pending = {
      approval_id: 'a-batch', plan_digest: 'digest-batch', tool: 'write_lorebook_entries', resumable: true,
      execution_status: 'partial', execution_state: { report: { status: 'partial', changed_count: 2 } },
      preview: { targets: [{ id: 'book-1', label: 'Book 1' }] },
    }
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-batch' })
      handlers.onAgentEvent?.('task', { status: 'approval_required', completed_steps: 0, total_steps: 1 })
      handlers.onAgentEvent?.('approval', { approval_id: 'a-batch', plan_digest: 'digest-batch', tool: 'write_lorebook_entries', preview: pending.preview })
    })
    streamConfirmResult({ ok: true, partial: true, pending })
    streamResumeResult({ ok: true, result: { status: 'completed' } })
    const assistant = useAssistant()

    await assistant.send('批量执行', 'zh-CN')
    await assistant.confirmApproval(true)
    expect(assistant.pendingApproval.value).toMatchObject({ approval_id: 'a-batch', resumable: true })
    expect(assistant.task.value).toMatchObject({ status: 'paused' })

    await assistant.resumeApproval()
    expect(mocks.confirmStream).toHaveBeenCalledOnce()
    expect(mocks.resumeStream).toHaveBeenCalledWith('s-batch', 'a-batch', 'digest-batch', expect.any(Object))
    expect(assistant.task.value).toMatchObject({ status: 'completed', completed_steps: 1 })
    expect(assistant.pendingApproval.value).toBeNull()
  })

  it('keeps an expired resumable plan available for explicit resume', async () => {
    const pending = {
      approval_id: 'a-expired-resume', plan_digest: 'digest-expired-resume', tool: 'set_lorebooks_visibility',
      resumable: true, execution_status: 'partial', expires_at: Math.floor(Date.now() / 1000) - 30 * 60,
      execution_state: { report: { status: 'partial', changed_count: 2 } },
    }
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-expired-resume' })
      handlers.onAgentEvent?.('approval', pending)
    })
    streamResumeResult({ ok: true, result: { status: 'completed' } })
    const assistant = useAssistant()

    await assistant.send('继续暂停批量', 'zh-CN')
    expect(assistant.pendingApproval.value).toMatchObject({ resumable: true })
    expect(assistant.pendingApproval.value?.expired).not.toBe(true)

    await assistant.resumeApproval()
    expect(mocks.resumeStream).toHaveBeenCalledWith('s-expired-resume', pending.approval_id, pending.plan_digest, expect.any(Object))
  })

  it('caches an ok-false partial response with pending state without retrying confirm', async () => {
    const pending = { approval_id: 'a-ok-false', plan_digest: 'digest-ok-false', tool: 'write_lorebook_entries', resumable: true, execution_status: 'partial', execution_state: { report: { status: 'partial' } } }
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-ok-false' })
      handlers.onAgentEvent?.('approval', { approval_id: pending.approval_id, plan_digest: pending.plan_digest, tool: pending.tool })
    })
    streamConfirmResult({ ok: false, pending, result: { status: 'partial' } })
    const assistant = useAssistant()
    await assistant.send('保留部分结果', 'zh-CN')
    const first = await assistant.confirmApproval(true)
    const second = await assistant.confirmApproval(true)
    expect(first).toMatchObject({ ok: false, pending: { resumable: true } })
    expect(second).toMatchObject({ ok: false, pending: { resumable: true } })
    expect(mocks.confirmStream).toHaveBeenCalledOnce()
    expect(assistant.pendingApproval.value).toMatchObject({ approval_id: pending.approval_id, resumable: true })
  })

  it('keeps the report and resumable plan after a failed resume', async () => {
    const pending = {
      approval_id: 'a-retry', plan_digest: 'digest-retry', tool: 'write_lorebook_entries', resumable: true,
      execution_state: { report: { status: 'partial', changed_count: 1 } },
    }
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-retry' })
      handlers.onAgentEvent?.('approval', pending)
    })
    mocks.resumeStream.mockRejectedValueOnce(new Error('resume offline'))
    const assistant = useAssistant()
    await assistant.send('重试批量', 'zh-CN')
    await assistant.resumeApproval()
    expect(mocks.confirmStream).not.toHaveBeenCalled()
    expect(assistant.pendingApproval.value).toMatchObject({ approval_id: 'a-retry', execution_state: pending.execution_state })
    expect(assistant.actionError.value).toBe('resume offline')
  })

  it('sends an explicit rejection for a paused plan even when a cached partial receipt exists', async () => {
    const pending = { approval_id: 'a-cancel', plan_digest: 'digest-cancel', tool: 'write_lorebook_entries', resumable: true, execution_status: 'partial' }
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-cancel' })
      handlers.onAgentEvent?.('approval', pending)
    })
    streamConfirmResult({ ok: true, partial: true, pending })
    streamConfirmResult({ ok: true, cancelled: true })
    const assistant = useAssistant()
    await assistant.send('取消暂停批量', 'zh-CN')
    await assistant.confirmApproval(true)
    await assistant.confirmApproval(false)
    expect(mocks.confirmStream).toHaveBeenNthCalledWith(2, 's-cancel', 'a-cancel', 'digest-cancel', false, expect.any(Object))
    expect(assistant.pendingApproval.value).toBeNull()
  })

  it('does not send context for nested module routes', async () => {
    window.location.hash = '#/modules/private?game=g-1'
    const assistant = useAssistant()
    await assistant.send('查看模块', 'zh-CN')
    expect(mocks.agentStream.mock.calls[0][5]).toBeUndefined()
  })

  it('keeps the latest non-empty question while bounding request history', async () => {
    const assistant = useAssistant()
    for (let index = 0; index < 25; index += 1) await assistant.send(`question-${index}-${'x'.repeat(9000)}`, 'en')
    const history = mocks.agentStream.mock.calls.at(-1)?.[0] as Array<{ role: string; content: string }>
    expect(history.length).toBeLessThanOrEqual(20)
    expect(history.reduce((sum, item) => sum + item.content.length, 0)).toBeLessThanOrEqual(24_000)
    expect(history.every(item => item.content.length <= 8_000)).toBe(true)
    expect(history.at(-1)?.content).toContain('question-24')
  })

  it('ignores expired approvals and clears pending approval when a draft is reviewed', async () => {
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-approval' })
      handlers.onAgentEvent?.('approval', { approval_id: 'a-old', plan_digest: 'p-old', tool: 'save', expires_at: Math.floor(Date.now() / 1000) - 1 })
    })
    const assistant = useAssistant()
    await assistant.send('draft', 'en')
    expect(assistant.pendingApproval.value).toMatchObject({ approval_id: 'a-old', expired: true })

    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('approval', { approval_id: 'a-live', plan_digest: 'p-live', tool: 'save', expires_at: Math.floor(Date.now() / 1000) + 60 })
    })
    await assistant.send('next', 'en')
    expect(assistant.pendingApproval.value?.approval_id).toBe('a-live')
    mocks.review.mockResolvedValueOnce({ ok: true, draft_id: 'd-1', review: { ok: true } })
    await assistant.reviewDraft('d-1', { items: [] })
    expect(assistant.pendingApproval.value).toBeNull()
  })

  it('cancels the server proposal when a draft is edited and fences rerun proposals', async () => {
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-dirty' })
      handlers.onAgentEvent?.('approval', { approval_id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', plan_digest: 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', tool: 'save_content', arguments: { draft_id: 'd-dirty' } })
    }).mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('approval', { approval_id: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', plan_digest: 'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', tool: 'save_content', arguments: { draft_id: 'd-dirty' } })
    })
    const assistant = useAssistant()
    await assistant.send('draft', 'en')
    await assistant.invalidateApproval('d-dirty')
    expect(mocks.confirmStream).toHaveBeenCalledWith('s-dirty', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', false, expect.any(Object))
    await assistant.send('rerun', 'en')
    expect(assistant.pendingApproval.value).toMatchObject({ approval_id: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', recovery_required: true, expired: true })
    await assistant.confirmApproval(true)
    await assistant.resumeApproval()
    expect(mocks.confirmStream).toHaveBeenCalledTimes(1)
    expect(mocks.resumeStream).not.toHaveBeenCalled()
  })

  it('restores a failed receipt response after a confirmation network failure', async () => {
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('session', { session_id: 's-receipt' })
      handlers.onAgentEvent?.('approval', { approval_id: 'cccccccccccccccccccccccccccccccc', plan_digest: 'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', tool: 'save_content' })
    })
    mocks.confirmStream.mockRejectedValueOnce(new Error('offline'))
    mocks.session.mockResolvedValueOnce({ session_id: 's-receipt', pending: null, drafts: {}, status: 'idle', receipts: [{ approval_id: 'cccccccccccccccccccccccccccccccc', plan_digest: 'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', result: { partial: true }, response: { ok: false, error: 'server failed', result: { partial: true } } }] })
    const assistant = useAssistant()
    await assistant.send('save', 'en')
    const result = await assistant.confirmApproval(true)
    expect(result).toMatchObject({ ok: false, error: 'server failed' })
    expect(assistant.actionError.value).toBe('server failed')
  })

  it('shows failed and partial tool observations as failures instead of completed actions', async () => {
    mocks.agentStream.mockImplementationOnce(async (_messages: unknown, _locale: unknown, handlers: { onAgentEvent?: (event: string, payload: Record<string, unknown>) => void }) => {
      handlers.onAgentEvent?.('tool_result', { tool: 'write_lorebook_entries', result: { ok: false, partial: true, error: 'disk full' } })
    })
    const assistant = useAssistant()
    await assistant.send('write entries', 'en')
    expect(assistant.messages.value.at(-1)?.events?.[0].payload).toMatchObject({ ok: false, partial: true })
  })

  it('does not let an old review finally clear a new action busy state', async () => {
    let resolveFirst!: (value: unknown) => void
    let resolveSecond!: (value: unknown) => void
    mocks.review.mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve })).mockImplementationOnce(() => new Promise(resolve => { resolveSecond = resolve }))
    const assistant = useAssistant()
    assistant.sessionId.value = 's-fence'
    const first = assistant.reviewDraft('d-1', { items: [] })
    assistant.clear()
    assistant.sessionId.value = 's-fence-2'
    const second = assistant.reviewDraft('d-2', { items: [] })
    resolveFirst({ ok: true, draft_id: 'd-1', review: { ok: true } })
    await first
    expect(assistant.actionBusy.value).toBe(true)
    resolveSecond({ ok: true, draft_id: 'd-2', review: { ok: true } })
    await second
    expect(assistant.actionBusy.value).toBe(false)
  })
})
