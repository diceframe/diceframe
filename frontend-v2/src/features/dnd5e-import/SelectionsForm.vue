<script setup lang="ts">
import { computed } from 'vue'
import type {
  JsonObject,
  RulesetBuilderChoices,
  RulesetChoice,
  RulesetClassSpellChoices,
  RulesetSelectedClassSpells,
  RulesetSpellChoice,
} from '@/api/types'
import type { Dnd5eFeatureChoiceRecord, Dnd5eMissingEntry, Dnd5eSelections } from '@/api/dnd5eImport'
import { useLocale } from '@/composables/useLocale'
import { dnd5eImportCopy } from './copy'

const props = withDefaults(defineProps<{
  draft: JsonObject
  choices: RulesetBuilderChoices
  missing: Dnd5eMissingEntry[]
  selections: Dnd5eSelections
  disabled?: boolean
}>(), { disabled: false })
const emit = defineEmits<{ change: [next: Dnd5eSelections] }>()
const { locale } = useLocale()
const copy = computed(() => dnd5eImportCopy(locale.value).selections)

type RefListKey = 'class_skill_refs' | 'species_skill_refs' | 'species_feat_refs' | 'class_tool_refs' | 'language_refs'
type SpellListKey = 'cantrip' | 'prepared_spell' | 'spellbook'
interface FeatureSlot { owner: string; feature_id: string; choice_id: string; count: number; options: Array<{ value: string; name?: string }> }
type FeatureKeyParts = Pick<FeatureSlot, 'owner' | 'feature_id' | 'choice_id'>

function required(field: string): boolean {
  return props.missing.some(entry => entry.field === field)
}

function eff(key: keyof Dnd5eSelections): unknown {
  const own = props.selections[key]
  return own !== undefined ? own : props.draft[key]
}

function set(key: keyof Dnd5eSelections, value: unknown): void {
  emit('change', { ...props.selections, [key]: value })
}

function effList(key: RefListKey): string[] {
  const value = eff(key)
  return Array.isArray(value) ? value.map(String) : []
}

function effRecord(key: 'species_choice_answers'): Record<string, string> {
  const base = props.draft[key]
  const own = props.selections[key]
  return {
    ...(base && typeof base === 'object' ? base as Record<string, string> : {}),
    ...(own || {}),
  }
}

function choiceName(ref: string): string {
  const groups: RulesetChoice[][] = [
    props.choices.classes, props.choices.species, props.choices.backgrounds,
    props.choices.skills, props.choices.languages, props.choices.origin_feats,
    props.choices.equipment_packages, props.choices.background_equipment_packages,
    props.choices.class_tools,
  ]
  return groups.flat().find(item => item.ref === ref)?.name || ref.split(':').pop() || ref
}

function optionLabel(value: string): string {
  return value.includes(':') ? choiceName(value) : value.replaceAll('_', ' ')
}

function stringValue(key: keyof Dnd5eSelections): string {
  const value = eff(key)
  return typeof value === 'string' ? value : ''
}

function onSelect(key: keyof Dnd5eSelections, event: Event): void {
  set(key, (event.target as HTMLSelectElement).value)
}

// --- ref lists (skills / tools / languages) ---

function refDisabled(key: RefListKey, ref: string, max: number): boolean {
  const current = effList(key)
  if (current.includes(ref)) return false
  if (key === 'class_skill_refs' && effList('species_skill_refs').includes(ref)) return true
  if (key === 'species_skill_refs' && effList('class_skill_refs').includes(ref)) return true
  return Boolean(max && current.length >= max)
}

function toggleRef(key: RefListKey, ref: string, max: number): void {
  const current = [...effList(key)]
  const index = current.indexOf(ref)
  if (index >= 0) current.splice(index, 1)
  else if (!max || current.length < max) current.push(ref)
  set(key, current)
}

// --- background ability bonuses ---

const bonusIds = computed(() => props.choices.background_ability_refs.map(ref => ref.split(':')[1]).filter(Boolean))
const bonuses = computed<Record<string, number>>(() => {
  const value = eff('background_ability_bonuses')
  return value && typeof value === 'object' ? value as Record<string, number> : {}
})
const bonusTotal = computed(() => Object.values(bonuses.value).reduce((total, value) => total + (Number(value) || 0), 0))

function setBonusPreset(split: boolean): void {
  const ids = [...bonusIds.value]
  if (split) {
    set('background_ability_bonuses', Object.fromEntries(ids.map(id => [id, 1])))
    return
  }
  const base = (props.draft.base_abilities || {}) as Record<string, number>
  ids.sort((a, b) => Number(base[b] || 0) - Number(base[a] || 0))
  set('background_ability_bonuses', ids.length >= 2 ? { [ids[0]]: 2, [ids[1]]: 1 } : {})
}

function setBonus(id: string, event: Event): void {
  set('background_ability_bonuses', { ...bonuses.value, [id]: Number((event.target as HTMLSelectElement).value) })
}

// --- species choice answers ---

function speciesOptions(spec: { option_ids?: string[]; option_refs?: string[] }): string[] {
  return spec.option_ids || spec.option_refs || []
}

function setSpeciesAnswer(id: string, event: Event): void {
  set('species_choice_answers', { ...effRecord('species_choice_answers'), [id]: (event.target as HTMLSelectElement).value })
}

// --- feat follow-up choices ---

const featAnswers = computed<Record<string, Record<string, string[]>>>(() => {
  const base = props.draft.feat_choice_answers
  const own = props.selections.feat_choice_answers
  const merged: Record<string, Record<string, string[]>> = {}
  for (const source of [base, own]) {
    if (!source || typeof source !== 'object') continue
    for (const [featRef, answers] of Object.entries(source as Record<string, Record<string, string[]>>)) {
      merged[featRef] = { ...(merged[featRef] || {}), ...(answers || {}) }
    }
  }
  return merged
})

function featPicked(featRef: string, choiceId: string): string[] {
  return featAnswers.value[featRef]?.[choiceId] || []
}

function featDisabled(featRef: string, choiceId: string, value: string, count: number): boolean {
  if (count <= 1) return false
  const current = featPicked(featRef, choiceId)
  return !current.includes(value) && current.length >= count
}

function setFeatChoice(featRef: string, choiceId: string, value: string, count: number): void {
  const current = [...featPicked(featRef, choiceId)]
  if (count === 1) {
    current.splice(0, current.length, value)
  } else {
    const index = current.indexOf(value)
    if (index >= 0) current.splice(index, 1)
    else if (current.length < count) current.push(value)
  }
  set('feat_choice_answers', {
    ...featAnswers.value,
    [featRef]: { ...(featAnswers.value[featRef] || {}), [choiceId]: current },
  })
}

// --- feature branch choices (feature_option_choices) ---

const featureRecords = computed<Dnd5eFeatureChoiceRecord[]>(() => {
  const value = eff('feature_choices')
  return Array.isArray(value) ? value as Dnd5eFeatureChoiceRecord[] : []
})

function featureKey(slot: FeatureKeyParts): string {
  return `${slot.owner}|${slot.feature_id}|${slot.choice_id}`
}

function featureMissing(slot: FeatureSlot): boolean {
  return required(`feature_choices.${slot.owner}.${slot.feature_id}.${slot.choice_id}`)
}

function featurePicked(slot: FeatureSlot): string[] {
  const record = featureRecords.value.find(row => featureKey(row) === featureKey(slot))
  return (record?.selected_refs || []).map(ref => (typeof ref === 'string' ? ref : ref.ref))
}

function featureDisabled(slot: FeatureSlot, value: string): boolean {
  const current = featurePicked(slot)
  return !current.includes(value) && current.length >= slot.count
}

function toggleFeature(slot: FeatureSlot, value: string): void {
  const current = [...featurePicked(slot)]
  const index = current.indexOf(value)
  if (index >= 0) current.splice(index, 1)
  else if (current.length < slot.count) current.push(value)
  const rest = featureRecords.value.filter(row => featureKey(row) !== featureKey(slot))
  const next = current.length
    ? [...rest, { owner: slot.owner, feature_id: slot.feature_id, choice_id: slot.choice_id, selected_refs: current }]
    : rest
  set('feature_choices', next)
}

// --- class spells ---

const classSpells = computed<RulesetClassSpellChoices | null>(() => (
  props.choices.class_spells && 'requirements' in props.choices.class_spells
    ? props.choices.class_spells as RulesetClassSpellChoices
    : null
))

const spellSelections = computed<RulesetSelectedClassSpells>(() => {
  const value = eff('class_spell_choices')
  return value && typeof value === 'object' ? value as RulesetSelectedClassSpells : {}
})

function selectedSpellRefs(key: SpellListKey): string[] {
  const refs = spellSelections.value[`${key}_refs`]
  if (Array.isArray(refs)) return refs
  const ids = spellSelections.value[`${key}_ids`]
  return Array.isArray(ids) ? ids.map(id => `spell:${id}`) : []
}

function spellDisabled(key: SpellListKey, spell: RulesetSpellChoice, maximum: number): boolean {
  const current = selectedSpellRefs(key)
  if (current.includes(spell.ref)) return false
  if (
    key === 'prepared_spell'
    && classSpells.value?.requirements.spellbook_minimum
    && !selectedSpellRefs('spellbook').includes(spell.ref)
  ) return true
  return Boolean(maximum && current.length >= maximum)
}

function toggleSpell(key: SpellListKey, spell: RulesetSpellChoice, maximum: number): void {
  const current = [...selectedSpellRefs(key)]
  const index = current.indexOf(spell.ref)
  if (index >= 0) current.splice(index, 1)
  else if (!maximum || current.length < maximum) current.push(spell.ref)
  const next: RulesetSelectedClassSpells = { ...spellSelections.value }
  delete next[`${key}_ids`]
  next[`${key}_refs`] = current
  if (key === 'spellbook') {
    next.prepared_spell_refs = selectedSpellRefs('prepared_spell').filter(ref => current.includes(ref))
    delete next.prepared_spell_ids
  }
  set('class_spell_choices', next)
}
</script>

<template>
  <section class="dnd5e-selections" data-testid="dnd5e-selection-form">
    <h3>{{ copy.title }}</h3>
    <p class="dnd5e-selections-hint">{{ copy.hint }}</p>

    <!-- Unmapped class/species/background are reported as blocking missing entries
         by the preview; the backend selection contract does not accept overriding
         them, so they stay read-only in the missing list (fail closed). -->

    <fieldset v-if="bonusIds.length" :disabled="disabled" data-testid="dnd5e-bonuses">
      <legend>{{ copy.bonuses }} <em v-if="required('background_ability_bonuses')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <p class="dnd5e-field-hint">{{ copy.bonusesHint }} {{ copy.bonusTotal }}: {{ bonusTotal }}/3</p>
      <div class="dnd5e-bonus-actions">
        <button type="button" data-testid="dnd5e-bonus-2-1" @click="setBonusPreset(false)">{{ copy.bonus21 }}</button>
        <button type="button" data-testid="dnd5e-bonus-1-1-1" @click="setBonusPreset(true)">{{ copy.bonus111 }}</button>
      </div>
      <div class="dnd5e-bonus-grid">
        <label v-for="id in bonusIds" :key="id">
          <span>{{ copy.abilities[id] || id }}</span>
          <select :value="bonuses[id] || 0" :data-testid="`dnd5e-bonus-${id}`" @change="setBonus(id, $event)">
            <option :value="0">+0</option>
            <option :value="1">+1</option>
            <option :value="2">+2</option>
          </select>
        </label>
      </div>
    </fieldset>

    <fieldset v-if="choices.equipment_packages.length" :disabled="disabled">
      <legend>{{ copy.equipment }} <em v-if="required('equipment_package_ref')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <select :value="stringValue('equipment_package_ref')" data-testid="dnd5e-select-equipment_package_ref" @change="onSelect('equipment_package_ref', $event)">
        <option value="" disabled>{{ copy.choose }}</option>
        <option v-for="item in choices.equipment_packages" :key="item.ref" :value="item.ref">{{ item.name }}</option>
      </select>
    </fieldset>

    <fieldset v-if="choices.background_equipment_packages.length" :disabled="disabled">
      <legend>{{ copy.backgroundEquipment }} <em v-if="required('background_equipment_package_ref')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <select :value="stringValue('background_equipment_package_ref')" data-testid="dnd5e-select-background_equipment_package_ref" @change="onSelect('background_equipment_package_ref', $event)">
        <option value="" disabled>{{ copy.choose }}</option>
        <option v-for="item in choices.background_equipment_packages" :key="item.ref" :value="item.ref">{{ item.name }}</option>
      </select>
    </fieldset>

    <fieldset v-if="choices.languages.length" :disabled="disabled">
      <legend>{{ copy.languages }} ({{ effList('language_refs').length }}/3) <em v-if="required('language_refs')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <div class="dnd5e-check-grid">
        <label v-for="item in choices.languages" :key="item.ref" :class="{ unavailable: refDisabled('language_refs', item.ref, 3) }">
          <input
            type="checkbox"
            :checked="effList('language_refs').includes(item.ref)"
            :disabled="refDisabled('language_refs', item.ref, 3)"
            :data-testid="`dnd5e-check-language_refs-${item.ref}`"
            @change="toggleRef('language_refs', item.ref, 3)"
          > {{ item.name }}
        </label>
      </div>
    </fieldset>

    <fieldset v-if="choices.species_sizes.length > 1" :disabled="disabled">
      <legend>{{ copy.size }} <em v-if="required('species_size')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <div class="dnd5e-check-grid">
        <label v-for="size in choices.species_sizes" :key="size">
          <input type="radio" name="dnd5e-species-size" :checked="stringValue('species_size') === size" :value="size" :data-testid="`dnd5e-size-${size}`" @change="set('species_size', size)"> {{ optionLabel(size) }}
        </label>
      </div>
    </fieldset>

    <fieldset v-for="spec in choices.species_choices" :key="spec.id" :disabled="disabled">
      <legend>{{ copy.speciesChoice }}: {{ optionLabel(spec.id) }} <em v-if="required(`species_choice_answers.${spec.id}`)" class="dnd5e-required">{{ copy.required }}</em></legend>
      <select :value="effRecord('species_choice_answers')[spec.id] || ''" :data-testid="`dnd5e-select-species-${spec.id}`" @change="setSpeciesAnswer(spec.id, $event)">
        <option value="" disabled>{{ copy.choose }}</option>
        <option v-for="option in speciesOptions(spec)" :key="option" :value="option">{{ optionLabel(option) }}</option>
      </select>
    </fieldset>

    <fieldset v-if="choices.class_skill_count" :disabled="disabled">
      <legend>{{ copy.classSkills }} ({{ effList('class_skill_refs').length }}/{{ choices.class_skill_count }}) <em v-if="required('class_skill_refs')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <div class="dnd5e-check-grid">
        <label v-for="item in choices.class_skills" :key="item.ref" :class="{ unavailable: refDisabled('class_skill_refs', item.ref, choices.class_skill_count) }">
          <input
            type="checkbox"
            :checked="effList('class_skill_refs').includes(item.ref)"
            :disabled="refDisabled('class_skill_refs', item.ref, choices.class_skill_count)"
            :data-testid="`dnd5e-check-class_skill_refs-${item.ref}`"
            @change="toggleRef('class_skill_refs', item.ref, choices.class_skill_count)"
          > {{ item.name }}
        </label>
      </div>
    </fieldset>

    <fieldset v-if="choices.species_skill_count" :disabled="disabled">
      <legend>{{ copy.speciesSkills }} ({{ effList('species_skill_refs').length }}/{{ choices.species_skill_count }}) <em v-if="required('species_skill_refs')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <div class="dnd5e-check-grid">
        <label v-for="item in choices.species_skills" :key="item.ref" :class="{ unavailable: refDisabled('species_skill_refs', item.ref, choices.species_skill_count) }">
          <input
            type="checkbox"
            :checked="effList('species_skill_refs').includes(item.ref)"
            :disabled="refDisabled('species_skill_refs', item.ref, choices.species_skill_count)"
            :data-testid="`dnd5e-check-species_skill_refs-${item.ref}`"
            @change="toggleRef('species_skill_refs', item.ref, choices.species_skill_count)"
          > {{ item.name }}
        </label>
      </div>
    </fieldset>

    <fieldset v-if="choices.species_feat_count" :disabled="disabled">
      <legend>{{ copy.speciesFeats }} ({{ effList('species_feat_refs').length }}/{{ choices.species_feat_count }}) <em v-if="required('species_feat_refs')" class="dnd5e-required">{{ copy.required }}</em></legend>
      <div class="dnd5e-check-grid">
        <label v-for="item in choices.species_feats" :key="item.ref" :class="{ unavailable: refDisabled('species_feat_refs', item.ref, choices.species_feat_count) }">
          <input
            type="checkbox"
            :checked="effList('species_feat_refs').includes(item.ref)"
            :disabled="refDisabled('species_feat_refs', item.ref, choices.species_feat_count)"
            :data-testid="`dnd5e-check-species_feat_refs-${item.ref}`"
            @change="toggleRef('species_feat_refs', item.ref, choices.species_feat_count)"
          > {{ item.name }}
        </label>
      </div>
    </fieldset>

    <fieldset v-if="choices.class_tool_count" :disabled="disabled">
      <legend>{{ copy.classTools }} ({{ effList('class_tool_refs').length }}/{{ choices.class_tool_count }})</legend>
      <div class="dnd5e-check-grid">
        <label v-for="item in choices.class_tools" :key="item.ref" :class="{ unavailable: refDisabled('class_tool_refs', item.ref, choices.class_tool_count) }">
          <input
            type="checkbox"
            :checked="effList('class_tool_refs').includes(item.ref)"
            :disabled="refDisabled('class_tool_refs', item.ref, choices.class_tool_count)"
            :data-testid="`dnd5e-check-class_tool_refs-${item.ref}`"
            @change="toggleRef('class_tool_refs', item.ref, choices.class_tool_count)"
          > {{ item.name }}
        </label>
      </div>
    </fieldset>

    <fieldset v-for="feat in choices.feat_choices" :key="feat.feat_ref" :disabled="disabled">
      <legend>{{ copy.featChoices }}: {{ feat.name }}</legend>
      <p v-if="feat.summary" class="dnd5e-field-hint">{{ feat.summary }}</p>
      <div v-for="spec in feat.specs" :key="spec.id" class="dnd5e-feat-group">
        <b>{{ spec.name }} ({{ featPicked(feat.feat_ref, spec.id).length }}/{{ spec.count }})
          <em v-if="required(`feat_choice_answers.${feat.feat_ref}.${spec.id}`)" class="dnd5e-required">{{ copy.required }}</em>
        </b>
        <div class="dnd5e-check-grid">
          <label v-for="option in spec.options" :key="option.value" :class="{ unavailable: featDisabled(feat.feat_ref, spec.id, option.value, spec.count) }">
            <input
              :type="spec.count === 1 ? 'radio' : 'checkbox'"
              :name="`dnd5e-feat-${feat.feat_ref}-${spec.id}`"
              :checked="featPicked(feat.feat_ref, spec.id).includes(option.value)"
              :disabled="featDisabled(feat.feat_ref, spec.id, option.value, spec.count)"
              :data-testid="`dnd5e-feat-${feat.feat_ref}-${spec.id}-${option.value}`"
              @change="setFeatChoice(feat.feat_ref, spec.id, option.value, spec.count)"
            > {{ option.name }}
          </label>
        </div>
      </div>
    </fieldset>

    <fieldset v-for="slot in (choices.feature_option_choices || []) as FeatureSlot[]" :key="featureKey(slot)" :disabled="disabled">
      <legend>{{ copy.featureOptions }} ({{ featurePicked(slot).length }}/{{ slot.count }}) <em v-if="featureMissing(slot)" class="dnd5e-required">{{ copy.required }}</em></legend>
      <div class="dnd5e-check-grid">
        <label v-for="option in slot.options" :key="option.value" :class="{ unavailable: featureDisabled(slot, option.value) }">
          <input
            :type="slot.count === 1 ? 'radio' : 'checkbox'"
            :name="`dnd5e-feature-${featureKey(slot)}`"
            :checked="featurePicked(slot).includes(option.value)"
            :disabled="featureDisabled(slot, option.value)"
            :data-testid="`dnd5e-feature-${slot.owner}-${slot.feature_id}-${slot.choice_id}-${option.value}`"
            @change="toggleFeature(slot, option.value)"
          > {{ option.name || optionLabel(option.value) }}
        </label>
      </div>
    </fieldset>

    <fieldset v-if="classSpells" :disabled="disabled" data-testid="dnd5e-spells">
      <legend>{{ copy.cantrips }} / {{ copy.preparedSpells }}</legend>
      <div v-if="classSpells.requirements.cantrip_count" class="dnd5e-feat-group">
        <b>{{ copy.cantrips }} ({{ selectedSpellRefs('cantrip').length }}/{{ classSpells.requirements.cantrip_count }})</b>
        <div class="dnd5e-check-grid">
          <label v-for="spell in classSpells.cantrips" :key="spell.ref" :class="{ unavailable: spellDisabled('cantrip', spell, classSpells.requirements.cantrip_count) }">
            <input
              type="checkbox"
              :checked="selectedSpellRefs('cantrip').includes(spell.ref)"
              :disabled="spellDisabled('cantrip', spell, classSpells.requirements.cantrip_count)"
              :data-testid="`dnd5e-spell-cantrip-${spell.ref}`"
              @change="toggleSpell('cantrip', spell, classSpells.requirements.cantrip_count)"
            > {{ spell.name }}
          </label>
        </div>
      </div>
      <div v-if="classSpells.requirements.spellbook_minimum" class="dnd5e-feat-group">
        <b>{{ copy.spellbook }} ({{ selectedSpellRefs('spellbook').length }}/{{ classSpells.requirements.spellbook_minimum }})</b>
        <div class="dnd5e-check-grid">
          <label v-for="spell in classSpells.leveled_spells" :key="`book-${spell.ref}`" :class="{ unavailable: spellDisabled('spellbook', spell, classSpells.requirements.spellbook_minimum) }">
            <input
              type="checkbox"
              :checked="selectedSpellRefs('spellbook').includes(spell.ref)"
              :disabled="spellDisabled('spellbook', spell, classSpells.requirements.spellbook_minimum)"
              :data-testid="`dnd5e-spell-spellbook-${spell.ref}`"
              @change="toggleSpell('spellbook', spell, classSpells.requirements.spellbook_minimum)"
            > {{ spell.name }}
          </label>
        </div>
      </div>
      <div v-if="classSpells.requirements.prepared_spell_count" class="dnd5e-feat-group">
        <b>{{ copy.preparedSpells }} ({{ selectedSpellRefs('prepared_spell').length }}/{{ classSpells.requirements.prepared_spell_count }})</b>
        <p v-if="classSpells.requirements.spellbook_minimum" class="dnd5e-field-hint">{{ copy.spellbookHint }}</p>
        <div class="dnd5e-check-grid">
          <label v-for="spell in classSpells.leveled_spells" :key="`prepared-${spell.ref}`" :class="{ unavailable: spellDisabled('prepared_spell', spell, classSpells.requirements.prepared_spell_count) }">
            <input
              type="checkbox"
              :checked="selectedSpellRefs('prepared_spell').includes(spell.ref)"
              :disabled="spellDisabled('prepared_spell', spell, classSpells.requirements.prepared_spell_count)"
              :data-testid="`dnd5e-spell-prepared_spell-${spell.ref}`"
              @change="toggleSpell('prepared_spell', spell, classSpells.requirements.prepared_spell_count)"
            > {{ spell.name }}
          </label>
        </div>
      </div>
    </fieldset>
  </section>
</template>

<style scoped>
.dnd5e-selections { display: grid; gap: .7rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); min-width: 0; }
.dnd5e-selections h3 { margin: 0; color: var(--df-accent-strong); font-size: 1rem; }
.dnd5e-selections-hint { margin: 0; color: var(--df-text-muted); font-size: .78rem; }
fieldset { display: grid; gap: .5rem; margin: 0; padding: .7rem .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); min-width: 0; }
fieldset:disabled { opacity: .6; }
legend { padding: 0 .35rem; color: var(--df-text); font-size: .85rem; font-weight: 650; }
label { display: grid; gap: .3rem; color: var(--df-text-secondary); font-size: .82rem; }
select { width: 100%; min-width: 0; min-height: 40px; box-sizing: border-box; padding: .4rem .5rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text); }
.dnd5e-required { margin-left: .3rem; padding: .05rem .4rem; border-radius: 999px; background: color-mix(in srgb, var(--df-warning) 18%, var(--df-surface-raised)); color: var(--df-warning); font-size: .68rem; font-style: normal; font-weight: 700; }
.dnd5e-field-hint { margin: 0; color: var(--df-text-muted); font-size: .75rem; }
.dnd5e-bonus-actions { display: flex; gap: .5rem; flex-wrap: wrap; }
.dnd5e-bonus-actions button { min-height: 36px; padding: .3rem .7rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); color: var(--df-text); cursor: pointer; font-size: .78rem; }
.dnd5e-bonus-grid { display: grid; grid-template-columns: repeat(3, minmax(90px, 1fr)); gap: .5rem; }
.dnd5e-check-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: .4rem; }
.dnd5e-check-grid label { display: flex; align-items: center; gap: .45rem; min-height: 38px; padding: .35rem .5rem; border-radius: var(--df-radius-sm); background: var(--df-surface-2); }
.dnd5e-check-grid label.unavailable { opacity: .5; cursor: not-allowed; }
.dnd5e-check-grid input { width: 18px; height: 18px; flex: none; }
.dnd5e-feat-group { display: grid; gap: .4rem; }
.dnd5e-feat-group b { color: var(--df-text-secondary); font-size: .8rem; }
</style>