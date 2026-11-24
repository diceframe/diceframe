<script setup lang="ts">
/**
 * GM 裁定面板：先把状态读成定义，再决定要不要贴。
 *
 * 这里只发意图（apply / remove），不做任何本地判定——状态会带来什么由服务端
 * 的 catalog 解释；面板把"名称 + 规则摘要"摆在按钮旁边，就是为了让 GM 在
 * 动手之前看见它会做什么。
 */
import { computed, ref, watch } from 'vue'

export interface AdjudicableCondition {
  id: string
  name: string
  summary: string
}

export interface AdjudicationTarget {
  id: string
  name: string
}

const props = defineProps<{
  conditions: AdjudicableCondition[]
  targets: AdjudicationTarget[]
  busy: boolean
  labels: {
    title: string
    target: string
    pick: string
    apply: string
    remove: string
    hint: string
  }
}>()

const emit = defineEmits<{
  (event: 'submit', payload: { target_id: string; condition: string; action: 'apply' | 'remove' }): void
}>()

const targetId = ref('')
const conditionId = ref('')

watch(
  () => props.targets,
  list => { if (!list.some(item => item.id === targetId.value)) targetId.value = list[0]?.id ?? '' },
  { immediate: true },
)
watch(
  () => props.conditions,
  list => { if (!list.some(item => item.id === conditionId.value)) conditionId.value = list[0]?.id ?? '' },
  { immediate: true },
)

const selected = computed(() => props.conditions.find(item => item.id === conditionId.value))
const ready = computed(() => Boolean(targetId.value && conditionId.value) && !props.busy)

function send(action: 'apply' | 'remove'): void {
  if (!ready.value) return
  emit('submit', { target_id: targetId.value, condition: conditionId.value, action })
}
</script>

<template>
  <section v-if="conditions.length" class="condition-adjudicator" data-testid="gm-condition-adjudicator">
    <h4>{{ labels.title }}</h4>
    <div class="condition-fields">
      <label>
        <span>{{ labels.target }}</span>
        <select v-model="targetId" :disabled="busy">
          <option v-for="target in targets" :key="target.id" :value="target.id">{{ target.name }}</option>
        </select>
      </label>
      <label>
        <span>{{ labels.pick }}</span>
        <select v-model="conditionId" :disabled="busy">
          <option v-for="condition in conditions" :key="condition.id" :value="condition.id">
            {{ condition.name }}
          </option>
        </select>
      </label>
    </div>
    <p v-if="selected" class="condition-summary" data-testid="gm-condition-summary">
      {{ selected.summary }}
    </p>
    <p class="condition-hint">{{ labels.hint }}</p>
    <div class="condition-actions">
      <button type="button" :disabled="!ready" @click="send('apply')">{{ labels.apply }}</button>
      <button type="button" class="secondary" :disabled="!ready" @click="send('remove')">
        {{ labels.remove }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.condition-adjudicator { display: grid; gap: 8px; padding: 12px 14px; border: 1px solid rgb(120 150 175 / 28%); border-radius: 12px; background: rgb(9 15 22 / 70%); }
.condition-adjudicator h4 { margin: 0; font-size: 13px; color: #ffe5a8; }
.condition-fields { display: flex; flex-wrap: wrap; gap: 10px; }
.condition-fields label { display: grid; gap: 4px; font-size: 12px; color: #9aa9b4; }
.condition-fields select { min-width: 150px; padding: 6px 8px; border: 1px solid rgb(120 150 175 / 35%); border-radius: 8px; background: rgb(14 21 30 / 90%); color: #e8eef2; }
.condition-summary { margin: 0; font-size: 12px; line-height: 1.6; color: #d6e2ea; }
.condition-hint { margin: 0; font-size: 11px; color: #8fa0ab; }
.condition-actions { display: flex; gap: 8px; }
.condition-actions button { padding: 6px 14px; border: 1px solid rgb(120 150 175 / 40%); border-radius: 8px; background: rgb(30 44 58 / 90%); color: #e8eef2; font-size: 12px; cursor: pointer; }
.condition-actions button.secondary { background: transparent; }
.condition-actions button:disabled { opacity: .45; cursor: not-allowed; }
:global(body.light .condition-adjudicator) { background: #f6f8fa; border-color: #d7dee5; }
:global(body.light .condition-adjudicator h4) { color: #8a5a00; }
:global(body.light .condition-fields label) { color: #5a6672; }
:global(body.light .condition-fields select) { background: #fff; color: #1c242c; border-color: #c9d2da; }
:global(body.light .condition-summary) { color: #33404c; }
:global(body.light .condition-hint) { color: #6b7884; }
:global(body.light .condition-actions button) { background: #eef2f6; color: #1c242c; border-color: #c9d2da; }
</style>
