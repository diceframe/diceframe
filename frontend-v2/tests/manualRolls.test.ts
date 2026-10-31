import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import ManualRollsPanel from '../src/features/play/manual-rolls/ManualRollsPanel.vue'
import { canRollRequest, createOperationId, isVisibleToActor } from '@/features/play/manual-rolls/useManualRolls'
import type { ManualRollRequest } from '@/features/play/manual-rolls/types'
import { api } from '../src/api/client'

vi.mock('../src/api/client', () => {
  class ApiError extends Error {
    constructor(message: string, public status: number, public code?: string, public retryAfter?: number) { super(message) }
  }
  return { api: vi.fn(), apiBlob: vi.fn(), ApiError }
})

const mockedApi = vi.mocked(api)

const request: ManualRollRequest = {
  id: 'request-1', operation_id: 'operation-1', run_id: 'run-1', round_number: 2,
  created_by: 'gm', created_at: '2026-09-12T00:00:00Z', label: 'Perception', formula: 'd20',
  visibility: 'private', target_uids: ['player-1'], target_names: { 'player-1': 'Ari' },
  status: 'pending', results: {},
}

describe('manual roll helpers', () => {
  it('only offers a pending request to its target or GM', () => {
    expect(canRollRequest(request, 'player-1', false)).toBe(true)
    expect(canRollRequest(request, 'player-2', false)).toBe(false)
    expect(canRollRequest(request, 'gm', true)).toBe(true)
    expect(canRollRequest({ ...request, status: 'resolved' }, 'player-1', false)).toBe(false)
  })

  it('keeps private requests scoped to their targets and GM', () => {
    expect(isVisibleToActor(request, 'player-1', false)).toBe(true)
    expect(isVisibleToActor(request, 'player-2', false)).toBe(false)
    expect(isVisibleToActor(request, 'gm', true)).toBe(true)
    expect(isVisibleToActor({ ...request, visibility: 'party' }, 'player-2', false)).toBe(true)
  })

  it('does not offer a target another roll while the request waits for other players', () => {
    const partlyResolved: ManualRollRequest = { ...request, target_uids: ['player-1', 'player-2'], results: {
      'player-1': { formula: 'd20', rolls: [12], modifier: 0, total: 12, natural: 12, rolled_by: 'player-1', rolled_at: '2026-10-16T00:00:00Z' },
    } }
    expect(canRollRequest(partlyResolved, 'player-1', false)).toBe(false)
    expect(canRollRequest(partlyResolved, 'player-2', false)).toBe(true)
    expect(canRollRequest(partlyResolved, 'gm', true)).toBe(true)
  })

  it('creates a non-empty operation id for an idempotent create retry', () => {
    expect(createOperationId()).toMatch(/\S/)
  })
})

describe('manual rolls composer include_in_ai_context', () => {
  const players = [{ user_id: 'p1', character_name: 'Alice' }]
  let wrapper: ReturnType<typeof mount> | undefined

  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    mockedApi.mockReset()
    mockedApi.mockResolvedValue({ ok: true, run_id: 'run-1', requests: [] })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    document.body.innerHTML = ''
  })

  async function mountPanel() {
    const mounted = mount(ManualRollsPanel, {
      attachTo: document.body,
      global: { plugins: [i18n], stubs: { Teleport: true } },
      props: { gameKey: 'web|room|bot', runId: 'run-1', actorId: 'gm', isGm: true, players },
    })
    await flushPromises()
    wrapper = mounted
    return mounted
  }

  function buttonByText(source: ReturnType<typeof mount>, text: string) {
    const button = source.findAll('button').find(candidate => candidate.text() === text)
    if (!button) throw new Error(`未找到按钮：${text}`)
    return button
  }

  function lastCreateBody() {
    const calls = mockedApi.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
    expect(calls.length).toBeGreaterThan(0)
    const init = calls[calls.length - 1]![1] as RequestInit
    return JSON.parse(String(init.body)) as Record<string, unknown>
  }

  async function openComposer(source: ReturnType<typeof mount>) {
    await buttonByText(source, '发起投掷').trigger('click')
    await flushPromises()
  }

  it('sends include_in_ai_context=false for free rolls by default', async () => {
    const panel = await mountPanel()
    await openComposer(panel)
    const toggle = panel.get('[data-testid="manual-roll-include-ai"]')
    expect((toggle.element as HTMLInputElement).checked).toBe(false)
    await buttonByText(panel, '发送请求').trigger('click')
    await flushPromises()
    const body = lastCreateBody()
    expect(body.purpose).toBe('free')
    expect(body.include_in_ai_context).toBe(false)
  })

  it('sends include_in_ai_context=true after toggling the checkbox', async () => {
    const panel = await mountPanel()
    await openComposer(panel)
    const toggle = panel.get('[data-testid="manual-roll-include-ai"]')
    await toggle.setValue(true)
    await buttonByText(panel, '发送请求').trigger('click')
    await flushPromises()
    const body = lastCreateBody()
    expect(body.purpose).toBe('free')
    expect(body.include_in_ai_context).toBe(true)
  })

  it('does not send the toggle for rule checks and contests', async () => {
    const panel = await mountPanel()
    for (const purpose of ['check', 'contest'] as const) {
      mockedApi.mockClear()
      await openComposer(panel)
      await panel.get('select').setValue(purpose)
      expect(panel.find('[data-testid="manual-roll-include-ai"]').exists()).toBe(false)
      if (purpose === 'check') await panel.get('input[type="number"]').setValue(15)
      await buttonByText(panel, '发送请求').trigger('click')
      await flushPromises()
      const body = lastCreateBody()
      expect(body.purpose).toBe(purpose)
      expect('include_in_ai_context' in body).toBe(false)
    }
  })

  it.each(['', '1.5'])('keeps an invalid check target "%s" editable and allows correction', async (invalidTarget) => {
    const panel = await mountPanel()
    await openComposer(panel)
    await panel.get('select').setValue('check')
    const targetInput = panel.get('input[type="number"]')
    await targetInput.setValue(invalidTarget)
    await buttonByText(panel, '发送请求').trigger('click')
    await flushPromises()

    expect(panel.text()).toContain('例如：15')
    expect(mockedApi.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    expect(buttonByText(panel, '稍后').attributes('disabled')).toBeUndefined()
    expect(buttonByText(panel, '发送请求').attributes('disabled')).toBeUndefined()

    await targetInput.setValue(15)
    await buttonByText(panel, '发送请求').trigger('click')
    await flushPromises()
    expect(lastCreateBody()).toMatchObject({ purpose: 'check', target: 15 })
    expect(panel.find('[role="dialog"]').exists()).toBe(false)
  })

  it('shows a pending entry after postponing and keeps history readable without a create action for players', async () => {
    mockedApi.mockResolvedValue({ ok: true, run_id: 'run-1', requests: [request] })
    wrapper = mount(ManualRollsPanel, {
      attachTo: document.body, global: { plugins: [i18n], stubs: { Teleport: true } },
      props: { gameKey: 'web|room|bot', runId: 'run-1', actorId: 'player-1', isGm: false, players },
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="manual-roll-create"]').exists()).toBe(false)
    await buttonByText(wrapper, '稍后').trigger('click')
    expect(wrapper.get('[data-testid="manual-roll-pending"]').text()).toBe('待投掷 · 1')
    await wrapper.get('[data-testid="manual-roll-history"]').trigger('click')
    expect(wrapper.get('[data-testid="manual-roll-history-list"]').text()).toContain('Perception')
    expect(wrapper.find('.manual-roll-actions').exists()).toBe(false)
    expect(wrapper.emitted('availability')?.at(-1)).toEqual([true])
  })
})
