<script setup lang="ts">
import { useLocale } from '@/composables/useLocale'

defineProps<{ playMode?: string; diagnostic?: string }>()
const { t } = useLocale()
</script>

<template>
  <section class="view unsupported-gameplay" data-testid="unsupported-gameplay" role="alert">
    <h1>{{ t('unsupportedGameplayTitle') }}</h1>
    <p>{{ t('unsupportedGameplayMessage') }}</p>
    <p class="muted">{{ t('unsupportedGameplayReadonly') }}</p>
    <p v-if="diagnostic" class="muted" data-testid="unsupported-gameplay-diagnostic">{{ diagnostic }}</p>
</section>
</template>

<style scoped>
.unsupported-gameplay { max-width: 720px; margin: 64px auto; padding: 24px; }
.unsupported-gameplay h1 { margin: 0 0 12px; }
.unsupported-gameplay p { line-height: 1.6; }
</style>
