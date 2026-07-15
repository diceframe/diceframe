<script setup lang="ts">
import { NEmpty } from 'naive-ui'
defineProps<{ description?: string }>()
</script>
<template>
  <NEmpty :description="description ?? '暂无数据'" class="empty-state" />
</template>
