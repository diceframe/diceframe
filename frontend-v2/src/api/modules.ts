import { api } from '@/api/client'

export type ModuleSummary = {
  id: string
  name: string
  version: string
  content_profile: string
  content_delivery_mode: string
  is_module: boolean
  status: string
  // Counts describe installed declarations, independently of whether the pack is enabled.
  // null means unreadable; it must not be displayed as an empty package.
  adventure_count: number | null
  content_counts: Record<string, number> | null
  inventory_status?: 'available' | 'disabled' | 'unknown'
  lorebook_count: number
  // 已安装包声明的封面（包内相对路径，如 assets/cover.webp）；空表示没有。
  cover?: string
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
  adventures: Array<{ adventure_id: string; version: string; format: string; directory_id: string; name?: string; summary?: string }>
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
  // From the plugin index; empty when the index and manifest do not say.
  author: string
  license: string
  // Hub catalog stats; empty when the listing came from the GitHub index.
  stats?: { downloads_total?: number; rating_average?: number; likes?: number }
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
  // Where the listing came from; `hub` enables Hub stats and the detail dialog.
  source?: { hub?: boolean; mirror_name?: string; stale?: boolean }
  total?: number
  error_code?: string
  error?: string
}

export const moduleApi = {
  list: () => api<ModulesResponse>('/modules'),
  detail: (moduleId: string) => api<ModuleDetailResponse>(`/modules/${encodeURIComponent(moduleId)}`),
  compatibility: (moduleId: string) => api<ModuleCompatibilityResponse>(`/modules/${encodeURIComponent(moduleId)}/compatibility`),
  usages: (moduleId: string) => api<ModuleUsageResponse>(`/modules/${encodeURIComponent(moduleId)}/usages`),
  previewImport: (body: FormData) => api<ModulePreviewResponse>('/modules/import/preview', { method: 'POST', body }),
  import: (body: FormData) => api<{ ok: boolean; id?: string }>('/modules/import', { method: 'POST', body }),
  marketplace: (keyword = '', ruleset = 'all') => {
    const query = new URLSearchParams({
      ...(keyword ? { keyword } : {}),
      ...(ruleset && ruleset !== 'all' ? { ruleset } : {}),
    }).toString()
    return api<ModuleMarketplaceResponse>(`/modules/marketplace${query ? `?${query}` : ''}`)
  },
  installFromMarketplace: (moduleId: string, overwrite = false) => api<{ ok: boolean; id?: string; error?: string; error_code?: string }>(
    `/modules/${encodeURIComponent(moduleId)}/install`,
    { method: 'POST', body: JSON.stringify({ overwrite }) },
  ),
  // 生命周期复用既有插件宿主 API（模块 = data-only content-pack），
  // 破坏性 action 的 bound-save guard 由服务端统一强制。
  // `up_to_date`: the host found the installed release already current.
  update: (moduleId: string) => api<{ ok: boolean; up_to_date?: boolean; version?: string }>(
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
