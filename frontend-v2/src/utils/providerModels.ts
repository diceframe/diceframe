export type ModelCapability = 'chat' | 'image' | 'embedding' | 'tts' | 'asr'
export type ProviderTestMode = 'auto' | 'model' | 'embedding'
export type ProviderTestKind = Exclude<ProviderTestMode, 'auto'>

/** Settings provider-library editor draft. Kept beside the model role contract so
 * extracted provider components do not depend on the SettingsView page shell. */
export interface ProviderDraft {
  id: string
  name: string
  base_url: string
  api_format: string
  models: string[]
  model_capabilities: Record<string, ModelCapability>
  configuredMasked: string
}

export function modelCapability(model: string, override?: string): ModelCapability {
  if (override && ['chat', 'image', 'embedding', 'tts', 'asr'].includes(override)) {
    return override as ModelCapability
  }
  const value = model.toLowerCase()
  if (/(image|dall-e|flux|stable[-_. ]?diffusion|(^|[-_.])sd3|kolors|qwen[-_. ]?image|ideogram|imagen)/.test(value)) {
    return 'image'
  }
  if (/(embed|embedding|bge|e5-|text2vec|rerank)/.test(value)) return 'embedding'
  if (/(whisper|sensevoice|paraformer|funasr|speech[-_. ]?to[-_. ]?text|transcri|(^|[-_.])asr)/.test(value)) return 'asr'
  if (/(tts|cosyvoice|fish[-_. ]?speech|gpt[-_. ]?sovits|chattts|voice)/.test(value)) return 'tts'
  return 'chat'
}

export function providerTestKind(
  model: string,
  mode: ProviderTestMode = 'auto',
  capabilityOverride?: string,
): ProviderTestKind | null {
  if (mode !== 'auto') return mode
  const value = model.trim().toLowerCase()
  if (!value || /rerank/.test(value)) return null
  const capability = modelCapability(value, capabilityOverride)
  if (capability === 'embedding') return 'embedding'
  if (capability === 'chat') return 'model'
  return null
}

export interface CatalogSavedProvider {
  id: string
  api_format?: string
  models?: string[]
  model_capabilities?: Record<string, ModelCapability>
}

export type CatalogModelRoleId = 'main' | 'check' | 'embedding' | 'imagegen' | 'asr'

export interface CatalogModelRole {
  id: CatalogModelRoleId
  capability: ModelCapability
  providerKey: string
  modelKey: string
  apiFormats?: readonly string[]
  activation?: Readonly<Record<string, unknown>>
  /** 分配时恢复默认的字段：它们描述的是被替换掉的那个模型，例如推理强度。 */
  reset?: readonly string[]
}

/** 模型目录与“模型配置”页共用的用途注册表。
 * 新增模型用途时在这里声明能力、绑定字段和必要的启用字段，目录行无需再加
 * 一套专用按钮或保存逻辑。 */
export const CATALOG_MODEL_ROLES: readonly CatalogModelRole[] = [
  { id: 'main', capability: 'chat', providerKey: 'llm_provider_ref', modelKey: 'model', reset: ['model_reasoning_effort'] },
  {
    id: 'check',
    capability: 'chat',
    providerKey: 'check_provider_ref',
    modelKey: 'check_model',
    activation: { check_model_enabled: true },
    reset: ['check_model_reasoning_effort'],
  },
  {
    id: 'embedding',
    capability: 'embedding',
    providerKey: 'embedding_provider_ref',
    modelKey: 'embedding_model',
    activation: { embedding_enabled: true },
  },
  {
    id: 'imagegen',
    capability: 'image',
    providerKey: 'imagegen_provider_ref',
    modelKey: 'imagegen_model',
    apiFormats: ['openai'],
    activation: { imagegen_enabled: true },
  },
  {
    id: 'asr',
    capability: 'asr',
    providerKey: 'asr_provider_ref',
    modelKey: 'asr_model',
    activation: { asr_provider: 'openai-compatible' },
  },
]

export function catalogModelRole(roleId: CatalogModelRoleId): CatalogModelRole | undefined {
  return CATALOG_MODEL_ROLES.find(role => role.id === roleId)
}

/** 目录行的分配资格只认**已持久化**的 provider library。
 * UI 草稿（新建 provider / 未保存的新模型 / 刚改未保存的能力）不算数，
 * 否则 routing 会指向后端根本不存在的 provider/model 组合。 */
export function catalogModelRoleEligible(
  savedProviders: CatalogSavedProvider[],
  providerId: string,
  modelName: string,
  roleId: CatalogModelRoleId,
): boolean {
  const saved = savedProviders.find(provider => provider.id === providerId)
  const role = catalogModelRole(roleId)
  if (!role) return false
  if (!saved) return false
  if (!(saved.models || []).includes(modelName)) return false
  if (role.apiFormats && !role.apiFormats.includes(String(saved.api_format || ''))) return false
  return modelCapability(modelName, saved.model_capabilities?.[modelName]) === role.capability
}

export function isCatalogModelAssigned(
  config: Record<string, unknown>,
  roleId: CatalogModelRoleId,
  providerId: string,
  modelName: string,
): boolean {
  const role = catalogModelRole(roleId)
  return Boolean(
    role
    && config[role.providerKey] === providerId
    && config[role.modelKey] === modelName,
  )
}

/** 把目录模型分配给目标用途并持久化；保存失败或抛错时完整恢复所有受影响字段。 */
export async function assignCatalogModelRoleWithRollback(
  config: Record<string, unknown>,
  roleId: CatalogModelRoleId,
  providerId: string,
  modelName: string,
  save: () => Promise<boolean>,
): Promise<boolean> {
  const role = catalogModelRole(roleId)
  if (!role) return false

  const patch: Record<string, unknown> = {
    [role.providerKey]: providerId,
    [role.modelKey]: modelName,
    ...(role.activation || {}),
    ...Object.fromEntries((role.reset || []).map(key => [key, ''])),
  }
  const previous = new Map<string, { existed: boolean; value: unknown }>()
  for (const [key, value] of Object.entries(patch)) {
    previous.set(key, { existed: Object.prototype.hasOwnProperty.call(config, key), value: config[key] })
    config[key] = value
  }

  const rollback = () => {
    for (const [key, state] of previous) {
      if (state.existed) config[key] = state.value
      else delete config[key]
    }
  }

  try {
    if (await save()) return true
  } catch (error) {
    rollback()
    throw error
  }
  rollback()
  return false
}
