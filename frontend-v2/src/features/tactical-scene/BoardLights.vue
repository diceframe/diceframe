<script setup lang="ts">
import { computed } from 'vue'
import type { TacticalSceneLightProjection } from '@/api/tacticalScene'

const props = defineProps<{ lights: Record<string, TacticalSceneLightProjection> }>()

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value))
}

// M1 renders light reach as plain circles; color and intensity only feed
// opacity, never a lighting computation.
const items = computed(() => Object.values(props.lights)
  .filter(light => light.radius.dim > 0 || light.radius.bright > 0)
  .map(light => ({
    light,
    color: light.color || '#ffd27f',
    dimOpacity: 0.14 * clamp01(light.intensity),
    brightOpacity: 0.3 * clamp01(light.intensity),
  })))
</script>

<template>
  <g data-testid="board-lights" class="board-lights">
    <g v-for="item in items" :key="item.light.canonical_id" data-testid="board-light">
      <circle
        v-if="item.light.radius.dim > 0"
        :cx="item.light.position.x"
        :cy="item.light.position.y"
        :r="item.light.radius.dim"
        :fill="item.color"
        :opacity="item.dimOpacity"
      />
      <circle
        v-if="item.light.radius.bright > 0"
        :cx="item.light.position.x"
        :cy="item.light.position.y"
        :r="item.light.radius.bright"
        :fill="item.color"
        :opacity="item.brightOpacity"
      />
    </g>
  </g>
</template>