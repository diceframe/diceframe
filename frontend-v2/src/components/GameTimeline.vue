<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { CheckmarkCircleOutline, WarningOutline, InformationCircleOutline } from '@vicons/ionicons5'
import type { LogEntry, PublicAction, Player } from '@/api/types'
import type { DiceTag } from '@/utils/play'
import { parseAction, playerColor } from '@/utils/play'
import { api } from '@/api/client'
import { parseGMText, type LoreKeywords } from '@/utils/renderer'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{ log: LogEntry[]; live: PublicAction[]; players: Player[]; round: number; lore?: LoreKeywords; gameKey?: string; processing?: boolean; isGm?: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const { t } = useLocale()

const box = ref<HTMLElement | null>(null), hasNew = ref(false), awayFromBottom = ref(false)
const swipeError = ref("")
function name(uid: string, fallback?: string) { return fallback || props.players.find(p => p.user_id === uid)?.character_name || uid || t('characters') }

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
  if (Array.isArray(raw)) return raw.map(toAct)
  if (raw && typeof raw === 'object') return Object.entries(raw).map(([uid, text]) => { const p = parseAction(String(text)); return { uid, text: p.text, dice: p.dice } })
  return []
}
function liveAct(a: PublicAction): Act { return toAct(a) }

const rounds = computed(() => props.log.map((entry, index) => {
  const sw = entry.swipes || []
  const cur = Number(entry.current_swipe) || 0
  return { entry, round: Number(entry.round || index), gm: entry.gm_response ? parseGMText(String(entry.gm_response), props.lore) : null, swipes: sw, swipeCur: cur, swipeCount: sw.length }
}))

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
watch(() => [props.log.length, JSON.stringify(props.live), props.processing], async () => {
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
</script>

<template>
  <div class="timeline-wrap">
    <div ref="box" class="timeline" data-testid="timeline" @scroll.passive="onScroll">
      <template v-for="item in rounds" :key="item.round">
        <div class="round-divider" v-if="item.round">{{ t('roundDivider', { round: item.round }) }}</div>
        <div v-for="a in actions(item.entry)" :key="a.uid + a.text" class="message player" :style="{ borderLeftColor: playerColor(a.uid) }">
          <strong :style="{ color: playerColor(a.uid) }">{{ name(a.uid) }}</strong>
          <p>{{ a.text }}</p>
          <span v-if="a.dice" class="dice-tag">🎲 {{ a.dice.system }}={{ a.dice.value }}</span>
        </div>
        <div v-if="item.gm" class="message gm">
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
      </template>
      <div v-for="a in live" :key="a.user_id" class="message player live" :style="{ borderLeftColor: playerColor(a.user_id) }">
        <strong :style="{ color: playerColor(a.user_id) }">{{ name(a.user_id, a.character_name) }} · {{ t('published') }} · {{ a.revision_count || 1 }}/3</strong>
        <p>{{ liveAct(a).text }}</p>
        <span v-if="liveAct(a).dice" class="dice-tag">🎲 {{ liveAct(a).dice?.system }}={{ liveAct(a).dice?.value }}</span>
      </div>
      <div v-if="processing" class="message gm thinking-message" aria-live="polite">
        <strong>{{ t('thinkingMessage') }}<span class="thinking-dots"><i></i><i></i><i></i></span></strong>
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
