<script setup lang="ts">
withDefaults(defineProps<{ size?: number; withText?: boolean; subtitle?: string }>(), {
  size: 34,
  withText: true,
  subtitle: '结构化 AI 跑团',
})
</script>

<template>
  <span class="brand-logo" :style="{ '--logo-size': `${size}px` }">
    <svg class="brand-mark" viewBox="0 0 48 48" :width="size" :height="size" aria-hidden="true">
      <rect class="frame" x="6" y="6" width="36" height="36" rx="6" />
      <path class="frame" d="M6 13V6H13M35 6H42V13M42 35V42H35M13 42H6V35" />
      <rect class="die" x="15" y="15" width="18" height="18" rx="3.5" />
      <circle class="pip" cx="20" cy="20" r="1.6" />
      <circle class="pip" cx="28" cy="20" r="1.6" />
      <circle class="pip" cx="24" cy="24" r="1.6" />
      <circle class="pip" cx="20" cy="28" r="1.6" />
      <circle class="pip" cx="28" cy="28" r="1.6" />
    </svg>
    <span v-if="withText" class="brand-text">
      <strong>DiceFrame</strong>
      <small v-if="subtitle">{{ subtitle }}</small>
    </span>
  </span>
</template>
