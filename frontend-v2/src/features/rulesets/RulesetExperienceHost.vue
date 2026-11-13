<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { errorMessage } from '@/api/client'
import { fetchRulesetExperience } from '@/api/rulesets'
import type { CharacterSheet, RulesetExperienceResponse } from '@/api/types'
import { resolveRulesetAdvancementExtension, resolveRulesetExperience } from './registry'

const props = withDefaults(defineProps<{
  ruleId: string
  language?: string
  initial?: CharacterSheet
  embedded?: boolean
  /** false = the caller owns the levels after submission (card-library wizard). */
  chain?: boolean
}>(), { language: '', embedded: false, chain: true })
const emit = defineEmits<{
  submit: [character: CharacterSheet, options?: { targetLevel?: number }]
  cancel: []
}>()

const experience = ref<RulesetExperienceResponse | null>(null)
const loading = ref(false)
const error = ref('')
// The builder only ever produces a level-1 card.  A starting level above 1 is
// the same chain the advancement panel already runs, so the host keeps itself
// open and hands the finished character over once, at the target level.
const chaining = ref<{ character: CharacterSheet; targetLevel: number } | null>(null)
const runtimeId = computed(() => String(experience.value?.ruleset_runtime?.id || ''))
const advancement = computed(() => (
  chaining.value && runtimeId.value
    ? resolveRulesetAdvancementExtension(runtimeId.value)
    : null
))

function onBuilderSubmit(
  character: CharacterSheet,
  options?: { targetLevel?: number },
): void {
  const target = Math.floor(Number(options?.targetLevel || 0))
  if (props.chain && target > 1) {
    chaining.value = { character, targetLevel: target }
    return
  }
  emit('submit', character, options)
}

function onChainCompleted(character: CharacterSheet): void {
  chaining.value = null
  emit('submit', character)
}
let sequence = 0
const host = ref<HTMLElement | null>(null)
const returnFocus = typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
  ? document.activeElement
  : null
const dialogLabel = computed(() => props.language.toLowerCase().startsWith('zh')
  ? '高级角色创建器'
  : 'Professional character builder')

function onDialogKey(event: KeyboardEvent): void {
  if (props.embedded) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('cancel')
    return
  }
  if (event.key !== 'Tab' || !host.value) return
  const focusable = Array.from(host.value.querySelectorAll<HTMLElement>(
    'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [href], [tabindex]:not([tabindex="-1"])',
  )).filter(item => item.getClientRects().length > 0)
  if (!focusable.length) {
    event.preventDefault()
    host.value.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

onMounted(() => {
  if (!props.embedded) void nextTick(() => host.value?.focus())
})
onBeforeUnmount(() => returnFocus?.focus())

const component = computed(() => (
  experience.value
    ? resolveRulesetExperience(experience.value.experience.profile)
    : null
))

watch(
  () => [props.ruleId, props.language] as const,
  async ([ruleId, language]) => {
    const current = ++sequence
    loading.value = true
    error.value = ''
    experience.value = null
    chaining.value = null
    try {
      const result = await fetchRulesetExperience(ruleId, language)
      if (current !== sequence) return
      experience.value = result
      if (!resolveRulesetExperience(result.experience.profile)) {
        error.value = `Unsupported ruleset experience: ${result.experience.profile}`
      }
    } catch (cause: unknown) {
      if (current === sequence) error.value = errorMessage(cause)
    } finally {
      if (current === sequence) loading.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <div
    ref="host"
    :class="['ruleset-experience-host', { embedded }]"
    :role="embedded ? 'region' : 'dialog'"
    :aria-label="dialogLabel"
    :aria-modal="embedded ? undefined : true"
    :tabindex="embedded ? undefined : -1"
    @keydown="onDialogKey"
  >
    <div v-if="loading" class="ruleset-host-state" role="status">正在读取高级规则数据…</div>
    <div v-else-if="error" class="ruleset-host-state error-banner" role="alert">
      <p>{{ error }}</p>
      <button @click="emit('cancel')">返回</button>
    </div>
    <component
      :is="advancement.component"
      v-else-if="advancement && chaining"
      :rule-id="ruleId"
      :character="chaining.character"
      :language="language"
      :target-level="chaining.targetLevel"
      @applied="onChainCompleted"
      @chain-completed="onChainCompleted"
      @cancel="emit('cancel')"
    />
    <div v-else-if="chaining" class="ruleset-host-state error-banner" role="alert">
      <p>{{ language.toLowerCase().startsWith('zh') ? '当前规则尚未注册升级界面。' : 'This ruleset has no registered advancement interface.' }}</p>
      <button @click="emit('cancel')">{{ language.toLowerCase().startsWith('zh') ? '返回' : 'Back' }}</button>
    </div>
    <component
      :is="component"
      v-else-if="component && experience"
      :rule-id="ruleId"
      :language="language"
      :initial="initial"
      :embedded="embedded"
      :experience="experience.experience"
      @submit="onBuilderSubmit"
      @cancel="emit('cancel')"
    />
  </div>
</template>

<style scoped>
.ruleset-experience-host:not(.embedded) {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgb(8 12 18 / 76%);
  backdrop-filter: blur(10px);
}
.ruleset-experience-host.embedded { width: 100%; }
.ruleset-host-state {
  width: min(560px, 100%);
  padding: 28px;
  border: 1px solid var(--border-color, #425064);
  border-radius: 18px;
  background: var(--card-bg, #151c27);
  text-align: center;
}
.ruleset-host-state button { min-height: 44px; }
.ruleset-host-state button:focus-visible { outline: 3px solid var(--df-interactive-strong, #64d7cf); outline-offset: 2px; }
@media (max-width: 640px) {
  .ruleset-experience-host:not(.embedded) { padding: 0; place-items: stretch; }
}
@media (prefers-reduced-motion: reduce) {
  .ruleset-experience-host:not(.embedded) { backdrop-filter: none; }
}
</style>
