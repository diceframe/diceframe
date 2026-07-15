<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { RuleAttributeEdit, RuleDetailResponse, RuleEditorState, RuleForm, RulesResponse, RuleSummary, RuleTemplate } from '../../api/types'
import { useToast } from '../../composables/useToast'
import { useConfirm } from '../../composables/useConfirm'
import Modal from '../../components/ui/Modal.vue'

const toast = useToast()
const { confirm } = useConfirm()

const data = ref<RulesResponse | null>(null)
const error = ref('')
const busy = ref(false)
const ruleEdit = ref<RuleEditorState | null>(null)
const ruleForm = ref<RuleForm | null>(null)
const ruleJson = ref('')
const advancedJson = ref(false)

const editorTitle = computed(() => {
  if (!ruleEdit.value) return '规则编辑器'
  if (ruleEdit.value.mode === 'new') return '新建自定义规则'
  if (ruleEdit.value.mode === 'copy') return '复制并编辑规则'
  return '编辑自定义规则'
})

function errorMessage(err: unknown): string { return err instanceof Error ? err.message : String(err || '操作失败') }

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
    currency: String(template.currency || '金币'),
    hp_formula: String(template.hp_formula || ''),
    gm_prompt_appendix: String(template.gm_prompt_appendix || ''),
    attributes: (template.attributes || []).map(toRuleAttr),
  }
}

function jsonTemplate(): RuleTemplate {
  try { return JSON.parse(ruleJson.value || '{}') as RuleTemplate }
  catch { throw new Error('规则 JSON 格式错误，请检查逗号、引号和括号') }
}

function applyFormToJson(): RuleTemplate {
  if (!ruleForm.value) throw new Error('规则表单尚未初始化')
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
  template.currency = form.currency || '金币'
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
  try { ruleForm.value = buildForm(jsonTemplate()); toast.success('已从 JSON 同步表单') }
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
      template.rule_name = `${rule.rule_name || rule.rule_id}（自定义）`
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
  if (!source) { error.value = '暂无可作为骨架的规则，请先确认 templates/rules 中至少有一套规则。'; return }
  try {
    const r = await api<RuleDetailResponse>(`/rules/${encodeURIComponent(source.rule_id)}`)
    const template = r.rule || {}
    const suffix = Date.now().toString(36)
    template.rule_id = `custom_rule_${suffix}`
    template.rule_name = '新的自定义规则'
    template.description = '从通用规则骨架新建，可调整骰子、属性、技能、资源与 GM 提示。'
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
    if (!template.rule_id) throw new Error('规则 ID 不能为空')
    if (!template.rule_name) throw new Error('规则名称不能为空')
    if (ruleEdit.value.mode === 'copy' || ruleEdit.value.mode === 'new') {
      await api('/rules', { method: 'POST', body: JSON.stringify({
        source_rule_id: ruleEdit.value.source_rule_id,
        rule_id: template.rule_id,
        rule_name: template.rule_name,
        description: template.description || '',
      }) })
      await api(`/rules/${encodeURIComponent(template.rule_id)}`, { method: 'PUT', body: JSON.stringify(template) })
      toast.success(ruleEdit.value.mode === 'new' ? '已新建自定义规则' : '已复制并保存自定义规则')
    } else {
      await api(`/rules/${encodeURIComponent(ruleEdit.value.id)}`, { method: 'PUT', body: JSON.stringify(template) })
      toast.success('规则已更新')
    }
    ruleEdit.value = null
    await load()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteRule(rule: RuleSummary) {
  const ok = await confirm({ title: '删除规则', content: `确定删除自定义规则「${rule.rule_name || rule.rule_id}」吗？此操作不可撤销。`, positiveText: '删除规则', type: 'error' })
  if (!ok) return
  try {
    await api(`/rules/${encodeURIComponent(rule.rule_id)}`, { method: 'DELETE' })
    toast.success('已删除')
    await load()
  } catch (e: unknown) { error.value = errorMessage(e) }
}
</script>

<template>
  <section class="view archive-page rules-page">
    <header class="view-title archive-hero">
      <div>
        <h1>规则系统</h1>
        <p class="muted">内置规则可复制为自定义规则；自定义规则可编辑、测试和删除。</p>
      </div>
      <div class="actions">
        <button class="primary" @click="openNewRule">新建规则</button>
        <button @click="load">刷新</button>
      </div>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div v-if="(data?.rules || []).length >= 2" class="rule-warn-card">
      当前存在 {{ (data?.rules || []).length }} 套规则。游戏创建后规则锁定，切换规则需要重建游戏。
    </div>

    <div class="card-grid">
      <article v-for="r in data?.rules || []" :key="r.rule_id" class="rule-card">
        <div>
          <h2>{{ r.rule_name || r.rule_id }}<small v-if="r.custom" class="badge badge-active">自定义</small><small v-else class="badge">内置</small></h2>
          <div class="rule-meta-row">
            <span><strong>骰子</strong>{{ r.dice_system || '—' }}</span>
            <span><strong>战斗</strong>{{ r.combat_model || '—' }}</span>
            <span><strong>属性</strong>{{ r.attr_count ?? '—' }} 项</span>
            <span v-if="r.file"><strong>文件</strong>{{ r.file }}</span>
          </div>
          <p>{{ r.description || '' }}</p>
        </div>
        <div class="actions">
          <button @click="openRule(r)">{{ r.custom ? '编辑规则' : '复制并编辑' }}</button>
          <button v-if="r.custom" class="danger" @click="deleteRule(r)" :disabled="busy">删除</button>
        </div>
      </article>
      <p v-if="!data?.rules?.length" class="muted">暂无规则。</p>
    </div>

    <Modal v-if="ruleEdit && ruleForm" :title="editorTitle" @close="ruleEdit = null">
      <div class="rule-editor-form">
        <div class="grid-2">
          <label>规则 ID<input v-model="ruleForm.rule_id" :disabled="ruleEdit.mode === 'edit'"></label>
          <label>规则名称<input v-model="ruleForm.rule_name"></label>
        </div>
        <label>说明<textarea rows="3" v-model="ruleForm.description"></textarea></label>
        <div class="grid-2">
          <label>骰子系统<select v-model="ruleForm.dice_system"><option value="d20">d20</option><option value="d100">d100</option><option value="none">无骰子</option></select></label>
          <label>战斗模型<input v-model="ruleForm.combat_model" placeholder="hp_based / lethal_narrative"></label>
          <label>机制<input v-model="ruleForm.mechanics"></label>
          <label>规则层级<input v-model="ruleForm.ruleset_level"></label>
          <label>属性总点数<input type="number" v-model.number="ruleForm.attribute_points"></label>
          <label>货币名称<input v-model="ruleForm.currency"></label>
          <label>最大技能数<input type="number" v-model.number="ruleForm.max_skills"></label>
          <label>技能点总量<input type="number" v-model.number="ruleForm.skill_point_total"></label>
        </div>
        <label>HP 公式<input v-model="ruleForm.hp_formula" placeholder="例如 max((con + siz) // 2, 1)"></label>

        <div class="rule-editor-section">
          <div class="section-head"><strong>属性</strong><button type="button" @click="addAttr">+ 属性</button></div>
          <div v-for="(a, i) in ruleForm.attributes" :key="i" class="rule-attr-edit">
            <input v-model="a.key" placeholder="key">
            <input v-model="a.name" placeholder="名称">
            <input type="number" v-model.number="a.min" placeholder="min">
            <input type="number" v-model.number="a.max" placeholder="max">
            <button type="button" class="danger" @click="removeAttr(i)">删除</button>
          </div>
        </div>

        <label>GM 规则提示<textarea rows="5" v-model="ruleForm.gm_prompt_appendix"></textarea></label>
        <details class="rule-json-box" :open="advancedJson" @toggle="advancedJson = ($event.target as HTMLDetailsElement).open">
          <summary>高级 JSON</summary>
          <div class="actions attr-actions">
            <button type="button" @click="applyFormToJson">表单写入 JSON</button>
            <button type="button" @click="syncFormFromJson">JSON 同步表单</button>
          </div>
          <textarea rows="16" v-model="ruleJson"></textarea>
        </details>
      </div>
      <template #actions>
        <button @click="ruleEdit = null">取消</button>
        <button class="primary" :disabled="busy" @click="saveRule">保存规则</button>
      </template>
    </Modal>
  </section>
</template>