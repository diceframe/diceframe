import { api, apiBlob } from '@/api/client'
import type {
  PluginContentImportResponse,
  PluginContentResponse,
  PluginInfo,
  HubPluginReadmeResponse,
  HubPreferences,
  HubRatingSummary,
  PluginMarketplaceItem,
  PluginMarketplaceResponse,
  PluginMirror,
  PluginMirrorsResponse,
  PluginMirrorTestResponse,
  PluginToolInvokeResponse,
  PluginToolsResponse,
  MapBackgroundSelection,
  SceneImageRef,
  WorldListResponse,
} from '@/api/types'

export type PluginTypeInfo = {
  id: string
  level: string
  filterable: boolean
  filter_order: number
}

const pluginPath = (pluginId: string, suffix = '') =>
  `/plugins/${encodeURIComponent(pluginId)}${suffix}`

export const pluginApi = {
  list: () => api<{ plugins: PluginInfo[] }>('/plugins'),
  listTypes: () => api<{ ok: boolean; types: PluginTypeInfo[] }>('/plugins/types'),
  tools: () => api<PluginToolsResponse>('/plugins/tools'),
  invokeTool: (pluginId: string, toolName: string, argumentsValue: Record<string, unknown>) =>
    api<PluginToolInvokeResponse>(
      `/plugins/tools/${encodeURIComponent(pluginId)}/${encodeURIComponent(toolName)}`,
      { method: 'POST', body: JSON.stringify({ arguments: argumentsValue, context: {} }) },
    ),
  marketplace: () => api<PluginMarketplaceResponse>('/plugins/marketplace'),
  hubPreferences: (language = 'zh-CN') => api<HubPreferences>(`/hub/preferences?lang=${encodeURIComponent(language)}`),
  updateHubPreferences: (
    telemetryEnabled: boolean,
    legalAcceptance?: HubPreferences['legal_documents'],
    language = 'zh-CN',
  ) => api<HubPreferences>('/hub/preferences', {
    method: 'PATCH',
    body: JSON.stringify({
      telemetry_enabled: telemetryEnabled,
      ...(legalAcceptance ? { legal_acceptance: legalAcceptance } : {}),
      lang: language,
    }),
  }),
  deleteHubIdentity: () => api<HubPreferences>('/hub/identity', { method: 'DELETE' }),
  hubDetail: (pluginId: string, signal?: AbortSignal) => api<PluginMarketplaceItem & { ok: boolean }>(
    `/hub/plugins/${encodeURIComponent(pluginId)}`,
    { signal },
  ),
  hubReadme: (pluginId: string, signal?: AbortSignal) => api<HubPluginReadmeResponse>(
    `/hub/plugins/${encodeURIComponent(pluginId)}/readme`,
    { signal },
  ),
  hubRatings: (pluginId: string, signal?: AbortSignal) => api<HubRatingSummary>(
    `/hub/plugins/${encodeURIComponent(pluginId)}/ratings`,
    { signal },
  ),
  setHubLike: (pluginId: string, liked: boolean) => api<{ ok: boolean; liked: boolean }>(
    `/hub/plugins/${encodeURIComponent(pluginId)}/like`, { method: liked ? 'PUT' : 'DELETE' },
  ),
  setHubRating: (pluginId: string, stars: number | null, tags: string[] = []) => api(
    `/hub/plugins/${encodeURIComponent(pluginId)}/rating`,
    stars === null
      ? { method: 'DELETE' }
      : { method: 'PUT', body: JSON.stringify({ stars, tags }) },
  ),
  mirrors: () => api<PluginMirrorsResponse>('/plugins/mirrors'),
  content: () => api<PluginContentResponse>('/plugins/content'),
  worlds: () => api<WorldListResponse>('/worlds'),
  docs: (pluginId: string) => api<{ ok: boolean; found?: boolean; name?: string; content?: string; error?: string }>(
    pluginPath(pluginId, '/docs'),
  ),
  importAllContent: (pluginId: string, targetWorldId: string) =>
    api<{ ok: boolean; imported_count?: number; error_count?: number; error?: string }>(
      '/plugins/content/import-all',
      { method: 'POST', body: JSON.stringify({ plugin_id: pluginId, target_world_id: targetWorldId }) },
    ),
  exportContent: (payload: {
    plugin_id: string
    name: string
    version: string
    description: string
    world_id?: string
    card_ids?: string[]
    rule_id?: string
      flat?: boolean
      include_portraits?: boolean
      include_scene_images?: boolean
      world_scene_image?: SceneImageRef
      rule_scene_image?: SceneImageRef
      include_map?: boolean
      map_background?: MapBackgroundSelection
      map_icons?: Array<{ id: string; file_name: string; file_data: string }>
  }) => apiBlob('/plugins/export', { method: 'POST', body: JSON.stringify(payload) }),
  updateConfig: (pluginId: string, payload: Record<string, unknown>) =>
    api(pluginPath(pluginId, '/config'), { method: 'PUT', body: JSON.stringify(payload) }),
  restart: (pluginId: string) => api(pluginPath(pluginId, '/restart'), { method: 'POST' }),
  clearCardCache: (pluginId: string) =>
    api<{ deleted?: number; bytes_deleted?: number }>(pluginPath(pluginId, '/card-cache/clear'), { method: 'POST' }),
  setRunning: (pluginId: string, running: boolean) =>
    api(pluginPath(pluginId, running ? '/start' : '/stop'), { method: 'POST' }),
  install: (body: FormData) => api('/plugins/install', { method: 'POST', body }),
  rescan: () => api('/plugins/rescan', { method: 'POST' }),
  installMarketplace: (pluginId: string, overwrite: boolean) =>
    api('/plugins/marketplace/install', {
      method: 'POST',
      body: JSON.stringify({ plugin_id: pluginId, overwrite }),
    }),
  update: (pluginId: string) => api(pluginPath(pluginId, '/update'), { method: 'POST' }),
  uninstall: (pluginId: string, deleteData = false) =>
    api<{ ok: boolean; uninstalled?: boolean; lorebook_removed?: number; cards_removed?: number; worlds_removed?: number; worlds_kept?: string[] }>(
      pluginPath(pluginId), { method: 'DELETE', body: JSON.stringify({ delete_data: deleteData }) },
    ),
  addMirror: (mirror: PluginMirror) =>
    api('/plugins/mirrors', { method: 'POST', body: JSON.stringify(mirror) }),
  updateMirror: (mirrorId: string, patch: Partial<PluginMirror>) =>
    api(`/plugins/mirrors/${encodeURIComponent(mirrorId)}`, {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
  deleteMirror: (mirrorId: string) =>
    api(`/plugins/mirrors/${encodeURIComponent(mirrorId)}`, { method: 'DELETE' }),
  testMirror: (mirrorId = '') =>
    api<PluginMirrorTestResponse>('/plugins/mirrors/test', {
      method: 'POST',
      body: JSON.stringify({ mirror_id: mirrorId }),
    }),
  importContent: (kind: string, itemId: unknown, pluginId: string, targetWorldId: string) =>
    api<PluginContentImportResponse>('/plugins/content/import', {
      method: 'POST',
      body: JSON.stringify({
        kind,
        id: itemId,
        plugin_id: pluginId,
        target_world_id: targetWorldId,
      }),
    }),
}
