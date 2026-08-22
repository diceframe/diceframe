import { i18n } from '@/i18n'
import { activePeerGameClient } from '@/peer/game/bridge'

const tokenKey = 'trpg_access_token'

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public retryAfter?: number) { super(message) }
}

export function isNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

export function errorCodeOf(data: unknown): string | undefined {
  if (data && typeof data === 'object' && 'error_code' in data) {
    const code = (data as { error_code?: unknown }).error_code
    return typeof code === 'string' && code ? code : undefined
  }
  return undefined
}

function isPlayerShareLocation(): boolean {
  if (location.hash.startsWith('#/join')) return true
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

function rateLimitMessage(data: unknown): string {
  const payload = data && typeof data === 'object' ? data as Record<string, unknown> : {}
  const seconds = Number(payload.retry_after)
  if (Number.isFinite(seconds) && seconds > 0) {
    return i18n.global.t('tooManyRequestsRetry', { seconds: Math.ceil(seconds) })
  }
  return i18n.global.t('tooManyRequests')
}

function retryAfterOf(data: unknown): number | undefined {
  const payload = data && typeof data === 'object' ? data as Record<string, unknown> : {}
  const seconds = Number(payload.retry_after)
  return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : undefined
}

async function handleUnauthorized(response: Response): Promise<void> {
  // /api/config is public config with sensitive fields masked; player share pages can also read without access_token.
  if (response.status === 401 && !isPlayerShareLocation() && !location.hash.startsWith('#/login') && !response.url.includes('/api/config')) {
    location.href = `/#/login?redirect=${encodeURIComponent(location.pathname + location.hash)}`
    throw new ApiError(i18n.global.t('loginRequired'), 401)
  }
}

/**
 * P2P 直连局的 API 转发点：把命中游戏路径的请求交给对端数据通道，
 * 由房主本机处理而非打本服务器。这是唯一接触 P2P 的请求入口；
 * 移除 P2P 功能时删掉本函数与 apiBlob 里的对应判断即可。
 */
async function interceptPeerApi<T>(
  path: string,
  init: RequestInit,
): Promise<{ handled: true; value: T } | { handled: false }> {
  const peerGame = activePeerGameClient()
  if (!peerGame) return { handled: false }
  const result = await peerGame.tryApi<T>(path, init)
  return result.handled ? { handled: true, value: result.value as T } : { handled: false }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const peer = await interceptPeerApi<T>(path, init)
  if (peer.handled) return peer.value
  const isRawBody = init.body instanceof FormData || init.body instanceof Blob
  const headers = authHeaders(init.headers, !isRawBody)
  applyConfirmHeader(headers, init)
  const response = await fetch(apiUrl(path), { ...init, headers })
  const data = await response.json().catch(() => ({}))
  await handleUnauthorized(response)
  if (response.status === 429) {
    const payload = data && typeof data === 'object' ? data as Record<string, unknown> : {}
    const message = typeof payload.error === 'string' && payload.error
      ? payload.error
      : rateLimitMessage(data)
    throw new ApiError(message, 429, errorCodeOf(data), retryAfterOf(data))
  }
  if (!response.ok) throw new ApiError(data.error || `HTTP ${response.status}`, response.status, errorCodeOf(data), retryAfterOf(data))
  return data
}

export async function apiBlob(path: string, init: RequestInit = {}): Promise<Response> {
  // P2P 直连局不传输二进制附件：命中游戏路径时抛 501，由调用方降级到内置资源。
  // 移除 P2P 功能时删除此判断。
  const peerGame = activePeerGameClient()
  if (peerGame?.handlesGamePath(path)) {
    throw new ApiError(i18n.global.t('peerBinaryUnavailable'), 501, 'peer_binary_unavailable')
  }
  const headers = authHeaders(init.headers, false)
  applyConfirmHeader(headers, init)
  const response = await fetch(apiUrl(path), { ...init, headers })
  await handleUnauthorized(response)
  if (response.status === 429) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(rateLimitMessage(data), 429, undefined, retryAfterOf(data))
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(data.error || `HTTP ${response.status}`, response.status, errorCodeOf(data), retryAfterOf(data))
  }
  return response
}

export async function validateAccessToken(value: string): Promise<void> {
  const headers = new Headers()
  if (value) headers.set('Authorization', `Bearer ${value}`)
  const response = await fetch(apiUrl('/login'), { method: 'POST', headers })
  if (response.status === 429) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(rateLimitMessage(data), 429)
  }
  if (!response.ok) throw new ApiError(i18n.global.t('incorrectPassword'), response.status)
}

export function setAccessToken(value: string) { localStorage.setItem(tokenKey, value) }
export function hasAccessToken(): boolean { return !!localStorage.getItem(tokenKey) }

export type OwnerAccessStatus = 'allowed' | 'login-required' | 'unavailable'

export async function checkOwnerAccess(): Promise<OwnerAccessStatus> {
  try {
    // Do not use apiUrl(): a player share query must never grant access to owner pages.
    const response = await fetch('/api/me', { headers: authHeaders(undefined, false) })
    if (response.status === 401) return 'login-required'
    return response.ok ? 'allowed' : 'unavailable'
  } catch {
    // A temporary network failure should not trap local users on the login page.
    return 'unavailable'
  }
}

export async function gameEventSource(gameKey: string, cursor = ''): Promise<EventSource> {
  const result = await api<{ ticket: string }>(`/games/${encodeURIComponent(gameKey)}/sse-ticket`, { method: 'POST' })
  const q = new URLSearchParams(shareQuery())
  q.set('ticket', result.ticket)
  if (cursor) q.set('cursor', cursor)
  return new EventSource(`/api/games/${encodeURIComponent(gameKey)}/sse?${q}`)
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code) {
    // 有稳定错误码时优先本地化文案（apiErrors.<code>）；未翻译回退后端原文。
    const localized = i18n.global.t(`apiErrors.${error.code}`)
    if (localized && localized !== `apiErrors.${error.code}`) return localized
  }
  return error instanceof Error ? error.message : String(error)
}
