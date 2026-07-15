<script setup lang="ts">
defineProps<{ title?: string; subtitle?: string }>()
</script>
<template>
  <header class="view-title">
    <div>
      <h1 v-if="title">{{ title }}</h1>
      <p v-if="subtitle" class="muted">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
  </header>
</template>
