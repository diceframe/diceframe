import { api } from '@/api/client'

export type ModuleSummary = {
  id: string
  name: string
  version: string
  content_profile: string
  content_delivery_mode: string
  is_module: boolean
  status: string
  adventure_count: number
  content_counts: Record<string, number>
}

export type ModulesResponse = {
  ok: boolean
  modules: ModuleSummary[]
}

export type ModuleDetail = ModuleSummary & {
  content: Record<string, Array<{ key: string; title: string; description: string }>>
  adventures: Array<{ adventure_id: string; version: string; format: string; directory_id: string }>
}

export type ModuleDetailResponse = { ok: boolean; module?: ModuleDetail; error_code?: string }
export type ModuleCompatibilityResponse = {
  ok: boolean
  module_id?: string
  blockers?: string[]
  warnings?: string[]
}
export type ModuleUsageResponse = {
  ok: boolean
  module_id?: string
  usages?: Array<{ adventure_id: string; games: Array<{ game_key: string; run_id: string; content_digest: string }> }>
  total_games?: number
}

export const moduleApi = {
  list: () => api<ModulesResponse>('/modules'),
  detail: (moduleId: string) => api<ModuleDetailResponse>(`/modules/${encodeURIComponent(moduleId)}`),
  compatibility: (moduleId: string) => api<ModuleCompatibilityResponse>(`/modules/${encodeURIComponent(moduleId)}/compatibility`),
  usages: (moduleId: string) => api<ModuleUsageResponse>(`/modules/${encodeURIComponent(moduleId)}/usages`),
}
