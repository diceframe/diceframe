<script setup lang="ts">
import { NButton, NIcon } from 'naive-ui'
import { SunnyOutline, MoonOutline } from '@vicons/ionicons5'
import { useTheme } from '@/composables/useTheme'
const { current, toggle } = useTheme()
</script>
<template>
  <NButton
    circle
    quaternary
    class="theme-toggle"
    :title="current === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
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
