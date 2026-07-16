<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, apiBlob, errorMessage } from '@/api/client'
import type { BatchDeleteGamesResponse, GameMutationResponse, GamesResponse, GameSummary } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import { clearCurrentGame, rememberCurrentGame } from '@/stores/gameContext'

const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()
const { t } = useLocale()

const games = ref<GameSummary[]>([])
const error = ref('')
function setError(e: unknown) { error.value = errorMessage(e) }
const busy = ref(false)
const selected = ref<string[]>([])

const activeGames = computed(() => games.value.filter(g => stateClass(g.state) === 'badge-active').length)
const playerCount = computed(() => games.value.reduce((sum, g) => sum + Number(g.player_count || 0), 0))
const roundCount = computed(() => games.value.reduce((sum, g) => sum + Number(g.round_number || 0), 0))
const latestScene = computed(() => games.value.find(g => g.scene)?.scene || t('noScene'))

async function load() {
  error.value = ''
  try {
    const r = await api<GamesResponse>('/games')
    games.value = r.games || []
  } catch (e: unknown) { setError(e) }
}

function play(key: string) {
  if (key) {
    const g = games.value.find(item => item.game_key === key)
    rememberCurrentGame(key, g?.world_name || '')
    router.push({ name: 'play', query: { game: key } })
  } else router.push({ name: 'create' })
}

async function remove(key: string) {
  const ok = await confirm({ title: t('deleteSaveTitle'), content: t('deleteSaveContent'), positiveText: t('deleteSaveTitle'), type: 'error' })
  if (!ok) return
  busy.value = true
  try {
    await api<unknown>(`/games/${encodeURIComponent(key)}`, { method: 'DELETE' })
    toast.success(t('deleted'))
    clearCurrentGame(key)
    await load()
  } catch (e: unknown) { setError(e) } finally { busy.value = false }
}

async function exportGame(key: string) {
  try {
    const r = await apiBlob(`/games/${encodeURIComponent(key)}/export`)
    const blob = await r.blob()
    const dispo = r.headers.get('Content-Disposition') || ''
    const m = dispo.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
    const baseName = games.value.find(g => g.game_key === key)?.world_name || 'save'
    const filename = m ? decodeURIComponent(m[1]) : `${baseName.replace(/[^A-Za-z0-9_\-]/g, '_')}.json`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
    toast.success(t('exported'))
  } catch (e: unknown) { setError(e) }
}

async function exportAll() {
  if (!games.value.length) { toast.info(t('noSavesToExport')); return }
  toast.info(`${t('exportStarting')} ${games.value.length}...`)
  for (const g of games.value) {
    await exportGame(g.game_key)
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  toast.success(t('exportDone'))
}

async function resetGame(key: string) {
  const ok = await confirm({ title: t('resetTitle'), content: t('resetContent'), positiveText: t('resetTitle'), type: 'warning' })
  if (!ok) return
  busy.value = true
  try {
    const r = await api<GameMutationResponse>(`/games/${encodeURIComponent(key)}/reset`, { method: 'POST' })
    if (!r.ok) throw new Error(r.error || t('resetFailed'))
    toast.success(`${t('resetDone')} ${r.seed_code || ''}`)
    await load()
  } catch (e: unknown) { setError(e) } finally { busy.value = false }
}

async function restartGame(key: string) {
  const ok = await confirm({ title: t('restartTitle'), content: t('restartContent'), positiveText: t('restartTitle'), type: 'warning' })
  if (!ok) return
  busy.value = true
  try {
    const r = await api<GameMutationResponse>(`/games/${encodeURIComponent(key)}/restart`, { method: 'POST' })
    if (!r.ok) throw new Error(r.error || t('restartFailed'))
    toast.success(`${t('restartDone')} ${r.seed_code || ''}`)
    await load()
  } catch (e: unknown) { setError(e) } finally { busy.value = false }
}

async function batchRemove() {
  if (!selected.value.length) return
  const ok = await confirm({ title: t('batchDeleteTitle'), content: t('batchDeleteContent', { count: selected.value.length }), positiveText: t('deleteSelected'), type: 'error' })
  if (!ok) return
  busy.value = true
  try {
    const r = await api<BatchDeleteGamesResponse>('/games/batch-delete', { method: 'POST', body: JSON.stringify({ game_keys: selected.value }) })
    const deleted = r.deleted?.length || 0
    const failed = r.failed?.length || 0
    toast.success(t('batchDeleted', { deleted, failed: failed ? t('failedCount', { count: failed }) : '' }))
    for (const key of selected.value) clearCurrentGame(key)
    selected.value = []
    await load()
  } catch (e: unknown) { setError(e) } finally { busy.value = false }
}

function selectAll() { selected.value = games.value.map(g => g.game_key) }
function selectInvert() {
  const set = new Set(selected.value)
  selected.value = games.value.filter(g => !set.has(g.game_key)).map(g => g.game_key)
}

function stateClass(s?: string) {
  return (s === 'active_action' || s === 'active_judgment' || s === 'waiting') ? 'badge-active' : 'badge-ended'
}
function stateLabel(s?: string) {
  const labels: Record<string, string> = {
    active_action: t('stateActiveAction'),
    active_judgment: t('stateActiveJudgment'),
    waiting: t('stateWaiting'),
    ended: t('stateEnded'),
    paused: t('statePaused'),
    creating: t('stateCreating'),
  }
  return (s && labels[s]) || s || t('stateUnknown')
}

onMounted(load)
</script>

<template>
  <section class="view overview-page">
    <header class="overview-hero">
      <div>
        <span class="section-kicker">{{ t('overviewKicker') }}</span>
        <h1>{{ t('overviewTitle') }}</h1>
        <p>{{ t('overviewSubtitle') }}</p>
      </div>
      <div class="overview-actions">
        <button v-if="games.length" @click="selectAll">{{ t('selectAll') }}</button>
        <button v-if="games.length" @click="selectInvert">{{ t('invertSelection') }}</button>
        <button v-if="games.length" @click="exportAll" :disabled="busy">{{ t('exportAll') }}</button>
        <button v-if="selected.length" class="danger" @click="batchRemove" :disabled="busy">{{ t('deleteSelected') }} {{ selected.length }}</button>
        <button class="primary" @click="play('')">{{ t('createAdventure') }}</button>
      </div>
    </header>

    <section class="overview-stats" aria-label="存档统计">
      <article><strong>{{ games.length }}</strong><span>{{ t('totalSaves') }}</span></article>
      <article><strong>{{ activeGames }}</strong><span>{{ t('activeGames') }}</span></article>
      <article><strong>{{ playerCount }}</strong><span>{{ t('playerSlots') }}</span></article>
      <article><strong>{{ roundCount }}</strong><span>{{ t('totalRounds') }}</span></article>
      <article class="wide"><strong>{{ latestScene }}</strong><span>{{ t('latestScene') }}</span></article>
    </section>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div v-if="games.length" class="game-grid">
      <article v-for="g in games" :key="g.game_key" class="game-card">
        <header>
          <label class="game-select compact">
            <input type="checkbox" :value="g.game_key" v-model="selected" :aria-label="t('chooseSave')">
            <span>{{ t('choose') }}</span>
          </label>
          <div class="game-card-badges">
            <small class="badge" :class="stateClass(g.state)">{{ stateLabel(g.state) }}</small>
            <small v-if="g.solo_mode" class="badge badge-active">{{ t('solo') }}</small>
            <small v-else class="badge">{{ t('multiplayer') }}</small>
          </div>
        </header>
        <h2>{{ g.world_name || g.game_key }}</h2>
        <p class="scene-line">{{ g.scene || t('notStarted') }}</p>
        <div class="game-card-meta">
          <span>{{ t('roundPrefix') }}{{ g.round_number || 0 }}{{ t('roundSuffix') }}</span>
          <span>{{ t('players') }} {{ g.player_count || 0 }}/{{ g.max_players || 0 }}</span>
          <span>LLM {{ g.total_llm_calls || 0 }}</span>
        </div>
        <p class="muted meta">{{ t('token') }} {{ g.total_tokens || 0 }}<span v-if="g.seed_code"> · {{ t('seed') }} <code>{{ g.seed_code }}</code></span></p>
        <div class="game-card-actions">
          <button class="success game-card-enter" @click="play(g.game_key)">{{ t('enter') }}</button>
          <div class="game-card-tools">
            <button @click="exportGame(g.game_key)">{{ t('export') }}</button>
            <button @click="restartGame(g.game_key)" :disabled="busy">{{ t('restart') }}</button>
            <button @click="resetGame(g.game_key)" :disabled="busy">{{ t('reset') }}</button>
            <button class="danger" @click="remove(g.game_key)" :disabled="busy">{{ t('delete') }}</button>
          </div>
        </div>
      </article>
    </div>

    <section v-else class="empty-panel">
      <h2>{{ t('emptyTitle') }}</h2>
      <p class="muted">{{ t('emptySubtitle') }}</p>
      <button class="primary" @click="play('')">{{ t('createAdventure') }}</button>
    </section>
  </section>
</template>