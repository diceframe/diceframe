<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { MapOutline, RefreshOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { api, errorMessage, hasAccessToken } from '@/api/client'
import type { GamesResponse, GameSummary } from '@/api/types'
import { fetchTacticalScenes, type TacticalSceneProjection, type TacticalSceneTokenDocument, type TacticalSceneWriteResponse } from '@/api/tacticalScene'
import { readCurrentGame, rememberCurrentGame, queryString } from '@/stores/gameContext'
import { useLocale } from '@/composables/useLocale'
import { tacticalSceneCopy, tacticalImportCopy, tacticalEditorCopy, tacticalPortableCopy } from './copy'
import Board from './Board.vue'
import ImportWizard from './ImportWizard.vue'
import PortableImportDialog from './PortableImportDialog.vue'
import SceneEditor from './SceneEditor.vue'
import CreateSceneDialog from './CreateSceneDialog.vue'
import TokenDetail from './TokenDetail.vue'
import TokenBindDialog from './TokenBindDialog.vue'
import { useBoardProjection } from './useBoardProjection'
import { isGmSceneDocument } from './useSceneEditor'
import './tactical-scene.css'

const route = useRoute(), router = useRouter()
const { locale } = useLocale()
const copy = computed(() => tacticalSceneCopy(locale.value))
const importCopy = computed(() => tacticalImportCopy(locale.value))
const portableCopy = computed(() => tacticalPortableCopy(locale.value))
const editorCopy = computed(() => tacticalEditorCopy(locale.value))
const showImport = ref(false)
const showPortableImport = ref(false)
const showCreate = ref(false)
const editingSceneId = ref('')
const games = ref<GameSummary[]>([])
const gameKey = ref('')
const scenes = ref<TacticalSceneProjection[]>([])
const activeSceneRef = ref<string | null>(null)
const selectedSceneId = ref('')
const loadingGames = ref(false), loadingScenes = ref(false), error = ref('')
let requestId = 0
const { scene: boardScene, revision: boardRevision, loading: boardLoading, error: boardError, retry: boardRetry } =
  useBoardProjection(gameKey, selectedSceneId)

const selectedTokenId = ref('')
const bindingToken = ref<TacticalSceneTokenDocument | null>(null)

const selectedToken = computed(() => {
  const scene = boardScene.value
  if (!scene || !selectedTokenId.value) return null
  return scene.placeables.tokens[selectedTokenId.value] ?? null
})

// Ownership is a GM-only scene field; seat projections never carry it.
const boardOwnership = computed(() => {
  const scene = boardScene.value
  return scene && isGmSceneDocument(scene) ? scene.ownership : null
})

function onSelectToken(id: string) {
  selectedTokenId.value = id
}

function openBind() {
  const scene = boardScene.value
  if (!scene || !isGmSceneDocument(scene) || !selectedTokenId.value) return
  const token = scene.placeables.tokens[selectedTokenId.value]
  if (token) bindingToken.value = token
}

function onBound(_response: TacticalSceneWriteResponse) {
  // Re-read the projection so the board reflects the new actor_ref; the dialog
  // stays open with its own success state.
  void boardRetry()
}

watch(selectedSceneId, () => {
  selectedTokenId.value = ''
  bindingToken.value = null
})

// Same identity rule as useGame: the owner session is GM unless a player
// preview is named; a named user is GM only when it matches gm_uid. This is a
// UI affordance — every write is still enforced server-side.
const routeUser = computed(() => queryString(route.query.user))
const isGm = computed(() => {
  const game = games.value.find(item => item.game_key === gameKey.value)
  if (!game) return false
  const gmUid = game.gm_uid || ''
  return (!routeUser.value && hasAccessToken()) || (!!routeUser.value && routeUser.value === gmUid)
})

function sceneName(scene: TacticalSceneProjection): string {
  return scene.display_name || scene.canonical_id
}

async function loadGames() {
  loadingGames.value = true
  error.value = ''
  try {
    const response = await api<GamesResponse>('/games')
    games.value = response.games || []
    const requested = queryString(route.query.game) || readCurrentGame()
    gameKey.value = games.value.some(game => game.game_key === requested)
      ? requested
      : games.value[0]?.game_key || ''
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loadingGames.value = false
  }
}

async function loadScenes() {
  const key = gameKey.value
  const current = ++requestId
  if (!key) {
    scenes.value = []
    activeSceneRef.value = null
    loadingScenes.value = false
    return
  }
  loadingScenes.value = true
  error.value = ''
  try {
    const response = await fetchTacticalScenes(key)
    if (current !== requestId) return
    scenes.value = response.scenes
    activeSceneRef.value = response.active_scene_ref
    const ids = response.scenes.map(scene => scene.canonical_id)
    if (!ids.includes(selectedSceneId.value)) {
      const active = response.active_scene_ref
      selectedSceneId.value = active && ids.includes(active) ? active : ids[0] || ''
    }
  } catch (cause) {
    if (current === requestId) {
      scenes.value = []
      activeSceneRef.value = null
      error.value = errorMessage(cause)
    }
  } finally {
    if (current === requestId) loadingScenes.value = false
  }
}

function selectGame(value: string) {
  if (!games.value.some(game => game.game_key === value)) return
  gameKey.value = value
  const game = games.value.find(item => item.game_key === value)
  rememberCurrentGame(value, game?.world_name || '')
  router.replace({ query: { ...route.query, game: value } })
}

watch(gameKey, () => { selectedSceneId.value = ''; showImport.value = false; showPortableImport.value = false; showCreate.value = false; editingSceneId.value = ''; void loadScenes() })
watch(() => queryString(route.query.game), (value) => {
  if (value && value !== gameKey.value && games.value.some(game => game.game_key === value)) gameKey.value = value
})
onMounted(() => { void loadGames() })
onBeforeUnmount(() => { requestId += 1 })

function openEditor(sceneId: string) {
  selectedSceneId.value = sceneId
  editingSceneId.value = sceneId
}

function onSceneCreated(sceneId: string) {
  showCreate.value = false
  void loadScenes()
  openEditor(sceneId)
}

function onEditorSaved() {
  void loadScenes()
}

function onEditorDeleted() {
  editingSceneId.value = ''
  selectedSceneId.value = ''
  void loadScenes()
}
</script>

<template>
  <main class="tactical-scene-page view" data-testid="tactical-scene-page">
    <header class="tactical-scene-header">
      <div>
        <span class="tactical-scene-kicker">{{ copy.kicker }}</span>
        <h1><NIcon :component="MapOutline" /> {{ copy.title }}</h1>
        <p>{{ copy.subtitle }}</p>
      </div>
      <div class="tactical-scene-actions">
        <label v-if="games.length" class="tactical-scene-game-picker">
          <span>{{ copy.chooseGame }}</span>
          <select :value="gameKey" @change="selectGame(($event.target as HTMLSelectElement).value)">
            <option v-for="game in games" :key="game.game_key" :value="game.game_key">{{ game.world_name || game.game_key }}</option>
          </select>
        </label>
        <button
          v-if="gameKey && isGm"
          type="button"
          class="tactical-scene-import-button"
          data-testid="tactical-scene-create-open"
          @click="showCreate = true"
        >{{ editorCopy.newScene }}</button>
        <button
          v-if="gameKey"
          type="button"
          class="tactical-scene-import-button"
          data-testid="tactical-import-open"
          @click="showImport = true"
        >{{ importCopy.importScene }}</button>
        <button
          v-if="gameKey && isGm"
          type="button"
          class="tactical-scene-import-button"
          data-testid="tactical-portable-import-open"
          @click="showPortableImport = true"
        >{{ portableCopy.open }}</button>
        <RouterLink class="tactical-scene-back-link" :to="{ name: 'play', query: gameKey ? { game: gameKey } : undefined }">{{ copy.backToPlay }}</RouterLink>
      </div>
    </header>

    <p v-if="error" class="tactical-scene-error" role="alert">
      {{ error }}
      <button type="button" @click="gameKey ? loadScenes() : loadGames()"><NIcon :component="RefreshOutline" /> {{ copy.retry }}</button>
    </p>
    <section v-if="loadingGames || loadingScenes" class="tactical-scene-state" role="status">{{ copy.loading }}</section>
    <section v-else-if="!games.length" class="tactical-scene-state">{{ copy.emptyGames }}</section>
    <section v-else-if="!scenes.length" class="tactical-scene-state">{{ copy.emptyScenes }}</section>
    <ul v-else class="tactical-scene-list">
      <li
        v-for="scene in scenes"
        :key="scene.canonical_id"
        class="tactical-scene-item"
        :class="{ selected: scene.canonical_id === selectedSceneId }"
        data-testid="tactical-scene-item"
      >
        <button
          type="button"
          class="tactical-scene-select"
          :aria-pressed="scene.canonical_id === selectedSceneId"
          @click="selectedSceneId = scene.canonical_id"
        >
          <span class="tactical-scene-name">{{ sceneName(scene) }}</span>
          <span v-if="scene.canonical_id === activeSceneRef" class="tactical-scene-active-badge">{{ copy.activeScene }}</span>
        </button>
        <button
          v-if="isGm"
          type="button"
          class="tactical-scene-edit-button"
          data-testid="tactical-scene-edit"
          @click="openEditor(scene.canonical_id)"
        >{{ editorCopy.edit }}</button>
      </li>
    </ul>
    <p v-if="gameKey && !isGm && scenes.length" class="tactical-scene-gm-note" data-testid="tactical-scene-gm-note">{{ editorCopy.gmOnly }}</p>

    <template v-if="selectedSceneId">
      <SceneEditor
        v-if="isGm && editingSceneId === selectedSceneId"
        :game-key="gameKey"
        :scene-id="selectedSceneId"
        @close="editingSceneId = ''"
        @saved="onEditorSaved"
        @deleted="onEditorDeleted"
        @activated="onEditorSaved"
      />
      <template v-else>
        <p v-if="boardError" class="tactical-scene-error" role="alert" data-testid="tactical-board-error">
          {{ boardError }}
          <button type="button" @click="boardRetry()"><NIcon :component="RefreshOutline" /> {{ copy.retry }}</button>
        </p>
        <section v-else-if="boardLoading" class="tactical-scene-state" role="status">{{ copy.loadingBoard }}</section>
        <div v-else-if="boardScene" class="tactical-board-layout">
          <Board
            :scene="boardScene"
            :game-key="gameKey"
            :selected-token-id="selectedTokenId"
            @select-token="onSelectToken"
          />
          <TokenDetail
            v-if="selectedToken"
            :token="selectedToken"
            :is-gm="isGm"
            :ownership="boardOwnership"
            @bind="openBind"
            @close="selectedTokenId = ''"
          />
        </div>
      </template>
    </template>

    <TokenBindDialog
      v-if="bindingToken && isGm && gameKey"
      :game-key="gameKey"
      :scene-id="selectedSceneId"
      :token="bindingToken"
      :revision="boardRevision"
      @close="bindingToken = null"
      @bound="onBound"
    />

    <CreateSceneDialog
      v-if="showCreate && gameKey"
      :game-key="gameKey"
      @close="showCreate = false"
      @created="onSceneCreated"
    />
    <ImportWizard
      v-if="showImport && gameKey"
      :game-key="gameKey"
      @close="showImport = false"
      @imported="loadScenes"
    />
    <PortableImportDialog
      v-if="showPortableImport && gameKey && isGm"
      :game-key="gameKey"
      @close="showPortableImport = false"
      @imported="loadScenes"
    />
  </main>
</template>