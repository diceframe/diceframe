import type { JsonObject } from '@/api/types'

export type FloaterKind = 'damage' | 'crit' | 'heal' | 'miss' | 'hit' | 'save' | 'fail' | 'status' | 'blocked'

/** One short-lived number or word that rises above a unit after a resolution. */
export interface Floater {
  key: string
  targetId: string
  kind: FloaterKind
  text: string
  /** Seconds before it appears, so several results on one unit read in order. */
  delay: number
  /** Position among this unit's results; later ones start lower so they don't overlap. */
  slot: number
}

export interface FeedbackLabels {
  miss: string
  hit: string
  saveOk: string
  saveFail: string
  blocked: string
  condition: (key: string) => string
}

const STEP = 0.32
const ATTACK_KINDS = new Set(['attack', 'spell_attack'])

/**
 * Turns the server's resolved events into floaters. Only reads fields the
 * ruleset already emits; unknown event types are ignored.
 */
export function floatersFromEvents(
  events: JsonObject[], labels: FeedbackLabels, batch: number,
): Floater[] {
  const out: Floater[] = []
  const perTarget = new Map<string, number>()
  const add = (targetId: string, kind: FloaterKind, text: string): void => {
    if (!targetId) return
    const seen = perTarget.get(targetId) ?? 0
    perTarget.set(targetId, seen + 1)
    out.push({ key: `${batch}:${out.length}`, targetId, kind, text, delay: seen * STEP, slot: seen })
  }

  // A hit is announced by the damage that follows; only say "hit" when none does.
  let pendingHit = ''
  const flushHit = (): void => {
    if (pendingHit) add(pendingHit, 'hit', labels.hit)
    pendingHit = ''
  }

  for (const event of events) {
    const type = String(event.type || '')
    if (type === 'check.resolved') {
      flushHit()
      const kind = String(event.kind || '')
      if (ATTACK_KINDS.has(kind)) {
        const target = String(event.target_id || '')
        if (event.success) pendingHit = target
        else add(target, 'miss', labels.miss)
      } else if (kind === 'saving_throw') {
        add(String(event.actor_id || ''), event.success ? 'save' : 'fail',
          event.success ? labels.saveOk : labels.saveFail)
      }
    } else if (type === 'resource.changed' && event.resource === 'hp') {
      const target = String(event.target_id || '')
      const delta = Number(event.delta || 0)
      // A hit fully soaked (0 damage) still reads as "hit" through flushHit().
      if (target === pendingHit && (delta || event.blocked_by)) pendingHit = ''
      if (delta < 0) add(target, event.critical ? 'crit' : 'damage', String(delta))
      else if (delta > 0) add(target, 'heal', `+${delta}`)
      else if (event.blocked_by) add(target, 'blocked', labels.blocked)
    } else if (type === 'condition.applied') {
      add(String(event.target_id || ''), 'status', labels.condition(String(event.condition || '')))
    }
  }
  flushHit()
  return out
}

/** Longest a batch needs on screen: last delay plus the animation itself. */
export function floaterLifetimeMs(floaters: Floater[]): number {
  const last = floaters.reduce((max, item) => Math.max(max, item.delay), 0)
  return Math.round((last + 1.6) * 1000)
}
