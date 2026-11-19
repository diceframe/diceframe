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
 * 症状：主样式表 `<link href=".../index-*.css">` 存在但 `sheet` 为 null——客户端收到了
 * 引用却没有应用样式。根因未证实（证书回调竞态是候选之一），这里只做症状检测，
 * 不推断成因；其它浏览器同样会运行本检测，因此探针必须足够保守以免误伤：
 * 仅当 SPA 已挂载（#app 存在）且主样式表条目失效时才判定为异常。
 */
export function detectMissingStyles(): boolean {
  try {
    // 必须等 DOM 加载完成后检测
    if (document.readyState !== 'complete') return false
    if (!document.getElementById('app')) return false // 非 SPA 页面（ splash 等）不判

    // Vite 主样式表：index.html 头部唯一的 <link href=".../index-<hash>.css">。
    // 它失效 = 整页无样式；其它 per-chunk link 失效只影响局部组件，交给
    // vite:preloadError 路径处理。
    const mainLink = Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]'))
      .find(link => /\/index-[^/]*\.css/i.test(link.getAttribute('href') || ''))
    if (!mainLink) return false
    return mainLink.sheet === null
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

  // 样式自愈：DOM 完成后检测主样式表是否被客户端拒绝（根因未证实，仅按症状恢复）
  const checkStylesOnLoad = () => {
    if (detectMissingStyles()) {
      console.warn('[DiceFrame] Main stylesheet not applied; attempting a reload to recover')
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
    // load 监听也要移除：uninstall 发生在 load 之前时，废弃的恢复动作不得再触发
    window.removeEventListener('load', checkStylesOnLoad)
    removeRouterError()
  }
}
