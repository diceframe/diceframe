import type { Router } from 'vue-router'

const RUNTIME_RELOAD_KEY = 'diceframe_runtime_reload_at'
const RUNTIME_RELOAD_COOLDOWN_MS = 30_000

type RuntimeRecoveryOptions = {
  storage?: Pick<Storage, 'getItem' | 'setItem'>
  now?: () => number
  reload?: () => void
}

export function isStaleRuntimeAssetError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '')
  return /(?:Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading chunk .+ failed|Unable to preload CSS)/iu.test(message)
}

/**
 * 检测样式表是否实际生效（DiceFrame WebView2 安装版偶发"服务器 200 但客户端无样式"问题）。
 * 症状：document.styleSheets 存在但所有 link.sheet 为 null（HTTPS 证书回调竞态导致客户端静默拒绝 CSS）。
 */
export function detectMissingStyles(): boolean {
  try {
    // 必须等 DOM 加载完成后检测
    if (document.readyState !== 'complete') return false

    const links = Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]'))
    if (links.length === 0) return false // 无样式表声明 = 不是样式丢失问题

    // 所有 <link rel="stylesheet"> 都存在但无一生效 → 样式被客户端拒绝
    const allRejected = links.length > 0 && links.every(link => link.sheet === null)
    if (allRejected) {
      console.error('[DiceFrame] All stylesheets failed to load (client-side rejection suspected)')
    }
    return allRejected
  } catch (error) {
    console.warn('[DiceFrame] Could not detect stylesheet status:', error)
    return false
  }
}

export function reserveRuntimeReload(
  storage: Pick<Storage, 'getItem' | 'setItem'>,
  now = Date.now(),
  cooldownMs = RUNTIME_RELOAD_COOLDOWN_MS,
): boolean {
  try {
    const previous = Number(storage.getItem(RUNTIME_RELOAD_KEY) || 0)
    if (Number.isFinite(previous) && previous > 0 && now - previous < cooldownMs) return false
    storage.setItem(RUNTIME_RELOAD_KEY, String(now))
    return true
  } catch {
    // Without persistent per-tab state an automatic reload could loop forever.
    return false
  }
}

export function installRuntimeRecovery(
  router: Pick<Router, 'onError'>,
  options: RuntimeRecoveryOptions = {},
): () => void {
  const storage = options.storage ?? window.sessionStorage
  const now = options.now ?? Date.now
  const reload = options.reload ?? (() => window.location.reload())

  const recover = (): boolean => {
    if (!reserveRuntimeReload(storage, now())) return false
    reload()
    return true
  }
  const onPreloadError = (event: Event) => {
    if (recover()) event.preventDefault()
  }
  window.addEventListener('vite:preloadError', onPreloadError)
  const removeRouterError = router.onError((error) => {
    if (isStaleRuntimeAssetError(error)) recover()
  })

  // WebView2 自愈：DOM 完成后检测样式表是否全部失效（证书回调竞态导致客户端拒绝）
  const checkStylesOnLoad = () => {
    if (detectMissingStyles()) {
      console.warn('[DiceFrame] Attempting recovery for missing stylesheets')
      recover()
    }
  }
  if (document.readyState === 'complete') {
    checkStylesOnLoad()
  } else {
    window.addEventListener('load', checkStylesOnLoad, { once: true })
  }

  return () => {
    window.removeEventListener('vite:preloadError', onPreloadError)
    removeRouterError()
  }
}
