import { api } from '@/api/client'
import type { JsonObject, RulesetSelectedClassSpells } from '@/api/types'

/** Conversion levels shared with the FVTT scene report (workplan §6.6). */
export type Dnd5eConversionLevel =
  | 'convertible'
  | 'saved_not_runnable'
  | 'degraded_display'
  | 'missing_external_ref'
  | 'missing_module_dependency'
  | 'unsupported_ruleset'
  | 'unknown_future_structure'
  | 'contains_script'

export interface Dnd5eConversionIssue {
  level: Dnd5eConversionLevel
  path: string
  detail: string
}

/** Mirrors `Dnd5eConversionReport.to_dict()` in src/compat/dnd5e/mapping.py. */
export interface Dnd5eConversionReport {
  source_version: string
  level: Dnd5eConversionLevel
  issues: Dnd5eConversionIssue[]
  unsupported: Array<{ path: string; kind: string; detail: string }>
  preserved_fields: string[]
  unknown_fields: string[]
  missing_references: Array<{ kind: string; path: string; external_id: string }>
  warnings: string[]
  runtime_reconciliation: Dnd5eReconciliationEntry[]
  contains_script: boolean
}

export interface Dnd5eReconciliationEntry {
  field: string
  declared: unknown
  recomputed: unknown
  note: string
}

export interface Dnd5eEntityDisplay {
  name: string
  ref: string | null
  raw_name: string
  raw_identifier: string
}

export interface Dnd5eClassDisplay extends Dnd5eEntityDisplay {
  levels: number | null
}

export interface Dnd5eItemDisplay {
  name: string
  type: string
  identifier: string
  image: string
  quantity: number | null
  weight: { value: unknown; units: unknown } | null
  price: { value: unknown; denomination: unknown } | null
  damage: { formula: string | null; types: string[]; declared: boolean }
  armor: { value: number | null; dex: number | null }
  properties: string[]
  description: string
  declared: boolean
  catalog_ref?: string | null
}

/** Presentation-safe actor DTO from `convert_actor` (display only, never executed). */
export interface Dnd5eActorDisplay {
  name: string
  type: string
  image: string
  level: number | null
  alignment: string
  hp: { value: number | null; max: number | null; temp: number | null; declared: boolean }
  armor_class: number | null
  speed: number | null
  declared: boolean
  species: Dnd5eEntityDisplay | null
  background: Dnd5eEntityDisplay | null
  class: Dnd5eClassDisplay[]
  abilities: Record<string, { score: number | null; declared: boolean }>
  skills: Array<{ id: string; ref: string | null; proficiency: number; declared: boolean }>
  languages: Array<{ key: string; ref: string | null; text?: string }>
  items: Dnd5eItemDisplay[]
}

export interface Dnd5eMissingEntry {
  field: string
  reason: string
}

export interface Dnd5eFeatureChoiceRecord {
  owner: string
  feature_id: string
  choice_id: string
  selected_refs: Array<string | { ref: string; object_ref?: string }>
}

/**
 * User choices overlaid onto the converted draft. Mirrors
 * `DRAFT_SELECTION_FIELDS` in src/compat/dnd5e/creation.py; `progression` is
 * deliberately absent — above level 1 the import fails closed instead of
 * guessing a level-up history.
 */
export interface Dnd5eSelections {
  background_ability_bonuses?: Record<string, number>
  equipment_package_ref?: string
  background_equipment_package_ref?: string
  language_refs?: string[]
  species_size?: string
  species_choice_answers?: Record<string, string>
  species_skill_refs?: string[]
  species_feat_refs?: string[]
  class_skill_refs?: string[]
  class_tool_refs?: string[]
  feat_choice_answers?: Record<string, Record<string, string[]>>
  feature_choices?: Dnd5eFeatureChoiceRecord[]
  class_spell_choices?: RulesetSelectedClassSpells
  subclass_ref?: string
  name?: string
  alignment?: string
  locale?: string
}

/** `preview` response of src/webui/services/dnd5e_import.py. */
export interface Dnd5ePreviewResponse {
  ok: boolean
  valid: boolean
  draft: JsonObject
  missing: Dnd5eMissingEntry[]
  errors: string[]
  display: Dnd5eActorDisplay
  report: Dnd5eConversionReport
  reconciliation: Dnd5eReconciliationEntry[]
  source_unresolved: Dnd5eMissingEntry[]
  character?: JsonObject
}

export interface Dnd5eCreatedReceipt {
  user_id?: string
  character_name?: string
  seat_token?: string
  reused?: boolean
}

/** `commit` response of src/webui/services/dnd5e_import.py. */
export interface Dnd5eCommitResponse {
  ok: boolean
  created: Dnd5eCreatedReceipt
  character: JsonObject
  report: Dnd5eConversionReport
  reconciliation: Dnd5eReconciliationEntry[]
}

function importPath(gameKey: string, action: 'preview' | 'commit'): string {
  return `/games/${encodeURIComponent(gameKey)}/dnd5e/characters/${action}`
}

function importBody(
  actor: JsonObject,
  selections: Dnd5eSelections,
  locale: string,
): string {
  return JSON.stringify({ actor, selections, locale })
}

export function previewDnd5eCharacter(
  gameKey: string,
  actor: JsonObject,
  selections: Dnd5eSelections = {},
  locale = '',
): Promise<Dnd5ePreviewResponse> {
  return api<Dnd5ePreviewResponse>(importPath(gameKey, 'preview'), {
    method: 'POST',
    body: importBody(actor, selections, locale),
  })
}

export function commitDnd5eCharacter(
  gameKey: string,
  actor: JsonObject,
  selections: Dnd5eSelections = {},
  locale = '',
): Promise<Dnd5eCommitResponse> {
  return api<Dnd5eCommitResponse>(importPath(gameKey, 'commit'), {
    method: 'POST',
    body: importBody(actor, selections, locale),
  })
}