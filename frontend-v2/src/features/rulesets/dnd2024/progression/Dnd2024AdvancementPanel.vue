<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type {
  JsonObject,
  RulesetAdvancementPreview,
  RulesetSpellChoice,
} from '@/api/types'
import {
  applyCharacterCardAdvancement,
  applyLiveCharacterAdvancement,
  applyRulesetAdvancement,
  completeCharacterCardChoices,
  fetchCharacterCardPendingChoices,
  previewCharacterCardAdvancement,
  previewLiveCharacterAdvancement,
  previewRulesetAdvancement,
} from '../api'

const props = withDefaults(defineProps<{
  ruleId: string
  character: JsonObject
  language?: string
  cardId?: string
  gameKey?: string
  userId?: string
  revision?: number
}>(), { language: 'zh-CN', cardId: '', gameKey: '', userId: '', revision: 0 })
const emit = defineEmits<{ applied: [character: JsonObject]; cancel: [] }>()

const ABILITIES = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const
const choices = ref<JsonObject>({ hp_method: 'fixed' })
// 进场时的法术书大小（含抄写），用来算本级上限；不随勾选变化。
const spellbookBaseline = ref(0)
const preview = ref<RulesetAdvancementPreview | null>(null)
const busy = ref(false)
const error = ref('')
const currentRevision = ref(props.revision)
const zh = computed(() => props.language.toLowerCase().startsWith('zh'))
const text = (cn: string, en: string) => zh.value ? cn : en
const requirements = computed(() => preview.value?.requirements || [])
const diff = computed(() => (preview.value?.diff || {}) as JsonObject)
const hpDiff = computed(() => (diff.value.hp || {}) as JsonObject)
const abilityDiff = computed(() => (diff.value.abilities || {}) as JsonObject)
const slotChanges = computed(() => (diff.value.spell_slot_changes || {}) as JsonObject)

function requirement(id: string): JsonObject | null {
  return requirements.value.find(item => item.id === id) || null
}

const featRequirement = computed(() => (
  requirement('epic_boon_ref') || requirement('feat_ref')
))
const abilityRequirement = computed(() => requirement('ability_score_increases'))
const spellRequirement = computed(() => requirement('class_spell_choices'))
// 换法术的额度由内容决定（战士式的"整表重排"只属于长休）：本级能新增几个、
// 能拿掉几个旧法术，全从要求里读，面板不写死任何一个职业的数字。
const spellBudgetText = computed(() => {
  const spec = spellRequirement.value
  if (!spec) return ''
  // 老版本服务端不带额度字段：宁可不提示，也不能凭空说"新增 0 个"。
  if (spec.additions === undefined && spec.replace_limit === undefined) return ''
  const additions = Number(spec.additions || 0)
  const replace = Number(spec.replace_limit || 0)
  const book = Number(spec.spellbook_additions || 0)
  const cantrips = Number(spec.cantrip_replace_limit || 0)
  const parts = [text(`本级新增 ${additions} 个法术`, `${additions} new spell(s) at this level`)]
  parts.push(replace > 0
    ? text(`最多替换 ${replace} 个旧法术`, `up to ${replace} old spell(s) may be replaced`)
    : text('不能替换旧法术，只能新增', 'old spells cannot be replaced; only additions'))
  if (book > 0) parts.push(text(`法术书需新增 ${book} 个`, `the spellbook gains ${book}`))
  if (cantrips > 0) parts.push(text(`戏法可替换 ${cantrips} 个`, `up to ${cantrips} cantrip(s) may be replaced`))
  return `${parts.join(' · ')}。`
})
const subclassRequirement = computed(() => requirement('subclass_ref'))

// A card can arrive with choices it never made: imported from outside, or
// carried over from before the choice was required.  The audit reports them so
// the player can pay before the level moves, instead of finding out too late
// that the whole subclass tree was never granted.
const pendingChoices = ref<JsonObject[]>([])
const pendingBusy = ref(false)
const pendingSubclass = ref('')
const pendingSubclassRow = computed<JsonObject | null>(() => (
  pendingChoices.value.find(row => row.id === 'subclass_ref') as JsonObject | undefined
) || null
)
const pendingOther = computed(() => (
  pendingChoices.value.filter(row => row.id !== 'subclass_ref')
))

async function refreshPending(): Promise<void> {
  if (!props.cardId) return
  try {
    const response = await fetchCharacterCardPendingChoices(props.cardId)
    pendingChoices.value = (response.missing || []) as JsonObject[]
    if (typeof response.revision === 'number') currentRevision.value = response.revision
  } catch {
    // A card that cannot be audited must not block the upgrade panel; the
    // commit path re-checks everything anyway.
    pendingChoices.value = []
  }
}

async function completePendingSubclass(): Promise<void> {
  if (!props.cardId || !pendingSubclass.value) return
  pendingBusy.value = true
  error.value = ''
  try {
    const operationId = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `repair-${Date.now()}-${Math.random().toString(16).slice(2)}`
    const response = await completeCharacterCardChoices(
      props.cardId,
      { subclass_ref: pendingSubclass.value },
      currentRevision.value,
      operationId,
    )
    if (typeof response.revision === 'number') currentRevision.value = response.revision
    pendingSubclass.value = ''
    await refreshPending()
    await refresh()
    emit('applied', response.character)
  } catch (cause: unknown) {
    error.value = String((cause as Error)?.message || cause)
  } finally { pendingBusy.value = false }
}

function canonical(): JsonObject {
  const nested = props.character.ruleset_character
  return nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as JsonObject
    : props.character
}

function initializeSpellChoices(): void {
  const spellcasting = canonical().spellcasting as JsonObject | undefined
  const classMagic = spellcasting?.class as JsonObject | undefined
  if (!classMagic) return
  const book = [...((classMagic.spellbook_refs as string[]) || [])]
  spellbookBaseline.value = book.length
  choices.value.class_spell_choices = {
    cantrip_refs: [...((classMagic.cantrip_refs as string[]) || [])],
    prepared_spell_refs: [...((classMagic.prepared_spell_refs as string[]) || [])],
    spellbook_refs: book,
  }
}

async function refresh(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const response = props.cardId
      ? await previewCharacterCardAdvancement(props.cardId, choices.value)
      : props.gameKey && props.userId
        ? await previewLiveCharacterAdvancement(props.gameKey, props.userId, choices.value)
        : await previewRulesetAdvancement(
          props.ruleId, props.character, choices.value, props.language,
        )
    if (typeof response.revision === 'number') currentRevision.value = response.revision
    preview.value = response.advancement
  } catch (cause: unknown) {
    error.value = String((cause as Error)?.message || cause)
  } finally { busy.value = false }
}

function setChoice(key: string, value: string): void {
  choices.value[key] = value
  if (key === 'feat_ref' || key === 'epic_boon_ref') {
    choices.value.ability_score_increases = {}
  }
  void refresh()
}

function abilityAllowed(ability: string): boolean {
  const allowed = abilityRequirement.value?.allowed
  return allowed === 'any' || !Array.isArray(allowed) || allowed.includes(ability)
}

function setAbility(ability: string, value: number): void {
  const current = {
    ...((choices.value.ability_score_increases as Record<string, number>) || {}),
  }
  if (value > 0) current[ability] = value
  else delete current[ability]
  choices.value.ability_score_increases = current
  void refresh()
}

type SpellList = 'cantrip_refs' | 'prepared_spell_refs' | 'spellbook_refs'

function selectedSpells(key: SpellList): string[] {
  const selected = choices.value.class_spell_choices as JsonObject | undefined
  return [...((selected?.[key] as string[]) || [])]
}

function spellLimit(key: SpellList): number {
  const spec = spellRequirement.value
  if (!spec) return 0
  if (key === 'cantrip_refs') return Number(spec.cantrip_count || 0)
  if (key === 'prepared_spell_refs') return Number(spec.prepared_spell_count || 0)
  // 法术书的下限是"至少"，不是上限：抄写过的书本来就比印刷数多，本级还要
  // 再加 spellbook_additions 个。上限按"开局旧书 + 本级新增"算，用固定的
  // baseline 而不是当前已选数——否则每勾一个，上限就跟着涨一格。
  const floor = Number(spec.spellbook_minimum || 0)
  const additions = Number(spec.spellbook_additions || 0)
  return Math.max(floor, spellbookBaseline.value + additions)
}

function toggleSpell(key: SpellList, spell: RulesetSpellChoice): void {
  const selected = selectedSpells(key)
  const index = selected.indexOf(spell.ref)
  if (index >= 0) selected.splice(index, 1)
  else if (selected.length < spellLimit(key)) selected.push(spell.ref)
  const spellChoices = {
    ...((choices.value.class_spell_choices as JsonObject) || {}),
    [key]: selected,
  }
  if (key === 'spellbook_refs') {
    spellChoices.prepared_spell_refs = selectedSpells('prepared_spell_refs')
      .filter(refValue => selected.includes(refValue))
  }
  choices.value.class_spell_choices = spellChoices
  void refresh()
}

function spellRows(key: 'cantrips' | 'leveled_spells'): RulesetSpellChoice[] {
  return (spellRequirement.value?.[key] as RulesetSpellChoice[]) || []
}

function preparedDisabled(spell: RulesetSpellChoice): boolean {
  return Number(spellRequirement.value?.spellbook_minimum || 0) > 0
    && !selectedSpells('spellbook_refs').includes(spell.ref)
}

function spellDisabled(key: SpellList, spell: RulesetSpellChoice): boolean {
  if (key === 'prepared_spell_refs' && preparedDisabled(spell)) return true
  const limit = spellLimit(key)
  return limit > 0 && selectedSpells(key).length >= limit && !selectedSpells(key).includes(spell.ref)
}

function friendlySpellError(message: string): string {
  const prepared = message.match(/^prepared_spell_refs must contain exactly (\d+) spells$/)
  if (prepared) {
    const required = Number(prepared[1])
    const selected = selectedSpells('prepared_spell_refs').length
    const remaining = Math.max(0, required - selected)
    return text(
      `准备法术需要正好 ${required} 个；现在已选 ${selected} 个${remaining ? `，还差 ${remaining} 个` : '，数量已满足'}。请在“准备法术”区域继续选择。`,
      `Prepared spells require exactly ${required}; ${selected} selected${remaining ? `, ${remaining} remaining` : ', count complete'}. Continue in the Prepared spells section.`,
    )
  }
  const spellbook = message.match(/^spellbook_refs must contain at least (\d+) spells$/)
  if (spellbook) {
    const required = Number(spellbook[1])
    const selected = selectedSpells('spellbook_refs').length
    const remaining = Math.max(0, required - selected)
    return text(
      `法术书至少要有 ${required} 个法术；现在已选 ${selected} 个${remaining ? `，还差 ${remaining} 个` : '，数量已满足'}。请在“法术书”区域继续选择。`,
      `The spellbook needs at least ${required} spells; ${selected} selected${remaining ? `, ${remaining} remaining` : ', count complete'}. Continue in the Spellbook section.`,
    )
  }
  const bookGain = message.match(/^spellbook_refs must add exactly (\d+) spell\(s\) at this level$/)
  if (bookGain) {
    return text(
      `升到本级时法术书要新增正好 ${bookGain[1]} 个法术；请在“法术书”区域新增后重试。`,
      `This level adds exactly ${bookGain[1]} spell(s) to the spellbook; add them in the Spellbook section and retry.`,
    )
  }
  const swap = message.match(/^prepared_spell_refs may replace at most (\d+) spell\(s\) at this level$/)
  if (swap) {
    return text(
      `本级最多替换 ${swap[1]} 个旧法术（长休才能重排整张列表）；请把要保留的旧法术重新勾选回来。`,
      `At most ${swap[1]} old spell(s) may be replaced at this level (reshuffling the whole list belongs to a long rest); re-check the old spells you want to keep.`,
    )
  }
  const cantripSwap = message.match(/^cantrip_refs may replace at most (\d+) cantrip\(s\) at this level$/)
  if (cantripSwap) {
    return text(
      `本级最多替换 ${cantripSwap[1]} 个戏法；请把要保留的戏法重新勾选回来。`,
      `At most ${cantripSwap[1]} cantrip(s) may be replaced at this level; re-check the cantrips you want to keep.`,
    )
  }
  if (message === 'prepared_spell_refs must be in spellbook_refs') {
    return text('准备法术必须先放进法术书；请先勾选法术书，再从其中选择准备法术。', 'Prepared spells must also be in the spellbook. Choose spellbook spells first.')
  }
  if (message === 'subclass_ref is required at this level') {
    return text('本级需要选择一个子职；请在“选择子职”区域完成选择。', 'Choose a subclass in the Choose subclass section for this level.')
  }
  if (message.includes('ability_score_increases')) {
    return text('属性提升还没有按要求分配完成；请检查每项属性的加值和合计要求。', 'Ability increases are incomplete; check each score and the required total.')
  }
  return message
}

function featureName(id: string): string {
  return id.replaceAll('_', ' ')
}

async function apply(): Promise<void> {
  if (!preview.value?.ok) return
  busy.value = true
  error.value = ''
  try {
    const operationId = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `advance-${Date.now()}-${Math.random().toString(16).slice(2)}`
    const response = props.cardId
      ? await applyCharacterCardAdvancement(
          props.cardId,
          choices.value,
          currentRevision.value,
          operationId,
        )
      : props.gameKey && props.userId
        ? await applyLiveCharacterAdvancement(
            props.gameKey, props.userId, choices.value, currentRevision.value, operationId,
          )
        : await applyRulesetAdvancement(
          props.ruleId, props.character, choices.value, props.language,
        )
    if (typeof response.revision === 'number') currentRevision.value = response.revision
    emit('applied', response.character)
  } catch (cause: unknown) {
    error.value = String((cause as Error)?.message || cause)
  } finally { busy.value = false }
}

onMounted(() => {
  initializeSpellChoices()
  void refresh()
  void refreshPending()
})
</script>

<template>
  <section class="advancement-panel" aria-labelledby="dnd-advancement-title">
    <header>
      <div><span>5E · 2024 · SRD</span><h3 id="dnd-advancement-title">{{ text('职业升级', 'Class advancement') }}</h3><p v-if="preview">Lv. {{ preview.from_level }} → Lv. {{ preview.to_level }}</p><p class="flow-hint">{{ text('先看本级变化，再完成需要选择的项目；底部按钮可用后才会真正升级。', 'Review the level changes, complete the choices, then apply when the bottom button is enabled.') }}</p></div>
    </header>

    <fieldset v-if="pendingChoices.length" class="pending-choices">
      <legend>{{ text('这张卡还有未完成的选项', 'This card still owes choices') }}</legend>
      <p class="error-help">{{ text('先把这些补齐再升级；补齐本身不会改变等级。', 'Pay these before the level moves. Filling them does not change the level.') }}</p>
      <div v-if="pendingSubclassRow" class="pending-subclass">
        <label>{{ text(`子职（第 ${Number(pendingSubclassRow.level)} 级）`, `Subclass (level ${Number(pendingSubclassRow.level)})`) }}</label>
        <select v-model="pendingSubclass">
          <option value="" disabled>{{ text('请选择', 'Choose') }}</option>
          <option v-for="option in (pendingSubclassRow.options as JsonObject[])" :key="String(option.value)" :value="String(option.value)">{{ option.name }}</option>
        </select>
        <button type="button" :disabled="pendingBusy || !pendingSubclass" @click="completePendingSubclass">{{ pendingBusy ? text('补齐中…', 'Saving…') : text('补齐子职', 'Complete subclass') }}</button>
      </div>
      <ul v-if="pendingOther.length">
        <li v-for="row in pendingOther" :key="String(row.id)">{{ text(`第 ${Number(row.level)} 级：${String(row.reason || row.id)}`, `Level ${Number(row.level)}: ${String(row.reason || row.id)}`) }}</li>
      </ul>
    </fieldset>

    <p v-if="busy && !preview" class="notice" role="status">{{ text('正在核对职业成长表…', 'Checking the class table…') }}</p>
    <template v-if="preview">
      <div class="level-summary">
        <article><span>{{ text('生命提升', 'HP gain') }}</span><b>+{{ hpDiff.gain }}</b><small>{{ text('固定值与体质修正已计入', 'Fixed value and Constitution included') }}</small></article>
        <article><span>{{ text('熟练加值', 'Proficiency') }}</span><b>+{{ (diff.proficiency_bonus as JsonObject)?.before }} → +{{ (diff.proficiency_bonus as JsonObject)?.after }}</b></article>
        <article><span>{{ text('获得职业特性', 'Features gained') }}</span><b>{{ ((diff.gained_feature_ids as string[]) || []).length }}</b></article>
      </div>

      <fieldset><legend>{{ text('生命值成长方式', 'Hit Point method') }}</legend><label><input v-model="choices.hp_method" type="radio" value="fixed" @change="refresh"> {{ text('采用职业固定值（推荐）', 'Use class fixed value (recommended)') }}</label><label><input v-model="choices.hp_method" type="radio" value="rolled" @change="refresh"> {{ text('掷职业生命骰', 'Roll the class Hit Die') }}</label><input v-if="choices.hp_method === 'rolled'" v-model.number="choices.hp_roll" type="number" min="1" :max="20" @change="refresh"></fieldset>

      <fieldset v-if="subclassRequirement"><legend>{{ text('选择子职', 'Choose subclass') }}</legend><select :value="choices.subclass_ref || ''" @change="setChoice('subclass_ref', ($event.target as HTMLSelectElement).value)"><option value="" disabled>{{ text('请选择', 'Choose') }}</option><option v-for="option in (subclassRequirement.options as JsonObject[])" :key="String(option.value)" :value="option.value">{{ option.name }}</option></select></fieldset>

      <fieldset v-if="featRequirement"><legend>{{ requirement('epic_boon_ref') ? text('选择史诗恩惠', 'Choose Epic Boon') : text('选择通用专长', 'Choose General feat') }}</legend><div class="feat-grid"><label v-for="option in (featRequirement.options as JsonObject[])" :key="String(option.value)" :class="{ unavailable: option.available === false }"><input type="radio" :name="String(featRequirement.id)" :disabled="option.available === false" :checked="choices[String(featRequirement.id)] === option.value" @change="setChoice(String(featRequirement.id), String(option.value))"><span><b>{{ option.name }}</b><small v-if="option.available === false">{{ text('前置条件未满足', 'Prerequisite not met') }}</small><code>{{ option.source_ref }}</code></span></label></div></fieldset>

      <fieldset v-if="abilityRequirement"><legend>{{ text(`属性提升（合计 ${abilityRequirement.total}）`, `Ability increase (total ${abilityRequirement.total})`) }}</legend><div class="ability-grid"><label v-for="ability in ABILITIES" :key="ability" :class="{ unavailable: !abilityAllowed(ability) }"><span>{{ ability.toUpperCase() }} <small>{{ abilityDiff[ability] }}</small></span><select :disabled="!abilityAllowed(ability)" :value="(choices.ability_score_increases as JsonObject)?.[ability] || 0" @change="setAbility(ability, Number(($event.target as HTMLSelectElement).value))"><option :value="0">+0</option><option :value="1">+1</option><option v-if="abilityRequirement.pattern === '2_or_1_1'" :value="2">+2</option></select></label></div></fieldset>

      <fieldset v-if="spellRequirement" class="spell-section"><legend>{{ text('调整职业法术', 'Update class spells') }}</legend><p class="spell-help">{{ text('每个区域标题都写着“已选 / 需要”。达到需要的数量后，其他选项会自动锁定；取消一个已选项即可重新选择。法师请先选法术书，再从法术书里选准备法术。', 'Each section shows selected / required. Once the required count is reached, extra options lock automatically; uncheck one to choose another. Wizards choose the spellbook first, then prepared spells from it.') }}</p><p v-if="spellBudgetText" class="spell-help spell-budget">{{ spellBudgetText }}</p><div v-if="Number(spellRequirement.cantrip_count)"><b>{{ text(`戏法：已选 ${selectedSpells('cantrip_refs').length} / 需要 ${spellRequirement.cantrip_count}`, `Cantrips: ${selectedSpells('cantrip_refs').length} selected / ${spellRequirement.cantrip_count} required`) }}</b><div class="spell-grid"><label v-for="spell in spellRows('cantrips')" :key="spell.ref"><input type="checkbox" :disabled="spellDisabled('cantrip_refs', spell)" :checked="selectedSpells('cantrip_refs').includes(spell.ref)" @change="toggleSpell('cantrip_refs', spell)"> {{ spell.name }}</label></div></div><div v-if="Number(spellRequirement.spellbook_minimum)"><b>{{ text(`法术书：已选 ${selectedSpells('spellbook_refs').length} / 需要 ${spellLimit('spellbook_refs')}`, `Spellbook: ${selectedSpells('spellbook_refs').length} selected / ${spellLimit('spellbook_refs')} required`) }}</b><div class="spell-grid"><label v-for="spell in spellRows('leveled_spells')" :key="`book-${spell.ref}`"><input type="checkbox" :disabled="spellDisabled('spellbook_refs', spell)" :checked="selectedSpells('spellbook_refs').includes(spell.ref)" @change="toggleSpell('spellbook_refs', spell)"> {{ spell.name }} <small>Lv.{{ spell.level }}</small></label></div></div><div v-if="Number(spellRequirement.prepared_spell_count)"><b>{{ text(`准备法术：已选 ${selectedSpells('prepared_spell_refs').length} / 需要 ${spellRequirement.prepared_spell_count}`, `Prepared: ${selectedSpells('prepared_spell_refs').length} selected / ${spellRequirement.prepared_spell_count} required`) }}</b><div class="spell-grid"><label v-for="spell in spellRows('leveled_spells')" :key="`prepared-${spell.ref}`" :class="{ unavailable: preparedDisabled(spell) || spellDisabled('prepared_spell_refs', spell) }"><input type="checkbox" :disabled="spellDisabled('prepared_spell_refs', spell)" :checked="selectedSpells('prepared_spell_refs').includes(spell.ref)" @change="toggleSpell('prepared_spell_refs', spell)"> {{ spell.name }} <small>Lv.{{ spell.level }}</small></label></div></div></fieldset>

      <fieldset><legend>{{ text('本级变化', 'Level changes') }}</legend><div class="feature-list"><span v-for="feature in (diff.gained_feature_ids as string[]) || []" :key="feature">{{ featureName(feature) }}</span></div><p v-if="Object.keys(slotChanges).length">{{ text('法术位变化', 'Spell slot changes') }}: <code>{{ JSON.stringify(slotChanges) }}</code></p></fieldset>

      <div v-if="preview.errors.length" class="errors" role="alert"><b>{{ text('升级还不能确认', 'Upgrade is not ready') }}</b><p class="error-help">{{ text('下面每一条都告诉你缺什么；按提示补齐后，系统会自动重新核对。', 'Each item below tells you what is missing. The panel rechecks automatically after you make a choice.') }}</p><ul><li v-for="message in preview.errors" :key="message">{{ friendlySpellError(message) }}</li></ul></div>
    </template>
    <p v-if="error" class="errors" role="alert">{{ error }}</p>
    <footer><button type="button" @click="emit('cancel')">{{ text('取消', 'Cancel') }}</button><button type="button" class="primary" :disabled="busy || !preview?.ok" @click="apply">{{ busy ? text('核对中…', 'Checking…') : text('确认升级', 'Apply level') }}</button></footer>
  </section>
</template>

<style scoped>
.advancement-panel { display: grid; gap: 16px; color: #e8edf5; }
.advancement-panel > header { display: flex; justify-content: space-between; gap: 16px; }
.advancement-panel header span { color: #d5a44f; font-size: 11px; letter-spacing: .16em; }
.advancement-panel h3 { margin: 3px 0; font-family: Georgia, serif; font-size: 27px; }
.advancement-panel header p { margin: 0; color: #aeb8c6; }.advancement-panel header .flow-hint { margin-top: 6px; font-size: 13px; line-height: 1.5; }
.advancement-panel header button { align-self: start; width: 44px; border-radius: 50%; font-size: 20px; }
.level-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; }
.level-summary article { display: grid; gap: 3px; padding: 13px; border: 1px solid #3b4656; border-radius: 11px; background: #111925; }
.level-summary span, .level-summary small { color: #9da9b8; }
.level-summary b { font-size: 19px; }
fieldset { display: grid; gap: 10px; padding: 13px; border: 1px solid #374354; border-radius: 12px; }
legend { color: #f1d29a; font-weight: 700; }
label { display: flex; gap: 8px; min-height: 44px; align-items: center; }
label input[type='radio'], label input[type='checkbox'] { width: 22px; height: 22px; }
.feat-grid, .spell-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 7px; }
.feat-grid label, .spell-grid label { padding: 8px; border-radius: 8px; background: #141d29; }
.feat-grid span { display: grid; gap: 2px; }
.feat-grid code { color: #8d9bad; font-size: 9px; }
.unavailable { opacity: .48; }
.ability-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 7px; }
.ability-grid label { display: grid; gap: 5px; }
.ability-grid select, select, input[type='number'] { min-height: 44px; border: 1px solid #465367; border-radius: 8px; background: #101722; color: #edf1f7; padding: 7px; }
.feature-list { display: flex; flex-wrap: wrap; gap: 6px; }
.feature-list span { padding: 5px 8px; border-radius: 99px; background: #263142; text-transform: capitalize; }
.errors { padding: 11px 13px; border: 1px solid #8c4650; border-radius: 9px; background: #351a20; color: #ffc1c7; }.errors .error-help { margin: 5px 0 0; line-height: 1.5; }.errors li { line-height: 1.55; }
.errors ul { margin: 6px 0 0; padding-left: 20px; }
.notice { color: #aeb8c6; }
.spell-help { margin: 0; color: #b9c5d3; line-height: 1.55; }
.spell-budget { color: var(--df-accent-strong, #d6b26a); }
footer { display: flex; justify-content: flex-end; gap: 8px; }
button { min-height: 44px; border: 1px solid #465367; border-radius: 8px; background: #192331; color: #edf1f7; padding: 8px 12px; }
button.primary { border-color: #bc8b3f; background: #9d6825; }
button:disabled { opacity: .45; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #e2b35e; outline-offset: 2px; }
:global(body.light .advancement-panel) { color: #2d2924; }
:global(body.light .advancement-panel .level-summary article), :global(body.light .advancement-panel fieldset) { border-color: #c7baa7; background: #fff; }
:global(body.light .advancement-panel .level-summary span), :global(body.light .advancement-panel .level-summary small), :global(body.light .advancement-panel .notice) { color: #514b43; }
:global(body.light .advancement-panel select), :global(body.light .advancement-panel input[type='number']) { border-color: #968a7b; background: #fff; color: #29241f; }
:global(body.light .advancement-panel .feat-grid label), :global(body.light .advancement-panel .spell-grid label) { background: #f1eee8; }
@media (max-width: 680px) {
  .level-summary { grid-template-columns: 1fr; }
  .ability-grid { grid-template-columns: repeat(3, 1fr); }
  .spell-grid { grid-template-columns: 1fr; }
  footer { position: sticky; bottom: 0; z-index: 2; flex-wrap: wrap; padding-block: 10px max(10px, env(safe-area-inset-bottom)); background: #111821f2; }
  :global(body.light .advancement-panel footer) { background: rgb(255 255 255 / 95%); }
}
@media (prefers-reduced-motion: reduce) { .advancement-panel * { scroll-behavior: auto !important; transition: none !important; animation: none !important; } }
</style>
