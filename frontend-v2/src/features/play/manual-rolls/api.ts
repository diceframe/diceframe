import { api } from '@/api/client'
import type { ManualRollComparison, ManualRollPurpose, ManualRollRequest, ManualRollRequestsResponse, ManualRollResult, ManualRollVisibility } from './types'

const path = (gameKey: string) => `/games/${encodeURIComponent(gameKey)}/roll-requests`

export function fetchManualRollRequests(gameKey: string) {
  return api<ManualRollRequestsResponse>(path(gameKey))
}

export function createManualRollRequest(gameKey: string, body: {
  operation_id: string; run_id: string; formula: string; label: string; purpose: ManualRollPurpose; target?: number | null; comparison?: ManualRollComparison; include_in_ai_context?: boolean; target_uids: string[]; visibility: ManualRollVisibility
}) {
  return api<{ ok: true; request: ManualRollRequest }>(path(gameKey), { method: 'POST', body: JSON.stringify(body) })
}

export function quickManualRoll(gameKey: string, body: {
  operation_id: string; run_id: string; formula: string; label?: string; visibility: ManualRollVisibility; include_in_ai_context: boolean
}) {
  return api<{ ok: true; request: ManualRollRequest; result: ManualRollResult }>(`${path(gameKey)}/quick`, { method: 'POST', body: JSON.stringify(body) })
}

export function resolveManualRollRequest(gameKey: string, requestId: string, body: { run_id: string; target_uid: string }) {
  return api<{ ok: true; request: ManualRollRequest }>(`${path(gameKey)}/${encodeURIComponent(requestId)}/roll`, { method: 'POST', body: JSON.stringify(body) })
}

export function cancelManualRollRequest(gameKey: string, requestId: string, body: { run_id: string }) {
  return api<{ ok: true; request: ManualRollRequest }>(`${path(gameKey)}/${encodeURIComponent(requestId)}/cancel`, { method: 'POST', body: JSON.stringify(body) })
}
