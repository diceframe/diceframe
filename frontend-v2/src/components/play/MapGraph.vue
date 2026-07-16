<script setup lang="ts">
import { computed } from 'vue'
import type { MapData, MapLocation } from '@/api/types'

const props = defineProps<{ map?: MapData | null; currentScene?: string }>()
const emit = defineEmits<{ 'lore-click': [name: string] }>()

interface MapNode { id: string; name: string; x: number; y: number; current: boolean }

const locations = computed<MapLocation[]>(() => props.map?.locations || [])
const nodes = computed<MapNode[]>(() => {
  const locs = locations.value
  const n = locs.length
  return locs.map((l, i) => {
    const angle = (i / Math.max(1, n)) * 2 * Math.PI - Math.PI / 2
    const r = 36
    return {
      id: String(l.id ?? l.name ?? i),
      name: l.name || l.id || `#${i}`,
      x: 50 + r * Math.cos(angle),
      y: 50 + r * Math.sin(angle),
      current: Boolean(props.currentScene && (l.name === props.currentScene || l.id === props.currentScene)),
    }
  })
})

const edges = computed(() => {
  const out: { x1: number; y1: number; x2: number; y2: number }[] = []
  const idx: Record<string, number> = {}
  nodes.value.forEach((n, i) => { idx[n.id] = i })
  const seen = new Set<string>()
  for (const loc of locations.value) {
    const ai = idx[String(loc.id ?? loc.name ?? '')]
    if (ai === undefined) continue
    for (const b of loc.connected_to || []) {
      const bi = idx[String(b)]
      if (bi === undefined) continue
      const key = ai < bi ? `${ai}-${bi}` : `${bi}-${ai}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push({ x1: nodes.value[ai].x, y1: nodes.value[ai].y, x2: nodes.value[bi].x, y2: nodes.value[bi].y })
    }
  }
  return out
})
</script>

<template>
  <section class="map-graph panel">
    <h2>场景地图</h2>
    <svg v-if="nodes.length" viewBox="0 0 100 100" class="map-svg" preserveAspectRatio="xMidYMid meet">
      <line v-for="(e, i) in edges" :key="'e' + i" :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2" class="map-edge" />
      <g
        v-for="n in nodes"
        :key="n.id"
        :class="['map-node', { current: n.current }]"
        :transform="`translate(${n.x},${n.y})`"
        @click="emit('lore-click', n.name)"
      >
        <circle r="3.2" />
        <text y="8" text-anchor="middle">{{ n.name }}</text>
        <text v-if="n.current" y="-5.5" text-anchor="middle" class="map-star">★</text>
      </g>
    </svg>
    <p v-else class="muted">暂无地图数据。</p>
  </section>
</template>
