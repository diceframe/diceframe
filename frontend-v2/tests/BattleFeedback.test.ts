import { describe, expect, it } from 'vitest'

import {
  floaterLifetimeMs, floatersFromEvents, type FeedbackLabels,
} from '../src/features/rulesets/dnd2024/combat/hud/feedback'

const labels: FeedbackLabels = {
  miss: 'Miss', hit: 'Hit', saveOk: 'Saved', saveFail: 'Failed', blocked: 'Blocked',
  condition: key => `cond:${key}`,
}
const play = (events: Record<string, unknown>[]) => floatersFromEvents(events, labels, 1)

describe('combat HUD feedback floaters', () => {
  it('shows the damage number instead of "hit" when an attack lands', () => {
    const result = play([
      { type: 'check.resolved', kind: 'attack', actor_id: 'player:a', target_id: 'enemy:g', success: true },
      { type: 'resource.changed', resource: 'hp', target_id: 'enemy:g', delta: -8 },
    ])
    expect(result.map(item => [item.targetId, item.kind, item.text])).toEqual([['enemy:g', 'damage', '-8']])
  })

  it('marks a critical hit and a miss', () => {
    const crit = play([
      { type: 'check.resolved', kind: 'attack', target_id: 'enemy:g', success: true, critical: true },
      { type: 'resource.changed', resource: 'hp', target_id: 'enemy:g', delta: -14, critical: true },
    ])
    expect(crit[0]).toMatchObject({ kind: 'crit', text: '-14' })

    const miss = play([{ type: 'check.resolved', kind: 'spell_attack', target_id: 'enemy:g', success: false }])
    expect(miss[0]).toMatchObject({ targetId: 'enemy:g', kind: 'miss', text: 'Miss' })
  })

  it('falls back to "hit" when a successful attack deals no HP change', () => {
    const result = play([
      { type: 'check.resolved', kind: 'attack', target_id: 'enemy:g', success: true },
      { type: 'check.resolved', kind: 'attack', target_id: 'enemy:h', success: false },
    ])
    expect(result.map(item => [item.targetId, item.kind])).toEqual([['enemy:g', 'hit'], ['enemy:h', 'miss']])

    const soaked = play([
      { type: 'check.resolved', kind: 'attack', target_id: 'enemy:g', success: true },
      { type: 'resource.changed', resource: 'hp', target_id: 'enemy:g', delta: 0 },
    ])
    expect(soaked.map(item => item.kind)).toEqual(['hit'])
  })

  it('reports saving throws on the creature that rolled, then its damage and conditions', () => {
    const result = play([
      { type: 'check.resolved', kind: 'saving_throw', actor_id: 'enemy:g', source_actor_id: 'player:a', success: false },
      { type: 'resource.changed', resource: 'hp', target_id: 'enemy:g', delta: -5 },
      { type: 'condition.applied', target_id: 'enemy:g', condition: 'prone' },
    ])
    expect(result.map(item => item.text)).toEqual(['Failed', '-5', 'cond:prone'])
    // Several results on one unit appear one after another, not on top of each other.
    expect(result.map(item => item.delay)).toEqual([0, 0.32, 0.64])
    expect(result.map(item => item.slot)).toEqual([0, 1, 2])
  })

  it('shows healing and blocked healing, and ignores other events and resources', () => {
    const result = play([
      { type: 'intent.submitted' },
      { type: 'resource.changed', resource: 'spell_slot', target_id: 'player:a', delta: -1 },
      { type: 'resource.changed', resource: 'hp', target_id: 'player:a', delta: 7, healing: true },
      { type: 'resource.changed', resource: 'hp', target_id: 'player:b', delta: 0, blocked_by: 'cannot_regain_hp' },
    ])
    expect(result.map(item => [item.kind, item.text])).toEqual([['heal', '+7'], ['blocked', 'Blocked']])
  })

  it('gives every floater of a batch a unique key and a lifetime that covers the last delay', () => {
    const result = floatersFromEvents([
      { type: 'resource.changed', resource: 'hp', target_id: 'enemy:g', delta: -1 },
      { type: 'resource.changed', resource: 'hp', target_id: 'enemy:g', delta: -2 },
    ], labels, 4)
    expect(new Set(result.map(item => item.key)).size).toBe(2)
    expect(result[0]!.key.startsWith('4:')).toBe(true)
    expect(floaterLifetimeMs(result)).toBeGreaterThan(1900)
  })
})
