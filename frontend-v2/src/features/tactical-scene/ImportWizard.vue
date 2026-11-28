<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ApiError, errorMessage } from '@/api/client'
import {
  FOUNDRY_IMPORT_MAX_BYTES,
  commitFoundrySceneImport,
  previewFoundrySceneImport,
  type FoundryCommitResponse,
  type FoundryImportAction,
  type FoundryImportPlanItem,
  type FoundryImportPolicy,
  type FoundryPreviewResponse,
} from '@/api/tacticalScene'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import ConversionReportPanel from './ConversionReportPanel.vue'
import { tacticalImportCopy } from './copy'

const props = defineProps<{ gameKey: string }>()
const emit = defineEmits<{ close: []; imported: [] }>()
const { locale } = useLocale()
const copy = computed(() => tacticalImportCopy(locale.value))

const scenePayload = ref<Record<string, unknown> | null>(null)
const fileName = ref('')
const sourceVersion = ref('v14')
const preview = ref<FoundryPreviewResponse | null>(null)
const result = ref<FoundryCommitResponse | null>(null)
const actions = reactive<Record<string, FoundryImportAction>>({})
const operationId = ref('')
const busy = ref<'idle' | 'preview' | 'commit'>('idle')
const error = ref('')
const stale = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const actionLabels = computed<Record<FoundryImportAction, string>>(() => ({
  create: copy.value.actionCreate,
  update: copy.value.actionUpdate,
  duplicate: copy.value.actionDuplicate,
  skip: copy.value.actionSkip,
}))

function optionsFor(item: FoundryImportPlanItem): FoundryImportAction[] {
  // The backend rejects create on an existing scene and update on a new one;
  // only offer the policies that are valid for the conflict marker.
  return item.conflict === 'existing' ? ['update', 'duplicate', 'skip'] : ['create', 'duplicate', 'skip']
}

function defaultAction(item: FoundryImportPlanItem): FoundryImportAction {
  return item.conflict === 'existing' ? 'update' : 'create'
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    created: copy.value.statusCreated,
    updated: copy.value.statusUpdated,
    duplicated: copy.value.statusDuplicated,
    skipped: copy.value.statusSkipped,
  }
  return labels[status] || status
}

async function selectFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || busy.value !== 'idle') return
  error.value = ''
  stale.value = false
  preview.value = null
  result.value = null
  scenePayload.value = null
  fileName.value = file.name
  if (file.size > FOUNDRY_IMPORT_MAX_BYTES) {
    error.value = copy.value.fileTooLarge
    return
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(await file.text())
  } catch {
    error.value = copy.value.invalidJson
    return
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    error.value = copy.value.invalidJson
    return
  }
  scenePayload.value = parsed as Record<string, unknown>
}

async function runPreview() {
  if (!scenePayload.value || busy.value !== 'idle' || !props.gameKey) return
  busy.value = 'preview'
  error.value = ''
  stale.value = false
  try {
    const response = await previewFoundrySceneImport(props.gameKey, scenePayload.value, sourceVersion.value)
    preview.value = response
    result.value = null
    for (const key of Object.keys(actions)) delete actions[key]
    for (const item of response.plan.items) actions[item.canonical_id] = defaultAction(item)
    // A fresh preview is a new plan: a new operation id keeps commit retries
    // idempotent without replaying an older plan's receipt.
    operationId.value = `import-${crypto.randomUUID()}`
  } catch (cause) {
    preview.value = null
    error.value = errorMessage(cause)
  } finally {
    busy.value = 'idle'
  }
}

function buildPolicies(): Record<string, FoundryImportPolicy> {
  const policies: Record<string, FoundryImportPolicy> = {}
  for (const item of preview.value?.plan.items || []) {
    const action = actions[item.canonical_id] || defaultAction(item)
    policies[item.canonical_id] = action === 'update'
      ? { action, expected_revision: item.existing_revision ?? 0 }
      : { action }
  }
  return policies
}

async function commit() {
  if (!preview.value || busy.value !== 'idle' || !props.gameKey) return
  busy.value = 'commit'
  error.value = ''
  try {
    const response = await commitFoundrySceneImport(
      props.gameKey, preview.value.plan, preview.value.plan_digest, buildPolicies(), operationId.value,
    )
    result.value = response
    stale.value = false
    emit('imported')
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 'foundry_import_stale') {
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
  scenePayload.value = null
  fileName.value = ''
  preview.value = null
  result.value = null
  error.value = ''
  stale.value = false
  for (const key of Object.keys(actions)) delete actions[key]
  if (fileInput.value) fileInput.value.value = ''
}
</script>

<template>
  <Modal :title="copy.title" dialog-class="foundry-import-dialog" @close="emit('close')">
    <section class="foundry-import">
      <p class="foundry-import-hint">{{ copy.hint }}</p>
      <p class="foundry-import-limits">{{ copy.limits }}</p>

      <template v-if="!result">
        <label class="foundry-import-field">
          {{ copy.chooseFile }}
          <input
            ref="fileInput"
            type="file"
            accept=".json,application/json"
            data-testid="foundry-import-file"
            :disabled="busy !== 'idle'"
            @change="selectFile"
          >
        </label>
        <p v-if="fileName" class="foundry-import-filename">{{ fileName }}</p>
        <label class="foundry-import-field">
          {{ copy.sourceVersion }}
          <select v-model="sourceVersion" data-testid="foundry-import-version" :disabled="busy !== 'idle' || !!preview">
            <option value="v14">v14</option>
            <option value="v13">v13</option>
          </select>
        </label>
        <button
          type="button"
          data-testid="foundry-import-preview"
          :disabled="!scenePayload || busy !== 'idle'"
          @click="runPreview"
        >{{ busy === 'preview' ? copy.previewing : copy.preview }}</button>
      </template>

      <p v-if="error" class="error-banner" role="alert" data-testid="foundry-import-error">{{ error }}</p>
      <div v-if="stale" class="error-banner" role="alert" data-testid="foundry-import-stale">
        <strong>{{ copy.staleTitle }}</strong> {{ copy.staleHint }}
        <button type="button" data-testid="foundry-import-repreview" :disabled="busy !== 'idle'" @click="runPreview">{{ copy.rePreview }}</button>
      </div>

      <section v-if="preview && !result" class="foundry-plan" data-testid="foundry-import-plan">
        <h3>{{ copy.planTitle }}</h3>
        <fieldset
          v-for="item in preview.plan.items"
          :key="item.canonical_id"
          class="foundry-plan-item"
          :disabled="busy !== 'idle'"
          data-testid="foundry-plan-item"
        >
          <legend>{{ item.display_name || item.canonical_id }}</legend>
          <p class="foundry-plan-meta">
            <span class="foundry-conflict-badge" :class="`foundry-conflict-${item.conflict}`">
              {{ item.conflict === 'existing' ? copy.conflictExisting : copy.conflictNew }}
            </span>
            <span v-if="item.conflict === 'existing'">{{ copy.existingRevision }} {{ item.existing_revision }}</span>
          </p>
          <label class="foundry-import-field">
            {{ copy.policy }}
            <select
              :value="actions[item.canonical_id]"
              data-testid="foundry-import-policy"
              @change="actions[item.canonical_id] = ($event.target as HTMLSelectElement).value as FoundryImportAction"
            >
              <option v-for="action in optionsFor(item)" :key="action" :value="action">{{ actionLabels[action] }}</option>
            </select>
          </label>
          <ConversionReportPanel :report="item.report" :scrubbed-fields="preview.plan.scrubbed_fields" />
        </fieldset>
        <button
          type="button"
          class="primary"
          data-testid="foundry-import-commit"
          :disabled="busy !== 'idle'"
          @click="commit"
        >{{ busy === 'commit' ? copy.committing : copy.commit }}</button>
      </section>

      <section v-if="result" class="foundry-receipt" data-testid="foundry-import-receipt">
        <h3>{{ copy.receiptTitle }}</h3>
        <p v-if="result.idempotent" role="status" data-testid="foundry-import-idempotent">{{ copy.receiptIdempotent }}</p>
        <p v-else role="status">{{ copy.imported }}</p>
        <p class="foundry-receipt-meta">{{ copy.receiptOperation }}: <code>{{ result.operation_id }}</code></p>
        <p class="foundry-receipt-meta">{{ copy.receiptRevision }}: {{ result.receipt.revision }}</p>
        <ul class="foundry-receipt-items">
          <li v-for="row in result.receipt.items" :key="row.canonical_id" data-testid="foundry-receipt-item">
            <span class="foundry-receipt-action">{{ actionLabels[row.action] }}</span>
            {{ row.canonical_id }} · {{ statusLabel(row.status) }}
          </li>
        </ul>
        <div class="foundry-receipt-actions">
          <button type="button" data-testid="foundry-import-recommit" :disabled="busy !== 'idle'" @click="commit">{{ copy.retryCommit }}</button>
          <button type="button" data-testid="foundry-import-startover" :disabled="busy !== 'idle'" @click="startOver">{{ copy.startOver }}</button>
        </div>
      </section>
    </section>
  </Modal>
</template>

<style scoped>
.foundry-import { display: grid; gap: .7rem; min-width: 0; overflow-wrap: anywhere; }
.foundry-import-hint { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.foundry-import-limits { margin: 0; color: var(--df-text-muted); font-size: .78rem; }
.foundry-import-field { display: grid; gap: .3rem; color: var(--df-text-secondary); font-size: .85rem; }
.foundry-import-field input, .foundry-import-field select { width: 100%; min-width: 0; box-sizing: border-box; }
.foundry-import-filename { margin: 0; color: var(--df-text-muted); font-size: .78rem; overflow-wrap: anywhere; }
.foundry-plan { display: grid; gap: .7rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.foundry-plan h3, .foundry-receipt h3 { margin: 0; color: var(--df-accent-strong); font-size: 1rem; }
.foundry-plan-item { display: grid; gap: .6rem; min-width: 0; margin: 0; padding: .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); }
.foundry-plan-item legend { padding: 0 .35rem; color: var(--df-text); font-weight: 650; overflow-wrap: anywhere; }
.foundry-plan-meta { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; margin: 0; color: var(--df-text-muted); font-size: .78rem; }
.foundry-conflict-badge { padding: .2rem .5rem; border-radius: 999px; border: 1px solid var(--df-border-soft); font-size: .72rem; font-weight: 700; }
.foundry-conflict-existing { border-color: var(--df-accent-strong); color: var(--df-accent-strong); }
.foundry-conflict-new { color: var(--df-text-secondary); }
.foundry-receipt { display: grid; gap: .6rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.foundry-receipt p { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.foundry-receipt-meta { color: var(--df-text-muted); font-size: .78rem; }
.foundry-receipt-items { display: grid; gap: .45rem; margin: 0; padding-left: 1.2rem; color: var(--df-text-secondary); font-size: .85rem; }
.foundry-receipt-action { display: inline-flex; margin-right: .35rem; padding: .2rem .45rem; border-radius: 999px; color: var(--df-on-accent); background: var(--df-interactive); font-size: .72rem; font-weight: 750; }
.foundry-receipt-actions { display: flex; gap: .6rem; flex-wrap: wrap; }
code { padding: 0 .25rem; border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text-secondary); font-size: .78rem; overflow-wrap: anywhere; }
</style>