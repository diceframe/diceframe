<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { CheckmarkCircleOutline, WarningOutline, InformationCircleOutline } from '@vicons/ionicons5'
import type { CheckResult, LogEntry, PublicAction, Player } from '@/api/types'
import type { DiceTag } from '@/utils/play'
import { parseAction, playerColor } from '@/utils/play'
import { api } from '@/api/client'
import { parseGMText, type LoreKeywords } from '@/utils/renderer'
import { useLocale } from '@/composables/useLocale'
import PortraitImage from '@/components/PortraitImage.vue'

const props = defineProps<{ log: LogEntry[]; live: PublicAction[]; players: Player[]; round: number; lore?: LoreKeywords; gameKey?: string; ruleId?: string; processing?: boolean; isGm?: boolean; liveNarration?: string; pendingChecks?: CheckResult[]; currentUserId?: string; luckBusyId?: string }>()
const emit = defineEmits<{ refresh: []; luck: [check: CheckResult, spend: boolean] }>()
const { t } = useLocale()

const box = ref<HTMLElement | null>(null), hasNew = ref(false), awayFromBottom = ref(false)
const swipeError = ref("")
const INITIAL_VISIBLE_ROUNDS = 20
const ROUND_BATCH_SIZE = 20
const visibleRoundCount = ref(INITIAL_VISIBLE_ROUNDS)
function name(uid: string, fallback?: string) { return fallback || props.players.find(p => p.user_id === uid)?.character_name || uid || t('characters') }
function portrait(uid: string) { return props.players.find(p => p.user_id === uid)?.character_sheet?.portrait }

interface Act { uid: string; text: string; dice: DiceTag | null }
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}
function toAct(input: unknown): Act {
  const source = record(input)
  const full = String(source.text || source.action || input || '')
  const { text, dice } = parseAction(full)
  return { uid: String(source.user_id || ''), text, dice }
}
function actions(entry: LogEntry): Act[] {
  const raw = entry.player_actions || entry.actions || []
  if (Array.isArray(raw)) return raw.map(toAct).filter(action => action.uid !== 'system')
  if (raw && typeof raw === 'object') return Object.entries(raw).map(([uid, text]) => { const p = parseAction(String(text)); return { uid, text: p.text, dice: p.dice } }).filter(action => action.uid !== 'system')
  return []
}
function checks(entry: LogEntry): CheckResult[] {
  return Array.isArray(entry.check_results) ? entry.check_results : []
}
function checkMath(check: CheckResult): string {
  if (typeof check.threshold === 'number') return `${check.dice || 'd100'}=${check.roll} / ${check.threshold}`
  const modifier = Number(check.modifier || 0)
  const modifierText = modifier ? ` ${modifier > 0 ? '+' : '-'} ${Math.abs(modifier)}` : ''
  const total = typeof check.total === 'number' ? ` = ${check.total}` : ''
  const dc = typeof check.dc === 'number' ? ` / DC ${check.dc}` : ''
  return `${check.dice || 'd20'}=${check.roll}${modifierText}${total}${dc}`
}
function liveAct(a: PublicAction): Act { return toAct(a) }
function canDecideLuck(check: CheckResult): boolean {
  return !!props.isGm || (!!props.currentUserId && check.actor_uid === props.currentUserId)
}

const visibleLog = computed(() => props.log.slice(-visibleRoundCount.value))
const hiddenRoundCount = computed(() => Math.max(0, props.log.length - visibleLog.value.length))
const rounds = computed(() => visibleLog.value.map((entry, index) => {
  const sw = entry.swipes || []
  const cur = Number(entry.current_swipe) || 0
  return { entry, round: Number(entry.round || props.log.length - visibleLog.value.length + index), gm: entry.gm_response ? parseGMText(String(entry.gm_response), props.lore) : null, swipes: sw, swipeCur: cur, swipeCount: sw.length }
}))

async function showEarlier() {
  const el = box.value
  const previousHeight = el?.scrollHeight || 0
  const previousTop = el?.scrollTop || 0
  visibleRoundCount.value = Math.min(props.log.length, visibleRoundCount.value + ROUND_BATCH_SIZE)
  await nextTick()
  if (el) el.scrollTop = previousTop + el.scrollHeight - previousHeight
}

function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error || t('branchOperationFailed')) }
async function swipeTo(round: number, idx: number) {
  if (!props.gameKey) return
  try {
    swipeError.value = ""
    await api<unknown>(`/games/${encodeURIComponent(props.gameKey)}/swipe/${round}`, { method: 'POST', body: JSON.stringify({ swipe_index: idx }) })
    emit('refresh')
  } catch (error: unknown) {
    swipeError.value = errorMessage(error)
  }
}
async function reroll(round: number) {
  if (!props.gameKey) return
  try {
    swipeError.value = ""
    await api<unknown>(`/games/${encodeURIComponent(props.gameKey)}/swipe/${round}`, { method: 'PUT', body: '{}' })
    emit('refresh')
  } catch (error: unknown) {
    swipeError.value = errorMessage(error)
  }
}
const initialized = ref(false)
function isNearBottom() {
  const el = box.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= 72
}
function updateScrollState() {
  const near = isNearBottom()
  awayFromBottom.value = !near
  if (near) hasNew.value = false
}
function latest() {
  const el = box.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  hasNew.value = false
  awayFromBottom.value = false
}
function onScroll() { updateScrollState() }
watch(() => [props.log.length, JSON.stringify(props.live), props.processing, props.liveNarration], async () => {
  const wasNearBottom = isNearBottom()
  await nextTick()
  if (!initialized.value) {
    latest()
    initialized.value = true
  } else if (wasNearBottom) {
    latest()
  } else {
    hasNew.value = true
    updateScrollState()
  }
})
watch(() => props.gameKey, () => {
  visibleRoundCount.value = INITIAL_VISIBLE_ROUNDS
  initialized.value = false
  hasNew.value = false
  awayFromBottom.value = false
})
</script>

<template>
  <div class="timeline-wrap">
    <div ref="box" class="timeline" data-testid="timeline" @scroll.passive="onScroll">
      <div v-if="hiddenRoundCount" class="timeline-history-gate">
        <button type="button" class="ghost" @click="showEarlier">{{ t('showEarlierRounds', { count: Math.min(ROUND_BATCH_SIZE, hiddenRoundCount) }) }}</button>
        <span>{{ t('earlierRoundsHidden', { count: hiddenRoundCount }) }}</span>
      </div>
      <template v-for="item in rounds" :key="item.round">
        <div class="round-divider" v-if="item.round">{{ t('roundDivider', { round: item.round }) }}</div>
        <div v-for="a in actions(item.entry)" :key="a.uid + a.text" class="message player message-with-avatar" :style="{ borderLeftColor: playerColor(a.uid) }">
          <PortraitImage :portrait="portrait(a.uid)" :rule-id="ruleId" :seed="a.uid" :name="name(a.uid)" :size="42" />
          <div class="message-copy">
            <strong :style="{ color: playerColor(a.uid) }">{{ name(a.uid) }}</strong>
            <p>{{ a.text }}</p>
            <span v-if="a.dice" class="dice-tag">🎲 {{ a.dice.system }}={{ a.dice.value }}</span>
          </div>
        </div>
        <div v-for="check in checks(item.entry)" :key="check.check_id || `${check.actor_uid}-${check.roll}`" class="message check-result-card" :class="{ success: String(check.verdict).includes('成功'), failure: String(check.verdict).includes('失败') }">
          <strong><NIcon :component="InformationCircleOutline" size="15" /> {{ check.label || '检定' }} · {{ check.actor_name }}</strong>
          <p>{{ checkMath(check) }} → <b>{{ check.verdict }}</b></p>
          <details v-if="typeof check.hard_threshold === 'number'">
            <summary>成功等级</summary>
            <span>普通 ≤ {{ check.threshold }} · 困难 ≤ {{ check.hard_threshold }} · 极难 ≤ {{ check.extreme_threshold }}</span>
          </details>
          <span v-if="check.luck_decision === 'spent' && check.luck_spent" class="dice-tag">{{ t('luckSpent', { cost: check.luck_spent }) }}</span>
          <span v-else-if="check.luck_decision === 'declined'" class="dice-tag">{{ t('luckDeclined') }}</span>
          <span v-else-if="check.luck_spend_available && check.luck_cost" class="dice-tag">{{ t('spendLuckForSuccess', { cost: check.luck_cost }) }}</span>
        </div>
        <div v-if="item.gm" class="message gm message-with-avatar">
          <span class="narrator-avatar" aria-hidden="true">GM</span>
          <div class="message-copy">
          <strong>{{ t('gmRound', { round: item.round }) }}</strong>
          <p v-for="(p, i) in item.gm.paragraphs" :key="'p' + i" class="chat-gm" v-html="p"></p>
          <div v-if="item.gm.states.length" class="state-card-list">
            <div v-for="(s, i) in item.gm.states" :key="'s' + i" class="state-card" :class="s.cls">
              <span class="state-card-title">
                <NIcon v-if="s.cls === 'good'" :component="CheckmarkCircleOutline" size="14" />
                <NIcon v-else-if="s.cls === 'warn'" :component="WarningOutline" size="14" />
                <NIcon v-else :component="InformationCircleOutline" size="14" />
                {{ s.title }}
              </span>
              <div class="state-card-body" v-html="s.body"></div>
            </div>
          </div>
          <div v-if="item.gm.tags.length" class="tag-line">
            <span v-for="(t, i) in item.gm.tags" :key="'t' + i" class="tag-badge" :class="t.cls">{{ t.text }}</span>
          </div>
          <div v-if="item.swipeCount > 1 && isGm" class="swipe-bar">
            <div class="swipe-group">
            <button @click="swipeTo(item.round, item.swipeCur - 1)" :disabled="item.swipeCur <= 0">←</button>
            <span>{{ item.swipeCur + 1 }}/{{ item.swipeCount }}</span>
            <button @click="swipeTo(item.round, item.swipeCur + 1)" :disabled="item.swipeCur >= item.swipeCount - 1">→</button>
            </div>
            <button v-if="item.swipeCount < 5" class="ghost" @click="reroll(item.round)">{{ t('regenerate') }}</button>
          </div>
          <p v-if="swipeError && isGm" class="muted">{{ swipeError }}</p>
          </div>
        </div>
      </template>
      <div v-for="a in live" :key="a.user_id" class="message player live message-with-avatar" :style="{ borderLeftColor: playerColor(a.user_id) }">
        <PortraitImage :portrait="portrait(a.user_id)" :rule-id="ruleId" :seed="a.user_id" :name="name(a.user_id, a.character_name)" :size="42" />
        <div class="message-copy">
          <strong :style="{ color: playerColor(a.user_id) }">{{ name(a.user_id, a.character_name) }} · {{ t('published') }} · {{ a.revision_count || 1 }}/3</strong>
          <p>{{ liveAct(a).text }}</p>
          <span v-if="liveAct(a).dice" class="dice-tag">🎲 {{ liveAct(a).dice?.system }}={{ liveAct(a).dice?.value }}</span>
        </div>
      </div>
      <div v-for="check in pendingChecks || []" :key="`pending-${check.check_id}`" class="message check-result-card failure pending-luck-card">
        <strong><NIcon :component="InformationCircleOutline" size="15" /> {{ check.label || '检定' }} · {{ check.actor_name }}</strong>
        <p>{{ checkMath(check) }} → <b>{{ check.verdict }}</b></p>
        <details v-if="typeof check.hard_threshold === 'number'">
          <summary>成功等级</summary>
          <span>普通 ≤ {{ check.threshold }} · 困难 ≤ {{ check.hard_threshold }} · 极难 ≤ {{ check.extreme_threshold }}</span>
        </details>
        <div v-if="canDecideLuck(check)" class="luck-decision-actions">
          <button class="dice-tag dice-tag-button" type="button" :disabled="!!luckBusyId" @click="emit('luck', check, true)">{{ t('spendLuckForSuccess', { cost: check.luck_cost || 0 }) }}</button>
          <button class="ghost luck-decline-button" type="button" :disabled="!!luckBusyId" @click="emit('luck', check, false)">{{ t('keepFailure') }}</button>
        </div>
        <span v-else class="dice-tag">{{ t('waitLuckDecision', { name: check.actor_name || check.actor_uid || '' }) }}</span>
      </div>
      <div v-if="processing" class="message gm thinking-message message-with-avatar" aria-live="polite">
        <span class="narrator-avatar" aria-hidden="true">GM</span>
        <div class="message-copy">
          <strong>{{ t('thinkingMessage') }}<span v-if="!liveNarration" class="thinking-dots"><i></i><i></i><i></i></span></strong>
          <p v-if="liveNarration" class="chat-gm live-narration">{{ liveNarration }}</p>
        </div>
      </div>
      <div v-if="!log.length && !live.length && !processing" class="timeline-empty"><strong>{{ t('adventureNotStarted') }}</strong><span>{{ t('firstActionHint') }}</span></div>
    </div>
    <button
      v-if="awayFromBottom || hasNew"
      class="new-message"
      :class="{ unread: hasNew }"
      type="button"
      :aria-label="t('scrollLatest')"
      @click="latest"
    >{{ hasNew ? t('newMessages') : t('backToBottom') }} ↓</button>
  </div>
</template>
