<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { CheckResult } from '@/api/types'
import { useLocale } from '@/composables/useLocale'

const props = withDefaults(defineProps<{
  check: CheckResult
  animate?: boolean
  canDecideLuck?: boolean
  busy?: boolean
}>(), { animate: false, canDecideLuck: false, busy: false })
const emit = defineEmits<{ luck: [check: CheckResult, spend: boolean] }>()
const { t } = useLocale()
const revealed = ref(!props.animate)
let revealTimer: number | undefined

const status = computed<'critical' | 'fumble' | 'success' | 'failure'>(() => {
  if (props.check.is_critical) return 'critical'
  if (props.check.is_fumble) return 'fumble'
  const verdict = String(props.check.verdict || '').toLowerCase()
  return verdict.includes('成功') || verdict.includes('success') ? 'success' : 'failure'
})
const statusLabel = computed(() => ({
  critical: t('criticalSuccess'),
  fumble: t('criticalFailure'),
  success: t('checkSuccess'),
  failure: t('checkFailure'),
})[status.value])
const math = computed(() => {
  const check = props.check
  if (typeof check.threshold === 'number') return `${check.dice || 'd100'}=${check.roll} / ${check.threshold}%`
  const modifier = Number(check.modifier || 0)
  const modifierText = modifier ? ` ${modifier > 0 ? '+' : '-'} ${Math.abs(modifier)}` : ''
  const total = typeof check.total === 'number' ? ` = ${check.total}` : ''
  if (typeof check.opponent_total === 'number') {
    const opponentModifier = Number(check.opponent_modifier || 0)
    return `${check.dice || 'd20'}=${check.roll}${modifierText}${total} / ${check.opponent_name || t('checkOpponent')} d20=${check.opponent_roll} ${opponentModifier >= 0 ? '+' : '-'} ${Math.abs(opponentModifier)} = ${check.opponent_total}`
  }
  const dc = typeof check.dc === 'number' ? ` / DC ${check.dc}` : ''
  return `${check.dice || 'd20'}=${check.roll}${modifierText}${total}${dc}`
})
const diceFaces = computed(() => {
  const rolls = props.check.rolls?.length ? props.check.rolls : [props.check.roll]
  return rolls.filter((value): value is number => typeof value === 'number').join(', ')
})
// 服务端保留的渠道来源：DC / 掷骰方式 / 环境修正各自为什么被采用。
const plannerSources = computed(() => {
  const check = props.check
  return [
    check.dc_reason ? t('checkDcReason', { detail: check.dc_reason }) : '',
    check.advantage_reason ? t('checkAdvantageReason', { detail: check.advantage_reason }) : '',
    check.modifier_reason ? t('checkModifierReason', { detail: check.modifier_reason }) : '',
  ].filter(Boolean).join('；')
})

onMounted(() => {
  if (!props.animate) return
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  revealTimer = window.setTimeout(() => { revealed.value = true }, reduced ? 0 : 720)
})
onUnmounted(() => {
  if (revealTimer !== undefined) window.clearTimeout(revealTimer)
})
</script>

<template>
  <article
    class="message check-reveal-card"
    :class="[status, { rolling: !revealed }]"
    :aria-label="t('checkCardLabel', { name: check.actor_name || check.actor_uid || '' })"
  >
    <div class="check-reveal-head">
      <span class="check-die" aria-hidden="true">{{ revealed ? check.roll : '✦' }}</span>
      <div>
        <strong>{{ check.label || t('checkLabel') }} · {{ check.actor_name }}</strong>
        <small>{{ check.dice || 'd20' }} · {{ check.attribute || check.skill || t('checkAttribute') }}</small>
      </div>
      <b class="check-verdict">{{ revealed ? statusLabel : t('diceRolling') }}</b>
    </div>
    <p v-if="revealed" class="check-math">{{ math }}</p>
    <details v-if="revealed">
      <summary>{{ t('checkDetails') }}</summary>
      <span>{{ t('checkDiceFaces', { rolls: diceFaces }) }}</span>
      <span>{{ t('checkCalculation', { calculation: math }) }}</span>
      <span>{{ t('checkVerdictDetail', { verdict: statusLabel }) }}</span>
      <span v-if="typeof check.hard_threshold === 'number'">
        {{ t('checkSuccessLevels', { normal: check.threshold ?? 0, hard: check.hard_threshold ?? 0, extreme: check.extreme_threshold ?? 0 }) }}
      </span>
      <span v-if="check.modifier_breakdown">{{ t('checkModifierBreakdown', { detail: check.modifier_breakdown }) }}</span>
      <span v-if="check.advantage_note">{{ t('checkAdvantageNote', { detail: check.advantage_note }) }}</span>
      <span v-if="plannerSources">{{ t('checkPlannerSources', { detail: plannerSources }) }}</span>
      <span v-if="check.assist?.length">{{ t('checkAssist', { players: check.assist.join(', ') }) }}</span>
    </details>
    <div v-if="revealed && check.luck_decision === 'pending'" class="luck-decision-actions">
      <template v-if="canDecideLuck">
        <button class="dice-tag dice-tag-button" type="button" :disabled="busy" @click="emit('luck', check, true)">{{ t('spendLuckForSuccess', { cost: check.luck_cost || 0 }) }}</button>
        <button class="dice-tag dice-tag-button luck-decline-button" type="button" :disabled="busy" @click="emit('luck', check, false)">{{ t('keepFailure') }}</button>
      </template>
      <span v-else class="dice-tag">{{ t('waitLuckDecision', { name: check.actor_name || check.actor_uid || '' }) }}</span>
    </div>
    <span v-else-if="revealed && check.luck_decision === 'spent' && check.luck_spent" class="dice-tag">{{ t('luckSpent', { cost: check.luck_spent }) }}</span>
    <span v-else-if="revealed && check.luck_decision === 'declined'" class="dice-tag">{{ t('luckDeclined') }}</span>
  </article>
</template>

<style scoped>
.check-reveal-card{position:relative;margin:7px auto;border-left:4px solid var(--df-danger);background:linear-gradient(135deg,rgba(45,37,25,.98),rgba(24,30,29,.96));overflow:hidden;transition:border-color .25s ease,box-shadow .25s ease}
:root[data-mode="light"] .check-reveal-card{background:linear-gradient(135deg,var(--df-surface-2),var(--df-surface-3))}
:root[data-mode="light"] .check-die{background:rgba(0,0,0,.06)}
.check-reveal-card.success{border-left-color:var(--df-success)}
.check-reveal-card.critical{border-left-color:var(--df-accent-strong);box-shadow:0 0 24px rgba(216,173,82,.2)}
.check-reveal-card.fumble{border-left-color:var(--df-danger);box-shadow:0 0 22px rgba(185,58,58,.18)}
.check-reveal-head{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:10px}
.check-reveal-head strong{display:block;color:var(--df-accent-strong)}
.check-reveal-head small{display:block;margin-top:2px;color:var(--df-text-muted)}
.check-die{display:grid;place-items:center;width:42px;height:42px;border:1px solid var(--df-border-soft);border-radius:10px;background:rgba(0,0,0,.2);font-size:18px;font-weight:900;color:var(--df-accent-strong)}
.rolling .check-die{animation:check-roll .72s cubic-bezier(.2,.8,.2,1) infinite}
.check-verdict{color:var(--df-text);white-space:nowrap}
.success .check-verdict{color:var(--df-success)}
.failure .check-verdict,.fumble .check-verdict{color:var(--df-danger)}
.critical .check-verdict{color:var(--df-accent-strong)}
.check-math{margin:8px 0 2px;padding-left:54px;font-variant-numeric:tabular-nums}
details{margin:5px 0 0 54px;color:var(--df-text-muted);font-size:12px}
details span{display:block;margin-top:3px}
summary{cursor:pointer}
@keyframes check-roll{0%{transform:rotate(0) scale(.9)}50%{transform:rotate(190deg) scale(1.08)}100%{transform:rotate(360deg) scale(.9)}}
@media(max-width:520px){.check-reveal-head{grid-template-columns:38px minmax(0,1fr)}.check-die{width:36px;height:36px}.check-verdict{grid-column:2}.check-math,details{padding-left:0;margin-left:0}}
@media(prefers-reduced-motion:reduce){.rolling .check-die{animation:none}}
</style>
