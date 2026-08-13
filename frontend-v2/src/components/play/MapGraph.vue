<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { MapData, MapLocation } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { forceLayout, type MapNode } from '@/utils/mapLayout'

const props = withDefaults(defineProps<{
  map?: MapData | null
  currentScene?: string
  variant?: 'compact' | 'workspace'
  selectedLocationId?: string
  showHeader?: boolean
}>(), {
  variant: 'compact',
  selectedLocationId: '',
  showHeader: true,
})
const emit = defineEmits<{
  'lore-click': [name: string]
  'location-select': [location: MapLocation]
}>()
const { t } = useLocale()

const locations = computed<MapLocation[]>(() => props.map?.locations || [])
const assetCount = computed(() => {
  const assets = props.map?.assets || {}
  return (assets.icons?.length || 0) + (assets.scenes?.length || 0)
})
const backgroundUrl = computed(() => props.map?.active_map?.background?.url || '')
const mapName = computed(() => props.map?.active_map?.name || t('mapTitle'))
const locationIndex = computed(() => new Map(
  locations.value.map(location => [String(location.id ?? location.name ?? ''), location]),
))

const nodes = computed<MapNode[]>(() =>
  forceLayout(locations.value, {
    anchorId: String(props.map?.current_location_id || props.currentScene || '') || undefined,
  }),
)
const currentNode = computed(() => nodes.value.find(node => node.current))

const edges = computed(() => {
  const out: { x1: number; y1: number; x2: number; y2: number }[] = []
  const idx: Record<string, number> = {}
  nodes.value.forEach((n, i) => { idx[n.id] = i })
  const nameIdx: Record<string, number> = {}
  nodes.value.forEach((n, i) => { nameIdx[n.name] = i; nameIdx[n.id] = i })
  const seen = new Set<string>()
  for (const loc of locations.value) {
    const ai = idx[String(loc.id ?? loc.name ?? '')]
    if (ai === undefined) continue
    for (const b of loc.connected_to || []) {
      const bi = nameIdx[String(b)]
      if (bi === undefined) continue
      const key = ai < bi ? `${ai}-${bi}` : `${bi}-${ai}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push({ x1: nodes.value[ai].x, y1: nodes.value[ai].y, x2: nodes.value[bi].x, y2: nodes.value[bi].y })
    }
  }
  return out
})

// 力导向输出落在 [-50, 50]，viewBox 基线 0 0 100 100

// ---- 视图状态：zoom + 视图中心的世界坐标（centerX/Y 即视野正中所指的世界点） ----
// 力导向把当前场景★锚定在世界原点 (0,0)，故 resetView 让视图中心对准 (0,0)，★ 在正中。
const zoom = ref(1)
const centerX = ref(0)
const centerY = ref(0)
const svgEl = ref<SVGSVGElement | null>(null)

const MIN_ZOOM = 0.25
const MAX_ZOOM = 8

function viewportScale(targetZoom = zoom.value) {
  const nominalSize = 100 / targetZoom
  const rect = svgEl.value?.getBoundingClientRect()
  if (!rect?.width || !rect.height) {
    return 100 / nominalSize
  }
  return Math.min(rect.width / nominalSize, rect.height / nominalSize)
}

const viewBox = computed(() => {
  const w = 100 / zoom.value
  const h = 100 / zoom.value
  return `${centerX.value - w / 2} ${centerY.value - h / 2} ${w} ${h}`
})

let resetAnim = 0

function resetView(animate = false) {
  if (resetAnim) { cancelAnimationFrame(resetAnim); resetAnim = 0 }
  const defaultZoom = Number(props.map?.active_map?.default_view?.zoom)
  const defaultX = Number(props.map?.active_map?.default_view?.x)
  const defaultY = Number(props.map?.active_map?.default_view?.y)
  const target = {
    zoom: Number.isFinite(defaultZoom) ? Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, defaultZoom)) : 1,
    centerX: currentNode.value?.x ?? (Number.isFinite(defaultX) ? defaultX : 0),
    centerY: currentNode.value?.y ?? (Number.isFinite(defaultY) ? defaultY : 0),
  }
  if (!animate) { zoom.value = target.zoom; centerX.value = target.centerX; centerY.value = target.centerY; return }
  // 简单插值动画回当前场景（布局锚点在中心）
  const start = { zoom: zoom.value, centerX: centerX.value, centerY: centerY.value }
  const startTime = performance.now()
  const duration = 260
  const step = (now: number) => {
    const p = Math.min(1, (now - startTime) / duration)
    const ease = 1 - Math.pow(1 - p, 3)
    zoom.value = start.zoom + (target.zoom - start.zoom) * ease
    centerX.value = start.centerX + (target.centerX - start.centerX) * ease
    centerY.value = start.centerY + (target.centerY - start.centerY) * ease
    if (p < 1) { resetAnim = requestAnimationFrame(step) } else { resetAnim = 0 }
  }
  resetAnim = requestAnimationFrame(step)
}

// 地图首次加载或地点数据变化时回到中心（★ 是布局锚点，必在中心）；
// 场景文本随剧情每轮变化，不因此抢用户视角，只有点「回到当前场景」才复位。
watch(
  () => `${props.map?.active_map?.id || ''}:${props.map?.current_location_id || ''}:${props.map?.locations?.length || 0}`,
  () => { resetView() },
  { immediate: true },
)

// ---- 缩放：滚轮以光标为锚点 ----
function onWheel(e: WheelEvent) {
  e.preventDefault()
  const factor = Math.exp(-e.deltaY * 0.002)
  const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom.value * factor))
  if (next === zoom.value) return
  // 以光标位置为缩放锚点：把光标下的世界坐标固定在光标下
  const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
  if (!rect.width || !rect.height) { zoom.value = next; return }
  const localX = e.clientX - rect.left - rect.width / 2
  const localY = e.clientY - rect.top - rect.height / 2
  const scale = viewportScale()
  const mx = centerX.value + localX / scale
  const my = centerY.value + localY / scale
  zoom.value = next
  const nextScale = viewportScale(next)
  centerX.value = mx - localX / nextScale
  centerY.value = my - localY / nextScale
}

// ---- 平移：pointer 拖拽，区分点击（触发 lore-click）与拖拽 ----
let dragging = false
let moved = false
let lastX = 0, lastY = 0

function onPointerDown(e: PointerEvent) {
  dragging = true
  moved = false
  lastX = e.clientX
  lastY = e.clientY
  try {
    ;(e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId)
  } catch {
    // 合成事件或浏览器不支持时忽略：指针捕获是优化，缺失仍可拖拽
  }
}

function onPointerMove(e: PointerEvent) {
  if (!dragging) return
  const dx = e.clientX - lastX
  const dy = e.clientY - lastY
  lastX = e.clientX
  lastY = e.clientY
  // 拖拽阈值：超过才视为拖拽（否则是点击），但首次超阈值的位移也应用
  if (!moved) {
    if (Math.abs(dx) + Math.abs(dy) <= 2) return
    moved = true
  }
  // 屏幕像素 → 世界坐标：viewBox 宽 100/zoom 映射到元素渲染宽度
  // centerX -= dx：viewBox x 减小 → 显示更左的世界 → 屏幕内容右移 → 跟手
  // （viewBox x 增大 = 看更右的世界 = 内容在屏幕上左移，所以方向取反）
  const worldPerPixel = 1 / viewportScale()
  centerX.value -= dx * worldPerPixel
  centerY.value -= dy * worldPerPixel
}

function onPointerUp() {
  dragging = false
}

function onNodeClick(id: string, name: string) {
  if (moved) return // 拖拽过就不再触发节点点击
  const location = locationIndex.value.get(id)
    || locations.value.find(item => item.name === name)
  if (location) emit('location-select', location)
  emit('lore-click', name)
}

function iconUrl(id: string): string {
  return String(locationIndex.value.get(id)?.icon_url || '')
}

const handleWheel = (e: WheelEvent) => onWheel(e)

onMounted(() => {
  const el = svgEl.value
  if (el) {
    el.addEventListener('wheel', handleWheel, { passive: false })
  }
})
onBeforeUnmount(() => {
  const el = svgEl.value
  if (el) {
    el.removeEventListener('wheel', handleWheel)
  }
})
</script>

<template>
  <section :class="['map-graph', `map-graph-${variant}`, { 'has-background': backgroundUrl }]">
    <div v-if="showHeader" class="map-head">
      <div><span class="map-kicker">MAP</span><h2>{{ mapName }}</h2></div>
      <button v-if="nodes.length" class="map-recenter" :title="t('mapRecenterTitle')" @click="resetView(true)">
        {{ t('mapRecenter') }}
      </button>
    </div>
    <div v-if="nodes.length" class="map-viewport">
      <img v-if="backgroundUrl" :src="backgroundUrl" alt="" class="map-background-image">
      <span v-if="backgroundUrl" class="map-background-shade" aria-hidden="true" />
      <svg
      ref="svgEl"
      :viewBox="viewBox"
      class="map-svg"
      role="img"
      :aria-label="mapName"
      preserveAspectRatio="xMidYMid meet"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    >
      <line v-for="(e, i) in edges" :key="'e' + i" :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2" class="map-edge" />
      <g
        v-for="n in nodes"
        :key="n.id"
        :class="['map-node', { current: n.current, selected: selectedLocationId === n.id }]"
        :transform="`translate(${n.x},${n.y})`"
        role="button"
        tabindex="0"
        :aria-label="n.current ? t('mapCurrentLocationLabel', { name: n.name }) : n.name"
        @click="onNodeClick(n.id, n.name)"
        @keydown.enter.prevent="onNodeClick(n.id, n.name)"
        @keydown.space.prevent="onNodeClick(n.id, n.name)"
      >
        <circle r="4.6" class="map-node-target" />
        <image v-if="iconUrl(n.id)" :href="iconUrl(n.id)" x="-3.2" y="-3.2" width="6.4" height="6.4" preserveAspectRatio="xMidYMid meet" class="map-node-icon" />
        <circle v-else r="3.2" class="map-node-dot" />
        <text y="8" text-anchor="middle">{{ n.name }}</text>
        <text v-if="n.current" y="-6.2" text-anchor="middle" class="map-star">★</text>
      </g>
      </svg>
    </div>
    <p v-else class="muted">{{ t('noMapData') }}</p>
    <p v-if="assetCount && variant === 'compact'" class="map-asset-note muted">{{ t('mapAssetsLoaded', { count: assetCount }) }}</p>
  </section>
</template>
