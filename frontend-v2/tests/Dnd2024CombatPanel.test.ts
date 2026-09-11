import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(), submit: vi.fn(), decide: vi.fn(),
  locale: 'en',
}))

vi.mock('../src/features/rulesets/dnd2024/api', () => ({
  fetchRulesetAvailableActions: mocks.fetch,
  submitRulesetIntent: mocks.submit,
  resolveRulesetDecision: mocks.decide,
}))
vi.mock('../src/composables/useLocale', () => ({
  useLocale: () => ({ locale: ref(mocks.locale) }),
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

describe('D&D 2024 combat panel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.locale = 'en'
    mocks.fetch.mockReset().mockResolvedValue(response('none'))
    mocks.submit.mockReset().mockResolvedValue(response('active'))
    mocks.decide.mockReset().mockResolvedValue(response('active'))
  })

  it('keeps the catalog hidden in a fresh game and starts from a server-provided preset after explicit preparation', async () => {
    const wrapper = mount(Dnd2024CombatPanel, {
      props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('No combat is active')
    expect(wrapper.text()).not.toContain('First Skirmish')
    expect(wrapper.find('.encounter-start select').exists()).toBe(false)
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

    // GM 明确“脱离冒险，准备自由遭遇”后才出现通用目录，且提交必须声明 sandbox。
    await wrapper.get('.guided-preset .unprepared-actions .combat-primary').trigger('click')
    expect(wrapper.text()).toContain('leaves the active adventure package')
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

    await wrapper.get('.action-card button').trigger('click')
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

    const weaponSelect = wrapper.get('.action-card select')
    expect((weaponSelect.element as HTMLSelectElement).value).toBe('item:javelin')
    const options = weaponSelect.findAll('option')
    expect(options[0].attributes('disabled')).toBeDefined()
    expect(options[0].text()).toContain('Out of range')
    expect(options[1].text()).toContain('In range')
    expect(wrapper.text()).toContain('Distance to target 25 ft')

    await wrapper.get('.action-card button').trigger('click')
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

    expect(wrapper.get('.action-card option').text()).toContain('巨剑 · 2d6 挥砍')
    await wrapper.get('.action-card button').trigger('click')
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

    expect(wrapper.findAll('.track-token')).toHaveLength(2)
    const destination = wrapper.get('button[aria-label="Position 20 ft"]')
    expect(destination.classes()).toContain('reachable')
    await destination.trigger('click')

    const movementInput = wrapper.get('.action-card input[type="number"]')
    expect((movementInput.element as HTMLInputElement).value).toBe('20')
    expect(destination.classes()).toContain('selected')
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

    await wrapper.get('.compact-actions .danger').trigger('click')
    await flushPromises()
    expect(wrapper.get('.confirm-card').element).toBe(document.activeElement)
    expect(wrapper.get('.confirm-card').attributes('role')).toBe('group')
    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()

    expect(mocks.submit.mock.calls[0][1]).toMatchObject({ type: 'combat.end' })
    expect(wrapper.emitted('navigate')).toEqual([['campaign']])
    wrapper.unmount()
  })
})
