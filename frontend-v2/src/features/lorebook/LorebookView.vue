<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import type { CharacterListResponse, GameSummary, GamesResponse, LorebookResponse, LoreEntry, LoreGenerateResponse, Player, WorldCreateResponse, WorldListResponse, WorldSummary } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { activePeerGameClient } from '@/peer/game/bridge'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale, type Locale } from '@/composables/useLocale'
import { SUPPORTED_LOCALES } from '@/i18n'
import type { MessageKey } from '@/i18n'
import { contentLanguageOf, filterByContentLanguage, localeLabel } from '@/utils/contentLanguage'
import Modal from '@/components/ui/Modal.vue'
import LorePerspectiveInspector from './LorePerspectiveInspector.vue'
import LoreVisibilityBadge from './LoreVisibilityBadge.vue'
import LorebookSidebar, { type LorebookCard } from './LorebookSidebar.vue'
import LoreImportDialog, { type LoreImportDecision } from './LoreImportDialog.vue'
import LoreBindingsDialog, { type LoreBinding } from './LoreBindingsDialog.vue'
import LoreEntryAdvanced from './LoreEntryAdvanced.vue'
import { useLorePerspective } from './useLorePerspective'
import {
  DEFAULT_LORE_ENTRY_FILTERS, LORE_TYPE_ORDER, applyLoreProductActivation, filterLoreEntries,
  isLoreEntryFilterActive, loreEntryActivationOptions, loreEntryEnabled, loreEntrySourceOptions,
  loreProductActivation, normalizeLoreEntryType, normalizeVectorActivation,
  type LoreEntryFilters, type LoreProductActivationMode,
} from './entryFilters'
import { normalizeVisibilityValues, sanitizeCharacterVisibility, visibilityModeOf, type LoreVisibilityMode } from './visibility'

interface LoreEdit extends LoreEntry {
  tier?: string
  content?: string
  match_mode?: string
  vector_activation?: string
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
  probability: number
  group?: string
  group_weight: number
  secondary_keys: string[]
  selective_logic: string
  use_regex: boolean
  case_sensitive: boolean
  match_whole_words: boolean
  scan_depth: number
  priority: number
  prompt_slot: string
  groups: string[]
  /** canonical 字段：预算不足时该条目优先纳入。 */
  prioritize_inclusion: boolean
  non_recursable: boolean
  prevent_further_recursion: boolean
  delay_until_recursion: boolean
  recursion_level: number
}

interface LorebookImportPreview {
  format: string
  counts: { entries: number; mapped: number; warnings: number; unsupported: number }
  warnings: string[]
  character?: { name: string; book_name?: string; entries: number } | null
}

interface LorebookListResponse {
  books: Array<{ id: string; name: string; primary?: boolean; scope?: string; enabled?: boolean }>
}

const toast = useToast()
const { confirm } = useConfirm()
const { locale, t } = useLocale()

const game = ref(readCurrentGame())
const worlds = ref<WorldSummary[]>([])
const worldLanguage = ref<Locale>(locale.value)
const currentWorldId = ref('')
const data = ref<LorebookResponse>({ entries: [] })
const error = ref('')
const busy = ref(false)
const loreEdit = ref<LoreEdit | null>(null)
const generatePrompt = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const importPreview = ref<LorebookImportPreview>()
const importDialogOpen = ref(false)
const pendingImportPayload = ref<unknown>()
const showNewWorld = ref(false)
const players = ref<Player[]>([])
const newWorld = ref({ name: '', description: '', language: locale.value })
const entries = computed(() => data.value.entries || [])
const languageWorlds = computed(() => filterByContentLanguage(worlds.value, worldLanguage.value))
const currentWorld = computed(() => worlds.value.find(w => worldIdOf(w) === currentWorldId.value))
const lorebookBooks = ref<LorebookListResponse['books']>([])
const activeBookId = ref('')
const activeLoreType = ref('all')
const loreTypeOrder = LORE_TYPE_ORDER

const { effectiveViewer, viewerFallback, characterViewerLocked, setViewer, preview, previewError, projectionOf, refreshPreview, activationText, activation, activationLoading, activationError, refreshActivationPreview } = useLorePerspective(currentWorldId, game, players)
const lockedReason = computed<'standalone' | 'peer' | ''>(() => {
  if (!characterViewerLocked.value) return ''
  return game.value && activePeerGameClient() ? 'peer' : 'standalone'
})

const selectedEntryId = ref('')
const selectedEntry = computed(() => entries.value.find(e => e.id && e.id === selectedEntryId.value) || null)
const selectedProjection = computed(() => projectionOf(selectedEntryId.value))
function toggleEntrySelection(entry: LoreEntry) {
  if (!entry.id) return
  selectedEntryId.value = selectedEntryId.value === entry.id ? '' : entry.id
}

const perspectiveFilter = ref<'all' | 'visible' | 'hidden'>('all')
function matchesPerspectiveFilter(entry: LoreEntry): boolean {
  if (perspectiveFilter.value === 'all') return true
  const visible = projectionOf(entry.id)?.visible || false
  return perspectiveFilter.value === 'visible' ? visible : !visible
}

function resolveInspectorOpen(): boolean {
  // 「收起」的选择在任何屏宽都尊重；「展开」只在宽屏生效——
  // 否则宽屏上随手展开一次，手机每次进页面都会被抽屉自动遮挡。
  // storage 读取失败视为无保存值，继续走屏宽判断（手机默认收起）。
  let saved: string | null = null
  try {
    saved = localStorage.getItem('lore_inspector_open')
  } catch {
    saved = null
  }
  if (saved === '0') return false
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true
  return !window.matchMedia('(max-width: 1100px)').matches
}
const inspectorOpen = ref(resolveInspectorOpen())
function persistInspectorOpen(open: boolean) {
  try {
    localStorage.setItem('lore_inspector_open', open ? '1' : '0')
  } catch {
    // storage 不可用时仅当前 session 生效
  }
}
function toggleInspector() {
  inspectorOpen.value = !inspectorOpen.value
  persistInspectorOpen(inspectorOpen.value)
}
function closeInspector() {
  inspectorOpen.value = false
  persistInspectorOpen(false)
}

function worldIdOf(w: WorldSummary | undefined): string { return String(w?.id || w?.world_id || '') }
function worldNameOf(w: WorldSummary | undefined): string { return String(w?.name || w?.world_name || w?.id || '') }
function cloneLore(entry: LoreEntry): LoreEdit { return JSON.parse(JSON.stringify(entry)) as LoreEdit }

function toggleNewWorld() {
  showNewWorld.value = !showNewWorld.value
  if (showNewWorld.value) newWorld.value.language = worldLanguage.value
}

async function loadWorlds() {
  error.value = ''
  try {
    const r = await api<WorldListResponse>('/worlds')
    worlds.value = r.worlds || []
    if (game.value) {
      const [games, characters] = await Promise.all([
        api<GamesResponse>('/games'),
        api<CharacterListResponse>(`/games/${encodeURIComponent(game.value)}/characters`).catch(() => ({ players: [] } as CharacterListResponse)),
      ])
      players.value = characters.players || []
      const cur = (games.games || []).find((g: GameSummary) => g.game_key === game.value)
      if (cur?.world_id) {
        currentWorldId.value = cur.world_id
        const activeWorld = worlds.value.find(w => worldIdOf(w) === cur.world_id)
        worldLanguage.value = activeWorld
          ? contentLanguageOf(activeWorld)
          : contentLanguageOf({ language: cur.language })
      }
    }
    if (!languageWorlds.value.some(w => worldIdOf(w) === currentWorldId.value)) {
      currentWorldId.value = worldIdOf(languageWorlds.value[0])
    }
  } catch (e: unknown) { error.value = errorMessage(e) }
}

watch(currentWorldId, () => { if (currentWorldId.value) { loadLore(); loadLorebooks() } })
watch(worldLanguage, () => {
  if (languageWorlds.value.some(w => worldIdOf(w) === currentWorldId.value)) return
  currentWorldId.value = worldIdOf(languageWorlds.value[0])
  if (!currentWorldId.value) data.value = { entries: [] }
})
watch(locale, next => { if (!game.value) worldLanguage.value = next })

async function loadLore() {
  if (!currentWorldId.value) { data.value = { entries: [] }; return }
  error.value = ''; data.value = { entries: [] }
  try {
    const bookId = activeBookId.value || `world:${currentWorldId.value}`
    if (bookId === `world:${currentWorldId.value}`) {
      data.value = await api<LorebookResponse>(`/lorebook/${encodeURIComponent(currentWorldId.value)}`)
    } else {
      const result = await api<{ entries: LoreEntry[] }>(`/lorebooks/${encodeURIComponent(bookId)}/entries`)
      data.value = { entries: result.entries || [] }
    }
    // 换书/刷新后旧 id 不能再指向任何条目，否则批量操作会打向不存在的目标。
    const loaded = new Set((data.value.entries || []).map(entry => entry.id).filter(Boolean) as string[])
    bulkSelectedIds.value = new Set([...bulkSelectedIds.value].filter(id => loaded.has(id)))
  } catch (e: unknown) { error.value = errorMessage(e) }
}

onMounted(loadWorlds)

function openLore(entry?: LoreEntry) {
  loreEdit.value = entry ? cloneLore(entry) : {
    name: '', type: 'npc', tier: 'background', keywords: [], content: '',
    match_mode: 'any', unreliable: false, sync_on_enter: false, is_constant: false,
    triggers_recursive: [], visible_to: [], connected_to: [], sticky: 0,
    cooldown: 0, delay: 0, order: 100, probability: 100, group: '', group_weight: 1,
    secondary_keys: [], selective_logic: 'any', use_regex: false, case_sensitive: false,
    match_whole_words: false, scan_depth: 0, priority: 100, prompt_slot: 'world_background', groups: [],
    // canonical 默认与后端 payload.setdefault 对齐：显式发值必须等于后端默认，否则 UI 与库里不一致。
    vector_activation: 'hybrid',
    prioritize_inclusion: false,
    non_recursable: false, prevent_further_recursion: false, delay_until_recursion: false,
    recursion_level: 0,
  }
  normalizeActivationModel(loreEdit.value)
  visibilityMode.value = visibilityModeOf(loreEdit.value.visible_to)
}

/**
 * 编辑模型必须始终带具体值：vector_activation 缺失/非法时收敛到与后端同向的 hybrid，
 * is_constant 收敛成 boolean。否则「UI 显示 off、后端存 hybrid」这类分歧只会在保存后才暴露。
 */
function normalizeActivationModel(edit: LoreEdit) {
  edit.is_constant = !!edit.is_constant
  edit.vector_activation = normalizeVectorActivation(edit.vector_activation)
}

/**
 * 第一屏「触发方式」= is_constant + vector_activation 的组合，不是 legacy match_mode。
 * 读写都走 entryFilters 的同一套映射，保证编辑器与列表筛选口径一致。
 */
const productActivation = computed<LoreProductActivationMode>({
  get: () => loreEdit.value ? loreProductActivation(loreEdit.value) : 'keyword',
  set: mode => {
    const edit = loreEdit.value
    if (!edit) return
    const next = applyLoreProductActivation(edit, mode)
    edit.is_constant = next.is_constant
    edit.vector_activation = next.vector_activation
  },
})

// 编辑表单的可见性三档：GM 秘密 / 全队公开 / 指定角色。
// 切档直接改写 visible_to；「指定角色」保留点名条目、剥离公开标记，
// 避免 ["*"] 被带进角色文本框造成档位与内容不一致。
const visibilityMode = ref<LoreVisibilityMode>('gm')
function setVisibilityMode(mode: LoreVisibilityMode) {
  visibilityMode.value = mode
  if (!loreEdit.value) return
  const current = loreEdit.value.visible_to || []
  if (mode === 'public') {
    loreEdit.value.visible_to = ['*']
  } else if (mode === 'gm') {
    loreEdit.value.visible_to = []
  } else {
    loreEdit.value.visible_to = sanitizeCharacterVisibility(current)
  }
}

// 指定角色：队员直接点选（写入 canonical uid），文本框兜底手动添加外部角色。
function characterLabel(p: Player) { return String(p.character_name || p.user_id) }
function isVisibleToPlayer(p: Player) {
  if (!loreEdit.value) return false
  const current = new Set(normalizeVisibilityValues(loreEdit.value.visible_to).map(value => value.toLowerCase()))
  const uid = String(p.user_id).trim().toLowerCase()
  const name = String(p.character_name || '').trim().toLowerCase()
  return current.has(uid) || (name !== '' && current.has(name))
}
function toggleCharacterVisible(p: Player) {
  if (!loreEdit.value) return
  const uid = String(p.user_id).trim()
  const name = String(p.character_name || '').trim().toLowerCase()
  const norm = (value: string) => value.trim().toLowerCase()
  const current = normalizeVisibilityValues(loreEdit.value.visible_to)
  const kept = current.filter(
    value => norm(value) !== uid.toLowerCase() && (name === '' || norm(value) !== name),
  )
  const wasSelected = kept.length !== current.length
  loreEdit.value.visible_to = wasSelected ? kept : [...kept, uid]
}
// 手输路径同样过 sanitize：* / public / 公开 等 marker 不会混进「指定角色」档
function setCharacterTargets(e: Event) {
  if (!loreEdit.value) return
  const values = (e.target as HTMLInputElement).value.split(/[,，、]/).map(x => x.trim()).filter(Boolean)
  loreEdit.value.visible_to = sanitizeCharacterVisibility(values)
}
// 保存前按当前档位做最后一次归一化（defense-in-depth：未来 UI 改动也不漂移）
function normalizeVisibilityForSave() {
  if (!loreEdit.value) return
  if (visibilityMode.value === 'gm') loreEdit.value.visible_to = []
  else if (visibilityMode.value === 'public') loreEdit.value.visible_to = ['*']
  else loreEdit.value.visible_to = sanitizeCharacterVisibility(loreEdit.value.visible_to || [])
}

function arrText(a: unknown) { return Array.isArray(a) ? a.join(t('listSeparator')) : '' }
// 历史 visible_to 可能是逗号分隔字符串（"Alice,Bob"）：arrText 的 Array-only
// 分支会把它渲染成空白，误导用户以为点名丢了。可见性输入框专用读取路径，
// 与 visibilityModeOf 同走归一化；keywords / connected_to 等仍用 arrText。
function visibilityTargetsText(value: unknown) {
  return normalizeVisibilityValues(value).join(t('listSeparator'))
}
function normalizeLoreType(type: unknown): string {
  return normalizeLoreEntryType(type)
}
function typeLabel(type: string | undefined) {
  const labels: Record<string, MessageKey> = { npc: 'contentGroupNpc', location: 'loreTypeLocation', faction: 'loreTypeFaction', item: 'contentGroupItem', event: 'loreTypeEvent', puzzle: 'loreTypePuzzle', spell: 'loreTypeSpell', class: 'loreTypeClass', other: 'loreTypeOther' }
  const key = labels[String(type || '')]
  return key ? t(key) : String(type || t('loreEntry'))
}
/** 产品触发方式（is_constant + vector_activation）的显示名：编辑器与列表筛选共用。 */
function productActivationLabel(mode: string) {
  const labels: Record<string, MessageKey> = {
    keyword: 'loreActivationKeyword',
    always: 'loreActivationAlways',
    hybrid: 'loreActivationHybrid',
    semantic: 'loreActivationSemantic',
  }
  const key = labels[String(mode || '')]
  return key ? t(key) : String(mode || '')
}
function tierLabel(tier: string | undefined) {
  const labels: Record<string, MessageKey> = { core: 'core', background: 'background', archived: 'archived' }
  const key = labels[String(tier || '')]
  return key ? t(key) : String(tier || t('background'))
}
function loreBody(entry: LoreEntry) { return String(entry.content || '').trim() || t('noContent') }
function loreKeywords(entry: LoreEntry) { return arrText(entry.keywords).slice(0, 80) }
function loreConnections(entry: LoreEntry) { return arrText((entry as LoreEdit).connected_to).slice(0, 80) }

// ---- 条目搜索 / 筛选（纯前端，覆盖已加载的条目） ---------------------------
// 类型过滤与分类 tab 共用 canonical 值：tab 与下拉框永远是同一个状态。
const entryFilters = ref<LoreEntryFilters>({ ...DEFAULT_LORE_ENTRY_FILTERS })
const activeFilters = computed<LoreEntryFilters>(() => ({ ...entryFilters.value, type: activeLoreType.value }))
const filtersActive = computed(() => isLoreEntryFilterActive(activeFilters.value))
const sourceOptions = computed(() => loreEntrySourceOptions(entries.value))
const activationOptions = computed(() => loreEntryActivationOptions(entries.value))
const visibleEntries = computed(() => filterLoreEntries(entries.value, activeFilters.value))
function resetEntryFilters() {
  entryFilters.value = { ...DEFAULT_LORE_ENTRY_FILTERS }
  activeLoreType.value = 'all'
}
// 某个类型 tab 的数字只排除「类型」这一个条件，否则 tab 之间无法互相比较。
const typeAgnosticEntries = computed(() => filterLoreEntries(entries.value, { ...activeFilters.value, type: 'all' }))
function loreTypeCount(type: string) {
  return typeAgnosticEntries.value.filter(entry => normalizeLoreType(entry.type) === type).length
}
const loreTypeTabs = computed(() => [
  { type: 'all', label: t('allLoreTypes'), count: typeAgnosticEntries.value.length },
  ...loreTypeOrder.map(type => ({ type, label: typeLabel(type), count: loreTypeCount(type) })),
])
const loreSections = computed(() => {
  const selected = activeLoreType.value
  const types = selected === 'all' ? [...loreTypeOrder] : [selected]
  return types
    .map(type => ({
      type,
      label: typeLabel(type),
      entries: visibleEntries.value.filter(entry => normalizeLoreType(entry.type) === type && matchesPerspectiveFilter(entry)),
    }))
    .filter(section => selected !== 'all' || section.entries.length)
})
function setArr(field: keyof LoreEdit, e: Event) {
  const v = (e.target as HTMLInputElement).value.split(/[,，、]/).map(x => x.trim()).filter(Boolean)
  if (loreEdit.value) (loreEdit.value as Record<string, unknown>)[field] = v
}

async function saveLore() {
  if (!loreEdit.value) return
  normalizeVisibilityForSave()
  // 发出去的就是 UI 上看到的：不会出现「界面 off、库里 hybrid」。
  normalizeActivationModel(loreEdit.value)
  const entry: LoreEdit = { ...loreEdit.value, world_id: currentWorldId.value }
  const bookId = activeBookId.value || `world:${currentWorldId.value}`
  const path = bookId === `world:${currentWorldId.value}`
    ? (entry.id ? `/lorebook/${encodeURIComponent(entry.id)}` : '/lorebook')
    : (entry.id ? `/lorebooks/${encodeURIComponent(bookId)}/entries/${encodeURIComponent(entry.id)}` : `/lorebooks/${encodeURIComponent(bookId)}/entries`)
  try {
    await api<unknown>(path, { method: entry.id ? 'PUT' : 'POST', body: JSON.stringify(entry) })
    toast.success(entry.id ? t('updated') : t('created'))
    loreEdit.value = null
    await loadLore()
    await loadWorlds()
    await refreshPreview()
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function deleteLore(entry: LoreEntry) {
  if (!entry.id) return
  const ok = await confirm({ title: t('deleteLoreEntryTitle'), content: t('deleteLoreEntryContent', { name: entry.name || t('unnamedLoreEntry') }), positiveText: t('deleteLoreEntryAction'), type: 'error' })
  if (!ok) return
  try {
    const bookId = activeBookId.value || `world:${currentWorldId.value}`
    await api<unknown>(bookId === `world:${currentWorldId.value}` ? `/lorebook/${encodeURIComponent(entry.id)}` : `/lorebooks/${encodeURIComponent(bookId)}/entries/${encodeURIComponent(entry.id)}`, { method: 'DELETE' })
    toast.success(t('deleted'))
    if (selectedEntryId.value === entry.id) selectedEntryId.value = ''
    await loadLore()
    await loadWorlds()
    await refreshPreview()
  } catch (e: unknown) { error.value = errorMessage(e) }
}

// ---- 批量操作 --------------------------------------------------------------
// 多选只存在于前端（已加载的条目 id）；每个动作都走既有 per-entry 端点，
// 移动走 canonical 的 /entries/{id}/move，不新增批量 API。
const bulkSelectedIds = ref<Set<string>>(new Set())
const bulkBusy = ref(false)
const bulkMoveOpen = ref(false)
const bulkMoveTarget = ref('')

const bulkBookId = computed(() => activeBookId.value || (currentWorldId.value ? `world:${currentWorldId.value}` : ''))
const bulkSelectedEntries = computed(() => entries.value.filter(entry => entry.id && bulkSelectedIds.value.has(entry.id)))
const bulkSelectedCount = computed(() => bulkSelectedEntries.value.length)
const bulkMoveTargets = computed(() => lorebookBooks.value.filter(book => book.id !== bulkBookId.value))
const allVisibleSelected = computed(() => {
  const rows = visibleEntries.value.filter(entry => entry.id)
  return rows.length > 0 && rows.every(entry => bulkSelectedIds.value.has(String(entry.id)))
})

function isBulkSelected(entry: LoreEntry): boolean {
  return !!entry.id && bulkSelectedIds.value.has(entry.id)
}
function toggleBulkSelect(entry: LoreEntry) {
  if (!entry.id) return
  const next = new Set(bulkSelectedIds.value)
  if (next.has(entry.id)) next.delete(entry.id)
  else next.add(entry.id)
  bulkSelectedIds.value = next
}
function toggleBulkSelectAll() {
  const next = new Set(bulkSelectedIds.value)
  const rows = visibleEntries.value.filter(entry => entry.id)
  if (allVisibleSelected.value) rows.forEach(entry => next.delete(String(entry.id)))
  else rows.forEach(entry => next.add(String(entry.id)))
  bulkSelectedIds.value = next
}
function clearBulkSelection() {
  bulkSelectedIds.value = new Set()
  bulkMoveOpen.value = false
  bulkMoveTarget.value = ''
}
function entryEndpoint(entryId: string): string {
  return `/lorebooks/${encodeURIComponent(bulkBookId.value)}/entries/${encodeURIComponent(entryId)}`
}

/** 逐条执行并如实报告成功/失败条数：部分失败不能被整体成功掩盖。 */
async function runBulkAction(action: (entry: LoreEntry) => Promise<unknown>, doneKey: MessageKey) {
  const targets = bulkSelectedEntries.value
  if (!targets.length || !bulkBookId.value) return
  bulkBusy.value = true
  try {
    const results = await Promise.allSettled(targets.map(entry => action(entry)))
    const failed = results.filter(result => result.status === 'rejected').length
    const succeeded = targets.length - failed
    if (succeeded > 0) toast.success(t(doneKey, { count: succeeded }))
    if (failed > 0) toast.error(t('loreBulkPartialFailure', { count: failed }))
    if (failed === 0) clearBulkSelection()
    await loadLore()
    await loadWorlds()
    await refreshPreview()
  } finally { bulkBusy.value = false }
}

async function bulkSetEnabled(enabled: boolean) {
  await runBulkAction(
    entry => api(entryEndpoint(String(entry.id)), { method: 'PUT', body: JSON.stringify({ enabled }) }),
    enabled ? 'loreBulkEnabledDone' : 'loreBulkDisabledDone',
  )
}
async function bulkSetVisibility(mode: 'gm' | 'public') {
  const visibleTo = mode === 'public' ? ['*'] : []
  await runBulkAction(
    entry => api(entryEndpoint(String(entry.id)), { method: 'PUT', body: JSON.stringify({ visible_to: visibleTo }) }),
    'loreBulkVisibilityDone',
  )
}
async function bulkDelete() {
  const count = bulkSelectedCount.value
  if (!count) return
  const ok = await confirm({
    title: t('loreBulkDelete'),
    content: t('loreBulkDeleteConfirm', { count }),
    positiveText: t('delete'),
    type: 'error',
  })
  if (!ok) return
  await runBulkAction(
    entry => api(entryEndpoint(String(entry.id)), { method: 'DELETE' }),
    'loreBulkDeletedDone',
  )
}
async function bulkMove() {
  const target = bulkMoveTarget.value
  if (!target || !bulkMoveTargets.value.some(book => book.id === target)) return
  await runBulkAction(
    entry => api(`${entryEndpoint(String(entry.id))}/move`, {
      method: 'POST',
      body: JSON.stringify({ target_book_id: target }),
    }),
    'loreBulkMovedDone',
  )
}

// ---- Book 设置（scan_depth / token_budget / recursive_scanning）------------
const bookSettingsOpen = ref(false)
const bookSettingsBusy = ref(false)
const bookSettings = ref({ scan_depth: 0, token_budget: 0, recursive_scanning: false })

function openBookSettings() {
  const book = lorebookBooks.value.find(item => item.id === bulkBookId.value) as Record<string, unknown> | undefined
  bookSettings.value = {
    scan_depth: Number(book?.scan_depth ?? 0) || 0,
    token_budget: Number(book?.token_budget ?? 0) || 0,
    recursive_scanning: !!book?.recursive_scanning,
  }
  bookSettingsOpen.value = true
}
async function saveBookSettings() {
  if (!bulkBookId.value) return
  bookSettingsBusy.value = true
  try {
    await api(`/lorebooks/${encodeURIComponent(bulkBookId.value)}`, {
      method: 'PUT',
      body: JSON.stringify({
        scan_depth: Number(bookSettings.value.scan_depth) || 0,
        token_budget: Number(bookSettings.value.token_budget) || 0,
        recursive_scanning: !!bookSettings.value.recursive_scanning,
      }),
    })
    toast.success(t('updated'))
    bookSettingsOpen.value = false
    await loadLorebooks()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { bookSettingsBusy.value = false }
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
    await refreshPreview()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function createWorld() {
  if (!newWorld.value.name.trim()) { toast.error(t('enterWorldName')); return }
  busy.value = true
  try {
    const createdLanguage = contentLanguageOf({ language: newWorld.value.language })
    const r = await api<WorldCreateResponse>('/worlds', { method: 'POST', body: JSON.stringify({ name: newWorld.value.name, description: newWorld.value.description, language: newWorld.value.language }) })
    if (!r.ok) throw new Error(r.error || t('createFailed'))
    toast.success(t('worldCreated'))
    newWorld.value = { name: '', description: '', language: locale.value }
    showNewWorld.value = false
    await loadWorlds()
    worldLanguage.value = createdLanguage
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
    selectedEntryId.value = ''
    await loadWorlds()
    if (languageWorlds.value.length) currentWorldId.value = worldIdOf(languageWorlds.value[0])
    else data.value = { entries: [] }
  } catch (e: unknown) { error.value = errorMessage(e) }
}

function exportLore() {
  const bookId = activeBookId.value || `world:${currentWorldId.value}`
  api<{ ok: boolean; [key: string]: unknown }>(`/lorebooks/${encodeURIComponent(bookId)}/export`).then(result => {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `lorebook_${bookId}.json`; a.click(); URL.revokeObjectURL(url)
    toast.success(t('exported'))
  }).catch(e => { error.value = errorMessage(e) })
}

async function importLore(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const imported = JSON.parse(text) as unknown
    const payload = Array.isArray(imported)
      ? { entries: imported.filter((en): en is Record<string, unknown> => !!en && typeof en === 'object') }
      : imported
    if (!payload || typeof payload !== 'object') throw new Error(t('jsonArrayRequired'))
    pendingImportPayload.value = payload
    importPreview.value = await api<LorebookImportPreview>('/lorebooks/import/preview', { method: 'POST', body: JSON.stringify(payload) })
    importDialogOpen.value = true
  } catch (err: unknown) { error.value = `${t('importFailed')}: ${errorMessage(err)}` } finally {
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function loadLorebooks() {
  if (!currentWorldId.value) { lorebookBooks.value = []; return }
  try {
    const query = new URLSearchParams({ world_id: currentWorldId.value })
    if (game.value) query.set('game_key', game.value)
    const result = await api<LorebookListResponse>(`/lorebooks?${query.toString()}`)
    lorebookBooks.value = (result.books || []).map(book => ({ ...book, primary: book.primary || book.id === `world:${currentWorldId.value}` }))
    if (!activeBookId.value || !lorebookBooks.value.some(book => book.id === activeBookId.value)) activeBookId.value = `world:${currentWorldId.value}`
  } catch {
    const world = currentWorld.value
    lorebookBooks.value = world ? [{ id: currentWorldId.value, name: worldNameOf(world), primary: true, scope: 'world' }] : []
  }
}

function selectLorebook(bookId: string) {
  if (!lorebookBooks.value.some(item => item.id === bookId)) return
  activeBookId.value = bookId
  void loadLore()
}

/** 导入目标与 binding 全部来自对话框里用户的显式选择，不再隐式塞主世界书。 */
async function confirmLoreImport(decision: LoreImportDecision) {
  if (!importPreview.value || !currentWorldId.value) return
  try {
    if (!pendingImportPayload.value || typeof pendingImportPayload.value !== 'object') throw new Error(t('importFailed'))
    const body: Record<string, unknown> = { payload: pendingImportPayload.value }
    if (decision.bookId) body.book_id = decision.bookId
    if (decision.binding) {
      body.binding = decision.bookId && decision.bookId === primaryBookId.value
        ? { ...decision.binding, role: 'primary' }
        : decision.binding
    }
    const result = await api<{ ok: boolean; book_id?: string }>('/lorebooks/import', { method: 'POST', body: JSON.stringify(body) })
    importDialogOpen.value = false
    pendingImportPayload.value = undefined
    importPreview.value = undefined
    toast.success(t('importedLorebook'))
    await loadLorebooks()
    if (result?.book_id) selectLorebook(result.book_id)
    await loadLore(); await loadWorlds(); await refreshPreview()
  } catch (err: unknown) { error.value = `${t('importFailed')}: ${errorMessage(err)}` }
}

// ---- Book 管理（create / rename / delete / enable / bindings）---------------

const primaryBookId = computed(() => (currentWorldId.value ? `world:${currentWorldId.value}` : ''))
const bindingsDialogOpen = ref(false)
const bindingsBook = ref<LorebookCard | null>(null)
const bookBindings = ref<LoreBinding[]>([])

/** 绑定目标用的角色名单：Lore 视角已经加载过 players，这里不再重复请求。 */
const bindableCharacters = computed(() =>
  players.value
    .map(player => ({
      uid: String(player.user_id || ''),
      name: String(player.character_name || player.user_id || ''),
    }))
    .filter(item => item.uid),
)

async function createBook() {
  if (!currentWorldId.value) return
  const name = window.prompt('新世界书名称')
  if (!name || !name.trim()) return
  busy.value = true
  try {
    const id = `book:${Date.now().toString(36)}`
    await api('/lorebooks', { method: 'POST', body: JSON.stringify({ id, name: name.trim() }) })
    await loadLorebooks()
    selectLorebook(id)
    toast.success('已创建世界书')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function renameBook(book: LorebookCard) {
  const name = window.prompt('重命名世界书', book.name)
  if (!name || !name.trim() || name.trim() === book.name) return
  busy.value = true
  try {
    await api(`/lorebooks/${encodeURIComponent(book.id)}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) })
    await loadLorebooks()
    toast.success('已重命名')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function removeBook(book: LorebookCard) {
  if (book.primary) return
  const ok = await confirm({
    title: '删除世界书',
    content: `删除世界书「${book.name}」？其中的条目会一并删除。`,
    positiveText: '删除世界书',
    type: 'error',
  })
  if (!ok) return
  busy.value = true
  try {
    await api(`/lorebooks/${encodeURIComponent(book.id)}`, { method: 'DELETE' })
    if (activeBookId.value === book.id) activeBookId.value = primaryBookId.value
    await loadLorebooks(); await loadLore()
    toast.success('已删除')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function toggleBookEnabled(book: LorebookCard) {
  busy.value = true
  try {
    await api(`/lorebooks/${encodeURIComponent(book.id)}`, { method: 'PUT', body: JSON.stringify({ enabled: book.enabled === false }) })
    await loadLorebooks()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function openBindings(book: LorebookCard) {
  bindingsBook.value = book
  bindingsDialogOpen.value = true
  await loadBindings(book.id)
}

async function loadBindings(bookId: string) {
  try {
    const result = await api<{ ok: boolean; bindings: LoreBinding[] }>(`/lorebooks/${encodeURIComponent(bookId)}/bindings`)
    bookBindings.value = result.bindings || []
  } catch (e: unknown) { error.value = errorMessage(e); bookBindings.value = [] }
}

async function addBinding(binding: { scope_kind: string; scope_id: string }) {
  const book = bindingsBook.value
  if (!book) return
  busy.value = true
  try {
    await api(`/lorebooks/${encodeURIComponent(book.id)}/bindings`, {
      method: 'POST',
      body: JSON.stringify({ id: `bind:${book.id}:${binding.scope_kind}:${Date.now().toString(36)}`, ...binding }),
    })
    await loadBindings(book.id); await loadLorebooks()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function removeBinding(bindingId: string) {
  const book = bindingsBook.value
  if (!book) return
  busy.value = true
  try {
    await api(`/lorebook-bindings/${encodeURIComponent(bindingId)}`, { method: 'DELETE' })
    await loadBindings(book.id); await loadLorebooks()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="view archive-page lorebook-page">
    <div class="lorebook-shell" :class="{ 'inspector-open': inspectorOpen }">
      <LorebookSidebar
        :books="lorebookBooks"
        :active-id="activeBookId"
        :busy="busy"
        @select="selectLorebook"
        @create="createBook"
        @rename="renameBook"
        @remove="removeBook"
        @toggle-enabled="toggleBookEnabled"
        @bindings="openBindings"
        @import="fileInput?.click()"
        @export="exportLore"
      />
      <main class="lorebook-workspace">
    <header class="view-title archive-hero">
      <div>
        <span class="section-kicker">{{ t('lorebookKicker') }}</span>
        <h1>{{ t('navLorebook') }}</h1>
        <p v-if="game">{{ t('currentSave') }}: {{ game }}</p>
        <p v-else class="muted">{{ t('standaloneLorebookHint') }}</p>
      </div>
      <div class="lore-header-actions">
        <button v-if="bulkBookId" :disabled="busy" @click="openBookSettings">{{ t('loreBookSettings') }}</button>
        <button @click="toggleInspector">{{ t('loreInspectorToggle') }}</button>
        <button @click="loadWorlds">{{ t('refresh') }}</button>
      </div>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div class="lore-world-bar">
      <label class="lore-language-filter">
        <span>{{ t('contentLanguage') }}</span>
        <select v-model="worldLanguage">
          <option v-for="code in SUPPORTED_LOCALES" :key="code" :value="code">{{ localeLabel(code) }}</option>
        </select>
      </label>
      <select v-model="currentWorldId">
        <option value="" disabled>{{ t('chooseWorldEllipsis') }}</option>
        <option v-for="w in languageWorlds" :key="worldIdOf(w)" :value="worldIdOf(w)">{{ worldNameOf(w) }} ({{ t('entriesCount', { count: w.entry_count || 0 }) }})</option>
      </select>
      <button class="success" @click="toggleNewWorld">+ {{ t('newWorld') }}</button>
      <button v-if="currentWorldId" class="danger" @click="deleteWorld" :disabled="busy">{{ t('deleteWorldAction') }}</button>
    </div>

    <details v-if="showNewWorld" class="ai-block" open>
      <summary>{{ t('newWorld') }}</summary>
      <label>{{ t('worldName') }}<input v-model="newWorld.name" :placeholder="t('nameNewWorld')"></label>
      <label>{{ t('contentLanguage') }}
        <select v-model="newWorld.language">
          <option v-for="code in SUPPORTED_LOCALES" :key="code" :value="code">{{ localeLabel(code) }}</option>
        </select>
      </label>
      <label>{{ t('description') }}<textarea rows="2" v-model="newWorld.description"></textarea></label>
      <div class="actions"><button @click="showNewWorld = false">{{ t('cancel') }}</button><button class="primary" :disabled="busy" @click="createWorld">{{ t('create') }}</button></div>
    </details>

    <div class="lore-tools">
      <button class="success" :disabled="!currentWorldId" @click="openLore()">{{ t('addLoreEntry') }}</button>
      <input v-model="generatePrompt" :placeholder="t('generateLorePlaceholder')">
      <button @click="generateLore" :disabled="busy || !currentWorldId">{{ t('aiGenerate') }}</button>
      <button @click="exportLore" :disabled="!data?.entries?.length">{{ t('export') }}</button>
      <button @click="fileInput?.click()" :disabled="!currentWorldId">{{ t('import') }}</button>
      <input ref="fileInput" type="file" accept="application/json" @change="importLore" hidden>
    </div>

    <p class="memory-meta" v-if="currentWorldId">
      {{ worldNameOf(currentWorld) || currentWorldId }} · {{ localeLabel(worldLanguage) }} · {{ t('lorebookEntryCount', { count: entries.length }) }}
    </p>

    <section v-if="entries.length" class="lore-entry-toolbar" :aria-label="t('loreFilterLabel')">
      <div class="lore-entry-toolbar__row">
        <input
          v-model="entryFilters.search"
          class="lore-entry-search"
          type="search"
          :placeholder="t('loreEntrySearchPlaceholder')"
          :aria-label="t('loreEntrySearchPlaceholder')"
        >
        <select v-model="activeLoreType" class="lore-entry-filter lore-filter-type" :aria-label="t('type')">
          <option value="all">{{ t('allLoreTypes') }}</option>
          <option v-for="tp in loreTypeOrder" :key="tp" :value="tp">{{ typeLabel(tp) }}</option>
        </select>
        <select v-model="entryFilters.visibility" class="lore-entry-filter lore-filter-visibility" :aria-label="t('loreVisibilityLabel')">
          <option value="all">{{ t('loreFilterAll') }}</option>
          <option value="public">{{ t('loreVisibilityPublic') }}</option>
          <option value="characters">{{ t('loreVisibilityCharacters') }}</option>
          <option value="gm">{{ t('loreAudienceGmSecret') }}</option>
        </select>
        <select v-model="entryFilters.state" class="lore-entry-filter lore-filter-state" :aria-label="t('status')">
          <option value="all">{{ t('loreFilterAll') }}</option>
          <option value="enabled">{{ t('enabled') }}</option>
          <option value="disabled">{{ t('disabled') }}</option>
        </select>
        <select v-model="entryFilters.activation" class="lore-entry-filter lore-filter-activation" :aria-label="t('loreActivationMode')">
          <option value="">{{ t('loreFilterAll') }}</option>
          <option v-for="mode in activationOptions" :key="mode" :value="mode">{{ productActivationLabel(mode) }}</option>
        </select>
        <select v-model="entryFilters.source" class="lore-entry-filter lore-filter-source" :aria-label="t('source')">
          <option value="all">{{ t('loreFilterAll') }}</option>
          <option v-for="item in sourceOptions" :key="item" :value="item">{{ item }}</option>
        </select>
        <button v-if="filtersActive" class="lore-entry-filter-reset" @click="resetEntryFilters">{{ t('reset') }}</button>
      </div>

      <div class="lore-entry-bulk" role="group" :aria-label="t('loreBulkActions')">
        <label class="lore-entry-bulk__select">
          <input
            type="checkbox"
            class="lore-bulk-select-all"
            :checked="allVisibleSelected"
            @change="toggleBulkSelectAll"
          >
          {{ t('loreBulkSelectVisible') }}
        </label>
        <span class="muted small">{{ t('loreBulkSelectedCount', { count: bulkSelectedCount }) }}</span>
        <button :disabled="bulkBusy || !bulkSelectedCount" @click="bulkSetEnabled(true)">{{ t('loreBulkEnable') }}</button>
        <button :disabled="bulkBusy || !bulkSelectedCount" @click="bulkSetEnabled(false)">{{ t('loreBulkDisable') }}</button>
        <button :disabled="bulkBusy || !bulkSelectedCount" @click="bulkSetVisibility('public')">{{ t('loreBulkMakePublic') }}</button>
        <button :disabled="bulkBusy || !bulkSelectedCount" @click="bulkSetVisibility('gm')">{{ t('loreBulkMakeGm') }}</button>
        <button :disabled="bulkBusy || !bulkSelectedCount || !bulkMoveTargets.length" @click="bulkMoveOpen = true">{{ t('loreBulkMove') }}</button>
        <button class="danger" :disabled="bulkBusy || !bulkSelectedCount" @click="bulkDelete">{{ t('loreBulkDelete') }}</button>
        <button :disabled="bulkBusy || !bulkSelectedCount" @click="clearBulkSelection">{{ t('clearSelection') }}</button>
      </div>
    </section>

    <div v-if="entries.length" class="lore-type-tabs">
      <button
        v-for="tab in loreTypeTabs"
        :key="tab.type"
        :class="{ active: activeLoreType === tab.type }"
        @click="activeLoreType = tab.type"
      >
        <span>{{ tab.label }}</span>
        <strong>{{ tab.count }}</strong>
      </button>
    </div>

    <div v-if="loreSections.length" class="lore-categories">
      <section v-for="section in loreSections" :key="section.type" class="lore-category-section">
        <header class="lore-category-head">
          <h2>{{ section.label }}</h2>
          <span>{{ t('loreCategoryCount', { count: section.entries.length }) }}</span>
        </header>
        <div class="memory-list lore-list">
          <article
            v-for="e in section.entries"
            :key="e.id || e.name"
            class="memory-row lore-row"
            :class="{ selected: e.id && e.id === selectedEntryId }"
            @click="toggleEntrySelection(e)"
          >
            <div class="memory-row-main">
              <div class="memory-row-head">
                <input
                  type="checkbox"
                  class="lore-row-select"
                  :checked="isBulkSelected(e)"
                  :aria-label="t('loreBulkSelectEntry', { name: e.name || t('unnamedLoreEntry') })"
                  @click.stop
                  @change="toggleBulkSelect(e)"
                >
                <strong>{{ e.name || t('unnamedLoreEntry') }}</strong>
                <LoreVisibilityBadge :projection="projectionOf(e.id)" />
                <span class="badge">{{ typeLabel(e.type) }}</span>
                <span class="badge" :class="{ low: e.tier === 'archived' }">{{ tierLabel(e.tier) }}</span>
                <span v-if="!loreEntryEnabled(e)" class="badge low">{{ t('disabled') }}</span>
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
              <button @click.stop="openLore(e)">{{ t('edit') }}</button>
              <button class="danger" @click.stop="deleteLore(e)">{{ t('delete') }}</button>
            </div>
          </article>
        </div>
      </section>
    </div>

    <section v-else-if="entries.length && filtersActive" class="empty-panel lore-no-matches">
      <h2>{{ t('loreNoMatches') }}</h2>
      <p class="muted">{{ t('loreNoMatchesHint') }}</p>
      <button @click="resetEntryFilters">{{ t('reset') }}</button>
    </section>

    <section v-else-if="entries.length" class="empty-panel">
      <h2>{{ t('emptyLoreCategory') }}</h2>
      <p class="muted">{{ t('chooseAnotherLoreCategory') }}</p>
    </section>

    <section v-else-if="currentWorldId && !busy" class="empty-panel">
      <h2>{{ t('noLoreEntries') }}</h2>
      <p class="muted">{{ t('noLoreEntriesHint') }}</p>
    </section>

    <section v-else-if="!currentWorldId && !busy && !showNewWorld" class="empty-panel">
      <h2>{{ t('chooseWorldEllipsis') }}</h2>
      <p class="muted">{{ t('standaloneLorebookHint') }}</p>
    </section>

    <Modal v-if="bulkMoveOpen" :title="t('loreBulkMove')" @close="bulkMoveOpen = false">
      <label>{{ t('loreBulkMoveTarget') }}
        <select v-model="bulkMoveTarget" class="lore-bulk-move-target">
          <option value="">{{ t('loreBulkMovePick') }}</option>
          <option v-for="book in bulkMoveTargets" :key="book.id" :value="book.id">{{ book.name }}</option>
        </select>
      </label>
      <p class="muted">{{ t('loreBulkMoveHint', { count: bulkSelectedCount }) }}</p>
      <template #actions>
        <button @click="bulkMoveOpen = false">{{ t('cancel') }}</button>
        <button class="primary" :disabled="bulkBusy || !bulkMoveTarget" @click="bulkMove">{{ t('loreBulkMove') }}</button>
      </template>
    </Modal>

    <Modal v-if="bookSettingsOpen" :title="t('loreBookSettings')" @close="bookSettingsOpen = false">
      <label>{{ t('loreBookScanDepth') }}
        <input v-model.number="bookSettings.scan_depth" class="lore-book-scan-depth" type="number" min="0">
      </label>
      <label>{{ t('loreBookTokenBudget') }}
        <input v-model.number="bookSettings.token_budget" class="lore-book-token-budget" type="number" min="0">
      </label>
      <div class="check-row">
        <label><input v-model="bookSettings.recursive_scanning" class="lore-book-recursive-scanning" type="checkbox"> {{ t('loreBookRecursiveScanning') }}</label>
      </div>
      <p class="muted">{{ t('loreBookSettingsHint') }}</p>
      <template #actions>
        <button @click="bookSettingsOpen = false">{{ t('cancel') }}</button>
        <button class="primary" :disabled="bookSettingsBusy" @click="saveBookSettings">{{ t('saveAction') }}</button>
      </template>
    </Modal>

    <Modal v-if="loreEdit" :title="loreEdit.id ? t('editLoreEntry') : t('newLoreEntry')" @close="loreEdit = null">
      <!-- 第一屏只保留日常要改的六项；其余全部折叠进 LoreEntryAdvanced。 -->
      <section class="lore-entry-simple">
        <label>{{ t('name') }}<input v-model="loreEdit.name"></label>
        <label>{{ t('type') }}<select v-model="loreEdit.type"><option v-for="tp in loreTypeOrder" :key="tp" :value="tp">{{ typeLabel(tp) }}</option></select></label>
        <label>{{ t('content') }}<textarea rows="6" v-model="loreEdit.content"></textarea></label>
        <label>{{ t('loreActivationMode') }}<select v-model="productActivation" class="lore-entry-trigger-mode"><option value="keyword">{{ t('loreActivationKeyword') }}</option><option value="always">{{ t('loreActivationAlways') }}</option><option value="hybrid">{{ t('loreActivationHybrid') }}</option><option value="semantic">{{ t('loreActivationSemantic') }}</option></select></label>
        <label>{{ t('keywords') }}<input :value="arrText(loreEdit.keywords)" @input="setArr('keywords', $event)" :placeholder="t('keywordsPlaceholder')"></label>
        <label>{{ t('loreVisibilityLabel') }}</label>
        <div class="lore-filter-options" role="radiogroup" :aria-label="t('loreVisibilityLabel')">
          <button type="button" role="radio" :aria-checked="visibilityMode === 'gm'" :class="{ active: visibilityMode === 'gm' }" @click="setVisibilityMode('gm')">{{ t('loreAudienceGmSecret') }}</button>
          <button type="button" role="radio" :aria-checked="visibilityMode === 'public'" :class="{ active: visibilityMode === 'public' }" @click="setVisibilityMode('public')">{{ t('loreVisibilityPublic') }}</button>
          <button type="button" role="radio" :aria-checked="visibilityMode === 'characters'" :class="{ active: visibilityMode === 'characters' }" @click="setVisibilityMode('characters')">{{ t('loreVisibilityCharacters') }}</button>
        </div>
        <template v-if="visibilityMode === 'characters'">
          <div v-if="players.length" class="lore-filter-options" role="group" :aria-label="t('visibleCharacters')">
            <button v-for="p in players" :key="p.user_id" type="button" :class="{ active: isVisibleToPlayer(p) }" @click="toggleCharacterVisible(p)">{{ characterLabel(p) }}</button>
          </div>
          <label>{{ t('visibleCharacters') }}<input :value="visibilityTargetsText(loreEdit.visible_to)" @input="setCharacterTargets" :placeholder="t('visibleCharactersPlaceholder')"></label>
        </template>
      </section>
      <LoreEntryAdvanced v-model="loreEdit" />
      <template #actions><button @click="loreEdit = null">{{ t('cancel') }}</button><button class="primary" @click="saveLore">{{ t('saveAction') }}</button></template>
    </Modal>
      </main>
      <div v-if="inspectorOpen" class="lore-inspector-backdrop" @click="closeInspector"></div>
      <LorePerspectiveInspector
        v-if="inspectorOpen"
        :players="players"
        :viewer="effectiveViewer"
        :viewer-fallback="viewerFallback"
        :character-viewer-locked="characterViewerLocked"
        :locked-reason="lockedReason"
        :preview="preview"
        :preview-error="previewError"
        :selected-entry="selectedEntry"
        :selected-projection="selectedProjection"
        :filter="perspectiveFilter"
        :action-text="activationText"
        :activation="activation"
        :activation-loading="activationLoading"
        :activation-error="activationError"
        @select-viewer="setViewer"
        @select-filter="perspectiveFilter = $event"
        @update:action-text="activationText = $event"
        @refresh-activation="refreshActivationPreview"
        @close="closeInspector"
      />
    </div>
    <LoreImportDialog
      :open="importDialogOpen"
      :preview="importPreview"
      :books="lorebookBooks"
      :characters="bindableCharacters"
      :world-id="currentWorldId"
      :world-name="worldNameOf(currentWorld)"
      :game-key="game"
      :primary-book-id="primaryBookId"
      @close="importDialogOpen = false"
      @confirm="confirmLoreImport"
    />
    <LoreBindingsDialog
      :open="bindingsDialogOpen"
      :book-name="bindingsBook?.name"
      :bindings="bookBindings"
      :world-id="currentWorldId"
      :world-name="worldNameOf(currentWorld)"
      :game-key="game"
      :characters="bindableCharacters"
      :busy="busy"
      @close="bindingsDialogOpen = false"
      @add="addBinding"
      @remove="removeBinding"
    />
  </section>
</template>
