import { api } from '@/api/client'

export type HonorsBadgeShape =
  | 'shield'
  | 'diamond'
  | 'coin'
  | 'wax-seal'
  | 'hexagon'
  | 'compass'
  | string

export type HonorsWallMode = 'listed' | 'counter'

export type LocalizedText = Record<string, string>

export type HonorsBadgeCategory =
  | 'milestone'
  | 'creation'
  | 'community'
  | 'tribute'
  | string

export interface HonorsBadgeDefinition {
  id: string
  category?: HonorsBadgeCategory
  shape: HonorsBadgeShape
  wall_mode: HonorsWallMode
  theme: string
  craft?: LocalizedText
  name: LocalizedText
  subtitle?: LocalizedText
  description?: LocalizedText
  obtain_hint?: LocalizedText
  image_url?: string
  mask_url?: string
}

export interface HonorsActiveCredential {
  id: string
  name: string
  badges: string[]
  iat: number
  note?: string
  featured_badge_id: string
  activated_at: number
}

export interface HonorsStateResponse {
  ok: boolean
  catalog: HonorsBadgeDefinition[]
  active: HonorsActiveCredential | null
  remote_configured: boolean
  redeem_url: string
  warning?: string
}

export interface HonorsWallItem {
  id: string
  name: string
  badges: string[]
  featured_badge_id?: string
  is_self?: boolean
}

export interface HonorsWallResponse {
  ok: boolean
  source: 'remote' | 'offline'
  reason?: string
  page: number
  limit: number
  total: number
  items: HonorsWallItem[]
  counters: Record<string, number>
  catalog: HonorsBadgeDefinition[]
  revoked: string[]
}

export interface HonorsWallQuery {
  page?: number
  limit?: number
  badge?: string
  q?: string
  refresh?: boolean
}

export function getHonorsState(): Promise<HonorsStateResponse> {
  return api<HonorsStateResponse>('/honors')
}

export function activateHonorsToken(token: string): Promise<HonorsStateResponse> {
  return api<HonorsStateResponse>('/honors/activate', {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

export function setFeaturedHonorBadge(badgeId: string): Promise<HonorsStateResponse> {
  return api<HonorsStateResponse>('/honors/featured', {
    method: 'POST',
    body: JSON.stringify({ badge_id: badgeId }),
  })
}

export function clearHonorsCredential(): Promise<HonorsStateResponse> {
  return api<HonorsStateResponse>('/honors', {
    method: 'DELETE',
  })
}

export function getHonorsWall(query: HonorsWallQuery = {}): Promise<HonorsWallResponse> {
  const params = new URLSearchParams()
  if (query.page) params.set('page', String(query.page))
  if (query.limit) params.set('limit', String(query.limit))
  if (query.badge) params.set('badge', query.badge)
  if (query.q) params.set('q', query.q)
  if (query.refresh) params.set('refresh', '1')
  const qs = params.toString()
  return api<HonorsWallResponse>(qs ? `/honors/wall?${qs}` : '/honors/wall')
}
