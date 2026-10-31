<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api, apiBlob, errorMessage } from '@/api/client'
import type { BatchDeleteGamesResponse, GameMutationResponse, GamesResponse, GameSummary, SceneImageRef } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import { clearCurrentGame, rememberCurrentGame } from '@/stores/gameContext'
import { NIcon } from 'naive-ui'
import {
  BookOutline, HourglassOutline, PeopleOutline, DiceOutline, TrophyOutline,
  CompassOutline, EnterOutline, RefreshOutline, DownloadOutline, TrashOutline,
  LinkOutline, ImageOutline, HelpCircleOutline,
  SearchOutline, GridOutline, ListOutline,
} from '@vicons/ionicons5'
import PeerConnectModal from '@/features/peer/PeerConnectModal.vue'
import { pruneGameSceneImageCache, resolveGameSceneImageUrl, sceneImageStyle } from '@/api/sceneImages'
import { ruleSceneUrl } from '@/composables/useBackgroundImages'
import { getRendezvousConfig } from '@/api/peer'
import { sortGames, type SaveSortMode } from './saveSorting'
import { readOverviewGamesCache, writeOverviewGamesCache } from './overviewCache'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import GameSceneImageDialog from '@/components/common/GameSceneImageDialog.vue'
import GettingStartedCard from '@/features/onboarding/GettingStartedCard.vue'
import { gettingStartedCopy } from '@/features/onboarding/copy'
import { useGettingStartedGuide } from '@/features/onboarding/useGettingStartedGuide'
import { appTourCopy } from '@/features/onboarding/tourCopy'
import { startAppTour } from '@/features/onboarding/useAppTour'

const router = useRouter()
const overviewRoot = ref<HTMLElement | null>(null)
const lastToolsSummary = ref<HTMLElement | null>(null)
const peerModalOpen = ref(false)
const coverTarget = ref<GameSummary | null>(null)

const toast = useToast()
const { confirm } = useConfirm()
const { locale, t } = useLocale()
const guideCopy = computed(() => gettingStartedCopy(locale.value))
const tourCopy = computed(() => appTourCopy(locale.value))
const { guideDismissed, guideCard, guideLauncher, dismissGuide, reopenGuide } = useGettingStartedGuide()
const overviewCopy = computed(() => {
  const lang = locale.value
  if (lang === 'zh-CN') return { loading: '正在读取存档…', management: '存档管理', actions: '存档操作', selected: '已选择', adventureLibrary: '浏览内容' }
  if (lang === 'ja') return { loading: 'セーブを読み込み中…', management: 'セーブ管理', actions: 'セーブ操作', selected: '選択済み', adventureLibrary: 'コンテンツを見る' }
  if (lang === 'de') return { loading: 'Spielstände werden geladen…', management: 'Spielstandverwaltung', actions: 'Spielstandaktionen', selected: 'Ausgewählt', adventureLibrary: 'Inhalte durchsuchen' }
  if (lang === 'ru') return { loading: 'Загрузка сохранений…', management: 'Управление сохранениями', actions: 'Действия с сохранением', selected: 'Выбрано', adventureLibrary: 'Обзор контента' }
  return { loading: 'Loading saves…', management: 'Save management', actions: 'Save actions', selected: 'Selected', adventureLibrary: 'Browse content' }
})

const games = ref<GameSummary[]>(readOverviewGamesCache())
const peerEntryVisible = ref(false)
const saveSort = ref<SaveSortMode>('recent')
const libraryQuery = ref('')
const libraryView = ref<'grid' | 'list'>('grid')
const sortedGames = computed(() => sortGames(games.value, saveSort.value, locale.value))
const featuredGame = computed(() => sortGames(games.value, 'recent', locale.value)[0])
const visibleGames = computed(() => {
  const query = libraryQuery.value.trim().toLocaleLowerCase(locale.value)
  if (!query) return sortedGames.value
  return sortedGames.value.filter(game => [game.world_name, game.game_key, game.scene, game.rule_id]
    .filter(Boolean)
    .some(value => String(value).toLocaleLowerCase(locale.value).includes(query)))
})
const sceneImageUrls = ref<Record<string, string>>({})
// Keep a local, lightweight scene visible while per-save artwork is fetched.
// The archive index is intentionally rendered before those optional requests
// finish, so every card needs a real first-paint image instead of a flat fill.
const immediateSceneFallback = '/v2-assets/ui/campaign-mountain-city.jpg'
let loadGeneration = 0
let overviewDisposed = false
let overviewActive = false
const error = ref('')
function setError(e: unknown) { error.value = errorMessage(e) }
const busy = ref(false)
const loading = ref(false)
const selected = ref<string[]>([])
const selectionMode = ref(false)

const activeGames = computed(() => games.value.filter(g => stateClass(g.state) === 'badge-active').length)
const playerCount = computed(() => games.value.reduce((sum, g) => sum + Number(g.player_count || 0), 0))
const roundCount = computed(() => games.value.reduce((sum, g) => sum + Number(g.round_number || 0), 0))
const latestScene = computed(() => sortedGames.value.find(g => g.scene)?.scene || t('noScene'))
const statItems = computed(() => [
  { key: 'saves', value: games.value.length, label: t('totalSaves'), icon: BookOutline },
  { key: 'active', value: activeGames.value, label: t('activeGames'), icon: HourglassOutline },
  { key: 'players', value: playerCount.value, label: t('playerSlots'), icon: PeopleOutline },
  { key: 'rounds', value: roundCount.value, label: t('totalRounds'), icon: DiceOutline },
  { key: 'scene', value: latestScene.value, label: t('latestScene'), icon: TrophyOutline, wide: true },
])

async function load() {
  const generation = ++loadGeneration
  // Keep an already-rendered archive visible while /games refreshes.  This
  // is what makes a route return feel instant; the fresh index can update in
  // place instead of replacing the page with a loading state first.
  loading.value = games.value.length === 0
  error.value = ''
  try {
    const r = await api<GamesResponse>('/games')
    if (overviewDisposed || generation !== loadGeneration) return
    games.value = r.games || []
    writeOverviewGamesCache(games.value)
    pruneGameSceneImageCache(games.value.map(game => game.game_key))
    // Render the archive list as soon as the lightweight index arrives. Scene
    // artwork is optional decoration and can be hydrated in the background;
    // a slow image endpoint must not leave the whole homepage in a loading
    // state when there are many saves.
    loading.value = false
    const snapshot = [...games.value]
    // Resolve built-in references immediately and retain cached custom URLs.
    // The image endpoint is only touched for a new/changed custom reference.
    sceneImageUrls.value = Object.fromEntries(snapshot.map(game => [
      game.game_key,
      gameSceneUrlFromReference(game),
    ]))
    void Promise.allSettled(snapshot.map(async game => [
      game.game_key,
      await resolveGameSceneImageUrl(
        game.game_key,
        String(game.rule_id || ''),
        false,
        game.scene_image || null,
      ),
    ] as const)).then(results => {
      // A refresh may have started while artwork was resolving. Do not let a
      // stale response replace the newer archive map.
      if (overviewDisposed || generation !== loadGeneration) {
        return
      }
      const resolved = new Map(
        results
          .filter((result): result is PromiseFulfilledResult<readonly [string, string]> => result.status === 'fulfilled')
          .map(result => result.value),
      )
      // Keep each card's immediate/reference image if one optional custom
      // artwork request fails. A single stale or missing asset must not make
      // every cover wait for the next route refresh.
      sceneImageUrls.value = Object.fromEntries(snapshot.map(game => [
        game.game_key,
        resolved.get(game.game_key) || sceneImageUrls.value[game.game_key] || immediateSceneFallback,
      ]))
    })
  } catch (e: unknown) {
    if (!overviewDisposed && generation === loadGeneration) setError(e)
  } finally {
    if (!overviewDisposed && generation === loadGeneration) loading.value = false
  }
}

async function loadPeerEntryVisibility() {
  peerEntryVisible.value = false
  try {
    const config = await getRendezvousConfig()
    peerEntryVisible.value = config.entry_visible === true
  } catch {
    // Fail closed: an unavailable Hub cannot establish a new rendezvous session.
  }
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
  for (const g of sortedGames.value) {
    await exportGame(g.game_key)
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  toast.success(t('exportDone'))
}

const saveImportInput = ref<HTMLInputElement | null>(null)

async function onImportSave(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const r = await api<{ ok?: boolean; game_key?: string; error?: string; player_access_closed?: boolean }>('/games/import', {
      method: 'POST',
      body: form,
    })
    if (!r.ok) throw new Error(r.error || t('importFailed'))
    toast.success(t('saveImported', { name: r.game_key || '' }))
    // Room passwords never travel with a save: a protected game arrives closed.
    if (r.player_access_closed) toast.warning(t('importClosedPlayerAccess'))
    await load()
  } catch (e: unknown) { setError(e) } finally { busy.value = false; input.value = '' }
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
    const deletedKeys = new Set(r.deleted || [])
    for (const key of deletedKeys) clearCurrentGame(key)
    selected.value = selected.value.filter(key => !deletedKeys.has(key))
    await load()
  } catch (e: unknown) { setError(e) } finally { busy.value = false }
}

function enterSelectionMode() { selectionMode.value = true }
function exitSelectionMode() { selectionMode.value = false; selected.value = [] }
function selectAll() { selected.value = games.value.map(g => g.game_key) }
function selectInvert() {
  const set = new Set(selected.value)
  selected.value = games.value.filter(g => !set.has(g.game_key)).map(g => g.game_key)
}
function clearSelection() { selected.value = [] }

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

function gameSceneStyle(game: GameSummary): Record<string, string> {
  return sceneImageStyle(sceneImageUrls.value[game.game_key] || immediateSceneFallback)
}

function gameSceneUrl(game: GameSummary): string {
  return sceneImageUrls.value[game.game_key] || immediateSceneFallback
}

function gameSceneUrlFromReference(game: GameSummary): string {
  const reference = game.scene_image
  if (!reference || reference.kind === 'builtin') {
    return ruleSceneUrl(reference?.id || String(game.rule_id || ''))
  }
  // Keep the previous custom cover during a background refresh. A changed
  // reference is replaced when the cached resolver finishes, avoiding a
  // visible fallback flash on route return.
  return sceneImageUrls.value[game.game_key] || immediateSceneFallback
}

function openToolDetails(): HTMLElement[] {
  return Array.from(overviewRoot.value?.querySelectorAll<HTMLElement>('.overview-archive-tools[open], .overview-game-tools[open]') || [])
}

function closeTools(restoreFocus = false) {
  const summary = lastToolsSummary.value
  openToolDetails().forEach(details => { details.removeAttribute('open') })
  lastToolsSummary.value = null
  if (restoreFocus && summary) void nextTick(() => summary.focus())
}

function changeCover(game: GameSummary) {
  const summary = lastToolsSummary.value
  closeTools()
  summary?.focus()
  coverTarget.value = { ...game }
}

async function onCoverSaved(gameKey: string, reference: SceneImageRef) {
  // Invalidate artwork and index reads started before this confirmed change.
  loadGeneration += 1
  games.value = games.value.map(game => game.game_key === gameKey ? { ...game, scene_image: reference } : game)
  writeOverviewGamesCache(games.value)
  const savedGame = games.value.find(game => game.game_key === gameKey)
  if (savedGame) {
    const savedReference = JSON.stringify(reference)
    // The confirmed cover can render even when the separate index refresh fails.
    void resolveGameSceneImageUrl(gameKey, String(savedGame.rule_id || ''), false, reference).then(url => {
      if (!overviewDisposed && JSON.stringify(games.value.find(game => game.game_key === gameKey)?.scene_image) === savedReference) {
        sceneImageUrls.value = { ...sceneImageUrls.value, [gameKey]: url }
      }
    }, () => { /* Optional artwork can be fetched again on the next refresh. */ })
  }
  await load()
}

function onToolsToggle(event: Event) {
  const details = event.currentTarget as HTMLDetailsElement
  if (!details.open) return
  lastToolsSummary.value = details.querySelector('summary') as HTMLElement | null
  lastToolsSummary.value?.focus()
  openToolDetails().forEach(other => {
    if (other !== details) other.removeAttribute('open')
  })
}

function onOverviewPointerDown(event: PointerEvent) {
  const target = event.target as Node | null
  if (target && !(target as Element).closest?.('.overview-game-tools, .overview-archive-tools')) closeTools()
}

function onOverviewKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && openToolDetails().length) {
    event.preventDefault()
    closeTools(true)
  }
}

function activateOverview() {
  if (overviewActive) return
  overviewActive = true
  overviewDisposed = false
  void load()
  void loadPeerEntryVisibility()
  document.addEventListener('pointerdown', onOverviewPointerDown)
  document.addEventListener('keydown', onOverviewKeydown)
}

function deactivateOverview() {
  if (!overviewActive) return
  overviewActive = false
  overviewDisposed = true
  coverTarget.value = null
  document.removeEventListener('pointerdown', onOverviewPointerDown)
  document.removeEventListener('keydown', onOverviewKeydown)
  closeTools()
}

onMounted(activateOverview)
onActivated(activateOverview)
onDeactivated(deactivateOverview)
onBeforeUnmount(deactivateOverview)
</script>

<template>
  <section ref="overviewRoot" class="view overview-page">
    <div class="overview-layout">
    <div class="overview-main">
        <ContentPageHeader
          class="overview-hero"
          kicker="GAME ARCHIVE"
          :title="t('overviewTitle')"
          :heading-aria-label="t('overviewTitle')"
          :description="t('overviewSubtitle')"
        >
          <template #title-actions>
            <button ref="guideLauncher" type="button" class="overview-guide-launcher" data-df-ui="button" data-testid="overview-open-guide" @click="reopenGuide">
              <NIcon :component="HelpCircleOutline" aria-hidden="true" />{{ guideCopy.reopen }}
            </button>
            <button type="button" class="overview-guide-launcher" data-df-ui="button" data-testid="overview-start-tour" @click="startAppTour(locale)">
              <NIcon :component="CompassOutline" aria-hidden="true" />{{ tourCopy.replay }}
            </button>
          </template>
          <template #actions>
            <div class="overview-actions">
              <button data-testid="overview-open-join" class="overview-entry-action peer-launch-button" @click="peerModalOpen = true"><NIcon :component="LinkOutline" />{{ t('friendJoinTitle') }}</button>
              <RouterLink data-testid="overview-adventure-library" class="overview-entry-action content-button" :to="{ name: 'library' }">{{ overviewCopy.adventureLibrary }}</RouterLink>
              <button data-testid="overview-create-adventure" data-tour="create-game" class="overview-entry-action primary" @click="play('')">{{ t('createAdventure') }}</button>
              <button v-if="error" data-testid="overview-load-retry" type="button" @click="load" :disabled="busy"><NIcon :component="RefreshOutline" />{{ t('retry') }}</button>
              <!-- With no archive yet the library toolbar does not exist, so keep
                   an import entry here as a small empty-state escape hatch. -->
              <button v-if="!loading && !games.length" class="overview-empty-import" @click="saveImportInput?.click()" :disabled="busy">{{ t('importSave') }}</button>
              <input ref="saveImportInput" type="file" accept=".zip" @change="onImportSave" hidden>
            </div>
          </template>
        </ContentPageHeader>

    <GettingStartedCard
      v-if="!guideDismissed && !loading && !error"
      ref="guideCard"
      :has-games="games.length > 0"
      @connect="peerModalOpen = true"
      @dismiss="dismissGuide"
    />

    <Card v-if="featuredGame" as="section" class="overview-featured" data-testid="overview-featured">
      <div class="overview-featured-art" :style="gameSceneStyle(featuredGame)" aria-hidden="true">
        <!-- The featured cover is already cached when returning from another
             route; synchronous decode prevents a one-frame blank/old fallback
             while the browser schedules an async decode. -->
        <img :src="gameSceneUrl(featuredGame)" alt="" fetchpriority="high" decoding="sync">
      </div>
      <div class="overview-featured-copy">
        <span class="section-kicker">{{ t('recentAdventures') }}</span>
        <h2>{{ featuredGame.world_name || featuredGame.game_key }}</h2>
        <div class="overview-featured-details">
          <p>{{ featuredGame.scene || t('notStarted') }}</p>
          <div class="overview-featured-facts">
            <span>{{ t('roundPrefix') }}{{ featuredGame.round_number || 0 }}{{ t('roundSuffix') }}</span>
            <span>{{ t('players') }} {{ featuredGame.player_count || 0 }}/{{ featuredGame.max_players || 0 }}</span>
            <span>{{ stateLabel(featuredGame.state) }}</span>
          </div>
          <Button variant="primary" size="lg" class="overview-featured-enter" @click="play(featuredGame.game_key)"><span>{{ t('enter') }}</span><NIcon :component="EnterOutline" /></Button>
        </div>
      </div>
    </Card>

    <details class="overview-stats-disclosure" open>
      <summary>{{ t('archiveStats') }}</summary>
    <section class="overview-stats" :aria-label="t('archiveStats')">
      <article v-for="item in statItems" :key="item.key" :class="{ wide: item.wide }">
        <NIcon :component="item.icon" />
        <div><strong>{{ item.value }}</strong><span>{{ item.label }}</span></div>
      </article>
    </section>
    </details>

    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <section v-if="loading" class="empty-panel overview-loading" data-testid="overview-loading" role="status"><h2>{{ overviewCopy.loading }}</h2></section>

    <section v-else-if="games.length" class="adventure-library">
      <header class="library-heading">
        <div class="library-heading-copy"><span><i />{{ t('recentAdventures') }}</span><small>{{ games.length }} {{ t('totalSaves') }}</small></div>
        <div class="library-heading-actions" :class="{ 'is-selection-mode': selectionMode }">
          <label class="library-search-field">
            <NIcon :component="SearchOutline" aria-hidden="true" />
            <input v-model="libraryQuery" type="search" :placeholder="locale === 'zh-CN' ? '搜索存档、世界或场景' : 'Search saves, worlds, or scenes'" :aria-label="locale === 'zh-CN' ? '搜索存档' : 'Search saves'">
          </label>
          <label class="save-sort-field">
            <span>{{ t('saveSort') }}</span>
            <select v-model="saveSort" class="save-sort-select" :aria-label="t('saveSort')">
              <option value="recent">{{ t('saveSortRecent') }}</option>
              <option value="oldest">{{ t('saveSortOldest') }}</option>
              <option value="name">{{ t('saveSortName') }}</option>
              <option value="round">{{ t('saveSortRound') }}</option>
            </select>
          </label>
          <button v-if="!selectionMode" class="overview-select-saves" data-testid="overview-select-saves" @click="enterSelectionMode">{{ t('chooseSave') }}</button>
          <template v-else>
            <span class="overview-selection-count">{{ selected.length }} {{ overviewCopy.selected }}</span>
            <button @click="selectAll">{{ t('selectAll') }}</button>
            <button @click="selectInvert">{{ t('invertSelection') }}</button>
            <button @click="clearSelection" :disabled="!selected.length">{{ t('clearSelection') }}</button>
            <button data-testid="overview-exit-selection" @click="exitSelectionMode">{{ t('cancel') }}</button>
            <button v-if="selected.length" class="danger" @click="batchRemove" :disabled="busy">{{ t('deleteSelected') }} {{ selected.length }}</button>
          </template>
          <button class="overview-import-saves" @click="saveImportInput?.click()" :disabled="busy">{{ t('importSave') }}</button>
          <details class="overview-archive-tools" @toggle="onToolsToggle">
            <summary>{{ overviewCopy.management }}</summary>
            <div>
              <button @click="closeTools(true); exportAll()" :disabled="busy">{{ t('exportAll') }}</button>
            </div>
          </details>
          <div class="library-view-toggle" role="group" :aria-label="locale === 'zh-CN' ? '存档视图' : 'Save view'">
            <button type="button" :class="{ active: libraryView === 'grid' }" :aria-pressed="libraryView === 'grid'" :title="locale === 'zh-CN' ? '网格视图' : 'Grid view'" @click="libraryView = 'grid'"><NIcon :component="GridOutline" /></button>
            <button type="button" :class="{ active: libraryView === 'list' }" :aria-pressed="libraryView === 'list'" :title="locale === 'zh-CN' ? '列表视图' : 'List view'" @click="libraryView = 'list'"><NIcon :component="ListOutline" /></button>
          </div>
        </div>
      </header>
      <div v-if="visibleGames.length" class="game-grid" :class="{ 'is-list': libraryView === 'list' }">
        <article v-for="g in visibleGames" :key="g.game_key" :data-game-key="g.game_key" class="game-card" :class="{ 'is-selected': selected.includes(g.game_key) }">
          <div class="game-card-art" :style="gameSceneStyle(g)" aria-hidden="true">
            <img :src="gameSceneUrl(g)" alt="" loading="lazy" decoding="async">
          </div>
          <div class="game-card-cover">
            <span class="cover-sigil"><NIcon :component="CompassOutline" /></span>
            <label v-if="selectionMode" class="game-select compact">
              <input type="checkbox" :value="g.game_key" v-model="selected" :aria-label="t('chooseSave')">
              <span>{{ t('choose') }}</span>
            </label>
            <div class="game-card-badges">
              <small class="badge" :class="stateClass(g.state)">{{ stateLabel(g.state) }}</small>
              <small v-if="g.solo_mode" class="badge badge-active">{{ t('solo') }}</small>
              <small v-else class="badge">{{ t('multiplayer') }}</small>
            </div>
          </div>
          <div class="game-card-body">
            <h2 :title="g.world_name || g.game_key">{{ g.world_name || g.game_key }}</h2>
            <p class="scene-line">{{ g.scene || t('notStarted') }}</p>
            <div class="game-card-meta">
              <span>{{ t('roundPrefix') }}{{ g.round_number || 0 }}{{ t('roundSuffix') }}</span>
              <span>{{ t('players') }} {{ g.player_count || 0 }}/{{ g.max_players || 0 }}</span>
              <span>LLM {{ g.total_llm_calls || 0 }}</span>
            </div>
            <p class="muted meta">{{ t('token') }} {{ g.total_tokens || 0 }}<span v-if="g.seed_code"> · {{ t('seed') }} <code>{{ g.seed_code }}</code></span></p>
            <div class="game-card-actions">
              <Button variant="primary" size="lg" class="game-card-enter" @click="play(g.game_key)"><span>{{ t('enter') }}</span><NIcon :component="EnterOutline" /></Button>
                <details class="overview-game-tools" @toggle="onToolsToggle"><summary>{{ overviewCopy.actions }}</summary><div class="game-card-tools">
                <button @click="closeTools(true); exportGame(g.game_key)"><NIcon :component="DownloadOutline" />{{ t('export') }}</button>
                <button type="button" data-testid="save-change-cover" @click="changeCover(g)" :disabled="busy"><NIcon :component="ImageOutline" />{{ t('worldsActionChangeCover') }}</button>
                <button @click="closeTools(true); restartGame(g.game_key)" :disabled="busy"><NIcon :component="RefreshOutline" />{{ t('restart') }}</button>
                <button @click="closeTools(true); resetGame(g.game_key)" :disabled="busy"><NIcon :component="CompassOutline" />{{ t('reset') }}</button>
                <button class="danger" @click="closeTools(true); remove(g.game_key)" :disabled="busy"><NIcon :component="TrashOutline" />{{ t('delete') }}</button>
                </div></details>
            </div>
          </div>
        </article>
      </div>
      <section v-else class="empty-panel overview-filter-empty"><h2>{{ locale === 'zh-CN' ? '没有匹配的存档' : 'No matching saves' }}</h2><button type="button" @click="libraryQuery = ''">{{ locale === 'zh-CN' ? '清除搜索' : 'Clear search' }}</button></section>
    </section>

    <section v-else-if="!loading && !error" class="empty-panel" data-testid="overview-empty">
      <h2>{{ t('emptyTitle') }}</h2>
      <p class="muted">{{ t('emptySubtitle') }}</p>
      <button class="primary" @click="play('')">{{ t('createAdventure') }}</button>
    </section>
      </div>

      <PeerConnectModal v-model:show="peerModalOpen" :peer-available="peerEntryVisible" />
      <GameSceneImageDialog
        v-if="coverTarget"
        :key="coverTarget.game_key"
        :game-key="coverTarget.game_key"
        :rule-id="String(coverTarget.rule_id || '')"
        :game-name="coverTarget.world_name || coverTarget.game_key"
        @close="coverTarget = null"
        @saved="onCoverSaved"
      />
    </div>
  </section>
</template>

<style scoped>
.overview-layout {
  position: relative;
  min-height: 0;
}
.overview-main {
  width: 100%;
  min-width: 0;
}
.overview-guide-launcher {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 44px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--df-radius-sm);
  color: var(--df-text-muted);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}
.overview-guide-launcher:hover { color: var(--df-accent-strong); background: var(--df-hover); }
.overview-guide-launcher:focus-visible { outline: 2px solid var(--df-interactive-strong); outline-offset: 2px; }
.overview-guide-launcher .n-icon { font-size: 16px; }

</style>

<style src="./overview.css"></style>
