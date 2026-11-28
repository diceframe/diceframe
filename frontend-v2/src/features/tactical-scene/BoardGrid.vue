<script setup lang="ts">
import { computed } from 'vue'
import type { TacticalSceneGrid } from '@/api/tacticalScene'

const props = defineProps<{
  grid: TacticalSceneGrid
  rect: { x: number; y: number; width: number; height: number }
  origin: { x: number; y: number }
}>()

// Safety cap for extreme scenes: keep a aligned subset of the true grid lines
// instead of mounting tens of thousands of SVG nodes.
const MAX_LINES_PER_AXIS = 800

interface GridLine { x1: number; y1: number; x2: number; y2: number }

const lines = computed<GridLine[]>(() => {
  const cell = props.grid.size
  if (props.grid.type !== 'square' || !(cell > 0)) return []
  const { x, y, width, height } = props.rect
  if (!(width > 0) || !(height > 0)) return []
  const out: GridLine[] = []
  const span = (lo: number, hi: number, origin: number) => {
    const first = Math.floor((lo - origin) / cell)
    const last = Math.ceil((hi - origin) / cell)
    const step = Math.max(1, Math.ceil((last - first + 1) / MAX_LINES_PER_AXIS))
    const marks: number[] = []
    for (let k = first; k <= last; k += step) marks.push(origin + k * cell)
    return marks
  }
  for (const gx of span(x, x + width, props.origin.x)) {
    out.push({ x1: gx, y1: y, x2: gx, y2: y + height })
  }
  for (const gy of span(y, y + height, props.origin.y)) {
    out.push({ x1: x, y1: gy, x2: x + width, y2: gy })
  }
  return out
})

const stroke = computed(() => props.grid.color || 'currentColor')
// alpha 0 hides lines in the source; the read-only board still shows a faint
// square grid so the cell layout stays perceivable.
const opacity = computed(() => (props.grid.alpha > 0 ? props.grid.alpha : 0.15))
</script>

<template>
  <g data-testid="board-grid" class="board-grid" :stroke="stroke" :stroke-opacity="opacity">
    <line
      v-for="(line, index) in lines"
      :key="index"
      :x1="line.x1"
      :y1="line.y1"
      :x2="line.x2"
      :y2="line.y2"
      vector-effect="non-scaling-stroke"
    />
  </g>
</template>