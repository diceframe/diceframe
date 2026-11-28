<script setup lang="ts">
import { computed } from 'vue'
import type { TacticalSceneDocument } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalEditorCopy } from './copy'
import type { PlaceableRef } from './useSceneEditor'

const props = defineProps<{
  scene: TacticalSceneDocument
  selection: PlaceableRef[]
}>()

const emit = defineEmits<{
  'set-field': [ref: PlaceableRef, field: string, value: number | string | boolean]
  remove: [refs: PlaceableRef[]]
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalEditorCopy(locale.value))

const single = computed<PlaceableRef | null>(() => (props.selection.length === 1 ? props.selection[0]! : null))
const token = computed(() => (single.value?.collection === 'tokens' ? props.scene.placeables.tokens[single.value.id] ?? null : null))
const wall = computed(() => (single.value?.collection === 'walls' ? props.scene.placeables.walls[single.value.id] ?? null : null))
const light = computed(() => (single.value?.collection === 'lights' ? props.scene.placeables.lights[single.value.id] ?? null : null))
const note = computed(() => (single.value?.collection === 'notes' ? props.scene.placeables.notes[single.value.id] ?? null : null))
const opaque = computed(() => !!single.value && !token.value && !wall.value && !light.value && !note.value)

function setNumber(field: string, e: Event) {
  const raw = (e.target as HTMLInputElement | HTMLSelectElement).value
  if (raw.trim() === '' || !single.value) return
  const value = Number(raw)
  if (Number.isFinite(value)) emit('set-field', single.value, field, value)
}

function setChecked(field: string, e: Event) {
  if (single.value) emit('set-field', single.value, field, (e.target as HTMLInputElement).checked)
}

function setText(field: string, e: Event) {
  if (single.value) emit('set-field', single.value, field, (e.target as HTMLInputElement | HTMLTextAreaElement).value)
}
</script>

<template>
  <section class="editor-panel editor-inspector" data-testid="editor-inspector">
    <h3>{{ copy.inspector }}</h3>
    <p v-if="!selection.length" class="editor-muted">{{ copy.noSelection }}</p>
    <template v-else-if="selection.length > 1">
      <p data-testid="editor-multi-count">{{ selection.length }} {{ copy.multiSelected }}</p>
      <button type="button" class="editor-danger" data-testid="editor-delete-selection" @click="emit('remove', selection)">{{ copy.deleteSelected }}</button>
    </template>
    <template v-else-if="single">
      <p class="editor-muted editor-inspector-id">
        {{ copy.collections[single.collection] }} · <code>{{ single.id }}</code>
      </p>

      <template v-if="token">
        <div class="editor-field-grid">
          <label>{{ copy.positionX }}<input type="number" step="1" :value="token.position.x" data-testid="editor-token-x" @change="setNumber('x', $event)"></label>
          <label>{{ copy.positionY }}<input type="number" step="1" :value="token.position.y" data-testid="editor-token-y" @change="setNumber('y', $event)"></label>
          <label>{{ copy.tokenWidth }}<input type="number" step="0.5" min="0.25" :value="token.size.width" data-testid="editor-token-width" @change="setNumber('width', $event)"></label>
          <label>{{ copy.tokenHeight }}<input type="number" step="0.5" min="0.25" :value="token.size.height" data-testid="editor-token-height" @change="setNumber('height', $event)"></label>
          <label>{{ copy.rotation }}<input type="number" step="5" :value="token.rotation" data-testid="editor-token-rotation" @change="setNumber('rotation', $event)"></label>
          <label>{{ copy.disposition }}
            <select :value="token.disposition" data-testid="editor-token-disposition" @change="setNumber('disposition', $event)">
              <option :value="-2">{{ copy.dispositionSecret }}</option>
              <option :value="-1">{{ copy.dispositionHostile }}</option>
              <option :value="0">{{ copy.dispositionNeutral }}</option>
              <option :value="1">{{ copy.dispositionFriendly }}</option>
            </select>
          </label>
        </div>
        <label class="editor-check"><input type="checkbox" :checked="token.hidden" data-testid="editor-token-hidden" @change="setChecked('hidden', $event)"> {{ copy.hidden }}</label>
        <label class="editor-check"><input type="checkbox" :checked="token.locked" data-testid="editor-token-locked" @change="setChecked('locked', $event)"> {{ copy.locked }}</label>
      </template>

      <template v-else-if="wall">
        <div class="editor-field-grid">
          <label>{{ copy.wallEndpoints }} x0<input type="number" step="1" :value="wall.endpoints.x0" data-testid="editor-wall-x0" @change="setNumber('x0', $event)"></label>
          <label>{{ copy.wallEndpoints }} y0<input type="number" step="1" :value="wall.endpoints.y0" data-testid="editor-wall-y0" @change="setNumber('y0', $event)"></label>
          <label>{{ copy.wallEndpoints }} x1<input type="number" step="1" :value="wall.endpoints.x1" data-testid="editor-wall-x1" @change="setNumber('x1', $event)"></label>
          <label>{{ copy.wallEndpoints }} y1<input type="number" step="1" :value="wall.endpoints.y1" data-testid="editor-wall-y1" @change="setNumber('y1', $event)"></label>
          <label>{{ copy.direction }}
            <select :value="wall.direction" data-testid="editor-wall-direction" @change="setNumber('direction', $event)">
              <option :value="0">0</option><option :value="1">1</option><option :value="2">2</option>
            </select>
          </label>
          <label>{{ copy.door }}
            <select :value="wall.door" data-testid="editor-wall-door" @change="setNumber('door', $event)">
              <option :value="0">0</option><option :value="1">1</option><option :value="2">2</option>
            </select>
          </label>
          <label>{{ copy.doorState }}
            <select :value="wall.door_state" data-testid="editor-wall-door-state" @change="setNumber('door_state', $event)">
              <option :value="0">0</option><option :value="1">1</option><option :value="2">2</option>
            </select>
          </label>
        </div>
      </template>

      <template v-else-if="light">
        <div class="editor-field-grid">
          <label>{{ copy.positionX }}<input type="number" step="1" :value="light.position.x" data-testid="editor-light-x" @change="setNumber('x', $event)"></label>
          <label>{{ copy.positionY }}<input type="number" step="1" :value="light.position.y" data-testid="editor-light-y" @change="setNumber('y', $event)"></label>
          <label>{{ copy.lightRadiusDim }}<input type="number" step="5" min="0" :value="light.radius.dim" data-testid="editor-light-dim" @change="setNumber('dim', $event)"></label>
          <label>{{ copy.lightRadiusBright }}<input type="number" step="5" min="0" :value="light.radius.bright" data-testid="editor-light-bright" @change="setNumber('bright', $event)"></label>
          <label>{{ copy.intensity }}<input type="number" step="0.1" min="0" max="1" :value="light.intensity" data-testid="editor-light-intensity" @change="setNumber('intensity', $event)"></label>
          <label>{{ copy.angle }}<input type="number" step="5" min="0" max="360" :value="light.angle" data-testid="editor-light-angle" @change="setNumber('angle', $event)"></label>
          <label>{{ copy.color }}<input type="text" :value="light.color" placeholder="#rrggbb" data-testid="editor-light-color" @change="setText('color', $event)"></label>
        </div>
        <label class="editor-check"><input type="checkbox" :checked="light.hidden" data-testid="editor-light-hidden" @change="setChecked('hidden', $event)"> {{ copy.hidden }}</label>
      </template>

      <template v-else-if="note">
        <div class="editor-field-grid">
          <label>{{ copy.positionX }}<input type="number" step="1" :value="note.position.x" data-testid="editor-note-x" @change="setNumber('x', $event)"></label>
          <label>{{ copy.positionY }}<input type="number" step="1" :value="note.position.y" data-testid="editor-note-y" @change="setNumber('y', $event)"></label>
        </div>
        <label class="editor-field-block">{{ copy.noteText }}<textarea rows="3" :value="note.text" data-testid="editor-note-text" @change="setText('text', $event)" /></label>
      </template>

      <p v-else-if="opaque" class="editor-muted">{{ copy.opaqueHint }}</p>

      <button type="button" class="editor-danger" data-testid="editor-delete-selection" @click="emit('remove', [single])">{{ copy.deleteSelected }}</button>
    </template>
  </section>
</template>