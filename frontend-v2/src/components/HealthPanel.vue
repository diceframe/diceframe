<script setup lang="ts">
import { computed } from 'vue'
import type { GameDetail, HealthEvent, HealthResponse } from '../api/types'
import { gameStateLabel } from '../utils/play'

const props = defineProps<{ health?: HealthResponse | null; detail?: GameDetail | null; isGm: boolean }>()
const emit = defineEmits<{ resolve: [id: string, action: string] }>()

const events = computed<HealthEvent[]>(() => (props.health?.events || []).filter(Boolean))
const active = computed(() => events.value.filter(e => !e.resolved && !e.ignored))
const rollback = computed(() => events.value.filter(e => e.component === 'gm_control' || e.code === 'GM_ROLLBACK' || e.code === 'GM_COMMAND').slice(-3).reverse())
const abnormal = computed(() => events.value.filter(e => !e.resolved && !e.ignored && (e.severity === 'warning' || e.severity === 'error')).slice(0, 5))
const memory = computed(() => events.value.filter(e => { const c = String(e.component || ''); return c === 'memory' || c === 'save' || c.includes('embedding') }).slice(-4).reverse())
const d = computed<GameDetail>(() => props.detail || { game_key: '' })
const visible = computed(() => props.isGm || props.detail?.solo_mode)
</script>

<template>
  <section v-if="visible" class="health-panel">
    <div class="status-tags">
      <span class="status-chip"><strong>轮次</strong>{{ d.round_number || 0 }}</span>
      <span class="status-chip"><strong>阶段</strong>{{ gameStateLabel(d.state) }}</span>
      <span class="status-chip"><strong>模式</strong>{{ d.solo_mode ? '单人' : '多人' }}</span>
      <span class="status-chip"><strong>玩家</strong>{{ d.multiplayer?.player_count || 0 }}/{{ d.multiplayer?.max_players || 0 }}</span>
      <span class="status-chip"><strong>待行动</strong>{{ d.multiplayer?.waiting_players?.length || 0 }}</span>
      <span class="status-chip"><strong>Token</strong>{{ d.total_tokens || 0 }}</span>
    </div>
    <div v-if="active.length" class="hp-block"><strong class="hp-head">未处理异常</strong>
      <div v-for="e in active" :key="e.id" class="hp-event warn"><span>{{ e.title || e.message || e.code }}</span>
        <div class="hp-actions"><button @click="emit('resolve', e.id, 'resolve')">已处理</button><button @click="emit('resolve', e.id, 'ignore')">忽略</button></div>
      </div>
    </div>
    <div v-if="rollback.length" class="hp-block"><strong class="hp-head">回退 / GM 修正</strong><div v-for="e in rollback" :key="'r' + e.id" class="hp-event muted">{{ e.title || e.code || e.component }}</div></div>
    <div v-if="memory.length" class="hp-block"><strong class="hp-head">记忆 / 向量 / 存档</strong><div v-for="e in memory" :key="'m' + e.id" class="hp-event muted">{{ e.title || e.code || e.component }}</div></div>
    <p v-if="!events.length" class="muted">暂无系统状态事件。</p>
  </section>
</template>
