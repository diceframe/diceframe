<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, errorMessage } from '../../api/client'
import type { GameDetail, GameLogResponse, HealthResponse, LogEntry, LorebookResponse, LoreEntry, PrivateLogResponse, PrivateMessage } from '../../api/types'
import { readCurrentGame } from '../../stores/gameContext'
import { parseGMText, type LoreKeywords } from '../../utils/renderer'

interface LogViewData extends GameLogResponse { _lore?: LoreKeywords }
interface LogAction { uid: string; text: string }
interface HealthWithStatus extends HealthResponse { status?: Record<string, unknown> }

const game = ref(readCurrentGame())
const data = ref<LogViewData>({ log: [] })
const gameDetail = ref<GameDetail | null>(null)
const healthData = ref<HealthWithStatus | null>(null)
const privateMsgs = ref<PrivateMessage[]>([])
const error = ref('')
const tab = ref<'log' | 'proclog'>('log')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const expandedRounds = ref<Set<number>>(new Set())

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function buildLore(entries: LoreEntry[] = []): LoreKeywords {
  const lore: LoreKeywords = { npc: [], location: [], item: [], faction: [], event: [], puzzle: [], other: [] }
  for (const e of entries) {
    const arr = lore[e.type as keyof LoreKeywords]
    if (arr && e.name) arr.push(e.name)
  }
  return lore
}

async function load() {
  error.value = ''; data.value = { log: [] }; gameDetail.value = null; healthData.value = null; privateMsgs.value = []
  if (!game.value) return
  try {
    const logParams = new URLSearchParams({ page: String(page.value), per_page: String(pageSize.value) })
    const [detail, log, health, priv] = await Promise.all([
      api<GameDetail>(`/games/${encodeURIComponent(game.value)}`),
      api<GameLogResponse>(`/games/${encodeURIComponent(game.value)}/log?${logParams}`),
      api<HealthWithStatus>(`/games/${encodeURIComponent(game.value)}/health?include_resolved=true`),
      api<PrivateLogResponse>(`/games/${encodeURIComponent(game.value)}/private-log`),
    ])
    gameDetail.value = detail
    healthData.value = health
    privateMsgs.value = priv.messages || priv.private_log || []
    total.value = Number(log.total ?? (log.log || []).length)
    let lore: LoreKeywords | undefined
    if (detail.world_id) {
      try {
        const lb = await api<LorebookResponse>(`/lorebook/${encodeURIComponent(detail.world_id)}`)
        lore = buildLore(lb.entries || [])
      } catch { lore = undefined }
    }
    data.value = { ...log, _lore: lore }
  } catch (e: unknown) { error.value = errorMessage(e) }
}
onMounted(load)

function setPageSize(value: number | string) {
  pageSize.value = Number(value) || 10
  page.value = 1
  expandedRounds.value = new Set()
  load()
}

function actionsOf(entry: LogEntry): LogAction[] {
  const raw = entry.player_actions || entry.actions || []
  if (Array.isArray(raw)) {
    return raw.map(item => {
      const action = record(item)
      return { uid: String(action.user_id || ''), text: String(action.text || action.action || item) }
    })
  }
  if (raw && typeof raw === 'object') return Object.entries(raw).map(([uid, text]) => ({ uid, text: String(text) }))
  return []
}

const logs = computed(() => {
  const lore = data.value._lore
  return (data.value.log || []).map((e, i) => ({
    e, i, round: e.round ?? i,
    swipes: e.swipes && e.swipes.length > 1 ? (e.current_swipe || 0) + 1 + '/' + e.swipes.length : '',
    actions: actionsOf(e),
    gm: e.gm_response ? parseGMText(String(e.gm_response), lore) : null,
    gmLength: String(e.gm_response || '').length,
    tags: e.tags_summary,
  }))
})
const proclog = computed(() => logs.value.slice().reverse())
const totalPages = computed(() => Math.max(1, Number(data.value.total_pages) || Math.ceil(total.value / pageSize.value) || 1))
function goPage(n: number) {
  if (n < 1 || n > totalPages.value || n === page.value) return
  page.value = n
  expandedRounds.value = new Set()
  load()
}

function isExpanded(round: number) {
  return expandedRounds.value.has(round)
}
function toggleExpanded(round: number) {
  const next = new Set(expandedRounds.value)
  if (next.has(round)) next.delete(round)
  else next.add(round)
  expandedRounds.value = next
}
function isLongLog(item: { gmLength: number; gm: ReturnType<typeof parseGMText> | null; actions: LogAction[] }) {
  return item.gmLength > 900 || (item.gm?.paragraphs.length || 0) > 4 || item.actions.some(a => a.text.length > 260)
}

const statusChips = computed(() => {
  const s = healthData.value?.status
  if (!s || typeof s !== 'object') return []
  return Object.entries(s).map(([k, v]) => ({ key: k, val: String(v) }))
})
const recentHealth = computed(() => [...(healthData.value?.events || [])].reverse().slice(0, 10))
const recentPrivate = computed(() => [...privateMsgs.value].reverse().slice(0, 5))
const hasSystem = computed(() => statusChips.value.length || recentHealth.value.length || recentPrivate.value.length)
</script>

<template>
  <section class="view archive-page logs-page">
    <header class="view-title archive-hero">
      <div>
        <h1>游戏日志</h1>
        <p v-if="game">当前存档：{{ game }}</p>
        <p v-else class="muted">未选择存档，请在游玩页进入一局游戏。</p>
      </div>
      <button @click="load">刷新</button>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div v-if="hasSystem" class="lore-system">
      <h3>系统记录</h3>
      <div v-if="statusChips.length" class="status-tags">
        <span v-for="c in statusChips" :key="c.key" class="status-chip"><strong>{{ c.key }}</strong> {{ c.val }}</span>
      </div>
      <div v-if="recentHealth.length" class="events">
        <div v-for="(ev, i) in recentHealth" :key="i" class="event" :class="{ warn: ev.severity === 'warn' || ev.severity === 'error' }">
          <span class="ev-title">[R{{ ev.round }}] {{ ev.title }}</span>{{ ev.message }}
        </div>
      </div>
      <div v-if="recentPrivate.length">
        <p class="muted" style="margin: 8px 0 4px">GM 悄悄话</p>
        <div v-for="(m, i) in recentPrivate" :key="i" class="quiet">
          [R{{ m.round }}] {{ m.character_name || m.user_id }}：{{ m.text }}
        </div>
      </div>
    </div>

    <div class="mode-tabs">
      <button type="button" :class="{ active: tab === 'log' }" @click="tab = 'log'">对话日志</button>
      <button type="button" :class="{ active: tab === 'proclog' }" @click="tab = 'proclog'">处理日志</button>
    </div>
    <div class="log-toolbar">
      <span>默认只加载当前页，避免长团日志一次性撑爆页面。</span>
      <label>每页
        <select :value="pageSize" @change="setPageSize(($event.target as HTMLSelectElement).value)">
          <option :value="10">10 轮</option>
          <option :value="20">20 轮</option>
          <option :value="50">50 轮</option>
        </select>
      </label>
    </div>

    <div v-if="tab === 'log'" class="log-reader">
      <article v-for="item in logs" :key="item.round">
        <h2>第 {{ item.round }} 轮<span v-if="item.swipes" class="muted"> · {{ item.swipes }} 分支</span></h2>
        <div class="log-entry-body" :class="{ collapsed: isLongLog(item) && !isExpanded(item.round) }">
          <div v-for="a in item.actions" :key="a.uid + a.text" class="log-action">
            <strong class="log-actor">{{ a.uid }}</strong> {{ a.text }}
          </div>
          <template v-if="item.gm">
            <div v-for="(p, j) in item.gm.paragraphs" :key="'p' + j" class="chat-gm" v-html="p"></div>
            <div v-if="item.gm.states.length" class="state-card-list">
              <div v-for="(s, j) in item.gm.states" :key="'s' + j" class="state-card" :class="s.cls">
                <div class="state-card-title">{{ s.title }}</div>
                <div class="state-card-body" v-html="s.body"></div>
              </div>
            </div>
            <div v-if="item.gm.tags.length" class="tag-line">
              <span v-for="(t, j) in item.gm.tags" :key="'t' + j" class="tag-badge" :class="t.cls">{{ t.text }}</span>
            </div>
          </template>
        </div>
        <button v-if="isLongLog(item)" class="log-expand" @click="toggleExpanded(item.round)">
          {{ isExpanded(item.round) ? '收起本轮' : '展开本轮完整内容' }}
        </button>
      </article>
      <p v-if="!logs.length" class="muted">暂无日志。</p>
    </div>

    <div v-else class="console-log">
      <div v-for="(item, i) in proclog" :key="item.round ?? i" class="proc-line">
        <span class="muted">trpg@round-{{ String(item.round).padStart(3, '0') }}</span>
        <span class="cmd">$ parse llm-tags</span>
        <template v-if="item.tags && item.tags.has_tags">
          <br><span class="ok">[OK]</span> {{ item.tags.count || (item.tags.tags || []).length }} tags
          <span v-for="(t, j) in item.tags.tags || []" :key="j"><br>  <span class="tag-badge">{{ t }}</span></span>
        </template>
        <template v-else><br><span class="warn">[WARN]</span> no state tags emitted</template>
      </div>
      <p v-if="!proclog.length" class="muted">暂无日志。</p>
    </div>

    <nav v-if="totalPages > 1" class="memory-pager">
      <button :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 轮</span>
      <button :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
    </nav>
  </section>
</template>
