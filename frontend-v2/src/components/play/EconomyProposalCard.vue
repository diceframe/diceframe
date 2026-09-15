<script setup lang="ts">
import { computed } from 'vue'
import { CashOutline, CheckmarkCircleOutline, TimeOutline, WarningOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import type { PendingPayment } from '@/api/types'
import type { CurrencySystem } from '@/utils/currency'
import { formatCurrencyAmount } from '@/utils/currency'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{
  proposal: PendingPayment
  currency: string
  currencySystem?: CurrencySystem | null
  playerName: (uid?: string) => string
  dismissLabel: string
  help: string
  solo?: boolean
  busy?: boolean
}>()
const emit = defineEmits<{ confirm: []; reject: []; dismiss: [] }>()
const { t } = useLocale()

const reward = computed(() => props.proposal.kind === 'reward')
const tone = computed(() => reward.value ? 'reward' : 'payment')
const title = computed(() => reward.value ? t('economyRewardTitle') : t('gmPaymentTitle'))
const icon = computed(() => reward.value ? CheckmarkCircleOutline : CashOutline)
// canonical 金额统一经 formatter 换算为显示金额（25 美分 → $0.25），
// 组件内不自行 ÷100。
const amountText = computed(() => formatCurrencyAmount(
  props.proposal.amount ?? 0,
  props.currencySystem,
  props.currency,
))
const description = computed(() => {
  const p = props.proposal
  if (reward.value) {
    const values = {
      target: props.playerName(p.recipient_uid || p.uid),
      amount: amountText.value,
      reason: p.reason || '',
    }
    return t(props.solo ? 'economySoloRewardContent' : 'economyRewardContent', values)
  }
  return t('gmPaymentContent', {
    target: props.playerName(p.payer_uid || p.uid),
    amount: amountText.value,
    reason: p.reason ? t('gmPaymentReason', { reason: p.reason }) : '',
  })
})
const helpText = computed(() => reward.value && props.solo ? t('economySoloRewardHelp') : props.help)
</script>

<template>
  <article class="economy-proposal-card" :class="`economy-proposal-${tone}`" aria-live="polite">
    <header class="economy-proposal-header">
      <div class="economy-proposal-heading">
        <span class="economy-proposal-icon" aria-hidden="true"><NIcon :component="icon" size="18" /></span>
        <div>
          <strong>{{ title }}</strong>
          <span class="economy-proposal-status"><NIcon :component="TimeOutline" size="13" />{{ t('economyPendingAction', { count: 1 }) }}</span>
        </div>
      </div>
      <button type="button" class="economy-proposal-close" :title="t('close')" @click="emit('dismiss')">✕</button>
    </header>

    <p class="economy-proposal-description">{{ description }}</p>
    <p v-if="proposal.rewards?.length" class="economy-proposal-rewards">
      {{ t('gmPaymentRewards', { items: proposal.rewards.map(item => item.name).join('、') }) }}
    </p>
    <p class="economy-proposal-help"><NIcon :component="WarningOutline" size="14" />{{ helpText }}</p>
    <footer class="economy-proposal-actions">
      <button type="button" @click="emit('dismiss')">{{ dismissLabel }}</button>
      <button type="button" class="danger" :disabled="busy" @click="emit('reject')">{{ t('reject') }}</button>
      <button type="button" class="primary" :disabled="busy" @click="emit('confirm')">
        {{ reward ? (solo ? t('economySoloRewardConfirm') : t('economyApproveReward')) : t('confirmPurchase') }}
      </button>
    </footer>
  </article>
</template>
