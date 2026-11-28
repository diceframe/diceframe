import type { CharacterPortrait, RulesetCombatTarget, RulesetGridBoard } from '@/api/types'

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
  /** 角色卡上的头像；战斗本身不带美术，缺省时 HUD 用首字母块。 */
  portrait?: CharacterPortrait | null
  /** Grid footprint in cells; undefined for theater tokens. */
  footprint?: { w: number; h: number }
}

/** Which layout component draws a given server `position_mode`. */
export type BattlefieldLayoutKind = 'line' | 'grid' | 'list'

// `theater` is the single-axis track; `grid` is the square board (workplan
// §6.14). New modes register here; unknown modes degrade to a plain roster
// instead of guessing at coordinates.
const LAYOUT_BY_POSITION_MODE: Record<string, BattlefieldLayoutKind> = {
  theater: 'line',
  grid: 'grid',
}

export function battlefieldLayoutFor(positionMode: string | undefined): BattlefieldLayoutKind {
  return LAYOUT_BY_POSITION_MODE[String(positionMode || 'theater')] ?? 'list'
}

export function buildBattleTokens(
  actors: RulesetCombatTarget[],
  currentActorId: string,
  ownActorId: string,
  portraitFor?: (actor: RulesetCombatTarget) => CharacterPortrait | null,
): BattleToken[] {
  return actors.map((actor) => {
    const maxHp = Math.max(1, Number(actor.max_hp) || 1)
    const hp = Number(actor.hp) || 0
    const dead = Boolean(actor.conditions?.dead)
    const down = hp <= 0 && actor.kind !== 'enemy' && !dead
    // Grid coordinates come from the combat-owned spatial_position; theater
    // keeps the single-axis `position`. The client never merges scene coords.
    const spatial = actor.spatial_position
    const coord: BattleCoord = spatial
      ? { x: Number(spatial.x) || 0, y: Number(spatial.y) || 0 }
      : { x: Number(actor.position || 0) }
    const footprint = spatial?.footprint
      ? { w: Math.max(1, Number(spatial.footprint.w) || 1), h: Math.max(1, Number(spatial.footprint.h) || 1) }
      : undefined
    return {
      id: actor.actor_id,
      name: actor.name,
      side: actor.kind === 'enemy' ? 'enemy' : 'ally',
      hp,
      maxHp: Number(actor.max_hp) || 0,
      hpRatio: Math.max(0, Math.min(1, hp / maxHp)),
      armorClass: actor.armor_class,
      conditions: Object.keys(actor.conditions || {}),
      coord,
      footprint,
      isCurrent: actor.actor_id === currentActorId,
      isDefeated: dead || (hp <= 0 && !down),
      isDying: down,
      isOwn: actor.actor_id === `player:${ownActorId}`,
      portrait: portraitFor ? portraitFor(actor) : null,
    }
  })
}

/**
 * Distance in feet between two coordinates; the single place that knows the
 * metric. Grid coords (both carry `y`) use the D&D 2024 Chebyshev rule — every
 * step, diagonals included, costs one cell — while theater stays single-axis.
 */
export function battleDistance(from: BattleCoord, to: BattleCoord, cellSize = 5): number {
  if (from.y !== undefined && to.y !== undefined) {
    return Math.max(Math.abs(from.x - to.x), Math.abs(from.y - to.y)) * cellSize
  }
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

// ── square-grid layout (workplan §6.14) ─────────────────────────────────────
//
// Pure geometry mirroring the server's `combat/spatial.py`. The reachability
// set is a *client hint only*: the server re-checks budget, occupancy and walls
// on every move intent. Coordinates are cells, never canvas pixels.

export interface GridCell {
  x: number
  y: number
}

export function gridCellKey(x: number, y: number): string {
  return `${x},${y}`
}

interface GridPlacement {
  x: number
  y: number
  w: number
  h: number
}

function placementOf(token: BattleToken): GridPlacement {
  return {
    x: token.coord.x,
    y: token.coord.y ?? 0,
    w: token.footprint?.w ?? 1,
    h: token.footprint?.h ?? 1,
  }
}

function footprintCells(p: GridPlacement): Array<[number, number]> {
  const cells: Array<[number, number]> = []
  for (let dx = 0; dx < p.w; dx += 1) {
    for (let dy = 0; dy < p.h; dy += 1) cells.push([p.x + dx, p.y + dy])
  }
  return cells
}

function anchorOf(p: GridPlacement): [number, number] {
  return [p.x + p.w / 2, p.y + p.h / 2]
}

function orientation(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): number {
  return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
}

function onSegment(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): boolean {
  return Math.min(ax, bx) <= cx && cx <= Math.max(ax, bx)
    && Math.min(ay, by) <= cy && cy <= Math.max(ay, by)
}

function segmentsCross(
  ax: number, ay: number, bx: number, by: number,
  cx: number, cy: number, dx: number, dy: number,
): boolean {
  const o1 = orientation(ax, ay, bx, by, cx, cy)
  const o2 = orientation(ax, ay, bx, by, dx, dy)
  const o3 = orientation(cx, cy, dx, dy, ax, ay)
  const o4 = orientation(cx, cy, dx, dy, bx, by)
  if ((o1 > 0) !== (o2 > 0) && (o3 > 0) !== (o4 > 0)) return true
  if (o1 === 0 && onSegment(ax, ay, bx, by, cx, cy)) return true
  if (o2 === 0 && onSegment(ax, ay, bx, by, dx, dy)) return true
  if (o3 === 0 && onSegment(cx, cy, dx, dy, ax, ay)) return true
  if (o4 === 0 && onSegment(cx, cy, dx, dy, bx, by)) return true
  return false
}

/** Whether the straight anchor-to-anchor segment crosses any blocking wall. */
export function gridWallBlocks(
  walls: RulesetGridBoard['walls'],
  start: GridCell,
  end: GridCell,
  footprint?: { w: number; h: number },
): boolean {
  const w = footprint?.w ?? 1
  const h = footprint?.h ?? 1
  const [ax, ay] = anchorOf({ x: start.x, y: start.y, w, h })
  const [bx, by] = anchorOf({ x: end.x, y: end.y, w, h })
  return (walls || []).some(wall => segmentsCross(ax, ay, bx, by, wall.x0, wall.y0, wall.x1, wall.y1))
}

/**
 * Cells the acting token may move to within its remaining budget, avoiding
 * occupied footprints and walls. Hint only — the server stays authoritative.
 */
export function gridReachableCells(
  board: RulesetGridBoard,
  tokens: BattleToken[],
  actorId: string,
  movementRemaining: number,
): Set<string> {
  const reachable = new Set<string>()
  const mover = tokens.find(token => token.id === actorId)
  if (!mover || mover.coord.y === undefined) return reachable
  const start = placementOf(mover)
  const cellSize = Math.max(1, Number(board.cell_size) || 5)
  const budgetCells = Math.floor(Math.max(0, movementRemaining) / cellSize)
  if (budgetCells <= 0) return reachable
  const occupied = new Set<string>()
  for (const token of tokens) {
    if (token.id === actorId || token.coord.y === undefined) continue
    for (const [cx, cy] of footprintCells(placementOf(token))) occupied.add(gridCellKey(cx, cy))
  }
  const walls = board.walls || []
  for (let x = 0; x + start.w <= board.width; x += 1) {
    for (let y = 0; y + start.h <= board.height; y += 1) {
      if (x === start.x && y === start.y) continue
      if (Math.max(Math.abs(x - start.x), Math.abs(y - start.y)) > budgetCells) continue
      const target: GridPlacement = { x, y, w: start.w, h: start.h }
      if (footprintCells(target).some(([cx, cy]) => occupied.has(gridCellKey(cx, cy)))) continue
      if (gridWallBlocks(walls, start, target, { w: start.w, h: start.h })) continue
      reachable.add(gridCellKey(x, y))
    }
  }
  return reachable
}
