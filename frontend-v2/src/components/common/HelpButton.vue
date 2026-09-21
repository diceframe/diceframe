<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NIcon, NModal } from 'naive-ui'
import { HelpCircleOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{ title: string; buttonLabel?: string; compact?: boolean }>()
const { t } = useLocale()
const show = ref(false)
</script>

<template>
  <NButton size="small" secondary class="help-btn" :class="{ compact: props.compact }" :title="title" :aria-label="title" @click="show = true">
    <template v-if="props.compact" #icon>
      <NIcon :component="HelpCircleOutline" />
    </template>
    <span v-if="!props.compact">{{ buttonLabel ?? t('help') }}</span>
  </NButton>
  <NModal v-model:show="show" preset="card" :title="title" style="max-width:520px" :bordered="false">
    <div class="help-tutorial">
      <slot />
    </div>
  </NModal>
</template>

<style scoped>
.help-btn{min-height:30px;font-size:12px;font-weight:650;color:var(--df-interactive-strong)}
.help-btn.compact{min-height:24px;min-width:24px;width:24px;padding:0;margin-left:6px;border-radius:50%;font-size:0;line-height:1;vertical-align:middle}
.help-btn.compact :deep(.n-button__icon){margin:0}
.help-btn :deep(.n-button__icon){font-size:16px}
.help-tutorial h4{margin:14px 0 4px}
.help-tutorial h4:first-child{margin-top:0}
.help-tutorial p{margin:4px 0;line-height:1.6}
.help-tutorial ul{margin:6px 0;padding-left:20px;line-height:1.8}
.help-tutorial code{background:color-mix(in srgb,var(--df-accent) 12%,transparent);padding:1px 5px;border-radius:4px;font-family:var(--df-font-mono);font-size:12px}
</style>
