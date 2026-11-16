<script setup lang="ts">
defineProps<{ node: Record<string, unknown> }>()
const emit = defineEmits<{ action: [payload: { name: string; arguments: Record<string, unknown> }] }>()

function children(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object') : []
}

function items(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function scalar(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function actionArguments(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asNode(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}
</script>

<template>
  <div v-if="node.component === 'group'" class="gameplay-node-group">
    <strong v-if="node.label">{{ node.label }}</strong>
    <GameplayDeclarativeNode
      v-for="(child, index) in children(node.children)"
      :key="index"
      :node="child"
      @action="emit('action', $event)"
    />
  </div>
  <p v-else-if="node.component === 'text'" class="gameplay-node-text">
    {{ node.label }}{{ node.label && node.projected_value !== undefined ? ': ' : '' }}{{ scalar(node.projected_value ?? node.value) }}
  </p>
  <output v-else-if="node.component === 'value'" class="gameplay-node-value">
    <span v-if="node.label">{{ node.label }}: </span>{{ scalar(node.projected_value ?? node.value) }}
  </output>
  <progress
    v-else-if="node.component === 'progress'"
    class="gameplay-node-progress"
    :value="Number(node.projected_value ?? node.value ?? 0)"
    :min="Number(node.min ?? 0)"
    :max="Number(node.max ?? 100)"
  />
  <button v-else-if="node.component === 'button'" type="button" class="gameplay-node-button" @click="emit('action', { name: String(node.action || ''), arguments: actionArguments(node.arguments) })">
    {{ node.label || node.action }}
  </button>
  <ul v-else-if="node.component === 'list'" class="gameplay-node-list">
    <li v-for="(item, index) in items(node.projected_value)" :key="index">
      <GameplayDeclarativeNode v-if="item && typeof item === 'object' && !Array.isArray(item)" :node="asNode(item)" @action="emit('action', $event)" />
      <span v-else>{{ scalar(item) }}</span>
    </li>
  </ul>
</template>

<style scoped>
.gameplay-node-group { display: grid; gap: 8px; padding: 10px 0; }
.gameplay-node-text, .gameplay-node-value { display: block; margin: 6px 0; }
.gameplay-node-progress { width: 100%; }
.gameplay-node-button { padding: 6px 10px; }
.gameplay-node-list { margin: 6px 0; padding-left: 20px; }
</style>
