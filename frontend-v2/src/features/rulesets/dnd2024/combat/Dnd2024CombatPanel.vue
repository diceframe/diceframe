<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import {
  FlashOutline,
  FootstepsOutline,
  PlayForwardOutline,
  RibbonOutline,
  ShieldOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import {
  fetchRulesetAvailableActions,
  resolveRulesetDecision,
  submitRulesetIntent,
} from '@/features/rulesets/dnd2024/api'
import type {
  CharacterClassResource,
  JsonObject,
  RulesetCombatAction,
  RulesetCombatReactionFeature,
  RulesetCombatSpell,
  RulesetCombatTarget,
  RulesetCombatWeapon,
  RulesetGameplayResponse,
  RulesetPendingDecision,
} from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import CombatLiveBar from '@/components/play/CombatLiveBar.vue'
import ActionDock, { type DockTab } from './hud/ActionDock.vue'
import ActionTile from './hud/ActionTile.vue'
import Battlefield from './hud/Battlefield.vue'
import { battleDistance, buildBattleTokens } from './hud/battleView'
import InitiativeBar from './hud/InitiativeBar.vue'
import type { BattlefieldLabels } from './hud/labels'
import TargetCard from './hud/TargetCard.vue'
import { useFeedback } from './hud/useFeedback'
import { combatCopy } from './combatCopy'
import {
  friendlyCombatError as friendlyCombatErrorFor, intentId, localizedTerm as localizedTermFor,
} from './combatSupport'
import EncounterPrep from './EncounterPrep.vue'
import SpellAvailability from './SpellAvailability.vue'
import { spellCopy } from './spellCopy'

const props = defineProps<{
  gameKey: string
  actorId: string
  isGm: boolean
  refreshKey?: number
}>()
const emit = defineEmits<{
  refresh: []
  navigate: [target: 'campaign']
}>()
const { locale, t } = useLocale()
const spellText = computed(() => spellCopy(locale.value))

const data = ref<RulesetGameplayResponse | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const selectedWeaponId = ref('')
const selectedSpellRef = ref('')
const selectedSlot = ref(0)
const selectedTargetId = ref('')
// 施法目标按"目标条目"维护：单目标仍是 1 条；升环或多射线时由 projection
// 声明上限，前端只做条目编排，合法性仍由服务端判定。
const selectedSpellTargets = ref<string[]>([''])
const selectedSpellTargetCount = ref(1)
const selectedResistType = ref('')
const teleportDistance = ref<number | string>('')
const movementDistance = ref(5)
const staged = ref<JsonObject | null>(null)
const confirmCard = ref<HTMLElement | null>(null)
let pollTimer: number | undefined
let returnFocus: HTMLElement | null = null
let loadSequence = 0

const copy = computed(() => combatCopy(locale.value))
const localizedTerm = (value: unknown): string => localizedTermFor(value, locale.value)
const friendlyCombatError = (cause: unknown): string => friendlyCombatErrorFor(cause, locale.value)

const gameplay = computed(() => data.value?.gameplay)
const combat = computed(() => gameplay.value?.combat)
const actions = computed(() => data.value?.available_actions || [])
const action = (type: string) => actions.value.find(item => item.type === type)
const currentActor = computed(() => combat.value?.actors.find(
  actor => actor.actor_id === combat.value?.current_actor_id,
))
const combatMode = computed(() => String(combat.value?.mode || ''))
const adventureActive = computed(() => String(
  gameplay.value?.campaign?.tutorial?.status || '',
) === 'active')
const attackAction = computed(() => action('attack'))
const spellAction = computed(() => action('cast_spell'))
const spellActor = computed(() => combat.value?.actors?.find(actor => actor.actor_id === (spellAction.value?.actor_id || `player:${props.actorId}`)))
// 职业特性提供的战斗能力：服务端只返回当前真实可用的 capability，前端只渲染
// 服务端给的 id / 名称 / 成本 / 目标要求，绝不自己判断职业或等级。
const capabilityActions = computed<RulesetCombatAction[]>(() => (
  actions.value.filter(item => item.type === 'class_capability')
))
const capabilityTargets = computed<RulesetCombatTarget[]>(() => (
  capabilityActions.value.find(item => item.requires_target)?.targets || []
))
const capabilityTargetId = ref('')
// 只有服务端投影出的数组才渲染：老存档 / 敌人 actor / 缺失投影一律退化为「没有
// 职业资源」，绝不能让一个不是数组的值把整块战斗面板渲染炸掉。
const currentClassResources = computed<CharacterClassResource[]>(() => {
  const rows = currentActor.value?.class_resources
  return Array.isArray(rows) ? rows.filter(row => Number(row.maximum) > 0) : []
})
function capabilityCostLabel(capability: RulesetCombatAction): string {
  return (capability.costs || []).map(cost => cost.kind === 'resource'
    ? `${cost.name} ${cost.amount}（${cost.current}/${cost.maximum}）`
    : cost.name).join(' · ')
}
// 非战斗施法（§35.3）：仅当 server available_intents 提供 exploration.cast_spell
// 时渲染入口，不永久硬编码按钮。
const explorationAction = computed(() => action('exploration.cast_spell'))
const explorationSpellRef = ref('')
const explorationSlot = ref(0)
const explorationTargetId = ref('')
const explorationSpell = computed(() => (
  (explorationAction.value?.spells || []).find(item => item.spell_ref === explorationSpellRef.value) || null
))
const explorationSlotValid = computed(() => Boolean(
  explorationSpell.value
  && explorationSpell.value.available_slot_levels.includes(explorationSlot.value)
))
const moveAction = computed(() => action('move'))
const pendingDecision = computed(() => (
  action('decision.resolve')?.decisions?.[0]
  || combat.value?.pending_decisions?.find(item => item.assigned_to === props.actorId)
))
const selectedWeapon = computed(() => attackAction.value?.weapons?.find(
  item => (item.weapon_ref || item.id) === selectedWeaponId.value,
))
const attackDistance = computed(() => distanceTo(selectedTargetId.value))
const selectedWeaponRange = computed(() => selectedWeapon.value
  ? weaponRangeState(selectedWeapon.value, selectedTargetId.value)
  : null)
const hasUsableWeapon = computed(() => (attackAction.value?.weapons || []).some(
  weapon => weaponRangeState(weapon, selectedTargetId.value).usable,
))
const selectedSpell = computed(() => spellAction.value?.spells?.find(
  item => item.spell_ref === selectedSpellRef.value,
))
// 前端只按 projection 计算"可选择条目上限"（升环加目标/射线条数），
// 不计算伤害、临时 HP 或任何结算结果。
const spellTargetLimit = computed(() => {
  const spell = selectedSpell.value
  const base = Math.max(1, Number(spell?.target_count ?? 1))
  const upcast = Math.max(0, Number(spell?.upcast_target_count ?? 0))
  const slot = Number(selectedSlot.value)
  const level = Number(spell?.level ?? 0)
  return base + upcast * Math.max(0, slot - level)
})
const spellTargetsValid = computed(() => {
  const spell = selectedSpell.value
  if (!spell) return false
  const ids = selectedSpellTargets.value.filter(Boolean)
  if (!ids.length || ids.length > spellTargetLimit.value) return false
  if (!spell.repeat_targets && new Set(ids).size !== ids.length) return false
  if (spell.resist_choice?.length && !selectedResistType.value) return false
  if (spell.mode === 'teleport' && !validTeleportDistance()) return false
  return true
})
const latestEvents = computed(() => {
  const batches = data.value?.result?.resolved_event_batches
  if (Array.isArray(batches)) {
    return batches.flatMap(batch => (
      Array.isArray(batch.events) ? batch.events as JsonObject[] : []
    )).filter(event => event.type !== 'intent.submitted')
  }
  const batch = data.value?.result?.event_batch
  return Array.isArray(batch?.events) ? batch.events as JsonObject[] : []
})
const waitingText = computed(() => {
  const current = String(combat.value?.current_actor_id || '')
  if (!current) return copy.value.waiting
  if (current.startsWith('enemy:')) return copy.value.enemyActing
  if (current.startsWith('companion:')) {
    // §35.1：队友回合显示"米拉正在行动…"，而不是"等待队友点击"。
    return `${targetName(current)}（AI 队友）正在行动…`
  }
  if (current === `player:${props.actorId}`) return copy.value.waiting
  return `${copy.value.waitingForTeammate}：${targetName(current)}`
})
const selectedDestination = computed(() => (
  currentActor.value ? Number(currentActor.value.position || 0) + Number(movementDistance.value || 0) : null
))
const currentTurnLabel = computed(() => {
  const actorId = String(combat.value?.current_actor_id || '')
  if (actorId === `player:${props.actorId}`) return copy.value.yourTurn
  if (actorId.startsWith('enemy:')) return copy.value.enemyTurn
  if (actorId.startsWith('companion:')) return copy.value.companionTurn
  return copy.value.partyTurn
})
const stagedSummary = computed(() => {
  const payload = staged.value
  if (!payload) return ''
  const type = String(payload.type || '')
  const target = targetName(String(payload.target_id || ''))
  if (type === 'attack') {
    const ref = String(payload.weapon_ref || payload.attack_id || '')
    const weapon = attackAction.value?.weapons?.find(item => (item.weapon_ref || item.id) === ref)
    return `${copy.value.attack} · ${weapon?.name || weapon?.id || ref} → ${target}`
  }
  if (type === 'cast_spell') return `${copy.value.cast} · ${selectedSpell.value?.name || payload.spell_ref} → ${target}`
  if (type === 'move') return `${copy.value.move} · ${Number(payload.distance || 0)} ${copy.value.feet}`
  if (type === 'class_capability') {
    const capability = capabilityActions.value.find(
      item => item.capability_id === payload.capability_id,
    )
    const label = capability?.label || String(payload.capability_id || '')
    const cost = capability ? capabilityCostLabel(capability) : ''
    const aimed = capability?.requires_target ? ` → ${target}` : ''
    return [label, cost].filter(Boolean).join(' · ') + aimed
  }
  return localizedTerm(type)
})

function canReachPosition(position: number): boolean {
  if (!moveAction.value || !currentActor.value) return false
  const distance = position - Number(currentActor.value.position || 0)
  return distance !== 0 && Math.abs(distance) <= Number(moveAction.value.movement_remaining || 0)
}

function chooseTrackPosition(position: number): void {
  if (!canReachPosition(position) || !currentActor.value) return
  movementDistance.value = position - Number(currentActor.value.position || 0)
  selectDockTab('move')
}

const battleTokens = computed(() => buildBattleTokens(
  combat.value?.actors || [],
  String(combat.value?.current_actor_id || ''),
  props.actorId,
))
const battlefieldLabels = computed<BattlefieldLabels>(() => ({
  hp: copy.value.hp,
  feet: copy.value.feet,
  position: copy.value.position,
  condition: localizedTerm,
  selectTarget: name => `${copy.value.target}: ${name}`,
}))
const { floaters, play: playFeedback, clear: clearFeedback } = useFeedback(() => ({
  miss: copy.value.feedbackMiss,
  hit: copy.value.feedbackHit,
  saveOk: copy.value.saveOk,
  saveFail: copy.value.saveFail,
  blocked: copy.value.healBlocked,
  condition: localizedTerm,
}))

type DockMode = '' | 'attack' | 'spell' | 'capability' | 'explore' | 'move'
const dockTab = ref<'action' | 'bonus' | 'move' | 'reaction'>('action')
const dockMode = ref<DockMode>('')
const inspectedId = ref('')
const selectedCapabilityId = ref('')

const castingKey = (spell: RulesetCombatSpell) => String(spell.casting_time || '').trim().toLowerCase().replace(/[\s-]+/g, '_')
const isBonusSpell = (spell: RulesetCombatSpell) => Boolean(spell.trigger_target_id) || castingKey(spell) === 'bonus_action'
const isReactionSpell = (spell: RulesetCombatSpell) => castingKey(spell) === 'reaction' && !spell.trigger_target_id
// Spells split by the turn resource they spend; reaction spells are answered
// through the server's decision prompt instead of being picked here.
const spellChoices = computed(() => (spellAction.value?.spells || []).filter(spell => (
  !isReactionSpell(spell) && (dockTab.value === 'bonus' ? isBonusSpell(spell) : !isBonusSpell(spell))
)))
const showSpellTile = computed(() => {
  if (dockTab.value === 'bonus') return spellChoices.value.length > 0
  if (dockTab.value !== 'action') return false
  return spellChoices.value.length > 0 || Boolean(spellActor.value?.spells?.length)
})
const visibleTileCount = computed(() => {
  if (dockTab.value === 'move') return moveAction.value ? 1 : 0
  if (dockTab.value === 'reaction') return 0
  const spell = showSpellTile.value ? 1 : 0
  if (dockTab.value === 'bonus') return spell
  return spell + (attackAction.value ? 1 : 0) + (explorationAction.value ? 1 : 0) + simpleActions.value.length
    + (capabilityActions.value.length || currentClassResources.value.length ? 1 : 0)
})
const simpleActions = computed(() => (['dash', 'dodge', 'disengage', 'death_save'] as const).filter(type => action(type)))
const simpleLabel = (type: string) => ({
  dash: copy.value.dash, dodge: copy.value.dodge, disengage: copy.value.disengage, death_save: copy.value.deathSave,
}[type] || type)
const battleTokenIds = computed(() => battleTokens.value.map(token => token.id))
const targetCardLabels = computed(() => ({
  hp: copy.value.hp, ac: copy.value.ac, distance: copy.value.distance, feet: copy.value.feet,
  status: copy.value.status, none: copy.value.none, sideAlly: copy.value.sideAlly,
  sideEnemy: copy.value.sideEnemy, empty: copy.value.pickTarget, listSeparator: t('listSeparator'),
  condition: localizedTerm,
}))
const selectedCapability = computed(() => capabilityActions.value.find(
  item => item.capability_id === selectedCapabilityId.value,
))
// The unit the open action would be sent at; empty while no aimed action is open.
const aimedTargetId = computed(() => {
  switch (dockMode.value) {
    case 'attack': return selectedTargetId.value
    case 'spell': return selectedSpellTargets.value[0] || ''
    case 'capability': return selectedCapability.value?.requires_target ? capabilityTargetId.value : ''
    case 'explore': return explorationTargetId.value
    default: return ''
  }
})
// The battlefield marks what will actually be hit; the side card shows whatever
// the player clicked last, which may be a unit the open action cannot reach.
const focusId = computed(() => aimedTargetId.value || inspectedId.value)
const cardToken = computed(() => (
  battleTokens.value.find(token => token.id === (inspectedId.value || focusId.value)) || null
))
const currentToken = computed(() => battleTokens.value.find(token => token.isCurrent) || null)
const cardDistance = computed(() => (
  currentToken.value && cardToken.value ? battleDistance(currentToken.value.coord, cardToken.value.coord) : null
))
const initiativeTokens = computed(() => (combat.value?.initiative || [])
  .map(id => battleTokens.value.find(token => token.id === id))
  .filter((token): token is NonNullable<typeof token> => Boolean(token)))
const ownToken = computed(() => battleTokens.value.find(token => token.isOwn) || currentToken.value)
// `combat.economy` belongs to whoever is acting, so the dock only opens on the
// viewer's own turn; everyone else keeps the read-only waiting state.
const isOwnTurn = computed(() => combat.value?.current_actor_id === `player:${props.actorId}`)
const dockTabs = computed<DockTab[]>(() => {
  const economy = combat.value?.economy || {}
  const movement = Number(economy.movement ?? 0)
  // Reactions are tracked per actor, not in the turn economy.
  const reaction = Number(combat.value?.reactions?.[`player:${props.actorId}`] ?? 0) > 0
  const spent = copy.value.spent
  return [
    { id: 'action', label: copy.value.tabAction, available: Boolean(economy.action), note: economy.action ? '' : spent },
    { id: 'bonus', label: copy.value.tabBonus, available: Boolean(economy.bonus_action), note: economy.bonus_action ? '' : spent },
    { id: 'move', label: copy.value.tabMove, available: movement > 0, note: `${movement} ${copy.value.feet}` },
    { id: 'reaction', label: copy.value.tabReaction, available: reaction, note: reaction ? '' : spent },
  ]
})
const dockTargetToken = computed(() => battleTokens.value.find(token => token.id === aimedTargetId.value) || null)
const dockTargetDistance = computed(() => (
  currentToken.value && dockTargetToken.value ? battleDistance(currentToken.value.coord, dockTargetToken.value.coord) : null
))
const dockCost = computed(() => {
  if (dockMode.value === 'attack') return copy.value.action
  if (dockMode.value === 'spell' || dockMode.value === 'explore') {
    const spell = dockMode.value === 'spell' ? selectedSpell.value : explorationSpell.value
    if (!spell) return ''
    return spell.trigger_target_id ? copy.value.bonus : localizedTerm(spell.casting_time)
  }
  if (dockMode.value === 'capability') return selectedCapability.value ? capabilityCostLabel(selectedCapability.value) : ''
  if (dockMode.value === 'move') return `${Math.abs(Number(movementDistance.value || 0))} ${copy.value.feet}`
  return ''
})
const dockNeedsTarget = computed(() => dockMode.value === 'attack' || dockMode.value === 'spell'
  || (dockMode.value === 'capability' && Boolean(selectedCapability.value?.requires_target)))
const canStageDock = computed(() => {
  if (busy.value) return false
  switch (dockMode.value) {
    case 'attack': return Boolean(selectedWeapon.value && selectedTargetId.value && selectedWeaponRange.value?.usable)
    // Only a spell listed under the open tab may be sent from it.
    case 'spell': return Boolean(spellTargetsValid.value
      && spellChoices.value.some(item => item.spell_ref === selectedSpellRef.value))
    case 'capability': return Boolean(selectedCapability.value
      && (!selectedCapability.value.requires_target || capabilityTargetId.value))
    case 'explore': return Boolean(explorationSpellRef.value && explorationSlotValid.value)
    case 'move': return Boolean(movementDistance.value)
    default: return false
  }
})

function openDockMode(mode: DockMode, keepOpen = false): void {
  dockMode.value = !keepOpen && dockMode.value === mode ? '' : mode
  if (dockMode.value === 'spell') {
    const choices = spellChoices.value
    if (choices.length && !choices.some(item => item.spell_ref === selectedSpellRef.value)) {
      selectedSpellRef.value = choices[0]!.spell_ref
    }
  }
  if (dockMode.value === 'capability' && !selectedCapability.value) {
    selectedCapabilityId.value = capabilityActions.value[0]?.capability_id || ''
  }
  if (dockMode.value === 'explore' && !explorationSpellRef.value) {
    explorationSpellRef.value = explorationAction.value?.spells?.[0]?.spell_ref || ''
  }
}

function stageDock(): void {
  if (!canStageDock.value) return
  if (dockMode.value === 'attack') stageAttack()
  else if (dockMode.value === 'spell') stageSpell()
  else if (dockMode.value === 'capability' && selectedCapability.value) stageCapability(selectedCapability.value)
  else if (dockMode.value === 'explore') stageExplorationSpell()
  else if (dockMode.value === 'move') stageMove()
}

function selectDockTab(tab: string): void {
  dockTab.value = tab as typeof dockTab.value
  dockMode.value = tab === 'move' && moveAction.value ? 'move' : ''
}

// A landed melee hit lets the player add a bonus-action spell before damage;
// bring that choice forward instead of leaving it behind another tab.
watch(
  () => (pendingDecision.value?.kind === 'melee_hit_spell' ? pendingDecision.value.decision_id : ''),
  (decisionId) => {
    if (!decisionId) return
    dockTab.value = 'bonus'
    openDockMode('spell', true)
  },
  { immediate: true },
)

// Clicking a unit aims every action that can legally reach it, so the choice
// carries over when the player switches between attack, spell and ability.
function pickBattleTarget(actorId: string): void {
  inspectedId.value = actorId
  if (attackAction.value?.targets?.some(item => item.actor_id === actorId)) selectedTargetId.value = actorId
  if (targetsFor(selectedSpell.value).some(item => item.actor_id === actorId)) {
    const targets = selectedSpellTargets.value.slice()
    if (!targets.includes(actorId)) {
      const empty = targets.findIndex(id => !id)
      targets[empty < 0 ? 0 : empty] = actorId
      selectedSpellTargets.value = targets
    }
  }
  if (capabilityTargets.value.some(item => item.actor_id === actorId)) capabilityTargetId.value = actorId
  if (explorationAction.value?.targets?.some(item => item.actor_id === actorId)) explorationTargetId.value = actorId
}

function targetName(actorId: string): string {
  return combat.value?.actors.find(actor => actor.actor_id === actorId)?.name || actorId || copy.value.none
}

function distanceTo(targetId: string): number {
  if (!currentActor.value || !targetId) return 0
  const target = combat.value?.actors.find(actor => actor.actor_id === targetId)
    || attackAction.value?.targets?.find(actor => actor.actor_id === targetId)
  return Math.abs(Number(currentActor.value.position || 0) - Number(target?.position || 0))
}

function weaponRangeState(weapon: RulesetCombatWeapon, targetId: string) {
  const distance = distanceTo(targetId)
  const normal = Number(weapon.thrown_range ?? weapon.range ?? 5)
  const maximum = Number(weapon.long_range ?? normal)
  return {
    distance,
    normal,
    maximum,
    usable: distance <= maximum,
    disadvantage: distance > normal && distance <= maximum,
  }
}

function weaponRangeLabel(weapon: RulesetCombatWeapon): string {
  const state = weaponRangeState(weapon, selectedTargetId.value)
  const status = !state.usable ? copy.value.tooFar : state.disadvantage ? copy.value.longRange : copy.value.inRange
  const limit = state.disadvantage ? state.maximum : state.normal
  return `${status}（${state.distance}/${limit} ${copy.value.feet}）`
}

function chooseUsableWeapon(): void {
  const weapons = attackAction.value?.weapons || []
  if (!weapons.length || !selectedTargetId.value) {
    selectedWeaponId.value = weapons[0]?.weapon_ref || weapons[0]?.id || ''
    return
  }
  const current = weapons.find(item => (item.weapon_ref || item.id) === selectedWeaponId.value)
  if (current && weaponRangeState(current, selectedTargetId.value).usable) return
  const best = [...weapons].sort((left, right) => {
    const leftState = weaponRangeState(left, selectedTargetId.value)
    const rightState = weaponRangeState(right, selectedTargetId.value)
    const score = (state: ReturnType<typeof weaponRangeState>) => !state.usable ? 2 : state.disadvantage ? 1 : 0
    return score(leftState) - score(rightState)
  })[0]
  selectedWeaponId.value = best?.weapon_ref || best?.id || ''
}

function targetsFor(spell?: RulesetCombatSpell): RulesetCombatTarget[] {
  const targets = spellAction.value?.targets || attackAction.value?.targets || []
  if (!spell || !currentActor.value) return targets
  if (spell.trigger_target_id) return targets.filter(target => target.actor_id === spell.trigger_target_id)
  if (spell.target_scope === 'self') {
    return targets.filter(target => target.actor_id === currentActor.value?.actor_id)
  }
  const allied = (target: RulesetCombatTarget): boolean => (
    target.side ? target.side === 'party' : target.kind === currentActor.value?.kind
  )
  if (spell.mode === 'revive') {
    // 死亡状态由服务端投影；只有明确 is_dead 的 player 盟友才可复活，
    // 0 HP 仍在死亡豁免中的角色不算 dead。服务端 Revivify 只接受 player，
    // 因此死亡 companion 不能作为合法候选出现。
    return targets.filter(target => allied(target) && target.kind === 'player' && target.is_dead === true)
  }
  if (spell.target_side === 'ally') return targets.filter(allied)
  if (spell.target_side === 'enemy') return targets.filter(target => !allied(target))
  if (spell.target_side === 'any') return targets
  // 旧服务端没有 target_side：保持原有 mode 语义作为兼容边界。
  const beneficial = spell.mode === 'healing' || spell.mode === 'buff' || spell.mode === 'stabilize'
  return targets.filter(target => beneficial ? allied(target) : !allied(target))
}

function validTeleportDistance(): boolean {
  const distance = Number(teleportDistance.value)
  return Number.isInteger(distance) && distance !== 0
}

function spellTargetOptions(): string[] {
  return targetsFor(selectedSpell.value).map(target => target.actor_id)
}

function resizeSpellTargets(count: number): void {
  const next = Math.max(1, Math.min(Number(count) || 1, spellTargetLimit.value))
  const current = selectedSpellTargets.value.slice(0, next)
  const options = spellTargetOptions()
  while (current.length < next) {
    const distinct = options.find(id => !current.includes(id))
    current.push(distinct ?? options[0] ?? '')
  }
  selectedSpellTargets.value = current
  selectedSpellTargetCount.value = next
}

function syncSpellTargets(): void {
  const spell = selectedSpell.value
  selectedSpellTargetCount.value = 1
  selectedSpellTargets.value = [targetsFor(spell)[0]?.actor_id || '']
  selectedResistType.value = spell?.resist_choice?.[0] || ''
  teleportDistance.value = ''
}

// A refresh (the 30 s poll, a teammate's turn) must not throw away what the
// player already picked: every choice survives while it is still legal.
function resetSelections(): void {
  const keepOrFirst = (current: string, legal: RulesetCombatTarget[]) => (
    legal.some(item => item.actor_id === current) ? current : legal[0]?.actor_id || ''
  )
  const keepSlot = (current: number, levels: number[] | undefined) => (
    levels?.includes(current) ? current : levels?.[0] ?? 0
  )

  const exploring = explorationAction.value?.spells?.find(item => item.spell_ref === explorationSpellRef.value)
  explorationSpellRef.value = exploring?.spell_ref || ''
  explorationSlot.value = exploring ? keepSlot(explorationSlot.value, exploring.available_slot_levels) : 0
  if (!explorationAction.value?.targets?.some(item => item.actor_id === explorationTargetId.value)) {
    explorationTargetId.value = ''
  }

  const spells = spellAction.value?.spells || []
  const spell = spells.find(item => item.spell_ref === selectedSpellRef.value) || spellChoices.value[0] || spells[0]
  selectedSpellRef.value = spell?.spell_ref || ''
  selectedSlot.value = keepSlot(selectedSlot.value, spell?.available_slot_levels)
  // 刷新时保留仍然合法的施法目标；目标数量与上限由投影重新收敛。
  const legalSpellTargets = targetsFor(spell)
  const keptSpellTargets = selectedSpellTargets.value.filter(id => (
    id && legalSpellTargets.some(item => item.actor_id === id)
  ))
  const targetCount = Math.max(1, Math.min(keptSpellTargets.length || 1, spellTargetLimit.value))
  const nextSpellTargets = keptSpellTargets.slice(0, targetCount)
  while (nextSpellTargets.length < targetCount) {
    const distinct = legalSpellTargets.find(item => !nextSpellTargets.includes(item.actor_id))
    nextSpellTargets.push(distinct?.actor_id || legalSpellTargets[0]?.actor_id || '')
  }
  selectedSpellTargets.value = nextSpellTargets
  selectedSpellTargetCount.value = targetCount
  if (spell?.resist_choice?.length) {
    if (!spell.resist_choice.includes(selectedResistType.value)) {
      selectedResistType.value = spell.resist_choice[0] || ''
    }
  } else {
    selectedResistType.value = ''
  }
  selectedTargetId.value = keepOrFirst(selectedTargetId.value, attackAction.value?.targets || [])
  capabilityTargetId.value = keepOrFirst(capabilityTargetId.value, capabilityTargets.value)
  chooseUsableWeapon()
  if (!currentActor.value || !canReachPosition(Number(currentActor.value.position || 0) + Number(movementDistance.value || 0))) {
    movementDistance.value = Math.min(5, Number(moveAction.value?.movement_remaining || 5))
  }
  if (!capabilityActions.value.some(item => item.capability_id === selectedCapabilityId.value)) {
    selectedCapabilityId.value = capabilityActions.value[0]?.capability_id || ''
  }
}

// Back to a clean dock once an intent lands or the turn passes on.
function resetDock(): void {
  dockTab.value = 'action'
  dockMode.value = ''
  inspectedId.value = ''
}
// The first load is not a turn change, so it must not undo a decision prompt.
watch(
  () => (combat.value ? `${combat.value.round}:${combat.value.current_actor_id}` : ''),
  (_next, previous) => { if (previous) resetDock() },
)

function clearGameScopedState(): void {
  clearFeedback()
  data.value = null
  busy.value = false
  error.value = ''
  notice.value = ''
  selectedWeaponId.value = ''
  selectedSpellRef.value = ''
  selectedSlot.value = 0
  selectedTargetId.value = ''
  selectedSpellTargets.value = ['']
  selectedSpellTargetCount.value = 1
  selectedResistType.value = ''
  teleportDistance.value = ''
  movementDistance.value = 5
  staged.value = null
  returnFocus = null
}

async function load(silent = false): Promise<void> {
  const gameKey = props.gameKey
  if (!gameKey || (busy.value && silent)) return
  const sequence = ++loadSequence
  if (!silent) busy.value = true
  try {
    const response = await fetchRulesetAvailableActions(gameKey)
    if (sequence !== loadSequence || props.gameKey !== gameKey) return
    data.value = response
    error.value = ''
    resetSelections()
  } catch (cause: unknown) {
    if (sequence !== loadSequence || props.gameKey !== gameKey) return
    if (!silent || !data.value) error.value = friendlyCombatError(cause)
  } finally {
    if (!silent && sequence === loadSequence && props.gameKey === gameKey) busy.value = false
  }
}

function stage(payload: JsonObject): void {
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  staged.value = {
    ...payload,
    intent_id: intentId(),
    expected_version: gameplay.value?.state_version ?? 0,
  }
  void nextTick(() => confirmCard.value?.focus())
}

// Resolves to true once the server accepted the intent.
async function submit(payload: JsonObject): Promise<boolean> {
  const gameKey = props.gameKey
  busy.value = true
  try {
    const response = await submitRulesetIntent(gameKey, payload)
    if (props.gameKey !== gameKey) return false
    data.value = response
    staged.value = null
    error.value = ''
    notice.value = latestEvents.value.map(describeEvent).filter(Boolean).join(' · ')
    playFeedback(latestEvents.value)
    resetSelections()
    resetDock()
    emit('refresh')
    if (payload.type === 'combat.end') emit('navigate', 'campaign')
    return true
  } catch (cause: unknown) {
    if (props.gameKey !== gameKey) return false
    error.value = friendlyCombatError(cause)
    await load(true)
    return false
  } finally {
    if (props.gameKey === gameKey) busy.value = false
  }
}

async function confirmStaged(): Promise<void> {
  if (staged.value) await submit(staged.value)
}

function cancelStaged(): void {
  staged.value = null
  void nextTick(() => returnFocus?.focus())
}

function stageAttack(): void {
  const weapon = selectedWeapon.value
  if (!weapon || !selectedTargetId.value || !attackAction.value) return
  stage({
    type: 'attack', actor_id: attackAction.value.actor_id,
    target_id: selectedTargetId.value,
    ...(weapon.weapon_ref ? { weapon_ref: weapon.weapon_ref } : { attack_id: weapon.id }),
  })
}

function stageSpell(): void {
  const spell = selectedSpell.value
  if (!spell || !spellAction.value || !spellTargetsValid.value) return
  const ids = selectedSpellTargets.value.filter(Boolean)
  const payload: JsonObject = {
    type: 'cast_spell', actor_id: spellAction.value.actor_id,
    spell_ref: spell.spell_ref, slot_level: selectedSlot.value,
  }
  // 单目标继续用 target_id，多目标才用 target_ids，两者互斥。
  if (ids.length > 1) payload.target_ids = ids
  else payload.target_id = ids[0]
  if (spell.resist_choice?.length) payload.resist_type = selectedResistType.value
  if (spell.mode === 'teleport') payload.teleport_distance = Number(teleportDistance.value)
  stage(payload)
}

function stageExplorationSpell(): void {
  const actionPayload = explorationAction.value
  if (!actionPayload || !explorationSpell.value || !explorationSlotValid.value) return
  stage({
    type: 'exploration.cast_spell',
    actor_id: String(actionPayload.actor_id || ''),
    spell_ref: explorationSpellRef.value,
    slot_level: explorationSlot.value,
    target_ids: explorationTargetId.value ? [explorationTargetId.value] : [],
  })
}

function stageMove(): void {
  if (!moveAction.value || !movementDistance.value) return
  stage({
    type: 'move', actor_id: moveAction.value.actor_id,
    distance: Number(movementDistance.value),
  })
}

function stageSimple(type: string): void {
  const item = action(type)
  if (!item) return
  stage({ type, actor_id: item.actor_id })
}

// 职业能力统一走 canonical intent：只提交 capability id 与目标，成本与结算
// 全部由服务端 capability 声明和既有攻击链决定。
function stageCapability(capability: RulesetCombatAction): void {
  if (!capability.capability_id) return
  const payload: JsonObject = {
    type: 'class_capability',
    actor_id: capability.actor_id,
    capability_id: capability.capability_id,
  }
  if (capability.requires_target) {
    const targetId = capabilityTargetId.value
    if (!targetId) return
    payload.target_id = targetId
  }
  stage(payload)
}

const reactionSpellRef = ref('')
const reactionSlot = ref(0)
const reactionSpells = computed(() => pendingDecision.value?.kind === 'hit_reaction_spell'
  ? (pendingDecision.value.spells as RulesetCombatSpell[] | undefined) || []
  : [])
const reactionFeatures = computed(() => pendingDecision.value?.kind === 'hit_reaction_spell'
  ? (pendingDecision.value.features as RulesetCombatReactionFeature[] | undefined) || []
  : [])
const reactionSpell = computed(() => (
  reactionSpells.value.find(item => item.spell_ref === reactionSpellRef.value) || reactionSpells.value[0] || null
))
watch(reactionSpell, spell => {
  if (!spell) return
  reactionSpellRef.value = spell.spell_ref
  if (!spell.available_slot_levels.includes(reactionSlot.value)) reactionSlot.value = spell.available_slot_levels[0] ?? 0
}, { immediate: true })

async function decide(decision: RulesetPendingDecision, option: string, extra: JsonObject = {}): Promise<void> {
  const gameKey = props.gameKey
  busy.value = true
  try {
    const payload = {
      type: 'decision.resolve', decision_id: decision.decision_id,
      intent_id: intentId(), expected_version: gameplay.value?.state_version ?? 0, option, ...extra,
    }
    const response = await resolveRulesetDecision(gameKey, decision.decision_id, payload)
    if (props.gameKey !== gameKey) return
    data.value = response
    error.value = ''
    // A declined smite or a Shield answer is where a deferred hit lands.
    playFeedback(latestEvents.value)
    resetSelections()
    resetDock()
    emit('refresh')
  } catch (cause: unknown) {
    if (props.gameKey !== gameKey) return
    error.value = friendlyCombatError(cause)
    await load(true)
  } finally {
    if (props.gameKey === gameKey) busy.value = false
  }
}

function describeEvent(event: JsonObject): string {
  const type = String(event.type || '')
  if (type === 'check.resolved') {
    const success = event.success ? '✓' : '✕'
    return `${success} ${localizedTerm(event.kind)} ${event.total ?? event.natural ?? ''}`
  }
  if (type === 'resource.changed') {
    return `${targetName(String(event.target_id || ''))} HP ${Number(event.delta || 0) > 0 ? '+' : ''}${event.delta}`
  }
  return type.replace(/^dnd2024\./, '')
}

watch(selectedSpellRef, () => {
  const spell = selectedSpell.value
  selectedSlot.value = spell?.available_slot_levels?.[0] ?? 0
  // Switching spells keeps the unit the player clicked when the new spell can reach it.
  const legal = targetsFor(spell)
  const kept = [inspectedId.value, ...selectedSpellTargets.value]
    .find(id => id && legal.some(item => item.actor_id === id))
  syncSpellTargets()
  if (kept) selectedSpellTargets.value = [kept]
})
watch(selectedSlot, () => {
  if (selectedSpellTargets.value.length > spellTargetLimit.value) {
    resizeSpellTargets(spellTargetLimit.value)
  }
})
watch(selectedSpellTargetCount, count => resizeSpellTargets(count))
watch(explorationSpellRef, () => {
  explorationSlot.value = explorationSpell.value?.available_slot_levels?.[0] ?? 0
})
watch(selectedTargetId, () => chooseUsableWeapon())
watch(() => props.gameKey, (next, previous) => {
  if (next === previous) return
  loadSequence += 1
  clearGameScopedState()
  if (next) void load()
})
watch(() => props.refreshKey, () => void load(true))
onMounted(() => {
  void load()
  pollTimer = window.setInterval(() => {
    if (document.visibilityState !== 'hidden') void load(true)
  }, 30000)
})
onBeforeUnmount(() => { if (pollTimer) window.clearInterval(pollTimer) })
</script>

<template>
  <section class="dnd-combat" aria-labelledby="dnd-combat-title">
    <header class="combat-header">
      <div>
        <p class="eyebrow">5E 2024 SRD · {{ copy.authority }}</p>
        <h2 id="dnd-combat-title">{{ copy.title }}</h2>
      </div>
      <div class="combat-header-tools">
        <span v-if="combatMode" class="combat-mode-badge" :class="combatMode">
          {{ combatMode === 'story'
            ? copy.storyMode
            : adventureActive ? copy.sandboxOutsideAdventure : copy.sandboxMode }}
        </span>
        <button :disabled="busy" @click="load()">{{ copy.refresh }}</button>
      </div>
    </header>

    <p v-if="busy && !data" class="combat-state" role="status">{{ copy.loading }}</p>
    <p v-if="error" class="combat-error" role="alert">{{ error }}</p>
    <CombatLiveBar
      v-if="gameplay"
      :gameplay="gameplay"
      :actor-id="props.actorId"
      embedded
    />
    <details class="combat-rules-note">
      <summary>{{ copy.rulesNote }}</summary>
      <p>{{ copy.legalOnly }}</p>
    </details>

    <template v-if="gameplay">
      <EncounterPrep
        v-if="combat?.status !== 'active'"
        :key="gameKey"
        :game-key="gameKey"
        :actor-id="actorId"
        :is-gm="isGm"
        :data="data"
        :busy="busy"
        :submit="submit"
        @navigate="emit('navigate', $event)"
      />

      <template v-else>
        <InitiativeBar
          :order="initiativeTokens"
          :round-label="`${copy.round} ${combat.round}`"
          :turn-label="currentTurnLabel"
          :acting-name="targetName(combat.current_actor_id)"
          :list-label="copy.current"
        />
        <p v-if="combat.current_actor_id === `player:${actorId}`" class="turn-guide">{{ copy.turnGuide }}</p>

        <section class="battlefield" :aria-label="copy.tacticalTrack">
          <div class="battlefield-body">
            <Battlefield
              :position-mode="combat.position_mode"
              :tokens="battleTokens"
              :movement-remaining="Number(moveAction?.movement_remaining || 0)"
              :target-id="focusId"
              :selectable-ids="battleTokenIds"
              :selected-destination="dockMode === 'move' ? selectedDestination : null"
              :can-reach="canReachPosition"
              :labels="battlefieldLabels"
              :floaters="floaters"
              @select-token="pickBattleTarget"
              @choose-position="chooseTrackPosition"
            />
            <TargetCard :token="cardToken" :distance="cardDistance" :labels="targetCardLabels" />
          </div>
          <p class="battlefield-hint">{{ copy.chooseDestination }}</p>
        </section>

        <section v-if="pendingDecision" class="decision-card" aria-live="assertive">
          <strong>{{ pendingDecision.kind === 'melee_hit_spell' ? spellText.hit : pendingDecision.kind === 'hit_reaction_spell' ? spellText.hitReaction : `${copy.reaction} · ${localizedTerm(pendingDecision.kind)}` }}</strong>
          <p v-if="pendingDecision.kind === 'melee_hit_spell'">{{ spellText.hitHint }}</p>
          <template v-if="pendingDecision.kind === 'hit_reaction_spell'">
            <p>{{ spellText.hitReactionHint(targetName(String(pendingDecision.attacker_id || '')), Number(pendingDecision.attack_total), Number(pendingDecision.armor_class)) }}</p>
            <label v-if="reactionSpells.length > 1">{{ copy.spell }}
              <select v-model="reactionSpellRef">
                <option v-for="spell in reactionSpells" :key="spell.spell_ref" :value="spell.spell_ref">{{ spell.name }}</option>
              </select>
            </label>
            <label v-if="reactionSpell && reactionSpell.available_slot_levels.length > 1">{{ copy.slot }}
              <select v-model.number="reactionSlot">
                <option v-for="slot in reactionSpell.available_slot_levels" :key="slot" :value="slot">{{ slot === 0 ? spellText.featFree : `${slot}` }}</option>
              </select>
            </label>
            <div>
              <button v-if="reactionSpell" class="combat-primary" :disabled="busy" @click="decide(pendingDecision, 'cast', { spell_ref: reactionSpell.spell_ref, slot_level: reactionSlot })">
                {{ spellText.castReaction(reactionSpell.name, Number(reactionSpell.armor_class_bonus || 0), Number(pendingDecision.attack_total) < Number(pendingDecision.armor_class) + Number(reactionSpell.armor_class_bonus || 0)) }}
              </button>
              <button
                v-for="feature in reactionFeatures"
                :key="feature.feature_id"
                :disabled="busy"
                @click="decide(pendingDecision, 'feature', { feature_id: feature.feature_id })"
              >{{ spellText.useReactionFeature(feature.name, Number(feature.damage_multiplier)) }}</button>
              <button :disabled="busy" @click="decide(pendingDecision, 'decline')">{{ spellText.declineReaction }}</button>
            </div>
          </template>
          <div v-else>
            <button v-if="pendingDecision.options.includes('counter')" class="combat-primary" :disabled="busy" @click="decide(pendingDecision, 'counter')">{{ copy.counter }}</button>
            <button v-if="pendingDecision.options.includes('resolve')" class="combat-primary" :disabled="busy" @click="decide(pendingDecision, 'resolve')">{{ copy.resolve }}</button>
            <button :disabled="busy" @click="decide(pendingDecision, 'decline')">{{ pendingDecision.kind === 'melee_hit_spell' ? spellText.decline : copy.decline }}</button>
          </div>
        </section>

        <ActionDock
          v-if="isOwnTurn && actions.length && (!pendingDecision || pendingDecision.kind === 'melee_hit_spell')"
          :active-tab="dockTab"
          :own="ownToken"
          :hp-label="copy.hp"
          :tabs="dockTabs"
          :dock-label="copy.actionDock"
          @update:active-tab="selectDockTab"
        >
          <template #tab-icon-action><NIcon :component="FlashOutline" /></template>
          <template #tab-icon-bonus><NIcon :component="SparklesOutline" /></template>
          <template #tab-icon-move><NIcon :component="FootstepsOutline" /></template>
          <template #tab-icon-reaction><NIcon :component="ShieldOutline" /></template>

          <template #identity>
            <div v-if="currentClassResources.length" class="class-resource-strip">
              <span v-for="resource in currentClassResources" :key="resource.id">
                <small>{{ resource.name }}</small>
                <strong>{{ resource.current }} / {{ resource.maximum }}</strong>
              </span>
            </div>
          </template>

          <template #options>
            <p v-if="dockTab === 'reaction'" class="dock-hint">{{ copy.reactionHint }}</p>
            <p v-else-if="!dockMode" class="dock-hint">{{ visibleTileCount ? copy.chooseModeHint : copy.noActionInTab }}</p>

            <div v-if="dockMode === 'attack' && attackAction" class="mode-panel" data-mode="attack">
              <label>{{ copy.weapon }}
                <select v-model="selectedWeaponId">
                  <option
                    v-for="weaponItem in attackAction.weapons"
                    :key="weaponItem.weapon_ref || weaponItem.id"
                    :value="weaponItem.weapon_ref || weaponItem.id"
                    :disabled="!weaponRangeState(weaponItem, selectedTargetId).usable"
                  >
                    {{ weaponItem.name || weaponItem.id }} · {{ weaponItem.damage }} {{ localizedTerm(weaponItem.damage_type) }} · {{ weaponRangeLabel(weaponItem) }}
                  </option>
                </select>
              </label>
              <label>{{ copy.target }}
                <select v-model="selectedTargetId">
                  <option v-for="targetItem in attackAction.targets" :key="targetItem.actor_id" :value="targetItem.actor_id">
                    {{ targetItem.name }} · {{ targetItem.hp }}/{{ targetItem.max_hp }} HP
                  </option>
                </select>
              </label>
              <p v-if="selectedTargetId" :class="['range-guide', { blocked: !hasUsableWeapon }]">
                {{ copy.targetDistance }} {{ attackDistance }} {{ copy.feet }}。
                {{ hasUsableWeapon ? copy.autoWeapon : copy.moveCloser }}
              </p>
            </div>

            <div v-if="dockMode === 'spell'" class="mode-panel" data-mode="spell" data-testid="combat-spell-card">
              <template v-if="spellAction && spellChoices.length">
                <label>{{ copy.spell }}
                  <select v-model="selectedSpellRef">
                    <option v-for="spellItem in spellChoices" :key="spellItem.spell_ref" :value="spellItem.spell_ref">
                      {{ spellItem.name }} · {{ spellItem.trigger_target_id ? copy.bonus : localizedTerm(spellItem.casting_time) }}
                    </option>
                  </select>
                </label>
                <label v-if="selectedSpell?.level">{{ copy.slot }}
                  <select v-model.number="selectedSlot">
                    <option v-for="level in selectedSpell.available_slot_levels" :key="level" :value="level">{{ level === 0 ? spellText.free : level }}</option>
                  </select>
                </label>
                <label v-if="spellTargetLimit > 1">{{ copy.targetCount }}
                  <select v-model.number="selectedSpellTargetCount">
                    <option v-for="count in spellTargetLimit" :key="count" :value="count">{{ count }}</option>
                  </select>
                </label>
                <label v-for="(_entry, index) in selectedSpellTargets" :key="index">
                  {{ copy.target }}{{ selectedSpellTargets.length > 1 ? ` ${index + 1}` : '' }}
                  <select v-model="selectedSpellTargets[index]">
                    <option v-for="targetItem in targetsFor(selectedSpell)" :key="targetItem.actor_id" :value="targetItem.actor_id">
                      {{ targetItem.name }} · {{ targetItem.hp }}/{{ targetItem.max_hp }} HP
                    </option>
                  </select>
                </label>
                <label v-if="selectedSpell?.resist_choice?.length">{{ copy.resistType }}
                  <select v-model="selectedResistType">
                    <option v-for="kind in selectedSpell.resist_choice" :key="kind" :value="kind">{{ localizedTerm(kind) }}</option>
                  </select>
                </label>
                <label v-if="selectedSpell?.mode === 'teleport'">{{ copy.teleportDistance }}
                  <input v-model.number="teleportDistance" type="number" step="5" />
                </label>
              </template>
              <SpellAvailability :spells="spellActor?.spells || []" />
            </div>

            <div v-if="dockMode === 'capability'" class="mode-panel" data-mode="capability">
              <div v-if="capabilityActions.length" class="capability-list" role="group" :aria-label="copy.capabilityChoice">
                <button
                  v-for="capability in capabilityActions"
                  :key="capability.capability_id"
                  type="button"
                  :class="{ active: capability.capability_id === selectedCapabilityId }"
                  :aria-pressed="capability.capability_id === selectedCapabilityId"
                  @click="selectedCapabilityId = capability.capability_id || ''"
                >
                  <span class="capability-button-label">{{ capability.label }}</span>
                  <small class="capability-button-cost">{{ capabilityCostLabel(capability) }}</small>
                </button>
              </div>
              <p v-else class="range-guide">{{ copy.noCapabilityAvailable }}</p>
              <label v-if="selectedCapability?.requires_target && capabilityTargets.length">{{ copy.target }}
                <select v-model="capabilityTargetId">
                  <option v-for="targetItem in capabilityTargets" :key="targetItem.actor_id" :value="targetItem.actor_id">
                    {{ targetItem.name }} · {{ targetItem.hp }}/{{ targetItem.max_hp }} HP
                  </option>
                </select>
              </label>
              <p v-else-if="selectedCapability?.requires_target" class="range-guide blocked">{{ copy.noCapabilityTarget }}</p>
            </div>

            <div v-if="dockMode === 'explore' && explorationAction" class="mode-panel" data-mode="explore">
              <label>{{ copy.spell }}
                <select v-model="explorationSpellRef">
                  <option v-for="spellItem in explorationAction.spells" :key="spellItem.spell_ref" :value="spellItem.spell_ref">
                    {{ spellItem.name }} · {{ localizedTerm(spellItem.casting_time) }}
                  </option>
                </select>
              </label>
              <label v-if="explorationSpell?.level">{{ copy.slot }}
                <select v-model.number="explorationSlot">
                  <option v-for="level in explorationSpell.available_slot_levels" :key="level" :value="level">{{ level }}</option>
                </select>
              </label>
              <label>{{ copy.target }}
                <select v-model="explorationTargetId">
                  <option v-for="targetItem in explorationAction.targets" :key="targetItem.actor_id" :value="targetItem.actor_id">
                    {{ targetItem.name }} · {{ targetItem.hp }}/{{ targetItem.max_hp }} HP
                  </option>
                </select>
              </label>
            </div>

            <div v-if="dockMode === 'move' && moveAction" class="mode-panel" data-mode="move">
              <p class="dock-hint">{{ copy.moveHint }}</p>
              <label>{{ copy.move }}
                <input v-model.number="movementDistance" type="number" :min="-Number(moveAction.movement_remaining || 0)" :max="Number(moveAction.movement_remaining || 0)" step="5">
                <small>± {{ moveAction.movement_remaining }} {{ copy.feet }}</small>
              </label>
            </div>
          </template>

          <template #tiles>
            <template v-if="dockTab === 'action'">
              <ActionTile v-if="attackAction" :label="copy.attack" :hint="selectedWeapon?.name || selectedWeapon?.id" :active="dockMode === 'attack'" data-tile="attack" @click="openDockMode('attack')">
                <template #icon><NIcon :component="FlashOutline" /></template>
              </ActionTile>
            </template>
            <ActionTile v-if="showSpellTile" :label="copy.cast" :active="dockMode === 'spell'" data-tile="spell" @click="openDockMode('spell')">
              <template #icon><NIcon :component="SparklesOutline" /></template>
            </ActionTile>
            <template v-if="dockTab === 'action'">
              <ActionTile v-if="capabilityActions.length || currentClassResources.length" :label="copy.classAbilities" :active="dockMode === 'capability'" data-tile="capability" @click="openDockMode('capability')">
                <template #icon><NIcon :component="RibbonOutline" /></template>
              </ActionTile>
              <ActionTile v-if="explorationAction" :label="copy.explorationCast" :active="dockMode === 'explore'" data-tile="explore" @click="openDockMode('explore')">
                <template #icon><NIcon :component="SparklesOutline" /></template>
              </ActionTile>
              <ActionTile v-for="type in simpleActions" :key="type" :label="simpleLabel(type)" :disabled="busy" @click="stageSimple(type)">
                <template #icon><NIcon :component="type === 'dodge' ? ShieldOutline : type === 'death_save' ? SparklesOutline : FootstepsOutline" /></template>
              </ActionTile>
            </template>
            <ActionTile v-if="dockTab === 'move' && moveAction" :label="copy.move" :hint="`${moveAction.movement_remaining} ${copy.feet}`" :active="dockMode === 'move'" data-tile="move" @click="openDockMode('move')">
              <template #icon><NIcon :component="FootstepsOutline" /></template>
            </ActionTile>
          </template>

          <template #summary>
            <div v-if="dockMode" class="dock-target">
              <span v-if="dockNeedsTarget"><small>{{ copy.target }}</small><b>{{ dockTargetToken?.name || copy.noTarget }}</b></span>
              <span v-if="dockCost"><small>{{ copy.consumes }}</small><b>{{ dockCost }}</b></span>
              <span v-if="dockNeedsTarget && dockTargetDistance !== null"><small>{{ copy.distance }}</small><b>{{ dockTargetDistance }} {{ copy.feet }}</b></span>
            </div>
            <button type="button" class="dock-confirm" :disabled="!canStageDock" @click="stageDock">
              <NIcon :component="FlashOutline" />{{ copy.confirmAction }}
            </button>
            <p v-if="action('end_turn')" class="end-turn-ready" role="status"><NIcon :component="PlayForwardOutline" />{{ copy.canEndTurn }}</p>
            <button v-if="action('end_turn')" type="button" class="dock-end" :disabled="busy" @click="stageSimple('end_turn')"><NIcon :component="PlayForwardOutline" />{{ copy.endTurn }}</button>
            <button v-if="action('combat.end')" type="button" class="danger dock-end" :disabled="busy" @click="stageSimple('combat.end')"><NIcon :component="ShieldOutline" />{{ copy.endCombat }}</button>
          </template>
        </ActionDock>
        <template v-else>
          <p class="combat-state">{{ waitingText }}</p>
          <button v-if="action('combat.end')" type="button" class="danger dock-end" :disabled="busy" @click="stageSimple('combat.end')"><NIcon :component="ShieldOutline" />{{ copy.endCombat }}</button>
        </template>

        <section v-if="staged" ref="confirmCard" class="confirm-card" role="group" :aria-label="copy.confirm" tabindex="-1">
          <strong>{{ copy.confirm }}</strong>
          <code>{{ stagedSummary }}</code>
          <div>
            <button :disabled="busy" @click="cancelStaged">{{ copy.cancel }}</button>
            <button class="combat-primary" :disabled="busy" @click="confirmStaged">{{ copy.confirm }}</button>
          </div>
        </section>

        <section v-if="latestEvents.length || notice" class="resolution-log" aria-live="polite">
          <h3>{{ copy.lastResult }}</h3>
          <ul>
            <li v-for="(event, index) in latestEvents" :key="index">{{ describeEvent(event) }}</li>
            <li v-if="!latestEvents.length && notice">{{ notice }}</li>
          </ul>
        </section>
      </template>
    </template>
  </section>
</template>

<style scoped>
.dnd-combat { display: grid; grid-template-columns: minmax(0, 1fr); grid-auto-rows: max-content; align-content: start; gap: 14px; margin: 14px 0; padding: 16px; border: 1px solid #806339; border-radius: 16px; background: linear-gradient(145deg, rgb(23 20 19 / 96%), rgb(19 27 35 / 96%)); color: #f2eadc; box-shadow: 0 18px 48px rgb(0 0 0 / 24%); }
.combat-header, .decision-card, .confirm-card { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.combat-header h2 { margin: 2px 0 0; font: 700 clamp(19px, 2vw, 25px)/1.2 Georgia, serif; }
.eyebrow { margin: 0; color: #d7b873; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.preset-description, .combat-state { margin: 0; color: #bcb5aa; font-size: 13px; }
.combat-rules-note { color: #bcb5aa; font-size: 12px; }
.combat-rules-note summary { width: max-content; cursor: pointer; color: #d7b873; }
.combat-rules-note p { margin: 7px 0 0; line-height: 1.5; }
.combat-error { margin: 0; padding: 9px 11px; border: 1px solid #b85151; border-radius: 9px; background: rgb(115 28 28 / 25%); color: #ffd6d6; }
.combat-header-tools { display: flex; align-items: center; gap: 9px; }
.combat-mode-badge { padding: 3px 9px; border: 1px solid #6d5a35; border-radius: 999px; color: #f0d79c; background: rgb(64 46 20 / 55%); font-size: 11px; letter-spacing: .04em; white-space: nowrap; }
.combat-mode-badge.sandbox { border-color: #46626f; color: #bcd8e6; background: rgb(20 44 56 / 55%); }
.combat-primary { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.turn-guide { margin: -4px 0 0; padding: 7px 10px; border-left: 3px solid #d2a855; color: #d9d1c4; background: rgb(80 58 23 / 24%); font-size: 12px; line-height: 1.5; }
.battlefield { display: grid; gap: 6px; }
.battlefield-body { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: 10px; align-items: start; }
.dock-hint { margin: 0; color: #8fa0ac; font-size: 12px; line-height: 1.5; }
.mode-panel { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; align-items: end; padding: 10px; border: 1px solid rgb(120 150 175 / 28%); border-radius: 12px; background: rgb(9 15 22 / 70%); }
.mode-panel > .range-guide, .mode-panel > .dock-hint, .mode-panel > .class-resource-strip, .mode-panel > .capability-list { grid-column: 1 / -1; }
.mode-panel label { display: grid; gap: 4px; color: #aebcc6; font-size: 12px; }
.mode-panel select, .mode-panel input { width: 100%; min-height: 40px; padding-inline: 10px; border: 1px solid #4a5560; border-radius: 8px; background: #0d1319; color: #f3eee7; }
.capability-list { display: flex; flex-wrap: wrap; gap: 6px; }
.capability-list button { display: grid; gap: 2px; min-height: 40px; padding: 5px 12px; border: 1px solid rgb(120 150 175 / 40%); border-radius: 9px; background: rgb(15 24 35 / 90%); color: #e8eef2; text-align: left; cursor: pointer; }
.capability-list button.active { border-color: #e4b75e; background: rgb(88 62 22 / 60%); }
.dock-target { display: grid; gap: 5px; padding: 9px 10px; border: 1px solid rgb(120 150 175 / 28%); border-radius: 12px; background: rgb(9 15 22 / 70%); }
.dock-target span { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; font-size: 12px; }
.dock-target small { color: #9aa9b4; }
.dock-target b { overflow: hidden; color: #ffe5a8; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.dock-confirm, .dock-end { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 46px; border-radius: 12px; font-weight: 700; cursor: pointer; }
.dock-confirm { border: 1px solid #e4b75e; background: linear-gradient(180deg, #b87d27, #7a4f16); color: #fff6df; box-shadow: 0 0 14px rgb(228 183 94 / 25%); }
.dock-confirm:disabled { border-color: #5a5648; background: #2a2820; color: #8a8576; box-shadow: none; cursor: not-allowed; }
.dock-end { border: 1px solid rgb(120 150 175 / 45%); background: linear-gradient(180deg, #1f3347, #131f2c); color: #e8eef2; }
.dock-end.danger { border-color: #9d4a4f; background: linear-gradient(180deg, #4a1f24, #2a1013); }
@media (max-width: 960px) { .battlefield-body { grid-template-columns: minmax(0, 1fr); } }
.battlefield-hint { margin: 0; color: #8fa0ac; font-size: 12px; }
.range-guide { margin: 0; color: #b8d7bc; font-size: 12px; line-height: 1.5; }
.range-guide.blocked { color: #f0b3a7; }
button, select, input { min-height: 44px; font: inherit; }
.capability-button-label { font-weight: 700; }
.capability-button-cost { width: 100%; color: #c8c2b8; font-size: 11px; }
.class-resource-strip { display: flex; gap: 8px; flex-wrap: wrap; }
.class-resource-strip > span { display: grid; gap: 2px; padding: 6px 9px; border: 1px solid #4a5560; border-radius: 8px; background: #0d1319; }
.class-resource-strip small { color: #9ca6ae; font-size: 11px; }
.class-resource-strip strong { font-size: 14px; }
.end-turn-ready { display: flex; align-items: center; gap: 6px; margin: 2px 0 0; padding: 8px 9px; border: 1px solid #b88b3e; border-radius: 8px; background: rgb(197 148 67 / 14%); color: #f2d28f; font-size: 12px; font-weight: 700; }
.decision-card, .confirm-card { padding: 12px; border: 1px solid #c2974a; border-radius: 12px; background: #322716; }
.decision-card div, .confirm-card div { display: flex; gap: 7px; flex-wrap: wrap; }
.confirm-card { position: sticky; bottom: 12px; z-index: 2; box-shadow: 0 12px 36px rgb(0 0 0 / 45%); }
.confirm-card code { color: #efd195; }
.combat-primary { border-color: #c59443; background: #a56f24; color: white; }
.resolution-log h3 { margin: 0 0 7px; }
.resolution-log ul { display: flex; gap: 6px; flex-wrap: wrap; margin: 0; padding: 0; list-style: none; }
.resolution-log li { padding: 4px 8px; border-radius: 7px; background: #202b34; color: #cdd7df; font-size: 12px; }
button:focus-visible, select:focus-visible, input:focus-visible, .confirm-card:focus-visible { outline: 3px solid #e4b75e; outline-offset: 2px; }
:global(body.light .dnd-combat) { border-color: #9b743a; background: linear-gradient(145deg, #fffaf0, #f1f6f7); color: #27231e; box-shadow: 0 14px 34px rgb(76 59 29 / 12%); }
:global(body.light .dnd-combat .combat-rules-note), :global(body.light .dnd-combat .preset-description), :global(body.light .dnd-combat .combat-state), :global(body.light .dnd-combat .combat-summary small) { color: #514b43; }
:global(body.light .dnd-combat .resolution-log li) { background: #e8eef0; color: #27383e; }
:global(body.light .dnd-combat .combat-mode-badge) { border-color: #ad8a4b; background: #f6ecd6; color: #4b3a1c; }
:global(body.light .dnd-combat .combat-mode-badge.sandbox) { border-color: #8ba7b5; background: #eaf3f7; color: #26414d; }
@media (max-width: 700px) {
  .dnd-combat { gap: 10px; margin-inline: 0; padding: 10px; border-radius: 12px; }
  .combat-header, .decision-card, .confirm-card { align-items: stretch; flex-direction: column; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; } }
</style>
