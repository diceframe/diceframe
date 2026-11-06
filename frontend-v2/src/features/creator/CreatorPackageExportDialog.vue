<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, apiBlob, errorMessage } from '@/api/client'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import type { AdventureSummary, AdventuresResponse, RuleSummary, RulesResponse, WorldListResponse, WorldSummary } from '@/api/types'
import type { CreatorPackageExportMode } from './copy'
import { CONTENT_PACK_ID_PATTERN, contentPackSubmissionUrl } from './submission'

type Book = { id: string; name?: string; entry_count?: number }
const props = defineProps<{ mode: CreatorPackageExportMode }>()
const emit = defineEmits<{ close: [] }>()
const { locale, t } = useLocale()
const name = ref(''), packId = ref(''), version = ref('0.1.0'), description = ref('')
const worlds = ref<WorldSummary[]>([]), adventures = ref<AdventureSummary[]>([]), books = ref<Book[]>([]), rules = ref<RuleSummary[]>([])
const selectedWorld = ref(''), selectedRule = ref(''), selectedAdventures = ref<string[]>([]), selectedBooks = ref<string[]>([])
const busy = ref(false), loading = ref(true), error = ref('')
const isAdventureModule = computed(() => props.mode === 'adventure')
const exported = ref<{ blob: Blob; id: string; version: string } | null>(null)

const copy = computed(() => ({
  resourceTitle: t('creatorExportResourceTitle'), resourceIntro: t('creatorExportResourceIntro'),
  adventureTitle: t('creatorExportAdventureTitle'), adventureIntro: t('creatorExportAdventureIntro'),
  basic: t('creatorExportBasic'), id: t('creatorExportId'), name: t('creatorExportName'), version: t('creatorExportVersion'), description: t('creatorExportDescription'),
  resources: t('creatorExportResources'), extras: t('creatorExportExtras'), world: t('creatorExportWorld'), rule: t('creatorExportRule'),
  adventures: t('creatorExportAdventures'), books: t('creatorExportBooks'),
  resourceExport: t('creatorExportResourceAction'), adventureExport: t('creatorExportAdventureAction'), close: t('cancel'),
  loading: t('creatorExportLoading'), empty: t('creatorExportEmpty'),
  needIdName: t('creatorExportNeedIdName'), idInvalid: t('creatorExportIdInvalid'),
  resourceRequired: t('creatorExportResourceRequired'), adventureRequired: t('creatorExportAdventureRequired'),
  entries: t('creatorExportEntries'),
  preview: t('creatorExportPreview'), previewCount: t('creatorExportPreviewCount'), previewRuntimes: t('creatorExportPreviewRuntimes'),
  previewPolicies: t('creatorExportPreviewPolicies'), previewResources: t('creatorExportPreviewResources'), none: t('creatorExportNone'),
  resourceSummary: (world: number, rule: number, book: number) => [
    world && t('creatorExportSummaryWorld', { count: world }),
    rule && t('creatorExportSummaryRule', { count: rule }),
    book && t('creatorExportSummaryBook', { count: book }),
  ].filter(Boolean).join(' · '),
  fixedWorldMissing: (title: string, world: string) => t('creatorExportFixedWorldMissing', { title, world }),
  ready: t('creatorExportReady'), privacy: t('creatorExportPrivacy'),
  doneTitle: t('creatorExportDoneTitle'), doneText: (file: string) => t('creatorExportDoneText', { file }),
  submitTitle: t('contentPackSubmitTitle'),
  submitSteps: [t('creatorExportSubmitStepDownload'), t('contentPackSubmitStepForm'), t('contentPackSubmitStepReview')],
  submit: t('creatorExportSubmit'), done: t('creatorExportDone'),
}))
// The export language only picks the generated README and default locale;
// keep the existing zh/en split so other UI locales do not change packages.
const exportLanguage = computed(() => String(locale.value).startsWith('zh') ? 'zh-CN' : 'en')

function adventureSelectionValue(item: AdventureSummary): string {
  return JSON.stringify([item.source_kind || (item.custom ? 'user' : 'builtin'), item.source_id || '', item.adventure_id])
}

function selectedAdventureRefs(): Array<{ adventure_id: string; source_kind: string; source_id: string }> {
  return selectedAdventures.value.flatMap(value => {
    try {
      const [source_kind, source_id, adventure_id] = JSON.parse(value) as unknown[]
      if (typeof adventure_id !== 'string' || !adventure_id.trim()) return []
      return [{ adventure_id, source_kind: typeof source_kind === 'string' ? source_kind : '', source_id: typeof source_id === 'string' ? source_id : '' }]
    } catch { return [] }
  })
}

function worldId(world: WorldSummary): string {
  return String(world.id || world.world_id || '')
}

function worldName(id: string): string {
  const world = worlds.value.find(item => worldId(item) === id)
  return String(world?.name || world?.world_name || id)
}

function policyLabel(policy: string): string {
  if (policy === 'fixed') return t('adventurePolicyFixed')
  if (policy === 'agnostic') return t('adventurePolicyAgnostic')
  return t('adventurePolicyPortable')
}

const chosenAdventures = computed(() => {
  const chosen = new Set(selectedAdventures.value)
  return adventures.value.filter(item => chosen.has(adventureSelectionValue(item)))
})

// The preview only restates what the adventure summaries already declare.
// It never guesses whether the installing instance has a world; a fixed
// world that is not exported is reported as a risk, not as a failure.
// The listing's `compatibility` is not used: without a chosen ruleset and
// world it is always "incompatible", so it says nothing about the export.
const preview = computed(() => {
  const items = chosenAdventures.value
  const runtimes = [...new Set(items.flatMap(item => item.required_runtime?.id
    ? [`${item.required_runtime.id} ≥ ${item.required_runtime.minimum_version}`] : []))]
  const policies = [...new Set(items.map(item => policyLabel(item.world_policy)))]
  const resources = copy.value.resourceSummary(selectedWorld.value ? 1 : 0, selectedRule.value ? 1 : 0, selectedBooks.value.length)
  const warnings = items.flatMap(item => {
    const title = item.name || item.adventure_id
    const found: string[] = []
    if (item.world_policy === 'fixed' && item.recommended_world_id && item.recommended_world_id !== selectedWorld.value) {
      found.push(copy.value.fixedWorldMissing(title, worldName(item.recommended_world_id)))
    }
    return found
  })
  return { count: items.length, runtimes, policies, resources, warnings }
})

onMounted(async () => {
  try {
    const [w, a, b, r] = await Promise.all([
      api<WorldListResponse>('/worlds'),
      isAdventureModule.value
        ? api<AdventuresResponse>(`/adventures?language=${encodeURIComponent(locale.value)}`)
        : Promise.resolve({ ok: true, adventures: [] } as AdventuresResponse),
      api<{ books?: Book[] }>('/lorebooks?scope=all'),
      api<RulesResponse>(`/rules?language=${encodeURIComponent(locale.value)}`),
    ])
    worlds.value = w.worlds || []; adventures.value = a.adventures || []; books.value = b.books || []; rules.value = r.rules || []
  } catch (cause) { error.value = errorMessage(cause) } finally { loading.value = false }
})

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob), anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url)
}

const exportedFile = computed(() => exported.value ? `${exported.value.id}-${exported.value.version}.dfplugin` : '')

const submissionUrl = computed(() => exported.value ? contentPackSubmissionUrl(exported.value.id) : '')

function submitToHub() {
  if (!exported.value) return
  saveBlob(exported.value.blob, `${exported.value.id}-${exported.value.version}.zip`)
  window.open(submissionUrl.value, '_blank', 'noopener')
}

async function exportPack() {
  if (!packId.value.trim() || !name.value.trim()) { error.value = copy.value.needIdName; return }
  if (!CONTENT_PACK_ID_PATTERN.test(packId.value.trim())) { error.value = copy.value.idInvalid; return }
  if (isAdventureModule.value && !selectedAdventures.value.length) { error.value = copy.value.adventureRequired; return }
  if (!isAdventureModule.value && !selectedWorld.value && !selectedRule.value && !selectedBooks.value.length) { error.value = copy.value.resourceRequired; return }
  busy.value = true; error.value = ''
  try {
    const response = await apiBlob('/plugins/export', { method: 'POST', body: JSON.stringify({
      plugin_id: packId.value.trim(), name: name.value.trim(), version: version.value.trim() || '0.1.0', description: description.value.trim(),
      world_id: selectedWorld.value, rule_id: selectedRule.value, adventure_ids: [],
      // Only the adventure-module export sends adventures; the backend marks
      // a pack as adventure-module exactly when it contains adventures.
      adventure_refs: isAdventureModule.value ? selectedAdventureRefs() : [],
      lorebook_ids: selectedBooks.value, language: exportLanguage.value,
    }) })
    const blob = await response.blob()
    exported.value = { blob, id: packId.value.trim(), version: version.value.trim() || '0.1.0' }
    saveBlob(blob, exportedFile.value)
  } catch (cause) { error.value = errorMessage(cause) } finally { busy.value = false }
}
</script>

<template>
  <Modal :title="isAdventureModule ? copy.adventureTitle : copy.resourceTitle" dialog-class="creator-package-dialog" @close="emit('close')">
    <p class="creator-package-intro">{{ isAdventureModule ? copy.adventureIntro : copy.resourceIntro }}</p>
    <p v-if="loading" role="status">{{ copy.loading }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <template v-if="exported">
      <section class="creator-package-section" data-testid="creator-export-done">
        <h3>{{ copy.doneTitle }}</h3>
        <p class="creator-package-intro">{{ copy.doneText(exportedFile) }}</p>
      </section>
      <section class="creator-package-section">
        <h3>{{ copy.submitTitle }}</h3>
        <ol class="creator-package-steps"><li v-for="step in copy.submitSteps" :key="step">{{ step }}</li></ol>
      </section>
      <footer class="creator-package-actions"><button type="button" @click="emit('close')">{{ copy.done }}</button><button type="button" class="primary" data-testid="creator-export-submit-to-hub" @click="submitToHub">{{ copy.submit }}</button></footer>
    </template>
    <template v-else-if="!loading">
      <section class="creator-package-section"><h3>{{ copy.basic }}</h3><div class="creator-package-grid">
        <label><span>{{ copy.id }}</span><input v-model="packId" :placeholder="isAdventureModule ? 'my-adventure-module' : 'my-resource-pack'"></label>
        <label><span>{{ copy.name }}</span><input v-model="name"></label>
        <label><span>{{ copy.version }}</span><input v-model="version"></label>
        <label class="wide"><span>{{ copy.description }}</span><textarea v-model="description" rows="2"></textarea></label>
      </div></section>
      <section v-if="isAdventureModule" class="creator-package-section"><div class="creator-package-grid">
        <fieldset class="wide" data-testid="creator-export-adventures"><legend>{{ copy.adventures }}</legend><label v-for="item in adventures" :key="adventureSelectionValue(item)" class="creator-package-check"><input v-model="selectedAdventures" type="checkbox" :value="adventureSelectionValue(item)"><span>{{ item.name || item.adventure_id }} <small>{{ item.source_id || item.source_kind }}</small></span></label><small v-if="!adventures.length">{{ copy.empty }}</small></fieldset>
      </div></section>
      <section class="creator-package-section"><h3>{{ isAdventureModule ? copy.extras : copy.resources }}</h3><div class="creator-package-grid">
        <label><span>{{ copy.world }}</span><select v-model="selectedWorld"><option value="">—</option><option v-for="item in worlds" :key="worldId(item)" :value="worldId(item)">{{ item.name || item.world_name || item.id }}</option></select></label>
        <label><span>{{ copy.rule }}</span><select v-model="selectedRule"><option value="">—</option><option v-for="item in rules" :key="item.rule_id" :value="item.rule_id">{{ item.rule_name || item.rule_id }}</option></select></label>
        <fieldset class="wide"><legend>{{ copy.books }}</legend><label v-for="item in books" :key="item.id" class="creator-package-check"><input v-model="selectedBooks" type="checkbox" :value="item.id"><span>{{ item.name || item.id }} <small v-if="item.entry_count !== undefined">{{ item.entry_count }} {{ copy.entries }}</small></span></label><small v-if="!books.length">{{ copy.empty }}</small></fieldset>
      </div></section>
      <section v-if="isAdventureModule && preview.count" class="creator-package-section" data-testid="creator-export-preview">
        <h3>{{ copy.preview }}</h3>
        <dl class="creator-package-preview">
          <div><dt>{{ copy.previewCount }}</dt><dd>{{ preview.count }}</dd></div>
          <div><dt>{{ copy.previewRuntimes }}</dt><dd>{{ preview.runtimes.join('、') || copy.none }}</dd></div>
          <div><dt>{{ copy.previewPolicies }}</dt><dd>{{ preview.policies.join('、') }}</dd></div>
          <div><dt>{{ copy.previewResources }}</dt><dd>{{ preview.resources || copy.none }}</dd></div>
        </dl>
        <ul v-if="preview.warnings.length" class="creator-package-warnings">
          <li v-for="warning in preview.warnings" :key="warning" data-testid="creator-export-warning">{{ warning }}</li>
        </ul>
        <p v-else class="creator-package-ready">{{ copy.ready }}</p>
        <p class="creator-package-privacy">{{ copy.privacy }}</p>
      </section>
      <footer class="creator-package-actions"><button type="button" @click="emit('close')">{{ copy.close }}</button><button type="button" class="primary" data-testid="creator-export-submit" :disabled="busy" @click="exportPack">{{ busy ? '…' : `${isAdventureModule ? copy.adventureExport : copy.resourceExport} .dfplugin` }}</button></footer>
    </template>
  </Modal>
</template>

<style scoped>
.creator-package-intro{margin:0;color:var(--df-text-secondary);line-height:1.5}.creator-package-section{display:grid;gap:.8rem;padding:.9rem 0;border-bottom:1px solid var(--df-border-soft)}.creator-package-section h3{margin:0;color:var(--df-accent-strong);font-size:.95rem}.creator-package-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem}.creator-package-grid label,.creator-package-grid fieldset{display:grid;gap:.35rem;min-width:0}.creator-package-grid .wide{grid-column:1/-1}.creator-package-grid input,.creator-package-grid select,.creator-package-grid textarea{box-sizing:border-box;width:100%;min-width:0;padding:.55rem;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:var(--df-control-bg);color:var(--df-text)}fieldset{border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);padding:.65rem}.creator-package-check{display:flex!important;align-items:center;gap:.45rem}.creator-package-check input{width:auto}.creator-package-check small{color:var(--df-text-muted)}.creator-package-actions{display:flex;justify-content:flex-end;gap:.6rem;padding-top:1rem}
.creator-package-preview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem;margin:0}.creator-package-preview div{display:grid;gap:.2rem;min-width:0}.creator-package-preview dt{color:var(--df-text-muted);font-size:.8rem}.creator-package-preview dd{margin:0;overflow-wrap:anywhere}
.creator-package-steps{display:grid;gap:.45rem;margin:0;padding-left:1.2rem;color:var(--df-text-secondary);line-height:1.5}
.creator-package-warnings{display:grid;gap:.35rem;margin:0;padding-left:1.1rem;color:var(--df-warning)}.creator-package-ready{margin:0;color:var(--df-text-secondary)}.creator-package-privacy{margin:0;color:var(--df-text-muted);font-size:.85rem}
@media(max-width:620px){.creator-package-grid,.creator-package-preview{grid-template-columns:1fr}}
</style>
