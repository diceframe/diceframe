<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { TacticalSceneProjection } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalSceneCopy } from './copy'
import BoardBackground from './BoardBackground.vue'
import BoardGrid from './BoardGrid.vue'
import BoardLights from './BoardLights.vue'
import BoardTokens from './BoardTokens.vue'
import BoardWalls from './BoardWalls.vue'

const props = defineProps<{
  scene: TacticalSceneProjection
  gameKey?: string
  selectedTokenId?: string | null
}>()

const emit = defineEmits<{
  'select-token': [id: string]
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalSceneCopy(locale.value))

const sceneName = computed(() => props.scene.display_name || props.scene.canonical_id)
const grid = computed(() => props.scene.grid)

// Renderer-layer geometry (workplan §0 decision 4): stored coordinates are
// canvas pixels. The core rect sits at (offset_x, offset_y) with width×height;
// padding extends it proportionally on every side and the background covers
// that padded rect. Grid lines stay aligned to the core rect origin.
const sceneRect = computed(() => {
  const d = props.scene.dimensions
  const padX = d.width * d.padding
  const padY = d.height * d.padding
  return {
    x: d.offset_x - padX,
    y: d.offset_y - padY,
    width: d.width + padX * 2,
    height: d.height + padY * 2,
  }
})
const gridOrigin = computed(() => ({ x: props.scene.dimensions.offset_x, y: props.scene.dimensions.offset_y }))

// Display-only nominal cell for scenes that declare no grid size (gridless
// with size 0); token sizes are stored in cells, so they need some pixel size.
const FALLBACK_CELL_SIZE = 100
const cellSize = computed(() => (grid.value.size > 0 ? grid.value.size : FALLBACK_CELL_SIZE))

// ---- view state: zoom is screen pixels per world pixel; center is the world
// point at the viewport middle (same viewBox approach as MapGraph.vue) ----
const MIN_ZOOM = 0.01
const MAX_ZOOM = 16
const zoom = ref(1)
const centerX = ref(0)
const centerY = ref(0)
const svgEl = ref<SVGSVGElement | null>(null)
const viewport = ref({ width: 0, height: 0 })

function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value))
}

function measure() {
  const rect = svgEl.value?.getBoundingClientRect()
  if (rect?.width && rect.height) viewport.value = { width: rect.width, height: rect.height }
}

const viewBox = computed(() => {
  const r = sceneRect.value
  const width = viewport.value.width || r.width || 1000
  const height = viewport.value.height || r.height || 700
  const w = width / zoom.value
  const h = height / zoom.value
  return `${centerX.value - w / 2} ${centerY.value - h / 2} ${w} ${h}`
})

function fitView() {
  const r = sceneRect.value
  if (!(r.width > 0) || !(r.height > 0)) {
    zoom.value = 1
    centerX.value = 0
    centerY.value = 0
    return
  }
  const width = viewport.value.width || r.width
  const height = viewport.value.height || r.height
  zoom.value = clampZoom(Math.min(width / (r.width * 1.04), height / (r.height * 1.04)))
  centerX.value = r.x + r.width / 2
  centerY.value = r.y + r.height / 2
}

// Keep the world point under the given screen position fixed while zooming.
function zoomAt(clientX: number, clientY: number, factor: number) {
  const next = clampZoom(zoom.value * factor)
  if (next === zoom.value) return
  const rect = svgEl.value?.getBoundingClientRect()
  if (!rect?.width || !rect.height) { zoom.value = next; return }
  const localX = clientX - rect.left - rect.width / 2
  const localY = clientY - rect.top - rect.height / 2
  const worldX = centerX.value + localX / zoom.value
  const worldY = centerY.value + localY / zoom.value
  zoom.value = next
  centerX.value = worldX - localX / next
  centerY.value = worldY - localY / next
}

function zoomBy(factor: number) {
  const rect = svgEl.value?.getBoundingClientRect()
  if (rect?.width && rect.height) zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor)
  else zoom.value = clampZoom(zoom.value * factor)
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.002))
}

// ---- pan / pinch via pointer events ----
const pointers = new Map<number, { x: number; y: number }>()
let pinchDistance = 0

function pointerDistance(): number {
  const [a, b] = [...pointers.values()]
  return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0
}

function onPointerDown(e: PointerEvent) {
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  try {
    ;(e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId)
  } catch {
    // Synthetic events or missing pointer capture: panning still works.
  }
  if (pointers.size === 2) pinchDistance = pointerDistance()
}

function onPointerMove(e: PointerEvent) {
  const prev = pointers.get(e.pointerId)
  if (!prev) return
  const dx = e.clientX - prev.x
  const dy = e.clientY - prev.y
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  if (pointers.size === 1) {
    centerX.value -= dx / zoom.value
    centerY.value -= dy / zoom.value
    return
  }
  if (pointers.size === 2) {
    const nextDistance = pointerDistance()
    if (!(pinchDistance > 0) || !(nextDistance > 0)) { pinchDistance = nextDistance; return }
    const factor = nextDistance / pinchDistance
    pinchDistance = nextDistance
    const [a, b] = [...pointers.values()]
    if (a && b) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, factor)
  }
}

function onPointerUp(e: PointerEvent) {
  pointers.delete(e.pointerId)
  pinchDistance = pointers.size === 2 ? pointerDistance() : 0
}

watch(() => props.scene.canonical_id, () => { fitView() })

onMounted(() => {
  measure()
  fitView()
  svgEl.value?.addEventListener('wheel', onWheel, { passive: false })
  window.addEventListener('resize', measure)
})
onBeforeUnmount(() => {
  svgEl.value?.removeEventListener('wheel', onWheel)
  window.removeEventListener('resize', measure)
})
</script>

<template>
  <section class="tactical-board" data-testid="tactical-board">
    <header class="tactical-board-toolbar">
      <h2>{{ sceneName }}</h2>
      <span v-if="grid.type === 'hex'" class="tactical-board-hint" data-testid="board-hex-hint">{{ copy.hexHint }}</span>
      <div class="tactical-board-controls">
        <button type="button" :aria-label="copy.zoomOut" :title="copy.zoomOut" data-testid="board-zoom-out" @click="zoomBy(0.8)">−</button>
        <button type="button" :aria-label="copy.zoomIn" :title="copy.zoomIn" data-testid="board-zoom-in" @click="zoomBy(1.25)">+</button>
        <button type="button" data-testid="board-reset-view" @click="fitView()">{{ copy.resetView }}</button>
      </div>
    </header>
    <div class="tactical-board-viewport">
      <svg
        ref="svgEl"
        :viewBox="viewBox"
        class="tactical-board-svg"
        role="img"
        :aria-label="`${copy.boardLabel}: ${sceneName}`"
        preserveAspectRatio="xMidYMid meet"
        data-testid="tactical-board-svg"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      >
        <BoardBackground :background="scene.background" :rect="sceneRect" :game-key="gameKey" />
        <BoardGrid :grid="grid" :rect="sceneRect" :origin="gridOrigin" />
        <BoardLights :lights="scene.placeables.lights" />
        <BoardWalls :walls="scene.placeables.walls" />
        <BoardTokens :tokens="scene.placeables.tokens" :cell-size="cellSize" :selected-id="selectedTokenId" @select="emit('select-token', $event)" />
      </svg>
    </div>
  </section>
</template>