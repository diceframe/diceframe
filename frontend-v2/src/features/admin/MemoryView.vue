<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { MemoriesResponse, MemoryEntry } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { useLocale } from '@/composables/useLocale'
import Modal from '@/components/ui/Modal.vue'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'

const { confirm } = useConfirm()
const toast = useToast()
const { t } = useLocale()
const router = useRouter()

const game = ref(readCurrentGame())
const data = ref<MemoriesResponse>({ memories: [] })
const error = ref('')
const busy = ref(false)
const memoryEdit = ref<MemoryEntry | null>(null)

const keyword = ref('')
const searchKeyword = ref('')
const page = ref(1)
const pageSize = 30
const total = ref(0)
const loadedProjectionKey = ref('')
let loadSequence = 0

function projectionKey(gameKey: string, pageNumber: number, keywordValue: string): string {
  return `${gameKey}\u0000${pageNumber}\u0000${keywordValue}`
}

const memories = computed<MemoryEntry[]>(() => data.value.memories || data.value.entries || [])
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * pageSize + 1)
const rangeEnd = computed(() => Math.min(page.value * pageSize, total.value))

async function load() {
  if (!game.value) { data.value = { memories: [] }; total.value = 0; return }
  const requestedGame = game.value
  const requestedProjectionKey = projectionKey(requestedGame, page.value, searchKeyword.value)
  if (loadedProjectionKey.value !== requestedProjectionKey) {
    data.value = { memories: [] }
    total.value = 0
  }
  const requestSequence = ++loadSequence
  error.value = ''
  busy.value = true
  try {
    const params = new URLSearchParams()
    if (searchKeyword.value) params.set('keyword', searchKeyword.value)
    params.set('limit', String(pageSize))
    params.set('offset', String((page.value - 1) * pageSize))
    const nextData = await api<MemoriesResponse>(`/games/${encodeURIComponent(requestedGame)}/memories?${params}`)
    if (game.value !== requestedGame || requestSequence !== loadSequence || projectionKey(game.value, page.value, searchKeyword.value) !== requestedProjectionKey) return
    data.value = nextData
    loadedProjectionKey.value = requestedProjectionKey
    total.value = Number(data.value.total ?? (data.value.memories || data.value.entries || []).length)
    if (page.value > totalPages.value) page.value = totalPages.value
  } catch (e: unknown) {
    if (game.value === requestedGame && requestSequence === loadSequence && projectionKey(game.value, page.value, searchKeyword.value) === requestedProjectionKey) error.value = errorMessage(e)
  } finally {
    if (requestSequence === loadSequence) busy.value = false
  }
}
onMounted(load)

function search() {
  searchKeyword.value = keyword.value.trim()
  page.value = 1
  load()
}
function clearSearch() {
  keyword.value = ''
  searchKeyword.value = ''
  page.value = 1
  load()
}
function goPage(n: number) {
  if (n < 1 || n > totalPages.value || n === page.value) return
  page.value = n
  load()
}

function cloneMemory(item: MemoryEntry): MemoryEntry { return JSON.parse(JSON.stringify(item)) as MemoryEntry }
function openMemory(item: MemoryEntry) { memoryEdit.value = cloneMemory(item) }
function memoryTitle(item: MemoryEntry) { return item.entity || item.title || item.type || t('memoryFallback') }
function memoryBody(item: MemoryEntry) { return item.value || item.content || item.text || item.summary || '' }

async function saveMemory() {
  if (!memoryEdit.value?.id || !game.value) return
  busy.value = true
  try {
    await api<unknown>(`/games/${encodeURIComponent(game.value)}/memories/${encodeURIComponent(memoryEdit.value.id)}`, { method: 'PUT', body: JSON.stringify(memoryEdit.value) })
    memoryEdit.value = null
    await load()
    toast.success(t('memoryCorrected'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteMemory(item: MemoryEntry) {
  if (!game.value || !item.id) return
  const ok = await confirm({ title: t('forgetMemoryTitle'), content: t('forgetMemoryContent'), positiveText: t('forgetMemoryAction'), negativeText: t('cancel'), type: 'warning' })
  if (!ok) return
  busy.value = true
  try {
    await api<unknown>(`/games/${encodeURIComponent(game.value)}/memories/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
    await load()
    toast.success(t('forgotten'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="view archive-page memory-page">
    <ContentPageHeader compact class="memory-page-heading" kicker="CAMPAIGN MEMORY" :title="t('memoryArchive')" :description="game ? `${t('currentSave')}: ${game}` : t('noSaveSelectedHint')">
      <template #actions>
        <div class="memory-search">
          <input v-model="keyword" :placeholder="t('searchByEntity')" @keyup.enter="search" :disabled="!game" />
          <button @click="search" :disabled="!game || busy">{{ t('search') }}</button>
          <button v-if="searchKeyword" class="ghost" @click="clearSearch" :disabled="busy">{{ t('clear') }}</button>
          <button @click="load" :disabled="busy">{{ t('refresh') }}</button>
        </div>
      </template>
    </ContentPageHeader>

    <p class="memory-meta" v-if="game">{{ t('memoryMeta', { total, start: rangeStart, end: rangeEnd }) }}<span v-if="searchKeyword"> · {{ t('keywords') }} "{{ searchKeyword }}"</span></p>
    <div v-if="error" class="error-banner memory-error" role="alert">
      <span>{{ error }}</span>
      <button type="button" class="ghost" :disabled="busy" @click="load">{{ t('retry') }}</button>
    </div>

    <div v-if="memories.length" class="memory-list">
      <article v-for="m in memories" :key="m.id || memoryTitle(m)" class="memory-row">
        <div class="memory-row-main">
          <div class="memory-row-head">
            <strong>{{ memoryTitle(m) }}</strong>
            <span v-if="m.relation" class="muted small">{{ m.relation }}</span>
            <span v-if="m.confidence !== undefined && m.confidence !== null" class="badge" :class="{ low: Number(m.confidence) < 0.5 }">{{ t('confidence') }} {{ Number(m.confidence).toFixed(2) }}</span>
          </div>
          <p class="memory-row-body">{{ memoryBody(m) }}</p>
        </div>
        <div class="memory-row-actions">
          <button @click="openMemory(m)">{{ t('correct') }}</button>
          <button class="danger" :disabled="busy" @click="deleteMemory(m)">{{ t('forget') }}</button>
        </div>
      </article>
    </div>

    <section v-else-if="game && !busy && !error" class="empty-panel">
      <h2>{{ t('noLongTermMemory') }}</h2>
      <p class="muted">{{ t('noLongTermMemoryHint') }}</p>
    </section>

    <section v-else-if="!game" class="empty-panel empty-game" data-testid="memory-no-game">
      <h2>{{ t('memoryArchive') }}</h2>
      <p class="muted">{{ t('noSaveSelectedHint') }}</p>
      <button type="button" class="primary" @click="router.push({ name: 'overview' })">{{ t('viewSaves') }}</button>
    </section>

    <nav v-if="totalPages > 1" class="memory-pager">
      <button :disabled="page <= 1 || busy" @click="goPage(page - 1)">{{ t('previousPage') }}</button>
      <span>{{ t('pageOf', { page, total: totalPages }) }}</span>
      <button :disabled="page >= totalPages || busy" @click="goPage(page + 1)">{{ t('nextPage') }}</button>
    </nav>

    <Modal v-if="memoryEdit" :title="t('correctMemory')" @close="memoryEdit = null">
      <label>{{ t('entity') }}<input v-model="memoryEdit.entity"></label>
      <label>{{ t('relation') }}<input v-model="memoryEdit.relation"></label>
      <label>{{ t('content') }}<textarea rows="6" v-model="memoryEdit.value"></textarea></label>
      <label>{{ t('confidenceScore') }}<input type="number" min="0" max="1" step="0.05" v-model="memoryEdit.confidence"></label>
      <template #actions>
        <button @click="memoryEdit = null">{{ t('cancel') }}</button>
        <button class="primary" :disabled="busy" @click="saveMemory">{{ t('saveAction') }}</button>
      </template>
    </Modal>
  </section>
</template>

<style scoped>
.memory-page-heading { padding-top: 10px !important; padding-bottom: 8px !important; }
.memory-page-heading :deep(h1) { font-size: 28px; }
</style>
