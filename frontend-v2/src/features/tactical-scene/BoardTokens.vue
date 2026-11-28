<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TacticalSceneTokenProjection } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalSceneCopy } from './copy'
import { tokenDisplayName } from './tokenDisplay'

const props = defineProps<{
  tokens: Record<string, TacticalSceneTokenProjection>
  cellSize: number
  selectedId?: string | null
}>()

const emit = defineEmits<{
  select: [id: string]
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalSceneCopy(locale.value))

// Display-only disposition tint for the texture-less fallback; mechanics never
// read this color.
function dispositionFill(disposition: number): string {
  if (disposition === -1) return 'var(--df-danger-strong)'
  if (disposition === 0) return 'var(--df-warning)'
  if (disposition === 1) return 'var(--df-interactive-strong)'
  if (disposition === -2) return '#b07fd8'
  return 'var(--df-text-muted)'
}

const items = computed(() => Object.values(props.tokens).map(token => ({
  token,
  width: Math.max(0, token.size.width) * props.cellSize,
  height: Math.max(0, token.size.height) * props.cellSize,
  url: token.texture?.source_path || '',
  fill: dispositionFill(token.disposition),
  label: tokenDisplayName(token),
})))

// Failed loads are keyed by id + URL so a changed texture retries naturally.
const failed = ref(new Set<string>())
function failedKey(id: string, url: string): string { return `${id}|${url}` }
function onImageError(id: string, url: string) { failed.value.add(failedKey(id, url)) }

function onKeydown(id: string, event: KeyboardEvent) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    emit('select', id)
  }
}
</script>

<template>
  <g data-testid="board-tokens">
    <g
      v-for="item in items"
      :key="item.token.canonical_id"
      :transform="`translate(${item.token.position.x},${item.token.position.y})`"
      :opacity="item.token.alpha"
      class="board-token"
      :class="{ selected: selectedId === item.token.canonical_id }"
      role="button"
      tabindex="0"
      :aria-label="`${copy.boardLabel}: ${item.label}`"
      :aria-pressed="selectedId === item.token.canonical_id"
      data-testid="board-token"
      :data-id="item.token.canonical_id"
      @pointerdown.stop
      @click="emit('select', item.token.canonical_id)"
      @keydown="onKeydown(item.token.canonical_id, $event)"
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
          :fill="item.fill"
          class="board-token-fallback"
          vector-effect="non-scaling-stroke"
        />
      </g>
      <rect
        v-if="selectedId === item.token.canonical_id"
        class="board-token-selected-ring"
        :width="item.width"
        :height="item.height"
        fill="none"
        vector-effect="non-scaling-stroke"
      />
      <!-- Transparent hit area keeps the whole cell clickable / tappable. -->
      <rect class="board-token-hit" :width="item.width" :height="item.height" fill="transparent" />
    </g>
  </g>
</template>