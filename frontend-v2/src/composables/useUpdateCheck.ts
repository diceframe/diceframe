import { computed, ref } from 'vue'
import { api } from '@/api/client'
import type { UpdateCheckResponse } from '@/api/types'

const updateInfo = ref<UpdateCheckResponse | null>(null)
const updateChecking = ref(false)
// 标记本次进程是否已成功拉取过一次结果；只有成功才置位，
// 否则首次请求失败(如启动时尚未登录的 401)会让 updateInfo 永远停在 null，
// 后续非 force 的自动检查都直接返回 null、不再重试，导致更新提醒永久失效。
const updateChecked = ref(false)
const updateAvailable = computed(() => Boolean(updateInfo.value?.ok && updateInfo.value.update_available))

async function checkForUpdates(force = false): Promise<UpdateCheckResponse | null> {
  if (updateChecking.value) return updateInfo.value
  if (!force && updateChecked.value && updateInfo.value) return updateInfo.value
  updateChecking.value = true
  try {
    updateInfo.value = await api<UpdateCheckResponse>('/system/update-check')
    updateChecked.value = true
    return updateInfo.value
  } finally {
    updateChecking.value = false
  }
}

export function useUpdateCheck() {
  return {
    updateInfo,
    updateChecking,
    updateAvailable,
    checkForUpdates,
  }
}
