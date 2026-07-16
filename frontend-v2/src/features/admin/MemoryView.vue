<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, errorMessage } from '@/api/client'
import type { MemoriesResponse, MemoryEntry } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import Modal from '@/components/ui/Modal.vue'

const { confirm } = useConfirm()
const toast = useToast()

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

const memories = computed<MemoryEntry[]>(() => data.value.memories || data.value.entries || [])
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * pageSize + 1)
const rangeEnd = computed(() => Math.min(page.value * pageSize, total.value))

async function load() {
  if (!game.value) { data.value = { memories: [] }; total.value = 0; return }
  error.value = ''
  busy.value = true
  try {
    const params = new URLSearchParams()
    if (searchKeyword.value) params.set('keyword', searchKeyword.value)
    params.set('limit', String(pageSize))
    params.set('offset', String((page.value - 1) * pageSize))
    data.value = await api<MemoriesResponse>(`/games/${encodeURIComponent(game.value)}/memories?${params}`)
    total.value = Number(data.value.total ?? (data.value.memories || data.value.entries || []).length)
    if (page.value > totalPages.value) page.value = totalPages.value
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
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
function memoryTitle(item: MemoryEntry) { return item.entity || item.title || item.type || '记忆' }
function memoryBody(item: MemoryEntry) { return item.value || item.content || item.text || item.summary || '' }

async function saveMemory() {
  if (!memoryEdit.value?.id || !game.value) return
  busy.value = true
  try {
    await api<unknown>(`/games/${encodeURIComponent(game.value)}/memories/${encodeURIComponent(memoryEdit.value.id)}`, { method: 'PUT', body: JSON.stringify(memoryEdit.value) })
    memoryEdit.value = null
    await load()
    toast.success('记忆已修正')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteMemory(item: MemoryEntry) {
  if (!game.value || !item.id) return
  const ok = await confirm({ title: '遗忘记忆', content: '确定遗忘这条长期记忆吗？', positiveText: '遗忘记忆', negativeText: '取消', type: 'warning' })
  if (!ok) return
  busy.value = true
  try {
    await api<unknown>(`/games/${encodeURIComponent(game.value)}/memories/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
    await load()
    toast.success('已遗忘')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="view archive-page memory-page">
    <header class="archive-hero">
      <div>
        <span class="section-kicker">长期记忆</span>
        <h1>记忆档案</h1>
        <p v-if="game">当前存档：{{ game }}</p>
        <p v-else class="muted">未选择存档，请在游玩页进入一局游戏。</p>
      </div>
      <div class="memory-search">
        <input v-model="keyword" placeholder="按实体名搜索" @keyup.enter="search" :disabled="!game" />
        <button @click="search" :disabled="!game || busy">搜索</button>
        <button v-if="searchKeyword" class="ghost" @click="clearSearch" :disabled="busy">清除</button>
        <button @click="load" :disabled="busy">刷新</button>
      </div>
    </header>

    <p class="memory-meta" v-if="game">共 {{ total }} 条记忆，显示第 {{ rangeStart }}-{{ rangeEnd }} 条<span v-if="searchKeyword"> · 关键词「{{ searchKeyword }}」</span></p>
    <p v-if="error" class="error-banner">{{ error }}</p>

    <div v-if="memories.length" class="memory-list">
      <article v-for="m in memories" :key="m.id || memoryTitle(m)" class="memory-row">
        <div class="memory-row-main">
          <div class="memory-row-head">
            <strong>{{ memoryTitle(m) }}</strong>
            <span v-if="m.relation" class="muted small">{{ m.relation }}</span>
            <span v-if="m.confidence !== undefined && m.confidence !== null" class="badge" :class="{ low: Number(m.confidence) < 0.5 }">置信 {{ Number(m.confidence).toFixed(2) }}</span>
          </div>
          <p class="memory-row-body">{{ memoryBody(m) }}</p>
        </div>
        <div class="memory-row-actions">
          <button @click="openMemory(m)">修正</button>
          <button class="danger" :disabled="busy" @click="deleteMemory(m)">遗忘</button>
        </div>
      </article>
    </div>

    <section v-else-if="game && !busy" class="empty-panel">
      <h2>暂无长期记忆</h2>
      <p class="muted">剧情推进后，角色关系、地点线索和重要事实会在这里沉淀。</p>
    </section>

    <nav v-if="totalPages > 1" class="memory-pager">
      <button :disabled="page <= 1 || busy" @click="goPage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页</span>
      <button :disabled="page >= totalPages || busy" @click="goPage(page + 1)">下一页</button>
    </nav>

    <Modal v-if="memoryEdit" title="修正记忆" @close="memoryEdit = null">
      <label>实体<input v-model="memoryEdit.entity"></label>
      <label>关系<input v-model="memoryEdit.relation"></label>
      <label>内容<textarea rows="6" v-model="memoryEdit.value"></textarea></label>
      <label>置信度<input type="number" min="0" max="1" step="0.05" v-model="memoryEdit.confidence"></label>
      <template #actions>
        <button @click="memoryEdit = null">取消</button>
        <button class="primary" :disabled="busy" @click="saveMemory">保存</button>
      </template>
    </Modal>
  </section>
</template>
