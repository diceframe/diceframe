export type JsonObject = Record<string, unknown>

export interface CharacterResource { current?: number; max?: number; min?: number }

export interface CharacterIdentity { [key: string]: string | number | undefined }

export interface CharacterSkill { name: string; value?: number }

export interface CharacterItem { name?: string; type?: string; damage?: number; slot?: string; quality?: string; qty?: number; effect?: string; category?: string; note?: string; [key: string]: unknown }

export interface CharacterSheet {
  character_name?: string
  race?: string
  class?: string
  level?: number
  xp?: number
  level_up_points?: number
  background?: string
  identity?: CharacterIdentity
  resources?: Record<string, CharacterResource>
  currency?: { amount?: number }
  hp?: number
  max_hp?: number
  gold?: number
  attributes?: Record<string, number>
  skills?: Array<string | CharacterSkill>
  equipment?: CharacterItem[]
  inventory?: CharacterItem[]
  key_items?: CharacterItem[]
  [key: string]: unknown
}

export interface CharacterCard extends CharacterSheet {
  id?: string
  card_id?: string
  source?: string
  character_name: string
  race?: string
  class?: string
}

export interface Player {
  user_id: string
  character_name: string
  character_sheet?: CharacterSheet
  [key: string]: unknown
}

export interface PublicAction {
  user_id: string
  character_name?: string
  text: string
  revision_count?: number
  timestamp?: string
  dice_pending?: boolean
  dice_system?: string
  dice_roll_source?: string
}

export interface Multiplayer {
  state?: string
  ready_count?: number
  active_count?: number
  away_count?: number
  player_count?: number
  max_players?: number
  ready_players?: Player[]
  waiting_players?: Player[]
  away_players?: Player[]
  submitted_actions?: PublicAction[]
}

export interface PendingPayment {
  id?: string
  payment_id?: string
  uid?: string
  amount?: number
  round?: number
  item?: string
  description?: string
  reason?: string
  status?: string
  [key: string]: unknown
}

export interface GameDetail {
  game_key: string
  world_name?: string
  world_id?: string
  gm_uid?: string
  scene?: string
  round_number?: number
  state?: string
  language?: string
  solo_mode?: boolean
  player_access_open?: boolean
  has_room_password?: boolean
  multiplayer?: Multiplayer
  quick_actions?: string[]
  pending_payments?: PendingPayment[]
  total_tokens?: number
  [key: string]: unknown
}

export interface LogEntry {
  round?: number
  gm_response?: string
  player_actions?: unknown
  actions?: unknown
  swipes?: unknown[]
  current_swipe?: number
  tags_summary?: LogTagsSummary
  [key: string]: unknown
}

export interface PrivateMessage {
  text?: string
  user_id?: string
  round?: number
  character_name?: string
  [key: string]: unknown
}

export interface MapLocation {
  id?: string
  name: string
  connected_to?: string[]
  [key: string]: unknown
}

export interface MapAsset {
  id: string
  name?: string
  description?: string
  plugin_id?: string
  plugin_name?: string
  path?: string
  url?: string
}

export interface MapData {
  locations: MapLocation[]
  assets?: {
    icons?: MapAsset[]
    scenes?: MapAsset[]
    grids?: MapAsset[]
  }
  [key: string]: unknown
}

export interface LoreEntry {
  id?: string
  world_id?: string
  name: string
  type?: string
  tier?: string
  content?: string
  summary?: string
  description?: string
  keywords?: string[]
  is_constant?: boolean
  unreliable?: boolean
  [key: string]: unknown
}

export interface LorebookResponse {
  entries?: LoreEntry[]
  [key: string]: unknown
}

export interface GameSummary {
  game_key: string
  world_name?: string
  world_id?: string
  scene?: string
  state?: string
  language?: string
  solo_mode?: boolean
  round_number?: number
  player_count?: number
  max_players?: number
  total_llm_calls?: number
  total_tokens?: number
  seed_code?: string
  [key: string]: unknown
}

export interface GamesResponse {
  games?: GameSummary[]
}

export interface GameMutationResponse {
  ok?: boolean
  error?: string
  game_key?: string
  world_name?: string
  seed_code?: string
  language?: string
  [key: string]: unknown
}

export interface BatchDeleteGamesResponse {
  deleted?: string[]
  failed?: Array<{ game_key?: string; error?: string }>
  [key: string]: unknown
}

export interface GeneratedCharacterResponse {
  ok?: boolean
  error?: string
  character?: CharacterSheet
}
export interface GeneratedWorldResponse {
  ok?: boolean
  error?: string
  world_id: string
  world_name?: string
  language?: string
  [key: string]: unknown
}

export interface GeneratedRuleResponse {
  ok?: boolean
  error?: string
  rule_id?: string
  rule_name?: string
  description?: string
  source_rule_id?: string
  rule?: RuleTemplate
  [key: string]: unknown
}

export interface PlayerCreateResponse {
  ok?: boolean
  error?: string
  user_id: string
  [key: string]: unknown
}

export interface CharacterImportResponse {
  ok?: boolean
  error?: string
  card?: CharacterCard
}

export interface RuleAttribute {
  key: string
  min: number
  max: number
  name?: string
  name_en?: string
  display_name?: string
  [key: string]: unknown
}

export interface SkillSpec {
  key?: string
  name?: string
  value?: number
  [key: string]: unknown
}

export interface IdentityFieldSpec {
  key: string
  label?: string | { zh?: string; en?: string }
  type?: string
  legacy_field?: keyof CharacterSheet | string
  [key: string]: unknown
}

export interface ResourceSpec {
  key: string
  label?: string | { zh?: string; en?: string }
  max?: number
  [key: string]: unknown
}

export interface SpecialStatSpec {
  key: string
  name?: string
  max?: number
  [key: string]: unknown
}
export interface CharacterListResponse {
  players?: Player[]
  npcs?: CharacterCard[]
  rule_attrs?: RuleAttribute[]
  rule_attrs_total?: number
  rule_meta?: RuleMeta
  rule_special_stats?: SpecialStatSpec[]
  [key: string]: unknown
}

export interface CharacterCardsResponse {
  cards?: CharacterCard[]
}

export interface LogActionRecord {
  user_id?: string
  text?: string
  action?: string
  [key: string]: unknown
}

export interface LogTagsSummary {
  has_tags?: boolean
  count?: number
  tags?: string[]
  [key: string]: unknown
}

export interface MemoryEntry {
  id?: string
  entity?: string
  title?: string
  type?: string
  relation?: string
  value?: string
  content?: string
  text?: string
  summary?: string
  confidence?: number
  [key: string]: unknown
}

export interface MemoriesResponse {
  memories?: MemoryEntry[]
  entries?: MemoryEntry[]
  total?: number
  [key: string]: unknown
}

export interface LoreGenerateResponse {
  ok?: boolean
  error?: string
  count?: number
  entries?: LoreEntry[]
}

export interface WorldCreateResponse {
  ok?: boolean
  error?: string
  world_id?: string
  id?: string
  [key: string]: unknown
}
export interface GameLogResponse {
  log?: LogEntry[]
  total?: number
  total_pages?: number
  page?: number
}

export interface PrivateLogResponse {
  messages?: PrivateMessage[]
  private_log?: PrivateMessage[]
}

export interface HealthEvent {
  id: string
  title?: string
  message?: string
  code?: string
  component?: string
  severity?: string
  round?: number
  resolved?: boolean
  ignored?: boolean
  [key: string]: unknown
}

export interface HealthResponse {
  events: HealthEvent[]
  [key: string]: unknown
}

export interface PlayerContextResponse {
  preview?: boolean
  [key: string]: unknown
}

export interface RuleMeta {
  rule_id?: string
  rule_name?: string
  dice_system?: string
  attr_hint?: string
  attr_hint_en?: string
  skill_hint?: string
  skill_hint_en?: string
  hp_formula?: string
  mechanics?: string
  currency?: string
  auto_hp?: boolean
  attribute_points?: number
  attributes?: RuleAttribute[]
  max_skills?: number
  skill_mode?: string
  skill_point_total?: number
  max_skill_value?: number
  skill_point_spend_mode?: string
  skill_base_values?: Record<string, number>
  skill_pools?: Record<string, string[]>
  skill_pool?: SkillSpec[]
  skills?: SkillSpec[]
  identity_schema?: IdentityFieldSpec[]
  resource_schema?: ResourceSpec[]
  ui_schema?: { currency_label?: string | { zh?: string; en?: string }; [key: string]: unknown }
  rule_special_stats?: SpecialStatSpec[]
  [key: string]: unknown
}

export interface CommandResponse {
  ok?: boolean
  error?: string
  narration?: string
  quick_actions?: string[]
  forced_waiting?: string[]
  [key: string]: unknown
}

export interface ActionSubmitResponse {
  phase?: 'dice' | string
  message?: string
  narration?: string
  roll?: {
    ok?: boolean
    value?: number
    critical?: boolean
    fumble?: boolean
  }
  [key: string]: unknown
}
export interface BotBindTokenResponse {
  bind_token: string
  [key: string]: unknown
}

export interface WorldTemplateSummary {
  id?: string
  world_id?: string
  name?: string
  world_name?: string
  description?: string
  default_rule?: string
  language?: string
  [key: string]: unknown
}

export interface WorldSummary {
  id?: string
  world_id?: string
  name?: string
  world_name?: string
  description?: string
  entry_count?: number
  language?: string
  [key: string]: unknown
}

export interface WorldTemplatesResponse {
  templates?: WorldTemplateSummary[]
}

export interface WorldListResponse {
  worlds?: WorldSummary[]
}

export interface WorldCandidate {
  id: string
  name: string
  description: string
  source: string
  default_rule: string
  entry_count?: number
}

export interface RuleAttributeEdit {
  key: string
  name: string
  min: number
  max: number
}

export interface RuleSummary {
  rule_id: string
  rule_name?: string
  description?: string
  dice_system?: string
  combat_model?: string
  attr_count?: number
  custom?: boolean
  file?: string
  [key: string]: unknown
}

export interface RulesResponse {
  rules?: RuleSummary[]
  total?: number
}

export interface RuleTemplate extends JsonObject {
  rule_id?: string
  rule_name?: string
  description?: string
  dice_system?: string
  combat_model?: string
  mechanics?: string
  ruleset_level?: string
  attribute_points?: number
  max_skills?: number
  skill_point_total?: number
  currency?: string
  hp_formula?: string
  gm_prompt_appendix?: string
  attributes?: RuleAttributeEdit[]
  skill_pool?: SkillSpec[]
  skills?: SkillSpec[]
  custom?: boolean
  source_rule_id?: string
}

export interface RuleDetailResponse {
  ok?: boolean
  rule?: RuleTemplate
  error?: string
}

export interface RuleForm {
  rule_id: string
  rule_name: string
  description: string
  dice_system: string
  combat_model: string
  mechanics: string
  ruleset_level: string
  attribute_points: number
  max_skills: number
  skill_point_total: number
  currency: string
  hp_formula: string
  gm_prompt_appendix: string
  attributes: RuleAttributeEdit[]
}

export type RuleEditorState =
  | { mode: 'new'; source_rule_id: string; id: string; name: string }
  | { mode: 'copy'; source_rule_id: string; id: string; name: string }
  | { mode: 'edit'; id: string; name: string }
export interface PluginField {
  type: string
  title?: string
  description?: string
  default?: unknown
  enum?: string[]
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  minLength?: number
  maxLength?: number
  ui?: { control?: string; group?: string; sensitive?: boolean; order?: number; generate?: boolean; env?: string }
}

export interface PluginInfo {
  id: string
  name: string
  version?: string
  description?: string
  plugin_type?: string
  support?: { level: 'supported' | 'partial' | 'reserved' | 'unsupported'; summary: string }
  has_entrypoint?: boolean
  enabled: boolean
  running: boolean
  status: string
  schema?: { properties?: Record<string, PluginField> }
  config?: Record<string, unknown>
  error?: string
  capabilities?: string[]
  permissions?: string[]
  permission_details?: Array<{ id: string; description?: string }>
  tools?: PluginToolDescriptor[]
  contributions?: PluginContribution[]
  docs?: string
}

export interface PluginToolDescriptor {
  plugin_id: string
  plugin_name: string
  name: string
  title?: string
  description?: string
  input_schema: JsonObject
}

export interface PluginToolsResponse {
  ok: boolean
  error?: string
  tools: PluginToolDescriptor[]
  total?: number
}

export interface PluginToolInvokeResponse {
  ok: boolean
  error?: string
  plugin_id?: string
  tool_name?: string
  result?: JsonObject
}

export interface PluginContribution {
  plugin_id: string
  plugin_name: string
  plugin_type: string
  kind: string
  key: string
  path: string
  title?: string
  description?: string
}

export interface PluginTheme {
  id: string
  name: string
  description?: string
  plugin_id: string
  plugin_name?: string
  tokens?: {
    base?: Record<string, string>
    dark?: Record<string, string>
    light?: Record<string, string>
  }
}

export interface PluginThemesResponse {
  ok: boolean
  error?: string
  themes: PluginTheme[]
  total?: number
}

export interface PluginContentResource {
  id?: string
  name?: string
  character_name?: string
  description?: string
  plugin_id?: string
  plugin_name?: string
  source?: string
  readonly?: boolean
  [key: string]: unknown
}

export interface PluginContentResponse {
  ok: boolean
  error?: string
  resources: Record<string, PluginContentResource[]>
  total?: number
}

export interface PluginContentImportResponse {
  ok?: boolean
  error?: string
  imported_as?: 'character_card' | 'lorebook_entry' | string
  card?: CharacterCard
  entry?: LoreEntry
  source_plugin_id?: string
  [key: string]: unknown
}

export interface PluginMarketplaceItem {
  id: string
  name: string
  version?: string
  description?: string
  plugin_type?: string
  support?: { level: 'supported' | 'partial' | 'reserved' | 'unsupported'; summary: string }
  repository_url?: string
  archive_url?: string
  release_tag?: string
  release_url?: string
  branch?: string
  distribution?: 'repository' | 'bundled' | string
  risk_level?: 'declarative' | 'unrestricted-process' | 'bundled' | 'unknown' | string
  update_policy?: 'automatic' | 'notify' | 'application' | 'approval-required' | 'blocked' | string
  author?: unknown
  license?: string
  tags?: string[]
  capabilities?: string[]
  permissions?: string[]
  docs?: string
  homepage?: string
  installed?: boolean
  installed_version?: string
  installed_commit_sha?: string
  installed_update_policy?: string
  trust_level?: 'official' | 'verified' | 'community' | string
  commit_sha?: string
  approved_permissions?: string[]
  permission_changes?: string[]
  verified?: boolean
  installable?: boolean
  verification_error?: string
  manifest?: Record<string, unknown>
}

export interface PluginMarketplaceResponse {
  ok: boolean
  error?: string
  plugins: PluginMarketplaceItem[]
  total?: number
  source?: {
    mirror_name?: string
    mirror_id?: string
    mirror_index?: number
    total_mirrors?: number
    elapsed_ms?: number
    url?: string
    error?: string
  }
}

export interface PluginMirror {
  id: string
  name: string
  raw_prefix: string
  clone_prefix: string
  enabled: boolean
  priority: number
}

export interface PluginMirrorsResponse {
  ok: boolean
  error?: string
  mirrors: PluginMirror[]
}

export interface PluginMirrorTestResult {
  ok: boolean
  error?: string
  mirror_id?: string
  mirror_name?: string
  mirror_index?: number
  total_mirrors?: number
  elapsed_ms?: number
  status?: number
  url?: string
}

export interface PluginMirrorTestResponse {
  ok: boolean
  error?: string
  results: PluginMirrorTestResult[]
}

export interface SecretField {
  configured: boolean
  masked: string
}

export interface BotTokenResponse {
  ok: boolean
  token: string
  masked: string
  regenerated: boolean
}

export interface AppConfig {
  base_url?: string
  model?: string
  api_format?: string
  api_key?: SecretField
  fallback1_enabled?: boolean
  fallback1_base_url?: string
  fallback1_api_key?: SecretField
  fallback1_model?: string
  fallback1_api_format?: string
  fallback2_enabled?: boolean
  fallback2_base_url?: string
  fallback2_api_key?: SecretField
  fallback2_model?: string
  fallback2_api_format?: string
  embedding_enabled?: boolean
  embedding_base_url?: string
  embedding_api_key?: SecretField
  embedding_model?: string
  embedding_max_input?: number
  narrative_max_tokens?: number
  character_gen_max_tokens?: number
  summary_max_tokens?: number
  brief_max_tokens?: number
  analysis_max_tokens?: number
  text_gen_max_tokens?: number
  proxy_enabled?: boolean
  proxy_url?: string
  proxy_source?: string
  proxy_supported?: boolean
  public_base_url?: string
  access_password?: SecretField
  bot_token?: SecretField
  bot_token_source?: 'env' | 'generated'
  [key: string]: unknown
}

export interface TestResult {
  ok: boolean
  error?: string
  response?: string
  elapsed?: number
  tokens?: number
  dimension?: number
  status?: number
}

export interface UpdateAsset {
  name: string
  download_url: string
  size?: number
}
export interface UpdateRelease {
  version:string
  tag_name?:string
  name?:string
  body?:string
  html_url?:string
  published_at?:string
  prerelease?:boolean
  assets?:UpdateAsset[]
}
export interface UpdateCheckResponse {
  ok:boolean
  error?:string
  message?:string
  current_version:string
  repository?:string
  update_available:boolean
  no_release?:boolean
  latest?:UpdateRelease
  release_url?:string
  releases_url?:string
  source_url?:string
  install_hint?:Record<string,string>
}
