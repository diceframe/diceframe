<script setup lang="ts">
import PageHeader from './PageHeader.vue'
defineProps<{ title?: string; subtitle?: string; narrow?: boolean }>()
</script>
<template>
  <section class="view" :class="{ narrow }">
    <PageHeader v-if="title || $slots.actions" :title="title" :subtitle="subtitle">
      <template v-if="$slots.actions" #actions><slot name="actions" /></template>
    </PageHeader>
    <slot />
  </section>
</template>
