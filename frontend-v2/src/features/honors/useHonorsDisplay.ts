import { computed, ref } from 'vue'
import {
  getHonorsState,
  type HonorsActiveCredential,
  type HonorsBadgeDefinition,
} from '@/api/honors'

const SHOW_IN_PLAY_KEY = 'diceframe:honors:showInPlay'

function readInitialShowInPlay(): boolean {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return true
    const stored = window.localStorage.getItem(SHOW_IN_PLAY_KEY)
    return stored === null ? true : stored !== 'false'
  } catch {
    return true
  }
}

const activeCredential = ref<HonorsActiveCredential | null>(null)
const honorCatalog = ref<HonorsBadgeDefinition[]>([])
const showHonorsInPlay = ref<boolean>(readInitialShowInPlay())
let loadPromise: Promise<void> | null = null
let loaded = false

export function syncHonorsDisplay(state: {
  active: HonorsActiveCredential | null
  catalog?: HonorsBadgeDefinition[]
}): void {
  activeCredential.value = state.active || null
  if (state.catalog?.length) {
    honorCatalog.value = state.catalog
  }
  loaded = true
}

export function setShowHonorsInPlay(value: boolean): void {
  showHonorsInPlay.value = value
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(SHOW_IN_PLAY_KEY, value ? 'true' : 'false')
    }
  } catch {
    // Ignore storage failures in private browsing / test environments
  }
}

/**
 * Lazily reads local honor badge state from the backend (`data/honors.json`).
 * Performs zero external network calls and degrades silently on error.
 */
export function ensureHonorsLoaded(): Promise<void> {
  if (loaded) return Promise.resolve()
  if (loadPromise) return loadPromise
  loadPromise = getHonorsState()
    .then((state) => {
      syncHonorsDisplay({ active: state.active, catalog: state.catalog })
    })
    .catch(() => {
      // Never disturb play page if backend is mocked or offline
    })
    .finally(() => {
      loadPromise = null
    })
  return loadPromise
}

export function resetHonorsDisplayForTest(): void {
  activeCredential.value = null
  honorCatalog.value = []
  showHonorsInPlay.value = true
  loaded = false
  loadPromise = null
}

const featuredBadge = computed<HonorsBadgeDefinition | null>(() => {
  const active = activeCredential.value
  if (!active || !active.badges?.length) return null
  const targetId = active.featured_badge_id || active.badges[0]
  if (!targetId) return null
  return (
    honorCatalog.value.find(item => item.id === targetId) || {
      id: targetId,
      shape: 'coin',
      wall_mode: 'listed',
      theme: 'starlight',
      name: { 'zh-CN': targetId, en: targetId },
    }
  )
})

const equippedPlayBadge = computed<HonorsBadgeDefinition | null>(() =>
  showHonorsInPlay.value ? featuredBadge.value : null,
)

export function useHonorsDisplay() {
  return {
    activeCredential,
    honorCatalog,
    showHonorsInPlay,
    featuredBadge,
    equippedPlayBadge,
    ensureHonorsLoaded,
    syncHonorsDisplay,
    setShowHonorsInPlay,
  }
}
