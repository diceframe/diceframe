import { apiUrl, authHeaders } from '@/api/client'
import { i18n } from '@/i18n'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export type AssistantContext = {
  route: string
  game_key?: string
  world_id?: string
  adventure_id?: string
  book_id?: string
  project_id?: string
}

export type AgentEventName = 'session' | 'status' | 'tool_start' | 'tool_result' | 'draft' | 'approval' | 'navigate' | 'authorization' | 'task'

export type AgentAuthorizationMode = 'suggest' | 'routine' | 'task'
export interface AgentAuthorization {
  mode: AgentAuthorizationMode
  scope: Record<string, string>
  domains: string[]
  expires_at: number
  max_steps: number
  remaining_steps: number
}
export interface AgentTask {
  status: string
  completed_steps: number
  total_steps?: number
  used_tokens?: number
  token_budget?: number
  usage_complete?: boolean
  budget_reason?: 'tokens' | 'steps'
  error?: string
}
export interface AgentCapability {
  name: string
  label: string
  domain: string
  version: number
  effect: string
  automatic: boolean
  description: string
  parameters?: Record<string, unknown>
  instructions?: string
  requires_confirmation?: boolean
}
export interface AgentCapabilityDomain {
  domain: string
  count?: number
}

export interface AgentDraftReview {
  ok?: boolean
  draft?: unknown
  plan_digest?: string
  blockers?: unknown[]
  kind?: string
  text?: string
  [key: string]: unknown
}

export interface AgentDraftEvent {
  draft_id: string
  review: AgentDraftReview
}

export interface AgentApproval {
  approval_id: string
  plan_digest: string
  tool: string
  label?: string
  arguments?: unknown
  expires_at?: number | string
  recovery_required?: boolean
  expired?: boolean
  resumable?: boolean
  execution_status?: string
  execution_state?: { report?: unknown; [key: string]: unknown }
  preview?: unknown
}

export interface AgentSession {
  ok?: boolean
  session_id: string
  pending: AgentApproval | null
  drafts: Record<string, AgentDraftReview>
  receipts: Array<{ approval_id: string; plan_digest: string; result?: unknown; response?: AgentConfirmResult }>
  status?: string
  authorization?: AgentAuthorization | null
  task?: AgentTask | null
}

export interface AgentConfirmResult {
  ok: boolean
  result?: unknown
  navigation?: { route: string; query?: Record<string, unknown> }
  cancelled?: boolean
  partial?: boolean
  pending?: AgentApproval | null
  error?: string
}

export interface AgentReviewResult {
  ok: boolean
  draft_id: string
  review: AgentDraftReview
  approval?: AgentApproval
}

export interface AssistantSource {
  source: string
  heading: string
}

export interface AssistantStreamHandlers {
  onDelta: (text: string) => void
  onSources?: (sources: AssistantSource[]) => void
  /** 服务端因输出截断需放大预算重试：清空当前已显示内容后重新流式。 */
  onReset?: () => void
  onAgentEvent?: (event: string, payload: Record<string, unknown>) => void
}

export class AssistantStreamError extends Error {
  readonly code: string

  constructor(message: string, code = 'ASSISTANT_FAILED') {
    super(message)
    this.name = 'AssistantStreamError'
    this.code = code
  }
}

export async function streamAssistantChat(
  messages: ChatMessage[],
  locale: string,
  handlers: AssistantStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(apiUrl('/assistant/chat'), {
    method: 'POST',
    headers: authHeaders(undefined, true),
    body: JSON.stringify({ messages, language: locale }),
    signal,
    credentials: 'include',
  })
  if (!resp.ok || !resp.body) {
    throw new AssistantStreamError(await responseError(resp), `HTTP_${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const consume = (final = false): boolean => {
    while (true) {
      const separator = buffer.match(/\r?\n\r?\n/)
      if (!separator || separator.index === undefined) break
      const block = buffer.slice(0, separator.index)
      buffer = buffer.slice(separator.index + separator[0].length)
      if (dispatchEvent(block, handlers)) return true
    }
    if (final && buffer.trim()) {
      const done = dispatchEvent(buffer, handlers)
      buffer = ''
      return done
    }
    return false
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        consume(true)
        return
      }
      buffer += decoder.decode(value, { stream: true })
      if (consume()) {
        await reader.cancel()
        return
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function streamAssistantAgent(
  messages: ChatMessage[],
  locale: string,
  handlers: AssistantStreamHandlers,
  signal?: AbortSignal,
  sessionId?: string,
  context?: AssistantContext,
): Promise<void> {
  const resp = await fetch(apiUrl('/assistant/agent/run'), {
    method: 'POST',
    headers: authHeaders(undefined, true),
    body: JSON.stringify({
      messages,
      language: locale,
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(context ? { context } : {}),
    }),
    signal,
    credentials: 'include',
  })
  if (!resp.ok || !resp.body) {
    throw new AssistantStreamError(await responseError(resp), `HTTP_${resp.status}`)
  }
  await consumeAssistantStream(resp, handlers)
}

function dispatchEvent(block: string, handlers: AssistantStreamHandlers): boolean {
  const lines = block.split(/\r?\n/)
  const event = lines.find(line => line.startsWith('event:'))?.slice(6).trim() || 'message'
  const data = lines
    .filter(line => line.startsWith('data:'))
    .map(line => line.slice(5).replace(/^ /, ''))
    .join('\n')
  if (event === 'done') return true
  if (!data) return false

  const payload = parsePayload(data)
  if (event === 'session' || event === 'status' || event === 'tool_start' || event === 'tool_result' || event === 'draft' || event === 'approval' || event === 'navigate' || event === 'authorization' || event === 'task') {
    handlers.onAgentEvent?.(event, payload)
  }
  if (event === 'error') {
    throw new AssistantStreamError(
      typeof payload.error === 'string' ? payload.error : i18n.global.t('assistantError'),
      typeof payload.code === 'string' ? payload.code : 'ASSISTANT_FAILED',
    )
  }
  if (event === 'sources') {
    const sources = Array.isArray(payload.sources)
      ? payload.sources.filter(isAssistantSource)
      : []
    handlers.onSources?.(sources)
    return false
  }
  if (event === 'reset') {
    handlers.onReset?.()
    return false
  }
  if (typeof payload.delta === 'string') handlers.onDelta(payload.delta)
  return false
}

async function consumeAssistantStream(resp: Response, handlers: AssistantStreamHandlers): Promise<void> {
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const consume = (final = false): boolean => {
    while (true) {
      const separator = buffer.match(/\r?\n\r?\n/)
      if (!separator || separator.index === undefined) break
      const block = buffer.slice(0, separator.index)
      buffer = buffer.slice(separator.index + separator[0].length)
      if (dispatchEvent(block, handlers)) return true
    }
    if (final && buffer.trim()) {
      const done = dispatchEvent(buffer, handlers)
      buffer = ''
      return done
    }
    return false
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        consume(true)
        return
      }
      buffer += decoder.decode(value, { stream: true })
      if (consume()) {
        await reader.cancel()
        return
      }
    }
  } finally {
    reader.releaseLock()
  }
}

async function agentJson<T>(path: string, body?: Record<string, unknown>): Promise<T> {
  const resp = await fetch(apiUrl(path), {
    method: body ? 'POST' : 'GET',
    headers: authHeaders(undefined, Boolean(body)),
    ...(body ? { body: JSON.stringify(body) } : {}),
    credentials: 'include',
  })
  const payload = await resp.json().catch(() => ({})) as Record<string, unknown>
  if (!resp.ok) {
    const message = typeof payload.error === 'string' ? payload.error : await responseError(resp)
    throw new AssistantStreamError(message, `HTTP_${resp.status}`)
  }
  return payload as T
}

export function confirmAssistantAgent(
  sessionId: string,
  approvalId: string,
  planDigest: string,
  approved: boolean,
): Promise<AgentConfirmResult> {
  return agentJson<AgentConfirmResult>('/assistant/agent/confirm', {
    session_id: sessionId,
    approval_id: approvalId,
    plan_digest: planDigest,
    approved,
  })
}

export async function streamAssistantAgentConfirm(
  sessionId: string,
  approvalId: string,
  planDigest: string,
  approved: boolean,
  handlers: AssistantStreamHandlers & { onConfirmResult?: (result: AgentConfirmResult) => void },
): Promise<void> {
  return _streamConfirmOrResume('/assistant/agent/confirm',
    { session_id: sessionId, approval_id: approvalId, plan_digest: planDigest, approved },
    handlers)
}

async function _streamConfirmOrResume(
  path: string,
  body: Record<string, unknown>,
  handlers: AssistantStreamHandlers & { onConfirmResult?: (result: AgentConfirmResult) => void },
): Promise<void> {
  const resp = await fetch(apiUrl(path), {
    method: 'POST',
    headers: authHeaders(undefined, true),
    body: JSON.stringify(body),
    credentials: 'include',
  })
  if (!resp.ok || !resp.body) {
    throw new AssistantStreamError(await responseError(resp), `HTTP_${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const consume = (final = false): boolean => {
    while (true) {
      const sep = buffer.match(/\r?\n\r?\n/)
      if (!sep || sep.index === undefined) break
      const block = buffer.slice(0, sep.index)
      buffer = buffer.slice(sep.index + sep[0].length)
      const lines = block.split(/\r?\n/)
      const event = lines.find(l => l.startsWith('event:'))?.slice(6).trim() || 'message'
      const data = lines.filter(l => l.startsWith('data:')).map(l => l.slice(5).replace(/^ /, '')).join('\n')
      if (event === 'done') return true
      if (!data) continue
      let payload: Record<string, unknown>
      try { payload = JSON.parse(data) } catch { continue }
      if (event === 'confirm_result') {
        handlers.onConfirmResult?.(payload as unknown as AgentConfirmResult)
      } else if (event === 'error') {
        throw new AssistantStreamError(
          typeof payload.error === 'string' ? payload.error : 'Operation failed',
          typeof payload.code === 'string' ? payload.code : 'AGENT_FAILED',
        )
      } else {
        if (event === 'message' && typeof payload.delta === 'string') handlers.onDelta(payload.delta)
        handlers.onAgentEvent?.(event, payload)
      }
    }
    if (final && buffer.trim()) { consume(false); buffer = '' }
    return false
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) { buffer += decoder.decode(); consume(true); return }
      buffer += decoder.decode(value, { stream: true })
      if (consume()) { await reader.cancel(); return }
    }
  } finally { reader.releaseLock() }
}

export function resumeAssistantAgent(
  sessionId: string,
  approvalId: string,
  planDigest: string,
): Promise<AgentConfirmResult> {
  return agentJson<AgentConfirmResult>('/assistant/agent/resume', {
    session_id: sessionId,
    approval_id: approvalId,
    plan_digest: planDigest,
  })
}

export async function streamAssistantAgentResume(
  sessionId: string,
  approvalId: string,
  planDigest: string,
  handlers: AssistantStreamHandlers & { onConfirmResult?: (result: AgentConfirmResult) => void },
): Promise<void> {
  return _streamConfirmOrResume('/assistant/agent/resume',
    { session_id: sessionId, approval_id: approvalId, plan_digest: planDigest },
    handlers)
}

export function reviewAssistantAgent(
  sessionId: string,
  draftId: string,
  draft: unknown,
): Promise<AgentReviewResult> {
  return agentJson<AgentReviewResult>('/assistant/agent/review', {
    session_id: sessionId,
    draft_id: draftId,
    draft,
  })
}

export function getAssistantAgentSession(sessionId: string): Promise<AgentSession> {
  return agentJson<AgentSession>(`/assistant/agent/session/${encodeURIComponent(sessionId)}`)
}

export function getAssistantAgentCapabilities(query: { q?: string; domain?: string; offset?: number; game_key?: string } = {}): Promise<{ ok: true; revision: string; capabilities: AgentCapability[]; domains?: AgentCapabilityDomain[]; offset?: number; has_more?: boolean; total?: number; discovery_hint?: string }> {
  const params = new URLSearchParams()
  if (query.q?.trim()) params.set('q', query.q.trim())
  if (query.domain?.trim()) params.set('domain', query.domain.trim())
  if (query.game_key?.trim()) params.set('game_key', query.game_key.trim())
  if (typeof query.offset === 'number' && query.offset > 0) params.set('offset', String(Math.floor(query.offset)))
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return agentJson(`/assistant/agent/capabilities${suffix}`)
}

export function authorizeAssistantAgent(input: {
  session_id?: string
  mode: AgentAuthorizationMode
  scope?: Record<string, string>
  domains?: string[]
  max_steps?: number
}): Promise<{ ok: true; session_id: string; authorization: AgentAuthorization | null }> {
  return agentJson('/assistant/agent/authorization', { ...input, max_steps: Math.min(64, Math.max(1, input.max_steps ?? 32)) })
}

export function cancelAssistantAgent(sessionId: string): Promise<{ ok: true; cancelled: boolean; status?: string }> {
  return agentJson('/assistant/agent/cancel', { session_id: sessionId })
}

function parsePayload(data: string): Record<string, unknown> {
  try {
    const value = JSON.parse(data) as unknown
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

function isAssistantSource(value: unknown): value is AssistantSource {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return typeof record.source === 'string' && typeof record.heading === 'string'
}

async function responseError(resp: Response): Promise<string> {
  try {
    const payload = await resp.json() as { error?: unknown }
    if (typeof payload.error === 'string' && payload.error) return payload.error
  } catch {
    // Fall through to the stable UI message.
  }
  return i18n.global.t('assistantRequestFailed')
}
