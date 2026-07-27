import { computed, ref } from 'vue'
import { api } from '@/api/client'
import type { UpdateStatusResponse, UpdateDownloadResponse } from '@/api/types'

// 单例：更新下载状态跨组件共享（设置页打开/关闭不丢失进行中的下载）。
const updateStatus = ref<UpdateStatusResponse | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null

const ACTIVE_STATES = new Set<UpdateStatusResponse['state']>(['downloading', 'verifying'])
const isDownloading = computed(() => ACTIVE_STATES.has(updateStatus.value?.state || 'idle'))
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

async function refreshStatus(): Promise<void> {
  try {
    updateStatus.value = await api<UpdateStatusResponse>('/system/update/status')
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

export function useUpdater() {
  return { updateStatus, isDownloading, downloadPercent, refreshStatus, startDownload }
}
