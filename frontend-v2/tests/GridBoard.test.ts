import { mount } from '@vue/test-utils'
import { nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../src/composables/useLocale', () => ({
  useLocale: () => ({
    locale: ref('en'),
    t: (key: string, params?: Record<string, unknown>) => (params ? `${key}:${JSON.stringify(params)}` : key),
  }),
}))

import GridBoard from '../src/features/rulesets/dnd2024/combat/hud/GridBoard.vue'
import type { BattleToken } from '../src/features/rulesets/dnd2024/combat/hud/battleView'
import type { RulesetGridBoard } from '../src/api/types'
import type { BattlefieldLabels } from '../src/features/rulesets/dnd2024/combat/hud/labels'

function token(id: string, x: number, y: number, extra: Partial<BattleToken> = {}): BattleToken {
  return {
    id, name: id, side: id.startsWith('enemy:') ? 'enemy' : 'ally',
    hp: 10, maxHp: 10, hpRatio: 1, conditions: [],
    coord: { x, y }, footprint: { w: 1, h: 1 },
    isCurrent: false, isDefeated: false, isDying: false, isOwn: false,
    ...extra,
  }
}

const labels: BattlefieldLabels = {
  hp: 'HP', feet: 'ft', position: 'Pos', condition: key => key, selectTarget: name => `Select ${name}`,
}

function board(overrides: Partial<RulesetGridBoard> = {}): RulesetGridBoard {
  return { id: 'scene:test', width: 10, height: 10, cell_size: 5, distance_metric: 'dnd2024', walls: [], ...overrides }
}

function mountBoard(props: Partial<InstanceType<typeof GridBoard>['$props']> = {}) {
  const wrapper = mount(GridBoard, {
    props: {
      board: board(),
      tokens: [token('player:a', 0, 0, { isCurrent: true, isOwn: true }), token('enemy:b', 5, 5)],
      movementRemaining: 30,
      targetId: '',
      selectableIds: ['player:a', 'enemy:b'],
      selectedCell: null,
      reachable: new Set(['1,0', '2,0']),
      canMove: true,
      labels,
      ...props,
    },
  })
  // jsdom has no layout; pin the svg box so pointer coords map to cells.
  const svg = wrapper.get('svg').element as SVGSVGElement
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 400, right: 400, bottom: 400, x: 0, y: 0, toJSON: () => ({}) })
  return wrapper
}

describe('GridBoard', () => {
  it('renders the board, walls and one group per token', () => {
    const wrapper = mountBoard({
      board: board({ walls: [{ x0: 2.5, y0: 0, x1: 2.5, y1: 3 }] }),
    })
    expect(wrapper.get('svg').attributes('viewBox')).toBe('0 0 400 400')
    expect(wrapper.findAll('.grid-wall')).toHaveLength(1)
    expect(wrapper.findAll('.grid-token')).toHaveLength(2)
    expect(wrapper.get('[data-token="player:a"]').classes()).toContain('current')
  })

  it('marks the targeted token and the selected cell', () => {
    const wrapper = mountBoard({ targetId: 'enemy:b', selectedCell: { x: 2, y: 0 } })
    expect(wrapper.get('[data-token="enemy:b"]').classes()).toContain('targeted')
    expect(wrapper.find('.grid-cell-selected').exists()).toBe(true)
    expect(wrapper.get('[data-cell="2,0"]').classes()).toContain('selected')
  })

  it('emits chooseCell when a reachable cell is clicked on your turn', async () => {
    const wrapper = mountBoard()
    await wrapper.get('[data-cell="2,0"]').trigger('click')
    expect(wrapper.emitted('chooseCell')).toEqual([[{ x: 2, y: 0 }]])
  })

  it('does not emit chooseCell when it is not your turn', async () => {
    const wrapper = mountBoard({ canMove: false })
    await wrapper.get('[data-cell="2,0"]').trigger('click')
    expect(wrapper.emitted('chooseCell')).toBeUndefined()
  })

  it('selects a target when a token is tapped without dragging', async () => {
    const wrapper = mountBoard()
    const enemy = wrapper.get('[data-token="enemy:b"]').element
    const svg = wrapper.get('svg').element
    enemy.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 220, clientY: 220 }))
    svg.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('selectToken')).toEqual([['enemy:b']])
    expect(wrapper.emitted('chooseCell')).toBeUndefined()
  })

  it('emits a move target cell when the acting token is dragged to a reachable cell', async () => {
    const wrapper = mountBoard()
    const mover = wrapper.get('[data-token="player:a"]').element
    const svg = wrapper.get('svg').element
    mover.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 20, clientY: 20 }))
    svg.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 100, clientY: 20 }))
    svg.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('chooseCell')).toEqual([[{ x: 2, y: 0 }]])
  })

  it('falls back to selecting the token when dragged to an unreachable cell', async () => {
    const wrapper = mountBoard()
    const mover = wrapper.get('[data-token="player:a"]').element
    const svg = wrapper.get('svg').element
    mover.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 20, clientY: 20 }))
    svg.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 300, clientY: 300 }))
    svg.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('chooseCell')).toBeUndefined()
    expect(wrapper.emitted('selectToken')).toEqual([['player:a']])
  })
})