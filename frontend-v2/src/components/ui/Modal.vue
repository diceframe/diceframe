<script setup lang="ts">
import { useLocale } from '@/composables/useLocale'

defineProps<{title:string}>()
const emit=defineEmits<{close:[]}>()
const { t } = useLocale()
</script>
<template>
  <Teleport to="body">
    <div class="modal">
      <section class="dialog" style="max-height:90vh;overflow-y:auto">
        <header><h2>{{title}}</h2><button class="modal-x" @click="emit('close')" :title="t('close')">✕</button></header>
        <slot/>
        <div v-if="$slots.actions" class="actions"><slot name="actions"/></div>
      </section>
    </div>
  </Teleport>
</template>
