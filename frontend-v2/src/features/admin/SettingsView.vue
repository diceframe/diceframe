<script setup lang="ts">
import { computed, h, nextTick, onMounted, ref, watch, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NInput, NInputNumber, NSelect, NSwitch, NTag, NIcon, NSpin, NProgress, NModal, NCollapse, NCollapseItem } from 'naive-ui'
import {
  ServerOutline, CubeOutline, CloudDownloadOutline,
  LockClosedOutline, OptionsOutline, InformationCircleOutline, ShareSocialOutline, PeopleOutline,
  KeyOutline, CopyOutline, EyeOutline, RefreshOutline, ColorPaletteOutline,
  ImageOutline, PowerOutline,
  TrashOutline, CheckmarkCircleOutline, AlertCircleOutline, SparklesOutline,
  VolumeHighOutline, ShieldCheckmarkOutline, ChevronDownOutline,
} from '@vicons/ionicons5'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { shouldAutoDownloadUpdate, updateStateForVersion, useUpdater } from '@/composables/useUpdater'
import { useLocale } from '@/composables/useLocale'
import { selectReleaseNotes } from '@/utils/releaseNotes'
import { renderSafeMarkdown } from '@/utils/markdown'
import { initializeTts, ttsRate, setTtsRate } from '@/utils/tts'
import { initializeAsr } from '@/utils/asr'
import { api, apiBlob, errorMessage } from '@/api/client'
import { speechApi } from '@/api/speech'
import { pluginApi } from '@/api/plugins'
import { securityApi, type SecurityTransportStatus } from '@/api/security'
import type { MessageKey } from '@/i18n'
import type { SecretKey } from '@/stores/useSettingsStore'
import type { AppConfig, HubPreferences, LoginAuditEntry, LoginAuditResponse, TestResult, TtsVoiceCatalog } from '@/api/types'
import TestResultCard from '@/components/admin/TestResultCard.vue'
import ProviderLibrary from '@/features/admin/settings/ProviderLibrary.vue'
import ProviderCatalogModal from '@/features/admin/settings/ProviderCatalogModal.vue'
import ModelRoutingPane from '@/features/admin/settings/ModelRoutingPane.vue'
import { useSpeechModelTest } from '@/features/admin/settings/useSpeechModelTest'
import ProviderModelRow from '@/features/admin/settings/ProviderModelRow.vue'
import ProviderTestSection from '@/features/admin/settings/ProviderTestSection.vue'
import HelpButton from '@/components/common/HelpButton.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import { copyToClipboard } from '@/utils/clipboard'
import { useTheme } from '@/composables/useTheme'
import { useBackgroundImages, type BackgroundSlot } from '@/composables/useBackgroundImages'
import { useProviderModelSettings } from '@/composables/useProviderModelSettings'
import { useAssistantPreferences } from '@/composables/useAssistantPreferences'
import { resetAssistantLauncherPosition } from '@/composables/useAssistantLauncherPosition'
import { loadAssistantVoiceConfig, useAssistantVoice } from '@/composables/useAssistantVoice'
import { assistantSettingsCopy } from '@/features/assistant/settingsCopy'
import { ASSISTANT_REACTION_CATEGORIES, assistantReactionCategoryCopy } from '@/features/assistant/reactions'
import { ttsRuntimeConfig } from '@/utils/tts'
import { assistantWorkspaceCopy } from '@/features/assistant/workspaceCopy'
import { currentBackendUrl, isStandaloneFrontend, normalizeBackendUrl, setBackendUrl } from '@/api/connection'
import { isSettingsSectionAvailable, legacySettingsFocus, normalizeSettingsSection, type SettingsSectionId } from '@/utils/settingsSections'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import LanguagePreferences from '@/features/admin/settings/LanguagePreferences.vue'
import SettingsSearch from '@/features/admin/settings/SettingsSearch.vue'
import GameDefaultsSettings from '@/features/admin/settings/GameDefaultsSettings.vue'
import { revealSettingTarget, settingsSearchTarget, settingsGroupIds, settingsGroupName, settingsGroupFor, type SettingsSearchResult } from '@/features/admin/settings/settingsSearch'

type StatusTone = 'default' | 'success' | 'warning' | 'error' | 'info'
type UpdatePackageKind = 'source' | 'portable' | 'docker'
type SystemStatusItem = { label: string; value: string; detail: string; tone: StatusTone; icon: Component }
type RoutingStatusItem = SystemStatusItem & { enabled: boolean; order: number }
type SettingsSection = { id: SettingsSectionId; labelKey: MessageKey; icon: Component }
type RuntimeLogStatus = {
  ok: boolean
  retention_days: number
  file_count: number
  total_bytes: number
  cleared_files?: number
  cleared_bytes?: number
}

const store = useSettingsStore()
const route = useRoute()
const router = useRouter()
const sponsorModalOpen = ref(false)
const sponsorQrUrl = `${import.meta.env.BASE_URL}sponsor-wechat-qr.png`
const toast = useToast()
const { confirm } = useConfirm()
const { updateInfo, updateChecking, checkForUpdates } = useUpdateCheck()
const {
  updateStatus,
  reloadCountdown,
  downloadPercent,
  startDownload,
  applyUpdate,
  rollbackUpdate,
  refreshStatus,
  isUpdateBusy,
  restartApplication,
  waitForApplicationRestart,
} = useUpdater()
const { t, locale } = useLocale()
const {
  enabled: assistantEnabled, setEnabled: setAssistantEnabled,
  reactions: assistantReactions, setReactions: setAssistantReactions,
  voiceInput: assistantVoiceInput, setVoiceInput: setAssistantVoiceInput,
  reactionSpeech: assistantReactionSpeech, setReactionSpeech: setAssistantReactionSpeech,
  autoNavigate: assistantAutoNavigate, setAutoNavigate: setAssistantAutoNavigate,
  disabledReactionCategories: assistantDisabledReactions, setReactionCategory: setAssistantReactionCategory,
} = useAssistantPreferences()
const assistantReactionCategories = computed(() => {
  const copy = assistantReactionCategoryCopy(locale.value)
  return ASSISTANT_REACTION_CATEGORIES.map(id => ({ id, ...copy[id], on: !assistantDisabledReactions.value.includes(id) }))
})
const { voiceReply: assistantVoiceReply, setVoiceReply: setAssistantVoiceReply, inputEngine: assistantInputEngine, outputEngine: assistantOutputEngine } = useAssistantVoice()
const agentSettingsCopy = computed(() => assistantSettingsCopy(locale.value))
const assistantInputHint = computed(() => ({
  server: agentSettingsCopy.value.inputServer,
  browser: agentSettingsCopy.value.inputBrowser,
  none: agentSettingsCopy.value.inputNone,
})[assistantInputEngine.value])
const assistantOutputHint = computed(() => assistantOutputEngine.value === 'server'
  ? agentSettingsCopy.value.outputServer(ttsRuntimeConfig.value.provider)
  : assistantOutputEngine.value === 'browser' ? agentSettingsCopy.value.outputBrowser : agentSettingsCopy.value.outputNone)
// Release 保持中英双语单文；展示层按界面语言选段，识别不到分段时整段回退。
const releaseNotesText = computed(() =>
  selectReleaseNotes(updateInfo.value?.latest?.body, locale.value).text,
)
const releaseNotesHtml = computed(() => renderSafeMarkdown(releaseNotesText.value))
const { current: themeMode, skin: activeSkin, builtinSkins, apply: applyThemeMode, applySkin } = useTheme()
const standaloneFrontend = isStandaloneFrontend()
const backendUrl = ref(standaloneFrontend ? currentBackendUrl() : '')
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
const runtimeLogStatus = ref<RuntimeLogStatus | null>(null)
const runtimeLogBusy = ref(false)
const runtimeLogExportBusy = ref(false)

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const amount = value / (1024 ** index)
  return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`
}

async function loadRuntimeLogStatus() {
  try {
    runtimeLogStatus.value = await api<RuntimeLogStatus>('/system/runtime-logs')
  } catch {
    runtimeLogStatus.value = null
  }
}

async function clearRuntimeLogs() {
  const ok = await confirm({
    title: t('runtimeLogsClearTitle'),
    content: t('runtimeLogsClearConfirm'),
    positiveText: t('runtimeLogsClearAction'),
    type: 'warning',
  })
  if (!ok) return
  runtimeLogBusy.value = true
  try {
    const result = await api<RuntimeLogStatus>('/system/runtime-logs/clear', { method: 'POST' })
    runtimeLogStatus.value = result
    toast.success(t('runtimeLogsCleared', { size: formatBytes(result.cleared_bytes || 0) }))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    runtimeLogBusy.value = false
  }
}

function switchBackend() {
  const nextBackendUrl = normalizeBackendUrl(backendUrl.value)
  if (!nextBackendUrl) {
    toast.error(t('invalidServerAddress'))
    return
  }
  if (nextBackendUrl === currentBackendUrl()) {
    toast.info(t('backendAlreadyConnected'))
    return
  }
  setBackendUrl(nextBackendUrl)
  window.location.assign(`${window.location.pathname}${window.location.search}#/login`)
}

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

const section = ref<SettingsSectionId>('general')
const connectionSection: SettingsSection = { id: 'connection', labelKey: 'settingsSectionConnection', icon: ServerOutline }
const sections: SettingsSection[] = [
  { id: 'general', labelKey: 'settingsSectionGeneral', icon: OptionsOutline },
  { id: 'game', labelKey: 'settingsSectionGame', icon: PeopleOutline },
  { id: 'appearance', labelKey: 'settingsSectionAppearance', icon: ColorPaletteOutline },
  { id: 'api', labelKey: 'settingsSectionApi', icon: ServerOutline },
  { id: 'models', labelKey: 'settingsSectionModels', icon: SparklesOutline },
  ...(standaloneFrontend ? [connectionSection] : []),
  { id: 'network', labelKey: 'settingsSectionNetwork', icon: CloudDownloadOutline },
  { id: 'sharing', labelKey: 'settingsSectionSharing', icon: ShareSocialOutline },
  { id: 'botapi', labelKey: 'settingsSectionBotApi', icon: KeyOutline },
  { id: 'access', labelKey: 'settingsSectionAccess', icon: LockClosedOutline },
  { id: 'security', labelKey: 'settingsSectionSecurity', icon: ShieldCheckmarkOutline },
  { id: 'about', labelKey: 'settingsSectionAbout', icon: InformationCircleOutline },
]

const settingsGroups = computed(() => {
  return settingsGroupIds.map(id => ({
    id,
    label: settingsGroupName(id, locale.value),
    sections: sections.filter(sectionItem => settingsGroupFor(sectionItem.id) === id),
  }))
})

// 窄屏（平板/手机）下节列表收成一行「当前节」，点开才展开完整目录；
// 宽屏保持常驻侧栏。用户的选择记到 localStorage。
const SETTINGS_NAV_OPEN_KEY = 'settings_nav_open'
const settingsNavNarrow = ref(false)
const settingsNavOpen = ref(false)
const activeSection = computed(() => sections.find(s => s.id === section.value) || sections[0])

function applySettingsNavWidth(matches: boolean) {
  settingsNavNarrow.value = matches
  // 拖回宽屏时收起展开状态，再次进入窄屏从折叠默认开始
  if (!matches) settingsNavOpen.value = false
}

function watchSettingsNavWidth() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  const media = window.matchMedia('(max-width: 980px)')
  applySettingsNavWidth(media.matches)
  // 注意：窄→宽的 change 事件 matches=false，必须把 narrow 置回 false，
  // 否则拖大窗口后节列表永远隐藏（resize 回宽屏侧栏消失的来源）。
  const listener = (event: MediaQueryListEvent) => applySettingsNavWidth(event.matches)
  if (typeof media.addEventListener === 'function') media.addEventListener('change', listener)
  else if (typeof media.addListener === 'function') media.addListener(listener)
}

function toggleSettingsNav() {
  settingsNavOpen.value = !settingsNavOpen.value
  try {
    localStorage.setItem(SETTINGS_NAV_OPEN_KEY, settingsNavOpen.value ? '1' : '0')
  } catch {
    // storage 不可用时仅当前 session 生效
  }
}

function resolveSettingsNavOpen() {
  try {
    return localStorage.getItem(SETTINGS_NAV_OPEN_KEY) === '1'
  } catch {
    return false
  }
}

settingsNavOpen.value = resolveSettingsNavOpen()
watchSettingsNavWidth()

function selectSettingsSection(id: SettingsSectionId) {
  section.value = id
  if (settingsNavNarrow.value) settingsNavOpen.value = false
  // A copied URL or refresh should reopen the section the user is viewing.
  void router.replace({ query: { ...route.query, section: id, focus: undefined } })
}

async function selectSearchResult(result: SettingsSearchResult) {
  if (!isSettingsSectionAvailable(result.section, standaloneFrontend)) return
  section.value = result.section
  if (settingsNavNarrow.value) settingsNavOpen.value = false
  await router.replace({ query: { ...route.query, section: result.section, focus: result.id } })
  await nextTick()
  revealSettingTarget(document.getElementById(result.target))
}

function queryValue(value: unknown): string {
  return String(Array.isArray(value) ? (value[0] || '') : (value || ''))
}

function syncRouteTarget() {
  const routeSection = queryValue(route.query.section)
  const requestedSection = normalizeSettingsSection(routeSection)
  if (requestedSection && isSettingsSectionAvailable(requestedSection, standaloneFrontend)) {
    section.value = requestedSection
  }
  if (queryValue(route.query.focus) === 'update') {
    section.value = 'about'
    void nextTick(() => {
      document.getElementById('settings-update')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  } else {
    const focus = queryValue(route.query.focus) || legacySettingsFocus(routeSection) || ''
    // Focus IDs are metadata IDs, never arbitrary DOM selectors from the URL.
    const focused = settingsSearchTarget(focus, locale.value, standaloneFrontend)
    if (focused) {
      section.value = focused.section
      void nextTick(() => revealSettingTarget(document.getElementById(focused.target)))
    }
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
const ttsProvider = computed(() => String(store.config.tts_provider || 'browser'))
const ttsVoiceOptions = computed(() => (
  ttsVoices.value?.voices.filter(voice => voice.engine === ttsProvider.value) || []
))

async function loadTtsVoices() {
  try { ttsVoices.value = await speechApi.voices() }
  catch { ttsVoices.value = null }
}

// 引擎级参数（音频格式、缓存、超时）随模型配置保存，这里只存角色音色映射。
const TTS_CONFIG_KEYS = [
  'tts_default_voice', 'tts_gm_voice', 'tts_player_voice', 'tts_timeout_seconds',
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

const { ttsTesting, asrTesting, asrTestRecording, asrTestText, testTts, testAsr } = useSpeechModelTest()

const {
  providerDrafts,
  providerTestModels,
  providerTestModes,
  providerSearch,
  activeProviderId,
  providerSaving,
  providerTestingId,
  providerFetchingModelsId,
  providerTestedId,
  providerTestedKind,
  providerTestResult,
  providerCatalogOpen,
  modelRoutingSaving,
  catalogAssignmentBusy,
  providerLibrarySupported,
  readyProviderIds,
  activeProvider,
  activeCatalogProvider,
  activeProviderModelGroups,
  providerCatalogSourceModels,
  addProviderDraft,
  removeProviderDraft,
  setProviderSecret,
  providerSecretValue,
  saveProvidersList,
  testProviderDraft,
  fetchProviderModels,
  openProviderCatalog,
  addCustomProviderModel,
  toggleCatalogModel,
  addAllCatalogModels,
  removeProviderModel,
  draftModelCapabilitySelection,
  setDraftModelCapability,
  providerModelCapabilityOptions,
  providerTestModeOptions,
  providerDraftReady,
  setActiveProviderTestModel,
  setActiveProviderTestMode,
  providerTestActionLabel,
  providerMark,
  providerStyle,
  modelCapabilityLabels,
  providerById,
  modelBindingSummary,
  catalogModelAssignmentOptions,
  catalogModelAssignmentValue,
  catalogModelCanAssign,
  assignCatalogModelRole,
  saveModelRouting,
  setModelRoutingBool,
} = useProviderModelSettings({
  refreshModelRuntimes: async () => {
    await Promise.all([initializeTts(true), initializeAsr(true), loadTtsVoices()])
  },
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
  return mode === 'source' || mode === 'portable' || mode === 'docker' ? mode : null
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
const dockerRuntimeUpgradeRequired = computed(() => (
  updateStatus.value?.kind === 'docker'
  && /newer base image|base runtime|requires cp\d+|different Docker/i.test(updateStatus.value?.error || '')
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
  return Boolean(p && p.base_url)
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
  const speechReady = ttsReady && asrReady
  const speechPartial = ttsReady !== asrReady
  const proxyEnabled = !!c.proxy_enabled
  const mainDetail = mainProvider ? `${mainProvider.name || mainProvider.id} · ${c.model || t('modelUnset')}` : t('modelRoutingUnassigned')
  const ttsDetail = ttsBuiltIn
    ? (ttsMode === 'edge-tts' ? t('ttsProviderEdge') : t('ttsProviderBrowser'))
    : (ttsProviderConfig ? `${ttsProviderConfig.name || ttsProviderConfig.id} · ${c.tts_model || t('modelUnset')}` : t('modelRoutingUnassigned'))
  const asrDetail = asrMode === 'disabled'
    ? t('asrProviderDisabled')
    : (asrProviderConfig ? `${asrProviderConfig.name || asrProviderConfig.id} · ${c.asr_model || t('modelUnset')}` : t('modelRoutingUnassigned'))
  const routingItems: RoutingStatusItem[] = [
    {
      label: t('statusMainModel'),
      value: mainReady ? t('statusComplete') : t('statusNeedsSetup'),
      detail: mainDetail,
      tone: mainReady ? 'success' : 'warning',
      icon: ServerOutline,
      enabled: mainReady,
      order: 0,
    },
    {
      label: t('statusFallback'),
      value: enabledFallbacks.length ? t('routesAvailable', { ready: readyFallbacks.length, total: enabledFallbacks.length }) : t('disabled'),
      detail: enabledFallbacks.length
        ? enabledFallbacks.map(item => `${item.name}: ${item.provider?.name || t('modelRoutingUnassigned')} · ${item.model || t('modelUnset')}`).join(' · ')
        : t('fallbackDetailHint'),
      tone: !enabledFallbacks.length ? 'default' : readyFallbacks.length === enabledFallbacks.length ? 'success' : 'warning',
      icon: CubeOutline,
      enabled: enabledFallbacks.length > 0,
      order: 1,
    },
    {
      label: t('statusSpeechModels'),
      value: speechReady ? t('statusComplete') : speechPartial ? t('statusPartial') : t('statusNeedsSetup'),
      detail: `TTS: ${ttsDetail} · ASR: ${asrDetail}`,
      tone: speechReady ? 'success' : 'warning',
      icon: VolumeHighOutline,
      enabled: speechReady,
      order: 2,
    },
  ]
  const [mainRoutingItem, ...optionalRoutingItems] = routingItems
  const statusItems: SystemStatusItem[] = [
    mainRoutingItem,
    ...optionalRoutingItems.sort((left, right) => Number(right.enabled) - Number(left.enabled) || left.order - right.order),
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
  return statusItems
})

onMounted(() => {
  void loadAssistantVoiceConfig(true)
  void initializeBackgrounds()
  void (async () => {
    await store.load()
    syncRouteTarget()
    await loadTtsVoices()
  })()
  void refreshStatus()
  void loadHubPreferences()
  void loadRuntimeLogStatus()
  void loadSecurityStatus()
  syncRouteTarget()
})
watch(() => [route.query.section, route.query.focus], syncRouteTarget)

// 从更新弹窗的“去设置”进入：跳转后自动开始下载，用户无需再点一次下载按钮。
// 仅在 source/portable/docker-managed、确有新版且无进行中任务时触发一次；
// 旧 Docker、development 和只读模式下 requiredUpdateKind 为 null，不触发。
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
  if (value === 'access') {
    void loadLoginHistory()
  }
  if (value === 'security') void loadSecurityStatus()
})

// ---- 连接安全（HTTP / HTTPS） ----
const securityStatus = ref<SecurityTransportStatus | null>(null)
const securityBusy = ref<'enable' | 'disable' | 'regenerate' | 'acme' | ''>('')
const acmeIdentifierType = ref<'dns' | 'ip'>('dns')
const acmeIdentifier = ref('')
const acmeContactEmail = ref('')
const acmeChallengePort = ref(80)

async function loadSecurityStatus() {
  try {
    const status = await securityApi.status()
    securityStatus.value = status
    if (status.tls_mode === 'lets_encrypt' && status.acme) {
      acmeIdentifierType.value = status.acme.identifier_type
      acmeIdentifier.value = status.acme.identifier
      acmeContactEmail.value = status.acme.contact_email
      acmeChallengePort.value = status.acme.http_challenge_port
    }
  } catch {
    securityStatus.value = null
  }
}

async function copySecurityFingerprint() {
  const fingerprint = securityStatus.value?.certificate?.fingerprint_sha256 || ''
  if (!fingerprint) return
  await copyToClipboard(fingerprint)
  toast.success(t('securityFingerprintCopied'))
}

// HTTPS 启用后同端口换协议，旧 http:// 链接会失效；把当前地址一键复制给玩家。
const currentOrigin = window.location.origin

async function copyCurrentAddress() {
  if (!currentOrigin) return
  await copyToClipboard(currentOrigin)
  toast.success(t('securityAddressCopied'))
}

// 服务以新 scheme 重启后当前 origin 会失效，轮询目标 origin 就绪再跳转。
async function waitAndNavigateToOrigin(targetOrigin: string): Promise<void> {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 1000))
    try {
      await fetch(`${targetOrigin}/api/system/update/health`, { mode: 'no-cors' })
      break
    } catch {
      // 重启期间连接失败是预期行为，继续等待。
    }
  }
  window.location.assign(targetOrigin)
}

async function enableLocalHttps() {
  if (securityBusy.value) return
  securityBusy.value = 'enable'
  try {
    const prepared = await securityApi.prepare('self_signed')
    if (!prepared.ok || !prepared.token || !prepared.certificate) {
      toast.error(prepared.error || t('securityPrepareFailed'))
      return
    }
    const fingerprint = prepared.certificate.fingerprint_sha256
    const confirmed = await confirm({
      title: t('securityEnableLocalHttps'),
      dialogClass: 'security-confirm-dialog',
      contentClass: 'security-confirm-content',
      content: () => h('div', { class: 'security-confirm-body' }, [
        h('p', { class: 'security-confirm-lead' }, t('securityEnableConfirmIntro')),
        h('ol', { class: 'security-confirm-steps' }, [
          h('li', t('securityEnableConfirmAddress')),
          h('li', t('securityEnableConfirmTrust')),
          h('li', t('securityEnableConfirmPlugin')),
          h('li', t('securityEnableConfirmMobileApp')),
        ]),
        h('div', { class: 'security-confirm-fingerprint' }, [
          h('span', t('securityFingerprintLabel')),
          h('code', fingerprint),
        ]),
      ]),
      type: 'warning',
      positiveText: t('securityEnableConfirmAction'),
      negativeText: t('cancel'),
    })
    if (!confirmed) return
    const activated = await securityApi.activate(prepared.token)
    if (!activated.ok || !activated.target_origin) {
      toast.error(activated.error || t('securityActivateFailed'))
      return
    }
    toast.info(t('securitySwitchingOrigin', { origin: activated.target_origin }))
    await waitAndNavigateToOrigin(activated.target_origin)
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    securityBusy.value = ''
  }
}

async function exportRuntimeLogs() {
  runtimeLogExportBusy.value = true
  try {
    const response = await apiBlob('/system/runtime-logs/export')
    const disposition = response.headers.get('Content-Disposition') || ''
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || 'DiceFrame-runtime-logs.zip'
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    toast.success(t('runtimeLogsExported'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    runtimeLogExportBusy.value = false
  }
}

async function enableLetsEncrypt() {
  if (securityBusy.value) return
  const identifier = acmeIdentifier.value.trim()
  if (!identifier) {
    toast.error(t('securityAcmeIdentifierRequired'))
    return
  }
  securityBusy.value = 'acme'
  try {
    const prepared = await securityApi.prepare('lets_encrypt', {
      identifier_type: acmeIdentifierType.value,
      identifier,
      contact_email: acmeContactEmail.value.trim(),
      challenge_type: 'http-01',
      http_challenge_port: Number(acmeChallengePort.value) || 80,
    })
    if (!prepared.ok || !prepared.token || !prepared.certificate) {
      toast.error(prepared.error || t('securityAcmePrepareFailed'))
      return
    }
    const warningText = prepared.warnings?.length ? `\n\n${prepared.warnings.join('\n')}` : ''
    const confirmed = await confirm({
      title: t('securityEnableLetsEncrypt'),
      content: `${t('securityAcmeConfirm')}\n${t('securityCertValidity')}: ${prepared.certificate.not_after}${warningText}`,
      type: 'warning',
      positiveText: t('securityEnableConfirmAction'),
      negativeText: t('cancel'),
    })
    if (!confirmed) return
    const activated = await securityApi.activate(prepared.token)
    if (!activated.ok || !activated.target_origin) {
      toast.error(activated.error || t('securityActivateFailed'))
      return
    }
    toast.info(t('securitySwitchingOrigin', { origin: activated.target_origin }))
    await waitAndNavigateToOrigin(activated.target_origin)
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    securityBusy.value = ''
  }
}

async function disableLocalHttps() {
  if (securityBusy.value) return
  const ok = await confirm({
    title: t('securityDisableHttps'),
    content: t('securityDisableConfirm'),
    type: 'warning',
    positiveText: t('securityDisableConfirmAction'),
    negativeText: t('cancel'),
  })
  if (!ok) return
  securityBusy.value = 'disable'
  try {
    const result = await securityApi.disable()
    if (!result.ok || !result.target_origin) {
      toast.error(result.error || t('securityDisableFailed'))
      return
    }
    toast.info(t('securitySwitchingOrigin', { origin: result.target_origin }))
    await waitAndNavigateToOrigin(result.target_origin)
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    securityBusy.value = ''
  }
}

async function regenerateLocalCertificate() {
  if (securityBusy.value) return
  const ok = await confirm({
    title: t('securityRegenerateCertificate'),
    content: t('securityRegenerateConfirm'),
    type: 'warning',
    positiveText: t('securityRegenerateConfirmAction'),
    negativeText: t('cancel'),
  })
  if (!ok) return
  securityBusy.value = 'regenerate'
  try {
    const result = await securityApi.regenerate()
    if (!result.ok) {
      toast.error(result.error || t('securityRegenerateFailed'))
      return
    }
    toast.success(t('securityRegenerateDone'))
    await loadSecurityStatus()
    if (result.restart_required) {
      toast.info(t('securityRegenerateRestartHint'))
    }
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    securityBusy.value = ''
  }
}

function setStr(key: keyof AppConfig, v: string | number) { store.setConfigField(key, String(v).trim()) }
function setSecret(key: SecretKey, v: string | number) { store.secrets[key] = String(v).trim() }
function eventValue(event: Event) { return (event.target as HTMLSelectElement | null)?.value || '' }
function setNum(key: keyof AppConfig, v: string | number | null) {
  if (v === null || v === '') { store.setConfigField(key, 0); return }
  store.setConfigField(key, Number(v) || 0)
}
function setBool(key: keyof AppConfig, v: string | number | boolean) { store.setConfigField(key, Boolean(v)) }

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

async function rollbackProgram() {
  const targetVersion = updateStatus.value?.rollback?.target_version || ''
  const ok = await confirm({
    title: t('rollbackUpdateTitle'),
    content: t(updateStatus.value?.rollback?.restores_data ? 'rollbackDataConfirm' : 'rollbackUpdateConfirm', { version: targetVersion }),
    positiveText: t('rollbackUpdateAction'),
    negativeText: t('cancel'),
    type: 'warning',
  })
  if (!ok) return
  try {
    const result = await rollbackUpdate()
    if (!result.ok) {
      toast.error(result.error || t('rollbackUpdateFailed'))
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
      <ContentPageHeader class="settings-page-heading" kicker="CONTROL ROOM" :title="t('settingsTitle')" :description="t('settingsSubtitle')">
        <template #actions>
          <NButton :loading="store.loading" @click="store.load()">{{ t('refresh') }}</NButton>
        </template>
      </ContentPageHeader>
      <details class="settings-status-disclosure">
        <summary>{{ t('settingsSystemStatusAria') }} <span class="settings-status-count">{{ systemStatusItems.length }}</span></summary>
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
      </details>
    </div>
    <div class="settings-layout">
      <aside
        class="settings-nav"
        :class="{ collapsed: settingsNavNarrow && !settingsNavOpen, expanded: settingsNavNarrow && settingsNavOpen }"
      >
        <SettingsSearch :standalone-frontend="standaloneFrontend" @select="selectSearchResult" />
        <button
          v-if="settingsNavNarrow"
          type="button"
          class="nav-item settings-nav-toggle"
          :aria-expanded="settingsNavOpen"
          @click="toggleSettingsNav"
        >
          <NIcon :component="activeSection.icon" />
          <span>{{ t(activeSection.labelKey) }}</span>
          <NIcon :component="ChevronDownOutline" class="settings-nav-chevron" :class="{ open: settingsNavOpen }" />
        </button>
        <div v-show="!settingsNavNarrow || settingsNavOpen" class="settings-nav-groups">
        <template v-for="group in settingsGroups" :key="group.id">
          <div v-show="!settingsNavNarrow || settingsNavOpen" class="settings-nav-group-label">{{ group.label }}</div>
          <button
            v-for="s in group.sections"
            v-show="!settingsNavNarrow || settingsNavOpen"
            :key="s.id"
            :class="['nav-item', { active: section === s.id }]"
            :data-section="s.id"
            :data-group="group.id"
            @click="selectSettingsSection(s.id)"
          >
            <NIcon :component="s.icon" />
            <span>{{ t(s.labelKey) }}</span>
          </button>
        </template>
        </div>
      </aside>

      <div class="settings-content">
        <NSpin :show="store.loading">
          <div v-show="section === 'game'" class="settings-pane settings-game-pane">
            <GameDefaultsSettings />
          </div>
          <div v-show="section === 'general'" class="settings-pane settings-general-pane">
            <section id="settings-language" class="settings-general-section">
              <LanguagePreferences />
            </section>
            <section id="settings-voice" class="settings-general-group" :aria-label="t('settingsSectionVoice')">
              <h3 class="settings-general-group-title">{{ t('settingsSectionVoice') }}</h3>
              <section id="settings-tts" class="advanced-section advanced-section-wide tts-section">
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
                  <NButton v-if="ttsProvider !== 'browser'" :loading="ttsTesting" @click="testTts(() => saveTts(false))">{{ t('ttsSaveAndTest') }}</NButton>
                </footer>
              </section>
            </section>
            <section id="settings-general-agent" class="settings-general-group" :aria-label="t('settingsSectionAgent')">
              <h3 class="settings-general-group-title">{{ t('settingsSectionAgent') }}</h3>
              <section id="settings-agent" class="advanced-section advanced-section-wide">
                <header class="advanced-section-head"><NIcon :component="SparklesOutline" /><div><h3>{{ t('assistantTitle') }}</h3><p>{{ t('assistantAppearanceHint') }}</p></div></header>
                <div class="advanced-row"><div><strong>{{ t('assistantTitle') }}</strong><small>{{ agentSettingsCopy.enabledHint }}</small></div><div class="switch-inline"><NSwitch :value="assistantEnabled" @update:value="setAssistantEnabled" /><span>{{ assistantEnabled ? t('enabled') : t('disabled') }}</span></div></div>
                <div class="advanced-row"><div><strong>{{ agentSettingsCopy.autoNavigate }}</strong><small>{{ agentSettingsCopy.autoNavigateHint }}</small></div><div class="switch-inline"><NSwitch data-testid="assistant-auto-navigate" :value="assistantAutoNavigate" @update:value="setAssistantAutoNavigate" /><span>{{ assistantAutoNavigate ? t('enabled') : t('disabled') }}</span></div></div>
                <div class="advanced-row"><div><strong>{{ agentSettingsCopy.reactions }}</strong><small>{{ agentSettingsCopy.reactionsHint }}</small></div><div class="switch-inline"><NSwitch data-testid="assistant-reactions" :value="assistantReactions" @update:value="setAssistantReactions" /><span>{{ assistantReactions ? t('enabled') : t('disabled') }}</span></div></div>
                <div v-if="assistantReactions" class="advanced-row assistant-reaction-types-row"><div><strong>{{ agentSettingsCopy.reactionTypes }}</strong><small>{{ agentSettingsCopy.reactionTypesHint }}</small></div><div class="assistant-reaction-types" role="group" :aria-label="agentSettingsCopy.reactionTypes"><button v-for="category in assistantReactionCategories" :key="category.id" type="button" class="assistant-reaction-chip" :class="{ on: category.on }" :aria-pressed="category.on" :title="category.hint" :data-testid="`assistant-reaction-${category.id}`" @click="setAssistantReactionCategory(category.id, !category.on)">{{ category.label }}</button></div></div>
                <div v-if="assistantReactions" class="advanced-row"><div><strong>{{ agentSettingsCopy.reactionSpeech }}</strong><small>{{ agentSettingsCopy.reactionSpeechHint }}</small></div><div class="switch-inline"><NSwitch data-testid="assistant-reaction-speech" :value="assistantReactionSpeech" :disabled="assistantOutputEngine === 'none'" @update:value="setAssistantReactionSpeech" /><span>{{ assistantReactionSpeech ? t('enabled') : t('disabled') }}</span></div></div>
                <div class="advanced-row"><div><strong>{{ agentSettingsCopy.voiceInput }}</strong><small>{{ assistantInputHint }}</small></div><div class="switch-inline"><NSwitch data-testid="assistant-voice-input" :value="assistantVoiceInput" :disabled="assistantInputEngine === 'none'" @update:value="setAssistantVoiceInput" /><span>{{ assistantVoiceInput ? t('enabled') : t('disabled') }}</span></div></div>
                <div class="advanced-row"><div><strong>{{ agentSettingsCopy.voiceReply }}</strong><small>{{ agentSettingsCopy.voiceReplyHint }} {{ assistantOutputHint }}</small></div><div class="switch-inline"><NSwitch data-testid="assistant-voice-reply-setting" :value="assistantVoiceReply" :disabled="assistantOutputEngine === 'none'" @update:value="setAssistantVoiceReply" /><span>{{ assistantVoiceReply ? t('enabled') : t('disabled') }}</span></div></div>
                <div class="advanced-row"><div><strong>{{ t('assistantResetPosition') }}</strong><small>{{ t('assistantResetPositionHint') }}</small></div><button type="button" data-testid="assistant-reset-position" @click="resetAssistantLauncherPosition">{{ assistantWorkspaceCopy(locale).resetPosition }}</button></div>
              </section>
              <section id="settings-agent-budget" class="advanced-section">
                <header class="advanced-section-head"><NIcon :component="OptionsOutline" /><div><h3>{{ t('assistantAgentTokens') }}</h3><p>{{ t('assistantAgentTokensHint') }}</p></div></header>
                <div class="advanced-row token-row"><div><strong>{{ t('assistantAgentReplyBudget') }}</strong><small>{{ t('assistantAgentReplyBudgetHint') }}</small></div><div class="token-input-wrap"><NInputNumber class="advanced-number" :value="Number(store.config.assistant_agent_max_tokens ?? 0)" :step="256" @update:value="setNum('assistant_agent_max_tokens', $event)" /><span>Token</span></div></div>
                <div class="advanced-row token-row"><div><strong>{{ t('assistantAgentTaskBudget') }}</strong><small>{{ t('assistantAgentTaskBudgetHint') }}</small></div><div class="token-input-wrap"><NInputNumber class="advanced-number" :value="Number(store.config.assistant_agent_task_max_tokens ?? 256000)" :min="16000" :max="2000000" :step="4096" @update:value="setNum('assistant_agent_task_max_tokens', $event)" /><span>Token</span></div></div>
                <footer class="advanced-save-row"><NButton type="primary" @click="save(['assistant_agent_max_tokens', 'assistant_agent_task_max_tokens'])">{{ t('saveAction') }}</NButton></footer>
              </section>
            </section>
            <section id="settings-maintenance" class="settings-general-group" :aria-label="t('settingsSectionSystem')">
              <h3 class="settings-general-group-title">{{ t('settingsSectionSystem') }}</h3>
              <section v-if="updateStatus?.self_update.supported && updateStatus.rollback?.available" id="settings-rollback" class="advanced-section">
                <div class="advanced-row">
                  <div>
                    <strong>{{ t('rollbackUpdate') }}</strong>
                    <small>{{ t('rollbackAvailableHint', { version: updateStatus.rollback.target_version || '' }) }}</small>
                  </div>
                  <NButton secondary :disabled="isUpdateBusy" @click="rollbackProgram">{{ t('rollbackUpdate') }}</NButton>
                </div>
              </section>
              <section id="settings-runtime-logs" class="advanced-section runtime-logs-section">
                <header class="advanced-section-head">
                  <NIcon :component="TrashOutline" />
                  <div><h3>{{ t('runtimeLogsTitle') }}</h3><p>{{ t('runtimeLogsHint') }}</p></div>
                </header>
                <div class="advanced-row">
                  <div>
                    <strong>{{ t('runtimeLogsRetention', { days: runtimeLogStatus?.retention_days || 30 }) }}</strong>
                    <small>{{ runtimeLogStatus ? t('runtimeLogsUsage', { count: runtimeLogStatus.file_count, size: formatBytes(runtimeLogStatus.total_bytes) }) : t('runtimeLogsUnavailable') }}</small>
                  </div>
                  <NButton type="error" secondary :disabled="runtimeLogExportBusy" :loading="runtimeLogBusy" @click="clearRuntimeLogs">
                    <template #icon><NIcon :component="TrashOutline" /></template>
                    {{ t('runtimeLogsClearAction') }}
                  </NButton>
                </div>
                <div class="advanced-row">
                  <div>
                    <strong>{{ t('runtimeLogsExportAction') }}</strong>
                    <small>{{ t('runtimeLogsExportHint') }}</small>
                  </div>
                  <NButton secondary :disabled="runtimeLogBusy || !runtimeLogStatus?.file_count" :loading="runtimeLogExportBusy" @click="exportRuntimeLogs">
                    <template #icon><NIcon :component="CloudDownloadOutline" /></template>
                    {{ t('runtimeLogsExportAction') }}
                  </NButton>
                </div>
              </section>
              <section id="settings-test-timeout" class="advanced-section test-timeout-section">
                <header class="advanced-section-head">
                  <NIcon :component="ServerOutline" />
                  <div><h3>{{ t('testTimeoutTitle') }}</h3><p>{{ t('testTimeoutHint') }}</p></div>
                </header>
                <div class="advanced-row">
                  <div><strong>{{ t('testTimeoutSeconds') }}</strong><small>{{ t('testTimeoutScope') }}</small></div>
                  <NInputNumber class="advanced-number" :value="Number(store.config.test_timeout_seconds ?? 30)" :min="5" :max="300" :step="5" @update:value="setNum('test_timeout_seconds', $event)" />
                </div>
                <footer class="advanced-save-row">
                  <NButton type="primary" @click="save(['test_timeout_seconds'])">{{ t('saveAction') }}</NButton>
                </footer>
              </section>
            </section>
          </div>
          <div v-show="section === 'api'" class="settings-pane ai-management-pane">
            <header class="ai-manager-header">
              <div>
                <h3>{{ t('aiProviders') }}</h3>
                <p>{{ t('aiProvidersHint') }}</p>
              </div>
              <HelpButton :title="t('deepseekHelpTitle')">
                <h4>{{ t('deepseekHelpStep1Title') }}</h4>
                <p>{{ t('deepseekHelpStep1TextBefore') }} <a href="https://platform.deepseek.com/" target="_blank" rel="noopener">{{ t('deepseekPlatform') }}</a>{{ t('deepseekHelpStep1TextAfter') }}</p>
                <h4>{{ t('deepseekHelpStep2Title') }}</h4>
                <p>{{ t('deepseekHelpStep2Text') }} <code>sk-xxxxxxxx</code>{{ t('deepseekHelpStep2Suffix') }}</p>
                <h4>{{ t('deepseekHelpStep3Title') }}</h4>
                <p>{{ t('deepseekHelpStep3Text') }}</p>
                <ul>
                  <li><strong>{{ t('providerName') }}</strong>: DeepSeek</li>
                  <li><strong>{{ t('apiFormat') }}</strong>: {{ t('apiFormatOpenAI') }}</li>
                  <li><strong>Base URL</strong>: <code>https://api.deepseek.com</code></li>
                  <li><strong>API Key</strong>: {{ t('deepseekHelpApiKey') }}</li>
                  <li><strong>{{ t('providerModels') }}</strong>: <code>deepseek-v4-pro</code>, <code>deepseek-v4-flash</code></li>
                </ul>
                <p>{{ t('deepseekHelpFinish') }}</p>
              </HelpButton>
            </header>

            <div v-if="!providerLibrarySupported" class="provider-backend-warning">
              <NIcon :component="AlertCircleOutline" />
              <div>
                <strong>{{ t('providerBackendOutdatedTitle') }}</strong>
                <p>{{ t('providerBackendOutdated') }}</p>
              </div>
            </div>

            <div id="settings-providers" class="ai-provider-workspace">
              <ProviderLibrary
                v-model:active-provider-id="activeProviderId"
                v-model:search="providerSearch"
                :providers="providerDrafts"
                :ready-provider-ids="readyProviderIds"
                :can-add="providerLibrarySupported"
                @add="addProviderDraft"
              />

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
                        :value="providerSecretValue(activeProvider.id)"
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

                  <p class="provider-models-main-hint">
                    {{ t('providerModelsCurrentMain', { binding: modelBindingSummary(store.config.llm_provider_ref, store.config.model) }) }}
                  </p>
                  <div v-if="activeProviderModelGroups.length" class="provider-model-groups">
                    <section v-for="group in activeProviderModelGroups" :key="group.name" class="provider-model-group">
                      <header>
                        <strong>{{ group.name }}</strong>
                        <span>{{ group.models.length }}</span>
                      </header>
                      <div class="provider-model-list">
                        <ProviderModelRow
                          v-for="modelName in group.models"
                          :key="modelName"
                          :model-name="modelName"
                          :capability-summary="modelCapabilityLabels(modelName, activeProvider.model_capabilities[modelName]).join(' · ')"
                          :manual-value="draftModelCapabilitySelection(activeProvider, modelName)"
                          :manual-options="providerModelCapabilityOptions"
                          :assignment-value="catalogModelAssignmentValue(activeProvider, modelName)"
                          :assignment-options="catalogModelAssignmentOptions(activeProvider, modelName)"
                          :assignment-placeholder="t('providerModelAssignPlaceholder')"
                          :assignment-loading="catalogAssignmentBusy === `${activeProvider.id}:${modelName}`"
                          :assignment-disabled="modelRoutingSaving || catalogAssignmentBusy !== '' || !catalogModelCanAssign(activeProvider, modelName)"
                          :assignment-title="catalogModelCanAssign(activeProvider, modelName) ? '' : t('providerModelAssignmentUnavailable')"
                          :remove-title="t('providerRemove')"
                          @update:manual-value="setDraftModelCapability(activeProvider, modelName, $event)"
                          @update:assignment-value="assignCatalogModelRole(activeProvider, modelName, $event)"
                          @remove="removeProviderModel(activeProvider, modelName)"
                        />
                      </div>
                    </section>
                  </div>
                  <p v-else class="provider-model-empty">{{ t('providerNoModels') }}</p>
                </section>

                <ProviderTestSection
                  :model-value="providerTestModels[activeProvider.id] ?? ''"
                  :model-placeholder="activeProvider.models[0] || String(store.config.model || 'gpt-4o-mini')"
                  :mode-value="providerTestModes[activeProvider.id] || 'auto'"
                  :mode-options="providerTestModeOptions"
                  :action-label="providerTestActionLabel(providerTestModels[activeProvider.id] || activeProvider.models[0] || String(store.config.model || 'gpt-4o-mini'), providerTestModes[activeProvider.id] || 'auto')"
                  :testing="providerTestingId === activeProvider.id"
                  :saving="providerSaving"
                  :can-save="providerLibrarySupported"
                  :show-result="providerTestedId === activeProvider.id"
                  :result="providerTestResult"
                  :result-kind="providerTestedKind"
                  @update:model-value="setActiveProviderTestModel"
                  @update:mode-value="setActiveProviderTestMode"
                  @test="testProviderDraft(activeProvider, providerTestModels[activeProvider.id] || activeProvider.models[0] || '')"
                  @save="saveProvidersList"
                  @remove="removeProviderDraft(providerDrafts.findIndex(provider => provider.id === activeProvider?.id))"
                />
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

          <ProviderCatalogModal
            v-model:show="providerCatalogOpen"
            :provider="activeCatalogProvider"
            :models="providerCatalogSourceModels"
            :loading="providerFetchingModelsId === activeCatalogProvider?.id"
            @refresh="activeCatalogProvider && fetchProviderModels(activeCatalogProvider)"
            @toggle="toggleCatalogModel"
            @add-custom="addCustomProviderModel"
            @add-all="addAllCatalogModels"
          />



          <div id="settings-network" v-show="section === 'network'" class="settings-pane">
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

          <div id="settings-connection" v-if="standaloneFrontend" v-show="section === 'connection'" class="settings-pane">
            <h3>{{ t('backendConnectionTitle') }}</h3>
            <p class="muted">{{ t('backendConnectionHelp') }}</p>
            <div class="form-row">
              <label>{{ t('serverAddress') }}</label>
              <NInput v-model:value="backendUrl" :placeholder="t('serverAddressPlaceholder')" />
            </div>
            <div class="actions-row">
              <NButton type="primary" @click="switchBackend">{{ t('switchBackend') }}</NButton>
            </div>
          </div>

          <div id="settings-sharing" v-show="section === 'sharing'" class="settings-pane">
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
            <h3>{{ t('corsOriginsTitle') }}</h3>
            <p class="muted">{{ t('corsOriginsHelp') }}</p>
            <NInput
              :value="store.config.web_cors_origins ?? ''"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 5 }"
              :disabled="store.config.web_cors_origins_source === 'env'"
              :placeholder="t('corsOriginsPlaceholder')"
              @update:value="setStr('web_cors_origins', $event)"
            />
            <p class="form-hint">
              {{ store.config.web_cors_origins_source === 'env' ? t('corsOriginsEnvOverride') : t('corsOriginsConfigHint') }}
            </p>
            <div class="actions-row">
              <NButton
                type="primary"
                :disabled="store.config.web_cors_origins_source === 'env'"
                @click="save(['web_cors_origins'])"
              >{{ t('saveCorsOrigins') }}</NButton>
            </div>
          </div>

          <div id="settings-botapi" v-show="section === 'botapi'" class="settings-pane">
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
            <section id="settings-appearance" class="appearance-mode-section">
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

          <div id="settings-security" v-show="section === 'security'" class="settings-pane advanced-settings-pane security-pane">
            <section id="settings-hub-privacy" v-show="section === 'security'" class="advanced-section advanced-section-wide hub-section">
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
            <section class="advanced-section advanced-section-wide security-guide">
              <header class="advanced-section-head">
                <NIcon :component="InformationCircleOutline" />
                <div><h3>{{ t('securityGuideTitle') }}</h3></div>
              </header>
              <div class="security-guide-grid">
                <article class="security-guide-card security-guide-card-lan">
                  <NIcon :component="ServerOutline" />
                  <div>
                    <span class="security-guide-kicker">LAN</span>
                  <strong>{{ t('securityGuideLan') }}</strong>
                  <small>{{ t('securityGuideLanHint') }}</small>
                  </div>
                </article>
                <article class="security-guide-card security-guide-card-public">
                  <NIcon :component="ShareSocialOutline" />
                  <div>
                    <span class="security-guide-kicker">INTERNET</span>
                  <strong>{{ t('securityGuidePublic') }}</strong>
                  <small>{{ t('securityGuidePublicHint') }}</small>
                  </div>
                </article>
              </div>
            </section>
            <section class="advanced-section advanced-section-wide security-connection-section">
              <header class="advanced-section-head">
                <NIcon :component="ShieldCheckmarkOutline" />
                <div><h3>{{ t('securityConnectionTitle') }}</h3><p>{{ t('securityConnectionHint') }}</p></div>
              </header>
              <p v-if="securityStatus?.degraded_error" class="error-text security-degraded">{{ securityStatus.degraded_error }}</p>
              <div class="advanced-row security-mode-row">
                <div>
                  <strong>{{ t('securityModeHttp') }}</strong>
                  <small>{{ t('securityModeHttpHint') }}</small>
                </div>
                <div class="security-mode-actions">
                  <NTag v-if="securityStatus?.tls_mode === 'off'" type="success" size="small" round>{{ t('securityModeActive') }}</NTag>
                  <NButton v-else size="small" secondary :loading="securityBusy === 'disable'" :disabled="securityBusy !== ''" @click="disableLocalHttps">
                    {{ t('securitySwitchToHttp') }}
                  </NButton>
                </div>
              </div>
              <div class="advanced-row security-mode-row">
                <div>
                  <strong>{{ t('securityModeSelfSigned') }}</strong>
                  <small>{{ t('securityModeSelfSignedHint') }}</small>
                </div>
                <div class="security-mode-actions">
                  <NTag v-if="securityStatus?.tls_mode === 'self_signed'" type="warning" size="small" round>
                    {{ t('securityInsecureIndicator') }}
                  </NTag>
                  <NTag v-if="securityStatus?.tls_mode === 'self_signed'" type="success" size="small" round>{{ t('securityModeActive') }}</NTag>
                  <NButton
                    v-else
                    size="small"
                    type="primary"
                    secondary
                    :loading="securityBusy === 'enable'"
                    :disabled="securityBusy !== ''"
                    @click="enableLocalHttps"
                  >{{ securityStatus?.tls_mode === 'lets_encrypt' ? t('securitySwitchToLocalHttps') : t('securityEnableLocalHttps') }}</NButton>
                </div>
              </div>
              <NCollapse class="security-self-signed-guide" :display-directive="'show'">
                <NCollapseItem :title="t('securitySelfSignedGuideTitle')" name="self-signed-guide">
                  <div class="security-guide-warning">
                    <NIcon :component="AlertCircleOutline" />
                    <div>
                      <strong>{{ t('securityGuideWarning') }}</strong>
                      <small>{{ t('securityGuideWarningHint') }}</small>
                    </div>
                  </div>
                  <p class="security-proxy-intro">{{ t('securityProxyIntro') }}</p>
                  <ol class="security-proxy-steps">
                    <li>
                      <strong>{{ t('securityProxyStepDomain') }}</strong>
                      <small>{{ t('securityProxyStepDomainHint') }}</small>
                    </li>
                    <li>
                      <strong>{{ t('securityProxyStepCert') }}</strong>
                      <small>{{ t('securityProxyStepCertHint') }}</small>
                    </li>
                    <li>
                      <strong>{{ t('securityProxyStepProxy') }}</strong>
                      <small>{{ t('securityProxyStepProxyHint') }}</small>
                    </li>
                  </ol>
                  <div class="security-proxy-code">
                    <code>docker run -d --name diceframe -p 127.0.0.1:9876:9876 -v ./data:/app/data ghcr.io/diceframe/diceframe:latest</code>
                    <code>caddy reverse-proxy --from game.example.com --to 127.0.0.1:9876</code>
                  </div>
                  <div class="security-proxy-note">
                    <NIcon :component="InformationCircleOutline" />
                    <small>{{ t('securityProxyNote') }}</small>
                  </div>
                  <div class="security-proxy-note">
                    <NIcon :component="ShareSocialOutline" />
                    <small>{{ t('securityProxyTunnelNote') }}</small>
                  </div>
                </NCollapseItem>
              </NCollapse>
              <div class="advanced-row security-mode-lets-encrypt">
                <div class="security-mode-summary">
                  <div class="security-mode-copy">
                    <strong>{{ t('securityModeLetsEncrypt') }}</strong>
                    <small>{{ t('securityModeLetsEncryptHint') }}</small>
                  </div>
                  <NTag v-if="securityStatus?.tls_mode === 'lets_encrypt'" type="success" size="small" round>{{ t('securityModeActive') }}</NTag>
                </div>
                <NCollapse class="security-lets-encrypt-setup" :display-directive="'show'">
                  <NCollapseItem :title="t('securityAcmeSetupTitle')" name="lets-encrypt-setup">
                    <div class="security-acme-workflow">
                      <label class="security-acme-step">
                        <span class="security-step-index">1</span>
                        <span class="security-step-content">
                          <strong>{{ t('securityAcmeStepType') }}</strong>
                          <small>{{ t('securityAcmeStepTypeHint') }}</small>
                          <NSelect v-model:value="acmeIdentifierType" size="small" :options="[
                            { label: t('securityAcmeDomain'), value: 'dns' },
                            { label: t('securityAcmePublicIp'), value: 'ip' },
                          ]" />
                        </span>
                      </label>
                      <label class="security-acme-step security-acme-address-step">
                        <span class="security-step-index">2</span>
                        <span class="security-step-content">
                          <strong>{{ t('securityAcmeStepAddress') }}</strong>
                          <small>{{ acmeIdentifierType === 'dns' ? t('securityAcmeDomainHint') : t('securityAcmeIpHint') }}</small>
                          <span class="security-acme-address-fields">
                            <NInput v-model:value="acmeIdentifier" size="small" :placeholder="acmeIdentifierType === 'dns' ? 'game.example.com' : t('securityAcmeIpPlaceholder')" />
                            <NInput v-model:value="acmeContactEmail" size="small" :placeholder="t('securityAcmeEmailOptional')" />
                          </span>
                        </span>
                      </label>
                      <label class="security-acme-step">
                        <span class="security-step-index">3</span>
                        <span class="security-step-content">
                          <strong>{{ t('securityAcmeStepVerify') }}</strong>
                          <small>{{ t('securityAcmeStepVerifyHint') }}</small>
                          <NInputNumber v-model:value="acmeChallengePort" size="small" :min="1" :max="65535" />
                        </span>
                      </label>
                    </div>
                    <div class="security-acme-actions">
                      <small>{{ t('securityAcmeActionHint') }}</small>
                      <NButton type="primary" :loading="securityBusy === 'acme'" :disabled="securityBusy !== ''" @click="enableLetsEncrypt">
                        {{ securityStatus?.tls_mode === 'lets_encrypt' ? t('securityReissueLetsEncrypt') : t('securityEnableLetsEncrypt') }}
                      </NButton>
                    </div>
                  </NCollapseItem>
                </NCollapse>
              </div>
            </section>

            <section class="advanced-section">
              <header class="advanced-section-head">
                <NIcon :component="LockClosedOutline" />
                <div><h3>{{ t('securityAccessProtectionTitle') }}</h3><p>{{ t('securityAccessProtectionHint') }}</p></div>
              </header>
              <div class="advanced-row">
                <div>
                  <strong>{{ t('settingsSectionAccess') }}</strong>
                  <small>{{ t('securityAccessProtectionEntry') }}</small>
                </div>
                <NButton size="small" @click="section = 'access'">{{ t('securityAccessProtectionOpen') }}</NButton>
              </div>
            </section>

            <section v-if="securityStatus?.certificate" class="advanced-section">
              <header class="advanced-section-head">
                <NIcon :component="ShieldCheckmarkOutline" />
                <div><h3>{{ t('securityCertificateTitle') }}</h3><p>{{ t('securityCertificateHint') }}</p></div>
              </header>
              <div class="advanced-row security-address-row">
                <div class="security-address-cell">
                  <strong>{{ currentOrigin }}</strong>
                  <small>{{ t('securityCurrentAddressHint') }}</small>
                </div>
                <div class="security-mode-actions">
                  <NButton size="small" @click="copyCurrentAddress">
                    <template #icon><NIcon :component="CopyOutline" /></template>
                    {{ t('securityCopyAddress') }}
                  </NButton>
                </div>
              </div>
              <div class="advanced-row">
                <div><strong>{{ t('securityCertType') }}</strong><small>{{ securityStatus.tls_mode === 'lets_encrypt' ? t('securityModeLetsEncrypt') : t('securityModeSelfSigned') }}</small></div>
              </div>
              <div v-if="securityStatus.certificate.identifier" class="advanced-row">
                <div><strong>{{ t('securityCertIdentifier') }}</strong><small>{{ securityStatus.certificate.identifier }}</small></div>
              </div>
              <div class="advanced-row">
                <div><strong>{{ t('securityCertIssuer') }}</strong><small>{{ securityStatus.certificate.issuer }}</small></div>
              </div>
              <div class="advanced-row">
                <div><strong>{{ t('securityCertValidity') }}</strong><small>{{ securityStatus.certificate.not_before }} → {{ securityStatus.certificate.not_after }}</small></div>
              </div>
              <div v-if="securityStatus.tls_mode === 'lets_encrypt'" class="advanced-row">
                <div><strong>{{ t('securityCertRenewal') }}</strong><small>{{ securityStatus.certificate.renewal_status || t('securityCertRenewalUnknown') }}</small></div>
              </div>
              <div class="advanced-row">
                <div><strong>{{ t('securityFingerprintLabel') }}</strong><small class="security-fingerprint">{{ securityStatus.certificate.fingerprint_sha256 }}</small></div>
                <div class="security-mode-actions">
                  <NButton size="small" @click="copySecurityFingerprint">
                    <template #icon><NIcon :component="CopyOutline" /></template>
                    {{ t('securityCopyFingerprint') }}
                  </NButton>
                  <NButton
                    v-if="securityStatus?.tls_mode === 'self_signed'"
                    size="small"
                    type="warning"
                    secondary
                    :loading="securityBusy === 'regenerate'"
                    :disabled="securityBusy !== ''"
                    @click="regenerateLocalCertificate"
                  >{{ t('securityRegenerateCertificate') }}</NButton>
                </div>
              </div>
            </section>
            <section v-else-if="securityStatus && securityStatus.tls_mode === 'off'" class="advanced-section security-certificate-placeholder">
              <header class="advanced-section-head">
                <NIcon :component="KeyOutline" />
                <div><h3>{{ t('securityCertificateTitle') }}</h3><p>{{ t('securityCertificateHint') }}</p></div>
              </header>
              <div class="advanced-row">
                <div>
                  <strong>{{ t('securityFingerprintLabel') }}</strong>
                  <small>{{ t('securityNoCertificate') }}</small>
                </div>
              </div>
            </section>
          </div>

          <div id="settings-access" v-show="section === 'access'" class="settings-pane">
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

          <div v-show="section === 'models'" class="settings-pane advanced-settings-pane">
          <ModelRoutingPane
            id="settings-model-routing"
            class="advanced-section-wide"
            v-show="section === 'models'"
            :supported="providerLibrarySupported"
            :saving="modelRoutingSaving"
            :embedding-testing="testing && testKind === 'embedding'"
            :embedding-result="testKind === 'embedding' ? testResult : null"
            @save="saveModelRouting"
            @open-providers="section = 'api'"
            @toggle-and-save="setModelRoutingBool"
            @test-embedding="runTest('embedding')"
            :tts-testing="ttsTesting"
            :asr-testing="asrTesting"
            :asr-recording="asrTestRecording"
            :asr-test-text="asrTestText"
            @test-tts="testTts(saveModelRouting)"
            @test-asr="testAsr(saveModelRouting)"
            @tts-voices-changed="loadTtsVoices"
          />
          </div>

          <div v-show="section === 'about'" class="settings-pane about">
            <section id="settings-update" class="update-card" :aria-label="t('versionUpdate')">
              <header class="update-card-head">
                <h4>{{ t('versionUpdate') }}</h4>
                <NTag :type="updateTagType" size="small" round>{{ updateTagLabel }}</NTag>
              </header>
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
                <div class="update-notes-body safe-markdown" v-html="releaseNotesHtml" />
              </div>
              <div v-if="updateStatus && (updateInfo?.update_available || isDisplayedUpdateBusy)" class="update-download">
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
                    <p v-if="dockerRuntimeUpgradeRequired">{{ t('updateDockerRuntimeRequired') }}</p>
                  </div>
                  <div v-else-if="displayedUpdateState === 'applying'" class="muted">
                    {{ t('updateApplying') }}
                  </div>
                  <div v-else-if="displayedUpdateState === 'restarting'" class="muted">
                    {{ t('updateRestarting') }}
                  </div>
                  <div v-else-if="displayedUpdateState === 'done'" class="update-staged">
                    <NTag type="success" size="small" round>{{ updateStatus.operation === 'rollback' ? t('rollbackUpdateDone') : t('updateApplied') }}</NTag>
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
                      {{ requiredUpdateKind === 'docker'
                        ? t('downloadUpdateDocker')
                        : requiredUpdateKind === 'portable'
                          ? t('downloadUpdatePortable')
                          : t('downloadUpdateSource') }}
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
                <a href="https://github.com/diceframe/diceframe-mobile/releases" target="_blank" rel="noopener"><span>{{ t('androidClient') }}</span><strong>diceframe-mobile</strong></a>
                <a href="https://github.com/diceframe/diceframe/graphs/contributors" target="_blank" rel="noopener"><span>{{ t('contributors') }}</span><strong>{{ t('viewContributors') }}</strong></a>
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
                <img :src="sponsorQrUrl" :alt="t('wechatSponsorQr')" loading="lazy">
                <p class="muted">{{ t('supportProjectText') }}</p>
              </div>
            </NModal>
          </div>
        </NSpin>
      </div>
    </div>
  </section>
</template>

<style scoped>
.settings-status-disclosure {
  margin: 0 0 14px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-md, 10px);
  background: color-mix(in srgb, var(--df-surface-1) 88%, transparent);
}

.settings-status-disclosure > summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 13px;
  cursor: pointer;
  color: var(--df-text-secondary);
  font-size: 13px;
  font-weight: 650;
  user-select: none;
}

.settings-status-disclosure > summary:hover { color: var(--df-text); background: color-mix(in srgb, var(--df-accent) 7%, transparent); }
.settings-status-disclosure > summary:focus-visible { outline: 2px solid var(--df-accent); outline-offset: -2px; }
.settings-status-count { color: var(--df-text-muted); font-variant-numeric: tabular-nums; }
.settings-status-disclosure[open] > summary { border-bottom: 1px solid var(--df-border-soft); }
.settings-status-disclosure .system-status-grid { margin: 0; padding: 10px; }

.settings-nav-group-label {
  margin: 13px 10px 4px;
  color: var(--df-text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.settings-nav .nav-item { min-height: 44px; }

.appearance-agent-section {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 22px;
  padding: 14px 16px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-md, 10px);
  background: color-mix(in srgb, var(--df-surface-1) 88%, transparent);
}

.appearance-agent-section h4,
.appearance-agent-section p { margin: 0; }
.appearance-agent-section p { margin-top: 4px; color: var(--df-text-muted); font-size: 12px; line-height: 1.5; }

@media (max-width: 560px) {
  .settings-search { max-width: none; }
  .appearance-agent-section { align-items: flex-start; flex-direction: column; }
}

@media (max-width: 980px) {
  .settings-nav-group-label { margin-top: 9px; }
}
</style>
