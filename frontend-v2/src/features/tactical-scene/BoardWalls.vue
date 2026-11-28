<script setup lang="ts">
import { computed } from 'vue'
import type { TacticalSceneWallProjection } from '@/api/tacticalScene'

const props = defineProps<{ walls: Record<string, TacticalSceneWallProjection> }>()

const items = computed(() => Object.values(props.walls))
</script>

<template>
  <g data-testid="board-walls" class="board-walls">
    <line
      v-for="wall in items"
      :key="wall.canonical_id"
      :x1="wall.endpoints.x0"
      :y1="wall.endpoints.y0"
      :x2="wall.endpoints.x1"
      :y2="wall.endpoints.y1"
      :class="['board-wall', { 'board-wall-door': wall.door > 0 }]"
      vector-effect="non-scaling-stroke"
      data-testid="board-wall"
    />
  </g>
</template>