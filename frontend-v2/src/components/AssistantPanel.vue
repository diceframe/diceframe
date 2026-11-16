<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { NButton, NIcon, NInput } from 'naive-ui'
import {
  AddOutline, ChatbubblesOutline, CloseOutline, ContractOutline, CreateOutline, DocumentTextOutline, ExpandOutline, MicOutline,
  RefreshOutline, SendOutline, StopCircleOutline, TrashOutline, VolumeHighOutline, VolumeMuteOutline,
} from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import { useAssistant } from '@/composables/useAssistant'
import { useAssistantWorkspace } from '@/composables/useAssistantWorkspace'
import { renderSafeMarkdown } from '@/utils/markdown'
import AgentTaskCard from '@/components/assistant/AgentTaskCard.vue'
import AgentExecutionSummary from '@/components/assistant/AgentExecutionSummary.vue'
import AgentAuthorizationPanel from '@/components/assistant/AgentAuthorizationPanel.vue'
import AssistantAvatar, { type AssistantAvatarMood } from '@/components/assistant/AssistantAvatar.vue'
import { loadAssistantVoiceConfig, useAssistantVoice } from '@/composables/useAssistantVoice'
import { assistantVoiceCopy } from '@/features/assistant/voiceCopy'
import { useAssistantConversations } from '@/composables/useAssistantConversations'
import { useAssistantPreferences } from '@/composables/useAssistantPreferences'
import { assistantConversationCopy } from '@/features/assistant/conversationCopy'
import { appendDictated } from '@/utils/asr'
import { assistantCopy, assistantToolLabel } from '@/features/assistant/copy'
import { assistantWorkspaceCopy } from '@/features/assistant/workspaceCopy'
import { assistantAuthorizationCopy } from '@/features/assistant/authorizationCopy'
import { generatedImageUrl } from '@/api/generatedImages'

// `expanded`: the window is maximized; `wide`: the window is wide enough for the side-by-side layout.
const props = withDefaults(defineProps<{ expanded?: boolean; wide?: boolean; reviewRequest?: number }>(), { expanded: false, wide: false, reviewRequest: 0 })
const emit = defineEmits<{ close: []; toggleExpanded: [] }>()
const { t, locale } = useLocale()
const { messages, streaming, send, stop, retryLast, pendingApproval, drafts, status, navigation, actionBusy, actionError, confirmApproval, resumeApproval, cancelApproval, reviewDraft, saveDraft, invalidateApproval, authorization, task, authorizationBusy, authorizationError, authorize } = useAssistant()
const draftList = computed(() => Object.values(drafts.value))
const eventList = computed(() => messages.value.flatMap(message => message.events || []))
const agentCopy = computed(() => assistantCopy(locale.value))
const workspaceCopy = computed(() => assistantWorkspaceCopy(locale.value))
const { collapsed: workspaceCollapsed, toggle: toggleWorkspace } = useAssistantWorkspace()
const workspaceForcedOpen = ref(false)
const workspaceIsCollapsed = computed(() => workspaceForcedOpen.value ? false : workspaceCollapsed.value)
const workspaceId = useId()
const locationHash = ref(typeof window === 'undefined' ? '' : window.location.hash)
const agentContext = computed(() => {
  const query = locationHash.value.split('?')[1]?.split('#')[0]
  if (!query) return {}
  const params = new URLSearchParams(query)
  // Use the most specific page object. Requiring its parent IDs as extra
  // action arguments would prevent otherwise valid scoped book/card edits.
  for (const [target, keys] of [['book_id', ['book', 'book_id']], ['card_id', ['card', 'card_id']], ['game_key', ['game', 'game_key']], ['world_id', ['world', 'world_id']]] as const) {
    for (const key of keys) {
      const value = params.get(key)?.trim()
      if (value && value.length <= 200) return { [target]: value }
    }
  }
  return {}
})
const imageUrls = ref<Record<string, string>>({})
const imageLoads = new Set<string>()
const draft = ref('')
const voice = useAssistantVoice()
const conversationsApi = useAssistantConversations()
const { conversations, activeId: activeConversationId } = conversationsApi
const conversationCopy = computed(() => assistantConversationCopy(locale.value))
const historyOpen = ref(false)
const renamingId = ref('')
const renameDraft = ref('')
const conversationBusy = computed(() => streaming.value || actionBusy.value)
const conversationList = computed(() => {
  const list = [...conversations.value]
  if (messages.value.length && !list.some(item => item.id === activeConversationId.value)) {
    list.unshift({ id: activeConversationId.value, title: '', createdAt: Date.now(), updatedAt: Date.now(), sessionId: null, messages: [] })
  }
  return list
})
function conversationLabel(title: string): string { return title || conversationCopy.value.untitled }
function conversationTime(value: number): string {
  try {
    return new Intl.DateTimeFormat(locale.value, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(value)
  } catch { return '' }
}
function newConversation() {
  if (!conversationsApi.startNew()) return
  draft.value = ''
  historyOpen.value = false
}
async function openConversation(id: string) {
  if (await conversationsApi.open(id)) historyOpen.value = !props.expanded ? false : historyOpen.value
}
async function deleteConversation(id: string) {
  await conversationsApi.remove(id)
}
function startRename(id: string, title: string) {
  renamingId.value = id
  renameDraft.value = title
}
function finishRename() {
  if (renamingId.value) conversationsApi.rename(renamingId.value, renameDraft.value)
  renamingId.value = ''
}
const { listening, transcribing, interim, voiceError, serverMessage, voiceReply, voiceInput, inputEngine, speaking } = voice
const voiceCopy = computed(() => assistantVoiceCopy(locale.value))
const voiceInputAvailable = computed(() => voiceInput.value && inputEngine.value !== 'none')
const voiceErrorText = computed(() => serverMessage.value
  || (voiceError.value ? voiceCopy.value[voiceError.value === 'mic-denied' ? 'micDenied' : voiceError.value] : ''))
onMounted(() => { void loadAssistantVoiceConfig() })
watch(activeConversationId, () => voice.cancelListening())
const headMood = computed<AssistantAvatarMood>(() => {
  if (listening.value) return 'listening'
  if (speaking.value) return 'speaking'
  if (streaming.value || actionBusy.value) return 'thinking'
  if (pendingApproval.value || actionError.value) return 'attention'
  return 'idle'
})
const transcriptEl = ref<HTMLElement | null>(null)
const tasksEl = ref<HTMLElement | null>(null)
const approvalEl = ref<HTMLElement | null>(null)
// The workspace is for things that need the owner: drafts, confirmations,
// errors, a page to open, or live progress. Finished actions are shown inline
// under the reply that performed them.
const hasTasks = computed(() => draftList.value.length > 0 || pendingApproval.value || actionError.value || pendingNavigation.value)
const workspaceVisible = computed(() => hasTasks.value || (streaming.value && Boolean(status.value)) || props.expanded)
const workspaceStatus = computed(() => {
  if (actionError.value || pendingApproval.value?.expired || pendingApproval.value?.recovery_required) return workspaceCopy.value.attention
  if (pendingApproval.value?.resumable) return assistantAuthorizationCopy(locale.value).taskStatus(pendingApproval.value.execution_status || 'paused')
  if (pendingApproval.value) return agentCopy.value.statusApproval
  if (streaming.value || actionBusy.value) return workspaceCopy.value.working
  return draftList.value.length ? workspaceCopy.value.drafts(draftList.value.length) : agentCopy.value.statusIdle
})
// The global shell restores the server session while the launcher is closed,
// so the FAB can surface pending approvals before this panel is mounted.

const quickQuestions = () => [
  { id: 'create', prompt: agentCopy.value.create },
  { id: 'start', prompt: agentCopy.value.start },
  { id: 'api', prompt: t('assistantQuickApi') },
  { id: 'plugin', prompt: t('assistantQuickPlugin') },
  { id: 'players', prompt: t('assistantQuickPlayers') },
  { id: 'tunnel', prompt: t('assistantQuickTunnel') },
  { id: 'runtime-logs', prompt: t('assistantQuickLogs') },
]

function scrollToBottom() {
  void nextTick(() => {
    const element = transcriptEl.value
    if (element && element.clientHeight > 0) element.scrollTop = element.scrollHeight
  })
}

function scrollTasksToBottom() {
  void nextTick(() => {
    const element = tasksEl.value
    const editing = element?.contains(document.activeElement) && document.activeElement?.matches('input, textarea, select, [contenteditable="true"]')
    if (element && element.clientHeight > 0 && !editing) element.scrollTop = element.scrollHeight
  })
}

watch(messages, scrollToBottom, { deep: true })
watch([pendingApproval, status, streaming], scrollTasksToBottom)
watch(() => props.expanded, () => { scrollToBottom(); scrollTasksToBottom() })
watch(workspaceCollapsed, value => { if (!value) scrollTasksToBottom() })
function focusPendingApproval() {
  workspaceForcedOpen.value = true
  void nextTick(() => approvalEl.value?.scrollIntoView?.({ block: 'nearest' }))
}
watch(() => props.reviewRequest, value => {
  if (!value) return
  focusPendingApproval()
}, { immediate: true })

function renderMd(text: string): string {
  return renderSafeMarkdown(text)
}

async function onSubmit() {
  const value = draft.value.trim()
  if (!value || streaming.value) return
  draft.value = ''
  voice.stopSpeaking()
  await send(value, locale.value)
}

function toggleListening() {
  if (listening.value) { voice.stopListening(); return }
  const conversationAtStart = activeConversationId.value
  // A spoken request is sent as soon as recognition settles, like talking to NOMI.
  voice.startListening(locale.value, (text) => {
    if (activeConversationId.value !== conversationAtStart) return
    draft.value = appendDictated(draft.value, text)
    void onSubmit()
  })
}

function toggleVoiceReply() {
  voice.setVoiceReply(!voiceReply.value)
}

watch(streaming, (isStreaming, wasStreaming) => {
  if (isStreaming || !wasStreaming || !voiceReply.value) return
  const last = messages.value[messages.value.length - 1]
  if (last?.role === 'assistant' && last.content) voice.speakReply(last.content, locale.value)
})
onBeforeUnmount(() => voice.cancelListening())

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void onSubmit()
  }
}

function ask(question: string) {
  if (streaming.value) return
  draft.value = question
  void onSubmit()
}

function saveDraftLocalized(draftId: string) { return saveDraft(draftId, locale.value) }
function statusText(value: string): string {
  if (value === 'idle') return agentCopy.value.statusIdle
  if (value === 'approval_required') return agentCopy.value.statusApproval
  return value
}
function toolLabel(tool: unknown): string { return assistantToolLabel(locale.value, tool) }
const currentHash = ref(typeof window === 'undefined' ? '' : window.location.hash)
const onHashChange = () => { currentHash.value = window.location.hash; locationHash.value = window.location.hash }
onMounted(() => window.addEventListener('hashchange', onHashChange))
onBeforeUnmount(() => window.removeEventListener('hashchange', onHashChange))
function navigationHash(target: { route: string; query?: Record<string, string> }): string {
  const query = target.query ? new URLSearchParams(target.query).toString() : ''
  return `#/${target.route}${query ? `?${query}` : ''}`
}
const navigationIsCurrent = computed(() => Boolean(navigation.value && navigationHash(navigation.value) === currentHash.value))
// Page changes the agent made by itself, so the reply can offer a way back.
const autoFollowed = ref<{ hash: string; from: string } | null>(null)
const pendingNavigation = computed(() => Boolean(navigation.value && !navigationIsCurrent.value
  && autoFollowed.value?.hash !== navigationHash(navigation.value)))
const { autoNavigate } = useAssistantPreferences()
watch(navigation, (target) => {
  // Only navigations the agent performs while answering are followed; results
  // of confirmed actions (e.g. "open game") stay a button the owner chooses.
  if (!target || !streaming.value || !autoNavigate.value) return
  const hash = navigationHash(target)
  if (hash === currentHash.value) return
  autoFollowed.value = { hash, from: currentHash.value }
  window.location.hash = hash
})
type ToolEvent = { event: string; payload: Record<string, unknown> }
function messageActions(message: { events?: ToolEvent[] }): ToolEvent[] {
  return (message.events || []).filter(event => event.event === 'tool_result')
}
function actionNavigation(event: ToolEvent): { route: string; query?: Record<string, string> } | null {
  const result = event.payload.result as Record<string, unknown> | undefined
  const raw = (result && typeof result === 'object' ? result.navigation : null) as Record<string, unknown> | null
  const route = typeof raw?.route === 'string' ? raw.route.replace(/^\//, '') : ''
  if (!route || !workspaceCopy.value.routes[route]) return null
  const query: Record<string, string> = {}
  if (raw?.query && typeof raw.query === 'object') for (const [key, value] of Object.entries(raw.query as Record<string, unknown>)) if (typeof value === 'string') query[key] = value
  return Object.keys(query).length ? { route, query } : { route }
}
function actionLabel(event: ToolEvent): string {
  const target = event.payload.tool === 'navigate' ? actionNavigation(event) : null
  if (target && event.payload.ok !== false) return workspaceCopy.value.opened(workspaceCopy.value.routes[target.route])
  const label = toolLabel(event.payload.tool)
  if (event.payload.ok === false) return `${label} · ${agentCopy.value.failure}`
  return event.payload.partial ? `${label} · ${agentCopy.value.partial}` : label
}
function goTo(target: { route: string; query?: Record<string, string> }) {
  window.location.hash = navigationHash(target)
}
function goBack() {
  const from = autoFollowed.value?.from
  if (from === undefined) return
  autoFollowed.value = null
  window.location.hash = from || '#/overview'
}
function navigationLabel(route: string): string {
  if (route === 'play' && !navigationIsCurrent.value) return agentCopy.value.openGame
  const page = workspaceCopy.value.routes[route]
  if (!page) return toolLabel('navigate')
  return navigationIsCurrent.value ? workspaceCopy.value.alreadyHere(page) : workspaceCopy.value.goTo(page)
}
function resultAssetId(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return ''
  const record = value as Record<string, unknown>
  const reference = record.reference && typeof record.reference === 'object' ? record.reference as Record<string, unknown> : {}
  return reference.kind === 'generated' && typeof reference.asset_id === 'string'
    ? reference.asset_id
    : typeof record.asset_id === 'string' ? record.asset_id : ''
}
async function loadResultImage(value: unknown) {
  const assetId = resultAssetId(value)
  if (!assetId || imageLoads.has(assetId) || imageUrls.value[assetId]) return
  imageLoads.add(assetId)
  try { imageUrls.value[assetId] = await generatedImageUrl(assetId) } catch { /* Keep the result text usable when the image expires. */ }
}
watch(eventList, events => {
  for (const event of events) if (event.event === 'tool_result') void loadResultImage(event.payload.result)
}, { deep: true, immediate: true })
onBeforeUnmount(() => { for (const url of Object.values(imageUrls.value)) URL.revokeObjectURL(url) })

function eventResult(value: unknown, detailed = false): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return detailed ? value : value.slice(0, 180)
  try {
    const text = JSON.stringify(value)
    return detailed ? text : text.slice(0, 240)
  } catch { return detailed ? String(value) : String(value).slice(0, 180) }
}

const approvalTarget = computed(() => {
  const preview = pendingApproval.value?.preview
  if (!preview || typeof preview !== 'object' || Array.isArray(preview)) return ''
  const record = preview as Record<string, unknown>
  return typeof record.target_name === 'string' ? record.target_name : ''
})

function followNavigation() {
  const target = navigation.value
  if (!target || navigationIsCurrent.value) return
  window.location.hash = navigationHash(target)
}
function continueBudgetedTask() { void send(locale.value.startsWith('zh') ? '继续未完成的任务' : 'Continue the unfinished task', locale.value) }
function toggleTaskWorkspace() {
  if (workspaceForcedOpen.value) {
    workspaceForcedOpen.value = false
    // On desktop the persisted workspace is already open underneath the
    // temporary focus override, so the first click should still collapse it.
    if (!workspaceCollapsed.value) toggleWorkspace()
  } else toggleWorkspace()
}
</script>

<template>
  <section class="assistant-panel" :class="{ 'assistant-panel-expanded': props.expanded || props.wide }" aria-labelledby="assistant-panel-title">
    <header class="assistant-panel-head">
      <span class="assistant-brand" aria-hidden="true"><AssistantAvatar :mood="headMood" :hands="false" /></span>
      <div class="assistant-head-copy">
        <strong id="assistant-panel-title">{{ t('assistantTitle') }}</strong>
        <span>{{ t('assistantSubtitle') }}</span>
      </div>
      <div class="assistant-head-actions">
      <NButton
        size="small"
        quaternary
        circle
        data-testid="assistant-history-toggle"
        :title="conversationCopy.history"
        :aria-label="conversationCopy.history"
        :aria-pressed="historyOpen"
        :type="historyOpen ? 'primary' : 'default'"
        @click="historyOpen = !historyOpen"
      >
        <template #icon><NIcon :component="ChatbubblesOutline" /></template>
      </NButton>
      <NButton size="small" quaternary circle data-testid="assistant-new-conversation" :title="conversationCopy.newConversation" :aria-label="conversationCopy.newConversation" :disabled="conversationBusy || !messages.length" @click="newConversation">
        <template #icon><NIcon :component="AddOutline" /></template>
      </NButton>
      <NButton
        size="small"
        quaternary
        circle
        data-testid="assistant-voice-reply"
        :title="voiceReply ? voiceCopy.speakOff : voiceCopy.speakOn"
        :aria-label="voiceReply ? voiceCopy.speakOff : voiceCopy.speakOn"
        :aria-pressed="voiceReply"
        :type="voiceReply ? 'primary' : 'default'"
        @click="toggleVoiceReply"
      >
        <template #icon><NIcon :component="voiceReply ? VolumeHighOutline : VolumeMuteOutline" /></template>
      </NButton>
      <NButton size="small" quaternary circle data-testid="assistant-toggle-expanded" :title="props.expanded ? agentCopy.shrinkWindow : agentCopy.expandWindow" :aria-label="props.expanded ? agentCopy.shrinkWindow : agentCopy.expandWindow" :aria-expanded="props.expanded" @click="emit('toggleExpanded')">
        <template #icon><NIcon :component="props.expanded ? ContractOutline : ExpandOutline" /></template>
      </NButton>
      <NButton size="small" quaternary circle :title="t('close')" @click="emit('close')">
        <template #icon><NIcon :component="CloseOutline" /></template>
      </NButton>
      </div>
    </header>
    <AgentAuthorizationPanel
      :locale="locale"
      :authorization="authorization"
      :task="task"
      :pending-approval="Boolean(pendingApproval)"
      :busy="authorizationBusy || streaming || actionBusy"
      :error="authorizationError"
      :context="agentContext"
      :on-authorize="authorize"
      :on-continue="continueBudgetedTask"
      :on-review="focusPendingApproval"
    />

    <div class="assistant-body" :class="{ 'assistant-body-history': historyOpen }">
    <aside v-if="historyOpen" class="assistant-history" data-testid="assistant-history" :aria-label="conversationCopy.history">
      <button type="button" class="assistant-history-new" :disabled="conversationBusy || !messages.length" @click="newConversation">
        <NIcon :component="AddOutline" /> {{ conversationCopy.newConversation }}
      </button>
      <p v-if="!conversationList.length" class="assistant-history-empty">{{ conversationCopy.empty }}</p>
      <ul>
        <li
          v-for="item in conversationList"
          :key="item.id"
          class="assistant-history-item"
          :class="{ active: item.id === activeConversationId }"
        >
          <input
            v-if="renamingId === item.id"
            v-model="renameDraft"
            class="assistant-history-rename"
            :aria-label="conversationCopy.rename"
            maxlength="60"
            @keydown.enter.prevent="finishRename"
            @keydown.esc.prevent="renamingId = ''"
            @blur="finishRename"
          >
          <button
            v-else
            type="button"
            class="assistant-history-open"
            :disabled="conversationBusy && item.id !== activeConversationId"
            :aria-current="item.id === activeConversationId ? 'true' : undefined"
            @click="openConversation(item.id)"
          >
            <strong>{{ item.id === activeConversationId && !item.title ? conversationCopy.current : conversationLabel(item.title) }}</strong>
            <small>{{ conversationTime(item.updatedAt) }}</small>
          </button>
          <button type="button" class="assistant-history-tool" :title="conversationCopy.rename" :aria-label="conversationCopy.rename" @click="startRename(item.id, item.title)">
            <NIcon :component="CreateOutline" />
          </button>
          <button type="button" class="assistant-history-tool danger" :title="conversationCopy.remove" :aria-label="conversationCopy.remove" :disabled="conversationBusy && item.id === activeConversationId" @click="deleteConversation(item.id)">
            <NIcon :component="TrashOutline" />
          </button>
        </li>
      </ul>
    </aside>

    <div class="assistant-messages" aria-live="polite">
      <div ref="transcriptEl" class="assistant-transcript">
        <div v-if="!messages.length" class="assistant-empty">
          <span class="assistant-empty-avatar" aria-hidden="true"><AssistantAvatar mood="happy" /></span>
          <strong>{{ t('assistantEmpty') }}</strong>
          <p>{{ t('assistantEmptyHint') }}</p>
          <div class="assistant-quick-grid">
            <button v-for="question in quickQuestions()" :key="question.id" type="button" :data-assistant-intent="question.id" @click="ask(question.prompt)">
              {{ question.prompt }}
            </button>
          </div>
          <small>{{ t('assistantVersionHint') }}</small>
        </div>

        <article
          v-for="(message, index) in messages"
          :key="index"
          class="assistant-message"
          :class="`assistant-message-${message.role}`"
        >
          <span v-if="message.role === 'assistant'" class="assistant-message-avatar" aria-hidden="true">
            <AssistantAvatar :track="false" :hands="false" :mood="index === messages.length - 1 && streaming ? 'thinking' : 'idle'" />
          </span>
          <div class="assistant-message-stack">
            <div
              v-if="message.content || ((streaming || pendingApproval) && message.role === 'assistant' && index === messages.length - 1)"
              class="assistant-bubble"
              :class="message.role"
            >
              <span v-if="pendingApproval && index === messages.length - 1 && !message.content">{{ pendingApproval.resumable ? assistantAuthorizationCopy(locale).resumeHint : agentCopy.statusApproval }}</span>
              <span
                v-else-if="streaming && index === messages.length - 1 && !message.content"
                class="assistant-thinking"
              >
                <i /><i /><i /><span class="sr-only">{{ t('assistantThinking') }}</span>
              </span>
              <div v-else-if="message.role === 'assistant'" class="safe-markdown" v-html="renderMd(message.content)" />
              <span v-else>{{ message.content }}</span>
            </div>

            <details v-if="message.sources?.length" class="assistant-sources">
              <summary><NIcon :component="DocumentTextOutline" />{{ t('assistantSources') }}</summary>
              <ul>
                <li v-for="source in message.sources" :key="`${source.source}:${source.heading}`">
                  <span>{{ source.heading }}</span>
                </li>
              </ul>
            </details>

            <ul v-if="messageActions(message).length" class="assistant-actions" data-testid="assistant-actions">
              <li
                v-for="(event, actionIndex) in messageActions(message)"
                :key="actionIndex"
                class="assistant-action"
                :class="{ failed: event.payload.ok === false }"
              >
                <span class="assistant-action-mark" aria-hidden="true">{{ event.payload.ok === false ? '✕' : '✓' }}</span>
                <span class="assistant-action-label">{{ actionLabel(event) }}</span>
                <template v-if="actionNavigation(event)">
                  <button
                    v-if="autoFollowed && navigationHash(actionNavigation(event)!) === currentHash && autoFollowed.hash === currentHash"
                    type="button"
                    class="assistant-action-link"
                    data-testid="assistant-navigation-back"
                    @click="goBack"
                  >{{ workspaceCopy.back }}</button>
                  <button
                    v-else-if="navigationHash(actionNavigation(event)!) !== currentHash"
                    type="button"
                    class="assistant-action-link"
                    @click="goTo(actionNavigation(event)!)"
                  >{{ workspaceCopy.openAgain }}</button>
                </template>
                <img
                  v-if="resultAssetId(event.payload.result) && imageUrls[resultAssetId(event.payload.result)]"
                  :src="imageUrls[resultAssetId(event.payload.result)]"
                  alt="Generated result"
                  class="assistant-result-image"
                >
                <AgentExecutionSummary
                  v-if="event.payload.tool !== 'navigate'"
                  :result="event.payload.result"
                  :locale="locale"
                />
                <details v-if="event.payload.tool !== 'navigate'" class="assistant-action-details">
                  <summary>{{ workspaceCopy.details }}</summary>
                  <pre>{{ eventResult(event.payload.result, true) }}</pre>
                </details>
              </li>
            </ul>

            <div v-if="message.error || message.stopped" class="assistant-inline-state">
              <span>{{ message.error || t('assistantStopped') }}</span>
              <NButton
                v-if="index === messages.length - 1"
                size="tiny"
                quaternary
                @click="retryLast(locale)"
              >
                <template #icon><NIcon :component="RefreshOutline" /></template>
                {{ t('assistantRetry') }}
              </NButton>
            </div>
          </div>
        </article>
      </div>

      <section v-if="workspaceVisible" class="assistant-tasks" :class="{ 'assistant-workspace-collapsed': workspaceIsCollapsed }">
        <button type="button" class="assistant-workspace-toggle" data-testid="assistant-workspace-toggle" :aria-expanded="!workspaceIsCollapsed" :aria-controls="workspaceId" :aria-label="workspaceIsCollapsed ? workspaceCopy.expand : workspaceCopy.collapse" @click="toggleTaskWorkspace">
          <strong>{{ agentCopy.workspaceTitle }}</strong>
          <span class="assistant-workspace-state" :class="{ pending: pendingApproval || actionError }">{{ workspaceStatus }}</span>
          <span v-if="pendingApproval" class="assistant-workspace-badge" :title="assistantAuthorizationCopy(locale).reviewPending">1</span>
          <span aria-hidden="true">{{ workspaceIsCollapsed ? '⌃' : '⌄' }}</span>
        </button>
        <div :id="workspaceId" ref="tasksEl" v-show="!workspaceIsCollapsed" class="assistant-workspace-body">
          <p v-if="!hasTasks" class="assistant-tasks-empty">{{ agentCopy.workspaceHint }}</p>
          <div v-if="pendingApproval || (streaming && status)" class="assistant-status" role="status">{{ agentCopy.status }}: {{ pendingApproval ? (pendingApproval.resumable ? assistantAuthorizationCopy(locale).taskStatus(pendingApproval.execution_status || 'paused') : agentCopy.statusApproval) : statusText(status) }}</div>

          <AgentTaskCard v-for="task in draftList" :key="task.draft_id" :draft="task" :busy="actionBusy" :streaming="streaming" @edit="invalidateApproval" @review="reviewDraft($event.draft_id, $event.draft)" @save="saveDraftLocalized" />

          <div v-if="actionError" class="assistant-inline-state" role="alert">{{ actionError }}</div>

          <button
            v-if="pendingNavigation && navigation"
            type="button"
            class="assistant-navigation"
            data-testid="assistant-navigation"
            :disabled="navigationIsCurrent"
            @click="followNavigation"
          >{{ navigationLabel(navigation.route) }}</button>

          <article v-if="pendingApproval" ref="approvalEl" class="assistant-approval" data-agent-approval>
            <strong>{{ agentCopy.pending }}</strong>
            <span>{{ toolLabel(pendingApproval.tool) }}</span>
            <strong v-if="approvalTarget">{{ agentCopy.target }}: {{ approvalTarget }}</strong>
            <span v-if="pendingApproval.expired || pendingApproval.recovery_required" class="assistant-approval-invalid">{{ agentCopy.approvalInvalid }}</span>
            <AgentExecutionSummary :approval="pendingApproval" :locale="locale" />
            <details v-if="pendingApproval.preview || pendingApproval.arguments">
              <summary>{{ agentCopy.details }}</summary>
              <pre>{{ eventResult(pendingApproval.preview || pendingApproval.arguments, true) }}</pre>
            </details>
            <div class="assistant-approval-actions">
              <NButton v-if="!pendingApproval.resumable" size="small" type="primary" :loading="actionBusy" :disabled="actionBusy || pendingApproval.expired || pendingApproval.recovery_required" @click="confirmApproval(true)">{{ agentCopy.approve }}</NButton>
              <NButton v-else size="small" type="primary" data-testid="agent-resume" :loading="actionBusy" :disabled="actionBusy || pendingApproval.expired || pendingApproval.recovery_required" @click="resumeApproval">{{ assistantAuthorizationCopy(locale).resumeTargets }}</NButton>
              <NButton size="small" quaternary :loading="actionBusy" :disabled="actionBusy" @click="cancelApproval">{{ agentCopy.cancel }}</NButton>
            </div>
          </article>
        </div>
      </section>
    </div>
    </div>

    <footer class="assistant-composer" :class="{ 'assistant-composer-voice': voiceInputAvailable }">
      <NInput
        v-model:value="draft"
        type="textarea"
        :placeholder="transcribing ? voiceCopy.transcribing : listening ? (interim || voiceCopy.listening) : t('assistantPlaceholder')"
        :autosize="{ minRows: 1, maxRows: 5 }"
        @keydown="onKeydown"
      />
      <button
        v-if="voiceInputAvailable"
        type="button"
        class="assistant-mic"
        :class="{ active: listening }"
        data-testid="assistant-mic"
        :title="listening ? voiceCopy.stopListening : voiceCopy.listen"
        :aria-label="listening ? voiceCopy.stopListening : voiceCopy.listen"
        :aria-pressed="listening"
        :disabled="transcribing || (streaming && !listening)"
        @click="toggleListening"
      >
        <NIcon :component="listening ? StopCircleOutline : MicOutline" size="18" />
      </button>
      <NButton v-if="streaming" type="warning" class="assistant-send" @click="stop">
        <template #icon><NIcon :component="StopCircleOutline" /></template>
        {{ t('assistantStop') }}
      </NButton>
      <NButton v-else type="primary" class="assistant-send" :disabled="!draft.trim()" @click="onSubmit">
        <template #icon><NIcon :component="SendOutline" /></template>
        {{ t('assistantSend') }}
      </NButton>
      <span v-if="voiceErrorText" class="assistant-composer-hint assistant-voice-error" role="alert">{{ voiceErrorText }}</span>
      <span v-else-if="speaking" class="assistant-composer-hint"><button type="button" class="assistant-stop-speaking" @click="voice.stopSpeaking()">{{ voiceCopy.stopSpeaking }}</button></span>
      <span v-else class="assistant-composer-hint">{{ pendingApproval ? `${pendingApproval.resumable ? assistantAuthorizationCopy(locale).taskStatus(pendingApproval.execution_status || 'paused') : agentCopy.statusApproval} · ${toolLabel(pendingApproval.tool)}` : t('assistantInputHint') }}</span>
    </footer>
  </section>
</template>

<style scoped>
.assistant-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: var(--df-text);
  background:
    radial-gradient(circle at 100% 0, color-mix(in srgb, var(--df-interactive) 12%, transparent), transparent 26%),
    var(--df-surface-1);
}

.assistant-panel-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 14px 15px;
  border-bottom: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-raised) 88%, transparent);
  backdrop-filter: blur(16px);
}

.assistant-head-actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 2px;
}

.assistant-empty-avatar {
  display: block;
  width: 64px;
  height: 64px;
  margin: 0 auto 6px;
}

.assistant-body {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}

.assistant-body > .assistant-messages { flex: 1 1 auto; min-width: 0; }

.assistant-history {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 232px;
  flex: none;
  padding: 12px 10px;
  overflow-y: auto;
  border-right: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-2) 70%, var(--df-surface-1));
}

.assistant-history ul { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }

.assistant-history-new {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 34px;
  border: 1px dashed color-mix(in srgb, var(--df-interactive) 55%, var(--df-border-soft));
  border-radius: var(--df-radius-sm);
  color: var(--df-interactive-strong);
  background: transparent;
  cursor: pointer;
}
.assistant-history-new:disabled { opacity: 0.5; cursor: not-allowed; }
.assistant-history-empty { margin: 8px 4px; color: var(--df-text-muted); font-size: 12px; }

.assistant-history-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border: 1px solid transparent;
  border-radius: var(--df-radius-sm);
}
.assistant-history-item.active {
  border-color: color-mix(in srgb, var(--df-interactive) 50%, var(--df-border-soft));
  background: color-mix(in srgb, var(--df-interactive) 12%, transparent);
}
.assistant-history-open {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  justify-content: stretch;
  justify-items: start;
  gap: 2px;
  width: 100%;
  min-width: 0;
  padding: 6px 8px;
  border: 0;
  color: inherit;
  background: none;
  text-align: left;
  cursor: pointer;
}
.assistant-history-open strong { max-width: 100%; overflow: hidden; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.assistant-history-open small { color: var(--df-text-muted); font-size: 11px; }
.assistant-history-open:disabled { opacity: 0.55; cursor: not-allowed; }
.assistant-history-rename {
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid var(--df-interactive);
  border-radius: var(--df-radius-sm);
  color: inherit;
  background: var(--df-control-bg);
  font: inherit;
  font-size: 13px;
}
.assistant-history-tool {
  display: inline-grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 6px;
  color: var(--df-text-muted);
  background: none;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, color 0.15s ease;
}
.assistant-history-item:hover .assistant-history-tool,
.assistant-history-item.active .assistant-history-tool,
.assistant-history-tool:focus-visible { opacity: 1; }
.assistant-history-tool:hover { color: var(--df-interactive-strong); }
.assistant-history-tool.danger:hover { color: var(--df-danger, #e57373); }
.assistant-history-tool:disabled { opacity: 0.3; cursor: not-allowed; }

/* In the side drawer the list floats over the transcript. */
.assistant-panel:not(.assistant-panel-expanded) .assistant-history {
  position: absolute;
  inset: 0 auto 0 0;
  z-index: 3;
  width: min(280px, 82%);
  box-shadow: 12px 0 28px rgb(0 0 0 / 0.32);
  backdrop-filter: blur(14px);
}


.assistant-brand {
  display: block;
  flex: none;
  width: 38px;
  height: 38px;
}

.assistant-head-copy {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.assistant-head-copy strong {
  color: var(--df-accent-strong);
  font-size: 15px;
  /* 长会话标题与创作工坊侧一致：省略，不撑爆头部。 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-head-copy span {
  overflow: hidden;
  color: var(--df-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-messages {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
  flex-direction: column;
  gap: 0;
}

.assistant-transcript {
  display: flex;
  flex: 1 1 0;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  padding: 18px 15px;
  scroll-behavior: smooth;
}

.assistant-tasks {
  display: flex;
  flex: 0 1 auto;
  flex-direction: column;
  min-height: 0;
  max-height: 45%;
  overflow: hidden;
  border-top: 1px solid var(--df-border-soft);
  background: var(--df-surface-1);
}

.assistant-workspace-collapsed { flex: 0 0 auto; }
.assistant-workspace-toggle { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; width: 100%; min-height: 44px; padding: 9px 15px; border: 0; border-radius: 0; background: var(--df-surface-2); color: var(--df-accent-strong); text-align: start; }
.assistant-workspace-toggle:hover { transform: none; }
.assistant-workspace-toggle strong { margin-right: auto; font-size: 13px; }
.assistant-workspace-state { color: var(--df-text-muted); font-size: 11px; }
.assistant-workspace-state.pending { color: var(--df-accent-strong); }
.assistant-workspace-badge { display: inline-grid; place-items: center; min-width: 17px; height: 17px; padding: 0 4px; border-radius: 999px; color: var(--df-on-accent); background: var(--df-danger-strong, #c94b55); font-size: 10px; font-weight: 800; }
.assistant-workspace-body { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 12px 15px; }

.assistant-tasks-empty {
  margin: 0;
  color: var(--df-text-muted);
  font-size: 13px;
  line-height: 1.7;
}

@media (min-width: 801px) {
  .assistant-panel-expanded .assistant-messages {
    display: grid;
    grid-template-columns: minmax(0, 7fr) minmax(280px, 3fr);
    grid-template-rows: minmax(0, 1fr);
    gap: 0;
    overflow: hidden;
    padding: 0;
  }

  .assistant-panel-expanded .assistant-tasks {
    max-height: none;
    border-top: 0;
    border-left: 1px solid var(--df-border-soft);
    background: color-mix(in srgb, var(--df-surface-2) 45%, var(--df-surface-1));
  }
  .assistant-panel-expanded .assistant-workspace-body { flex: 1 1 0; }
}

.assistant-empty {
  display: grid;
  width: min(100%, 360px);
  margin: auto;
  justify-items: center;
  gap: 9px;
  padding: 24px 6px;
  text-align: center;
}

.assistant-empty strong {
  color: var(--df-accent-strong);
  font-size: 17px;
}

.assistant-empty p,
.assistant-empty small {
  margin: 0;
  color: var(--df-text-muted);
  line-height: 1.6;
}

.assistant-quick-grid {
  display: grid;
  width: 100%;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 8px 0 2px;
}

.assistant-quick-grid button {
  min-height: 54px;
  padding: 9px 10px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-md);
  color: var(--df-text-secondary);
  background: color-mix(in srgb, var(--df-control-bg) 82%, transparent);
  font-size: 12px;
  line-height: 1.4;
  text-align: left;
}

.assistant-quick-grid button:last-child {
  grid-column: 1 / -1;
}

.assistant-message {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.assistant-message-user {
  justify-content: flex-end;
}

.assistant-message-avatar {
  display: block;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  margin-top: 2px;
}

.assistant-message-stack {
  display: grid;
  /* Keep wide content (code blocks, tables) inside the bubble instead of
     letting it size the column past the drawer. */
  grid-template-columns: minmax(0, 1fr);
  max-width: 88%;
  min-width: 0;
  gap: 6px;
}

.assistant-bubble {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--df-border-soft);
  border-radius: 14px;
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.assistant-bubble.user {
  border-bottom-right-radius: 5px;
  border-color: color-mix(in srgb, var(--df-interactive) 50%, var(--df-border-soft));
  background: color-mix(in srgb, var(--df-interactive) 15%, var(--df-surface-raised));
  white-space: pre-wrap;
}

.assistant-bubble.assistant {
  border-top-left-radius: 5px;
  background: linear-gradient(155deg, var(--df-surface-raised), var(--df-surface-2));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .04);
}

.assistant-thinking {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 20px;
}

.assistant-thinking i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--df-interactive-strong);
  animation: assistant-pulse 1s ease-in-out infinite;
}

.assistant-thinking i:nth-child(2) { animation-delay: .14s; }
.assistant-thinking i:nth-child(3) { animation-delay: .28s; }

@keyframes assistant-pulse {
  0%, 70%, 100% { opacity: .25; transform: translateY(0); }
  35% { opacity: 1; transform: translateY(-3px); }
}

.assistant-sources {
  color: var(--df-text-muted);
  font-size: 11px;
}

.assistant-sources summary {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

.assistant-sources ul {
  display: grid;
  gap: 5px;
  margin: 7px 0 0;
  padding: 8px 10px 8px 24px;
  border-left: 1px solid var(--df-border-soft);
}

.assistant-sources li span {
  display: block;
}

.assistant-inline-state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 9px;
  border: 1px solid color-mix(in srgb, var(--df-danger) 38%, var(--df-border-soft));
  border-radius: var(--df-radius-sm);
  color: var(--df-danger-strong);
  background: color-mix(in srgb, var(--df-danger) 8%, transparent);
  font-size: 11px;
}

.assistant-status,
.assistant-approval,
.assistant-tool-results {
  padding: 9px 10px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-sm);
  color: var(--df-text-secondary);
  background: color-mix(in srgb, var(--df-control-bg) 84%, transparent);
  font-size: 11px;
}

.assistant-approval { display: grid; gap: 7px; }
.assistant-approval > span { color: var(--df-text-muted); }
.assistant-approval-actions { display: flex; gap: 7px; }
.assistant-tool-result { display: grid; gap: 5px; padding-top: 7px; overflow-wrap: anywhere; }
.assistant-result-success { color: var(--df-success-strong); }
.assistant-result-failure { color: var(--df-danger-strong); }
.assistant-result-image { display: block; max-width: 100%; max-height: 220px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); object-fit: contain; }
.assistant-tool-result pre,
.assistant-approval pre { max-height: 12rem; overflow: auto; white-space: pre-wrap; }
.assistant-navigation:disabled { opacity: 0.6; cursor: default; }

.assistant-actions { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
.assistant-action {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 4px 9px;
  border: 1px solid color-mix(in srgb, var(--df-success, #6fbf8a) 35%, var(--df-border-soft));
  border-radius: 999px;
  color: var(--df-text-muted);
  background: color-mix(in srgb, var(--df-success, #6fbf8a) 8%, transparent);
  font-size: 12px;
  width: fit-content;
  max-width: 100%;
}
.assistant-action.failed {
  border-color: color-mix(in srgb, var(--df-danger, #e57373) 45%, var(--df-border-soft));
  background: color-mix(in srgb, var(--df-danger, #e57373) 8%, transparent);
}
.assistant-action:has(.assistant-result-image, .assistant-action-details[open]) { border-radius: 12px; width: 100%; }
.assistant-action-mark { color: var(--df-success, #6fbf8a); font-weight: 700; }
.assistant-action.failed .assistant-action-mark { color: var(--df-danger, #e57373); }
.assistant-action-label { color: var(--df-text); }
.assistant-action-link {
  padding: 0;
  border: 0;
  color: var(--df-interactive-strong);
  background: none;
  font: inherit;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.assistant-action-details summary { color: var(--df-text-muted); cursor: pointer; }
.assistant-action-details[open] { flex-basis: 100%; }
.assistant-action-details pre { max-height: 10rem; margin: 6px 0 2px; overflow: auto; white-space: pre-wrap; font-size: 11px; }
.assistant-action .assistant-result-image { flex-basis: 100%; }
.assistant-navigation { align-self: flex-start; padding: 7px 10px; border: 1px solid var(--df-interactive); border-radius: var(--df-radius-sm); color: var(--df-on-accent); background: var(--df-interactive); cursor: pointer; }

.assistant-composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: end;
  padding: 12px 14px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-raised) 94%, transparent);
  box-shadow: 0 -12px 28px color-mix(in srgb, #000 10%, transparent);
}

.assistant-send {
  min-height: 36px;
}

.assistant-composer-voice {
  grid-template-columns: minmax(0, 1fr) auto auto;
}

.assistant-mic {
  display: inline-grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--df-border-soft);
  border-radius: 50%;
  color: var(--df-text-muted);
  background: color-mix(in srgb, var(--df-surface-2) 80%, transparent);
  cursor: pointer;
  transition: color 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}

.assistant-mic:hover:not(:disabled) { color: var(--df-interactive-strong); border-color: var(--df-interactive); }
.assistant-mic:disabled { opacity: 0.45; cursor: not-allowed; }
.assistant-mic.active {
  color: var(--df-on-accent);
  border-color: var(--df-interactive);
  background: var(--df-interactive);
  animation: assistant-mic-pulse 1.6s ease-out infinite;
}

@keyframes assistant-mic-pulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--df-interactive) 55%, transparent); }
  100% { box-shadow: 0 0 0 10px transparent; }
}

.assistant-voice-error { color: var(--df-danger, #e57373); }
.assistant-stop-speaking {
  padding: 0;
  border: 0;
  color: var(--df-interactive-strong);
  background: none;
  font: inherit;
  cursor: pointer;
  text-decoration: underline;
}

.assistant-composer-hint {
  grid-column: 1 / -1;
  color: var(--df-text-muted);
  font-size: 10px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

@media (max-width: 420px) {
  .assistant-quick-grid { grid-template-columns: 1fr; }
  .assistant-message-stack { max-width: 92%; }
}
</style>
