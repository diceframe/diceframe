<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { api } from '@/api/client'
import type { ActionSubmitResponse, GameDetail } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { SYSTEM_DICE_MARKER_PREFIX } from '@/utils/play'
import DiceButton from './play/DiceButton.vue'

const props = defineProps<{ gameKey: string; userId: string; detail: GameDetail; disabled?: boolean }>()
const emit = defineEmits<{ refresh: []; processing: [value: boolean] }>()
const { t } = useLocale()

const text = ref(''), busy = ref(false), pending = ref(''), notice = ref('')
const editingInstead = ref(false)
const dicePhase = ref<'idle' | 'rolling' | 'result'>('idle')
const diceValue = ref<number | undefined>(undefined)
const diceCrit = ref(false), diceFumble = ref(false)
let diceTimer: ReturnType<typeof setTimeout> | null = null

const own = computed(() => props.detail.multiplayer?.submitted_actions?.find(a => a.user_id === props.userId))
const pendingRollText = computed(() => pending.value || (!editingInstead.value && own.value?.dice_pending ? stripRollMarker(own.value.text) : ''))
const hint = computed(() => props.detail.solo_mode ? t('soloHint') : own.value ? t('submittedHint', { count: own.value.revision_count || 1 }) : t('defaultHint'))
const defaultQuickActions = computed(() => [t('quickObserve'), t('quickExplore'), t('quickTalk'), t('quickPrepareCombat')])
const quickActions = computed(() => (props.detail.quick_actions?.length ? props.detail.quick_actions : defaultQuickActions.value) as string[])
const locked = computed(() => props.disabled || busy.value || dicePhase.value !== 'idle')
const diceNotice = computed(() => notice.value || t('diceNeeded'))

function clearDiceTimer() { if (diceTimer) { clearTimeout(diceTimer); diceTimer = null } }
function stripRollMarker(value: string) {
  return String(value || '').split('\n').filter(line => !line.startsWith(SYSTEM_DICE_MARKER_PREFIX)).join('\n').trim()
}

async function submit(confirm = false) {
  const action = (confirm ? pendingRollText.value : text.value).trim()
  if (!action || locked.value) return
  busy.value = true; notice.value = ''; emit('processing', true)
  if (confirm) { dicePhase.value = 'rolling'; diceValue.value = undefined; diceCrit.value = false; diceFumble.value = false }
  try {
    const r = await api<ActionSubmitResponse>(`/games/${encodeURIComponent(props.gameKey)}/action`, { method: 'POST', body: JSON.stringify({ text: action, confirm, server_roll: confirm }) })
    if (r.phase === 'dice') { pending.value = action; editingInstead.value = false; notice.value = r.message || t('diceNeeded'); dicePhase.value = 'idle'; emit('refresh'); return }
    if (confirm && r.roll?.ok) {
      dicePhase.value = 'result'; diceValue.value = r.roll.value; diceCrit.value = !!r.roll.critical; diceFumble.value = !!r.roll.fumble
      text.value = ''; pending.value = ''; editingInstead.value = false
      clearDiceTimer()
      diceTimer = setTimeout(() => { dicePhase.value = 'idle'; notice.value = t('actionRecorded'); emit('refresh') }, 1800)
      return
    }
    text.value = ''; pending.value = ''; editingInstead.value = false; notice.value = t('actionRecorded'); emit('refresh')
  } catch (e: unknown) { notice.value = e instanceof Error ? e.message : String(e); dicePhase.value = 'idle' } finally { busy.value = false; emit('processing', false) }
}

onUnmounted(clearDiceTimer)
</script>

<template>
  <div class="composer">
    <div class="composer-head">
      <div>
        <strong>{{ t('composerTitle') }}</strong>
        <span>{{ hint }}</span>
      </div>
      <DiceButton :phase="dicePhase" :value="diceValue" :crit="diceCrit" :fumble="diceFumble" />
    </div>
    <div class="quick-actions" :aria-label="t('quickActions')">
      <button v-for="action in quickActions" :key="action" :disabled="locked" @click="text = action">{{ action }}</button>
    </div>
    <div v-if="pendingRollText" class="dice-prompt">
      <span>{{ diceNotice }}</span>
      <button class="primary" @click="submit(true)" :disabled="locked">{{ t('rollDice') }}</button>
      <button @click="pending = ''; notice = ''; editingInstead = true">{{ t('changeAction') }}</button>
    </div>
    <div v-else class="composer-row">
      <textarea v-model="text" :disabled="locked" :placeholder="t('actionPlaceholder')" @keydown.ctrl.enter.prevent="submit()" />
      <button class="primary" @click="submit()" :disabled="locked || !text.trim()">{{ busy ? t('processing') : t('action') }}</button>
    </div>
    <div v-if="notice && !pending && dicePhase === 'idle'" class="notice">{{ notice }}</div>
  </div>
</template>
