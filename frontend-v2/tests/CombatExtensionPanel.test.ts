import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  api: mocks.api,
}))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: mocks.success, error: mocks.error }),
}))

import CombatExtensionPanel from '../src/components/play/CombatExtensionPanel.vue'

function detail(overrides: Record<string, unknown> = {}) {
  return {
    game_key: 'web|combat|bot',
    combat_extension: {
      scheduler: {
        kind: 'threshold',
        ready: ['player:ally'],
        gauges: { 'player:ally': 80, 'npc:boss': 100 },
      },
      entities: ['player:ally', 'npc:boss'],
      entity_names: { 'player:ally': '阿刃', 'npc:boss': '黑骑士' },
      pools: {
        'player:ally': {
          hp: { current: 12, maximum: 12 },
        },
      },
      actions: [{
        id: 'ability:qi_palm',
        kind: 'ability',
        name: '内力掌',
        costs: [{ resource: 'qi', amount: { op: 'constant', value: 8 } }],
        consume_item: { item: '回春丹', qty: 1 },
      }],
      ...overrides,
    },
  }
}

describe('CombatExtensionPanel', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    mocks.api.mockReset()
    mocks.success.mockReset()
    mocks.error.mockReset()
  })

  it('shows ATB state and lets the GM advance the scheduler', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (String(path).endsWith('/combat/scheduler/advance')) {
        return { ok: true, ready: ['player:ally'], gauges: { 'player:ally': 100, 'npc:boss': 80 } }
      }
      throw new Error(`unexpected api call: ${path}`)
    })
    const wrapper = mount(CombatExtensionPanel, {
      global: { plugins: [i18n] },
      props: { detail: detail({ actions: [] }), gameKey: 'web|combat|bot', selfUid: 'ally', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('战斗扩展')
    expect(wrapper.text()).toContain('调度器')
    expect(wrapper.text()).toContain('阿刃')
    expect(wrapper.text()).toContain('黑骑士')
    expect(wrapper.text()).toContain('行动条')
    expect(wrapper.text()).toContain('80')

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(mocks.api).toHaveBeenCalledOnce()
    expect(String(mocks.api.mock.calls[0][0])).toContain('/combat/scheduler/advance')
    expect(wrapper.emitted('changed')).toHaveLength(1)
    wrapper.unmount()
  })

  it('uses the selected GM actor and shows cost metadata on actions', async () => {
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path).endsWith('/combat/action')) {
        return { ok: true }
      }
      throw new Error(`unexpected api call: ${path} ${init?.method || 'GET'}`)
    })
    const wrapper = mount(CombatExtensionPanel, {
      global: { plugins: [i18n] },
      props: { detail: detail({ scheduler: null, pools: {} }), gameKey: 'web|combat|bot', selfUid: 'ally', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('内力掌')
    expect(wrapper.text()).toContain('消耗')
    expect(wrapper.text()).toContain('回春丹 ×1')

    const selects = wrapper.findAll('select')
    await selects[0].setValue('npc:boss')
    await selects[1].setValue('self')
    await wrapper.get('button').trigger('click')
    await flushPromises()

    const actionCall = mocks.api.mock.calls.find(call => String(call[0]).endsWith('/combat/action'))
    expect(actionCall).toBeTruthy()
    const body = JSON.parse(String((actionCall?.[1] as RequestInit | undefined)?.body || '{}'))
    expect(body).toMatchObject({
      actor_id: 'npc:boss',
      action_id: 'ability:qi_palm',
    })
    wrapper.unmount()
  })

  it('requires an explicit target instead of silently targeting the actor', async () => {
    const wrapper = mount(CombatExtensionPanel, {
      global: { plugins: [i18n] },
      props: { detail: detail({ scheduler: null }), gameKey: 'web|combat|bot', selfUid: 'ally', isGm: false },
    })

    await wrapper.get('button').trigger('click')

    expect(mocks.api).not.toHaveBeenCalled()
    expect(mocks.error).toHaveBeenCalledWith('请选择目标')
    wrapper.unmount()
  })

  it('uses ready participants for non-gauge schedulers', async () => {
    const wrapper = mount(CombatExtensionPanel, {
      global: { plugins: [i18n] },
      props: {
        detail: detail({
          scheduler: {
            kind: 'round_robin',
            ready: ['player:ally'],
            gauges: {},
            participants: ['player:ally', 'npc:boss'],
          },
        }),
        gameKey: 'web|combat|bot',
        selfUid: 'ally',
        isGm: true,
      },
    })

    expect(wrapper.text()).toContain('阿刃')
    expect(wrapper.text()).toContain('黑骑士')
    expect(wrapper.findAll('.combat-ext-scheduler-row')).toHaveLength(2)
    wrapper.unmount()
  })
})
