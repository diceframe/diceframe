import type { JsonObject, RulesetSelectedClassSpells } from '@/api/types'

export interface Dnd2024Draft extends JsonObject {
  locale: string
  name: string
  level: number
  alignment: string
  ability_method: string
  base_abilities: Record<string, number>
  background_ability_bonuses: Record<string, number>
  class_ref: string
  species_ref: string
  background_ref: string
  species_size?: string
  species_choice_answers: Record<string, string>
  species_skill_refs: string[]
  species_feat_refs: string[]
  feat_choice_answers: Record<string, Record<string, string[]>>
  class_skill_refs: string[]
  class_tool_refs: string[]
  equipment_package_ref: string
  background_equipment_package_ref: string
  language_refs: string[]
  class_spell_choices: RulesetSelectedClassSpells
  feature_choices?: {
    owner: string
    feature_id: string
    choice_id: string
    selected_refs: (string | { ref: string; object_ref?: string })[]
  }[]
}
