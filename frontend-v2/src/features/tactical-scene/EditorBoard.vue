<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type {
  TacticalSceneDocument,
  TacticalSceneTokenDocument,
  TacticalSceneWallEndpoints,
} from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalSceneCopy } from './copy'
import { samePlaceableRef, type EditorTool, type PlaceableRef } from './useSceneEditor'
import BoardBackground from './BoardBackground.vue'
import BoardGrid from './BoardGrid.vue'

const props = defineProps<{
  scene: TacticalSceneDocument
  gameKey?: string
  tool: EditorTool
  selection: PlaceableRef[]
}>()

const emit = defineEmits<{
  select: [ref: PlaceableRef, additive: boolean]
  'select-many': [refs: PlaceableRef[], additive: boolean]
  'clear-selection': []
  'place-token': [point: { x: number; y: number }]
  'draw-wall': [endpoints: TacticalSceneWallEndpoints]
  'gesture-begin': []
  'gesture-move': [refs: PlaceableRef[], dx: number, dy: number]
  'gesture-resize': [ref: PlaceableRef, width: number, height: number]
  'gesture-rotate': [ref: PlaceableRef, rotation: number]
}>()

// Stored coordinates are canvas pixels; token size is in grid cells (the
// adapter keeps FVTT width/height as cells, the read-only board multiplies by
// the cell size). The editor follows the same convention.
const { locale } = useLocale()
const copy = computed(() => tacticalSceneCopy(locale.value))
const FALLBACK_CELL_SIZE = 100
const cellSize = computed(() => (props.scene.grid.size > 0 ? props.scene.grid.size : FALLBACK_CELL_SIZE))

const sceneRect = computed(() => {
  const d = props.scene.dimensions
  const padX = d.width * d.padding
  const padY = d.height * d.padding
  return { x: d.offset_x - padX, y: d.offset_y - padY, width: d.width + padX * 2, height: d.height + padY * 2 }
})
const gridOrigin = computed(() => ({ x: props.scene.dimensions.offset_x, y: props.scene.dimensions.offset_y }))

// ---- view state (same viewBox approach as Board.vue / MapGraph.vue) --------
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

function toWorld(clientX: number, clientY: number): { x: number; y: number } {
  const rect = svgEl.value?.getBoundingClientRect()
  if (!rect?.width || !rect.height) return { x: centerX.value, y: centerY.value }
  return {
    x: centerX.value + (clientX - rect.left - rect.width / 2) / zoom.value,
    y: centerY.value + (clientY - rect.top - rect.height / 2) / zoom.value,
  }
}

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

// ---- pointer gestures ------------------------------------------------------
type Drag =
  | { kind: 'pan'; lastX: number; lastY: number }
  | { kind: 'move'; refs: PlaceableRef[]; lastX: number; lastY: number; started: boolean }
  | { kind: 'marquee'; startX: number; startY: number; currentX: number; currentY: number; additive: boolean }
  | { kind: 'wall'; startX: number; startY: number; currentX: number; currentY: number }
  | { kind: 'token'; startX: number; startY: number; moved: boolean }
  | { kind: 'resize'; ref: PlaceableRef; started: boolean }
  | { kind: 'rotate'; ref: PlaceableRef; started: boolean }

const pointers = new Map<number, { x: number; y: number }>()
let pinchDistance = 0
let drag: Drag | null = null
const marquee = ref<{ x: number; y: number; width: number; height: number } | null>(null)
const wallPreview = ref<TacticalSceneWallEndpoints | null>(null)
const DRAG_THRESHOLD_PX = 3
const MIN_WALL_LENGTH = 2

function pointerDistance(): number {
  const [a, b] = [...pointers.values()]
  return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0
}

function registerPointer(e: PointerEvent) {
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  try {
    svgEl.value?.setPointerCapture(e.pointerId)
  } catch {
    // Synthetic events or missing pointer capture: gestures still work.
  }
  if (pointers.size === 2) {
    pinchDistance = pointerDistance()
    drag = null
    marquee.value = null
    wallPreview.value = null
  }
}

function isSelected(ref: PlaceableRef): boolean {
  return props.selection.some(item => samePlaceableRef(item, ref))
}

function tokenAt(ref: PlaceableRef): TacticalSceneTokenDocument | null {
  if (ref.collection !== 'tokens') return null
  return props.scene.placeables.tokens[ref.id] ?? null
}

function onItemPointerDown(ref: PlaceableRef, e: PointerEvent) {
  if (props.tool !== 'select') return
  e.stopPropagation()
  registerPointer(e)
  if (pointers.size > 1) return
  if (e.shiftKey) {
    emit('select', ref, true)
    return
  }
  const refs = isSelected(ref) ? props.selection : [ref]
  if (!isSelected(ref)) emit('select', ref, false)
  const movable = refs.some(item => {
    const token = tokenAt(item)
    return !(item.collection === 'tokens' && token?.locked)
  })
  if (!movable) return
  drag = { kind: 'move', refs, lastX: e.clientX, lastY: e.clientY, started: false }
}

function onHandlePointerDown(kind: 'resize' | 'rotate', ref: PlaceableRef, e: PointerEvent) {
  e.stopPropagation()
  registerPointer(e)
  if (pointers.size > 1) return
  drag = { kind, ref, started: false }
}

function onPointerDown(e: PointerEvent) {
  registerPointer(e)
  if (pointers.size > 1) return
  const world = toWorld(e.clientX, e.clientY)
  if (props.tool === 'pan') {
    drag = { kind: 'pan', lastX: e.clientX, lastY: e.clientY }
  } else if (props.tool === 'select') {
    drag = { kind: 'marquee', startX: world.x, startY: world.y, currentX: world.x, currentY: world.y, additive: e.shiftKey }
    marquee.value = { x: world.x, y: world.y, width: 0, height: 0 }
  } else if (props.tool === 'token') {
    drag = { kind: 'token', startX: e.clientX, startY: e.clientY, moved: false }
  } else if (props.tool === 'wall') {
    drag = { kind: 'wall', startX: world.x, startY: world.y, currentX: world.x, currentY: world.y }
    wallPreview.value = { x0: world.x, y0: world.y, x1: world.x, y1: world.y }
  }
}

function onPointerMove(e: PointerEvent) {
  const prev = pointers.get(e.pointerId)
  if (!prev) return
  const dxScreen = e.clientX - prev.x
  const dyScreen = e.clientY - prev.y
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  if (pointers.size === 2) {
    const nextDistance = pointerDistance()
    if (!(pinchDistance > 0) || !(nextDistance > 0)) { pinchDistance = nextDistance; return }
    const factor = nextDistance / pinchDistance
    pinchDistance = nextDistance
    const [a, b] = [...pointers.values()]
    if (a && b) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, factor)
    return
  }
  if (!drag) return
  const world = toWorld(e.clientX, e.clientY)
  if (drag.kind === 'pan') {
    centerX.value -= dxScreen / zoom.value
    centerY.value -= dyScreen / zoom.value
    drag.lastX = e.clientX
    drag.lastY = e.clientY
    return
  }
  if (drag.kind === 'move') {
    if (!drag.started) {
      if (Math.hypot(e.clientX - drag.lastX, e.clientY - drag.lastY) < DRAG_THRESHOLD_PX) return
      drag.started = true
      emit('gesture-begin')
    }
    emit('gesture-move', drag.refs, (e.clientX - drag.lastX) / zoom.value, (e.clientY - drag.lastY) / zoom.value)
    drag.lastX = e.clientX
    drag.lastY = e.clientY
    return
  }
  if (drag.kind === 'marquee') {
    drag.currentX = world.x
    drag.currentY = world.y
    marquee.value = {
      x: Math.min(drag.startX, world.x),
      y: Math.min(drag.startY, world.y),
      width: Math.abs(world.x - drag.startX),
      height: Math.abs(world.y - drag.startY),
    }
    return
  }
  if (drag.kind === 'wall') {
    drag.currentX = world.x
    drag.currentY = world.y
    wallPreview.value = { x0: drag.startX, y0: drag.startY, x1: world.x, y1: world.y }
    return
  }
  if (drag.kind === 'token') {
    if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) >= DRAG_THRESHOLD_PX) {
      drag.moved = true
    }
    if (drag.moved) {
      centerX.value -= dxScreen / zoom.value
      centerY.value -= dyScreen / zoom.value
    }
    return
  }
  const token = tokenAt(drag.ref)
  if (!token) return
  if (!drag.started) {
    drag.started = true
    emit('gesture-begin')
  }
  if (drag.kind === 'resize') {
    const width = Math.max(0.25, (world.x - token.position.x) / cellSize.value)
    const height = Math.max(0.25, (world.y - token.position.y) / cellSize.value)
    emit('gesture-resize', drag.ref, width, height)
    return
  }
  const cx = token.position.x + token.size.width * cellSize.value / 2
  const cy = token.position.y + token.size.height * cellSize.value / 2
  const angle = Math.atan2(world.y - cy, world.x - cx) * 180 / Math.PI + 90
  emit('gesture-rotate', drag.ref, ((angle % 360) + 360) % 360)
}

function boundsOf(ref: PlaceableRef): { x: number; y: number; width: number; height: number } | null {
  if (ref.collection === 'tokens') {
    const token = props.scene.placeables.tokens[ref.id]
    if (!token) return null
    return { x: token.position.x, y: token.position.y, width: token.size.width * cellSize.value, height: token.size.height * cellSize.value }
  }
  if (ref.collection === 'walls') {
    const wall = props.scene.placeables.walls[ref.id]
    if (!wall) return null
    const e = wall.endpoints
    return { x: Math.min(e.x0, e.x1), y: Math.min(e.y0, e.y1), width: Math.abs(e.x1 - e.x0), height: Math.abs(e.y1 - e.y0) }
  }
  if (ref.collection === 'lights') {
    const light = props.scene.placeables.lights[ref.id]
    if (!light) return null
    const r = Math.max(light.radius.dim, 12)
    return { x: light.position.x - r, y: light.position.y - r, width: r * 2, height: r * 2 }
  }
  if (ref.collection === 'notes') {
    const note = props.scene.placeables.notes[ref.id]
    if (!note) return null
    return { x: note.position.x - 12, y: note.position.y - 12, width: 24, height: 24 }
  }
  return null
}

function onPointerUp(e: PointerEvent) {
  pointers.delete(e.pointerId)
  pinchDistance = pointers.size === 2 ? pointerDistance() : 0
  const current = drag
  drag = null
  if (!current || pointers.size > 0) {
    marquee.value = null
    wallPreview.value = null
    return
  }
  if (current.kind === 'marquee') {
    const rect = marquee.value
    marquee.value = null
    if (!rect || (rect.width < 2 && rect.height < 2)) {
      if (!current.additive) emit('clear-selection')
      return
    }
    const refs: PlaceableRef[] = []
    for (const collection of ['tokens', 'walls', 'lights', 'notes'] as const) {
      for (const id of Object.keys(props.scene.placeables[collection])) {
        const ref = { collection, id }
        const bounds = boundsOf(ref)
        if (bounds
          && bounds.x < rect.x + rect.width && bounds.x + bounds.width > rect.x
          && bounds.y < rect.y + rect.height && bounds.y + bounds.height > rect.y) {
          refs.push(ref)
        }
      }
    }
    emit('select-many', refs, current.additive)
    return
  }
  if (current.kind === 'wall') {
    const preview = wallPreview.value
    wallPreview.value = null
    if (preview && Math.hypot(preview.x1 - preview.x0, preview.y1 - preview.y0) >= MIN_WALL_LENGTH) {
      emit('draw-wall', preview)
    }
    return
  }
  if (current.kind === 'token' && !current.moved) {
    emit('place-token', toWorld(e.clientX, e.clientY))
  }
}

// ---- rendered items --------------------------------------------------------
const tokens = computed(() => Object.values(props.scene.placeables.tokens).map(token => ({
  token,
  width: Math.max(0, token.size.width) * cellSize.value,
  height: Math.max(0, token.size.height) * cellSize.value,
  url: token.texture?.source_path || '',
  selected: isSelected({ collection: 'tokens', id: token.canonical_id }),
})))

const failed = ref(new Set<string>())
function failedKey(id: string, url: string): string { return `${id}|${url}` }
function onImageError(id: string, url: string) { failed.value.add(failedKey(id, url)) }

function dispositionFill(disposition: number): string {
  if (disposition === -1) return 'var(--df-danger-strong)'
  if (disposition === 0) return 'var(--df-warning)'
  if (disposition === 1) return 'var(--df-interactive-strong)'
  if (disposition === -2) return '#b07fd8'
  return 'var(--df-text-muted)'
}

const walls = computed(() => Object.values(props.scene.placeables.walls).map(wall => ({
  wall,
  selected: isSelected({ collection: 'walls', id: wall.canonical_id }),
})))

const lights = computed(() => Object.values(props.scene.placeables.lights).map(light => ({
  light,
  color: light.color || '#ffd27f',
  hitRadius: Math.max(light.radius.dim, 12),
  selected: isSelected({ collection: 'lights', id: light.canonical_id }),
})))

const notes = computed(() => Object.values(props.scene.placeables.notes).map(note => ({
  note,
  selected: isSelected({ collection: 'notes', id: note.canonical_id }),
})))

const handleToken = computed(() => {
  if (props.selection.length !== 1) return null
  const ref = props.selection[0]!
  if (ref.collection !== 'tokens') return null
  const token = tokenAt(ref)
  if (!token || token.locked) return null
  return {
    ref,
    token,
    width: token.size.width * cellSize.value,
    height: token.size.height * cellSize.value,
    size: 12 / zoom.value,
    rotationOffset: 26 / zoom.value,
  }
})

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
  <div class="tactical-board-viewport editor-board-viewport">
    <svg
      ref="svgEl"
      :viewBox="viewBox"
      class="tactical-board-svg editor-board-svg"
      :class="`editor-tool-${tool}`"
      role="application"
      :aria-label="`Scene editor canvas: ${scene.display_name || scene.canonical_id}`"
      preserveAspectRatio="xMidYMid meet"
      data-testid="editor-board-svg"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    >
      <BoardBackground :background="scene.background" :rect="sceneRect" :game-key="gameKey" />
      <BoardGrid :grid="scene.grid" :rect="sceneRect" :origin="gridOrigin" />

      <g data-testid="editor-lights">
        <g
          v-for="item in lights"
          :key="item.light.canonical_id"
          :opacity="item.light.hidden ? 0.45 : 1"
          data-testid="editor-light"
          :data-id="item.light.canonical_id"
          @pointerdown="onItemPointerDown({ collection: 'lights', id: item.light.canonical_id }, $event)"
        >
          <circle
            v-if="item.light.radius.dim > 0"
            :cx="item.light.position.x"
            :cy="item.light.position.y"
            :r="item.light.radius.dim"
            :fill="item.color"
            :opacity="0.14 * item.light.intensity"
          />
          <circle
            v-if="item.light.radius.bright > 0"
            :cx="item.light.position.x"
            :cy="item.light.position.y"
            :r="item.light.radius.bright"
            :fill="item.color"
            :opacity="0.3 * item.light.intensity"
          />
          <circle
            :cx="item.light.position.x"
            :cy="item.light.position.y"
            :r="item.hitRadius"
            class="editor-hit"
            :class="{ 'editor-selected-ring': item.selected }"
          />
        </g>
      </g>

      <g data-testid="editor-walls">
        <g
          v-for="item in walls"
          :key="item.wall.canonical_id"
          data-testid="editor-wall"
          :data-id="item.wall.canonical_id"
          @pointerdown="onItemPointerDown({ collection: 'walls', id: item.wall.canonical_id }, $event)"
        >
          <line
            :x1="item.wall.endpoints.x0"
            :y1="item.wall.endpoints.y0"
            :x2="item.wall.endpoints.x1"
            :y2="item.wall.endpoints.y1"
            class="editor-hit-line"
          />
          <line
            :x1="item.wall.endpoints.x0"
            :y1="item.wall.endpoints.y0"
            :x2="item.wall.endpoints.x1"
            :y2="item.wall.endpoints.y1"
            :class="['board-wall', { 'board-wall-door': item.wall.door > 0, 'editor-selected-line': item.selected }]"
            vector-effect="non-scaling-stroke"
          />
        </g>
      </g>

      <g data-testid="editor-notes">
        <g
          v-for="item in notes"
          :key="item.note.canonical_id"
          :transform="`translate(${item.note.position.x},${item.note.position.y})`"
          data-testid="editor-note"
          :data-id="item.note.canonical_id"
          @pointerdown="onItemPointerDown({ collection: 'notes', id: item.note.canonical_id }, $event)"
        >
          <rect x="-12" y="-12" width="24" height="24" class="editor-note-marker" :class="{ 'editor-selected-ring': item.selected }" />
          <text y="28" text-anchor="middle" class="editor-note-text">{{ item.note.text || item.note.canonical_id }}</text>
        </g>
      </g>

      <g data-testid="editor-tokens">
        <g
          v-for="item in tokens"
          :key="item.token.canonical_id"
          :transform="`translate(${item.token.position.x},${item.token.position.y})`"
          :opacity="(item.token.hidden ? 0.45 : 1) * item.token.alpha"
          data-testid="editor-token"
          :data-id="item.token.canonical_id"
          @pointerdown="onItemPointerDown({ collection: 'tokens', id: item.token.canonical_id }, $event)"
        >
          <g :transform="`rotate(${item.token.rotation} ${item.width / 2} ${item.height / 2})`">
            <image
              v-if="item.url && !failed.has(failedKey(item.token.canonical_id, item.url))"
              :href="item.url"
              :width="item.width"
              :height="item.height"
              preserveAspectRatio="xMidYMid slice"
              @error="onImageError(item.token.canonical_id, item.url)"
            />
            <circle
              v-else
              :cx="item.width / 2"
              :cy="item.height / 2"
              :r="Math.min(item.width, item.height) * 0.42"
              :fill="dispositionFill(item.token.disposition)"
              class="board-token-fallback"
              vector-effect="non-scaling-stroke"
            />
            <rect
              v-if="item.selected"
              :width="item.width"
              :height="item.height"
              class="editor-selection-box"
              vector-effect="non-scaling-stroke"
            />
          </g>
        </g>
      </g>

      <g v-if="handleToken" data-testid="editor-handles">
        <line
          :x1="handleToken.token.position.x + handleToken.width / 2"
          :y1="handleToken.token.position.y"
          :x2="handleToken.token.position.x + handleToken.width / 2"
          :y2="handleToken.token.position.y - handleToken.rotationOffset"
          class="editor-handle-stem"
          vector-effect="non-scaling-stroke"
        />
        <circle
          :cx="handleToken.token.position.x + handleToken.width / 2"
          :cy="handleToken.token.position.y - handleToken.rotationOffset"
          :r="handleToken.size / 2"
          class="editor-handle"
          data-testid="editor-rotate-handle"
          @pointerdown="onHandlePointerDown('rotate', handleToken.ref, $event)"
        />
        <rect
          :x="handleToken.token.position.x + handleToken.width - handleToken.size / 2"
          :y="handleToken.token.position.y + handleToken.height - handleToken.size / 2"
          :width="handleToken.size"
          :height="handleToken.size"
          class="editor-handle"
          data-testid="editor-resize-handle"
          @pointerdown="onHandlePointerDown('resize', handleToken.ref, $event)"
        />
      </g>

      <line
        v-if="wallPreview"
        :x1="wallPreview.x0"
        :y1="wallPreview.y0"
        :x2="wallPreview.x1"
        :y2="wallPreview.y1"
        class="editor-wall-preview"
        vector-effect="non-scaling-stroke"
        data-testid="editor-wall-preview"
      />
      <rect
        v-if="marquee"
        :x="marquee.x"
        :y="marquee.y"
        :width="marquee.width"
        :height="marquee.height"
        class="editor-marquee"
        vector-effect="non-scaling-stroke"
        data-testid="editor-marquee"
      />
    </svg>
    <div class="editor-board-zoom">
      <button type="button" :aria-label="copy.zoomOut" :title="copy.zoomOut" data-testid="editor-zoom-out" @click="zoomBy(0.8)">−</button>
      <button type="button" :aria-label="copy.zoomIn" :title="copy.zoomIn" data-testid="editor-zoom-in" @click="zoomBy(1.25)">+</button>
      <button type="button" :title="copy.resetView" data-testid="editor-reset-view" @click="fitView()">⤢</button>
    </div>
  </div>
</template>