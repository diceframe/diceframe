import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RulesetPanels from '@/features/play/RulesetPanels.vue'

const { submitRulesetIntent } = vi.hoisted(() => ({ submitRulesetIntent: vi.fn() }))
vi.mock('@/api/rulesets', () => ({ submitRulesetIntent }))

afterEach(() => { vi.clearAllMocks() })

const panels = [
  {
    component: 'group',
    label: 'Clocks',
    children: [
      { component: 'text', label: 'Doom', value: '3 / 6' },
      { component: 'progress', label: 'Doom clock', value: 3, min: 0, max: 6 },
    ],
  },
  {
    component: 'list',
    label: 'Opportunities',
    value: ['A merchant offers a map', 'A rival is watching'],
  },
  {
    component: 'button',
    label: 'Reroll',
    action: 'fu.reroll',
    arguments: { actor_id: 'alice', check_id: 'check-1' },
  },
] as const

describe('ruleset declarative panels', () => {
  it('renders runtime-provided nodes without knowing their meaning', () => {
    const wrapper = mount(RulesetPanels, {
      props: { gameKey: 'room-1', panels: panels as never },
    })
    expect(wrapper.find('[data-testid="ruleset-panels"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Clocks')
    expect(wrapper.text()).toContain('Doom')
    expect(wrapper.find('progress').attributes('max')).toBe('6')
    expect(wrapper.text()).toContain('A merchant offers a map')
    expect(wrapper.text()).toContain('Reroll')
    wrapper.unmount()
  })

  it('submits a panel button as an ordinary intent', async () => {
    submitRulesetIntent.mockResolvedValue({ ok: true })
    const wrapper = mount(RulesetPanels, {
      props: { gameKey: 'room-1', panels: panels as never },
    })
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(submitRulesetIntent).toHaveBeenCalledWith('room-1', {
      actor_id: 'alice',
      check_id: 'check-1',
      type: 'fu.reroll',
    })
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    wrapper.unmount()
  })

  it('surfaces a rejected action instead of silently dropping it', async () => {
    submitRulesetIntent.mockResolvedValue({ ok: false, error: 'not your turn' })
    const wrapper = mount(RulesetPanels, {
      props: { gameKey: 'room-1', panels: panels as never },
    })
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('not your turn')
    expect(wrapper.emitted('refresh')).toBeUndefined()
    wrapper.unmount()
  })

  it('drops malformed nodes and stays hidden when nothing is usable', () => {
    const wrapper = mount(RulesetPanels, {
      props: { gameKey: 'room-1', panels: [null, {}, { label: 'no component' }] as never },
    })
    expect(wrapper.find('[data-testid="ruleset-panels"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
