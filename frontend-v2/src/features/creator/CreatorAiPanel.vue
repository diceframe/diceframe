<script setup lang="ts">
// W3 拍板 G：AI 许愿面板 MVP —— 「AI 提议 + 用户确认 + diff 展示」。
// 复用 useAssistant（/api/assistant/agent/* 全契约）与 creator 域工具；
// 工具写入后由本面板重新读取工程并给出逐文件差异摘要。
import { computed, ref, watch } from 'vue'
import { NButton, NTag } from 'naive-ui'
import { useAssistant } from '@/composables/useAssistant'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'
import { creatorApi, type CreatorProject } from '@/api/creator'

type DiffRow = { path: string; status: 'added' | 'removed' | 'changed'; added: number; removed: number }

const props = defineProps<{ project: CreatorProject | null }>()
const emit = defineEmits<{ 'files-changed': [] }>()
const { t, locale } = useLocale()
const { messages, streaming, send, stop, pendingApproval, confirmApproval, actionBusy } = useAssistant()

const wish = ref('')
const snapshot = ref<Array<[string, string]>>([])
const diffRows = ref<DiffRow[]>([])
const diffVisible = ref(false)

const WISH_CHIP_KEYS: MessageKey[] = [
  'creatorAiChipOutline',
  'creatorAiChipNpc',
  'creatorAiChipLore',
  'creatorAiChipBalance',
]
const DIFF_STATUS_KEYS: Record<DiffRow['status'], MessageKey> = {
  added: 'creatorAiDiffAdded',
  removed: 'creatorAiDiffRemoved',
  changed: 'creatorAiDiffChanged',
}

function bagLineDiff(oldText: string, newText: string): { added: number; removed: number } {
  // 行袋差（非 LCS）：足够回答「改了多少」，不承诺精确补丁语义。
  const oldLines = oldText.split('\n')
  const newLines = newText.split('\n')
  const bag = new Map<string, number>()
  for (const line of oldLines) bag.set(line, (bag.get(line) || 0) + 1)
  let removed = 0
  for (const line of oldLines) {
    const count = bag.get(line) || 0
    if (count > 0) bag.set(line, count - 1)
    else removed += 1
  }
  const newBag = new Map<string, number>()
  for (const line of newLines) newBag.set(line, (newBag.get(line) || 0) + 1)
  let added = 0
  for (const line of newLines) {
    const count = newBag.get(line) || 0
    if (count > 0) newBag.set(line, count - 1)
    else added += 1
  }
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
    if (rows.length) emit('files-changed')
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
  await snapshotProject()
  const contextNote = props.project
    ? `（当前工程：${props.project.name}，工程 id ${props.project.id}，当前 revision ${props.project.revision}。` +
      `修改工程文件请使用 creator_project_update 工具，携带 expected_revision=${props.project.revision}；改动前先 creator_project_read 了解现状。）`
    : ''
  await send(contextNote ? `${value}\n\n${contextNote}` : value, locale.value)
}

const displayMessages = computed(() => messages.value.filter(message => message.content.trim()))
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
          <span class="creator-ai-text">{{ message.content || t('creatorAiWorking') }}</span>
        </p>
      </template>
      <p v-if="!displayMessages.length" class="muted">{{ t('creatorAiEmpty') }}</p>
    </div>

    <div v-if="pendingApproval" class="creator-ai-approval" data-testid="creator-ai-approval">
      <strong>{{ pendingApproval.label || pendingApproval.tool }}</strong>
      <pre class="creator-ai-args">{{ JSON.stringify(pendingApproval.arguments, null, 1).slice(0, 1200) }}</pre>
      <div class="creator-ai-approval-actions">
        <NButton size="small" type="primary" :loading="actionBusy" :disabled="pendingApproval.expired" data-testid="creator-ai-approve" @click="confirmApproval(true)">
          {{ t('creatorAiApprove') }}
        </NButton>
        <NButton size="small" secondary :disabled="actionBusy" data-testid="creator-ai-reject" @click="confirmApproval(false)">
          {{ t('creatorAiReject') }}
        </NButton>
      </div>
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

    <div class="creator-ai-chips">
      <button v-for="chipKey in WISH_CHIP_KEYS" :key="chipKey" type="button" class="creator-ai-chip" :disabled="streaming" @click="sendWish(t(chipKey))">
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
.creator-ai-approval { display: grid; gap: .45rem; padding: .65rem; border: 1px solid color-mix(in srgb, var(--df-warning) 55%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: color-mix(in srgb, var(--df-warning) 8%, var(--df-surface-1)); }
.creator-ai-args { max-height: 9rem; overflow: auto; margin: 0; padding: .5rem; border-radius: var(--df-radius-sm); background: var(--df-surface-2); font: .72rem/1.4 ui-monospace, Consolas, monospace; white-space: pre-wrap; }
.creator-ai-approval-actions { display: flex; gap: .5rem; }
.creator-ai-diff { display: grid; gap: .3rem; padding: .6rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); font-size: .82rem; }
.creator-ai-diff-row { display: flex; align-items: center; gap: .5rem; margin: 0; }
.creator-ai-diff-row code { font-size: .78rem; }
.creator-ai-chips { display: flex; flex-wrap: wrap; gap: .4rem; }
.creator-ai-chip { padding: .25rem .6rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text-muted); font-size: .78rem; cursor: pointer; }
.creator-ai-chip:hover { color: var(--df-text); border-color: var(--df-interactive); }
.creator-ai-input { display: grid; gap: .4rem; }
.creator-ai-textarea { width: 100%; box-sizing: border-box; resize: vertical; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); padding: .55rem; color: var(--df-text); background: var(--df-surface-2); font: .85rem/1.5 inherit; }
.creator-ai-input-actions { display: flex; justify-content: flex-end; }
.creator-ai-note { margin: 0; font-size: .74rem; }
</style>
