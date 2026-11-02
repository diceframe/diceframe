<script setup lang="ts">
import type { Component } from 'vue'

// One size and stroke for every left-sidebar row, so the labels start at the same x.
defineProps<{ icon: Component }>()
</script>

<template>
  <component :is="icon" class="sidebar-row-icon" :size="16" :stroke-width="1.5" aria-hidden="true" focusable="false" />
</template>

<style scoped>
.sidebar-row-icon { flex: none; }
</style>
