import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConditionAdjudicator from '../src/features/rulesets/dnd2024/combat/hud/ConditionAdjudicator.vue'

const labels = {
  title: 'Adjudicate a condition', target: 'Target', pick: 'Condition',
  apply: 'Apply', remove: 'Remove', hint: 'Read the definition first.',
}
const conditions = [
  { id: 'poisoned', name: 'Poisoned', summary: 'Disadvantage on attacks and checks.' },
  { id: 'prone', name: 'Prone', summary: 'Your attacks have disadvantage.' },
]
const targets = [
  { id: 'player:a', name: 'Alice' },
  { id: 'enemy:goblin-1', name: 'Goblin' },
]

function mountPanel(overrides: Record<string, unknown> = {}) {
  return mount(ConditionAdjudicator, {
    props: { conditions, targets, busy: false, labels, ...overrides },
  })
}

describe('GM condition adjudicator', () => {
  it('shows the rules summary of the selected state before anything is applied', async () => {
    const wrapper = mountPanel()

    expect(wrapper.get('[data-testid="gm-condition-summary"]').text())
      .toBe('Disadvantage on attacks and checks.')

    await wrapper.findAll('select')[1].setValue('prone')
    expect(wrapper.get('[data-testid="gm-condition-summary"]').text())
      .toBe('Your attacks have disadvantage.')
  })

  it('sends the chosen target, condition and action', async () => {
    const wrapper = mountPanel()
    await wrapper.findAll('select')[0].setValue('enemy:goblin-1')

    const [apply, remove] = wrapper.findAll('button')
    await apply.trigger('click')
    await remove.trigger('click')

    expect(wrapper.emitted('submit')).toEqual([
      [{ target_id: 'enemy:goblin-1', condition: 'poisoned', action: 'apply' }],
      [{ target_id: 'enemy:goblin-1', condition: 'poisoned', action: 'remove' }],
    ])
  })

  it('blocks the buttons while the server is busy', () => {
    const wrapper = mountPanel({ busy: true })

    for (const button of wrapper.findAll('button')) {
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
    }
  })

  it('renders nothing when the server sent no catalog (player view)', () => {
    const wrapper = mountPanel({ conditions: [] })
    expect(wrapper.find('[data-testid="gm-condition-adjudicator"]').exists()).toBe(false)
  })
})
