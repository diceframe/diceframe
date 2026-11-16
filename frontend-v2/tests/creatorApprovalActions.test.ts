import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useCreatorAssistant } from '../src/composables/useCreatorAssistant'

const mocks = vi.hoisted(() => ({ confirm: vi.fn(), resume: vi.fn(), session: vi.fn() }))
vi.mock('../src/api/assistant', () => ({
  streamAssistantAgentConfirm: mocks.confirm,
  streamAssistantAgentResume: mocks.resume,
  getAssistantAgentSession: mocks.session,
  streamAssistantAgent: vi.fn(),
  cancelAssistantAgent: vi.fn().mockResolvedValue({ ok: true }),
}))

const approval = { approval_id: 'approval-a', plan_digest: 'digest-a', tool: 'creator.apply', resumable: true }

async function setup() {
  localStorage.setItem('diceframe_creator_assistant_session_project-a', 'session-a')
  mocks.session.mockResolvedValue({ session_id: 'session-a', pending: { ...approval } })
  const scope = ref<string | undefined>('project-a')
  const assistant = useCreatorAssistant(scope)
  await nextTick()
  await Promise.resolve()
  return { scope, assistant }
}

describe('Creator approval actions', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    mocks.confirm.mockImplementation(async (_session, _id, _digest, _approved, handlers) => {
      handlers.onConfirmResult({ ok: true })
    })
    mocks.resume.mockImplementation(async (_session, _id, _digest, handlers) => {
      handlers.onConfirmResult({ ok: true })
    })
  })

  it('sends the explicit rejection rather than silently ignoring cancel', async () => {
    const { assistant } = await setup()
    await assistant.cancelApproval()
    expect(mocks.confirm).toHaveBeenCalledWith('session-a', 'approval-a', 'digest-a', false, expect.any(Object))
    expect(assistant.pendingApproval.value).toBeNull()
  })

  it('uses the resume endpoint without approving the plan again', async () => {
    const { assistant } = await setup()
    await assistant.resumeApproval()
    expect(mocks.resume).toHaveBeenCalledWith('session-a', 'approval-a', 'digest-a', expect.any(Object))
    expect(mocks.confirm).not.toHaveBeenCalled()
    expect(assistant.pendingApproval.value).toBeNull()
  })

  it.each([{ expired: true }, { recovery_required: true }, { resumable: false }])('does not resume an ineligible approval %j', async (flags) => {
    const { assistant } = await setup()
    assistant.pendingApproval.value = { ...approval, ...flags }
    await assistant.resumeApproval()
    expect(mocks.resume).not.toHaveBeenCalled()
  })

  it('ignores late approval output after switching project', async () => {
    let release!: () => void
    mocks.resume.mockImplementation(async (_session, _id, _digest, handlers) => {
      await new Promise<void>((resolve) => { release = resolve })
      handlers.onDelta('old project result')
      handlers.onAgentEvent('tool_result', { ok: true })
      handlers.onConfirmResult({ ok: true })
    })
    const { scope, assistant } = await setup()
    const pending = assistant.resumeApproval()
    scope.value = 'project-b'
    await nextTick()
    release()
    await pending
    expect(assistant.messages.value).toEqual([])
    expect(assistant.pendingApproval.value).toBeNull()
    expect(assistant.actionBusy.value).toBe(false)
  })
})
