<script setup lang="ts">
import { computed, ref } from 'vue'
import { ApiError, errorMessage } from '@/api/client'
import {
  FOUNDRY_IMPORT_MAX_BYTES,
  importPortableScene,
  previewPortableSceneImport,
  type PortableSceneImportResponse,
  type PortableScenePreviewResponse,
} from '@/api/tacticalScene'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import { tacticalPortableCopy } from './copy'

const props = defineProps<{ gameKey: string }>()
const emit = defineEmits<{ close: []; imported: [] }>()
const { locale } = useLocale()
const copy = computed(() => tacticalPortableCopy(locale.value))

const definition = ref<Record<string, unknown> | null>(null)
const fileName = ref('')
const preview = ref<PortableScenePreviewResponse | null>(null)
const result = ref<PortableSceneImportResponse | null>(null)
const confirmNameCollision = ref(false)
const busy = ref<'idle' | 'preview' | 'import'>('idle')
const error = ref('')
const stale = ref(false)
const nameConflict = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const showNameConfirm = computed(() => Boolean(preview.value?.name_collision) || nameConflict.value)
const canImport = computed(() => (
  !!preview.value && !result.value && busy.value === 'idle'
  && (!preview.value.name_collision || confirmNameCollision.value)
))

async function selectFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || busy.value !== 'idle') return
  error.value = ''
  stale.value = false
  nameConflict.value = false
  preview.value = null
  result.value = null
  definition.value = null
  confirmNameCollision.value = false
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
  definition.value = parsed as Record<string, unknown>
}

async function runPreview() {
  if (!definition.value || busy.value !== 'idle' || !props.gameKey) return
  busy.value = 'preview'
  error.value = ''
  stale.value = false
  nameConflict.value = false
  try {
    const response = await previewPortableSceneImport(props.gameKey, definition.value)
    preview.value = response
    result.value = null
    confirmNameCollision.value = false
  } catch (cause) {
    preview.value = null
    error.value = errorMessage(cause)
  } finally {
    busy.value = 'idle'
  }
}

async function commit() {
  if (!canImport.value || !definition.value || !preview.value) return
  busy.value = 'import'
  error.value = ''
  try {
    const response = await importPortableScene(props.gameKey, definition.value, {
      confirmNameCollision: confirmNameCollision.value,
      expectedRevision: preview.value.revision,
    })
    result.value = response
    stale.value = false
    nameConflict.value = false
    emit('imported')
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 'tactical_scene_stale_revision') {
      stale.value = true
    } else if (cause instanceof ApiError && cause.code === 'scene_import_name_conflict') {
      nameConflict.value = true
      error.value = errorMessage(cause)
    } else {
      error.value = errorMessage(cause)
    }
  } finally {
    busy.value = 'idle'
  }
}

function startOver() {
  if (busy.value !== 'idle') return
  definition.value = null
  fileName.value = ''
  preview.value = null
  result.value = null
  confirmNameCollision.value = false
  error.value = ''
  stale.value = false
  nameConflict.value = false
  if (fileInput.value) fileInput.value.value = ''
}
</script>

<template>
  <Modal :title="copy.title" dialog-class="portable-import-dialog" @close="emit('close')">
    <section class="portable-import">
      <p class="portable-import-hint">{{ copy.hint }}</p>
      <p class="portable-import-limits">{{ copy.limits }}</p>

      <template v-if="!result">
        <label class="portable-import-field">
          {{ copy.chooseFile }}
          <input
            ref="fileInput"
            type="file"
            accept=".json,application/json"
            data-testid="portable-import-file"
            :disabled="busy !== 'idle'"
            @change="selectFile"
          >
        </label>
        <p v-if="fileName" class="portable-import-filename">{{ fileName }}</p>
        <button
          type="button"
          data-testid="portable-import-preview"
          :disabled="!definition || busy !== 'idle'"
          @click="runPreview"
        >{{ busy === 'preview' ? copy.previewing : copy.preview }}</button>
      </template>

      <p v-if="error" class="error-banner" role="alert" data-testid="portable-import-error">{{ error }}</p>
      <div v-if="stale" class="error-banner" role="alert" data-testid="portable-import-stale">
        <strong>{{ copy.staleTitle }}</strong> {{ copy.staleHint }}
        <button type="button" data-testid="portable-import-repreview" :disabled="busy !== 'idle'" @click="runPreview">{{ copy.rePreview }}</button>
      </div>

      <section v-if="preview && !result" class="portable-preview" data-testid="portable-import-preview-plan">
        <h3>{{ copy.previewTitle }}</h3>
        <p class="portable-preview-action" :class="`portable-action-${preview.action}`" data-testid="portable-preview-action">
          {{ preview.action === 'create' ? copy.actionCreate : copy.actionNoop }}
        </p>
        <p class="portable-import-limits">{{ copy.sceneId }}: <code>{{ preview.scene_id }}</code></p>
        <p class="portable-import-limits">{{ copy.revision }}: {{ preview.revision }}</p>
        <div v-if="showNameConfirm" class="portable-name-collision" data-testid="portable-name-collision">
          <p>{{ copy.nameCollision }}</p>
          <label class="portable-name-confirm">
            <input v-model="confirmNameCollision" type="checkbox" data-testid="portable-confirm-name">
            {{ copy.confirmNameCollision }}
          </label>
        </div>
        <button
          type="button"
          class="primary"
          data-testid="portable-import-commit"
          :disabled="!canImport"
          @click="commit"
        >{{ busy === 'import' ? copy.importing : copy.import }}</button>
      </section>

      <section v-if="result" class="portable-receipt" data-testid="portable-import-receipt">
        <p v-if="result.created" role="status" data-testid="portable-receipt-created">{{ copy.imported }}</p>
        <p v-else role="status" data-testid="portable-receipt-noop">{{ copy.noop }}</p>
        <p class="portable-import-limits">{{ copy.sceneId }}: <code>{{ result.scene_id }}</code></p>
        <p class="portable-import-limits">{{ copy.revision }}: {{ result.revision }}</p>
        <div class="portable-receipt-actions">
          <button type="button" data-testid="portable-import-startover" :disabled="busy !== 'idle'" @click="startOver">{{ copy.startOver }}</button>
        </div>
      </section>
    </section>
  </Modal>
</template>

<style scoped>
.portable-import { display: grid; gap: .7rem; min-width: 0; overflow-wrap: anywhere; }
.portable-import-hint { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.portable-import-limits { margin: 0; color: var(--df-text-muted); font-size: .78rem; }
.portable-import-field { display: grid; gap: .3rem; color: var(--df-text-secondary); font-size: .85rem; }
.portable-import-field input { width: 100%; min-width: 0; box-sizing: border-box; }
.portable-import-filename { margin: 0; color: var(--df-text-muted); font-size: .78rem; overflow-wrap: anywhere; }
.portable-preview { display: grid; gap: .6rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.portable-preview h3 { margin: 0; color: var(--df-accent-strong); font-size: 1rem; }
.portable-preview-action { margin: 0; font-size: .88rem; font-weight: 650; }
.portable-action-create { color: var(--df-accent-strong); }
.portable-action-noop { color: var(--df-text-secondary); }
.portable-name-collision { display: grid; gap: .4rem; padding: .7rem .8rem; border: 1px solid color-mix(in srgb, var(--df-warning, #d39b4d) 55%, var(--df-border-soft)); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-warning, #d39b4d) 9%, var(--df-surface-1)); }
.portable-name-collision p { margin: 0; color: var(--df-text-secondary); font-size: .82rem; }
.portable-name-confirm { display: flex; align-items: center; gap: .5rem; color: var(--df-text); font-size: .84rem; font-weight: 600; }
.portable-name-confirm input { width: auto; }
.portable-receipt { display: grid; gap: .6rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-success-strong, var(--df-accent)) 40%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.portable-receipt p { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.portable-receipt-actions { display: flex; gap: .6rem; flex-wrap: wrap; }
.error-banner { margin: 0; padding: .6rem .8rem; border: 1px solid var(--df-danger); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-danger) 12%, var(--df-surface-2)); color: var(--df-danger-strong); font-size: .84rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
button { min-height: 40px; padding: .4rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); color: var(--df-text); cursor: pointer; }
button.primary { border-color: var(--df-interactive); background: var(--df-interactive); color: var(--df-on-accent); }
button:disabled { opacity: .48; cursor: not-allowed; }
code { padding: 0 .25rem; border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text-secondary); font-size: .78rem; overflow-wrap: anywhere; }
</style>