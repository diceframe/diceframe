import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(), submit: vi.fn(), decide: vi.fn(), plan: vi.fn(),
  locale: 'en',
}))

vi.mock('../src/features/rulesets/dnd2024/api', () => ({
  fetchRulesetAvailableActions: mocks.fetch,
  submitRulesetIntent: mocks.submit,
  resolveRulesetDecision: mocks.decide,
  planRulesetTemporaryEncounter: mocks.plan,
}))
// 面板自带的 copy 字典按 locale 取中/英；A1 新增的摘要与编辑弹窗文案走 i18n，
// 所以 mock 必须同时提供 t，否则新版摘要渲染会直接抛错。
vi.mock('../src/composables/useLocale', () => ({
  useLocale: () => ({
    locale: ref(mocks.locale),
    t: (key: string) => i18n.global.t(key as never),
  }),
}))

import Dnd2024CombatPanel from '../src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue'

function response(status: 'none' | 'active' = 'none') {
  const active = status === 'active'
  return {
    ok: true,
    game_key: 'web|combat|bot',
    rule_id: 'dnd2024_srd',
    ruleset_runtime: {
      id: 'core:dnd2024', version: 1, requested_minimum_version: 1,
      capabilities: {
        experience_profile: 'dnd2024', character_builder: 'professional',
        authoritative_intents: true, deterministic_combat: true,
        versioned_state: true, session_zero: false, tutorial_coach: false,
      },
    },
    gameplay: {
      state_schema_version: 1,
      state_version: active ? 1 : 0,
      encounter_presets: [{
        id: 'first_skirmish', name: 'First Skirmish', description: 'Standard encounter',
        difficulty: 'standard', enemies: [{ id: 'goblin-1', hp: 7 }],
      }],
      encounter_access: {
        mode: 'sandbox', status: active ? 'active' : 'pending',
        can_start: !active, unprepared: false,
        encounter_preset_id: '', encounter_instance_id: '', origin_step_id: '',
        catalog: 'bundle',
      },
      combat: {
        status, round: active ? 1 : 0, turn_index: 0,
        current_actor_id: active ? 'player:gm' : '',
        initiative: active ? ['player:gm', 'enemy:goblin-1'] : [],
        position_mode: 'theater',
        economy: active ? { action: 1, bonus_action: 1, movement: 30, reaction: 1 } : {},
        reactions: {}, pending_decisions: [],
        mode: active ? 'sandbox' : '',
        adventure_binding: null,
        actors: active ? [
          { actor_id: 'player:gm', kind: 'player', name: 'Guardian', hp: 12, max_hp: 12, position: 0, armor_class: 16, conditions: {} },
          { actor_id: 'enemy:goblin-1', kind: 'enemy', name: 'Goblin', hp: 7, max_hp: 7, position: 5, armor_class: 12, conditions: {} },
        ] : [],
      },
    },
    available_actions: active ? [{
      type: 'attack', label: 'Attack', actor_id: 'player:gm', expected_version: 1,
      weapons: [{ id: 'greatsword', name: 'Greatsword', weapon_ref: 'item:greatsword', damage: '2d6', damage_type: 'slashing' }],
      targets: [{ actor_id: 'enemy:goblin-1', kind: 'enemy', name: 'Goblin', hp: 7, max_hp: 7, position: 5 }],
    }, {
      type: 'move', label: 'Move', actor_id: 'player:gm', expected_version: 1,
      movement_remaining: 30,
    }, {
      type: 'end_turn', label: 'End Turn', actor_id: 'player:gm', expected_version: 1,
    }] : [{
      type: 'combat.start', label: 'Start Combat', expected_version: 0, requires: ['enemies'],
    }],
  }
}

function explorationResponse(availableSlotLevels: number[] = [1, 2]) {
  const result = response('active') as any
  result.available_actions = [{
    type: 'exploration.cast_spell', label: 'Cast a spell', actor_id: 'player:gm',
    expected_version: 0,
    spells: [{
      spell_ref: 'spell:cure_wounds', name: 'Cure Wounds', level: 1,
      casting_time: 'action', available_slot_levels: availableSlotLevels,
    }],
    targets: [{
      actor_id: 'player:gm', kind: 'player', name: 'Guardian',
      hp: 8, max_hp: 12, position: 0,
    }],
  }]
  return result
}

type Panel = ReturnType<typeof mount>

// The dock works in two steps: pick an action tile, then confirm it.
async function openTile(wrapper: Panel, tile: string) {
  await wrapper.get(`[data-tile="${tile}"]`).trigger('click')
  return wrapper.get(`.mode-panel[data-mode="${tile}"]`)
}
async function stageFromDock(wrapper: Panel) {
  await wrapper.get('.dock-confirm').trigger('click')
}

describe('D&D 2024 combat panel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.locale = 'en'
    i18n.global.locale.value = 'en'
    mocks.fetch.mockReset().mockResolvedValue(response('none'))
    mocks.submit.mockReset().mockResolvedValue(response('active'))
    mocks.decide.mockReset().mockResolvedValue(response('active'))
    mocks.plan.mockReset().mockResolvedValue({ ok: false, error: 'unavailable' })
  })

  it('keeps known spells visible with the reason an unavailable spell cannot be cast', async () => {
    mocks.locale = 'zh-CN'
    const data = response('active') as any
    data.gameplay.combat.actors[0].spells = [
      { spell_ref: 'spell:divine_smite', name: '至圣斩', blocked_reason: 'requires_melee_hit' },
      { spell_ref: 'spell:searing_smite', name: '炽焰斩', blocked_reason: 'not_automated' },
    ]
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    await wrapper.get('[data-tile="spell"]').trigger('click')
    const card = wrapper.get('[data-testid=combat-spell-card]')
    expect(card.text()).toContain('至圣斩')
    expect(card.text()).toContain('近战武器或徒手攻击命中后可用')
    expect(card.text()).toContain('尚未接入战斗自动结算')
    expect(card.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('lets a hit defender cast Shield as a reaction or take the hit', async () => {
    mocks.locale = 'zh-CN'
    const data = response('active') as any
    const decision = {
      decision_id: 'hit-reaction:1', kind: 'hit_reaction_spell', assigned_to: 'gm', options: ['cast', 'decline'],
      actor_id: 'player:gm', attacker_id: 'enemy:goblin-1', attack_total: 14, armor_class: 12,
      spells: [{ spell_ref: 'spell:shield', name: '护盾术', level: 1, mode: 'reaction_defense', available_slot_levels: [0, 1], armor_class_bonus: 5 }],
    }
    data.gameplay.combat.pending_decisions = [decision]
    data.available_actions = [{ type: 'decision.resolve', expected_version: 1, decisions: [decision] }]
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = wrapper.get('.decision-card')
    expect(card.text()).toContain('你被攻击命中了')
    expect(card.text()).toContain('攻击总值 14')
    expect(card.text()).toContain('本次攻击将落空')
    await card.get('.combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.decide).toHaveBeenCalledOnce()
    expect(mocks.decide.mock.calls[0][2]).toMatchObject({ option: 'cast', spell_ref: 'spell:shield', slot_level: 0 })
    wrapper.unmount()
  })

  it('offers the counterspell option for a pending counter window', async () => {
    mocks.locale = 'zh-CN'
    const data = response('active') as any
    const decision = {
      decision_id: 'decision:cast', kind: 'counter_spell', assigned_to: 'gm',
      options: ['counter', 'decline'], threat_actor_ids: ['enemy:goblin-1'],
    }
    data.gameplay.combat.pending_decisions = [decision]
    data.available_actions = [{ type: 'decision.resolve', expected_version: 1, decisions: [decision] }]
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = wrapper.get('.decision-card')
    expect(card.get('.combat-primary').text()).toBe('反制法术')
    await card.get('.combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.decide).toHaveBeenCalledOnce()
    expect(mocks.decide.mock.calls[0][2]).toMatchObject({ option: 'counter' })
    wrapper.unmount()
  })

  it('offers the free use and exact hit target while a melee-hit decision is pending', async () => {
    mocks.locale = 'zh-CN'
    const data = response('active') as any
    const decision = { decision_id: 'hit:1', kind: 'melee_hit_spell', assigned_to: 'gm', options: ['decline'] }
    data.gameplay.combat.pending_decisions = [decision]
    const targets = data.gameplay.combat.actors
    data.available_actions = [
      { type: 'decision.resolve', expected_version: 1, decisions: [decision] },
      { type: 'cast_spell', expected_version: 1, actor_id: 'player:gm', targets: [...targets, { ...targets[1], actor_id: 'enemy:other', name: 'Other' }], spells: [
        { spell_ref: 'spell:divine_smite', name: '至圣斩', level: 1, mode: 'triggered_damage', casting_time: 'Bonus Action', available_slot_levels: [0, 1], trigger_target_id: 'enemy:goblin-1' },
      ] },
    ]
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    expect(wrapper.get('.decision-card').text()).toContain('不追加法术，结算攻击')
    expect(wrapper.get('.decision-card').findAll('button')).toHaveLength(1)
    const card = wrapper.get('[data-testid=combat-spell-card]')
    expect(card.text()).toContain('免费施放（长休恢复）')
    expect(card.findAll('select').at(-1)?.findAll('option')).toHaveLength(1)
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit).toHaveBeenCalledOnce()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({ type: 'cast_spell', spell_ref: 'spell:divine_smite', actor_id: 'player:gm', target_id: 'enemy:goblin-1', slot_level: 0 })
    expect(mocks.submit.mock.calls[0][1]).not.toHaveProperty('damage')
    wrapper.unmount()
  })

  it('keeps hostile attack targets separate from allied spell targets', async () => {
    const data = response('active') as any
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets: data.gameplay.combat.actors, spells: [{ spell_ref: 'spell:cure_wounds', name: 'Cure Wounds', level: 1, mode: 'healing', casting_time: 'Action', available_slot_levels: [1] }],
    })
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const attackCard = await openTile(wrapper, 'attack')
    expect((attackCard.findAll('select')[1]!.element as HTMLSelectElement).value).toBe('enemy:goblin-1')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({ type: 'attack', target_id: 'enemy:goblin-1' })
    const spellCard = await openTile(wrapper, 'spell')
    expect((spellCard.findAll('select')[2]!.element as HTMLSelectElement).value).toBe('player:gm')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[1][1]).toMatchObject({ type: 'cast_spell', target_id: 'player:gm' })
    wrapper.unmount()
  })

  it('projects a self-only spell onto the caster', async () => {
    const data = response('active') as any
    const targets = [
      ...data.gameplay.combat.actors,
      { actor_id: 'player:ally', kind: 'player', side: 'party', name: 'Ally', hp: 12, max_hp: 12, position: 0, is_dead: false },
    ]
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets,
      spells: [{
        spell_ref: 'spell:false_life', name: 'False Life', level: 1, mode: 'temp_hp',
        casting_time: 'Action', available_slot_levels: [1],
        target_scope: 'self', target_side: 'ally', target_count: 1,
      }],
    })
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = await openTile(wrapper, 'spell')
    const targetSelect = card.findAll('select').at(-1)!
    expect(targetSelect.findAll('option')).toHaveLength(1)
    expect((targetSelect.element as HTMLSelectElement).value).toBe('player:gm')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({ type: 'cast_spell', target_id: 'player:gm' })
    expect(mocks.submit.mock.calls[0][1]).not.toHaveProperty('target_ids')
    wrapper.unmount()
  })

  it('offers only dead allies for a revive spell', async () => {
    const data = response('active') as any
    const targets = [
      ...data.gameplay.combat.actors,
      { actor_id: 'player:fallen', kind: 'player', side: 'party', name: 'Fallen', hp: 0, max_hp: 12, position: 0, is_dead: true },
      { actor_id: 'player:dying', kind: 'player', side: 'party', name: 'Dying', hp: 0, max_hp: 12, position: 0, is_dead: false },
      // 服务端 Revivify 只接受 player，死亡 companion 不能出现在合法候选中。
      { actor_id: 'companion:fallen', kind: 'companion', side: 'party', name: 'Fallen Companion', hp: 0, max_hp: 12, position: 0, is_dead: true },
    ]
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets,
      spells: [{
        spell_ref: 'spell:revivify', name: 'Revivify', level: 3, mode: 'revive',
        casting_time: 'Action', available_slot_levels: [3],
        target_side: 'ally', target_count: 1,
      }],
    })
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = await openTile(wrapper, 'spell')
    const targetSelect = card.findAll('select').at(-1)!
    expect(targetSelect.findAll('option')).toHaveLength(1)
    expect((targetSelect.element as HTMLSelectElement).value).toBe('player:fallen')
    wrapper.unmount()
  })

  it('submits multiple targets for an upcast spell', async () => {
    const data = response('active') as any
    const goblin = data.gameplay.combat.actors[1]
    const targets = [
      ...data.gameplay.combat.actors,
      { ...goblin, actor_id: 'enemy:goblin-2', name: 'Goblin 2' },
    ]
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets,
      spells: [{
        spell_ref: 'spell:hold_person', name: 'Hold Person', level: 2, mode: 'saving_throw',
        casting_time: 'Action', available_slot_levels: [2, 3],
        target_side: 'enemy', target_count: 1, upcast_target_count: 1,
      }],
    })
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = await openTile(wrapper, 'spell')
    await card.findAll('select')[1]!.setValue('3')
    await flushPromises()
    await card.findAll('select')[2]!.setValue('2')
    await flushPromises()
    expect(card.findAll('select')[3]!.findAll('option')).toHaveLength(2)
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    const sent = mocks.submit.mock.calls[0][1]
    expect(sent).toMatchObject({ type: 'cast_spell', target_ids: ['enemy:goblin-1', 'enemy:goblin-2'] })
    expect(sent).not.toHaveProperty('target_id')
    wrapper.unmount()
  })

  it('allows repeated targets for a ray spell', async () => {
    const data = response('active') as any
    const goblin = data.gameplay.combat.actors[1]
    const targets = [
      ...data.gameplay.combat.actors,
      { ...goblin, actor_id: 'enemy:goblin-2', name: 'Goblin 2' },
    ]
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets,
      spells: [{
        spell_ref: 'spell:scorching_ray', name: 'Scorching Ray', level: 2, mode: 'spell_attack',
        casting_time: 'Action', available_slot_levels: [2],
        target_side: 'enemy', target_count: 3, upcast_target_count: 1, repeat_targets: true,
      }],
    })
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = await openTile(wrapper, 'spell')
    expect(card.findAll('select')[2]!.findAll('option')).toHaveLength(3)
    await card.findAll('select')[2]!.setValue('3')
    await flushPromises()
    expect(card.findAll('select')).toHaveLength(6)
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    const sent = mocks.submit.mock.calls[0][1]
    expect(sent).toMatchObject({ type: 'cast_spell' })
    expect(sent.target_ids).toHaveLength(3)
    expect(sent).not.toHaveProperty('target_id')
    wrapper.unmount()
  })

  it('requires and submits a resistance choice', async () => {
    const data = response('active') as any
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets: data.gameplay.combat.actors,
      spells: [{
        spell_ref: 'spell:protection_from_energy', name: 'Protection from Energy', level: 3,
        mode: 'buff', casting_time: 'Action', available_slot_levels: [3],
        target_side: 'ally', target_count: 1, resist_choice: ['acid', 'fire'],
      }],
    })
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = await openTile(wrapper, 'spell')
    const resist = card.findAll('select').find(select => select.text().includes('Acid'))!
    await resist.setValue('fire')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'cast_spell', target_id: 'player:gm', resist_type: 'fire',
    })
    wrapper.unmount()
  })

  it('requires and submits a teleport distance', async () => {
    const data = response('active') as any
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets: data.gameplay.combat.actors,
      spells: [{
        spell_ref: 'spell:misty_step', name: 'Misty Step', level: 2, mode: 'teleport',
        casting_time: 'Bonus Action', available_slot_levels: [2],
        target_scope: 'self', target_side: 'ally', target_count: 1,
      }],
    })
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    await wrapper.get('#dock-tab-bonus').trigger('click')
    const card = await openTile(wrapper, 'spell')
    expect(wrapper.get('.dock-confirm').attributes('disabled')).toBeDefined()
    await card.get('input[type=number]').setValue('25')
    expect(wrapper.get('.dock-confirm').attributes('disabled')).toBeUndefined()
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'cast_spell', target_id: 'player:gm', teleport_distance: 25,
    })
    wrapper.unmount()
  })

  it('lets dispel magic target either side', async () => {
    const data = response('active') as any
    data.available_actions.push({ type: 'cast_spell', expected_version: 1, actor_id: 'player:gm',
      targets: data.gameplay.combat.actors,
      spells: [{
        spell_ref: 'spell:dispel_magic', name: 'Dispel Magic', level: 3, mode: 'dispel',
        casting_time: 'Action', available_slot_levels: [3],
        target_side: 'any', target_count: 1,
      }],
    })
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    const card = await openTile(wrapper, 'spell')
    const targetSelect = card.findAll('select').at(-1)!
    expect(targetSelect.findAll('option')).toHaveLength(2)
    await targetSelect.setValue('enemy:goblin-1')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'cast_spell', target_id: 'enemy:goblin-1',
    })
    wrapper.unmount()
  })

  it('floats the resolved damage above the struck unit and clears it afterwards', async () => {
    const data = response('active') as any
    mocks.fetch.mockResolvedValue(data)
    mocks.submit.mockResolvedValue({
      ...data,
      result: {
        resolved_event_batches: [{ events: [
          { type: 'check.resolved', kind: 'attack', actor_id: 'player:gm', target_id: 'enemy:goblin-1', success: true },
          { type: 'resource.changed', resource: 'hp', target_id: 'enemy:goblin-1', delta: -8 },
        ] }],
      },
    })
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    await openTile(wrapper, 'attack')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()

    const goblin = wrapper.get('.battle-token[data-actor-id="enemy:goblin-1"]')
    expect(goblin.get('.floater.damage').text()).toBe('-8')
    expect(goblin.classes()).toContain('struck')
    expect(wrapper.get('.battle-token[data-actor-id="player:gm"]').find('.floater').exists()).toBe(false)

    vi.advanceTimersByTime(3000)
    await flushPromises()
    expect(wrapper.find('.floater').exists()).toBe(false)
    wrapper.unmount()
  })

  it('uses the first legal slot for an exploration spell and rejects an empty slot list', async () => {
    mocks.fetch.mockResolvedValueOnce(explorationResponse())
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    const card = await openTile(wrapper, 'explore')
    await card.find('select').setValue('spell:cure_wounds')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'exploration.cast_spell', spell_ref: 'spell:cure_wounds', slot_level: 1,
    })
    wrapper.unmount()

    mocks.fetch.mockResolvedValueOnce(explorationResponse([]))
    const unavailable = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()
    const unavailableCard = await openTile(unavailable, 'explore')
    await unavailableCard.find('select').setValue('spell:cure_wounds')
    expect(unavailable.get('.dock-confirm').attributes('disabled')).toBeDefined()
    unavailable.unmount()
  })

  it('keeps the catalog hidden in a fresh game and starts from a server-provided preset after explicit preparation', async () => {
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('No combat is active')
    expect(wrapper.text()).not.toContain('First Skirmish')
    expect(wrapper.find('.encounter-start select').exists()).toBe(false)
    expect(wrapper.find('.ai-encounter-toggle').exists()).toBe(true)
    await wrapper.get('.manual-encounter-toggle').trigger('click')
    expect(wrapper.text()).toContain('First Skirmish')
    await wrapper.get('.encounter-start .combat-primary').trigger('click')
    await flushPromises()

    expect(mocks.submit).toHaveBeenCalledOnce()
    const payload = mocks.submit.mock.calls[0][1]
    expect(payload).toMatchObject({
      type: 'combat.start', expected_version: 0,
      encounter_preset_id: 'first_skirmish',
      enemies: [{ id: 'goblin-1', hp: 7 }],
    })
    expect(payload).not.toHaveProperty('submitted_by')
    wrapper.unmount()
  })

  it('clears the previous game immediately and ignores its late combat response', async () => {
    let resolveOld: (value: ReturnType<typeof response>) => void = () => undefined
    mocks.fetch
      .mockImplementationOnce(() => new Promise<ReturnType<typeof response>>(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce(response('none'))
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|old|bot', actorId: 'gm', isGm: true },
    })

    await wrapper.setProps({ gameKey: 'web|fresh|bot' })
    await flushPromises()
    resolveOld(response('active'))
    await flushPromises()

    expect(mocks.fetch.mock.calls.map(call => call[0])).toEqual([
      'web|old|bot', 'web|fresh|bot',
    ])
    expect(wrapper.text()).not.toContain('Goblin')
    expect(wrapper.text()).toContain('No combat is active')
    wrapper.unmount()
  })

  it('explains when the shared narrative requested authoritative combat', async () => {
    const requested = response('none') as any
    requested.gameplay.encounter_request = { status: 'pending', source: 'narrative', round: 3 }
    mocks.fetch.mockResolvedValueOnce(requested)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('The AI GM detected an engagement')
    // 没有匹配到合法遭遇时不得自动展示通用目录，更不能默认选中地精。
    expect(wrapper.find('.encounter-start select').exists()).toBe(false)
    expect(wrapper.find('.encounter-start .combat-primary').exists()).toBe(false)
    expect(wrapper.find('.ai-encounter-toggle').exists()).toBe(true)
    await wrapper.get('.manual-encounter-toggle').trigger('click')
    expect(wrapper.text()).toContain('Select a free encounter')
    expect(wrapper.text()).toContain('could not match the current opposition')
    expect(wrapper.get('.encounter-start select').element).toBeTruthy()
    wrapper.unmount()
  })

  it('reports an unprepared story encounter instead of substituting a generic preset', async () => {
    const unprepared = response('none') as any
    unprepared.gameplay.encounter_access = {
      mode: 'blocked', status: 'blocked', can_start: false, unprepared: true,
      adventure_id: 'lanterns_of_greymoor', encounter_preset_id: '',
      encounter_instance_id: '', origin_step_id: 'greycloak_standoff',
      catalog: 'bundle',
    }
    unprepared.gameplay.campaign = {
      session_zero: { status: 'locked', revision: 1, responses: {} },
      session_zero_defaults: {}, proposals: [], entities: {}, chapter_summaries: [],
      tutorial: {
        status: 'active', coach_enabled: true, history: [], hints_used: {},
        adventure: { id: 'lanterns_of_greymoor', name: 'The Lost Lanterns', summary: '', estimated_minutes: 90, chapter_count: 3 },
        current_step: {
          id: 'greycloak_standoff', chapter_id: 'greycloak', title: 'Cloaked Strangers',
          narration: 'Two cloaked figures block the road.', objective: 'Decide how to approach.',
          hint: 'Talk or fight.', requires: 'none', encounter_preset_id: '', choices: [],
        }, requirement_met: false,
      },
    }
    unprepared.gameplay.encounter_request = { status: 'pending', source: 'narrative', round: 2 }
    unprepared.available_actions = []
    mocks.fetch.mockResolvedValueOnce(unprepared)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('The current story has no prepared encounter')
    expect(wrapper.text()).toContain('will not substitute a generic training encounter')
    // 地精/狼既不显示，也不能被一键开战。
    expect(wrapper.find('.encounter-start select').exists()).toBe(false)
    expect(wrapper.find('.encounter-start > .combat-primary').exists()).toBe(false)
    expect(wrapper.find('.manual-encounter-toggle').exists()).toBe(false)

    // AI 临时准备遭遇：只调用只读 plan API，不自动开战、不出现通用目录。
    await wrapper.get('.guided-preset .unprepared-actions .combat-primary').trigger('click')
    expect(mocks.plan).toHaveBeenCalledWith('web|combat|bot')
    expect(wrapper.find('.encounter-start select').exists()).toBe(false)
    // GM 明确“临时准备自由遭遇”后才出现通用目录，且提交必须声明 sandbox。
    await wrapper.get('.guided-preset .unprepared-actions button:last-child').trigger('click')
    expect(wrapper.text()).toContain('not written into the adventure package')
    await wrapper.get('.encounter-start .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'combat.start', mode: 'sandbox', encounter_preset_id: 'first_skirmish',
    })
    wrapper.unmount()
  })

  it('never declares a story-bound encounter as sandbox', async () => {
    const guided = response('none') as any
    guided.gameplay.encounter_access = {
      mode: 'story', status: 'pending', can_start: true, unprepared: false,
      adventure_id: 'lanterns_of_greymoor', encounter_preset_id: 'first_skirmish',
      encounter_instance_id: 'tutorial:lanterns_of_greymoor:thorn_ambush',
      origin_step_id: 'thorn_ambush', catalog: 'adventure',
    }
    guided.available_actions = [{
      type: 'combat.start', label: 'Start Combat', expected_version: 0,
      requires: ['encounter_preset_id'], encounter_preset_id: 'first_skirmish',
      encounter_instance_id: 'tutorial:lanterns_of_greymoor:thorn_ambush',
    }]
    guided.gameplay.campaign = {
      session_zero: { status: 'locked', revision: 1, responses: {} },
      session_zero_defaults: {}, proposals: [], entities: {}, chapter_summaries: [],
      tutorial: {
        status: 'active', coach_enabled: true, history: [], hints_used: {},
        adventure: { id: 'lanterns_of_greymoor', name: 'The Lost Lanterns', summary: '', estimated_minutes: 90, chapter_count: 3 },
        current_step: {
          id: 'thorn_ambush', chapter_id: 'thorn_glade', title: 'The First Encounter',
          narration: 'A goblin notices you in the grove.', objective: 'Learn initiative and take one action.',
          hint: 'Move or shoot.', requires: 'combat_ended', encounter_preset_id: 'first_skirmish', choices: [],
        }, requirement_met: false,
      },
    }
    mocks.fetch.mockResolvedValueOnce(guided)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.get('.guided-preset strong').text()).toBe('First Skirmish')
    expect(wrapper.text()).toContain('Encounter：first_skirmish')
    expect(wrapper.find('.ai-encounter-toggle').exists()).toBe(false)
    await wrapper.get('.encounter-start .combat-primary').trigger('click')
    await flushPromises()
    const payload = mocks.submit.mock.calls[0][1]
    expect(payload).toMatchObject({
      type: 'combat.start', encounter_preset_id: 'first_skirmish',
      encounter_instance_id: 'tutorial:lanterns_of_greymoor:thorn_ambush',
    })
    expect(payload).not.toHaveProperty('mode')
    wrapper.unmount()
  })

  it('flags an active free combat as outside the adventure package', async () => {
    const free = response('active') as any
    free.gameplay.combat.mode = 'sandbox'
    free.gameplay.combat.adventure_binding = null
    free.gameplay.campaign = {
      session_zero: { status: 'locked', revision: 1, responses: {} },
      session_zero_defaults: {}, proposals: [], entities: {}, chapter_summaries: [],
      tutorial: {
        status: 'active', coach_enabled: true, history: [], hints_used: {},
        adventure: { id: 'lanterns_of_greymoor', name: 'The Lost Lanterns', summary: '', estimated_minutes: 90, chapter_count: 3 },
        current_step: null, requirement_met: false,
      },
    }
    mocks.fetch.mockResolvedValueOnce(free)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.get('.combat-mode-badge').text()).toBe('Free combat · not part of the active adventure')
    wrapper.unmount()
  })

  it('does not present another enemy as an immediate next battle after combat ends', async () => {
    const ended = response('active') as any
    ended.gameplay.combat.status = 'ended'
    ended.gameplay.combat.outcome = 'victory'
    ended.gameplay.combat.current_actor_id = ''
    ended.available_actions = [{ type: 'combat.start', label: 'Start Combat', expected_version: 2, requires: ['enemies'] }]
    mocks.fetch.mockResolvedValueOnce(ended)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Combat has ended')
    expect(wrapper.text()).toContain('Prepare next encounter')
    expect(wrapper.find('.encounter-ended select').exists()).toBe(false)
    await wrapper.get('.encounter-ended-actions .combat-primary').trigger('click')
    expect(wrapper.find('.encounter-ended select').exists()).toBe(true)
    // 与「手动准备遭遇」一致：打开选择器后必须立刻可开战，而不是等用户先动一下下拉框。
    expect(wrapper.get('.next-encounter-picker .combat-primary').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('uses the AI GM catalog match as a one-click combat confirmation', async () => {
    const requested = response('none') as any
    requested.gameplay.encounter_request = {
      status: 'pending', source: 'narrative', round: 3,
      encounter_preset_id: 'first_skirmish', confidence: 0.94,
    }
    requested.gameplay.encounter_presets.push({
      id: 'crypt_pair', name: 'Crypt Pair', description: 'Two undead opponents',
      difficulty: 'standard', enemies: [{ id: 'skeleton-1', hp: 13 }],
    })
    mocks.fetch.mockResolvedValueOnce(requested)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('AI GM matched opposition')
    expect(wrapper.get('.guided-preset strong').text()).toBe('First Skirmish')
    expect(wrapper.find('.encounter-alternatives select').exists()).toBe(true)
    await wrapper.get('.encounter-start .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'combat.start', encounter_preset_id: 'first_skirmish',
    })
    wrapper.unmount()
  })

  it('lets a party member ready up without granting combat-start authority', async () => {
    const requested = response('none') as any
    requested.gameplay.encounter_request = {
      status: 'pending', source: 'narrative', encounter_preset_id: 'first_skirmish',
      ready_player_ids: [],
      readiness: {
        ready_player_ids: [], required_player_ids: ['ally'], ready_count: 0,
        required_count: 1, all_ready: false,
        players: [{ player_id: 'ally', name: 'Scout', ready: false }],
      },
    }
    requested.available_actions = [{ type: 'encounter.ready', label: 'Ready', expected_version: 0 }]
    mocks.fetch.mockResolvedValueOnce(requested)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'ally', isGm: false },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Scout')
    expect(wrapper.text()).toContain('Not ready')
    expect(wrapper.text()).toContain('Waiting for the GM')
    expect(wrapper.find('select').exists()).toBe(false)
    await wrapper.get('.party-readiness button').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'encounter.ready', expected_version: 0,
    })
    wrapper.unmount()
  })

  it('names the requested encounter for a player view without the GM-only preset catalog', async () => {
    const requested = response('none') as any
    delete requested.gameplay.encounter_presets
    requested.gameplay.encounter_preview = {
      id: 'first_skirmish', name: 'First Skirmish', description: 'Standard encounter',
    }
    requested.gameplay.encounter_request = {
      status: 'pending', source: 'narrative', encounter_preset_id: 'first_skirmish',
      ready_player_ids: [],
    }
    requested.available_actions = [{ type: 'encounter.ready', label: 'Ready', expected_version: 0 }]
    mocks.fetch.mockResolvedValueOnce(requested)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'ally', isGm: false },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('AI GM matched opposition')
    expect(wrapper.text()).toContain('First Skirmish')
    expect(wrapper.text()).toContain('Standard encounter')
    expect(wrapper.find('select').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a guided story encounter to a player without flagging the absent catalog as broken', async () => {
    const guided = response('none') as any
    delete guided.gameplay.encounter_presets
    guided.gameplay.encounter_preview = { id: 'first_skirmish', name: 'First Skirmish', description: 'Ambush' }
    guided.gameplay.campaign = {
      tutorial: {
        status: 'active',
        current_step: {
          id: 'fight', title: 'Ambush', narration: 'Goblins leap out.',
          requires: 'combat_ended', encounter_preset_id: 'first_skirmish',
        },
      },
    }
    guided.available_actions = []
    mocks.fetch.mockResolvedValueOnce(guided)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'ally', isGm: false },
    })
    await flushPromises()

    expect(wrapper.get('.guided-preset strong').text()).toBe('First Skirmish')
    expect(wrapper.find('.guided-preset .combat-error').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows the GM every party member readiness state before combat', async () => {
    const requested = response('none') as any
    requested.gameplay.encounter_request = {
      status: 'pending', source: 'narrative', encounter_preset_id: 'first_skirmish',
      ready_player_ids: ['ally-a'],
      readiness: {
        ready_player_ids: ['ally-a'], required_player_ids: ['ally-a', 'ally-b'],
        ready_count: 1, required_count: 2, all_ready: false,
        players: [
          { player_id: 'ally-a', name: 'Scout', ready: true },
          { player_id: 'ally-b', name: 'Mage', ready: false },
        ],
      },
    }
    mocks.fetch.mockResolvedValueOnce(requested)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Party ready 1/2')
    expect(wrapper.text()).toContain('Scout')
    expect(wrapper.text()).toContain('Mage')
    expect(wrapper.find('.party-readiness button').exists()).toBe(false)
    expect(wrapper.find('.encounter-start > .combat-primary').exists()).toBe(true)
    wrapper.unmount()
  })

  it('carries the selected adventure into its story encounter and selects its preset', async () => {
    const guided = response('none') as any
    guided.gameplay.encounter_access = {
      mode: 'story', status: 'pending', can_start: true, unprepared: false,
      adventure_id: 'lanterns_of_greymoor', encounter_preset_id: 'first_skirmish',
      encounter_instance_id: 'tutorial:lanterns_of_greymoor:thorn_ambush',
      origin_step_id: 'thorn_ambush', catalog: 'adventure',
    }
    guided.gameplay.campaign = {
      session_zero: { status: 'locked', revision: 1, responses: {} },
      session_zero_defaults: {}, proposals: [], entities: {}, chapter_summaries: [],
      tutorial: {
        status: 'active', coach_enabled: true, history: [], hints_used: {},
        adventure: { id: 'lanterns_of_greymoor', name: 'The Lost Lanterns', summary: '', estimated_minutes: 90, chapter_count: 3 },
        current_step: {
          id: 'thorn_ambush', chapter_id: 'thorn_glade', title: 'The First Encounter',
          narration: 'A goblin notices you in the grove.', objective: 'Learn initiative and take one action.',
          hint: 'Move or shoot.', requires: 'combat_ended', encounter_preset_id: 'first_skirmish', choices: [],
        }, requirement_met: false,
      },
    }
    mocks.fetch.mockResolvedValueOnce(guided)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Story encounter')
    expect(wrapper.text()).toContain('A goblin notices you in the grove.')
    expect(wrapper.text()).toContain('current opposition comes from the active adventure')
    expect(wrapper.get('.guided-preset strong').text()).toBe('First Skirmish')
    expect(wrapper.find('.encounter-start select').exists()).toBe(false)
    wrapper.unmount()
  })

  it('stages an attack for confirmation before submitting it', async () => {
    mocks.fetch.mockResolvedValueOnce(response('active'))
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    await openTile(wrapper, 'attack')
    await stageFromDock(wrapper)
    expect(wrapper.find('.confirm-card').exists()).toBe(true)
    expect(mocks.submit).not.toHaveBeenCalled()
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()

    expect(mocks.submit).toHaveBeenCalledOnce()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'attack', actor_id: 'player:gm', target_id: 'enemy:goblin-1',
      weapon_ref: 'item:greatsword', expected_version: 1,
    })
    wrapper.unmount()
  })

  it('selects an in-range weapon first and blocks weapons that cannot reach the target', async () => {
    const distant = response('active')
    const attack = distant.available_actions[0] as unknown as {
      weapons: Array<Record<string, unknown>>
      targets: Array<{ position: number }>
    }
    attack.weapons = [
      { id: 'greatsword', weapon_ref: 'item:greatsword', damage: '2d6', range: 5 },
      { id: 'javelin', weapon_ref: 'item:javelin', damage: '1d6', thrown_range: 30, long_range: 120 },
    ]
    attack.targets[0].position = 25
    distant.gameplay.combat.actors[1].position = 25
    mocks.fetch.mockResolvedValueOnce(distant)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    const weaponSelect = (await openTile(wrapper, 'attack')).get('select')
    expect((weaponSelect.element as HTMLSelectElement).value).toBe('item:javelin')
    const options = weaponSelect.findAll('option')
    expect(options[0].attributes('disabled')).toBeDefined()
    expect(options[0].text()).toContain('Out of range')
    expect(options[1].text()).toContain('In range')
    expect(wrapper.text()).toContain('Distance to target 25 ft')

    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({ weapon_ref: 'item:javelin' })
    wrapper.unmount()
  })

  it('hides tutorial presets from standard free-play encounter selection', async () => {
    const standard = response('none') as any
    standard.gameplay.encounter_presets.push({
      id: 'training_only', name: 'Training Only', description: 'Help sandbox',
      difficulty: 'tutorial', enemies: [{ id: 'dummy', hp: 1 }],
    })
    mocks.fetch.mockResolvedValueOnce(standard)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    await wrapper.get('.manual-encounter-toggle').trigger('click')
    const options = wrapper.findAll('.encounter-start option').map(option => option.text())
    expect(options).toContain('First Skirmish · Standard')
    expect(options.join(' ')).not.toContain('Training Only')
    wrapper.unmount()
  })

  it('renders localized weapon and damage display without changing its canonical ref', async () => {
    mocks.locale = 'zh-CN'
    const localized = response('active')
    const attack = localized.available_actions[0] as any
    attack.weapons[0].name = '巨剑'
    mocks.fetch.mockResolvedValueOnce(localized)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect((await openTile(wrapper, 'attack')).get('option').text()).toContain('巨剑 · 2d6 挥砍')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({ weapon_ref: 'item:greatsword' })
    wrapper.unmount()
  })

  it('uses the tactical track to select an authoritative movement distance', async () => {
    mocks.fetch.mockResolvedValueOnce(response('active'))
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.findAll('.battle-token')).toHaveLength(2)
    const destination = wrapper.get('button[aria-label="Position 20 ft"]')
    expect(destination.classes()).toContain('reachable')
    await destination.trigger('click')

    const movementInput = wrapper.get('.mode-panel[data-mode="move"] input[type="number"]')
    expect((movementInput.element as HTMLInputElement).value).toBe('20')
    expect(destination.classes()).toContain('selected')
    wrapper.unmount()
  })

  it('aims the attack when a unit on the battlefield is clicked', async () => {
    const twoFoes = response('active') as any
    twoFoes.gameplay.combat.actors.push(
      { actor_id: 'enemy:goblin-2', kind: 'enemy', name: 'Goblin Archer', hp: 5, max_hp: 5, position: 25, armor_class: 12, conditions: {} },
    )
    twoFoes.available_actions[0].targets.push(
      { actor_id: 'enemy:goblin-2', kind: 'enemy', name: 'Goblin Archer', hp: 5, max_hp: 5, position: 25 },
    )
    mocks.fetch.mockResolvedValueOnce(twoFoes)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    const archer = wrapper.get('.battle-token[data-actor-id="enemy:goblin-2"]')
    await archer.get('.token-hit').trigger('click')

    expect(archer.classes()).toContain('targeted')
    expect(wrapper.get('.range-bridge').text()).toContain('25 ft')
    expect(wrapper.get('.target-card').text()).toContain('Goblin Archer')
    expect(wrapper.get('.target-card').text()).toContain('Distance')
    wrapper.unmount()
  })

  it('keeps marking the aimed target while the player inspects another unit', async () => {
    const data = response('active') as any
    data.gameplay.combat.actors[0].conditions = { dodging: {}, blessed: {} }
    mocks.fetch.mockResolvedValueOnce(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()

    await openTile(wrapper, 'attack')
    await wrapper.get('.battle-token[data-actor-id="player:gm"] .token-hit').trigger('click')

    expect(wrapper.get('.battle-token[data-actor-id="enemy:goblin-1"]').classes()).toContain('targeted')
    expect(wrapper.get('.battle-token[data-actor-id="player:gm"]').classes()).not.toContain('targeted')
    expect(wrapper.get('.dock-target').text()).toContain('Goblin')
    // The side card follows the click, and lists statuses with the locale's separator.
    expect(wrapper.get('.target-card').text()).toContain('Guardian')
    expect(wrapper.get('.target-card').text()).toContain('Dodging, Blessed')
    wrapper.unmount()
  })

  it('shows the movement ghost only while a move is being planned', async () => {
    mocks.fetch.mockResolvedValueOnce(response('active'))
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()

    expect(wrapper.find('.move-ghost').exists()).toBe(false)
    expect(wrapper.find('.stage-floor button.selected').exists()).toBe(false)
    await wrapper.get('#dock-tab-move').trigger('click')
    expect(wrapper.get('button[aria-label="Position 5 ft"]').find('.move-ghost').exists()).toBe(true)
    wrapper.unmount()
  })

  it('moves between dock tabs with the arrow keys', async () => {
    mocks.fetch.mockResolvedValueOnce(response('active'))
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true }, attachTo: document.body,
    })
    await flushPromises()

    const actionTab = wrapper.get('#dock-tab-action')
    expect(actionTab.attributes('tabindex')).toBe('0')
    expect(wrapper.get('#dock-tab-bonus').attributes('tabindex')).toBe('-1')
    await actionTab.trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.get('#dock-tab-bonus').attributes('aria-selected')).toBe('true')
    expect(document.activeElement?.id).toBe('dock-tab-bonus')
    await wrapper.get('#dock-tab-bonus').trigger('keydown', { key: 'End' })
    expect(wrapper.get('#dock-tab-reaction').attributes('aria-selected')).toBe('true')
    wrapper.unmount()
  })

  it('keeps a bonus-action spell and slot picked across the periodic refresh', async () => {
    const data = response('active') as any
    data.available_actions.push({
      type: 'cast_spell', expected_version: 1, actor_id: 'player:gm', targets: data.gameplay.combat.actors,
      spells: [
        { spell_ref: 'spell:cure_wounds', name: 'Cure Wounds', level: 1, mode: 'healing', casting_time: 'Action', available_slot_levels: [1] },
        { spell_ref: 'spell:healing_word', name: 'Healing Word', level: 1, mode: 'healing', casting_time: 'Bonus Action', available_slot_levels: [1, 2] },
      ],
    })
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()

    await wrapper.get('#dock-tab-bonus').trigger('click')
    const card = await openTile(wrapper, 'spell')
    expect((card.findAll('select')[0]!.element as HTMLSelectElement).value).toBe('spell:healing_word')
    await card.findAll('select')[1]!.setValue('2')

    vi.advanceTimersByTime(30000)
    await flushPromises()
    expect(mocks.fetch).toHaveBeenCalledTimes(2)

    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'cast_spell', spell_ref: 'spell:healing_word', slot_level: 2,
    })
    wrapper.unmount()
  })

  it('floats the deferred hit damage once the melee-hit decision is declined', async () => {
    const data = response('active') as any
    const decision = { decision_id: 'hit:1', kind: 'melee_hit_spell', assigned_to: 'gm', options: ['decline'] }
    data.gameplay.combat.pending_decisions = [decision]
    data.available_actions = [{ type: 'decision.resolve', expected_version: 1, decisions: [decision] }, ...data.available_actions]
    mocks.fetch.mockResolvedValue(data)
    mocks.decide.mockResolvedValue({
      ...response('active'),
      result: { resolved_event_batches: [{ events: [
        { type: 'resource.changed', resource: 'hp', target_id: 'enemy:goblin-1', delta: -6 },
      ] }] },
    })
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()
    expect(wrapper.get('#dock-tab-bonus').attributes('aria-selected')).toBe('true')

    await wrapper.get('.decision-card button').trigger('click')
    await flushPromises()

    expect(mocks.decide).toHaveBeenCalledOnce()
    expect(wrapper.get('.battle-token[data-actor-id="enemy:goblin-1"] .floater').text()).toBe('-6')
    expect(wrapper.get('#dock-tab-action').attributes('aria-selected')).toBe('true')
    wrapper.unmount()
  })

  it('shows a waiting state when the server exposes no action', async () => {
    const waiting = response('active')
    waiting.available_actions = []
    mocks.fetch.mockResolvedValueOnce(waiting)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'player-two', isGm: false },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Waiting for teammate：Guardian')
    expect(wrapper.find('.combat-actions').exists()).toBe(false)
    wrapper.unmount()
  })

  it("keeps the dock closed on another actor's turn and never shows their economy", async () => {
    const data = response('active') as any
    data.gameplay.combat.current_actor_id = 'enemy:goblin-1'
    data.gameplay.combat.turn_index = 1
    data.gameplay.combat.economy = { action: 0, bonus_action: 1, movement: 0, reaction: 1 }
    data.gameplay.combat.reactions = { 'player:gm': 0 }
    data.available_actions = [{ type: 'combat.message', label: 'Combat message', expected_version: 1 }]
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: false } })
    await flushPromises()

    expect(wrapper.find('[data-tile]').exists()).toBe(false)
    expect(wrapper.find('[role=tab]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Pick an action')
    expect(wrapper.find('.combat-state').exists()).toBe(true)
    wrapper.unmount()
  })

  it('still lets the GM end the combat while an enemy acts', async () => {
    const data = response('active') as any
    data.gameplay.combat.current_actor_id = 'enemy:goblin-1'
    data.gameplay.combat.turn_index = 1
    data.available_actions = [{ type: 'combat.end', label: 'End Combat', expected_version: 1 }]
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()

    expect(wrapper.find('[data-tile]').exists()).toBe(false)
    expect(wrapper.find('.dock-end').exists()).toBe(true)
    wrapper.unmount()
  })

  it("reads the reaction tab from the viewer's own reaction, not the turn economy", async () => {
    const data = response('active') as any
    data.gameplay.combat.economy = { action: 1, bonus_action: 1, movement: 30, reaction: 1 }
    data.gameplay.combat.reactions = { 'player:gm': 0 }
    mocks.fetch.mockResolvedValue(data)
    const wrapper = mount(Dnd2024CombatPanel, { props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true } })
    await flushPromises()

    const tabs = wrapper.findAll('[role=tab]')
    const reaction = tabs[tabs.length - 1]
    expect(reaction.text()).toContain('Reaction')
    expect(reaction.classes()).toContain('spent')
    wrapper.unmount()
  })

  it('shows a seated player the exact current and maximum HP of a wounded enemy', async () => {
    const wounded = response('active') as any
    wounded.available_actions = []
    wounded.gameplay.combat.actors[1] = { ...wounded.gameplay.combat.actors[1], hp: 3, max_hp: 7 }
    mocks.fetch.mockResolvedValueOnce(wounded)
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'player-two', isGm: false },
    })
    await flushPromises()

    const card = wrapper.findAll('.battle-token.enemy').find(item => item.text().includes('Goblin'))!
    expect(card.text()).toContain('HP 3/7')
    const bar = card.get('.hp-track')
    expect(bar.attributes('aria-valuenow')).toBe('3')
    expect(bar.attributes('aria-valuemax')).toBe('7')
    expect(bar.get('i').attributes('style')).toContain(`width: ${3 / 7 * 100}%`)
    wrapper.unmount()
  })

  it('focuses explicit confirmation and returns to the campaign after combat ends', async () => {
    const ending = response('active')
    ;(ending.available_actions as unknown as Array<Record<string, unknown>>).push({
      type: 'combat.end', label: 'End Combat', actor_id: 'player:gm', expected_version: 1,
    })
    mocks.fetch.mockResolvedValueOnce(ending)
    mocks.submit.mockResolvedValue(response('none'))
    const wrapper = mount(Dnd2024CombatPanel, {
      attachTo: document.body,
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    await wrapper.get('.dock-end.danger').trigger('click')
    await flushPromises()
    expect(wrapper.get('.confirm-card').element).toBe(document.activeElement)
    expect(wrapper.get('.confirm-card').attributes('role')).toBe('group')
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()

    expect(mocks.submit.mock.calls[0][1]).toMatchObject({ type: 'combat.end' })
    expect(wrapper.emitted('navigate')).toEqual([['campaign']])
    wrapper.unmount()
  })

  // ---- AI 临时遭遇草稿：默认摘要、按需编辑弹窗、保存后摘要更新 ----

  function aiEncounter() {
    return {
      ok: true,
      encounter: {
        title: 'Wolf Pack', description: 'Three wolves stalk the ruins.',
        enemies: [
          {
            id: 'wolf-1', name: 'Wolf', hp: 11, armor_class: 13, speed: 40,
            position: 25, initiative_modifier: 2,
            attacks: [{ id: 'bite', name: 'Bite', attack_bonus: 4, damage: '1d6+2', range: 5, long_range: 5 }],
          },
          {
            id: 'wolf-2', name: 'Wolf', hp: 11, armor_class: 13, speed: 40, position: 30,
            attacks: [{ id: 'bite', name: 'Bite', attack_bonus: 4, damage: '1d6+2', range: 5, long_range: 5 }],
          },
          {
            id: 'dire-wolf', name: 'Dire Wolf', hp: 18, armor_class: 14, speed: 40, position: 35,
            attacks: [{ id: 'bite', name: 'Bite', attack_bonus: 5, damage: '2d6+3', range: 5, long_range: 5 }],
          },
        ],
      },
    }
  }

  // 重新生成返回一份完全不同的提案：用来证明旧草稿的脏字段不会残留。
  function regeneratedEncounter() {
    return {
      ok: true,
      encounter: {
        title: 'Boar', description: 'A boar charges.',
        enemies: [{
          id: 'boar-1', name: 'Boar', hp: 13, armor_class: 12, speed: 40, position: 20,
          attacks: [{ id: 'tusk', name: 'Tusk', attack_bonus: 3, damage: '1d6+1', range: 5, long_range: 5 }],
        }],
      },
    }
  }

  // 编辑弹窗走 Teleport：测试里把它就地渲染，才能直接查询弹窗内容。
  async function mountWithDraft(proposal: unknown = aiEncounter()) {
    mocks.plan.mockResolvedValue(proposal)
    const wrapper = mount(Dnd2024CombatPanel, {
      global: { stubs: { Teleport: true } },
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()
    await wrapper.get('.ai-encounter-toggle').trigger('click')
    await flushPromises()
    return wrapper
  }

  async function openEnemyEditor(wrapper: ReturnType<typeof mount>) {
    await wrapper.get('.ai-encounter-preview .ai-encounter-edit').trigger('click')
    await flushPromises()
  }

  async function expandDraftEnemy(wrapper: ReturnType<typeof mount>, index = 0) {
    await wrapper.findAll('.ai-encounter-enemies .ai-draft-toggle')[index].trigger('click')
  }

  // actions 顺序固定为：恢复 AI 原稿 / 取消 / 保存修改。
  async function saveEnemyEditor(wrapper: ReturnType<typeof mount>) {
    await wrapper.get('.modal .dialog .actions .combat-primary').trigger('click')
    await flushPromises()
  }

  function draftInput(wrapper: ReturnType<typeof mount>, label: string, index = 0) {
    return wrapper.findAll(`.ai-encounter-enemies input[aria-label="${label}"]`)[index]
  }

  function summaryLines(wrapper: ReturnType<typeof mount>) {
    return wrapper.findAll('.ai-encounter-summary li').map(item => item.text())
  }

  function submittedEnemies() {
    return mocks.submit.mock.calls[0][1].enemies as Array<Record<string, any>>
  }

  it('shows an encounter summary instead of the full enemy editor by default', async () => {
    const wrapper = await mountWithDraft()

    expect(summaryLines(wrapper)).toEqual(['Wolf ×2', 'Dire Wolf ×1'])
    // 默认没有几十个输入框：编辑器与弹窗都不存在，只有一个「编辑敌人」入口。
    expect(wrapper.find('.ai-encounter-enemies').exists()).toBe(false)
    expect(wrapper.find('.modal').exists()).toBe(false)
    expect(wrapper.find('.ai-encounter-preview .ai-encounter-edit').exists()).toBe(true)
    wrapper.unmount()
  })

  it('opens the enemy editor only after 编辑敌人 and updates the summary after saving', async () => {
    const wrapper = await mountWithDraft()
    expect(wrapper.find('.ai-draft-fields').exists()).toBe(false)

    await openEnemyEditor(wrapper)
    expect(wrapper.get('.modal .dialog h2').text()).toBe(i18n.global.t('encounterEditorTitle'))
    // 弹窗内部每个敌人默认折叠。
    expect(wrapper.find('.ai-draft-fields').exists()).toBe(false)

    await expandDraftEnemy(wrapper, 0)
    await draftInput(wrapper, 'Name').setValue('Grey Wolf')
    await draftInput(wrapper, 'Qty').setValue('3')
    await draftInput(wrapper, 'HP').setValue('9')
    // 保存前摘要不动：弹窗编辑的是工作副本。
    expect(summaryLines(wrapper)).toEqual(['Wolf ×2', 'Dire Wolf ×1'])

    await saveEnemyEditor(wrapper)
    expect(wrapper.find('.modal').exists()).toBe(false)
    expect(wrapper.find('.ai-encounter-enemies').exists()).toBe(false)
    expect(summaryLines(wrapper)).toEqual(['Grey Wolf ×3', 'Wolf ×1', 'Dire Wolf ×1'])
    wrapper.unmount()
  })

  it('discards unsaved editor changes when the GM cancels', async () => {
    const wrapper = await mountWithDraft()
    await openEnemyEditor(wrapper)
    await expandDraftEnemy(wrapper, 0)
    await draftInput(wrapper, 'Name').setValue('Grey Wolf')
    await wrapper.get('.modal .dialog .actions button:nth-child(2)').trigger('click')
    await flushPromises()

    expect(wrapper.find('.modal').exists()).toBe(false)
    expect(summaryLines(wrapper)).toEqual(['Wolf ×2', 'Dire Wolf ×1'])
    await openEnemyEditor(wrapper)
    await expandDraftEnemy(wrapper, 0)
    expect((draftInput(wrapper, 'Name').element as HTMLInputElement).value).toBe('Wolf')
    wrapper.unmount()
  })

  it('regenerates a fresh draft through planRulesetTemporaryEncounter and drops stale edits', async () => {
    const wrapper = await mountWithDraft()
    await openEnemyEditor(wrapper)
    await expandDraftEnemy(wrapper, 0)
    await draftInput(wrapper, 'Name').setValue('Stale Wolf')

    mocks.plan.mockResolvedValueOnce(regeneratedEncounter())
    await wrapper.get('.ai-encounter-preview .unprepared-actions button:nth-child(2)').trigger('click')
    await flushPromises()

    expect(mocks.plan).toHaveBeenCalledTimes(2)
    // 重新生成后弹窗必须关掉，不能把上一个提案的脏字段留在编辑状态里。
    expect(wrapper.find('.modal').exists()).toBe(false)
    expect(summaryLines(wrapper)).toEqual(['Boar ×1'])
    await openEnemyEditor(wrapper)
    await expandDraftEnemy(wrapper, 0)
    expect((draftInput(wrapper, 'Name').element as HTMLInputElement).value).toBe('Boar')
    wrapper.unmount()
  })

  it('sends only the selected enemies after the GM unticks one draft entry', async () => {
    const wrapper = await mountWithDraft()
    await openEnemyEditor(wrapper)
    const checkboxes = wrapper.findAll('.ai-encounter-enemies input[type="checkbox"]')
    expect(checkboxes.length).toBe(3)
    await checkboxes[1].setValue(false)
    await saveEnemyEditor(wrapper)
    expect(summaryLines(wrapper)).toEqual(['Wolf ×1', 'Dire Wolf ×1'])

    await wrapper.get('.ai-encounter-preview .combat-primary').trigger('click')
    await flushPromises()

    expect(mocks.submit).toHaveBeenCalledOnce()
    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'combat.start', mode: 'sandbox', temporary_encounter: true,
    })
    expect(submittedEnemies().map(enemy => enemy.id)).toEqual(['wolf-1', 'dire-wolf'])
    wrapper.unmount()
  })

  it('expands saved quantities into uniquely identified instances with the edited stats', async () => {
    const wrapper = await mountWithDraft()
    await openEnemyEditor(wrapper)
    await expandDraftEnemy(wrapper, 0)
    await draftInput(wrapper, 'Qty').setValue('3')
    await draftInput(wrapper, 'HP').setValue('9')
    await draftInput(wrapper, 'Armor Class').setValue('15')
    await saveEnemyEditor(wrapper)
    await wrapper.get('.ai-encounter-preview .combat-primary').trigger('click')
    await flushPromises()

    const enemies = submittedEnemies()
    expect(enemies.map(enemy => enemy.id)).toEqual([
      'wolf-1', 'wolf-1-2', 'wolf-1-3', 'wolf-2', 'dire-wolf',
    ])
    expect(enemies[1]).toMatchObject({ id: 'wolf-1-2', hp: 9, armor_class: 15 })
    expect(enemies[0]).not.toHaveProperty('quantity')
    expect(enemies[0]).not.toHaveProperty('selected')
    wrapper.unmount()
  })

  it('submits the edited enemy and attack fields to combat.start', async () => {
    const wrapper = await mountWithDraft()
    await openEnemyEditor(wrapper)
    await expandDraftEnemy(wrapper, 0)
    await draftInput(wrapper, 'Name').setValue('Grey Wolf')
    await draftInput(wrapper, 'HP').setValue('9')
    await draftInput(wrapper, 'Attack bonus').setValue('6')
    await draftInput(wrapper, 'Damage formula').setValue('1d8+2')
    await draftInput(wrapper, 'Normal range').setValue('10')
    await draftInput(wrapper, 'Long range').setValue('20')
    await saveEnemyEditor(wrapper)
    await wrapper.get('.ai-encounter-preview .combat-primary').trigger('click')
    await flushPromises()

    const [first] = submittedEnemies()
    expect(first).toMatchObject({
      id: 'wolf-1', name: 'Grey Wolf', hp: 9, armor_class: 13,
      attacks: [{ id: 'bite', name: 'Bite', attack_bonus: 6, damage: '1d8+2', range: 10, long_range: 20 }],
    })
    wrapper.unmount()
  })

  it('disables the start action when every draft enemy is unticked', async () => {
    const wrapper = await mountWithDraft()
    await openEnemyEditor(wrapper)
    for (const checkbox of wrapper.findAll('.ai-encounter-enemies input[type="checkbox"]')) {
      await checkbox.setValue(false)
    }
    await saveEnemyEditor(wrapper)

    expect(summaryLines(wrapper)).toEqual([])
    expect(wrapper.text()).toContain(i18n.global.t('encounterNoEnemiesSelected'))
    expect(wrapper.get('.ai-encounter-preview .combat-primary').attributes('disabled')).toBeDefined()
    expect(mocks.submit).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('restores the untouched AI proposal from the editor', async () => {
    const wrapper = await mountWithDraft()
    await openEnemyEditor(wrapper)
    await expandDraftEnemy(wrapper, 0)
    await draftInput(wrapper, 'Name').setValue('Grey Wolf')
    await draftInput(wrapper, 'HP').setValue('99')
    await wrapper.get('.modal .dialog .actions button:first-child').trigger('click')

    expect((draftInput(wrapper, 'Name').element as HTMLInputElement).value).toBe('Wolf')
    expect((draftInput(wrapper, 'HP').element as HTMLInputElement).value).toBe('11')
    await saveEnemyEditor(wrapper)
    expect(summaryLines(wrapper)).toEqual(['Wolf ×2', 'Dire Wolf ×1'])

    await wrapper.get('.ai-encounter-preview .combat-primary').trigger('click')
    await flushPromises()
    const [first] = submittedEnemies()
    expect(first).toMatchObject({ id: 'wolf-1', name: 'Wolf', hp: 11, armor_class: 13 })
    wrapper.unmount()
  })
})

// ---------------------------------------------------------------------------
// Class feature capabilities (Monk v1)
//
// These tests mock the real server contract: available intents shaped like the
// D&D runtime's `class_capability` entries plus the actor's projected
// `class_resources`.  They never mock "is a monk" -- the panel has no such
// concept, and the frontend must never invent a capability the server omitted.
// ---------------------------------------------------------------------------

interface CapabilityFixture {
  id: string
  label: string
  costs: Array<Record<string, unknown>>
  requiresTarget?: boolean
}

function capabilityCosts(includeFocus: boolean): Array<Record<string, unknown>> {
  const costs: Array<Record<string, unknown>> = [
    { kind: 'bonus_action', name: 'Bonus Action', amount: 1, current: 1, maximum: 1 },
  ]
  if (includeFocus) {
    costs.push({
      kind: 'resource', name: 'Focus Points', amount: 1, current: 2, maximum: 2,
    })
  }
  return costs
}

const MONK_TARGET = {
  actor_id: 'enemy:goblin-1', kind: 'enemy', name: 'Goblin',
  hp: 7, max_hp: 7, position: 5,
}

function monkResponse(options: {
  focus?: number | null
  bonusActionAvailable?: boolean
  capabilities?: CapabilityFixture[]
} = {}) {
  const result = response('active') as any
  const focus = options.focus
  if (options.bonusActionAvailable === false) {
    result.gameplay.combat.economy.bonus_action = 0
  }
  result.gameplay.combat.actors[0].class_resources = focus === null || focus === undefined
    ? []
    : [{ id: 'focus_points', name: 'Focus Points', current: focus, maximum: 2 }]
  result.available_actions = [
    ...result.available_actions,
    ...(options.capabilities || []).map(capability => ({
      type: 'class_capability',
      actor_id: 'player:gm',
      expected_version: 1,
      capability_id: capability.id,
      label: capability.label,
      costs: capability.costs,
      requires_target: capability.requiresTarget !== false,
      ...(capability.requiresTarget === false ? {} : { targets: [MONK_TARGET] }),
    })),
  ]
  return result
}

function levelTwoCapabilities(): CapabilityFixture[] {
  return [
    { id: 'bonus_unarmed_strike', label: 'Bonus Unarmed Strike', costs: capabilityCosts(false) },
    { id: 'flurry_of_blows', label: 'Flurry of Blows', costs: capabilityCosts(true) },
    { id: 'patient_defense', label: 'Patient Defense', costs: capabilityCosts(false), requiresTarget: false },
    { id: 'step_of_the_wind', label: 'Step of the Wind', costs: capabilityCosts(false), requiresTarget: false },
  ]
}

async function mountMonk(payload: ReturnType<typeof monkResponse>) {
  mocks.fetch.mockResolvedValueOnce(payload)
  const wrapper = mount(Dnd2024CombatPanel, {
    props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
  })
  await flushPromises()
  return wrapper
}

async function capabilityButtons(wrapper: ReturnType<typeof mount>) {
  if (!wrapper.find('.mode-panel[data-mode="capability"]').exists()) {
    const tile = wrapper.find('[data-tile="capability"]')
    if (tile.exists()) await tile.trigger('click')
  }
  return wrapper.findAll('.capability-list button')
}

describe('D&D 2024 combat panel class capabilities', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.locale = 'en'
    mocks.fetch.mockReset().mockResolvedValue(response('none'))
    mocks.submit.mockReset().mockResolvedValue(response('active'))
    mocks.decide.mockReset().mockResolvedValue(response('active'))
    mocks.plan.mockReset().mockResolvedValue({ ok: false, error: 'unavailable' })
  })

  it('renders the server-provided capabilities with their costs and the projected Focus', async () => {
    const wrapper = await mountMonk(monkResponse({
      focus: 2, capabilities: levelTwoCapabilities(),
    }))

    const buttons = await capabilityButtons(wrapper)
    const card = wrapper.get('.mode-panel[data-mode="capability"]')
    expect(wrapper.get('[data-tile="capability"]').text()).toContain('Class abilities')
    expect(card.text()).toContain('Flurry of Blows')
    expect(card.text()).toContain('Focus Points 1（2/2）')
    expect(card.text()).toContain('Bonus Unarmed Strike')
    expect(wrapper.get('.class-resource-strip').text()).toContain('Focus Points')
    expect(wrapper.get('.class-resource-strip').text()).toContain('2 / 2')
    expect(buttons.map(button => button.text())).toHaveLength(4)
    wrapper.unmount()
  })

  it('submits the canonical capability intent and refreshes Focus and the bonus action at once', async () => {
    const wrapper = await mountMonk(monkResponse({
      focus: 2, capabilities: levelTwoCapabilities(),
    }))
    const spent = monkResponse({ focus: 1, bonusActionAvailable: false, capabilities: [] })
    mocks.submit.mockResolvedValueOnce(spent)

    const flurry = (await capabilityButtons(wrapper))
      .find(button => button.text().includes('Flurry of Blows'))!
    await flurry.trigger('click')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()

    expect(mocks.submit.mock.calls[0][1]).toMatchObject({
      type: 'class_capability',
      actor_id: 'player:gm',
      capability_id: 'flurry_of_blows',
      target_id: 'enemy:goblin-1',
    })
    // 服务端在同一份响应里返回新状态：专注点与附赠动作立即刷新，无需重新加载。
    expect(wrapper.get('.class-resource-strip').text()).toContain('1 / 2')
    expect(await capabilityButtons(wrapper)).toHaveLength(0)
    expect(wrapper.text()).toContain('No class capability is available this turn.')
    wrapper.unmount()
  })

  it('never invents a capability the server did not return', async () => {
    const wrapper = await mountMonk(monkResponse({ focus: 2, capabilities: [] }))

    expect(await capabilityButtons(wrapper)).toHaveLength(0)
    // 资源仍然可见（服务端投影了 Focus），但按钮只可能来自服务端 capability。
    expect(wrapper.get('.class-resource-strip').text()).toContain('2 / 2')
    wrapper.unmount()
  })

  it('omits the target field for capabilities the server does not require a target for', async () => {
    const wrapper = await mountMonk(monkResponse({
      focus: 2,
      capabilities: [{
        id: 'step_of_the_wind', label: 'Step of the Wind',
        costs: capabilityCosts(false), requiresTarget: false,
      }],
    }))

    await (await capabilityButtons(wrapper))[0]!.trigger('click')
    await stageFromDock(wrapper)
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()

    const payload = mocks.submit.mock.calls[0][1]
    expect(payload).toMatchObject({ type: 'class_capability', capability_id: 'step_of_the_wind' })
    expect(payload).not.toHaveProperty('target_id')
    wrapper.unmount()
  })

  it('shows a level 1 monk capability without any Focus resource', async () => {
    const wrapper = await mountMonk(monkResponse({
      focus: null,
      capabilities: [{ id: 'bonus_unarmed_strike', label: 'Bonus Unarmed Strike', costs: capabilityCosts(false) }],
    }))

    expect(wrapper.find('.class-resource-strip').exists()).toBe(false)
    expect(await capabilityButtons(wrapper)).toHaveLength(1)
    wrapper.unmount()
  })

  it('hides the whole card for a character without class resources or capabilities', async () => {
    // 非武僧（也没有任何职业能力）：不出现空的 Bonus Action / 职业能力入口。
    const wrapper = await mountMonk(response('active') as any)

    expect(wrapper.find('[data-tile="capability"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Class abilities')
    wrapper.unmount()
  })

  it('still renders a legacy sheet whose actors carry no class-resource projection', async () => {
    // 升级前的老角色卡（以及升级前的服务端）根本没有这两个投影字段：面板必须
    // 照常渲染，只是不显示职业资源。
    const payload = monkResponse({ focus: null, capabilities: [] })
    for (const actor of payload.gameplay.combat.actors) {
      delete (actor as any).class_resources
      delete (actor as any).class_features
    }

    const wrapper = await mountMonk(payload)

    expect(wrapper.find('.turn-banner').exists()).toBe(true)
    expect(wrapper.find('.class-resource-strip').exists()).toBe(false)
    expect(wrapper.find('[data-tile="capability"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders a server-provided capability for a legacy sheet without resources', async () => {
    // 能力来自服务端 available_actions，资源投影缺失时只影响资源条。
    const payload = monkResponse({
      focus: null,
      capabilities: [{ id: 'bonus_unarmed_strike', label: 'Bonus Unarmed Strike', costs: capabilityCosts(false) }],
    })
    for (const actor of payload.gameplay.combat.actors) {
      delete (actor as any).class_resources
    }

    const wrapper = await mountMonk(payload)

    expect(wrapper.find('.class-resource-strip').exists()).toBe(false)
    expect(await capabilityButtons(wrapper)).toHaveLength(1)
    wrapper.unmount()
  })

  it('still renders while an enemy actor holds the turn and projects no class resources', async () => {
    // 敌人 actor 没有职业资源，服务端的 per-actor 投影对它们曾经发过 `{}`；
    // 前端只能渲染数组，遇到别的形状必须退化为「没有职业资源」，绝不能让
    // 一次渲染异常把整块战斗面板（以及整页）带下去（Browser smoke 的实际故障）。
    const payload = monkResponse({ focus: null, capabilities: [] })
    payload.gameplay.combat.current_actor_id = 'enemy:goblin-1'
    payload.gameplay.combat.actors[1].class_resources = {}

    const wrapper = await mountMonk(payload)

    expect(wrapper.find('.turn-banner').exists()).toBe(true)
    expect(wrapper.find('.class-resource-strip').exists()).toBe(false)
    expect(wrapper.find('[data-tile="capability"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('ignores a legacy class-resource map instead of rendering or crashing on it', async () => {
    // 老存档可能保留另一种形状（id -> {current, maximum}）。它不是本 PR 的投影，
    // 里面没有本地化名字，因此只允许被忽略，不允许被当成列表使用。
    const payload = monkResponse({ focus: null, capabilities: [] })
    ;(payload.gameplay.combat.actors[0] as any).class_resources = {
      focus_points: { current: 2, maximum: 2 },
    }

    const wrapper = await mountMonk(payload)

    expect(wrapper.find('.turn-banner').exists()).toBe(true)
    expect(wrapper.find('.class-resource-strip').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('undefined')
    wrapper.unmount()
  })
})
