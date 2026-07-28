import { computed, ref } from 'vue'
import { api } from '@/api/client'
import type { UpdateStatusResponse, UpdateDownloadResponse, UpdateApplyResponse } from '@/api/types'

// 单例：更新下载状态跨组件共享（设置页打开/关闭不丢失进行中的下载）。
const updateStatus = ref<UpdateStatusResponse | null>(null)
const reloadCountdown = ref<number | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let reloadTimer: ReturnType<typeof setTimeout> | null = null
let reloadAfterPortableUpdate = false

const ACTIVE_STATES = new Set<UpdateStatusResponse['state']>(['downloading', 'verifying', 'applying', 'restarting'])
const DOWNLOAD_STATES = new Set<UpdateStatusResponse['state']>(['downloading', 'verifying'])
const RELOAD_DELAY_SECONDS = 5
const isDownloading = computed(() => DOWNLOAD_STATES.has(updateStatus.value?.state || 'idle'))
const isUpdateBusy = computed(() => ACTIVE_STATES.has(updateStatus.value?.state || 'idle'))
const downloadPercent = computed(() => {
  const s = updateStatus.value
  if (!s || !s.total_bytes) return 0
  return Math.min(100, Math.round((s.downloaded_bytes || 0) / s.total_bytes * 100))
})

function schedulePoll(): void {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
  if (ACTIVE_STATES.has(updateStatus.value?.state || 'idle')) {
    pollTimer = setTimeout(() => { void refreshStatus() }, 1500)
  }
}

function startReloadCountdown(): void {
  if (reloadTimer || typeof window === 'undefined') return
  reloadCountdown.value = RELOAD_DELAY_SECONDS
  const tick = () => {
    const remaining = reloadCountdown.value
    if (remaining === null) return
    if (remaining <= 1) {
      reloadCountdown.value = 0
      reloadTimer = null
      window.location.reload()
      return
    }
    reloadCountdown.value = remaining - 1
    reloadTimer = setTimeout(tick, 1000)
  }
  reloadTimer = setTimeout(tick, 1000)
}

function observePortableUpdateCompletion(): void {
  if (!reloadAfterPortableUpdate) return
  const state = updateStatus.value?.state
  if (state === 'done') {
    reloadAfterPortableUpdate = false
    startReloadCountdown()
  } else if (state === 'failed' || state === 'rolled-back') {
    reloadAfterPortableUpdate = false
  }
}

async function refreshStatus(): Promise<void> {
  try {
    updateStatus.value = await api<UpdateStatusResponse>('/system/update/status')
    observePortableUpdateCompletion()
  } catch {
    // 静默：UI 保留上次状态，避免轮询期间偶发错误刷屏
  }
  schedulePoll()
}

async function startDownload(kind: 'source' | 'portable'): Promise<UpdateDownloadResponse> {
  const result = await api<UpdateDownloadResponse>(`/system/update/download?kind=${kind}`, { method: 'POST' })
  if (result.ok) {
    await refreshStatus()
  }
  return result
}

async function applyUpdate(): Promise<UpdateApplyResponse> {
  const isPortable = updateStatus.value?.kind === 'portable'
  const result = await api<UpdateApplyResponse>('/system/update/apply', { method: 'POST' })
  if (result.ok) {
    reloadAfterPortableUpdate = isPortable
    await refreshStatus()
  }
  return result
}

export function useUpdater() {
  return {
    updateStatus,
    reloadCountdown,
    isDownloading,
    isUpdateBusy,
    downloadPercent,
    refreshStatus,
    startDownload,
    applyUpdate,
  }
}
