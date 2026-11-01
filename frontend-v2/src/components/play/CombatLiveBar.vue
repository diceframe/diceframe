<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { ListOutline, OpenOutline, ShieldOutline } from '@vicons/ionicons5'
import type { RulesetCombatEvent, RulesetGameplayView } from '@/api/types'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import { reportAssistantReaction } from '@/composables/useAssistantReactions'
import { combatRollEvidence } from '@/features/play/check-modes/combatEvidence'

const props = defineProps<{
  gameplay: RulesetGameplayView
  actorId: string
  embedded?: boolean
}>()
const emit = defineEmits<{ openCombat: [] }>()
const { locale } = useLocale()
const historyOpen = ref(false)
const changed = ref(false)
const presentedBatchId = ref('')
const queuedBatchIds = ref<string[]>([])
const replaying = ref(false)
let changedTimer: number | undefined
let playbackTimer: number | undefined
let playbackInitialized = false
const seenBatchIds = new Set<string>()

const zh = computed(() => locale.value.startsWith('zh'))
const combat = computed(() => props.gameplay.combat)
const pending = computed(() => props.gameplay.encounter_request?.status === 'pending')
const events = computed(() => props.gameplay.recent_combat_events || [])
// The server projection follows the authoritative ledger order. Do not infer a
// second chronology from round/turn fields here: several events in one intent
// legitimately share those values.
const orderedEvents = computed(() => events.value)
const eventBatches = computed(() => {
  const batches: Array<{ id: string; events: RulesetCombatEvent[] }> = []
  for (const event of orderedEvents.value) {
    const id = String(event.batch_id || event.event_id || '')
    const current = batches[batches.length - 1]
    if (!current || current.id !== id) batches.push({ id, events: [event] })
    else current.events.push(event)
  }
  return batches
})
const latestBatch = computed(() => eventBatches.value[eventBatches.value.length - 1])
const presentedBatch = computed(() => (
  eventBatches.value.find(batch => batch.id === presentedBatchId.value) || latestBatch.value
))
const presentedEvents = computed(() => presentedBatch.value?.events || [])
const currentActor = computed(() => combat.value.actors.find(
  actor => actor.actor_id === combat.value.current_actor_id,
))
const ownActor = computed(() => combat.value.actors.find(
  actor => actor.actor_id === `player:${props.actorId}`,
))
const isOwnTurn = computed(() => combat.value.current_actor_id === `player:${props.actorId}`)
const lastResult = computed(() => {
  const reversed = [...presentedEvents.value].reverse()
  for (const type of [
    'dnd2024.death_save.resolved', 'check.resolved', 'dnd2024.combat.ended',
    'dnd2024.combat.message', 'dnd2024.position.changed', 'dnd2024.combat.started',
    'dnd2024.turn.advanced', 'resource.changed',
  ]) {
    const event = reversed.find(item => item.type === type)
    if (event) return event
  }
  return undefined
})
const latestImpact = computed(() => [...presentedEvents.value].reverse().find(event => (
  event.type === 'resource.changed' && Number(event.amount ?? event.delta ?? 0) !== 0
)))
const isReplaying = computed(() => replaying.value)
const presentedActor = computed(() => presentedEvents.value.find(event => event.actor_id)?.actor_name || '')
const presentedActorId = computed(() => presentedEvents.value.find(event => event.actor_id)?.actor_id || '')
const displayOwnTurn = computed(() => isReplaying.value
  ? presentedActorId.value === `player:${props.actorId}`
  : isOwnTurn.value)
const displayEnemyTurn = computed(() => isReplaying.value
  ? presentedActorId.value.startsWith('enemy:')
  : currentActor.value?.kind === 'enemy')
const presentedRound = computed(() => presentedEvents.value.find(event => event.round)?.round || combat.value.round)
const deathSaves = computed(() => ownActor.value?.death_saves || {})
const deathSaveSuccesses = computed(() => Number(deathSaves.value.successes || 0))
const deathSaveFailures = computed(() => Number(deathSaves.value.failures || 0))
const showDeathSaves = computed(() => Boolean(
  ownActor.value && (ownActor.value.hp <= 0 || deathSaveSuccesses.value || deathSaveFailures.value),
))
const deathSaveStatus = computed(() => {
  const conditions = ownActor.value?.conditions || {}
  if ('dead' in conditions) return zh.value ? '角色死亡' : 'Dead'
  if ('stable' in conditions) return zh.value ? '伤势稳定' : 'Stable'
  return isOwnTurn.value ? (zh.value ? '本轮等待投掷' : 'Roll this turn') : (zh.value ? '等待下个回合' : 'Waiting for next turn')
})
const readiness = computed(() => props.gameplay.encounter_request?.readiness)
const latestResultKey = computed(() => {
  const event = lastResult.value
  return event ? `${event.event_id}:${event.state_version}` : ''
})
const title = computed(() => {
  if (pending.value && combat.value.status !== 'active') {
    const ready = readiness.value
    return zh.value
      ? `战斗准备 · ${ready?.ready_count || 0}/${ready?.required_count || 0} 名队友已准备`
      : `Combat ready · ${ready?.ready_count || 0}/${ready?.required_count || 0} party members ready`
  }
  if (combat.value.status === 'ended') return zh.value ? '战斗已经结束' : 'Combat ended'
  if (isReplaying.value && presentedActor.value) return zh.value
    ? `第 ${presentedRound.value} 轮 · ${presentedActor.value}的行动`
    : `Round ${presentedRound.value} · ${presentedActor.value}'s action`
  if (isOwnTurn.value) return zh.value
    ? `第 ${combat.value.round} 轮 · 轮到你行动`
    : `Round ${combat.value.round} · Your turn`
  const name = currentActor.value?.name || combat.value.current_actor_id
  return zh.value
    ? `第 ${combat.value.round} 轮 · ${name}正在行动`
    : `Round ${combat.value.round} · ${name} is acting`
})

function eventText(event?: RulesetCombatEvent): string {
  if (!event) return pending.value
    ? (zh.value ? '等待队伍准备与 GM 确认敌情。' : 'Waiting for the party and GM to confirm the encounter.')
    : (zh.value ? '战斗状态已经同步。' : 'Combat state synchronized.')
  const actor = event.actor_name || event.actor_id || ''
  const target = event.target_name || event.target_id || ''
  if (event.type === 'check.resolved') {
    const roll = event.total ?? event.natural ?? ''
    const dc = event.target ?? ''
    const verdict = event.success
      ? (event.critical ? (zh.value ? '暴击命中' : 'critical hit') : (zh.value ? '命中' : 'hit'))
      : (zh.value ? '未命中' : 'miss')
    const kind = String(event.kind || '')
    if (kind === 'death_save') {
      return zh.value
        ? `${actor}进行死亡豁免：d20 ${event.natural ?? roll}，${event.success ? '成功' : '失败'}`
        : `${actor} makes a death save: d20 ${event.natural ?? roll}, ${event.success ? 'success' : 'failure'}`
    }
    if (kind === 'attack' || kind === 'spell_attack' || kind === 'opportunity_attack') {
      const dice = combatRollEvidence(event, locale.value)
      const modifier = Number(event.modifier || 0) + Number(event.check_bonus || 0)
      return zh.value
        ? `${actor}攻击${target}：${dice} + ${modifier} = ${roll} vs AC ${dc}，${verdict}`
        : `${actor} attacks ${target}: ${dice} + ${modifier} = ${roll} vs AC ${dc}, ${verdict}`
    }
    return zh.value
      ? `${actor}攻击${target}：${roll} vs ${dc}，${verdict}`
      : `${actor} attacks ${target}: ${roll} vs ${dc}, ${verdict}`
  }
  if (event.type === 'resource.changed') {
    const amount = Math.abs(Number(event.delta || event.amount || 0))
    const healing = Boolean((event as RulesetCombatEvent & { healing?: boolean }).healing) || Number(event.delta || 0) > 0
    const critical = Boolean(event.critical)
    return healing
      ? (zh.value ? `${target}恢复 ${amount} 点生命` : `${target} recovers ${amount} HP`)
      : (zh.value
        ? `${target}受到 ${amount} 点伤害${critical ? '（暴击）' : ''}`
        : `${target} takes ${amount} damage${critical ? ' (critical)' : ''}`)
  }
  if (event.type === 'dnd2024.position.changed') {
    return zh.value ? `${actor}移动 ${Math.abs(Number(event.distance || 0))} 尺` : `${actor} moves ${Math.abs(Number(event.distance || 0))} ft`
  }
  if (event.type === 'dnd2024.combat.message') return `${actor}：${event.text || ''}`
  if (event.type === 'dnd2024.combat.started') return zh.value ? '先攻已经确定，战斗开始。' : 'Initiative is set. Combat begins.'
  if (event.type === 'dnd2024.combat.ended') {
    const reason = String(event.reason || '')
    if (reason === 'victory') return zh.value ? '敌人已经全部倒下，战斗胜利。' : 'All enemies are down. Victory.'
    if (reason === 'party_defeated') return zh.value ? '队伍已经战败。' : 'The party has been defeated.'
    if (reason === 'party_incapacitated') return zh.value ? '全队失去战斗能力，战斗已经停止。' : 'The whole party is incapacitated. Combat has stopped.'
    return zh.value ? '战斗结算完成。' : 'Combat resolved.'
  }
  if (event.type === 'dnd2024.turn.advanced') return zh.value ? `轮到${actor}行动` : `${actor}'s turn`
  if (event.type === 'dnd2024.death_save.resolved') {
    if (event.dead) return zh.value ? `${actor}死亡豁免失败满 3 次，已经死亡` : `${actor} has died after 3 failed death saves`
    if (event.stable) return zh.value ? `${actor}死亡豁免成功满 3 次，伤势稳定` : `${actor} is stable after 3 successful death saves`
    if (Number(event.hp || 0) > 0) return zh.value ? `${actor}掷出自然 20，恢复 1 HP 并苏醒` : `${actor} rolled a natural 20, regained 1 HP, and woke up`
    return zh.value
      ? `${actor}死亡豁免：成功 ${event.successes || 0}/3，失败 ${event.failures || 0}/3`
      : `${actor} death saves: ${event.successes || 0}/3 successes, ${event.failures || 0}/3 failures`
  }
  if (event.type === 'condition.applied') {
    const condition = String(event.condition || '')
    const labels: Record<string, [string, string]> = {
      dodging: ['进入闪避状态', 'starts dodging'], disengaged: ['脱离接战', 'disengages'],
      stable: ['伤势稳定', 'stabilizes'], unconscious: ['陷入昏迷', 'falls unconscious'],
    }
    const label = labels[condition] || [condition.replaceAll('_', ' '), condition.replaceAll('_', ' ')]
    return zh.value ? `${target || actor}${label[0]}` : `${target || actor} ${label[1]}`
  }
  if (event.type === 'condition.removed') {
    const condition = String(event.condition || '').replaceAll('_', ' ')
    return zh.value ? `${target || actor}不再处于${condition}状态` : `${target || actor} is no longer ${condition}`
  }
  return event.type.replace(/^dnd2024\./, '')
}

type HistoryToken = { text: string; kind: 'plain' | 'self' | 'ally' | 'enemy' | 'roll' }

function participantKind(id: unknown): HistoryToken['kind'] {
  const actorId = String(id || '')
  if (actorId === `player:${props.actorId}`) return 'self'
  if (actorId.startsWith('player:')) return 'ally'
  if (actorId.startsWith('enemy:')) return 'enemy'
  return 'plain'
}

function eventTokens(event: RulesetCombatEvent): HistoryToken[] {
  const source = eventText(event)
  const ranges: Array<{ start: number; end: number; kind: HistoryToken['kind'] }> = []
  const addText = (value: unknown, kind: HistoryToken['kind']) => {
    const needle = String(value || '').trim()
    if (!needle || kind === 'plain') return
    let start = source.indexOf(needle)
    while (start >= 0) {
      ranges.push({ start, end: start + needle.length, kind })
      start = source.indexOf(needle, start + needle.length)
    }
  }
  addText(event.actor_name, participantKind(event.actor_id))
  addText(event.target_name, participantKind(event.target_id))
  for (const match of source.matchAll(/d20\b[^，,]*?vs\s+AC\s+\d+/gi)) {
    const start = match.index ?? -1
    if (start >= 0) ranges.push({ start, end: start + match[0].length, kind: 'roll' })
  }
  ranges.sort((a, b) => a.start - b.start || b.end - a.end)
  const tokens: HistoryToken[] = []
  let cursor = 0
  for (const range of ranges) {
    if (range.start < cursor) continue
    if (range.start > cursor) tokens.push({ text: source.slice(cursor, range.start), kind: 'plain' })
    tokens.push({ text: source.slice(range.start, range.end), kind: range.kind })
    cursor = range.end
  }
  if (cursor < source.length) tokens.push({ text: source.slice(cursor), kind: 'plain' })
  return tokens.length ? tokens : [{ text: source, kind: 'plain' }]
}

const impactAmount = computed(() => Math.abs(Number(latestImpact.value?.amount || latestImpact.value?.delta || 0)))
const impactIsHealing = computed(() => Number(latestImpact.value?.delta || 0) > 0)
const impactTarget = computed(() => latestImpact.value?.target_name || latestImpact.value?.target_id || '')
const impactSource = computed(() => latestImpact.value?.actor_name || latestImpact.value?.actor_id || '')

function announceChange(duration = 2200): void {
  changed.value = true
  if (changedTimer) window.clearTimeout(changedTimer)
  changedTimer = window.setTimeout(() => { changed.value = false }, duration)
}

// Tell the DF Agent avatar about the most notable thing in a live batch,
// seen from this player's side of the table.
function reportCombatReaction(batch: RulesetCombatEvent[]): void {
  const own = `player:${props.actorId}`
  const ally = (id?: string) => Boolean(id && id.startsWith('player:'))
  for (const event of batch) {
    if (event.type === 'dnd2024.combat.started') return reportAssistantReaction('combatStart')
    if (event.type === 'dnd2024.combat.ended') return reportAssistantReaction('combatEnd')
  }
  const ownImpact = batch.find(event => event.type === 'resource.changed' && event.target_id === own && Number(event.amount ?? event.delta ?? 0) !== 0)
  if (ownImpact) {
    const delta = Number(ownImpact.amount ?? ownImpact.delta ?? 0)
    if (delta < 0 && typeof ownImpact.hp === 'number' && ownImpact.hp <= 0) return reportAssistantReaction('downed')
    return reportAssistantReaction(delta < 0 ? 'hurt' : 'heal', { amount: delta })
  }
  if (batch.some(event => event.type === 'dnd2024.death_save.resolved' && event.actor_id === own)) return reportAssistantReaction('deathSave')
  const attack = batch.find(event => event.type === 'check.resolved' && ally(event.actor_id)
    && ['attack', 'spell_attack', 'opportunity_attack'].includes(String(event.kind || '')))
  if (attack) reportAssistantReaction(attack.critical ? 'attackCrit' : attack.success ? 'attackHit' : 'attackMiss')
}

function playNextBatch(): void {
  if (playbackTimer) window.clearTimeout(playbackTimer)
  playbackTimer = undefined
  const next = queuedBatchIds.value.shift()
  if (!next) {
    replaying.value = false
    presentedBatchId.value = latestBatch.value?.id || ''
    return
  }
  replaying.value = true
  presentedBatchId.value = next
  reportCombatReaction(eventBatches.value.find(batch => batch.id === next)?.events || [])
  announceChange(1050)
  playbackTimer = window.setTimeout(playNextBatch, 1100)
}

watch(
  () => eventBatches.value.map(batch => batch.id).join('|'),
  () => {
    const ids = eventBatches.value.map(batch => batch.id).filter(Boolean)
    if (!playbackInitialized) {
      ids.forEach(id => seenBatchIds.add(id))
      presentedBatchId.value = ids[ids.length - 1] || ''
      playbackInitialized = true
      return
    }
    const additions = ids.filter(id => !seenBatchIds.has(id))
    additions.forEach(id => seenBatchIds.add(id))
    if (!additions.length) return
    queuedBatchIds.value.push(...additions)
    if (!replaying.value) playNextBatch()
  },
  { immediate: true },
)

watch(isOwnTurn, (own, wasOwn) => {
  if (own && wasOwn === false && combat.value.status === 'active') reportAssistantReaction('yourTurn')
})
watch(
  () => `${combat.value.round}:${combat.value.current_actor_id}`,
  (next, previous) => {
    if (!previous || next === previous) return
    announceChange()
  },
)
watch(latestResultKey, (next, previous) => {
  if (!previous || !next || next === previous) return
  announceChange(3000)
})
onBeforeUnmount(() => {
  if (changedTimer) window.clearTimeout(changedTimer)
  if (playbackTimer) window.clearTimeout(playbackTimer)
})
</script>

<template>
  <section
    class="combat-live-bar"
    :class="{ own: displayOwnTurn, enemy: displayEnemyTurn, changed, pending }"
    aria-live="assertive"
  >
    <NIcon class="combat-live-icon" :component="ShieldOutline" />
    <div class="combat-live-copy">
      <strong>{{ title }} <em v-if="changed" class="combat-live-updated">{{ zh ? '刚刚更新' : 'Updated just now' }}</em></strong>
      <span>{{ eventText(lastResult) }}</span>
      <div v-if="showDeathSaves" class="combat-death-saves" role="status">
        <b>{{ zh ? '死亡豁免' : 'Death saves' }}</b>
        <span class="save-pips success-pips"><small>{{ zh ? '成功' : 'Success' }}</small><i v-for="slot in 3" :key="`success-${slot}`" :class="{ filled: slot <= deathSaveSuccesses }" /></span>
        <span class="save-pips failure-pips"><small>{{ zh ? '失败' : 'Failure' }}</small><i v-for="slot in 3" :key="`failure-${slot}`" :class="{ filled: slot <= deathSaveFailures }" /></span>
        <em>{{ deathSaveStatus }}</em>
      </div>
    </div>
    <div v-if="latestImpact" class="combat-live-impact" :class="{ healing: impactIsHealing }" role="status" aria-live="polite">
      <small>{{ impactSource }} → {{ impactTarget }} · HP</small>
      <strong>{{ impactIsHealing ? '+' : '-' }}{{ impactAmount }}</strong>
      <span>{{ impactIsHealing ? (zh ? '恢复' : 'healed') : (zh ? '伤害' : 'damage') }}</span>
    </div>
    <div class="combat-live-actions">
      <button type="button" @click="historyOpen = true">
        <NIcon :component="ListOutline" />{{ zh ? '行动历史' : 'History' }}
      </button>
      <button v-if="!props.embedded" type="button" class="primary" @click="emit('openCombat')">
        <NIcon :component="OpenOutline" />{{ zh ? '打开战斗工具' : 'Open combat' }}
      </button>
    </div>
  </section>

  <Modal
    v-if="historyOpen"
    :title="zh ? '公共战斗行动历史' : 'Shared combat history'"
    dialog-class="combat-history-dialog"
    @close="historyOpen = false"
  >
    <p v-if="!events.length" class="combat-history-empty">{{ zh ? '尚无战斗记录。' : 'No combat events yet.' }}</p>
    <ol v-else class="combat-history-list">
      <li v-for="(event, eventIndex) in [...orderedEvents].reverse()" :key="event.event_id">
        <span class="combat-history-meta">
          <span v-if="event.round" class="combat-history-round">{{ zh ? `第 ${event.round} 轮` : `Round ${event.round}` }}</span>
          <span v-if="eventIndex === 0" class="combat-history-new">{{ zh ? '新' : 'NEW' }}</span>
        </span>
        <strong><span
          v-for="(token, index) in eventTokens(event)"
          :key="`${event.event_id}-${index}`"
          :class="['combat-history-token', token.kind]"
        >{{ token.text }}</span></strong>
      </li>
    </ol>
  </Modal>
</template>

<style scoped>
.combat-live-bar { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: 12px; padding: 11px 13px; border: 1px solid #536b7b; border-radius: 13px; background: linear-gradient(115deg, rgb(19 37 49 / 96%), rgb(19 25 31 / 96%)); color: #edf5f8; box-shadow: 0 10px 26px rgb(0 0 0 / 18%); }
.combat-live-bar.own { border-color: #d0a54d; background: linear-gradient(115deg, rgb(76 55 20 / 96%), rgb(29 29 27 / 96%)); }
.combat-live-bar.enemy { border-color: #945257; background: linear-gradient(115deg, rgb(69 31 34 / 96%), rgb(27 25 28 / 96%)); }
.combat-live-bar.pending { border-color: #8f733e; }
.combat-live-bar.changed { animation: combat-live-pulse 1.2s ease-out; }
.combat-live-icon { font-size: 24px; color: #e4b75e; }
.combat-live-copy { display: grid; min-width: 0; gap: 3px; }
.combat-live-copy > strong, .combat-live-copy > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.combat-live-copy strong { font-size: 14px; }
.combat-live-updated { display: inline-block; margin-left: 6px; padding: 2px 6px; border: 1px solid #e4b75e; border-radius: 999px; color: #ffe4a0; background: rgb(228 183 94 / 16%); font-size: 10px; font-style: normal; font-weight: 700; vertical-align: middle; }
.combat-live-copy span { color: #c4d0d6; font-size: 12px; }
.combat-live-impact { display: grid; grid-template-columns: auto auto auto; align-items: baseline; gap: 5px; padding: 7px 10px; border: 1px solid #ad4d56; border-radius: 9px; background: #4b1f25; white-space: nowrap; }
.combat-live-impact small { color: #ffc8cc; font-size: 10px; grid-column: 1 / -1; }
.combat-live-impact strong { color: #ffe8e9; font-size: 22px; line-height: 1; }
.combat-live-impact span { color: #ffc8cc; font-size: 11px; }
.combat-live-impact.healing { border-color: #4c9978; background: #173d30; }.combat-live-impact.healing small, .combat-live-impact.healing span { color: #bdebd7; }.combat-live-impact.healing strong { color: #d9ffed; }
.combat-live-bar.changed .combat-live-impact { animation: combat-impact-pop .46s ease-out; }
.combat-death-saves { display: flex; flex-wrap: wrap; align-items: center; gap: 7px 10px; margin-top: 5px; }
.combat-death-saves > b { color: #f3e6cf; font-size: 11px; }
.combat-death-saves > em { color: #d6c8b4; font-size: 10px; font-style: normal; }
.save-pips { display: inline-flex; align-items: center; gap: 4px; }
.save-pips small { margin-right: 2px; color: #bfc8cd; font-size: 9px; }
.save-pips i { width: 9px; height: 9px; border: 1px solid #75838a; border-radius: 50%; background: transparent; }
.save-pips.success-pips i.filled { border-color: #69c99c; background: #69c99c; box-shadow: 0 0 7px rgb(105 201 156 / 50%); }
.save-pips.failure-pips i.filled { border-color: #ef737c; background: #ef737c; box-shadow: 0 0 7px rgb(239 115 124 / 50%); }
.combat-live-actions { display: flex; gap: 7px; }
.combat-live-actions button { display: inline-flex; align-items: center; gap: 5px; min-height: 38px; }
.combat-history-list { display: grid; gap: 7px; max-height: min(62vh, 560px); margin: 0; padding: 0; overflow: auto; list-style: none; }
.combat-history-list li { display: grid; grid-template-columns: minmax(72px, auto) minmax(0, 1fr); gap: 9px; padding: 9px 11px; border: 1px solid var(--df-border-soft); border-radius: 9px; background: var(--df-surface-2); }
.combat-history-meta { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 5px; }
.combat-history-round { color: var(--df-text-muted); font-size: 11px; }
.combat-history-new { display: inline-flex; align-items: center; min-height: 18px; padding: 1px 5px; border: 1px solid #e4b75e; border-radius: 999px; color: #ffe4a0; background: rgb(228 183 94 / 16%); font-size: 10px; font-style: normal; font-weight: 800; letter-spacing: .04em; line-height: 1; }
.combat-history-list strong { font-size: 13px; font-weight: 600; }
.combat-history-token.self { color: #f1c768; }.combat-history-token.ally { color: #76c8f2; }.combat-history-token.enemy { color: #f17f86; }.combat-history-token.roll { color: #7ddbd4; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-weight: 800; }
.combat-history-empty { color: var(--df-text-muted); }
@keyframes combat-live-pulse { 0% { box-shadow: 0 0 0 0 rgb(228 183 94 / 58%); } 55% { box-shadow: 0 0 0 7px rgb(228 183 94 / 0%); } 100% { box-shadow: 0 10px 26px rgb(0 0 0 / 18%); } }
@keyframes combat-impact-pop { 0% { opacity: .3; transform: scale(.84); } 65% { transform: scale(1.06); } 100% { opacity: 1; transform: scale(1); } }
@media (max-width: 700px) {
  .combat-live-bar { position: sticky; z-index: 8; top: 4px; grid-template-columns: auto minmax(0, 1fr); gap: 8px; padding: 9px 10px; }
  .combat-live-icon { font-size: 20px; }
  .combat-live-impact { grid-column: 1 / -1; justify-self: stretch; }
  .combat-live-actions { grid-column: 1 / -1; }
  .combat-live-actions button { flex: 1; justify-content: center; min-height: 36px; padding: 0 8px; font-size: 12px; }
  .combat-live-copy span { white-space: normal; line-height: 1.35; }
  .combat-history-list li { grid-template-columns: 1fr; gap: 3px; }
}
@media (prefers-reduced-motion: reduce) { .combat-live-bar.changed, .combat-live-bar.changed .combat-live-impact { animation: none; } }
</style>
