<script setup lang="ts">
import { computed } from 'vue'
import { ref } from 'vue'
import { api } from '@/api/client'
import GameplayDeclarativeNode from './GameplayDeclarativeNode.vue'

const props = defineProps<{
  modeId?: string
  gameKey?: string
  projection?: Record<string, unknown> | null
}>()
const emit = defineEmits<{ refresh: [] }>()

const serialized = computed(() => JSON.stringify(props.projection?.view ?? {}, null, 2))
const surfaces = computed(() => {
  const ui = props.projection?.view && typeof props.projection.view === 'object'
    ? (props.projection.view as Record<string, unknown>).ui
    : null
  return ui && typeof ui === 'object' && Array.isArray((ui as Record<string, unknown>).surfaces)
    ? (ui as Record<string, unknown>).surfaces as Record<string, unknown>[]
    : []
})
const actionBusy = ref(false)
const actionError = ref('')

function root(surface: Record<string, unknown>): Record<string, unknown> | null {
  return surface.root && typeof surface.root === 'object'
    ? surface.root as Record<string, unknown>
    : null
}

async function runAction(payload: { name: string; arguments: Record<string, unknown> }) {
  if (!payload.name || !props.gameKey || actionBusy.value) return
  actionBusy.value = true
  actionError.value = ''
  try {
    const result = await api<{ ok?: boolean; error?: string }>(
      `/games/${encodeURIComponent(props.gameKey)}/gameplay-actions/${encodeURIComponent(payload.name)}`,
      { method: 'POST', body: JSON.stringify(payload.arguments) },
    )
    if (!result?.ok) actionError.value = result?.error || 'Gameplay action failed'
    else emit('refresh')
  } catch (error: unknown) {
    actionError.value = error instanceof Error ? error.message : String(error)
  } finally {
    actionBusy.value = false
  }
}
</script>

<template>
  <section class="view gameplay-projection" data-testid="gameplay-projection-view" role="status">
    <h1>{{ modeId || 'Gameplay' }}</h1>
    <p class="muted">This gameplay is supplied by its owner provider.</p>
    <p v-if="actionError" role="alert">{{ actionError }}</p>
    <div v-if="surfaces.length" data-testid="gameplay-declarative-surfaces" class="gameplay-surfaces">
      <section v-for="(surface, index) in surfaces" :key="String(surface.slot || index)" class="gameplay-surface">
        <h2>{{ surface.slot }}</h2>
        <GameplayDeclarativeNode v-if="root(surface)" :node="root(surface)!" @action="runAction" />
      </section>
    </div>
    <pre v-else data-testid="gameplay-projection">{{ serialized }}</pre>
  </section>
</template>

<style scoped>
.gameplay-projection { max-width: 960px; margin: 32px auto; padding: 24px; }
.gameplay-projection h1 { margin: 0 0 8px; }
.gameplay-projection pre { overflow: auto; max-height: 70vh; padding: 16px; border-radius: 8px; background: var(--df-surface-2); }
.gameplay-surfaces { display: grid; gap: 16px; }
.gameplay-surface { padding: 12px; border: 1px solid var(--df-border-soft); border-radius: 8px; }
.gameplay-surface h2 { margin: 0 0 8px; font-size: 1rem; }
</style>
