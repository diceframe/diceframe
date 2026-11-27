<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
  /** 0 = single-level mode (unchanged behaviour); N >= 2 = keep going to N. */
  targetLevel?: number
}>(), {
  language: 'zh-CN', cardId: '', gameKey: '', userId: '', revision: 0, targetLevel: 0,
})
const emit = defineEmits<{
  applied: [character: JsonObject]
  chainCompleted: [character: JsonObject]
  cancel: []
}>()

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
// 本级新获得的特性里带选项的那些。后端把每一项发成一条 requirement，
// 结构固定为 {owner, feature_id, choice_id, count, options}，提交时合成
// 一个 `feature_choices` 数组 —— 缺了这段渲染，界面只会显示
// "feature choice X requires a selection at this level"，而没有任何东西
// 可以点，确认按钮也就永远按不下去。
const featureChoiceRequirements = computed(() => (
  requirements.value.filter(item => item.kind === 'feature_choice')
))
// 一次选择可能是"哪个选项"，也可能是"哪个选项 + 绑定哪个对象"（祈唤绑定
// 戏法）。绑定属于**这一次选择**，所以状态里存 pair，而不是另开一张表：
// 同一个可重复选项可以各绑一个戏法，存成字符串就再也区分不出两次选择。
type FeaturePick = { ref: string; object_ref?: string }
const featureSelections = ref<Record<string, FeaturePick[]>>({})
// 每个选项当前在"绑定对象"下拉里选中的值。
const featureBindChoice = ref<Record<string, string>>({})

// Wild Shape 的已知形态：与其它槽位共用 requirements 通道，只是提交键不同
// （后端读 choices.known_form_refs）。数量、CR 上限、候选都由内容给出。
const knownFormsRequirement = computed<JsonObject | null>(() => (
  requirements.value.find(item => item.kind === 'known_forms') as JsonObject | undefined
) || null
)
const knownForms = ref<string[]>([])

function knownFormLimit(): number {
  return Number(knownFormsRequirement.value?.count || 0)
}

function knownFormIsPicked(ref: string): boolean {
  return knownForms.value.includes(ref)
}

function syncKnownForms(): void {
  if (knownForms.value.length) choices.value.known_form_refs = [...knownForms.value]
  else delete choices.value.known_form_refs
}

// Metamagic：与已知形态同一形状（共用 requirements 通道、提交键不同）。
// 2/10/17 级是**必答**槽位，不选就不能确认升级——后端在通用规则里判。
const metamagicRequirement = computed<JsonObject | null>(() => (
  requirements.value.find(item => item.kind === 'metamagic') as JsonObject | undefined
) || null
)
const metamagicPicks = ref<string[]>([])

function metamagicLimit(): number {
  return Number(metamagicRequirement.value?.count || 0)
}

function metamagicLabel(value: string): string {
  const rows = (metamagicRequirement.value?.options as JsonObject[] | undefined) || []
  const row = rows.find(item => String(item.value) === value)
  return String(row?.name || value)
}

function syncMetamagic(): void {
  if (metamagicPicks.value.length) {
    choices.value.metamagic_option_ids = [...metamagicPicks.value]
  } else {
    delete choices.value.metamagic_option_ids
  }
}

function toggleMetamagic(value: string): void {
  const index = metamagicPicks.value.indexOf(value)
  if (index >= 0) metamagicPicks.value.splice(index, 1)
  else if (metamagicPicks.value.length < metamagicLimit()) metamagicPicks.value.push(value)
  syncMetamagic()
  void refresh()
}

// 每级可换一个已知选项：替换是可选动作（原文 "can replace"），所以留空即不换。
const metamagicReplaceRequirement = computed<JsonObject | null>(() => (
  requirements.value.find(item => item.kind === 'metamagic_replace') as JsonObject | undefined
) || null
)
const metamagicDrop = ref('')
const metamagicAdd = ref('')

function syncMetamagicReplace(): void {
  if (metamagicDrop.value && metamagicAdd.value) {
    choices.value.metamagic_replace = { drop: metamagicDrop.value, add: metamagicAdd.value }
  } else {
    delete choices.value.metamagic_replace
  }
}

function setMetamagicDrop(value: string): void {
  metamagicDrop.value = value
  syncMetamagicReplace()
  void refresh()
}

function setMetamagicAdd(value: string): void {
  metamagicAdd.value = value
  syncMetamagicReplace()
  void refresh()
}

function toggleKnownForm(ref: string): void {
  const index = knownForms.value.indexOf(ref)
  if (index >= 0) knownForms.value.splice(index, 1)
  else if (knownForms.value.length < knownFormLimit()) knownForms.value.push(ref)
  syncKnownForms()
  void refresh()
}

const FEATURE_CHOICE_NAMES: Record<string, [string, string]> = {
  additional_fighting_style: ['额外战斗风格', 'Additional Fighting Style'],
  expertise: ['专精', 'Expertise'],
  scholar: ['学者', 'Scholar'],
}

function featureChoiceTitle(spec: JsonObject): string {
  const pair = FEATURE_CHOICE_NAMES[String(spec.feature_id)]
  return pair ? text(pair[0], pair[1]) : featureName(String(spec.feature_id))
}

function featureChoiceSelected(spec: JsonObject): FeaturePick[] {
  return featureSelections.value[String(spec.id)] || []
}

function featureChoiceIsPicked(
  spec: JsonObject, value: string, objectRef = '',
): boolean {
  return featureChoiceSelected(spec).some(
    pick => pick.ref === value && (pick.object_ref || '') === objectRef,
  )
}

function featureChoiceAtLimit(spec: JsonObject): boolean {
  const limit = Number(spec.count || 1)
  return featureChoiceSelected(spec).length >= limit
}

function featureBindOptions(option: JsonObject): JsonObject[] {
  return (option.bind_options as JsonObject[]) || []
}

// 选项需要绑定对象时，默认挑第一个可用项：让玩家先点选项再回头选对象，
// 会在"还没选对象"的中间态被服务端拒绝，界面看起来像点不动。
function featureBindValue(option: JsonObject): string {
  const key = String(option.value)
  const explicit = featureBindChoice.value[key]
  if (explicit) return explicit
  return String(featureBindOptions(option).find(row => row.available !== false)?.value || '')
}

function setFeatureBind(option: JsonObject, value: string): void {
  featureBindChoice.value[String(option.value)] = value
}

function featureToggle(spec: JsonObject, option: JsonObject): void {
  const value = String(option.value)
  const bindOptions = featureBindOptions(option)
  const objectRef = bindOptions.length ? featureBindValue(option) : ''
  const key = String(spec.id)
  const selected = [...featureChoiceSelected(spec)]
  const index = selected.findIndex(
    pick => pick.ref === value && (pick.object_ref || '') === objectRef,
  )
  if (index >= 0) selected.splice(index, 1)
  else if (selected.length < Number(spec.count || 1)) {
    selected.push(objectRef ? { ref: value, object_ref: objectRef } : { ref: value })
  }
  if (selected.length) featureSelections.value[key] = selected
  else delete featureSelections.value[key]
  syncFeatureChoices()
  void refresh()
}

// 替换槽：后端发送 `replace: {count, drop_options, add_options}`。drop 可能是
// 字符串（不绑定的选择）也可能是 {ref, object_ref}（绑定的选择），原样提交，
// 由服务端按同一形状匹配——客户端不替它猜。
const featureReplacements = ref<Record<string, { drop: JsonObject | string; add: string }>>({})

function featureReplaceSlot(spec: JsonObject): JsonObject | null {
  return (spec.replace as JsonObject | undefined) || null
}

function featureReplaceValue(spec: JsonObject): { drop: JsonObject | string; add: string } {
  return featureReplacements.value[String(spec.id)] || { drop: '', add: '' }
}

function featureReplaceLabel(spec: JsonObject, option: JsonObject | string): string {
  if (typeof option === 'string') {
    const row = ((spec.options as JsonObject[]) || []).find(item => item.value === option)
    return String(row?.name || option)
  }
  const row = ((spec.options as JsonObject[]) || []).find(item => item.value === option.ref)
  const bound = String(option.object_ref || '')
  return `${String(row?.name || option.ref)}${bound ? ` · ${bound.replace('spell:', '')}` : ''}`
}

function setFeatureReplace(spec: JsonObject, field: 'drop' | 'add', value: string): void {
  const key = String(spec.id)
  const current = { ...featureReplaceValue(spec) }
  const slot = featureReplaceSlot(spec)
  if (field === 'drop') {
    const rows = (slot?.drop_options as (JsonObject | string)[]) || []
    current.drop = rows.find(row => featureReplaceLabel(spec, row) === value) || ''
  } else {
    current.add = value
  }
  featureReplacements.value[key] = current
  syncFeatureChoices()
  void refresh()
}

function clearFeatureReplace(spec: JsonObject): void {
  delete featureReplacements.value[String(spec.id)]
  syncFeatureChoices()
  void refresh()
}

function unavailableLabel(option: JsonObject): string {
  const reasons = (option.unavailable_reasons as string[]) || []
  // "这个规则集还没有实现该效果" 与 "你的前置条件不满足" 是两件事：前者
  // 玩家永远选不到，后者补齐属性/熟练就能选。混成一句话会让人一直找办法。
  if (reasons.includes('effect_not_implemented')) {
    return text('本规则集尚未实现该效果', 'Not implemented in this ruleset yet')
  }
  if (reasons.includes('not_proficient')) return text('尚未熟练', 'Not proficient')
  if (reasons.includes('already_expertise')) return text('已有专精', 'Already has Expertise')
  if (reasons.includes('level')) return text('等级不足', 'Level too low')
  if (reasons.includes('already_owned')) return text('已拥有', 'Already owned')
  if (reasons.includes('already_bound')) return text('已绑定该对象', 'Already bound to it')
  if (reasons.includes('no_eligible_cantrip')) {
    return text('没有可绑定的戏法', 'No eligible cantrip')
  }
  if (reasons.includes('prerequisite_invocation')) {
    return text('缺少前置祈唤', 'Missing prerequisite invocation')
  }
  if (reasons.includes('ability_prerequisite')) {
    return text('属性不满足', 'Ability prerequisite not met')
  }
  if (reasons.includes('spellcasting')) {
    return text('需要施法能力', 'Requires spellcasting')
  }
  return text('前置条件未满足', 'Prerequisite not met')
}

function syncFeatureChoices(): void {
  const rows: JsonObject[] = []
  for (const spec of featureChoiceRequirements.value) {
    const selected = featureChoiceSelected(spec)
    if (!selected.length) continue
    rows.push({
      owner: String(spec.owner || ''),
      feature_id: String(spec.feature_id || ''),
      choice_id: String(spec.choice_id || ''),
      // 一次选择是 {ref, object_ref} 或裸 ref：服务端两种都认，但不带对象
      // 的绑定选择会被拒，所以这里不把带绑定的选择压成字符串。
      selected_refs: selected.map(
        pick => (pick.object_ref ? { ref: pick.ref, object_ref: pick.object_ref } : pick.ref),
      ),
    })
  }
  // 只提交当前这一级真正存在的槽位：把上一轮预览留下的记录发回去，后端
  // 会按 "matching no slot" 整条拒绝，玩家看到的是一个他已经改掉的旧错。
  if (rows.length) choices.value.feature_choices = rows
  else delete choices.value.feature_choices
  const replacement = Object.values(featureReplacements.value).find(
    item => item.drop && item.add,
  )
  if (replacement) choices.value.feature_choice_replace = replacement
  else delete choices.value.feature_choice_replace
}



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
    // 补齐子职只在卡还没升级时发生；链式模式下它不该关掉面板。
    if (!chained.value) emit('applied', response.character)
  } catch (cause: unknown) {
    error.value = String((cause as Error)?.message || cause)
  } finally { pendingBusy.value = false }
}

// A chain replaces the character it is upgrading: level 2's result is level 3's
// input.  The prop stays the entrance point, this ref is what the panel moves.
const workingCharacter = ref<JsonObject>(props.character)

function canonical(character: JsonObject = workingCharacter.value): JsonObject {
  const nested = character.ruleset_character
  return nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as JsonObject
    : character
}

function levelOf(character: JsonObject): number {
  const inner = canonical(character)
  const progression = inner.progression as JsonObject | undefined
  const build = inner.build as JsonObject | undefined
  return Number(
    character.level || inner.level || progression?.level || build?.level || 0,
  )
}

const targetLevel = computed(() => Math.max(0, Math.floor(Number(props.targetLevel || 0))))
// `targetLevel` at or below the current level is a no-op, not a chain: falling
// back to single-level mode keeps the panel honest instead of spinning.
const chained = computed(() => (
  targetLevel.value >= 2 && levelOf(workingCharacter.value) < targetLevel.value
))

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
          props.ruleId, workingCharacter.value, choices.value, props.language,
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

function prepareNextLevel(character: JsonObject): void {
  workingCharacter.value = character
  // Everything this level asked for (subclass, feat, abilities, spells) now
  // lives in the character itself.  Only the player's hit-point preference
  // carries over; re-seeding the rest keeps level N+1's own choices blank.
  const hpMethod = choices.value.hp_method
  choices.value = { hp_method: hpMethod === 'rolled' ? 'rolled' : 'fixed' }
  initializeSpellChoices()
  preview.value = null
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
          props.ruleId, workingCharacter.value, choices.value, props.language,
        )
    if (typeof response.revision === 'number') currentRevision.value = response.revision
    const changed = response.character as JsonObject
    const reached = levelOf(changed)
    const moreLevels = chained.value
      && reached > levelOf(workingCharacter.value)
      && reached < targetLevel.value
    if (moreLevels) {
      // Keep the panel open and preview the next level with the character the
      // server just returned -- the chain is N-1 ordinary advancements, so the
      // persisted result is exactly what replay already accepts.
      prepareNextLevel(changed)
      await refresh()
      return
    }
    if (chained.value) emit('chainCompleted', changed)
    else emit('applied', changed)
  } catch (cause: unknown) {
    error.value = String((cause as Error)?.message || cause)
  } finally { busy.value = false }
}

// The host may hand over a different character (cancel then reopen, or a card
// reload).  Start that over instead of upgrading a stale copy.
watch(() => props.character, next => {
  if (!next || next === workingCharacter.value) return
  workingCharacter.value = next
  currentRevision.value = props.revision
  const hpMethod = choices.value.hp_method
  choices.value = { hp_method: hpMethod === 'rolled' ? 'rolled' : 'fixed' }
  initializeSpellChoices()
  preview.value = null
  pendingChoices.value = []
  void refresh()
  void refreshPending()
})

onMounted(() => {
  initializeSpellChoices()
  void refresh()
  void refreshPending()
})
</script>

<template>
  <section class="advancement-panel" aria-labelledby="dnd-advancement-title">
    <header>
      <div><span>5E · 2024 · SRD</span><h3 id="dnd-advancement-title">{{ text('职业升级', 'Class advancement') }}</h3><p v-if="preview">Lv. {{ preview.from_level }} → Lv. {{ preview.to_level }}<span v-if="chained" class="chain-target">{{ text(`（目标 ${targetLevel} 级）`, ` (target level ${targetLevel})`) }}</span></p><p class="flow-hint">{{ text('先看本级变化，再完成需要选择的项目；底部按钮可用后才会真正升级。', 'Review the level changes, complete the choices, then apply when the bottom button is enabled.') }}</p></div>
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

      <fieldset v-if="metamagicRequirement"><legend>{{ text('超魔选项', 'Metamagic options') }}</legend><p class="spell-help">{{ text(`术士 2 级获得超魔，本级要选 ${metamagicLimit()} 个；不选无法确认升级（错过不会补）。`, `Metamagic arrives at Sorcerer 2; choose ${metamagicLimit()} now. The level cannot be confirmed without them.`) }}</p><p class="spell-help"><b>{{ text(`已选 ${metamagicPicks.length} / 需要 ${metamagicLimit()}`, `${metamagicPicks.length} selected / ${metamagicLimit()} required`) }}</b></p><div class="feat-grid"><label v-for="option in (metamagicRequirement.options as JsonObject[])" :key="String(option.value)"><input type="checkbox" :disabled="!metamagicPicks.includes(String(option.value)) && metamagicPicks.length >= metamagicLimit()" :checked="metamagicPicks.includes(String(option.value))" @change="toggleMetamagic(String(option.value))"><span><b>{{ option.name || option.value }}</b></span></label></div></fieldset>
      <fieldset v-if="metamagicReplaceRequirement"><legend>{{ text('替换一个超魔选项（可选）', 'Replace one Metamagic option (optional)') }}</legend><p class="spell-help">{{ text('每次获得术士等级可以换掉一个已知选项；换成的必须是尚未拥有的。', 'Each Sorcerer level may replace one known option with one you do not know.') }}</p><div class="feat-grid"><label><span>{{ text('换掉', 'Replace') }}<select :value="metamagicDrop" @change="setMetamagicDrop(($event.target as HTMLSelectElement).value)"><option value="">{{ text('不换', 'Keep all') }}</option><option v-for="value in ((metamagicReplaceRequirement.known as string[]) || [])" :key="value" :value="value">{{ metamagicLabel(value) }}</option></select></span></label><label><span>{{ text('换成', 'With') }}<select :value="metamagicAdd" @change="setMetamagicAdd(($event.target as HTMLSelectElement).value)"><option value="">{{ text('未选择', 'Not chosen') }}</option><option v-for="option in (metamagicReplaceRequirement.options as JsonObject[])" :key="String(option.value)" :value="String(option.value)">{{ option.name || option.value }}</option></select></span></label></div></fieldset>
      <fieldset v-if="knownFormsRequirement"><legend>{{ text('已知野兽形态', 'Known beast forms') }}</legend><p class="spell-help">{{ text(`Wild Shape 在 2 级开始，本级要选满 ${knownFormLimit()} 个形态；CR 上限 ${knownFormsRequirement.cr_cap}，8 级前不能选带飞行速度的形态。长休时可以用一个替换其中一个。`, `Wild Shape starts at level 2; choose ${knownFormLimit()} forms now. CR cap ${knownFormsRequirement.cr_cap}, and no flying forms before level 8. A long rest can swap one of them.`) }}</p><p class="spell-help"><b>{{ text(`已选 ${knownForms.length} / 需要 ${knownFormLimit()}`, `${knownForms.length} selected / ${knownFormLimit()} required`) }}</b></p><div class="feat-grid"><label v-for="option in (knownFormsRequirement.options as JsonObject[])" :key="String(option.value)"><input type="checkbox" :disabled="!knownFormIsPicked(String(option.value)) && knownForms.length >= knownFormLimit()" :checked="knownFormIsPicked(String(option.value))" @change="toggleKnownForm(String(option.value))"><span><b>{{ option.name || option.value }}</b><small>CR {{ option.cr }}</small></span></label></div></fieldset>

      <fieldset v-for="spec in featureChoiceRequirements" :key="String(spec.id)"><legend>{{ featureChoiceTitle(spec) }}</legend><p class="spell-help">{{ text(`本级需要选择 ${spec.count} 项。标灰的选项写着不可选的原因：本规则集尚未实现的专长会一直无法选中，前置条件不足的补齐后即可。可重复的选项每选一次绑一个对象。`, `Choose ${spec.count} for this level. Greyed options name their reason: feats this ruleset does not implement stay unavailable, while unmet prerequisites become selectable once satisfied. Repeatable options bind one object per pick.`) }}</p><div class="feat-grid"><label v-for="option in (spec.options as JsonObject[])" :key="String(option.value)" :class="{ unavailable: option.available === false }"><input :type="Number(spec.count || 1) > 1 ? 'checkbox' : 'radio'" :name="String(spec.id)" :disabled="option.available === false || (featureBindOptions(option).length === 0 && !featureChoiceIsPicked(spec, String(option.value)) && featureChoiceAtLimit(spec))" :checked="featureChoiceIsPicked(spec, String(option.value), featureBindValue(option))" @change="featureToggle(spec, option)"><span><b>{{ option.name || option.value }}</b><small v-if="option.available === false">{{ unavailableLabel(option) }}</small><select v-if="featureBindOptions(option).length" :value="featureBindValue(option)" @click.stop @change.stop="setFeatureBind(option, ($event.target as HTMLSelectElement).value)"><option v-for="bind in featureBindOptions(option)" :key="String(bind.value)" :value="String(bind.value)" :disabled="bind.available === false">{{ bind.value }}<template v-if="bind.available === false"> · {{ text('已绑定', 'bound') }}</template></option></select><code v-if="option.source_ref">{{ option.source_ref }}</code></span></label></div><div v-if="featureReplaceSlot(spec)" class="replace-row"><b>{{ text('本級可替换一个', 'Replace one this level') }}</b><select :value="featureReplaceValue(spec).drop ? featureReplaceLabel(spec, featureReplaceValue(spec).drop) : ''" @change="setFeatureReplace(spec, 'drop', ($event.target as HTMLSelectElement).value)"><option value="">{{ text('不替换', 'Keep them') }}</option><option v-for="row in (featureReplaceSlot(spec)?.drop_options as (JsonObject | string)[])" :key="featureReplaceLabel(spec, row)" :value="featureReplaceLabel(spec, row)">{{ featureReplaceLabel(spec, row) }}</option></select><select :value="featureReplaceValue(spec).add" @change="setFeatureReplace(spec, 'add', ($event.target as HTMLSelectElement).value)"><option value="">{{ text('换成…', 'Replace with…') }}</option><option v-for="row in (featureReplaceSlot(spec)?.add_options as string[])" :key="row" :value="row">{{ row }}</option></select><button v-if="featureReplaceValue(spec).drop || featureReplaceValue(spec).add" type="button" @click="clearFeatureReplace(spec)">{{ text('取消替换', 'Clear') }}</button></div></fieldset>

      <fieldset v-if="featRequirement"><legend>{{ requirement('epic_boon_ref') ? text('选择史诗恩惠', 'Choose Epic Boon') : text('选择通用专长', 'Choose General feat') }}</legend><div class="feat-grid"><label v-for="option in (featRequirement.options as JsonObject[])" :key="String(option.value)" :class="{ unavailable: option.available === false }"><input type="radio" :name="String(featRequirement.id)" :disabled="option.available === false" :checked="choices[String(featRequirement.id)] === option.value" @change="setChoice(String(featRequirement.id), String(option.value))"><span><b>{{ option.name }}</b><small v-if="option.available === false">{{ text('前置条件未满足', 'Prerequisite not met') }}</small><code>{{ option.source_ref }}</code></span></label></div></fieldset>

      <fieldset v-if="abilityRequirement"><legend>{{ text(`属性提升（合计 ${abilityRequirement.total}）`, `Ability increase (total ${abilityRequirement.total})`) }}</legend><div class="ability-grid"><label v-for="ability in ABILITIES" :key="ability" :class="{ unavailable: !abilityAllowed(ability) }"><span>{{ ability.toUpperCase() }} <small>{{ abilityDiff[ability] }}</small></span><select :disabled="!abilityAllowed(ability)" :value="(choices.ability_score_increases as JsonObject)?.[ability] || 0" @change="setAbility(ability, Number(($event.target as HTMLSelectElement).value))"><option :value="0">+0</option><option :value="1">+1</option><option v-if="abilityRequirement.pattern === '2_or_1_1'" :value="2">+2</option></select></label></div></fieldset>

      <fieldset v-if="spellRequirement" class="spell-section"><legend>{{ text('调整职业法术', 'Update class spells') }}</legend><p class="spell-help">{{ text('每个区域标题都写着“已选 / 需要”。达到需要的数量后，其他选项会自动锁定；取消一个已选项即可重新选择。法师请先选法术书，再从法术书里选准备法术。', 'Each section shows selected / required. Once the required count is reached, extra options lock automatically; uncheck one to choose another. Wizards choose the spellbook first, then prepared spells from it.') }}</p><p v-if="spellBudgetText" class="spell-help spell-budget">{{ spellBudgetText }}</p><div v-if="Number(spellRequirement.cantrip_count)"><b>{{ text(`戏法：已选 ${selectedSpells('cantrip_refs').length} / 需要 ${spellRequirement.cantrip_count}`, `Cantrips: ${selectedSpells('cantrip_refs').length} selected / ${spellRequirement.cantrip_count} required`) }}</b><div class="spell-grid"><label v-for="spell in spellRows('cantrips')" :key="spell.ref"><input type="checkbox" :disabled="spellDisabled('cantrip_refs', spell)" :checked="selectedSpells('cantrip_refs').includes(spell.ref)" @change="toggleSpell('cantrip_refs', spell)"> {{ spell.name }}</label></div></div><div v-if="Number(spellRequirement.spellbook_minimum)"><b>{{ text(`法术书：已选 ${selectedSpells('spellbook_refs').length} / 需要 ${spellLimit('spellbook_refs')}`, `Spellbook: ${selectedSpells('spellbook_refs').length} selected / ${spellLimit('spellbook_refs')} required`) }}</b><div class="spell-grid"><label v-for="spell in spellRows('leveled_spells')" :key="`book-${spell.ref}`"><input type="checkbox" :disabled="spellDisabled('spellbook_refs', spell)" :checked="selectedSpells('spellbook_refs').includes(spell.ref)" @change="toggleSpell('spellbook_refs', spell)"> {{ spell.name }} <small>Lv.{{ spell.level }}</small></label></div></div><div v-if="Number(spellRequirement.prepared_spell_count)"><b>{{ text(`准备法术：已选 ${selectedSpells('prepared_spell_refs').length} / 需要 ${spellRequirement.prepared_spell_count}`, `Prepared: ${selectedSpells('prepared_spell_refs').length} selected / ${spellRequirement.prepared_spell_count} required`) }}</b><div class="spell-grid"><label v-for="spell in spellRows('leveled_spells')" :key="`prepared-${spell.ref}`" :class="{ unavailable: preparedDisabled(spell) || spellDisabled('prepared_spell_refs', spell) }"><input type="checkbox" :disabled="spellDisabled('prepared_spell_refs', spell)" :checked="selectedSpells('prepared_spell_refs').includes(spell.ref)" @change="toggleSpell('prepared_spell_refs', spell)"> {{ spell.name }} <small>Lv.{{ spell.level }}</small></label></div></div></fieldset>

      <fieldset><legend>{{ text('本级变化', 'Level changes') }}</legend><div class="feature-list"><span v-for="feature in (diff.gained_feature_ids as string[]) || []" :key="feature">{{ featureName(feature) }}</span></div><p v-if="Object.keys(slotChanges).length">{{ text('法术位变化', 'Spell slot changes') }}: <code>{{ JSON.stringify(slotChanges) }}</code></p></fieldset>

      <div v-if="preview.errors.length" class="errors" role="alert"><b>{{ text('升级还不能确认', 'Upgrade is not ready') }}</b><p class="error-help">{{ text('下面每一条都告诉你缺什么；按提示补齐后，系统会自动重新核对。', 'Each item below tells you what is missing. The panel rechecks automatically after you make a choice.') }}</p><ul><li v-for="message in preview.errors" :key="message">{{ friendlySpellError(message) }}</li></ul></div>
    </template>
    <p v-if="error" class="errors" role="alert">{{ error }}</p>
    <footer><button type="button" @click="emit('cancel')">{{ text('取消', 'Cancel') }}</button><button type="button" class="primary" :disabled="busy || !preview?.ok" @click="apply">{{ busy ? text('核对中…', 'Checking…') : chained ? text('确认本级', 'Apply level') : text('确认升级', 'Apply level') }}</button></footer>
  </section>
</template>

<style scoped>
.advancement-panel { display: grid; gap: 16px; color: #e8edf5; }
.advancement-panel > header { display: flex; justify-content: space-between; gap: 16px; }
.advancement-panel header span { color: #d5a44f; font-size: 11px; letter-spacing: .16em; }
.advancement-panel h3 { margin: 3px 0; font-family: Georgia, serif; font-size: 27px; }
.advancement-panel header p { margin: 0; color: #aeb8c6; }.advancement-panel header .flow-hint { margin-top: 6px; font-size: 13px; line-height: 1.5; }.chain-target { color: #d5a44f; }
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
