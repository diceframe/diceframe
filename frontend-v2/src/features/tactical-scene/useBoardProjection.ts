import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { errorMessage, gameEventSource, isNotFoundError } from '@/api/client'
import {
  fetchTacticalScene,
  isTacticalSceneUpdatedEvent,
  type TacticalSceneProjection,
  type TacticalSceneUpdatedEvent,
} from '@/api/tacticalScene'

const RECONNECT_DELAY_MS = 5000
const POLL_INTERVAL_MS = 30000

/**
 * Read-only board projection for one scene. The server already applied the
 * viewer whitelist (GM document vs seat projection); whatever arrives here is
 * rendered as-is, never re-filtered. Live updates follow the slot revision:
 * an event at or below the revision we already hold is stale and ignored.
 */
export function useBoardProjection(gameKey: Ref<string>, sceneId: Ref<string>) {
  const scene = ref<TacticalSceneProjection | null>(null)
  const revision = ref(0)
  const loading = ref(false)
  const error = ref('')

  let source: EventSource | null = null
  let reconnectTimer: number | undefined
  let pollTimer: number | undefined
  let connectVersion = 0
  let loadVersion = 0

  async function load(silent = false) {
    const key = gameKey.value
    const id = sceneId.value
    const version = ++loadVersion
    if (!key || !id) return
    if (!silent) {
      loading.value = true
      error.value = ''
    }
    try {
      const response = await fetchTacticalScene(key, id)
      if (version !== loadVersion) return
      scene.value = response.scene
      revision.value = response.revision
      error.value = ''
    } catch (cause) {
      if (version !== loadVersion) return
      if (isNotFoundError(cause)) {
        // The scene was deleted or the game is gone: surface it even silently.
        scene.value = null
        revision.value = 0
        error.value = errorMessage(cause)
      } else if (!silent || !scene.value) {
        error.value = errorMessage(cause)
      }
    } finally {
      if (version === loadVersion) loading.value = false
    }
  }

  function applyEvent(event: TacticalSceneUpdatedEvent) {
    if (event.scene_id !== sceneId.value) return
    if (event.revision <= revision.value) return
    if (event.projection === null) {
      // Deleted (or no longer decodable) scene: the event asks for a re-read.
      void load(true)
      return
    }
    scene.value = event.projection
    revision.value = event.revision
  }

  function clearTimers() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = undefined }
    if (pollTimer) { clearInterval(pollTimer); pollTimer = undefined }
  }

  function stopLive() {
    connectVersion += 1
    loadVersion += 1
    source?.close()
    source = null
    clearTimers()
  }

  function scheduleReconnect() {
    if (!pollTimer) pollTimer = window.setInterval(() => { void load(true) }, POLL_INTERVAL_MS)
    if (!reconnectTimer) {
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = undefined
        void load(true)
        void connect()
      }, RECONNECT_DELAY_MS)
    }
  }

  async function connect() {
    const version = ++connectVersion
    source?.close()
    source = null
    clearTimers()
    const key = gameKey.value
    if (!key) return
    try {
      const next = await gameEventSource(key)
      if (version !== connectVersion || key !== gameKey.value) { next.close(); return }
      source = next
      next.onopen = () => {
        if (source !== next) return
        if (pollTimer) { clearInterval(pollTimer); pollTimer = undefined }
      }
      next.onmessage = (ev: MessageEvent) => {
        if (source !== next) return
        let payload: unknown = null
        try { payload = JSON.parse(ev.data) } catch { payload = null }
        if (isTacticalSceneUpdatedEvent(payload)) applyEvent(payload)
      }
      next.onerror = () => {
        if (source !== next) return
        source?.close()
        source = null
        scheduleReconnect()
      }
    } catch {
      if (version === connectVersion) scheduleReconnect()
    }
  }

  watch([gameKey, sceneId], ([key, id], prev) => {
    const gameChanged = !prev || key !== prev[0]
    const hadScene = Boolean(prev?.[1])
    if (gameChanged || (hadScene && !id)) stopLive()
    if (!key || !id) {
      scene.value = null
      revision.value = 0
      loading.value = false
      error.value = ''
      return
    }
    if (gameChanged || !hadScene) void connect()
    if (id !== prev?.[1]) {
      scene.value = null
      revision.value = 0
      error.value = ''
    }
    void load()
  }, { immediate: true })

  onBeforeUnmount(stopLive)

  function retry() { void load() }

  return { scene, revision, loading, error, retry }
}