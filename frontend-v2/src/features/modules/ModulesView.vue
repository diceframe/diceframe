<script setup lang="ts">
import { useLocale } from '@/composables/useLocale'

const { t } = useLocale()
</script>

<template>
  <section class="view modules-page">
    <p class="eyebrow">{{ t('modulesKicker') }}</p>
    <h1>{{ t('navModules') }}</h1>
    <p class="view-intro">{{ t('modulesIntro') }}</p>
    <div class="empty-state">
      <p>{{ t('modulesComingSoon') }}</p>
    </div>
  </section>
</template>
