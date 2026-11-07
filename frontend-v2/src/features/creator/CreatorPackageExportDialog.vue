<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { api, apiBlob, errorMessage } from '@/api/client'
import { uploadMapBackground } from '@/api/mapBackgrounds'
import { uploadSceneImage } from '@/api/sceneImages'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import { fileToBase64 } from '@/utils/characterImport'
import type { AdventureSummary, AdventuresResponse, CharacterCard, RuleSummary, RulesResponse, WorldListResponse, WorldSummary } from '@/api/types'
import type { CreatorPackageExportMode } from './copy'
import { CONTENT_PACK_ID_PATTERN, contentPackSubmissionUrl, repositorySubmissionUrl } from './submission'

type Book = { id: string; name?: string; entry_count?: number }
// `package` is the installable .dfplugin; `source` is the flat zip an author
// pushes to their own repository (the backend's `flat` option).
type ExportFormat = 'package' | 'source'
const MAP_ICON_LIMIT = 128
const props = defineProps<{ mode: CreatorPackageExportMode }>()
const emit = defineEmits<{ close: [] }>()
const { locale, t } = useLocale()
const toast = useToast()
const name = ref(''), packId = ref(''), version = ref('0.1.0'), description = ref('')
const worlds = ref<WorldSummary[]>([]), adventures = ref<AdventureSummary[]>([]), books = ref<Book[]>([]), rules = ref<RuleSummary[]>([]), cards = ref<CharacterCard[]>([])
const selectedWorld = ref(''), selectedRule = ref(''), selectedAdventures = ref<string[]>([]), selectedBooks = ref<string[]>([]), selectedCards = ref<string[]>([])
// Same defaults as the backend, which packages existing portraits, covers
// and the world map unless told otherwise; the switches only make that visible.
const includePortraits = ref(true), includeSceneImages = ref(true), includeMap = ref(true)
const worldCover = ref<File | null>(null), ruleCover = ref<File | null>(null), mapBackground = ref<File | null>(null), mapIcons = ref<File[]>([])
const format = ref<ExportFormat>('package')
const cover = ref<File | null>(null)
const coverPreview = ref('')
const busy = ref(false), loading = ref(true), error = ref('')
const errorRef = ref<HTMLElement | null>(null)

// 导出按钮在弹窗底部，错误横幅在顶部：只写 error 的话，用户点了「下一步」
// 会觉得毫无反应。所以失败时同时弹 toast，并把弹窗滚回提示的位置。
function fail(message: string) {
  error.value = message
  if (message) toast.error(message)
  void nextTick(() => {
    errorRef.value?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  })
}
const isAdventureModule = computed(() => props.mode === 'adventure')
const exported = ref<{ blob: Blob; id: string; version: string; source: boolean } | null>(null)

const copy = computed(() => ({
  resourceTitle: t('creatorExportResourceTitle'), resourceIntro: t('creatorExportResourceIntro'),
  adventureTitle: t('creatorExportAdventureTitle'), adventureIntro: t('creatorExportAdventureIntro'),
  basic: t('creatorExportBasic'), id: t('creatorExportId'), name: t('creatorExportName'), version: t('creatorExportVersion'), description: t('creatorExportDescription'),
  resources: t('creatorExportResources'), extras: t('creatorExportExtras'), world: t('creatorExportWorld'), rule: t('creatorExportRule'),
  adventures: t('creatorExportAdventures'), books: t('creatorExportBooks'), cards: t('creatorExportCards'),
  resourceExport: t('creatorExportResourceAction'), adventureExport: t('creatorExportAdventureAction'), close: t('cancel'),
  loading: t('creatorExportLoading'), empty: t('creatorExportEmpty'),
  needIdName: t('creatorExportNeedIdName'), idInvalid: t('creatorExportIdInvalid'),
  resourceRequired: t('creatorExportResourceRequired'), adventureRequired: t('creatorExportAdventureRequired'),
  entries: t('creatorExportEntries'),
  media: t('creatorExportMedia'), mediaIntro: t('creatorExportMediaIntro'),
  portraits: t('includeContentPackPortraits'), portraitsHint: t('includeContentPackPortraitsHint'),
  sceneImages: t('includeContentPackSceneImages'), sceneImagesHint: t('includeContentPackSceneImagesHint'),
  worldCover: t('worldSceneImage'), worldCoverHint: t('worldSceneImageHint'), ruleCover: t('ruleSceneImage'), ruleCoverHint: t('ruleSceneImageHint'),
  map: t('includeContentPackMap'), mapHint: t('includeContentPackMapHint'), mapNeedsWorld: t('creatorExportMapNeedsWorld'),
  mapBackground: t('contentPackMapBackground'), mapBackgroundHint: t('contentPackMapBackgroundHint'),
  mapIcons: t('contentPackMapIcons'), mapIconsHint: t('contentPackMapIconsHint'),
  mapIconsSelected: (count: number) => t('contentPackMapIconsSelected', { count }), mapIconLimit: t('exportMapIconLimit'),
  format: t('creatorExportFormat'),
  formatPackage: t('creatorExportFormatPackage'), formatPackageHint: t('creatorExportFormatPackageHint'),
  formatSource: t('creatorExportFormatSource'), formatSourceHint: t('creatorExportFormatSourceHint'),
  cover: t('creatorExportCover'), coverHint: t('creatorExportCoverHint'),
  coverPick: t('creatorExportCoverPick'), coverClear: t('creatorExportCoverClear'), coverInvalid: t('creatorExportCoverInvalid'),
  preview: t('creatorExportPreview'), previewCount: t('creatorExportPreviewCount'), previewRuntimes: t('creatorExportPreviewRuntimes'),
  previewPolicies: t('creatorExportPreviewPolicies'), previewResources: t('creatorExportPreviewResources'), none: t('creatorExportNone'),
  resourceSummary: (world: number, rule: number, book: number, card: number) => [
    world && t('creatorExportSummaryWorld', { count: world }),
    rule && t('creatorExportSummaryRule', { count: rule }),
    book && t('creatorExportSummaryBook', { count: book }),
    card && t('creatorExportSummaryCard', { count: card }),
  ].filter(Boolean).join(' · '),
  fixedWorldMissing: (title: string, world: string) => t('creatorExportFixedWorldMissing', { title, world }),
  ready: t('creatorExportReady'), privacy: t('creatorExportPrivacy'),
  doneTitle: t('creatorExportDoneTitle'), doneText: (file: string) => t('creatorExportDoneText', { file }),
  submitTitle: t('contentPackSubmitTitle'),
  submitSteps: [t('creatorExportSubmitStepDownload'), t('contentPackSubmitStepForm'), t('contentPackSubmitStepReview')],
  submit: t('creatorExportSubmit'), done: t('creatorExportDone'),
  sourceDoneText: (file: string) => t('creatorExportSourceDoneText', { file }),
  sourceTitle: t('creatorExportSourceTitle'),
  sourceSteps: [t('creatorExportSourceStepRepo'), t('creatorExportSourceStepRelease'), t('creatorExportSourceStepRegister')],
  sourceRegister: t('creatorExportSourceRegister'),
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

// The backend matches character cards by `id` only.
const cardOptions = computed(() => cards.value.flatMap(card => {
  const id = String(card.id || '')
  return id ? [{ id, label: String(card.character_name || id) }] : []
}))

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
// 两个模式共用的导出预览：先给汇总，再把「具体打包了哪些东西」列出来。
// 资源包以前完全没有预览，只能靠心里记着自己勾了什么。
const preview = computed(() => {
  const items = chosenAdventures.value
  const runtimes = [...new Set(items.flatMap(item => item.required_runtime?.id
    ? [`${item.required_runtime.id} ≥ ${item.required_runtime.minimum_version}`] : []))]
  const policies = [...new Set(items.map(item => policyLabel(item.world_policy)))]
  const resources = copy.value.resourceSummary(selectedWorld.value ? 1 : 0, selectedRule.value ? 1 : 0, selectedBooks.value.length, selectedCards.value.length)
  const warnings = items.flatMap(item => {
    const title = item.name || item.adventure_id
    const found: string[] = []
    if (item.world_policy === 'fixed' && item.recommended_world_id && item.recommended_world_id !== selectedWorld.value) {
      found.push(copy.value.fixedWorldMissing(title, worldName(item.recommended_world_id)))
    }
    return found
  })
  const nameList = (names: string[], total: number) => {
    const shown = names.slice(0, 3).join('、')
    return total > 3 ? `${shown} +${total - 3}` : shown
  }
  const bookNames = books.value.filter(book => selectedBooks.value.includes(book.id)).map(book => book.name || book.id)
  const cardNames = cardOptions.value.filter(card => selectedCards.value.includes(card.id)).map(card => card.label)
  const contents = [
    selectedWorld.value && { label: copy.value.world, value: worldName(selectedWorld.value) },
    selectedRule.value && { label: copy.value.rule, value: rules.value.find(item => item.rule_id === selectedRule.value)?.rule_name || selectedRule.value },
    bookNames.length > 0 && { label: copy.value.books, value: nameList(bookNames, bookNames.length) },
    cardNames.length > 0 && { label: copy.value.cards, value: nameList(cardNames, cardNames.length) },
  ].filter(Boolean) as Array<{ label: string; value: string }>
  return { count: items.length, runtimes, policies, resources, warnings, contents, hasContents: contents.length > 0 || items.length > 0 }
})

onMounted(async () => {
  try {
    const [w, a, b, r, c] = await Promise.all([
      api<WorldListResponse>('/worlds'),
      isAdventureModule.value
        ? api<AdventuresResponse>(`/adventures?language=${encodeURIComponent(locale.value)}`)
        : Promise.resolve({ ok: true, adventures: [] } as AdventuresResponse),
      api<{ books?: Book[] }>('/lorebooks?scope=all'),
      api<RulesResponse>(`/rules?language=${encodeURIComponent(locale.value)}`),
      api<{ cards?: CharacterCard[] }>('/character-cards'),
    ])
    worlds.value = w.worlds || []; adventures.value = a.adventures || []; books.value = b.books || []; rules.value = r.rules || []; cards.value = c.cards || []
  } catch (cause) { fail(errorMessage(cause)) } finally { loading.value = false }
})

function pickFile(event: Event): File | null {
  return (event.target as HTMLInputElement).files?.[0] || null
}

function pickFiles(event: Event): File[] {
  return Array.from((event.target as HTMLInputElement).files || [])
}

// 封面随包分发、安装后由本机接口读取；这里只挡类型和体积，尺寸交给校验器。
const COVER_LIMIT_BYTES = 512 * 1024
const COVER_TYPES = ['image/webp', 'image/png', 'image/jpeg']

function setCoverPreview(file: File | null) {
  if (coverPreview.value) URL.revokeObjectURL(coverPreview.value)
  coverPreview.value = file ? URL.createObjectURL(file) : ''
}

function onCoverPick(event: Event) {
  const file = pickFile(event)
  if (!file) return
  if (!COVER_TYPES.includes(file.type) || file.size > COVER_LIMIT_BYTES) {
    fail(copy.value.coverInvalid)
    return
  }
  cover.value = file
  setCoverPreview(file)
}

function clearCover() {
  cover.value = null
  setCoverPreview(null)
}

onUnmounted(() => setCoverPreview(null))

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob), anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url)
}

// Matches the backend's Content-Disposition filenames.
const exportedFile = computed(() => {
  const pack = exported.value
  if (!pack) return ''
  return pack.source ? `${pack.id}-${pack.version}-src.zip` : `${pack.id}-${pack.version}.dfplugin`
})

const submissionUrl = computed(() => exported.value ? contentPackSubmissionUrl(exported.value.id) : '')
const registerUrl = computed(() => exported.value ? repositorySubmissionUrl(exported.value.id) : '')

function submitToHub() {
  if (!exported.value) return
  saveBlob(exported.value.blob, `${exported.value.id}-${exported.value.version}.zip`)
  window.open(submissionUrl.value, '_blank', 'noopener')
}

function openRegisterForm() {
  if (registerUrl.value) window.open(registerUrl.value, '_blank', 'noopener')
}

async function exportPack() {
  if (!packId.value.trim() || !name.value.trim()) { fail(copy.value.needIdName); return }
  if (!CONTENT_PACK_ID_PATTERN.test(packId.value.trim())) { fail(copy.value.idInvalid); return }
  if (isAdventureModule.value && !selectedAdventures.value.length) { fail(copy.value.adventureRequired); return }
  if (!isAdventureModule.value && !selectedWorld.value && !selectedRule.value && !selectedBooks.value.length && !selectedCards.value.length) {
    fail(copy.value.resourceRequired); return
  }
  // The backend only packages a map together with a world.
  const withMap = includeMap.value && Boolean(selectedWorld.value)
  if (withMap && mapIcons.value.length > MAP_ICON_LIMIT) { fail(copy.value.mapIconLimit); return }
  busy.value = true; error.value = ''
  try {
    const source = format.value === 'source'
    // Only upload images for options that will actually be packaged.
    const [worldSceneImage, ruleSceneImage, mapBackgroundRef, mapIconUploads] = await Promise.all([
      includeSceneImages.value && selectedWorld.value && worldCover.value ? uploadSceneImage(worldCover.value) : undefined,
      includeSceneImages.value && selectedRule.value && ruleCover.value ? uploadSceneImage(ruleCover.value) : undefined,
      withMap && mapBackground.value ? uploadMapBackground(mapBackground.value) : undefined,
      withMap
        ? Promise.all(mapIcons.value.map(async file => ({ id: file.name.replace(/\.[^.]+$/, ''), file_name: file.name, file_data: await fileToBase64(file) })))
        : [],
    ])
    const response = await apiBlob('/plugins/export', { method: 'POST', body: JSON.stringify({
      plugin_id: packId.value.trim(), name: name.value.trim(), version: version.value.trim() || '0.1.0', description: description.value.trim(),
      world_id: selectedWorld.value, rule_id: selectedRule.value, adventure_ids: [],
      // Only the adventure-module export sends adventures; the backend marks
      // a pack as adventure-module exactly when it contains adventures.
      adventure_refs: isAdventureModule.value ? selectedAdventureRefs() : [],
      lorebook_ids: selectedBooks.value, card_ids: selectedCards.value,
      include_portraits: includePortraits.value, include_scene_images: includeSceneImages.value,
      world_scene_image: worldSceneImage, rule_scene_image: ruleSceneImage,
      include_map: withMap, map_background: mapBackgroundRef, map_icons: mapIconUploads,
      flat: source, language: exportLanguage.value,
      cover_data: cover.value ? await fileToBase64(cover.value) : '',
      cover_filename: cover.value?.name || '',
    }) })
    const blob = await response.blob()
    exported.value = { blob, id: packId.value.trim(), version: version.value.trim() || '0.1.0', source }
    saveBlob(blob, exportedFile.value)
  } catch (cause) { fail(errorMessage(cause)) } finally { busy.value = false }
}
</script>

<template>
  <Modal :title="isAdventureModule ? copy.adventureTitle : copy.resourceTitle" dialog-class="creator-package-dialog" @close="emit('close')">
    <p class="creator-package-intro">{{ isAdventureModule ? copy.adventureIntro : copy.resourceIntro }}</p>
    <p v-if="loading" role="status">{{ copy.loading }}</p>
    <p v-if="error" ref="errorRef" class="error-banner" role="alert">{{ error }}</p>
    <template v-if="exported?.source">
      <section class="creator-package-section" data-testid="creator-export-done">
        <h3>{{ copy.doneTitle }}</h3>
        <p class="creator-package-intro">{{ copy.sourceDoneText(exportedFile) }}</p>
      </section>
      <section class="creator-package-section">
        <h3>{{ copy.sourceTitle }}</h3>
        <ol class="creator-package-steps"><li v-for="step in copy.sourceSteps" :key="step">{{ step }}</li></ol>
      </section>
      <footer class="creator-package-actions"><button type="button" @click="emit('close')">{{ copy.done }}</button><button type="button" class="primary" data-testid="creator-export-register-repository" @click="openRegisterForm">{{ copy.sourceRegister }}</button></footer>
    </template>
    <template v-else-if="exported">
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
        <details class="wide creator-package-collapsible" data-testid="creator-export-books" open>
          <summary><span>{{ copy.books }}</span><small>{{ selectedBooks.length }} / {{ books.length }}</small></summary>
          <div class="creator-package-checklist">
            <label v-for="item in books" :key="item.id" class="creator-package-check"><input v-model="selectedBooks" type="checkbox" :value="item.id"><span>{{ item.name || item.id }} <small v-if="item.entry_count !== undefined">{{ item.entry_count }} {{ copy.entries }}</small></span></label>
            <small v-if="!books.length">{{ copy.empty }}</small>
          </div>
        </details>
        <details class="wide creator-package-collapsible" data-testid="creator-export-cards" open>
          <summary><span>{{ copy.cards }}</span><small>{{ selectedCards.length }} / {{ cardOptions.length }}</small></summary>
          <div class="creator-package-checklist">
            <label v-for="item in cardOptions" :key="item.id" class="creator-package-check"><input v-model="selectedCards" type="checkbox" :value="item.id"><span>{{ item.label }}</span></label>
            <small v-if="!cardOptions.length">{{ copy.empty }}</small>
          </div>
        </details>
      </div></section>
      <section class="creator-package-section" data-testid="creator-export-media">
        <h3>{{ copy.media }}</h3>
        <p class="creator-package-hint">{{ copy.mediaIntro }}</p>
        <div class="creator-package-media-grid">
          <div class="creator-package-cover" :class="{ 'has-image': coverPreview }" data-testid="creator-export-cover-block">
            <img v-if="coverPreview" :src="coverPreview" alt="" class="creator-package-cover-image">
            <div class="creator-package-cover-body">
              <strong>{{ copy.cover }}</strong>
              <div class="creator-package-cover-actions">
                <label class="creator-package-file"><span>{{ copy.coverPick }}</span><input type="file" accept="image/webp,image/png,image/jpeg" data-testid="creator-export-cover" @change="onCoverPick"><small>{{ cover?.name || copy.coverHint }}</small></label>
                <button v-if="cover" type="button" class="creator-package-cover-clear" data-testid="creator-export-cover-clear" @click="clearCover">{{ copy.coverClear }}</button>
              </div>
            </div>
          </div>
          <div class="creator-package-media-card">
            <label class="creator-package-option"><input v-model="includePortraits" type="checkbox" data-testid="creator-export-include-portraits"><span><strong>{{ copy.portraits }}</strong><small>{{ copy.portraitsHint }}</small></span></label>
          </div>
          <div class="creator-package-media-card">
            <label class="creator-package-option"><input v-model="includeSceneImages" type="checkbox" data-testid="creator-export-include-scene-images"><span><strong>{{ copy.sceneImages }}</strong><small>{{ copy.sceneImagesHint }}</small></span></label>
            <div v-if="includeSceneImages && (selectedWorld || selectedRule)" class="creator-package-files">
              <label v-if="selectedWorld" class="creator-package-file"><span>{{ copy.worldCover }}</span><input type="file" accept="image/jpeg,image/png,image/webp" data-testid="creator-export-world-cover" @change="worldCover = pickFile($event)"><small>{{ worldCover?.name || copy.worldCoverHint }}</small></label>
              <label v-if="selectedRule" class="creator-package-file"><span>{{ copy.ruleCover }}</span><input type="file" accept="image/jpeg,image/png,image/webp" data-testid="creator-export-rule-cover" @change="ruleCover = pickFile($event)"><small>{{ ruleCover?.name || copy.ruleCoverHint }}</small></label>
            </div>
          </div>
          <div class="creator-package-media-card">
            <label class="creator-package-option" :class="{ disabled: !selectedWorld }"><input v-model="includeMap" type="checkbox" :disabled="!selectedWorld" data-testid="creator-export-include-map"><span><strong>{{ copy.map }}</strong><small>{{ selectedWorld ? copy.mapHint : copy.mapNeedsWorld }}</small></span></label>
            <div v-if="includeMap && selectedWorld" class="creator-package-files">
              <label class="creator-package-file"><span>{{ copy.mapBackground }}</span><input type="file" accept="image/jpeg,image/png,image/webp" data-testid="creator-export-map-background" @change="mapBackground = pickFile($event)"><small>{{ mapBackground?.name || copy.mapBackgroundHint }}</small></label>
              <label class="creator-package-file"><span>{{ copy.mapIcons }}</span><input type="file" accept="image/jpeg,image/png,image/webp" multiple data-testid="creator-export-map-icons" @change="mapIcons = pickFiles($event)"><small>{{ mapIcons.length ? copy.mapIconsSelected(mapIcons.length) : copy.mapIconsHint }}</small></label>
            </div>
          </div>
        </div>
      </section>
      <section class="creator-package-section" data-testid="creator-export-format">
        <h3>{{ copy.format }}</h3>
        <div class="creator-package-options is-split">
          <label class="creator-package-option"><input v-model="format" type="radio" name="creator-export-format" value="package"><span><strong>{{ copy.formatPackage }}</strong><small>{{ copy.formatPackageHint }}</small></span></label>
          <label class="creator-package-option"><input v-model="format" type="radio" name="creator-export-format" value="source" data-testid="creator-export-format-source"><span><strong>{{ copy.formatSource }}</strong><small>{{ copy.formatSourceHint }}</small></span></label>
        </div>
      </section>
      <section v-if="preview.hasContents" class="creator-package-section" data-testid="creator-export-preview">
        <h3>{{ copy.preview }}</h3>
        <dl class="creator-package-preview">
          <template v-if="isAdventureModule">
            <div><dt>{{ copy.previewCount }}</dt><dd>{{ preview.count }}</dd></div>
            <div><dt>{{ copy.previewRuntimes }}</dt><dd>{{ preview.runtimes.join('、') || copy.none }}</dd></div>
            <div><dt>{{ copy.previewPolicies }}</dt><dd>{{ preview.policies.join('、') }}</dd></div>
          </template>
          <div><dt>{{ copy.previewResources }}</dt><dd>{{ preview.resources || copy.none }}</dd></div>
        </dl>
        <ul v-if="preview.contents.length" class="creator-package-contents" data-testid="creator-export-contents">
          <li v-for="item in preview.contents" :key="item.label"><span>{{ item.label }}</span><strong>{{ item.value }}</strong></li>
        </ul>
        <template v-if="isAdventureModule">
          <ul v-if="preview.warnings.length" class="creator-package-warnings">
            <li v-for="warning in preview.warnings" :key="warning" data-testid="creator-export-warning">{{ warning }}</li>
          </ul>
          <p v-else class="creator-package-ready">{{ copy.ready }}</p>
          <p class="creator-package-privacy">{{ copy.privacy }}</p>
        </template>
      </section>
      <footer class="creator-package-actions"><button type="button" @click="emit('close')">{{ copy.close }}</button><button type="button" class="primary" data-testid="creator-export-submit" :disabled="busy" @click="exportPack">{{ busy ? '…' : `${isAdventureModule ? copy.adventureExport : copy.resourceExport} ${format === 'source' ? '.zip' : '.dfplugin'}` }}</button></footer>
    </template>
  </Modal>
</template>

<style scoped>
.creator-package-intro{margin:0;color:var(--df-text-secondary);line-height:1.5}.creator-package-section{display:grid;gap:.8rem;padding:.9rem 0;border-bottom:1px solid var(--df-border-soft)}.creator-package-section h3{margin:0;color:var(--df-accent-strong);font-size:.95rem}.creator-package-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem}.creator-package-grid label,.creator-package-grid fieldset{display:grid;gap:.35rem;min-width:0}.creator-package-grid .wide{grid-column:1/-1}.creator-package-grid input,.creator-package-grid select,.creator-package-grid textarea{box-sizing:border-box;width:100%;min-width:0;padding:.55rem;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:var(--df-control-bg);color:var(--df-text)}fieldset{border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);padding:.65rem}.creator-package-check{display:flex!important;align-items:center;gap:.45rem}.creator-package-check input{width:auto}.creator-package-check small{color:var(--df-text-muted)}.creator-package-actions{display:flex;justify-content:flex-end;gap:.6rem;padding-top:1rem}
.creator-package-hint{margin:0;color:var(--df-text-muted);font-size:.85rem;line-height:1.5}
.creator-package-options{display:grid;gap:.6rem}.creator-package-options.is-split{grid-template-columns:repeat(2,minmax(0,1fr))}
.creator-package-option-group{display:grid;gap:.5rem}
.creator-package-option{display:flex;align-items:flex-start;gap:.55rem;min-width:0;margin:0;padding:.6rem .7rem;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:color-mix(in srgb,var(--df-control-bg) 68%,transparent);cursor:pointer}.creator-package-option input{flex:0 0 auto;margin:.2rem 0 0}.creator-package-option span{display:grid;gap:.2rem;min-width:0}.creator-package-option strong{font-size:.9rem}.creator-package-option small{color:var(--df-text-muted);line-height:1.45}.creator-package-option.disabled{opacity:.6;cursor:default}
.creator-package-files{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.5rem;padding-left:1.6rem}
/* 媒体区：封面整行，其余三项各占一格，文件选择器放进卡片内部，
   不再用缩进表示从属关系。 */
.creator-package-media-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:.6rem}
.creator-package-media-card{display:grid;gap:.5rem;align-content:start;min-width:0;padding:.6rem .7rem;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:color-mix(in srgb,var(--df-control-bg) 68%,transparent)}
.creator-package-media-card .creator-package-option{padding:0;border:0;background:none}
.creator-package-media-card .creator-package-files{padding-left:1.6rem;grid-template-columns:repeat(2,minmax(0,1fr))}
.creator-package-cover{grid-column:1/-1}
/* 资料册 / 角色卡：默认可折叠，summary 上直接给已选数量。 */
.creator-package-collapsible{border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);padding:.55rem .7rem}
.creator-package-collapsible>summary{display:flex;align-items:center;gap:.5rem;cursor:pointer;font-size:.9rem;list-style:none}
.creator-package-collapsible>summary::-webkit-details-marker{display:none}
.creator-package-collapsible>summary::after{content:'▾';margin-left:auto;color:var(--df-text-muted);transition:transform .15s ease}
.creator-package-collapsible:not([open])>summary::after{transform:rotate(-90deg)}
.creator-package-collapsible>summary small{color:var(--df-text-muted);font-size:.78rem;font-weight:400}
.creator-package-checklist{display:grid;gap:.3rem;margin-top:.55rem;max-height:14rem;overflow:auto}
.creator-package-cover{display:grid;grid-template-columns:minmax(0,1fr);gap:.75rem;align-items:start;padding:.6rem .7rem;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:color-mix(in srgb,var(--df-control-bg) 68%,transparent)}
.creator-package-cover.has-image{grid-template-columns:10rem minmax(0,1fr)}
.creator-package-cover-image{width:100%;aspect-ratio:16/9;object-fit:cover;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm)}
.creator-package-cover-body{display:grid;gap:.4rem;min-width:0}
.creator-package-cover-body strong{font-size:.9rem}
.creator-package-cover-actions{display:flex;flex-wrap:wrap;align-items:end;gap:.5rem}
.creator-package-cover-clear{align-self:end}
.creator-package-file{display:grid;gap:.25rem;min-width:0;margin:0;padding:.55rem .65rem;border:1px dashed var(--df-border-soft);border-radius:var(--df-radius-sm);font-size:.85rem}.creator-package-file input{max-width:100%;color:var(--df-text-secondary);font-size:.78rem}.creator-package-file small{color:var(--df-text-muted);line-height:1.45;overflow-wrap:anywhere}
.creator-package-preview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem;margin:0}.creator-package-preview div{display:grid;gap:.2rem;min-width:0}.creator-package-preview dt{color:var(--df-text-muted);font-size:.8rem}.creator-package-preview dd{margin:0;overflow-wrap:anywhere}
.creator-package-steps{display:grid;gap:.45rem;margin:0;padding-left:1.2rem;color:var(--df-text-secondary);line-height:1.5}
.creator-package-contents{display:grid;gap:.3rem;margin:0;padding:0;list-style:none}
.creator-package-contents li{display:flex;gap:.5rem;align-items:baseline;font-size:.85rem;color:var(--df-text-secondary)}
.creator-package-contents li span{flex:0 0 auto;min-width:4.5rem;color:var(--df-text-muted)}
.creator-package-contents li strong{min-width:0;overflow-wrap:anywhere;font-weight:600}
.creator-package-warnings{display:grid;gap:.35rem;margin:0;padding-left:1.1rem;color:var(--df-warning)}.creator-package-ready{margin:0;color:var(--df-text-secondary)}.creator-package-privacy{margin:0;color:var(--df-text-muted);font-size:.85rem}
@media(max-width:620px){.creator-package-grid,.creator-package-preview,.creator-package-options.is-split,.creator-package-files,.creator-package-media-grid{grid-template-columns:1fr}.creator-package-files{padding-left:0}.creator-package-collapsible>summary small{margin-left:0}}
</style>
