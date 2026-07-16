<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import type { GameSummary, GamesResponse, LorebookResponse, LoreEntry, LoreGenerateResponse, WorldCreateResponse, WorldListResponse, WorldSummary } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import Modal from '@/components/ui/Modal.vue'

interface LoreEdit extends LoreEntry {
  tier?: string
  content?: string
  match_mode?: string
  unreliable?: boolean
  sync_on_enter?: boolean
  is_constant?: boolean
  triggers_recursive?: string[]
  visible_to?: string[]
  connected_to?: string[]
  sticky?: number
  cooldown?: number
  delay?: number
  order?: number
  probability?: number
  group?: string
  group_weight?: number
}

const toast = useToast()
const { confirm } = useConfirm()

const game = ref(readCurrentGame())
const worlds = ref<WorldSummary[]>([])
const currentWorldId = ref('')
const data = ref<LorebookResponse>({ entries: [] })
const error = ref('')
const busy = ref(false)
const loreEdit = ref<LoreEdit | null>(null)
const generatePrompt = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const showNewWorld = ref(false)
const newWorld = ref({ name: '', description: '' })
const entries = computed(() => data.value.entries || [])
const currentWorld = computed(() => worlds.value.find(w => worldIdOf(w) === currentWorldId.value))

function worldIdOf(w: WorldSummary | undefined): string { return String(w?.id || w?.world_id || '') }
function worldNameOf(w: WorldSummary | undefined): string { return String(w?.name || w?.world_name || w?.id || '') }
function cloneLore(entry: LoreEntry): LoreEdit { return JSON.parse(JSON.stringify(entry)) as LoreEdit }

async function loadWorlds() {
  error.value = ''
  try {
    const r = await api<WorldListResponse>('/worlds')
    worlds.value = r.worlds || []
    if (game.value) {
      const games = await api<GamesResponse>('/games')
      const cur = (games.games || []).find((g: GameSummary) => g.game_key === game.value)
      if (cur?.world_id) currentWorldId.value = cur.world_id
    }
    if (!currentWorldId.value && worlds.value.length) currentWorldId.value = worldIdOf(worlds.value[0])
  } catch (e: unknown) { error.value = errorMessage(e) }
}

watch(currentWorldId, () => { if (currentWorldId.value) loadLore() })

async function loadLore() {
  if (!currentWorldId.value) { data.value = { entries: [] }; return }
  error.value = ''; data.value = { entries: [] }
  try {
    data.value = await api<LorebookResponse>(`/lorebook/${encodeURIComponent(currentWorldId.value)}`)
  } catch (e: unknown) { error.value = errorMessage(e) }
}

onMounted(loadWorlds)

function openLore(entry?: LoreEntry) {
  loreEdit.value = entry ? cloneLore(entry) : {
    name: '', type: 'npc', tier: 'background', keywords: [], content: '',
    match_mode: 'any', unreliable: false, sync_on_enter: false, is_constant: false,
    triggers_recursive: [], visible_to: [], connected_to: [], sticky: 0,
    cooldown: 0, delay: 0, order: 100, probability: 100, group: '', group_weight: 1,
  }
}

function arrText(a: unknown) { return Array.isArray(a) ? a.join('、') : '' }
function typeLabel(type: string | undefined) {
  const labels: Record<string, string> = { npc: 'NPC', location: '地点', faction: '势力', item: '物品', event: '事件', puzzle: '谜题', other: '其他' }
  return labels[String(type || '')] || String(type || '条目')
}
function tierLabel(tier: string | undefined) {
  const labels: Record<string, string> = { core: '核心', background: '背景', archived: '归档' }
  return labels[String(tier || '')] || String(tier || '背景')
}
function loreBody(entry: LoreEntry) { return String(entry.content || '').trim() || '暂无内容' }
function loreKeywords(entry: LoreEntry) { return arrText(entry.keywords).slice(0, 80) }
function loreConnections(entry: LoreEntry) { return arrText((entry as LoreEdit).connected_to).slice(0, 80) }
function setArr(field: keyof LoreEdit, e: Event) {
  const v = (e.target as HTMLInputElement).value.split(/[,，、]/).map(x => x.trim()).filter(Boolean)
  if (loreEdit.value) (loreEdit.value as Record<string, unknown>)[field] = v
}

async function saveLore() {
  if (!loreEdit.value) return
  const entry: LoreEdit = { ...loreEdit.value, world_id: currentWorldId.value }
  const path = entry.id ? `/lorebook/${encodeURIComponent(entry.id)}` : '/lorebook'
  try {
    await api<unknown>(path, { method: entry.id ? 'PUT' : 'POST', body: JSON.stringify(entry) })
    toast.success(entry.id ? '已更新' : '已新增')
    loreEdit.value = null
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function deleteLore(entry: LoreEntry) {
  if (!entry.id) return
  const ok = await confirm({ title: '删除条目', content: `确定删除世界书条目「${entry.name}」吗？`, positiveText: '删除条目', type: 'error' })
  if (!ok) return
  try {
    await api<unknown>(`/lorebook/${encodeURIComponent(entry.id)}`, { method: 'DELETE' })
    toast.success('已删除')
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function generateLore() {
  if (!generatePrompt.value.trim()) { toast.error('请输入生成描述'); return }
  busy.value = true
  try {
    const r = await api<LoreGenerateResponse>(`/lorebook/${encodeURIComponent(currentWorldId.value)}/generate`, { method: 'POST', body: JSON.stringify({ prompt: generatePrompt.value }) })
    toast.success(`AI 已生成 ${r.count || (r.entries?.length || 0)} 条`)
    generatePrompt.value = ''
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function createWorld() {
  if (!newWorld.value.name.trim()) { toast.error('请输入世界名'); return }
  busy.value = true
  try {
    const r = await api<WorldCreateResponse>('/worlds', { method: 'POST', body: JSON.stringify({ name: newWorld.value.name, description: newWorld.value.description }) })
    if (!r.ok) throw new Error(r.error || '创建失败')
    toast.success('已创建世界')
    newWorld.value = { name: '', description: '' }
    showNewWorld.value = false
    await loadWorlds()
    if (r.world_id || r.id) currentWorldId.value = String(r.world_id || r.id)
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteWorld() {
  if (!currentWorldId.value) return
  const w = worlds.value.find(x => worldIdOf(x) === currentWorldId.value)
  const ok = await confirm({ title: '删除世界', content: `确定删除世界「${worldNameOf(w) || currentWorldId.value}」及其全部条目吗？此操作不可撤销。`, positiveText: '删除世界', type: 'error' })
  if (!ok) return
  try {
    await api<unknown>(`/worlds/${encodeURIComponent(currentWorldId.value)}`, { method: 'DELETE' })
    toast.success('已删除世界')
    currentWorldId.value = ''
    await loadWorlds()
    if (worlds.value.length) currentWorldId.value = worldIdOf(worlds.value[0])
    else data.value = { entries: [] }
  } catch (e: unknown) { error.value = errorMessage(e) }
}

function exportLore() {
  const entries = data.value.entries || []
  const blob = new Blob([JSON.stringify(entries, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `lorebook_${currentWorldId.value}.json`
  a.click()
  URL.revokeObjectURL(url)
  toast.success('已导出')
}

async function importLore(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const entries = JSON.parse(text) as unknown
    if (!Array.isArray(entries)) throw new Error('JSON 格式错误：需要数组')
    for (const en of entries) {
      if (!en || typeof en !== 'object') continue
      await api<unknown>('/lorebook', { method: 'POST', body: JSON.stringify({ ...en, world_id: currentWorldId.value, id: undefined }) })
    }
    toast.success(`已导入 ${entries.length} 条`)
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = '导入失败: ' + errorMessage(e) } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}
</script>

<template>
  <section class="view archive-page lorebook-page">
    <header class="view-title archive-hero">
      <div>
        <span class="section-kicker">世界书</span>
        <h1>世界书</h1>
        <p v-if="game">当前存档：{{ game }}</p>
        <p v-else class="muted">独立管理世界书条目。</p>
      </div>
      <button @click="loadWorlds">刷新</button>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div class="lore-world-bar">
      <select v-model="currentWorldId">
        <option value="" disabled>选择世界…</option>
        <option v-for="w in worlds" :key="w.id" :value="w.id">{{ w.name }}（{{ w.entry_count || 0 }} 条）</option>
      </select>
      <button class="primary" @click="showNewWorld = !showNewWorld">+ 新建世界</button>
      <button v-if="currentWorldId" class="danger" @click="deleteWorld" :disabled="busy">删除世界</button>
    </div>

    <details v-if="showNewWorld" class="ai-block" open>
      <summary>新建世界</summary>
      <label>世界名<input v-model="newWorld.name" placeholder="为新世界命名"></label>
      <label>说明<textarea rows="2" v-model="newWorld.description"></textarea></label>
      <div class="actions"><button @click="showNewWorld = false">取消</button><button class="primary" :disabled="busy" @click="createWorld">创建</button></div>
    </details>

    <div class="lore-tools">
      <button class="primary" :disabled="!currentWorldId" @click="openLore()">新增条目</button>
      <input v-model="generatePrompt" placeholder="用自然语言批量生成世界书">
      <button @click="generateLore" :disabled="busy || !currentWorldId">AI 生成</button>
      <button @click="exportLore" :disabled="!data?.entries?.length">导出</button>
      <button @click="fileInput?.click()" :disabled="!currentWorldId">导入</button>
      <input ref="fileInput" type="file" accept="application/json" @change="importLore" hidden>
    </div>

    <p class="memory-meta" v-if="currentWorldId">
      {{ worldNameOf(currentWorld) || currentWorldId }} · 共 {{ entries.length }} 条世界书
    </p>

    <div v-if="entries.length" class="memory-list lore-list">
      <article v-for="e in entries" :key="e.id || e.name" class="memory-row lore-row">
        <div class="memory-row-main">
          <div class="memory-row-head">
            <strong>{{ e.name || '未命名条目' }}</strong>
            <span class="badge">{{ typeLabel(e.type) }}</span>
            <span class="badge" :class="{ low: e.tier === 'archived' }">{{ tierLabel(e.tier) }}</span>
            <span v-if="e.unreliable" class="badge low">不可靠</span>
            <span v-if="e.is_constant" class="badge">常驻</span>
          </div>
          <p class="memory-row-body">{{ loreBody(e) }}</p>
          <p v-if="loreKeywords(e) || loreConnections(e)" class="muted small lore-row-extra">
            <span v-if="loreKeywords(e)">关键词：{{ loreKeywords(e) }}</span>
            <span v-if="loreConnections(e)">关联：{{ loreConnections(e) }}</span>
          </p>
        </div>
        <div class="memory-row-actions">
          <button @click="openLore(e)">编辑</button>
          <button class="danger" @click="deleteLore(e)">删除</button>
        </div>
      </article>
    </div>

    <section v-else-if="currentWorldId && !busy" class="empty-panel">
      <h2>暂无世界书条目</h2>
      <p class="muted">可以新增条目，或用自然语言批量生成地点、人物、势力和事件。</p>
    </section>

    <Modal v-if="loreEdit" :title="(loreEdit.id ? '编辑' : '新增') + '世界书条目'" @close="loreEdit = null">
      <label>名称<input v-model="loreEdit.name"></label>
      <label>类型<select v-model="loreEdit.type"><option value="npc">NPC</option><option value="location">地点</option><option value="faction">势力</option><option value="item">物品</option><option value="event">事件</option><option value="puzzle">谜题</option><option value="other">其他</option></select></label>
      <label>层级<select v-model="loreEdit.tier"><option value="core">核心</option><option value="background">背景</option><option value="archived">归档</option></select></label>
      <label>关键词<input :value="arrText(loreEdit.keywords)" @input="setArr('keywords', $event)" placeholder="逗号分隔，命中即激活"></label>
      <label>内容<textarea rows="6" v-model="loreEdit.content"></textarea></label>
      <label>关键词匹配模式<select v-model="loreEdit.match_mode"><option value="any">任一命中（默认）</option><option value="all">全部命中</option><option value="not_any">全部不命中(NOT)</option><option value="not_all">非全部命中</option></select></label>
      <div class="check-row"><label><input type="checkbox" v-model="loreEdit.unreliable">不可靠记忆</label><label><input type="checkbox" v-model="loreEdit.sync_on_enter">进入时同步</label><label><input type="checkbox" v-model="loreEdit.is_constant">常驻</label></div>
      <label>递归触发<input :value="arrText(loreEdit.triggers_recursive)" @input="setArr('triggers_recursive', $event)" placeholder="逗号分隔，命中后额外激活的条目"></label>
      <label>可见角色<input :value="arrText(loreEdit.visible_to)" @input="setArr('visible_to', $event)" placeholder="逗号分隔，留空则全员可见"></label>
      <label>关联条目<input :value="arrText(loreEdit.connected_to)" @input="setArr('connected_to', $event)" placeholder="逗号分隔，关联条目 id"></label>
      <div class="grid-2"><label>置顶轮数<input type="number" v-model.number="loreEdit.sticky"></label><label>冷却<input type="number" v-model.number="loreEdit.cooldown"></label></div>
      <div class="grid-2"><label>延迟<input type="number" v-model.number="loreEdit.delay"></label><label>顺序<input type="number" v-model.number="loreEdit.order"></label></div>
      <div class="grid-2"><label>概率 (%)<input type="number" v-model.number="loreEdit.probability"></label><label>分组<input v-model="loreEdit.group"></label></div>
      <label>组内权重<input type="number" v-model.number="loreEdit.group_weight"></label>
      <template #actions><button @click="loreEdit = null">取消</button><button class="primary" @click="saveLore">保存</button></template>
    </Modal>
  </section>
</template>
