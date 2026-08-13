<script setup lang="ts">
import { ref, watch } from 'vue'
import type { MapData } from '@/api/types'
import { errorMessage } from '@/api/client'
import { mapBackgroundChoice, updateGameMapBackground } from '@/api/mapBackgrounds'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import MapBackgroundPicker from '@/components/common/MapBackgroundPicker.vue'
import Modal from '@/components/ui/Modal.vue'

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

watch(
  () => props.open,
  (open) => {
    if (!open) return
    choice.value = mapBackgroundChoice(props.map?.background_selection)
    file.value = null
  },
  { immediate: true },
)

function close() {
  if (busy.value) return
  file.value = null
  emit('close')
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
    <template #actions>
      <button :disabled="busy" @click="close">{{ t('cancel') }}</button>
      <button class="primary" :disabled="busy" @click="save">
        {{ busy ? t('saving') : t('saveAction') }}
      </button>
    </template>
  </Modal>
</template>
