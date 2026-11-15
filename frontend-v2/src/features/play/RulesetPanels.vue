<script setup lang="ts">
import { computed, ref } from 'vue'
import { submitRulesetIntent } from '@/api/rulesets'
import type { GameplayDeclarativeNode } from '@/api/types'
import GameplayDeclarativeNodeView from '@/features/gameplays/GameplayDeclarativeNode.vue'

const props = defineProps<{
  gameKey: string
  panels?: GameplayDeclarativeNode[] | null
}>()
const emit = defineEmits<{ refresh: [] }>()

// 规则运行时返回的节点一律当作不可信输入：非对象、无 component 的条目丢弃，
// 而不是渲染一个空白卡片。
const items = computed<GameplayDeclarativeNode[]>(() => (
  Array.isArray(props.panels)
    ? props.panels.filter((node): node is GameplayDeclarativeNode => (
      !!node && typeof node === 'object' && !Array.isArray(node)
      && typeof (node as GameplayDeclarativeNode).component === 'string'
    ))
    : []
))

const busy = ref(false)
const error = ref('')

/**
 * 面板按钮 → 一次普通 intent。
 *
 * `action` 是 intent 的 `type`，`arguments` 是其余字段。走已有
 * `POST /games/{key}/intents`，因此插件不需要额外端点，也不需要
 * gameplay provider 身份。
 */
async function runAction(payload: { name: string; arguments: Record<string, unknown> }) {
  if (!payload.name || !props.gameKey || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await submitRulesetIntent(props.gameKey, {
      ...payload.arguments,
      type: payload.name,
    })
    if (result?.ok) emit('refresh')
    else error.value = String((result as { error?: string })?.error || 'Action was not accepted')
  } catch (cause: unknown) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="items.length" class="ruleset-panels" data-testid="ruleset-panels">
    <p v-if="error" class="ruleset-panels__error" role="alert">{{ error }}</p>
    <section
      v-for="(node, index) in items"
      :key="index"
      class="ruleset-panels__card"
      :aria-busy="busy ? 'true' : 'false'"
    >
      <GameplayDeclarativeNodeView :node="node" @action="runAction" />
    </section>
  </div>
</template>

<style scoped>
.ruleset-panels { display: grid; gap: 8px; margin: 0 0 8px; }
.ruleset-panels__card {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-md);
  background: color-mix(in srgb, var(--df-surface-1) 92%, transparent);
}
.ruleset-panels__card :deep(button) { justify-self: start; }
.ruleset-panels__error { margin: 0; color: var(--df-danger, #c0392b); }
</style>
