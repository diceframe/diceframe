<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useFuCopy } from './copy'

type Kind = 'monsters' | 'items' | 'skills'
type Draft = Record<string, any>
interface Row { id: string; name: string; enabled?: boolean; [key: string]: unknown }

const kind = ref<Kind>('monsters')
const rows = ref<Row[]>([])
const loading = ref(false)
const error = ref('')
const notice = ref('')
const draft = ref<Draft | null>(null)
const advancedText = ref('')
const isNew = ref(false)
const { t } = useFuCopy()

const tabs = computed<Array<{ id: Kind; label: string }>>(() => [
  { id: 'monsters', label: t('dataTabMonsters') },
  { id: 'items', label: t('dataTabItems') },
  { id: 'skills', label: t('dataTabSkills') },
])
const kindLabel = computed(() => tabs.value.find(tab => tab.id === kind.value)?.label || kind.value)
const attributeOptions = computed(() => [
  { id: 'dex', label: t('attrDex') }, { id: 'ins', label: t('attrIns') },
  { id: 'mig', label: t('attrMig') }, { id: 'wlp', label: t('attrWlp') },
])
const damageTypes = ['physical', 'fire', 'ice', 'lightning', 'light', 'dark', 'poison']

function newTemplate(kindValue: Kind): Draft {
  if (kindValue === 'monsters') {
    return {
      id: 'new_monster', name: t('defaultMonsterName'), enabled: true, rank: 'minion',
      hp: 20, defense: 10, mdef: 10, initiative: 8,
      villain: false, fate: 0, attacks: [], skills: [], affinities: {},
    }
  }
  if (kindValue === 'items') {
    return {
      id: 'new_item', name: t('defaultItemName'), enabled: true, category: 'common',
      description: '', price: 0,
    }
  }
  return {
    id: 'new_skill', name: t('defaultSkillName'), enabled: true, class: 'custom', kind: 'active',
    mp: 0, hp: 0, target: { scope: 'enemy', count: 1 }, check: { attributes: ['mig', 'mig'], dl: 'defense' },
    effects: [], text: '',
  }
}

function normalize(value: Draft, kindValue: Kind) {
  if (!Array.isArray(value.attacks)) value.attacks = []
  if (!Array.isArray(value.skills)) value.skills = []
  for (const attack of value.attacks) {
    if (!Array.isArray(attack.attributes) || attack.attributes.length !== 2) attack.attributes = ['mig', 'mig']
    if (attack.damage == null) attack.damage = 0
  }
  for (const skill of value.skills) {
    if (!skill.scope) skill.scope = 'single'
    if (!skill.damage_type) skill.damage_type = 'physical'
    if (skill.bonus == null) skill.bonus = 0
    if (skill.cooldown == null) skill.cooldown = 3
  }
  if (!value.affinities || typeof value.affinities !== 'object') value.affinities = {}
  if (!value.target || typeof value.target !== 'object') value.target = { scope: 'enemy', count: 1 }
  if (!value.check || typeof value.check !== 'object') value.check = { attributes: ['mig', 'mig'], dl: 'defense' }
  if (!Array.isArray(value.effects)) value.effects = []
  if (kindValue === 'items') {
    if (!value.effect || typeof value.effect !== 'object') value.effect = {}
    if (!value.category) value.category = 'common'
  }
}

function advancedSource(value: Draft): string {
  if (kind.value === 'monsters') return JSON.stringify(value.affinities || {}, null, 2)
  if (kind.value === 'skills') return JSON.stringify(value.effects || [], null, 2)
  return ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await api<{ ok?: boolean; entries?: Row[]; error?: string }>(`/fu-content/${kind.value}`)
    rows.value = response.entries || []
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
    rows.value = []
  } finally {
    loading.value = false
  }
}

function switchKind(next: Kind) {
  kind.value = next
  draft.value = null
  void load()
}

function edit(row: Row) {
  isNew.value = false
  const copy = JSON.parse(JSON.stringify(row)) as Draft
  normalize(copy, kind.value)
  draft.value = copy
  advancedText.value = advancedSource(copy)
}

function create() {
  isNew.value = true
  const value = newTemplate(kind.value)
  normalize(value, kind.value)
  draft.value = value
  advancedText.value = advancedSource(value)
}

function close() {
  draft.value = null
  advancedText.value = ''
}

function addAttack() {
  if (!draft.value) return
  draft.value.attacks.push({ name: t('actionAttack'), attributes: ['mig', 'mig'], damage: 5 })
}

function addMonsterSkill() {
  if (!draft.value) return
  draft.value.skills.push({ name: t('skill'), scope: 'single', damage_type: 'physical', bonus: 5, cooldown: 3 })
}

function addEffect() {
  if (!draft.value) return
  draft.value.effects.push({ type: 'damage', amount: 5 })
}

function removeAt(list: any[], index: number) {
  list.splice(index, 1)
}

async function save() {
  if (!draft.value) return
  error.value = ''
  notice.value = ''
  const payload = JSON.parse(JSON.stringify(draft.value)) as Draft
  try {
    if (kind.value === 'monsters') {
      payload.affinities = advancedText.value.trim() ? JSON.parse(advancedText.value) : {}
      payload.hp = Number(payload.hp) || 0
      payload.defense = Number(payload.defense) || 0
      payload.mdef = Number(payload.mdef) || 0
      payload.initiative = Number(payload.initiative) || 0
      for (const attack of payload.attacks) {
        attack.damage = Number(attack.damage) || 0
        if (!Array.isArray(attack.attributes) || attack.attributes.length !== 2) attack.attributes = ['mig', 'mig']
      }
      for (const skill of payload.skills) {
        skill.bonus = Number(skill.bonus) || 0
        skill.cooldown = Number(skill.cooldown) || 3
      }
    } else if (kind.value === 'items') {
      payload.price = Number(payload.price) || 0
      if (payload.category === 'consumable') {
        payload.ip_cost = Number(payload.ip_cost) || 0
      }
    } else {
      payload.mp = Number(payload.mp) || 0
      payload.hp = Number(payload.hp) || 0
      payload.target.count = Number(payload.target.count) || 1
      payload.effects = advancedText.value.trim() ? JSON.parse(advancedText.value) : []
    }
  } catch (e: unknown) {
    error.value = t('invalidAdvancedJson', { error: e instanceof Error ? e.message : String(e) })
    return
  }
  const targetId = String(payload.id || '').trim()
  if (!targetId) {
    error.value = t('idRequired')
    return
  }
  try {
    await api(`/fu-content/${kind.value}/${encodeURIComponent(targetId)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
    notice.value = t('saved', { name: payload.name || payload.id })
    close()
    await load()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function toggle(row: Row) {
  try {
    await api(`/fu-content/${kind.value}/${encodeURIComponent(row.id)}`, {
      method: 'PUT',
      body: JSON.stringify({ ...row, enabled: !row.enabled }),
    })
    await load()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function remove(row: Row) {
  if (!window.confirm(t('confirmDeleteEntry', { name: row.name || row.id }))) return
  try {
    await api(`/fu-content/${kind.value}/${encodeURIComponent(row.id)}`, { method: 'DELETE' })
    notice.value = t('deleted', { name: row.name || row.id })
    await load()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(load)
</script>

<template>
  <section class="fu-data-page">
    <header class="fu-data-head">
      <div>
        <p class="eyebrow">FABULA ULTIMA DATA</p>
        <h1>{{ t('dataTitle') }}</h1>
        <p class="muted">{{ t('dataIntro') }}</p>
      </div>
    </header>

    <nav class="fu-data-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        type="button"
        :class="{ active: kind === tab.id }"
        @click="switchKind(tab.id)"
      >{{ tab.label }}</button>
    </nav>

    <div class="fu-data-toolbar">
      <strong>{{ kindLabel }}</strong>
      <span class="muted">{{ t('entryCount', { count: rows.length }) }}</span>
      <button type="button" class="primary" @click="create">{{ t('newEntry') }}</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>
    <p v-if="loading" class="muted">{{ t('loading') }}</p>

    <div v-if="!loading && rows.length" class="fu-data-table">
      <div class="fu-data-row head">
        <span>{{ t('enabled') }}</span><span>ID</span><span>{{ t('name') }}</span><span>{{ t('operations') }}</span>
      </div>
      <div v-for="row in rows" :key="row.id" class="fu-data-row">
        <label><input type="checkbox" :checked="row.enabled !== false" @change="toggle(row)"></label>
        <code>{{ row.id }}</code>
        <span>{{ row.name }}</span>
        <span class="actions">
          <button type="button" @click="edit(row)">{{ t('edit') }}</button>
          <button type="button" class="danger" @click="remove(row)">{{ t('delete') }}</button>
        </span>
      </div>
    </div>
    <p v-else-if="!loading" class="muted">{{ t('emptyTable') }}</p>

    <div v-if="draft" class="fu-data-overlay" @click.self="close">
      <div class="fu-data-editor">
        <div class="fu-data-toolbar">
          <strong>{{ t(isNew ? 'editorNew' : 'editorEdit', { kind: kindLabel }) }}</strong>
          <button type="button" @click="close">{{ t('close') }}</button>
        </div>

        <div class="form-grid">
          <label class="field">
            <span>{{ t('idLocked') }}</span>
            <input v-model="draft.id" :disabled="!isNew" type="text">
          </label>
          <label class="field">
            <span>{{ t('name') }}</span>
            <input v-model="draft.name" type="text">
          </label>
          <label class="field switch-field">
            <span>{{ t('enabled') }}</span>
            <input v-model="draft.enabled" type="checkbox">
          </label>
        </div>

        <template v-if="kind === 'monsters'">
          <div class="form-grid">
            <label class="field">
              <span>{{ t('rank') }}</span>
              <select v-model="draft.rank">
                <option value="minion">{{ t('rankMinion') }}</option>
                <option value="elite">{{ t('rankElite') }}</option>
                <option value="boss">Boss</option>
              </select>
            </label>
            <label class="field"><span>HP</span><input v-model.number="draft.hp" type="number" min="1"></label>
            <label class="field"><span>{{ t('defense') }}</span><input v-model.number="draft.defense" type="number" min="0"></label>
            <label class="field"><span>{{ t('magicDefense') }}</span><input v-model.number="draft.mdef" type="number" min="0"></label>
            <label class="field"><span>{{ t('initiative') }}</span><input v-model.number="draft.initiative" type="number" min="0"></label>
            <label class="field switch-field"><span>{{ t('villain') }}</span><input v-model="draft.villain" type="checkbox"></label>
            <label v-if="draft.villain" class="field"><span>{{ t('ultimaPoints') }}</span><input v-model.number="draft.fate" type="number" min="0"></label>
          </div>

          <div class="section-head"><strong>{{ t('attacksHeading') }}</strong><button type="button" @click="addAttack">{{ t('addAttack') }}</button></div>
          <div v-for="(attack, index) in draft.attacks" :key="'a' + index" class="sub-row">
            <input v-model="attack.name" type="text" :placeholder="t('name')">
            <select v-model="attack.attributes[0]">
              <option v-for="attr in attributeOptions" :key="attr.id" :value="attr.id">{{ attr.label }}</option>
            </select>
            <select v-model="attack.attributes[1]">
              <option v-for="attr in attributeOptions" :key="attr.id" :value="attr.id">{{ attr.label }}</option>
            </select>
            <input v-model.number="attack.damage" type="number" min="0" :placeholder="t('damage')">
            <button type="button" class="danger" @click="removeAt(draft.attacks, index)">{{ t('delete') }}</button>
          </div>

          <div class="section-head"><strong>{{ t('skillsHeading') }}</strong><button type="button" @click="addMonsterSkill">{{ t('addSkill') }}</button></div>
          <div v-for="(skill, index) in draft.skills" :key="'s' + index" class="sub-row sub-row-skill">
            <input v-model="skill.name" type="text" :placeholder="t('skillName')">
            <select v-model="skill.scope">
              <option value="single">{{ t('scopeSingle') }}</option>
              <option value="all_players">{{ t('scopeAll') }}</option>
              <option value="self">{{ t('scopeSelf') }}</option>
            </select>
            <select v-model="skill.damage_type">
              <option v-for="type in damageTypes" :key="type" :value="type">{{ type }}</option>
            </select>
            <input v-model.number="skill.bonus" type="number" min="0" :placeholder="t('bonus')">
            <input v-model.number="skill.cooldown" type="number" min="1" :placeholder="t('cooldown')">
            <input v-model="skill.status" type="text" :placeholder="t('statusOptional')">
            <button type="button" class="danger" @click="removeAt(draft.skills, index)">{{ t('delete') }}</button>
          </div>
        </template>

        <template v-else-if="kind === 'items'">
          <div class="form-grid">
            <label class="field">
              <span>{{ t('category') }}</span>
              <select v-model="draft.category">
                <option value="common">{{ t('categoryCommon') }}</option>
                <option value="consumable">{{ t('categoryConsumable') }}</option>
                <option value="equipment">{{ t('equipment') }}</option>
              </select>
            </label>
            <label class="field"><span>{{ t('price') }}</span><input v-model.number="draft.price" type="number" min="0"></label>
            <label class="field field-wide"><span>{{ t('description') }}</span><textarea v-model="draft.description" rows="3" /></label>
          </div>

          <template v-if="draft.category === 'consumable'">
            <div class="form-grid">
              <label class="field"><span>{{ t('ipCost') }}</span><input v-model.number="draft.ip_cost" type="number" min="0"></label>
              <label class="field">
                <span>{{ t('actionType') }}</span>
                <select v-model="draft.action">
                  <option value="action">{{ t('mainAction') }}</option>
                  <option value="quick">{{ t('quickAction') }}</option>
                </select>
              </label>
              <label class="field">
                <span>{{ t('effect') }}</span>
                <select v-model="draft.effect.type">
                  <option value="heal">{{ t('effectHeal') }}</option>
                  <option value="cleanse">{{ t('effectCleanseOption') }}</option>
                  <option value="damage">{{ t('effectDamage') }}</option>
                  <option value="status">{{ t('effectStatus') }}</option>
                </select>
              </label>
              <label class="field"><span>{{ t('value') }}</span><input v-model.number="draft.effect.amount" type="number" min="0"></label>
              <label class="field"><span>{{ t('target') }}</span><input v-model="draft.effect.target" type="text" placeholder="ally / self"></label>
              <label class="field"><span>{{ t('damageType') }}</span><input v-model="draft.effect.damage_type" type="text" placeholder="physical"></label>
            </div>
          </template>

          <template v-if="draft.category === 'equipment'">
            <div class="form-grid">
              <label class="field">
                <span>{{ t('equipmentType') }}</span>
                <select v-model="draft.equipment_type">
                  <option value="weapon">{{ t('weapon') }}</option>
                  <option value="armor">{{ t('armor') }}</option>
                  <option value="shield">{{ t('shield') }}</option>
                </select>
              </label>
              <label class="field"><span>{{ t('weaponCategory') }}</span><input v-model="draft.weapon_category" type="text" placeholder="sword / axe"></label>
              <label class="field"><span>{{ t('hands') }}</span><input v-model.number="draft.hands" type="number" min="1" max="2"></label>
              <label class="field"><span>{{ t('range') }}</span><input v-model="draft.range" type="text" placeholder="melee / ranged"></label>
              <label class="field"><span>{{ t('damage') }}</span><input v-model.number="draft.damage" type="number" min="0"></label>
              <label class="field"><span>{{ t('accuracy') }}</span><input v-model.number="draft.accuracy" type="number"></label>
              <label class="field"><span>{{ t('defense') }}</span><input v-model.number="draft.armor" type="number" min="0"></label>
              <label class="field"><span>{{ t('magicDefense') }}</span><input v-model.number="draft.mdef" type="number" min="0"></label>
              <label class="field"><span>{{ t('initiativeModifier') }}</span><input v-model.number="draft.initiative" type="number"></label>
            </div>
          </template>
        </template>

        <template v-else>
          <div class="form-grid">
            <label class="field"><span>{{ t('className') }}</span><input v-model="draft.class" type="text" placeholder="weaponmaster"></label>
            <label class="field">
              <span>{{ t('skillKind') }}</span>
              <select v-model="draft.kind">
                <option value="active">{{ t('kindActive') }}</option>
                <option value="quick">{{ t('kindQuick') }}</option>
                <option value="passive">{{ t('kindPassive') }}</option>
                <option value="reaction">{{ t('kindReaction') }}</option>
                <option value="spell">{{ t('kindSpell') }}</option>
              </select>
            </label>
            <label class="field"><span>MP</span><input v-model.number="draft.mp" type="number" min="0"></label>
            <label class="field"><span>HP</span><input v-model.number="draft.hp" type="number" min="0"></label>
            <label class="field">
              <span>{{ t('targetScope') }}</span>
              <select v-model="draft.target.scope">
                <option value="enemy">{{ t('scopeEnemy') }}</option>
                <option value="enemies">{{ t('scopeEnemies') }}</option>
                <option value="ally">{{ t('scopeAlly') }}</option>
                <option value="allies">{{ t('scopeAllies') }}</option>
                <option value="self">{{ t('scopeSelf') }}</option>
              </select>
            </label>
            <label class="field"><span>{{ t('targetCount') }}</span><input v-model.number="draft.target.count" type="number" min="1"></label>
            <label class="field">
              <span>{{ t('firstAttribute') }}</span>
              <select v-model="draft.check.attributes[0]">
                <option v-for="attr in attributeOptions" :key="attr.id" :value="attr.id">{{ attr.label }}</option>
              </select>
            </label>
            <label class="field">
              <span>{{ t('secondAttribute') }}</span>
              <select v-model="draft.check.attributes[1]">
                <option v-for="attr in attributeOptions" :key="attr.id" :value="attr.id">{{ attr.label }}</option>
              </select>
            </label>
            <label class="field">
              <span>{{ t('versus') }}</span>
              <select v-model="draft.check.dl">
                <option value="defense">{{ t('defense') }}</option>
                <option value="mdef">{{ t('magicDefense') }}</option>
              </select>
            </label>
            <label class="field field-wide"><span>{{ t('description') }}</span><textarea v-model="draft.text" rows="3" /></label>
          </div>
          <div class="section-head"><strong>{{ t('effectsHeading') }}</strong><button type="button" @click="addEffect">{{ t('addEffect') }}</button></div>
          <div v-for="(effect, index) in draft.effects" :key="'e' + index" class="sub-row">
            <input v-model="effect.type" type="text" placeholder="type">
            <input v-model.number="effect.amount" type="number" min="0" :placeholder="t('value')">
            <input v-model="effect.target" type="text" :placeholder="t('targetOptional')">
            <button type="button" class="danger" @click="removeAt(draft.effects, index)">{{ t('delete') }}</button>
          </div>
        </template>

        <details v-if="kind !== 'items'" class="advanced-box">
          <summary>{{ kind === 'monsters' ? t('advancedAffinities') : t('advancedEffects') }}</summary>
          <textarea v-model="advancedText" rows="8" spellcheck="false" />
        </details>

        <div class="fu-data-toolbar editor-actions">
          <button type="button" class="primary" @click="save">{{ t('save') }}</button>
          <button type="button" @click="close">{{ t('cancel') }}</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.fu-data-page { max-width: 1100px; margin: 0 auto; padding: 28px 24px 60px; }
.fu-data-head h1 { margin: 4px 0 8px; }
.eyebrow { color: var(--df-text-muted); font-size: 11px; letter-spacing: .14em; }
.fu-data-tabs { display: flex; gap: 8px; margin: 20px 0 16px; flex-wrap: wrap; }
.fu-data-tabs button { border: 1px solid var(--df-border-soft, #333); background: transparent; color: inherit; padding: 8px 14px; border-radius: 8px; cursor: pointer; }
.fu-data-tabs button.active { border-color: var(--df-accent, #7aa2f7); color: var(--df-accent, #7aa2f7); }
.fu-data-toolbar { display: flex; align-items: center; gap: 12px; margin: 12px 0; }
.fu-data-toolbar .primary { margin-left: auto; }
.fu-data-toolbar button, .fu-data-row button, .section-head button { border: 1px solid var(--df-border-soft, #333); background: transparent; color: inherit; padding: 5px 10px; border-radius: 6px; cursor: pointer; }
.fu-data-toolbar button.primary { border-color: var(--df-accent, #7aa2f7); color: var(--df-accent, #7aa2f7); }
.fu-data-table { border: 1px solid var(--df-border-soft, #333); border-radius: 8px; overflow: hidden; }
.fu-data-row { display: grid; grid-template-columns: 50px 1.2fr 1fr auto; gap: 10px; align-items: center; padding: 8px 12px; border-bottom: 1px solid var(--df-border-soft, #222); }
.fu-data-row:last-child { border-bottom: 0; }
.fu-data-row.head { color: var(--df-text-muted); font-size: 11px; background: rgba(127,127,127,.08); }
.fu-data-row code { font-size: 12px; opacity: .9; }
.fu-data-row .actions { display: flex; gap: 6px; }
.fu-data-row button.danger, .danger { color: #e05555; }
.fu-data-overlay { position: fixed; inset: 0; z-index: 1200; display: flex; align-items: center; justify-content: center; padding: 24px; background: rgba(0, 0, 0, .62); backdrop-filter: blur(2px); }
.fu-data-editor { width: min(900px, 100%); max-height: 90vh; overflow: auto; padding: 16px 18px 18px; border: 1px solid var(--df-border-soft, #333); border-radius: 10px; background: var(--df-bg-panel, #171b22); box-shadow: 0 24px 70px rgba(0,0,0,.45); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; margin: 10px 0; }
.field { display: grid; gap: 5px; font-size: 12px; }
.field-wide { grid-column: 1 / -1; }
.field input, .field select, .field textarea, .sub-row input, .sub-row select, .advanced-box textarea { width: 100%; background: rgba(0,0,0,.18); color: inherit; border: 1px solid var(--df-border-soft, #333); border-radius: 6px; padding: 7px 8px; font-size: 12px; font-family: inherit; }
.field textarea, .advanced-box textarea { resize: vertical; font-family: ui-monospace, monospace; }
.switch-field { align-content: start; }
.switch-field input { width: auto; justify-self: start; }
.section-head { display: flex; align-items: center; justify-content: space-between; margin: 16px 0 8px; }
.sub-row { display: grid; grid-template-columns: 1.4fr .9fr .9fr .7fr auto; gap: 8px; align-items: center; margin-bottom: 6px; }
.sub-row-skill { grid-template-columns: 1.3fr .8fr .8fr .7fr .6fr 1fr auto; }
.advanced-box { margin: 14px 0; border: 1px solid var(--df-border-soft, #333); border-radius: 6px; padding: 8px 10px; }
.advanced-box summary { cursor: pointer; font-size: 12px; color: var(--df-text-muted); }
.advanced-box textarea { margin-top: 8px; }
.editor-actions { justify-content: flex-end; margin-top: 16px; }
.error { color: #e05555; }
.notice { color: #7aa2f7; }
@media (max-width: 720px) {
  .form-grid { grid-template-columns: 1fr; }
  .sub-row, .sub-row-skill { grid-template-columns: 1fr 1fr; }
}
</style>
