import { describe, expect, it } from 'vitest'

import {
  battleDistance, battlefieldLayoutFor, buildBattleTokens, lineExtent, lineSlots,
} from '../src/features/rulesets/dnd2024/combat/hud/battleView'
import type { RulesetCombatTarget } from '../src/api/types'

function actor(id: string, position: number, extra: Partial<RulesetCombatTarget> = {}): RulesetCombatTarget {
  return {
    actor_id: id, kind: id.startsWith('enemy:') ? 'enemy' : 'player', name: id,
    hp: 10, max_hp: 10, position, ...extra,
  }
}

describe('combat HUD battle view', () => {
  it('maps actors to tokens with side, own and current flags', () => {
    const tokens = buildBattleTokens([
      actor('player:gm', 0), actor('enemy:goblin-1', 5, { hp: 0, conditions: { dodging: {} } }),
    ], 'player:gm', 'gm')

    expect(tokens[0]).toMatchObject({ side: 'ally', isOwn: true, isCurrent: true, coord: { x: 0 } })
    expect(tokens[1]).toMatchObject({
      side: 'enemy', isOwn: false, isCurrent: false, isDefeated: true, hpRatio: 0, conditions: ['dodging'],
    })
  })

  it('tells a downed party member apart from a defeated enemy or a dead ally', () => {
    const [dying, enemy, dead] = buildBattleTokens([
      actor('player:a', 0, { hp: 0, conditions: { unconscious: {}, death_saves: {} } }),
      actor('enemy:b', 5, { hp: 0 }),
      actor('player:c', 0, { hp: 0, conditions: { dead: {} } }),
    ], '', 'a')
    expect(dying).toMatchObject({ isDying: true, isDefeated: false })
    expect(enemy).toMatchObject({ isDying: false, isDefeated: true })
    expect(dead).toMatchObject({ isDying: false, isDefeated: true })
  })

  it('clamps the HP ratio and survives a zero max HP', () => {
    const [over, none] = buildBattleTokens([
      actor('player:a', 0, { hp: 30, max_hp: 10 }), actor('player:b', 0, { hp: 0, max_hp: 0 }),
    ], '', 'a')
    expect(over!.hpRatio).toBe(1)
    expect(none!.hpRatio).toBe(0)
  })

  it('routes theater to the line layout and unknown modes to the roster', () => {
    expect(battlefieldLayoutFor('theater')).toBe('line')
    expect(battlefieldLayoutFor(undefined)).toBe('line')
    expect(battlefieldLayoutFor('hex_grid')).toBe('list')
  })

  it('measures distance through coordinates', () => {
    expect(battleDistance({ x: 5 }, { x: 25 })).toBe(20)
  })

  it('pads the line by one full move and keeps a 30 ft stage', () => {
    const extent = lineExtent(buildBattleTokens([actor('player:a', 0), actor('enemy:b', 5)], '', 'a'), 30)
    expect(extent.min).toBe(-30)
    expect(extent.max).toBe(60)
    expect(extent.ticks[0]).toBe(-30)
    expect(extent.ticks.at(-1)).toBe(60)
  })

  it('stacks units that would overlap and keeps distant ones on one row', () => {
    const tokens = buildBattleTokens([
      actor('player:a', 0), actor('player:b', 0), actor('enemy:c', 5), actor('enemy:d', 40),
    ], '', 'a')
    const slots = lineSlots(tokens, lineExtent(tokens, 0))

    expect(slots.get('player:a')!.lane).toBe(0)
    expect(slots.get('player:b')!.lane).toBe(1)
    expect(slots.get('enemy:c')!.lane).toBe(1 + 1) // neighbours of both rows
    expect(slots.get('enemy:d')!.lane).toBe(0)
  })
})
