<script setup lang="ts">
import { NButton, NIcon } from 'naive-ui'
import { SunnyOutline, MoonOutline } from '@vicons/ionicons5'
import { useTheme } from '@/composables/useTheme'
import { useLocale } from '@/composables/useLocale'
const { current, toggle } = useTheme()
const { t } = useLocale()
</script>
<template>
  <NButton
    circle
    quaternary
    class="theme-toggle"
    :title="current === 'dark' ? t('switchToLightTheme') : t('switchToDarkTheme')"
    @click="toggle"
  >
    <template #icon>
      <NIcon size="18">
        <MoonOutline v-if="current === 'dark'" />
        <SunnyOutline v-else />
      </NIcon>
    </template>
  </NButton>
</template>
