<script setup lang="ts">
import { computed, ref } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import type { Player } from '@/api/types'

const { t } = useLocale()

const props = defineProps<{
  players: Player[]
  payerUid: string
  recipientUid: string
  busy?: boolean
}>()

const emit = defineEmits<{
  close: []
  submit: [payload: { payer_uid: string; recipient_uid: string; amount: number; reason: string; items: string[] }]
}>()

// 显式初始化：弹窗每次打开都重新挂载，预选值直接来自 props，
// 不再依赖"值变化才触发"的 watch。
const payerUid = ref(props.payerUid)
const recipientUid = ref(props.recipientUid)
const amount = ref(1)
const reason = ref('')
const items = ref('')

const normalizedItems = computed(() => items.value.split(/[,，、\n]/).map(item => item.trim()).filter(Boolean))
const canSubmit = computed(() => Boolean(payerUid.value) && amount.value >= 1)

function submit() {
  if (!canSubmit.value) return
  emit('submit', {
    payer_uid: payerUid.value,
    recipient_uid: recipientUid.value || payerUid.value,
    amount: Math.trunc(amount.value),
    reason: reason.value.trim(),
    items: normalizedItems.value,
  })
}
</script>

<template>
  <Modal :title="t('createPaymentProposal')" @close="emit('close')">
    <div class="gm-payment-form">
      <label>
        <span>{{ t('paymentPayer') }}</span>
        <select v-model="payerUid">
          <option v-for="player in players" :key="player.user_id" :value="player.user_id">{{ player.character_name || player.user_id }}</option>
        </select>
      </label>
      <label>
        <span>{{ t('paymentRecipient') }}</span>
        <select v-model="recipientUid">
          <option v-for="player in players" :key="player.user_id" :value="player.user_id">{{ player.character_name || player.user_id }}</option>
        </select>
      </label>
      <label>
        <span>{{ t('paymentAmount') }}</span>
        <input v-model.number="amount" type="number" min="1" max="100000">
      </label>
      <label>
        <span>{{ t('paymentReason') }}</span>
        <input v-model="reason" :placeholder="t('paymentReasonPlaceholder')" maxlength="240">
      </label>
      <label>
        <span>{{ t('paymentItems') }}</span>
        <input v-model="items" :placeholder="t('paymentItemsPlaceholder')">
      </label>
      <p class="muted">{{ t('paymentProposalHelp') }}</p>
    </div>
    <template #actions>
      <button type="button" @click="emit('close')">{{ t('cancel') }}</button>
      <button type="button" class="primary" :disabled="busy || !canSubmit" @click="submit">{{ busy ? t('saving') : t('createProposal') }}</button>
    </template>
  </Modal>
</template>
