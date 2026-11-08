<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CheckResult } from '@/api/types'
import { api, errorMessage } from '@/api/client'
import { useLocale } from '@/composables/useLocale'
import { pushCopy } from './copy'
import { gamePreferencesCopy } from '@/features/game-preferences/copy'
const props = defineProps<{ gameKey?: string; check: CheckResult; actorId?: string; isGm?: boolean; readonly?: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const { locale } = useLocale()
const c = computed(() => pushCopy(locale.value))
const push = computed(() => props.check.push)
const supported = computed(() => push.value?.schema_version === 1)
const owner = computed(() => !!props.actorId && props.actorId === props.check.actor_uid)
const actionable = computed(() => supported.value && !props.readonly && !!props.gameKey)
const ownerOfferedConflict = computed(() => owner.value && !!props.isGm && push.value?.status === 'offered')
const draft = ref(''), busy = ref(false), error = ref('')
const adjustingRisk = ref(false)
let epoch = 0
let lastValidRunId = String(props.check.push?.run_id || '')
watch([() => props.gameKey, () => props.check.check_id, () => props.check.push?.run_id, () => props.actorId, () => props.isGm, () => props.readonly], (values, previous) => {
  const sameIdentity = previous?.[1] === values[1] && previous?.[0] === values[0] && previous?.[3] === values[3] && previous?.[4] === values[4] && previous?.[5] === values[5]
  if (sameIdentity) {
    const runChanged = !!values[2] && !!lastValidRunId && values[2] !== lastValidRunId
    if (values[2]) lastValidRunId = values[2]
    if (runChanged) { epoch++; draft.value = ''; error.value = ''; busy.value = false; adjustingRisk.value = false; return }
    // A stale log refresh can omit push briefly. Cancel an in-flight response
    // while retaining the user's draft for the next projection.
    if (previous?.[2] && !values[2]) { epoch++; busy.value = false; error.value = '' }
    return
  }
  lastValidRunId = values[2] || ''
  epoch++; draft.value = ''; error.value = ''; busy.value = false; adjustingRisk.value = false
})
async function send(operation: string) {
  if (!actionable.value || busy.value || !push.value) return
  const captured = ++epoch
  const body = { run_id: push.value.run_id, round_number: push.value.round, expected_revision: push.value.revision,
    ...(['request', 'reject'].includes(operation) ? { reason: draft.value } : operation === 'offer' ? { consequence: draft.value } : {}) }
  busy.value = true; error.value = ''
  try {
    const result = await api<{ ok?: boolean; error?: string }>(`/games/${encodeURIComponent(props.gameKey || '')}/checks/${encodeURIComponent(props.check.check_id || '')}/push/${operation}`, { method: 'POST', body: JSON.stringify(body) })
    if (captured !== epoch) return
    if (!result.ok) throw new Error(result.error || c.value.error)
    draft.value = ''; if (operation === 'offer') adjustingRisk.value = false
    emit('refresh')
  } catch (err) {
    if (captured === epoch) error.value = errorMessage(err)
  } finally { if (captured === epoch) busy.value = false }
}
</script>
<template>
  <section v-if="push" class="push-decision" data-testid="push-decision" :data-check-id="check.check_id">
    <strong>{{ c.title }}</strong>
    <template v-if="supported">
      <p v-if="owner && isGm && push.status === 'pending'">{{ c.ownerPending }}</p>
      <p v-else-if="owner && isGm && push.status === 'requested'">{{ c.ownerRequested }}</p>
      <p v-else-if="owner && isGm && push.status === 'offered'">{{ c.ownerOffered }}</p>
      <p v-else>{{ c[push.status] }}</p>
      <p v-if="push.reason">{{ c.reason }}: {{ push.reason }}</p>
      <p v-if="push.consequence"><strong>{{ c.risk }}:</strong> {{ push.consequence }}</p>
      <p v-if="push.decision_reason">{{ push.decision_reason === 'push_disabled_by_gm' ? gamePreferencesCopy(locale).pushDisabled : push.decision_reason }}</p>
      <p v-if="push.original">{{ push.original.roll }} → {{ check.roll }}</p>
      <p v-if="push.failed">{{ c.failed }}</p>
      <template v-if="actionable">
        <p v-if="push.status === 'pending' && owner">{{ c.hint }}</p>
        <button v-if="ownerOfferedConflict && !adjustingRisk" data-testid="push-adjust-risk" type="button" @click="adjustingRisk = true">{{ c.adjustRisk }}</button>
        <label v-if="(push.status === 'pending' && owner) || (['requested', 'offered'].includes(push.status) && isGm && (!ownerOfferedConflict || adjustingRisk))">
          {{ push.status === 'pending' ? c.reason : c.risk }}
          <textarea v-model="draft" maxlength="1000" :disabled="busy" rows="2" />
        </label>
        <div class="push-actions">
          <button v-if="push.status === 'pending' && owner" data-testid="push-request" :disabled="busy || !draft.trim()" @click="send('request')">{{ c.request }}</button>
          <template v-if="['requested', 'offered'].includes(push.status) && isGm && (!ownerOfferedConflict || adjustingRisk)">
            <button data-testid="push-offer" :disabled="busy || !draft.trim()" @click="send('offer')">{{ c.offer }}</button>
            <button :disabled="busy || !draft.trim()" @click="send('reject')">{{ c.reject }}</button>
          </template>
          <button v-if="push.status === 'offered' && owner && (!ownerOfferedConflict || !adjustingRisk)" data-testid="push-confirm" :disabled="busy" @click="send('confirm')">{{ c.confirm }}</button>
          <button v-if="['pending', 'requested', 'offered'].includes(push.status) && owner" data-testid="push-decline" :disabled="busy" @click="send('decline')">{{ c.decline }}</button>
        </div>
      </template>
    </template>
    <p v-else>{{ c.stale }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.push-decision { margin-top: 12px; padding: 12px; border: 1px solid var(--border-color, #777); border-radius: 10px; overflow-wrap: anywhere; }
.push-decision p { margin: 6px 0; }
.push-decision label { display: grid; gap: 6px; }
.push-decision textarea { box-sizing: border-box; width: 100%; min-width: 0; color: inherit; background: var(--bg-card, transparent); border-radius: 6px; padding: 8px; }
.push-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.push-actions button { border: 1px solid currentColor; border-radius: 6px; padding: 6px 10px; color: inherit; background: transparent; cursor: pointer; }
.push-actions button:disabled { opacity: .5; cursor: default; }
[role=alert] { color: var(--error-color, #d66); }
</style>
