<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { apiBlob } from '@/api/client'

const props = defineProps<{ assetId: string; alt?: string }>()
const url = ref('')
const failed = ref(false)

onMounted(async () => {
  try {
    const response = await apiBlob(`/scene-images/${encodeURIComponent(props.assetId)}`)
    url.value = URL.createObjectURL(await response.blob())
  } catch {
    failed.value = true
  }
})

onBeforeUnmount(() => {
  if (url.value.startsWith('blob:')) URL.revokeObjectURL(url.value)
})
</script>

<template>
  <figure v-if="url" class="scene-image-block" data-testid="scene-image">
    <img :src="url" :alt="alt || ''" loading="lazy" />
  </figure>
</template>

<style scoped>
.scene-image-block {
  margin: 10px 0 4px;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--df-border, rgba(255, 255, 255, 0.12));
  background: var(--df-bg-soft, rgba(0, 0, 0, 0.2));
}

.scene-image-block img {
  display: block;
  width: 100%;
  height: auto;
}
</style>
