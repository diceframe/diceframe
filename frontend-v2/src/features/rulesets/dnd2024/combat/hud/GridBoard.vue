<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RulesetGridBoard } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { gridCellKey, type BattleToken, type GridCell } from './battleView'
import type { Floater } from './feedback'
import type { BattlefieldLabels } from './labels'

/**
 * Square-grid battlefield (workplan §6.14 / ROAD PR-5). Renders the server's
 * board snapshot and combat-owned cells; drag/click only emit a move intent
 * target cell — the server re-checks budget, occupancy and walls. The client
 * never writes coordinates and never merges scene projections.
 */
const props = defineProps<{
  board: RulesetGridBoard
  tokens: BattleToken[]
  movementRemaining: number
  targetId: string
  selectableIds: string[]
  selectedCell: GridCell | null
  reachable: Set<string>
  canMove: boolean
  labels: BattlefieldLabels
  floaters?: Floater[]
}>()
const emit = defineEmits<{ selectToken: [id: string]; chooseCell: [cell: GridCell] }>()

const { t } = useLocale()

const CELL = 40
const width = computed(() => props.board.width * CELL)
const height = computed(() => props.board.height * CELL)
const viewBox = computed(() => `0 0 ${width.value} ${height.value}`)
const frameStyle = computed(() => ({ aspectRatio: `${width.value} / ${height.value}` }))

const gridPath = computed(() => {
  const parts: string[] = []
  for (let x = 0; x <= props.board.width; x += 1) parts.push(`M${x * CELL} 0V${height.value}`)
  for (let y = 0; y <= props.board.height; y += 1) parts.push(`M0 ${y * CELL}H${width.value}`)
  return parts.join('')
})

const reachableCells = computed(() => Array.from(props.reachable).map((key) => {
  const [x, y] = key.split(',').map(Number)
  return { key, x: x!, y: y! }
}))

const walls = computed(() => props.board.walls || [])

function tokenHue(token: BattleToken): number {
  let hash = 0
  for (const char of token.name) hash = (hash * 31 + char.codePointAt(0)!) % 360
  const hue = token.side === 'enemy' ? 350 + (hash % 24) - 12 : 190 + (hash % 80)
  return ((hue % 360) + 360) % 360
}

function tokenBox(token: BattleToken) {
  const w = (token.footprint?.w ?? 1) * CELL
  const h = (token.footprint?.h ?? 1) * CELL
  const x = token.coord.x * CELL
  const y = (token.coord.y ?? 0) * CELL
  return { x, y, w, h, cx: x + w / 2, cy: y + h / 2, r: Math.min(w, h) * 0.36 }
}

function initials(name: string): string {
  return name.trim().slice(0, 1).toUpperCase() || '?'
}

function floatersFor(id: string): Floater[] {
  return (props.floaters || []).filter(item => item.targetId === id)
}

function cellLabel(cell: GridCell): string {
  return t('dndGridCell', { x: cell.x, y: cell.y })
}

// ---- pointer interaction: tap selects a target, drag moves the acting token ----
const svgEl = ref<SVGSVGElement | null>(null)
interface DragState {
  tokenId: string
  startX: number
  startY: number
  draggable: boolean
  active: boolean
  cell: GridCell | null
}
const drag = ref<DragState | null>(null)
let lastDragEnd = 0

function cellFromEvent(e: { clientX: number; clientY: number }): GridCell | null {
  const svg = svgEl.value
  if (!svg) return null
  const rect = svg.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  const x = Math.floor((e.clientX - rect.left) / rect.width * props.board.width)
  const y = Math.floor((e.clientY - rect.top) / rect.height * props.board.height)
  if (x < 0 || y < 0 || x >= props.board.width || y >= props.board.height) return null
  return { x, y }
}

function onTokenPointerDown(e: PointerEvent, token: BattleToken): void {
  drag.value = {
    tokenId: token.id,
    startX: e.clientX,
    startY: e.clientY,
    draggable: props.canMove && token.isCurrent,
    active: false,
    cell: null,
  }
  try {
    svgEl.value?.setPointerCapture(e.pointerId)
  } catch {
    // Synthetic events may not support capture; tap/drag still resolve on up.
  }
}

function onPointerMove(e: PointerEvent): void {
  const state = drag.value
  if (!state || !state.draggable) return
  const moved = Math.hypot(e.clientX - state.startX, e.clientY - state.startY) > 6
  drag.value = { ...state, active: state.active || moved, cell: cellFromEvent(e) }
}

function onPointerUp(): void {
  const state = drag.value
  if (!state) return
  drag.value = null
  if (state.active && state.draggable) {
    lastDragEnd = Date.now()
    if (state.cell && props.reachable.has(gridCellKey(state.cell.x, state.cell.y))) {
      emit('chooseCell', state.cell)
      return
    }
  }
  emit('selectToken', state.tokenId)
}

function onCellClick(cell: GridCell): void {
  // A drag that ended over a cell already emitted; swallow the trailing click.
  if (Date.now() - lastDragEnd < 250) return
  if (!props.canMove || !props.reachable.has(gridCellKey(cell.x, cell.y))) return
  emit('chooseCell', { x: cell.x, y: cell.y })
}

const ghost = computed(() => {
  const state = drag.value
  if (!state?.active || !state.cell) return null
  const mover = props.tokens.find(token => token.id === state.tokenId)
  if (!mover) return null
  const box = tokenBox({ ...mover, coord: { x: state.cell.x, y: state.cell.y } })
  return { ...box, hue: tokenHue(mover), name: mover.name }
})
</script>

<template>
  <div class="grid-board" data-testid="grid-board">
    <div class="grid-board-frame" :style="frameStyle">
      <svg
        ref="svgEl"
        :viewBox="viewBox"
        class="grid-board-svg"
        role="img"
        :aria-label="t('dndGridBoard')"
        preserveAspectRatio="xMidYMid meet"
        data-testid="grid-board-svg"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      >
        <rect class="grid-bg" :width="width" :height="height" />
        <path class="grid-lines" :d="gridPath" />

        <rect
          v-for="cell in reachableCells"
          :key="`reach-${cell.key}`"
          class="grid-cell-reachable"
          :class="{ selected: selectedCell && selectedCell.x === cell.x && selectedCell.y === cell.y }"
          :x="cell.x * CELL"
          :y="cell.y * CELL"
          :width="CELL"
          :height="CELL"
          :data-cell="cell.key"
          :role="canMove ? 'button' : undefined"
          :tabindex="canMove ? 0 : undefined"
          :aria-label="cellLabel(cell)"
          @click="onCellClick(cell)"
          @keydown.enter="onCellClick(cell)"
        />

        <rect
          v-if="selectedCell"
          class="grid-cell-selected"
          :x="selectedCell.x * CELL"
          :y="selectedCell.y * CELL"
          :width="CELL"
          :height="CELL"
        />

        <line
          v-for="(wall, index) in walls"
          :key="`wall-${index}`"
          class="grid-wall"
          :x1="wall.x0 * CELL"
          :y1="wall.y0 * CELL"
          :x2="wall.x1 * CELL"
          :y2="wall.y1 * CELL"
        />

        <g v-if="ghost" class="grid-ghost" :style="{ '--ghost-hue': ghost.hue }">
          <circle :cx="ghost.cx" :cy="ghost.cy" :r="ghost.r" />
          <text :x="ghost.cx" :y="ghost.cy">{{ initials(ghost.name) }}</text>
        </g>

        <g
          v-for="token in tokens"
          :key="token.id"
          class="grid-token"
          :class="{ current: token.isCurrent, targeted: token.id === targetId, defeated: token.isDefeated, own: token.isOwn }"
          :style="{ '--token-hue': tokenHue(token) }"
          :data-token="token.id"
          role="button"
          :tabindex="selectableIds.includes(token.id) ? 0 : -1"
          :aria-label="labels.selectTarget(token.name)"
          @pointerdown="onTokenPointerDown($event, token)"
          @keydown.enter="emit('selectToken', token.id)"
        >
          <title>{{ token.name }} · {{ labels.hp }} {{ token.hp }}/{{ token.maxHp }}</title>
          <circle v-if="token.isCurrent" class="grid-token-current-ring" :cx="tokenBox(token).cx" :cy="tokenBox(token).cy" :r="tokenBox(token).r + 5" />
          <circle v-if="token.id === targetId" class="grid-token-target-ring" :cx="tokenBox(token).cx" :cy="tokenBox(token).cy" :r="tokenBox(token).r + 3" />
          <circle class="grid-token-body" :cx="tokenBox(token).cx" :cy="tokenBox(token).cy" :r="tokenBox(token).r" />
          <text class="grid-token-initial" :x="tokenBox(token).cx" :y="tokenBox(token).cy">{{ initials(token.name) }}</text>
          <rect
            class="grid-token-hp"
            :x="tokenBox(token).cx - tokenBox(token).r"
            :y="tokenBox(token).cy + tokenBox(token).r + 2"
            :width="tokenBox(token).r * 2 * token.hpRatio"
            :height="3"
          />
          <text
            v-for="floater in floatersFor(token.id)"
            :key="floater.key"
            class="grid-floater"
            :class="`kind-${floater.kind}`"
            :x="tokenBox(token).cx"
            :y="tokenBox(token).cy - tokenBox(token).r - 4 - floater.slot * 12"
          >{{ floater.text }}</text>
        </g>
      </svg>
    </div>
    <p class="grid-board-hint">{{ t('dndGridMoveHint') }}</p>
  </div>
</template>

<style scoped>
.grid-board { position: relative; border: 1px solid rgb(90 110 125 / 55%); border-radius: 16px; overflow: hidden; background: linear-gradient(180deg, #131c26, #0b1118); box-shadow: inset 0 0 60px rgb(0 0 0 / 55%); }
.grid-board-frame { position: relative; width: 100%; max-height: 60vh; }
.grid-board-svg { display: block; width: 100%; height: 100%; touch-action: none; }
.grid-bg { fill: rgb(255 255 255 / 2%); }
.grid-lines { stroke: rgb(255 255 255 / 8%); stroke-width: 1; fill: none; }
.grid-cell-reachable { fill: rgb(228 183 94 / 22%); cursor: pointer; }
.grid-cell-reachable:hover { fill: rgb(228 183 94 / 42%); }
.grid-cell-reachable.selected { fill: rgb(228 183 94 / 55%); }
.grid-cell-selected { fill: none; stroke: #e4b75e; stroke-width: 2; pointer-events: none; }
.grid-wall { stroke: #c9a24b; stroke-width: 3; stroke-linecap: round; }
.grid-token { cursor: pointer; }
.grid-token-body { fill: hsl(var(--token-hue) 45% 34%); stroke: hsl(var(--token-hue) 55% 62%); stroke-width: 2; }
.grid-token-initial { fill: hsl(var(--token-hue) 90% 92%); font-size: 15px; font-weight: 800; text-anchor: middle; dominant-baseline: central; pointer-events: none; }
.grid-token-hp { fill: #6fce7f; }
.grid-token.defeated { opacity: .4; }
.grid-token-current-ring { fill: none; stroke: #e4b75e; stroke-width: 2; stroke-dasharray: 4 3; }
.grid-token-target-ring { fill: none; stroke: #ff8a8a; stroke-width: 2; }
.grid-token.own .grid-token-body { stroke-width: 3; }
.grid-ghost { opacity: .55; pointer-events: none; }
.grid-ghost circle { fill: hsl(var(--ghost-hue) 45% 34%); stroke: hsl(var(--ghost-hue) 55% 62%); stroke-width: 2; }
.grid-ghost text { fill: hsl(var(--ghost-hue) 90% 92%); font-size: 15px; font-weight: 800; text-anchor: middle; dominant-baseline: central; }
.grid-floater { font-size: 12px; font-weight: 700; text-anchor: middle; fill: #ffd6d6; pointer-events: none; }
.grid-floater.kind-heal { fill: #9fe6a8; }
.grid-floater.kind-miss { fill: #9fb4c4; }
.grid-board-hint { margin: 0; padding: 8px 12px; color: #8c9aa5; font-size: 11px; border-top: 1px solid rgb(255 255 255 / 8%); }
@media (prefers-reduced-motion: reduce) { .grid-token, .grid-cell-reachable { transition: none; } }
</style>