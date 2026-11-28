<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { api, errorMessage } from '@/api/client'
import { useLocale } from '@/composables/useLocale'
import Modal from '@/components/ui/Modal.vue'
import ReviewPlanOverview from '@/features/content-review/ReviewPlanOverview.vue'
import AdventureGraphForm from './AdventureGraphForm.vue'
import ReferenceMapForm from './ReferenceMapForm.vue'
import ScriptAiOrganizer from './ScriptAiOrganizer.vue'
import { importCopy } from './importCopy'
import type { ScriptDraft, ScriptPreview } from './scriptTypes'
import type { MessageKey } from '@/i18n'

const props = defineProps<{ initialPreview?: ScriptPreview }>()
const emit = defineEmits<{ close: []; published: [id: string] }>()
const { locale, t } = useLocale()
const copy = computed(() => importCopy(locale.value))
const resourcePending = ref(false)
const file = ref<File | null>(null)
const draft = ref<ScriptDraft | null>(null)
const preview = ref<ScriptPreview | null>(null)
const validatedSnapshot = ref('')
const reviewed = ref(false)
const busy = ref(false)
const aiRunning = ref(false)
const error = ref('')
const publishStatusId = useId()
const snapshot = computed(() => JSON.stringify(draft.value))
const validPreview = computed(() => !resourcePending.value && validatedSnapshot.value === snapshot.value)
const canPublish = computed(() => !aiRunning.value && reviewed.value && validPreview.value && preview.value && !preview.value.blockers.length)
const messageKeys: Record<string, MessageKey> = {
  scriptDefaultGm: 'scriptDefaultGm', scriptMechanicsReference: 'scriptMechanicsReference',
  scriptUnknownMarker: 'scriptUnknownMarker', scriptUnreachableScenes: 'scriptUnreachableScenes',
  scriptUnresolvedReferences: 'scriptUnresolvedReferences',
}
const diagnostic = (code: string) => {
  if (code === 'storyDraftGmReview') return locale.value === 'zh-CN'
    ? '本局计划已复制为独立草稿。所有场景默认仅 GM 可见；公开前请移走描述中的转场条件和 GM 提示。不会复制本局进度或角色状态。'
    : 'The plan was copied to an independent draft. Scenes start GM-only; remove exit conditions and GM hints before making descriptions public. Live progress and character state are excluded.'
  if (code === 'storyDraftNpcReferencesOmitted') return locale.value === 'zh-CN'
    ? '核心人物引用没有随本局状态复制。请在草稿中另行创建或映射人物资料。'
    : 'Live NPC references were omitted. Create or map reusable NPC material in this draft.'
  const external = { externalImportReference: copy.value.reference, externalImportMedia: copy.value.media, externalImportScriptsOmitted: copy.value.scripts, externalImportSceneUnconverted: t('adventureSceneUnconverted'), externalImportSceneScriptsOmitted: t('adventureSceneScriptsOmitted'), pdfReviewLayout: copy.value.pdf } as Record<string, string>
  return messageKeys[code] ? t(messageKeys[code]) : external[code] || code
}
const publishReasons = computed(() => {
  const reasons: string[] = []
  if (resourcePending.value) reasons.push(copy.value.publishPending)
  else if (!validPreview.value) reasons.push(copy.value.publishValidate)
  if (validPreview.value) reasons.push(...(preview.value?.blockers || []).map(diagnostic))
  if (!reviewed.value) reasons.push(copy.value.publishReview)
  return reasons
})

function selectFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  draft.value = null; preview.value = null; reviewed.value = false; error.value = ''
}

async function run(validateOnly = false) {
  busy.value = true
  error.value = ''
  reviewed.value = false
  try {
    let result: ScriptPreview
    if (validateOnly && draft.value) {
      result = await api<ScriptPreview>('/adventures/script/review', { method: 'POST', body: JSON.stringify(draft.value) })
    } else {
      if (!file.value) return
      const body = new FormData(); body.append('file', file.value)
      result = await api<ScriptPreview>(`/adventures/script/preview?language=${encodeURIComponent(locale.value)}`, { method: 'POST', body })
    }
    if (!result.ok) throw new Error(t('operationFailed'))
    draft.value = result.draft
    preview.value = result
    validatedSnapshot.value = snapshot.value
  } catch (cause) {
    validatedSnapshot.value = ''
    error.value = errorMessage(cause)
  } finally { busy.value = false }
}

function keepReferenceAsProse(index: number) {
  draft.value?.references.splice(index, 1)
  reviewed.value = false
}

function setAiRunning(value: boolean) {
  aiRunning.value = value
  if (value) reviewed.value = false
}

function applyAiPreview(result: ScriptPreview) {
  draft.value = result.draft
  preview.value = result
  validatedSnapshot.value = snapshot.value
  reviewed.value = false
  error.value = ''
}

async function publish() {
  if (!canPublish.value || !draft.value || !preview.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await api<{ ok: boolean; adventure_id: string; error?: string }>('/adventures/script/commit', {
      method: 'POST', body: JSON.stringify({ ...draft.value, reviewed: true, plan_digest: preview.value.plan_digest }),
    })
    if (!result.ok) throw new Error(result.error || t('operationFailed'))
    emit('published', result.adventure_id)
  } catch (cause) { error.value = errorMessage(cause) }
  finally { busy.value = false }
}
if (props.initialPreview) applyAiPreview(props.initialPreview)
</script>

<template>
  <Modal :title="copy.title" dialog-class="adventure-editor-dialog" @close="emit('close')">
    <section class="script-review">
      <div class="script-import-intro">
        <p class="script-import-formats">{{ copy.formats }}</p>
        <div class="script-safety-callout" role="note">
          <strong>{{ t('scriptDefaultGm') }}</strong>
          <span>{{ t('scriptMechanicsReference') }}</span>
        </div>
      </div>
      <div class="script-file-picker">
        <label>{{ t('scriptFile') }}<input type="file" accept=".md,.txt,.json,.xml,.mod,.pdf" :disabled="busy || aiRunning" @change="selectFile"></label>
        <button type="button" class="primary" data-testid="script-preview" :disabled="!file || busy || aiRunning" @click="run()">{{ t('scriptPreview') }}</button>
      </div>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <template v-if="draft && preview">
        <ul v-if="preview.warnings.length" class="script-warning-list"><li v-for="warning in preview.warnings" :key="warning">{{ diagnostic(warning) }}</li></ul>
        <ScriptAiOrganizer :draft="draft" :disabled="busy || resourcePending" @running="setAiRunning" @apply="applyAiPreview" />
        <fieldset class="script-draft-editor" :disabled="busy || aiRunning">
          <AdventureGraphForm class="script-editor-section" :files="draft.files" @pending="resourcePending = $event" />
        <ReviewPlanOverview :plan="preview.commit_plan" />
        <ReferenceMapForm :draft="draft" @prose="keepReferenceAsProse" />
        <details class="script-identity"><summary>{{ t('scriptIdentity') }}</summary>
          <label>{{ t('directoryId') }}<input v-model="draft.directory_id"></label>
          <label>{{ t('canonicalAdventureId') }}<input v-model="draft.files['manifest.json'].adventure_id"></label>
        </details>
        </fieldset>
        <div :id="publishStatusId" class="script-publish-status" :class="{ 'is-ready': canPublish && !busy }" role="status" aria-live="polite" data-testid="script-publish-status">
          <p v-if="busy || aiRunning">{{ copy.publishBusy }}</p>
          <template v-else-if="publishReasons.length">
            <strong>{{ copy.publishNeeds }}</strong>
            <ul><li v-for="(reason, index) in publishReasons" :key="index">{{ reason }}</li></ul>
          </template>
          <p v-else>{{ copy.publishReady }}</p>
        </div>
        <div class="script-review-actions">
          <button type="button" data-testid="script-validate" :disabled="busy || aiRunning || resourcePending" :aria-describedby="publishStatusId" @click="run(true)">{{ t('scriptValidate') }}</button>
          <label class="script-reviewed"><input v-model="reviewed" type="checkbox" data-testid="script-reviewed" :disabled="busy || aiRunning">{{ t('scriptReviewConfirmed') }}</label>
          <button type="button" class="primary" data-testid="script-publish" :disabled="!canPublish || busy" :aria-describedby="publishStatusId" @click="publish">{{ t('scriptPublish') }}</button>
        </div>
      </template>
    </section>
  </Modal>
</template>

<style scoped>
.script-review { display: grid; gap: 1rem; min-width: 0; }
.script-draft-editor { display: grid; gap: 1rem; min-width: 0; margin: 0; padding: 0; border: 0; }
.script-import-intro { display: grid; gap: .65rem; padding: .95rem 1rem; border: 1px solid color-mix(in srgb, var(--df-accent) 30%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: linear-gradient(135deg, color-mix(in srgb, var(--df-accent) 9%, var(--df-surface-2)), var(--df-surface-1)); }
.script-import-formats { margin: 0; color: var(--df-text-secondary); line-height: 1.55; }
.script-safety-callout { display: grid; gap: .25rem; padding: .7rem .8rem; border-left: 3px solid var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 9%, var(--df-surface-1)); color: var(--df-text-secondary); font-size: .86rem; line-height: 1.5; }
.script-safety-callout strong { color: var(--df-accent-strong); }
.script-file-picker { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .8rem; align-items: end; padding: .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); }
.script-file-picker label { margin: 0; }
.script-warning-list { display: grid; gap: .4rem; margin: 0; padding: .75rem .9rem .75rem 2rem; border: 1px solid color-mix(in srgb, var(--df-accent) 28%, var(--df-border-soft)); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-accent) 7%, var(--df-surface-1)); color: var(--df-text-secondary); }
.script-review-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: .65rem; padding-top: .25rem; border-top: 1px solid var(--df-border-soft); }
.script-publish-status { padding: .75rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); color: var(--df-text-secondary); font-size: .86rem; line-height: 1.6; }
.script-publish-status strong { color: var(--df-accent-strong); }
.script-publish-status p { margin: 0; }
.script-publish-status ul { display: grid; gap: .25rem; margin: .35rem 0 0; padding-left: 1.25rem; }
.script-publish-status.is-ready { color: var(--df-accent-strong); }
.script-reference, .script-reviewed { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; }
.script-reference { justify-content: space-between; }
.script-reviewed { margin: 0; color: var(--df-text-secondary); }
.script-reviewed input { width: auto; }
.script-identity { padding: .75rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); }
.script-identity summary { color: var(--df-accent-strong); font-weight: 700; cursor: pointer; }
details label { display: grid; gap: .4rem; margin-top: .5rem; }
@media (max-width: 620px) { .script-file-picker { grid-template-columns: 1fr; } .script-review-actions { justify-content: stretch; } .script-review-actions > * { flex: 1 1 100%; } }
</style>
