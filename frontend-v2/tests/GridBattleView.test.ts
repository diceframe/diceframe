import { describe, expect, it } from 'vitest'

import {
  battleDistance, battlefieldLayoutFor, buildBattleTokens, gridCellKey,
  gridReachableCells, gridWallBlocks,
} from '../src/features/rulesets/dnd2024/combat/hud/battleView'
import type { RulesetCombatTarget, RulesetGridBoard } from '../src/api/types'

function actor(id: string, x: number, y: number, extra: Partial<RulesetCombatTarget> = {}): RulesetCombatTarget {
  return {
    actor_id: id, kind: id.startsWith('enemy:') ? 'enemy' : 'player', name: id,
    hp: 10, max_hp: 10, position: 0,
    spatial_position: { x, y, footprint: { w: 1, h: 1 } },
    position_authority: 'combat',
    ...extra,
  }
}

function board(overrides: Partial<RulesetGridBoard> = {}): RulesetGridBoard {
  return { id: 'scene:test', width: 10, height: 10, cell_size: 5, distance_metric: 'dnd2024', walls: [], ...overrides }
}

describe('grid battle view', () => {
  it('registers grid alongside theater and degrades unknown modes to the roster', () => {
    expect(battlefieldLayoutFor('grid')).toBe('grid')
    expect(battlefieldLayoutFor('theater')).toBe('line')
    expect(battlefieldLayoutFor(undefined)).toBe('line')
    expect(battlefieldLayoutFor('zone')).toBe('list')
    expect(battlefieldLayoutFor('hex_grid')).toBe('list')
  })

  it('reads grid coordinates and footprint from the combat-owned spatial_position', () => {
    const [token] = buildBattleTokens([
      actor('player:a', 3, 4, { spatial_position: { x: 3, y: 4, footprint: { w: 2, h: 2 } } }),
    ], 'player:a', 'a')
    expect(token!.coord).toEqual({ x: 3, y: 4 })
    expect(token!.footprint).toEqual({ w: 2, h: 2 })
    expect(token!.isCurrent).toBe(true)
  })

  it('keeps theater tokens single-axis when no spatial_position is present', () => {
    const [token] = buildBattleTokens([
      { actor_id: 'player:a', kind: 'player', name: 'a', hp: 10, max_hp: 10, position: 15 },
    ], '', 'a')
    expect(token!.coord).toEqual({ x: 15 })
    expect(token!.footprint).toBeUndefined()
  })

  it('measures grid distance with the Chebyshev rule (diagonals cost one cell)', () => {
    expect(battleDistance({ x: 0, y: 0 }, { x: 3, y: 4 }, 5)).toBe(20)
    expect(battleDistance({ x: 0, y: 0 }, { x: 3, y: 3 }, 5)).toBe(15)
    // theater stays single-axis
    expect(battleDistance({ x: 5 }, { x: 25 })).toBe(20)
  })

  it('highlights cells within the movement budget and excludes the current cell', () => {
    const tokens = buildBattleTokens([actor('player:a', 0, 0)], 'player:a', 'a')
    const reachable = gridReachableCells(board(), tokens, 'player:a', 30)
    expect(reachable.has(gridCellKey(6, 0))).toBe(true)
    expect(reachable.has(gridCellKey(0, 6))).toBe(true)
    expect(reachable.has(gridCellKey(6, 6))).toBe(true)
    expect(reachable.has(gridCellKey(7, 0))).toBe(false)
    expect(reachable.has(gridCellKey(0, 0))).toBe(false)
  })

  it('avoids cells occupied by another token footprint', () => {
    const tokens = buildBattleTokens([
      actor('player:a', 0, 0),
      actor('enemy:b', 2, 0, { spatial_position: { x: 2, y: 0, footprint: { w: 2, h: 1 } } }),
    ], 'player:a', 'a')
    const reachable = gridReachableCells(board(), tokens, 'player:a', 30)
    expect(reachable.has(gridCellKey(1, 0))).toBe(true)
    expect(reachable.has(gridCellKey(2, 0))).toBe(false)
    expect(reachable.has(gridCellKey(3, 0))).toBe(false)
  })

  it('avoids cells whose straight path crosses a wall', () => {
    const walls = [{ x0: 2.5, y0: -1, x1: 2.5, y1: 1 }]
    const tokens = buildBattleTokens([actor('player:a', 0, 0)], 'player:a', 'a')
    const reachable = gridReachableCells(board({ walls }), tokens, 'player:a', 30)
    expect(reachable.has(gridCellKey(1, 0))).toBe(true)
    expect(reachable.has(gridCellKey(3, 0))).toBe(false)
  })

  it('keeps a large footprint inside the board and returns nothing without budget', () => {
    const tokens = buildBattleTokens([
      actor('player:a', 8, 8, { spatial_position: { x: 8, y: 8, footprint: { w: 2, h: 2 } } }),
    ], 'player:a', 'a')
    const reachable = gridReachableCells(board(), tokens, 'player:a', 100)
    expect(reachable.has(gridCellKey(0, 0))).toBe(true)
    expect(reachable.has(gridCellKey(9, 9))).toBe(false)
    expect(gridReachableCells(board(), tokens, 'player:a', 0).size).toBe(0)
  })

  it('returns nothing for a theater token or a missing mover', () => {
    const theater = buildBattleTokens([
      { actor_id: 'player:a', kind: 'player', name: 'a', hp: 10, max_hp: 10, position: 0 },
    ], 'player:a', 'a')
    expect(gridReachableCells(board(), theater, 'player:a', 30).size).toBe(0)
    expect(gridReachableCells(board(), [], 'player:a', 30).size).toBe(0)
  })

  it('detects wall blocking on the anchor-to-anchor segment', () => {
    const walls = [{ x0: 2.5, y0: -1, x1: 2.5, y1: 1 }]
    expect(gridWallBlocks(walls, { x: 0, y: 0 }, { x: 3, y: 0 })).toBe(true)
    expect(gridWallBlocks(walls, { x: 0, y: 0 }, { x: 1, y: 0 })).toBe(false)
    expect(gridWallBlocks([], { x: 0, y: 0 }, { x: 3, y: 0 })).toBe(false)
  })
})