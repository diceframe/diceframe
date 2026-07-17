<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import type { GameSummary, GamesResponse, LorebookResponse, LoreEntry, LoreGenerateResponse, WorldCreateResponse, WorldListResponse, WorldSummary } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'
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
const { locale, t } = useLocale()

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
const newWorld = ref({ name: '', description: '', language: locale.value })
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

function arrText(a: unknown) { return Array.isArray(a) ? a.join(t('listSeparator')) : '' }
function typeLabel(type: string | undefined) {
  const labels: Record<string, MessageKey> = { npc: 'contentGroupNpc', location: 'loreTypeLocation', faction: 'loreTypeFaction', item: 'contentGroupItem', event: 'loreTypeEvent', puzzle: 'loreTypePuzzle', other: 'loreTypeOther' }
  const key = labels[String(type || '')]
  return key ? t(key) : String(type || t('loreEntry'))
}
function tierLabel(tier: string | undefined) {
  const labels: Record<string, MessageKey> = { core: 'core', background: 'background', archived: 'archived' }
  const key = labels[String(tier || '')]
  return key ? t(key) : String(tier || t('background'))
}
function loreBody(entry: LoreEntry) { return String(entry.content || '').trim() || t('noContent') }
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
    toast.success(entry.id ? t('updated') : t('created'))
    loreEdit.value = null
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function deleteLore(entry: LoreEntry) {
  if (!entry.id) return
  const ok = await confirm({ title: t('deleteLoreEntryTitle'), content: t('deleteLoreEntryContent', { name: entry.name || t('unnamedLoreEntry') }), positiveText: t('deleteLoreEntryAction'), type: 'error' })
  if (!ok) return
  try {
    await api<unknown>(`/lorebook/${encodeURIComponent(entry.id)}`, { method: 'DELETE' })
    toast.success(t('deleted'))
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function generateLore() {
  if (!generatePrompt.value.trim()) { toast.error(t('enterGenerationPrompt')); return }
  busy.value = true
  try {
    const r = await api<LoreGenerateResponse>(`/lorebook/${encodeURIComponent(currentWorldId.value)}/generate`, {
      method: 'POST',
      body: JSON.stringify({ prompt: generatePrompt.value, language: currentWorld.value?.language || locale.value }),
    })
    toast.success(t('aiGeneratedEntries', { count: r.count || (r.entries?.length || 0) }))
    generatePrompt.value = ''
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function createWorld() {
  if (!newWorld.value.name.trim()) { toast.error(t('enterWorldName')); return }
  busy.value = true
  try {
    const r = await api<WorldCreateResponse>('/worlds', { method: 'POST', body: JSON.stringify({ name: newWorld.value.name, description: newWorld.value.description, language: newWorld.value.language }) })
    if (!r.ok) throw new Error(r.error || t('createFailed'))
    toast.success(t('worldCreated'))
    newWorld.value = { name: '', description: '', language: locale.value }
    showNewWorld.value = false
    await loadWorlds()
    if (r.world_id || r.id) currentWorldId.value = String(r.world_id || r.id)
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteWorld() {
  if (!currentWorldId.value) return
  const w = worlds.value.find(x => worldIdOf(x) === currentWorldId.value)
  const ok = await confirm({ title: t('deleteWorldTitle'), content: t('deleteWorldContent', { name: worldNameOf(w) || currentWorldId.value }), positiveText: t('deleteWorldAction'), type: 'error' })
  if (!ok) return
  try {
    await api<unknown>(`/worlds/${encodeURIComponent(currentWorldId.value)}`, { method: 'DELETE' })
    toast.success(t('worldDeleted'))
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
  toast.success(t('exported'))
}

async function importLore(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const entries = JSON.parse(text) as unknown
    if (!Array.isArray(entries)) throw new Error(t('jsonArrayRequired'))
    for (const en of entries) {
      if (!en || typeof en !== 'object') continue
      await api<unknown>('/lorebook', { method: 'POST', body: JSON.stringify({ ...en, world_id: currentWorldId.value, id: undefined }) })
    }
    toast.success(t('importedEntries', { count: entries.length }))
    await loadLore()
    await loadWorlds()
  } catch (e: unknown) { error.value = `${t('importFailed')}: ${errorMessage(e)}` } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}
</script>

<template>
  <section class="view archive-page lorebook-page">
    <header class="view-title archive-hero">
      <div>
        <span class="section-kicker">{{ t('navLorebook') }}</span>
        <h1>{{ t('navLorebook') }}</h1>
        <p v-if="game">{{ t('currentSave') }}: {{ game }}</p>
        <p v-else class="muted">{{ t('standaloneLorebookHint') }}</p>
      </div>
      <button @click="loadWorlds">{{ t('refresh') }}</button>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div class="lore-world-bar">
      <select v-model="currentWorldId">
        <option value="" disabled>{{ t('chooseWorldEllipsis') }}</option>
        <option v-for="w in worlds" :key="w.id" :value="w.id">{{ w.name }} ({{ t('entriesCount', { count: w.entry_count || 0 }) }})</option>
      </select>
      <button class="primary" @click="showNewWorld = !showNewWorld">+ {{ t('newWorld') }}</button>
      <button v-if="currentWorldId" class="danger" @click="deleteWorld" :disabled="busy">{{ t('deleteWorldAction') }}</button>
    </div>

    <details v-if="showNewWorld" class="ai-block" open>
      <summary>{{ t('newWorld') }}</summary>
      <label>{{ t('worldName') }}<input v-model="newWorld.name" :placeholder="t('nameNewWorld')"></label>
      <label>{{ t('contentLanguage') }}
        <select v-model="newWorld.language">
          <option value="zh-CN">{{ t('chinese') }}</option>
          <option value="en">{{ t('english') }}</option>
        </select>
      </label>
      <label>{{ t('description') }}<textarea rows="2" v-model="newWorld.description"></textarea></label>
      <div class="actions"><button @click="showNewWorld = false">{{ t('cancel') }}</button><button class="primary" :disabled="busy" @click="createWorld">{{ t('create') }}</button></div>
    </details>

    <div class="lore-tools">
      <button class="primary" :disabled="!currentWorldId" @click="openLore()">{{ t('addLoreEntry') }}</button>
      <input v-model="generatePrompt" :placeholder="t('generateLorePlaceholder')">
      <button @click="generateLore" :disabled="busy || !currentWorldId">{{ t('aiGenerate') }}</button>
      <button @click="exportLore" :disabled="!data?.entries?.length">{{ t('export') }}</button>
      <button @click="fileInput?.click()" :disabled="!currentWorldId">{{ t('import') }}</button>
      <input ref="fileInput" type="file" accept="application/json" @change="importLore" hidden>
    </div>

    <p class="memory-meta" v-if="currentWorldId">
      {{ worldNameOf(currentWorld) || currentWorldId }} · {{ t('lorebookEntryCount', { count: entries.length }) }}
    </p>

    <div v-if="entries.length" class="memory-list lore-list">
      <article v-for="e in entries" :key="e.id || e.name" class="memory-row lore-row">
        <div class="memory-row-main">
          <div class="memory-row-head">
            <strong>{{ e.name || t('unnamedLoreEntry') }}</strong>
            <span class="badge">{{ typeLabel(e.type) }}</span>
            <span class="badge" :class="{ low: e.tier === 'archived' }">{{ tierLabel(e.tier) }}</span>
            <span v-if="e.unreliable" class="badge low">{{ t('unreliable') }}</span>
            <span v-if="e.is_constant" class="badge">{{ t('constant') }}</span>
          </div>
          <p class="memory-row-body">{{ loreBody(e) }}</p>
          <p v-if="loreKeywords(e) || loreConnections(e)" class="muted small lore-row-extra">
            <span v-if="loreKeywords(e)">{{ t('keywords') }}: {{ loreKeywords(e) }}</span>
            <span v-if="loreConnections(e)">{{ t('connections') }}: {{ loreConnections(e) }}</span>
          </p>
        </div>
        <div class="memory-row-actions">
          <button @click="openLore(e)">{{ t('edit') }}</button>
          <button class="danger" @click="deleteLore(e)">{{ t('delete') }}</button>
        </div>
      </article>
    </div>

    <section v-else-if="currentWorldId && !busy" class="empty-panel">
      <h2>{{ t('noLoreEntries') }}</h2>
      <p class="muted">{{ t('noLoreEntriesHint') }}</p>
    </section>

    <Modal v-if="loreEdit" :title="loreEdit.id ? t('editLoreEntry') : t('newLoreEntry')" @close="loreEdit = null">
      <label>{{ t('name') }}<input v-model="loreEdit.name"></label>
      <label>{{ t('type') }}<select v-model="loreEdit.type"><option value="npc">NPC</option><option value="location">{{ t('loreTypeLocation') }}</option><option value="faction">{{ t('loreTypeFaction') }}</option><option value="item">{{ t('contentGroupItem') }}</option><option value="event">{{ t('loreTypeEvent') }}</option><option value="puzzle">{{ t('loreTypePuzzle') }}</option><option value="other">{{ t('loreTypeOther') }}</option></select></label>
      <label>{{ t('tier') }}<select v-model="loreEdit.tier"><option value="core">{{ t('core') }}</option><option value="background">{{ t('background') }}</option><option value="archived">{{ t('archived') }}</option></select></label>
      <label>{{ t('keywords') }}<input :value="arrText(loreEdit.keywords)" @input="setArr('keywords', $event)" :placeholder="t('keywordsPlaceholder')"></label>
      <label>{{ t('content') }}<textarea rows="6" v-model="loreEdit.content"></textarea></label>
      <label>{{ t('keywordMatchMode') }}<select v-model="loreEdit.match_mode"><option value="any">{{ t('matchAny') }}</option><option value="all">{{ t('matchAll') }}</option><option value="not_any">{{ t('matchNotAny') }}</option><option value="not_all">{{ t('matchNotAll') }}</option></select></label>
      <div class="check-row"><label><input type="checkbox" v-model="loreEdit.unreliable">{{ t('unreliableMemory') }}</label><label><input type="checkbox" v-model="loreEdit.sync_on_enter">{{ t('syncOnEnter') }}</label><label><input type="checkbox" v-model="loreEdit.is_constant">{{ t('constant') }}</label></div>
      <label>{{ t('recursiveTrigger') }}<input :value="arrText(loreEdit.triggers_recursive)" @input="setArr('triggers_recursive', $event)" :placeholder="t('recursiveTriggerPlaceholder')"></label>
      <label>{{ t('visibleCharacters') }}<input :value="arrText(loreEdit.visible_to)" @input="setArr('visible_to', $event)" :placeholder="t('visibleCharactersPlaceholder')"></label>
      <label>{{ t('connectedEntries') }}<input :value="arrText(loreEdit.connected_to)" @input="setArr('connected_to', $event)" :placeholder="t('connectedEntriesPlaceholder')"></label>
      <div class="grid-2"><label>{{ t('stickyRounds') }}<input type="number" v-model.number="loreEdit.sticky"></label><label>{{ t('cooldown') }}<input type="number" v-model.number="loreEdit.cooldown"></label></div>
      <div class="grid-2"><label>{{ t('delay') }}<input type="number" v-model.number="loreEdit.delay"></label><label>{{ t('order') }}<input type="number" v-model.number="loreEdit.order"></label></div>
      <div class="grid-2"><label>{{ t('probabilityPercent') }}<input type="number" v-model.number="loreEdit.probability"></label><label>{{ t('group') }}<input v-model="loreEdit.group"></label></div>
      <label>{{ t('groupWeight') }}<input type="number" v-model.number="loreEdit.group_weight"></label>
      <template #actions><button @click="loreEdit = null">{{ t('cancel') }}</button><button class="primary" @click="saveLore">{{ t('saveAction') }}</button></template>
    </Modal>
  </section>
</template>
