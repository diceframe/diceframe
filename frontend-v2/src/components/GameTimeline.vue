<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { CheckmarkCircleOutline, WarningOutline, InformationCircleOutline, ReaderOutline } from '@vicons/ionicons5'
import type { CheckResult, LogEntry, PublicAction, Player, StoryRecap } from '@/api/types'
import type { DiceTag } from '@/utils/play'
import { parseAction, playerColor } from '@/utils/play'
import { api } from '@/api/client'
import { parseGMText, type LoreKeywords } from '@/utils/renderer'
import { useLocale } from '@/composables/useLocale'
import PortraitImage from '@/components/PortraitImage.vue'
import CheckRevealCard from '@/components/play/CheckRevealCard.vue'
import { initializeTts, speakingKey, ttsSupported, ttsToggle } from '@/utils/tts'

const props = defineProps<{ log: LogEntry[]; live: PublicAction[]; players: Player[]; round: number; lore?: LoreKeywords; gameKey?: string; ruleId?: string; processing?: boolean; isGm?: boolean; liveNarration?: string; pendingChecks?: CheckResult[]; revealChecks?: CheckResult[]; currentUserId?: string; luckBusyId?: string }>()
const emit = defineEmits<{ refresh: []; luck: [check: CheckResult, spend: boolean] }>()

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
function recaps(entry: LogEntry): StoryRecap[] {
  return Array.isArray(entry.story_recaps)
    ? entry.story_recaps.filter(recap => recap && typeof recap.text === 'string' && recap.text.trim())
    : []
}
function recapRange(recap: StoryRecap): string {
  const from = Number(recap.from_round || 0)
  const to = Number(recap.to_round || from)
  if (from === 0 && to === 0) return t('storyRecapOpening')
  if (from === 0) return t('storyRecapOpeningRange', { to })
  return from === to
    ? t('storyRecapSingleRound', { round: to })
    : t('storyRecapRange', { from, to })
}
function liveAct(a: PublicAction): Act { return toAct(a) }
function canDecideLuck(check: CheckResult): boolean {
  return !!props.isGm || (!!props.currentUserId && check.actor_uid === props.currentUserId)
}
const activeChecks = computed(() => props.revealChecks?.length ? props.revealChecks : (props.pendingChecks || []))
function emitLuck(check: CheckResult, spend: boolean) { emit('luck', check, spend) }

const visibleLog = computed(() => props.log.slice(-visibleRoundCount.value))
const hiddenRoundCount = computed(() => Math.max(0, props.log.length - visibleLog.value.length))
const rounds = computed(() => visibleLog.value.map((entry, index) => {
  const sw = entry.swipes || []
  const cur = Number(entry.current_swipe) || 0
  return { entry, round: Number(entry.round || props.log.length - visibleLog.value.length + index), gm: entry.gm_response ? parseGMText(String(entry.gm_response), props.lore) : null, swipes: sw, swipeCur: cur, swipeCount: sw.length }
}))
const recapSignature = computed(() => props.log.flatMap(entry => recaps(entry).map(recap => recap.id || recap.text)).join('|'))

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
watch(() => [props.log.length, JSON.stringify(props.live), props.processing, props.liveNarration, recapSignature.value], async () => {
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
  void initializeTts()
}, { immediate: true })

// --- 本地朗读（文字转语音） ---
const { t, isEnglish } = useLocale()
const ttsVoiceLang = computed(() => (isEnglish.value ? 'en-US' : 'zh-CN'))
const autoSpeakKey = 'trpg_auto_speak_gm'
function autoSpeakEnabled(): boolean {
  try { return localStorage.getItem(autoSpeakKey) === '1' } catch { return false }
}
// 新 GM 叙事到达时若开启自动朗读，则朗读该段。仅在有新内容且用户开启时触发。
const lastAutoSpoken = ref('')
watch(() => rounds.value, async (latest) => {
  await initializeTts()
  if (!autoSpeakEnabled() || !ttsSupported()) return
  const newest = latest[latest.length - 1]
  if (!newest?.gm) return
  const text = newest.gm.paragraphs.join(' ')
  const sig = newest.round + ':' + text
  if (sig === lastAutoSpoken.value) return
  lastAutoSpoken.value = sig
  ttsToggle(text, `gm:${newest.round}`, { lang: ttsVoiceLang.value, gameKey: props.gameKey, role: 'gm' })
}, { deep: true })

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
            <strong :style="{ color: playerColor(a.uid) }">{{ name(a.uid) }}<button
              v-if="ttsSupported()"
              type="button"
              class="tts-button"
              :class="{ active: speakingKey === 'act:' + a.uid + a.text }"
              :title="speakingKey === 'act:' + a.uid + a.text ? t('ttsStop') : t('ttsSpeak')"
              @click="ttsToggle(a.text, 'act:' + a.uid + a.text, { lang: ttsVoiceLang, gameKey, role: 'player' })"
            >{{ speakingKey === 'act:' + a.uid + a.text ? '⏸' : '🔊' }}</button></strong>
            <p>{{ a.text }}</p>
            <span v-if="a.dice" class="dice-tag">🎲 {{ a.dice.system }}={{ a.dice.value }}</span>
          </div>
        </div>
        <CheckRevealCard v-for="check in checks(item.entry)" :key="check.check_id || `${check.actor_uid}-${check.roll}`" :check="check" />
        <div v-if="item.gm" class="message gm message-with-avatar">
          <span class="narrator-avatar" aria-hidden="true">GM</span>
          <div class="message-copy">
          <strong>{{ t('gmRound', { round: item.round }) }}<button
            v-if="ttsSupported()"
            type="button"
            class="tts-button"
            :class="{ active: speakingKey === 'gm:' + item.round }"
            :title="speakingKey === 'gm:' + item.round ? t('ttsStop') : t('ttsSpeakGm')"
            @click="ttsToggle(item.gm.paragraphs.join(' '), 'gm:' + item.round, { lang: ttsVoiceLang, gameKey, role: 'gm' })"
          >{{ speakingKey === 'gm:' + item.round ? '⏸' : '🔊' }}</button></strong>
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
        <div v-for="recap in recaps(item.entry)" :key="recap.id || recap.text" class="message story-recap message-with-avatar" data-testid="story-recap-card">
          <span class="recap-avatar" aria-hidden="true"><NIcon :component="ReaderOutline" size="19" /></span>
          <div class="message-copy">
            <strong>{{ t('storyRecapTitle') }} <small>{{ recapRange(recap) }}</small></strong>
            <p class="story-recap-text">{{ recap.text }}</p>
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
      <CheckRevealCard
        v-for="check in activeChecks"
        :key="`pending-${check.check_id}`"
        :check="check"
        animate
        :can-decide-luck="check.luck_decision === 'pending' && canDecideLuck(check)"
        :busy="!!luckBusyId"
        @luck="emitLuck"
      />
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
