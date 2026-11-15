import { ref } from 'vue'
import {
  confirmAssistantAgent,
  streamAssistantAgentConfirm,
  streamAssistantAgentResume,
  authorizeAssistantAgent,
  cancelAssistantAgent,
  getAssistantAgentSession,
  reviewAssistantAgent,
  streamAssistantAgent,
  type AgentApproval,
  type AgentAuthorization,
  type AgentAuthorizationMode,
  type AgentTask,
  type AgentConfirmResult,
  type AgentDraftReview,
  type AgentSession,
  type AssistantContext,
  type AssistantSource,
  type ChatMessage,
} from '@/api/assistant'
import { assistantSaveDraftPrompt } from '@/features/assistant/copy'
import { ProgressiveTextQueue } from './progressiveTextQueue'

export interface AssistantEventRecord { event: string; payload: Record<string, unknown> }
export interface AssistantMessage extends ChatMessage {
  sources?: AssistantSource[]
  events?: AssistantEventRecord[]
  error?: string
  stopped?: boolean
}
export interface AssistantDraft { draft_id: string; review: AgentDraftReview }
export interface AssistantNavigation { route: string; query?: Record<string, string> }

const STORAGE_KEY = 'diceframe_assistant_messages'
const SESSION_STORAGE_KEY = 'diceframe_assistant_session_id'
const MAX_MESSAGES = 20
const MAX_STORED_CHARS = 24_000
const MAX_EVENT_CHARS = 4_000
const messages = ref<AssistantMessage[]>(loadStored())
const streaming = ref(false)
const status = ref('')
const sessionId = ref(loadSessionId())
const pendingApproval = ref<AgentApproval | null>(null)
const drafts = ref<Record<string, AssistantDraft>>({})
const receipts = ref<AgentSession['receipts']>([])
const navigation = ref<AssistantNavigation | null>(null)
const actionBusy = ref(false)
const actionError = ref('')
const authorization = ref<AgentAuthorization | null>(null)
const task = ref<AgentTask | null>(null)
const authorizationBusy = ref(false)
const authorizationError = ref('')
const dirtyDraftIds = new Set<string>()
let controller: AbortController | null = null
let requestGeneration = 0
let activeQueue: ProgressiveTextQueue | null = null
let activeAssistant: AssistantMessage | null = null
let activeDisplayResolve: (() => void) | null = null

function loadSessionId(): string | null {
  try { const value = localStorage.getItem(SESSION_STORAGE_KEY)?.trim(); return value || null } catch { return null }
}
function saveSessionId(value: string | null) {
  sessionId.value = value
  try { if (value) localStorage.setItem(SESSION_STORAGE_KEY, value); else localStorage.removeItem(SESSION_STORAGE_KEY) } catch { /* optional */ }
}
function loadStored(): AssistantMessage[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') as unknown
    return Array.isArray(parsed) ? parsed.filter(isStoredMessage).slice(-MAX_MESSAGES) : []
  } catch { return [] }
}
function isStoredMessage(value: unknown): value is AssistantMessage {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  if ((record.role !== 'user' && record.role !== 'assistant') || typeof record.content !== 'string' || record.content.length > 12_000) return false
  return record.events === undefined || (Array.isArray(record.events) && record.events.every(event => event && typeof event === 'object'))
}
/** The newest messages that fit the browser storage budget of one conversation. */
export function compactConversation(source: AssistantMessage[]): AssistantMessage[] {
  const selected: AssistantMessage[] = []; let chars = 0
  for (const message of [...source].reverse()) {
    if (selected.length >= MAX_MESSAGES) break
    const size = JSON.stringify(message).length
    if (selected.length && chars + size > MAX_STORED_CHARS) break
    selected.push(message); chars += size
  }
  return selected.reverse()
}
export function storedConversationMessages(value: unknown): AssistantMessage[] {
  return Array.isArray(value) ? value.filter(isStoredMessage).slice(-MAX_MESSAGES) : []
}
function persist() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(compactConversation(messages.value))) } catch { /* optional */ }
}

const SECRET_KEY = /(password|passwd|token|secret|api[_-]?key|authorization|credential|cookie)/i
function compactValue(value: unknown, depth = 0): unknown {
  if (depth > 3) return '[…]'
  if (typeof value === 'string') return value.length > MAX_EVENT_CHARS ? `${value.slice(0, MAX_EVENT_CHARS)}…` : value
  if (Array.isArray(value)) return value.slice(0, 20).map(item => compactValue(item, depth + 1))
  if (value && typeof value === 'object') {
    const output: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value)) {
      const budgetField = /^(?:max_steps|remaining_steps|max_tokens)$/.test(key)
      if (!SECRET_KEY.test(key) || (budgetField && typeof item === 'number')) output[key] = compactValue(item, depth + 1)
    }
    return output
  }
  return value
}
function compactPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const value = compactValue(payload)
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function appendEvent(message: AssistantMessage, event: string, payload: Record<string, unknown>) {
  if (event === 'tool_result') {
    const result = payload.result && typeof payload.result === 'object' && !Array.isArray(payload.result)
      ? payload.result as Record<string, unknown> : {}
    payload = { ...payload, ok: payload.ok ?? result.ok ?? true, partial: payload.partial ?? result.partial ?? false }
  }
  const events = message.events || (message.events = [])
  events.push({ event, payload: compactPayload(payload) }); if (events.length > 80) events.splice(0, events.length - 80)
}

export function readAssistantContext(): AssistantContext | undefined {
  if (typeof location === 'undefined') return undefined
  const match = (location.hash || '').match(/^#\/?([^?/#]+)(?:\?([^#]*))?$/)
  if (!match) return undefined
  let route = ''
  try { route = decodeURIComponent(match[1] || '').trim() } catch { return undefined }
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(route)) return undefined
  const routeMap: Record<string, string> = { play: 'play', create: 'create', worlds: 'worlds', lorebook: 'lorebook', adventures: 'adventures' }
  const allowedRoutes = new Set(['overview', 'create', 'play', 'characters', 'library', 'creator', 'map', 'lorebook', 'worlds', 'adventures', 'modules', 'memory', 'logs', 'rules', 'settings', 'plugins'])
  if (!allowedRoutes.has(route)) return undefined
  const context: AssistantContext = { route: routeMap[route] || route }; const params = new URLSearchParams(match[2] || '')
  const aliases: Record<string, keyof AssistantContext> = { game: 'game_key', game_key: 'game_key', world: 'world_id', world_id: 'world_id', adventure: 'adventure_id', adventure_id: 'adventure_id', book: 'book_id', book_id: 'book_id' }
  for (const [key, target] of Object.entries(aliases)) {
    const value = params.get(key)?.trim(); if (value && value.length <= 200) context[target] = value
  }
  return context
}

const NAV_ROUTES = new Set(['overview', 'create', 'play', 'characters', 'library', 'creator', 'map', 'lorebook', 'worlds', 'adventures', 'modules', 'memory', 'logs', 'rules', 'settings', 'plugins'])
const NAV_QUERY_KEYS = new Set(['game', 'section', 'world', 'book', 'adventure', 'intent'])
function safeNavigation(value: unknown): AssistantNavigation | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if ('url' in record || 'hash' in record) return null
  let route = typeof record.route === 'string' ? record.route.trim() : ''
  if (route.startsWith('/')) route = route.slice(1)
  if (!NAV_ROUTES.has(route) || route.includes(':') || /javascript|https?|url|hash/i.test(route)) return null
  const query: Record<string, string> = {}
  if (record.query && typeof record.query === 'object' && !Array.isArray(record.query)) {
    for (const [key, raw] of Object.entries(record.query as Record<string, unknown>)) {
      if (!NAV_QUERY_KEYS.has(key) || SECRET_KEY.test(key)) return null
      if (typeof raw !== 'string' && typeof raw !== 'number' && typeof raw !== 'boolean') continue
      const text = String(raw); if (text.length <= 200 && !/javascript:|https?:\/\//i.test(text)) query[key] = text
    }
  }
  return { route, ...(Object.keys(query).length ? { query } : {}) }
}
function approvalExpired(value: AgentApproval): boolean {
  if (value.recovery_required) return true
  // A resumable plan has already been dispatched. Its original approval
  // window must not prevent the owner from explicitly resuming the remaining
  // targets in a fresh execution window.
  if (value.resumable) return false
  if (value.expires_at === undefined || value.expires_at === null) return false
  const raw = typeof value.expires_at === 'number' || /^\d+(?:\.\d+)?$/.test(String(value.expires_at))
    ? Number(value.expires_at) * (Number(value.expires_at) < 10_000_000_000 ? 1000 : 1)
    : Date.parse(String(value.expires_at))
  return Number.isFinite(raw) && raw <= Date.now()
}
function normaliseApproval(value: unknown): AgentApproval | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (typeof record.approval_id !== 'string' || typeof record.plan_digest !== 'string' || typeof record.tool !== 'string') return null
  const approval = {
    approval_id: record.approval_id,
    plan_digest: record.plan_digest,
    tool: record.tool,
    label: typeof record.label === 'string' ? record.label : undefined,
    arguments: record.arguments,
    expires_at: typeof record.expires_at === 'number' || typeof record.expires_at === 'string' ? record.expires_at : undefined,
    preview: record.preview,
    recovery_required: record.recovery_required === true,
    resumable: record.resumable === true,
    execution_status: typeof record.execution_status === 'string' ? record.execution_status : undefined,
    execution_state: record.execution_state && typeof record.execution_state === 'object' && !Array.isArray(record.execution_state) ? record.execution_state as AgentApproval['execution_state'] : undefined,
  }
  const invalid = approvalExpired(approval)
  return { ...approval, ...(invalid ? { expired: true } : {}) }
}
function approvalDraftId(approval: AgentApproval): string | undefined {
  const find = (value: unknown, depth = 0): string | undefined => {
    if (depth > 3 || !value || typeof value !== 'object' || Array.isArray(value)) return undefined
    const record = value as Record<string, unknown>
    for (const key of ['draft_id', 'draftId']) if (typeof record[key] === 'string') return record[key]
    for (const child of Object.values(record)) { const found = find(child, depth + 1); if (found) return found }
    return undefined
  }
  return find(approval.arguments) || find(approval.preview)
}
function markDirtyApproval(approval: AgentApproval | null): AgentApproval | null {
  if (!approval) return null
  const draftId = approvalDraftId(approval)
  if (draftId && dirtyDraftIds.has(draftId)) return { ...approval, expired: true, recovery_required: true }
  return approval
}
function updateFromSession(snapshot: AgentSession) {
  if (snapshot.session_id) saveSessionId(snapshot.session_id)
  pendingApproval.value = markDirtyApproval(normaliseApproval(snapshot.pending))
  receipts.value = Array.isArray(snapshot.receipts) ? snapshot.receipts.map(receipt => ({ ...receipt, result: receipt.result !== undefined ? receipt.result : receipt.response?.result })) : []; const restored: Record<string, AssistantDraft> = {}
  if (snapshot.drafts && typeof snapshot.drafts === 'object') for (const [id, review] of Object.entries(snapshot.drafts)) if (review && typeof review === 'object') restored[id] = { draft_id: id, review }
  for (const id of dirtyDraftIds) if (drafts.value[id]) restored[id] = drafts.value[id]
  drafts.value = restored; status.value = typeof snapshot.status === 'string' ? snapshot.status : ''
  authorization.value = snapshot.authorization || null
  task.value = snapshot.task || null
}
async function restoreSession() {
  const current = sessionId.value || loadSessionId(); if (!current) return
  if (!sessionId.value) sessionId.value = current
  const generation = requestGeneration
  try {
    const snapshot = await getAssistantAgentSession(current)
    if (generation !== requestGeneration) return
    if (streaming.value || actionBusy.value) return
    updateFromSession(snapshot)
  }
  catch (error: unknown) {
    const code = error && typeof error === 'object' && 'code' in error ? String((error as { code?: unknown }).code) : ''
    if (generation !== requestGeneration) return
    if (code.includes('401') || code.includes('403') || code.includes('404')) { saveSessionId(null); pendingApproval.value = null; drafts.value = {}; receipts.value = [] }
  }
}

async function send(text: string, locale = 'zh-CN') {
  const value = text.trim(); if (!value || streaming.value) return
  const candidates: ChatMessage[] = messages.value.filter(message => message.content.trim()).map(({ role, content }) => ({ role, content: content.slice(0, 8_000) }))
  candidates.push({ role: 'user', content: value.slice(0, 8_000) })
  const requestMessages: ChatMessage[] = []; let totalChars = 0
  for (const message of candidates.reverse()) {
    if (requestMessages.length >= 20 || totalChars + message.content.length > 24_000) break
    requestMessages.push(message); totalChars += message.content.length
  }
  requestMessages.reverse()
  const userMessage: AssistantMessage = { role: 'user', content: value }; const assistantMessage: AssistantMessage = { role: 'assistant', content: '', events: [] }
  messages.value.push(userMessage, assistantMessage); const assistantIndex = messages.value.length - 1; streaming.value = true; status.value = ''; actionError.value = ''
  const generation = ++requestGeneration; controller = new AbortController()
  const requestSignal = controller.signal
  activeAssistant = messages.value[assistantIndex] || assistantMessage
  let resolveDisplay!: () => void
  const displayComplete = new Promise<void>(resolve => { resolveDisplay = resolve })
  activeDisplayResolve = resolveDisplay
  const displayMessage = () => messages.value[assistantIndex]
  const queue = new ProgressiveTextQueue({
    onText: (chunk) => {
      if (generation !== requestGeneration || requestSignal.aborted) return
      const message = displayMessage()
      if (message) message.content += chunk
    },
    onIdle: () => {
      if (generation === requestGeneration) resolveDisplay()
    },
  })
  activeQueue = queue
  try {
    await streamAssistantAgent(requestMessages, locale, {
      onDelta(delta) { if (generation === requestGeneration && !requestSignal.aborted) queue.push(delta) },
      onSources(sources) { if (generation === requestGeneration && !requestSignal.aborted) { const message = displayMessage(); if (message) message.sources = sources } },
      onReset() { if (generation === requestGeneration && !requestSignal.aborted) { queue.resetPending(); const message = displayMessage(); if (message) message.content = '' } },
      onAgentEvent(event, payload) {
        if (generation !== requestGeneration || requestSignal.aborted) return
        const message = displayMessage(); if (!message) return
        appendEvent(message, event, payload)
        if (event === 'session' && typeof payload.session_id === 'string' && payload.session_id.trim()) saveSessionId(payload.session_id.trim())
        if (event === 'status') status.value = typeof payload.message === 'string' ? payload.message : ''
        if (event === 'approval') pendingApproval.value = markDirtyApproval(normaliseApproval(payload))
        if (event === 'draft' && typeof payload.draft_id === 'string' && payload.review && typeof payload.review === 'object') drafts.value[payload.draft_id] = { draft_id: payload.draft_id, review: payload.review as AgentDraftReview }
        if (event === 'navigate') navigation.value = safeNavigation(payload)
        if (event === 'authorization') authorization.value = payload.authorization && typeof payload.authorization === 'object' ? payload.authorization as AgentAuthorization : null
        if (event === 'task') task.value = payload as unknown as AgentTask
      },
    }, requestSignal, sessionId.value || undefined, readAssistantContext())
    queue.closeInput()
    await displayComplete
  } catch (error: unknown) {
    if (generation !== requestGeneration) return
    queue.flush()
    const message = displayMessage()
    if (message) {
      if (error instanceof DOMException && error.name === 'AbortError') message.stopped = true
      else message.error = error instanceof Error ? error.message : (locale.startsWith('zh') ? '助手请求失败，请重试。' : 'Assistant request failed.')
    }
  } finally {
    if (generation !== requestGeneration) return
    controller = null; activeQueue?.cancel(); activeQueue = null; activeAssistant = null; activeDisplayResolve = null; streaming.value = false; persist()
  }
}
function stop() {
  if (!controller) return
  const currentSession = sessionId.value
  const stopGeneration = requestGeneration
  if (activeAssistant) activeAssistant.stopped = true
  activeQueue?.flush()
  controller.abort()
  // Cancellation is a server operation; aborting the model stream alone cannot
  // stop already dispatched work. The server keeps committed writes intact.
  if (currentSession) void cancelAssistantAgent(currentSession).then(() => {
    if (stopGeneration === requestGeneration && sessionId.value === currentSession) {
      authorization.value = null
      task.value = { ...(task.value || { status: 'cancelled', completed_steps: 0 }), status: 'cancelled' }
    }
  }).catch(() => undefined)
}

async function authorize(mode: AgentAuthorizationMode, scope?: Record<string, string>, domains?: string[], maxSteps = 32) {
  if (authorizationBusy.value) return
  const generation = requestGeneration
  const expectedSession = sessionId.value
  authorizationBusy.value = true; authorizationError.value = ''
  try {
    const result = await authorizeAssistantAgent({ session_id: expectedSession || undefined, mode, scope, domains, max_steps: maxSteps })
    if (generation !== requestGeneration || sessionId.value !== expectedSession) return
    saveSessionId(result.session_id); authorization.value = result.authorization
  } catch (error: unknown) {
    if (generation !== requestGeneration || sessionId.value !== expectedSession) return
    authorizationError.value = error instanceof Error ? error.message : 'Authorization request failed.'
  } finally { if (generation === requestGeneration) authorizationBusy.value = false }
}
async function retryLast(locale = 'zh-CN') {
  if (streaming.value) return; let userIndex = -1
  for (let index = messages.value.length - 1; index >= 0; index -= 1) if (messages.value[index].role === 'user') { userIndex = index; break }
  if (userIndex < 0) return; const text = messages.value[userIndex].content; messages.value.splice(userIndex); await send(text, locale)
}
function addResult(result: AgentConfirmResult, tool = '') {
  const message = [...messages.value].reverse().find(item => item.role === 'assistant')
  if (message) appendEvent(message, 'tool_result', { ok: result.ok, partial: result.partial, tool, result: result.result, error: result.error })
  if (result.navigation) navigation.value = safeNavigation(result.navigation)
}
function updateTaskAfterApproval(result: AgentConfirmResult, approved: boolean, resuming = false) {
  const current = task.value
  if (!current || (current.status !== 'approval_required' && !resuming)) return
  if (approved && result.pending) {
    task.value = { ...current, status: result.pending.execution_status === 'cancelled' ? 'cancelled' : 'paused', error: undefined }
  } else if (result.ok && approved && !result.partial) {
    task.value = { ...current, status: 'completed', completed_steps: current.completed_steps + 1 }
  } else if (result.ok && approved) {
    task.value = { ...current, status: result.pending?.execution_status || 'partial', error: undefined }
  } else if (result.ok && !approved) {
    task.value = { ...current, status: 'cancelled' }
  } else {
    const recovery = result && typeof result === 'object' && (result as AgentConfirmResult & { recovery_required?: boolean }).recovery_required === true
    task.value = { ...current, status: recovery ? 'recovery_required' : 'failed', ...(result.error ? { error: result.error } : {}) }
  }
}

function applyApprovalResult(result: AgentConfirmResult, approval: AgentApproval, approved: boolean) {
  // A partial/cancelled execution keeps the signed plan available for an
  // explicit resume. Rejections still clear it as the user asked.
  if (!approved) {
    pendingApproval.value = null
  } else if (result.pending) {
    pendingApproval.value = markDirtyApproval(normaliseApproval(result.pending))
  } else if (result.partial) {
    // The server contract should include pending for resumable partial work.
    // Retain the current plan if an older server omits it so a receipt cannot
    // make the task appear complete or discard the report.
    pendingApproval.value = markDirtyApproval(approval)
  } else {
    pendingApproval.value = null
  }
  addResult(result, approval.tool)
  updateTaskAfterApproval(result, approved, approval.resumable === true)
  actionError.value = result.ok || result.pending ? '' : result.error || 'Assistant action failed.'
}
function receiptResponse(receipt: AgentSession['receipts'][number]): AgentConfirmResult {
  if (receipt.response && typeof receipt.response.ok === 'boolean') return receipt.response
  const raw = receipt.result
  if (raw && typeof raw === 'object' && !Array.isArray(raw) && typeof (raw as Record<string, unknown>).ok === 'boolean') return raw as AgentConfirmResult
  return { ok: true, result: raw }
}
function cacheReceipt(approval: AgentApproval, response: AgentConfirmResult) {
  const next = { approval_id: approval.approval_id, plan_digest: approval.plan_digest, result: response.result, response }
  const index = receipts.value.findIndex(item => item.approval_id === approval.approval_id && item.plan_digest === approval.plan_digest)
  if (index >= 0) receipts.value.splice(index, 1, next)
  else receipts.value.push(next)
}
async function confirmApproval(approved = true): Promise<AgentConfirmResult | undefined> {
  const pending = pendingApproval.value; const currentSession = sessionId.value; if (!pending || !currentSession || actionBusy.value) return undefined
  if (approved && (pending.expired || pending.recovery_required)) return undefined
  const receipt = approved
    ? receipts.value.find(item => item.approval_id === pending.approval_id && item.plan_digest === pending.plan_digest)
    : undefined
  if (receipt) {
    const recovered = receiptResponse(receipt)
    applyApprovalResult(recovered, pending, approved)
    return recovered
  }
  actionBusy.value = true; actionError.value = ''
  const generation = requestGeneration
  // Ensure there's an assistant message to stream summary into
  if (!messages.value.length || messages.value[messages.value.length - 1].role !== 'assistant') {
    messages.value.push({ role: 'assistant', content: '', events: [] })
  }
  const assistantIdx = messages.value.length - 1
  try {
    let confirmResult: AgentConfirmResult | undefined
    await streamAssistantAgentConfirm(currentSession, pending.approval_id, pending.plan_digest, approved, {
      onDelta(delta) {
        if (generation !== requestGeneration) return
        messages.value[assistantIdx] = { ...messages.value[assistantIdx], content: messages.value[assistantIdx].content + delta }
      },
      onAgentEvent(event, payload) {
        if (generation !== requestGeneration) return
        const current = messages.value[assistantIdx]
        messages.value[assistantIdx] = { ...current, events: [...(current.events || []), { event, payload }] }
      },
      onConfirmResult(result) { confirmResult = result },
    })
    if (generation !== requestGeneration) return undefined
    const result = confirmResult ?? { ok: false, error: 'No result received' }
    if (approved && (result.ok || result.pending || result.partial)) cacheReceipt(pending, result)
    applyApprovalResult(result, pending, approved)
    persist(); return result
  } catch (error: unknown) {
    if (generation !== requestGeneration) return undefined
    try {
      const snapshot = await getAssistantAgentSession(currentSession)
      if (generation === requestGeneration) {
        updateFromSession(snapshot)
        const r = receipts.value.find(item => item.approval_id === pending.approval_id && item.plan_digest === pending.plan_digest)
        if (r) { const recovered = receiptResponse(r); applyApprovalResult(recovered, pending, approved); return recovered }
      }
    } catch { /* Preserve the original confirmation failure for the user. */ }
    actionError.value = error instanceof Error ? error.message : 'Assistant action failed.'; return undefined
  }
  finally { if (generation === requestGeneration) actionBusy.value = false }
}
async function resumeApproval(): Promise<AgentConfirmResult | undefined> {
  const pending = pendingApproval.value
  const currentSession = sessionId.value
  if (!pending?.resumable || pending.expired || pending.recovery_required || !currentSession || actionBusy.value) return undefined
  const generation = requestGeneration
  actionBusy.value = true
  actionError.value = ''
  if (!messages.value.length || messages.value[messages.value.length - 1].role !== 'assistant') {
    messages.value.push({ role: 'assistant', content: '', events: [] })
  }
  const assistantIdx = messages.value.length - 1
  try {
    let resumeResult: AgentConfirmResult | undefined
    await streamAssistantAgentResume(currentSession, pending.approval_id, pending.plan_digest, {
      onDelta(delta) {
        if (generation !== requestGeneration || sessionId.value !== currentSession) return
        messages.value[assistantIdx] = { ...messages.value[assistantIdx], content: messages.value[assistantIdx].content + delta }
      },
      onAgentEvent(event, payload) {
        if (generation !== requestGeneration || sessionId.value !== currentSession) return
        const current = messages.value[assistantIdx]
        messages.value[assistantIdx] = { ...current, events: [...(current.events || []), { event, payload }] }
      },
      onConfirmResult(result) { resumeResult = result },
    })
    if (generation !== requestGeneration || sessionId.value !== currentSession) return undefined
    const result = resumeResult ?? { ok: false, error: 'No result received' }
    if (result.ok || result.pending || result.partial) cacheReceipt(pending, result)
    applyApprovalResult(result, pending, true)
    persist()
    return result
  } catch (error: unknown) {
    if (generation !== requestGeneration || sessionId.value !== currentSession) return undefined
    actionError.value = error instanceof Error ? error.message : 'Assistant action failed.'
    return undefined
  } finally {
    if (generation === requestGeneration && sessionId.value === currentSession) actionBusy.value = false
  }
}
async function cancelApproval() { stop(); return confirmApproval(false) }
async function reviewDraft(draftId: string, draft: unknown) {
  const currentSession = sessionId.value; if (!currentSession || actionBusy.value) return undefined
  const generation = requestGeneration
  pendingApproval.value = null
  actionBusy.value = true; actionError.value = ''
  try {
    const result = await reviewAssistantAgent(currentSession, draftId, draft)
    if (generation !== requestGeneration) return undefined
    drafts.value[draftId] = { draft_id: draftId, review: result.review }
    dirtyDraftIds.delete(draftId)
    pendingApproval.value = markDirtyApproval(normaliseApproval(result.approval))
    persist(); return result
  }
  catch (error: unknown) { if (generation !== requestGeneration) return undefined; actionError.value = error instanceof Error ? error.message : 'Draft review failed.'; return undefined }
  finally { if (generation === requestGeneration) actionBusy.value = false }
}
async function saveDraft(draftId: string, locale = 'zh-CN') { await send(assistantSaveDraftPrompt(locale, draftId), locale) }
async function invalidateApproval(draftId?: string) {
  if (draftId) dirtyDraftIds.add(draftId)
  const pending = pendingApproval.value; pendingApproval.value = null
  const currentSession = sessionId.value
  if (!pending || !currentSession) return
  const generation = requestGeneration
  actionBusy.value = true
  try { await confirmAssistantAgent(currentSession, pending.approval_id, pending.plan_digest, false) } catch { /* The local dirty fence still prevents confirming the stale proposal. */ }
  finally { if (generation === requestGeneration) actionBusy.value = false }
}
function resetRuntime() {
  ++requestGeneration; activeQueue?.cancel(); activeQueue = null; activeDisplayResolve?.(); activeDisplayResolve = null; controller?.abort(); controller = null; activeAssistant = null; streaming.value = false; actionBusy.value = false; authorizationBusy.value = false; status.value = ''; pendingApproval.value = null; drafts.value = {}; receipts.value = []; navigation.value = null; actionError.value = ''; authorization.value = null; task.value = null; authorizationError.value = ''; dirtyDraftIds.clear()
}
function clear() {
  resetRuntime(); messages.value = []; saveSessionId(null)
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* optional */ }
}
/** Make another stored conversation the active one and reload its server session. */
async function loadConversation(nextMessages: AssistantMessage[], nextSessionId: string | null) {
  resetRuntime(); messages.value = storedConversationMessages(nextMessages); saveSessionId(nextSessionId?.trim() || null); persist()
  await restoreSession()
}
export function useAssistant() { return { messages, streaming, status, sessionId, pendingApproval, drafts, navigation, actionBusy, actionError, authorization, task, authorizationBusy, authorizationError, send, stop, retryLast, clear, loadConversation, restoreSession, confirmApproval, resumeApproval, cancelApproval, reviewDraft, saveDraft, invalidateApproval, authorize } }
