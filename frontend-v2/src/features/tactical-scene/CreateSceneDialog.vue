<script setup lang="ts">
import { computed, ref } from 'vue'
import { errorMessage } from '@/api/client'
import { createTacticalScene, fetchTacticalScenes, type TacticalSceneDocument } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalEditorCopy } from './copy'

const props = defineProps<{ gameKey: string }>()
const emit = defineEmits<{
  created: [sceneId: string]
  close: []
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalEditorCopy(locale.value))

// Same canonical identity grammar as the backend contract
// (src/tactical_scene/contract.py is_canonical_id).
const CANONICAL_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/u

const displayName = ref('')
const canonicalId = ref(`scene-${Date.now().toString(36)}`)
const creating = ref(false)
const error = ref('')

const idValid = computed(() => CANONICAL_ID.test(canonicalId.value))

function blankScene(id: string, name: string): TacticalSceneDocument {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: id,
    display_name: name,
    navigation_name: '',
    source_ref: null,
    external_ids: {},
    ownership: {},
    sort: 0,
    background: null,
    foreground: null,
    thumbnail: null,
    dimensions: { width: 1400, height: 1000, padding: 0.25, offset_x: 0, offset_y: 0, initial_view: { x: 700, y: 500, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
    environment: { darkness: 0, global_illumination: false, fog: { exploration: false, reset_policy: '' }, weather: '' },
    placeables: { tokens: {}, tiles: {}, walls: {}, lights: {}, sounds: {}, notes: {}, drawings: {}, templates: {}, regions: {} },
    levels: [],
    flags: {},
    extensions: {},
    provenance: {},
    revision: 0,
  }
}

async function submit() {
  if (!idValid.value || creating.value) return
  creating.value = true
  error.value = ''
  try {
    // Read the slot revision immediately before creating so the stale guard
    // only trips on a genuine concurrent write.
    const list = await fetchTacticalScenes(props.gameKey)
    const response = await createTacticalScene(props.gameKey, blankScene(canonicalId.value, displayName.value.trim()), list.revision)
    emit('created', response.scene_id)
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="editor-dialog-backdrop" data-testid="create-scene-dialog" @click.self="emit('close')">
    <section class="editor-dialog" role="dialog" :aria-label="copy.createTitle">
      <h2>{{ copy.createTitle }}</h2>
      <p class="editor-muted">{{ copy.createHint }}</p>
      <label class="editor-field-block">{{ copy.nameLabel }}
        <input v-model="displayName" type="text" data-testid="create-scene-name">
      </label>
      <label class="editor-field-block">{{ copy.canonicalId }}
        <input v-model="canonicalId" type="text" data-testid="create-scene-id">
      </label>
      <p class="editor-muted">{{ copy.idHint }}</p>
      <p v-if="!idValid" class="editor-field-error" role="alert" data-testid="create-scene-id-invalid">{{ copy.idInvalid }}</p>
      <p v-if="error" class="editor-field-error" role="alert" data-testid="create-scene-error">{{ error }}</p>
      <div class="editor-dialog-actions">
        <button type="button" :disabled="!idValid || creating" data-testid="create-scene-submit" @click="submit()">{{ creating ? copy.creating : copy.create }}</button>
        <button type="button" data-testid="create-scene-cancel" @click="emit('close')">{{ copy.cancel }}</button>
      </div>
    </section>
  </div>
</template>