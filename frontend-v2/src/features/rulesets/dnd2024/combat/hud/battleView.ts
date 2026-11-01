import type { RulesetCombatTarget } from '@/api/types'

/**
 * Presentation model for the combat HUD.
 *
 * The HUD never reads `actor.position` directly: every layout goes through
 * `BattleCoord`, so adding an axis (y, height, zones…) means adding a layout
 * and a distance rule here, not touching the panel.
 */
export interface BattleCoord {
  x: number
  y?: number
}

export type BattleSide = 'ally' | 'enemy'

export interface BattleToken {
  id: string
  name: string
  side: BattleSide
  hp: number
  maxHp: number
  hpRatio: number
  armorClass?: number
  conditions: string[]
  coord: BattleCoord
  isCurrent: boolean
  /** Out of the fight: an enemy at 0 HP, or anyone who is dead. */
  isDefeated: boolean
  /** A party member at 0 HP who is still making death saves (or is stable). */
  isDying: boolean
  isOwn: boolean
}

/** Which layout component draws a given server `position_mode`. */
export type BattlefieldLayoutKind = 'line' | 'list'

// `theater` is the only mode the server emits today: one axis, 5 ft cells.
// New modes register here; unknown modes degrade to a plain roster instead of
// guessing at coordinates.
const LAYOUT_BY_POSITION_MODE: Record<string, BattlefieldLayoutKind> = {
  theater: 'line',
}

export function battlefieldLayoutFor(positionMode: string | undefined): BattlefieldLayoutKind {
  return LAYOUT_BY_POSITION_MODE[String(positionMode || 'theater')] ?? 'list'
}

export function buildBattleTokens(
  actors: RulesetCombatTarget[],
  currentActorId: string,
  ownActorId: string,
): BattleToken[] {
  return actors.map((actor) => {
    const maxHp = Math.max(1, Number(actor.max_hp) || 1)
    const hp = Number(actor.hp) || 0
    const dead = Boolean(actor.conditions?.dead)
    const down = hp <= 0 && actor.kind !== 'enemy' && !dead
    return {
      id: actor.actor_id,
      name: actor.name,
      side: actor.kind === 'enemy' ? 'enemy' : 'ally',
      hp,
      maxHp: Number(actor.max_hp) || 0,
      hpRatio: Math.max(0, Math.min(1, hp / maxHp)),
      armorClass: actor.armor_class,
      conditions: Object.keys(actor.conditions || {}),
      coord: { x: Number(actor.position || 0) },
      isCurrent: actor.actor_id === currentActorId,
      isDefeated: dead || (hp <= 0 && !down),
      isDying: down,
      isOwn: actor.actor_id === `player:${ownActorId}`,
    }
  })
}

/** Distance in feet between two coordinates; the single place that knows the metric. */
export function battleDistance(from: BattleCoord, to: BattleCoord): number {
  return Math.abs(from.x - to.x)
}

// ── single-axis layout ──────────────────────────────────────────────────────

export const LINE_STEP = 5
const LINE_MIN_SPAN = 30

export interface LineExtent {
  min: number
  max: number
  ticks: number[]
}

/** Visible span: every actor, the default 30 ft stage, and one full move either way. */
export function lineExtent(tokens: BattleToken[], movementRemaining: number): LineExtent {
  const xs = tokens.length ? tokens.map(token => token.coord.x) : [0]
  const min = Math.floor((Math.min(0, ...xs) - movementRemaining) / LINE_STEP) * LINE_STEP
  const max = Math.ceil((Math.max(LINE_MIN_SPAN, ...xs) + movementRemaining) / LINE_STEP) * LINE_STEP
  const count = Math.max(1, (max - min) / LINE_STEP + 1)
  return { min, max, ticks: Array.from({ length: count }, (_, index) => min + index * LINE_STEP) }
}

export interface LineSlot {
  /** 0-based cell index along the axis. */
  index: number
  /** Stacking row; units closer than `minGap` cells never share a row. */
  lane: number
}

/**
 * Cards are wider than one 5 ft cell, so besides the same-cell case we also
 * push neighbours onto another row until they no longer overlap.
 */
export function lineSlots(tokens: BattleToken[], extent: LineExtent, minGap = 2): Map<string, LineSlot> {
  const slots = new Map<string, LineSlot>()
  const lanes: number[][] = []
  for (const token of tokens) {
    const index = Math.max(0, Math.min(
      extent.ticks.length - 1,
      Math.round((token.coord.x - extent.min) / LINE_STEP),
    ))
    let lane = lanes.findIndex(row => row.every(other => Math.abs(other - index) >= minGap))
    if (lane < 0) {
      lanes.push([])
      lane = lanes.length - 1
    }
    lanes[lane]!.push(index)
    slots.set(token.id, { index, lane })
  }
  return slots
}

export function lineLaneCount(slots: Map<string, LineSlot>): number {
  let lanes = 1
  for (const slot of slots.values()) lanes = Math.max(lanes, slot.lane + 1)
  return lanes
}
