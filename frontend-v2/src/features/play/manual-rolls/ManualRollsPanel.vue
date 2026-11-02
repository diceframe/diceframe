<script setup lang="ts">
import { computed, ref, toRef, watch } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import { useLocale } from '@/composables/useLocale'
import { useManualRolls } from './useManualRolls'
import type { ManualRollComparison, ManualRollPurpose, ManualRollRequest, ManualRollVisibility } from './types'

const props = defineProps<{
  gameKey: string; runId: string; actorId: string; isGm: boolean; lastActivity?: string; preview?: boolean; delegate?: boolean
  players: Array<{ user_id: string; character_name: string }>
}>()
const emit = defineEmits<{ availability: [available: boolean] }>()
const { locale } = useLocale()
const zh = computed(() => locale.value.startsWith('zh'))
const copy = computed(() => zh.value ? {
  title: '手动投掷', request: '发起投掷', retry: '重试', empty: '手动投掷', history: '投掷记录',
  purpose: '用途', free: '自由投掷', check: '规则检定', contest: '对抗比较', target: '目标值', targetPlaceholder: '例如：15', comparison: '判定方向', auto: '按当前规则自动', atLeast: '达到目标即成功', atMost: '不超过目标即成功',
  pending: '等待投掷', resolved: '已完成', cancelled: '已取消', rollFor: '代投',
  gmOverride: 'GM 代投', requestNotice: 'GM 请求你进行一次投掷，请确认后提交结果。',
  cancel: '取消请求', pendingRoll: '待投掷', dialog: '发起手动投掷', label: '说明',
  labelPlaceholder: '例如：察觉检定', formula: '骰子公式', formulaHint: '例如 d20、d20+2、2d6+1；自由投掷只记录骰值，规则检定会显示成败。', visibility: '可见范围',
  party: '全队可见', private: '仅目标可见', targets: '投掷角色', later: '稍后',
  send: '发送请求', sending: '发送中…', roll: '投掷', rolling: '投掷中…',
  chooseTarget: '请至少选择一名投掷角色。',
  tellAi: '将结果告知 AI', aiAlwaysIncluded: '规则检定与对抗比较的结果始终会告知 AI。',
} : {
  title: 'Manual rolls', request: 'Request roll', retry: 'Retry', empty: 'Manual roll', history: 'Roll history',
  pending: 'Pending', resolved: 'Resolved', cancelled: 'Cancelled', rollFor: 'Roll for',
  gmOverride: 'GM override', requestNotice: 'The GM requested a roll from you. Confirm it to submit the result.',
  cancel: 'Cancel request', pendingRoll: 'Pending roll', dialog: 'Request a manual roll',
  purpose: 'Purpose', free: 'Free roll', check: 'Rule check', contest: 'Contest', target: 'Target', targetPlaceholder: 'e.g. 15', comparison: 'Success direction', auto: 'Use current rules', atLeast: 'Meet or exceed target', atMost: 'Stay at or below target',
  label: 'Label', labelPlaceholder: 'e.g. Perception', formula: 'Dice formula', formulaHint: 'Examples: d20, d20+2, 2d6+1. Free rolls only record the result; checks also show success or failure.',
  visibility: 'Visibility', party: 'Party', private: 'Targets only', targets: 'Targets',
  later: 'Later', send: 'Send request', sending: 'Sending…', roll: 'Roll',
  rolling: 'Rolling…', chooseTarget: 'Select at least one target.',
  tellAi: 'Tell the AI', aiAlwaysIncluded: 'Rule check and contest results are always shared with the AI.',
})
const viewerIsGm = computed(() => props.isGm && !props.preview)
const rolls = useManualRolls(toRef(props, 'gameKey'), toRef(props, 'runId'), toRef(props, 'actorId'), viewerIsGm, toRef(props, 'lastActivity'))
const visibleRequests = rolls.visibleRequests
const activeRequest = rolls.activeRequest
const composerOpen = ref(false), historyOpen = ref(false), formula = ref('d20'), label = ref(''), purpose = ref<ManualRollPurpose>('free'), target = ref<number | null>(null), comparison = ref<ManualRollComparison>('auto'), visibility = ref<ManualRollVisibility>('party'), targetUids = ref<string[]>([])
const includeInAi = ref(false)
const busy = ref(false), submitError = ref(''), draftOperationId = ref('')
const canCreate = computed(() => viewerIsGm.value)
const canAct = computed(() => !props.preview || Boolean(props.delegate))
const available = computed(() => Boolean(canCreate.value || visibleRequests.value.length || rolls.error.value))
watch(available, value => emit('availability', value), { immediate: true })

function openPending() {
  const own = rolls.pendingForActor.value.find(item => item.target_uids.includes(props.actorId) && !item.results[props.actorId])
  if (own) { historyOpen.value = false; rolls.reopen(own.id) }
  else historyOpen.value = true
}
function reopen(request: ManualRollRequest) { historyOpen.value = false; rolls.reopen(request.id) }

function resetComposer() { formula.value = 'd20'; label.value = ''; purpose.value = 'free'; target.value = null; comparison.value = 'auto'; visibility.value = 'party'; targetUids.value = props.players.map(player => player.user_id); includeInAi.value = false; submitError.value = ''; draftOperationId.value = '' }
function openComposer() { resetComposer(); composerOpen.value = true }
function toggleTarget(uid: string) { targetUids.value = targetUids.value.includes(uid) ? targetUids.value.filter(item => item !== uid) : [...targetUids.value, uid] }
async function create() {
  if (!targetUids.value.length) { submitError.value = copy.value.chooseTarget; return }
  submitError.value = ''
  if (purpose.value === 'check' && (target.value === null || !Number.isInteger(target.value))) { submitError.value = copy.value.targetPlaceholder; return }
  busy.value = true
  // 检定/对抗由服务端强制收录，前端不发送该开关；自由投掷按勾选发送严格布尔。
  const includeInAiContext = purpose.value === 'free' ? includeInAi.value : undefined
  try { draftOperationId.value = await rolls.create({ formula: formula.value, label: label.value, purpose: purpose.value, target: target.value, comparison: comparison.value, include_in_ai_context: includeInAiContext, target_uids: targetUids.value, visibility: visibility.value, operation_id: draftOperationId.value || undefined }); composerOpen.value = false; draftOperationId.value = '' }
  catch (error: unknown) { submitError.value = error instanceof Error ? error.message : String(error) }
  finally { busy.value = false }
}
async function resolve(request: ManualRollRequest, targetUid = props.actorId) { busy.value = true; try { await rolls.roll(request, targetUid) } catch (error: unknown) { submitError.value = error instanceof Error ? error.message : String(error) } finally { busy.value = false } }
async function cancel(request: ManualRollRequest) { busy.value = true; try { await rolls.cancel(request) } catch (error: unknown) { submitError.value = error instanceof Error ? error.message : String(error) } finally { busy.value = false } }
function verdictText(value?: string) {
  if (zh.value) return value === 'success' ? '成功' : value === 'failure' ? '失败' : value === 'winner' ? '胜者' : value === 'loss' ? '落败' : ''
  return value === 'success' ? 'success' : value === 'failure' ? 'failure' : value === 'winner' ? 'winner' : value === 'loss' ? 'loss' : ''
}
function resultText(request: ManualRollRequest) { return Object.entries(request.results).map(([uid, result]) => `${request.target_names[uid] || uid}: ${result.total}${result.target == null ? '' : ` / ${result.target}`}${verdictText(result.verdict) ? ` · ${verdictText(result.verdict)}` : ''}`).join(' · ') }
function statusText(status: ManualRollRequest['status']) { return copy.value[status] }
function purposeText(value?: ManualRollPurpose) { return value === 'check' ? copy.value.check : value === 'contest' ? copy.value.contest : copy.value.free }
</script>

<template>
  <section v-if="available" class="manual-rolls" data-testid="manual-rolls" :aria-label="copy.title">
    <button v-if="canCreate" type="button" data-testid="manual-roll-create" @click="openComposer">{{ copy.request }}</button>
    <button v-if="rolls.pendingForActor.value.length" type="button" class="primary" data-testid="manual-roll-pending" @click="openPending">{{ copy.pendingRoll }} · {{ rolls.pendingForActor.value.length }}</button>
    <button v-if="visibleRequests.length" type="button" data-testid="manual-roll-history" @click="historyOpen = true">{{ copy.history }}</button>
    <p v-if="rolls.error.value" class="manual-roll-error" role="alert">{{ rolls.error.value }} <button type="button" @click="rolls.refresh">{{ copy.retry }}</button></p>
  </section>

  <Modal v-if="composerOpen" :title="copy.dialog" @close="composerOpen = false">
    <label>{{ copy.label }} <input v-model="label" maxlength="200" :placeholder="copy.labelPlaceholder" /></label>
    <label>{{ copy.formula }} <input v-model="formula" placeholder="d20 + 2" /><small class="manual-roll-field-hint">{{ copy.formulaHint }}</small></label>
    <label>{{ copy.purpose }} <select v-model="purpose"><option value="free">{{ copy.free }}</option><option value="check">{{ copy.check }}</option><option value="contest">{{ copy.contest }}</option></select></label>
    <template v-if="purpose === 'check'"><label>{{ copy.target }} <input v-model.number="target" type="number" min="1" max="10000" :placeholder="copy.targetPlaceholder" /></label><label>{{ copy.comparison }} <select v-model="comparison"><option value="auto">{{ copy.auto }}</option><option value="at_least">{{ copy.atLeast }}</option><option value="at_most">{{ copy.atMost }}</option></select></label></template>
    <label>{{ copy.visibility }} <select v-model="visibility"><option value="party">{{ copy.party }}</option><option value="private">{{ copy.private }}</option></select></label>
    <label v-if="purpose === 'free'">{{ copy.tellAi }} <input v-model="includeInAi" type="checkbox" data-testid="manual-roll-include-ai" /></label>
    <small v-else class="manual-roll-field-hint">{{ copy.aiAlwaysIncluded }}</small>
    <fieldset class="manual-roll-targets"><legend>{{ copy.targets }}</legend><label v-for="player in players" :key="player.user_id" class="form-check"><input type="checkbox" :checked="targetUids.includes(player.user_id)" @change="toggleTarget(player.user_id)" /> <span>{{ player.character_name || player.user_id }}</span></label></fieldset>
    <p v-if="submitError" class="muted">{{ submitError }}</p>
    <template #actions><button :disabled="busy" @click="composerOpen = false">{{ copy.later }}</button><button class="primary" :disabled="busy" @click="create">{{ busy ? copy.sending : copy.send }}</button></template>
  </Modal>

  <Modal v-if="historyOpen" :title="copy.history" @close="historyOpen = false">
    <p v-if="submitError" class="manual-roll-error" role="alert">{{ submitError }}</p>
    <div class="manual-roll-list" data-testid="manual-roll-history-list">
      <article v-for="request in visibleRequests" :key="request.id" class="manual-roll-card">
        <strong>{{ request.label || copy.empty }} · {{ request.formula }} · {{ purposeText(request.purpose) }}</strong>
        <span>{{ statusText(request.status) }} · {{ request.target_uids.map(uid => request.target_names[uid] || uid).join(', ') }}</span>
        <p v-if="resultText(request)">{{ resultText(request) }}</p>
        <div v-if="viewerIsGm && request.status === 'pending'" class="manual-roll-actions">
          <button v-for="uid in request.target_uids" :key="uid" type="button" :disabled="busy || preview || Boolean(request.results[uid])" @click="resolve(request, uid)">{{ copy.gmOverride }} · {{ request.target_names[uid] || uid }}</button>
          <button type="button" class="danger-quiet" :disabled="busy || preview" @click="cancel(request)">{{ copy.cancel }}</button>
        </div>
        <button v-if="!viewerIsGm && rolls.pendingForActor.value.some(item => item.id === request.id)" type="button" @click="reopen(request)">{{ copy.roll }}</button>
      </article>
    </div>
  </Modal>

  <Modal v-if="activeRequest && !historyOpen && !composerOpen" :title="activeRequest.label || copy.title" @close="rolls.dismiss(activeRequest.id)">
    <p class="manual-roll-notice" role="status" aria-live="assertive">{{ copy.requestNotice }}</p>
    <p>{{ copy.roll }} {{ activeRequest.formula }}</p>
    <p v-if="submitError" class="muted">{{ submitError }}</p>
    <template #actions><button @click="rolls.dismiss(activeRequest.id)">{{ copy.later }}</button><button class="primary" :disabled="busy || !canAct" @click="resolve(activeRequest)">{{ busy ? copy.rolling : copy.roll }}</button></template>
  </Modal>
</template>

<style scoped>
.manual-rolls { display: grid; min-width: 0; gap: 7px; }
.manual-rolls > button { width: 100%; min-height: 34px; padding: 7px 9px; font-size: 12px; }
.manual-roll-list { display: grid; min-width: 0; gap: 10px; }
.manual-roll-card { display: grid; min-width: 0; gap: 7px; padding: 12px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); overflow-wrap: anywhere; }
.manual-roll-card > span { color: var(--df-text-muted); font-size: 12px; }
.manual-roll-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.manual-roll-actions button { flex: 1 1 auto; }
.danger-quiet, .manual-roll-error { color: var(--df-danger-strong); }
.manual-roll-error { display: grid; gap: 6px; font-size: 12px; overflow-wrap: anywhere; }
.manual-roll-notice { padding: 10px; border-left: 3px solid var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 10%, transparent); }
.manual-roll-targets { display: grid; gap: 7px; margin: 12px 0; padding: 10px; border: 1px solid var(--df-border-soft); }
.manual-roll-targets .form-check { display: flex; align-items: center; gap: 7px; }
.manual-roll-targets input[type="checkbox"] { width: 16px; height: 16px; }
.manual-roll-field-hint { display: block; margin-top: 4px; color: var(--df-text-muted); font-size: 12px; }
</style>
