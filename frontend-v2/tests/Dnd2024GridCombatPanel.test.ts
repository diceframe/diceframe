import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(), submit: vi.fn(), decide: vi.fn(), plan: vi.fn(), api: vi.fn(),
  locale: 'en',
}))

vi.mock('../src/api/client', async importOriginal => ({
  ...(await importOriginal<typeof import('../src/api/client')>()),
  api: mocks.api,
}))
vi.mock('../src/features/rulesets/dnd2024/api', () => ({
  fetchRulesetAvailableActions: mocks.fetch,
  submitRulesetIntent: mocks.submit,
  resolveRulesetDecision: mocks.decide,
  planRulesetTemporaryEncounter: mocks.plan,
}))
vi.mock('../src/composables/useLocale', () => ({
  useLocale: () => ({
    locale: ref(mocks.locale),
    t: (key: string, params?: Record<string, unknown>) => i18n.global.t(key as never, params as never),
  }),
}))

import Dnd2024CombatPanel from '../src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue'

function gridResponse(withBoard = true) {
  const board = {
    id: 'scene:chapel', width: 10, height: 10, cell_size: 5, distance_metric: 'dnd2024',
    walls: [{ x0: 4.5, y0: 0, x1: 4.5, y1: 4 }],
  }
  return {
    ok: true,
    game_key: 'web|combat|bot',
    rule_id: 'dnd2024_srd',
    ruleset_runtime: {
      id: 'core:dnd2024', version: 1, requested_minimum_version: 1,
      capabilities: { experience_profile: 'dnd2024', authoritative_intents: true, deterministic_combat: true, versioned_state: true },
    },
    gameplay: {
      state_schema_version: 1,
      state_version: 1,
      encounter_presets: [],
      encounter_access: { mode: 'sandbox', status: 'active', can_start: false, unprepared: false, catalog: 'bundle' },
      combat: {
        status: 'active', round: 1, turn_index: 0,
        current_actor_id: 'player:gm',
        initiative: ['player:gm', 'enemy:goblin-1'],
        position_mode: 'grid',
        ...(withBoard ? { board } : {}),
        spatial_position: {
          'player:gm': { x: 0, y: 0, footprint: { w: 1, h: 1 } },
          'enemy:goblin-1': { x: 6, y: 6, footprint: { w: 1, h: 1 } },
        },
        economy: { action: 1, bonus_action: 1, movement: 30, reaction: 1 },
        reactions: {}, pending_decisions: [],
        mode: 'sandbox', adventure_binding: null,
        actors: [
          {
            actor_id: 'player:gm', kind: 'player', name: 'Guardian', hp: 12, max_hp: 12, position: 0,
            armor_class: 16, conditions: {},
            spatial_position: { x: 0, y: 0, footprint: { w: 1, h: 1 } }, position_authority: 'combat',
          },
          {
            actor_id: 'enemy:goblin-1', kind: 'enemy', name: 'Goblin', hp: 7, max_hp: 7, position: 0,
            armor_class: 12, conditions: {},
            spatial_position: { x: 6, y: 6, footprint: { w: 1, h: 1 } }, position_authority: 'combat',
          },
        ],
      },
    },
    available_actions: [{
      type: 'move', label: 'Move', actor_id: 'player:gm', expected_version: 1, movement_remaining: 30,
    }, {
      type: 'end_turn', label: 'End Turn', actor_id: 'player:gm', expected_version: 1,
    }],
  }
}

async function mountPanel(data: unknown) {
  mocks.fetch.mockResolvedValue(data)
  const wrapper = mount(Dnd2024CombatPanel, {
    props: { gameKey: 'web|combat|bot', actorId: 'gm', isGm: true },
  })
  await flushPromises()
  return wrapper
}

describe('D&D 2024 grid combat panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.mockRejectedValue(new Error('no portraits'))
    mocks.submit.mockResolvedValue(gridResponse())
  })

  it('renders the grid board instead of the theater track when the server declares grid', async () => {
    const wrapper = await mountPanel(gridResponse())
    expect(wrapper.find('[data-testid="grid-board"]').exists()).toBe(true)
    expect(wrapper.find('.stage-scroll').exists()).toBe(false)
    expect(wrapper.findAll('.grid-token')).toHaveLength(2)
    wrapper.unmount()
  })

  it('falls back to the roster when grid is declared without a board (no fake map)', async () => {
    const wrapper = await mountPanel(gridResponse(false))
    expect(wrapper.find('[data-testid="grid-board"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="battlefield-roster"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('stages a target_cell move intent (never a distance) from a reachable cell', async () => {
    const wrapper = await mountPanel(gridResponse())
    await wrapper.get('[data-cell="2,0"]').trigger('click')
    await flushPromises()

    // The move dock shows the chosen cell, not a theater distance input.
    expect(wrapper.find('.mode-panel[data-mode="move"] input[type="number"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="grid-move-cell"]').text()).toContain('2, 0')

    await wrapper.get('.dock-confirm').trigger('click')
    await flushPromises()
    expect(wrapper.get('.confirm-card code').text()).toContain('2, 0')

    await wrapper.get('.confirm-card .combat-primary').trigger('click')
    await flushPromises()

    expect(mocks.submit).toHaveBeenCalledTimes(1)
    const payload = mocks.submit.mock.calls[0]![1] as Record<string, unknown>
    expect(payload.type).toBe('move')
    expect(payload.actor_id).toBe('player:gm')
    expect(payload.target_cell).toEqual({ x: 2, y: 0 })
    expect(payload.distance).toBeUndefined()
    wrapper.unmount()
  })

  it('does not stage a move when no cell has been chosen', async () => {
    const wrapper = await mountPanel(gridResponse())
    await wrapper.get('#dock-tab-move').trigger('click')
    const confirm = wrapper.get('.dock-confirm')
    expect(confirm.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})