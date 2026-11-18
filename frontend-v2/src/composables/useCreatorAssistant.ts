import { ref, watch, type Ref } from 'vue'
import {
  cancelAssistantAgent,
  streamAssistantAgentConfirm,
  streamAssistantAgentResume,
  getAssistantAgentSession,
  streamAssistantAgent,
  type AgentApproval,
  type AgentConfirmResult,
  type AgentSession,
  type ChatMessage,
} from '@/api/assistant'
import type { AssistantMessage } from './useAssistant'

const MAX_MESSAGES = 20
const MAX_CHARS = 24_000

function storageKey(scope: string, kind: 'messages' | 'session'): string {
  return `diceframe_creator_assistant_${kind}_${encodeURIComponent(scope)}`
}

function compactConversation(messages: AssistantMessage[]): AssistantMessage[] {
  const selected: AssistantMessage[] = []
  let chars = 0
  for (const message of [...messages].reverse()) {
    if (selected.length >= MAX_MESSAGES) break
    const size = JSON.stringify(message).length
    if (selected.length && chars + size > MAX_CHARS) break
    selected.push(message)
    chars += size
  }
  return selected.reverse()
}

function loadMessages(scope: string): AssistantMessage[] {
  if (!scope) return []
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(scope, 'messages')) || 'null') as unknown
    if (!Array.isArray(value)) return []
    return value
      .filter(item => item && typeof item === 'object' && (item as AssistantMessage).role && typeof (item as AssistantMessage).content === 'string')
      .slice(-MAX_MESSAGES)
      .map(item => {
        const message = item as AssistantMessage
        const marker = message.role === 'user' ? message.content.indexOf('\n\n（当前工程：') : -1
        return marker >= 0 ? { ...message, content: message.content.slice(0, marker).trim() } : message
      }) as AssistantMessage[]
  } catch {
    return []
  }
}

function loadSession(scope: string): string | null {
  if (!scope) return null
  try { return localStorage.getItem(storageKey(scope, 'session'))?.trim() || null } catch { return null }
}

function saveState(scope: string, messages: AssistantMessage[], sessionId: string | null): void {
  if (!scope) return
  try {
    localStorage.setItem(storageKey(scope, 'messages'), JSON.stringify(compactConversation(messages)))
    if (sessionId) localStorage.setItem(storageKey(scope, 'session'), sessionId)
    else localStorage.removeItem(storageKey(scope, 'session'))
  } catch { /* localStorage is optional */ }
}

function normaliseApproval(value: unknown): AgentApproval | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const item = value as Record<string, unknown>
  if (typeof item.approval_id !== 'string' || typeof item.plan_digest !== 'string' || typeof item.tool !== 'string') return null
  return {
    approval_id: item.approval_id,
    plan_digest: item.plan_digest,
    tool: item.tool,
    label: typeof item.label === 'string' ? item.label : undefined,
    arguments: item.arguments,
    preview: item.preview,
    expires_at: typeof item.expires_at === 'number' || typeof item.expires_at === 'string' ? item.expires_at : undefined,
    resumable: item.resumable === true,
    expired: item.expired === true,
    recovery_required: item.recovery_required === true,
    execution_status: typeof item.execution_status === 'string' ? item.execution_status : undefined,
  }
}

/** A project-scoped DF Agent session for Creator Studio. */
export function useCreatorAssistant(scope: Ref<string | undefined>) {
  const messages = ref<AssistantMessage[]>([])
  const streaming = ref(false)
  const status = ref('')
  const task = ref<AgentSession['task']>(null)
  const actionError = ref('')
  const pendingApproval = ref<AgentApproval | null>(null)
  const actionBusy = ref(false)
  const sessionId = ref<string | null>(null)
  // Auto-approve: when true, mutation proposals are confirmed without user interaction
  const autoApprove = ref((() => {
    try { return localStorage.getItem('diceframe_creator_auto_approve') === '1' } catch { return false }
  })())
  let activeScope = ''
  let controller: AbortController | null = null
  let switchGeneration = 0

  function persist(): void { saveState(activeScope, messages.value, sessionId.value) }

  function resetRuntime(): void {
    controller?.abort()
    controller = null
    streaming.value = false
    status.value = ''
    task.value = null
    actionError.value = ''
    pendingApproval.value = null
    actionBusy.value = false
  }

  function updateFromSession(snapshot: AgentSession): void {
    if (snapshot.session_id) sessionId.value = snapshot.session_id
    pendingApproval.value = normaliseApproval(snapshot.pending)
    status.value = snapshot.status || ''
    task.value = snapshot.task || null
  }

  async function restoreSession(generation: number): Promise<void> {
    if (!sessionId.value) return
    try {
      const snapshot = await getAssistantAgentSession(sessionId.value)
      if (generation !== switchGeneration || activeScope !== scope.value) return
      updateFromSession(snapshot)
    } catch {
      if (generation !== switchGeneration || activeScope !== scope.value) return
      // A missing or expired project session only means the local transcript
      // remains available; the next request starts a fresh server session.
      sessionId.value = null
      pendingApproval.value = null
      persist()
    }
  }

  async function switchScope(nextScope: string): Promise<void> {
    const generation = ++switchGeneration
    if (activeScope) persist()
    resetRuntime()
    activeScope = nextScope
    messages.value = loadMessages(nextScope)
    sessionId.value = loadSession(nextScope)
    if (nextScope) await restoreSession(generation)
  }

  watch(scope, value => { void switchScope(String(value || '')) }, { immediate: true })

  async function send(text: string, locale = 'zh-CN', hiddenInstruction = ''): Promise<void> {
    const value = text.trim()
    if (!value || !activeScope || streaming.value) return
    const history: ChatMessage[] = messages.value
      .filter(message => message.content.trim())
      .map(({ role, content }) => ({ role, content: content.slice(0, 8_000) }))
    history.push({ role: 'user', content: value.slice(0, 8_000) })
    const requestMessages = history.slice(-20)
    if (hiddenInstruction && requestMessages.length) {
      const last = requestMessages.length - 1
      requestMessages[last] = { role: 'user', content: `${requestMessages[last].content}\n\n${hiddenInstruction}` }
    }
    const assistant: AssistantMessage = { role: 'assistant', content: '', events: [] }
    messages.value.push({ role: 'user', content: value }, assistant)
    // Keep a stable index so onDelta mutations trigger Vue reactivity
    const assistantIdx = messages.value.length - 1
    streaming.value = true
    status.value = ''
    actionError.value = ''
    controller = new AbortController()
    const generation = switchGeneration
    try {
      await streamAssistantAgent(requestMessages, locale, {
        onDelta(delta) {
          if (generation === switchGeneration) {
            messages.value[assistantIdx] = { ...messages.value[assistantIdx], content: messages.value[assistantIdx].content + delta }
          }
        },
        onAgentEvent(event, payload) {
          if (generation !== switchGeneration) return
          const current = messages.value[assistantIdx]
          const events = [...(current.events || []), { event, payload }]
          messages.value[assistantIdx] = { ...current, events: events.length > 40 ? events.slice(-40) : events }
          if (event === 'session' && typeof payload.session_id === 'string') sessionId.value = payload.session_id
          if (event === 'status') status.value = typeof payload.message === 'string' ? payload.message : ''
          if (event === 'approval') pendingApproval.value = normaliseApproval(payload)
          if (event === 'task') task.value = payload as unknown as AgentSession['task']
        },
      }, controller.signal, sessionId.value || undefined, { route: 'creator',
        ...(activeScope.startsWith('project-') ? { project_id: activeScope } : {}) })
    } catch (error: unknown) {
      assistant.error = error instanceof Error ? error.message : 'Assistant request failed.'
    } finally {
      if (generation === switchGeneration) {
        controller = null
        streaming.value = false
        persist()
      }
    }
  }

  function stop(): void {
    const current = sessionId.value
    controller?.abort()
    controller = null
    streaming.value = false
    if (current) void cancelAssistantAgent(current).catch(() => undefined)
  }

  function addResult(result: AgentConfirmResult, tool = ''): void {
    const message = [...messages.value].reverse().find(item => item.role === 'assistant')
    if (!message) return
    const idx = messages.value.lastIndexOf(message)
    if (idx < 0) return
    const events = [...(message.events || []), { event: 'tool_result', payload: { ok: result.ok, tool, result: result.result, error: result.error } }]
    messages.value[idx] = { ...message, events }
  }

  async function confirmApproval(approved = true, resume = false): Promise<AgentConfirmResult | undefined> {
    const approval = pendingApproval.value
    const currentSession = sessionId.value
    const generation = switchGeneration
    if (!approval || !currentSession || actionBusy.value) return undefined
    if (resume && (!approval.resumable || approval.expired || approval.recovery_required)) return undefined
    const isCurrent = () => generation === switchGeneration && sessionId.value === currentSession
    actionBusy.value = true
    actionError.value = ''
    try {
      let confirmResult: AgentConfirmResult | undefined

      // Ensure there's an assistant message to append delta/events to
      if (!messages.value.length || messages.value[messages.value.length - 1].role !== 'assistant') {
        messages.value.push({ role: 'assistant', content: '', events: [] })
      }
      const assistantIdx = messages.value.length - 1

      const handlers = {
          onDelta(delta: string) {
            if (!isCurrent()) return
            messages.value[assistantIdx] = {
              ...messages.value[assistantIdx],
              content: messages.value[assistantIdx].content + delta,
            }
          },
          onAgentEvent(event: string, payload: Record<string, unknown>) {
            if (!isCurrent()) return
            const current = messages.value[assistantIdx]
            const events = [...(current.events || []), { event, payload }]
            messages.value[assistantIdx] = { ...current, events }
          },
          onConfirmResult(result: AgentConfirmResult) {
            confirmResult = result
          },
      }
      if (resume) {
        await streamAssistantAgentResume(currentSession, approval.approval_id, approval.plan_digest, handlers)
      } else {
        await streamAssistantAgentConfirm(currentSession, approval.approval_id, approval.plan_digest, approved, handlers)
      }
      if (!isCurrent()) return undefined
      const result = confirmResult ?? { ok: false, error: 'No result received' }
      if (approved) addResult(result, approval.tool)
      pendingApproval.value = result.pending ? normaliseApproval(result.pending) : null
      if (!result.ok && !result.pending) actionError.value = result.error || 'Assistant action failed.'
      persist()
      return result
    } catch (error: unknown) {
      if (!isCurrent()) return undefined
      actionError.value = error instanceof Error ? error.message : 'Assistant action failed.'
      return undefined
    } finally {
      if (isCurrent()) actionBusy.value = false
    }
  }

  function resumeApproval(): Promise<AgentConfirmResult | undefined> {
    return confirmApproval(true, true)
  }

  function cancelApproval(): Promise<AgentConfirmResult | undefined> {
    return confirmApproval(false)
  }

  function toggleAutoApprove(): void {
    autoApprove.value = !autoApprove.value
    try { localStorage.setItem('diceframe_creator_auto_approve', autoApprove.value ? '1' : '0') } catch { /* ignore */ }
  }

  // When auto-approve is on, silently confirm any incoming approval
  watch(pendingApproval, (approval) => {
    if (approval && autoApprove.value && !actionBusy.value) {
      void confirmApproval(true)
    }
  })

  return { messages, streaming, status, task, actionError, pendingApproval, actionBusy, autoApprove, toggleAutoApprove, send, stop, confirmApproval,
    // Stubs to match useAssistant interface (not needed in creator context)
    drafts: ref<Record<string, import('./useAssistant').AssistantDraft>>({}),
    navigation: ref<import('./useAssistant').AssistantNavigation | null>(null),
    authorization: ref<import('@/api/assistant').AgentAuthorization | null>(null),
    authorizationBusy: ref(false),
    authorizationError: ref(''),
    retryLast: async (_locale: string) => { /* no-op */ },
    resumeApproval,
    cancelApproval,
    invalidateApproval: () => { /* no-op */ },
    reviewDraft: async (_draftId: string, _draft: import('@/api/assistant').AgentDraftReview) => { /* no-op */ },
    saveDraft: async (_draftId: string, _locale: string) => { /* no-op */ },
    authorize: async (_mode: import('@/api/assistant').AgentAuthorizationMode, _scope?: Record<string, string>, _domains?: string[], _maxSteps?: number) => { /* no-op */ },
  }
}
