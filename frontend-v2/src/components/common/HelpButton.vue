<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NIcon, NModal } from 'naive-ui'
import { HelpCircleOutline } from '@vicons/ionicons5'

defineProps<{ title: string; buttonLabel?: string }>()
const show = ref(false)
</script>

<template>
  <NButton text class="help-btn" @click="show = true">
    <template #icon>
      <NIcon :component="HelpCircleOutline" />
    </template>
    {{ buttonLabel ?? '帮助' }}
  </NButton>
  <NModal v-model:show="show" preset="card" :title="title" style="max-width:520px" :bordered="false">
    <div class="help-tutorial">
      <slot />
    </div>
  </NModal>
</template>

<style scoped>
.help-btn{font-size:13px;font-weight:400}
.help-tutorial h4{margin:14px 0 4px}
.help-tutorial h4:first-child{margin-top:0}
.help-tutorial p{margin:4px 0;line-height:1.6}
.help-tutorial ul{margin:6px 0;padding-left:20px;line-height:1.8}
.help-tutorial code{background:rgba(216,173,82,.12);padding:1px 5px;border-radius:4px;font-family:var(--font-mono);font-size:12px}
</style>
