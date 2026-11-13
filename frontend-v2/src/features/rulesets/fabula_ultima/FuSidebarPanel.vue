<script setup lang="ts">
// Fabula Ultima 的局内面板：冲突步骤、GM 工具（遭遇 / 战利品 / 旅行 / 手动叙事 / 休息）、
// 机会与商人。由 features/rulesets/registry.ts 按 runtime id 挂到通用侧栏的
// after-character 插槽；通用组件里不出现任何 FU 分支。
import { computed, ref, watch } from 'vue'
import { Activity } from 'lucide-vue-next'
import type { GameDetail, Player } from '@/api/types'
import { submitRulesetIntent } from '@/api/rulesets'
import SidebarRowIcon from '@/components/SidebarRowIcon.vue'
import { useFuCopy, type FuCopyKey } from './copy'

const props = defineProps<{ detail: GameDetail; player?: Player; players?: Player[]; isGm?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useFuCopy()
const sheet = computed<Record<string, unknown>>(() => (props.player?.character_sheet as Record<string, unknown>) || {})

interface FuEnemy { id: string; name: string; hp: number; max_hp: number; defense: number; defending?: boolean; skills?: Array<{ name: string; text?: string; scope?: string; cooldown?: number }> }
interface FuSkillView {
  name: string
  level: number
  known: boolean
  kind: string
  spell: boolean
  action: string
  mp: number
  hp: number
  scope: string
  count: number
  choices?: string[]
  elements?: string[]
  boost_max?: number
  text: string
}
interface FuTargetView {
  id: string
  name: string
  hp: number
  max_hp: number
}
interface FuConflictView {
  status?: string
  round: number
  step?: number
  manual_steps?: boolean
  can_reroll?: boolean
  reroll_actor?: string
  selectable?: Array<{ id: string; name: string; side: string }>
  current: string
  current_name?: string
  actor?: { key: string; name: string; control?: string; skills?: FuSkillView[]; items?: Array<{ name: string; type: string; damage?: number; armor?: number }>; used_action?: boolean; used_quick?: boolean }
  paused?: boolean
  targets?: { enemies?: FuTargetView[]; allies?: FuTargetView[] }
  enemies: Record<string, FuEnemy>
  clocks: Record<string, { name: string; segments: number; filled: number }>
  actions: Array<{ id: string; label: string; needs_target: boolean }>
}
interface FuMonster { id: string; profile_id: string; name: string; rank?: string }
const ENEMY_TEMPLATES: Array<Omit<FuMonster, 'name'> & { nameKey: FuCopyKey }> = [
  { id: 'slime', profile_id: 'slime', nameKey: 'monsterSlime', rank: 'minion' },
  { id: 'goblin', profile_id: 'goblin', nameKey: 'monsterGoblin', rank: 'minion' },
  { id: 'orc', profile_id: 'orc', nameKey: 'monsterOrc', rank: 'minion' },
  { id: 'skeleton', profile_id: 'skeleton', nameKey: 'monsterSkeleton', rank: 'minion' },
  { id: 'golem', profile_id: 'golem', nameKey: 'monsterGolem', rank: 'elite' },
  { id: 'whelp', profile_id: 'whelp', nameKey: 'monsterWhelp', rank: 'elite' },
  { id: 'assassin', profile_id: 'assassin', nameKey: 'monsterAssassin', rank: 'minion' },
  { id: 'mage', profile_id: 'mage', nameKey: 'monsterMage', rank: 'minion' },
  { id: 'captain', profile_id: 'captain', nameKey: 'monsterCaptain', rank: 'elite' },
  { id: 'witch', profile_id: 'witch', nameKey: 'monsterWitch', rank: 'boss' },
]
const fuView = computed<Record<string, unknown>>(() => {
  const raw = (props.detail as unknown as { fabula_ultima?: Record<string, unknown> }).fabula_ultima
  return raw && typeof raw === 'object' ? raw : {}
})
const isFuRule = computed(() => String(props.detail.rule_id || '') === 'fabula_ultima')
const fuMonsters = computed<FuMonster[]>(() => {
  const raw = fuView.value.encounter_catalog
  return Array.isArray(raw) && raw.length
    ? raw as FuMonster[]
    : ENEMY_TEMPLATES.map(({ nameKey, ...monster }) => ({ ...monster, name: t(nameKey) }))
})
interface FuItemView { id: string; name: string; category?: string; type?: string; damage?: number; armor?: number; price?: number }
const fuItemCatalog = computed<FuItemView[]>(() => {
  const raw = fuView.value.item_catalog
  return Array.isArray(raw) ? raw as FuItemView[] : []
})
const fuLootItems = computed(() => fuItemCatalog.value.filter(item => item.category !== 'consumable'))
interface FuMerchantGood { item_id: string; name: string; price: number; stock: number }
interface FuMerchantView {
  active?: boolean
  goods: FuMerchantGood[]
  order: string[]
  index: number
  round: number
  skipped: string[]
  current: string
  current_name: string
  participants: Array<{ user_id: string; name: string }>
}
const fuMerchant = computed<FuMerchantView | null>(() => {
  const raw = fuView.value.merchant
  return raw && typeof raw === 'object' ? raw as FuMerchantView : null
})
interface FuMerchantRow { key: number; itemId: string; price: number; stock: number }
let fuMerchantRowKey = 1
const fuMerchantRows = ref<FuMerchantRow[]>([{ key: 0, itemId: '', price: 0, stock: 1 }])
const fuMerchantMessage = ref('')
function addMerchantRow() {
  fuMerchantRows.value = [...fuMerchantRows.value, { key: fuMerchantRowKey++, itemId: '', price: 0, stock: 1 }]
}
function removeMerchantRow(key: number) {
  const rows = fuMerchantRows.value.filter(row => row.key !== key)
  fuMerchantRows.value = rows.length ? rows : [{ key: fuMerchantRowKey++, itemId: '', price: 0, stock: 1 }]
}
const fuGold = computed(() => {
  const raw = sheet.value.gold
  if (typeof raw === 'number') return raw
  const currency = sheet.value.currency
  if (currency && typeof currency === 'object') return Number((currency as Record<string, unknown>).amount || 0)
  return 0
})
async function startMerchant() {
  if (!props.isGm) return
  const goods = fuMerchantRows.value
    .filter(row => row.itemId)
    .map(row => ({ item_id: row.itemId, price: Math.max(0, Number(row.price) || 0), stock: Math.max(1, Number(row.stock) || 1) }))
  if (!goods.length) {
    fuError.value = t('merchantNeedsGoods')
    return
  }
  const summary = await submitFuIntent({ type: 'merchant.start', goods })
  if (summary !== null) fuMerchantMessage.value = summary || t('merchantOpened')
}
async function buyMerchant(itemId: string) {
  const summary = await submitFuIntent({ type: 'merchant.buy', item_id: itemId })
  if (summary !== null) fuMerchantMessage.value = summary || t('merchantBought')
}
async function skipMerchant() {
  const summary = await submitFuIntent({ type: 'merchant.skip' })
  if (summary !== null) fuMerchantMessage.value = summary || t('merchantSkipped')
}
interface FuLootRow { key: number; itemId: string; difficulty: number; amount: number }
let fuLootRowKey = 1
const fuLootRows = ref<FuLootRow[]>([{ key: 0, itemId: '', difficulty: 1, amount: 1 }])
const fuLootResult = ref('')
function addLootRow() {
  fuLootRows.value = [...fuLootRows.value, { key: fuLootRowKey++, itemId: '', difficulty: 1, amount: 1 }]
}
function removeLootRow(key: number) {
  const rows = fuLootRows.value.filter(row => row.key !== key)
  fuLootRows.value = rows.length ? rows : [{ key: fuLootRowKey++, itemId: '', difficulty: 1, amount: 1 }]
}
interface FuEncounterRow { key: number; profileId: string }
let fuEncounterRowKey = 1
const fuEncounterRows = ref<FuEncounterRow[]>([{ key: 0, profileId: '' }])
function addEncounterRow() {
  fuEncounterRows.value = [...fuEncounterRows.value, { key: fuEncounterRowKey++, profileId: '' }]
}
function removeEncounterRow(key: number) {
  const rows = fuEncounterRows.value.filter(row => row.key !== key)
  fuEncounterRows.value = rows.length ? rows : [{ key: fuEncounterRowKey++, profileId: '' }]
}
const fuConflict = computed<FuConflictView | null>(() => {
  const raw = fuView.value.conflict
  if (!raw || typeof raw !== 'object') return null
  const conflict = raw as FuConflictView
  if (conflict.status && conflict.status !== 'active') return null
  return conflict
})
const fuEnemies = computed(() => Object.values(fuConflict.value?.enemies || {}))
const fuClocks = computed(() => Object.entries(fuConflict.value?.clocks || {}))
const fuActions = computed(() => fuConflict.value?.actions || [])
const fuEnemyTarget = ref('')
const fuEnemyAction = ref('attack')
const fuEnemySkill = ref('')
const fuCurrentEnemy = computed(() => fuEnemies.value.find(enemy => `enemy:${enemy.id}` === fuConflict.value?.current) || null)
watch(fuCurrentEnemy, (enemy) => {
  fuEnemyAction.value = 'attack'
  fuEnemySkill.value = enemy?.skills?.[0]?.name || ''
}, { immediate: true })
const fuAllies = computed(() => fuConflict.value?.targets?.allies || [])
watch(fuAllies, (list) => {
  if (!list.some(target => target.id === fuEnemyTarget.value)) fuEnemyTarget.value = list[0]?.id || ''
}, { immediate: true })
function selectFuActor(actorId: string) {
  if (!props.isGm || !actorId) return
  void sendFuIntent({ type: 'conflict.select_actor', actor_id: actorId })
}
function enemyAct() {
  if (!props.isGm) return
  const intent: Record<string, unknown> = { type: 'conflict.enemy_action', action: fuEnemyAction.value }
  if (['attack', 'hinder'].includes(fuEnemyAction.value)) intent.target = fuEnemyTarget.value
  if (fuEnemyAction.value === 'skill') {
    intent.skill = fuEnemySkill.value
    intent.target = fuEnemyTarget.value
  }
  void sendFuIntent(intent)
}
function enemyAiAction() {
  if (!props.isGm) return
  void sendFuIntent({ type: 'conflict.ai_action' })
}
interface FuAdventureNode {
  id: string
  type: string
  name: string
  description?: string
  encounter_ref?: string
  choices?: Array<{ choice_id: string; label: string; to: string }>
}
const fuAdventure = computed<{ name?: string; active?: FuAdventureNode[]; ending?: string; suggestions?: Array<{ proposal_id: string; node_id: string; reason: string }> } | null>(() => {
  const raw = fuView.value.adventure
  return raw && typeof raw === 'object' ? raw as { name?: string; active?: FuAdventureNode[]; ending?: string; suggestions?: Array<{ proposal_id: string; node_id: string; reason: string }> } : null
})
const fuAction = ref('attack')
const fuTarget = ref('')
const fuSkill = ref('')
const fuSkillChoice = ref('')
const fuSkillElement = ref('fire')
const fuSkillAttribute = ref('dex')
const fuSkillBoost = ref(false)
const fuGmOverride = ref(false)
const fuOpportunityTarget = ref('')
const fuOpportunityStatus = ref('shaken')
const fuOpportunityTargets = computed(() => {
  const targets = fuConflict.value?.targets
  const combatTargets = [...(targets?.allies || []), ...(targets?.enemies || [])]
  if (combatTargets.length) return combatTargets
  return (props.players || []).map(player => ({
    id: `player:${player.user_id}`,
    name: player.character_name || player.user_id,
    hp: 0,
    max_hp: 0,
  }))
})
const fuTargets = ref<string[]>([])
const fuEquipItem = ref('')
const fuResourceItem = ref('potion')
const fuBusy = ref(false)
const fuError = ref('')
const fuNotice = ref('')
const fuSkills = computed<FuSkillView[]>(() => (fuConflict.value?.actor?.skills || []).filter(skill => skill.kind !== 'passive' && (skill.kind !== 'reaction' || skill.name === '挺身守护')))
const SKILL_CHOICE_LABELS: Record<string, FuCopyKey> = {
  daze: 'statusDazed', weak: 'statusWeak', mp: 'choiceDrainMp', shaken: 'statusShaken', slow: 'statusSlow',
  shield: 'choiceBreakShield', armor: 'choiceBreakArmor', vulnerable: 'choiceVulnerable',
}
function skillChoiceLabel(id: string): string { return SKILL_CHOICE_LABELS[id] ? t(SKILL_CHOICE_LABELS[id]) : id }
function skillOptionLabel(skill: FuSkillView): string {
  const detail = `${skill.action === 'quick' ? t('kindQuick') : t('kindActive')}${skill.mp ? ` · MP ${skill.mp}` : ''}${skill.hp ? ` · HP ${skill.hp}` : ''}`
  return t('skillOption', { name: skill.name, detail })
}
const SPELL_ELEMENTS = computed(() => [
  { id: 'fire', name: t('elementFire') }, { id: 'ice', name: t('elementIce') }, { id: 'lightning', name: t('elementLightning') },
  { id: 'light', name: t('elementLight') }, { id: 'dark', name: t('elementDark') },
])
const fuItems = computed(() => fuConflict.value?.actor?.items || [])
const RESOURCE_ITEMS = computed(() => [
  { id: 'potion', name: t('resourcePotion') },
  { id: 'remedy', name: t('resourceRemedy') },
  { id: 'bomb', name: t('resourceBomb') },
])
const fuSelectedSkill = computed(() => fuSkills.value.find(skill => skill.name === fuSkill.value) || null)
const fuIsSkill = computed(() => fuAction.value === 'skill')
const fuActorIsAi = computed(() => fuConflict.value?.actor?.control === 'ai')
const fuActorSpent = computed(() => Boolean(fuConflict.value?.actor?.used_action))
const fuTurnLocked = computed(() => Boolean(fuActorSpent.value || (fuActorIsAi.value && !fuGmOverride.value && !fuConflict.value?.paused)))
const fuScope = computed(() => (fuIsSkill.value ? fuSelectedSkill.value?.scope || 'enemy' : 'enemy'))
const fuMaxTargets = computed(() => (fuIsSkill.value ? Math.max(1, fuSelectedSkill.value?.count || 1) : 1))
const fuTargetList = computed<FuTargetView[]>(() => {
  const targets = fuConflict.value?.targets
  if (!targets) return []
  if (fuAction.value === 'resource') {
    return fuResourceItem.value === 'bomb' ? (targets.enemies || []) : (targets.allies || [])
  }
  if (fuAction.value === 'defend') return targets.allies || []
  if (['ally', 'allies', 'all_allies', 'self_or_ally'].includes(fuScope.value)) return targets.allies || []
  return targets.enemies || []
})
const fuNeedsTarget = computed(() => {
  if (fuIsSkill.value) return !['self', 'all_enemies', 'all_allies'].includes(fuScope.value)
  if (fuAction.value === 'resource') return true
  if (fuAction.value === 'equip') return false
  if (fuAction.value === 'defend' || fuAction.value === 'investigate') return true
  return ['attack', 'hinder'].includes(fuAction.value)
})
watch(() => fuConflict.value?.current, () => { fuError.value = ''; fuNotice.value = '' })
watch(fuEnemies, (list) => {
  if (!list.length) { fuTarget.value = ''; return }
  if (!list.some(enemy => enemy.id === fuTarget.value)) fuTarget.value = list[0].id
}, { immediate: true })
watch(fuActions, (list) => {
  if (!list.length) return
  if (!list.some(action => action.id === fuAction.value)) fuAction.value = list[0].id
}, { immediate: true })
watch(fuSkills, (list) => {
  if (!list.length) { fuSkill.value = ''; return }
  if (!list.some(skill => skill.name === fuSkill.value)) fuSkill.value = list[0].name
}, { immediate: true })
watch(fuItems, (list) => {
  if (!list.length) { fuEquipItem.value = ''; return }
  if (!list.some(item => item.name === fuEquipItem.value)) fuEquipItem.value = list[0].name
}, { immediate: true })
watch([fuAction, fuSkill, fuResourceItem], () => {
  fuTargets.value = []
  const list = fuTargetList.value
  if (!list.length) return
  if (['ally', 'allies', 'all_allies', 'self_or_ally'].includes(fuScope.value)) {
    const lowest = [...list].sort((a, b) => (a.hp / Math.max(1, a.max_hp)) - (b.hp / Math.max(1, b.max_hp)))[0]
    fuTarget.value = lowest?.id || list[0].id
    return
  }
  if (!list.some(item => item.id === fuTarget.value)) fuTarget.value = list[0].id
})
watch(fuSelectedSkill, (skill) => {
  fuSkillChoice.value = skill?.choices?.[0] || ''
  fuSkillElement.value = 'fire'
  fuSkillBoost.value = false
})
function intentSummary(response: unknown): string {
  const payload = response as { summary?: unknown; result?: { summary?: unknown } }
  const summary = payload.result?.summary ?? payload.summary
  return Array.isArray(summary) ? summary.join(t('summarySeparator')) : ''
}

/** GM 工具与购物都走规则意图；成功返回服务端摘要，失败返回 null 并显示错误。 */
async function submitFuIntent(intent: Record<string, unknown>): Promise<string | null> {
  fuError.value = ''
  try {
    const response = await submitRulesetIntent(props.detail.game_key, intent)
    emit('changed')
    return intentSummary(response)
  } catch (e: unknown) {
    fuError.value = e instanceof Error ? e.message : String(e)
    return null
  }
}

async function sendFuIntent(intent: Record<string, unknown>) {
  if (fuBusy.value) {
    fuError.value = t('intentBusy')
    return
  }
  fuBusy.value = true
  fuError.value = ''
  fuNotice.value = ''
  try {
    const response = await submitRulesetIntent(props.detail.game_key, intent)
    fuNotice.value = intentSummary(response) || t('actionDone')
    emit('changed')
  } catch (e: unknown) {
    fuError.value = e instanceof Error ? e.message : String(e)
  } finally {
    fuBusy.value = false
  }
}
async function applyOpportunity(kind: 'advantage' | 'affliction') {
  if (!props.isGm || !fuOpportunityTarget.value) return
  const summary = await submitFuIntent({
    type: 'opportunity.apply',
    kind,
    target: fuOpportunityTarget.value,
    status: fuOpportunityStatus.value,
  })
  if (summary) fuNotice.value = summary
}
const fuNarrationText = ref('')
const fuTravelDestination = ref('')
const fuTravelDie = ref(8)
const fuRestConfirm = ref(false)
async function distributeLoot() {
  if (!props.isGm) return
  const pool = fuLootRows.value
    .filter(row => row.itemId)
    .map(row => row.itemId === 'gold'
      ? { item_id: 'gold', amount: Math.max(1, Number(row.amount) || 1), difficulty: Number(row.difficulty) || 1 }
      : { item_id: row.itemId, difficulty: Number(row.difficulty) || 1 })
  if (!pool.length) {
    fuError.value = t('lootNeedsItems')
    return
  }
  const summary = await submitFuIntent({ type: 'loot.distribute', pool })
  if (summary !== null) fuLootResult.value = summary || t('lootDistributed')
}
async function travel() {
  if (!props.isGm) return
  const summary = await submitFuIntent({
    type: 'travel.roll',
    destination: fuTravelDestination.value.trim(),
    die: fuTravelDie.value,
  })
  if (summary === null) return
  fuNotice.value = summary || t('travelRecorded')
  fuTravelDestination.value = ''
}
async function addManualNarration() {
  if (!props.isGm || !fuNarrationText.value.trim()) return
  const summary = await submitFuIntent({ type: 'narration.add', text: fuNarrationText.value.trim() })
  if (summary !== null) fuNarrationText.value = ''
}
function restParty() {
  if (!props.isGm) return
  if (!fuRestConfirm.value) {
    fuRestConfirm.value = true
    return
  }
  fuRestConfirm.value = false
  void sendFuIntent({ type: 'rest.take' })
}
function startConflict() {
  const enemies = fuEncounterRows.value
    .map(row => row.profileId)
    .filter(Boolean)
    .map(profileId => ({ template_id: profileId }))
  if (!enemies.length) {
    fuError.value = t('encounterNeedsMonster')
    return
  }
  void sendFuIntent({ type: 'conflict.start', enemies })
}
function startAdventureEncounter(node: FuAdventureNode) {
  void sendFuIntent({ type: 'conflict.start', encounter_ref: node.encounter_ref, node_id: node.id })
}
function completeAdventureNode(node: FuAdventureNode, choiceId = '') {
  const intent: Record<string, unknown> = { type: 'adventure.node.complete', node_id: node.id }
  if (choiceId) intent.choice_id = choiceId
  void sendFuIntent(intent)
}
function completeSuggestedNode(nodeId: string) {
  void sendFuIntent({ type: 'adventure.node.complete', node_id: nodeId })
}
function dismissSuggestion(proposalId: string) {
  void sendFuIntent({ type: 'adventure.completion_suggestion.dismiss', proposal_id: proposalId })
}
function runFuAction() {
  const intent: Record<string, unknown> = { type: 'conflict.action', action: fuAction.value }
  const actorKey = fuConflict.value?.actor?.key
  if (actorKey) intent.actor_id = actorKey
  if (fuIsSkill.value) {
    intent.skill = fuSkill.value
    if (fuSelectedSkill.value?.choices?.length) intent.choice = fuSkillChoice.value
    if (fuSelectedSkill.value?.spell) {
      intent.element = fuSkillElement.value
      if (fuSkillBoost.value) intent.boost = 10
    }
    if (fuTargets.value.length) intent.targets = [...fuTargets.value]
    else if (fuNeedsTarget.value && fuTarget.value) intent.targets = [fuTarget.value]
  } else if (fuAction.value === 'equip') {
    intent.item = fuEquipItem.value
  } else if (fuAction.value === 'resource') {
    intent.item = fuResourceItem.value
    if (fuTarget.value) intent.target = fuTarget.value
  } else if (fuNeedsTarget.value && fuTarget.value) {
    intent.target = fuTarget.value
  }
  if (fuAction.value === 'objective' && fuClocks.value.length) intent.clock_id = fuClocks.value[0][0]
  void sendFuIntent(intent)
}
function endConflict() {
  void sendFuIntent({ type: 'conflict.end' })
}
function rerollConflict() {
  void sendFuIntent({ type: 'conflict.reroll' })
}
</script>

<template>
    <details v-if="isFuRule" class="panel sidebar-disclosure" :open="Boolean(fuConflict)">
      <summary><SidebarRowIcon :icon="Activity" /><strong>{{ t('conflict') }}</strong><span v-if="fuConflict">{{ t('stepNumber', { step: fuConflict.step || 0 }) }}</span></summary>
      <div class="sidebar-disclosure-body">
        <template v-if="fuConflict">
          <div v-if="isGm" class="growth-row">
            <strong>{{ t('selectActor') }}</strong>
          </div>
          <div v-if="isGm" class="growth-row">
            <button
              v-for="actor in (fuConflict.selectable || [])"
              :key="actor.id"
              type="button"
              class="growth-btn"
              :class="{ active: fuConflict.current === actor.id }"
              :disabled="fuBusy"
              @click="selectFuActor(actor.id)"
            >{{ actor.name }}</button>
          </div>
          <div v-if="isGm" class="growth-row">
            <button type="button" class="growth-btn" :disabled="fuBusy" @click="endConflict">{{ t('endConflict') }}</button>
            <button
              v-if="fuConflict.can_reroll"
              type="button"
              class="growth-btn"
              :disabled="fuBusy"
              @click="rerollConflict"
            >{{ t('fabulaReroll') }}</button>
          </div>
          <p v-if="!fuConflict.current" class="muted">{{ t('gmSelectsActor') }}</p>
          <template v-if="fuConflict.current">
          <p class="muted">{{ t('currentActor', { name: fuConflict.current_name || fuConflict.current || '—' }) }}</p>
          <template v-if="fuConflict.current.startsWith('enemy:')">
            <p class="muted">{{ t('monsterManualAction') }}</p>
            <label class="growth-row">{{ t('action') }}
              <select v-model="fuEnemyAction" class="growth-select">
                <option value="attack">{{ t('actionAttack') }}</option>
                <option value="skill">{{ t('skill') }}</option>
                <option value="defend">{{ t('actionDefend') }}</option>
                <option value="hinder">{{ t('actionHinder') }}</option>
                <option value="objective">{{ t('actionObjective') }}</option>
                <option value="investigate">{{ t('actionInvestigate') }}</option>
              </select>
            </label>
            <label v-if="fuEnemyAction === 'skill'" class="growth-row">{{ t('skill') }}
              <select v-model="fuEnemySkill" class="growth-select">
                <option v-for="skill in (fuCurrentEnemy?.skills || [])" :key="skill.name" :value="skill.name">{{ skill.name }}</option>
              </select>
            </label>
            <label v-if="['attack', 'skill', 'hinder'].includes(fuEnemyAction)" class="growth-row">{{ t('target') }}
              <select v-model="fuEnemyTarget" class="growth-select">
                <option v-for="target in fuAllies" :key="target.id" :value="target.id">{{ t('targetOption', { name: target.name, hp: target.hp, max: target.max_hp }) }}</option>
              </select>
            </label>
            <div class="growth-row">
              <button type="button" class="growth-btn" :disabled="fuBusy" @click="enemyAct">{{ t('execute') }}</button>
              <button type="button" class="growth-btn" :disabled="fuBusy" @click="enemyAiAction">{{ t('aiAction') }}</button>
            </div>
          </template>
          <template v-else>
          <p v-if="fuConflict.paused" class="muted">{{ t('pausedHint') }}</p>
          <p v-else-if="fuActorIsAi && !fuGmOverride" class="muted">{{ t('aiActing') }}</p>
          <label v-if="fuActorIsAi" class="growth-row fu-target-option">
            <input v-model="fuGmOverride" type="checkbox">
            {{ t('gmOverride') }}
          </label>
          <p v-if="fuConflict.actor?.used_quick" class="muted">{{ t('quickActionUsed') }}</p>
          <p v-if="fuActorSpent" class="muted">{{ t('turnSpent') }}</p>
          <p v-if="fuConflict.actor?.used_action" class="muted">{{ t('mainActionUsed') }}</p>
          <div v-for="enemy in fuEnemies" :key="enemy.id" class="growth-row">
            <strong>{{ enemy.name }}</strong>
            <span class="muted">{{ t('enemyStats', { hp: enemy.hp, max: enemy.max_hp, defense: enemy.defense }) }}</span>
          </div>
          <div v-for="[clockId, clock] in fuClocks" :key="clockId" class="muted">
            {{ clock.name }} {{ clock.filled }}/{{ clock.segments }}
          </div>
          <label class="growth-row">{{ t('action') }}
            <select v-model="fuAction" class="growth-select">
              <option v-for="action in fuActions" :key="action.id" :value="action.id">{{ action.label }}</option>
            </select>
          </label>
          <template v-if="fuIsSkill">
            <label class="growth-row">{{ t('skill') }}
              <select v-model="fuSkill" class="growth-select">
                <option v-for="skill in fuSkills" :key="skill.name" :value="skill.name">{{ skillOptionLabel(skill) }}</option>
              </select>
            </label>
            <p v-if="fuSelectedSkill" class="muted">{{ fuSelectedSkill.text }}</p>
            <label v-if="fuSelectedSkill?.choices?.length" class="growth-row">{{ t('effect') }}
              <select v-model="fuSkillChoice" class="growth-select">
                <option v-for="choice in fuSelectedSkill.choices" :key="choice" :value="choice">{{ skillChoiceLabel(choice) }}</option>
              </select>
            </label>
            <label v-if="fuSelectedSkill?.name === '鼓舞'" class="growth-row">{{ t('attributeDieBonus') }}
              <select v-model="fuSkillAttribute" class="growth-select">
                <option value="dex">{{ t('attrDex') }}</option><option value="ins">{{ t('attrIns') }}</option>
                <option value="mig">{{ t('attrMig') }}</option><option value="wlp">{{ t('attrWlp') }}</option>
              </select>
            </label>
            <template v-if="fuSelectedSkill?.elements?.length">
              <label class="growth-row">{{ t('element') }}
                <select v-model="fuSkillElement" class="growth-select">
                  <option v-for="element in SPELL_ELEMENTS.filter(item => fuSelectedSkill?.elements?.includes(item.id))" :key="element.id" :value="element.id">{{ element.name }}</option>
                </select>
              </label>
            </template>
            <label v-if="fuSelectedSkill?.boost_max" class="growth-row fu-target-option">
              <input v-model="fuSkillBoost" type="checkbox">
              {{ t('skillBoost', { mp: Math.min(10, fuSelectedSkill.boost_max) }) }}
            </label>
          </template>
          <label v-if="fuAction === 'equip'" class="growth-row">{{ t('equipment') }}
            <select v-model="fuEquipItem" class="growth-select">
              <option v-for="item in fuItems" :key="item.name" :value="item.name">{{ t('equipOption', { name: item.name, type: item.type }) }}</option>
            </select>
          </label>
          <label v-if="fuAction === 'resource'" class="growth-row">{{ t('inventory') }}
            <select v-model="fuResourceItem" class="growth-select">
              <option v-for="item in RESOURCE_ITEMS" :key="item.id" :value="item.id">{{ item.name }}</option>
            </select>
          </label>
          <label v-if="fuNeedsTarget && fuMaxTargets <= 1" class="growth-row">{{ t('target') }}
            <select v-model="fuTarget" class="growth-select">
              <option v-for="target in fuTargetList" :key="target.id" :value="target.id">{{ t('targetOption', { name: target.name, hp: target.hp, max: target.max_hp }) }}</option>
            </select>
          </label>
          <div v-else-if="fuNeedsTarget" class="growth-row fu-targets">
            <span class="muted">{{ t('targetsUpTo', { count: fuMaxTargets }) }}</span>
            <label v-for="target in fuTargetList" :key="target.id" class="fu-target-option">
              <input v-model="fuTargets" type="checkbox" :value="target.id">
              {{ t('targetOption', { name: target.name, hp: target.hp, max: target.max_hp }) }}
            </label>
          </div>
          <div class="growth-row">
            <button type="button" class="growth-btn" :disabled="fuBusy || fuTurnLocked" @click="runFuAction">{{ t('execute') }}</button>
            <button v-if="isGm && fuActorIsAi" type="button" class="growth-btn" :disabled="fuBusy" @click="enemyAiAction">{{ t('aiAction') }}</button>
            <button
              v-if="fuConflict.can_reroll && (isGm || fuConflict.reroll_actor === `player:${player?.user_id}`)"
              type="button"
              class="growth-btn"
              :disabled="fuBusy"
              @click="rerollConflict"
            >{{ t('fabulaReroll') }}</button>
          </div>
          </template>
          </template>
          <div v-if="fuConflict.can_reroll && (isGm || fuConflict.reroll_actor === `player:${player?.user_id}`)" class="growth-row">
            <button type="button" class="growth-btn" :disabled="fuBusy" @click="rerollConflict">{{ t('fabulaReroll') }}</button>
          </div>
        </template>
        <template v-else>
          <template v-if="fuAdventure">
            <p class="muted">{{ t('adventureName', { name: fuAdventure.name }) }}</p>
            <div v-for="node in (fuAdventure.active || [])" :key="node.id" class="growth-row fu-adventure-node">
              <strong>{{ node.name }}</strong>
              <span class="muted">{{ node.description }}</span>
              <div class="growth-row">
                <button v-if="node.type === 'encounter'" type="button" class="growth-btn" :disabled="fuBusy" @click="startAdventureEncounter(node)">{{ t('startEncounter') }}</button>
                <template v-else-if="node.type === 'decision'">
                  <button v-for="choice in node.choices || []" :key="choice.choice_id" type="button" class="growth-btn" :disabled="fuBusy" @click="completeAdventureNode(node, choice.choice_id)">{{ choice.label }}</button>
                </template>
                <button v-else type="button" class="growth-btn" :disabled="fuBusy" @click="completeAdventureNode(node)">{{ t('completeNode') }}</button>
              </div>
            </div>
            <p v-if="fuAdventure.ending" class="muted">{{ t('endingReached', { ending: fuAdventure.ending }) }}</p>
            <div v-for="suggestion in fuAdventure.suggestions || []" :key="suggestion.proposal_id" class="growth-row fu-adventure-node">
              <strong>{{ t('gmSuggestsNode', { node: suggestion.node_id }) }}</strong>
              <span class="muted">{{ suggestion.reason }}</span>
              <div class="growth-row">
                <button type="button" class="growth-btn" :disabled="fuBusy" @click="completeSuggestedNode(suggestion.node_id)">{{ t('completeNode') }}</button>
                <button type="button" class="growth-btn" :disabled="fuBusy" @click="dismissSuggestion(suggestion.proposal_id)">{{ t('dismiss') }}</button>
              </div>
            </div>
          </template>
          <p v-else class="muted">{{ t('noConflict') }}</p>
          <template v-if="isGm">
            <div class="growth-row"><strong>{{ t('encounter') }}</strong></div>
            <div v-for="row in fuEncounterRows" :key="row.key" class="growth-row fu-monster-row">
              <select v-model="row.profileId" class="growth-select">
                <option value="">{{ t('selectMonster') }}</option>
                <option v-for="monster in fuMonsters" :key="monster.profile_id" :value="monster.profile_id">{{ monster.name }}</option>
              </select>
              <button type="button" class="growth-btn" :disabled="fuBusy" @click="removeEncounterRow(row.key)">{{ t('remove') }}</button>
            </div>
            <div class="growth-row">
              <button type="button" class="growth-btn" :disabled="fuBusy" @click="addEncounterRow">{{ t('addMonster') }}</button>
            </div>
            <div class="growth-row">
              <button type="button" class="growth-btn" :disabled="fuBusy" @click="startConflict">{{ t('startConflict') }}</button>
              <button type="button" class="growth-btn" :disabled="fuBusy" @click="restParty">{{ fuRestConfirm ? t('confirmRest') : t('rest') }}</button>
            </div>
            <div class="growth-row">
              <input v-model="fuNarrationText" class="growth-input" :placeholder="t('narrationPlaceholder')">
              <button type="button" class="growth-btn" :disabled="!fuNarrationText.trim()" @click="addManualNarration">{{ t('insertNarration') }}</button>
            </div>
            <div class="growth-row">
              <input v-model="fuTravelDestination" class="growth-input" :placeholder="t('travelDestinationPlaceholder')">
              <select v-model.number="fuTravelDie" class="growth-select">
                <option :value="6">d6</option>
                <option :value="8">d8</option>
                <option :value="10">d10</option>
                <option :value="12">d12</option>
              </select>
              <button type="button" class="growth-btn" @click="travel">{{ t('travel') }}</button>
            </div>
            <div class="growth-row"><strong>{{ t('loot') }}</strong></div>
            <div v-for="row in fuLootRows" :key="row.key" class="growth-row fu-monster-row">
              <select v-model="row.itemId" class="growth-select">
                <option value="">{{ t('selectItem') }}</option>
                <option value="gold">{{ t('gold') }}</option>
                <option v-for="item in fuLootItems" :key="item.id" :value="item.id">{{ item.name }}</option>
              </select>
              <input v-if="row.itemId === 'gold'" v-model.number="row.amount" type="number" min="1" class="growth-input" :placeholder="t('goldAmount')">
              <input v-model.number="row.difficulty" type="number" min="1" max="20" class="growth-input" :placeholder="t('difficulty')">
              <button type="button" class="growth-btn" @click="removeLootRow(row.key)">{{ t('remove') }}</button>
            </div>
            <div class="growth-row">
              <button type="button" class="growth-btn" @click="addLootRow">{{ t('addLoot') }}</button>
              <button type="button" class="growth-btn" @click="distributeLoot">{{ t('distributeLoot') }}</button>
            </div>
            <p v-if="fuLootResult" class="muted">{{ fuLootResult }}</p>
            <template v-if="!fuMerchant?.active">
              <div class="growth-row"><strong>{{ t('merchant') }}</strong></div>
              <div v-for="row in fuMerchantRows" :key="row.key" class="growth-row fu-monster-row">
                <select v-model="row.itemId" class="growth-select" @change="row.price = Number(fuItemCatalog.find(item => item.id === row.itemId)?.price || 0)">
                  <option value="">{{ t('selectGoods') }}</option>
                  <option v-for="item in fuLootItems" :key="item.id" :value="item.id">{{ item.name }}</option>
                </select>
                <input v-model.number="row.price" type="number" min="0" class="growth-input" :placeholder="t('price')">
                <input v-model.number="row.stock" type="number" min="1" class="growth-input" :placeholder="t('stock')">
                <button type="button" class="growth-btn" @click="removeMerchantRow(row.key)">{{ t('remove') }}</button>
              </div>
              <div class="growth-row">
                <button type="button" class="growth-btn" @click="addMerchantRow">{{ t('addGoods') }}</button>
                <button type="button" class="growth-btn" @click="startMerchant">{{ t('openMerchant') }}</button>
              </div>
            </template>
          </template>
          <p v-else class="muted">{{ t('waitingForGm') }}</p>
        </template>
        <!-- 怪物 / 物品 / 技能数据表不进全局导航，从 FU 面板进入（服务端仍只允许 owner 编辑）。 -->
        <RouterLink v-if="isGm" class="growth-btn fu-data-link" to="/fu-data">{{ t('openDataTables') }}</RouterLink>
        <p v-if="fuNotice" class="muted">{{ fuNotice }}</p>
        <p v-if="fuError" class="form-hint warn">{{ fuError }}</p>
      </div>
    </details>

    <div v-if="isFuRule && isGm" class="panel fu-opportunity-panel">
      <strong>{{ t('opportunity') }}</strong>
      <div class="growth-row">
        <select v-model="fuOpportunityTarget" class="growth-select">
          <option value="">{{ t('selectTarget') }}</option>
          <option v-for="target in fuOpportunityTargets" :key="target.id" :value="target.id">{{ target.name }}</option>
        </select>
      </div>
      <div class="growth-row">
        <select v-model="fuOpportunityStatus" class="growth-select">
          <option value="dazed">{{ t('statusDazed') }}</option>
          <option value="shaken">{{ t('statusShaken') }}</option>
          <option value="slow">{{ t('statusSlow') }}</option>
          <option value="weak">{{ t('statusWeak') }}</option>
        </select>
        <button type="button" class="growth-btn" :disabled="!fuOpportunityTarget" @click="applyOpportunity('advantage')">{{ t('advantage') }}</button>
        <button type="button" class="growth-btn" :disabled="!fuOpportunityTarget" @click="applyOpportunity('affliction')">{{ t('affliction') }}</button>
      </div>
    </div>

    <div v-if="isFuRule && fuMerchant?.active" class="panel fu-merchant-panel">
      <strong>{{ t('merchantRound', { round: fuMerchant.round }) }}</strong>
      <p class="muted">{{ t('merchantCurrent', { name: fuMerchant.current_name }) }}</p>
      <div v-for="good in fuMerchant.goods" :key="good.item_id" class="growth-row">
        <span>{{ good.name }}</span>
        <span class="muted">{{ t('goodsLine', { price: good.price, stock: good.stock }) }}</span>
        <button
          type="button"
          class="growth-btn"
          :disabled="fuMerchant.current !== player?.user_id || fuGold < good.price"
          @click="buyMerchant(good.item_id)"
        >{{ t('buy') }}</button>
      </div>
      <div class="growth-row">
        <button
          type="button"
          class="growth-btn"
          :disabled="fuMerchant.current !== player?.user_id && !isGm"
          @click="skipMerchant"
        >{{ t('skip') }}</button>
      </div>
      <p v-if="fuMerchantMessage" class="muted">{{ fuMerchantMessage }}</p>
    </div>
</template>

<style scoped>
.growth-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 6px 0; font-size: 12px; }
.growth-row .muted { flex: 1 1 100%; margin: 0; }
.growth-select { flex: 1 1 auto; min-width: 0; font-size: 12px; }
.growth-btn { border: 1px solid rgba(127, 127, 127, 0.35); background: transparent; color: inherit; border-radius: 4px; padding: 2px 8px; font-size: 12px; cursor: pointer; }
.growth-btn:disabled { opacity: 0.45; cursor: default; }
.fu-adventure-node { flex-direction: column; align-items: stretch; gap: 4px; padding: 6px 0; border-bottom: 1px solid var(--df-border-soft, rgba(127,127,127,.2)); }
.fu-targets { flex-direction: column; align-items: stretch; gap: 4px; }
.fu-target-option { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.fu-opportunity-panel { display: flex; flex-direction: column; gap: 6px; padding: 10px; margin-top: 8px; }
.fu-merchant-panel { display: flex; flex-direction: column; gap: 6px; padding: 10px; margin-top: 8px; }
</style>
