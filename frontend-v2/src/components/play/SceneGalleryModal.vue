<script setup lang="ts">
import { ref, watch } from 'vue'
import type { SceneGalleryItem } from '@/api/types'
import { errorMessage } from '@/api/client'
import { fetchGeneratedImages, useGeneratedImageAsMapBackground } from '@/api/generatedImages'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import Modal from '@/components/ui/Modal.vue'
import SceneImageBlock from '@/components/play/SceneImageBlock.vue'

const props = defineProps<{
  open: boolean
  gameKey: string
  isGm?: boolean
}>()
const emit = defineEmits<{
  close: []
  backgroundSaved: []
}>()
const { t } = useLocale()
const toast = useToast()
const items = ref<SceneGalleryItem[]>([])
const loading = ref(false)
const error = ref('')
const applyingId = ref('')

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    error.value = ''
    await reload()
  },
  { immediate: true },
)

async function reload() {
  if (!props.gameKey) return
  loading.value = true
  try {
    items.value = await fetchGeneratedImages(props.gameKey, 'scene')
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

async function applyAsBackground(item: SceneGalleryItem) {
  if (!props.gameKey || applyingId.value) return
  applyingId.value = item.asset_id
  try {
    await useGeneratedImageAsMapBackground(props.gameKey, item.asset_id)
    toast.success(t('mapBackgroundSet'))
    emit('backgroundSaved')
  } catch (cause: unknown) {
    toast.error(errorMessage(cause))
  } finally {
    applyingId.value = ''
  }
}
</script>

<template>
  <Modal v-if="open" :title="t('sceneGallery')" @close="emit('close')">
    <p v-if="loading" class="scene-gallery-hint">{{ t('galleryLoading') }}</p>
    <p v-else-if="error" class="scene-gallery-hint scene-gallery-error">{{ error }}</p>
    <p v-else-if="!items.length" class="scene-gallery-hint">{{ t('sceneGalleryEmpty') }}</p>
    <div v-else class="scene-gallery-grid" data-testid="scene-gallery">
      <div v-for="item in items" :key="item.generation_id" class="scene-gallery-item">
        <div v-if="item.round" class="scene-gallery-round">{{ t('roundDivider', { round: item.round }) }}</div>
        <SceneImageBlock :asset-id="item.asset_id" :game-key="gameKey" :alt="item.prompt || ''" />
        <p v-if="item.prompt" class="scene-gallery-prompt">{{ item.prompt }}</p>
        <button
          v-if="isGm"
          class="ghost scene-gallery-apply"
          :disabled="applyingId === item.asset_id"
          @click="applyAsBackground(item)"
        >
          {{ applyingId === item.asset_id ? t('saving') : t('setAsMapBackground') }}
        </button>
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.scene-gallery-hint {
  margin: 0;
  color: var(--df-text-muted, rgba(255, 255, 255, 0.6));
  font-size: 13px;
}

.scene-gallery-error {
  color: var(--df-danger, #e2a1a1);
}

.scene-gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
  max-height: 60vh;
  overflow-y: auto;
  padding: 2px;
}

.scene-gallery-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-radius: 10px;
  border: 1px solid var(--df-border, rgba(255, 255, 255, 0.12));
  padding: 8px;
  background: var(--df-bg-soft, rgba(0, 0, 0, 0.18));
}

.scene-gallery-round {
  font-size: 12px;
  color: var(--df-text-muted, rgba(255, 255, 255, 0.55));
}

.scene-gallery-prompt {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--df-text-muted, rgba(255, 255, 255, 0.6));
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.scene-gallery-apply {
  align-self: flex-start;
  font-size: 12px;
  padding: 4px 10px;
}
</style>
