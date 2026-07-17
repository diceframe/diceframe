<script setup lang="ts">
import { computed, onMounted, ref, watch, type Component } from 'vue'
import { NButton, NInput, NInputNumber, NSwitch, NTag, NIcon, NCollapse, NCollapseItem, NSpin } from 'naive-ui'
import {
  ServerOutline, CubeOutline, CloudDownloadOutline, ExtensionPuzzleOutline,
  LockClosedOutline, OptionsOutline, InformationCircleOutline, ShareSocialOutline,
} from '@vicons/ionicons5'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useUpdateCheck } from '@/composables/useUpdateCheck'
import { useLocale } from '@/composables/useLocale'
import { errorMessage } from '@/api/client'
import type { MessageKey } from '@/i18n'
import type { SecretKey } from '@/stores/useSettingsStore'
import type { AppConfig, SecretField, TestResult } from '@/api/types'
import AppPage from '@/components/common/AppPage.vue'
import TestResultCard from '@/components/admin/TestResultCard.vue'
import HelpButton from '@/components/common/HelpButton.vue'
import PluginSettings from '@/features/plugins/PluginSettings.vue'

type SectionId = 'api' | 'memory' | 'network' | 'sharing' | 'plugins' | 'access' | 'advanced' | 'about'
type StatusTone = 'default' | 'success' | 'warning' | 'error' | 'info'
type SystemStatusItem = { label: string; value: string; detail: string; tone: StatusTone }
type SettingsSection = { id: SectionId; labelKey: MessageKey; icon: Component }

const store = useSettingsStore()
const toast = useToast()
const { confirm } = useConfirm()
const { updateInfo, updateChecking, checkForUpdates } = useUpdateCheck()
const { t } = useLocale()

const section = ref<SectionId>('api')
const sections: SettingsSection[] = [
  { id: 'api', labelKey: 'settingsSectionApi', icon: ServerOutline },
  { id: 'memory', labelKey: 'settingsSectionMemory', icon: CubeOutline },
  { id: 'network', labelKey: 'settingsSectionNetwork', icon: CloudDownloadOutline },
  { id: 'sharing', labelKey: 'settingsSectionSharing', icon: ShareSocialOutline },
  { id: 'plugins', labelKey: 'settingsSectionPlugins', icon: ExtensionPuzzleOutline },
  { id: 'access', labelKey: 'settingsSectionAccess', icon: LockClosedOutline },
  { id: 'advanced', labelKey: 'settingsSectionAdvanced', icon: OptionsOutline },
  { id: 'about', labelKey: 'settingsSectionAbout', icon: InformationCircleOutline },
]

const testing = ref(false)
const testResult = ref<TestResult | null>(null)
const testKind = ref<'model' | 'embedding' | 'proxy' | ''>('')

const password = ref('')
const passwordConfirm = ref('')
const locationOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin

const proxySourceLabel = computed(() => {
  const s = store.config.proxy_source
  if (s === 'config') return t('proxySourceConfig')
  if (s === 'env') return t('proxySourceEnv')
  if (s === 'disabled') return t('proxySourceDisabled')
  return t('proxySourceUnset')
})
const proxyFormatLabel = computed(() => (store.config.proxy_supported ? t('proxyFormatSupported') : t('proxyFormatUnsupported')))
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
    },
    {
      label: t('statusFallback'),
      value: enabledFallbacks.length ? t('routesAvailable', { ready: readyFallbacks.length, total: enabledFallbacks.length }) : t('disabled'),
      detail: enabledFallbacks.length ? enabledFallbacks.map(item => `${item.name}: ${item.model || t('modelUnset')}`).join(' · ') : t('fallbackDetailHint'),
      tone: !enabledFallbacks.length ? 'default' : readyFallbacks.length === enabledFallbacks.length ? 'success' : 'warning',
    },
    {
      label: t('statusVectorMemory'),
      value: c.embedding_enabled ? (embeddingReady ? t('enabled') : t('statusIncomplete')) : t('disabled'),
      detail: `${c.embedding_model || t('modelUnset')} · ${c.embedding_base_url || t('endpointUnset')} · ${t('inputLimit')} ${c.embedding_max_input || t('auto')}`,
      tone: c.embedding_enabled ? (embeddingReady ? 'success' : 'warning') : 'default',
    },
    {
      label: t('statusNetworkProxy'),
      value: proxyEnabled ? t('enabled') : t('disabled'),
      detail: `${proxySourceLabel.value} · ${proxyFormatLabel.value}${c.proxy_url ? ` · ${c.proxy_url}` : ''}`,
      tone: proxyEnabled ? (c.proxy_supported === false ? 'error' : 'info') : 'default',
    },
    {
      label: t('statusAccessControl'),
      value: c.access_password?.configured ? t('passwordSet') : t('passwordUnset'),
      detail: c.access_password?.configured ? t('currentCredential', { masked: c.access_password.masked }) : t('localAccessNoPassword'),
      tone: c.access_password?.configured ? 'success' : 'default',
    },
  ]
})

onMounted(() => store.load())
watch(section, () => {
  const sc = document.querySelector('.n-layout-scroll-container') as HTMLElement | null
  sc?.scrollTo({ top: 0 })
})

function setStr(key: keyof AppConfig, v: string | number) { store.setConfigField(key, String(v).trim()) }
function setSecret(key: SecretKey, v: string | number) { store.secrets[key] = String(v).trim() }
function eventValue(event: Event) { return (event.target as HTMLSelectElement | null)?.value || '' }
function setNum(key: keyof AppConfig, v: string | number | null) {
  if (v === null || v === '') { store.setConfigField(key, 0); return }
  store.setConfigField(key, Number(v) || 0)
}
function setBool(key: keyof AppConfig, v: string | number | boolean) { store.setConfigField(key, Boolean(v)) }

const tokenFields: { key: keyof AppConfig; labelKey: MessageKey }[] = [
  { key: 'narrative_max_tokens', labelKey: 'narrativeTokens' },
  { key: 'character_gen_max_tokens', labelKey: 'characterGenTokens' },
  { key: 'summary_max_tokens', labelKey: 'summaryTokens' },
  { key: 'brief_max_tokens', labelKey: 'briefTokens' },
  { key: 'analysis_max_tokens', labelKey: 'analysisTokens' },
  { key: 'text_gen_max_tokens', labelKey: 'textGenTokens' },
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
</script>

<template>
  <AppPage :title="t('settingsTitle')" :subtitle="t('settingsSubtitle')">
    <template #actions>
      <NButton :loading="store.loading" @click="store.load()">{{ t('refresh') }}</NButton>
    </template>

    <p v-if="store.error" class="error-banner">{{ store.error }}</p>
    <section class="system-status-grid" :aria-label="t('settingsSystemStatusAria')">
      <article v-for="item in systemStatusItems" :key="item.label" class="system-status-card">
        <div class="system-status-head">
          <span>{{ item.label }}</span>
          <NTag :type="item.tone" size="small" round>{{ item.value }}</NTag>
        </div>
        <p>{{ item.detail }}</p>
      </article>
    </section>
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
                <li><strong>{{ t('model') }}</strong>: <code>deepseek-v4-pro</code></li>
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

          <div v-show="section === 'plugins'" class="settings-pane">
            <PluginSettings />
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
          </div>

          <div v-show="section === 'advanced'" class="settings-pane">
            <h3>{{ t('generationParams') }}</h3>
            <div v-for="item in tokenFields" :key="item.key" class="form-row">
              <label>{{ t(item.labelKey) }}</label>
              <NInputNumber :value="Number(store.config[item.key] ?? 0)" @update:value="setNum(item.key, $event)" style="width:100%" />
            </div>
            <div class="actions-row">
              <NButton type="primary" @click="save(['narrative_max_tokens', 'character_gen_max_tokens', 'summary_max_tokens', 'brief_max_tokens', 'analysis_max_tokens', 'text_gen_max_tokens'])">{{ t('saveAction') }}</NButton>
            </div>
          </div>

          <div v-show="section === 'about'" class="settings-pane about">
            <section class="about-card">
              <h3>{{ t('aboutDiceFrame') }}</h3>
              <p>{{ t('aboutIntro1') }}</p>
              <p>{{ t('aboutIntro2') }}</p>
              <h4>{{ t('whatCanDo') }}</h4>
              <ul>
                <li>{{ t('aboutFeature1') }}</li>
                <li>{{ t('aboutFeature2') }}</li>
                <li>{{ t('aboutFeature3') }}</li>
                <li>{{ t('aboutFeature4') }}</li>
                <li>{{ t('aboutFeature5') }}</li>
              </ul>
              <h4>{{ t('disclaimer') }}</h4>
              <p class="muted">{{ t('disclaimerText') }}</p>
              <h4>{{ t('contact') }}</h4>
              <p>{{ t('projectAddress') }}: <a href="https://github.com/diceframe/diceframe" target="_blank" rel="noopener">diceframe/diceframe</a></p>
              <p>{{ t('issueFeedback') }}: <a href="https://github.com/diceframe/diceframe/issues" target="_blank" rel="noopener">{{ t('submitIssue') }}</a></p>
              <p>{{ t('qqGroup') }}: 1060613588</p>
            </section>
            <section class="update-card" :aria-label="t('versionUpdate')">
              <div class="update-card-head">
                <div>
                  <h4>{{ t('versionUpdate') }}</h4>
                </div>
                <NTag :type="updateTagType" size="small" round>{{ updateTagLabel }}</NTag>
              </div>
              <div class="update-meta">
                <span>{{ t('currentVersion') }}: {{ updateInfo?.current_version || t('clickCheckVersion') }}</span>
                <span v-if="updateInfo?.latest">{{ t('latestVersion') }}: {{ updateInfo.latest.tag_name || updateInfo.latest.version }}</span>
                <span v-if="updateInfo?.latest?.published_at">{{ t('publishedAt') }}: {{ updateInfo.latest.published_at.slice(0, 10) }}</span>
              </div>
              <p v-if="updateInfo?.error" class="muted">{{ t('checkFailed') }}: {{ updateInfo.error }}</p>
              <p v-else-if="updateInfo?.no_release" class="muted">{{ updateInfo.message || t('repoNoReleaseMessage') }}</p>
              <p v-else-if="updateInfo?.update_available" class="muted">{{ t('updateAvailableHelp') }}</p>
              <div v-if="updateInfo?.latest?.body" class="update-notes">
                <strong>{{ t('releaseNotes') }}</strong>
                <pre>{{ updateInfo.latest.body }}</pre>
              </div>
              <div class="actions-row">
                <NButton :loading="updateChecking" @click="checkUpdate">{{ t('checkUpdate') }}</NButton>
                <NButton :disabled="!updateInfo?.release_url && !updateInfo?.releases_url && !updateInfo?.source_url" @click="openUpdateUrl">{{ t('openReleasePage') }}</NButton>
              </div>
            </section>
            <section class="sponsor-card" :aria-label="t('supportProject')">
              <div>
                <h4>{{ t('supportProject') }}</h4>
                <p>{{ t('supportProjectText') }}</p>
              </div>
              <img src="/sponsor-wechat-qr.png" :alt="t('wechatSponsorQr')" loading="lazy">
            </section>
          </div>
        </NSpin>
      </div>
    </div>
  </AppPage>
</template>
