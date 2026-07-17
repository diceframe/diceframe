<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { RuleAttributeEdit, RuleDetailResponse, RuleEditorState, RuleForm, RulesResponse, RuleSummary, RuleTemplate } from '@/api/types'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import Modal from '@/components/ui/Modal.vue'

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

const editorTitle = computed(() => {
  if (!ruleEdit.value) return t('ruleEditor')
  if (ruleEdit.value.mode === 'new') return t('newCustomRule')
  if (ruleEdit.value.mode === 'copy') return t('copyAndEditRule')
  return t('editCustomRule')
})

function errorMessage(err: unknown): string { return err instanceof Error ? err.message : String(err || t('operationFailed')) }

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
  return {
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
    hp_formula: String(template.hp_formula || ''),
    gm_prompt_appendix: String(template.gm_prompt_appendix || ''),
    attributes: (template.attributes || []).map(toRuleAttr),
  }
}

function jsonTemplate(): RuleTemplate {
  try { return JSON.parse(ruleJson.value || '{}') as RuleTemplate }
  catch { throw new Error(t('ruleJsonInvalid')) }
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
  template.hp_formula = form.hp_formula || ''
  template.gm_prompt_appendix = form.gm_prompt_appendix || ''
  template.attributes = form.attributes
    .filter(a => a.key.trim())
    .map(a => ({ key: a.key.trim(), name: a.name.trim() || a.key.trim(), min: Number(a.min) || 0, max: Number(a.max) || 0 }))
  if (ruleEdit.value?.mode === 'copy' || ruleEdit.value?.mode === 'new') {
    template.custom = true
    template.source_rule_id = ruleEdit.value.source_rule_id
  }
  ruleJson.value = JSON.stringify(template, null, 2)
  return template
}

function syncFormFromJson() {
  try { ruleForm.value = buildForm(jsonTemplate()); toast.success(t('syncedFormFromJson')) }
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
      await api('/rules', { method: 'POST', body: JSON.stringify({
        source_rule_id: ruleEdit.value.source_rule_id,
        rule_id: template.rule_id,
        rule_name: template.rule_name,
        description: template.description || '',
      }) })
      await api(`/rules/${encodeURIComponent(template.rule_id)}`, { method: 'PUT', body: JSON.stringify(template) })
      toast.success(ruleEdit.value.mode === 'new' ? t('customRuleCreated') : t('customRuleCopiedSaved'))
    } else {
      await api(`/rules/${encodeURIComponent(ruleEdit.value.id)}`, { method: 'PUT', body: JSON.stringify(template) })
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
    await api(`/rules/${encodeURIComponent(rule.rule_id)}`, { method: 'DELETE' })
    toast.success(t('deleted'))
    await load()
  } catch (e: unknown) { error.value = errorMessage(e) }
}
</script>

<template>
  <section class="view archive-page rules-page">
    <header class="view-title archive-hero">
      <div>
        <h1>{{ t('ruleSystem') }}</h1>
        <p class="muted">{{ t('ruleSystemSubtitle') }}</p>
      </div>
      <div class="actions">
        <button class="primary" @click="openNewRule">{{ t('newRule') }}</button>
        <button @click="load">{{ t('refresh') }}</button>
      </div>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div v-if="(data?.rules || []).length >= 2" class="rule-warn-card">
      {{ t('multipleRulesWarning', { count: (data?.rules || []).length }) }}
    </div>

    <div class="card-grid">
      <article v-for="r in data?.rules || []" :key="r.rule_id" class="rule-card">
        <div>
          <h2>{{ r.rule_name || r.rule_id }}<small v-if="r.custom" class="badge badge-active">{{ t('custom') }}</small><small v-else class="badge">{{ t('builtin') }}</small></h2>
          <div class="rule-meta-row">
            <span><strong>{{ t('dice') }}</strong>{{ r.dice_system || '-' }}</span>
            <span><strong>{{ t('combat') }}</strong>{{ r.combat_model || '-' }}</span>
            <span><strong>{{ t('attributes') }}</strong>{{ r.attr_count ?? '-' }} {{ t('itemsUnit') }}</span>
            <span v-if="r.file"><strong>{{ t('file') }}</strong>{{ r.file }}</span>
          </div>
          <p>{{ r.description || '' }}</p>
        </div>
        <div class="actions">
          <button @click="openRule(r)">{{ r.custom ? t('editRule') : t('copyAndEdit') }}</button>
          <button v-if="r.custom" class="danger" @click="deleteRule(r)" :disabled="busy">{{ t('delete') }}</button>
        </div>
      </article>
      <p v-if="!data?.rules?.length" class="muted">{{ t('noRules') }}</p>
    </div>

    <Modal v-if="ruleEdit && ruleForm" :title="editorTitle" @close="ruleEdit = null">
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
          <label>{{ t('currencyName') }}<input v-model="ruleForm.currency"></label>
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

        <label>{{ t('gmRulePrompt') }}<textarea rows="5" v-model="ruleForm.gm_prompt_appendix"></textarea></label>
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
