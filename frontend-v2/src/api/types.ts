export type JsonObject = Record<string, unknown>

export interface CharacterResource { current?: number; max?: number; min?: number }

export interface CharacterIdentity { [key: string]: string | number | undefined }

export interface CharacterSkill { name: string; value?: number }

export interface CharacterItem { name?: string; type?: string; damage?: number; slot?: string; quality?: string; qty?: number; effect?: string; category?: string; note?: string; [key: string]: unknown }

export interface CharacterPortrait {
  kind: 'builtin' | 'upload' | 'plugin'
  id?: string
  asset_id?: string
  plugin_id?: string
  path?: string
}

export interface SceneImageRef {
  kind: 'builtin' | 'upload' | 'plugin' | 'asset'
  id?: string
  asset_id?: string
  plugin_id?: string
  path?: string
}

export interface MapBackgroundSelection {
  kind: 'auto' | 'none' | 'builtin' | 'upload' | 'plugin'
  id?: string
  asset_id?: string
  map_id?: string
}

export interface MapBackgroundOption {
  id: string
  kind: MapBackgroundSelection['kind']
  name: string
  description?: string
  plugin_name?: string
  url?: string
  selection?: MapBackgroundSelection
}

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
  portrait?: CharacterPortrait | null
  [key: string]: unknown
}

export interface CharacterCard extends CharacterSheet {
  id?: string
  card_id?: string
  source?: string
  schema_version?: number
  rule_id?: string
  rule_name?: string
  rule_version?: string
  mechanics?: string
  language?: string
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

export interface CheckRequest {
  check_id?: string
  required?: boolean
  actor_uid?: string
  actor_name?: string
  dice_system: 'd20' | 'd100' | string
  label?: string
  intent?: string
  skill?: string
  attribute?: string
  advantage_mode?: string
  advantage_note?: string | null
  target?: number
  circumstance_modifier?: number
  kind?: 'check' | 'save' | 'attack' | string
  opponent?: string
  opponent_name?: string
  opponent_roll?: number
  opponent_modifier?: number
  opponent_total?: number
  assist?: string[]
  planner_source?: string
}

export interface CheckResult {
  check_id?: string
  label?: string
  actor_uid?: string
  actor_name?: string
  dice?: string
  attribute?: string | null
  skill?: string
  roll?: number
  rolls?: number[]
  modifier?: number
  modifier_breakdown?: string | null
  total?: number
  dc?: number
  threshold?: number
  hard_threshold?: number
  extreme_threshold?: number
  verdict?: string
  luck_spend_available?: boolean
  luck_cost?: number | null
  luck_decision?: 'pending' | 'spent' | 'declined' | string
  luck_spent?: number
  luck_remaining?: number
  original_verdict?: string
  luck_resolved_at?: string
  is_critical?: boolean
  is_fumble?: boolean
  advantage_mode?: string
  advantage_note?: string | null
  kind?: 'check' | 'save' | 'attack' | string
  opponent?: string
  opponent_name?: string
  opponent_roll?: number
  opponent_modifier?: number
  opponent_total?: number
  assist?: string[]
  planner_source?: string
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
  check_request?: CheckRequest
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
  recipient_uid?: string
  rewards?: Array<{ name:string; category?:string }>
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
  rule_id?: string
  scene_image?: SceneImageRef
  map_background?: MapBackgroundSelection
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
  pending_luck_decisions?: CheckResult[]
  round_check_results?: CheckResult[]
  total_tokens?: number
  token_budget_bump?: TokenBudgetBump | null
  [key: string]: unknown
}

export interface TokenBudgetBump {
  kind: 'narrative' | string
  from: number
  to: number
}

export interface StoryRecap {
  id?: string
  text: string
  from_round?: number
  to_round?: number
  created_at?: string
}

export interface LogEntry {
  round?: number
  gm_response?: string
  player_actions?: unknown
  actions?: unknown
  swipes?: unknown[]
  current_swipe?: number
  tags_summary?: LogTagsSummary
  check_results?: CheckResult[]
  story_recaps?: StoryRecap[]
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
  content?: string
  keywords?: string[]
  source?: 'lorebook' | 'plugin' | string
  plugin_id?: string
  plugin_name?: string
  x?: number
  y?: number
  icon_url?: string
  image_url?: string
  [key: string]: unknown
}

export interface MapAsset {
  id: string
  ref?: string
  name?: string
  description?: string
  plugin_id?: string
  plugin_name?: string
  path?: string
  url?: string
}

export interface MapDefinition {
  id: string
  source_id?: string
  name: string
  description?: string
  mode: 'graph' | string
  plugin_id?: string
  plugin_name?: string
  background?: MapAsset | null
  default_view?: { x?: number; y?: number; zoom?: number }
}

export interface MapData {
  schema_version?: number
  map_mode?: 'graph' | string
  locations: MapLocation[]
  current_scene?: string
  current_location_id?: string
  active_map?: MapDefinition | null
  background_selection?: MapBackgroundSelection
  background_options?: MapBackgroundOption[]
  assets?: {
    icons?: MapAsset[]
    scenes?: MapAsset[]
  }
  capabilities?: {
    can_expand?: boolean
    can_edit?: boolean
    has_background?: boolean
    has_plugin_assets?: boolean
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
  rule_id?: string
  scene_image?: SceneImageRef
  map_background?: MapBackgroundSelection
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
  world_id?: string
  world_name?: string
  narration?: string
  players?: Player[]
  round_number?: number
  state?: string
  seed_code?: string
  language?: string
  generated_password?: string
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
  imported_as?: 'character_card' | 'npc'
  npc_name?: string
  world_id?: string
  lorebook_entries?: number
  nsfw_warning?: boolean
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
  rule_version?: string
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
  check_request?: CheckRequest
  check_result?: CheckResult
  check_results?: CheckResult[]
  pending_luck_decisions?: CheckResult[]
  advanced?: boolean
  roll?: {
    ok?: boolean
    dice_system?: string
    value?: number
    rolls?: number[]
    critical?: boolean
    fumble?: boolean
  }
  [key: string]: unknown
}

export interface LuckDecisionResponse extends ActionSubmitResponse {
  ok?: boolean
  error?: string
  ready_to_resolve?: boolean
  already_resolved?: boolean
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
  scene_image?: SceneImageRef
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
  scene_image?: SceneImageRef
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
  scene_image?: SceneImageRef
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
  rule_name_en?: string
  description?: string
  dice_system?: string
  combat_model?: string
  attr_count?: number
  custom?: boolean
  file?: string
  source_rule_id?: string
  scene_image?: SceneImageRef
  [key: string]: unknown
}

export interface RulesResponse {
  rules?: RuleSummary[]
  total?: number
}

export interface CharacterSchemaResponse {
  ok?: boolean
  error?: string
  rule_attrs?: RuleAttribute[]
  rule_attrs_total?: number
  rule_classes?: string[]
  rule_special_stats?: SpecialStatSpec[]
  rule_meta?: RuleMeta
  skill_pool?: Array<string | SkillSpec>
}

export interface RuleTemplate extends JsonObject {
  rule_id?: string
  rule_name?: string
  source_rule_id?: string
  scene_image?: SceneImageRef
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
  min_app_version?: string
  needs_core_update?: boolean
  tool_ui?: string
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
  tool_ui?: string
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
  schema_version: 2
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
  stars?: number
  distribution?: 'repository' | 'bundled' | string
  risk_level?: 'declarative' | 'unrestricted-process' | 'bundled' | 'unknown' | string
  update_policy?: 'automatic' | 'notify' | 'application' | 'approval-required' | 'blocked' | string
  author?: unknown
  license?: string
  tags?: string[]
  capabilities?: string[]
  permissions?: string[]
  tool_ui?: string
  min_app_version?: string
  needs_core_update?: boolean
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
  latest?: {
    version?: string
    release_tag?: string
    release_url?: string
    commit_sha?: string
    published_at?: string
    requires_approval?: boolean
  }
  manifest?: Record<string, unknown>
  stats?: {
    downloads_total?: number
    downloads_30d?: number
    installs_total?: number
    likes?: number
    rating_count?: number
    rating_average?: number
    rating_score?: number
  }
  security?: {
    install_allowed?: boolean
    blocking_reasons?: string[]
    warning_reasons?: string[]
    [key: string]: unknown
  }
  readme?: {
    available?: boolean
    status?: string
    content_hash?: string | null
    synced_at?: string | null
  }
  liked?: boolean
  own_rating?: { stars: number; tags: string[] } | null
  // Hub 详情把"当前安装实例是否已点赞/已评分"放在 viewer 里（installation 维度）
  viewer?: {
    liked?: boolean
    rating?: { stars: number; tags: string[] } | null
  }
  generated_at?: string
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
    hub?: boolean
    stale?: boolean
  }
}

export interface HubPreferences {
  ok: boolean
  available: boolean
  telemetry_enabled: boolean
  choice_made: boolean
  identity_created: boolean
  legal_version: string
  legal_accepted: boolean
  legal_documents: Record<'terms' | 'privacy', {
    version: string
    updated_at: string
    language: 'zh' | 'en'
    sha256: string
  }>
}

export interface HubPluginReadmeResponse {
  ok: boolean
  plugin_id: string
  html: string
  markdown?: string
  content_hash?: string
  synced_at?: string
  source?: {
    hub?: boolean
    github?: boolean
    cached?: boolean
    stale?: boolean
    error?: string
  }
}

export interface HubRatingSummary {
  ok: boolean
  count: number
  average: number
  bayesian_score: number
  distribution: Record<'1' | '2' | '3' | '4' | '5', number>
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
  update_channel?: 'stable' | 'preview'
  tts_provider?: 'browser' | 'openai-compatible' | 'gpt-sovits'
  tts_base_url?: string
  tts_api_key?: SecretField
  tts_model?: string
  tts_audio_format?: 'mp3' | 'opus' | 'aac' | 'flac' | 'wav' | 'pcm'
  tts_default_voice?: string
  tts_gm_voice?: string
  tts_player_voice?: string
  tts_timeout_seconds?: number
  tts_cache_mb?: number
  [key: string]: unknown
}

export interface TtsVoiceProfile {
  id: string
  name: string
  engine: 'openai-compatible' | 'gpt-sovits' | string
  voice_id?: string
  language?: string
  description?: string
  plugin_id?: string
  plugin_name?: string
  preview_url?: string
  license?: string
  source?: 'personal' | 'plugin' | 'provider' | string
}

export interface TtsPersonalVoiceProfile {
  id: string
  name: string
  engine: 'openai-compatible' | 'gpt-sovits'
  voice_id?: string
  language?: string
  description?: string
  prompt_text?: string
  prompt_language?: string
  server_reference_path?: string
  has_reference_audio?: boolean
  source: 'personal'
}

export interface TtsPersonalVoiceProfileInput {
  name: string
  engine: 'openai-compatible' | 'gpt-sovits'
  voice_id?: string
  language?: string
  description?: string
  prompt_text?: string
  prompt_language?: string
  server_reference_path?: string
  file_data?: string
  file_name?: string
}

export interface TtsPersonalVoiceProfilesResponse {
  ok: boolean
  profiles: TtsPersonalVoiceProfile[]
  total: number
}

export interface TtsVoiceCatalog {
  ok: boolean
  provider: 'browser' | 'openai-compatible' | 'gpt-sovits'
  backend_enabled: boolean
  model?: string
  audio_format?: string
  default_voice?: string
  gm_voice?: string
  player_voice?: string
  max_text_chars?: number
  voices: TtsVoiceProfile[]
}

export interface TtsSpeechRequest {
  text: string
  voice?: string
  language?: string
  speed?: number
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

export interface LoginAuditEntry {
  at: string
  ip: string
  success: boolean
}

export interface LoginAuditResponse {
  entries: LoginAuditEntry[]
  max_entries: number
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
  channel?:string
  latest?:UpdateRelease
  release_url?:string
  releases_url?:string
  source_url?:string
  install_hint?:Record<string,string>
}
export interface UpdateSelfUpdateInfo {
  supported:boolean
  mode?:'portable' | 'source' | 'development' | 'docker'
  reason:string
  hint:string
}
export interface UpdateStatusResponse {
  state:'idle' | 'downloading' | 'verifying' | 'staged' | 'applying' | 'restarting' | 'done' | 'rolled-back' | 'failed'
  version?:string
  kind?:'source' | 'portable'
  asset?:string
  downloaded_bytes?:number
  total_bytes?:number
  mirror_used?:string
  error?:string
  path?:string
  sha256?:string
  downloaded_at?:number
  restart_needed?:boolean
  candidate_dir?:string
  backup_dir?:string
  current_version:string
  self_update:UpdateSelfUpdateInfo
}
export interface UpdateDownloadResponse {
  ok:boolean
  error?:string
  state?:string
  version?:string
  asset?:string
  no_release?:boolean
}

export interface TunnelProvider {
  plugin_id: string
  name: string
  running: boolean
  min_app_version?: string
  needs_core_update?: boolean
}

export interface TunnelStatus {
  ok: boolean
  active: boolean
  url?: string
  published_at?: number
  public_base_url?: string
  providers: TunnelProvider[]
  error?: string
}
export interface UpdateApplyResponse {
  ok:boolean
  error?:string
  state?:string
  version?:string
}
export interface ApplicationRestartResponse {
  ok:boolean
  error?:string
  restarting?:boolean
  boot_id:string
}
export interface ApplicationHealthResponse {
  ok:boolean
  version:string
  pid:number
  boot_id:string
}
