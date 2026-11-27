import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CheckRevealCard from '../src/components/play/CheckRevealCard.vue'
import { i18n } from '../src/i18n'

describe('CheckRevealCard', () => {
  beforeEach(() => { i18n.global.locale.value = 'zh-CN' })
  afterEach(() => { vi.useRealTimers() })

  const clickCheck = {
    check_id: 'shared', actor_uid: 'p1', actor_name: '阿岚', label: '力量检定',
    dice: 'd20', roll: 14, rolls: [14], modifier: 3, total: 17, verdict: '成功',
  }

  it('keeps unclicked results masked indefinitely and across remounts without a countdown', async () => {
    vi.useFakeTimers()
    const options = { global: { plugins: [i18n] }, props: { check: clickCheck, clickMode: true, canReveal: true } }
    const first = mount(CheckRevealCard, options)
    expect(first.find('.check-reveal-countdown').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(first.find('.check-math').exists()).toBe(false)
    expect(first.emitted('reveal')).toBeUndefined()
    first.unmount()
    const refreshed = mount(CheckRevealCard, options)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(refreshed.find('.check-math').exists()).toBe(false)
    refreshed.unmount()
  })

  it('reveals all clients only after the server shared marker arrives', async () => {
    vi.useFakeTimers()
    const actor = mount(CheckRevealCard, { global: { plugins: [i18n] }, props: { check: clickCheck, clickMode: true, canReveal: true } })
    const spectator = mount(CheckRevealCard, { global: { plugins: [i18n] }, props: { check: clickCheck, clickMode: true, canReveal: false } })
    await actor.get('.check-reveal-actions button').trigger('click')
    expect(actor.emitted('reveal')?.[0]).toEqual([clickCheck])
    expect(actor.find('.check-math').exists()).toBe(false)
    expect(spectator.find('.check-reveal-actions button').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(spectator.find('.check-math').exists()).toBe(false)
    const reveal = { by: 'p1', at: '2026-10-07T00:00:00Z' }
    await actor.setProps({ reveal })
    await spectator.setProps({ reveal })
    expect(actor.get('.check-math').text()).toBe(spectator.get('.check-math').text())
    const refreshed = mount(CheckRevealCard, { global: { plugins: [i18n] }, props: { check: clickCheck, clickMode: true, reveal } })
    expect(refreshed.get('.check-math').text()).toBe(actor.get('.check-math').text())
    actor.unmount(); spectator.unmount(); refreshed.unmount()
  })

  it('keeps the automatic presentation mode available independently', () => {
    const wrapper = mount(CheckRevealCard, { global: { plugins: [i18n] }, props: { check: clickCheck, clickMode: false } })
    expect(wrapper.get('.check-math').text()).toContain('d20=14 + 3 = 17')
    expect(wrapper.find('.check-reveal-actions').exists()).toBe(false)
    wrapper.unmount()
  })

  it('cancels an automatic animation when the room switches to click mode', async () => {
    vi.useFakeTimers()
    const wrapper = mount(CheckRevealCard, { global: { plugins: [i18n] }, props: { check: clickCheck, animate: true, clickMode: false } })
    await wrapper.setProps({ clickMode: true })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(wrapper.find('.check-math').exists()).toBe(false)
    expect(wrapper.get('article').classes()).not.toContain('rolling')
    wrapper.unmount()
  })

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

  it('shows the frozen server label when a check carries no verdict code', () => {
    // 自定义规则 runtime / 旧存档：没有 code 时不猜成败，原样显示后端文本。
    const legacy = {
      check_id: 'legacy', actor_uid: 'p1', actor_name: '阿岚', label: 'Barely',
      dice: 'runtime', roll: 7, verdict: 'Barely',
    }
    const wrapper = mount(CheckRevealCard, { global: { plugins: [i18n] }, props: { check: legacy } })
    expect(wrapper.get('.check-verdict').text()).toBe('Barely')
    expect(wrapper.classes()).toContain('unknown')
    expect(wrapper.classes()).not.toContain('success')
    expect(wrapper.classes()).not.toContain('failure')
  })

  it('renders the verdict from its code in the interface language', async () => {
    const coded = {
      check_id: 'coded', actor_uid: 'p1', actor_name: 'Aster', label: 'Stealth',
      dice: 'd20', roll: 3, total: 3, dc: 15, verdict: '失败', verdict_code: 'critical_failure',
    }
    const wrapper = mount(CheckRevealCard, { global: { plugins: [i18n] }, props: { check: coded } })
    expect(wrapper.classes()).toContain('fumble')
    expect(wrapper.get('.check-verdict').text()).toBe(i18n.global.t('criticalFailure'))
    i18n.global.locale.value = 'en'
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.check-verdict').text()).toBe(i18n.global.t('criticalFailure'))
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

  it('badges the dice mode and marks which die the server took', () => {
    const wrapper = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: {
        check: {
          check_id: 'c4', actor_name: '米拉', label: '攻击检定', dice: 'd20',
          roll: 6, rolls: [17, 6], modifier: 3, total: 9, dc: 14,
          advantage_mode: 'disadvantage', advantage_note: '长距离射击：劣势',
          verdict: '失败',
        },
      },
    })
    expect(wrapper.get('.check-mode-badge').text()).toBe('劣势')
    expect(wrapper.get('.check-mode-badge').classes()).toContain('disadvantage')
    const chips = wrapper.findAll('.check-die-chip')
    expect(chips).toHaveLength(2)
    expect(chips[0].text()).toContain('17')
    expect(chips[0].classes()).not.toContain('taken')
    expect(chips[1].text()).toContain('6')
    expect(chips[1].classes()).toContain('taken')
    expect(chips[1].text()).toContain('采用')
  })

  it('marks no die as taken when the server roll matches none of the faces', () => {
    const wrapper = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: {
        check: {
          check_id: 'c6', actor_name: '米拉', label: '攻击检定', dice: 'd20',
          roll: 12, rolls: [17, 6], modifier: 3, total: 15, dc: 14,
          advantage_mode: 'advantage', verdict: '成功',
        },
      },
    })
    const chips = wrapper.findAll('.check-die-chip')
    expect(chips).toHaveLength(2)
    expect(chips.some((chip) => chip.classes().includes('taken'))).toBe(false)
    expect(wrapper.find('.check-dice-chips').text()).not.toContain('采用')
  })

  it('keeps the plain faces line for single-die results', () => {
    const wrapper = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: {
        check: {
          check_id: 'c5', actor_name: '阿岚', label: '感知检定', dice: 'd20',
          roll: 11, rolls: [11], modifier: 0, total: 11, dc: 12,
          verdict: '失败',
        },
      },
    })
    expect(wrapper.find('.check-mode-badge').exists()).toBe(false)
    expect(wrapper.find('.check-dice-chips').exists()).toBe(false)
    expect(wrapper.find('details').text()).toContain('骰面：11')
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
    expect(wrapper.text()).toContain('d100=1 / 65')
    expect(wrapper.text()).toContain('大成功')
    vi.useRealTimers()
  })

  it('animates the revealed dice once, but does not replay historical results', async () => {
    vi.useFakeTimers()
    const active = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: { animate: true, check: { ...clickCheck, check_id: 'new-roll' } },
    })
    expect(active.find('.dice-tray-reveal').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(720)
    expect(active.find('.dice-tray-reveal').exists()).toBe(true)
    await vi.advanceTimersByTimeAsync(5000)
    expect(active.find('.dice-tray-reveal').exists()).toBe(false)
    active.unmount()

    const historical = mount(CheckRevealCard, {
      global: { plugins: [i18n] },
      props: { check: { ...clickCheck, check_id: 'old-roll' } },
    })
    expect(historical.find('.dice-tray-reveal').exists()).toBe(false)
    historical.unmount()
    vi.useRealTimers()
  })
})
