<script setup lang="ts">
import { NModal } from 'naive-ui'
defineProps<{ show: boolean; title?: string; width?: number; closable?: boolean }>()
const emit = defineEmits<{ close: [] }>()
function onUpdate(v: boolean) { if (!v) emit('close') }
</script>
<template>
  <NModal
    :show="show"
    :auto-focus="false"
    preset="card"
    :title="title"
    :closable="closable ?? true"
    :style="{ maxWidth: (width ?? 620) + 'px', maxHeight: '90vh', overflow: 'auto' }"
    @update:show="onUpdate"
  >
    <div class="app-modal-body"><slot /></div>
    <template v-if="$slots.actions" #footer>
      <div class="actions"><slot name="actions" /></div>
    </template>
  </NModal>
</template>
