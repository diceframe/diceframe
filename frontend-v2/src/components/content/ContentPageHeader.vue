<script setup lang="ts">
import { useLocale } from '@/composables/useLocale'

defineProps<{ kicker: string; title: string; description?: string; headingAriaLabel?: string; compact?: boolean }>()
const { locale } = useLocale()
// In English the kicker and title are both English and often repeat each other —
// hide the kicker to avoid redundancy. In all other locales the kicker stays
// (Chinese gets an English kicker + Chinese title; other locales get English
// kicker + native title, which provides useful bilingual context).
const showKicker = locale.value !== 'en'
</script>

<template>
  <header class="content-page-heading" :class="{ 'is-compact': compact }">
    <div class="content-page-heading-copy">
      <p v-if="showKicker" class="eyebrow content-page-heading-kicker">{{ kicker }}</p>
      <div v-if="$slots['title-actions']" class="content-page-heading-title-row">
        <h1 :aria-label="headingAriaLabel">{{ title }}</h1>
        <slot name="title-actions" />
      </div>
      <h1 v-else :aria-label="headingAriaLabel">{{ title }}</h1>
      <p v-if="description && !compact" class="view-intro">{{ description }}</p>
      <p v-else-if="description" class="view-intro view-intro-compact">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="content-page-heading-actions"><slot name="actions" /></div>
  </header>
</template>

<style scoped>
.content-page-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; min-width: 0; margin-bottom: 20px; padding: 12px 0 18px; border-bottom: 1px solid var(--df-border-soft); }
.content-page-heading.is-compact { padding: 8px 0 12px; margin-bottom: 14px; }
.content-page-heading-copy { min-width: 0; max-width: 760px; }
.content-page-heading-kicker { margin: 0 0 7px; color: var(--df-interactive-strong); font-size: 10px; font-weight: 700; letter-spacing: .16em; }
.content-page-heading-title-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.content-page-heading h1 { margin: 0; font: 700 clamp(28px, 3vw, 42px)/1.15 var(--df-font-title); letter-spacing: -.02em; }
.content-page-heading.is-compact h1 { font-size: clamp(20px, 2vw, 28px); }
.content-page-heading .view-intro { margin: 8px 0 0; max-width: 68ch; color: var(--df-text-muted); font-size: 14px; line-height: 1.55; }
.content-page-heading .view-intro-compact { font-size: 13px; margin-top: 4px; }
.content-page-heading-actions { flex: 0 1 auto; min-width: 0; }
@media (max-width: 760px) { .content-page-heading { align-items: stretch; flex-direction: column; gap: 12px; padding-top: 8px; } .content-page-heading-actions { width: 100%; } }
</style>
