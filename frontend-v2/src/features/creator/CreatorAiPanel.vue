<script setup lang="ts">
// W3 拍板 G：AI 许愿面板 MVP —— 「AI 提议 + 用户确认 + diff 展示」。
// 复用 useAssistant（/api/assistant/agent/* 全契约）与 creator 域工具；
// 工具写入后由本面板重新读取工程并给出逐文件差异摘要。
import { computed, ref, watch } from 'vue'
import { NButton, NTag } from 'naive-ui'
import { useCreatorAssistant } from '@/composables/useCreatorAssistant'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'
import { creatorApi, type CreatorProject } from '@/api/creator'
import { assistantToolLabel } from '@/features/assistant/copy'
import AgentExecutionSummary from '@/components/assistant/AgentExecutionSummary.vue'

type DiffRow = { path: string; status: 'added' | 'removed' | 'changed'; added: number; removed: number }

const props = defineProps<{ project: CreatorProject | null }>()
const emit = defineEmits<{ 'files-changed': [paths: string[]] }>()
const { t, locale } = useLocale()
const projectScope = computed(() => props.project?.id)
const { messages, streaming, status, task, actionError, send, stop, pendingApproval, confirmApproval, actionBusy } = useCreatorAssistant(projectScope)

const wish = ref('')
const snapshot = ref<Array<[string, string]>>([])
const diffRows = ref<DiffRow[]>([])
const diffVisible = ref(false)
const noChangeVisible = ref(false)

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
    if (manifest.content_profile === 'adventure-module' || Object.keys(files).some(path => path.startsWith('adventures/'))) return 'adventure'
  } catch {
    // The editor will show malformed plugin.json; keep the generic adventure
    // actions available so the user can ask the agent to repair it.
  }
  if (Object.keys(files).some(path => path.startsWith('content/rules/') || path.startsWith('rules/'))) return 'rule'
  if (files['docs/gameplay-design.md']) return 'gameplay'
  return 'adventure'
})

const wishChipKeys = computed(() => WISH_CHIP_KEYS[creatorKind.value])

watch(() => props.project?.id, () => {
  wish.value = ''
  snapshot.value = []
  diffRows.value = []
  diffVisible.value = false
  noChangeVisible.value = false
})

function bagLineDiff(oldText: string, newText: string): { added: number; removed: number } {
  // 行袋差（非 LCS）：足够回答「改了多少」，不承诺精确补丁语义。
  const counts = (text: string) => {
    const result = new Map<string, number>()
    for (const line of text.split('\n')) result.set(line, (result.get(line) || 0) + 1)
    return result
  }
  const oldBag = counts(oldText)
  const newBag = counts(newText)
  let added = 0
  let removed = 0
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
    noChangeVisible.value = !rows.length && !pendingApproval.value
    if (rows.length) emit('files-changed', rows.map(row => row.path))
  } catch {
    // 读取失败不打扰对话；用户可在编辑器里手动刷新。
  }
}

watch(streaming, (active, previous) => {
  if (previous && !active) void refreshDiff()
})
watch(pendingApproval, (value, previous) => {
  if (previous && !value) void refreshDiff()
})

async function sendWish(text?: string): Promise<void> {
  const value = (text ?? wish.value).trim()
  if (!value || streaming.value) return
  wish.value = ''
  noChangeVisible.value = false
  await snapshotProject()
  const contextNote = props.project
    ? `（当前工程：${props.project.name}，工程 id ${props.project.id}，当前 revision ${props.project.revision}。` +
      `这是执行型请求，不要只给建议：先用 creator_project_read 读取工程；需要改变内容时必须调用 creator_project_update，携带 expected_revision=${props.project.revision}；` +
      `当前工程类型是 ${creatorKind.value}，只修改与该类型对应的源文件；修改完成后返回实际改动文件，并继续等待用户确认执行计划。）`
    : ''
  await send(value, locale.value, contextNote)
}

const displayMessages = computed(() => messages.value.filter(message => message.content.trim() || message.error || message.stopped))
const agentEvents = computed(() => messages.value
  .flatMap(message => message.events || [])
  .filter(event => ['tool_start', 'tool_result', 'approval', 'status', 'task'].includes(event.event))
  .slice(-8))
function agentEventLabel(event: { event: string; payload: Record<string, unknown> }): string {
  if (event.event === 'tool_start') return String(event.payload.label || event.payload.tool || 'Agent tool')
  if (event.event === 'approval') return t('creatorAiEventApproval')
  if (event.event === 'status') return String(event.payload.message || t('creatorAiEventStatus'))
  if (event.event === 'task') return String(event.payload.status || t('creatorAiEventTask'))
  return String(event.payload.tool || t('creatorAiEventResult'))
}
const hasLiveExecutionState = computed(() => {
  const taskStatus = task.value?.status
  return streaming.value
    || !!actionError.value
    || (!!status.value && status.value !== 'idle')
    || (!!taskStatus && !['completed', 'idle'].includes(taskStatus))
})
</script>

<template>
  <div class="creator-ai-panel" data-testid="creator-ai-panel">
    <header class="creator-ai-heading">
      <strong>{{ t('creatorAiTitle') }}</strong>
      <p class="muted">{{ t('creatorAiHint') }}</p>
    </header>

    <div class="creator-ai-transcript">
      <template v-for="(message, index) in displayMessages" :key="index">
        <p class="creator-ai-message" :class="`is-${message.role}`">
          <span class="creator-ai-role">{{ message.role === 'user' ? t('creatorAiRoleYou') : 'DF Agent' }}</span>
          <span v-if="message.content" class="creator-ai-text">{{ message.content }}</span>
          <span v-else-if="message.error" class="creator-ai-text error-text">{{ message.error }}</span>
          <span v-else-if="message.stopped" class="creator-ai-text muted">{{ t('creatorAiStopped') }}</span>
          <span v-else class="creator-ai-text">{{ t('creatorAiWorking') }}</span>
        </p>
      </template>
      <p v-if="!displayMessages.length" class="muted">{{ t('creatorAiEmpty') }}</p>
    </div>

    <div v-if="agentEvents.length" class="creator-ai-events" data-testid="creator-ai-events">
      <strong>{{ t('creatorAiEventsTitle') }}</strong>
      <p v-for="(event, index) in agentEvents" :key="`${event.event}-${index}`">
        <span class="creator-ai-event-dot" aria-hidden="true"></span>{{ agentEventLabel(event) }}
      </p>
    </div>

    <div v-if="pendingApproval" class="creator-ai-approval" data-testid="creator-ai-approval">
      <strong>{{ t('creatorAiApprovalTitle') }}</strong>
      <span class="creator-ai-approval-tool">{{ assistantToolLabel(locale, pendingApproval.tool) }}</span>
      <AgentExecutionSummary :approval="pendingApproval" :locale="locale" />
      <details v-if="pendingApproval.preview || pendingApproval.arguments" class="creator-ai-approval-details">
        <summary>{{ t('creatorAiApprovalDetails') }}</summary>
        <pre class="creator-ai-args">{{ JSON.stringify(pendingApproval.preview || pendingApproval.arguments, null, 2).slice(0, 1800) }}</pre>
      </details>
      <div class="creator-ai-approval-actions">
        <NButton size="small" type="primary" :loading="actionBusy" :disabled="pendingApproval.expired" data-testid="creator-ai-approve" @click="confirmApproval(true)">
          {{ t('creatorAiApprove') }}
        </NButton>
        <NButton size="small" secondary :disabled="actionBusy" data-testid="creator-ai-reject" @click="confirmApproval(false)">
          {{ t('creatorAiReject') }}
        </NButton>
      </div>
    </div>

    <div v-if="hasLiveExecutionState" class="creator-ai-execution" data-testid="creator-ai-execution" role="status">
      <strong>{{ streaming ? t('creatorAiExecuting') : task?.status || status || t('creatorAiWaiting') }}</strong>
      <span v-if="status" class="muted">{{ status }}</span>
      <span v-if="task?.total_steps" class="muted">{{ task.completed_steps || 0 }} / {{ task.total_steps }}</span>
      <p v-if="actionError" class="error-text">{{ actionError }}</p>
    </div>

    <div v-if="diffVisible" class="creator-ai-diff" data-testid="creator-ai-diff">
      <strong>{{ t('creatorAiDiffTitle') }}</strong>
      <p v-for="row in diffRows" :key="row.path" class="creator-ai-diff-row">
        <NTag size="small" :type="row.status === 'removed' ? 'error' : row.status === 'added' ? 'success' : 'warning'">
          {{ t(DIFF_STATUS_KEYS[row.status]) }}
        </NTag>
        <code>{{ row.path }}</code>
        <span v-if="row.status === 'changed'" class="muted">+{{ row.added }} / -{{ row.removed }}</span>
      </p>
    </div>
    <p v-if="noChangeVisible" class="creator-ai-no-change" data-testid="creator-ai-no-change" role="status">{{ t('creatorAiNoFileChange') }}</p>

    <div class="creator-ai-chips">
      <button v-for="chipKey in wishChipKeys" :key="chipKey" type="button" class="creator-ai-chip" :disabled="streaming" @click="sendWish(t(chipKey))">
        {{ t(chipKey) }}
      </button>
    </div>

    <div class="creator-ai-input">
      <textarea
        v-model="wish"
        class="creator-ai-textarea"
        rows="2"
        :placeholder="t('creatorAiPlaceholder')"
        :data-testid="`creator-ai-input`"
        @keydown.enter.exact.prevent="sendWish()"
      />
      <div class="creator-ai-input-actions">
        <NButton v-if="streaming" size="small" secondary @click="stop">{{ t('creatorAiStop') }}</NButton>
        <NButton v-else size="small" type="primary" :disabled="!wish.trim()" data-testid="creator-ai-send" @click="sendWish()">
          {{ t('creatorAiSend') }}
        </NButton>
      </div>
    </div>
    <p class="muted creator-ai-note">{{ t('creatorAiBoundary') }}</p>
  </div>
</template>

<style scoped>
.creator-ai-panel { display: grid; gap: .7rem; }
.creator-ai-heading strong { font-size: .95rem; }
.creator-ai-transcript { display: grid; gap: .5rem; max-height: 260px; overflow: auto; padding: .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.creator-ai-message { display: grid; gap: .15rem; margin: 0; }
.creator-ai-role { font-size: .72rem; font-weight: 700; color: var(--df-text-muted); }
.creator-ai-message.is-user .creator-ai-role { color: var(--df-interactive-strong); }
.creator-ai-text { white-space: pre-wrap; font-size: .85rem; line-height: 1.5; }
.creator-ai-events { display: grid; gap: .3rem; padding: .55rem .7rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); font-size: .78rem; }
.creator-ai-events strong { font-size: .76rem; color: var(--df-text-muted); }
.creator-ai-events p { display: flex; align-items: center; gap: .4rem; margin: 0; color: var(--df-text-secondary); }
.creator-ai-event-dot { width: .4rem; height: .4rem; flex: 0 0 auto; border-radius: 50%; background: var(--df-interactive-strong); }
.creator-ai-approval { display: grid; gap: .45rem; padding: .65rem; border: 1px solid color-mix(in srgb, var(--df-warning) 55%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: color-mix(in srgb, var(--df-warning) 8%, var(--df-surface-1)); }
.creator-ai-approval-tool { color: var(--df-text-secondary); font-size: .82rem; font-weight: 700; }
.creator-ai-approval-details { color: var(--df-text-muted); font-size: .78rem; }
.creator-ai-approval-details summary { cursor: pointer; }
.creator-ai-args { max-height: 9rem; overflow: auto; margin: 0; padding: .5rem; border-radius: var(--df-radius-sm); background: var(--df-surface-2); font: .72rem/1.4 ui-monospace, Consolas, monospace; white-space: pre-wrap; }
.creator-ai-approval-actions { display: flex; gap: .5rem; }
.creator-ai-execution { display: grid; gap: .25rem; padding: .6rem .7rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); font-size: .8rem; }
.creator-ai-execution p { margin: 0; }
.creator-ai-diff { display: grid; gap: .3rem; padding: .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); font-size: .82rem; }
.creator-ai-diff-row { display: flex; align-items: center; gap: .5rem; margin: 0; }
.creator-ai-diff-row code { font-size: .78rem; }
.creator-ai-no-change { margin: 0; padding: .55rem .7rem; border: 1px solid color-mix(in srgb, var(--df-warning) 45%, var(--df-border-soft)); border-radius: var(--df-radius-md); color: var(--df-warning); background: color-mix(in srgb, var(--df-warning) 8%, transparent); font-size: .8rem; }
.creator-ai-chips { display: flex; flex-wrap: wrap; gap: .4rem; }
.creator-ai-chip { padding: .25rem .6rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text-muted); font-size: .78rem; cursor: pointer; }
.creator-ai-chip:hover { color: var(--df-text); border-color: var(--df-interactive); }
.creator-ai-input { display: grid; gap: .4rem; }
.creator-ai-textarea { width: 100%; box-sizing: border-box; resize: vertical; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); padding: .55rem; color: var(--df-text); background: var(--df-surface-2); font: .85rem/1.5 inherit; }
.creator-ai-input-actions { display: flex; justify-content: flex-end; }
.creator-ai-note { margin: 0; font-size: .74rem; }
</style>
