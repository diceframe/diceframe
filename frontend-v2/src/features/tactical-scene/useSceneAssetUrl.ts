import { onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { apiBlob } from '@/api/client'

const MAP_BACKGROUND_RE = /^map-backgrounds\/([^/?#]+?)(?:\.[A-Za-z0-9]+)?$/
const GENERATED_IMAGE_RE = /^generated-images\/([^/?#]+?)(?:\.[A-Za-z0-9]+)?$/

/**
 * Map a stored Tactical Scene `source_path` to an authenticated backend media
 * endpoint when it refers to a DiceFrame-managed upload or generated image.
 * External or static paths return `null` and render directly.
 */
export function resolveSceneAssetEndpoint(sourcePath: string, gameKey?: string): string | null {
  const path = sourcePath.trim()
  if (!path) return null
  if (path.startsWith('/api/')) return path.slice('/api'.length)
  const mapMatch = MAP_BACKGROUND_RE.exec(path)
  if (mapMatch && gameKey) {
    return `/games/${encodeURIComponent(gameKey)}/map-background-asset/${encodeURIComponent(mapMatch[1]!)}`
  }
  const generatedMatch = GENERATED_IMAGE_RE.exec(path)
  if (generatedMatch) {
    return `/generated-images/${encodeURIComponent(generatedMatch[1]!)}`
  }
  return null
}

/**
 * Resolve a Tactical Scene asset path into a displayable `<image :href>` URL.
 * Authenticated DiceFrame media routes are fetched via `apiBlob` into a managed
 * object URL and revoked automatically on change or unmount.
 */
export function useSceneAssetUrl(
  sourcePath: MaybeRefOrGetter<string>,
  gameKey?: MaybeRefOrGetter<string | undefined>,
) {
  const resolvedUrl = ref('')
  const failed = ref(false)
  let objectUrl = ''
  let sequence = 0

  function revoke() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl)
      objectUrl = ''
    }
  }

  async function refresh() {
    const raw = String(toValue(sourcePath) || '').trim()
    const key = gameKey ? String(toValue(gameKey) || '').trim() : ''
    const current = ++sequence
    failed.value = false
    revoke()
    if (!raw) {
      resolvedUrl.value = ''
      return
    }
    const endpoint = resolveSceneAssetEndpoint(raw, key || undefined)
    if (!endpoint) {
      resolvedUrl.value = raw
      return
    }
    resolvedUrl.value = ''
    try {
      const response = await apiBlob(endpoint)
      if (current !== sequence) return
      const blob = await response.blob()
      if (current !== sequence) return
      objectUrl = URL.createObjectURL(blob)
      resolvedUrl.value = objectUrl
    } catch {
      if (current === sequence) {
        resolvedUrl.value = ''
        failed.value = true
      }
    }
  }

  watch(
    () => [String(toValue(sourcePath) || ''), gameKey ? String(toValue(gameKey) || '') : ''] as const,
    () => { void refresh() },
    { immediate: true },
  )

  onBeforeUnmount(() => {
    sequence += 1
    revoke()
  })

  function markFailed() {
    failed.value = true
  }

  return { resolvedUrl, failed, markFailed }
}
