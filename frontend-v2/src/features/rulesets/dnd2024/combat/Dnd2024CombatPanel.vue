<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import {
  CheckmarkCircleOutline,
  FlashOutline,
  FootstepsOutline,
  HourglassOutline,
  PlayForwardOutline,
  ShieldOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import {
  fetchRulesetAvailableActions,
  planRulesetTemporaryEncounter,
  resolveRulesetDecision,
  submitRulesetIntent,
} from '@/features/rulesets/dnd2024/api'
import type {
  JsonObject,
  RulesetCombatSpell,
  RulesetCombatTarget,
  RulesetCombatWeapon,
  RulesetGameplayResponse,
  RulesetPendingDecision,
  RulesetTemporaryEncounter,
} from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import CombatLiveBar from '@/components/play/CombatLiveBar.vue'

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
const { locale } = useLocale()

const data = ref<RulesetGameplayResponse | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const selectedPresetId = ref('')
const selectedWeaponId = ref('')
const selectedSpellRef = ref('')
const selectedSlot = ref(0)
const selectedTargetId = ref('')
const nextEncounterOpen = ref(false)
const manualEncounterOpen = ref(false)
const movementDistance = ref(5)
const staged = ref<JsonObject | null>(null)
const confirmCard = ref<HTMLElement | null>(null)
let pollTimer: number | undefined
let returnFocus: HTMLElement | null = null
let loadSequence = 0

const copy = computed(() => locale.value.startsWith('zh') ? {
  title: '战斗', authority: 'D&D 5E 2024 · 权威规则结算', rulesNote: '结算说明',
  loading: '正在同步战斗状态…', refresh: '刷新', start: '确认进入战斗',
  chooseEncounter: '选择当前遭遇', noCombat: '当前没有进行中的战斗。', noEncounter: '当前没有可用于正式对局的遭遇。',
  recommendedEncounter: 'AI GM 匹配的敌情', changeEncounter: '改用其他合法遭遇',
  storyBridge: '剧情遭遇', storyPurpose: '当前敌情来自正在进行的冒险。', storyAction: '确认后进入先攻；敌方由 AI GM 自动行动，每位玩家只操作自己的角色。',
  guidedEncounter: '当前敌情', guidedStart: '进入战斗', waitingForGm: '等待 GM 确认进入战斗。', guidedOnly: '遭遇已经由剧情绑定，无需再次选择。',
  narrativeRequest: 'AI GM 已识别到交战态势。确认敌情后将直接进入先攻。',
  round: '轮次', current: '当前行动者', position: '位置', hp: '生命值', ac: '护甲等级',
  action: '动作', bonus: '附赠动作', movement: '移动', reaction: '反应',
  attack: '攻击', cast: '施放法术', target: '目标', weapon: '武器', spell: '法术', slot: '法术位',
  move: '移动', feet: '尺', dash: '疾走', dodge: '闪避', disengage: '脱离接战',
  endTurn: '结束回合', endCombat: '结束战斗', deathSave: '死亡豁免', stabilize: '稳定伤者',
  confirm: '确认并交给服务器结算', cancel: '取消', resolve: '发动反应', decline: '放弃反应',
  legalOnly: '这里只显示当前合法动作；若状态已经变化，服务器会拒绝过期请求并说明原因。',
  yourTurn: '轮到你行动', enemyTurn: 'AI GM 正在处理敌方回合', partyTurn: '队友回合', availableActions: '当前可用动作',
  waiting: '尚未轮到你的角色。', waitingForTeammate: '等待队友行动', enemyActing: '敌方正在由服务器自动行动…', ended: '战斗已经结束。', conditions: '状态',
  lastResult: '最近结算', none: '无', economy: '本回合资源', available: '可用', spent: '已用',
  inRange: '可用', longRange: '远距攻击（劣势）', tooFar: '距离不足',
  targetDistance: '你与目标相距', autoWeapon: '已优先选择当前距离可用的武器。',
  moveCloser: '当前武器都够不到目标。请先移动靠近，或改用射程更远的武器。',
  tacticalTrack: '战术距离带', chooseDestination: '每格 5 尺；轮到你时可点击可达格选择移动距离。',
  ready: '准备', cancelReady: '取消准备', partyReady: '队伍准备状态', readyStatus: '已准备', unreadyStatus: '未准备',
  readySummary: '队友准备', startHint: 'GM 可在确认敌情与队伍状态后开始战斗。',
  manualEncounter: '手动准备遭遇',
  chooseFreeEncounter: '选择自由遭遇',
  sandboxHint: 'AI GM 没能把当前敌情匹配到合法遭遇。可以由 AI 临时准备，或手动选择一场自由遭遇。',
  unpreparedTitle: '当前剧情尚未配置专业战斗遭遇',
  unpreparedHint: '剧情已经进入交战态势，但这个冒险包没有为当前敌情绑定合法的专业遭遇。服务端不会用通用训练遭遇替代它。',
  aiPrepare: 'AI 临时准备遭遇',
  aiHint: 'AI 会参考当前剧情临时生成一场自由遭遇；不会修改冒险包内容。',
  aiPreparing: 'AI 正在临时准备遭遇…',
  aiFailed: '无法生成合法的临时遭遇。你仍可以手动选择自由遭遇。',
  aiPreviewTag: 'AI 临时遭遇',
  aiConfirmStart: '确认进入战斗',
  aiRegenerate: '重新生成',
  aiEncounterNote: '这场战斗不会写入冒险包的正式遭遇定义；战斗结束后仍可继续当前冒险剧情。',
  tempSpeed: '速度',
  unpreparedAction: '临时准备自由遭遇',
  sandboxWarning: '这是一场临时自由遭遇：不会写入冒险包的正式遭遇定义，战斗结束后仍可继续当前冒险剧情。',
  storyMode: '剧情遭遇',
  sandboxMode: '自由遭遇',
  sandboxOutsideAdventure: '自由战斗 · 不属于当前冒险包',
  missingPreset: '冒险包引用的遭遇不存在，需要先修复冒险包内容。',
  encounterSource: '遭遇',
  nextEncounter: '准备下一场遭遇', nextEncounterHint: '当前战斗已经结算。可返回冒险，或由 GM 明确准备下一场战斗。', returnToAdventure: '返回冒险', cancelNextEncounter: '暂不准备',
  turnGuide: '本回合可以组合使用移动、一个动作和可用的附赠动作；完成操作后请手动结束回合。脱离接战只会避免本回合的机会攻击，仍需移动离开敌人范围。',
  canEndTurn: '当前可结束回合',
} : {
  title: 'Combat', authority: 'D&D 5E 2024 · Authoritative resolution', rulesNote: 'Resolution rules',
  loading: 'Synchronizing combat…', refresh: 'Refresh', start: 'Confirm Combat',
  chooseEncounter: 'Select the encounter', noCombat: 'No combat is active.', noEncounter: 'No encounter is available for standard play.',
  recommendedEncounter: 'AI GM matched opposition', changeEncounter: 'Use another legal encounter',
  storyBridge: 'Story encounter', storyPurpose: 'The current opposition comes from the active adventure.', storyAction: 'Confirm to enter initiative. The AI GM operates enemies while each player controls only their own character.',
  guidedEncounter: 'Current opposition', guidedStart: 'Enter Combat', waitingForGm: 'Waiting for the GM to confirm combat.', guidedOnly: 'The story already selected this encounter; no additional preset choice is needed.',
  narrativeRequest: 'The AI GM detected an engagement. Confirm the opposition to enter initiative.',
  round: 'Round', current: 'Current actor', position: 'Position', hp: 'HP', ac: 'Armor Class',
  action: 'Action', bonus: 'Bonus Action', movement: 'Movement', reaction: 'Reaction',
  attack: 'Attack', cast: 'Cast Spell', target: 'Target', weapon: 'Weapon', spell: 'Spell', slot: 'Slot',
  move: 'Move', feet: 'ft', dash: 'Dash', dodge: 'Dodge', disengage: 'Disengage',
  endTurn: 'End Turn', endCombat: 'End Combat', deathSave: 'Death Save', stabilize: 'Stabilize',
  confirm: 'Confirm and let the server resolve', cancel: 'Cancel', resolve: 'Use Reaction', decline: 'Decline',
  legalOnly: 'Only currently legal actions are shown. The server rejects stale requests with a reason.',
  yourTurn: 'Your turn', enemyTurn: 'AI GM is resolving the enemy turn', partyTurn: 'Party turn', availableActions: 'Available actions',
  waiting: 'Waiting for your character’s turn.', waitingForTeammate: 'Waiting for teammate', enemyActing: 'The enemy is acting automatically…', ended: 'Combat has ended.', conditions: 'Conditions',
  lastResult: 'Latest Resolution', none: 'None', economy: 'Turn Resources', available: 'Available', spent: 'Spent',
  inRange: 'In range', longRange: 'Long range (disadvantage)', tooFar: 'Out of range',
  targetDistance: 'Distance to target', autoWeapon: 'A weapon usable at this distance is selected first.',
  moveCloser: 'None of your weapons can reach this target. Move closer or use a longer-ranged weapon.',
  tacticalTrack: 'Tactical distance track', chooseDestination: 'Each cell is 5 ft. On your turn, select a reachable cell to set movement.',
  ready: 'Ready', cancelReady: 'Cancel ready', partyReady: 'Party readiness', readyStatus: 'Ready', unreadyStatus: 'Not ready',
  readySummary: 'Party ready', startHint: 'The GM can start after reviewing the opposition and party status.',
  manualEncounter: 'Prepare encounter manually',
  chooseFreeEncounter: 'Select a free encounter',
  sandboxHint: 'The AI GM could not match the current opposition to a legal encounter. Prepare one with AI, or pick a free encounter manually.',
  unpreparedTitle: 'The current story has no prepared encounter',
  unpreparedHint: 'The story has entered combat, but this adventure package binds no legal encounter to the current opposition. The server will not substitute a generic training encounter.',
  aiPrepare: 'AI temporary encounter',
  aiHint: 'The AI drafts a free encounter from the current story; the adventure package is never modified.',
  aiPreparing: 'The AI is preparing an encounter…',
  aiFailed: 'Could not generate a legal temporary encounter. You can still prepare a free encounter manually.',
  aiPreviewTag: 'AI temporary encounter',
  aiConfirmStart: 'Confirm and start combat',
  aiRegenerate: 'Regenerate',
  aiEncounterNote: 'This combat is not written into the adventure package; the story continues afterwards.',
  tempSpeed: 'Speed',
  unpreparedAction: 'Prepare a temporary free encounter',
  sandboxWarning: 'This is a temporary free encounter: it is not written into the adventure package, and the story continues after combat.',
  storyMode: 'Story encounter',
  sandboxMode: 'Free encounter',
  sandboxOutsideAdventure: 'Free combat · not part of the active adventure',
  missingPreset: 'The adventure package references an encounter that does not exist; fix the package content first.',
  encounterSource: 'Encounter',
  nextEncounter: 'Prepare next encounter', nextEncounterHint: 'This combat is resolved. Return to the adventure, or have the GM explicitly prepare another encounter.', returnToAdventure: 'Return to adventure', cancelNextEncounter: 'Not yet',
  turnGuide: 'You can combine movement, one action, and an available bonus action this turn. End the turn when finished. Disengage prevents opportunity attacks for this turn; you still need to move out of enemy range.',
  canEndTurn: 'You can end the turn now',
})

const gameplay = computed(() => data.value?.gameplay)
const combat = computed(() => gameplay.value?.combat)
const actions = computed(() => data.value?.available_actions || [])
const action = (type: string) => actions.value.find(item => item.type === type)
const currentActor = computed(() => combat.value?.actors.find(
  actor => actor.actor_id === combat.value?.current_actor_id,
))
const selectableEncounterPresets = computed(() => (gameplay.value?.encounter_presets || []).filter(
  preset => String(preset.difficulty || '') !== 'tutorial',
))
const selectedPreset = computed(() => gameplay.value?.encounter_presets.find(
  preset => preset.id === selectedPresetId.value,
))
const guidedCombatStep = computed(() => {
  const step = gameplay.value?.campaign?.tutorial?.current_step
  return step?.requires === 'combat_ended' && step.encounter_preset_id ? step : null
})
const guidedCombatPreset = computed(() => guidedCombatStep.value
  ? gameplay.value?.encounter_presets.find(preset => preset.id === guidedCombatStep.value?.encounter_preset_id)
  : undefined)
const narrativeCombatPending = computed(() => gameplay.value?.encounter_request?.status === 'pending')
const encounterReadiness = computed(() => gameplay.value?.encounter_request?.readiness)
// 服务端权威模式：story=剧情绑定遭遇，sandbox=自由遭遇，
// blocked+unprepared=活动冒险包尚未配置专业战斗遭遇。
const encounterAccess = computed(() => gameplay.value?.encounter_access)
const encounterMode = computed(() => String(encounterAccess.value?.mode || ''))
const storyUnprepared = computed(() => Boolean(encounterAccess.value?.unprepared))
const sandboxFlow = computed(() => encounterMode.value !== 'story')
const combatMode = computed(() => String(combat.value?.mode || ''))
const adventureActive = computed(() => String(
  gameplay.value?.campaign?.tutorial?.status || '',
) === 'active')
const sandboxDeclared = ref(false)
// AI 临时遭遇：只读提案（生成 → GM 预览 → 确认），与手动自由遭遇流程并行；
// 确认走现有 combat.start(mode=sandbox)，服务端权威校验仍然完整生效。
const aiProposal = ref<RulesetTemporaryEncounter | null>(null)
const aiBusy = ref(false)
const aiError = ref('')
const readyAction = computed(() => action('encounter.ready'))
const unreadyAction = computed(() => action('encounter.unready'))
const isReady = computed(() => Boolean(
  encounterReadiness.value?.ready_player_ids?.includes(props.actorId),
))
const requestedCombatPreset = computed(() => {
  const presetId = gameplay.value?.encounter_request?.encounter_preset_id
  return presetId
    ? selectableEncounterPresets.value.find(preset => preset.id === presetId)
    : undefined
})
const canPlanTemporaryEncounter = computed(() => Boolean(
  props.isGm
  && combat.value?.status !== 'active'
  && narrativeCombatPending.value
  && encounterMode.value !== 'story'
  && !requestedCombatPreset.value
))
const attackAction = computed(() => action('attack'))
const spellAction = computed(() => action('cast_spell'))
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
const selectedTarget = computed(() => targetsFor(selectedSpell.value).find(
  item => item.actor_id === selectedTargetId.value,
))
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
  if (current === `player:${props.actorId}`) return copy.value.waiting
  return `${copy.value.waitingForTeammate}：${targetName(current)}`
})
const trackMin = computed(() => {
  const positions = combat.value?.actors.map(actor => Number(actor.position || 0)) || [0]
  const movement = Number(moveAction.value?.movement_remaining || 0)
  return Math.floor((Math.min(0, ...positions) - movement) / 5) * 5
})
const trackMax = computed(() => {
  const positions = combat.value?.actors.map(actor => Number(actor.position || 0)) || [0]
  const movement = Number(moveAction.value?.movement_remaining || 0)
  return Math.ceil((Math.max(30, ...positions) + movement) / 5) * 5
})
const trackTicks = computed(() => Array.from(
  { length: Math.max(1, (trackMax.value - trackMin.value) / 5 + 1) },
  (_, index) => trackMin.value + index * 5,
))
const selectedDestination = computed(() => (
  currentActor.value ? Number(currentActor.value.position || 0) + Number(movementDistance.value || 0) : null
))
const currentTurnLabel = computed(() => {
  const actorId = String(combat.value?.current_actor_id || '')
  if (actorId === `player:${props.actorId}`) return copy.value.yourTurn
  if (actorId.startsWith('enemy:')) return copy.value.enemyTurn
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
}

const trackLaneCount = computed(() => {
  const positions = combat.value?.actors.map(actor => Number(actor.position || 0)) || []
  const counts = new Map<number, number>()
  positions.forEach(position => counts.set(position, (counts.get(position) || 0) + 1))
  return Math.max(1, ...counts.values())
})
const trackCanvasStyle = computed(() => ({
  minWidth: `${Math.max(1, trackTicks.value.length) * 34}px`,
  paddingTop: `${16 + trackLaneCount.value * 30}px`,
}))

function tokenTrackStyle(position: number, index: number): Record<string, string> {
  const numericPosition = Number(position || 0)
  const tickIndex = Math.max(0, Math.min(
    trackTicks.value.length - 1,
    Math.round((numericPosition - trackMin.value) / 5),
  ))
  const earlierActors = combat.value?.actors.slice(0, index) || []
  const lane = earlierActors.filter(actor => Number(actor.position || 0) === numericPosition).length
  return {
    left: `${(tickIndex + 0.5) / Math.max(1, trackTicks.value.length) * 100}%`,
    top: `${8 + lane * 30}px`,
  }
}

function localizedTerm(value: unknown): string {
  const raw = String(value || '')
  const key = raw.trim().toLowerCase().replace(/[\s-]+/g, '_')
  const labels: Record<string, [string, string]> = {
    tutorial: ['教学', 'Tutorial'], standard: ['标准', 'Standard'], challenging: ['挑战', 'Challenging'], lethal: ['致命', 'Lethal'],
    action: ['动作', 'Action'], bonus_action: ['附赠动作', 'Bonus Action'], reaction: ['反应', 'Reaction'],
    slashing: ['挥砍', 'Slashing'], piercing: ['穿刺', 'Piercing'], bludgeoning: ['钝击', 'Bludgeoning'],
    acid: ['强酸', 'Acid'], cold: ['寒冷', 'Cold'], fire: ['火焰', 'Fire'], force: ['力场', 'Force'], lightning: ['闪电', 'Lightning'],
    necrotic: ['黯蚀', 'Necrotic'], poison: ['毒素', 'Poison'], psychic: ['心灵', 'Psychic'], radiant: ['光耀', 'Radiant'], thunder: ['雷鸣', 'Thunder'],
    opportunity_attack: ['借机攻击', 'Opportunity Attack'], attack: ['攻击检定', 'Attack'], spell_attack: ['法术攻击检定', 'Spell Attack'],
    saving_throw: ['豁免检定', 'Saving Throw'], death_save: ['死亡豁免', 'Death Save'], medicine: ['医药检定', 'Medicine Check'], concentration: ['专注检定', 'Concentration Check'],
    blessed: ['祝福', 'Blessed'], hexed: ['受诅咒', 'Hexed'], shield_of_faith: ['虔诚护盾', 'Shield of Faith'],
  dodging: ['闪避', 'Dodging'], disengaged: ['已脱离接战', 'Disengaged'], stable: ['伤势稳定', 'Stable'], unconscious: ['昏迷', 'Unconscious'],
    dead: ['死亡', 'Dead'], defeated: ['已击败', 'Defeated'], death_saves: ['死亡豁免记录', 'Death Saves'],
    slowed_10: ['速度降低 10 尺', 'Speed reduced by 10 ft'], next_attack_advantage: ['下次攻击有优势', 'Next attack has advantage'],
    next_attack_disadvantage: ['下次攻击有劣势', 'Next attack has disadvantage'], faerie_fire: ['妖火标记', 'Faerie Fire'],
  }
  const pair = labels[key]
  return pair ? (locale.value.startsWith('zh') ? pair[0] : pair[1]) : raw.replaceAll('_', ' ')
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

function friendlyCombatError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : String(cause)
  if (!locale.value.startsWith('zh')) return message
  const outOfRange = message.match(/target is out of range \((\d+) > (\d+) feet\)/i)
  if (outOfRange) {
    return `目标距离为 ${outOfRange[1]} 尺，这个武器最远只能攻击 ${outOfRange[2]} 尺。请换远程武器，或先移动靠近。`
  }
  if (/not your turn|requested actor is not the current actor/i.test(message)) return '现在还没轮到这个角色。请等待当前行动者结束回合。'
  if (/already been spent|action is not available/i.test(message)) return '这个动作资源本回合已经用掉了。请选择仍可用的动作，或结束回合。'
  if (/state version|stale|expected_version/i.test(message)) return '战斗状态刚刚发生变化，界面已为你刷新。请按最新状态再试一次。'
  if (/automatic combat turn exceeded the safety limit/i.test(message)) return '敌方自动回合未能正常结束，本次操作已安全回滚。请刷新战斗状态后重试。'
  return message
}

function targetsFor(spell?: RulesetCombatSpell): RulesetCombatTarget[] {
  const targets = spellAction.value?.targets || attackAction.value?.targets || []
  if (!spell || !currentActor.value) return targets
  const allied = spell.mode === 'healing' || spell.mode === 'buff'
  return targets.filter(target => allied
    ? target.kind === currentActor.value?.kind
    : target.kind !== currentActor.value?.kind)
}

function resetSelections(): void {
  const guidedPresetId = guidedCombatStep.value?.encounter_preset_id
  const preset = guidedCombatPreset.value || requestedCombatPreset.value || selectableEncounterPresets.value[0]
  const shouldSelectEncounter = Boolean(
    guidedCombatStep.value
    || requestedCombatPreset.value
    || manualEncounterOpen.value
    // GM 明确点了「准备下一场遭遇」：与手动准备一样，预选目录第一条，
    // 否则开始按钮会一直是禁用状态。
    || nextEncounterOpen.value,
  )
  if (!shouldSelectEncounter) selectedPresetId.value = ''
  else if (guidedPresetId && guidedCombatPreset.value) selectedPresetId.value = guidedPresetId
  else if (!selectableEncounterPresets.value.some(item => item.id === selectedPresetId.value)) {
    selectedPresetId.value = preset?.id || ''
  }
  const spell = spellAction.value?.spells?.[0]
  selectedSpellRef.value = spell?.spell_ref || ''
  selectedSlot.value = spell?.available_slot_levels?.[0] ?? 0
  const targets = spell ? targetsFor(spell) : attackAction.value?.targets || []
  selectedTargetId.value = targets[0]?.actor_id || ''
  chooseUsableWeapon()
  movementDistance.value = Math.min(5, Number(moveAction.value?.movement_remaining || 5))
}

function clearGameScopedState(): void {
  data.value = null
  busy.value = false
  error.value = ''
  notice.value = ''
  selectedPresetId.value = ''
  selectedWeaponId.value = ''
  selectedSpellRef.value = ''
  selectedSlot.value = 0
  selectedTargetId.value = ''
  nextEncounterOpen.value = false
  manualEncounterOpen.value = false
  sandboxDeclared.value = false
  aiProposal.value = null
  aiError.value = ''
  movementDistance.value = 5
  staged.value = null
  returnFocus = null
}

function openManualEncounter(): void {
  manualEncounterOpen.value = true
  resetSelections()
}

// 与「手动准备遭遇」对称：打开下一场遭遇选择器时立即预选目录条目，
// 否则开始按钮会在用户没碰过下拉框前保持禁用。
function toggleNextEncounter(): void {
  nextEncounterOpen.value = !nextEncounterOpen.value
  if (nextEncounterOpen.value) resetSelections()
}

// GM 明确选择“临时准备自由遭遇”后才允许使用通用目录。
function declareSandboxEncounter(): void {
  sandboxDeclared.value = true
  manualEncounterOpen.value = true
  resetSelections()
}

function cancelAiProposal(): void {
  aiProposal.value = null
  aiError.value = ''
}

// 生成与开战严格分两步：这里只拿提案，绝不自动开战；失败完全无副作用。
async function planTemporaryEncounter(): Promise<void> {
  const gameKey = props.gameKey
  aiBusy.value = true
  aiError.value = ''
  try {
    const response = await planRulesetTemporaryEncounter(gameKey)
    if (props.gameKey !== gameKey) return
    if (response.encounter) {
      aiProposal.value = response.encounter
    } else {
      aiProposal.value = null
      aiError.value = response.error || copy.value.aiFailed
    }
  } catch (cause: unknown) {
    if (props.gameKey !== gameKey) return
    aiProposal.value = null
    aiError.value = friendlyCombatError(cause) || copy.value.aiFailed
  } finally {
    if (props.gameKey === gameKey) aiBusy.value = false
  }
}

// 确认入口：提交现有 combat.start（mode=sandbox + enemies，不带
// encounter_preset_id）；服务端权威校验再次完整执行，篡改预览也进不了战斗。
async function confirmAiEncounter(): Promise<void> {
  const proposal = aiProposal.value
  if (!proposal?.enemies?.length) return
  await submit({
    intent_id: intentId(),
    type: 'combat.start',
    expected_version: gameplay.value?.state_version ?? 0,
    mode: 'sandbox',
    enemies: proposal.enemies,
  })
  if (!error.value) {
    aiProposal.value = null
    aiError.value = ''
  }
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
    if (data.value.gameplay.combat?.status !== 'ended') nextEncounterOpen.value = false
    error.value = ''
    resetSelections()
  } catch (cause: unknown) {
    if (sequence !== loadSequence || props.gameKey !== gameKey) return
    if (!silent || !data.value) error.value = friendlyCombatError(cause)
  } finally {
    if (!silent && sequence === loadSequence && props.gameKey === gameKey) busy.value = false
  }
}

function intentId(): string {
  return globalThis.crypto?.randomUUID?.() || `intent-${Date.now()}-${Math.random().toString(16).slice(2)}`
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

async function submit(payload: JsonObject): Promise<void> {
  const gameKey = props.gameKey
  busy.value = true
  try {
    const response = await submitRulesetIntent(gameKey, payload)
    if (props.gameKey !== gameKey) return
    data.value = response
    staged.value = null
    error.value = ''
    notice.value = latestEvents.value.map(describeEvent).filter(Boolean).join(' · ')
    resetSelections()
    emit('refresh')
    if (payload.type === 'combat.end') emit('navigate', 'campaign')
  } catch (cause: unknown) {
    if (props.gameKey !== gameKey) return
    error.value = friendlyCombatError(cause)
    await load(true)
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
  if (!spell || !selectedTargetId.value || !spellAction.value) return
  stage({
    type: 'cast_spell', actor_id: spellAction.value.actor_id,
    target_id: selectedTargetId.value, spell_ref: spell.spell_ref,
    slot_level: selectedSlot.value,
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

async function startCombat(): Promise<void> {
  if (!selectedPreset.value) return
  const payload: JsonObject = {
    intent_id: intentId(), type: 'combat.start',
    expected_version: gameplay.value?.state_version ?? 0,
    encounter_preset_id: selectedPreset.value.id,
    encounter_instance_id: action('combat.start')?.encounter_instance_id,
    enemies: selectedPreset.value.enemies,
  }
  // 自由遭遇必须由 GM 明确声明，服务端据此拒绝把通用预设当成剧情绑定。
  if (sandboxFlow.value) payload.mode = 'sandbox'
  await submit(payload)
}

async function toggleReady(): Promise<void> {
  const item = isReady.value ? unreadyAction.value : readyAction.value
  if (!item) return
  await submit({
    intent_id: intentId(),
    type: item.type,
    expected_version: gameplay.value?.state_version ?? 0,
  })
}

async function decide(decision: RulesetPendingDecision, option: string): Promise<void> {
  const gameKey = props.gameKey
  busy.value = true
  try {
    const payload = {
      type: 'decision.resolve', decision_id: decision.decision_id,
      intent_id: intentId(), expected_version: gameplay.value?.state_version ?? 0, option,
    }
    const response = await resolveRulesetDecision(gameKey, decision.decision_id, payload)
    if (props.gameKey !== gameKey) return
    data.value = response
    error.value = ''
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
  selectedTargetId.value = targetsFor(spell)[0]?.actor_id || ''
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
      <section v-if="combat?.status !== 'active'" class="encounter-start">
        <template v-if="combat?.status !== 'ended'">
        <div v-if="narrativeCombatPending && !guidedCombatStep" class="story-bridge">
          <span class="story-bridge-label">{{ copy.storyBridge }}</span>
          <p>{{ copy.narrativeRequest }}</p>
        </div>
        <template v-if="guidedCombatStep && combat?.status !== 'ended'">
          <div class="story-bridge">
            <span class="story-bridge-label">{{ copy.storyBridge }}</span>
            <h3>{{ guidedCombatStep.title }}</h3>
            <p>{{ guidedCombatStep.narration }}</p>
            <aside><b>{{ copy.storyPurpose }}</b> {{ copy.storyAction }}</aside>
          </div>
        </template>
        <p v-else>{{ combat?.status === 'ended' ? copy.ended : copy.noCombat }}</p>
        <div v-if="guidedCombatStep" class="guided-preset">
          <span>{{ copy.guidedEncounter }}</span>
          <strong>{{ guidedCombatPreset?.name || guidedCombatStep.encounter_preset_id }}</strong>
          <small class="encounter-source">{{ copy.encounterSource }}：{{ guidedCombatStep.encounter_preset_id }}</small>
          <p>{{ guidedCombatPreset?.description }}</p>
          <p v-if="!guidedCombatPreset" class="combat-error" role="alert">{{ copy.missingPreset }}</p>
          <small v-else>{{ copy.guidedOnly }}</small>
        </div>
        <div v-else-if="storyUnprepared && !sandboxDeclared" class="guided-preset unprepared">
          <strong>{{ copy.unpreparedTitle }}</strong>
          <p>{{ copy.unpreparedHint }}</p>
          <div class="unprepared-actions">
            <button type="button" @click="emit('navigate', 'campaign')">
              <NIcon :component="PlayForwardOutline" />{{ copy.returnToAdventure }}
            </button>
            <button v-if="canPlanTemporaryEncounter" type="button" class="combat-primary" :disabled="aiBusy" @click="planTemporaryEncounter">
              <NIcon :component="SparklesOutline" />{{ aiBusy ? copy.aiPreparing : copy.aiPrepare }}
            </button>
            <button v-if="isGm" type="button" @click="declareSandboxEncounter">
              <NIcon :component="ShieldOutline" />{{ copy.unpreparedAction }}
            </button>
          </div>
          <p v-if="isGm" class="combat-state">{{ copy.aiHint }}</p>
        </div>
        <div v-else-if="requestedCombatPreset" class="guided-preset">
          <span>{{ copy.recommendedEncounter }}</span>
          <strong>{{ requestedCombatPreset.name }}</strong>
          <p>{{ requestedCombatPreset.description }}</p>
          <details v-if="isGm && selectableEncounterPresets.length > 1" class="encounter-alternatives">
            <summary>{{ copy.changeEncounter }}</summary>
            <label>
              <span>{{ copy.chooseEncounter }}</span>
              <select v-model="selectedPresetId">
                <option v-for="preset in selectableEncounterPresets" :key="preset.id" :value="preset.id">
                  {{ preset.name }} · {{ localizedTerm(preset.difficulty) }}
                </option>
              </select>
            </label>
          </details>
        </div>

        <p v-if="aiError && !aiProposal" class="combat-error" role="alert">{{ aiError }}</p>
        <div v-if="isGm && aiProposal" class="guided-preset ai-encounter-preview">
          <span>{{ copy.aiPreviewTag }}</span>
          <strong>{{ aiProposal.title }}</strong>
          <p>{{ aiProposal.description }}</p>
          <ul class="ai-encounter-enemies">
            <li v-for="enemy in aiProposal.enemies" :key="enemy.id">
              <header>
                <strong>{{ enemy.name }}</strong>
                <small>HP {{ enemy.hp }} · AC {{ enemy.armor_class }} · {{ copy.tempSpeed }} {{ enemy.speed }}</small>
              </header>
              <small v-for="attack in enemy.attacks" :key="attack.id" class="ai-encounter-attack">
                {{ attack.name }} +{{ attack.attack_bonus }} / {{ attack.damage }}
              </small>
            </li>
          </ul>
          <p class="combat-state">{{ copy.aiEncounterNote }}</p>
          <div class="unprepared-actions">
            <button type="button" :disabled="aiBusy" @click="planTemporaryEncounter">
              <NIcon :component="SparklesOutline" />{{ copy.aiRegenerate }}
            </button>
            <button type="button" class="combat-primary" :disabled="busy" @click="confirmAiEncounter">
              <NIcon :component="PlayForwardOutline" />{{ copy.aiConfirmStart }}
            </button>
            <button type="button" @click="cancelAiProposal">{{ copy.cancel }}</button>
          </div>
        </div>

        <section v-if="encounterReadiness?.required_count" class="party-readiness" aria-live="polite">
          <header>
            <strong>{{ copy.partyReady }}</strong>
            <span>{{ copy.readySummary }} {{ encounterReadiness.ready_count }}/{{ encounterReadiness.required_count }}</span>
          </header>
          <ul>
            <li v-for="player in encounterReadiness.players" :key="player.player_id" :class="{ ready: player.ready }">
              <NIcon :component="player.ready ? CheckmarkCircleOutline : HourglassOutline" />
              <span>{{ player.name }}</span>
              <strong>{{ player.ready ? copy.readyStatus : copy.unreadyStatus }}</strong>
            </li>
          </ul>
          <button v-if="!isGm && (readyAction || unreadyAction)" class="combat-primary" :disabled="busy" @click="toggleReady">
            <NIcon :component="isReady ? HourglassOutline : CheckmarkCircleOutline" />
            {{ isReady ? copy.cancelReady : copy.ready }}
          </button>
        </section>

        <template v-if="isGm && (action('combat.start') || sandboxDeclared)">
          <template v-if="!guidedCombatStep && !requestedCombatPreset">
            <div v-if="!manualEncounterOpen && !sandboxDeclared" class="unprepared-actions">
              <button type="button" class="manual-encounter-toggle" @click="openManualEncounter">
                <NIcon :component="ShieldOutline" />{{ copy.manualEncounter }}
              </button>
              <button v-if="canPlanTemporaryEncounter" type="button" class="ai-encounter-toggle" :disabled="aiBusy" @click="planTemporaryEncounter">
                <NIcon :component="SparklesOutline" />{{ aiBusy ? copy.aiPreparing : copy.aiPrepare }}
              </button>
            </div>
            <template v-else>
              <p v-if="sandboxDeclared" class="combat-warning">{{ copy.sandboxWarning }}</p>
              <p v-else-if="narrativeCombatPending" class="combat-state">{{ copy.sandboxHint }}</p>
              <label>
                <span>{{ copy.chooseFreeEncounter }}</span>
                <select v-model="selectedPresetId">
                  <option v-for="preset in selectableEncounterPresets" :key="preset.id" :value="preset.id">
                    {{ preset.name }} · {{ localizedTerm(preset.difficulty) }}
                  </option>
                </select>
              </label>
              <p class="preset-description">{{ selectedPreset?.description }}</p>
            </template>
          </template>
          <p v-if="!guidedCombatStep && !selectableEncounterPresets.length" class="combat-state">{{ copy.noEncounter }}</p>
          <p v-if="encounterReadiness?.required_count" class="combat-state">{{ copy.startHint }}</p>
          <button
            v-if="guidedCombatStep || requestedCombatPreset || manualEncounterOpen || sandboxDeclared"
            class="combat-primary"
            :disabled="busy || !selectedPreset"
            @click="startCombat"
          >
            <NIcon :component="PlayForwardOutline" />{{ guidedCombatStep ? copy.guidedStart : copy.start }}
          </button>
        </template>
        <p v-else-if="(guidedCombatStep || narrativeCombatPending) && combat?.status !== 'ended'" class="combat-state">{{ copy.waitingForGm }}</p>
        </template>
        <section v-else class="encounter-ended" aria-live="polite">
          <strong>{{ copy.ended }}</strong>
          <p>{{ copy.nextEncounterHint }}</p>
          <div class="encounter-ended-actions">
            <button type="button" @click="emit('navigate', 'campaign')"><NIcon :component="PlayForwardOutline" />{{ copy.returnToAdventure }}</button>
            <button v-if="isGm && action('combat.start')" type="button" class="combat-primary" @click="toggleNextEncounter">
              <NIcon :component="PlayForwardOutline" />{{ nextEncounterOpen ? copy.cancelNextEncounter : copy.nextEncounter }}
            </button>
          </div>
          <div v-if="nextEncounterOpen" class="next-encounter-picker">
            <label>
              <span>{{ copy.chooseEncounter }}</span>
              <select v-model="selectedPresetId">
                <option v-for="preset in selectableEncounterPresets" :key="preset.id" :value="preset.id">
                  {{ preset.name }} · {{ localizedTerm(preset.difficulty) }}
                </option>
              </select>
            </label>
            <p class="preset-description">{{ selectedPreset?.description }}</p>
            <button class="combat-primary" :disabled="busy || !selectedPreset" @click="startCombat">
              <NIcon :component="PlayForwardOutline" />{{ copy.start }}
            </button>
          </div>
        </section>
      </section>

      <template v-else>
        <section :class="['turn-banner', currentActor?.kind || 'player']" aria-live="polite">
          <div><small>{{ copy.round }} {{ combat.round }}</small><strong>{{ currentTurnLabel }}</strong></div>
          <span>{{ targetName(combat.current_actor_id) }}</span>
        </section>

        <div class="combat-summary">
          <span><NIcon :component="FlashOutline" /><i><small>{{ copy.action }}</small><strong>{{ combat.economy.action ? copy.available : copy.spent }}</strong></i></span>
          <span><NIcon :component="SparklesOutline" /><i><small>{{ copy.bonus }}</small><strong>{{ combat.economy.bonus_action ? copy.available : copy.spent }}</strong></i></span>
          <span><NIcon :component="FootstepsOutline" /><i><small>{{ copy.movement }}</small><strong>{{ combat.economy.movement ?? 0 }} {{ copy.feet }}</strong></i></span>
          <span><NIcon :component="ShieldOutline" /><i><small>{{ copy.reaction }}</small><strong>{{ combat.economy.reaction ? copy.available : copy.spent }}</strong></i></span>
        </div>
        <p v-if="combat.current_actor_id === `player:${actorId}`" class="turn-guide">{{ copy.turnGuide }}</p>

        <ol class="initiative" :aria-label="copy.current">
          <li
            v-for="(actorIdValue, index) in combat.initiative"
            :key="actorIdValue"
            :class="[{ current: actorIdValue === combat.current_actor_id }, actorIdValue.startsWith('enemy:') ? 'enemy' : 'player']"
            :aria-current="actorIdValue === combat.current_actor_id ? 'step' : undefined"
          >
            <span>{{ index + 1 }}</span><strong>{{ targetName(actorIdValue) }}</strong>
          </li>
        </ol>

        <section class="tactical-track" :aria-label="copy.tacticalTrack">
          <header><strong>{{ copy.tacticalTrack }}</strong><span>{{ copy.chooseDestination }}</span></header>
          <div class="track-surface">
            <div class="track-canvas" :style="trackCanvasStyle">
              <span
                v-for="(actor, index) in combat.actors"
                :key="`track-${actor.actor_id}`"
                :class="['track-token', actor.kind, { current: actor.actor_id === combat.current_actor_id }]"
                :style="tokenTrackStyle(actor.position, index)"
                :title="`${actor.name} · ${actor.position} ${copy.feet}`"
              >{{ actor.name.slice(0, 2) }}</span>
              <div class="track-cells" :style="{ gridTemplateColumns: `repeat(${trackTicks.length}, minmax(34px, 1fr))` }">
                <button
                  v-for="position in trackTicks"
                  :key="position"
                  type="button"
                  :class="{ reachable: canReachPosition(position), selected: selectedDestination === position }"
                  :disabled="!canReachPosition(position)"
                  :aria-label="`${copy.position} ${position} ${copy.feet}`"
                  @click="chooseTrackPosition(position)"
                ><span>{{ position }}</span></button>
              </div>
            </div>
          </div>
        </section>

        <div class="actor-grid">
          <article v-for="actor in combat.actors" :key="actor.actor_id" :class="['actor-card', actor.kind, { current: actor.actor_id === combat.current_actor_id, defeated: actor.hp <= 0 }]">
            <header><strong>{{ actor.name }}</strong><span>{{ actor.position }} {{ copy.feet }}</span></header>
            <div
              class="hp-track"
              role="progressbar"
              :aria-label="`${actor.name} ${copy.hp}`"
              aria-valuemin="0"
              :aria-valuemax="actor.max_hp"
              :aria-valuenow="actor.hp"
            ><i :style="{ width: `${Math.max(0, Math.min(100, actor.hp / Math.max(1, actor.max_hp) * 100))}%` }" /></div>
            <p>{{ copy.hp }} {{ actor.hp }}/{{ actor.max_hp }} · {{ copy.ac }} {{ actor.armor_class }}</p>
            <small v-if="Object.keys(actor.conditions || {}).length">
              {{ copy.conditions }}: {{ Object.keys(actor.conditions || {}).map(localizedTerm).join('、') }}
            </small>
          </article>
        </div>

        <section v-if="pendingDecision" class="decision-card" aria-live="assertive">
          <strong>{{ copy.reaction }} · {{ localizedTerm(pendingDecision.kind) }}</strong>
          <div>
            <button class="combat-primary" :disabled="busy" @click="decide(pendingDecision, 'resolve')">{{ copy.resolve }}</button>
            <button :disabled="busy" @click="decide(pendingDecision, 'decline')">{{ copy.decline }}</button>
          </div>
        </section>

        <section v-else-if="actions.length" class="available-actions">
          <h3>{{ copy.availableActions }}</h3>
          <div class="combat-actions">
          <section v-if="attackAction" class="action-card">
            <h3><NIcon :component="FlashOutline" />{{ copy.action }} · {{ copy.attack }}</h3>
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
            <button :disabled="busy || !selectedWeapon || !selectedTargetId || !selectedWeaponRange?.usable" @click="stageAttack"><NIcon :component="FlashOutline" />{{ copy.attack }}</button>
          </section>

          <section v-if="spellAction" class="action-card">
            <h3><NIcon :component="SparklesOutline" />{{ copy.action }} / {{ copy.bonus }} · {{ copy.cast }}</h3>
            <label>{{ copy.spell }}
              <select v-model="selectedSpellRef">
                <option v-for="spellItem in spellAction.spells" :key="spellItem.spell_ref" :value="spellItem.spell_ref">
                  {{ spellItem.name }} · {{ localizedTerm(spellItem.casting_time) }}
                </option>
              </select>
            </label>
            <label v-if="selectedSpell?.level">{{ copy.slot }}
              <select v-model.number="selectedSlot">
                <option v-for="level in selectedSpell.available_slot_levels" :key="level" :value="level">{{ level }}</option>
              </select>
            </label>
            <label>{{ copy.target }}
              <select v-model="selectedTargetId">
                <option v-for="targetItem in targetsFor(selectedSpell)" :key="targetItem.actor_id" :value="targetItem.actor_id">
                  {{ targetItem.name }} · {{ targetItem.hp }}/{{ targetItem.max_hp }} HP
                </option>
              </select>
            </label>
            <button :disabled="busy || !selectedSpell || !selectedTarget" @click="stageSpell"><NIcon :component="SparklesOutline" />{{ copy.cast }}</button>
          </section>

          <section v-if="moveAction" class="action-card">
            <h3><NIcon :component="FootstepsOutline" />{{ copy.movement }}</h3>
            <label>{{ copy.move }}
              <input v-model.number="movementDistance" type="number" :min="-Number(moveAction.movement_remaining || 0)" :max="Number(moveAction.movement_remaining || 0)" step="5">
              <small>± {{ moveAction.movement_remaining }} {{ copy.feet }}</small>
            </label>
            <button :disabled="busy || !movementDistance" @click="stageMove"><NIcon :component="FootstepsOutline" />{{ copy.move }}</button>
          </section>

          <section class="action-card compact-actions">
            <h3><NIcon :component="ShieldOutline" />{{ copy.economy }}</h3>
            <button v-if="action('dash')" :disabled="busy" @click="stageSimple('dash')"><NIcon :component="FootstepsOutline" />{{ copy.dash }}</button>
            <button v-if="action('dodge')" :disabled="busy" @click="stageSimple('dodge')"><NIcon :component="ShieldOutline" />{{ copy.dodge }}</button>
            <button v-if="action('disengage')" :disabled="busy" @click="stageSimple('disengage')"><NIcon :component="FootstepsOutline" />{{ copy.disengage }}</button>
            <button v-if="action('death_save')" :disabled="busy" @click="stageSimple('death_save')"><NIcon :component="SparklesOutline" />{{ copy.deathSave }}</button>
            <p v-if="action('end_turn')" class="end-turn-ready" role="status"><NIcon :component="PlayForwardOutline" />{{ copy.canEndTurn }}</p>
            <button v-if="action('end_turn')" :disabled="busy" @click="stageSimple('end_turn')"><NIcon :component="PlayForwardOutline" />{{ copy.endTurn }}</button>
            <button v-if="action('combat.end')" class="danger" :disabled="busy" @click="stageSimple('combat.end')"><NIcon :component="ShieldOutline" />{{ copy.endCombat }}</button>
          </section>
          </div>
        </section>
        <p v-else class="combat-state">{{ waitingText }}</p>

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
.dnd-combat { display: grid; grid-auto-rows: max-content; align-content: start; gap: 14px; margin: 14px 0; padding: 16px; border: 1px solid #806339; border-radius: 16px; background: linear-gradient(145deg, rgb(23 20 19 / 96%), rgb(19 27 35 / 96%)); color: #f2eadc; box-shadow: 0 18px 48px rgb(0 0 0 / 24%); }
.combat-header, .actor-card header, .decision-card, .confirm-card { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.combat-header h2 { margin: 2px 0 0; font: 700 clamp(19px, 2vw, 25px)/1.2 Georgia, serif; }
.eyebrow { margin: 0; color: #d7b873; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.preset-description, .combat-state { margin: 0; color: #bcb5aa; font-size: 13px; }
.combat-rules-note { color: #bcb5aa; font-size: 12px; }
.combat-rules-note summary { width: max-content; cursor: pointer; color: #d7b873; }
.combat-rules-note p { margin: 7px 0 0; line-height: 1.5; }
.combat-error { margin: 0; padding: 9px 11px; border: 1px solid #b85151; border-radius: 9px; background: rgb(115 28 28 / 25%); color: #ffd6d6; }
.story-bridge { display: grid; gap: 8px; padding: 14px; border: 1px solid #a17b3f; border-radius: 12px; background: linear-gradient(135deg, rgb(91 62 26 / 42%), rgb(27 30 34 / 88%)); }
.story-bridge-label { color: #f0c975; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.story-bridge h3, .story-bridge p { margin: 0; }
.story-bridge p { color: #e3d9c6; line-height: 1.6; }
.story-bridge aside { padding: 9px 11px; border-left: 3px solid #d5a64f; background: rgb(14 20 24 / 56%); color: #f2e6cf; line-height: 1.55; }
.guided-preset { display: grid; gap: 5px; padding: 11px 12px; border: 1px solid #a17b3f; border-radius: 10px; background: rgb(91 62 26 / 24%); }.guided-preset span, .guided-preset small { color: #f0c975; font-size: 12px; }.guided-preset strong { font-size: 17px; }.guided-preset p { margin: 0; color: #e3d9c6; line-height: 1.5; }
.guided-preset.unprepared { border-color: #b0803c; background: rgb(70 48 20 / 34%); }
.guided-preset.unprepared strong { color: #f4d9a4; }
.guided-preset .unprepared-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
.guided-preset .unprepared-actions button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.ai-encounter-preview { border-color: #6d6f4a; background: rgb(52 56 24 / 28%); }
.ai-encounter-enemies { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.ai-encounter-enemies li { display: grid; gap: 3px; padding: 8px 10px; border: 1px solid #6d5a35; border-radius: 9px; background: rgb(14 20 24 / 42%); }
.ai-encounter-enemies header { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.ai-encounter-enemies header small { color: #cfc6b4; }
.ai-encounter-attack { color: #b9c7b0; font-size: 12px; }
.encounter-source { color: #d9cba6; font-size: 11px; }
.combat-header-tools { display: flex; align-items: center; gap: 9px; }
.combat-mode-badge { padding: 3px 9px; border: 1px solid #6d5a35; border-radius: 999px; color: #f0d79c; background: rgb(64 46 20 / 55%); font-size: 11px; letter-spacing: .04em; white-space: nowrap; }
.combat-mode-badge.sandbox { border-color: #46626f; color: #bcd8e6; background: rgb(20 44 56 / 55%); }
.combat-warning { margin: 0; padding: 9px 11px; border: 1px solid #b0803c; border-radius: 9px; background: rgb(70 48 20 / 34%); color: #f2d9a8; font-size: 13px; line-height: 1.5; }
.encounter-alternatives { margin-top: 5px; }
.encounter-alternatives summary { color: #d5c5aa; cursor: pointer; font-size: 12px; }
.encounter-alternatives label { margin-top: 8px; }
.party-readiness { display: grid; gap: 8px; padding: 11px 12px; border: 1px solid #46525d; border-radius: 11px; background: rgb(14 21 28 / 72%); }
.party-readiness header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.party-readiness header span { color: #b9c3ca; font-size: 12px; }
.party-readiness ul { display: flex; gap: 7px; flex-wrap: wrap; margin: 0; padding: 0; list-style: none; }
.party-readiness li { display: inline-flex; align-items: center; gap: 6px; padding: 6px 8px; border: 1px solid #55464a; border-radius: 999px; color: #d6b9bb; background: #271d21; font-size: 12px; }
.party-readiness li.ready { border-color: #56745b; color: #c7e3ca; background: #18271d; }
.party-readiness li strong { font-size: 11px; }
.party-readiness button, .combat-primary { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.encounter-ended, .next-encounter-picker { display: grid; gap: 9px; }
.encounter-ended { padding: 13px; border: 1px solid #56745b; border-radius: 11px; background: rgb(24 45 29 / 68%); }
.encounter-ended > strong { color: #d2ebd3; font-size: 16px; }
.encounter-ended > p { margin: 0; color: #c5d5c6; line-height: 1.55; }
.encounter-ended-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.encounter-ended-actions button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.next-encounter-picker { margin-top: 4px; padding-top: 11px; border-top: 1px solid rgb(174 211 178 / 24%); }
.turn-banner { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 76px; padding: 13px 16px; border: 1px solid #4b6575; border-radius: 12px; background: linear-gradient(135deg, #173142, #111a22); }
.turn-banner.enemy { border-color: #78484b; background: linear-gradient(135deg, #452326, #17181c); }
.turn-banner div { display: grid; gap: 4px; }
.turn-banner small { color: #9db7c6; font-size: 11px; text-transform: uppercase; }
.turn-banner strong { font-size: 18px; }
.turn-banner > span { color: #f4d48d; font: 700 clamp(19px, 3vw, 28px)/1.2 Georgia, serif; text-align: right; }
.combat-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.combat-summary > span { display: flex; align-items: center; gap: 9px; padding: 9px 11px; border: 1px solid #394551; border-radius: 10px; background: #151c23; }
.combat-summary > span > .n-icon { flex: 0 0 auto; color: #e0b766; font-size: 20px; }
.combat-summary i { display: grid; gap: 3px; min-width: 0; font-style: normal; }
.combat-summary small { color: #9ca6ae; }
.turn-guide { margin: -4px 0 0; padding: 7px 10px; border-left: 3px solid #d2a855; color: #d9d1c4; background: rgb(80 58 23 / 24%); font-size: 12px; line-height: 1.5; }
.initiative { display: flex; gap: 6px; min-height: 34px; margin: 0; padding: 0; overflow-x: auto; overflow-y: hidden; list-style: none; }
.initiative li { display: inline-flex; align-items: center; gap: 6px; min-width: max-content; padding: 5px 9px 5px 6px; border: 1px solid #3b4650; border-radius: 999px; color: #aeb8c0; }
.initiative li > span { display: grid; width: 22px; height: 22px; place-items: center; border-radius: 50%; background: #26333d; font-size: 10px; }
.initiative li.enemy > span { background: #522a2d; }
.initiative li.current { border-color: #d2a855; background: #362b19; color: #ffe5a8; }
.tactical-track { display: grid; gap: 7px; padding: 11px; border: 1px solid #3b4650; border-radius: 12px; background: #0e151c; }
.tactical-track > header { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.tactical-track > header span { color: #aeb8c0; font-size: 12px; }
.track-surface { width: 100%; overflow-x: auto; overflow-y: hidden; scrollbar-gutter: stable; }
.track-canvas { position: relative; width: 100%; }
.track-cells { display: grid; width: 100%; }
.track-cells button { min-width: 34px; min-height: 34px; padding: 0; border-radius: 0; border-color: #35414b; color: #89949c; background: #151e26; font-size: 10px; }
.track-cells button.reachable { border-color: #97783d; color: #f0cf8b; background: #2c261b; cursor: pointer; }
.track-cells button.selected { border-color: #e4b75e; background: #5c421d; box-shadow: inset 0 0 0 2px #e4b75e; }
.track-cells button:disabled { opacity: 1; cursor: default; }
.track-token { position: absolute; z-index: 1; display: grid; place-items: center; width: 28px; height: 28px; transform: translateX(-50%); border: 2px solid #6ba2be; border-radius: 50%; background: #173347; color: #eef9ff; font-size: 10px; font-weight: 800; box-shadow: 0 4px 9px rgb(0 0 0 / 38%); }
.track-token.enemy { border-color: #c56b6b; background: #55292b; }
.track-token.current { outline: 3px solid #e4b75e; outline-offset: 2px; }
.actor-grid, .combat-actions { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }
.actor-card, .action-card { display: grid; gap: 8px; padding: 12px; border: 1px solid #3b4650; border-radius: 12px; background: #111820; }
.actor-card.enemy { border-color: #664446; }
.actor-card.current { border-color: #d2a855; box-shadow: inset 0 0 0 1px rgb(210 168 85 / 34%); }
.actor-card.defeated { opacity: .62; }
.actor-card p, .action-card h3 { margin: 0; }
.action-card h3, .action-card button { display: flex; align-items: center; justify-content: center; gap: 6px; }
.action-card h3 { justify-content: flex-start; }
.available-actions { display: grid; gap: 9px; }
.available-actions > h3 { margin: 0; color: #f0cf8b; font-size: 14px; }
.range-guide { margin: 0; color: #b8d7bc; font-size: 12px; line-height: 1.5; }
.range-guide.blocked { color: #f0b3a7; }
.actor-card small { color: #c5aa83; }
.hp-track { height: 6px; overflow: hidden; border-radius: 999px; background: #3a3030; }
.hp-track i { display: block; height: 100%; background: linear-gradient(90deg, #8d3030, #d77055); }
.action-card label, .encounter-start label { display: grid; gap: 5px; color: #c8c2b8; font-size: 12px; }
button, select, input { min-height: 44px; font: inherit; }
.action-card select, .action-card input, .encounter-start select { width: 100%; min-height: 44px; padding-inline: 10px; border: 1px solid #4a5560; border-radius: 8px; background: #0d1319; color: #f3eee7; }
.compact-actions { align-content: start; }
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
:global(body.light .dnd-combat .story-bridge) { border-color: #aa8546; background: #fff7e7; }
:global(body.light .dnd-combat .story-bridge p), :global(body.light .dnd-combat .story-bridge aside) { color: #403728; }
:global(body.light .dnd-combat .actor-card), :global(body.light .dnd-combat .action-card), :global(body.light .dnd-combat .combat-summary span) { border-color: #b8b2a6; background: #fff; }
:global(body.light .dnd-combat .tactical-track) { border-color: #b8b2a6; background: #f7f9fa; }
:global(body.light .dnd-combat .action-card select), :global(body.light .dnd-combat .action-card input), :global(body.light .dnd-combat .encounter-start select) { border-color: #908779; background: #fff; color: #211e1a; }
:global(body.light .dnd-combat .turn-banner) { border-color: #8ca4b0; background: linear-gradient(135deg, #edf6fa, #fff); }
:global(body.light .dnd-combat .turn-banner.enemy) { border-color: #c59b9b; background: linear-gradient(135deg, #fff0ef, #fff); }
:global(body.light .dnd-combat .turn-banner small), :global(body.light .dnd-combat .combat-rules-note), :global(body.light .dnd-combat .preset-description), :global(body.light .dnd-combat .combat-state), :global(body.light .dnd-combat .combat-summary small) { color: #514b43; }
:global(body.light .dnd-combat .resolution-log li) { background: #e8eef0; color: #27383e; }
:global(body.light .dnd-combat .guided-preset) { border-color: #b08a44; background: #fdf5e6; }
:global(body.light .dnd-combat .guided-preset p), :global(body.light .dnd-combat .guided-preset strong) { color: #3b3226; }
:global(body.light .dnd-combat .guided-preset.unprepared), :global(body.light .dnd-combat .combat-warning) { border-color: #b0803c; background: #fdf3de; }
:global(body.light .dnd-combat .combat-mode-badge) { border-color: #ad8a4b; background: #f6ecd6; color: #4b3a1c; }
:global(body.light .dnd-combat .combat-mode-badge.sandbox) { border-color: #8ba7b5; background: #eaf3f7; color: #26414d; }
@media (max-width: 700px) {
  .dnd-combat { gap: 10px; margin-inline: 0; padding: 10px; border-radius: 12px; }
  .combat-summary { display: flex; gap: 7px; padding-bottom: 3px; overflow-x: auto; scroll-snap-type: x proximity; }
  .combat-summary > span { flex: 0 0 134px; scroll-snap-align: start; }
  .actor-grid, .combat-actions { display: flex; gap: 8px; overflow-x: auto; scroll-snap-type: x proximity; }
  .actor-card, .action-card { flex: 0 0 min(82vw, 280px); scroll-snap-align: start; }
  .combat-header, .decision-card, .confirm-card { align-items: stretch; flex-direction: column; }
  .turn-banner { align-items: flex-start; flex-direction: column; }
  .turn-banner > span { text-align: left; }
  .tactical-track > header, .party-readiness header { align-items: flex-start; flex-direction: column; }
  .story-bridge, .guided-preset { padding: 10px; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; } }
</style>
