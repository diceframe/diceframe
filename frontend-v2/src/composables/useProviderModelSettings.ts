import { computed, ref, watch } from 'vue'
import { errorMessage } from '@/api/client'
import type { AppConfig, TestResult } from '@/api/types'
import type { MessageKey } from '@/i18n'
import {
  providerSecretKey,
  useSettingsStore,
  type AiProviderInput,
  type ProviderModelsResponse,
} from '@/stores/useSettingsStore'
import {
  assignCatalogModelRoleWithRollback,
  CATALOG_MODEL_ROLES,
  catalogModelRoleEligible,
  isCatalogModelAssigned,
  modelCapability,
  providerTestKind,
  type CatalogModelRoleId,
  type ModelCapability,
  type ProviderDraft,
  type ProviderTestKind,
  type ProviderTestMode,
} from '@/utils/providerModels'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'

type ProviderModelGroup = { name: string; models: string[] }
type Translator = (key: MessageKey, params?: Record<string, string | number>) => string

export interface ProviderModelSettingsToast {
  success: (content: string) => void
  error: (content: string) => void
  info: (content: string) => void
  warning: (content: string) => void
}

interface ProviderTestInput {
  providerId?: string
  baseUrl: string
  apiKey: string
  apiFormat: string
  model: string
  kind?: ProviderTestKind
}

interface ProviderModelsInput {
  providerId?: string
  baseUrl: string
  apiKey: string
  apiFormat: string
}

export interface ProviderModelSettingsStore {
  config: Partial<AppConfig>
  secrets: Record<string, string | undefined>
  saveProviders: (providers: AiProviderInput[]) => Promise<string[]>
  saveSection: (keys: string[], secretKeys?: string[]) => Promise<string[]>
  testProvider: (input: ProviderTestInput) => Promise<TestResult>
  fetchProviderModels: (input: ProviderModelsInput) => Promise<ProviderModelsResponse>
  setConfigField: (key: keyof AppConfig, value: unknown) => void
}

export interface UseProviderModelSettingsOptions {
  store?: ProviderModelSettingsStore
  t?: Translator
  toast?: ProviderModelSettingsToast
  refreshModelRuntimes?: () => Promise<void>
  createProviderId?: () => string
}

export const MODEL_ROUTING_CONFIG_KEYS = [
  'llm_provider_ref', 'model',
  'fallback1_enabled', 'fallback1_provider_ref', 'fallback1_model',
  'fallback2_enabled', 'fallback2_provider_ref', 'fallback2_model',
  'embedding_enabled', 'embedding_provider_ref', 'embedding_model', 'embedding_max_input',
  'tts_provider', 'tts_provider_ref', 'tts_model', 'tts_default_voice',
  'asr_provider', 'asr_provider_ref', 'asr_model',
  'imagegen_enabled', 'imagegen_auto_scene', 'imagegen_provider_ref', 'imagegen_model',
  'imagegen_square_size', 'imagegen_landscape_size', 'imagegen_quality',
  'imagegen_style_prefix', 'imagegen_timeout_seconds',
] as const

export function hydrateProviderDrafts(list: AppConfig['ai_providers']): ProviderDraft[] {
  return (list || []).map(provider => ({
    id: provider.id,
    name: provider.name,
    base_url: provider.base_url,
    api_format: String(provider.api_format || 'openai'),
    models: [...new Set((provider.models || []).map(model => String(model).trim()).filter(Boolean))],
    model_capabilities: { ...(provider.model_capabilities || {}) },
    configuredMasked: provider.api_key?.configured ? provider.api_key.masked : '',
  }))
}

export function serializeProviderDrafts(drafts: ProviderDraft[]): AiProviderInput[] {
  return drafts.map(draft => ({
    id: draft.id,
    name: draft.name,
    base_url: draft.base_url,
    api_format: draft.api_format,
    models: [...draft.models],
    model_capabilities: { ...draft.model_capabilities },
  }))
}

export function useProviderModelSettings(options: UseProviderModelSettingsOptions = {}) {
  const store = options.store || useSettingsStore()
  const t = options.t || useLocale().t
  const toast = options.toast || useToast()
  const refreshModelRuntimes = options.refreshModelRuntimes || (async () => {})
  const createProviderId = options.createProviderId || (() => (
    `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
  ))

  const providerDrafts = ref<ProviderDraft[]>([])
  const providerTestModels = ref<Record<string, string>>({})
  const providerTestModes = ref<Record<string, ProviderTestMode>>({})
  const providerSearch = ref('')
  const activeProviderId = ref('')
  const providerSaving = ref(false)
  const providerTestingId = ref('')
  const providerFetchingModelsId = ref('')
  const providerTestedId = ref('')
  const providerTestedKind = ref<ProviderTestKind>('model')
  const providerTestResult = ref<TestResult | null>(null)
  const providerCatalogOpen = ref(false)
  const providerCatalogProviderId = ref('')
  const providerCatalogModels = ref<Record<string, string[]>>({})
  const modelRoutingSaving = ref(false)
  const catalogAssignmentBusy = ref('')

  const providerLibrarySupported = computed(() => (
    Object.prototype.hasOwnProperty.call(store.config, 'ai_providers')
  ))
  const savedProviderIds = computed(() => new Set((store.config.ai_providers || []).map(provider => provider.id)))
  const readyProviderIds = computed(() => providerDrafts.value.filter(providerDraftReady).map(provider => provider.id))
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

  function syncProviderDrafts(list: AppConfig['ai_providers']) {
    providerDrafts.value = hydrateProviderDrafts(list)
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
    const provider: ProviderDraft = {
      id: createProviderId(),
      name: '',
      base_url: '',
      api_format: 'openai',
      models: [],
      model_capabilities: {},
      configuredMasked: '',
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

  function setProviderSecret(id: string, value: string | number) {
    store.secrets[providerSecretKey(id)] = String(value).trim()
  }

  function providerSecretValue(id: string): string {
    return store.secrets[providerSecretKey(id)] || ''
  }

  async function saveProvidersList(): Promise<boolean> {
    if (!providerLibrarySupported.value) {
      toast.error(t('providerBackendOutdated'))
      return false
    }
    if (providerSaving.value) return false
    providerSaving.value = true
    try {
      const warnings = await store.saveProviders(serializeProviderDrafts(providerDrafts.value))
      toast.success(t('settingsSaved'))
      warnings.forEach(warning => toast.warning(warning))
      return true
    } catch (error: unknown) {
      toast.error(errorMessage(error))
      return false
    } finally {
      providerSaving.value = false
    }
  }

  async function testProviderDraft(draft: ProviderDraft, testModel: string) {
    if (providerTestingId.value) return
    providerTestingId.value = draft.id
    providerTestResult.value = null
    const model = testModel.trim() || String(store.config.model || 'gpt-4o-mini')
    const kind = providerTestKind(
      model,
      providerTestModes.value[draft.id] || 'auto',
      draft.model_capabilities[model],
    )
    if (!kind) {
      providerTestedId.value = draft.id
      providerTestedKind.value = 'model'
      providerTestResult.value = { ok: false, error: t('providerTestUnsupported') }
      providerTestingId.value = ''
      return
    }
    try {
      providerTestResult.value = await store.testProvider({
        providerId: savedProviderIds.value.has(draft.id) ? draft.id : undefined,
        baseUrl: draft.base_url,
        apiKey: store.secrets[providerSecretKey(draft.id)]?.trim() || '',
        apiFormat: draft.api_format,
        model,
        kind,
      })
      providerTestedId.value = draft.id
      providerTestedKind.value = kind
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      providerTestingId.value = ''
    }
  }

  async function fetchProviderModels(draft: ProviderDraft) {
    if (providerFetchingModelsId.value) return
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
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      providerFetchingModelsId.value = ''
    }
  }

  async function openProviderCatalog(draft: ProviderDraft) {
    providerCatalogProviderId.value = draft.id
    providerCatalogOpen.value = true
    if (!providerCatalogModels.value[draft.id]) {
      providerCatalogModels.value[draft.id] = [...draft.models]
      await fetchProviderModels(draft)
    }
  }

  function addProviderModel(draft: ProviderDraft, value: string) {
    const model = String(value || '').trim()
    if (model && !draft.models.includes(model)) draft.models.push(model)
  }

  function addCustomProviderModel(model: string) {
    if (!activeCatalogProvider.value) return
    addProviderModel(activeCatalogProvider.value, model)
    const id = activeCatalogProvider.value.id
    providerCatalogModels.value[id] = [
      ...new Set([...(providerCatalogModels.value[id] || []), model].filter(Boolean)),
    ]
  }

  function toggleCatalogModel(model: string) {
    if (!activeCatalogProvider.value) return
    if (activeCatalogProvider.value.models.includes(model)) removeProviderModel(activeCatalogProvider.value, model)
    else addProviderModel(activeCatalogProvider.value, model)
  }

  function addAllCatalogModels(models: string[]) {
    if (!activeCatalogProvider.value) return
    activeCatalogProvider.value.models = [...new Set([...activeCatalogProvider.value.models, ...models])]
  }

  function removeProviderModel(draft: ProviderDraft, model: string) {
    draft.models = draft.models.filter(item => item !== model)
    delete draft.model_capabilities[model]
  }

  function draftModelCapabilitySelection(draft: ProviderDraft | null, model: string): ModelCapability | 'auto' {
    return draft?.model_capabilities[model] || 'auto'
  }

  function setDraftModelCapability(draft: ProviderDraft, model: string, capability: string) {
    if (capability === 'auto') {
      delete draft.model_capabilities[model]
      return
    }
    if (!['chat', 'image', 'embedding', 'tts', 'asr'].includes(capability)) return
    draft.model_capabilities[model] = capability as ModelCapability
  }

  const providerModelCapabilityOptions = computed(() => [
    { label: t('modelCapabilityAuto'), value: 'auto' },
    { label: t('modelCapabilityChat'), value: 'chat' },
    { label: t('modelCapabilityImage'), value: 'image' },
    { label: t('modelCapabilityEmbedding'), value: 'embedding' },
    { label: t('modelCapabilityTts'), value: 'tts' },
    { label: t('modelCapabilityAsr'), value: 'asr' },
  ])

  const providerTestModeOptions = computed(() => [
    { label: t('providerTestAuto'), value: 'auto' },
    { label: t('providerTestChat'), value: 'model' },
    { label: t('providerTestEmbedding'), value: 'embedding' },
  ])

  function providerHasKey(draft: ProviderDraft): boolean {
    return Boolean(draft.configuredMasked || store.secrets[providerSecretKey(draft.id)]?.trim())
  }

  function providerDraftReady(draft: ProviderDraft): boolean {
    return Boolean(draft.base_url.trim())
  }

  function setActiveProviderTestModel(value: string | number) {
    if (activeProvider.value) providerTestModels.value[activeProvider.value.id] = String(value)
  }

  function setActiveProviderTestMode(value: string) {
    if (!activeProvider.value || !['auto', 'model', 'embedding'].includes(value)) return
    providerTestModes.value[activeProvider.value.id] = value as ProviderTestMode
  }

  function providerTestActionLabel(model: string, mode: ProviderTestMode): string {
    return providerTestKind(model, mode) === 'embedding' ? t('testEmbeddingConnection') : t('testConnection')
  }

  function providerMark(draft: ProviderDraft): string {
    return (draft.name || draft.id).trim().slice(0, 1).toUpperCase()
  }

  function providerStyle(providerId: string) {
    let hash = 0
    for (const character of providerId) hash = ((hash << 5) - hash) + character.charCodeAt(0)
    return { '--provider-hue': String(Math.abs(hash) % 360) }
  }

  function modelCapabilityLabels(model: string, override?: string): string[] {
    const capability = modelCapability(model, override)
    const labels = [override ? t('modelCapabilityManualOverride') : t('modelCapabilityAuto')]
    if (capability === 'image') return [...labels, t('modelCapabilityImage')]
    if (capability === 'embedding') return [...labels, t('modelCapabilityEmbedding')]
    if (capability === 'tts') return [...labels, t('modelCapabilityTts')]
    if (capability === 'asr') return [...labels, t('modelCapabilityAsr')]
    labels.push(t('modelCapabilityChat'))
    if (/(reason|thinking|deepseek-r|(^|[-_.])r1|(^|[-_.])o[134])/.test(model.toLowerCase())) {
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
    return [...groups.entries()].map(([name, models]) => ({ name, models }))
  }

  function providerById(id: string) {
    return (store.config.ai_providers || []).find(provider => provider.id === id)
  }

  function modelBindingSummary(providerId: unknown, model: unknown): string {
    const provider = providerById(String(providerId || ''))
    if (!provider) return t('modelRoutingUnassigned')
    return `${provider.name || provider.id} · ${String(model || t('modelUnset'))}`
  }

  const catalogRoleLabelKeys: Record<CatalogModelRoleId, MessageKey> = {
    main: 'modelRoleMain',
    embedding: 'modelRoleEmbedding',
    imagegen: 'modelRoleImagegen',
    asr: 'modelRoleAsr',
  }

  function catalogModelAssignmentOptions(provider: ProviderDraft, modelName: string) {
    return CATALOG_MODEL_ROLES.map(role => ({
      label: t(catalogRoleLabelKeys[role.id]),
      value: role.id,
      disabled: !catalogModelRoleEligible(store.config.ai_providers || [], provider.id, modelName, role.id),
    }))
  }

  function catalogModelAssignmentValue(provider: ProviderDraft, modelName: string): CatalogModelRoleId | null {
    return CATALOG_MODEL_ROLES.find(role => (
      isCatalogModelAssigned(store.config as Record<string, unknown>, role.id, provider.id, modelName)
    ))?.id || null
  }

  function catalogModelCanAssign(provider: ProviderDraft, modelName: string): boolean {
    return CATALOG_MODEL_ROLES.some(role => (
      catalogModelRoleEligible(store.config.ai_providers || [], provider.id, modelName, role.id)
    ))
  }

  async function saveModelRouting(): Promise<boolean> {
    if (!providerLibrarySupported.value) {
      toast.error(t('providerBackendOutdated'))
      return false
    }
    if (modelRoutingSaving.value) return false
    modelRoutingSaving.value = true
    try {
      const warnings = await store.saveSection([...MODEL_ROUTING_CONFIG_KEYS])
      await refreshModelRuntimes()
      toast.success(t('modelRoutingSaved'))
      warnings.forEach(warning => toast.warning(warning))
      return true
    } catch (error: unknown) {
      toast.error(errorMessage(error))
      return false
    } finally {
      modelRoutingSaving.value = false
    }
  }

  async function setModelRoutingBool(key: keyof AppConfig, value: boolean): Promise<boolean> {
    if (modelRoutingSaving.value) return false
    const config = store.config as Record<string, unknown>
    const previous = { existed: Object.prototype.hasOwnProperty.call(config, key), value: config[key] }
    store.setConfigField(key, value)
    const saved = await saveModelRouting()
    if (!saved) {
      if (previous.existed) store.setConfigField(key, previous.value)
      else delete config[key]
    }
    return saved
  }

  async function assignCatalogModelRole(
    provider: ProviderDraft,
    modelName: string,
    roleId: CatalogModelRoleId,
  ): Promise<boolean> {
    if (!providerLibrarySupported.value || modelRoutingSaving.value || catalogAssignmentBusy.value) return false
    if (isCatalogModelAssigned(store.config as Record<string, unknown>, roleId, provider.id, modelName)) return true
    if (!catalogModelRoleEligible(store.config.ai_providers || [], provider.id, modelName, roleId)) return false
    catalogAssignmentBusy.value = `${provider.id}:${modelName}`
    try {
      return await assignCatalogModelRoleWithRollback(
        store.config as Record<string, unknown>,
        roleId,
        provider.id,
        modelName,
        saveModelRouting,
      )
    } finally {
      catalogAssignmentBusy.value = ''
    }
  }

  return {
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
    providerCatalogProviderId,
    providerCatalogModels,
    modelRoutingSaving,
    catalogAssignmentBusy,
    providerLibrarySupported,
    readyProviderIds,
    activeProvider,
    activeCatalogProvider,
    activeProviderModelGroups,
    providerCatalogSourceModels,
    syncProviderDrafts,
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
    providerHasKey,
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
  }
}
