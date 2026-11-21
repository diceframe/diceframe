import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { cancelManualRollRequest, createManualRollRequest, fetchManualRollRequests, resolveManualRollRequest } from './api'
import type { ManualRollComparison, ManualRollPurpose, ManualRollRequest, ManualRollVisibility } from './types'

export function createOperationId(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID() : `roll-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function canRollRequest(request: ManualRollRequest, actorId: string, isGm: boolean): boolean {
  return request.status === 'pending' && (isGm
    ? request.target_uids.some(uid => !request.results[uid])
    : request.target_uids.includes(actorId) && !request.results[actorId])
}
export function isVisibleToActor(request: ManualRollRequest, actorId: string, isGm: boolean): boolean {
  return request.visibility === 'party' || isGm || request.target_uids.includes(actorId)
}

export function useManualRolls(gameKey: Ref<string>, runId: Ref<string>, actorId: Ref<string>, isGm: Ref<boolean>, activity: Ref<string | undefined>) {
  const requests = ref<ManualRollRequest[]>([])
  const loading = ref(false), error = ref(''), dismissed = ref(new Set<string>()), activeRequestId = ref('')
  let timer: number | undefined
  let revision = 0
  const visibleRequests = computed(() => requests.value.filter(item => isVisibleToActor(item, actorId.value, isGm.value)))
  const pendingForActor = computed(() => visibleRequests.value.filter(item => canRollRequest(item, actorId.value, isGm.value)))
  // GM 也可能同时扮演自己的角色（单人局或 GM 自建角色）。只有请求
  // 的目标是当前 actor 时才弹玩家确认窗；GM 对其他玩家的请求仍留在
  // 控台列表中，由目标玩家确认，避免 GM 页面替玩家自动掷骰。
  const targetPending = computed(() => visibleRequests.value.filter(item => (
    canRollRequest(item, actorId.value, false)
  )))
  const activeRequest = computed(() => targetPending.value.find(item => item.id === activeRequestId.value) || targetPending.value.find(item => !dismissed.value.has(item.id)) || null)

  function reset() { requests.value = []; error.value = ''; dismissed.value = new Set(); activeRequestId.value = ''; revision++ }
  async function refresh() {
    const key = gameKey.value, expectedRun = runId.value, expectedActor = actorId.value, current = ++revision
    if (!key || !expectedRun || !expectedActor) { reset(); return }
    loading.value = true
    try {
      const response = await fetchManualRollRequests(key)
      if (current !== revision || key !== gameKey.value || expectedRun !== runId.value || expectedActor !== actorId.value || response.run_id !== expectedRun) return
      requests.value = response.requests || []
      error.value = ''
    } catch (cause: unknown) {
      if (current === revision) error.value = cause instanceof Error ? cause.message : String(cause)
    } finally { if (current === revision) loading.value = false }
  }
  function dismiss(id: string) { dismissed.value = new Set([...dismissed.value, id]); if (activeRequestId.value === id) activeRequestId.value = '' }
  function reopen(id: string) { activeRequestId.value = id; dismissed.value.delete(id); dismissed.value = new Set(dismissed.value) }
  async function create(input: { formula: string; label: string; purpose: ManualRollPurpose; target?: number | null; comparison?: ManualRollComparison; include_in_ai_context?: boolean; target_uids: string[]; visibility: ManualRollVisibility; operation_id?: string; check?: { ability: string; skill: string; dc: number } }) {
    const operation_id = input.operation_id || createOperationId()
    await createManualRollRequest(gameKey.value, { ...input, operation_id, run_id: runId.value })
    await refresh(); return operation_id
  }
  async function roll(request: ManualRollRequest, targetUid = actorId.value) { await resolveManualRollRequest(gameKey.value, request.id, { run_id: runId.value, target_uid: targetUid }); await refresh() }
  async function cancel(request: ManualRollRequest) { await cancelManualRollRequest(gameKey.value, request.id, { run_id: runId.value }); await refresh() }
  watch([gameKey, runId, actorId], reset)
  watch(activity, () => void refresh())
  watch([gameKey, runId, actorId], () => { if (timer) clearInterval(timer); if (gameKey.value && runId.value && actorId.value) { void refresh(); timer = window.setInterval(() => void refresh(), 5000) } }, { immediate: true })
  onBeforeUnmount(() => { revision++; if (timer) clearInterval(timer) })
  return { requests, visibleRequests, pendingForActor, activeRequest, loading, error, refresh, dismiss, reopen, create, roll, cancel }
}
