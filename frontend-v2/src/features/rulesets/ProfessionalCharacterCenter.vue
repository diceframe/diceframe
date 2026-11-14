<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import type {
  CharacterClassFeature,
  CharacterClassResource,
  CharacterPortrait,
  CharacterSheet,
  JsonObject,
  RestSessionStatus,
  RulesetSpellOption,
  SpellPreparationOptions,
} from '@/api/types'
import PortraitPicker from '@/components/admin/PortraitPicker.vue'
import {
  fetchLiveCharacterSpellOptions,
  resolveLiveCharacterRest,
  type RestSubmissionOptions,
} from '@/api/rulesets'

const props = withDefaults(defineProps<{
  character: CharacterSheet
  target: 'game' | 'card'
  ruleId: string
  language?: string
  gameKey?: string
  userId?: string
  cardId?: string
  restSession?: RestSessionStatus | null
  initialFocus?: 'rest'
}>(), { language: 'zh-CN', gameKey: '', userId: '', cardId: '' })
const emit = defineEmits<{
  saved: [character: CharacterSheet, reason?: 'profile' | 'rest']
  'rest-pending': []
  cancel: []
}>()

type Tab = 'overview' | 'profile' | 'build' | 'magic'
const activeTab = ref<Tab>('overview')
const restCenter = ref<HTMLElement | null>(null)
watch(() => props.initialFocus, focus => {
  if (focus !== 'rest' || props.target !== 'game') return
  activeTab.value = 'magic'
  void nextTick(() => restCenter.value?.scrollIntoView?.({ block: 'nearest' }))
}, { immediate: true })
const busy = ref(false)
const failure = ref('')
const zh = computed(() => props.language.toLowerCase().startsWith('zh'))
const text = (cn: string, en: string) => zh.value ? cn : en
const canonical = computed<JsonObject>(() => {
  const value = props.character.ruleset_character
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {}
})
const identity = computed(() => (canonical.value.identity as JsonObject | undefined) || {})
const build = computed(() => (canonical.value.build as JsonObject | undefined) || {})
const abilities = computed(() => (canonical.value.abilities as JsonObject | undefined) || {})
const resources = computed(() => (canonical.value.resources as JsonObject | undefined) || {})
const derived = computed(() => (canonical.value.derived as JsonObject | undefined) || {})
const spellcasting = computed(() => (canonical.value.spellcasting as JsonObject | undefined) || {})
const classMagic = computed(() => (spellcasting.value.class as JsonObject | undefined) || {})
const progression = computed(() => (canonical.value.progression as JsonObject | undefined) || {})
const classLevels = computed(() => (build.value.class_levels as JsonObject[] | undefined) || [])
const hp = computed(() => Number(resources.value.hp || props.character.hp || 0))
const maxHp = computed(() => Number(resources.value.max_hp || props.character.max_hp || 0))
const restType = ref<'short' | 'long'>('short')
const restConfirmed = ref(false)
const restHitDice = reactive<Record<string, number>>({})
const hitDiceRows = computed(() => Object.entries(
  (resources.value.hit_dice as Record<string, number> | undefined) || {},
).map(([die, available]) => ({
  die,
  available: Number(available || 0),
  maximum: Number(classLevels.value[0]?.level || available || 0),
})))
const myRestStatus = computed(() => props.restSession?.participants?.find(row => row.user_id === props.userId)?.status || '')

// 长休换准备法术：容量、替换上限与合法性全部来自服务端策略，前端只负责选择与提示。
const spellOptions = ref<SpellPreparationOptions | null>(null)
const spellLoadError = ref('')
const spellDraft = reactive<{ prepared: string[]; cantrips: string[] }>({
  prepared: [],
  cantrips: [],
})
const spellPolicy = computed(() => spellOptions.value?.policy || null)
const canChangePrepared = computed(() => (
  restType.value === 'long'
  && !!spellPolicy.value
  && (
    spellPolicy.value.long_rest_replace === 'any'
    || Number(spellPolicy.value.long_rest_replace) > 0
  )
))
const canChangeCantrips = computed(() => (
  canChangePrepared.value && spellPolicy.value?.cantrip_swap_on === 'long_rest'
))
const spellLevelGroups = computed(() => {
  const groups = new Map<number, RulesetSpellOption[]>()
  for (const spell of spellOptions.value?.leveled_spells || []) {
    const list = groups.get(spell.level) || []
    list.push(spell)
    groups.set(spell.level, list)
  }
  return [...groups.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([level, spells]) => ({ level, spells }))
})
const preparedOld = computed(() => new Set(spellOptions.value?.prepared_spell_refs || []))
const cantripOld = computed(() => new Set(spellOptions.value?.cantrip_refs || []))
const preparedSwapLeft = computed(() => {
  const policy = spellPolicy.value
  if (!policy) return 0
  if (policy.long_rest_replace === 'any') return Number.POSITIVE_INFINITY
  const dropped = [...preparedOld.value]
    .filter(ref => !spellDraft.prepared.includes(ref)).length
  return Math.max(0, Number(policy.long_rest_replace) - dropped)
})
const cantripSwapLeft = computed(() => {
  if (!canChangeCantrips.value) return 0
  const dropped = [...cantripOld.value]
    .filter(ref => !spellDraft.cantrips.includes(ref)).length
  return Math.max(0, 1 - dropped)
})
const preparedReady = computed(() => (
  !canChangePrepared.value
  || spellDraft.prepared.length === Number(spellOptions.value?.prepared_capacity || 0)
))
const preparedChanged = computed(() => {
  if (!canChangePrepared.value) return false
  const same = (left: string[], right: string[]) => (
    [...left].sort().join('|') === [...right].sort().join('|')
  )
  return (
    !same(spellDraft.prepared, spellOptions.value?.prepared_spell_refs || [])
    || (
      canChangeCantrips.value
      && !same(spellDraft.cantrips, spellOptions.value?.cantrip_refs || [])
    )
  )
})
const preparedChangeHint = computed(() => {
  if (!canChangePrepared.value) return ''
  const capacity = Number(spellOptions.value?.prepared_capacity || 0)
  const swap = spellPolicy.value?.long_rest_replace
  if (spellDraft.prepared.length !== capacity) {
    return text(
      `已选 ${spellDraft.prepared.length} / ${capacity}，需要选满才能提交`,
      `${spellDraft.prepared.length} / ${capacity} selected; fill the capacity to submit`,
    )
  }
  if (swap !== 'any' && swap !== undefined && Number(swap) > 0) {
    return text(
      `本次最多替换 ${swap} 个旧法术`,
      `At most ${swap} old spell(s) may be replaced`,
    )
  }
  return text('可以自由重排准备列表', 'The list may be rearranged freely')
})

async function loadSpellOptions(): Promise<void> {
  if (props.target !== 'game' || !props.gameKey || !props.userId) return
  spellLoadError.value = ''
  try {
    const response = await fetchLiveCharacterSpellOptions(props.gameKey, props.userId)
    spellOptions.value = response.spells
    resetSpellDraft()
  } catch (cause: unknown) {
    spellLoadError.value = errorMessage(cause)
  }
}

function resetSpellDraft(): void {
  spellDraft.prepared = [...(spellOptions.value?.prepared_spell_refs || [])]
  spellDraft.cantrips = [...(spellOptions.value?.cantrip_refs || [])]
}

function togglePrepared(ref: string): void {
  const index = spellDraft.prepared.indexOf(ref)
  if (index >= 0) {
    // 未准备的旧法术只能在下手名额内换掉。
    if (preparedOld.value.has(ref) && preparedSwapLeft.value < 1) return
    spellDraft.prepared.splice(index, 1)
    return
  }
  if (spellDraft.prepared.length >= Number(spellOptions.value?.prepared_capacity || 0)) return
  spellDraft.prepared.push(ref)
}

function toggleCantrip(ref: string): void {
  const index = spellDraft.cantrips.indexOf(ref)
  if (index >= 0) {
    if (cantripOld.value.has(ref) && cantripSwapLeft.value < 1) return
    spellDraft.cantrips.splice(index, 1)
    return
  }
  const capacity = Number(spellOptions.value?.cantrip_capacity || 0)
    || spellDraft.cantrips.length
  if (spellDraft.cantrips.length >= capacity) return
  spellDraft.cantrips.push(ref)
}

// 职业能力与职业资源完全来自服务端投影：前端不判断职业等级、不算武艺骰、
// 不算资源上限，只渲染 name / current / maximum。
const classFeatures = computed<CharacterClassFeature[]>(() => (
  Array.isArray(props.character.class_features) ? props.character.class_features : []
))
const classResourceRows = computed(() => (
  (Array.isArray(props.character.class_resources) ? props.character.class_resources : [])
    .filter(row => Number(row.maximum) > 0)
))
function featureValueText(feature: CharacterClassFeature): string {
  const values = (feature.values || {}) as Record<string, unknown>
  const parts: string[] = []
  const die = values.unarmed_damage_die
  if (typeof die === 'string' && die) parts.push(`${text('武艺骰', 'Martial Arts die')} ${die}`)
  const abilities = values.unarmed_ability_choice
  if (Array.isArray(abilities) && abilities.length) {
    parts.push(`${text('攻击属性', 'Attack ability')} ${abilities.map(item => abilityName(String(item))).join(' / ')}`)
  }
  return parts.join(' · ')
}
function resourcePercent(resource: CharacterClassResource): number {
  const maximum = Number(resource.maximum || 0)
  if (maximum <= 0) return 0
  return Math.min(100, Math.max(0, Number(resource.current || 0) / maximum * 100))
}

const form = reactive<{
  character_name: string
  portrait: CharacterPortrait | null
  profile: Record<string, string>
}>({
  character_name: '',
  portrait: null,
  profile: {},
})

watch(
  () => props.character,
  character => {
    const nested = character.ruleset_character as JsonObject | undefined
    const nestedIdentity = (nested?.identity as JsonObject | undefined) || {}
    const profile = (nested?.profile as JsonObject | undefined) || {}
    form.character_name = String(nestedIdentity.name || character.character_name || '')
    form.portrait = character.portrait?.kind ? { ...character.portrait } : null
    form.profile = Object.fromEntries(
      ['pronouns', 'appearance', 'personality', 'backstory', 'ideals', 'bonds', 'flaws', 'notes']
        .map(key => [key, String(profile[key] || '')]),
    )
  },
  { immediate: true, deep: true },
)
watch(
  () => [props.restSession?.active, props.restSession?.rest] as const,
  ([active, rest]) => {
    if (active && (rest === 'short' || rest === 'long')) {
      restType.value = rest
      activeTab.value = 'magic'
    }
  },
  { immediate: true },
)
// 准备法术候选只在玩家真的切到长休时拉一次；短休不需要，也不必每次开卡都多打一次请求。
watch(restType, value => {
  if (value !== 'long' || spellOptions.value || spellLoadError.value) return
  void loadSpellOptions()
}, { immediate: true })

const abilityLabels: Record<string, [string, string]> = {
  str: ['力量', 'Strength'], dex: ['敏捷', 'Dexterity'], con: ['体质', 'Constitution'],
  int: ['智力', 'Intelligence'], wis: ['感知', 'Wisdom'], cha: ['魅力', 'Charisma'],
}
const alignmentLabels: Record<string, [string, string]> = {
  lawful_good: ['守序善良', 'Lawful Good'], neutral_good: ['中立善良', 'Neutral Good'],
  chaotic_good: ['混乱善良', 'Chaotic Good'], lawful_neutral: ['守序中立', 'Lawful Neutral'],
  true_neutral: ['绝对中立', 'True Neutral'], neutral: ['中立', 'Neutral'],
  chaotic_neutral: ['混乱中立', 'Chaotic Neutral'], lawful_evil: ['守序邪恶', 'Lawful Evil'],
  neutral_evil: ['中立邪恶', 'Neutral Evil'], chaotic_evil: ['混乱邪恶', 'Chaotic Evil'],
}
const referenceLabels: Record<string, [string, string]> = {
  human: ['人类', 'Human'], dwarf: ['矮人', 'Dwarf'], elf: ['精灵', 'Elf'], halfling: ['半身人', 'Halfling'],
  dragonborn: ['龙裔', 'Dragonborn'], gnome: ['侏儒', 'Gnome'], goliath: ['歌利亚', 'Goliath'], orc: ['兽人', 'Orc'], tiefling: ['提夫林', 'Tiefling'],
  soldier: ['士兵', 'Soldier'], acolyte: ['侍僧', 'Acolyte'], artisan: ['工匠', 'Artisan'], charlatan: ['江湖骗子', 'Charlatan'],
  criminal: ['罪犯', 'Criminal'], entertainer: ['艺人', 'Entertainer'], farmer: ['农夫', 'Farmer'], guard: ['卫兵', 'Guard'],
  guide: ['向导', 'Guide'], hermit: ['隐士', 'Hermit'], merchant: ['商人', 'Merchant'], noble: ['贵族', 'Noble'], sage: ['贤者', 'Sage'], sailor: ['水手', 'Sailor'], scribe: ['抄写员', 'Scribe'], wayfarer: ['漂泊者', 'Wayfarer'],
  small: ['小型', 'Small'], medium: ['中型', 'Medium'],
  barbarian: ['野蛮人', 'Barbarian'], bard: ['吟游诗人', 'Bard'], cleric: ['牧师', 'Cleric'], druid: ['德鲁伊', 'Druid'],
  fighter: ['战士', 'Fighter'], monk: ['武僧', 'Monk'], paladin: ['圣武士', 'Paladin'], ranger: ['游侠', 'Ranger'],
  rogue: ['游荡者', 'Rogue'], sorcerer: ['术士', 'Sorcerer'], warlock: ['邪术师', 'Warlock'], wizard: ['法师', 'Wizard'],
  single_class: ['单职业', 'Single class'], multiclass: ['多职业', 'Multiclass'],
  acrobatics: ['体操', 'Acrobatics'], animal_handling: ['驯兽', 'Animal Handling'], arcana: ['奥秘', 'Arcana'], athletics: ['运动', 'Athletics'],
  deception: ['欺瞒', 'Deception'], history: ['历史', 'History'], insight: ['洞悉', 'Insight'], intimidation: ['威吓', 'Intimidation'],
  investigation: ['调查', 'Investigation'], medicine: ['医药', 'Medicine'], nature: ['自然', 'Nature'], perception: ['察觉', 'Perception'],
  performance: ['表演', 'Performance'], persuasion: ['游说', 'Persuasion'], religion: ['宗教', 'Religion'], sleight_of_hand: ['巧手', 'Sleight of Hand'],
  stealth: ['隐匿', 'Stealth'], survival: ['求生', 'Survival'],
}
function refName(value: unknown): string {
  const key = String(value || '').split(':').at(-1) || ''
  if (!key) return '—'
  const label = referenceLabels[key]
  return label ? (zh.value ? label[0] : label[1]) : key.replaceAll('_', ' ')
}
function alignmentName(value: unknown): string {
  const key = String(value || '')
  const label = alignmentLabels[key]
  return label ? (zh.value ? `${label[0]}（${label[1]}）` : label[1]) : refName(value)
}
function abilityName(key: string): string {
  const label = abilityLabels[key]
  return label ? (zh.value ? `${label[0]}（${key.toUpperCase()}）` : label[1]) : key.toUpperCase()
}
function modifier(score: unknown): string {
  const value = Math.floor((Number(score || 10) - 10) / 2)
  return value >= 0 ? `+${value}` : String(value)
}
function spellRefs(key: string): string[] {
  return ((classMagic.value[key] as string[] | undefined) || []).map(refName)
}
// 起源专长（魔法学徒）授予的戏法与 1 环法术：独立于职业法术，按所选施法属性施放。
const featSpellRows = computed(() => {
  const rows = (spellcasting.value.magic_initiate as JsonObject[] | undefined) || []
  return rows.map(row => ({
    feat: refName(row.feat_ref),
    ability: abilityName(String(row.spellcasting_ability || '')),
    cantrips: ((row.cantrip_ids as string[] | undefined) || []).map(refName),
    spell: row.level_1_spell_id ? refName(row.level_1_spell_id) : '',
    freeCastUsed: Boolean(row.free_cast_used),
  }))
})

const spellSlotRows = computed(() => {
  const current = (classMagic.value.slots_current as Record<string, unknown> | undefined) || {}
  const maximum = (classMagic.value.slots_max as Record<string, unknown> | undefined) || {}
  const levels = [...new Set([...Object.keys(current), ...Object.keys(maximum)])]
    .filter(level => /^\d+$/.test(level))
    .sort((a, b) => Number(a) - Number(b))
  return levels.map(level => ({
    level,
    current: Number(current[level] || 0),
    maximum: Number(maximum[level] || 0),
  }))
})

async function save(): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    const path = props.target === 'card'
      ? `/character-cards/${encodeURIComponent(props.cardId)}/profile`
      : `/games/${encodeURIComponent(props.gameKey)}/character/${encodeURIComponent(props.userId)}/profile`
    const response = await api<{ ok: boolean; character?: CharacterSheet; card?: CharacterSheet; error?: string; error_code?: string }>(path, {
      method: 'PATCH',
      body: JSON.stringify({
        character_name: form.character_name,
        portrait: form.portrait,
        profile: form.profile,
      }),
    })
    if (!response.ok) {
      if (response.error_code === 'PROFESSIONAL_CHARACTER_BLUEPRINT_REQUIRED') {
        throw new Error(text(
          '这个专业角色缺少规则数据，请重新打开当前规则的专业建卡器完成后再保存。',
          'This professional character is missing its rules data. Reopen the current professional character builder and finish it before saving.',
        ))
      }
      throw new Error(response.error || text('保存失败', 'Save failed'))
    }
    emit('saved', response.character || response.card || props.character, 'profile')
  } catch (cause: unknown) {
    failure.value = errorMessage(cause)
  } finally { busy.value = false }
}

function operationId(prefix: string): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function completeRest(): Promise<void> {
  if (props.target !== 'game' || !props.gameKey || !props.userId || !restConfirmed.value) return
  busy.value = true
  failure.value = ''
  try {
    const spend = Object.fromEntries(
      hitDiceRows.value.map(({ die, available }) => [
        die,
        Math.max(0, Math.min(available, Number(restHitDice[die] || 0))),
      ]),
    )
    // 没有改动就整块不发：服务端会拒绝「换了个寂寞」的提交。
    const options: RestSubmissionOptions | null = preparedChanged.value
      ? {
          preparedSpells: {
            prepared_spell_refs: [...spellDraft.prepared],
            ...(canChangeCantrips.value ? { cantrip_refs: [...spellDraft.cantrips] } : {}),
          },
        }
      : null
    const response = await resolveLiveCharacterRest(
      props.gameKey,
      props.userId,
      restType.value,
      spend,
      Number(props.character.ruleset_revision || 0),
      operationId('rest'),
      options,
    )
    if (response.pending && !response.resolved) {
      restConfirmed.value = false
      emit('rest-pending')
      return
    }
    emit('saved', response.character as CharacterSheet, 'rest')
    // 结算后旧候选已经过期；重拉一次，草稿跟着新的准备列表重置。
    void loadSpellOptions()
  } catch (cause: unknown) {
    failure.value = errorMessage(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="professional-character-center">
    <header class="center-hero">
      <div>
        <span>5E · 2024 · SRD</span>
        <h2>{{ form.character_name || text('未命名冒险者', 'Unnamed adventurer') }}</h2>
      </div>
    </header>

    <nav class="center-tabs" :aria-label="text('角色资料分类', 'Character sections')">
      <button :class="{ active: activeTab === 'overview' }" @click="activeTab = 'overview'">{{ text('基础信息', 'Basic information') }}</button>
      <button :class="{ active: activeTab === 'profile' }" @click="activeTab = 'profile'">{{ text('人物资料', 'Profile') }}</button>
      <button :class="{ active: activeTab === 'build' }" @click="activeTab = 'build'">{{ text('构筑与成长', 'Build & growth') }}</button>
      <button :class="{ active: activeTab === 'magic' }" @click="activeTab = 'magic'">{{ text('法术与资源', 'Magic & resources') }}</button>
    </nav>

    <div class="center-scroll-region">
      <div v-if="activeTab === 'overview'" class="center-panel overview-panel">
      <div class="vital-grid">
        <article><small>{{ text('等级', 'Level') }}</small><strong>{{ build.level || props.character.level || 1 }}</strong><span>{{ refName(classLevels[0]?.class_ref || props.character.class) }}</span></article>
        <article><small>{{ text('生命值', 'Hit Points') }}</small><strong>{{ hp }}/{{ maxHp }}</strong><span>{{ text('归零会陷入濒死', 'At zero, you begin dying') }}</span></article>
        <article><small>{{ text('护甲等级', 'Armor Class') }}</small><strong>{{ derived.armor_class || '—' }}</strong><span>{{ text('敌人通常要达到此数值才能命中', 'Enemies usually need this result to hit') }}</span></article>
        <article><small>{{ text('熟练加值', 'Proficiency') }}</small><strong>+{{ derived.proficiency_bonus || 0 }}</strong><span>{{ text('你擅长的检定会加上它', 'Added to things you are trained in') }}</span></article>
      </div>
      <div class="ability-grid">
        <article v-for="(score, key) in abilities" :key="String(key)">
          <small>{{ abilityName(String(key)) }}</small><strong>{{ score }}</strong><span>{{ modifier(score) }}</span>
        </article>
      </div>
      <section v-if="classFeatures.length" class="class-feature-section">
        <div class="section-heading">
          <h3>{{ text('职业能力', 'Class features') }}</h3>
          <span>{{ text('来自当前职业与等级', 'From your current class and level') }}</span>
        </div>
        <article v-for="feature in classFeatures" :key="feature.id" class="class-feature-card">
          <header><strong>{{ feature.name }}</strong><small v-if="featureValueText(feature)">{{ featureValueText(feature) }}</small></header>
          <p v-if="feature.summary">{{ feature.summary }}</p>
        </article>
      </section>
      </div>

      <form v-else-if="activeTab === 'profile'" class="center-panel profile-panel" @submit.prevent="save">
      <p class="safe-edit-note">{{ text('以下都是叙事资料，可随时修改；不会重算属性、装备、生命值或法术。', 'Everything here is narrative profile data and never recalculates abilities, equipment, HP, or spells.') }}</p>
      <label>{{ text('角色名', 'Character name') }}<input v-model="form.character_name" maxlength="120" required></label>
      <PortraitPicker v-model="form.portrait" :rule-id="ruleId" :seed="cardId || userId || form.character_name" :name="form.character_name" />
      <div class="profile-grid">
        <label>{{ text('称谓 / 代词', 'Pronouns') }}<input v-model="form.profile.pronouns" :placeholder="text('例如：她 / he / they', 'For example: she / he / they')"></label>
        <label>{{ text('外貌特征', 'Appearance') }}<textarea v-model="form.profile.appearance" rows="3" :placeholder="text('别人第一眼会注意到什么？', 'What do people notice first?')"></textarea></label>
        <label class="profile-wide">{{ text('性格与习惯', 'Personality') }}<textarea v-model="form.profile.personality" rows="3" :placeholder="text('紧张时、开心时会怎么做？', 'How do they act when nervous or happy?')"></textarea></label>
        <label class="profile-wide">{{ text('人物经历', 'Backstory') }}<textarea v-model="form.profile.backstory" rows="5" :placeholder="text('从哪里来？为什么踏上冒险？', 'Where did they come from, and why do they adventure?')"></textarea></label>
        <label>{{ text('理想', 'Ideals') }}<textarea v-model="form.profile.ideals" rows="2" :placeholder="text('最相信什么？', 'What do they believe in most?')"></textarea></label>
        <label>{{ text('牵绊', 'Bonds') }}<textarea v-model="form.profile.bonds" rows="2" :placeholder="text('最在意的人、地点或承诺', 'A person, place, or promise that matters')"></textarea></label>
        <label>{{ text('弱点', 'Flaws') }}<textarea v-model="form.profile.flaws" rows="2" :placeholder="text('什么会让他做出不理智的选择？', 'What leads them into bad decisions?')"></textarea></label>
        <label>{{ text('仅供自己记录', 'Personal notes') }}<textarea v-model="form.profile.notes" rows="2"></textarea></label>
      </div>
      </form>

      <div v-else-if="activeTab === 'build'" class="center-panel read-only-panel">
      <p class="locked-note">{{ text('这些字段共同决定角色规则能力。为避免角色失效，此处只读；升级请使用“职业升级”。', 'These fields determine the legal build and are read-only here. Use Class advancement to level up safely.') }}</p>
      <dl>
        <div><dt>{{ text('物种', 'Species') }}</dt><dd>{{ refName(identity.species_ref) }}</dd></div>
        <div><dt>{{ text('背景', 'Background') }}</dt><dd>{{ refName(identity.background_ref) }}</dd></div>
        <div><dt>{{ text('阵营', 'Alignment') }}</dt><dd>{{ alignmentName(identity.alignment) }}</dd></div>
        <div><dt>{{ text('体型', 'Size') }}</dt><dd>{{ refName(identity.size) }}</dd></div>
        <div><dt>{{ text('职业', 'Class') }}</dt><dd>{{ classLevels.map(row => `${refName(row.class_ref)} Lv.${row.level}`).join(' / ') || '—' }}</dd></div>
        <div><dt>{{ text('升级方式', 'Progression') }}</dt><dd>{{ refName(progression.mode) }}</dd></div>
      </dl>
      <h3>{{ text('熟练项', 'Proficiencies') }}</h3>
      <div class="tag-list"><span v-for="skill in ((canonical.proficiencies as JsonObject)?.skill_refs as string[]) || []" :key="skill">{{ refName(skill) }}</span></div>
      <h3>{{ text('规则来源', 'Rules source') }}</h3>
      <code>{{ (canonical.rule_binding as JsonObject)?.content_version || '—' }}</code>
      </div>

      <div v-else class="center-panel read-only-panel magic-panel">
      <p class="locked-note">{{ text('法术位和职业资源会在战斗、休息与升级时由规则引擎更新，不需要手工计算。', 'Spell slots and class resources are updated by combat, rest, and advancement rules.') }}</p>
      <dl>
        <div><dt>{{ text('施法关键属性', 'Spellcasting ability') }}</dt><dd>{{ abilityName(String(classMagic.ability || '')) }}</dd></div>
        <div><dt>{{ text('法术攻击', 'Spell attack') }}</dt><dd>+{{ derived.spell_attack_bonus || 0 }}</dd></div>
        <div><dt>{{ text('法术豁免 DC', 'Spell save DC') }}</dt><dd>{{ derived.spell_save_dc || '—' }}</dd></div>
      </dl>
      <section v-if="classResourceRows.length" class="resource-section class-resource-section">
        <div class="section-heading"><h3>{{ text('职业资源', 'Class resources') }}</h3><span>{{ text('当前 / 上限', 'Current / max') }}</span></div>
        <div class="spell-slot-grid">
          <article v-for="resource in classResourceRows" :key="resource.id" class="spell-slot-card">
            <small>{{ resource.name }}</small>
            <strong>{{ resource.current }} <span>/ {{ resource.maximum }}</span></strong>
            <div class="slot-meter" aria-hidden="true"><i :style="{ width: `${resourcePercent(resource)}%` }"></i></div>
          </article>
        </div>
      </section>
      <section class="resource-section">
        <div class="section-heading"><h3>{{ text('法术位', 'Spell slots') }}</h3><span>{{ text('当前 / 上限', 'Current / max') }}</span></div>
        <div v-if="spellSlotRows.length" class="spell-slot-grid">
          <article v-for="slot in spellSlotRows" :key="slot.level" class="spell-slot-card">
            <small>{{ text(`${slot.level} 环`, `Level ${slot.level}`) }}</small>
            <strong>{{ slot.current }} <span>/ {{ slot.maximum }}</span></strong>
            <div class="slot-meter" aria-hidden="true"><i :style="{ width: `${slot.maximum ? Math.min(100, Math.max(0, slot.current / slot.maximum * 100)) : 0}%` }"></i></div>
          </article>
        </div>
        <p v-else class="empty-resource">{{ text('当前没有可用法术位（戏法不消耗法术位）。', 'No spell slots at this level (cantrips do not use slots).') }}</p>
      </section>
      <section class="resource-section hit-dice-resource">
        <div class="section-heading"><h3>{{ text('生命骰', 'Hit Dice') }}</h3><span>{{ text('短休可用于恢复生命值', 'Used to recover HP during a short rest') }}</span></div>
        <div v-if="hitDiceRows.length" class="spell-slot-grid">
          <article v-for="row in hitDiceRows" :key="row.die" class="spell-slot-card">
            <small>{{ row.die }}</small>
            <strong>{{ row.available }} <span>/ {{ row.maximum }}</span></strong>
            <div class="slot-meter" aria-hidden="true"><i :style="{ width: `${row.maximum ? Math.min(100, Math.max(0, row.available / row.maximum * 100)) : 0}%` }"></i></div>
          </article>
        </div>
        <p v-else class="empty-resource">{{ text('当前没有生命骰数据。', 'No Hit Dice data at this level.') }}</p>
      </section>
      <h3>{{ text('戏法', 'Cantrips') }}</h3><div class="tag-list"><span v-for="spell in spellRefs('cantrip_refs')" :key="spell">{{ spell }}</span><i v-if="!spellRefs('cantrip_refs').length">{{ text('此职业当前没有职业戏法', 'No class cantrips at this level') }}</i></div>
      <h3>{{ text('已准备法术', 'Prepared spells') }}</h3><div class="tag-list"><span v-for="spell in spellRefs('prepared_spell_refs')" :key="spell">{{ spell }}</span><i v-if="!spellRefs('prepared_spell_refs').length">{{ text('此职业当前没有准备法术', 'No prepared spells at this level') }}</i></div>
      <template v-for="row in featSpellRows" :key="row.feat">
        <h3>{{ text('起源专长法术', 'Origin feat spells') }} · {{ row.feat }}<small> · {{ text('施法属性', 'Spellcasting ability') }} {{ row.ability }}</small></h3>
        <div class="tag-list">
          <span v-for="spell in row.cantrips" :key="spell">{{ spell }} · {{ text('戏法', 'Cantrip') }}</span>
          <span v-if="row.spell">{{ row.spell }} · {{ row.freeCastUsed ? text('免费施放已用（长休恢复）', 'Free cast used (Long Rest)') : text('可免费施放 1 次', '1 free cast ready') }}</span>
        </div>
      </template>
      <section v-if="target === 'game'" ref="restCenter" class="rest-center">
        <div><h3>{{ text('需要恢复？在这里完成休息', 'Need to recover? Complete a rest here') }}</h3><p>{{ text('你只选择休息类型和要花几颗生命骰；实际骰点、生命值、法术位和职业资源都由服务端计算。', 'Choose the rest type and how many Hit Dice to spend. The server rolls and updates HP, spell slots, and class resources.') }}</p></div>
        <div v-if="restSession?.active || restSession?.status === 'completed'" class="party-rest-status" role="status">
          <strong>{{ restSession.status === 'completed' ? text('队伍休息已完成', 'Party rest completed') : text(`队伍${restSession.rest === 'short' ? '短休' : '长休'}准备：${restSession.ready_count}/${restSession.active_count}`, `Party ${restSession.rest === 'short' ? 'short' : 'long'} rest: ${restSession.ready_count}/${restSession.active_count} ready`) }}</strong>
          <span v-for="row in restSession.participants" :key="row.user_id" :class="row.status">{{ row.character_name }} · {{ row.status === 'submitted' ? text('已准备', 'Ready') : text('等待', 'Waiting') }}</span>
        </div>
        <div class="rest-types">
          <label class="rest-type-option" :class="{ selected: restType === 'short' }"><input v-model="restType" type="radio" value="short" :disabled="restSession?.active"> <span><b>{{ text('短休', 'Short Rest') }}</b><small>{{ text('花生命骰回血，并恢复部分职业资源', 'Spend Hit Dice and recover some class resources') }}</small></span></label>
          <label class="rest-type-option" :class="{ selected: restType === 'long' }"><input v-model="restType" type="radio" value="long" :disabled="restSession?.active"> <span><b>{{ text('长休', 'Long Rest') }}</b><small>{{ text('回满生命值、生命骰、法术位与大部分资源', 'Restore HP, Hit Dice, spell slots, and most resources') }}</small></span></label>
        </div>
        <div v-if="restType === 'short' && hitDiceRows.length" class="hit-dice-grid">
          <label v-for="row in hitDiceRows" :key="row.die"><span>{{ row.die }} · {{ text(`可用 ${row.available}`, `${row.available} available`) }}</span><input v-model.number="restHitDice[row.die]" type="number" min="0" :max="row.available"></label>
        </div>
        <p v-if="restType === 'short'" class="server-roll-note">{{ text('不用自己填骰点：DiceFrame 会在服务端掷生命骰，刷新或重试也不会重复结算。', 'Do not enter roll results: DiceFrame rolls on the server, and retries cannot apply the rest twice.') }}</p>
        <section v-if="canChangePrepared" class="rest-preparation">
          <div class="section-heading">
            <h3>{{ text('长休结束时的法术准备', 'Spell preparation after this long rest') }}</h3>
            <span>{{ text(`准备位 ${spellDraft.prepared.length} / ${spellOptions?.prepared_capacity || 0}`, `${spellDraft.prepared.length} / ${spellOptions?.prepared_capacity || 0} prepared`) }}</span>
          </div>
          <p class="server-roll-note">{{ preparedChangeHint }}</p>
          <p v-if="spellLoadError" class="center-error" role="alert">{{ spellLoadError }}</p>
          <div v-if="canChangeCantrips" class="spell-pick-group">
            <h4>{{ text('戏法', 'Cantrips') }}<small>{{ text('长休可替换 1 个', 'One may be replaced after this rest') }}</small></h4>
            <div class="spell-chip-grid">
              <button v-for="spell in spellOptions?.cantrips || []" :key="spell.ref" type="button" class="spell-chip" :class="{ selected: spellDraft.cantrips.includes(spell.ref) }" @click="toggleCantrip(spell.ref)">{{ spell.name }}</button>
            </div>
          </div>
          <div v-for="group in spellLevelGroups" :key="group.level" class="spell-pick-group">
            <h4>{{ text(`${group.level} 环法术`, `Level ${group.level} spells`) }}</h4>
            <div class="spell-chip-grid">
              <button v-for="spell in group.spells" :key="spell.ref" type="button" class="spell-chip" :class="{ selected: spellDraft.prepared.includes(spell.ref) }" @click="togglePrepared(spell.ref)">{{ spell.name }}</button>
            </div>
          </div>
          <button type="button" class="spell-undo" :disabled="!preparedChanged" @click="resetSpellDraft">{{ text('撤销法术改动', 'Undo spell changes') }}</button>
        </section>
        <div class="rest-actions">
          <label class="rest-confirm"><input v-model="restConfirmed" type="checkbox"><span>{{ text('我确认游戏内时间会随本次休息推进', 'I understand that in-game time advances during this rest') }}</span></label>
        </div>
        <button type="button" class="primary rest-submit" :disabled="busy || !restConfirmed || !preparedReady || myRestStatus === 'submitted' || restSession?.status === 'resolving'" @click="completeRest">{{ busy ? text('结算中…', 'Resolving…') : myRestStatus === 'submitted' ? text('已提交，等待队伍', 'Submitted, waiting for party') : restSession?.status === 'resolving' ? text('队伍结算中…', 'Resolving party rest…') : text('确认并结算休息', 'Confirm and resolve rest') }}</button>
      </section>
      </div>

      <p v-if="failure" class="center-error" role="alert">{{ failure }}</p>
    </div>
    <footer>
      <button type="button" @click="emit('cancel')">{{ text('关闭', 'Close') }}</button>
      <button v-if="activeTab === 'profile'" type="button" class="primary" :disabled="busy || !form.character_name.trim()" @click="save">{{ busy ? text('保存中…', 'Saving…') : text('保存人物资料', 'Save profile') }}</button>
    </footer>
  </section>
</template>

<style scoped>
.professional-character-center { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; gap: 12px; width: 100%; min-width: 0; min-height: 0; overflow: hidden; color: var(--df-text); }
.center-hero { display: flex; justify-content: space-between; gap: 18px; padding: 4px 2px; }
.center-hero span { color: var(--df-accent); font-size: 12px; letter-spacing: .14em; }
.center-hero h2 { margin: 4px 0; font: 700 30px/1.15 Georgia, serif; }
.center-hero p { max-width: 720px; margin: 0; color: var(--df-text-muted); }
.center-hero > button { align-self: start; width: 44px; min-width: 44px; border-radius: 50%; font-size: 22px; }
.center-tabs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; z-index: 2; padding: 6px; border-radius: 14px; background: color-mix(in srgb, var(--df-surface-1) 92%, transparent); backdrop-filter: blur(12px); }
.center-tabs button.active { border-color: var(--df-accent); background: color-mix(in srgb, var(--df-accent) 16%, transparent); color: var(--df-accent-strong); }
.center-scroll-region { min-width: 0; min-height: 0; padding: 2px 8px 12px 2px; overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.center-panel { display: grid; gap: 16px; min-height: 320px; }
.vital-grid, .ability-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.vital-grid article, .ability-grid article { display: grid; gap: 4px; padding: 14px; border: 1px solid var(--df-border-soft); border-radius: 14px; background: color-mix(in srgb, var(--df-surface-1) 90%, var(--df-accent) 4%); }
.vital-grid strong { font-size: 24px; }.ability-grid strong { font-size: 22px; }
.vital-grid small, .ability-grid small, .vital-grid span { color: var(--df-text-muted); }
.ability-grid { grid-template-columns: repeat(6, 1fr); }.ability-grid article { text-align: center; }
.safe-edit-note, .locked-note { padding: 14px 16px; border-left: 4px solid var(--df-interactive); border-radius: 8px; background: color-mix(in srgb, var(--df-interactive) 11%, transparent); }
.profile-panel label { display: grid; gap: 6px; min-width: 0; }.profile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.profile-grid .profile-wide { grid-column: 1 / -1; }
.profile-panel input, .profile-panel textarea { width: 100%; min-width: 0; box-sizing: border-box; }.profile-panel textarea { min-height: 74px; resize: vertical; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }
.read-only-panel dl { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 0; }.read-only-panel dl div { padding: 12px; border: 1px solid var(--df-border-soft); border-radius: 10px; }.read-only-panel dt { color: var(--df-text-muted); }.read-only-panel dd { margin: 4px 0 0; font-weight: 700; }
.tag-list { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0; }.tag-list span { max-width: 100%; padding: 5px 9px; border: 1px solid var(--df-border-soft); border-radius: 999px; overflow-wrap: anywhere; }.tag-list i { color: var(--df-text-muted); }
.magic-panel { gap: 12px; }.magic-panel h3 { margin: 4px 0 0; }
.class-feature-section { display: grid; gap: 10px; }
.class-feature-section .section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.class-feature-section .section-heading h3 { margin: 0; }
.class-feature-section .section-heading span { color: var(--df-text-muted); font-size: 12px; }
.class-feature-card { display: grid; gap: 5px; padding: 12px 14px; border: 1px solid var(--df-border-soft); border-radius: 12px; background: color-mix(in srgb, var(--df-surface-1) 90%, var(--df-accent) 4%); }
.class-feature-card header { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.class-feature-card header small { color: var(--df-accent-strong); }
.class-feature-card p { margin: 0; color: var(--df-text-muted); line-height: 1.5; }
.class-resource-section .spell-slot-card strong { color: var(--df-accent-strong); }
.resource-section { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--df-border-soft); border-radius: 12px; background: color-mix(in srgb, var(--df-surface-1) 92%, var(--df-accent) 4%); }.section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }.section-heading h3 { margin: 0; }.section-heading span { color: var(--df-text-muted); font-size: 12px; }.spell-slot-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; }.spell-slot-card { display: grid; gap: 6px; padding: 10px; border: 1px solid var(--df-border-soft); border-radius: 9px; background: color-mix(in srgb, var(--df-control-bg) 72%, transparent); }.spell-slot-card small { color: var(--df-text-muted); }.spell-slot-card strong { font-size: 20px; }.spell-slot-card strong span { color: var(--df-text-muted); font-size: 14px; font-weight: 400; }.slot-meter { height: 4px; overflow: hidden; border-radius: 99px; background: color-mix(in srgb, var(--df-text) 12%, transparent); }.slot-meter i { display: block; height: 100%; border-radius: inherit; background: var(--df-accent); }.empty-resource { margin: 0; color: var(--df-text-muted); }
.rest-center { display: grid; gap: 10px; margin-top: 8px; padding: 13px; border: 1px solid color-mix(in srgb, var(--df-accent) 58%, var(--df-border-soft)); border-radius: 12px; background: color-mix(in srgb, var(--df-accent) 8%, transparent); }.rest-center h3, .rest-center p { margin: 0; }.rest-center p { color: var(--df-text-muted); }.rest-types { display: grid; grid-template-columns: repeat(2, minmax(0, 300px)); justify-content: start; gap: 8px; }.rest-type-option { display: grid; grid-template-columns: 16px minmax(0, 1fr); align-items: start; gap: 8px; min-height: 52px; padding: 8px 10px; box-sizing: border-box; border: 1px solid var(--df-border-soft); border-radius: 8px; cursor: pointer; transition: border-color .16s ease, background-color .16s ease; }.rest-type-option:hover { border-color: var(--df-accent); }.rest-type-option.selected { border-color: var(--df-accent); background: color-mix(in srgb, var(--df-accent) 12%, transparent); }.rest-type-option input[type="radio"] { flex: 0 0 16px; width: 16px; height: 16px; min-width: 16px; margin: 2px 0 0; accent-color: var(--df-accent); cursor: pointer; }.rest-type-option span { display: grid; gap: 1px; min-width: 0; text-align: left; }.rest-type-option b { line-height: 1.35; }.rest-type-option small { color: var(--df-text-muted); line-height: 1.35; }.party-rest-status { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 9px 10px; border: 1px solid color-mix(in srgb, var(--df-accent) 45%, transparent); border-radius: 8px; background: color-mix(in srgb, var(--df-accent) 10%, transparent); }.party-rest-status strong { flex-basis: 100%; }.party-rest-status span { color: var(--df-text-muted); font-size: 12px; }.party-rest-status span.submitted { color: var(--df-accent-strong); }.hit-dice-grid { display: flex; flex-wrap: wrap; gap: 9px; }.hit-dice-grid label { display: grid; gap: 5px; }.hit-dice-grid input { width: 92px; max-width: 100%; }.server-roll-note { font-size: 12px; }.rest-actions { display: block; padding-top: 4px; }.rest-submit { justify-self: end; }.rest-confirm { display: grid; grid-template-columns: 18px minmax(0, 1fr); align-items: start; gap: 9px; min-width: 0; }.rest-confirm input { width: 18px; height: 18px; margin: 1px 0 0; }.rest-confirm span { line-height: 1.45; }
.center-error { margin: 12px 0 0; padding: 10px; border-radius: 8px; background: color-mix(in srgb, var(--df-danger) 16%, transparent); color: var(--df-danger-strong); }.professional-character-center footer { display: flex; flex: 0 0 auto; justify-content: flex-end; gap: 10px; margin: 0; padding: 12px 2px 2px; border-top: 1px solid var(--df-border-soft); background: var(--df-surface-1); }
.rest-preparation { display: grid; gap: 9px; padding: 10px; border: 1px solid var(--df-border-soft); border-radius: 10px; background: color-mix(in srgb, var(--df-control-bg) 72%, transparent); }.spell-pick-group { display: grid; gap: 6px; }.spell-pick-group h4 { display: flex; align-items: baseline; gap: 8px; margin: 0; font-size: 13px; }.spell-pick-group h4 small { color: var(--df-text-muted); font-weight: 400; }.spell-chip-grid { display: flex; flex-wrap: wrap; gap: 6px; }.spell-chip { padding: 5px 10px; border: 1px solid var(--df-border-soft); border-radius: 999px; background: transparent; color: var(--df-text); font-size: 12px; cursor: pointer; }.spell-chip:hover { border-color: var(--df-accent); }.spell-chip.selected { border-color: var(--df-accent); background: color-mix(in srgb, var(--df-accent) 18%, transparent); color: var(--df-accent-strong); }.spell-undo { justify-self: start; padding: 5px 10px; font-size: 12px; }
@media (max-width: 720px) { .professional-character-center { width: 100%; gap: 9px; }.center-hero p { display: none; }.center-hero h2 { font-size: 22px; }.center-tabs { grid-template-columns: 1fr 1fr; }.center-scroll-region { padding-right: 3px; }.vital-grid { grid-template-columns: 1fr 1fr; }.ability-grid { grid-template-columns: repeat(3, 1fr); }.profile-grid, .read-only-panel dl { grid-template-columns: 1fr; }.profile-grid .profile-wide { grid-column: auto; }.rest-submit { width: 100%; }.professional-character-center footer button { min-height: 40px; } }
@media (max-width: 520px) { .rest-types { grid-template-columns: 1fr; } }
</style>
