<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { NButton, NTag } from 'naive-ui'
import { useCreatorAssistant } from '@/composables/useCreatorAssistant'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'
import { creatorApi, type CreatorProject } from '@/api/creator'
import { assistantToolLabel } from '@/features/assistant/copy'
import AgentExecutionSummary from '@/components/assistant/AgentExecutionSummary.vue'
import { renderSafeMarkdown } from '@/utils/markdown'

type DiffRow = { path: string; status: 'added' | 'removed' | 'changed'; added: number; removed: number }

const props = defineProps<{ project: CreatorProject | null; initialMessage?: string }>()
const emit = defineEmits<{ 'files-changed': [paths: string[]]; 'message-sent': [] }>()
const { t, locale } = useLocale()
const projectScope = computed(() => props.project?.id)
const { messages, streaming, status, actionError, send, stop, pendingApproval, confirmApproval, actionBusy, autoApprove, toggleAutoApprove } = useCreatorAssistant(projectScope)

const draft = ref('')
const transcriptEl = ref<HTMLElement | null>(null)
const snapshot = ref<Array<[string, string]>>([])
const diffRows = ref<DiffRow[]>([])
const diffVisible = ref(false)
let initialSent = false

type CreatorKind = 'adventure' | 'rule' | 'gameplay' | 'toolkit'
const WISH_CHIP_KEYS: Record<CreatorKind, MessageKey[]> = {
  adventure: ['creatorAiChipOutline', 'creatorAiChipNpc', 'creatorAiChipLore', 'creatorAiChipBalance'],
  rule: ['creatorAiRuleChipAttributes', 'creatorAiRuleChipChecks', 'creatorAiRuleChipCombat', 'creatorAiRuleChipReview'],
  gameplay: ['creatorAiGameplayChipLoop', 'creatorAiGameplayChipState', 'creatorAiGameplayChipExample', 'creatorAiGameplayChipReview'],
  toolkit: ['creatorAiToolkitChipDice', 'creatorAiToolkitChipSchema', 'creatorAiToolkitChipDocs', 'creatorAiToolkitChipCheck'],
}
const DIFF_STATUS_KEYS: Record<DiffRow['status'], MessageKey> = {
  added: 'creatorAiDiffAdded',
  removed: 'creatorAiDiffRemoved',
  changed: 'creatorAiDiffChanged',
}

const creatorKind = computed<CreatorKind>(() => {
  const files = props.project?.files || {}
  try {
    const manifest = JSON.parse(files['plugin.json'] || '{}') as Record<string, unknown>
    if (manifest.plugin_type === 'tool') return 'toolkit'
    if (manifest.content_profile === 'adventure-module' || Object.keys(files).some(p => p.startsWith('adventures/'))) return 'adventure'
  } catch { /* keep adventure as default */ }
  if (Object.keys(files).some(p => p.startsWith('content/rules/') || p.startsWith('rules/'))) return 'rule'
  if (files['docs/gameplay-design.md']) return 'gameplay'
  return 'adventure'
})

const wishChipKeys = computed(() => WISH_CHIP_KEYS[creatorKind.value])

watch(() => props.project?.id, () => {
  draft.value = ''
  snapshot.value = []
  diffRows.value = []
  diffVisible.value = false
  initialSent = false
})

function scrollToBottom() {
  void nextTick(() => {
    const el = transcriptEl.value
    if (el && el.clientHeight > 0) el.scrollTop = el.scrollHeight
  })
}
watch(messages, scrollToBottom, { deep: true })
watch(streaming, scrollToBottom)

function bagLineDiff(oldText: string, newText: string): { added: number; removed: number } {
  const counts = (text: string) => {
    const result = new Map<string, number>()
    for (const line of text.split('\n')) result.set(line, (result.get(line) || 0) + 1)
    return result
  }
  const oldBag = counts(oldText), newBag = counts(newText)
  let added = 0, removed = 0
  for (const [line, count] of newBag) added += Math.max(0, count - (oldBag.get(line) || 0))
  for (const [line, count] of oldBag) removed += Math.max(0, count - (newBag.get(line) || 0))
  return { added, removed }
}

async function snapshotProject(): Promise<void> {
  if (!props.project) return
  try {
    const result = await creatorApi.read(props.project.id)
    snapshot.value = Object.entries(result.project.files)
  } catch {
    snapshot.value = Object.entries(props.project.files)
  }
}

async function refreshDiff(): Promise<void> {
  if (!props.project) return
  try {
    const result = await creatorApi.read(props.project.id)
    const before = new Map(snapshot.value)
    const after = Object.entries(result.project.files)
    const rows: DiffRow[] = []
    for (const [path, content] of after) {
      if (!before.has(path)) rows.push({ path, status: 'added', added: content.split('\n').length, removed: 0 })
      else if (before.get(path) !== content) {
        const stats = bagLineDiff(before.get(path) || '', content)
        if (stats.added || stats.removed) rows.push({ path, status: 'changed', ...stats })
      }
    }
    for (const [path] of before) {
      if (!Object.prototype.hasOwnProperty.call(result.project.files, path)) rows.push({ path, status: 'removed', added: 0, removed: 0 })
    }
    diffRows.value = rows
    diffVisible.value = rows.length > 0
    if (rows.length) emit('files-changed', rows.map(r => r.path))
  } catch { /* silent */ }
}

watch(streaming, (active, previous) => { if (previous && !active) void refreshDiff() })
watch(pendingApproval, (value, previous) => { if (previous && !value) void refreshDiff() })

async function sendMessage(text?: string): Promise<void> {
  const value = (text ?? draft.value).trim()
  if (!value || streaming.value) return
  draft.value = ''
  await snapshotProject()
  const contextNote = props.project
    ? `（当前工程：${props.project.name}，工程 id ${props.project.id}，当前 revision ${props.project.revision}。` +
      `这是执行型请求，不要只给建议：先用 creator_project_read 读取工程；需要改变内容时必须调用 creator_project_update，携带 expected_revision=${props.project.revision}；` +
      `当前工程类型是 ${creatorKind.value}，只修改与该类型对应的源文件。）`
    : ''
  emit('message-sent')
  await send(value, locale.value, contextNote)
}

onMounted(async () => {
  if (props.initialMessage?.trim() && !initialSent) {
    initialSent = true
    await sendMessage(props.initialMessage)
  }
})

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void sendMessage()
  }
}

const displayMessages = computed(() => messages.value.filter(m => m.content?.trim() || m.error || m.stopped || (streaming.value && m.role === 'assistant')))

function messageActions(message: { events?: { event: string; payload: Record<string, unknown> }[] }) {
  return (message.events || []).filter(e => e.event === 'tool_result')
}
function actionLabel(event: { payload: Record<string, unknown> }): string {
  const label = assistantToolLabel(locale.value, event.payload.tool)
  if (event.payload.ok === false) return `${label} · 失败`
  return event.payload.partial ? `${label} · 部分完成` : label
}
</script>

<template>
  <div class="cai-panel">
    <!-- Transcript -->
    <div ref="transcriptEl" class="cai-transcript">
      <!-- Empty state -->
      <div v-if="!displayMessages.length && !streaming" class="cai-empty">
        <strong>{{ t('creatorStudioWelcome') }}</strong>
        <p class="muted">{{ project ? (locale === 'zh-CN' ? `工程：${project.name}` : `Project: ${project.name}`) : '' }}</p>
        <div class="cai-quick-grid">
          <button v-for="chipKey in wishChipKeys" :key="chipKey" type="button" :disabled="streaming" @click="sendMessage(t(chipKey))">
            {{ t(chipKey) }}
          </button>
        </div>
      </div>

      <!-- Messages -->
      <article
        v-for="(message, index) in displayMessages"
        :key="index"
        class="cai-message"
        :class="`cai-message-${message.role}`"
      >
        <span v-if="message.role === 'assistant'" class="cai-avatar" aria-hidden="true">DF</span>
        <div class="cai-message-stack">
          <div v-if="message.content || (streaming && message.role === 'assistant' && index === displayMessages.length - 1)" class="cai-bubble" :class="message.role">
            <span v-if="streaming && index === displayMessages.length - 1 && !message.content && message.role === 'assistant'" class="cai-thinking">
              <i /><i /><i />
            </span>
            <div v-else-if="message.role === 'assistant'" class="safe-markdown" v-html="renderSafeMarkdown(message.content)" />
            <span v-else>{{ message.content }}</span>
          </div>
          <span v-if="message.error" class="cai-inline-error">{{ message.error }}</span>
          <!-- Tool results -->
          <ul v-if="messageActions(message).length" class="cai-actions">
            <li v-for="(event, ei) in messageActions(message)" :key="ei" class="cai-action" :class="{ failed: event.payload.ok === false }">
              <span class="cai-action-mark">{{ event.payload.ok === false ? '✕' : '✓' }}</span>
              <span>{{ actionLabel(event) }}</span>
            </li>
          </ul>
        </div>
      </article>
    </div>

    <!-- Workspace (approval / status / diff) -->
    <section v-if="pendingApproval || actionError || diffVisible || (streaming && status)" class="cai-workspace">
      <!-- Status -->
      <div v-if="streaming && status" class="cai-status" role="status">{{ status }}</div>
      <!-- Error -->
      <div v-if="actionError" class="cai-inline-error" role="alert">{{ actionError }}</div>
      <!-- Approval -->
      <article v-if="pendingApproval" class="cai-approval">
        <div class="cai-approval-head">
          <strong>{{ t('creatorAiApprovalTitle') }}</strong>
          <span class="cai-approval-tool">{{ assistantToolLabel(locale, pendingApproval.tool) }}</span>
        </div>
        <AgentExecutionSummary :approval="pendingApproval" :locale="locale" />
        <details v-if="pendingApproval.preview || pendingApproval.arguments">
          <summary>{{ t('creatorAiApprovalDetails') }}</summary>
          <pre class="cai-args">{{ JSON.stringify(pendingApproval.preview || pendingApproval.arguments, null, 2).slice(0, 1800) }}</pre>
        </details>
        <div class="cai-approval-actions">
          <NButton size="small" type="primary" :loading="actionBusy" :disabled="pendingApproval.expired" @click="confirmApproval(true)">
            {{ t('creatorAiApprove') }}
          </NButton>
          <NButton size="small" secondary :disabled="actionBusy" @click="confirmApproval(false)">
            {{ t('creatorAiReject') }}
          </NButton>
        </div>
      </article>
      <!-- Diff -->
      <div v-if="diffVisible" class="cai-diff">
        <strong>{{ t('creatorAiDiffTitle') }}</strong>
        <p v-for="row in diffRows" :key="row.path" class="cai-diff-row">
          <NTag size="small" :type="row.status === 'removed' ? 'error' : row.status === 'added' ? 'success' : 'warning'">
            {{ t(DIFF_STATUS_KEYS[row.status]) }}
          </NTag>
          <code>{{ row.path }}</code>
          <span v-if="row.status === 'changed'" class="muted">+{{ row.added }} / -{{ row.removed }}</span>
        </p>
      </div>
    </section>

    <!-- Composer -->
    <footer class="cai-composer">
      <NInput
        v-model:value="draft"
        type="textarea"
        class="cai-input"
        :placeholder="t('creatorAiPlaceholder')"
        :autosize="{ minRows: 1, maxRows: 5 }"
        @keydown="onKeydown"
      />
      <div class="cai-composer-actions">
        <NButton
          v-if="messages.length"
          size="small"
          secondary
          :disabled="streaming"
          @click="() => { messages.splice(0); diffRows = []; diffVisible = false }"
        >
          {{ locale === 'zh-CN' ? '新对话' : 'New chat' }}
        </NButton>
        <button type="button" :class="['cai-auto-btn', { active: autoApprove }]" :title="locale === 'zh-CN' ? (autoApprove ? '自动确认已开启' : '开启自动确认') : (autoApprove ? 'Auto-approve on' : 'Auto-approve')" @click="toggleAutoApprove">
          {{ locale === 'zh-CN' ? (autoApprove ? '✓ 自动' : '自动') : (autoApprove ? '✓ Auto' : 'Auto') }}
        </button>
        <NButton v-if="streaming" type="warning" @click="stop">
          {{ t('creatorAiStop') }}
        </NButton>
        <NButton v-else type="primary" :disabled="!draft.trim()" @click="sendMessage()">
          {{ t('creatorAiSend') }}
        </NButton>
      </div>
      <span class="cai-hint muted">{{ t('creatorAiBoundary') }}</span>
    </footer>
  </div>
</template>

<style scoped>
/* Panel shell */
.cai-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; color: var(--df-text); background: radial-gradient(circle at 100% 0, color-mix(in srgb, var(--df-interactive) 10%, transparent), transparent 26%), var(--df-surface-1); }

/* Transcript */
.cai-transcript { flex: 1 1 0; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; padding: 18px 15px; scroll-behavior: smooth; }

/* Empty state */
.cai-empty { display: grid; width: min(100%, 360px); margin: auto; justify-items: center; gap: 9px; padding: 24px 6px; text-align: center; }
.cai-empty strong { color: var(--df-accent-strong); font-size: 15px; }
.cai-quick-grid { display: grid; width: 100%; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 8px 0 2px; }
.cai-quick-grid button { min-height: 54px; padding: 9px 10px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); color: var(--df-text-secondary); background: color-mix(in srgb, var(--df-control-bg) 82%, transparent); font-size: 12px; line-height: 1.4; text-align: left; cursor: pointer; }
.cai-quick-grid button:hover { border-color: var(--df-interactive); }

/* Messages */
.cai-message { display: flex; align-items: flex-start; gap: 8px; }
.cai-message-user { justify-content: flex-end; }
.cai-avatar { flex: 0 0 auto; width: 28px; height: 28px; border-radius: 50%; background: var(--df-interactive); color: #fff; font-size: 11px; font-weight: 800; display: flex; align-items: center; justify-content: center; }
.cai-message-stack { display: grid; grid-template-columns: minmax(0, 1fr); max-width: 88%; min-width: 0; gap: 6px; }
.cai-bubble { min-width: 0; padding: 10px 12px; border: 1px solid var(--df-border-soft); border-radius: 14px; font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.cai-bubble.user { border-radius: 14px 14px 4px 14px; border-color: color-mix(in srgb, var(--df-interactive) 50%, var(--df-border-soft)); background: color-mix(in srgb, var(--df-interactive) 15%, var(--df-surface-1)); white-space: pre-wrap; }
.cai-bubble.assistant { border-radius: 4px 14px 14px 14px; background: linear-gradient(155deg, var(--df-surface-1), var(--df-surface-2)); }
.cai-thinking { display: inline-flex; align-items: center; gap: 4px; min-height: 20px; }
.cai-thinking i { width: 5px; height: 5px; border-radius: 50%; background: var(--df-interactive-strong); animation: cai-pulse 1s ease-in-out infinite; }
.cai-thinking i:nth-child(2) { animation-delay: .14s; }
.cai-thinking i:nth-child(3) { animation-delay: .28s; }
@keyframes cai-pulse { 0%, 70%, 100% { opacity: .25; transform: translateY(0); } 35% { opacity: 1; transform: translateY(-3px); } }
.cai-inline-error { font-size: 11px; color: var(--df-danger-strong); padding: 6px 9px; border: 1px solid color-mix(in srgb, var(--df-danger) 38%, var(--df-border-soft)); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-danger) 8%, transparent); }
.cai-actions { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
.cai-action { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 4px 9px; border: 1px solid color-mix(in srgb, var(--df-success, #6fbf8a) 35%, var(--df-border-soft)); border-radius: 999px; color: var(--df-text-muted); background: color-mix(in srgb, var(--df-success, #6fbf8a) 8%, transparent); font-size: 12px; width: fit-content; }
.cai-action.failed { border-color: color-mix(in srgb, var(--df-danger) 45%, var(--df-border-soft)); background: color-mix(in srgb, var(--df-danger) 8%, transparent); }
.cai-action-mark { color: var(--df-success, #6fbf8a); font-weight: 700; }
.cai-action.failed .cai-action-mark { color: var(--df-danger); }

/* Workspace */
.cai-workspace { flex: 0 1 auto; display: flex; flex-direction: column; gap: 10px; max-height: 40%; overflow-y: auto; border-top: 1px solid var(--df-border-soft); padding: 12px 15px; background: var(--df-surface-1); }
.cai-status { font-size: 12px; color: var(--df-text-muted); }
.cai-approval { display: grid; gap: 7px; padding: 9px 10px; border: 1px solid color-mix(in srgb, var(--df-warning) 50%, var(--df-border-soft)); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-warning) 8%, var(--df-surface-1)); font-size: 12px; }
.cai-approval-head { display: flex; align-items: center; gap: 8px; }
.cai-approval-tool { color: var(--df-text-muted); font-size: 11px; }
.cai-approval-actions { display: flex; gap: 7px; }
.cai-args { max-height: 10rem; overflow: auto; margin: 0; padding: .5rem; border-radius: var(--df-radius-sm); background: var(--df-surface-2); font: .72rem/1.4 ui-monospace, Consolas, monospace; white-space: pre-wrap; }
.cai-diff { display: grid; gap: .3rem; }
.cai-diff-row { display: flex; align-items: center; gap: .5rem; margin: 0; font-size: .82rem; }
.cai-diff-row code { font-size: .78rem; }

/* Composer */
.cai-composer { flex: 0 0 auto; display: flex; flex-direction: column; gap: 8px; padding: 12px 14px calc(10px + env(safe-area-inset-bottom)); border-top: 1px solid var(--df-border-soft); background: color-mix(in srgb, var(--df-surface-1) 94%, transparent); }
.cai-input { width: 100%; min-height: 36px; }
.cai-input :deep(.n-input__textarea-el) { min-height: 36px !important; }
.cai-composer-actions { display: flex; justify-content: flex-end; gap: 8px; align-items: center; }
.cai-hint { font-size: 10px; color: var(--df-text-muted); }
.cai-auto-btn { padding: .25rem .6rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text-muted); font-size: .76rem; cursor: pointer; white-space: nowrap; }
.cai-auto-btn:hover { border-color: var(--df-interactive); color: var(--df-text); }
.cai-auto-btn.active { border-color: var(--df-success); color: var(--df-success-strong); background: color-mix(in srgb, var(--df-success) 10%, transparent); }
</style>
