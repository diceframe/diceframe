<script setup lang="ts">
import { ref, watch } from 'vue'
import type { MapData } from '@/api/types'
import { errorMessage } from '@/api/client'
import { mapBackgroundChoice, updateGameMapBackground } from '@/api/mapBackgrounds'
import { generateImage } from '@/api/generatedImages'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import MapBackgroundPicker from '@/components/common/MapBackgroundPicker.vue'
import Modal from '@/components/ui/Modal.vue'
import GeneratedImageThumbnail from '@/components/common/GeneratedImageThumbnail.vue'

const props = defineProps<{
  open: boolean
  gameKey: string
  map?: MapData | null
}>()
const emit = defineEmits<{
  close: []
  saved: []
}>()
const { t } = useLocale()
const toast = useToast()
const choice = ref('auto')
const file = ref<File | null>(null)
const busy = ref(false)
const generationPrompt = ref('')
const generatedAssetId = ref('')

watch(
  () => props.open,
  (open) => {
    if (!open) return
    choice.value = mapBackgroundChoice(props.map?.background_selection)
    file.value = null
    generatedAssetId.value = props.map?.background_selection?.kind === 'generated'
      ? String(props.map.background_selection.asset_id || '')
      : ''
  },
  { immediate: true },
)

function close() {
  if (busy.value) return
  file.value = null
  emit('close')
}

async function generateBackground() {
  const prompt = generationPrompt.value.trim()
  if (!prompt || !props.gameKey || busy.value) return
  busy.value = true
  try {
    const result = await generateImage({
      purpose: 'map',
      prompt,
      gameKey: props.gameKey,
      aspectRatio: '16:9',
      context: { target: 'map-background' },
    })
    generatedAssetId.value = result.asset_id
    choice.value = `generated:${result.asset_id}`
    file.value = null
    toast.success(t('mapBackgroundGenerated'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!props.gameKey) return
  busy.value = true
  try {
    await updateGameMapBackground(props.gameKey, choice.value, file.value)
    toast.success(t('mapBackgroundSaved'))
    emit('saved')
    emit('close')
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Modal v-if="open" :title="t('mapBackgroundManage')" @close="close">
    <MapBackgroundPicker
      v-model="choice"
      v-model:file="file"
      :options="map?.background_options || []"
    />
    <div class="image-generation-row">
      <input v-model="generationPrompt" :placeholder="t('mapGenerationPrompt')" @keydown.enter.prevent="generateBackground">
      <button class="primary" :disabled="busy || !generationPrompt.trim()" @click="generateBackground">{{ busy ? t('generatingEllipsis') : t('generateMapBackground') }}</button>
      <GeneratedImageThumbnail v-if="generatedAssetId" :asset-id="generatedAssetId" :alt="generationPrompt" :size="64" />
    </div>
    <template #actions>
      <button :disabled="busy" @click="close">{{ t('cancel') }}</button>
      <button class="primary" :disabled="busy" @click="save">
        {{ busy ? t('saving') : t('saveAction') }}
      </button>
    </template>
  </Modal>
</template>
