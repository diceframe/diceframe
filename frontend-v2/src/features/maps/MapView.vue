<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { MapOutline, RefreshOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { api, errorMessage, hasAccessToken } from '@/api/client'
import type { GamesResponse, GameSummary, MapData } from '@/api/types'
import MapWorkspace from '@/components/play/MapWorkspace.vue'
import { revokeMapBackgroundAsset } from '@/api/mapBackgrounds'
import { convertMapBackgroundToTacticalScene } from '@/api/tacticalScene'
import { readCurrentGame, rememberCurrentGame, queryString } from '@/stores/gameContext'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import { mapCopy } from './copy'
import { loadMapProjection, shouldApplyMapResponse } from './mapLoader'
import './map-experience.css'

const route = useRoute(), router = useRouter()
const { locale } = useLocale()
const copy = computed(() => mapCopy(locale.value))
const games = ref<GameSummary[]>([])
const gameKey = ref('')
const map = ref<MapData | null>(null)
const loadingGames = ref(false), loadingMap = ref(false), error = ref('')
let requestId = 0

const selectedGame = computed(() => games.value.find(game => game.game_key === gameKey.value))
const currentScene = computed(() => map.value?.current_scene || selectedGame.value?.scene || '')

// Same identity rule as useGame / TacticalSceneView: the owner session is GM
// unless a player preview is named. The write itself is enforced server-side.
const routeUser = computed(() => queryString(route.query.user))
const isGm = computed(() => {
  const game = selectedGame.value
  if (!game) return false
  return (!routeUser.value && hasAccessToken()) || (!!routeUser.value && routeUser.value === (game.gm_uid || ''))
})
const converting = ref(false)
const toast = useToast()

function setError(cause: unknown) { error.value = errorMessage(cause) }

async function convertToTacticalScene() {
  if (!gameKey.value || converting.value) return
  converting.value = true
  try {
    const result = await convertMapBackgroundToTacticalScene(gameKey.value)
    toast.success(result.created ? copy.value.convertCreated : copy.value.convertReused)
    router.push({ name: 'tactical-scene', query: { game: gameKey.value } })
  } catch (cause) {
    toast.error(errorMessage(cause))
  } finally {
    converting.value = false
  }
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
    setError(cause)
  } finally {
    loadingGames.value = false
  }
}

async function loadMap() {
  const key = gameKey.value
  const current = ++requestId
  if (!key) {
    revokeMapBackgroundAsset(map.value)
    map.value = null
    loadingMap.value = false
    return
  }
  loadingMap.value = true
  error.value = ''
  try {
    const next = await loadMapProjection(key)
    if (!shouldApplyMapResponse(current, requestId)) {
      revokeMapBackgroundAsset(next)
      return
    }
    revokeMapBackgroundAsset(map.value)
    map.value = next
  } catch (cause) {
    if (shouldApplyMapResponse(current, requestId)) {
      revokeMapBackgroundAsset(map.value)
      map.value = null
      setError(cause)
    }
  } finally {
    if (shouldApplyMapResponse(current, requestId)) loadingMap.value = false
  }
}

function selectGame(value: string) {
  if (!games.value.some(game => game.game_key === value)) return
  gameKey.value = value
  const game = games.value.find(item => item.game_key === value)
  rememberCurrentGame(value, game?.world_name || '')
  router.replace({ query: { ...route.query, game: value } })
}

function backToPlay() {
  router.push({ name: 'play', query: gameKey.value ? { game: gameKey.value } : undefined })
}

watch(gameKey, () => { void loadMap() })
watch(() => queryString(route.query.game), (value) => {
  if (value && value !== gameKey.value && games.value.some(game => game.game_key === value)) gameKey.value = value
})
onMounted(() => { void loadGames() })
onBeforeUnmount(() => {
  requestId += 1
  revokeMapBackgroundAsset(map.value)
})
</script>

<template>
  <main class="map-page view" data-testid="map-page">
    <header class="map-page-header">
      <div>
        <span class="map-page-kicker">CAMPAIGN MAP</span>
        <h1><NIcon :component="MapOutline" /> {{ copy.title }}</h1>
        <p>{{ copy.readOnly }}</p>
      </div>
      <div class="map-page-actions">
        <label v-if="games.length" class="map-game-picker">
          <span>{{ copy.chooseGame }}</span>
          <select :value="gameKey" @change="selectGame(($event.target as HTMLSelectElement).value)">
            <option v-for="game in games" :key="game.game_key" :value="game.game_key">{{ game.world_name || game.game_key }}</option>
          </select>
        </label>
        <button
          v-if="isGm && map"
          type="button"
          class="map-convert-button"
          :disabled="converting"
          data-testid="map-convert-tactical"
          @click="convertToTacticalScene"
        >{{ converting ? copy.convertBusy : copy.convertAction }}</button>
        <RouterLink class="map-back-link" :to="{ name: 'play', query: gameKey ? { game: gameKey } : undefined }">{{ copy.backToPlay }}</RouterLink>
      </div>
    </header>

    <p v-if="error" class="map-page-error" role="alert">{{ copy.error }} <button type="button" @click="gameKey ? loadMap() : loadGames()"><NIcon :component="RefreshOutline" /> {{ copy.retry }}</button></p>
    <section v-if="loadingGames || loadingMap" class="map-page-state" role="status">{{ copy.loading }}</section>
    <section v-else-if="!games.length" class="map-page-state">{{ copy.emptyGames }}</section>
    <section v-else-if="!map" class="map-page-state">{{ copy.emptyMap }}</section>
    <MapWorkspace
      v-else
      embedded
      :map="map"
      :current-scene="currentScene"
      @close="backToPlay"
    />
  </main>
</template>
