import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('../src/composables/useLocale', () => ({
  useLocale: () => ({ locale: ref('zh-CN') }),
}))

import CombatLiveBar from '../src/components/play/CombatLiveBar.vue'

function gameplay() {
  return {
    state_schema_version: 1,
    state_version: 4,
    encounter_presets: [],
    combat: {
      status: 'active', round: 2, turn_index: 0,
      current_actor_id: 'player:ally', initiative: ['player:ally', 'enemy:goblin'],
      position_mode: 'theater', economy: {}, reactions: {}, pending_decisions: [],
      actors: [
        { actor_id: 'player:ally', kind: 'player', name: '阿刃', hp: 12, max_hp: 12, position: 0, armor_class: 16 },
        { actor_id: 'enemy:goblin', kind: 'enemy', name: '哥布林', hp: 10, max_hp: 10, position: 20, armor_class: 15 },
      ],
    },
    recent_combat_events: [{
      event_id: 'batch:0', batch_id: 'batch', intent_type: 'attack', state_version: 4,
      type: 'check.resolved', kind: 'attack', actor_id: 'enemy:goblin', actor_name: '哥布林',
      target_id: 'player:ally', target_name: '阿刃', natural: 15, modifier: 0, total: 15, target: 16, success: false,
      round: 2,
    }],
  } as any
}

function mountBar(props: { gameplay: ReturnType<typeof gameplay>; actorId: string; embedded?: boolean }) {
  return mount(CombatLiveBar, {
    props,
    global: { stubs: { Modal: { template: '<div><slot /></div>' } } },
  })
}

function buttonByText(wrapper: ReturnType<typeof mountBar>, text: string) {
  const button = wrapper.findAll('button').find(item => item.text().includes(text))
  expect(button, `应找到按钮: ${text}`).toBeTruthy()
  return button!
}

describe('combat live bar', () => {
  it('makes the current turn and latest shared result prominent', async () => {
    const wrapper = mountBar({ gameplay: gameplay(), actorId: 'ally' })

    expect(wrapper.text()).toContain('第 2 轮 · 轮到你行动')
    expect(wrapper.text()).toContain('哥布林攻击阿刃：d20 15 + 0 = 15 vs AC 16，未命中')
    await buttonByText(wrapper, '打开战斗工具').trigger('click')
    expect(wrapper.emitted('openCombat')).toHaveLength(1)
  })

  it('renders a legacy bless projection with the additive bonus exactly once', () => {
    const next = gameplay()
    next.recent_combat_events = [{
      ...next.recent_combat_events[0],
      event_id: 'legacy:0',
      natural: 15, modifier: 5, check_bonus: 4, total: 24, target: 16, success: true,
      check_bonus_rolls: [{ condition: 'blessed', die: 4, roll: 4 }],
    }]
    const wrapper = mountBar({ gameplay: next, actorId: 'ally' })

    expect(wrapper.text()).toContain('哥布林攻击阿刃：d20 15 + 9 = 24 vs AC 16，命中')
    expect(wrapper.text()).not.toContain('+ 13')
    expect(wrapper.text()).not.toContain('+ 9 + 4')
  })

  it('labels critical hits in the shared history text', async () => {
    const next = gameplay()
    next.recent_combat_events = [{
      ...next.recent_combat_events[0],
      event_id: 'critical:0',
      critical: true,
      success: true,
      natural: 20,
      total: 24,
      target: 14,
    }]
    const wrapper = mountBar({ gameplay: next, actorId: 'ally' })

    expect(wrapper.text()).toContain('暴击命中')
    await buttonByText(wrapper, '行动历史').trigger('click')
    expect(wrapper.text()).toContain('暴击命中')
  })

  it('opens a shared action history without opening the combat tool', async () => {
    const wrapper = mountBar({ gameplay: gameplay(), actorId: 'ally' })

    await buttonByText(wrapper, '行动历史').trigger('click')
    // 历史面板打开：能看到事件内容；且不触发打开战斗工具
    expect(wrapper.text()).toContain('第 2 轮')
    expect(wrapper.text()).toContain('哥布林')
    expect(wrapper.emitted('openCombat')).toBeUndefined()
  })

  it('stays self-contained when embedded at the top of the combat tool', () => {
    const wrapper = mountBar({ gameplay: gameplay(), actorId: 'ally', embedded: true })
    expect(wrapper.findAll('button').some(item => item.text().includes('打开战斗工具'))).toBe(false)
    expect(wrapper.text()).toContain('行动历史')
  })

  it('announces a newly received resolution after a refresh', async () => {
    const next = gameplay()
    next.recent_combat_events = [{
      ...next.recent_combat_events[0], event_id: 'batch:1', state_version: 5,
      total: 8, target: 15,
    }]
    const wrapper = mountBar({ gameplay: gameplay(), actorId: 'ally' })

    expect(wrapper.text()).not.toContain('刚刚更新')
    await wrapper.setProps({ gameplay: next })
    await nextTick()
    expect(wrapper.text()).toContain('刚刚更新')
  })

  it('keeps damage visible and shows every participant in shared history', async () => {
    const next = gameplay()
    next.recent_combat_events.push({
      event_id: 'batch:1', batch_id: 'batch', intent_type: 'attack', state_version: 5,
      type: 'resource.changed', resource: 'hp', actor_id: 'enemy:goblin', actor_name: '哥布林',
      target_id: 'player:ally', target_name: '阿刃', delta: -6, amount: 6, round: 2,
    } as any, {
      event_id: 'batch:2', batch_id: 'batch', intent_type: 'combat.message', state_version: 6,
      type: 'dnd2024.combat.message', actor_id: 'player:friend', actor_name: '调调', text: '我来掩护。', round: 2,
    } as any)
    const wrapper = mountBar({ gameplay: next, actorId: 'ally' })

    expect(wrapper.text()).toContain('哥布林 → 阿刃 · HP')
    expect(wrapper.text()).toContain('-6')
    await buttonByText(wrapper, '行动历史').trigger('click')
    const historyText = wrapper.text()
    expect(historyText).toContain('哥布林')
    expect(historyText).toContain('调调')
    expect(historyText).toContain('阿刃')
    expect(historyText).toContain('d20 15 + 0 = 15 vs AC 16')
  })

  it('never pairs an older damage event with a newer attack result', () => {
    const next = gameplay()
    next.recent_combat_events = [{
      event_id: 'old:0', batch_id: 'old', intent_type: 'attack', state_version: 3,
      type: 'resource.changed', resource: 'hp', actor_id: 'enemy:goblin', actor_name: '哥布林',
      target_id: 'player:ally', target_name: '阿刃', delta: -9, amount: 9, round: 1,
    } as any, {
      ...next.recent_combat_events[0], event_id: 'new:0', batch_id: 'new', state_version: 4,
    }]

    const wrapper = mountBar({ gameplay: next, actorId: 'ally' })

    expect(wrapper.text()).toContain('未命中')
    expect(wrapper.text()).not.toContain('最新生命结算')
    expect(wrapper.text()).not.toContain('-9')
  })

  it('shares the same combat event while personalizing whose turn it is', () => {
    const shared = gameplay()
    const activePlayer = mountBar({ gameplay: shared, actorId: 'ally' })
    const teammate = mountBar({ gameplay: shared, actorId: 'friend' })

    expect(activePlayer.text()).toContain('第 2 轮 · 轮到你行动')
    expect(teammate.text()).toContain('第 2 轮 · 阿刃正在行动')
    expect(activePlayer.text()).toContain('哥布林攻击阿刃')
    expect(teammate.text()).toContain('哥布林攻击阿刃')
  })

  it('shows death-save progress and the once-per-turn state', () => {
    const next = gameplay()
    next.combat.actors[0].hp = 0
    next.combat.actors[0].death_saves = { successes: 2, failures: 1 }
    next.combat.actors[0].conditions = { unconscious: {} }

    const wrapper = mountBar({ gameplay: next, actorId: 'ally' })

    expect(wrapper.text()).toContain('死亡豁免')
    expect(wrapper.text()).toContain('本轮等待投掷')
    expect(wrapper.findAll('.save-pips.success-pips i.filled')).toHaveLength(2)
    expect(wrapper.findAll('.save-pips.failure-pips i.filled')).toHaveLength(1)
  })

  it('replays newly received automatic batches in order', async () => {
    vi.useFakeTimers()
    try {
      const initial = gameplay()
      const next = gameplay()
      next.combat.round = 3
      next.recent_combat_events.push({
        ...next.recent_combat_events[0], event_id: 'enemy-hit:0', batch_id: 'enemy-hit',
        state_version: 5, success: true, total: 18,
      }, {
        event_id: 'enemy-hit:1', batch_id: 'enemy-hit', intent_type: 'attack', state_version: 5,
        type: 'resource.changed', resource: 'hp', actor_id: 'enemy:goblin', actor_name: '哥布林',
        target_id: 'player:ally', target_name: '阿刃', delta: -4, amount: 4, round: 2,
      } as any, {
        event_id: 'enemy-end:0', batch_id: 'enemy-end', intent_type: 'end_turn', state_version: 6,
        type: 'dnd2024.turn.advanced', actor_id: 'player:ally', actor_name: '阿刃',
        previous_actor_id: 'enemy:goblin', round: 3, turn_index: 0,
      } as any)
      const wrapper = mountBar({ gameplay: initial, actorId: 'ally' })

      await wrapper.setProps({ gameplay: next })
      await nextTick()
      expect(wrapper.text()).toContain('哥布林的行动')
      expect(wrapper.text()).toContain('命中')
      expect(wrapper.text()).toContain('-4')

      await vi.advanceTimersByTimeAsync(1100)
      expect(wrapper.text()).toContain('轮到阿刃行动')

      await vi.advanceTimersByTimeAsync(1100)
      expect(wrapper.text()).toContain('第 3 轮 · 轮到你行动')
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the authoritative event order and only reverses it for newest-first display', async () => {
    const next = gameplay()
    next.recent_combat_events = [
      { ...next.recent_combat_events[0], event_id: 'z-event', state_version: 9, actor_name: '最早事件' },
      { ...next.recent_combat_events[0], event_id: 'a-event', state_version: 2, actor_name: '最新事件' },
    ]
    const wrapper = mountBar({ gameplay: next, actorId: 'ally' })

    await buttonByText(wrapper, '行动历史').trigger('click')
    const rows = wrapper.findAll('li')
    expect(rows[0].text()).toContain('最新事件')
    expect(rows[1].text()).toContain('最早事件')
    expect(rows[0].find('.combat-history-new').text()).toBe('新')
    expect(rows[1].find('.combat-history-new').exists()).toBe(false)
  })
})
