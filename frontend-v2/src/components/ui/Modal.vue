<script setup lang="ts">
defineProps<{title:string}>()
const emit=defineEmits<{close:[]}>()
</script>
<template>
  <Teleport to="body">
    <div class="modal">
      <section class="dialog" style="max-height:90vh;overflow-y:auto">
        <header><h2>{{title}}</h2><button class="modal-x" @click="emit('close')" title="关闭">✕</button></header>
        <slot/>
        <div v-if="$slots.actions" class="actions"><slot name="actions"/></div>
      </section>
    </div>
  </Teleport>
</template>
