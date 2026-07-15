<script setup lang="ts">
import { useDialog, useMessage } from 'naive-ui'
import { registerNaiveApis } from '@/composables/useNaiveBridge'

registerNaiveApis(useMessage(), useDialog())
</script>

<template>
  <slot />
</template>