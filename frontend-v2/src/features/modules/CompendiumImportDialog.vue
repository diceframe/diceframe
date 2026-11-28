<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ApiError, errorMessage } from '@/api/client'
import {
  CLIENT_REF_PATTERN,
  CONTENT_IMPORT_MAX_BYTES,
  commitContentImport,
  compendiumClientRef,
  compendiumSyncItem,
  contentInstallId,
  previewContentImport,
  type ContentSyncCommitResponse,
  type ContentSyncDecision,
  type ContentSyncPreviewResponse,
  type ContentSyncWarning,
} from '@/api/contentSync'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import { compendiumImportCopy } from './copy'

const emit = defineEmits<{ close: []; imported: [] }>()
const { locale } = useLocale()
const copy = computed(() => compendiumImportCopy(locale.value))

const documentText = ref('')
const fileName = ref('')
const packRef = ref('')
const preview = ref<ContentSyncPreviewResponse | null>(null)
const result = ref<ContentSyncCommitResponse | null>(null)
const decisions = reactive<Record<string, ContentSyncDecision | ''>>({})
const busy = ref<'idle' | 'preview' | 'commit'>('idle')
const error = ref('')
const stale = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const warnings = computed<ContentSyncWarning[]>(
  () => preview.value?.items.flatMap(item => item.warnings || []) || [],
)
const matchedItems = computed(() => preview.value?.items.filter(item => item.existing) || [])
const allDecided = computed(() => matchedItems.value.every(item => decisions[item.client_ref]))
const packRefValid = computed(() => CLIENT_REF_PATTERN.test(packRef.value))
const canPreview = computed(() => !!documentText.value && packRefValid.value && busy.value === 'idle')
const canCommit = computed(() => !!preview.value && allDecided.value && busy.value === 'idle' && !result.value)

const decisionLabels = computed<Record<ContentSyncDecision, string>>(() => ({
  update: copy.value.decisionUpdate,
  duplicate: copy.value.decisionDuplicate,
  skip: copy.value.decisionSkip,
}))

function actionLabel(action: string): string {
  const labels: Record<string, string> = {
    create: copy.value.actionCreate,
    update: copy.value.actionUpdate,
    unchanged: copy.value.actionUnchanged,
    unsupported: copy.value.actionUnsupported,
  }
  return labels[action] || action
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    created: copy.value.statusCreated,
    updated: copy.value.statusUpdated,
    duplicated: copy.value.statusDuplicated,
    skipped: copy.value.statusSkipped,
    unchanged: copy.value.statusUnchanged,
    error: copy.value.statusError,
    not_attempted: copy.value.statusNotAttempted,
  }
  return labels[status] || status
}

function warningLabel(code: string): string {
  return copy.value.warningLabels[code] || code
}

async function selectFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || busy.value !== 'idle') return
  error.value = ''
  stale.value = false
  preview.value = null
  result.value = null
  documentText.value = ''
  fileName.value = file.name
  packRef.value = compendiumClientRef(file.name)
  for (const key of Object.keys(decisions)) delete decisions[key]
  if (file.size > CONTENT_IMPORT_MAX_BYTES) {
    error.value = copy.value.fileTooLarge
    return
  }
  // The backend detects JSON / JSONL / YAML from the source text itself.
  documentText.value = await file.text()
}

async function runPreview() {
  if (!canPreview.value) return
  busy.value = 'preview'
  error.value = ''
  stale.value = false
  try {
    const response = await previewContentImport(
      { kind: 'device', id: contentInstallId() },
      [compendiumSyncItem(packRef.value, documentText.value)],
    )
    preview.value = response
    result.value = null
    for (const key of Object.keys(decisions)) delete decisions[key]
  } catch (cause) {
    preview.value = null
    error.value = errorMessage(cause)
  } finally {
    busy.value = 'idle'
  }
}

async function commit() {
  if (!canCommit.value || !preview.value) return
  busy.value = 'commit'
  error.value = ''
  try {
    const chosen: Record<string, ContentSyncDecision> = {}
    for (const item of preview.value.items) {
      const decision = decisions[item.client_ref]
      if (decision) chosen[item.client_ref] = decision
    }
    const response = await commitContentImport(
      { kind: 'device', id: contentInstallId() },
      [compendiumSyncItem(packRef.value, documentText.value)],
      preview.value.plan_digest,
      chosen,
    )
    result.value = response
    stale.value = false
    emit('imported')
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 'PLAN_STALE') {
      stale.value = true
    } else {
      error.value = errorMessage(cause)
    }
  } finally {
    busy.value = 'idle'
  }
}

function startOver() {
  if (busy.value !== 'idle') return
  documentText.value = ''
  fileName.value = ''
  packRef.value = ''
  preview.value = null
  result.value = null
  error.value = ''
  stale.value = false
  for (const key of Object.keys(decisions)) delete decisions[key]
  if (fileInput.value) fileInput.value.value = ''
}
</script>

<template>
  <Modal :title="copy.title" dialog-class="compendium-import-dialog" @close="emit('close')">
    <section class="compendium-import">
      <p class="compendium-import-hint">{{ copy.hint }}</p>
      <p class="compendium-import-limits">{{ copy.limits }}</p>

      <template v-if="!result">
        <label class="compendium-import-field">
          {{ copy.chooseFile }}
          <input
            ref="fileInput"
            type="file"
            accept=".json,.jsonl,.ndjson,.yaml,.yml"
            data-testid="compendium-import-file"
            :disabled="busy !== 'idle'"
            @change="selectFile"
          >
        </label>
        <p v-if="fileName" class="compendium-import-filename">{{ fileName }}</p>
        <label class="compendium-import-field">
          {{ copy.packRef }}
          <input
            v-model="packRef"
            type="text"
            data-testid="compendium-import-ref"
            :disabled="busy !== 'idle' || !!preview"
            maxlength="120"
          >
        </label>
        <p class="compendium-import-limits">{{ copy.packRefHint }}</p>
        <p v-if="packRef && !packRefValid" class="error-banner" role="alert" data-testid="compendium-ref-invalid">{{ copy.packRefInvalid }}</p>
        <button
          type="button"
          data-testid="compendium-import-preview"
          :disabled="!canPreview"
          @click="runPreview"
        >{{ busy === 'preview' ? copy.previewing : copy.preview }}</button>
      </template>

      <p v-if="error" class="error-banner" role="alert" data-testid="compendium-import-error">{{ error }}</p>
      <div v-if="stale" class="error-banner" role="alert" data-testid="compendium-import-stale">
        <strong>{{ copy.staleTitle }}</strong> {{ copy.staleHint }}
        <button type="button" data-testid="compendium-import-repreview" :disabled="busy !== 'idle'" @click="runPreview">{{ copy.rePreview }}</button>
      </div>

      <section v-if="preview && !result" class="compendium-plan" data-testid="compendium-import-plan">
        <h3>{{ copy.planTitle }}</h3>
        <p class="compendium-import-limits">{{ copy.planHint }}</p>
        <fieldset
          v-for="item in preview.items"
          :key="item.client_ref"
          class="compendium-plan-item"
          :disabled="busy !== 'idle'"
          data-testid="compendium-plan-item"
        >
          <legend>{{ item.existing?.name || item.client_ref }}</legend>
          <p class="compendium-plan-meta">
            <span class="compendium-action-badge" :class="`compendium-action-${item.action}`">{{ actionLabel(item.action) }}</span>
            <code>{{ item.client_ref }}</code>
          </p>
          <template v-if="item.existing">
            <p class="compendium-plan-meta">
              <span>{{ copy.existingName }}: {{ item.existing.canonical_id }}</span>
              <span v-if="item.existing.server_modified === true" class="compendium-server-modified">{{ copy.serverModified }}</span>
              <span v-else-if="item.existing.server_modified === null">{{ copy.serverUnknown }}</span>
            </p>
            <p class="compendium-plan-meta">
              <span>{{ copy.entriesAdd }} {{ item.existing.entries_add ?? 0 }}</span>
              <span>{{ copy.entriesUpdate }} {{ item.existing.entries_update ?? 0 }}</span>
              <span>{{ copy.entriesRemove }} {{ item.existing.entries_remove ?? 0 }}</span>
            </p>
            <label class="compendium-import-field">
              {{ copy.decisionLabel }}
              <select
                :value="decisions[item.client_ref] || ''"
                data-testid="compendium-decision"
                @change="decisions[item.client_ref] = ($event.target as HTMLSelectElement).value as ContentSyncDecision | ''"
              >
                <option value="" disabled>{{ copy.decisionPlaceholder }}</option>
                <option v-for="decision in item.allowed" :key="decision" :value="decision">{{ decisionLabels[decision] }}</option>
              </select>
            </label>
          </template>
          <p v-if="item.action === 'unsupported' && item.reason" class="compendium-plan-meta">{{ item.reason }}</p>
        </fieldset>
        <p v-if="!allDecided" class="compendium-import-limits" data-testid="compendium-decision-required">{{ copy.decisionRequired }}</p>

        <section class="compendium-report" data-testid="compendium-report">
          <h4>{{ copy.reportTitle }}</h4>
          <p class="compendium-import-limits">{{ copy.reportHint }}</p>
          <p v-if="!warnings.length" class="compendium-import-limits">{{ copy.noWarnings }}</p>
          <ul v-else class="compendium-warning-list">
            <li v-for="(warning, index) in warnings" :key="index" data-testid="compendium-warning">
              <strong>{{ warningLabel(warning.code) }}</strong>
              <span>{{ warning.message }}</span>
            </li>
          </ul>
        </section>

        <button
          type="button"
          class="primary"
          data-testid="compendium-import-commit"
          :disabled="!canCommit"
          @click="commit"
        >{{ busy === 'commit' ? copy.committing : copy.commit }}</button>
      </section>

      <section v-if="result" class="compendium-receipt" data-testid="compendium-import-receipt">
        <h3>{{ copy.receiptTitle }}</h3>
        <p v-if="result.ok" role="status" data-testid="compendium-receipt-ok">{{ copy.receiptCreated }}</p>
        <p v-else class="error-banner" role="alert" data-testid="compendium-receipt-partial">{{ copy.receiptPartial }}</p>
        <ul class="compendium-receipt-items">
          <li v-for="row in result.items" :key="row.client_ref" data-testid="compendium-receipt-item">
            <span class="compendium-receipt-status" :class="{ 'is-error': row.status === 'error' }">{{ statusLabel(row.status) }}</span>
            <code>{{ row.canonical_id || row.client_ref }}</code>
            <span v-if="row.error" class="compendium-receipt-error">{{ row.error }}</span>
          </li>
        </ul>
        <div class="compendium-receipt-actions">
          <button type="button" data-testid="compendium-import-startover" :disabled="busy !== 'idle'" @click="startOver">{{ copy.startOver }}</button>
        </div>
      </section>
    </section>
  </Modal>
</template>

<style scoped>
.compendium-import { display: grid; gap: .7rem; min-width: 0; overflow-wrap: anywhere; }
.compendium-import-hint { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.compendium-import-limits { margin: 0; color: var(--df-text-muted); font-size: .78rem; }
.compendium-import-field { display: grid; gap: .3rem; color: var(--df-text-secondary); font-size: .85rem; }
.compendium-import-field input, .compendium-import-field select { width: 100%; min-width: 0; box-sizing: border-box; }
.compendium-import-filename { margin: 0; color: var(--df-text-muted); font-size: .78rem; overflow-wrap: anywhere; }
.compendium-plan { display: grid; gap: .7rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.compendium-plan h3, .compendium-receipt h3 { margin: 0; color: var(--df-accent-strong); font-size: 1rem; }
.compendium-plan-item { display: grid; gap: .6rem; min-width: 0; margin: 0; padding: .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); }
.compendium-plan-item legend { padding: 0 .35rem; color: var(--df-text); font-weight: 650; overflow-wrap: anywhere; }
.compendium-plan-meta { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; margin: 0; color: var(--df-text-muted); font-size: .78rem; }
.compendium-action-badge { padding: .2rem .5rem; border-radius: 999px; border: 1px solid var(--df-border-soft); font-size: .72rem; font-weight: 700; }
.compendium-action-create { color: var(--df-text-secondary); }
.compendium-action-update, .compendium-action-unchanged { border-color: var(--df-accent-strong); color: var(--df-accent-strong); }
.compendium-action-unsupported { border-color: var(--df-danger); color: var(--df-danger-strong); }
.compendium-server-modified { color: var(--df-warn-strong, #c97b22); font-weight: 600; }
.compendium-report { display: grid; gap: .4rem; padding: .7rem .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); }
.compendium-report h4 { margin: 0; color: var(--df-accent-strong); font-size: .9rem; }
.compendium-warning-list { display: grid; gap: .4rem; margin: 0; padding-left: 1.2rem; color: var(--df-text-secondary); font-size: .8rem; }
.compendium-warning-list li { display: grid; gap: .15rem; }
.compendium-warning-list strong { color: var(--df-text); font-size: .78rem; }
.compendium-receipt { display: grid; gap: .6rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.compendium-receipt p { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.compendium-receipt-items { display: grid; gap: .45rem; margin: 0; padding-left: 1.2rem; color: var(--df-text-secondary); font-size: .85rem; }
.compendium-receipt-items li { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.compendium-receipt-status { display: inline-flex; padding: .2rem .45rem; border-radius: 999px; color: var(--df-on-accent); background: var(--df-interactive); font-size: .72rem; font-weight: 750; }
.compendium-receipt-status.is-error { background: var(--df-danger); }
.compendium-receipt-error { color: var(--df-danger-strong); font-size: .78rem; }
.compendium-receipt-actions { display: flex; gap: .6rem; flex-wrap: wrap; }
.error-banner { margin: 0; padding: .6rem .8rem; border: 1px solid var(--df-danger); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-danger) 12%, var(--df-surface-2)); color: var(--df-danger-strong); font-size: .84rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
button { min-height: 40px; padding: .4rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); color: var(--df-text); cursor: pointer; }
button.primary { border-color: var(--df-interactive); background: var(--df-interactive); color: var(--df-on-accent); }
button:disabled { opacity: .48; cursor: not-allowed; }
code { padding: 0 .25rem; border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text-secondary); font-size: .78rem; overflow-wrap: anywhere; }
</style>