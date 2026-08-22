<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NInput, NInputNumber, NSwitch, NTag, NIcon, NSpin, NProgress, NModal } from 'naive-ui'
import {
  ServerOutline, CubeOutline, CloudDownloadOutline,
  LockClosedOutline, OptionsOutline, InformationCircleOutline, ShareSocialOutline,
  KeyOutline, CopyOutline, EyeOutline, RefreshOutline, ColorPaletteOutline,
  ImageOutline, PowerOutline, MicOutline, SearchOutline, AddOutline,
  TrashOutline, CheckmarkCircleOutline, AlertCircleOutline, SparklesOutline,
  VolumeHighOutline,
} from '@vicons/ionicons5'
import { useSettingsStore, providerSecretKey } from '@/stores/useSettingsStore'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { shouldAutoDownloadUpdate, updateStateForVersion, useUpdater } from '@/composables/useUpdater'
import { useLocale } from '@/composables/useLocale'
import { initializeTts, ttsRate, setTtsRate } from '@/utils/tts'
import { asrLanguageFor, initializeAsr, startRecording, type RecordingSession } from '@/utils/asr'
import { ApiError, api, errorMessage } from '@/api/client'
import { speechApi } from '@/api/speech'
import { pluginApi } from '@/api/plugins'
import type { MessageKey } from '@/i18n'
import type { SecretKey } from '@/stores/useSettingsStore'
import type { AppConfig, HubPreferences, LoginAuditEntry, LoginAuditResponse, TestResult, TtsVoiceCatalog } from '@/api/types'
import TestResultCard from '@/components/admin/TestResultCard.vue'
import TtsVoiceProfiles from '@/components/admin/TtsVoiceProfiles.vue'
import HelpButton from '@/components/common/HelpButton.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import { copyToClipboard } from '@/utils/clipboard'
import { useTheme } from '@/composables/useTheme'
import { useBackgroundImages, type BackgroundSlot } from '@/composables/useBackgroundImages'

type SectionId = 'api' | 'models' | 'memory' | 'network' | 'sharing' | 'botapi' | 'appearance' | 'access' | 'advanced' | 'about'
type StatusTone = 'default' | 'success' | 'warning' | 'error' | 'info'
type UpdatePackageKind = 'source' | 'portable'
type ModelCapability = 'chat' | 'image' | 'embedding' | 'tts' | 'asr'
type ModelCatalogFilter = 'all' | ModelCapability
type ProviderModelGroup = { name: string; models: string[] }
type SystemStatusItem = { label: string; value: string; detail: string; tone: StatusTone; icon: Component }
type SettingsSection = { id: SectionId; labelKey: MessageKey; icon: Component }

const store = useSettingsStore()
const route = useRoute()
const sponsorModalOpen = ref(false)
const toast = useToast()
const { confirm } = useConfirm()
const { updateInfo, updateChecking, checkForUpdates } = useUpdateCheck()
const {
  updateStatus,
  reloadCountdown,
  downloadPercent,
  startDownload,
  applyUpdate,
  refreshStatus,
  isUpdateBusy,
  restartApplication,
  waitForApplicationRestart,
} = useUpdater()
const { t, locale } = useLocale()
const { current: themeMode, skin: activeSkin, builtinSkins, apply: applyThemeMode, applySkin } = useTheme()
const {
  options: backgroundOptions,
  previews: backgroundPreviews,
  custom: customBackgrounds,
  loading: backgroundsLoading,
  initialize: initializeBackgrounds,
  setBackground,
  resetBackground,
  resetAllBackgrounds,
} = useBackgroundImages()
const backgroundBusy = ref<BackgroundSlot | 'all' | ''>('')
const restartBusy = ref(false)
const hubPreferences = ref<HubPreferences | null>(null)
const hubPrivacyBusy = ref(false)

async function loadHubPreferences() {
  try {
    hubPreferences.value = await pluginApi.hubPreferences()
  } catch {
    hubPreferences.value = null
  }
}

async function toggleHubTelemetry(enabled: boolean) {
  hubPrivacyBusy.value = true
  try {
    hubPreferences.value = await pluginApi.updateHubPreferences(enabled)
    toast.success(t(enabled ? 'hubTelemetryEnabled' : 'hubTelemetryDisabled'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    hubPrivacyBusy.value = false
  }
}

async function clearHubIdentity() {
  const ok = await confirm({
    title: t('hubClearIdentityTitle'),
    content: t('hubClearIdentityConfirm'),
    positiveText: t('hubClearIdentity'),
    type: 'error',
  })
  if (!ok) return
  hubPrivacyBusy.value = true
  try {
    hubPreferences.value = await pluginApi.deleteHubIdentity()
    toast.success(t('hubIdentityCleared'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    hubPrivacyBusy.value = false
  }
}

async function onBackgroundFile(slot: BackgroundSlot, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  backgroundBusy.value = slot
  try {
    await setBackground(slot, file)
    toast.success(t('backgroundUpdated'))
  } catch (error) {
    const message = error instanceof Error ? error.message : ''
    toast.error(t(message === 'image-too-large' ? 'backgroundTooLarge' : 'backgroundUnsupported'))
  } finally {
    backgroundBusy.value = ''
  }
}

async function resetOneBackground(slot: BackgroundSlot) {
  backgroundBusy.value = slot
  try {
    await resetBackground(slot)
    toast.success(t('backgroundResetDone'))
  } finally {
    backgroundBusy.value = ''
  }
}

async function resetBackgrounds() {
  backgroundBusy.value = 'all'
  try {
    await resetAllBackgrounds()
    toast.success(t('backgroundResetDone'))
  } finally {
    backgroundBusy.value = ''
  }
}

const updateChannel = computed(() => store.config?.update_channel === 'preview' ? 'preview' : 'stable')
async function toggleUpdateChannel(enabled: boolean) {
  if (enabled) {
    const ok = await confirm({
      title: t('updateChannel'),
      content: t('previewChannelConfirm'),
      positiveText: t('previewChannelEnable'),
      negativeText: t('cancel'),
      type: 'warning',
    })
    if (!ok) return
  }
  try {
    await api('/config', { method: 'POST', body: JSON.stringify({ update_channel: enabled ? 'preview' : 'stable' }) })
    await store.load()
    const result = await checkForUpdates(true)   // 强制重查，替换单例缓存
    if (!result?.ok) toast.error(result?.error || t('updateCheckFailed'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

const section = ref<SectionId>('api')
const sections: SettingsSection[] = [
  { id: 'api', labelKey: 'settingsSectionApi', icon: ServerOutline },
  { id: 'models', labelKey: 'settingsSectionModels', icon: SparklesOutline },
  { id: 'memory', labelKey: 'settingsSectionMemory', icon: CubeOutline },
  { id: 'network', labelKey: 'settingsSectionNetwork', icon: CloudDownloadOutline },
  { id: 'sharing', labelKey: 'settingsSectionSharing', icon: ShareSocialOutline },
  { id: 'botapi', labelKey: 'settingsSectionBotApi', icon: KeyOutline },
  { id: 'appearance', labelKey: 'settingsSectionAppearance', icon: ColorPaletteOutline },
  { id: 'access', labelKey: 'settingsSectionAccess', icon: LockClosedOutline },
  { id: 'advanced', labelKey: 'settingsSectionAdvanced', icon: OptionsOutline },
  { id: 'about', labelKey: 'settingsSectionAbout', icon: InformationCircleOutline },
]

function queryValue(value: unknown): string {
  return String(Array.isArray(value) ? (value[0] || '') : (value || ''))
}

function syncRouteTarget() {
  const requestedSection = queryValue(route.query.section)
  if (sections.some(item => item.id === requestedSection)) {
    section.value = requestedSection as SectionId
  }
  if (queryValue(route.query.focus) === 'update') {
    section.value = 'about'
    void nextTick(() => {
      document.getElementById('settings-update')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }
}

// 自动朗读 GM 叙事开关：纯前端偏好，存 localStorage，与 GameTimeline 共用 key。
const autoSpeakKey = 'trpg_auto_speak_gm'
const autoSpeak = ref(false)
try { autoSpeak.value = localStorage.getItem(autoSpeakKey) === '1' } catch { /* localStorage 不可用时保持默认关 */ }
function setAutoSpeak(value: boolean) {
  autoSpeak.value = value
  try { localStorage.setItem(autoSpeakKey, value ? '1' : '0') } catch { /* localStorage 不可用时仅内存态生效 */ }
}

// 朗读语速偏好：读 tts 模块的 localStorage 值。
const ttsRateValue = ref(ttsRate())
function setTtsRateValue(value: number | null) {
  if (value == null) return
  ttsRateValue.value = value
  setTtsRate(value)
}
const ttsVoices = ref<TtsVoiceCatalog | null>(null)
const ttsTesting = ref(false)
const ttsProvider = computed(() => String(store.config.tts_provider || 'browser'))
const ttsVoiceOptions = computed(() => (
  ttsVoices.value?.voices.filter(voice => voice.engine === ttsProvider.value) || []
))

async function loadTtsVoices() {
  try { ttsVoices.value = await speechApi.voices() }
  catch { ttsVoices.value = null }
}

const EDGE_TTS_DEFAULT_VOICE = 'zh-CN-XiaoxiaoNeural'

function setTtsProvider(value: string) {
  setStr('tts_provider', value)
  if (value === 'browser' || value === 'edge-tts') setStr('tts_provider_ref', '')
  const currentVoice = String(store.config.tts_default_voice || '')
  if (value === 'edge-tts' && !currentVoice.endsWith('Neural')) {
    setStr('tts_default_voice', EDGE_TTS_DEFAULT_VOICE)
  } else if (value === 'openai-compatible' && currentVoice.endsWith('Neural')) {
    setStr('tts_default_voice', 'alloy')
  }
}

const TTS_CONFIG_KEYS = [
  'tts_audio_format', 'tts_default_voice', 'tts_gm_voice', 'tts_player_voice',
  'tts_timeout_seconds', 'tts_cache_mb',
]

async function saveTts(showToast = true): Promise<boolean> {
  try {
    await store.saveSection(TTS_CONFIG_KEYS, ['tts_api_key'])
    await Promise.all([loadTtsVoices(), initializeTts(true)])
    if (showToast) toast.success(t('settingsSaved'))
    return true
  } catch (error: unknown) {
    toast.error(errorMessage(error))
    return false
  }
}

async function testTts() {
  ttsTesting.value = true
  try {
    if (!await saveTts(false)) return
    const blob = await speechApi.test({
      text: t('ttsTestText'),
      voice: String(store.config.tts_gm_voice || store.config.tts_default_voice || ''),
      language: locale.value === 'ja' ? 'ja-JP' : locale.value === 'en' ? 'en-US' : 'zh-CN',
      speed: ttsRateValue.value,
    })
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    audio.onended = () => URL.revokeObjectURL(url)
    audio.onerror = () => URL.revokeObjectURL(url)
    await audio.play()
    toast.success(t('ttsTestStarted'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    ttsTesting.value = false
  }
}

// 语音识别（云端 ASR）：与 TTS 相同的保存/测试模式，测试改为录一段话再识别。
const asrProvider = computed(() => String(store.config.asr_provider || 'disabled'))
const asrTesting = ref(false)
const asrTestRecording = ref(false)
const asrTestText = ref('')
let asrTestSession: RecordingSession | null = null

const ASR_CONFIG_KEYS = ['asr_timeout_seconds']

function setAsrProvider(value: string) {
  setStr('asr_provider', value)
  if (value === 'disabled') setStr('asr_provider_ref', '')
}

// AI 服务商凭据库：草稿在本地编辑，保存时整体提交；secret 走 store.secrets 动态键。
interface ProviderDraft {
  id: string
  name: string
  base_url: string
  api_format: string
  models: string[]
  configuredMasked: string
}
const providerDrafts = ref<ProviderDraft[]>([])
const providerTestModels = ref<Record<string, string>>({})
const providerSearch = ref('')
const activeProviderId = ref('')
const providerSaving = ref(false)
const providerTestingId = ref('')
const providerFetchingModelsId = ref('')
const providerTestedId = ref('')
const providerTestResult = ref<TestResult | null>(null)
const providerCatalogOpen = ref(false)
const providerCatalogProviderId = ref('')
const providerCatalogModels = ref<Record<string, string[]>>({})
const providerCatalogSearch = ref('')
const providerCatalogFilter = ref<ModelCatalogFilter>('all')
const providerCatalogCustomModel = ref('')
const modelRoutingSaving = ref(false)

const providerLibrarySupported = computed(() => Object.prototype.hasOwnProperty.call(store.config, 'ai_providers'))
const savedProviderIds = computed(() => new Set((store.config.ai_providers || []).map(p => p.id)))
const filteredProviderDrafts = computed(() => {
  const query = providerSearch.value.trim().toLowerCase()
  if (!query) return providerDrafts.value
  return providerDrafts.value.filter(provider => (
    `${provider.name} ${provider.base_url} ${provider.models.join(' ')}`.toLowerCase().includes(query)
  ))
})
const activeProvider = computed(() => (
  providerDrafts.value.find(provider => provider.id === activeProviderId.value) || null
))
const activeCatalogProvider = computed(() => (
  providerDrafts.value.find(provider => provider.id === providerCatalogProviderId.value) || null
))
const activeProviderModelGroups = computed(() => groupProviderModels(activeProvider.value?.models || []))
const providerCatalogSourceModels = computed(() => (
  providerCatalogModels.value[providerCatalogProviderId.value] || []
))
const providerCatalogFilteredModels = computed(() => {
  const query = providerCatalogSearch.value.trim().toLowerCase()
  return providerCatalogSourceModels.value.filter(model => {
    const matchesQuery = !query || model.toLowerCase().includes(query)
    const matchesCapability = providerCatalogFilter.value === 'all'
      || modelCapability(model) === providerCatalogFilter.value
    return matchesQuery && matchesCapability
  })
})
const providerCatalogGroups = computed(() => groupProviderModels(providerCatalogFilteredModels.value))
const providerCatalogFilters = computed(() => {
  const models = providerCatalogSourceModels.value
  const filters: { id: ModelCatalogFilter; label: string; count: number }[] = [
    { id: 'all', label: t('modelPickerAll'), count: models.length },
    { id: 'chat', label: t('modelCapabilityChat'), count: models.filter(model => modelCapability(model) === 'chat').length },
    { id: 'image', label: t('modelCapabilityImage'), count: models.filter(model => modelCapability(model) === 'image').length },
    { id: 'embedding', label: t('modelCapabilityEmbedding'), count: models.filter(model => modelCapability(model) === 'embedding').length },
    { id: 'tts', label: t('modelCapabilityTts'), count: models.filter(model => modelCapability(model) === 'tts').length },
    { id: 'asr', label: t('modelCapabilityAsr'), count: models.filter(model => modelCapability(model) === 'asr').length },
  ]
  return filters
})

function syncProviderDrafts(list: AppConfig['ai_providers']) {
  providerDrafts.value = (list || []).map(p => ({
    id: p.id,
    name: p.name,
    base_url: p.base_url,
    api_format: String(p.api_format || 'openai'),
    models: [...new Set((p.models || []).map(model => String(model).trim()).filter(Boolean))],
    configuredMasked: p.api_key?.configured ? p.api_key.masked : '',
  }))
  if (!providerDrafts.value.some(provider => provider.id === activeProviderId.value)) {
    activeProviderId.value = providerDrafts.value[0]?.id || ''
  }
}
watch(() => store.config.ai_providers, syncProviderDrafts, { immediate: true })

function addProviderDraft() {
  if (!providerLibrarySupported.value) {
    toast.error(t('providerBackendOutdated'))
    return
  }
  const provider = {
    id: `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
    name: '', base_url: '', api_format: 'openai', models: [], configuredMasked: '',
  }
  providerDrafts.value.push(provider)
  activeProviderId.value = provider.id
}
function removeProviderDraft(index: number) {
  const [removed] = providerDrafts.value.splice(index, 1)
  if (removed?.id === activeProviderId.value) {
    activeProviderId.value = providerDrafts.value[Math.min(index, providerDrafts.value.length - 1)]?.id || ''
  }
}
function setProviderSecret(id: string, v: string | number) {
  store.secrets[providerSecretKey(id)] = String(v).trim()
}

async function saveProvidersList() {
  if (!providerLibrarySupported.value) {
    toast.error(t('providerBackendOutdated'))
    return
  }
  providerSaving.value = true
  try {
    await store.saveProviders(
      providerDrafts.value.map(d => ({
        id: d.id, name: d.name, base_url: d.base_url, api_format: d.api_format, models: d.models,
      })),
    )
    toast.success(t('settingsSaved'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    providerSaving.value = false
  }
}

async function testProviderDraft(draft: ProviderDraft, testModel: string) {
  providerTestingId.value = draft.id
  providerTestResult.value = null
  try {
    providerTestResult.value = await store.testProvider({
      // 已保存的服务商可让服务端取凭据；新草稿只带明文输入。
      providerId: savedProviderIds.value.has(draft.id) ? draft.id : undefined,
      baseUrl: draft.base_url,
      apiKey: store.secrets[providerSecretKey(draft.id)]?.trim() || '',
      apiFormat: draft.api_format,
      model: testModel.trim() || String(store.config.model || 'gpt-4o-mini'),
    })
    providerTestedId.value = draft.id
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    providerTestingId.value = ''
  }
}

async function fetchProviderModels(draft: ProviderDraft) {
  providerFetchingModelsId.value = draft.id
  try {
    const result = await store.fetchProviderModels({
      providerId: savedProviderIds.value.has(draft.id) ? draft.id : undefined,
      baseUrl: draft.base_url,
      apiKey: store.secrets[providerSecretKey(draft.id)]?.trim() || '',
      apiFormat: draft.api_format,
    })
    if (!result.ok) throw new Error(result.error || t('operationFailed'))
    providerCatalogModels.value[draft.id] = [...new Set([
      ...draft.models,
      ...result.models.map(model => String(model).trim()).filter(Boolean),
    ])]
    toast.success(t('providerModelsFetched', { count: providerCatalogModels.value[draft.id].length }))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    providerFetchingModelsId.value = ''
  }
}

async function openProviderCatalog(draft: ProviderDraft) {
  providerCatalogProviderId.value = draft.id
  providerCatalogSearch.value = ''
  providerCatalogFilter.value = 'all'
  providerCatalogCustomModel.value = ''
  providerCatalogOpen.value = true
  if (!providerCatalogModels.value[draft.id]) {
    providerCatalogModels.value[draft.id] = [...draft.models]
    await fetchProviderModels(draft)
  }
}

function addProviderModel(draft: ProviderDraft, value: string) {
  const model = String(value || '').trim()
  if (!model) return
  if (!draft.models.includes(model)) draft.models.push(model)
}

function addCustomProviderModel() {
  if (!activeCatalogProvider.value) return
  addProviderModel(activeCatalogProvider.value, providerCatalogCustomModel.value)
  const id = activeCatalogProvider.value.id
  providerCatalogModels.value[id] = [...new Set([...(providerCatalogModels.value[id] || []), providerCatalogCustomModel.value.trim()].filter(Boolean))]
  providerCatalogCustomModel.value = ''
}

function toggleCatalogModel(model: string) {
  if (!activeCatalogProvider.value) return
  if (activeCatalogProvider.value.models.includes(model)) removeProviderModel(activeCatalogProvider.value, model)
  else addProviderModel(activeCatalogProvider.value, model)
}

function addAllCatalogModels() {
  if (!activeCatalogProvider.value) return
  activeCatalogProvider.value.models = [...new Set([
    ...activeCatalogProvider.value.models,
    ...providerCatalogFilteredModels.value,
  ])]
}

function isCatalogModelSelected(model: string): boolean {
  return Boolean(activeCatalogProvider.value?.models.includes(model))
}

function removeProviderModel(draft: ProviderDraft, model: string) {
  draft.models = draft.models.filter(item => item !== model)
}

function providerHasKey(draft: ProviderDraft): boolean {
  return Boolean(draft.configuredMasked || store.secrets[providerSecretKey(draft.id)]?.trim())
}

function providerDraftReady(draft: ProviderDraft): boolean {
  return Boolean(draft.base_url.trim() && providerHasKey(draft))
}

function setActiveProviderTestModel(value: string | number) {
  if (!activeProvider.value) return
  providerTestModels.value[activeProvider.value.id] = String(value)
}

function providerMark(draft: ProviderDraft): string {
  return (draft.name || draft.id).trim().slice(0, 1).toUpperCase()
}

function providerStyle(providerId: string) {
  let hash = 0
  for (const character of providerId) hash = ((hash << 5) - hash) + character.charCodeAt(0)
  return { '--provider-hue': String(Math.abs(hash) % 360) }
}

function modelCapability(model: string): ModelCapability {
  const value = model.toLowerCase()
  if (/(image|dall-e|flux|stable[-_. ]?diffusion|(^|[-_.])sd3|kolors|qwen[-_. ]?image|ideogram|imagen)/.test(value)) {
    return 'image'
  }
  if (/(embed|embedding|bge|e5-|text2vec|rerank)/.test(value)) return 'embedding'
  if (/(whisper|sensevoice|paraformer|funasr|speech[-_. ]?to[-_. ]?text|transcri|(^|[-_.])asr)/.test(value)) return 'asr'
  if (/(tts|cosyvoice|fish[-_. ]?speech|gpt[-_. ]?sovits|chattts|voice)/.test(value)) return 'tts'
  return 'chat'
}

function modelCapabilityLabels(model: string): string[] {
  const capability = modelCapability(model)
  if (capability === 'image') return [t('modelCapabilityImage')]
  if (capability === 'embedding') return [t('modelCapabilityEmbedding')]
  if (capability === 'tts') return [t('modelCapabilityTts')]
  if (capability === 'asr') return [t('modelCapabilityAsr')]

  const labels = [t('modelCapabilityChat')]
  const value = model.toLowerCase()
  if (/(reason|thinking|deepseek-r|(^|[-_.])r1|(^|[-_.])o[134])/.test(value)) {
    labels.push(t('modelCapabilityReasoning'))
  }
  return labels
}

function groupProviderModels(models: string[]): ProviderModelGroup[] {
  const groups = new Map<string, string[]>()
  for (const model of models) {
    const slash = model.indexOf('/')
    const name = slash > 0 ? model.slice(0, slash) : t('providerOtherModels')
    const items = groups.get(name) || []
    items.push(model)
    groups.set(name, items)
  }
  return [...groups.entries()].map(([name, items]) => ({ name, models: items }))
}

function providerById(id: string) {
  return (store.config.ai_providers || []).find(p => p.id === id)
}

function savedProviderModels(providerId: string, capability?: ModelCapability): string[] {
  const models = providerById(providerId)?.models || []
  return capability ? models.filter(model => modelCapability(model) === capability) : models
}

function modelBindingSummary(providerId: unknown, model: unknown): string {
  const provider = providerById(String(providerId || ''))
  if (!provider) return t('modelRoutingUnassigned')
  return `${provider.name || provider.id} · ${String(model || t('modelUnset'))}`
}

function setModelRoleProvider(
  refKey: keyof AppConfig,
  modelKey: keyof AppConfig,
  providerId: string,
  capability: ModelCapability,
) {
  setStr(refKey, providerId)
  const models = savedProviderModels(providerId, capability)
  const current = String((store.config as Record<string, unknown>)[modelKey] || '')
  if (!models.includes(current)) setStr(modelKey, models[0] || '')
}

const MODEL_ROUTING_CONFIG_KEYS = [
  'llm_provider_ref', 'model',
  'fallback1_enabled', 'fallback1_provider_ref', 'fallback1_model',
  'fallback2_enabled', 'fallback2_provider_ref', 'fallback2_model',
  'embedding_provider_ref', 'embedding_model',
  'tts_provider', 'tts_provider_ref', 'tts_model',
  'asr_provider', 'asr_provider_ref', 'asr_model',
]

async function saveModelRouting() {
  if (!providerLibrarySupported.value) {
    toast.error(t('providerBackendOutdated'))
    return
  }
  modelRoutingSaving.value = true
  try {
    await store.saveSection(MODEL_ROUTING_CONFIG_KEYS)
    await Promise.all([initializeTts(true), initializeAsr(true), loadTtsVoices()])
    toast.success(t('modelRoutingSaved'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    modelRoutingSaving.value = false
  }
}

async function saveAsr(showToast = true): Promise<boolean> {
  try {
    await store.saveSection(ASR_CONFIG_KEYS, ['asr_api_key'])
    await initializeAsr(true)
    if (showToast) toast.success(t('settingsSaved'))
    return true
  } catch (error: unknown) {
    toast.error(errorMessage(error))
    return false
  }
}

async function testAsr() {
  if (asrTestRecording.value) {
    await stopAsrTest()
    return
  }
  asrTesting.value = true
  asrTestText.value = ''
  try {
    if (!await saveAsr(false)) return
    asrTestSession = await startRecording()
    asrTestRecording.value = true
  } catch (error: unknown) {
    if (error instanceof Error && error.message === 'asr-mic-denied') toast.error(t('asrMicDenied'))
    else toast.error(t('asrRecordFailed'))
  } finally {
    asrTesting.value = false
  }
}

async function stopAsrTest() {
  const session = asrTestSession
  asrTestSession = null
  asrTestRecording.value = false
  if (!session) return
  asrTesting.value = true
  try {
    const blob = await session.stop()
    asrTestText.value = await speechApi.transcribeTest(blob, asrLanguageFor(locale.value))
  } catch (error: unknown) {
    toast.error(error instanceof ApiError && error.message ? error.message : t('asrFailed'))
  } finally {
    asrTesting.value = false
  }
}

onUnmounted(() => {
  asrTestSession?.cancel()
  asrTestSession = null
  asrTestRecording.value = false
})

const testing = ref(false)
const testResult = ref<TestResult | null>(null)
const testKind = ref<'model' | 'embedding' | 'proxy' | ''>('')

const password = ref('')
const passwordConfirm = ref('')
const loginHistory = ref<LoginAuditEntry[]>([])
const loginHistoryLoading = ref(false)
const loginHistoryError = ref('')
const loginHistoryPage = ref(1)
const loginHistoryPageSize = 10
const loginHistoryTotalPages = computed(() => Math.max(1, Math.ceil(loginHistory.value.length / loginHistoryPageSize)))
const pagedLoginHistory = computed(() => {
  const start = (loginHistoryPage.value - 1) * loginHistoryPageSize
  return loginHistory.value.slice(start, start + loginHistoryPageSize)
})
const botToken = ref('')
const botTokenBusy = ref(false)
const locationOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin
const botApiAddress = computed(() => String(store.config.public_base_url || locationOrigin).replace(/\/$/, ''))

const proxySourceLabel = computed(() => {
  const s = store.config.proxy_source
  if (s === 'config') return t('proxySourceConfig')
  if (s === 'env') return t('proxySourceEnv')
  if (s === 'disabled') return t('proxySourceDisabled')
  return t('proxySourceUnset')
})
const proxyFormatLabel = computed(() => (store.config.proxy_supported ? t('proxyFormatSupported') : t('proxyFormatUnsupported')))
const requiredUpdateKind = computed<UpdatePackageKind | null>(() => {
  const mode = updateStatus.value?.self_update.mode
  return mode === 'source' || mode === 'portable' ? mode : null
})
const latestUpdateVersion = computed(() => (
  updateInfo.value?.latest?.version || updateInfo.value?.latest?.tag_name || ''
))
const displayedUpdateState = computed(() => updateStateForVersion(
  updateStatus.value,
  latestUpdateVersion.value,
))
const isDisplayedUpdateDownloading = computed(() => (
  displayedUpdateState.value === 'downloading' || displayedUpdateState.value === 'verifying'
))
const isDisplayedUpdateBusy = computed(() => (
  isDisplayedUpdateDownloading.value
  || displayedUpdateState.value === 'applying'
  || displayedUpdateState.value === 'restarting'
))
const updateTagType = computed<StatusTone>(() => {
  if (!updateInfo.value) return 'default'
  if (!updateInfo.value.ok) return 'error'
  if (updateInfo.value.no_release) return 'info'
  return updateInfo.value.update_available ? 'warning' : 'success'
})
const updateTagLabel = computed(() => {
  if (!updateInfo.value) return t('updateUnchecked')
  if (!updateInfo.value.ok) return t('updateCheckFailed')
  if (updateInfo.value.no_release) return t('updateNoRelease')
  return updateInfo.value.update_available ? t('updateFound') : t('updateLatest')
})
function providerReady(ref: unknown): boolean {
  const id = String(ref || '').trim()
  if (!id) return false
  const p = providerById(id)
  return Boolean(p && p.base_url && p.api_key?.configured)
}
const systemStatusItems = computed<SystemStatusItem[]>(() => {
  const c = store.config
  const mainProvider = providerById(String(c.llm_provider_ref || '').trim())
  const embeddingProvider = providerById(String(c.embedding_provider_ref || '').trim())
  const ttsProviderConfig = providerById(String(c.tts_provider_ref || '').trim())
  const asrProviderConfig = providerById(String(c.asr_provider_ref || '').trim())
  const mainReady = Boolean(c.model && providerReady(c.llm_provider_ref))
  const fallbackSlots = [
    {
      name: t('fallbackSlot1'),
      enabled: !!c.fallback1_enabled,
      provider: providerById(String(c.fallback1_provider_ref || '').trim()),
      model: c.fallback1_model,
      ready: Boolean(c.fallback1_model && providerReady(c.fallback1_provider_ref)),
    },
    {
      name: t('fallbackSlot2'),
      enabled: !!c.fallback2_enabled,
      provider: providerById(String(c.fallback2_provider_ref || '').trim()),
      model: c.fallback2_model,
      ready: Boolean(c.fallback2_model && providerReady(c.fallback2_provider_ref)),
    },
  ]
  const enabledFallbacks = fallbackSlots.filter(item => item.enabled)
  const readyFallbacks = enabledFallbacks.filter(item => item.ready)
  const embeddingReady = Boolean(c.embedding_enabled && c.embedding_model && providerReady(c.embedding_provider_ref))
  const ttsMode = String(c.tts_provider || 'browser')
  const asrMode = String(c.asr_provider || 'disabled')
  const ttsBuiltIn = ttsMode === 'browser' || ttsMode === 'edge-tts'
  const ttsReady = Boolean(ttsBuiltIn || (c.tts_model && providerReady(c.tts_provider_ref)))
  const asrReady = Boolean(asrMode === 'disabled' || (c.asr_model && providerReady(c.asr_provider_ref)))
  const proxyEnabled = !!c.proxy_enabled
  const mainDetail = mainProvider ? `${mainProvider.name || mainProvider.id} · ${c.model || t('modelUnset')}` : t('modelRoutingUnassigned')
  const ttsDetail = ttsBuiltIn
    ? (ttsMode === 'edge-tts' ? t('ttsProviderEdge') : t('ttsProviderBrowser'))
    : (ttsProviderConfig ? `${ttsProviderConfig.name || ttsProviderConfig.id} · ${c.tts_model || t('modelUnset')}` : t('modelRoutingUnassigned'))
  const asrDetail = asrMode === 'disabled'
    ? t('asrProviderDisabled')
    : (asrProviderConfig ? `${asrProviderConfig.name || asrProviderConfig.id} · ${c.asr_model || t('modelUnset')}` : t('modelRoutingUnassigned'))
  return [
    {
      label: t('statusMainModel'),
      value: mainReady ? t('statusComplete') : t('statusNeedsSetup'),
      detail: mainDetail,
      tone: mainReady ? 'success' : 'warning',
      icon: ServerOutline,
    },
    {
      label: t('statusFallback'),
      value: enabledFallbacks.length ? t('routesAvailable', { ready: readyFallbacks.length, total: enabledFallbacks.length }) : t('disabled'),
      detail: enabledFallbacks.length
        ? enabledFallbacks.map(item => `${item.name}: ${item.provider?.name || t('modelRoutingUnassigned')} · ${item.model || t('modelUnset')}`).join(' · ')
        : t('fallbackDetailHint'),
      tone: !enabledFallbacks.length ? 'default' : readyFallbacks.length === enabledFallbacks.length ? 'success' : 'warning',
      icon: CubeOutline,
    },
    {
      label: t('statusSpeechModels'),
      value: ttsReady && asrReady ? t('statusComplete') : t('statusNeedsSetup'),
      detail: `TTS: ${ttsDetail} · ASR: ${asrDetail}`,
      tone: ttsReady && asrReady ? 'success' : 'warning',
      icon: VolumeHighOutline,
    },
    {
      label: t('statusVectorMemory'),
      value: c.embedding_enabled ? (embeddingReady ? t('enabled') : t('statusIncomplete')) : t('disabled'),
      detail: `${embeddingProvider?.name || t('modelRoutingUnassigned')} · ${c.embedding_model || t('modelUnset')} · ${t('inputLimit')} ${c.embedding_max_input || t('auto')}`,
      tone: c.embedding_enabled ? (embeddingReady ? 'success' : 'warning') : 'default',
      icon: CubeOutline,
    },
    {
      label: t('statusNetworkProxy'),
      value: proxyEnabled ? t('enabled') : t('disabled'),
      detail: `${proxySourceLabel.value} · ${proxyFormatLabel.value}${c.proxy_url ? ` · ${c.proxy_url}` : ''}`,
      tone: proxyEnabled ? (c.proxy_supported === false ? 'error' : 'info') : 'default',
      icon: CloudDownloadOutline,
    },
    {
      label: t('statusAccessControl'),
      value: c.access_password?.configured ? t('passwordSet') : t('passwordUnset'),
      detail: c.access_password?.configured ? t('currentCredential', { masked: c.access_password.masked }) : t('localAccessNoPassword'),
      tone: c.access_password?.configured ? 'success' : 'default',
      icon: LockClosedOutline,
    },
  ]
})

onMounted(() => {
  void initializeBackgrounds()
  void (async () => {
    await store.load()
    await loadTtsVoices()
  })()
  void refreshStatus()
  void loadHubPreferences()
  syncRouteTarget()
})
watch(() => [route.query.section, route.query.focus], syncRouteTarget)

// 从更新弹窗的“去设置”进入：跳转后自动开始下载，用户无需再点一次下载按钮。
// 仅在 mode ∈ {source, portable}、确有新版、且无进行中/已完成任务时触发一次；
// docker/development/只读模式下 requiredUpdateKind 为 null，不触发。
let autoDownloadAttempted = false
watch(
  () => [requiredUpdateKind.value, updateStatus.value?.state, route.query.focus],
  async () => {
    if (autoDownloadAttempted) return
    const kind = requiredUpdateKind.value
    // 设置页不自动加载版本检查，先补查一次确认确有新版再决定是否下载。
    // 补查必须在守卫判断之前：否则 updateInfo 为 null（启动后首次检查失败、又未经过
    // 弹窗）时，shouldAutoDownloadUpdate 的 update_available 恒为 false，永远进不来。
    if (!updateInfo.value?.update_available) {
      try {
        const result = await checkForUpdates(true)
        if (!result?.update_available) return
      } catch {
        return
      }
    }
    const ready = shouldAutoDownloadUpdate(
      kind,
      updateStatus.value?.state,
      queryValue(route.query.focus),
      Boolean(updateInfo.value?.update_available),
    )
    if (!ready) return
    autoDownloadAttempted = true
    // ready 为 true 时 kind 必非空（shouldAutoDownloadUpdate 的守卫）。
    void downloadUpdatePackage(kind!)
  },
  { immediate: true },
)
watch(section, () => {
  const sc = document.querySelector('.n-layout-scroll-container') as HTMLElement | null
  sc?.scrollTo({ top: 0 })
})
watch(section, value => {
  if (value === 'access') void loadLoginHistory()
})

function setStr(key: keyof AppConfig, v: string | number) { store.setConfigField(key, String(v).trim()) }
function setSecret(key: SecretKey, v: string | number) { store.secrets[key] = String(v).trim() }
function eventValue(event: Event) { return (event.target as HTMLSelectElement | null)?.value || '' }
function setNum(key: keyof AppConfig, v: string | number | null) {
  if (v === null || v === '') { store.setConfigField(key, 0); return }
  store.setConfigField(key, Number(v) || 0)
}
function setBool(key: keyof AppConfig, v: string | number | boolean) { store.setConfigField(key, Boolean(v)) }

const tokenFields: { key: keyof AppConfig; labelKey: MessageKey; hintKey: MessageKey }[] = [
  { key: 'narrative_max_tokens', labelKey: 'narrativeTokens', hintKey: 'narrativeTokensHint' },
  { key: 'character_gen_max_tokens', labelKey: 'characterGenTokens', hintKey: 'characterGenTokensHint' },
  { key: 'summary_max_tokens', labelKey: 'summaryTokens', hintKey: 'summaryTokensHint' },
  { key: 'brief_max_tokens', labelKey: 'briefTokens', hintKey: 'briefTokensHint' },
  { key: 'analysis_max_tokens', labelKey: 'analysisTokens', hintKey: 'analysisTokensHint' },
  { key: 'text_gen_max_tokens', labelKey: 'textGenTokens', hintKey: 'textGenTokensHint' },
]

async function save(keys: string[], secretKeys: SecretKey[] = []) {
  try {
    await store.saveSection(keys, secretKeys)
    toast.success(t('settingsSaved'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

async function runTest(kind: 'model' | 'embedding' | 'proxy') {
  testing.value = true
  testResult.value = null
  testKind.value = kind
  try {
    testResult.value = await store.test(kind)
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    testing.value = false
  }
}

async function savePassword() {
  if (password.value.length < 6) { toast.error(t('passwordTooShort')); return }
  if (password.value !== passwordConfirm.value) { toast.error(t('passwordMismatch')); return }
  try {
    await store.saveAccessPassword(password.value)
    toast.success(t('accessPasswordUpdated'))
    password.value = passwordConfirm.value = ''
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

async function loadLoginHistory() {
  loginHistoryLoading.value = true
  loginHistoryError.value = ''
  try {
    const result = await api<LoginAuditResponse>('/login-history')
    loginHistory.value = result.entries || []
    loginHistoryPage.value = Math.min(loginHistoryPage.value, loginHistoryTotalPages.value)
  } catch (e: unknown) {
    loginHistoryError.value = errorMessage(e)
  } finally {
    loginHistoryLoading.value = false
  }
}

function formatLoginTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function goLoginHistoryPage(page: number) {
  if (page < 1 || page > loginHistoryTotalPages.value) return
  loginHistoryPage.value = page
}

async function clearProxy() {
  const ok = await confirm({
    title: t('clearProxyTitle'),
    content: t('clearProxyContent'),
    type: 'warning',
    positiveText: t('clearProxyAction'),
  })
  if (!ok) return
  try {
    await store.clearProxy()
    toast.success(t('proxyCleared'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

async function revealBotToken(): Promise<string> {
  botTokenBusy.value = true
  try {
    const result = await store.botToken('reveal')
    botToken.value = result.token
    return result.token
  } catch (e: unknown) {
    toast.error(errorMessage(e))
    return ''
  } finally {
    botTokenBusy.value = false
  }
}

async function copyBotToken() {
  const token = botToken.value || await revealBotToken()
  if (!token) return
  await copyToClipboard(token)
  toast.success(t('botApiTokenCopied'))
}

async function regenerateBotToken() {
  const ok = await confirm({
    title: t('regenerateBotApiToken'),
    content: t('regenerateBotApiTokenWarning'),
    type: 'warning',
    positiveText: t('regenerate'),
  })
  if (!ok) return
  botTokenBusy.value = true
  try {
    const result = await store.botToken('regenerate')
    botToken.value = result.token
    toast.success(t('botApiTokenRegenerated'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    botTokenBusy.value = false
  }
}

async function checkUpdate() {
  try {
    const result = await checkForUpdates(true)
    if (!result?.ok) {
      toast.error(result?.error || t('updateCheckFailed'))
    } else if (result.no_release) {
      toast.success(t('repoNoRelease'))
    } else if (result.update_available) {
      toast.success(t('updateFoundVersion', { version: result.latest?.tag_name || result.latest?.version || '' }))
    } else {
      toast.success(t('updateLatestToast'))
    }
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

function openUpdateUrl() {
  const url = updateInfo.value?.release_url || updateInfo.value?.releases_url || updateInfo.value?.source_url
  if (url) window.open(url, '_blank', 'noopener')
}

// 本次会话内由用户/自动下载触发的更新：下载完成（staged）后自动应用，
// 不再要求用户手动点"应用升级"。仅在亲眼看到 downloading->staged 的
// 转变时触发；打开页面时残留的旧 staged 状态不自动应用（避免未经确认重启服务）。
let autoApplyArmed = false

async function downloadUpdatePackage(kind: UpdatePackageKind) {
  try {
    const result = await startDownload(kind)
    if (!result.ok) {
      toast.error(result.error || t('updateDownloadFailed'))
    } else {
      autoApplyArmed = true
      toast.success(t('updateDownloadStarted'))
    }
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

watch(() => updateStatus.value?.state, (state) => {
  if (!autoApplyArmed || state !== 'staged') return
  if (updateStatus.value?.kind !== requiredUpdateKind.value) return
  autoApplyArmed = false
  void applyDownloadedUpdate()
})

async function applyDownloadedUpdate() {
  try {
    const result = await applyUpdate()
    if (!result.ok) {
      toast.error(result.error || t('updateApplyFailed'))
    }
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

async function restartProgram() {
  const ok = await confirm({
    title: t('restartProgram'),
    content: t('restartProgramConfirm'),
    type: 'warning',
    positiveText: t('restartProgramAction'),
    negativeText: t('cancel'),
  })
  if (!ok) return
  restartBusy.value = true
  try {
    const result = await restartApplication()
    toast.info(t('restartProgramStarted'))
    const ready = await waitForApplicationRestart(result.boot_id)
    if (ready) {
      window.location.reload()
      return
    }
    toast.warning(t('restartProgramTimeout'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    restartBusy.value = false
  }
}

function redownloadUpdatePackage() {
  if (requiredUpdateKind.value) {
    void downloadUpdatePackage(requiredUpdateKind.value)
  }
}
</script>

<template>
  <section class="view settings-page reference-settings-page">
    <p v-if="store.error" class="error-banner">{{ store.error }}</p>
    <div class="settings-overview-grid">
      <header class="settings-page-heading">
        <div>
          <span class="section-kicker">CONTROL ROOM</span>
          <h1>{{ t('settingsTitle') }}</h1>
          <p>{{ t('settingsSubtitle') }}</p>
        </div>
        <NButton :loading="store.loading" @click="store.load()">{{ t('refresh') }}</NButton>
      </header>
      <section class="system-status-grid" :aria-label="t('settingsSystemStatusAria')">
        <article v-for="item in systemStatusItems" :key="item.label" class="system-status-card">
          <NIcon :component="item.icon" class="system-status-icon" />
          <div class="system-status-copy">
            <div class="system-status-head">
              <span>{{ item.label }}</span>
              <NTag :type="item.tone" :class="['system-status-tag', `tone-${item.tone}`]" size="small" round>{{ item.value }}</NTag>
            </div>
            <p :title="item.detail">{{ item.detail }}</p>
          </div>
        </article>
      </section>
    </div>
    <div class="settings-layout">
      <aside class="settings-nav">
        <button
          v-for="s in sections"
          :key="s.id"
          :class="['nav-item', { active: section === s.id }]"
          @click="section = s.id"
        >
          <NIcon :component="s.icon" />
          <span>{{ t(s.labelKey) }}</span>
        </button>
      </aside>

      <div class="settings-content">
        <NSpin :show="store.loading">
          <div v-show="section === 'api'" class="settings-pane ai-management-pane">
            <header class="ai-manager-header">
              <div>
                <span class="section-kicker">MODEL CONTROL</span>
                <h3>{{ t('aiProviders') }}</h3>
                <p>{{ t('aiProvidersHint') }}</p>
              </div>
            </header>

            <div v-if="!providerLibrarySupported" class="provider-backend-warning">
              <NIcon :component="AlertCircleOutline" />
              <div>
                <strong>{{ t('providerBackendOutdatedTitle') }}</strong>
                <p>{{ t('providerBackendOutdated') }}</p>
              </div>
            </div>

            <div class="ai-provider-workspace">
              <aside class="provider-library">
                <div class="provider-search-box">
                  <NIcon :component="SearchOutline" />
                  <input v-model="providerSearch" :placeholder="t('providerSearch')">
                </div>
                <div class="provider-list">
                  <button
                    v-for="p in filteredProviderDrafts"
                    :key="p.id"
                    type="button"
                    :class="['provider-list-item', { active: activeProviderId === p.id }]"
                    @click="activeProviderId = p.id"
                  >
                    <span class="provider-avatar" :style="providerStyle(p.id)">{{ providerMark(p) }}</span>
                    <span class="provider-list-copy">
                      <strong>{{ p.name || p.base_url || t('providerNamePlaceholder') }}</strong>
                      <small>{{ t('providerCatalogCount', { count: p.models.length }) }}</small>
                    </span>
                    <i :class="{ ready: providerDraftReady(p) }" />
                  </button>
                  <p v-if="providerDrafts.length && !filteredProviderDrafts.length" class="provider-list-empty">
                    {{ t('providerSearchEmpty') }}
                  </p>
                </div>
                <footer class="provider-library-footer">
                  <button type="button" :disabled="!providerLibrarySupported" @click="addProviderDraft">
                    <NIcon :component="AddOutline" />
                    {{ t('providerAdd') }}
                  </button>
                </footer>
              </aside>

              <section v-if="activeProvider" class="provider-editor">
                <header class="provider-editor-head">
                  <div class="provider-editor-identity">
                    <span class="provider-avatar provider-avatar-large" :style="providerStyle(activeProvider.id)">
                      {{ providerMark(activeProvider) }}
                    </span>
                    <div>
                      <h4>{{ activeProvider.name || activeProvider.id }}</h4>
                      <span>{{ activeProvider.base_url || activeProvider.id }}</span>
                    </div>
                  </div>
                  <NTag :type="providerDraftReady(activeProvider) ? 'success' : 'warning'" round>
                    <NIcon :component="providerDraftReady(activeProvider) ? CheckmarkCircleOutline : AlertCircleOutline" />
                    {{ providerDraftReady(activeProvider) ? t('providerReady') : t('providerIncomplete') }}
                  </NTag>
                </header>

                <section class="provider-editor-section provider-credentials-section">
                  <div class="provider-section-title">
                    <div>
                      <h5>{{ t('providerCredentials') }}</h5>
                      <p>{{ t('providerCredentialsHint') }}</p>
                    </div>
                  </div>
                  <div class="provider-field-grid">
                    <label class="provider-field">
                      <span>{{ t('providerName') }}</span>
                      <NInput
                        :value="activeProvider.name"
                        :placeholder="t('providerNamePlaceholder')"
                        @update:value="activeProvider.name = String($event)"
                      />
                    </label>
                    <label class="provider-field">
                      <span>{{ t('apiFormat') }}</span>
                      <select :value="activeProvider.api_format" @change="activeProvider.api_format = eventValue($event)">
                        <option value="openai">{{ t('apiFormatOpenAI') }}</option>
                        <option value="anthropic">Anthropic</option>
                      </select>
                    </label>
                    <label class="provider-field provider-field-wide">
                      <span>API Key</span>
                      <NInput
                        :value="store.secrets[providerSecretKey(activeProvider.id)] ?? ''"
                        type="password"
                        show-password-on="click"
                        :placeholder="activeProvider.configuredMasked ? t('secretConfiguredPlaceholder', { masked: activeProvider.configuredMasked }) : ''"
                        @update:value="setProviderSecret(activeProvider.id, $event)"
                      />
                    </label>
                    <label class="provider-field provider-field-wide">
                      <span>Base URL</span>
                      <NInput
                        :value="activeProvider.base_url"
                        placeholder="https://api.example.com/v1"
                        @update:value="activeProvider.base_url = String($event).trim()"
                      />
                    </label>
                  </div>
                </section>

                <section class="provider-editor-section provider-models-section">
                  <div class="provider-section-title provider-model-toolbar">
                    <div>
                      <h5>{{ t('providerModels') }}</h5>
                      <p>{{ t('providerModelsHint') }}</p>
                    </div>
                    <NButton
                      size="small"
                      :loading="providerFetchingModelsId === activeProvider.id"
                      @click="openProviderCatalog(activeProvider)"
                    >
                      <template #icon><NIcon :component="CloudDownloadOutline" /></template>
                      {{ t('providerSelectModels') }}
                    </NButton>
                  </div>

                  <div v-if="activeProviderModelGroups.length" class="provider-model-groups">
                    <section v-for="group in activeProviderModelGroups" :key="group.name" class="provider-model-group">
                      <header>
                        <strong>{{ group.name }}</strong>
                        <span>{{ group.models.length }}</span>
                      </header>
                      <div class="provider-model-list">
                        <article v-for="modelName in group.models" :key="modelName" class="provider-model-row">
                          <span class="provider-model-orbit"><i /><i /></span>
                          <div class="provider-model-copy">
                            <strong>{{ modelName }}</strong>
                            <small>{{ modelCapabilityLabels(modelName).join(' · ') }}</small>
                          </div>
                          <button
                            type="button"
                            class="provider-model-remove"
                            :title="t('providerRemove')"
                            @click="removeProviderModel(activeProvider, modelName)"
                          >
                            <NIcon :component="TrashOutline" />
                          </button>
                        </article>
                      </div>
                    </section>
                  </div>
                  <p v-else class="provider-model-empty">{{ t('providerNoModels') }}</p>
                </section>

                <section class="provider-editor-section provider-test-section">
                  <label class="provider-field provider-field-wide">
                    <span>{{ t('providerTestModel') }}</span>
                    <NInput
                      :value="providerTestModels[activeProvider.id] ?? ''"
                      :placeholder="activeProvider.models[0] || String(store.config.model || 'gpt-4o-mini')"
                      @update:value="setActiveProviderTestModel"
                    />
                  </label>
                  <div class="provider-test-actions">
                    <NButton
                      :loading="providerTestingId === activeProvider.id"
                      @click="testProviderDraft(activeProvider, providerTestModels[activeProvider.id] || activeProvider.models[0] || '')"
                    >{{ t('testConnection') }}</NButton>
                    <NButton type="primary" :loading="providerSaving" :disabled="!providerLibrarySupported" @click="saveProvidersList">{{ t('providerSave') }}</NButton>
                    <NButton
                      quaternary
                      type="error"
                      @click="removeProviderDraft(providerDrafts.findIndex(provider => provider.id === activeProvider?.id))"
                    >{{ t('providerRemove') }}</NButton>
                  </div>
                  <TestResultCard
                    v-if="providerTestedId === activeProvider.id && providerTestResult"
                    :result="providerTestResult"
                    kind="model"
                  />
                </section>
              </section>

              <section v-else class="provider-editor provider-editor-empty">
                <span class="provider-empty-sigil"><NIcon :component="ServerOutline" /></span>
                <h4>{{ t('providerEmptyTitle') }}</h4>
                <p>{{ t('providerEmptyHint') }}</p>
                <div class="provider-empty-actions">
                  <NButton type="primary" :disabled="!providerLibrarySupported" @click="addProviderDraft">{{ t('providerAdd') }}</NButton>
                </div>
              </section>
            </div>

          </div>

          <NModal
            v-model:show="providerCatalogOpen"
            preset="card"
            class="provider-catalog-modal"
            :title="t('providerCatalogTitle', { name: activeCatalogProvider?.name || activeCatalogProvider?.id || '' })"
          >
            <div class="provider-catalog-tools">
              <label class="provider-catalog-search">
                <NIcon :component="SearchOutline" />
                <input v-model="providerCatalogSearch" :placeholder="t('modelPickerSearch')">
              </label>
              <NButton
                :loading="providerFetchingModelsId === activeCatalogProvider?.id"
                :disabled="!activeCatalogProvider"
                @click="activeCatalogProvider && fetchProviderModels(activeCatalogProvider)"
              >
                <template #icon><NIcon :component="RefreshOutline" /></template>
                {{ t('providerRefreshCatalog') }}
              </NButton>
            </div>
            <div class="provider-catalog-filters">
              <button
                v-for="filter in providerCatalogFilters"
                :key="filter.id"
                type="button"
                :class="{ active: providerCatalogFilter === filter.id }"
                @click="providerCatalogFilter = filter.id"
              >
                {{ filter.label }} <span>{{ filter.count }}</span>
              </button>
            </div>
            <div class="provider-catalog-body">
              <section v-for="group in providerCatalogGroups" :key="group.name" class="provider-catalog-group">
                <header><strong>{{ group.name }}</strong><span>{{ group.models.length }}</span></header>
                <button
                  v-for="modelName in group.models"
                  :key="modelName"
                  type="button"
                  :class="['provider-catalog-row', { selected: isCatalogModelSelected(modelName) }]"
                  @click="toggleCatalogModel(modelName)"
                >
                  <span class="provider-model-orbit"><i /><i /></span>
                  <span class="provider-catalog-copy">
                    <strong>{{ modelName }}</strong>
                    <small>{{ modelCapabilityLabels(modelName).join(' · ') }}</small>
                  </span>
                  <span class="provider-catalog-toggle">{{ isCatalogModelSelected(modelName) ? '−' : '+' }}</span>
                </button>
              </section>
              <p v-if="!providerCatalogGroups.length" class="provider-model-empty">{{ t('modelPickerEmpty') }}</p>
            </div>
            <footer class="provider-catalog-footer">
              <div class="provider-catalog-custom">
                <input v-model="providerCatalogCustomModel" :placeholder="t('providerModelPlaceholder')" @keydown.enter.prevent="addCustomProviderModel">
                <button type="button" @click="addCustomProviderModel">{{ t('providerAddModel') }}</button>
              </div>
              <NButton :disabled="!providerCatalogFilteredModels.length" @click="addAllCatalogModels">
                {{ t('providerAddAllModels') }}
              </NButton>
            </footer>
          </NModal>

          <div v-show="section === 'models'" class="settings-pane model-routing-pane">
            <header class="model-routing-header">
              <div>
                <span class="section-kicker">MODEL ROUTING</span>
                <h3>{{ t('modelRoutingTitle') }}</h3>
                <p>{{ t('modelRoutingHint') }}</p>
              </div>
              <NButton type="primary" :loading="modelRoutingSaving" :disabled="!providerLibrarySupported" @click="saveModelRouting">
                {{ t('modelRoutingSave') }}
              </NButton>
            </header>

            <div v-if="!providerLibrarySupported" class="provider-backend-warning compact">
              <NIcon :component="AlertCircleOutline" />
              <div><strong>{{ t('providerBackendOutdatedTitle') }}</strong><p>{{ t('providerBackendOutdated') }}</p></div>
            </div>
            <div v-else-if="!(store.config.ai_providers || []).length" class="model-routing-empty">
              <NIcon :component="ServerOutline" />
              <div><strong>{{ t('modelRoutingNoProviders') }}</strong><p>{{ t('modelRoutingNoProvidersHint') }}</p></div>
              <NButton @click="section = 'api'">{{ t('providerAdd') }}</NButton>
            </div>

            <div v-if="providerLibrarySupported && (store.config.ai_providers || []).length" class="model-routing-grid">
              <article class="model-role-card">
                <header><NIcon :component="SparklesOutline" /><div><h4>{{ t('modelRoleMain') }}</h4><p>{{ t('modelRoleMainHint') }}</p></div></header>
                <label><span>{{ t('providerName') }}</span><select :value="store.config.llm_provider_ref || ''" @change="setModelRoleProvider('llm_provider_ref', 'model', eventValue($event), 'chat')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="p in store.config.ai_providers || []" :key="p.id" :value="p.id">{{ p.name || p.id }}</option></select></label>
                <label><span>{{ t('model') }}</span><select :value="store.config.model || ''" :disabled="!store.config.llm_provider_ref" @change="setStr('model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="modelName in savedProviderModels(String(store.config.llm_provider_ref || ''), 'chat')" :key="modelName" :value="modelName">{{ modelName }}</option></select></label>
              </article>

              <article class="model-role-card">
                <header><NIcon :component="CubeOutline" /><div><h4>{{ t('fallbackSlot1') }}</h4><p>{{ t('modelRoleFallbackHint') }}</p></div></header>
                <label class="model-role-enabled"><span>{{ t('enabled') }}</span><NSwitch :value="!!store.config.fallback1_enabled" @update:value="setBool('fallback1_enabled', $event)" /></label>
                <label><span>{{ t('providerName') }}</span><select :value="store.config.fallback1_provider_ref || ''" :disabled="!store.config.fallback1_enabled" @change="setModelRoleProvider('fallback1_provider_ref', 'fallback1_model', eventValue($event), 'chat')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="p in store.config.ai_providers || []" :key="p.id" :value="p.id">{{ p.name || p.id }}</option></select></label>
                <label><span>{{ t('model') }}</span><select :value="store.config.fallback1_model || ''" :disabled="!store.config.fallback1_enabled || !store.config.fallback1_provider_ref" @change="setStr('fallback1_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="modelName in savedProviderModels(String(store.config.fallback1_provider_ref || ''), 'chat')" :key="modelName" :value="modelName">{{ modelName }}</option></select></label>
              </article>

              <article class="model-role-card">
                <header><NIcon :component="CubeOutline" /><div><h4>{{ t('fallbackSlot2') }}</h4><p>{{ t('modelRoleFallbackHint') }}</p></div></header>
                <label class="model-role-enabled"><span>{{ t('enabled') }}</span><NSwitch :value="!!store.config.fallback2_enabled" @update:value="setBool('fallback2_enabled', $event)" /></label>
                <label><span>{{ t('providerName') }}</span><select :value="store.config.fallback2_provider_ref || ''" :disabled="!store.config.fallback2_enabled" @change="setModelRoleProvider('fallback2_provider_ref', 'fallback2_model', eventValue($event), 'chat')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="p in store.config.ai_providers || []" :key="p.id" :value="p.id">{{ p.name || p.id }}</option></select></label>
                <label><span>{{ t('model') }}</span><select :value="store.config.fallback2_model || ''" :disabled="!store.config.fallback2_enabled || !store.config.fallback2_provider_ref" @change="setStr('fallback2_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="modelName in savedProviderModels(String(store.config.fallback2_provider_ref || ''), 'chat')" :key="modelName" :value="modelName">{{ modelName }}</option></select></label>
              </article>

              <article class="model-role-card">
                <header><NIcon :component="CubeOutline" /><div><h4>{{ t('modelRoleEmbedding') }}</h4><p>{{ t('modelRoleEmbeddingHint') }}</p></div></header>
                <label><span>{{ t('providerName') }}</span><select :value="store.config.embedding_provider_ref || ''" @change="setModelRoleProvider('embedding_provider_ref', 'embedding_model', eventValue($event), 'embedding')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="p in store.config.ai_providers || []" :key="p.id" :value="p.id">{{ p.name || p.id }}</option></select></label>
                <label><span>{{ t('model') }}</span><select :value="store.config.embedding_model || ''" :disabled="!store.config.embedding_provider_ref" @change="setStr('embedding_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="modelName in savedProviderModels(String(store.config.embedding_provider_ref || ''), 'embedding')" :key="modelName" :value="modelName">{{ modelName }}</option></select></label>
              </article>

              <article class="model-role-card">
                <header><NIcon :component="VolumeHighOutline" /><div><h4>{{ t('modelRoleTts') }}</h4><p>{{ t('modelRoleTtsHint') }}</p></div></header>
                <label><span>{{ t('modelRoutingMode') }}</span><select :value="store.config.tts_provider || 'browser'" @change="setTtsProvider(eventValue($event))"><option value="browser">{{ t('ttsProviderBrowser') }}</option><option value="edge-tts">{{ t('ttsProviderEdge') }}</option><option value="openai-compatible">{{ t('ttsProviderOpenAI') }}</option><option value="gpt-sovits">GPT-SoVITS</option></select></label>
                <template v-if="ttsProvider === 'openai-compatible' || ttsProvider === 'gpt-sovits'">
                  <label><span>{{ t('providerName') }}</span><select :value="store.config.tts_provider_ref || ''" @change="setModelRoleProvider('tts_provider_ref', 'tts_model', eventValue($event), 'tts')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="p in store.config.ai_providers || []" :key="p.id" :value="p.id">{{ p.name || p.id }}</option></select></label>
                  <label><span>{{ t('model') }}</span><select :value="store.config.tts_model || ''" :disabled="!store.config.tts_provider_ref" @change="setStr('tts_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="modelName in savedProviderModels(String(store.config.tts_provider_ref || ''), 'tts')" :key="modelName" :value="modelName">{{ modelName }}</option></select></label>
                </template>
              </article>

              <article class="model-role-card">
                <header><NIcon :component="MicOutline" /><div><h4>{{ t('modelRoleAsr') }}</h4><p>{{ t('modelRoleAsrHint') }}</p></div></header>
                <label><span>{{ t('modelRoutingMode') }}</span><select :value="store.config.asr_provider || 'disabled'" @change="setAsrProvider(eventValue($event))"><option value="disabled">{{ t('asrProviderDisabled') }}</option><option value="openai-compatible">{{ t('asrProviderOpenAI') }}</option></select></label>
                <template v-if="asrProvider === 'openai-compatible'">
                  <label><span>{{ t('providerName') }}</span><select :value="store.config.asr_provider_ref || ''" @change="setModelRoleProvider('asr_provider_ref', 'asr_model', eventValue($event), 'asr')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="p in store.config.ai_providers || []" :key="p.id" :value="p.id">{{ p.name || p.id }}</option></select></label>
                  <label><span>{{ t('model') }}</span><select :value="store.config.asr_model || ''" :disabled="!store.config.asr_provider_ref" @change="setStr('asr_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="modelName in savedProviderModels(String(store.config.asr_provider_ref || ''), 'asr')" :key="modelName" :value="modelName">{{ modelName }}</option></select></label>
                </template>
              </article>
            </div>
          </div>

          <div v-show="section === 'memory'" class="settings-pane">
            <div class="api-head-row"><h3>{{ t('vectorMemory') }}</h3><HelpButton :title="t('embeddingHelpTitle')">
              <h4>{{ t('embeddingHelpWhatTitle') }}</h4>
              <p>{{ t('embeddingHelpWhatText') }}</p>
              <h4>{{ t('embeddingHelpChooseTitle') }}</h4>
              <p>{{ t('embeddingHelpChooseBefore') }} <code>bge-m3</code>{{ t('embeddingHelpChooseAfter') }} <code>text-embedding-3-small</code>, <code>gte-large</code>, <code>nomic-embed-text</code>{{ t('embeddingHelpChooseSuffix') }}</p>
              <h4>{{ t('embeddingHelpConfigTitle') }}</h4>
              <p>{{ t('embeddingHelpCentralized') }}</p>
              <h4>{{ t('test') }}</h4>
              <p>{{ t('embeddingHelpTest') }}</p>
            </HelpButton></div>
            <div class="form-row"><label>{{ t('vectorMemory') }}</label><div class="switch-inline"><NSwitch :value="!!store.config.embedding_enabled" @update:value="setBool('embedding_enabled', $event)" /><span>{{ t('enabled') }}</span></div></div>
            <div class="model-binding-summary">
              <NIcon :component="CubeOutline" />
              <div><strong>{{ t('modelRoleEmbedding') }}</strong><small>{{ modelBindingSummary(store.config.embedding_provider_ref, store.config.embedding_model) }}</small></div>
              <NButton size="small" @click="section = 'models'">{{ t('modelRoutingOpen') }}</NButton>
            </div>
            <div class="form-row"><label>{{ t('maxInput') }}</label><NInputNumber :value="store.config.embedding_max_input ?? 0" @update:value="setNum('embedding_max_input', $event)" style="width:100%" /></div>
            <p class="form-hint">{{ t('maxInputHint') }}</p>
            <div class="actions-row">
              <NButton type="primary" @click="save(['embedding_enabled', 'embedding_max_input'])">{{ t('saveAction') }}</NButton>
              <NButton :loading="testing && testKind === 'embedding'" @click="runTest('embedding')">{{ t('testEmbeddingConnection') }}</NButton>
            </div>
            <TestResultCard v-if="testKind === 'embedding' && testResult" :result="testResult" kind="embedding" />
          </div>

          <div v-show="section === 'network'" class="settings-pane">
            <h3>{{ t('networkProxy') }}</h3>
            <div class="proxy-status">
              <NTag :type="store.config.proxy_source ? 'info' : 'default'" size="small">{{ t('source') }}: {{ proxySourceLabel }}</NTag>
              <NTag :type="store.config.proxy_supported ? 'success' : 'error'" size="small">{{ t('format') }}: {{ proxyFormatLabel }}</NTag>
            </div>
            <div class="form-row"><label>{{ t('proxy') }}</label><div class="switch-inline"><NSwitch :value="!!store.config.proxy_enabled" @update:value="setBool('proxy_enabled', $event)" /><span>{{ t('enabled') }}</span></div></div>
            <div class="form-row">
              <label>{{ t('proxyUrl') }}</label>
              <NInput :value="store.secrets.proxy_url ?? ''" :placeholder="store.config.proxy_url || t('proxyUrlPlaceholder')" @update:value="setSecret('proxy_url', $event)" />
            </div>
            <div class="actions-row">
              <NButton type="primary" @click="save(['proxy_enabled'], ['proxy_url'])">{{ t('saveAction') }}</NButton>
              <NButton :loading="testing && testKind === 'proxy'" @click="runTest('proxy')">{{ t('testConnection') }}</NButton>
              <NButton @click="clearProxy">{{ t('clearProxyAction') }}</NButton>
            </div>
            <p class="muted">{{ t('proxyHint') }}</p>
            <TestResultCard v-if="testKind === 'proxy' && testResult" :result="testResult" kind="proxy" />
          </div>

          <div v-show="section === 'sharing'" class="settings-pane">
            <h3>{{ t('sharingLinkAddress') }}</h3>
            <p class="muted">{{ t('sharingHelp') }}</p>
            <div class="form-row">
              <label>{{ t('publicBaseUrl') }}</label>
              <NInput
                :value="store.config.public_base_url ?? ''"
                :placeholder="t('publicBaseUrlPlaceholder')"
                @update:value="setStr('public_base_url', $event)"
              />
            </div>
            <p class="form-hint">{{ t('publicBaseUrlHint', { origin: locationOrigin }) }}</p>
            <div class="actions-row">
              <NButton type="primary" @click="save(['public_base_url'])">{{ t('saveSharingAddress') }}</NButton>
            </div>
          </div>

          <div v-show="section === 'botapi'" class="settings-pane">
            <h3>{{ t('botApiTitle') }}</h3>
            <p class="muted">{{ t('botApiHelp') }}</p>
            <div class="form-row">
              <label>{{ t('diceFrameServiceAddress') }}</label>
              <NInput :value="botApiAddress" readonly />
            </div>
            <p class="form-hint">{{ t('botApiAddressHint') }}</p>
            <div class="form-row">
              <label>{{ t('botApiToken') }}</label>
              <NInput
                :value="botToken"
                type="password"
                show-password-on="click"
                readonly
                :placeholder="store.config.bot_token?.configured ? t('botApiTokenReady', { masked: store.config.bot_token.masked }) : t('botApiTokenNotReady')"
              />
            </div>
            <div class="actions-row">
              <NButton :loading="botTokenBusy" @click="revealBotToken">
                <template #icon><NIcon :component="EyeOutline" /></template>
                {{ t('showBotApiToken') }}
              </NButton>
              <NButton :loading="botTokenBusy" @click="copyBotToken">
                <template #icon><NIcon :component="CopyOutline" /></template>
                {{ t('copyBotApiToken') }}
              </NButton>
              <NButton secondary type="warning" :loading="botTokenBusy" :disabled="store.config.bot_token_source === 'env'" @click="regenerateBotToken">
                <template #icon><NIcon :component="RefreshOutline" /></template>
                {{ t('regenerateBotApiToken') }}
              </NButton>
            </div>
            <p class="muted">{{ t('botApiBuiltinHint') }}</p>
            <p class="muted">{{ t('botApiMaiBotHint') }}</p>
            <p v-if="store.config.bot_token_source === 'env'" class="muted">{{ t('botApiEnvManagedHint') }}</p>
          </div>

          <div v-show="section === 'appearance'" class="settings-pane appearance-pane">
            <div class="api-head-row">
              <div>
                <h3>{{ t('appearanceTitle') }}</h3>
                <p class="muted">{{ t('appearanceSubtitle') }}</p>
              </div>
            </div>
            <section class="appearance-mode-section">
              <h4>{{ t('appearanceMode') }}</h4>
              <div class="appearance-mode-grid">
                <button :class="{ active: themeMode === 'dark' }" @click="applyThemeMode('dark')">
                  <span class="mode-preview mode-preview-dark" aria-hidden="true"><i /><i /><i /></span>
                  <strong>{{ t('darkMode') }}</strong>
                </button>
                <button :class="{ active: themeMode === 'light' }" @click="applyThemeMode('light')">
                  <span class="mode-preview mode-preview-light" aria-hidden="true"><i /><i /><i /></span>
                  <strong>{{ t('lightMode') }}</strong>
                </button>
              </div>
            </section>
            <section class="appearance-skin-section">
              <h4>{{ t('appearanceColor') }}</h4>
              <div class="appearance-skin-grid">
                <button
                  v-for="item in builtinSkins"
                  :key="item.id"
                  :class="{ active: activeSkin === item.id }"
                  @click="applySkin(item.id)"
                >
                  <span class="appearance-swatches" aria-hidden="true"><i v-for="color in item.swatches" :key="color" :style="{ background: color }" /></span>
                  <span><strong>{{ item.name }}</strong><small>{{ item.description }}</small></span>
                </button>
              </div>
            </section>
            <section class="appearance-background-section">
              <div class="appearance-section-heading">
                <div>
                  <h4><NIcon :component="ImageOutline" />{{ t('appearanceBackgrounds') }}</h4>
                  <p>{{ t('appearanceBackgroundsHint') }}</p>
                </div>
                <NButton size="small" :loading="backgroundBusy === 'all'" @click="resetBackgrounds">
                  {{ t('resetAllBackgrounds') }}
                </NButton>
              </div>
              <div class="appearance-background-grid" :class="{ loading: backgroundsLoading }">
                <article v-for="item in backgroundOptions" :key="item.id" class="background-option-card">
                  <div
                    class="background-option-preview"
                    :style="{ backgroundImage: `linear-gradient(180deg, transparent, color-mix(in srgb, var(--df-canvas) 66%, transparent)), url('${backgroundPreviews[item.id]}')` }"
                  >
                    <span v-if="customBackgrounds[item.id]">{{ t('localCustomImage') }}</span>
                    <span v-else class="builtin-background-badge">{{ t('builtin') }}</span>
                  </div>
                  <div class="background-option-copy">
                    <strong>{{ t(item.titleKey) }}</strong>
                    <small>{{ t(item.descriptionKey) }}</small>
                  </div>
                  <div class="background-option-actions">
                    <label class="background-file-button">
                      <input type="file" accept="image/jpeg,image/png,image/webp,image/avif" @change="onBackgroundFile(item.id, $event)">
                      <span>{{ t('chooseImage') }}</span>
                    </label>
                    <NButton size="small" :disabled="!customBackgrounds[item.id]" :loading="backgroundBusy === item.id" @click="resetOneBackground(item.id)">
                      {{ t('reset') }}
                    </NButton>
                  </div>
                </article>
              </div>
              <p class="appearance-local-note">{{ t('appearanceBackgroundLocalOnly') }}</p>
            </section>
            <div class="appearance-plugin-callout">
              <div><strong>{{ t('pluginThemes') }}</strong><p>{{ t('pluginThemesHint') }}</p></div>
              <RouterLink to="/plugins">{{ t('settingsSectionPlugins') }}</RouterLink>
            </div>
          </div>

          <div v-show="section === 'access'" class="settings-pane">
            <h3>{{ t('accessPassword') }}</h3>
            <p class="muted">{{ t('accessPasswordHelp') }}</p>
            <div class="form-row"><label>{{ t('newPassword') }}</label><NInput v-model:value="password" type="password" show-password-on="click" :placeholder="t('passwordMinPlaceholder')" /></div>
            <div class="form-row"><label>{{ t('repeatPassword') }}</label><NInput v-model:value="passwordConfirm" type="password" show-password-on="click" /></div>
            <div class="actions-row">
              <NButton type="primary" @click="savePassword">{{ t('savePassword') }}</NButton>
            </div>
            <p v-if="store.config.access_password?.configured" class="muted">{{ t('currentPasswordSet', { masked: store.config.access_password.masked }) }}</p>
            <div class="login-history-head">
              <div>
                <h4>{{ t('recentLogins') }}</h4>
                <p class="muted">{{ t('loginHistoryHint') }}</p>
              </div>
              <NButton size="small" :loading="loginHistoryLoading" @click="loadLoginHistory">
                <template #icon><NIcon :component="RefreshOutline" /></template>
                {{ t('refresh') }}
              </NButton>
            </div>
            <p v-if="loginHistoryError" class="error-text">{{ loginHistoryError }}</p>
            <template v-else-if="loginHistory.length">
              <div class="login-history-list">
                <div v-for="(entry, index) in pagedLoginHistory" :key="`${entry.at}-${entry.ip}-${index}`" class="login-history-row">
                  <span>{{ formatLoginTime(entry.at) }}</span>
                  <code>{{ entry.ip }}</code>
                  <NTag :type="entry.success ? 'success' : 'error'" size="small" round>
                    {{ entry.success ? t('loginSucceeded') : t('loginFailed') }}
                  </NTag>
                </div>
              </div>
              <nav v-if="loginHistoryTotalPages > 1" class="memory-pager login-history-pager">
                <NButton size="small" :disabled="loginHistoryPage <= 1" @click="goLoginHistoryPage(loginHistoryPage - 1)">
                  {{ t('previousPage') }}
                </NButton>
                <span>{{ t('pageOf', { page: loginHistoryPage, total: loginHistoryTotalPages }) }}</span>
                <NButton size="small" :disabled="loginHistoryPage >= loginHistoryTotalPages" @click="goLoginHistoryPage(loginHistoryPage + 1)">
                  {{ t('nextPage') }}
                </NButton>
              </nav>
            </template>
            <p v-else-if="!loginHistoryLoading" class="muted">{{ t('noLoginHistory') }}</p>
          </div>

          <div v-show="section === 'advanced'" class="settings-pane advanced-settings-pane">
            <section class="advanced-section tts-section">
              <header class="advanced-section-head">
                <NIcon :component="OptionsOutline" />
                <div><h3>{{ t('ttsSettings') }}</h3><p>{{ t('ttsSettingsHint') }}</p></div>
              </header>
              <div class="model-binding-summary advanced-binding-summary">
                <NIcon :component="VolumeHighOutline" />
                <div>
                  <strong>{{ t('modelRoleTts') }}</strong>
                  <small>{{ ttsProvider === 'browser' ? t('ttsProviderBrowser') : ttsProvider === 'edge-tts' ? t('ttsProviderEdge') : modelBindingSummary(store.config.tts_provider_ref, store.config.tts_model) }}</small>
                </div>
                <NButton size="small" @click="section = 'models'">{{ t('modelRoutingOpen') }}</NButton>
              </div>
              <template v-if="ttsProvider !== 'browser'">
                <div v-if="ttsProvider !== 'edge-tts'" class="advanced-row">
                  <div><strong>{{ t('ttsAudioFormat') }}</strong></div>
                  <select :value="store.config.tts_audio_format ?? 'mp3'" @change="setStr('tts_audio_format', eventValue($event))">
                    <option value="mp3">MP3</option><option value="wav">WAV</option><option value="opus">Opus</option><option value="flac">FLAC</option><option value="aac">AAC</option>
                  </select>
                </div>
                <p v-if="ttsProvider === 'edge-tts'" class="muted tts-inline-hint">{{ t('ttsEdgeHint') }}</p>
                <div class="advanced-row">
                  <div><strong>{{ t('ttsCacheSize') }}</strong></div>
                  <NInputNumber :value="Number(store.config.tts_cache_mb ?? 256)" :min="16" :max="2048" :step="64" @update:value="setNum('tts_cache_mb', $event)" />
                </div>
                <TtsVoiceProfiles :provider="ttsProvider" @changed="loadTtsVoices" />
              </template>

              <div class="advanced-row tts-subheading-row">
                <div><strong>{{ t('ttsRoleMapping') }}</strong><small>{{ t('ttsRoleMappingHint') }}</small></div>
              </div>
              <template v-if="ttsProvider !== 'browser'">
                <div class="advanced-row">
                  <div><strong>{{ t('ttsDefaultVoice') }}</strong></div>
                  <input :value="store.config.tts_default_voice ?? ''" list="diceframe-tts-voices" @input="setStr('tts_default_voice', eventValue($event))" />
                </div>
                <datalist id="diceframe-tts-voices">
                  <option v-for="voice in ttsVoiceOptions" :key="voice.id" :value="voice.id">{{ voice.name }}</option>
                </datalist>
                <div class="advanced-row">
                  <div><strong>{{ t('ttsGmVoice') }}</strong></div>
                  <input :value="store.config.tts_gm_voice ?? ''" list="diceframe-tts-voices" :placeholder="t('ttsFollowDefault')" @input="setStr('tts_gm_voice', eventValue($event))" />
                </div>
                <div class="advanced-row">
                  <div><strong>{{ t('ttsPlayerVoice') }}</strong></div>
                  <input :value="store.config.tts_player_voice ?? ''" list="diceframe-tts-voices" :placeholder="t('ttsFollowDefault')" @input="setStr('tts_player_voice', eventValue($event))" />
                </div>
                <p v-if="ttsProvider === 'gpt-sovits' && !ttsVoiceOptions.length" class="muted tts-inline-hint">{{ t('ttsGptVoiceHint') }}</p>
              </template>
              <p v-else class="muted tts-inline-hint">{{ t('ttsBrowserVoiceMappingHint') }}</p>

              <div class="advanced-row">
                <div><strong>{{ t('ttsAutoSpeak') }}</strong><small>{{ t('ttsAutoSpeakHint') }}</small></div>
                <div class="switch-inline"><NSwitch :value="autoSpeak" @update:value="setAutoSpeak" /><span>{{ t('enabled') }}</span></div>
              </div>
              <div class="advanced-row">
                <div><strong>{{ t('ttsRate') }}</strong><small>{{ t('ttsRateHint') }}</small></div>
                <NInputNumber class="advanced-number" :value="ttsRateValue" :min="0.5" :max="5" :step="0.1" @update:value="setTtsRateValue" />
              </div>
              <footer class="advanced-save-row">
                <NButton type="primary" @click="saveTts()">{{ t('saveAction') }}</NButton>
                <NButton v-if="ttsProvider !== 'browser'" :loading="ttsTesting" @click="testTts">{{ t('ttsSaveAndTest') }}</NButton>
              </footer>
            </section>
            <section class="advanced-section hub-section">
              <header class="advanced-section-head">
                <NIcon :component="InformationCircleOutline" />
                <div><h3>{{ t('hubPrivacyTitle') }}</h3><p>{{ t('hubTelemetryChoiceSummary') }}</p></div>
              </header>
              <div class="advanced-row">
                <div>
                  <strong>{{ t('hubTelemetryChoiceTitle') }}</strong>
                  <small>{{ hubPreferences?.choice_made ? t('hubChoiceRecorded') : t('hubChoiceNotRecorded') }}</small>
                </div>
                <div class="switch-inline">
                  <NSwitch
                    :value="Boolean(hubPreferences?.telemetry_enabled)"
                    :loading="hubPrivacyBusy"
                    :disabled="!hubPreferences?.available"
                    @update:value="toggleHubTelemetry"
                  />
                  <span>{{ hubPreferences?.telemetry_enabled ? t('enabled') : t('disabled') }}</span>
                </div>
              </div>
              <div class="advanced-row">
                <div>
                  <strong>{{ t('hubInstallationIdentity') }}</strong>
                  <small>{{ hubPreferences?.identity_created ? t('hubIdentityExists') : t('hubIdentityNotCreated') }}</small>
                </div>
                <NButton
                  type="error"
                  tertiary
                  :disabled="!hubPreferences?.identity_created"
                  :loading="hubPrivacyBusy"
                  @click="clearHubIdentity"
                >
                  {{ t('hubClearIdentity') }}
                </NButton>
              </div>
            </section>
            <section class="advanced-section generation-section">
              <header class="advanced-section-head">
                <NIcon :component="OptionsOutline" />
                <div><h3>{{ t('generationParams') }}</h3><p>{{ t('generationParamsHint') }}</p></div>
              </header>
              <div v-for="item in tokenFields" :key="item.key" class="advanced-row token-row">
                <div><strong>{{ t(item.labelKey) }}</strong><small>{{ t(item.hintKey) }}</small></div>
                <div class="token-input-wrap">
                  <NInputNumber class="advanced-number" :value="Number(store.config[item.key] ?? 0)" :step="256" @update:value="setNum(item.key, $event)" />
                  <span>Token</span>
                </div>
              </div>
              <footer class="advanced-save-row">
                <NButton type="primary" @click="save(['narrative_max_tokens', 'character_gen_max_tokens', 'summary_max_tokens', 'brief_max_tokens', 'analysis_max_tokens', 'text_gen_max_tokens'])">{{ t('saveAction') }}</NButton>
              </footer>
            </section>
            <section class="advanced-section asr-section">
              <header class="advanced-section-head">
                <NIcon :component="MicOutline" />
                <div><h3>{{ t('asrSettings') }}</h3><p>{{ t('asrSettingsHint') }}</p></div>
              </header>
              <div class="model-binding-summary advanced-binding-summary">
                <NIcon :component="MicOutline" />
                <div><strong>{{ t('modelRoleAsr') }}</strong><small>{{ asrProvider === 'disabled' ? t('asrProviderDisabled') : modelBindingSummary(store.config.asr_provider_ref, store.config.asr_model) }}</small></div>
                <NButton size="small" @click="section = 'models'">{{ t('modelRoutingOpen') }}</NButton>
              </div>
              <template v-if="asrProvider === 'openai-compatible'">
                <div class="advanced-row">
                  <div><strong>{{ t('asrTimeout') }}</strong></div>
                  <NInputNumber :value="Number(store.config.asr_timeout_seconds ?? 60)" :min="5" :max="300" :step="5" @update:value="setNum('asr_timeout_seconds', $event)" />
                </div>
                <p class="muted tts-inline-hint">{{ t('asrHint') }}</p>
              </template>
              <footer class="advanced-save-row">
                <NButton type="primary" @click="saveAsr()">{{ t('saveAction') }}</NButton>
                <NButton
                  v-if="asrProvider === 'openai-compatible'"
                  :loading="asrTesting && !asrTestRecording"
                  @click="testAsr"
                >{{ asrTestRecording ? t('asrTestStop') : t('asrSaveAndTest') }}</NButton>
              </footer>
              <p v-if="asrTestText" class="muted tts-inline-hint">{{ t('asrTestResult', { text: asrTestText }) }}</p>
            </section>
          </div>

          <div v-show="section === 'about'" class="settings-pane about">
            <section id="settings-update" class="update-card" :aria-label="t('versionUpdate')">
              <div class="update-card-head">
                <div>
                  <h4>{{ t('versionUpdate') }}</h4>
                </div>
                <NTag :type="updateTagType" size="small" round>{{ updateTagLabel }}</NTag>
              </div>
              <div class="update-meta">
                <span>{{ t('currentVersion') }}: {{ updateInfo?.current_version || t('clickCheckVersion') }}</span>
                <span class="update-channel-inline" :title="t('updateChannelHint')">
                  <NSwitch
                    size="small"
                    :value="updateChannel === 'preview'"
                    :disabled="isUpdateBusy"
                    @update:value="toggleUpdateChannel"
                  />
                  <span>{{ t('updateChannel') }}</span>
                  <NTag v-if="updateChannel === 'preview'" size="small" type="warning">{{ t('previewChannel') }}</NTag>
                </span>
                <span v-if="updateInfo?.latest">{{ t('latestVersion') }}: {{ updateInfo.latest.tag_name || updateInfo.latest.version }}<NTag v-if="updateInfo?.latest?.prerelease" size="small" type="warning">{{ t('prereleaseTag') }}</NTag></span>
                <span v-if="updateInfo?.latest?.published_at">{{ t('publishedAt') }}: {{ updateInfo.latest.published_at.slice(0, 10) }}</span>
              </div>
              <p v-if="updateInfo?.error" class="muted">{{ t('checkFailed') }}: {{ updateInfo.error }}</p>
              <p v-else-if="updateInfo?.no_release" class="muted">{{ updateInfo.message || t('repoNoReleaseMessage') }}</p>
              <p v-else-if="updateInfo?.update_available" class="muted">{{ t('updateAvailableHelp') }}</p>
              <div v-if="updateInfo?.latest?.body" class="update-notes">
                <strong>{{ t('releaseNotes') }}</strong>
                <pre>{{ updateInfo.latest.body }}</pre>
              </div>
              <div v-if="updateInfo?.update_available && updateStatus" class="update-download">
                <div v-if="!updateStatus.self_update.supported" class="muted">
                  {{ updateStatus.self_update.reason === 'docker'
                    ? t('updateDockerHint')
                    : (updateStatus.self_update.hint || t('updateSelfUpdateUnsupported')) }}
                </div>
                <template v-else>
                  <div v-if="displayedUpdateState === 'staged'" class="update-staged">
                    <NTag type="success" size="small" round>{{ t('updateStaged') }}</NTag>
                    <span class="muted">{{ t('updateStagedHint') }}</span>
                    <div class="actions-row">
                      <NButton v-if="updateStatus.kind === requiredUpdateKind" type="primary" @click="applyDownloadedUpdate">{{ t('applyUpdate') }}</NButton>
                      <NButton v-if="requiredUpdateKind" @click="redownloadUpdatePackage">{{ t('redownloadUpdate') }}</NButton>
                    </div>
                  </div>
                  <div v-else-if="displayedUpdateState === 'failed'" class="error-text">
                    {{ updateStatus.path ? t('updateApplyFailed') : t('updateDownloadFailed') }}: {{ updateStatus.error }}
                  </div>
                  <div v-else-if="displayedUpdateState === 'applying'" class="muted">
                    {{ t('updateApplying') }}
                  </div>
                  <div v-else-if="displayedUpdateState === 'restarting'" class="muted">
                    {{ t('updateRestarting') }}
                  </div>
                  <div v-else-if="displayedUpdateState === 'done'" class="update-staged">
                    <NTag type="success" size="small" round>{{ t('updateApplied') }}</NTag>
                    <span v-if="reloadCountdown !== null" class="muted">{{ t('updateReloadCountdown', { seconds: reloadCountdown }) }}</span>
                    <span v-else-if="updateStatus.restart_needed" class="muted">{{ t('updateRestartNeeded') }}</span>
                  </div>
                  <div v-else-if="displayedUpdateState === 'rolled-back'" class="error-text">
                    {{ t('updateRolledBack') }}: {{ updateStatus.error }}
                  </div>
                  <div v-else-if="isDisplayedUpdateDownloading" class="update-progress">
                    <div class="update-progress-head">
                      <span>{{ t('updateDownloading') }}</span>
                      <span class="muted">{{ updateStatus.mirror_used || '-' }}</span>
                    </div>
                    <NProgress :percentage="downloadPercent" />
                  </div>
                  <div v-if="!isDisplayedUpdateBusy && !['staged', 'done'].includes(displayedUpdateState)" class="actions-row">
                    <NButton v-if="requiredUpdateKind" @click="downloadUpdatePackage(requiredUpdateKind)">
                      {{ requiredUpdateKind === 'portable' ? t('downloadUpdatePortable') : t('downloadUpdateSource') }}
                    </NButton>
                  </div>
                </template>
              </div>
              <div class="actions-row">
                <NButton :loading="updateChecking" @click="checkUpdate">{{ t('checkUpdate') }}</NButton>
                <NButton :disabled="!updateInfo?.release_url && !updateInfo?.releases_url && !updateInfo?.source_url" @click="openUpdateUrl">{{ t('openReleasePage') }}</NButton>
                <NButton type="warning" :loading="restartBusy" @click="restartProgram">
                  <template #icon><NIcon :component="PowerOutline" /></template>
                  {{ t('restartProgram') }}
                </NButton>
              </div>
            </section>
            <section class="about-card">
              <header class="about-identity">
                <BrandLogo :size="58" :with-text="false" class="about-project-logo" />
                <div><h3>{{ t('aboutDiceFrame') }}</h3><p>{{ t('aboutIntro1') }}</p><p class="muted">{{ t('aboutIntro2') }}</p></div>
              </header>
              <div class="about-detail-grid">
                <article>
                  <h4>{{ t('whatCanDo') }}</h4>
                  <ul>
                    <li>{{ t('aboutFeature1') }}</li>
                    <li>{{ t('aboutFeature2') }}</li>
                    <li>{{ t('aboutFeature3') }}</li>
                    <li>{{ t('aboutFeature4') }}</li>
                    <li>{{ t('aboutFeature5') }}</li>
                  </ul>
                </article>
                <article>
                  <h4>{{ t('disclaimer') }}</h4>
                  <p class="muted">{{ t('disclaimerText') }}</p>
                </article>
              </div>
              <nav class="about-links" :aria-label="t('contact')">
                <a href="https://diceframe.com" target="_blank" rel="noopener"><span>{{ t('officialWebsite') }}</span><strong>diceframe.com</strong></a>
                <a href="https://diceframe.com/docs?doc=guide" target="_blank" rel="noopener"><span>{{ t('guideDocs') }}</span><strong>diceframe.com/docs</strong></a>
                <a href="https://github.com/diceframe/diceframe" target="_blank" rel="noopener"><span>{{ t('projectAddress') }}</span><strong>diceframe/diceframe</strong></a>
                <a href="https://github.com/diceframe/diceframe/issues" target="_blank" rel="noopener"><span>{{ t('issueFeedback') }}</span><strong>{{ t('submitIssue') }}</strong></a>
                <a href="/#/legal/terms" target="_blank" rel="noopener"><span>{{ t('legalDocumentLabel') }}</span><strong>{{ t('legalTermsTitle') }}</strong></a>
                <a href="/#/legal/privacy" target="_blank" rel="noopener"><span>{{ t('legalDocumentLabel') }}</span><strong>{{ t('legalPrivacyTitle') }}</strong></a>
              </nav>
              <div class="about-ctas">
                <a class="star-cta" href="https://github.com/diceframe/diceframe" target="_blank" rel="noopener">
                  <strong>⭐ {{ t('starOnGithub') }}</strong>
                  <small>{{ t('starOnGithubHint') }}</small>
                </a>
                <button class="sponsor-cta" @click="sponsorModalOpen = true">
                  <strong>{{ t('supportProject') }}</strong>
                  <small>{{ t('supportProjectText') }}</small>
                </button>
              </div>
            </section>
            <NModal v-model:show="sponsorModalOpen" preset="card" :title="t('supportProject')" style="max-width: 360px;">
              <div class="sponsor-modal">
                <img src="/sponsor-wechat-qr.png" :alt="t('wechatSponsorQr')" loading="lazy">
                <p class="muted">{{ t('supportProjectText') }}</p>
              </div>
            </NModal>
          </div>
        </NSpin>
      </div>
    </div>
  </section>
</template>
