<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { ActionSubmitResponse, CheckRequest, GameDetail } from '@/api/types'
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
const diceSystem = ref('d20')
const diceCrit = ref(false), diceFumble = ref(false)
const pendingCheck = ref<CheckRequest | null>(null)
let diceTimer: ReturnType<typeof setTimeout> | null = null

const own = computed(() => props.detail.multiplayer?.submitted_actions?.find(a => a.user_id === props.userId))
const pendingRollText = computed(() => pending.value || (!editingInstead.value && own.value?.dice_pending ? stripRollMarker(own.value.text) : ''))
const hint = computed(() => props.detail.solo_mode ? t('soloHint') : own.value ? t('submittedHint', { count: own.value.revision_count || 1 }) : t('defaultHint'))
const defaultQuickActions = computed(() => [t('quickObserve'), t('quickExplore'), t('quickTalk'), t('quickPrepareCombat')])
const quickActions = computed(() => (props.detail.quick_actions?.length ? props.detail.quick_actions : defaultQuickActions.value) as string[])
const locked = computed(() => props.disabled || busy.value || dicePhase.value !== 'idle')
const activeCheck = computed(() => pendingCheck.value || own.value?.check_request || null)
const diceNotice = computed(() => notice.value || activeCheck.value?.label || t('diceNeeded'))

function clearDiceTimer() { if (diceTimer) { clearTimeout(diceTimer); diceTimer = null } }
function stripRollMarker(value: string) {
  return String(value || '').split('\n').filter(line => !line.startsWith(SYSTEM_DICE_MARKER_PREFIX)).join('\n').trim()
}
function resetSubmissionState() {
  clearDiceTimer()
  pending.value = ''
  notice.value = ''
  editingInstead.value = false
  dicePhase.value = 'idle'
  diceValue.value = undefined
  diceSystem.value = 'd20'
  diceCrit.value = false
  diceFumble.value = false
  pendingCheck.value = null
}

const ownSignature = computed(() => own.value
  ? JSON.stringify([own.value.text, own.value.revision_count, own.value.dice_pending, own.value.dice_roll_source, own.value.check_request])
  : '')

watch(
  [() => props.detail.round_number, ownSignature],
  ([roundNumber, signature], [previousRoundNumber, previousSignature]) => {
    if (roundNumber !== previousRoundNumber || (previousSignature && !signature)) {
      resetSubmissionState()
    }
  },
)

async function submit(confirm = false) {
  const action = (confirm ? pendingRollText.value : text.value).trim()
  if (!action || locked.value) return
  busy.value = true; notice.value = ''; emit('processing', true)
  if (confirm) { dicePhase.value = 'rolling'; diceValue.value = undefined; diceCrit.value = false; diceFumble.value = false }
  try {
    const r = await api<ActionSubmitResponse>(`/games/${encodeURIComponent(props.gameKey)}/action`, { method: 'POST', body: JSON.stringify({ text: action, confirm, server_roll: confirm }) })
    if (r.phase === 'dice') {
      pending.value = action
      pendingCheck.value = r.check_request || null
      diceSystem.value = r.check_request?.dice_system || 'd20'
      editingInstead.value = false
      notice.value = r.message || t('diceNeeded')
      dicePhase.value = 'idle'
      emit('refresh')
      return
    }
    if (confirm && r.roll?.ok) {
      dicePhase.value = 'result'; diceValue.value = r.roll.value; diceSystem.value = r.roll.dice_system || activeCheck.value?.dice_system || 'd20'; diceCrit.value = !!r.roll.critical; diceFumble.value = !!r.roll.fumble
      text.value = ''; pending.value = ''; editingInstead.value = false
      if (r.phase === 'luck') {
        notice.value = t('luckDecisionRequired')
        emit('refresh')
      }
      clearDiceTimer()
      diceTimer = setTimeout(() => {
        dicePhase.value = 'idle'
        if (r.phase !== 'luck') notice.value = t('actionRecorded')
        emit('refresh')
      }, 1800)
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
      <DiceButton :phase="dicePhase" :value="diceValue" :dice="diceSystem" :crit="diceCrit" :fumble="diceFumble" />
    </div>
    <div class="quick-actions" :aria-label="t('quickActions')">
      <button v-for="action in quickActions" :key="action" :disabled="locked" @click="text = action">{{ action }}</button>
    </div>
    <div v-if="pendingRollText" class="dice-prompt">
      <span><strong>{{ activeCheck?.label || diceNotice }}</strong> · {{ activeCheck?.dice_system || diceSystem }}</span>
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
