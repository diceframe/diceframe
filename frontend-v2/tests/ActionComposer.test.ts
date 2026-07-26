import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import ActionComposer from '../src/components/ActionComposer.vue'
import { api } from '../src/api/client'
import type { GameDetail } from '../src/api/types'

vi.mock('../src/api/client', () => ({
  api: vi.fn(),
}))

const mockedApi = vi.mocked(api)

function detail(submitted = true, roundNumber = 3): GameDetail {
  return {
    game_key: 'web|room|bot',
    round_number: roundNumber,
    solo_mode: false,
    multiplayer: {
      submitted_actions: submitted
        ? [{ user_id: 'player-1', text: '检查门锁', revision_count: 1, dice_pending: true }]
        : [],
    },
  }
}

describe('ActionComposer rollback refresh', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    mockedApi.mockReset()
  })

  it('returns a player from a pending roll to the action input after same-round rollback', async () => {
    mockedApi.mockResolvedValue({ phase: 'dice', message: '需要掷骰' })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: {
        gameKey: 'web|room|bot',
        userId: 'player-1',
        detail: detail(false),
      },
    })

    await wrapper.get('textarea').setValue('检查门锁')
    await wrapper.get('.composer-row button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.dice-prompt').exists()).toBe(true)

    await wrapper.setProps({ detail: detail(true) })
    await wrapper.setProps({ detail: detail(false) })

    expect(wrapper.find('.dice-prompt').exists()).toBe(false)
    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(wrapper.find('.notice').exists()).toBe(false)
  })

  it('clears stale submission feedback when the round moves backward', async () => {
    mockedApi.mockResolvedValue({})
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: {
        gameKey: 'web|room|bot',
        userId: 'player-1',
        detail: detail(false, 4),
      },
    })

    await wrapper.get('textarea').setValue('观察走廊')
    await wrapper.get('.composer-row button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.notice').exists()).toBe(true)

    await wrapper.setProps({ detail: detail(false, 3) })

    expect(wrapper.find('.notice').exists()).toBe(false)
    expect(wrapper.find('textarea').exists()).toBe(true)
  })

  it('shows the structured rule check and keeps the d100 dice type', async () => {
    mockedApi
      .mockResolvedValueOnce({
        phase: 'dice',
        message: '需要潜行检定',
        check_request: { dice_system: 'd100', label: '潜行检定', skill: '潜行' },
      })
      .mockResolvedValueOnce({
        phase: 'done',
        roll: { ok: true, dice_system: 'd100', value: 54, critical: false, fumble: false },
      })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: {
        gameKey: 'web|room|bot',
        userId: 'player-1',
        detail: detail(false),
      },
    })

    await wrapper.get('textarea').setValue('悄悄上楼')
    await wrapper.get('.composer-row button').trigger('click')
    await flushPromises()
    expect(wrapper.get('.dice-prompt').text()).toContain('潜行检定 · d100')

    await wrapper.get('.dice-prompt .primary').trigger('click')
    await flushPromises()
    expect(wrapper.get('.dice-result').text()).toContain('d100 = 54')
  })
})
