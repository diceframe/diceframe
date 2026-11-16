import { i18n } from '@/i18n'
import { notifyAccessTokenChange } from '@/api/authSession'
import { activePeerGameClient } from '@/peer/game/bridge'
import {
  accessTokenStorageKey,
  buildApiUrl,
  currentBackendUrl,
  isStandaloneFrontend,
  redirectToBackendLogin,
} from '@/api/connection'
import { ROOM_TOKEN_HEADER, ROOM_TOKEN_REJECTED_EVENT, SEAT_TOKEN_HEADER, clearRoomToken, clearSeatToken, gameKeyOfApiPath, readRoomToken, readSeatToken } from '@/utils/seatToken'

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public retryAfter?: number) { super(message) }
}

/**
 * Retry an idempotent "enter the game" write once when the server reports a
 * short rate limit (429 with Retry-After). Players at one table often share a
 * public IP; a transient write budget should not strand them on the join page
 * or demote the GM view. Never use this for writes that must not repeat.
 */
export async function retryOnRateLimit<T>(request: () => Promise<T>, maxWaitSeconds = 10): Promise<T> {
  try {
    return await request()
  } catch (error: unknown) {
    const wait = error instanceof ApiError && error.status === 429 ? Number(error.retryAfter || 0) : 0
    if (!wait || wait > maxWaitSeconds) throw error
    await new Promise(resolve => setTimeout(resolve, wait * 1000))
    return await request()
  }
}

export function isNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

/**
 * The GM closed this game's player entrance. The server reports it as
 * PLAYER_ACCESS_CLOSED; a P2P host's bridge rejects with the bare code
 * `player_access_closed`.
 */
export function isPlayerAccessClosedError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.code === 'PLAYER_ACCESS_CLOSED' || error.code === 'player_access_closed'
  }
  return error instanceof Error && error.message === 'player_access_closed'
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
  const gk = q.get('game')
  // A share-link player is identified by its seat token (header), not by the
  // public uid; only the owner (preview / P2P delegation) still names a seat.
  const sendUser = hasAccessToken() || !(gk && readSeatToken(gk))
  for (const key of ['game','user','name','share','delegate']) {
    if (key === 'user' && !sendUser) continue
    if (q.has(key)) out.set(key, q.get(key)!)
  }
  return out.toString()
}

export function apiUrl(path: string): string {
  const query = shareQuery()
  return `${buildApiUrl(path)}${query ? (path.includes('?') ? '&' : '?') + query : ''}`
}

export function authHeaders(initHeaders?: HeadersInit, contentType = true): Headers {
  const headers = new Headers(initHeaders)
  if (contentType) headers.set('Content-Type', 'application/json')
  const token = localStorage.getItem(accessTokenStorageKey())
  if (token) headers.set('Authorization', `Bearer ${token}`)
  // 后端按 Accept-Language 返回对应语言的提示文案（见 src/webui/request_language.py）。
  // 显式设为应用的界面语言，否则浏览器带的是系统语言列表，和用户在应用里选的不一定一致。
  if (!headers.has('Accept-Language')) headers.set('Accept-Language', String(i18n.global.locale.value))
  return headers
}

/**
 * Share-link players prove their seat with the per-seat token for the game the
 * request targets. The owner never sends one implicitly: its own requests act
 * as GM (or name a previewed seat explicitly).
 */
export function applySeatTokenHeader(headers: Headers, path: string): void {
  if (headers.has(SEAT_TOKEN_HEADER) || hasAccessToken()) return
  // Only a player's own share page speaks for a seat. The GM's page and P2P
  // host delegation (which names the bridged seat itself) never attach one,
  // even on a host without an access password.
  if (!isPlayerShareLocation() || /[?&]delegate=(?:1|true|yes)(?:&|$)/u.test(path)) return
  const token = readSeatToken(gameKeyOfApiPath(path))
  if (token) headers.set(SEAT_TOKEN_HEADER, token)
}

/**
 * The seat token this browser held was revoked or replaced (e.g. the GM
 * re-issued the seat's link). Forget it and the cached identity, and send the
 * player to the join page, which explains that a new GM link is needed.
 */
function handleRejectedSeatToken(path: string, status: number, code?: string): void {
  if (status !== 401 || code !== 'SEAT_TOKEN_INVALID' || hasAccessToken()) return
  const gk = gameKeyOfApiPath(path)
  if (!gk) return
  clearSeatToken(gk)
  localStorage.removeItem('trpg_play_user_' + gk)
  const params = new URLSearchParams({ game: gk, share: '1', notice: 'seat' })
  if (!location.hash.startsWith('#/join')) location.hash = `/join?${params.toString()}`
}

/**
 * The room token this browser held was refused: it expired, or the GM changed
 * the room password. Forget it and send the player to the join page, which
 * asks for the room password again.
 */
function handleRejectedRoomToken(path: string, status: number, data: unknown): void {
  if (status !== 403 || hasAccessToken() || !isPlayerShareLocation()) return
  const payload = data && typeof data === 'object' ? data as Record<string, unknown> : {}
  if (payload.needs_room_password !== true) return
  const gk = gameKeyOfApiPath(path)
  if (!gk) return
  clearRoomToken(gk)
  if (location.hash.startsWith('#/join')) {
    window.dispatchEvent(new CustomEvent(ROOM_TOKEN_REJECTED_EVENT, { detail: { gameKey: gk } }))
    return
  }
  const params = new URLSearchParams({ game: gk, share: '1', notice: 'room' })
  location.hash = `/join?${params.toString()}`
}

/**
 * The room token from passing a room password travels in a header, never in
 * the URL, so it cannot end up in access logs. Only the game it was issued
 * for receives it.
 */
export function applyRoomTokenHeader(headers: Headers, path: string): void {
  if (headers.has(ROOM_TOKEN_HEADER)) return
  const token = readRoomToken(gameKeyOfApiPath(path))
  if (token) headers.set(ROOM_TOKEN_HEADER, token)
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

async function fetchWithConnectionRecovery(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init)
  } catch (error: unknown) {
    const isAbortError = typeof error === 'object' && error !== null && 'name' in error
      && (error as { name?: unknown }).name === 'AbortError'
    if (!isAbortError) redirectToBackendLogin()
    throw error
  }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const peer = await interceptPeerApi<T>(path, init)
  if (peer.handled) return peer.value
  const isRawBody = init.body instanceof FormData || init.body instanceof Blob
  const headers = authHeaders(init.headers, !isRawBody)
  applySeatTokenHeader(headers, path)
  applyRoomTokenHeader(headers, path)
  applyConfirmHeader(headers, init)
  const response = await fetchWithConnectionRecovery(apiUrl(path), { ...init, headers, credentials: 'include' })
  const data = await response.json().catch(() => ({}))
  await handleUnauthorized(response)
  if (response.status === 429) {
    const payload = data && typeof data === 'object' ? data as Record<string, unknown> : {}
    const message = typeof payload.error === 'string' && payload.error
      ? payload.error
      : rateLimitMessage(data)
    throw new ApiError(message, 429, errorCodeOf(data), retryAfterOf(data))
  }
  if (!response.ok) {
    handleRejectedSeatToken(path, response.status, errorCodeOf(data))
    handleRejectedRoomToken(path, response.status, data)
    throw new ApiError(data.error || `HTTP ${response.status}`, response.status, errorCodeOf(data), retryAfterOf(data))
  }
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
  applySeatTokenHeader(headers, path)
  applyRoomTokenHeader(headers, path)
  applyConfirmHeader(headers, init)
  const response = await fetchWithConnectionRecovery(apiUrl(path), { ...init, headers, credentials: 'include' })
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
  const response = await fetchWithConnectionRecovery(apiUrl('/login'), { method: 'POST', headers, credentials: 'include' })
  if (response.status === 429) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(rateLimitMessage(data), 429)
  }
  if (!response.ok) throw new ApiError(i18n.global.t('incorrectPassword'), response.status)
}

export function setAccessToken(value: string) {
  notifyAccessTokenChange()
  localStorage.setItem(accessTokenStorageKey(), value)
}
export function hasAccessToken(): boolean { return !!localStorage.getItem(accessTokenStorageKey()) }

export type OwnerAccessStatus = 'allowed' | 'login-required' | 'unavailable'

let ownerAccessProbe: Promise<OwnerAccessStatus> | null = null

async function probeOwnerAccess(): Promise<OwnerAccessStatus> {
  try {
    const response = await fetchWithConnectionRecovery(buildApiUrl('/me'), {
      headers: authHeaders(undefined, false),
      credentials: 'include',
    })
    if (response.status === 401) return 'login-required'
    if (isStandaloneFrontend() && !currentBackendUrl()) return 'login-required'
    return response.ok ? 'allowed' : 'unavailable'
  } catch {
    return isStandaloneFrontend() && !currentBackendUrl() ? 'login-required' : 'unavailable'
  }
}

export async function checkOwnerAccess(): Promise<OwnerAccessStatus> {
  if (ownerAccessProbe) return ownerAccessProbe
  const probe = probeOwnerAccess()
  ownerAccessProbe = probe
  try {
    return await probe
  } finally {
    if (ownerAccessProbe === probe) ownerAccessProbe = null
  }
}

export async function gameEventSource(gameKey: string, cursor = ''): Promise<EventSource> {
  const result = await api<{ ticket: string }>(`/games/${encodeURIComponent(gameKey)}/sse-ticket`, { method: 'POST' })
  const q = new URLSearchParams(shareQuery())
  q.set('ticket', result.ticket)
  if (cursor) q.set('cursor', cursor)
  return new EventSource(`${buildApiUrl(`/games/${encodeURIComponent(gameKey)}/sse`)}?${q}`, { withCredentials: true })
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code) {
    // 有稳定错误码时优先本地化文案（apiErrors.<code>）；未翻译回退后端原文。
    const localized = i18n.global.t(`apiErrors.${error.code}`)
    if (localized && localized !== `apiErrors.${error.code}`) return localized
  }
  return error instanceof Error ? error.message : String(error)
}
