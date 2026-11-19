import { describe, expect, it, vi } from 'vitest'
import {
  detectMissingStyles,
  installRuntimeRecovery,
  isStaleRuntimeAssetError,
  reserveRuntimeReload,
} from '@/runtimeRecovery'

function memoryStorage(): Pick<Storage, 'getItem' | 'setItem'> {
  const values = new Map<string, string>()
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value) },
  }
}

describe('runtime asset recovery', () => {
  it('recognizes lazy-module failures without treating ordinary route errors as stale builds', () => {
    expect(isStaleRuntimeAssetError(new TypeError('Failed to fetch dynamically imported module: /v2-assets/Settings-old.js'))).toBe(true)
    expect(isStaleRuntimeAssetError(new Error('permission denied'))).toBe(false)
  })

  it('allows one reload per cooldown window', () => {
    const storage = memoryStorage()
    expect(reserveRuntimeReload(storage, 1_000, 30_000)).toBe(true)
    expect(reserveRuntimeReload(storage, 5_000, 30_000)).toBe(false)
    expect(reserveRuntimeReload(storage, 31_001, 30_000)).toBe(true)
  })

  it('reloads and suppresses a stale Vite preload error once', () => {
    const reload = vi.fn()
    let routerError: ((error: unknown) => void) | undefined
    const remove = vi.fn()
    const router = {
      onError: vi.fn((handler: (error: unknown) => void) => {
        routerError = handler
        return remove
      }),
    }
    const uninstall = installRuntimeRecovery(router, {
      storage: memoryStorage(),
      now: () => 10_000,
      reload,
    })

    const event = new Event('vite:preloadError', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(reload).toHaveBeenCalledOnce()

    routerError?.(new Error('Loading chunk SettingsView failed'))
    expect(reload).toHaveBeenCalledOnce()

    uninstall()
    expect(remove).toHaveBeenCalledOnce()
  })

  it('detects a page whose stylesheets were all rejected by the client', () => {
    document.head.innerHTML = '<link rel="stylesheet" href="/v2-assets/assets/index-x.css">'
    const link = document.querySelector('link[rel="stylesheet"]') as HTMLLinkElement
    // 模拟客户端拒绝：sheet 始终为 null（jsdom 不会真正加载样式表，等价于拒绝态）
    Object.defineProperty(link, 'sheet', { get: () => null, configurable: true })
    expect(detectMissingStyles()).toBe(true)

    // 有任一样式表生效则不应触发自愈
    const applied = document.createElement('style')
    document.head.appendChild(applied)
    Object.defineProperty(link, 'sheet', { get: () => ({}), configurable: true })
    expect(detectMissingStyles()).toBe(false)
    document.head.innerHTML = ''
  })

  it('does not heal when there are no stylesheet links at all', () => {
    document.head.innerHTML = ''
    expect(detectMissingStyles()).toBe(false)
  })

  it('reloads once on load when every stylesheet is missing', () => {
    const reload = vi.fn()
    const router = { onError: vi.fn(() => vi.fn()) }
    document.head.innerHTML = '<link rel="stylesheet" href="/v2-assets/assets/index-y.css">'
    const link = document.querySelector('link[rel="stylesheet"]') as HTMLLinkElement
    Object.defineProperty(link, 'sheet', { get: () => null, configurable: true })

    const uninstall = installRuntimeRecovery(router, {
      storage: memoryStorage(),
      now: () => 50_000,
      reload,
    })
    window.dispatchEvent(new Event('load'))
    expect(reload).toHaveBeenCalledOnce()
    uninstall()
    document.head.innerHTML = ''
  })
})
