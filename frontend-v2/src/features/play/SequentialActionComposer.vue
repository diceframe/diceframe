<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { RulesetCombatAction } from '@/api/types'
import { submitRulesetIntent } from '@/api/rulesets'

const props = defineProps<{
  gameKey: string
  actorId: string
  actions: RulesetCombatAction[]
  sequentialChecks?: boolean
  compositeAction?: boolean
}>()
const emit = defineEmits<{ changed: [] }>()
const selected = ref<RulesetCombatAction[]>([])
const busy = ref(false)
const error = ref('')
const options = computed(() => props.actions.filter(action => {
  const raw = action as Record<string, unknown>
  return Boolean(raw.skill_id || raw.ability_id)
}))
const mode = computed(() => props.sequentialChecks ? 'sequential_checks' : 'composite_action')
const title = computed(() => props.sequentialChecks ? '顺序多检定' : '组合技能')
// Keep the exact submitted DTO and identity across uncertain transport errors.
// A changed draft/context is a new command, not a retry of the old one.
let pending: { signature: string, intent: Record<string, unknown> } | null = null
watch(() => [props.gameKey, props.actorId, mode.value], () => { pending = null })
function label(action: RulesetCombatAction): string {
  const raw = action as Record<string, unknown>
  return String(action.label || raw.skill_id || raw.ability_id || action.type)
}
function add(action: RulesetCombatAction) {
  if (busy.value || selected.value.length >= 3 || selected.value.includes(action)) return
  pending = null
  selected.value = [...selected.value, action]
}
function remove(index: number) {
  if (busy.value) return
  pending = null
  selected.value = selected.value.filter((_, i) => i !== index)
}
async function submit() {
  if (!selected.value.length || busy.value) return
  busy.value = true; error.value = ''
  try {
    const draft = {
      type: 'sequential_action',
      mode: mode.value,
      steps: selected.value.map((action, index) => {
        const raw = action as Record<string, unknown>
        return {
          step_id: `step-${index + 1}`,
          actor_id: String(raw.actor_id || props.actorId),
          order: index,
          ...(raw.skill_id ? { skill_id: String(raw.skill_id) } : { ability_id: String(raw.ability_id) }),
          target: raw.target ?? null,
          depends_on: index ? [`step-${index}`] : [],
          params: {},
          resource_cost: {},
        }
      }),
    }
    const signature = JSON.stringify([props.gameKey, props.actorId, draft])
    if (!pending || pending.signature !== signature) {
      pending = {
        signature,
        intent: JSON.parse(JSON.stringify({
          ...draft,
          intent_id: globalThis.crypto?.randomUUID?.() || `seq-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        })),
      }
    }
    const attempt = pending
    const result = await submitRulesetIntent(props.gameKey, attempt.intent)
    if (!result.ok) throw new Error(String((result as unknown as Record<string, unknown>).error || '规则运行时拒绝了这组动作'))
    if (pending === attempt) {
      pending = null
      selected.value = []
    }
    emit('changed')
  } catch (cause: unknown) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally { busy.value = false }
}
</script>

<template>
  <section v-if="(sequentialChecks || compositeAction) && options.length" class="sequential-action-composer" data-testid="sequential-action-composer">
    <header><strong>{{ title }}</strong><small>规则运行时负责顺序、资源与失败分支</small></header>
    <div class="sequential-selected">
      <button v-for="(action, index) in selected" :key="`${label(action)}-${index}`" type="button" :disabled="busy" @click="remove(index)">{{ index + 1 }}. {{ label(action) }} ×</button>
      <span v-if="!selected.length" class="muted">依次选择最多 3 步</span>
    </div>
    <div class="sequential-options"><button v-for="action in options" :key="label(action)" type="button" :disabled="busy || selected.includes(action) || selected.length >= 3" @click="add(action)">{{ label(action) }}</button></div>
    <button class="primary" type="button" :disabled="!selected.length || busy" @click="submit">{{ busy ? '提交中…' : '提交这组动作' }}</button>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.sequential-action-composer { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--df-border-soft); border-radius: 8px; }
.sequential-action-composer header { display: grid; gap: 2px; }
.sequential-action-composer small, .muted { color: var(--df-text-muted); }
.sequential-selected, .sequential-options { display: flex; flex-wrap: wrap; gap: 6px; }
.sequential-action-composer button { padding: 5px 8px; border-radius: 6px; }
.sequential-action-composer .primary { justify-self: start; }
.error { color: var(--df-danger, #c2413a); margin: 0; }
</style>
