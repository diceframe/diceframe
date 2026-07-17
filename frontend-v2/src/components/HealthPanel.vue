<script setup lang="ts">
import { computed } from 'vue'
import type { GameDetail, HealthEvent, HealthResponse } from '@/api/types'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{ health?: HealthResponse | null; detail?: GameDetail | null; isGm: boolean }>()
const emit = defineEmits<{ resolve: [id: string, action: string] }>()
const { t } = useLocale()

const events = computed<HealthEvent[]>(() => (props.health?.events || []).filter(Boolean))
const active = computed(() => events.value.filter(e => !e.resolved && !e.ignored))
const rollback = computed(() => events.value.filter(e => e.component === 'gm_control' || e.code === 'GM_ROLLBACK' || e.code === 'GM_COMMAND').slice(-3).reverse())
const abnormal = computed(() => events.value.filter(e => !e.resolved && !e.ignored && (e.severity === 'warning' || e.severity === 'error')).slice(0, 5))
const memory = computed(() => events.value.filter(e => { const c = String(e.component || ''); return c === 'memory' || c === 'save' || c.includes('embedding') }).slice(-4).reverse())
const d = computed<GameDetail>(() => props.detail || { game_key: '' })
const visible = computed(() => props.isGm || props.detail?.solo_mode)
function stateLabel(state?: string) {
  const labels: Record<string, string> = {
    active_action: t('actionPhase'),
    active_judgment: t('gmThinking'),
    paused: t('statePaused'),
    waiting: t('stateWaiting'),
    created: t('stateCreating'),
    ended: t('stateEnded'),
  }
  return (state && labels[state]) || state || t('stateUnknown')
}
</script>

<template>
  <section v-if="visible" class="health-panel">
    <div class="status-tags">
      <span class="status-chip"><strong>{{ t('round') }}</strong>{{ d.round_number || 0 }}</span>
      <span class="status-chip"><strong>{{ t('phase') }}</strong>{{ stateLabel(d.state) }}</span>
      <span class="status-chip"><strong>{{ t('mode') }}</strong>{{ d.solo_mode ? t('solo') : t('multiplayer') }}</span>
      <span class="status-chip"><strong>{{ t('players') }}</strong>{{ d.multiplayer?.player_count || 0 }}/{{ d.multiplayer?.max_players || 0 }}</span>
      <span class="status-chip"><strong>{{ t('waitingAction') }}</strong>{{ d.multiplayer?.waiting_players?.length || 0 }}</span>
      <span class="status-chip"><strong>Token</strong>{{ d.total_tokens || 0 }}</span>
    </div>
    <div v-if="active.length" class="hp-block"><strong class="hp-head">{{ t('unhandledIssues') }}</strong>
      <div v-for="e in active" :key="e.id" class="hp-event warn"><span>{{ e.title || e.message || e.code }}</span>
        <div class="hp-actions"><button @click="emit('resolve', e.id, 'resolve')">{{ t('resolved') }}</button><button @click="emit('resolve', e.id, 'ignore')">{{ t('ignore') }}</button></div>
      </div>
    </div>
    <div v-if="rollback.length" class="hp-block"><strong class="hp-head">{{ t('rollbackAndGmFix') }}</strong><div v-for="e in rollback" :key="'r' + e.id" class="hp-event muted">{{ e.title || e.code || e.component }}</div></div>
    <div v-if="memory.length" class="hp-block"><strong class="hp-head">{{ t('memoryVectorSave') }}</strong><div v-for="e in memory" :key="'m' + e.id" class="hp-event muted">{{ e.title || e.code || e.component }}</div></div>
    <p v-if="!events.length" class="muted">{{ t('noHealthEvents') }}</p>
  </section>
</template>
