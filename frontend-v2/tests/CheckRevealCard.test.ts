import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CheckRevealCard from '../src/components/play/CheckRevealCard.vue'
import { i18n } from '../src/i18n'

describe('CheckRevealCard', () => {
  beforeEach(() => { i18n.global.locale.value = 'zh-CN' })

  it('renders a rule-aware d20 result and details', () => {
    const wrapper = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: {
        check: {
          check_id: 'c1', actor_name: '阿岚', label: '力量检定', dice: 'd20',
          roll: 14, rolls: [14], modifier: 3, total: 17, dc: 15,
          verdict: '成功', is_critical: false, is_fumble: false,
        },
      },
    })
    expect(wrapper.text()).toContain('d20=14 + 3 = 17 / DC 15')
    expect(wrapper.text()).toContain('成功')
    expect(wrapper.get('article').attributes('aria-label')).toContain('阿岚')
    expect(wrapper.find('details').text()).toContain('14')
    expect(wrapper.find('details').text()).toContain('d20=14 + 3 = 17 / DC 15')
  })

  it('explains which channel each situational adjustment came from', () => {
    const wrapper = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: {
        check: {
          check_id: 'c3', actor_name: '阿岚', label: '开锁检定', dice: 'd20',
          roll: 14, rolls: [14, 6], modifier: 3, total: 17, dc: 15,
          advantage_mode: 'disadvantage', verdict: '成功',
          dc_reason: '锁本身很复杂', advantage_reason: '光线昏暗',
          modifier_reason: '地面湿滑',
          planner_notes: ['same_fact_as_advantage'],
          modifier_breakdown: '同一情境因素被重复计入，已按单一渠道结算。',
        },
      },
    })
    const details = wrapper.find('details').text()
    expect(details).toContain('难度依据：锁本身很复杂')
    expect(details).toContain('掷骰方式依据：光线昏暗')
    expect(details).toContain('环境修正依据：地面湿滑')
    expect(details).toContain('判定来源：难度依据：锁本身很复杂')
    // 缺失的依据不会凭空渲染。
    expect(details).not.toContain('undefined')
  })

  it('reveals the server result after the roll animation', async () => {
    vi.useFakeTimers()
    const wrapper = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: {
        animate: true,
        check: {
          check_id: 'c2', actor_name: '白露', label: '潜行检定', dice: 'd100',
          roll: 1, threshold: 65, verdict: '大成功', is_critical: true,
        },
      },
    })
    expect(wrapper.text()).toContain('掷骰中')
    await vi.advanceTimersByTimeAsync(720)
    expect(wrapper.text()).toContain('d100=1 / 65%')
    expect(wrapper.text()).toContain('大成功')
    vi.useRealTimers()
  })
})
