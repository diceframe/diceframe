<script setup lang="ts">
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import { ChevronBack, ChevronForward } from '@vicons/ionicons5'
import type { GameDetail, MapData, Player, PrivateMessage, RuleMeta } from '../api/types'
import { gameStateLabel } from '../utils/play'
import CharacterPanel from './CharacterPanel.vue'
import MapGraph from './play/MapGraph.vue'

interface Quest { title?: string; progress?: string; status?: string }
interface Relation { npc_name?: string; tier?: string }
interface Decision { title?: string; summary?: string; description?: string; round_number?: number }
interface PlotTracker { quests?: Record<string, Quest>; relations?: Record<string, Relation>; decisions?: Array<Decision | string> }

const props = defineProps<{ detail: GameDetail; player?: Player; privateMessages: PrivateMessage[]; map?: MapData | null; ruleMeta?: RuleMeta | null; collapsed?: boolean }>()
const emit = defineEmits<{ 'lore-click': [name: string]; 'toggle-sidebar': [] }>()

const pt = computed<PlotTracker>(() => (props.detail.plot_tracker && typeof props.detail.plot_tracker === 'object' ? props.detail.plot_tracker as PlotTracker : {}))
const activeQuests = computed(() => Object.values(pt.value.quests || {}).filter(q => q.status === 'active'))
const doneQuests = computed(() => Object.values(pt.value.quests || {}).filter(q => q.status === 'completed' || q.status === 'failed'))
const notableRelations = computed(() => Object.values(pt.value.relations || {}).filter(r => r.tier !== 'neutral'))
const recentDecisions = computed(() => (pt.value.decisions || []).slice(-5).reverse())
const hasPlot = computed(() => activeQuests.value.length || doneQuests.value.length || notableRelations.value.length || recentDecisions.value.length)
const recentPerceptions = computed(() => props.privateMessages.slice(-3).reverse())
function fmtDecision(d: Decision | string) {
  if (typeof d === 'string') return d
  const t = d.title || d.summary || d.description
  if (t) return (d.round_number ? '第' + d.round_number + '轮 · ' : '') + t
  return JSON.stringify(d)
}
function perceptionText(m: PrivateMessage | string) {
  return typeof m === 'string' ? m : m.text || ''
}
function perceptionKey(m: PrivateMessage | string, i: number) {
  if (typeof m === 'string') return `${i}:${m}`
  return `${m.user_id || ''}:${m.round || ''}:${m.timestamp || ''}:${m.text || i}`
}
</script>

<template>
  <aside class="game-sidebar" :class="{ collapsed }">
    <button class="sidebar-toggle" @click="emit('toggle-sidebar')" :title="collapsed ? '展开侧栏' : '收起侧栏'">
      <NIcon :component="collapsed ? ChevronForward : ChevronBack" size="16" />
    </button>
    <CharacterPanel :player="player" :rule-meta="ruleMeta" />

    <section class="panel" v-if="hasPlot">
      <h2>剧情追踪</h2>
      <div v-if="activeQuests.length" class="quest-group">
        <strong class="quest-head">当前任务</strong>
        <div v-for="(q, i) in activeQuests" :key="'qa' + i" class="quest-item"><strong>{{ q.title }}</strong><span v-if="q.progress" class="muted"> {{ q.progress }}</span></div>
      </div>
      <div v-if="doneQuests.length" class="quest-group">
        <strong class="quest-head muted">已结束任务</strong>
        <div v-for="(q, i) in doneQuests" :key="'qd' + i" class="quest-item" :class="q.status === 'completed' ? 'good' : 'warn'"><strong>{{ q.title }}</strong> <span class="muted">{{ q.status }}</span></div>
      </div>
      <div v-if="notableRelations.length" class="quest-group">
        <strong class="quest-head">NPC 关系</strong>
        <div v-for="(r, i) in notableRelations" :key="'r' + i" class="quest-item"><strong>{{ r.npc_name }}</strong> <span class="muted">{{ r.tier }}</span></div>
      </div>
      <div v-if="recentDecisions.length" class="quest-group">
        <strong class="quest-head">最近决策</strong>
        <div v-for="(d, i) in recentDecisions" :key="'d' + i" class="quest-item muted">{{ fmtDecision(d) }}</div>
      </div>
    </section>

    <section class="panel">
      <header><h2>角色感知</h2><span>{{ privateMessages.length }}</span></header>
      <div v-if="privateMessages.length" class="perceptions">
        <p
          v-for="(m, i) in recentPerceptions"
          :key="perceptionKey(m, i)"
          class="perception-item"
          :class="{ fresh: i === 0 }"
        >{{ perceptionText(m) }}</p>
      </div>
      <p v-else class="muted">暂无专属于你的感知。</p>
    </section>

    <section class="panel">
      <h2>状态信息</h2>
      <div class="chips"><span>{{ detail.solo_mode ? '单人' : '多人' }}</span><span>{{ gameStateLabel(detail.state) }}</span></div>
      <details><summary>多人协调</summary>
        <p>已行动 {{ detail.multiplayer?.ready_count || 0 }} / {{ detail.multiplayer?.active_count ?? detail.multiplayer?.player_count ?? 0 }}</p>
        <p>等待：{{ detail.multiplayer?.waiting_players?.map((p: Player) => p.character_name).join('、') || '无' }}</p>
        <p v-if="detail.multiplayer?.away_players?.length">暂离：{{ detail.multiplayer.away_players.map((p: Player) => p.character_name).join('、') }}</p>
      </details>
    </section>

    <MapGraph :map="map" :current-scene="detail.scene" @lore-click="emit('lore-click', $event)" />
  </aside>
</template>
