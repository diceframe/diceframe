import { computed, ref } from 'vue'
import { api } from '@/api/client'
import type { UpdateCheckResponse } from '@/api/types'

const updateInfo = ref<UpdateCheckResponse | null>(null)
const updateChecking = ref(false)
// Mark successful fetches only. If the initial request fails, for example a pre-login 401,
// later non-force automatic checks should still retry instead of freezing updateInfo at null.
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
