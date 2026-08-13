<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch, type Component } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NInput, NInputNumber, NSwitch, NTag, NIcon, NCollapse, NCollapseItem, NSpin, NProgress, NModal } from 'naive-ui'
import {
  ServerOutline, CubeOutline, CloudDownloadOutline,
  LockClosedOutline, OptionsOutline, InformationCircleOutline, ShareSocialOutline,
  KeyOutline, CopyOutline, EyeOutline, RefreshOutline, ColorPaletteOutline,
  ImageOutline, PowerOutline,
} from '@vicons/ionicons5'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { shouldAutoDownloadUpdate, updateStateForVersion, useUpdater } from '@/composables/useUpdater'
import { useLocale } from '@/composables/useLocale'
import { initializeTts, ttsRate, setTtsRate } from '@/utils/tts'
import { api, errorMessage } from '@/api/client'
import { speechApi } from '@/api/speech'
import { pluginApi } from '@/api/plugins'
import type { MessageKey } from '@/i18n'
import type { SecretKey } from '@/stores/useSettingsStore'
import type { AppConfig, HubPreferences, LoginAuditEntry, LoginAuditResponse, SecretField, TestResult, TtsVoiceCatalog } from '@/api/types'
import TestResultCard from '@/components/admin/TestResultCard.vue'
import TtsVoiceProfiles from '@/components/admin/TtsVoiceProfiles.vue'
import HelpButton from '@/components/common/HelpButton.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import { copyToClipboard } from '@/utils/clipboard'
import { useTheme } from '@/composables/useTheme'
import { useBackgroundImages, type BackgroundSlot } from '@/composables/useBackgroundImages'

type SectionId = 'api' | 'memory' | 'network' | 'sharing' | 'botapi' | 'appearance' | 'access' | 'advanced' | 'about'
type StatusTone = 'default' | 'success' | 'warning' | 'error' | 'info'
type UpdatePackageKind = 'source' | 'portable'
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

const TTS_CONFIG_KEYS = [
  'tts_provider', 'tts_base_url', 'tts_model', 'tts_audio_format',
  'tts_default_voice', 'tts_gm_voice', 'tts_player_voice',
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
function hasSecret(key: SecretKey, field?: SecretField) {
  return Boolean(store.secrets[key]?.trim() || field?.configured)
}
function apiFormatLabel(value?: unknown) {
  return value === 'anthropic' ? 'Anthropic' : t('apiFormatOpenAI')
}

const systemStatusItems = computed<SystemStatusItem[]>(() => {
  const c = store.config
  const mainReady = Boolean(c.base_url && c.model && hasSecret('api_key', c.api_key))
  const fallbackSlots = [
    { name: t('fallbackSlot1'), enabled: !!c.fallback1_enabled, model: c.fallback1_model, ready: Boolean(c.fallback1_base_url && c.fallback1_model && hasSecret('fallback1_api_key', c.fallback1_api_key)) },
    { name: t('fallbackSlot2'), enabled: !!c.fallback2_enabled, model: c.fallback2_model, ready: Boolean(c.fallback2_base_url && c.fallback2_model && hasSecret('fallback2_api_key', c.fallback2_api_key)) },
  ]
  const enabledFallbacks = fallbackSlots.filter(item => item.enabled)
  const readyFallbacks = enabledFallbacks.filter(item => item.ready)
  const embeddingReady = Boolean(c.embedding_enabled && c.embedding_base_url && c.embedding_model && hasSecret('embedding_api_key', c.embedding_api_key))
  const proxyEnabled = !!c.proxy_enabled
  return [
    {
      label: t('statusMainModel'),
      value: mainReady ? t('statusComplete') : t('statusNeedsSetup'),
      detail: `${apiFormatLabel(c.api_format)} · ${c.model || t('modelUnset')} · ${c.base_url || t('endpointUnset')} · ${hasSecret('api_key', c.api_key) ? t('keyConfigured') : t('keyMissing')}`,
      tone: mainReady ? 'success' : 'warning',
      icon: ServerOutline,
    },
    {
      label: t('statusFallback'),
      value: enabledFallbacks.length ? t('routesAvailable', { ready: readyFallbacks.length, total: enabledFallbacks.length }) : t('disabled'),
      detail: enabledFallbacks.length ? enabledFallbacks.map(item => `${item.name}: ${item.model || t('modelUnset')}`).join(' · ') : t('fallbackDetailHint'),
      tone: !enabledFallbacks.length ? 'default' : readyFallbacks.length === enabledFallbacks.length ? 'success' : 'warning',
      icon: CubeOutline,
    },
    {
      label: t('statusVectorMemory'),
      value: c.embedding_enabled ? (embeddingReady ? t('enabled') : t('statusIncomplete')) : t('disabled'),
      detail: `${c.embedding_model || t('modelUnset')} · ${c.embedding_base_url || t('endpointUnset')} · ${t('inputLimit')} ${c.embedding_max_input || t('auto')}`,
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

async function downloadUpdatePackage(kind: UpdatePackageKind) {
  try {
    const result = await startDownload(kind)
    if (!result.ok) {
      toast.error(result.error || t('updateDownloadFailed'))
    } else {
      toast.success(t('updateDownloadStarted'))
    }
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

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
          <div v-show="section === 'api'" class="settings-pane">
            <div class="api-head-row"><h3>{{ t('mainModelApi') }}</h3><HelpButton :title="t('deepseekHelpTitle')">
              <h4>{{ t('deepseekHelpStep1Title') }}</h4>
              <p>{{ t('deepseekHelpStep1TextBefore') }} <a href="https://platform.deepseek.com/" target="_blank" rel="noopener">{{ t('deepseekPlatform') }}</a>{{ t('deepseekHelpStep1TextAfter') }}</p>
              <h4>{{ t('deepseekHelpStep2Title') }}</h4>
              <p>{{ t('deepseekHelpStep2Text') }} <code>sk-xxxxxxxx</code>{{ t('deepseekHelpStep2Suffix') }}</p>
              <h4>{{ t('deepseekHelpStep3Title') }}</h4>
              <p>{{ t('deepseekHelpStep3Text') }}</p>
              <ul>
                <li><strong>{{ t('apiFormat') }}</strong>: {{ t('apiFormatOpenAI') }}</li>
                <li><strong>Base URL</strong>: <code>https://api.deepseek.com/v1</code></li>
                <li><strong>API Key</strong>: {{ t('deepseekHelpApiKey') }}</li>
                <li><strong>{{ t('model') }}</strong>: <code>deepseek-v4-flash</code></li>
              </ul>
              <p>{{ t('deepseekHelpFinish') }}</p>
            </HelpButton></div>
            <div class="form-row">
              <label>{{ t('apiFormat') }}</label>
              <select :value="store.config.api_format ?? 'openai'" @change="setStr('api_format', eventValue($event))">
                <option value="openai">{{ t('apiFormatOpenAI') }}</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
            <div class="form-row">
              <label>Base URL</label>
              <NInput
                :value="store.config.base_url ?? ''"
                :placeholder="store.config.api_format === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'"
                @update:value="setStr('base_url', $event)"
              />
            </div>
            <div class="form-row">
              <label>API Key</label>
              <NInput
                :value="store.secrets.api_key ?? ''"
                type="password"
                show-password-on="click"
                :placeholder="store.config.api_key?.configured ? t('secretConfiguredPlaceholder', { masked: store.config.api_key.masked }) : ''"
                @update:value="setSecret('api_key', $event)"
              />
            </div>
            <div class="form-row">
              <label>{{ t('model') }}</label>
              <NInput
                :value="store.config.model ?? ''"
                :placeholder="store.config.api_format === 'anthropic' ? 'claude-3-5-sonnet-latest' : 'gpt-4o-mini'"
                @update:value="setStr('model', $event)"
              />
            </div>
            <div class="actions-row">
              <NButton type="primary" @click="save(['api_format', 'base_url', 'model'], ['api_key'])">{{ t('saveAction') }}</NButton>
              <NButton :loading="testing && testKind === 'model'" @click="runTest('model')">{{ t('testConnection') }}</NButton>
            </div>
            <TestResultCard v-if="testKind === 'model' && testResult" :result="testResult" kind="model" />

            <NCollapse :default-expanded-names="[]">
              <NCollapseItem :title="t('fallbackModelCollapse')" name="fallback">
                <div class="form-row"><label>{{ t('fallbackSlot1') }}</label><div class="switch-inline"><NSwitch :value="!!store.config.fallback1_enabled" @update:value="setBool('fallback1_enabled', $event)" /><span>{{ t('enabled') }}</span></div></div>
                <div class="form-row"><label>{{ t('apiFormat') }}</label><select :value="store.config.fallback1_api_format ?? 'openai'" @change="setStr('fallback1_api_format', eventValue($event))"><option value="openai">{{ t('apiFormatOpenAI') }}</option><option value="anthropic">Anthropic</option></select></div>
                <div class="form-row"><label>Base URL</label><NInput :value="store.config.fallback1_base_url ?? ''" @update:value="setStr('fallback1_base_url', $event)" /></div>
                <div class="form-row"><label>API Key</label><NInput :value="store.secrets.fallback1_api_key ?? ''" type="password" show-password-on="click" @update:value="setSecret('fallback1_api_key', $event)" /></div>
                <div class="form-row"><label>{{ t('model') }}</label><NInput :value="store.config.fallback1_model ?? ''" @update:value="setStr('fallback1_model', $event)" /></div>
                <div class="form-row"><label>{{ t('fallbackSlot2') }}</label><div class="switch-inline"><NSwitch :value="!!store.config.fallback2_enabled" @update:value="setBool('fallback2_enabled', $event)" /><span>{{ t('enabled') }}</span></div></div>
                <div class="form-row"><label>{{ t('apiFormat') }}</label><select :value="store.config.fallback2_api_format ?? 'openai'" @change="setStr('fallback2_api_format', eventValue($event))"><option value="openai">{{ t('apiFormatOpenAI') }}</option><option value="anthropic">Anthropic</option></select></div>
                <div class="form-row"><label>Base URL</label><NInput :value="store.config.fallback2_base_url ?? ''" @update:value="setStr('fallback2_base_url', $event)" /></div>
                <div class="form-row"><label>API Key</label><NInput :value="store.secrets.fallback2_api_key ?? ''" type="password" show-password-on="click" @update:value="setSecret('fallback2_api_key', $event)" /></div>
                <div class="form-row"><label>{{ t('model') }}</label><NInput :value="store.config.fallback2_model ?? ''" @update:value="setStr('fallback2_model', $event)" /></div>
                <div class="actions-row">
                  <NButton type="primary" @click="save(['fallback1_enabled', 'fallback1_api_format', 'fallback1_base_url', 'fallback1_model', 'fallback2_enabled', 'fallback2_api_format', 'fallback2_base_url', 'fallback2_model'], ['fallback1_api_key', 'fallback2_api_key'])">{{ t('saveFallbackModels') }}</NButton>
                </div>
              </NCollapseItem>
            </NCollapse>
          </div>

          <div v-show="section === 'memory'" class="settings-pane">
            <div class="api-head-row"><h3>{{ t('vectorMemory') }}</h3><HelpButton :title="t('embeddingHelpTitle')">
              <h4>{{ t('embeddingHelpWhatTitle') }}</h4>
              <p>{{ t('embeddingHelpWhatText') }}</p>
              <h4>{{ t('embeddingHelpChooseTitle') }}</h4>
              <p>{{ t('embeddingHelpChooseBefore') }} <code>bge-m3</code>{{ t('embeddingHelpChooseAfter') }} <code>text-embedding-3-small</code>, <code>gte-large</code>, <code>nomic-embed-text</code>{{ t('embeddingHelpChooseSuffix') }}</p>
              <h4>{{ t('embeddingHelpConfigTitle') }}</h4>
              <ul>
                <li><strong>{{ t('embeddingEndpoint') }}</strong>: {{ t('embeddingHelpEndpoint') }} <code>https://api.siliconflow.cn/v1</code></li>
                <li><strong>API Key</strong>: {{ t('embeddingHelpKey') }}</li>
                <li><strong>{{ t('model') }}</strong>: <code>BAAI/bge-m3</code> {{ t('embeddingHelpModelSuffix') }} <code>bge-m3</code>{{ t('embeddingHelpParenEnd') }}</li>
                <li><strong>{{ t('maxInput') }}</strong>: {{ t('embeddingHelpMaxInput') }}</li>
              </ul>
              <p>{{ t('embeddingHelpProviders') }}</p>
              <h4>{{ t('test') }}</h4>
              <p>{{ t('embeddingHelpTest') }}</p>
            </HelpButton></div>
            <div class="form-row"><label>{{ t('vectorMemory') }}</label><div class="switch-inline"><NSwitch :value="!!store.config.embedding_enabled" @update:value="setBool('embedding_enabled', $event)" /><span>{{ t('enabled') }}</span></div></div>
            <div class="form-row"><label>{{ t('embeddingEndpoint') }}</label><NInput :value="store.config.embedding_base_url ?? ''" @update:value="setStr('embedding_base_url', $event)" /></div>
            <div class="form-row">
              <label>API Key</label>
              <NInput
                :value="store.secrets.embedding_api_key ?? ''"
                type="password"
                show-password-on="click"
                :placeholder="store.config.embedding_api_key?.configured ? t('secretConfiguredPlaceholder', { masked: store.config.embedding_api_key.masked }) : ''"
                @update:value="setSecret('embedding_api_key', $event)"
              />
            </div>
            <div class="form-row"><label>{{ t('model') }}</label><NInput :value="store.config.embedding_model ?? ''" @update:value="setStr('embedding_model', $event)" /></div>
            <div class="form-row"><label>{{ t('maxInput') }}</label><NInputNumber :value="store.config.embedding_max_input ?? 0" @update:value="setNum('embedding_max_input', $event)" style="width:100%" /></div>
            <p class="form-hint">{{ t('maxInputHint') }}</p>
            <div class="actions-row">
              <NButton type="primary" @click="save(['embedding_enabled', 'embedding_base_url', 'embedding_model', 'embedding_max_input'], ['embedding_api_key'])">{{ t('saveAction') }}</NButton>
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
              <div class="tts-settings-group">
                <div class="tts-group-heading">
                  <div><strong>{{ t('ttsEngineConnection') }}</strong><small>{{ t('ttsEngineConnectionHint') }}</small></div>
                </div>
                <div class="form-row">
                  <label>{{ t('ttsProvider') }}</label>
                  <select :value="store.config.tts_provider ?? 'browser'" @change="setStr('tts_provider', eventValue($event))">
                    <option value="browser">{{ t('ttsProviderBrowser') }}</option>
                    <option value="openai-compatible">{{ t('ttsProviderOpenAI') }}</option>
                    <option value="gpt-sovits">GPT-SoVITS</option>
                  </select>
                </div>
                <template v-if="ttsProvider !== 'browser'">
                  <div class="form-row">
                    <label>Base URL</label>
                    <NInput
                      :value="store.config.tts_base_url ?? ''"
                      :placeholder="ttsProvider === 'gpt-sovits' ? 'http://127.0.0.1:9880' : 'https://api.openai.com/v1'"
                      @update:value="setStr('tts_base_url', $event)"
                    />
                  </div>
                  <div class="form-row">
                    <label>API Key</label>
                    <NInput
                      :value="store.secrets.tts_api_key ?? ''"
                      type="password"
                      show-password-on="click"
                      :placeholder="store.config.tts_api_key?.configured ? t('secretConfiguredPlaceholder', { masked: store.config.tts_api_key.masked }) : t('ttsApiKeyOptional')"
                      @update:value="setSecret('tts_api_key', $event)"
                    />
                  </div>
                  <div v-if="ttsProvider === 'openai-compatible'" class="form-row">
                    <label>{{ t('model') }}</label>
                    <NInput :value="store.config.tts_model ?? 'tts-1'" placeholder="tts-1" @update:value="setStr('tts_model', $event)" />
                  </div>
                  <div class="form-row">
                    <label>{{ t('ttsAudioFormat') }}</label>
                    <select :value="store.config.tts_audio_format ?? 'mp3'" @change="setStr('tts_audio_format', eventValue($event))">
                      <option value="mp3">MP3</option><option value="wav">WAV</option><option value="opus">Opus</option><option value="flac">FLAC</option><option value="aac">AAC</option>
                    </select>
                  </div>
                  <div class="form-row">
                    <label>{{ t('ttsCacheSize') }}</label>
                    <NInputNumber :value="Number(store.config.tts_cache_mb ?? 256)" :min="16" :max="2048" :step="64" @update:value="setNum('tts_cache_mb', $event)" />
                  </div>
                  <TtsVoiceProfiles :provider="ttsProvider" @changed="loadTtsVoices" />
                </template>
              </div>

              <div class="tts-settings-group">
                <div class="tts-group-heading">
                  <div><strong>{{ t('ttsRoleMapping') }}</strong><small>{{ t('ttsRoleMappingHint') }}</small></div>
                </div>
                <template v-if="ttsProvider !== 'browser'">
                <div class="form-row">
                  <label>{{ t('ttsDefaultVoice') }}</label>
                  <input :value="store.config.tts_default_voice ?? ''" list="diceframe-tts-voices" @input="setStr('tts_default_voice', eventValue($event))" />
                </div>
                <datalist id="diceframe-tts-voices">
                  <option v-for="voice in ttsVoiceOptions" :key="voice.id" :value="voice.id">{{ voice.name }}</option>
                </datalist>
                <div class="form-row">
                  <label>{{ t('ttsGmVoice') }}</label>
                  <input :value="store.config.tts_gm_voice ?? ''" list="diceframe-tts-voices" :placeholder="t('ttsFollowDefault')" @input="setStr('tts_gm_voice', eventValue($event))" />
                </div>
                <div class="form-row">
                  <label>{{ t('ttsPlayerVoice') }}</label>
                  <input :value="store.config.tts_player_voice ?? ''" list="diceframe-tts-voices" :placeholder="t('ttsFollowDefault')" @input="setStr('tts_player_voice', eventValue($event))" />
                </div>
                  <p v-if="ttsProvider === 'gpt-sovits' && !ttsVoiceOptions.length" class="muted">{{ t('ttsGptVoiceHint') }}</p>
                </template>
                <p v-else class="muted">{{ t('ttsBrowserVoiceMappingHint') }}</p>
              </div>

              <div class="tts-settings-group">
                <div class="advanced-row">
                  <div><strong>{{ t('ttsAutoSpeak') }}</strong><small>{{ t('ttsAutoSpeakHint') }}</small></div>
                  <div class="switch-inline"><NSwitch :value="autoSpeak" @update:value="setAutoSpeak" /><span>{{ t('enabled') }}</span></div>
                </div>
                <div class="advanced-row">
                  <div><strong>{{ t('ttsRate') }}</strong><small>{{ t('ttsRateHint') }}</small></div>
                  <NInputNumber class="advanced-number" :value="ttsRateValue" :min="0.5" :max="5" :step="0.1" @update:value="setTtsRateValue" />
                </div>
              </div>
              <footer class="advanced-save-row">
                <NButton type="primary" @click="saveTts()">{{ t('saveAction') }}</NButton>
                <NButton v-if="ttsProvider !== 'browser'" :loading="ttsTesting" @click="testTts">{{ t('ttsSaveAndTest') }}</NButton>
              </footer>
            </section>
            <section class="advanced-section">
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
                <a href="https://github.com/diceframe/diceframe/tree/main/docs" target="_blank" rel="noopener"><span>{{ t('guideDocs') }}</span><strong>diceframe/diceframe</strong></a>
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
