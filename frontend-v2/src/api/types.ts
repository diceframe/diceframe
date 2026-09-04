export type JsonObject = Record<string, unknown>

export interface CharacterResource { current?: number; max?: number; min?: number }

export interface CharacterIdentity { [key: string]: string | number | undefined }

export interface CharacterSkill { name: string; value?: number }

export interface CharacterItem { name?: string; type?: string; damage?: number; slot?: string; quality?: string; qty?: number; effect?: string; category?: string; note?: string; [key: string]: unknown }

export type GeneratedImagePurpose = 'scene' | 'avatar' | 'item' | 'map' | 'freeform'

export interface GeneratedImageRecord {
  generation_id: string
  asset_id: string
  purpose: GeneratedImagePurpose
  prompt?: string
  revised_prompt?: string
  provider?: string
  model?: string
  created_at?: string
  context?: Record<string, unknown>
  round?: number
}

export interface CharacterPortrait {
  // Legacy saves can contain an empty object; treat it as no portrait.
  kind?: 'builtin' | 'upload' | 'plugin' | 'generated'
  id?: string
  asset_id?: string
  plugin_id?: string
  path?: string
}

export interface SceneImageRef {
  kind: 'builtin' | 'upload' | 'plugin' | 'asset' | 'generated'
  id?: string
  asset_id?: string
  plugin_id?: string
  path?: string
}

export interface MapBackgroundSelection {
  kind: 'auto' | 'none' | 'builtin' | 'upload' | 'plugin' | 'generated'
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
  active_locale?: string
  character_name: string
  race?: string
  class?: string
  ruleset_runtime?: RulesetRuntimeMeta
  ruleset_revision?: number
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

export interface RestSessionParticipant {
  user_id: string
  character_name: string
  status: 'waiting' | 'submitted' | string
}

export interface RestSessionStatus {
  active: boolean
  status: 'idle' | 'collecting' | 'resolving' | 'completed' | 'error' | string
  rest: 'short' | 'long' | null | string
  ready_count: number
  active_count: number
  participants: RestSessionParticipant[]
  resolved_at?: string
  error?: string
}

export interface LiveAdvancementPlayerStatus {
  user_id: string
  character_name: string
  level: number
  xp: number
  next_level_xp: number
  entitled: boolean
  target_level: number
  source: 'ai_gm' | 'gm' | string
}

export interface LiveAdvancementStatus {
  mode: 'milestone' | 'xp'
  authority: 'ai_gm' | 'gm'
  players: LiveAdvancementPlayerStatus[]
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
  kind?: 'payment' | 'purchase' | 'fee' | 'reward' | 'transfer' | string
  payer_uid?: string
  approval_policy?: string
  contributors?: Array<{ uid: string; amount: number }>
  approvals?: Record<string, boolean>
  sequence?: number
  run_id?: string
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
  narrative_perspective?: 'auto' | 'immersive' | 'third_person' | string
  advancement?: LiveAdvancementStatus
  rest_session?: RestSessionStatus
  player_access_open?: boolean
  has_room_password?: boolean
  multiplayer?: Multiplayer
  quick_actions?: string[]
  economy_proposals?: PendingPayment[]
  run_id?: string
  pending_luck_decisions?: CheckResult[]
  round_check_results?: CheckResult[]
  total_tokens?: number
  token_budget_bump?: TokenBudgetBump | null
  ruleset_runtime?: RulesetRuntimeMeta & {
    content_version?: string
    state_schema_version?: number
  }
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

export interface RoundSceneImage {
  reference?: SceneImageRef
  prompt?: string
  revised_prompt?: string
  status?: 'ready' | 'failed' | string
  swipe_index?: number
}

export type SceneGalleryItem = GeneratedImageRecord

export interface LogEntry {
  round?: number
  gm_response?: string
  state_changes?: string[]
  player_actions?: unknown
  actions?: unknown
  swipes?: unknown[]
  current_swipe?: number
  tags_summary?: LogTagsSummary
  check_results?: CheckResult[]
  story_recaps?: StoryRecap[]
  scene_image?: RoundSceneImage
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

export interface LoreProjection {
  visible: boolean
  audience: 'public' | 'character' | 'gm'
  subjects: string[]
}

export interface LorePreviewViewer {
  kind: 'gm' | 'party' | 'character'
  uid?: string
  name?: string
}

export interface LorePreviewSummary {
  total: number
  visible: number
  public: number
  character_only: number
  gm_secret: number
}

export interface LorePreviewResponse {
  ok?: boolean
  world_id?: string
  viewer?: LorePreviewViewer
  projections?: Record<string, LoreProjection>
  summary?: LorePreviewSummary
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
  narrative_perspective?: 'auto' | 'immersive' | 'third_person' | string
  gm_uid?: string
  round_number?: number
  player_count?: number
  max_players?: number
  total_llm_calls?: number
  total_tokens?: number
  started_at?: string
  last_activity?: string
  seed_code?: string
  [key: string]: unknown
}

export interface GamesResponse {
  games?: GameSummary[]
}

export interface GameMutationResponse {
  ok?: boolean
  error?: string
  error_code?: string
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
  adventure_binding?: Record<string, unknown>
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
  ruleset_runtime?: RulesetRuntimeMeta
  advancement?: LiveAdvancementStatus
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

export interface RulesetRuntimeCapabilities {
  experience_profile: string
  character_builder: 'legacy' | 'guided' | 'professional'
  character_lifecycle: 'legacy' | 'rules_aware'
  authoritative_intents: boolean
  deterministic_combat: boolean
  versioned_state: boolean
  session_zero: boolean
  tutorial_coach: boolean
  narrative_turns: boolean
  adventure_formats?: string[]
}

export interface RulesetRuntimeMeta {
  id: string
  version: number
  requested_minimum_version: number
  capabilities: RulesetRuntimeCapabilities
}

export type RulesetBuilderMode = 'quick' | 'guided' | 'expert'

export interface RulesetExperience {
  profile: string
  builder_mode: RulesetRuntimeCapabilities['character_builder']
  modes: RulesetBuilderMode[]
  content_version: string
  locale: string
}

export interface RulesetExperienceResponse {
  ok: boolean
  rule_id: string
  ruleset_runtime: RulesetRuntimeMeta
  experience: RulesetExperience
}

export interface RulesetChoice {
  ref: string
  id: string
  name: string
  summary: string
  automation_level: 'deterministic' | 'guided' | 'reference'
  source_ref: string
  recommendation_reason?: string
  difficulty?: 'beginner' | 'intermediate' | 'advanced' | string
  fantasy_tags?: string[]
  items?: Array<{ ref: string; name: string; quantity: number }>
}

export interface RulesetQuickCharacterPreset extends RulesetChoice {
  draft: JsonObject
  difficulty: 'beginner' | 'intermediate' | 'advanced' | string
  fantasy_tags: string[]
}

export interface RulesetSpeciesChoiceSpec {
  id: string
  count: number
  option_ids?: string[]
  option_refs?: string[]
}

export interface RulesetFeatChoiceOption {
  value: string
  name: string
  source_ref: string
}

export interface RulesetFeatChoiceSpec {
  id: string
  name: string
  count: number
  options: RulesetFeatChoiceOption[]
}

export interface RulesetFeatChoice {
  feat_ref: string
  name: string
  summary: string
  automation_level: 'deterministic' | 'guided' | 'reference'
  source_ref: string
  specs: RulesetFeatChoiceSpec[]
}

export interface RulesetAbilityMethodChoice {
  id: string
  values?: number[]
}

export interface RulesetSpellChoice {
  ref: string
  id: string
  name: string
  level: number
  school: string
  class_refs: string[]
  casting_time: string
  range: string
  components: string[]
  ritual: boolean
  concentration: boolean
  duration: string
  source_ref: string
}

export interface RulesetClassSpellRequirements {
  class_ref: string
  level: number
  cantrip_count: number
  prepared_spell_count: number
  spellbook_minimum: number
  maximum_spell_level: number
  slot_profile: string
  spell_slots: Record<string, number>
}

export interface RulesetClassSpellChoices {
  requirements: RulesetClassSpellRequirements
  cantrips: RulesetSpellChoice[]
  leveled_spells: RulesetSpellChoice[]
}

export interface RulesetSelectedClassSpells extends JsonObject {
  cantrip_ids?: string[]
  prepared_spell_ids?: string[]
  spellbook_ids?: string[]
  cantrip_refs?: string[]
  prepared_spell_refs?: string[]
  spellbook_refs?: string[]
}

export interface RulesetBuilderChoices {
  ability_methods: RulesetAbilityMethodChoice[]
  classes: RulesetChoice[]
  species: RulesetChoice[]
  backgrounds: RulesetChoice[]
  class_skills: RulesetChoice[]
  class_skill_count: number
  equipment_packages: RulesetChoice[]
  background_equipment_packages: RulesetChoice[]
  background_ability_refs: string[]
  species_sizes: string[]
  species_choices: RulesetSpeciesChoiceSpec[]
  species_skills: RulesetChoice[]
  species_skill_count: number
  species_feats: RulesetChoice[]
  species_feat_count: number
  feat_choices: RulesetFeatChoice[]
  class_tools: RulesetChoice[]
  class_tool_count: number
  recommended_base_abilities: Record<string, number>
  skills: RulesetChoice[]
  languages: RulesetChoice[]
  origin_feats: RulesetChoice[]
  quick_presets: RulesetQuickCharacterPreset[]
  class_spells: RulesetClassSpellChoices | Record<string, never>
  recommended_class_spells: RulesetSelectedClassSpells
  [key: string]: unknown
}

export interface RulesetProgressionRow {
  level: number
  proficiency_bonus: number
  gained_feature_ids: string[]
  tracks: Record<string, number>
  spell_slots: Record<string, number>
  slot_profile: string
  source_ref: string
  content_version: string
}

export interface RulesetProgressionResponse {
  ok: boolean
  rule_id: string
  progression: RulesetProgressionRow[]
}

export interface RulesetAdvancementPreview extends JsonObject {
  ok: boolean
  errors: string[]
  requirements: JsonObject[]
  from_level: number
  to_level: number
  class_ref: string
  source_ref: string
  content_version: string
  diff: JsonObject
  snapshot: JsonObject
}

export interface RulesetAdvancementPreviewResponse {
  ok: boolean
  rule_id: string
  advancement: RulesetAdvancementPreview
  card_id?: string
  revision?: number
}

export interface RulesetAdvancementApplyResponse {
  ok: boolean
  rule_id: string
  character: JsonObject
  card?: JsonObject
  card_id?: string
  revision?: number
  duplicate?: boolean
}

export interface RulesetRestResponse extends JsonObject {
  ok: boolean
  rule_id: string
  rest: 'short' | 'long'
  character: JsonObject
  events: JsonObject[]
  source_ref: string
  requires_elapsed_time_confirmation: boolean
  revision?: number
  duplicate?: boolean
  pending?: boolean
  resolved?: boolean
  rest_session?: RestSessionStatus
  party_results?: Array<{ user_id: string; character_name: string; events: JsonObject[] }>
}

export interface RulesetCombatTarget {
  actor_id: string
  kind: 'player' | 'enemy'
  name: string
  hp: number
  max_hp: number
  position: number
  armor_class?: number
  speed?: number
  conditions?: Record<string, JsonObject>
  concentration?: JsonObject | null
  death_saves?: Record<string, number>
}

export interface RulesetCombatWeapon extends JsonObject {
  id: string
  name?: string
  weapon_ref?: string
  attack_id?: string
  damage: string
  damage_type?: string
  range?: number
  thrown_range?: number
  long_range?: number
}

export interface RulesetCombatSpell extends JsonObject {
  spell_ref: string
  name: string
  level: number
  casting_time: string
  range: number
  mode: string
  available_slot_levels: number[]
}

export interface RulesetPendingDecision extends JsonObject {
  decision_id: string
  kind: string
  options: string[]
  assigned_to: string
}

export interface RulesetCombatAction extends JsonObject {
  type: string
  label: string
  actor_id?: string
  expected_version: number
  weapons?: RulesetCombatWeapon[]
  spells?: RulesetCombatSpell[]
  targets?: RulesetCombatTarget[]
  decisions?: RulesetPendingDecision[]
  movement_remaining?: number
  requires?: string[]
  choice_ids?: string[]
  submitted?: Record<string, string>
}

export interface RulesetEncounterPreset extends JsonObject {
  id: string
  name: string
  description: string
  difficulty: string
  enemies: JsonObject[]
}

export interface RulesetSessionZeroAgreement extends JsonObject {
  tone: string
  difficulty: 'story' | 'standard' | 'challenging' | 'lethal' | string
  content_rating: 'family' | 'teen' | 'mature' | string
  session_length_minutes: number
  pvp_policy: 'disabled' | 'consent' | 'enabled' | string
  safety_tool: string
  lines: string[]
  veils: string[]
  table_rules: string[]
  coach_enabled?: boolean
}

export interface RulesetCampaignProposal extends JsonObject {
  proposal_id: string
  entity_id: string
  kind: 'task' | 'clue' | 'fact' | 'item' | 'relationship' | string
  title: string
  summary: string
  visibility: 'public' | 'gm' | string
  status: 'pending' | 'confirmed' | 'rejected' | string
}

export interface RulesetCampaignEntity extends JsonObject {
  id: string
  kind: string
  title: string
  summary: string
  visibility: 'public' | 'gm' | string
  status?: string
}

export interface RulesetTutorialChoice extends JsonObject {
  id: string
  label: string
  description: string
  next_step_id: string
}

export interface RulesetTutorialStep extends JsonObject {
  id: string
  chapter_id: string
  title: string
  narration: string
  objective: string
  hint: string
  requires: string
  encounter_preset_id: string
  choices: RulesetTutorialChoice[]
}

export interface RulesetEncounterReadiness extends JsonObject {
  ready_player_ids: string[]
  required_player_ids: string[]
  ready_count: number
  required_count: number
  all_ready: boolean
  players: Array<{ player_id: string; name: string; ready: boolean }>
}

export interface RulesetCombatEvent extends JsonObject {
  event_id: string
  batch_id: string
  intent_type: string
  state_version: number
  type: string
  actor_id?: string
  actor_name?: string
  target_id?: string
  target_name?: string
  previous_actor_id?: string
  previous_actor_name?: string
  text?: string
  round?: number
  natural?: number
  modifier?: number
  total?: number
  target?: number
  success?: boolean
  critical?: boolean
  delta?: number
  amount?: number
  hp?: number
  damage_type?: string
  distance?: number
  reason?: string
}

export interface RulesetPartyDecision extends JsonObject {
  status: 'open' | string
  step_id: string
  choices: RulesetTutorialChoice[]
  submitted: Record<string, string>
  submitted_count: number
  total_players: number
}

export interface RulesetCampaignView extends JsonObject {
  automation?: {
    mode: 'auto' | 'assist' | 'manual' | string
    configured_by?: string
  }
  world_binding?: {
    world_id: string
    source?: string
  }
  adventure_binding?: {
    adventure_id: string
    world_id: string
    recommended_world_id?: string
    compatibility: 'not_selected' | 'compatible' | 'review_required' | string
    scene_source: 'world' | 'adventure' | string
  }
  session_zero: {
    status: 'not_started' | 'pending' | 'locked' | string
    revision: number
    agreement?: RulesetSessionZeroAgreement | null
    pending_agreement?: RulesetSessionZeroAgreement | null
    responses: Record<string, { response: string; comment?: string }>
  }
  session_zero_defaults: RulesetSessionZeroAgreement
  proposals: RulesetCampaignProposal[]
  entities: Record<string, RulesetCampaignEntity[]>
  party_decision?: RulesetPartyDecision
  tutorial: {
    status: 'not_started' | 'active' | 'completed' | string
    coach_enabled: boolean
    current_step?: RulesetTutorialStep | null
    requirement_met?: boolean
    adventure: { id: string; name: string; summary: string; estimated_minutes: number; chapter_count: number }
    history: JsonObject[]
    hints_used: Record<string, number>
  }
  chapter_summaries: JsonObject[]
}

export interface RulesetGameplayView {
  state_schema_version: number
  state_version: number
  combat: {
    status: 'none' | 'active' | 'ended' | string
    outcome?: string
    round: number
    turn_index: number
    current_actor_id: string
    initiative: string[]
    position_mode: string
    economy: Record<string, number | boolean | string>
    reactions: Record<string, number>
    pending_decisions: RulesetPendingDecision[]
    actors: RulesetCombatTarget[]
  }
  encounter_presets: RulesetEncounterPreset[]
  encounter_request?: {
    status: 'pending' | string
    source?: string
    round?: number
    encounter_preset_id?: string
    confidence?: number
    ready_player_ids?: string[]
    readiness?: RulesetEncounterReadiness
  } | null
  recent_combat_events?: RulesetCombatEvent[]
  director?: { context?: JsonObject; proposal?: RulesetDirectorProposal }
  campaign?: RulesetCampaignView
}

export interface RulesetDirectorProposal {
  kind?: 'narrative' | 'check' | 'party_decision' | 'combat' | 'adventure_choice' | string
  confidence?: number
  rationale?: string
  action_ids?: string[]
  encounter_preset_id?: string
  requires_gm_confirmation?: boolean
  mode?: 'auto' | 'assist' | 'manual' | string
  [key: string]: unknown
}

export interface RulesetGameplayResponse {
  ok: boolean
  game_key: string
  rule_id: string
  ruleset_runtime: RulesetRuntimeMeta
  gameplay: RulesetGameplayView
  available_actions: RulesetCombatAction[]
  result?: {
    applied: boolean
    duplicate: boolean
    replayed: boolean
    state_version: number
    event_batch: JsonObject
    pending_decision?: RulesetPendingDecision | null
    automatic_event_batches?: JsonObject[]
    resolved_event_batches?: JsonObject[]
  }
}

export interface RulesetBuilderChoicesResponse {
  ok: boolean
  rule_id: string
  choices: RulesetBuilderChoices
}

export interface RulesetBuilderValidationResponse {
  ok: boolean
  rule_id: string
  valid: boolean
  errors: string[]
}

export interface RulesetBuilderCharacterResponse {
  ok: boolean
  rule_id: string
  character: JsonObject
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

export interface KpQuestionResponse {
  ok: boolean
  kind: 'kp_table_talk'
  answer: string
  visibility: 'private' | 'party'
  exchange?: TableTalkExchange | null
  advanced: false
  action_consumed: false
  round_number: number
  provider_used?: string
  total_tokens?: number
}

export interface TableTalkExchange {
  id: string
  actor_uid: string
  actor_name: string
  question: string
  answer: string
  round: number
  created_at: string
  visibility: 'party'
}

export interface TableTalkResponse {
  ok: boolean
  exchanges: TableTalkExchange[]
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

export interface GmStyle {
  tone?: string
  verbosity?: 'brief' | 'normal' | 'detailed'
  custom_instructions?: string
}

export interface WorldTemplateSummary {
  id?: string
  world_id?: string
  name?: string
  world_name?: string
  description?: string
  default_rule?: string
  recommended_rules?: string[]
  scene_image?: SceneImageRef
  language?: string
  active_locale?: string
  lorebook_count?: number
  source?: 'builtin' | 'user' | 'plugin'
  game_scoped?: boolean
  plugin_id?: string
  plugin_name?: string
  gm_style?: GmStyle | null
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
  gm_style?: GmStyle | null
  [key: string]: unknown
}

export interface WorldCloneResponse {
  ok: boolean
  error?: string
  world_id?: string
  name?: string
  language?: string
}

export interface WorldTemplatesResponse {
  templates?: WorldTemplateSummary[]
}

export interface AdventureSummary {
  adventure_id: string
  version: string
  format: string
  world_policy: 'fixed' | 'portable' | 'agnostic'
  recommended_world_id: string
  required_runtime?: { id: string; minimum_version: number }
  name: string
  summary: string
  estimated_minutes: number
  compatibility: 'compatible' | 'incompatible'
  incompatibility_reasons: string[]
  directory_id?: string
  source?: 'builtin' | 'custom'
  custom?: boolean
  editable?: boolean
  in_use?: number
}

export interface AdventuresResponse {
  ok: boolean
  error?: string
  adventures: AdventureSummary[]
}

export interface AdventureDetail {
  adventure_id: string
  directory_id: string
  version: string
  format: string
  content_digest: string
  custom: boolean
  editable: boolean
  bound_games: string[]
  files: Record<string, unknown>
}

export interface AdventureDetailResponse {
  ok: boolean
  adventure: AdventureDetail
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
  ruleset_runtime?: RulesetRuntimeMeta
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
  ruleset_runtime?: RulesetRuntimeMeta
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
  ruleset_runtime?: RulesetRuntimeMeta
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
  ui?: {
    control?: string
    group?: string
    sensitive?: boolean
    order?: number
    generate?: boolean
    env?: string
    options_source?: string
    api_format?: string
    provider_ref_field?: string
    provider_base_url_env?: string
    provider_api_key_env?: string
    provider_api_format_env?: string
  }
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

export interface AiProvider {
  id: string
  name: string
  base_url: string
  api_format: 'openai' | 'anthropic' | string
  models?: string[]
  model_capabilities?: Record<string, 'chat' | 'image' | 'embedding' | 'tts' | 'asr'>
  api_key?: SecretField
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
  ai_providers?: AiProvider[]
  llm_provider_ref?: string
  fallback1_provider_ref?: string
  fallback2_provider_ref?: string
  embedding_provider_ref?: string
  tts_provider_ref?: string
  asr_provider_ref?: string
  imagegen_provider_ref?: string
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
  web_cors_origins?: string
  web_cors_origins_source?: 'env' | 'config' | string
  access_password?: SecretField
  bot_token?: SecretField
  bot_token_source?: 'env' | 'generated'
  update_channel?: 'stable' | 'preview'
  tts_provider?: 'browser' | 'openai-compatible' | 'gpt-sovits' | 'edge-tts'
  tts_base_url?: string
  tts_api_key?: SecretField
  tts_model?: string
  tts_audio_format?: 'mp3' | 'opus' | 'aac' | 'flac' | 'wav' | 'pcm'
  tts_default_voice?: string
  tts_gm_voice?: string
  tts_player_voice?: string
  tts_timeout_seconds?: number
  tts_cache_mb?: number
  asr_provider?: 'disabled' | 'openai-compatible'
  asr_base_url?: string
  asr_api_key?: SecretField
  asr_model?: string
  asr_timeout_seconds?: number
  imagegen_enabled?: boolean
  imagegen_auto_scene?: boolean
  imagegen_provider?: 'openai-compatible'
  imagegen_base_url?: string
  imagegen_api_key?: SecretField
  imagegen_model?: string
  imagegen_square_size?: string
  imagegen_landscape_size?: string
  imagegen_quality?: string
  imagegen_style_prefix?: string
  imagegen_timeout_seconds?: number
  test_timeout_seconds?: number
  model_request_timeout_seconds?: number
  [key: string]: unknown
}

export interface TtsVoiceProfile {
  id: string
  name: string
  engine: 'openai-compatible' | 'gpt-sovits' | 'edge-tts' | string
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
  engine: 'openai-compatible' | 'gpt-sovits' | 'edge-tts'
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
  engine: 'openai-compatible' | 'gpt-sovits' | 'edge-tts'
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
  provider: 'browser' | 'openai-compatible' | 'gpt-sovits' | 'edge-tts'
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
  kind?:'source' | 'portable' | 'docker'
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

export interface RendezvousPeerInvitation {
  peer_id: string
  token: string
}

export interface RendezvousRoomResponse {
  ok: boolean
  protocol_version: 2
  topology: 'host-star'
  room_code: string
  host_peer_id: string
  host_token: string
  invitations: RendezvousPeerInvitation[]
  expires_at: string
  websocket_url: string
}

export interface RendezvousConfigResponse {
  ok: boolean
  enabled: boolean
  entry_visible: boolean
  load_level: 'normal' | 'busy' | 'nearly_full'
  max_peers_per_room: number
  retry_after: number
  message: string
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
