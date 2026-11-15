<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { NButton, NIcon, NInput } from 'naive-ui'
import {
  AddOutline, ChatbubblesOutline, CloseOutline, ContractOutline, CreateOutline, DocumentTextOutline, ExpandOutline, MicOutline,
  RefreshOutline, SendOutline, StopCircleOutline, TrashOutline, VolumeHighOutline, VolumeMuteOutline,
} from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import { useCreatorAssistant } from '@/composables/useCreatorAssistant'
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
import type { AssistantDraft, AssistantEventRecord } from '@/composables/useAssistant'
import { generatedImageUrl } from '@/api/generatedImages'
import { creatorApi, type CreatorProject } from '@/api/creator'
import SubmissionWizard from './SubmissionWizard.vue'

// `expanded`: the window is maximized; `wide`: the window is wide enough for the side-by-side layout.
const props = withDefaults(defineProps<{ expanded?: boolean; wide?: boolean; reviewRequest?: number; projectId?: string; projectName?: string; projectRevision?: number; projectKind?: string; initialPrompt?: string }>(), { expanded: false, wide: false, reviewRequest: 0 })
const emit = defineEmits<{ close: []; toggleExpanded: []; 'files-changed': [paths: string[]] }>()
const { t, locale } = useLocale()
const _projectScope = computed(() => props.projectId)
const { messages, streaming, send: _rawSend, stop, retryLast, pendingApproval, drafts, status, navigation, actionBusy, actionError, confirmApproval, resumeApproval, cancelApproval, reviewDraft, saveDraft, invalidateApproval, authorization, task, authorizationBusy, authorizationError, authorize } = useCreatorAssistant(_projectScope)

// Live revision — starts from prop, updated whenever AI edits the project
const liveRevision = ref(props.projectRevision ?? 0)
watch(() => props.projectRevision, v => { if (v !== undefined && v > liveRevision.value) liveRevision.value = v })

// Wrap send to inject project context
async function send(text: string, loc: string) {
  const contextNote = props.projectId
    ? `（当前工程：${props.projectName || props.projectId}，工程 id ${props.projectId}，当前 revision ${liveRevision.value}。这是执行型请求：先用一两句话说明你的计划，再用 creator_project_read 读取工程；需要改变内容时调用 creator_project_update，携带 expected_revision=${liveRevision.value}；当前工程类型是 ${props.projectKind || 'adventure'}，只修改与该类型对应的源文件。每次操作完成后用文字告知用户结果。）`
    : ''
  await _rawSend(text, loc, contextNote)
}
// ── File panel ───────────────────────────────────────────────
const filesOpen = ref(false)
const projectData = ref<CreatorProject | null>(null)
const selectedFile = ref('')
const selectedContent = ref('')
const advancedEdit = ref(false)
const fileSearch = ref('')
const editorGroup = ref<'overview' | 'content' | 'rules' | 'docs' | 'all'>('overview')
const EDITOR_GROUPS = [
  { id: 'overview' as const, test: (p: string) => ['plugin.json', 'config.schema.json', 'README.md', 'manifest.json'].includes(p) },
  { id: 'content' as const, test: (p: string) => p.startsWith('content/') || p.startsWith('adventures/') },
  { id: 'rules' as const, test: (p: string) => p.startsWith('rules/') || p.startsWith('content/rules/') },
  { id: 'docs' as const, test: (p: string) => p.startsWith('docs/') },
]
const EDITOR_GROUP_KEYS = {
  overview: 'creatorEditorGroupOverview', content: 'creatorEditorGroupContent',
  rules: 'creatorEditorGroupRules', docs: 'creatorEditorGroupDocs', all: 'creatorEditorGroupAll',
} as const
const projectFiles = computed(() => Object.keys(projectData.value?.files || {}).sort())
const groupFiles = computed(() => {
  if (editorGroup.value === 'all') return projectFiles.value
  const group = EDITOR_GROUPS.find(g => g.id === editorGroup.value)
  return projectFiles.value.filter(p => group?.test(p))
})
const filteredFiles = computed(() => {
  const needle = fileSearch.value.trim().toLowerCase()
  return needle ? groupFiles.value.filter(p => p.toLowerCase().includes(needle)) : groupFiles.value
})
watch(editorGroup, () => { fileSearch.value = '' })

async function loadProjectFiles() {
  if (!props.projectId || projectData.value?.id === props.projectId) return
  try {
    const r = await creatorApi.read(props.projectId)
    projectData.value = r.project
    if (r.project.revision > liveRevision.value) liveRevision.value = r.project.revision
  } catch { /* silent */ }
}

function selectFile(path: string) {
  if (!projectData.value) return
  selectedFile.value = path
  selectedContent.value = projectData.value.files[path] || ''
}

async function saveFile() {
  if (!projectData.value || !selectedFile.value) return
  try {
    const r = await creatorApi.update(projectData.value.id, {
      expected_revision: projectData.value.revision,
      files: { [selectedFile.value]: selectedContent.value },
    })
    projectData.value = r.project
  } catch { /* silent */ }
}

async function deleteFile(path: string) {
  if (!projectData.value) return
  if (!window.confirm(locale.value === 'zh-CN' ? `删除 ${path}？` : `Delete ${path}?`)) return
  try {
    const r = await creatorApi.update(projectData.value.id, { expected_revision: projectData.value.revision, remove: [path] })
    projectData.value = r.project
    if (selectedFile.value === path) { selectedFile.value = ''; selectedContent.value = '' }
  } catch { /* silent */ }
}

watch(filesOpen, open => { if (open) void loadProjectFiles() })

// ── Publish panel ─────────────────────────────────────────────
const publishOpen = ref(false)
const publishBtnEl = ref<HTMLElement | null>(null)
const publishPopoverStyle = ref<Record<string, string>>({})

function updatePublishPopoverPosition() {
  if (!publishBtnEl.value) {
    // fallback to top-right corner
    publishPopoverStyle.value = { top: '56px', right: '16px' }
    return
  }
  const rect = publishBtnEl.value.getBoundingClientRect()
  publishPopoverStyle.value = {
    top: `${rect.bottom + 8}px`,
    right: `${window.innerWidth - rect.right}px`,
  }
}

function openPublishPopover() {
  updatePublishPopoverPosition()
  publishOpen.value = true
}
type PublishStep = 'idle' | 'running' | 'ok' | 'fail'
const publishValidate = ref<PublishStep>('idle')
const publishBuild = ref<PublishStep>('idle')
const publishSubmit = ref<PublishStep>('idle')
const publishErrors = ref<string[]>([])
const builtFileName = ref('')
const submissionPluginId = ref('')
const submissionVersion = ref('')

async function runPublish() {
  if (!props.projectId) return
  openPublishPopover()
  publishValidate.value = 'running'; publishBuild.value = 'idle'; publishSubmit.value = 'idle'; publishErrors.value = []
  try {
    const r = await creatorApi.validate(props.projectId)
    if (r.ok) publishValidate.value = 'ok'
    else { publishValidate.value = 'fail'; publishErrors.value = r.errors || []; return }
  } catch (e) { publishValidate.value = 'fail'; publishErrors.value = [String(e)]; return }
  publishBuild.value = 'running'
  const rev = projectData.value?.revision ?? props.projectRevision ?? 0
  try {
    const response = await creatorApi.build(props.projectId, rev)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const filename = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || props.projectId + '.dfplugin'
    builtFileName.value = filename
    a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url)
    publishBuild.value = 'ok'
  } catch (e) { publishBuild.value = 'fail'; publishErrors.value = [String(e)]; return }
  publishSubmit.value = 'running'
  try {
    const r = await creatorApi.submission(props.projectId, rev)
    if (r.ok) {
      const sub = r.submission as Record<string, unknown> | null
      submissionPluginId.value = String(sub?.plugin_id || props.projectId)
      submissionVersion.value = String(sub?.release_tag || '1.0').replace(/^v/, '')
      publishSubmit.value = 'ok'
    } else { publishSubmit.value = 'fail'; publishErrors.value = [r.error || ''] }
  } catch (e) { publishSubmit.value = 'fail'; publishErrors.value = [String(e)] }
}

// Emit files-changed when agent updates project files
const draftList = computed(() => Object.values(drafts.value) as AssistantDraft[])
const eventList = computed(() => messages.value.flatMap((message: { events?: AssistantEventRecord[] }) => message.events || []))
watch(eventList, (events: AssistantEventRecord[]) => {
  for (const event of events) {
    if (event.event === 'tool_result' && event.payload.tool === 'creator_project_update' && event.payload.ok !== false) {
      projectData.value = null // force reload file list on next open
      const result = event.payload.result as Record<string, unknown> | undefined
      // Update live revision from tool result so next send uses the correct value
      const newRevision = (result?.project as Record<string, unknown> | undefined)?.revision
      if (typeof newRevision === 'number' && newRevision > liveRevision.value) {
        liveRevision.value = newRevision
      }
      const files = result?.files as Record<string, unknown> | undefined
      const paths = files ? Object.keys(files) : []
      if (paths.length) emit('files-changed', paths)
    }
  }
}, { deep: true })
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
onMounted(() => {
  void loadAssistantVoiceConfig()
  // If opened with an initial prompt (e.g. from the hero input), send it automatically
  if (props.initialPrompt?.trim() && !messages.value.length) {
    draft.value = props.initialPrompt.trim()
    void nextTick(() => void onSubmit())
  }
})
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

const quickQuestions = () => {
  const zh = locale.value === 'zh-CN'
  const kind = props.projectKind || 'adventure'
  if (kind === 'rule') return [
    { id: 'attributes', prompt: zh ? '帮我完善属性、技能和点数配置' : 'Help me flesh out attributes, skills and point costs' },
    { id: 'checks', prompt: zh ? '帮我补齐三条检定示例和成功条件' : 'Add three example checks with success conditions' },
    { id: 'combat', prompt: zh ? '帮我补齐生命值、战斗和暴击规则' : 'Add HP, combat and critical hit rules' },
    { id: 'review', prompt: zh ? '帮我检查规则文件并修复明显矛盾' : 'Review rule files and fix any contradictions' },
  ]
  if (kind === 'toolkit') return [
    { id: 'dice', prompt: zh ? '帮我把工具实现成可调用的骰子助手' : 'Implement this as a callable dice helper tool' },
    { id: 'schema', prompt: zh ? '帮我补齐工具输入输出 schema 和错误处理' : 'Add input/output schema and error handling' },
    { id: 'docs', prompt: zh ? '更新 README，说明工具怎么安装和使用' : 'Update README with installation and usage instructions' },
    { id: 'check', prompt: zh ? '加入最小可运行示例和自检' : 'Add a minimal runnable example with self-check' },
  ]
  // adventure / gameplay default
  return [
    { id: 'outline', prompt: zh ? '帮我写入冒险大纲和前三个场景' : 'Write an adventure outline and first three scenes' },
    { id: 'npc', prompt: zh ? '给工程加两个有个性的 NPC' : 'Add two distinctive NPCs to this project' },
    { id: 'validate', prompt: zh ? '对当前工程运行预检并修复问题' : 'Validate the project and fix any issues found' },
    { id: 'review', prompt: zh ? '检查工程文件并给出改进建议' : 'Review the project files and suggest improvements' },
  ]
}

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
  if (!value || streaming.value || pendingApproval.value) return
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
watch(eventList, (events: AssistantEventRecord[]) => {
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

// Workspace tabs: 'tasks' | 'files'
const workspaceTab = ref<'tasks' | 'files'>('tasks')
watch(filesOpen, open => { if (open) { workspaceTab.value = 'files'; workspaceForcedOpen.value = true } })
watch(workspaceTab, tab => { if (tab === 'files') void loadProjectFiles() })

// Workspace resize
const workspaceHeight = ref(0) // 0 = use CSS default
const workspaceEl = ref<HTMLElement | null>(null)
function onResizeStart(e: PointerEvent) {
  if (!workspaceEl.value) return
  e.preventDefault()
  const startY = e.clientY
  const startH = workspaceEl.value.offsetHeight
  const onMove = (ev: PointerEvent) => {
    const delta = startY - ev.clientY
    const newH = Math.max(80, Math.min(startH + delta, window.innerHeight * 0.8))
    workspaceHeight.value = newH
  }
  const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp) }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}

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
        <strong id="assistant-panel-title">{{ props.projectName || (locale === 'zh-CN' ? '创作工坊' : 'Creator Studio') }}</strong>
        <span>{{ locale === 'zh-CN' ? 'AI 助手 · 创作工坊' : 'AI · Creator Studio' }}</span>
      </div>
      <div class="assistant-head-actions">
      <!-- Creator-specific: Files + Publish buttons -->
      <button v-if="props.projectId" type="button" :class="['creator-head-btn', { active: filesOpen }]" @click="filesOpen = !filesOpen">
        {{ locale === 'zh-CN' ? '文件' : 'Files' }}
      </button>
      <div v-if="props.projectId" class="creator-publish-wrap">
        <button ref="publishBtnEl" type="button" :class="['creator-head-btn', 'creator-head-btn-primary', { active: publishOpen }]" @click="publishOpen ? publishOpen = false : runPublish()">
          {{ locale === 'zh-CN' ? '发布' : 'Publish' }}
        </button>
      </div>
      <!-- Publish popover teleported to body to escape backdrop-filter stacking context -->
      <Teleport to="body">
        <template v-if="publishOpen">
          <div class="creator-publish-backdrop" @click.self="publishOpen = false" />
          <div class="creator-publish-popover" :style="publishPopoverStyle">
          <strong>{{ locale === 'zh-CN' ? '发布工程' : 'Publish project' }}</strong>
          <div class="creator-publish-steps">
            <div class="creator-publish-step" :class="`is-${publishValidate}`">
              <span class="creator-step-icon">{{ publishValidate === 'ok' ? '✓' : publishValidate === 'fail' ? '✗' : publishValidate === 'running' ? '…' : '①' }}</span>
              <span>{{ locale === 'zh-CN' ? '预检' : 'Validate' }}</span>
              <span v-if="publishValidate === 'ok'" class="creator-step-ok">{{ locale === 'zh-CN' ? '通过' : 'OK' }}</span>
              <span v-if="publishValidate === 'fail'" class="creator-step-fail">{{ locale === 'zh-CN' ? '未通过' : 'Failed' }}</span>
            </div>
            <div class="creator-publish-step" :class="`is-${publishBuild}`">
              <span class="creator-step-icon">{{ publishBuild === 'ok' ? '✓' : publishBuild === 'fail' ? '✗' : publishBuild === 'running' ? '…' : '②' }}</span>
              <span>{{ locale === 'zh-CN' ? '构建内容包' : 'Build' }}</span>
              <span v-if="publishBuild === 'ok'" class="creator-step-ok">{{ locale === 'zh-CN' ? '已下载' : 'Downloaded' }}</span>
            </div>
            <div class="creator-publish-step" :class="`is-${publishSubmit}`">
              <span class="creator-step-icon">{{ publishSubmit === 'ok' ? '✓' : publishSubmit === 'fail' ? '✗' : publishSubmit === 'running' ? '…' : '③' }}</span>
              <span>{{ locale === 'zh-CN' ? '准备投稿' : 'Submission' }}</span>
              <span v-if="publishSubmit === 'ok'" class="creator-step-ok">{{ locale === 'zh-CN' ? '已生成' : 'Ready' }}</span>
            </div>
          </div>
          <div v-if="publishSubmit === 'ok'" class="creator-submission-wizard">
            <SubmissionWizard :plugin-id="submissionPluginId || props.projectId || ''" :version="submissionVersion || '1.0'" :file-name="builtFileName || ''" />
          </div>
          <div v-if="publishErrors.length" class="creator-publish-errors">
            <p v-for="(e, i) in publishErrors" :key="i" class="error-text">{{ e }}</p>
          </div>
          <div class="creator-publish-footer">
            <button v-if="publishValidate === 'fail' || publishBuild === 'fail' || publishSubmit === 'fail'" type="button" class="creator-retry-btn" @click="runPublish">
              {{ locale === 'zh-CN' ? '重试' : 'Retry' }}
            </button>
            <NButton v-if="publishSubmit === 'ok'" size="small" secondary @click="publishOpen = false">{{ locale === 'zh-CN' ? '完成' : 'Done' }}</NButton>
          </div>
        </div>
        </template>
      </Teleport>
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
          <strong>{{ props.projectName ? (locale === 'zh-CN' ? `开始创作「${props.projectName}」` : `Start working on "${props.projectName}"`) : (locale === 'zh-CN' ? '创作工坊 · AI 助手' : 'Creator Studio · AI') }}</strong>
          <p>{{ locale === 'zh-CN' ? '描述你的想法，我来帮你把它写进工程里。' : 'Describe your idea and I\'ll write it into the project.' }}</p>
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

      <section
        v-if="workspaceVisible || (props.projectId && filesOpen)"
        ref="workspaceEl"
        class="assistant-tasks"
        :class="{ 'assistant-workspace-collapsed': workspaceIsCollapsed }"
        :style="workspaceHeight && !workspaceIsCollapsed ? { height: workspaceHeight + 'px', maxHeight: '80vh' } : {}"
      >
        <!-- Resize handle -->
        <div v-if="!workspaceIsCollapsed" class="assistant-workspace-resize" @pointerdown="onResizeStart" />

        <!-- Toggle bar with tabs -->
        <div class="assistant-workspace-toggle-bar">
          <button type="button" class="assistant-workspace-toggle" data-testid="assistant-workspace-toggle" :aria-expanded="!workspaceIsCollapsed" :aria-controls="workspaceId" :aria-label="workspaceIsCollapsed ? workspaceCopy.expand : workspaceCopy.collapse" @click="toggleTaskWorkspace">
            <span v-if="workspaceIsCollapsed" aria-hidden="true">⌃</span>
            <span v-else aria-hidden="true">⌄</span>
          </button>
          <button
            type="button"
            :class="['assistant-workspace-tab', { active: workspaceTab === 'tasks' }]"
            @click="workspaceTab = 'tasks'; workspaceForcedOpen = true; if (workspaceIsCollapsed) toggleTaskWorkspace()"
          >
            <strong>{{ agentCopy.workspaceTitle }}</strong>
            <span class="assistant-workspace-state" :class="{ pending: pendingApproval || actionError }">{{ workspaceStatus }}</span>
            <span v-if="pendingApproval" class="assistant-workspace-badge" :title="assistantAuthorizationCopy(locale).reviewPending">1</span>
          </button>
          <button
            v-if="props.projectId"
            type="button"
            :class="['assistant-workspace-tab', { active: workspaceTab === 'files' }]"
            @click="workspaceTab = 'files'; filesOpen = true; if (workspaceIsCollapsed) toggleTaskWorkspace()"
          >
            {{ locale === 'zh-CN' ? '文件' : 'Files' }}
            <span v-if="projectData" class="assistant-workspace-state">r{{ projectData.revision }}</span>
          </button>
        </div>

        <!-- Tasks pane -->
        <div v-show="!workspaceIsCollapsed && workspaceTab === 'tasks'" :id="workspaceId" ref="tasksEl" class="assistant-workspace-body">
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

        <!-- Files pane -->
        <div v-if="!workspaceIsCollapsed && workspaceTab === 'files' && props.projectId" class="assistant-workspace-body creator-files-body">
          <div class="creator-files-toolbar">
            <div class="creator-file-tabs">
              <button v-for="g in ['overview', 'content', 'rules', 'docs', 'all'] as const" :key="g"
                type="button" :class="{ active: editorGroup === g }" @click="editorGroup = g">
                {{ t(EDITOR_GROUP_KEYS[g]) }}
              </button>
            </div>
            <div class="creator-files-actions">
              <button type="button" class="creator-adv-toggle" @click="advancedEdit = !advancedEdit">
                {{ advancedEdit ? (locale === 'zh-CN' ? '只读' : 'Read-only') : (locale === 'zh-CN' ? '高级编辑' : 'Advanced') }}
              </button>
            </div>
          </div>
          <NInput v-model:value="fileSearch" size="small" clearable :placeholder="t('creatorFileSearchPlaceholder')" />
          <div class="creator-file-list">
            <div v-for="path in filteredFiles" :key="path" class="creator-file-row" :class="{ active: path === selectedFile }" @click="selectFile(path)">
              <span class="creator-file-name">{{ path }}</span>
              <button type="button" class="creator-file-del" @click.stop="deleteFile(path)">×</button>
            </div>
            <p v-if="!filteredFiles.length" class="muted" style="font-size:12px;padding:4px">{{ fileSearch ? t('creatorFileSearchEmpty') : t('creatorEditorGroupEmpty') }}</p>
          </div>
          <div v-if="selectedFile" class="creator-file-content">
            <code style="font-size:11px;color:var(--df-text-muted)">{{ selectedFile }}</code>
            <textarea v-model="selectedContent" class="creator-file-textarea" spellcheck="false" :readonly="!advancedEdit" />
            <NButton v-if="advancedEdit" size="small" @click="saveFile">{{ locale === 'zh-CN' ? '保存' : 'Save' }}</NButton>
          </div>
        </div>
      </section>
    </div>
    </div>

    <footer class="assistant-composer" :class="{ 'assistant-composer-voice': voiceInputAvailable }">
      <NInput
        v-model:value="draft"
        type="textarea"
        :placeholder="pendingApproval ? (locale === 'zh-CN' ? '请先确认或拒绝上方的操作…' : 'Approve or reject the pending action above…') : transcribing ? voiceCopy.transcribing : listening ? (interim || voiceCopy.listening) : t('assistantPlaceholder')"
        :disabled="!!pendingApproval"
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
      <NButton v-else type="primary" class="assistant-send" :disabled="!draft.trim() || !!pendingApproval" @click="onSubmit">
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
  /* 工程名可能极长（压测：整行重复词）：与「我的作品」行一致，省略而不是撑爆头部。 */
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

/* Resize handle */
.assistant-workspace-resize {
  flex: 0 0 auto;
  height: 6px;
  cursor: ns-resize;
  background: transparent;
  position: relative;
}
.assistant-workspace-resize::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 32px;
  height: 2px;
  border-radius: 999px;
  background: var(--df-border-soft);
  transition: background .15s;
}
.assistant-workspace-resize:hover::after { background: var(--df-interactive); }

/* Tab bar replacing old single toggle button */
.assistant-workspace-toggle-bar {
  display: flex;
  flex: 0 0 auto;
  align-items: stretch;
  background: var(--df-surface-2);
  border-bottom: 1px solid var(--df-border-soft);
}
.assistant-workspace-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  flex: 0 0 auto;
  border: 0;
  border-right: 1px solid var(--df-border-soft);
  border-radius: 0;
  background: transparent;
  color: var(--df-text-muted);
  cursor: pointer;
  font-size: 11px;
}
.assistant-workspace-toggle:hover { color: var(--df-text); }
.assistant-workspace-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  min-height: 38px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--df-text-muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color .12s, border-color .12s;
}
.assistant-workspace-tab:hover { color: var(--df-text); }
.assistant-workspace-tab.active { color: var(--df-text); border-bottom-color: var(--df-interactive); }
.assistant-workspace-tab strong { font-size: 12px; font-weight: 700; }

.assistant-workspace-state { color: var(--df-text-muted); font-size: 11px; }
.assistant-workspace-state.pending { color: var(--df-accent-strong); }
.assistant-workspace-badge { display: inline-grid; place-items: center; min-width: 17px; height: 17px; padding: 0 4px; border-radius: 999px; color: var(--df-on-accent); background: var(--df-danger-strong, #c94b55); font-size: 10px; font-weight: 800; }
.assistant-workspace-body { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 12px 15px; }

/* Files tab inside workspace */
.creator-files-body { gap: 8px; padding: 10px 12px; }
.creator-files-toolbar { display: flex; align-items: center; justify-content: space-between; gap: .5rem; flex-wrap: wrap; }

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

/* ── Creator-specific additions ───────────────────────── */
.creator-head-btn { padding: .2rem .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: transparent; color: var(--df-text-muted); font: .78rem/1 inherit; cursor: pointer; white-space: nowrap; }
.creator-head-btn:hover, .creator-head-btn.active { border-color: var(--df-interactive); color: var(--df-text); background: color-mix(in srgb, var(--df-interactive) 10%, transparent); }
.creator-head-btn-primary { border-color: color-mix(in srgb, var(--df-interactive) 50%, var(--df-border-soft)); color: var(--df-interactive-strong); }
.creator-publish-wrap { position: relative; }
.creator-publish-backdrop { position: fixed; inset: 0; z-index: 599; }
.creator-publish-popover { position: fixed; z-index: 600; width: min(400px, calc(100vw - 2rem)); max-height: 70vh; overflow-y: auto; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-interactive) 40%, var(--df-border-soft)); border-radius: var(--df-radius-lg); background: var(--df-surface-1); box-shadow: 0 8px 32px rgba(0,0,0,.28); }
.creator-publish-popover strong { display: block; font-size: .9rem; margin-bottom: .65rem; }
.creator-publish-steps { display: grid; gap: .5rem; }
.creator-publish-step { display: flex; align-items: center; gap: .5rem; font-size: .84rem; }
.creator-step-icon { width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; background: var(--df-surface-2); font-size: .78rem; font-weight: 700; flex: 0 0 auto; }
.is-ok .creator-step-icon { background: color-mix(in srgb, var(--df-success, #4caf50) 20%, var(--df-surface-2)); color: var(--df-success, #4caf50); }
.is-fail .creator-step-icon { background: color-mix(in srgb, var(--df-danger) 20%, var(--df-surface-2)); color: var(--df-danger-strong); }
.is-running .creator-step-icon { background: color-mix(in srgb, var(--df-interactive) 20%, var(--df-surface-2)); color: var(--df-interactive-strong); animation: creator-spin 1s linear infinite; }
@keyframes creator-spin { to { transform: rotate(360deg); } }
.creator-step-ok { margin-left: auto; color: var(--df-success, #4caf50); font-size: .76rem; font-weight: 700; }
.creator-step-fail { margin-left: auto; color: var(--df-danger-strong); font-size: .76rem; font-weight: 700; }
.creator-submission-wizard { margin-top: .65rem; padding-top: .65rem; border-top: 1px solid var(--df-border-soft); }
.creator-publish-errors p { margin: 0; font-size: .78rem; }
.creator-publish-footer { display: flex; justify-content: flex-end; gap: .5rem; margin-top: .6rem; }
.creator-retry-btn { padding: .25rem .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: transparent; color: var(--df-text-muted); font: .82rem/1 inherit; cursor: pointer; }
.creator-adv-toggle { padding: .15rem .5rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-interactive-strong); font-size: .72rem; font-weight: 700; cursor: pointer; }
.creator-file-tabs { display: flex; flex-wrap: wrap; gap: .25rem; }
.creator-file-tabs button { padding: .2rem .55rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text-muted); font-size: .72rem; font-weight: 600; cursor: pointer; }
.creator-file-tabs button.active { color: var(--df-text); border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 12%, var(--df-surface-2)); }
.creator-file-list { display: grid; gap: .15rem; max-height: 120px; overflow-y: auto; }
.creator-file-row { display: flex; align-items: center; border-radius: var(--df-radius-sm); cursor: pointer; }
.creator-file-row:hover, .creator-file-row.active { background: var(--df-surface-2); }
.creator-file-name { flex: 1; overflow: hidden; padding: .28rem .4rem; font-size: .76rem; color: var(--df-text-muted); text-overflow: ellipsis; white-space: nowrap; }
.creator-file-row.active .creator-file-name, .creator-file-row:hover .creator-file-name { color: var(--df-text); }
.creator-file-del { display: none; align-items: center; justify-content: center; width: 18px; height: 18px; border: 0; background: transparent; color: var(--df-text-muted); font-size: .82rem; cursor: pointer; border-radius: var(--df-radius-sm); margin-right: .2rem; flex: 0 0 auto; }
.creator-file-row:hover .creator-file-del { display: inline-flex; }
.creator-file-del:hover { color: var(--df-danger-strong); }
.creator-file-content { display: flex; flex-direction: column; gap: .3rem; }
.creator-file-textarea { min-height: 100px; width: 100%; box-sizing: border-box; resize: vertical; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); padding: .45rem; color: var(--df-text); background: var(--df-surface-2); font: .76rem/1.5 ui-monospace, Consolas, monospace; }
.creator-file-textarea[readonly] { opacity: .7; }
</style>
