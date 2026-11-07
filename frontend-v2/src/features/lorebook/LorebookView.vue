<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { api, ApiError, errorMessage } from '@/api/client'
import type { CharacterListResponse, GameSummary, GamesResponse, LorebookResponse, LoreEntry, LoreGenerateResponse, Player, WorldListResponse, WorldSummary } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { activePeerGameClient } from '@/peer/game/bridge'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale, type Locale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'
import { CONTENT_LANGUAGE_OPTIONS, contentLanguageLabelKey, contentLanguageOf, filterByContentLanguage } from '@/utils/contentLanguage'
import Modal from '@/components/ui/Modal.vue'
import LorePerspectiveInspector from './LorePerspectiveInspector.vue'
import LoreVisibilityBadge from './LoreVisibilityBadge.vue'
import LoreImportDialog, { type LoreImportDecision } from './LoreImportDialog.vue'
import LoreBindingsDialog, { type LoreBinding } from './LoreBindingsDialog.vue'
import LoreEntryAdvanced from './LoreEntryAdvanced.vue'
import LoreEntryEditorFrame from './LoreEntryEditorFrame.vue'
import LoreEntryDetail from './LoreEntryDetail.vue'
import LorebookCreateDialog, { type LorebookCreateDecision } from './LorebookCreateDialog.vue'
import LorebookUsageSummary from './LorebookUsageSummary.vue'
import LorebookRecoveryDialog from './LorebookRecoveryDialog.vue'
import { recoveryCopy } from './recoveryCopy'
import WorldDefinitionEditor, { type WorldDefinitionClonePayload } from '../worlds/WorldDefinitionEditor.vue'
import { lorebookCopy } from './copy'
import { loreWorkspaceCopy } from './workspaceCopy'
import { usageCopy } from './usageCopy'
import { useWorldSelectionRoute } from './useWorldSelectionRoute'
import { lorebookChoices } from './bookChoices'
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
  /** canonical 次级关键词门控开关；与 selective_logic（组合逻辑）是两个概念。 */
  selective: boolean
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
  plan_digest?: string
  commit_plan?: { operations?: Array<{ action?: string; existing_ref?: unknown }> }
}

interface LorebookListResponse {
  books: LorebookCard[]
}

/** Book 选择器 / 管理菜单共用的 canonical Book 视图（原 Sidebar 的 LorebookCard）。 */
interface LorebookCard { id: string; name: string; language?: string; scope?: string; primary?: boolean; enabled?: boolean; entry_count?: number; bindings?: LoreBinding[]; ref?: { kind?: string; source_kind?: string; source_id?: string } }

const toast = useToast()
const { confirm } = useConfirm()
const { locale, t } = useLocale()
const copy = computed(() => lorebookCopy(locale.value))
const usage = computed(() => usageCopy(locale.value))
const recovery = computed(() => recoveryCopy(locale.value))
const workspaceCopy = computed(() => loreWorkspaceCopy(locale.value))
const { requestedWorldId, requestedBookId, requestedEntryId, hasRequestedWorld, resolveWorldId, syncWorldId } = useWorldSelectionRoute()

const game = ref(readCurrentGame())
const worlds = ref<WorldSummary[]>([])
const worldLanguage = ref<Locale>(locale.value)
const currentWorldId = ref('')
const data = ref<LorebookResponse>({ entries: [] })
const error = ref('')
const busy = ref(false)
const loreEdit = ref<LoreEdit | null>(null)
const inlineEdit = ref(false)
const detailOpen = ref(false)
const editContext = ref<{ worldId: string; bookId: string } | null>(null)
const entrySaving = ref(false)
const initialEntryDraft = ref('')
const entryDirty = computed(() => loreEdit.value !== null && JSON.stringify(loreEdit.value) !== initialEntryDraft.value)
async function allowLeaveEntry(): Promise<boolean> {
  if (!entryDirty.value) return true
  return confirm({ title: workspaceCopy.value.unsaved, content: workspaceCopy.value.discardHint, positiveText: workspaceCopy.value.discard, negativeText: workspaceCopy.value.keep, type: 'warning' })
}
onBeforeRouteLeave(allowLeaveEntry)
onBeforeRouteUpdate((to, from) => to.query.world !== from.query.world ? allowLeaveEntry() : true)
async function returnToEntries() { if (await allowLeaveEntry()) closeEntryDetail() }
async function chooseWorldLanguage(event: Event) {
  if (await allowLeaveEntry()) worldLanguage.value = (event.target as HTMLSelectElement).value as Locale
  else (event.target as HTMLSelectElement).value = worldLanguage.value
}
async function changeWorldSelection(event: Event) {
  const select = event.target as HTMLSelectElement
  await selectWorld(select.value)
  select.value = currentWorldId.value
}
async function changeBookSelection(event: Event) {
  const select = event.target as HTMLSelectElement
  await selectLorebook(select.value)
  select.value = currentBookId.value
}
const generatePrompt = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const importPreview = ref<LorebookImportPreview>()
const importDialogOpen = ref(false)
const importBusy = ref(false)
const pendingImportPayload = ref<unknown>()
const showNewWorld = ref(false)
const showEditWorld = ref(false)
const editWorldId = ref('')
const editWorldInitial = ref<{ name?: string; description?: string; language?: string }>({})
let worldEditorContextEpoch = 0
const players = ref<Player[]>([])
const entries = computed(() => data.value.entries || [])
const languageWorlds = computed(() => filterByContentLanguage(worlds.value, worldLanguage.value))
const currentWorld = computed(() => worlds.value.find(w => worldIdOf(w) === currentWorldId.value))
const lorebookBooks = ref<LorebookListResponse['books']>([])
const activeBookId = ref('')
const bookCreateOpen = ref(false)
const bookCreateError = ref('')
const pendingBookBinding = ref<{ bookId: string; bookName: string; binding: NonNullable<LorebookCreateDecision['binding']> } | null>(null)
const activeLoreType = ref('all')
const loreTypeOrder = LORE_TYPE_ORDER
let worldsRequestEpoch = 0
let lorebookListRequestEpoch = 0
const usageRefreshToken = ref(0)
const recoveryDialogOpen = ref(false)
const routeBookError = ref('')
const routeWorldError = ref('')
const routeBookSelectionConsumed = ref(false)
const routeEntrySelectionConsumed = ref(false)
function routeBookNotFound(bookId: string): string {
  return locale.value === 'zh-CN' ? `资料册 “${bookId}” 不存在或当前不可用，请从内容库重新打开。` : `Lorebook “${bookId}” is unavailable. Open it again from the content library.`
}
function routeEntryNotFound(entryId: string, bookId: string): string {
  return locale.value === 'zh-CN' ? `条目 “${entryId}” 不存在于资料册 “${bookId}”。` : `Entry “${entryId}” is not in lorebook “${bookId}”.`
}
async function onRecoveryChanged() { await loadLorebooks(); await loadLore(); usageRefreshToken.value += 1 }

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
  // Keep an open draft attached to its entry until saved or cancelled.
  if (loreEdit.value && inlineEdit.value) return
  selectedEntryId.value = entry.id
  detailOpen.value = true
}
function closeEntryDetail() {
  loreEdit.value = null
  inlineEdit.value = false
  detailOpen.value = false
  selectedEntryId.value = ''
}
watch([currentWorldId, activeBookId], () => {
  closeEntryDetail()
  editContext.value = null
  selectedEntryId.value = ''
})

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
  if (saved !== '1') return false
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

function applyRouteWorldSelection(): boolean {
  if (!hasRequestedWorld.value) {
    error.value = ''
    routeWorldError.value = ''
    if (requestedBookId.value) {
      // A book-only URL is an explicit independent browsing context. Do not
      // inherit the active game's world or silently bind this book to it.
      currentWorldId.value = ''
      activeBookId.value = ''
      routeBookError.value = ''
      routeBookSelectionConsumed.value = false
      lorebookBooks.value = []
      data.value = { entries: [] }
      return true
    }
    return false
  }
  const selectedId = resolveWorldId(worlds.value)
  if (!selectedId) {
    currentWorldId.value = ''
    activeBookId.value = ''
    routeBookError.value = ''
    routeBookSelectionConsumed.value = false
    lorebookBooks.value = []
    data.value = { entries: [] }
    routeWorldError.value = copy.value.routeWorldNotFound.replace('{id}', requestedWorldId.value)
    error.value = requestedBookId.value ? '' : routeWorldError.value
    return true
  }
  error.value = ''
  routeWorldError.value = ''
  routeBookError.value = ''
  currentWorldId.value = selectedId
  const selected = worlds.value.find(world => worldIdOf(world) === selectedId)
  if (selected) worldLanguage.value = contentLanguageOf(selected)
  return true
}

function toggleNewWorld() {
  showNewWorld.value = !showNewWorld.value
  closeEditWorld()
}

function openEditWorld() {
  const world = currentWorld.value
  if (!world || !currentWorldId.value) return
  worldEditorContextEpoch += 1
  editWorldId.value = currentWorldId.value
  editWorldInitial.value = { name: worldNameOf(world), description: String(world.description || ''), language: contentLanguageOf(world) }
  showEditWorld.value = true
  showNewWorld.value = false
}

function closeEditWorld() {
  worldEditorContextEpoch += 1
  showEditWorld.value = false
}

async function onWorldDefinitionSaved(payload: { worldId: string; definition: { language: string } }) {
  if (!showEditWorld.value || editWorldId.value !== payload.worldId) return
  const savedLanguage = contentLanguageOf({ language: payload.definition.language })
  // Update the known row before changing the filter, so the filter watcher
  // retains this world while the authoritative catalog refresh is pending.
  worlds.value = worlds.value.map(world => worldIdOf(world) === payload.worldId
    ? { ...world, language: savedLanguage }
    : world)
  worldLanguage.value = savedLanguage
  closeEditWorld()
  await loadWorlds(true)
  toast.success(t('worldsUpdated'))
}

async function onWorldDefinitionCloned(payload: WorldDefinitionClonePayload) {
  const contextEpoch = worldEditorContextEpoch
  if (!showEditWorld.value || editWorldId.value !== payload.sourceWorldId) return
  await loadWorlds(true)
  if (contextEpoch !== worldEditorContextEpoch || !showEditWorld.value || editWorldId.value !== payload.sourceWorldId) return
  if (payload.language) worldLanguage.value = contentLanguageOf({ language: payload.language })
  if (!await selectWorld(payload.worldId)) return
  editWorldId.value = payload.worldId
  editWorldInitial.value = { name: payload.name, language: payload.language }
  showEditWorld.value = true
}

async function loadWorlds(preserveCurrentWorld = false) {
  const requestEpoch = ++worldsRequestEpoch
  error.value = ''
  const requestedWorldId = currentWorldId.value
  const requestedLanguage = worldLanguage.value
  try {
    const r = await api<WorldListResponse>('/worlds')
    if (requestEpoch !== worldsRequestEpoch) return
    worlds.value = r.worlds || []
    let selectionUnchanged = currentWorldId.value === requestedWorldId && worldLanguage.value === requestedLanguage
    const preserveSelection = preserveCurrentWorld && selectionUnchanged && Boolean(activeBookId.value)
      && (!currentWorldId.value || worlds.value.some(world => worldIdOf(world) === currentWorldId.value))
    const routeSelection = preserveSelection || applyRouteWorldSelection()
    if (game.value) {
      const [games, characters] = await Promise.all([
        api<GamesResponse>('/games'),
        api<CharacterListResponse>(`/games/${encodeURIComponent(game.value)}/characters`).catch(() => ({ players: [] } as CharacterListResponse)),
      ])
      if (requestEpoch !== worldsRequestEpoch) return
      players.value = characters.players || []
      const cur = (games.games || []).find((g: GameSummary) => g.game_key === game.value)
      selectionUnchanged = currentWorldId.value === requestedWorldId && worldLanguage.value === requestedLanguage
      if (cur?.world_id && !preserveCurrentWorld && selectionUnchanged && !routeSelection) {
        currentWorldId.value = cur.world_id
        const activeWorld = worlds.value.find(w => worldIdOf(w) === cur.world_id)
        worldLanguage.value = activeWorld
          ? contentLanguageOf(activeWorld)
          : contentLanguageOf({ language: cur.language })
      }
    }
    if (!routeSelection && selectionUnchanged && !languageWorlds.value.some(w => worldIdOf(w) === currentWorldId.value)) {
      currentWorldId.value = worldIdOf(languageWorlds.value[0])
    }
    if (routeSelection && !currentWorldId.value && requestedBookId.value) {
      await loadStandaloneBookFromRoute()
      if (requestEpoch !== worldsRequestEpoch) return
    }
  } catch (e: unknown) {
    if (requestEpoch === worldsRequestEpoch) error.value = errorMessage(e)
  }
}

async function loadStandaloneBookFromRoute() {
  if (!requestedBookId.value || currentWorldId.value) return
  await loadLorebooks('')
  if (!currentWorldId.value) await loadLore('')
}

watch(currentWorldId, async () => {
  if (showEditWorld.value && editWorldId.value && editWorldId.value !== currentWorldId.value) closeEditWorld()
  if (!currentWorldId.value) return
  const selectedWorldId = currentWorldId.value
  await loadLorebooks(selectedWorldId)
  if (currentWorldId.value !== selectedWorldId) return
  await loadLore(selectedWorldId)
})
watch(worldLanguage, () => {
  if (requestedBookId.value && !hasRequestedWorld.value && !currentWorldId.value) return
  if (hasRequestedWorld.value) {
    // A query is a browsable target, not a lock on the original card. Once
    // the user changes the language filter, keep the current canonical world
    // when it is available; otherwise move to the first candidate and carry
    // that explicit choice back into the query.
    if (!currentWorldId.value) return
    if (languageWorlds.value.some(world => worldIdOf(world) === currentWorldId.value)) return
    const nextWorldId = worldIdOf(languageWorlds.value[0])
    if (!nextWorldId) return
    currentWorldId.value = nextWorldId
    return
  }
  if (languageWorlds.value.some(w => worldIdOf(w) === currentWorldId.value)) return
  currentWorldId.value = worldIdOf(languageWorlds.value[0])
  if (!currentWorldId.value) data.value = { entries: [] }
})
watch(locale, next => { if (!game.value && !hasRequestedWorld.value) worldLanguage.value = next })
watch(currentWorldId, next => {
  if (hasRequestedWorld.value && next && next !== requestedWorldId.value) syncWorldId(next)
})
watch(requestedWorldId, (next, previous) => {
  // The world query is part of the route target even when the canonical book
  // stays the same. Re-open the book through the all-books contract so a
  // standalone/orphan book cannot be lost to a world-scoped response, and
  // allow entry targeting to be applied again for the new route.
  if (next !== previous && requestedBookId.value) {
    routeBookSelectionConsumed.value = false
    routeEntrySelectionConsumed.value = false
  }
  if (!hasRequestedWorld.value) {
    if (requestedBookId.value) {
      applyRouteWorldSelection()
      void loadStandaloneBookFromRoute()
    } else void loadWorlds()
  } else if (worlds.value.length) {
    const routeSelection = applyRouteWorldSelection()
    if (routeSelection && !currentWorldId.value && requestedBookId.value) void loadStandaloneBookFromRoute()
  } else {
    void loadWorlds()
  }
})
watch(requestedBookId, async (next, previous) => {
  if (next === previous) return
  routeBookSelectionConsumed.value = false
  routeEntrySelectionConsumed.value = false
  routeBookError.value = ''
  if (!next) {
    if (hasRequestedWorld.value) applyRouteWorldSelection()
    else { await loadWorlds(); return }
  }
  if (!hasRequestedWorld.value) {
    applyRouteWorldSelection()
    await loadStandaloneBookFromRoute()
  } else {
    const selectedWorldId = currentWorldId.value
    await loadLorebooks(selectedWorldId)
    if (currentWorldId.value === selectedWorldId && (selectedWorldId || next)) await loadLore(selectedWorldId)
  }
})
watch(requestedEntryId, (next, previous) => {
  if (next === previous) return
  routeEntrySelectionConsumed.value = false
  if (!next) return
  const loadedEntry = entries.value.find(entry => entry.id === next)
  if (loadedEntry) {
    selectedEntryId.value = next
    detailOpen.value = true
    routeEntrySelectionConsumed.value = true
  } else if (!loreEdit.value) {
    void loadLore()
  }
})

// 只有旧服务端「没有这条 canonical 路由」才回退到 legacy adapter：aiohttp 的
// 路由缺失是无 JSON 体的 404/405。网络故障、权限错误、资源 404 或写入失败
// 都必须原样抛出，不能再发第二次 legacy 写入。
function canonicalRouteMissing(error: unknown, bookId: string): boolean {
  return bookId === `world:${currentWorldId.value}`
    && error instanceof ApiError
    && (error.status === 404 || error.status === 405)
    && !error.code
    && error.message === `HTTP ${error.status}`
}

async function selectWorld(worldId: string): Promise<boolean> {
  const canonical = String(worldId || '').trim()
  if (!canonical || !worlds.value.some(world => worldIdOf(world) === canonical)) return false
  if (canonical !== currentWorldId.value && !await allowLeaveEntry()) return false
  currentWorldId.value = canonical
  return true
}

async function loadLore(worldId = currentWorldId.value, bookIdOverride = activeBookId.value || (worldId ? `world:${worldId}` : requestedBookId.value)) {
  if (!worldId && !bookIdOverride) { data.value = { entries: [] }; return }
  if (routeBookError.value) { data.value = { entries: [] }; error.value = routeBookError.value; return }
  const selectionEpochAtStart = bookSelectionEpoch.value
  const isCurrentRequest = () => currentWorldId.value === worldId
    && activeBookId.value === bookIdOverride
    && bookSelectionEpoch.value === selectionEpochAtStart
  error.value = ''; data.value = { entries: [] }
  try {
    const bookId = bookIdOverride
    try {
      const result = await api<{ entries: LoreEntry[] }>(`/lorebooks/${encodeURIComponent(bookId)}/entries`)
      if (!isCurrentRequest()) return
      data.value = { entries: result.entries || [] }
    } catch (canonicalError) {
      // Older deployments expose the primary-world adapter only.  Keep this
      // fallback at the UI boundary while new servers use the canonical Book
      // route above; the adapter can disappear once all clients migrate.
      if (!canonicalRouteMissing(canonicalError, bookId)) throw canonicalError
      const fallback = await api<LorebookResponse>(`/lorebook/${encodeURIComponent(worldId)}`)
      if (!isCurrentRequest()) return
      data.value = fallback
    }
    // 换书/刷新后旧 id 不能再指向任何条目，否则批量操作会打向不存在的目标。
    const loaded = new Set((data.value.entries || []).map(entry => entry.id).filter(Boolean) as string[])
    bulkSelectedIds.value = new Set([...bulkSelectedIds.value].filter(id => loaded.has(id)))
    if (requestedEntryId.value && !routeEntrySelectionConsumed.value) {
      if (loaded.has(requestedEntryId.value)) {
        selectedEntryId.value = requestedEntryId.value
        detailOpen.value = true
      } else {
        selectedEntryId.value = ''
        detailOpen.value = false
        error.value = routeEntryNotFound(requestedEntryId.value, bookId)
      }
      routeEntrySelectionConsumed.value = true
    }
  } catch (e: unknown) {
    if (isCurrentRequest()) error.value = errorMessage(e)
  }
}

onMounted(loadWorlds)

// 管理菜单是零依赖的 details/summary：点击菜单外（或打开另一个菜单）时自动收起，
// 菜单项自身由 runBookMenu 收起。
function closeOpenLoreMenus(event: Event) {
  const anchor = (event.target as HTMLElement | null)?.closest('details.lore-menu')
  document.querySelectorAll('.lore-menu[open]').forEach(menu => {
    if (menu !== anchor) menu.removeAttribute('open')
  })
}
onMounted(() => document.addEventListener('click', closeOpenLoreMenus))
onBeforeUnmount(() => document.removeEventListener('click', closeOpenLoreMenus))

function openLore(entry?: LoreEntry, inline = false) {
  inlineEdit.value = inline && Boolean(entry?.id)
  if (entry?.id) selectedEntryId.value = entry.id
  detailOpen.value = inlineEdit.value
  editContext.value = { worldId: currentWorldId.value, bookId: activeBookId.value || `world:${currentWorldId.value}` }
  loreEdit.value = entry ? cloneLore(entry) : {
    name: '', type: 'npc', tier: 'background', keywords: [], content: '',
    match_mode: 'any', unreliable: false, sync_on_enter: false, is_constant: false,
    triggers_recursive: [], visible_to: [], connected_to: [], sticky: 0,
    cooldown: 0, delay: 0, order: 100, probability: 100, group: '', group_weight: 1,
    secondary_keys: [], selective: true, selective_logic: 'any', use_regex: false, case_sensitive: false,
    match_whole_words: false, scan_depth: 0, priority: 100, prompt_slot: 'world_background', groups: [],
    // canonical 默认与后端 payload.setdefault 对齐：显式发值必须等于后端默认，否则 UI 与库里不一致。
    vector_activation: 'hybrid',
    prioritize_inclusion: false,
    non_recursable: false, prevent_further_recursion: false, delay_until_recursion: false,
    recursion_level: 0,
  }
  normalizeActivationModel(loreEdit.value)
  visibilityMode.value = visibilityModeOf(loreEdit.value.visible_to)
  initialEntryDraft.value = JSON.stringify(loreEdit.value)
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
  if (!loreEdit.value || entrySaving.value || !editContext.value) return
  const { worldId, bookId } = editContext.value
  if (worldId !== currentWorldId.value || bookId !== (activeBookId.value || `world:${currentWorldId.value}`)) return
  normalizeVisibilityForSave()
  // 发出去的就是 UI 上看到的：不会出现「界面 off、库里 hybrid」。
  normalizeActivationModel(loreEdit.value)
  const entry: LoreEdit = { ...loreEdit.value, world_id: worldId }
  const savingDraft = loreEdit.value
  const path = entry.id
    ? `/lorebooks/${encodeURIComponent(bookId)}/entries/${encodeURIComponent(entry.id)}`
    : `/lorebooks/${encodeURIComponent(bookId)}/entries`
  entrySaving.value = true
  try {
    try {
      await api<unknown>(path, { method: entry.id ? 'PUT' : 'POST', body: JSON.stringify(entry) })
    } catch (canonicalError) {
      if (!canonicalRouteMissing(canonicalError, bookId)) throw canonicalError
      const legacyPath = entry.id ? `/lorebook/${encodeURIComponent(entry.id)}` : '/lorebook'
      await api<unknown>(legacyPath, { method: entry.id ? 'PUT' : 'POST', body: JSON.stringify(entry) })
    }
    toast.success(entry.id ? t('updated') : t('created'))
    if (loreEdit.value === savingDraft) loreEdit.value = null
    if (worldId !== currentWorldId.value || bookId !== (activeBookId.value || `world:${currentWorldId.value}`)) return
    await loadLore()
    await loadWorlds(true)
    await refreshPreview()
  } catch (e: unknown) { error.value = errorMessage(e) }
  finally { entrySaving.value = false }
}

async function deleteLore(entry: LoreEntry) {
  if (!entry.id) return
  const worldId = currentWorldId.value
  const bookId = activeBookId.value || `world:${worldId}`
  const ok = await confirm({ title: t('deleteLoreEntryTitle'), content: t('deleteLoreEntryContent', { name: entry.name || t('unnamedLoreEntry') }), positiveText: t('deleteLoreEntryAction'), type: 'error' })
  if (!ok) return
  if (worldId !== currentWorldId.value || bookId !== (activeBookId.value || `world:${currentWorldId.value}`)) return
  try {
    try {
      await api<unknown>(`/lorebooks/${encodeURIComponent(bookId)}/entries/${encodeURIComponent(entry.id)}`, { method: 'DELETE' })
    } catch (canonicalError) {
      if (!canonicalRouteMissing(canonicalError, bookId)) throw canonicalError
      await api<unknown>(`/lorebook/${encodeURIComponent(entry.id)}`, { method: 'DELETE' })
    }
    toast.success(t('deleted'))
    if (selectedEntryId.value === entry.id) selectedEntryId.value = ''
    await loadLore()
    await loadWorlds(true)
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
    await loadWorlds(true)
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
      body: JSON.stringify({
        prompt: generatePrompt.value,
        language: currentWorld.value?.language || locale.value,
        book_id: currentBookId.value,
        ...(game.value ? { game_key: game.value } : {}),
      }),
    })
    toast.success(t('aiGeneratedEntries', { count: r.count || (r.entries?.length || 0) }))
    generatePrompt.value = ''
    await loadLore()
    await loadWorlds()
    await refreshPreview()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function onWorldCreated(payload: { worldId: string; definition: { language: string } }) {
  showNewWorld.value = false
  await loadWorlds()
  worldLanguage.value = contentLanguageOf({ language: payload.definition.language })
  currentWorldId.value = payload.worldId
  toast.success(t('worldCreated'))
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

async function loadLorebooks(worldId = currentWorldId.value) {
  const requestEpoch = ++lorebookListRequestEpoch
  if (!worldId && !requestedBookId.value) { lorebookBooks.value = []; usageRefreshToken.value += 1; return }
  const linkedBookId = requestedBookId.value
  const language = worldLanguage.value
  const retainLinkedBook = !!linkedBookId && (!routeBookSelectionConsumed.value || activeBookId.value === linkedBookId)
  const isCurrentRequest = () => currentWorldId.value === worldId && requestEpoch === lorebookListRequestEpoch
    && requestedBookId.value === linkedBookId && worldLanguage.value === language
  try {
    const query = new URLSearchParams({ world_id: worldId })
    if (game.value) query.set('game_key', game.value)
    if (!worldId) query.set('scope', 'all')
    const result = await api<LorebookListResponse>(`/lorebooks?${query.toString()}`)
    if (!isCurrentRequest()) return
    const books = [...(result.books || [])]
    // An explicit library link can open an independent/orphaned book, but
    // only that exact book may supplement the world-scoped response.
    if (worldId && retainLinkedBook && !books.some(book => book.id === linkedBookId)) {
      const catalogue = await api<LorebookListResponse>('/lorebooks?scope=all')
      if (!isCurrentRequest()) return
      const linked = (catalogue.books || []).find(book => book.id === linkedBookId)
      if (linked) books.push(linked)
    }
    lorebookBooks.value = lorebookChoices(books, language, retainLinkedBook ? linkedBookId : '')
      .map(book => ({ ...book, primary: book.primary || book.id === `world:${worldId}` }))
    if (requestedBookId.value && !routeBookSelectionConsumed.value) {
      if (lorebookBooks.value.some(book => book.id === requestedBookId.value)) {
        activeBookId.value = requestedBookId.value
        routeBookError.value = ''
      } else {
        activeBookId.value = ''
        routeBookError.value = routeBookNotFound(requestedBookId.value)
      }
      routeBookSelectionConsumed.value = true
    } else if (!activeBookId.value || !lorebookBooks.value.some(book => book.id === activeBookId.value)) {
      activeBookId.value = lorebookBooks.value.find(book => book.id === `world:${worldId}`)?.id
        || lorebookBooks.value[0]?.id || (worldId ? `world:${worldId}` : '')
      routeBookError.value = ''
    }
  } catch {
    if (!isCurrentRequest()) return
    const world = worlds.value.find(candidate => worldIdOf(candidate) === worldId)
    const fallbackBookId = `world:${worldId}`
    lorebookBooks.value = world ? [{ id: fallbackBookId, name: worldNameOf(world), primary: true, scope: 'world', entry_count: typeof world.entry_count === 'number' ? world.entry_count : undefined }] : []
    if (world && !requestedBookId.value) activeBookId.value = fallbackBookId
    if (requestedBookId.value) {
      activeBookId.value = ''
      routeBookError.value = routeBookNotFound(requestedBookId.value)
    }
  } finally {
    if (requestEpoch === lorebookListRequestEpoch) usageRefreshToken.value += 1
  }
}

// Async imports may refresh the Book list after the user has already switched
// context. Keep the user's later selection authoritative.
const bookSelectionEpoch = ref(0)

async function selectLorebook(bookId: string) {
  if (!lorebookBooks.value.some(item => item.id === bookId)) return
  if (bookId !== currentBookId.value && !await allowLeaveEntry()) return
  bookSelectionEpoch.value += 1
  activeBookId.value = bookId
  routeBookError.value = ''
  routeBookSelectionConsumed.value = true
  routeEntrySelectionConsumed.value = true
  void loadLore()
}

/** 导入目标与 binding 全部来自对话框里用户的显式选择，不再隐式塞主世界书。 */
async function confirmLoreImport(decision: LoreImportDecision) {
  if (importBusy.value || !importPreview.value || !currentWorldId.value) return
  importBusy.value = true
  const selectionEpochAtStart = bookSelectionEpoch.value
  const worldIdAtStart = currentWorldId.value
  try {
    if (!pendingImportPayload.value || typeof pendingImportPayload.value !== 'object') throw new Error(t('importFailed'))
    const body: Record<string, unknown> = { payload: pendingImportPayload.value }
    if (importPreview.value.plan_digest) body.plan_digest = importPreview.value.plan_digest
    body.duplicate_policy = decision.duplicatePolicy || 'update'
    if (decision.bookId) body.book_id = decision.bookId
    if (decision.binding) {
      body.binding = decision.bookId && decision.bookId === primaryBookId.value
        ? { ...decision.binding, role: 'primary' }
        : decision.binding
    }
    const result = await api<{ ok: boolean; book_id?: string }>('/lorebooks/import', { method: 'POST', body: JSON.stringify(body) })
    await loadLorebooks()
    if (result?.book_id && worldIdAtStart === currentWorldId.value && selectionEpochAtStart === bookSelectionEpoch.value) {
      await selectLorebook(result.book_id)
    }
    await loadLore(); await loadWorlds(true); await refreshPreview()
    // Completion is visible only once every import-owned refresh settled.
    // A later Book selection cannot be overwritten by this import.
    importDialogOpen.value = false
    pendingImportPayload.value = undefined
    importPreview.value = undefined
    toast.success(t('importedLorebook'))
  } catch (err: unknown) { error.value = `${t('importFailed')}: ${errorMessage(err)}` }
  finally { importBusy.value = false }
}

// ---- Book 管理（create / rename / delete / enable / bindings）---------------

const primaryBookId = computed(() => (currentWorldId.value ? `world:${currentWorldId.value}` : ''))
const bindingsDialogOpen = ref(false)
const bindingsBook = ref<LorebookCard | null>(null)
const bookBindings = ref<LoreBinding[]>([])

function isPrimaryBook(book: LorebookCard | null | undefined): boolean {
  return !!book && (book.primary === true || book.id === primaryBookId.value)
}

/** 当前编辑的 Book：显式选择优先，回落到世界主世界书。 */
const currentBookId = computed(() => activeBookId.value || primaryBookId.value)
const currentBook = computed(() => lorebookBooks.value.find(book => book.id === currentBookId.value) || null)
const currentBookIsPrimary = computed(() => isPrimaryBook(currentBook.value))
const SCOPE_LABEL_KEYS: Record<string, MessageKey> = {
  world: 'loreScopeWorld', game: 'loreScopeGame', character: 'loreScopeCharacter', global: 'loreScopeGlobal',
}
const currentBookScopeLabel = computed(() => {
  const scope = String(currentBook.value?.scope || '')
  if (!currentWorldId.value && scope === 'world' && currentBookWorldUnavailable.value) return copy.value.independentBookScope
  return scope ? t(SCOPE_LABEL_KEYS[scope] ?? 'loreBooksUnbound') : t('loreBooksUnbound')
})
const currentBookWorldUnavailable = computed(() => {
  const ref = currentBook.value?.ref
  const boundWorlds = (currentBook.value?.bindings || [])
    .filter(binding => binding.scope_kind === 'world' && binding.scope_id)
    .map(binding => binding.scope_id)
  const referencedWorlds = boundWorlds.length ? boundWorlds
    : ref?.kind === 'lorebook' && ref.source_kind === 'world' && ref.source_id ? [ref.source_id] : []
  return referencedWorlds.length > 0 && referencedWorlds.every(id => !worlds.value.some(world => worldIdOf(world) === id))
})
function bookScopeLabel(book: LorebookCard): string {
  const scope = String(book.scope || '')
  if (book.id === currentBookId.value && scope === 'world' && currentBookWorldUnavailable.value) return copy.value.independentBookScope
  return scope ? t(SCOPE_LABEL_KEYS[scope] ?? 'loreBooksUnbound') : t('loreBooksUnbound')
}
function bookOptionLabel(book: LorebookCard): string {
  const count = copy.value.entryCount.replace('{count}', book.entry_count == null ? '—' : String(book.entry_count))
  const disabled = book.enabled === false ? t('loreBookDisabledSuffix') : ''
  return `${book.name} · ${bookScopeLabel(book)} · ${count}${disabled}`
}

/**
 * 世界下拉里显示的是「这个世界总共多少资料」，不是某一本书的条目数。
 * 世界只有主册时两个数字必然相等，所以单书不写本数（避免看起来重复）；
 * 多书时才点出「N 本」，否则玩家会把它当成主册的条目数。
 */
function worldOptionLabel(world: WorldSummary): string {
  const entries = t('entriesCount', { count: world.entry_count || 0 })
  const books = Number(world.book_count || 0)
  return books > 1
    ? `${worldNameOf(world)} · ${t('booksCount', { count: books })} · ${entries}`
    : `${worldNameOf(world)} · ${entries}`
}

/** 菜单项点击后先收起菜单，再执行既有 handler；参数由调用处的 closure 显式绑定。 */
function runBookMenu(event: Event, action: () => void | Promise<void>) {
  (event.currentTarget as HTMLElement | null)?.closest('details')?.removeAttribute('open')
  void action()
}
function triggerImport() { fileInput.value?.click() }

/** 绑定目标用的角色名单：Lore 视角已经加载过 players，这里不再重复请求。 */
const bindableCharacters = computed(() =>
  players.value
    .map(player => ({
      uid: String(player.user_id || ''),
      name: String(player.character_name || player.user_id || ''),
    }))
    .filter(item => item.uid),
)

function openBookCreate() {
  if (currentWorldId.value) {
    bookCreateError.value = ''
    bookCreateOpen.value = true
  }
}

function onLoreContextSummaryClick(event: MouseEvent) {
  const summary = event.currentTarget as HTMLElement | null
  const details = summary?.parentElement as HTMLDetailsElement | null
  if (details?.open) event.preventDefault()
}

function closeBookCreate() {
  bookCreateOpen.value = false
  bookCreateError.value = ''
}

function bindingId(bookId: string, binding: NonNullable<LorebookCreateDecision['binding']>): string {
  return `bind:${bookId}:${binding.scope_kind}:${binding.scope_id || 'global'}`
}

async function bindBook(bookId: string, binding: NonNullable<LorebookCreateDecision['binding']>) {
  const result = await api<{ ok?: boolean; error?: string }>(`/lorebooks/${encodeURIComponent(bookId)}/bindings`, {
    method: 'POST',
    body: JSON.stringify({
      id: bindingId(bookId, binding),
      ...binding,
    }),
  })
  if (result?.ok === false) throw new Error(result.error || copy.value.bindingFailed)
}

async function createBook(decision: LorebookCreateDecision) {
  if (!currentWorldId.value) return
  busy.value = true
  bookCreateError.value = ''
  try {
    const id = `book:${Date.now().toString(36)}`
    const result = await api<{ ok?: boolean; error?: string; book?: LorebookCard }>('/lorebooks', {
      method: 'POST',
      body: JSON.stringify({ id, name: decision.name, language: worldLanguage.value }),
    })
    if (result.ok === false) throw new Error(result.error || t('createFailed'))
    closeBookCreate()
    pendingBookBinding.value = null
    if (decision.binding) {
      try {
        await bindBook(id, decision.binding)
        toast.success(t('loreBookCreated'))
      } catch (bindingError: unknown) {
        pendingBookBinding.value = { bookId: id, bookName: decision.name, binding: decision.binding }
        error.value = `${copy.value.bindingFailed} ${errorMessage(bindingError)}`
      }
    } else {
      toast.success(t('loreBookCreated'))
    }
    await loadLorebooks()
    selectLorebook(id)
  } catch (e: unknown) {
    const message = errorMessage(e)
    bookCreateError.value = message
    error.value = message
  } finally { busy.value = false }
}

async function retryBookBinding() {
  const pending = pendingBookBinding.value
  if (!pending) return
  busy.value = true
  try {
    await bindBook(pending.bookId, pending.binding)
    pendingBookBinding.value = null
    error.value = ''
    toast.success(copy.value.bindingRetried)
    await loadLorebooks()
    selectLorebook(pending.bookId)
  } catch (e: unknown) {
    error.value = `${copy.value.bindingFailed} ${errorMessage(e)}`
  } finally { busy.value = false }
}

async function renameBook(book: LorebookCard) {
  const name = window.prompt(t('loreBookRenamePrompt'), book.name)
  if (!name || !name.trim() || name.trim() === book.name) return
  busy.value = true
  try {
    await api(`/lorebooks/${encodeURIComponent(book.id)}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) })
    await loadLorebooks()
    toast.success(t('loreBookRenamed'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function removeBook(book: LorebookCard) {
  if (book.primary || book.id === primaryBookId.value) return
  const ok = await confirm({
    title: copy.value.deleteTitle,
    content: locale.value === 'zh-CN' ? `删除资料册「${book.name}」？其中的条目会一并删除。`
      : locale.value === 'ja' ? `資料集「${book.name}」を削除しますか？エントリも削除されます。`
        : locale.value === 'de' ? `Lorebook „${book.name}“ löschen? Die Einträge werden ebenfalls gelöscht.`
          : locale.value === 'ru' ? `Удалить лорбук «${book.name}»? Его записи также будут удалены.`
            : `Delete lorebook “${book.name}”? Its entries will also be removed.`,
    positiveText: copy.value.deleteAction,
    type: 'error',
  })
  if (!ok) return
  busy.value = true
  try {
    await api(`/lorebooks/${encodeURIComponent(book.id)}`, { method: 'DELETE' })
    if (activeBookId.value === book.id) activeBookId.value = primaryBookId.value
    await loadLorebooks(); await loadLore()
    toast.success(copy.value.deleted)
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
      <main class="lorebook-workspace">
    <header class="view-title archive-hero" data-testid="lorebook-header">
      <div>
        <span class="section-kicker">WORLD LORE</span>
        <h1>{{ copy.pageTitle }}</h1>
        <p v-if="game">{{ t('currentSave') }}: {{ game }}</p>
        <p v-else class="muted">{{ t('standaloneLorebookHint') }}</p>
      </div>
      <div class="lore-header-actions">
        <a class="lore-world-gallery-link" href="#/worlds">{{ copy.backToWorlds }}</a>
        <button v-if="bulkBookId" :disabled="busy" @click="openBookSettings">{{ copy.settingsTitle }}</button>
        <button @click="toggleInspector">{{ t('loreInspectorToggle') }}</button>
        <button @click="loadWorlds(true)">{{ t('refresh') }}</button>
      </div>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <details class="lore-context-switcher" open>
      <summary data-testid="lore-context-crumbs" @click="onLoreContextSummaryClick">
        <span>{{ copy.contextWorld }} <strong>{{ worldNameOf(currentWorld) || (currentBook ? copy.independentContext : copy.worldLabel) }}</strong></span>
        <span aria-hidden="true">/</span>
        <span>{{ copy.contextBook }} <strong>{{ currentBook?.name || copy.bookLabel }}</strong></span>
        <small>{{ copy.contextEntry }} {{ selectedEntry?.name || entries.length }}</small>
      </summary>
    <div class="lore-context-bar" data-testid="lore-context">
      <div class="lore-context-row lore-context-row--language">
        <label class="lore-language-filter">
          <span>{{ t('contentLanguage') }}</span>
          <select :value="worldLanguage" @change="chooseWorldLanguage">
            <option v-for="option in CONTENT_LANGUAGE_OPTIONS" :key="option.value" :value="option.value">{{ t(option.labelKey) }}</option>
          </select>
        </label>
      </div>
      <div class="lore-selection-grid">
      <div class="lore-context-row lore-context-row--world">
        <span class="lore-context-label">{{ copy.worldLabel }}</span>
        <div class="lore-context-controls">
        <select :value="currentWorldId" class="lore-context-select" :aria-label="copy.worldLabel" @change="changeWorldSelection">
          <option value="" disabled>{{ currentBook ? copy.independentContext : t('chooseWorldEllipsis') }}</option>
          <option v-for="w in languageWorlds" :key="worldIdOf(w)" :value="worldIdOf(w)">{{ worldOptionLabel(w) }}</option>
        </select>
        <details class="lore-menu">
          <summary class="lore-menu-trigger" data-testid="world-actions">{{ copy.manageWorld }}</summary>
          <div class="lore-menu-list">
            <button type="button" @click="runBookMenu($event, () => toggleNewWorld())">{{ t('newWorld') }}</button>
            <button v-if="currentWorldId" type="button" :disabled="busy" @click="runBookMenu($event, openEditWorld)">{{ t('worldsActionEdit') }}</button>
            <button v-if="currentWorldId" type="button" class="danger" :disabled="busy" @click="runBookMenu($event, () => deleteWorld())">{{ t('deleteWorldAction') }}</button>
          </div>
        </details>
        </div>
        <p class="lore-book-meta lore-context-hint">{{ copy.worldBookHint }}</p>
      </div>
      <div class="lore-context-row lore-context-row--book">
        <span class="lore-context-label">{{ copy.bookLabel }}</span>
        <div class="lore-context-controls">
        <select
          v-if="lorebookBooks.length > 1"
          class="lore-context-select"
          :value="currentBookId"
          :aria-label="copy.bookLabel"
          @change="changeBookSelection"
        >
          <option v-for="book in lorebookBooks" :key="book.id" :value="book.id">{{ bookOptionLabel(book) }}</option>
        </select>
        <span v-else-if="currentBook" class="lore-book-static" :aria-label="copy.bookLabel">
          {{ currentBook.name }}
        </span>
        <span v-if="currentBook" class="lore-book-state" :class="{ off: currentBook.enabled === false }" :title="currentBook.enabled === false ? usage.disabledState : usage.enabledState">
          {{ currentBook.enabled === false ? usage.disabledState : usage.enabledState }}
        </span>
        <span
          v-if="currentBook"
          class="lore-scope-chip"
          :class="{ unbound: !currentBook.scope }"
          data-testid="lore-scope-chip"
        >
          <small>{{ copy.usageScopeLabel }}</small>
          <strong>{{ currentBookScopeLabel }}</strong>
        </span>
        <button class="primary success" :disabled="busy || !currentWorldId" @click="openBookCreate">{{ copy.createTitle }}</button>
        <details class="lore-menu">
          <summary class="lore-menu-trigger" data-testid="book-actions">{{ copy.manageBook }}</summary>
          <div class="lore-menu-list">
            <button type="button" :disabled="busy || !currentBook" @click="runBookMenu($event, () => renameBook(currentBook!))">{{ t('loreBooksRename') }}</button>
            <button type="button" :disabled="busy || !currentBook" @click="runBookMenu($event, () => openBindings(currentBook!))">{{ t('loreBookUsageScope') }}</button>
            <button type="button" :disabled="busy || !currentBook" @click="runBookMenu($event, () => toggleBookEnabled(currentBook!))">
              {{ currentBook?.enabled === false ? t('loreBooksEnable') : t('loreBooksDisable') }}
            </button>
            <button type="button" :disabled="busy || !data?.entries?.length" @click="runBookMenu($event, () => exportLore())">{{ t('export') }}</button>
            <button type="button" :disabled="busy || !currentWorldId" @click="runBookMenu($event, () => triggerImport())">{{ t('import') }}</button>
            <button v-if="currentBook && !currentBookIsPrimary" type="button" class="danger" :disabled="busy" @click="runBookMenu($event, () => removeBook(currentBook!))">{{ copy.deleteAction }}</button>
            <p v-if="currentBook && currentBookIsPrimary" class="lore-menu-note">{{ copy.primaryManagementHint }}</p>
          </div>
        </details>
        <p v-if="currentBook" class="lore-book-meta">
          <template v-if="currentBookIsPrimary">{{ copy.primaryBadge }} · </template>{{ copy.entryCount.replace('{count}', currentBook.entry_count == null ? '—' : String(currentBook.entry_count)) }}
          <span class="sr-only">{{ copy.currentBookCount.replace('{count}', currentBook.entry_count == null ? '—' : String(currentBook.entry_count)) }}</span>
          <span> · {{ t('loreBookContextScope') }}：{{ currentBookScopeLabel }}</span>
        </p>
        </div>
      </div>
      </div>
      <div v-if="pendingBookBinding" class="lore-book-binding-warning" role="status">
        <span>{{ pendingBookBinding.bookName }} · {{ copy.bindingFailed }}</span>
        <button type="button" class="lore-book-binding-retry" :disabled="busy" @click="retryBookBinding">{{ copy.retryBinding }}</button>
      </div>
    </div>

    </details>

    <p v-if="currentBook && !currentWorldId && (routeWorldError || currentBookWorldUnavailable)" class="lore-context-warning" role="status">{{ routeWorldError || copy.independentBookScope }} {{ copy.independentBrowseHint }}</p>

    <LorebookUsageSummary
      :game-key="game"
      :viewer="effectiveViewer"
      :refresh-token="usageRefreshToken"
    />
    <LorebookRecoveryDialog v-if="recoveryDialogOpen" :open="recoveryDialogOpen" @close="recoveryDialogOpen = false" @changed="onRecoveryChanged" />

    <details v-if="showNewWorld" class="ai-block lore-world-creator" open>
      <summary>{{ t('newWorld') }}</summary>
      <WorldDefinitionEditor
        mode="create"
        :show-heading="false"
        :initial-language="worldLanguage"
        @saved="onWorldCreated"
        @cancel="showNewWorld = false"
      />
    </details>

    <details v-if="showEditWorld" class="ai-block lore-world-editor" open>
      <summary>{{ t('worldsEditTitle') }}</summary>
      <WorldDefinitionEditor
        :world-id="editWorldId"
        :show-heading="false"
        mode="edit"
        :source="String(currentWorld?.source || 'user')"
        :initial-name="editWorldInitial.name"
        :initial-description="editWorldInitial.description"
        :initial-language="editWorldInitial.language"
        @saved="onWorldDefinitionSaved"
        @cloned="onWorldDefinitionCloned"
        @cancel="closeEditWorld"
      />
    </details>

    <div class="lore-tools">
      <button class="primary success" :disabled="!currentBook" @click="openLore()">{{ t('addLoreEntry') }}</button>
      <input v-model="generatePrompt" :placeholder="t('generateLorePlaceholder')">
      <div class="lore-generation-actions">
        <button @click="generateLore" :disabled="busy || !currentWorldId">{{ t('aiGenerate') }}</button>
        <button type="button" data-testid="content-recovery-open" @click="recoveryDialogOpen = true">{{ recovery.title }}</button>
      </div>
      <!-- Import / Export 是 Book 生命周期操作，唯一入口在左侧 Books 区。 -->
      <input ref="fileInput" type="file" accept="application/json" @change="importLore" hidden>
    </div>

    <p class="memory-meta" v-if="currentWorldId">
      {{ worldNameOf(currentWorld) || currentWorldId }} · {{ t(contentLanguageLabelKey(worldLanguage)) }} · {{ t('lorebookEntryCount', { count: entries.length }) }}
    </p>

    <div class="lore-entry-workbench" :class="{ 'detail-open': detailOpen }">
    <section class="lore-entry-list-pane">
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
        <details class="lore-more-filters"><summary>{{ workspaceCopy.filters }}</summary><div>
        <select v-model="entryFilters.visibility" class="lore-entry-filter lore-filter-visibility" :aria-label="t('loreVisibilityLabel')">
          <option value="all">{{ t('loreVisibilityLabel') }} · {{ t('allLoreTypes') }}</option>
          <option value="public">{{ t('loreVisibilityPublic') }}</option>
          <option value="characters">{{ t('loreVisibilityCharacters') }}</option>
          <option value="gm">{{ t('loreAudienceGmSecret') }}</option>
        </select>
        <select v-model="entryFilters.state" class="lore-entry-filter lore-filter-state" :aria-label="t('status')">
          <option value="all">{{ t('status') }} · {{ t('allLoreTypes') }}</option>
          <option value="enabled">{{ t('enabled') }}</option>
          <option value="disabled">{{ t('disabled') }}</option>
        </select>
        <select v-model="entryFilters.activation" class="lore-entry-filter lore-filter-activation" :aria-label="t('loreActivationMode')">
          <option value="">{{ t('loreActivationMode') }} · {{ t('allLoreTypes') }}</option>
          <option v-for="mode in activationOptions" :key="mode" :value="mode">{{ productActivationLabel(mode) }}</option>
        </select>
        <select v-model="entryFilters.source" class="lore-entry-filter lore-filter-source" :aria-label="t('source')">
          <option value="all">{{ t('source') }} · {{ t('allLoreTypes') }}</option>
          <option v-for="item in sourceOptions" :key="item" :value="item">{{ item }}</option>
        </select>
        </div></details>
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
                <button class="lore-entry-name" @click.stop="toggleEntrySelection(e)">{{ e.name || t('unnamedLoreEntry') }}</button>
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
              <button @click.stop="openLore(e, true)">{{ t('edit') }}</button>
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

    <section v-else-if="currentBook && !busy" class="empty-panel">
      <h2>{{ t('noLoreEntries') }}</h2>
      <p class="muted">{{ t('noLoreEntriesHint') }}</p>
    </section>

    <section v-else-if="!currentWorldId && !busy && !showNewWorld" class="empty-panel">
      <h2>{{ t('chooseWorldEllipsis') }}</h2>
      <p class="muted">{{ t('standaloneLorebookHint') }}</p>
    </section>

    </section>
    <aside class="lore-detail-pane">
      <button v-if="detailOpen" class="lore-detail-back" data-testid="lore-detail-back" @click="returnToEntries">← {{ workspaceCopy.back }}</button>
    <LoreEntryEditorFrame v-if="loreEdit" :inline="inlineEdit" :title="loreEdit.id ? t('editLoreEntry') : t('newLoreEntry')" @close="loreEdit = null">
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
      <template #actions><button @click="loreEdit = null">{{ t('cancel') }}</button><button class="primary" :disabled="entrySaving" @click="saveLore">{{ t('saveAction') }}</button></template>
    </LoreEntryEditorFrame>
      <LoreEntryDetail v-if="selectedEntry && !loreEdit" :entry="selectedEntry" :projection="selectedProjection" :type-label="typeLabel(selectedEntry.type)" :activation-label="productActivationLabel(loreProductActivation(selectedEntry))" @edit="openLore(selectedEntry, true)" @remove="deleteLore(selectedEntry)" />
      <section v-else-if="!loreEdit" class="lore-detail-empty"><h2>{{ t('loreEntry') }}</h2><p>{{ workspaceCopy.choose }}</p></section>
    </aside>
    </div>

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

    <Modal v-if="bookSettingsOpen" :title="copy.settingsTitle" @close="bookSettingsOpen = false">
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
      :busy="importBusy"
      :preview="importPreview"
      :books="lorebookBooks"
      :characters="bindableCharacters"
      :world-id="currentWorldId"
      :world-name="worldNameOf(currentWorld)"
      :game-key="game"
      :primary-book-id="primaryBookId"
      @close="!importBusy && (importDialogOpen = false)"
      @confirm="confirmLoreImport"
    />
    <LorebookCreateDialog
      :open="bookCreateOpen"
      :world-id="currentWorldId"
      :world-name="worldNameOf(currentWorld)"
      :game-key="game || undefined"
      :busy="busy"
      :error="bookCreateError"
      @close="closeBookCreate"
      @submit="createBook"
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

<style scoped>
.lorebook-page .lore-tools {
  grid-template-columns: auto minmax(0, 1fr) auto;
}
.lorebook-page .lore-tools > input {
  min-width: 0;
}
.lore-generation-actions {
  display: flex;
  align-items: stretch;
  gap: 8px;
  min-width: 0;
}
.lore-generation-actions button {
  min-width: 0;
  white-space: normal;
}
.lore-menu-note {
  max-width: 28rem;
  margin: 0;
  padding: .45rem .65rem;
  color: var(--df-text-muted);
  font-size: .78rem;
  line-height: 1.45;
}
.lore-context-warning {
  margin: .75rem 0 0;
  padding: .65rem .8rem;
  border: 1px solid color-mix(in srgb, var(--df-warning, #c88a22) 40%, transparent);
  border-radius: 10px;
  color: var(--df-text-muted);
  font-size: .85rem;
}
@media (max-width: 800px) {
  .lorebook-page .lore-tools {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .lore-generation-actions {
    grid-column: 1 / -1;
  }
}
</style>
