import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import DiceTray from '../src/features/play/dice/DiceTray.vue'

describe('authoritative dice presentation', () => {
  const check = { dice: 'd20', rolls: [4, 19], roll: 19, total: 24, advantage_mode: 'advantage' }
  it('does not expose hidden faces, adopted values or outcome through text and attributes', () => {
    const wrapper = mount(DiceTray, { props: { check, revealed: false }, global: { plugins: [i18n] } })
    expect(wrapper.findAll('[data-die-value]')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('19')
    expect(wrapper.html()).not.toContain('24')
    expect(wrapper.html()).not.toContain('taken')
  })
  it('presents every backend d20 face and its adopted roll without doing a new roll', () => {
    const wrapper = mount(DiceTray, { props: { check, revealed: true }, global: { plugins: [i18n] } })
    expect(wrapper.findAll('[data-die-value]').map(die => die.attributes('data-die-value'))).toEqual(['4', '19'])
    expect(wrapper.get('[data-taken="true"]').attributes('data-die-value')).toBe('19')
    expect(wrapper.emitted()).toEqual({})
  })
  it('uses the server percentile faces and selected tens, including 00 + 0 = 100', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(DiceTray, {
      props: { revealed: true, check: { dice: 'd100', roll: 100, percentile_dice: { units: 0, tens: [0, 50], candidates: [100, 50], taken_index: 0, bonus_dice: 0, penalty_dice: 1 } } },
      global: { plugins: [i18n] },
    })
    expect(wrapper.findAll('[data-die-value]').map(die => die.attributes('data-die-value'))).toEqual(['0', '0', '50'])
    expect(wrapper.findAll('[data-taken="true"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('00')
    expect(wrapper.get('[data-die-value="0"]').text()).toContain('共享个位')
    expect(wrapper.findAll('[data-die-value]')[1]!.text()).toContain('十位骰')
    expect(wrapper.findAll('.tray-die-caption').filter(caption => caption.text() === '采用')).toHaveLength(1)
  })
  it('falls back to the supplied expression and faces for an unknown mechanic', () => {
    const wrapper = mount(DiceTray, { props: { revealed: true, check: { dice: 'pool:custom', rolls: [7, 2], roll: 7 } }, global: { plugins: [i18n] } })
    expect(wrapper.find('[data-die-value]').exists()).toBe(false)
    expect(wrapper.text()).toContain('pool:custom')
    expect(wrapper.text()).toContain('7, 2')
  })

  it('marks a newly revealed tray for one finite visual settle', () => {
    const wrapper = mount(DiceTray, {
      props: { revealed: true, animateReveal: true, check: { dice: 'd20', rolls: [7], roll: 7 } },
      global: { plugins: [i18n] },
    })
    expect(wrapper.get('[data-testid="dice-tray"]').classes()).toContain('dice-tray-reveal')
  })

  it('highlights natural 20 as critical and natural 1 as fumble, and toggles sound mute', async () => {
    const crit = mount(DiceTray, {
      props: { revealed: true, check: { dice: 'd20', rolls: [20], roll: 20 } },
      global: { plugins: [i18n] },
    })
    expect(crit.get('[data-testid="dice-tray"]').classes()).toContain('dice-tray-critical')
    expect(crit.get('[data-die-value="20"]').attributes('data-tone')).toBe('critical')

    const fumble = mount(DiceTray, {
      props: { revealed: true, check: { dice: 'd20', rolls: [1], roll: 1 } },
      global: { plugins: [i18n] },
    })
    expect(fumble.get('[data-testid="dice-tray"]').classes()).toContain('dice-tray-fumble')
    expect(fumble.get('[data-die-value="1"]').attributes('data-tone')).toBe('fumble')

    const toggle = crit.get('[data-testid="dice-sound-toggle"]')
    const before = toggle.attributes('aria-pressed')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-pressed')).not.toBe(before)
  })
})
