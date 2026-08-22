<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { ActionSubmitResponse, GameDetail } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { appendDictated, asrLanguageFor, initializeAsr, useVoiceInput, voiceInputSupported } from '@/utils/asr'

const props = defineProps<{ gameKey: string; userId: string; detail: GameDetail; disabled?: boolean }>()
const emit = defineEmits<{ refresh: []; processing: [value: boolean] }>()
const { t, locale } = useLocale()

const text = ref(''), busy = ref(false), notice = ref('')

const own = computed(() => props.detail.multiplayer?.submitted_actions?.find(a => a.user_id === props.userId))
const hint = computed(() => props.detail.solo_mode ? t('soloHint') : own.value ? t('submittedHint', { count: own.value.revision_count || 1 }) : t('defaultHint'))
const defaultQuickActions = computed(() => [t('quickObserve'), t('quickExplore'), t('quickTalk'), t('quickPrepareCombat')])
const quickActions = computed(() => (props.detail.quick_actions?.length ? props.detail.quick_actions : defaultQuickActions.value) as string[])
const locked = computed(() => props.disabled || busy.value)

const micAvailable = computed(() => voiceInputSupported())
const {
  recording, transcribing, errorCode, serverMessage, elapsedSeconds,
  toggle: toggleVoice, release: releaseVoice,
} = useVoiceInput({
  gameKey: props.gameKey,
  lang: () => asrLanguageFor(locale.value),
  onText: chunk => { text.value = appendDictated(text.value, chunk) },
})

const dictationError = computed(() => {
  if (errorCode.value === 'asr-mic-denied') return t('asrMicDenied')
  if (errorCode.value === 'asr-record-failed') return t('asrRecordFailed')
  if (errorCode.value === 'asr-failed') return t('asrFailed')
  return serverMessage.value
})

onMounted(() => { void initializeAsr() })

watch(locked, value => { if (value) releaseVoice() })

function resetSubmissionState() {
  notice.value = ''
}

const ownSignature = computed(() => own.value
  ? JSON.stringify([own.value.text, own.value.revision_count])
  : '')

watch(
  [() => props.detail.round_number, ownSignature],
  ([roundNumber, signature], [previousRoundNumber, previousSignature]) => {
    if (roundNumber !== previousRoundNumber || (previousSignature && !signature)) {
      resetSubmissionState()
    }
  },
)

async function submit() {
  const action = text.value.trim()
  if (!action || locked.value) return
  releaseVoice()
  busy.value = true; notice.value = ''; emit('processing', true)
  try {
    const r = await api<ActionSubmitResponse>(`/games/${encodeURIComponent(props.gameKey)}/action`, { method: 'POST', body: JSON.stringify({ text: action }) })
    text.value = ''
    notice.value = r.phase === 'luck' ? t('luckDecisionRequired') : t('actionRecorded')
    emit('refresh')
  } catch (e: unknown) { notice.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false; emit('processing', false) }
}
</script>

<template>
  <div class="composer">
    <div class="composer-head">
      <div class="composer-title-row">
        <strong>{{ t('composerTitle') }}</strong>
        <span v-if="hint" class="composer-hint">{{ hint }}</span>
      </div>
    </div>
    <div class="quick-actions" :aria-label="t('quickActions')">
      <button v-for="action in quickActions" :key="action" :disabled="locked" @click="text = action">{{ action }}</button>
    </div>
    <div v-if="dictationError" class="dictation-status error">{{ dictationError }}</div>
    <div v-else-if="recording" class="dictation-status">{{ t('asrRecording', { seconds: elapsedSeconds }) }}</div>
    <div v-else-if="transcribing" class="dictation-status">{{ t('asrTranscribing') }}</div>
    <div class="composer-row" :class="{ 'has-dictation': micAvailable }">
      <textarea v-model="text" :disabled="locked" :placeholder="t('actionPlaceholder')" @keydown.ctrl.enter.prevent="submit()" />
      <button
        v-if="micAvailable"
        type="button"
        class="dictation-toggle"
        :class="{ active: recording }"
        :title="recording ? t('asrStop') : t('asrVoice')"
        :aria-label="recording ? t('asrStop') : t('asrVoice')"
        :aria-pressed="recording"
        :disabled="transcribing || (locked && !recording)"
        @click="toggleVoice()"
      >{{ recording ? '⏹' : '🎤' }}</button>
      <button class="primary" @click="submit()" :disabled="locked || !text.trim()">{{ busy ? t('processing') : t('action') }}</button>
    </div>
    <div v-if="notice" class="notice">{{ notice }}</div>
  </div>
</template>

<style scoped>
.composer-row.has-dictation {
  grid-template-columns: minmax(0, 1fr) 44px 92px;
}

.dictation-toggle {
  min-width: 44px;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--df-interactive) 38%, var(--df-border-soft));
  border-radius: var(--df-radius-md);
  color: var(--df-text-secondary);
  background: color-mix(in srgb, var(--df-interactive) 10%, var(--df-control-bg));
  font-size: 16px;
}

.dictation-toggle.active {
  border-color: color-mix(in srgb, var(--df-danger) 55%, var(--df-border-soft));
  color: var(--df-danger-strong);
  background: color-mix(in srgb, var(--df-danger) 12%, transparent);
}

.dictation-toggle:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.dictation-status {
  margin-bottom: 7px;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, var(--df-interactive) 32%, var(--df-border-soft));
  border-radius: 6px;
  color: var(--df-text);
  background: color-mix(in srgb, var(--df-interactive) 8%, transparent);
  font-size: 12px;
  line-height: 1.5;
}

.dictation-status.error {
  border-color: color-mix(in srgb, var(--df-danger) 38%, var(--df-border-soft));
  color: var(--df-danger-strong);
  background: color-mix(in srgb, var(--df-danger) 8%, transparent);
}

@media (max-width: 800px) {
  .composer-row.has-dictation {
    grid-template-columns: 44px minmax(0, 1fr);
  }

  .composer-row textarea {
    grid-column: 1 / -1;
  }
}
</style>
