<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { CheckResult, CheckRevealRecord } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import CocDiceFaces from '@/features/play/dice/CocDiceFaces.vue'
import { cocCopy } from '@/features/play/dice/cocCopy'
import PushDecisionPanel from '@/features/play/check-decisions/PushDecisionPanel.vue'
import DiceTray from '@/features/play/dice/DiceTray.vue'
import { dicePresentationCopy } from '@/features/play/dice/copy'
import { reportAssistantReaction } from '@/composables/useAssistantReactions'

const props = withDefaults(defineProps<{
  check: CheckResult
  gameKey?: string
  actorId?: string
  isGm?: boolean
  readonly?: boolean
  animate?: boolean
  canDecideLuck?: boolean
  busy?: boolean
  clickMode?: boolean
  reveal?: CheckRevealRecord | null
  canReveal?: boolean
  revealerName?: string
}>(), { animate: false, canDecideLuck: false, busy: false, clickMode: false, reveal: null, canReveal: false, revealerName: '' })
const emit = defineEmits<{ luck: [check: CheckResult, spend: boolean]; reveal: [check: CheckResult]; refresh: [] }>()
const { t, locale } = useLocale()
// 「两枚骰子相加」的骰制由结果里的 dice_pair 标识；本组件不认具体规则名。
const isDicePair = computed(() => Array.isArray(props.check.dice_pair) && props.check.dice_pair.length === 2)
// 点击揭示模式下：服务端结果已权威落盘，这里只做共享揭示的表现遮罩。
// 不遮蔽数据本身（时间线公开结果），未揭示也不阻塞任何流程。
// 点击模式只有服务端的共享标记能翻开；未点击不会计时，也不阻塞规则推进。
const pendingClickReveal = computed(() => props.clickMode && !props.reveal)
const revealed = ref(!props.animate && !pendingClickReveal.value)
let revealTimer: number | undefined
let trayTimer: number | undefined
const trayExpanded = ref(false)
const recentReveal = ref(false)
const pendingDecision = computed(() => props.check.luck_decision === 'pending'
  || Boolean(props.check.push && ['pending', 'requested', 'offered'].includes(props.check.push.status)))
const showTray = computed(() => !revealed.value || pendingDecision.value || recentReveal.value || trayExpanded.value)
const trayCopy = computed(() => dicePresentationCopy(locale.value))
watch(revealed, (value, previous) => {
  if (trayTimer !== undefined) window.clearTimeout(trayTimer)
  recentReveal.value = value && !previous
  // Only live reveals reach the DF Agent avatar; history renders revealed.
  if (recentReveal.value) reportAssistantReaction(status.value)
  // Keep the tray expanded after a live reveal.  The old timeout made the
  // result disappear again a few seconds after the animation, which is easy
  // to miss during play.  `trayExpanded` remains a user-controlled opt-out:
  // clicking the tray toggle can still collapse it deliberately.
  if (recentReveal.value) {
    trayExpanded.value = true
    trayTimer = window.setTimeout(() => { recentReveal.value = false; trayTimer = undefined }, 5000)
  }
})
function toggleTray() {
  const open = !showTray.value
  recentReveal.value = false
  trayExpanded.value = open
}

function stopRevealAnimation() {
  if (revealTimer !== undefined) window.clearTimeout(revealTimer)
  revealTimer = undefined
}

function startRevealTimer() {
  stopRevealAnimation()
  revealed.value = false
  reportAssistantReaction('diceRoll')
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  revealTimer = window.setTimeout(() => { revealed.value = true; revealTimer = undefined }, reduced ? 0 : 720)
}

watch(pendingClickReveal, (pending) => {
  if (pending) { stopRevealAnimation(); revealed.value = false; return }
  if (props.animate) startRevealTimer()
  else revealed.value = true
})

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
  if (isDicePair.value) {
    // 两枚属性骰相加的骰制：DL 而不是 DC，并单独展示伤害用的高值。
    const rolls = (check.rolls || []).filter((value): value is number => typeof value === 'number')
    const pair = rolls.length ? rolls.join('+') : String(check.roll ?? '')
    const diceLabel = `d${check.dice_a ?? '?'}+d${check.dice_b ?? '?'}`
    const modifier = Number(check.modifier || 0)
    const modifierText = modifier ? ` ${modifier > 0 ? '+' : '-'} ${Math.abs(modifier)}` : ''
    const total = typeof check.total === 'number' ? ` = ${check.total}` : ''
    const dc = typeof check.dc === 'number' ? ` / DL ${check.dc}` : ''
    const high = typeof check.high_roll === 'number' ? `（高值 ${check.high_roll}）` : ''
    return `${diceLabel} = ${pair}${modifierText}${total}${dc}${high}`
  }
  if (typeof check.threshold === 'number') {
    const own = `${check.dice || 'd100'}=${check.roll} / ${check.threshold}`
    const opposed = check.opposed
    if (!opposed || typeof opposed.opponent_roll !== 'number') return own
    return `${own} · ${check.opponent_name || t('checkOpponent')} ${opposed.opponent_skill || ''} d100=${opposed.opponent_roll} / ${opposed.opponent_threshold ?? ''}`
  }
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
// 双骰（优势/劣势、奖励骰/惩罚骰）时逐枚展示，并标出权威结算采用的那一枚；
// 单骰或缺失 rolls 时保持纯文本，不改变既有展示。
const diceChips = computed<{ value: number; taken: boolean }[]>(() => {
  const rolls = (props.check.rolls?.length ? props.check.rolls : [props.check.roll])
    .filter((value): value is number => typeof value === 'number')
  if (rolls.length < 2) return []
  // 两枚骰子都计入总和时，没有“取其中一枚”的语义。
  if (isDicePair.value) return rolls.map((value) => ({ value, taken: false }))
  // 找不到权威 roll 时不标记任何一枚，避免把异常数据展示成确定的裁定。
  const takenIndex = rolls.findIndex((value) => value === props.check.roll)
  return rolls.map((value, index) => ({ value, taken: index === takenIndex }))
})
const modeBadge = computed(() => {
  const mode = String(props.check.advantage_mode || '')
  if (props.check.dice_mechanic === 'coc_bonus_penalty') {
    if (mode === 'advantage') return cocCopy[locale.value].bonus
    if (mode === 'disadvantage') return cocCopy[locale.value].penalty
  }
  if (mode === 'advantage') return t('checkModeAdvantage')
  if (mode === 'disadvantage') return t('checkModeDisadvantage')
  return ''
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
  if (pendingClickReveal.value) return
  if (!props.animate) return
  startRevealTimer()
})
onUnmounted(() => { stopRevealAnimation(); if (trayTimer !== undefined) window.clearTimeout(trayTimer) })
</script>

<template>
  <article
    class="message check-reveal-card"
    :class="[revealed ? status : 'pending', { rolling: !revealed && !pendingClickReveal }]"
    :aria-label="t('checkCardLabel', { name: check.actor_name || check.actor_uid || '' })"
  >
    <div class="check-reveal-head">
      <span class="check-die" aria-hidden="true">{{ revealed ? check.roll : '✦' }}</span>
      <div>
        <strong>{{ check.label || t('checkLabel') }} · {{ check.actor_name }}</strong>
        <small>{{ check.dice || 'd20' }} · {{ check.attribute || check.skill || t('checkAttribute') }}</small>
        <small v-if="modeBadge" class="check-mode-badge" :class="check.advantage_mode">{{ modeBadge }}</small>
      </div>
      <b class="check-verdict">{{ revealed ? statusLabel : (pendingClickReveal ? t('checkRevealPending') : t('diceRolling')) }}</b>
    </div>
    <button v-if="revealed && !pendingDecision" class="tray-toggle" data-testid="dice-tray-toggle" type="button" :aria-expanded="showTray" @click="toggleTray">{{ showTray ? trayCopy.hide : trayCopy.show }}</button>
    <div v-show="showTray" class="tray-presentation"><DiceTray :check="check" :revealed="revealed" :rolling="!revealed && !pendingClickReveal" :animate-reveal="recentReveal" /></div>
    <p v-if="revealed" class="check-math">{{ math }}</p>
    <div v-if="revealed && (check.opportunity?.length || check.fumble_opportunity?.length)" class="check-opportunity">
      <strong>{{ check.opportunity?.length ? t('checkOpportunityTitle') : t('checkFumbleOpportunityTitle') }}</strong>
      <ul>
        <li v-for="(item, index) in (check.opportunity?.length ? check.opportunity : check.fumble_opportunity)" :key="index">{{ item }}</li>
      </ul>
      <p class="check-opportunity-hint">{{ t('checkOpportunityHint') }}</p>
    </div>
    <div v-if="revealed" class="check-reveal-footer">
      <details>
        <summary>{{ t('checkDetails') }}</summary>
        <CocDiceFaces v-if="check.dice_mechanic === 'coc_bonus_penalty' && check.percentile_dice" :dice="check.percentile_dice" />
        <span v-else-if="diceChips.length" class="check-dice-chips" :aria-label="t('checkDiceFaces', { rolls: diceFaces })">
          <span
            v-for="(chip, index) in diceChips"
            :key="index"
            class="check-die-chip"
            :class="{ taken: chip.taken }"
          >{{ chip.value }}<small v-if="chip.taken">{{ t('checkDiceTaken') }}</small></span>
        </span>
        <span v-else>{{ t('checkDiceFaces', { rolls: diceFaces }) }}</span>
        <span>{{ t('checkCalculation', { calculation: math }) }}</span>
        <span>{{ t('checkVerdictDetail', { verdict: statusLabel }) }}</span>
        <span v-if="typeof check.hard_threshold === 'number'">
          {{ t('checkSuccessLevels', { normal: check.threshold ?? 0, hard: check.hard_threshold ?? 0, extreme: check.extreme_threshold ?? 0 }) }}
        </span>
        <span v-if="check.difficulty === 'hard' || check.difficulty === 'extreme'">
          {{ t(check.difficulty === 'hard' ? 'checkRequiredHard' : 'checkRequiredExtreme', { max: check.required_threshold ?? 0 }) }}
        </span>
        <span v-if="check.opposed?.opponent_verdict">
          {{ t('checkOpposedDetail', { name: check.opponent_name || t('checkOpponent'), verdict: check.opposed.opponent_verdict }) }}
        </span>
        <span v-if="check.achieved_verdict">{{ t('checkAchievedVerdict', { verdict: check.achieved_verdict }) }}</span>
        <span v-if="check.modifier_breakdown">{{ t('checkModifierBreakdown', { detail: check.modifier_breakdown }) }}</span>
        <span v-if="check.advantage_note">{{ t('checkAdvantageNote', { detail: check.advantage_note }) }}</span>
        <span v-if="plannerSources">{{ t('checkPlannerSources', { detail: plannerSources }) }}</span>
        <span v-if="check.assist?.length">{{ t('checkAssist', { players: check.assist.join(', ') }) }}</span>
      </details>
      <span v-if="clickMode && reveal" class="dice-tag check-revealed-by">{{ t('checkRevealBy', { name: revealerName || reveal.by || '' }) }}</span>
    </div>
    <div v-if="pendingClickReveal" class="check-reveal-actions">
      <button v-if="canReveal" class="dice-tag dice-tag-button" type="button" :disabled="busy" @click="emit('reveal', check)">{{ t('checkRevealAction') }}</button>
      <span v-else class="dice-tag">{{ t('checkRevealWaiting') }}</span>
    </div>
    <div v-if="revealed && check.luck_decision === 'pending'" class="luck-decision-actions">
      <template v-if="canDecideLuck">
        <button class="dice-tag dice-tag-button" type="button" :disabled="busy" @click="emit('luck', check, true)">{{ t('spendLuckForSuccess', { cost: check.luck_cost || 0 }) }}</button>
        <button class="dice-tag dice-tag-button luck-decline-button" type="button" :disabled="busy" @click="emit('luck', check, false)">{{ t('keepFailure') }}</button>
      </template>
      <span v-else class="dice-tag">{{ t('waitLuckDecision', { name: check.actor_name || check.actor_uid || '' }) }}</span>
    </div>
    <span v-else-if="revealed && check.luck_decision === 'spent' && check.luck_spent" class="dice-tag">{{ t('luckSpent', { cost: check.luck_spent }) }}</span>
    <span v-else-if="revealed && check.luck_decision === 'declined'" class="dice-tag">{{ t('luckDeclined') }}</span>
    <PushDecisionPanel v-if="revealed" :check="check" :game-key="gameKey" :actor-id="actorId" :is-gm="isGm" :readonly="readonly" @refresh="emit('refresh')" />
  </article>
</template>

<style scoped>
.check-reveal-card{position:relative;margin:7px auto;border-left:4px solid var(--df-danger);background:linear-gradient(135deg,rgba(45,37,25,.98),rgba(24,30,29,.96));overflow:hidden;transition:border-color .25s ease,box-shadow .25s ease}
:root[data-mode="light"] .check-reveal-card{background:linear-gradient(135deg,var(--df-surface-2),var(--df-surface-3))}
:root[data-mode="light"] .check-die{background:rgba(0,0,0,.06)}
.check-reveal-card.success{border-left-color:var(--df-success)}
.check-reveal-card.pending{border-left-color:var(--df-border-soft)}
.tray-toggle{display:block;margin:6px 0 0 auto;padding:3px 9px;font-size:11px;color:var(--df-text-muted);background:transparent;border:1px solid var(--df-border-soft)}
.check-reveal-card.critical{border-left-color:var(--df-accent-strong);box-shadow:0 0 24px rgba(216,173,82,.2)}
.check-reveal-card.fumble{border-left-color:var(--df-danger);box-shadow:0 0 22px rgba(185,58,58,.18)}
.check-reveal-head{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:10px}
.check-reveal-head strong{display:block;color:var(--df-accent-strong)}
.check-reveal-head small{display:block;margin-top:2px;color:var(--df-text-muted)}
.check-reveal-head .check-mode-badge{display:inline-flex;width:fit-content;max-width:100%;margin-top:3px;padding:1px 7px;border:1px solid var(--df-border-soft);border-radius:8px;font-size:11px;font-weight:700}
.check-mode-badge.advantage{color:var(--df-success);border-color:var(--df-success)}
.check-mode-badge.disadvantage{color:var(--df-danger);border-color:var(--df-danger)}
.check-dice-chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.check-die-chip{display:inline-flex;align-items:baseline;gap:4px;min-width:30px;justify-content:center;padding:2px 8px;border:1px dashed var(--df-border-soft);border-radius:8px;color:var(--df-text-muted);font-variant-numeric:tabular-nums}
.check-die-chip.taken{border-style:solid;border-color:var(--df-accent-strong);color:var(--df-accent-strong);font-weight:800}
.check-die-chip small{font-size:10px;font-weight:400}
.check-die{display:grid;place-items:center;width:42px;height:42px;border:1px solid var(--df-border-soft);border-radius:10px;background:rgba(0,0,0,.2);font-size:18px;font-weight:900;color:var(--df-accent-strong)}
.rolling .check-die{animation:check-roll .72s cubic-bezier(.2,.8,.2,1) infinite}
.check-verdict{color:var(--df-text);white-space:nowrap}
.success .check-verdict{color:var(--df-success)}
.failure .check-verdict,.fumble .check-verdict{color:var(--df-danger)}
.critical .check-verdict{color:var(--df-accent-strong)}
.check-math{margin:8px 0 2px;font-variant-numeric:tabular-nums}
.check-opportunity{margin-top:8px;padding:8px 10px;border:1px solid var(--df-border-soft);border-radius:6px;font-size:12px}
.check-opportunity strong{display:block;margin-bottom:4px;color:var(--df-accent-strong)}
.check-opportunity ul{margin:0;padding-left:16px;line-height:1.6}
.check-opportunity-hint{margin:6px 0 0;color:var(--df-text-muted)}
.check-reveal-footer{display:flex;align-items:flex-start;gap:12px;margin-top:10px;padding-top:8px;border-top:1px solid var(--df-border-soft)}
details{min-width:0;flex:1;margin:0;color:var(--df-text-muted);font-size:12px;line-height:1.7;overflow-wrap:anywhere}
details span{display:block;margin-top:5px}
summary{cursor:pointer}
.check-reveal-actions{display:flex;justify-content:center;margin:8px 0 0}
.check-revealed-by{flex:0 1 auto;margin-top:0;max-width:45%;white-space:normal}
@media(max-width:520px){.check-reveal-footer{flex-wrap:wrap;gap:4px 10px}.check-reveal-footer details{flex-basis:100%}.check-revealed-by{max-width:100%}}
@keyframes check-roll{0%{transform:rotate(0) scale(.9)}50%{transform:rotate(190deg) scale(1.08)}100%{transform:rotate(360deg) scale(.9)}}
@media(max-width:520px){.check-reveal-head{grid-template-columns:38px minmax(0,1fr)}.check-die{width:36px;height:36px}.check-verdict{grid-column:2}.check-math{padding-left:0;margin-left:0}}
@media(prefers-reduced-motion:reduce){.rolling .check-die{animation:none}}
</style>
