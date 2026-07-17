<script setup lang="ts">
import { NEmpty } from 'naive-ui'
import { useLocale } from '@/composables/useLocale'
defineProps<{ description?: string }>()
const { t } = useLocale()
</script>
<template>
  <NEmpty :description="description ?? t('noData')" class="empty-state" />
</template>
