import { i18n } from '@/i18n'

const tokenKey = 'trpg_access_token'

export class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }

function isPlayerShareLocation(): boolean {
  const q = new URLSearchParams(location.hash.split('?')[1] || '')
  return q.has('user') || q.get('share') === '1' || q.get('share') === 'true' || q.get('share') === 'yes'
}

function shareQuery(): string {
  const q = new URLSearchParams(location.hash.split('?')[1] || '')
  const out = new URLSearchParams()
  for (const key of ['game','user','name','share','delegate']) if (q.has(key)) out.set(key, q.get(key)!)
  const gk = q.get('game')
  if (gk) {
    const rt = localStorage.getItem('trpg_play_room_' + gk)
    if (rt) out.set('room_token', rt)
  }
  return out.toString()
}

function apiUrl(path: string): string {
  const query = shareQuery()
  return `/api${path}${query ? (path.includes('?') ? '&' : '?') + query : ''}`
}

function authHeaders(initHeaders?: HeadersInit, contentType = true): Headers {
  const headers = new Headers(initHeaders)
  if (contentType) headers.set('Content-Type', 'application/json')
  const token = localStorage.getItem(tokenKey)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

function applyConfirmHeader(headers: Headers, init: RequestInit): void {
  if (init.method && init.method !== 'GET') headers.set('X-TRPG-Confirm', 'true')
}

async function handleUnauthorized(response: Response): Promise<void> {
  // /api/config is public config with sensitive fields masked; player share pages can also read without access_token.
  if (response.status === 401 && !isPlayerShareLocation() && !location.hash.startsWith('#/login') && !response.url.includes('/api/config')) {
    location.href = `/#/login?redirect=${encodeURIComponent(location.pathname + location.hash)}`
    throw new ApiError(i18n.global.t('loginRequired'), 401)
  }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const isFormData = init.body instanceof FormData
  const headers = authHeaders(init.headers, !isFormData)
  applyConfirmHeader(headers, init)
  const response = await fetch(apiUrl(path), { ...init, headers })
  const data = await response.json().catch(() => ({}))
  await handleUnauthorized(response)
  if (!response.ok) throw new ApiError(data.error || `HTTP ${response.status}`, response.status)
  return data
}

export async function apiBlob(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = authHeaders(init.headers, false)
  applyConfirmHeader(headers, init)
  const response = await fetch(apiUrl(path), { ...init, headers })
  await handleUnauthorized(response)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(data.error || `HTTP ${response.status}`, response.status)
  }
  return response
}

export async function validateAccessToken(value: string): Promise<void> {
  const headers = new Headers()
  if (value) headers.set('Authorization', `Bearer ${value}`)
  const response = await fetch(apiUrl('/games'), { headers })
  if (!response.ok) throw new ApiError(i18n.global.t('incorrectPassword'), response.status)
}

export function setAccessToken(value: string) { localStorage.setItem(tokenKey, value) }
export function hasAccessToken(): boolean { return !!localStorage.getItem(tokenKey) }

export function gameEventSource(gameKey: string): EventSource {
  const q = new URLSearchParams(location.hash.split('?')[1] || '')
  const token = localStorage.getItem(tokenKey)
  if (token) q.set('token', token)
  return new EventSource(`/api/games/${encodeURIComponent(gameKey)}/sse?${q}`)
}

export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error) }
