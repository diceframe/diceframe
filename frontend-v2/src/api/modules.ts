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
  lorebook_count: number
}

export type ModulesResponse = {
  ok: boolean
  modules: ModuleSummary[]
}

export type ModuleBoundGame = {
  game_key: string
  adventure_id: string
  run_id: string
}

/** 服务端 guard 对某个受保护 action 的结论（FIX-06 §8）。 */
export type ModuleActionGuard = {
  allowed: boolean
  reason: string
  games: ModuleBoundGame[]
}

export type ModuleDetail = ModuleSummary & {
  content: Record<string, Array<{ key: string; title: string; description: string }>>
  adventures: Array<{ adventure_id: string; version: string; format: string; directory_id: string }>
  lorebooks: Array<{ id: string; name: string; description: string; language: string; enabled: boolean; source_kind: string; source_id: string }>
  bound_games?: ModuleBoundGame[]
  actions?: Record<string, ModuleActionGuard>
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
export type ModulePreviewResponse = { ok: boolean; blockers: string[]; warnings: string[] }
export type ExternalModulePreviewResponse = {
  ok: boolean
  adapter?: string
  format?: string
  source_name?: string
  manifest?: Record<string, unknown>
  warnings?: string[]
  blockers?: string[]
  review_required?: boolean
  auto_installable?: boolean
  error?: string
}

/** 在线模组（复用插件市场索引，只保留 content-pack；FIX-06 §8）。 */
export type ModuleMarketplaceItem = {
  id: string
  name: string
  version: string
  latest_version: string
  description: string
  content_profile: string
  content_delivery_mode: string
  adventure_count: number
  ruleset_targets: string[]
  languages: string[]
  tags: string[]
  trust_level: string
  distribution: string
  repository_url: string
  release_url: string
  stars: number
  installed: boolean
  installed_version: string
  update_available: boolean
  installable: boolean
  verification_error: string
  needs_core_update: boolean
  min_app_version: string
}

export type ModuleMarketplaceResponse = {
  ok: boolean
  modules: ModuleMarketplaceItem[]
  total?: number
  error_code?: string
  error?: string
}

export const moduleApi = {
  list: () => api<ModulesResponse>('/modules'),
  detail: (moduleId: string) => api<ModuleDetailResponse>(`/modules/${encodeURIComponent(moduleId)}`),
  compatibility: (moduleId: string) => api<ModuleCompatibilityResponse>(`/modules/${encodeURIComponent(moduleId)}/compatibility`),
  usages: (moduleId: string) => api<ModuleUsageResponse>(`/modules/${encodeURIComponent(moduleId)}/usages`),
  lorebooks: (moduleId: string) => api<{ ok: boolean; module_id?: string; lorebooks?: ModuleDetail['lorebooks']; error_code?: string }>(`/modules/${encodeURIComponent(moduleId)}/lorebooks`),
  previewImport: (body: FormData) => api<ModulePreviewResponse>('/modules/import/preview', { method: 'POST', body }),
  previewExternalImport: (body: FormData) => api<ExternalModulePreviewResponse>('/modules/external/preview', { method: 'POST', body }),
  import: (body: FormData) => api<{ ok: boolean; id?: string }>('/modules/import', { method: 'POST', body }),
  marketplace: (keyword = '') => api<ModuleMarketplaceResponse>(
    `/modules/marketplace${keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''}`,
  ),
  installFromMarketplace: (moduleId: string, overwrite = false) => api<{ ok: boolean; id?: string }>(
    `/modules/${encodeURIComponent(moduleId)}/install`,
    { method: 'POST', body: JSON.stringify({ overwrite }) },
  ),
  // 生命周期复用既有插件宿主 API（模块 = data-only content-pack），
  // 破坏性 action 的 bound-save guard 由服务端统一强制。
  update: (moduleId: string) => api<{ ok: boolean }>(
    `/plugins/${encodeURIComponent(moduleId)}/update`, { method: 'POST' },
  ),
  disable: (moduleId: string) => api<{ ok: boolean }>(
    `/plugins/${encodeURIComponent(moduleId)}/stop`, { method: 'POST' },
  ),
  enable: (moduleId: string) => api<{ ok: boolean }>(
    `/plugins/${encodeURIComponent(moduleId)}/start`, { method: 'POST' },
  ),
  uninstall: (moduleId: string, deleteData = false) => api<{ ok: boolean }>(
    `/plugins/${encodeURIComponent(moduleId)}`,
    { method: 'DELETE', body: JSON.stringify({ delete_data: deleteData }) },
  ),
}
