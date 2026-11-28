import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import FreeRollModal from '../src/features/play/manual-rolls/FreeRollModal.vue'
import { api } from '../src/api/client'

vi.mock('../src/api/client', () => {
  class ApiError extends Error {
    constructor(message: string, public status: number, public code?: string, public retryAfter?: number) { super(message) }
  }
  return { api: vi.fn(), apiBlob: vi.fn(), ApiError }
})

const mockedApi = vi.mocked(api)

describe('FreeRollModal', () => {
  let wrapper: ReturnType<typeof mount> | undefined

  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    mockedApi.mockReset()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    document.body.innerHTML = ''
  })

  function mountModal(extraProps: Record<string, unknown> = {}) {
    wrapper = mount(FreeRollModal, {
      attachTo: document.body,
      global: { plugins: [i18n], stubs: { Teleport: true } },
      props: { gameKey: 'web|room|bot', runId: 'run-1', ...extraProps },
    })
    return wrapper
  }

  it('stacks quick dice clicks and submits a one-step quick roll', async () => {
    mockedApi.mockResolvedValueOnce({
      ok: true,
      request: {
        id: 'mr-1', operation_id: 'op-1', run_id: 'run-1', round_number: 1,
        created_by: 'p1', created_at: '2026-11-28T00:00:00Z', label: '守夜',
        formula: '2d6+2', purpose: 'free', source: 'quick', visibility: 'private',
        target_uids: ['p1'], target_names: { p1: 'Alice' }, status: 'resolved', results: {},
      },
      result: {
        formula: '2d6+2', rolls: [3, 5], modifier: 2, total: 10, natural: 8,
        faces: [
          { sides: 6, value: 3, taken: true, sign: 1 },
          { sides: 6, value: 5, taken: true, sign: 1 },
        ],
        rolled_by: 'p1', rolled_at: '2026-11-28T00:00:00Z',
      },
    })

    const modal = mountModal()
    await modal.get('[data-testid="free-roll-d6"]').trigger('click')
    await modal.get('[data-testid="free-roll-d6"]').trigger('click')
    expect((modal.get('[data-testid="free-roll-formula"]').element as HTMLInputElement).value).toBe('2d6')

    const plusTwo = modal.findAll('button').find(btn => btn.text() === '+2')
    await plusTwo!.trigger('click')
    expect((modal.get('[data-testid="free-roll-formula"]').element as HTMLInputElement).value).toBe('2d6+2')

    await modal.get('[data-testid="free-roll-label"]').setValue('守夜')
    await modal.get('[data-testid="free-roll-visibility"]').setValue('private')
    await modal.get('[data-testid="free-roll-include-ai"]').setValue(true)
    await modal.get('form').trigger('submit')
    await flushPromises()

    expect(mockedApi).toHaveBeenCalledTimes(1)
    const [url, init] = mockedApi.mock.calls[0]!
    expect(url).toBe('/games/web%7Croom%7Cbot/roll-requests/quick')
    const payload = JSON.parse(String((init as RequestInit).body)) as Record<string, unknown>
    expect(payload.formula).toBe('2d6+2')
    expect(payload.label).toBe('守夜')
    expect(payload.visibility).toBe('private')
    expect(payload.include_in_ai_context).toBe(true)
    expect(payload.run_id).toBe('run-1')

    expect(modal.get('[data-testid="free-roll-total"]').text()).toBe('10')
    expect(modal.findAll('[data-die-value]').map(node => node.attributes('data-die-value'))).toEqual(['3', '5'])
    expect(modal.emitted('rolled')?.length).toBe(1)
  })

  it('disables rolling in preview mode without delegation', () => {
    const modal = mountModal({ preview: true, delegate: false })
    expect((modal.get('[data-testid="free-roll-submit"]').element as HTMLButtonElement).disabled).toBe(true)
  })
})
