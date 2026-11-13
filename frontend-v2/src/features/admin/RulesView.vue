<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { RuleAttributeEdit, RuleDetailResponse, RuleEditorState, RuleForm, RulesResponse, RuleSummary, RuleTemplate } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import { localizedField } from '@/utils/ruleSchema'
import Modal from '@/components/ui/Modal.vue'
import CombatExtensionEditor from '@/components/admin/CombatExtensionEditor.vue'
import { blockFromDraft, draftFromBlock, emptyDraft, type CombatDraft } from '@/features/admin/combatExtensionDraft'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import RuleResourcesEditor from './RuleResourcesEditor.vue'

const toast = useToast()
const { confirm } = useConfirm()
const { t } = useLocale()

const data = ref<RulesResponse | null>(null)
const error = ref('')
const busy = ref(false)
const ruleEdit = ref<RuleEditorState | null>(null)
const ruleForm = ref<RuleForm | null>(null)
const ruleJson = ref('')
const advancedJson = ref(false)
const combatDraft = ref<CombatDraft>(emptyDraft())
const search = ref('')
const kindFilter = ref<'all' | 'builtin' | 'custom'>('all')

const visibleRules = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase()
  return (data.value?.rules || []).filter(rule => {
    if (kindFilter.value === 'custom' && !rule.custom) return false
    if (kindFilter.value === 'builtin' && rule.custom) return false
    if (!needle) return true
    const text = [rule.rule_id, rule.rule_name, rule.rule_name_en, localizedField<string>(rule, 'rule_name'), localizedField<string>(rule, 'description'), rule.description, rule.ruleset_runtime?.id]
      .filter(Boolean).join(' ').toLocaleLowerCase()
    return text.includes(needle)
  })
})

function runtimeCapabilityLabels(rule: RuleSummary): string[] {
  const capabilities = rule.ruleset_runtime?.capabilities
  if (!capabilities) return ['自由文本回合']
  const labels: string[] = []
  if (capabilities.authoritative_intents) labels.push('权威动作')
  if (capabilities.deterministic_combat) labels.push('确定性战斗')
  if (capabilities.sequential_checks) labels.push('顺序多检定')
  if (capabilities.composite_action) labels.push('组合技能')
  if (capabilities.versioned_state) labels.push('可恢复状态')
  return labels.length ? labels : ['仅叙事投影']
}

const editorTitle = computed(() => {
  if (!ruleEdit.value) return t('ruleEditor')
  if (ruleEdit.value.mode === 'new') return t('newCustomRule')
  if (ruleEdit.value.mode === 'copy') return t('copyAndEditRule')
  return t('editCustomRule')
})

const combatResourceOptions = computed(() => (
  combatDraft.value.resources.map(resource => resource.id).filter(Boolean)
))

function errorMessage(err: unknown): string { return err instanceof Error ? err.message : String(err || t('operationFailed')) }

async function mutateRule(path: string, init: RequestInit) {
  const result = await api<{ ok: boolean; error?: string }>(path, init)
  if (result.ok !== true) throw new Error(result.error || t('operationFailed'))
  return result
}

async function load() {
  error.value = ''; data.value = null
  try { data.value = await api<RulesResponse>('/rules') }
  catch (e: unknown) { error.value = errorMessage(e) }
}
onMounted(load)

function toRuleAttr(attr: Partial<RuleAttributeEdit> = {}): RuleAttributeEdit {
  return {
    key: String(attr.key || ''),
    name: String(attr.name || ''),
    min: Number(attr.min ?? 0),
    max: Number(attr.max ?? 100),
  }
}

function buildForm(template: RuleTemplate): RuleForm {
  const form: RuleForm = {
    rule_id: String(template.rule_id || ''),
    rule_name: String(template.rule_name || ''),
    description: String(template.description || ''),
    dice_system: String(template.dice_system || 'd20'),
    combat_model: String(template.combat_model || 'hp_based'),
    mechanics: String(template.mechanics || ''),
    ruleset_level: String(template.ruleset_level || 'assisted'),
    attribute_points: Number(template.attribute_points || 0),
    max_skills: Number(template.max_skills || 0),
    skill_point_total: Number(template.skill_point_total || 0),
    currency: String(template.currency || t('goldCurrency')),
    currency_has_minor: false,
    currency_major: String(template.currency || t('goldCurrency')),
    currency_minor: '',
    currency_rate: 100,
    currency_symbol: '',
    currency_simple_editable: true,
    hp_formula: String(template.hp_formula || ''),
    special_stats: Array.isArray(template.special_stats) ? template.special_stats.map(stat => ({ ...stat })) : [],
    gm_prompt_appendix: String(template.gm_prompt_appendix || ''),
    attributes: (template.attributes || []).map(toRuleAttr),
  }
  const system = template.currency_system as {
    schema_version?: number
    base_unit?: string
    display_unit?: string
    units?: Array<{ id: string; name: string; symbol?: string; rate: number }>
  } | undefined
  if (system && system.schema_version === 2 && Array.isArray(system.units)) {
    const units = system.units
    if (units.length === 1) {
      form.currency_has_minor = false
      form.currency_major = String(units[0].name || form.currency_major)
      form.currency_symbol = String(units[0].symbol || '')
    } else if (units.length === 2) {
      const display = units.find(u => u.id === system.display_unit) || units[0]
      const base = units.find(u => u.id === system.base_unit)
        || units.find(u => Number(u.rate) === 1)
        || units[1]
      form.currency_has_minor = true
      form.currency_major = String(display.name || form.currency_major)
      form.currency_symbol = String(display.symbol || '')
      form.currency_minor = String(base.name || '')
      form.currency_rate = Number(display.rate) || 100
    } else {
      // 多单位高级货币：简单模式不覆盖，交给高级 JSON。
      form.currency_simple_editable = false
    }
  }
  return form
}

function jsonTemplate(): RuleTemplate {
  try { return JSON.parse(ruleJson.value || '{}') as RuleTemplate }
  catch { throw new Error(t('ruleJsonInvalid')) }
}

function isDefaultCombatDraft(draft: CombatDraft): boolean {
  const resource = draft.resources[0]
  const topLevelExtras = Object.keys(draft.raw || {}).filter(
    key => !['scheduler', 'resources', 'actions'].includes(key),
  )
  const resourceExtras = Object.keys(resource?.raw || {}).filter(
    key => !['id', 'source', 'stat', 'maximum', 'costable', 'damage_priority', 'damage_types'].includes(key),
  )
  return !draft.scheduler
    && draft.actions.length === 0
    && draft.resources.length === 1
    && resource?.id === 'hp'
    && resource.source === 'hp'
    && resource.stat == null
    && resource.maximum == null
    && resource.costable !== false
    && !resource.damage_priority
    && !resource.damage_types?.length
    && topLevelExtras.length === 0
    && resourceExtras.length === 0
}

function syncCombatDraft(template: RuleTemplate) {
  combatDraft.value = draftFromBlock(template.combat) || emptyDraft()
}

function applyFormToJson(): RuleTemplate {
  if (!ruleForm.value) throw new Error(t('ruleFormNotInitialized'))
  const form = ruleForm.value
  const template = jsonTemplate()
  template.rule_id = form.rule_id.trim()
  template.rule_name = form.rule_name.trim()
  template.description = form.description || ''
  template.dice_system = form.dice_system || 'd20'
  template.combat_model = form.combat_model || 'hp_based'
  template.mechanics = form.mechanics || String(template.mechanics || '')
  template.ruleset_level = form.ruleset_level || 'assisted'
  template.attribute_points = Number(form.attribute_points) || 0
  template.max_skills = Number(form.max_skills) || 0
  template.skill_point_total = Number(form.skill_point_total) || 0
  template.currency = form.currency || t('goldCurrency')
  if (form.currency_simple_editable) {
    const majorName = form.currency_major.trim() || form.currency || t('goldCurrency')
    const minorName = form.currency_minor.trim()
    if (form.currency_has_minor && minorName) {
      const rate = Math.max(2, Math.round(Number(form.currency_rate) || 100))
      const slug = (name: string, fallback: string) => (/^[a-z0-9_-]+$/i.test(name) ? name.toLowerCase() : fallback)
      template.currency_system = {
        schema_version: 2,
        base_unit: slug(minorName, 'minor'),
        display_unit: slug(majorName, 'major'),
        units: [
          { id: slug(majorName, 'major'), name: majorName, rate, ...(form.currency_symbol.trim() ? { symbol: form.currency_symbol.trim() } : {}) },
          { id: slug(minorName, 'minor'), name: minorName, rate: 1 },
        ],
      }
    } else {
      delete template.currency_system
    }
  }
  template.hp_formula = form.hp_formula || ''
  template.special_stats = form.special_stats.map(stat => ({ ...stat }))
  template.gm_prompt_appendix = form.gm_prompt_appendix || ''
  template.attributes = form.attributes
    .filter(a => a.key.trim())
    .map(a => ({ key: a.key.trim(), name: a.name.trim() || a.key.trim(), min: Number(a.min) || 0, max: Number(a.max) || 0 }))
  if (isDefaultCombatDraft(combatDraft.value)) delete template.combat
  else template.combat = blockFromDraft(combatDraft.value)
  if (ruleEdit.value?.mode === 'copy' || ruleEdit.value?.mode === 'new') {
    template.custom = true
    template.source_rule_id = ruleEdit.value.source_rule_id
  }
  ruleJson.value = JSON.stringify(template, null, 2)
  return template
}

function syncFormFromJson() {
  try {
    const template = jsonTemplate()
    ruleForm.value = buildForm(template)
    syncCombatDraft(template)
    toast.success(t('syncedFormFromJson'))
  }
  catch (e: unknown) { error.value = errorMessage(e) }
}

async function openRule(rule: RuleSummary) {
  try {
    const r = await api<RuleDetailResponse>(`/rules/${encodeURIComponent(rule.rule_id)}`)
    const template = r.rule || {}
    if (rule.custom) {
      ruleEdit.value = { mode: 'edit', id: rule.rule_id, name: rule.rule_name || rule.rule_id }
    } else {
      const nextId = `${rule.rule_id}_custom`
      template.rule_id = nextId
      template.rule_name = t('customRuleNameSuffix', { name: rule.rule_name || rule.rule_id })
      template.custom = true
      template.source_rule_id = rule.rule_id
      ruleEdit.value = { mode: 'copy', source_rule_id: rule.rule_id, id: nextId, name: template.rule_name }
    }
    ruleForm.value = buildForm(template)
    ruleJson.value = JSON.stringify(template, null, 2)
    syncCombatDraft(template)
    advancedJson.value = false
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function openNewRule() {
  const source = (data.value?.rules || []).find(r => !r.custom && r.rule_id === 'freeform_fantasy')
    || (data.value?.rules || []).find(r => !r.custom)
    || (data.value?.rules || [])[0]
  if (!source) { error.value = t('noRuleSkeleton'); return }
  try {
    const r = await api<RuleDetailResponse>(`/rules/${encodeURIComponent(source.rule_id)}`)
    const template = r.rule || {}
    const suffix = Date.now().toString(36)
    template.rule_id = `custom_rule_${suffix}`
    template.rule_name = t('defaultCustomRuleName')
    template.description = t('defaultCustomRuleDescription')
    template.custom = true
    template.source_rule_id = source.rule_id
    ruleEdit.value = { mode: 'new', source_rule_id: source.rule_id, id: template.rule_id, name: template.rule_name }
    ruleForm.value = buildForm(template)
    ruleJson.value = JSON.stringify(template, null, 2)
    syncCombatDraft(template)
    advancedJson.value = false
  } catch (e: unknown) { error.value = errorMessage(e) }
}
function addAttr() { ruleForm.value?.attributes.push({ key: '', name: '', min: 0, max: 100 }) }
function removeAttr(index: number) { ruleForm.value?.attributes.splice(index, 1) }

async function saveRule() {
  if (!ruleEdit.value || !ruleForm.value) return
  busy.value = true
  try {
    const template = applyFormToJson()
    if (!template.rule_id) throw new Error(t('ruleIdRequired'))
    if (!template.rule_name) throw new Error(t('ruleNameRequired'))
    if (ruleEdit.value.mode === 'copy' || ruleEdit.value.mode === 'new') {
      await mutateRule('/rules', { method: 'POST', body: JSON.stringify({
        source_rule_id: ruleEdit.value.source_rule_id,
        rule_id: template.rule_id,
        rule_name: template.rule_name,
        description: template.description || '',
      }) })
      await mutateRule(`/rules/${encodeURIComponent(template.rule_id)}`, { method: 'PUT', body: JSON.stringify(template) })
      toast.success(ruleEdit.value.mode === 'new' ? t('customRuleCreated') : t('customRuleCopiedSaved'))
    } else {
      await mutateRule(`/rules/${encodeURIComponent(ruleEdit.value.id)}`, { method: 'PUT', body: JSON.stringify(template) })
      toast.success(t('ruleUpdated'))
    }
    ruleEdit.value = null
    await load()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteRule(rule: RuleSummary) {
  const ok = await confirm({ title: t('deleteRuleTitle'), content: t('deleteRuleContent', { name: rule.rule_name || rule.rule_id }), positiveText: t('deleteRuleAction'), type: 'error' })
  if (!ok) return
  try {
    await mutateRule(`/rules/${encodeURIComponent(rule.rule_id)}`, { method: 'DELETE' })
    toast.success(t('deleted'))
    await load()
  } catch (e: unknown) { error.value = errorMessage(e) }
}
</script>

<template>
  <section class="view archive-page rules-page">
    <ContentPageHeader class="view-title archive-hero rules-page-heading" kicker="RULE COMPENDIUM" :title="t('ruleSystem')" :description="t('ruleSystemSubtitle')">
      <template #actions>
        <div class="actions">
          <button class="primary" @click="openNewRule">{{ t('newRule') }}</button>
          <button @click="load">{{ t('refresh') }}</button>
        </div>
      </template>
    </ContentPageHeader>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div class="rules-toolbar" role="search">
      <input v-model="search" type="search" :placeholder="t('rulesSearchPlaceholder')" :aria-label="t('rulesSearchPlaceholder')">
      <select v-model="kindFilter" :aria-label="t('rulesFilterLabel')">
        <option value="all">{{ t('rulesFilterAll') }}</option>
        <option value="builtin">{{ t('builtin') }}</option>
        <option value="custom">{{ t('custom') }}</option>
      </select>
      <span class="rules-count">{{ visibleRules.length }} / {{ data?.rules?.length || 0 }}</span>
    </div>

    <div v-if="(data?.rules || []).length >= 2" class="rule-warn-card">
      {{ t('multipleRulesWarning', { count: (data?.rules || []).length }) }}
    </div>

    <div class="card-grid">
      <article v-for="r in visibleRules" :key="r.rule_id" class="rule-card">
        <div>
          <h2><span>{{ localizedField<string>(r, 'rule_name') || r.rule_id }}</span><small v-if="r.custom" class="badge badge-active">{{ t('custom') }}</small><small v-else class="badge">{{ t('builtin') }}</small></h2>
          <div class="rule-meta-row">
            <span v-if="r.ruleset_runtime"><strong>{{ t('rulesRuntime') }}</strong>{{ r.ruleset_runtime.id }} · v{{ r.ruleset_runtime.version }}</span>
            <span v-else><strong>{{ t('rulesRuntime') }}</strong>{{ t('rulesTemplateRuntime') }}</span>
            <span><strong>{{ t('dice') }}</strong>{{ r.dice_system || '-' }}</span>
            <span><strong>{{ t('combat') }}</strong>{{ r.combat_model || '-' }}</span>
            <span><strong>{{ t('attributes') }}</strong>{{ r.attr_count ?? '-' }} {{ t('itemsUnit') }}</span>
            <span v-if="r.file"><strong>{{ t('file') }}</strong>{{ r.file }}</span>
          </div>
          <div class="rule-capability-row" aria-label="规则运行能力">
            <span v-for="capability in runtimeCapabilityLabels(r)" :key="capability" class="rule-capability">{{ capability }}</span>
          </div>
          <p :title="localizedField<string>(r, 'description') || ''">{{ localizedField<string>(r, 'description') || '' }}</p>
        </div>
        <div class="actions">
          <button @click="openRule(r)">{{ r.custom ? t('editRule') : t('copyAndEdit') }}</button>
          <button v-if="r.custom" class="danger" @click="deleteRule(r)" :disabled="busy">{{ t('delete') }}</button>
        </div>
      </article>
      <p v-if="data?.rules?.length && !visibleRules.length" class="muted">{{ t('rulesNoMatches') }}</p>
      <p v-else-if="data && !data.rules?.length" class="muted">{{ t('noRules') }}</p>
    </div>

    <Modal v-if="ruleEdit && ruleForm" :title="editorTitle" dialog-class="rule-edit-dialog" @close="ruleEdit = null">
      <div class="rule-editor-form">
        <div class="grid-2">
          <label>{{ t('ruleId') }}<input v-model="ruleForm.rule_id" :disabled="ruleEdit.mode === 'edit'"></label>
          <label>{{ t('ruleName') }}<input v-model="ruleForm.rule_name"></label>
        </div>
        <label>{{ t('description') }}<textarea rows="3" v-model="ruleForm.description"></textarea></label>
        <div class="grid-2">
          <label>{{ t('diceSystem') }}<select v-model="ruleForm.dice_system"><option value="d20">d20</option><option value="d100">d100</option><option value="none">{{ t('noDice') }}</option></select></label>
          <label>{{ t('combatModel') }}<input v-model="ruleForm.combat_model" placeholder="hp_based / lethal_narrative"></label>
          <label>{{ t('mechanics') }}<input v-model="ruleForm.mechanics"></label>
          <label>{{ t('rulesetLevel') }}<input v-model="ruleForm.ruleset_level"></label>
          <label>{{ t('attributePointTotal') }}<input type="number" v-model.number="ruleForm.attribute_points"></label>
          <label>{{ t('currencyName') }}<input v-model="ruleForm.currency" :disabled="!ruleForm.currency_simple_editable"></label>
          <template v-if="ruleForm.currency_simple_editable">
            <label class="currency-toggle">
              <input type="checkbox" v-model="ruleForm.currency_has_minor">
              {{ t('currencyHasMinor') }}
            </label>
          </template>
          <p v-else class="form-hint">{{ t('currencyAdvancedNote') }}</p>
          <template v-if="ruleForm.currency_simple_editable && ruleForm.currency_has_minor">
            <label>{{ t('currencyMajorUnit') }}<input v-model="ruleForm.currency_major" :placeholder="ruleForm.currency"></label>
            <label>{{ t('currencyMinorUnit') }}<input v-model="ruleForm.currency_minor"></label>
            <label>{{ t('currencyRateLabel') }}<input type="number" v-model.number="ruleForm.currency_rate" min="2"></label>
            <label>{{ t('currencySymbolLabel') }}<input v-model="ruleForm.currency_symbol" maxlength="4"></label>
          </template>
          <label>{{ t('maxSkills') }}<input type="number" v-model.number="ruleForm.max_skills"></label>
          <label>{{ t('skillPointTotal') }}<input type="number" v-model.number="ruleForm.skill_point_total"></label>
        </div>
        <label>HP {{ t('formula') }}<input v-model="ruleForm.hp_formula" :placeholder="t('hpFormulaPlaceholder')"></label>

        <div class="rule-editor-section">
          <div class="section-head"><strong>{{ t('attributes') }}</strong><button type="button" @click="addAttr">+ {{ t('attributes') }}</button></div>
          <div v-for="(a, i) in ruleForm.attributes" :key="i" class="rule-attr-edit">
            <input v-model="a.key" placeholder="key">
            <input v-model="a.name" :placeholder="t('name')">
            <input type="number" v-model.number="a.min" placeholder="min">
            <input type="number" v-model.number="a.max" placeholder="max">
            <button type="button" class="danger" @click="removeAttr(i)">{{ t('delete') }}</button>
          </div>
        </div>

        <RuleResourcesEditor v-model="ruleForm.special_stats" />
        <label>{{ t('gmRulePrompt') }}<textarea rows="5" v-model="ruleForm.gm_prompt_appendix"></textarea></label>
        <CombatExtensionEditor
          v-model="combatDraft"
          :resource-options="combatResourceOptions"
        />
        <details class="rule-json-box" :open="advancedJson" @toggle="advancedJson = ($event.target as HTMLDetailsElement).open">
          <summary>{{ t('advancedJson') }}</summary>
          <div class="actions attr-actions">
            <button type="button" @click="applyFormToJson">{{ t('writeFormToJson') }}</button>
            <button type="button" @click="syncFormFromJson">{{ t('syncJsonToForm') }}</button>
          </div>
          <textarea rows="16" v-model="ruleJson"></textarea>
        </details>
      </div>
      <template #actions>
        <button @click="ruleEdit = null">{{ t('cancel') }}</button>
        <button class="primary" :disabled="busy" @click="saveRule">{{ t('saveRule') }}</button>
      </template>
    </Modal>
  </section>
</template>

<style scoped>
.rules-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: .65rem; margin: 1rem 0; }
.rules-toolbar input { flex: 1 1 16rem; min-width: 0; }
.rules-toolbar select { min-height: 44px; }
.rules-count { color: var(--df-text-muted); font-size: .85rem; }
.rule-capability-row { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .65rem; }
.rule-capability { border: 1px solid var(--df-border-soft); border-radius: 999px; padding: .2rem .55rem; color: var(--df-text-muted); font-size: .75rem; }
@media (max-width: 480px) {
  .rules-toolbar input, .rules-toolbar select { width: 100%; }
  .rules-count { width: 100%; }
}
</style>
