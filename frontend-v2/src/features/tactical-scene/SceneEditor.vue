<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TacticalSceneCollection, TacticalSceneTokenDocument, TacticalSceneWallEndpoints } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalEditorCopy } from './copy'
import EditorBoard from './EditorBoard.vue'
import PlaceableInspector from './PlaceableInspector.vue'
import { useSceneEditor, type PlaceableRef } from './useSceneEditor'

const props = defineProps<{ gameKey: string; sceneId: string }>()
const emit = defineEmits<{
  close: []
  saved: []
  deleted: []
  activated: []
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalEditorCopy(locale.value))

const gameKeyRef = computed(() => props.gameKey)
const sceneIdRef = computed(() => props.sceneId)
const editor = useSceneEditor(gameKeyRef, sceneIdRef)

const confirmingDelete = ref(false)
const confirmingClose = ref(false)
let placeableCounter = 0

const sceneName = computed(() => editor.draft.value?.display_name || props.sceneId)
const toolHint = computed(() => {
  if (editor.tool.value === 'pan') return copy.value.panToolHint
  if (editor.tool.value === 'token') return copy.value.tokenToolHint
  if (editor.tool.value === 'wall') return copy.value.wallToolHint
  return copy.value.selectToolHint
})

const COLLECTION_ORDER: TacticalSceneCollection[] = ['tokens', 'walls', 'lights', 'notes', 'tiles', 'sounds', 'drawings', 'templates', 'regions']
const layerRows = computed(() => {
  const draft = editor.draft.value
  if (!draft) return []
  const rows: { ref: PlaceableRef; hidden: boolean; locked: boolean }[] = []
  for (const collection of COLLECTION_ORDER) {
    for (const id of Object.keys(draft.placeables[collection]).reverse()) {
      const hidden = collection === 'tokens' ? draft.placeables.tokens[id]!.hidden
        : collection === 'lights' ? draft.placeables.lights[id]!.hidden : false
      const locked = collection === 'tokens' ? draft.placeables.tokens[id]!.locked : false
      rows.push({ ref: { collection, id }, hidden, locked })
    }
  }
  return rows
})

function nextId(prefix: string): string {
  placeableCounter += 1
  return `${prefix}-${Date.now().toString(36)}-${placeableCounter}`
}

function placeToken(point: { x: number; y: number }) {
  const cell = editor.draft.value && editor.draft.value.grid.size > 0 ? editor.draft.value.grid.size : 100
  const document: TacticalSceneTokenDocument = {
    schema_version: 1,
    kind: 'token',
    canonical_id: nextId('token'),
    actor_ref: null,
    position: { x: point.x - cell / 2, y: point.y - cell / 2, elevation: 0, level: 0 },
    size: { width: 1, height: 1 },
    rotation: 0,
    texture: null,
    ring: {},
    alpha: 1,
    display: {},
    disposition: 0,
    hidden: false,
    locked: false,
    sight: {},
    detection_modes: [],
    light: {},
    bars: {},
    delta: {},
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
  }
  editor.addPlaceable('tokens', document)
}

function drawWall(endpoints: TacticalSceneWallEndpoints) {
  editor.addPlaceable('walls', {
    schema_version: 1,
    kind: 'wall',
    canonical_id: nextId('wall'),
    endpoints,
    direction: 0,
    door: 0,
    door_state: 0,
    restrictions: { move: 0, sight: 0, sound: 0, light: 0 },
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
  })
}

function resizeToken(ref: PlaceableRef, width: number, height: number) {
  editor.applyGesture(scene => {
    const token = scene.placeables.tokens[ref.id]
    if (token) {
      token.size.width = width
      token.size.height = height
    }
  })
}

function rotateToken(ref: PlaceableRef, rotation: number) {
  editor.applyGesture(scene => {
    const token = scene.placeables.tokens[ref.id]
    if (token) token.rotation = rotation
  })
}

function mimeOf(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase() || ''
  if (extension === 'jpg' || extension === 'jpeg') return 'image/jpeg'
  if (extension === 'png') return 'image/png'
  if (extension === 'webp') return 'image/webp'
  if (extension === 'gif') return 'image/gif'
  if (extension === 'svg') return 'image/svg+xml'
  return ''
}

function setBackground(e: Event) {
  const path = (e.target as HTMLInputElement).value.trim()
  editor.mutate(scene => {
    scene.background = path ? { asset_id: path, digest: '', source_path: path, mime: mimeOf(path) } : null
  })
}

function setSceneNumber(field: 'width' | 'height' | 'gridSize', e: Event) {
  const raw = (e.target as HTMLInputElement).value
  const value = Number(raw)
  if (raw.trim() === '' || !Number.isFinite(value)) return
  editor.mutate(scene => {
    if (field === 'gridSize') scene.grid.size = Math.max(0, Math.round(value))
    else scene.dimensions[field] = Math.max(0, Math.round(value))
  })
}

function setGridType(e: Event) {
  const value = (e.target as HTMLSelectElement).value
  editor.mutate(scene => { scene.grid.type = value })
}

function setDisplayName(e: Event) {
  const value = (e.target as HTMLInputElement).value
  editor.mutate(scene => { scene.display_name = value })
}

async function save() {
  if (await editor.save()) emit('saved')
}

async function overwrite() {
  if (await editor.saveOverwrite()) emit('saved')
}

async function activate() {
  if (await editor.activate()) emit('activated')
}

async function confirmDelete() {
  if (await editor.deleteScene()) {
    confirmingDelete.value = false
    emit('deleted')
  }
}

function requestClose() {
  if (editor.dirty.value) {
    confirmingClose.value = true
    return
  }
  emit('close')
}

watch(() => props.sceneId, () => {
  confirmingDelete.value = false
  confirmingClose.value = false
  void editor.load()
}, { immediate: true })
</script>

<template>
  <section class="tactical-board tactical-scene-editor" data-testid="scene-editor">
    <header class="tactical-board-toolbar editor-toolbar">
      <h2>{{ copy.editorTitle }} · {{ sceneName }}</h2>
      <span class="tactical-board-hint" data-testid="editor-gm-hint">{{ copy.gmOnly }}</span>
      <div class="editor-tools" role="toolbar" :aria-label="copy.editorTitle">
        <button type="button" :aria-pressed="editor.tool.value === 'select'" :class="{ active: editor.tool.value === 'select' }" data-testid="editor-tool-select" @click="editor.tool.value = 'select'">{{ copy.toolSelect }}</button>
        <button type="button" :aria-pressed="editor.tool.value === 'pan'" :class="{ active: editor.tool.value === 'pan' }" data-testid="editor-tool-pan" @click="editor.tool.value = 'pan'">{{ copy.toolPan }}</button>
        <button type="button" :aria-pressed="editor.tool.value === 'token'" :class="{ active: editor.tool.value === 'token' }" data-testid="editor-tool-token" @click="editor.tool.value = 'token'">{{ copy.toolToken }}</button>
        <button type="button" :aria-pressed="editor.tool.value === 'wall'" :class="{ active: editor.tool.value === 'wall' }" data-testid="editor-tool-wall" @click="editor.tool.value = 'wall'">{{ copy.toolWall }}</button>
      </div>
      <div class="tactical-board-controls">
        <button type="button" :disabled="!editor.canUndo.value" data-testid="editor-undo" @click="editor.undo()">{{ copy.undo }}</button>
        <button type="button" :disabled="!editor.dirty.value" data-testid="editor-discard" @click="editor.discard()">{{ copy.discard }}</button>
        <button type="button" :disabled="editor.saving.value" data-testid="editor-activate" @click="activate()">{{ copy.activate }}</button>
        <button type="button" class="editor-danger" data-testid="editor-delete-scene" @click="confirmingDelete = true">{{ copy.deleteScene }}</button>
        <button type="button" data-testid="editor-close" @click="requestClose()">{{ copy.close }}</button>
      </div>
    </header>

    <p class="editor-tool-hint">{{ toolHint }}</p>

    <div v-if="confirmingDelete" class="editor-confirm" role="alert" data-testid="editor-delete-confirm">
      <span>{{ copy.deleteConfirm }}</span>
      <button type="button" class="editor-danger" data-testid="editor-delete-confirm-yes" @click="confirmDelete()">{{ copy.confirmDelete }}</button>
      <button type="button" data-testid="editor-delete-confirm-no" @click="confirmingDelete = false">{{ copy.cancel }}</button>
    </div>
    <div v-if="confirmingClose" class="editor-confirm" role="alert" data-testid="editor-close-confirm">
      <span>{{ copy.unsaved }}</span>
      <button type="button" data-testid="editor-close-confirm-yes" @click="emit('close')">{{ copy.discard }}</button>
      <button type="button" data-testid="editor-close-confirm-no" @click="confirmingClose = false">{{ copy.cancel }}</button>
    </div>

    <div v-if="editor.conflict.value" class="editor-conflict" role="alert" data-testid="editor-conflict">
      <strong>{{ copy.conflictTitle }}</strong>
      <p>{{ copy.conflictHint }}</p>
      <button type="button" data-testid="editor-conflict-reload" @click="editor.load()">{{ copy.conflictReload }}</button>
      <button type="button" data-testid="editor-conflict-overwrite" @click="overwrite()">{{ copy.conflictOverwrite }}</button>
    </div>
    <p v-else-if="editor.saveError.value" class="tactical-scene-error editor-save-error" role="alert" data-testid="editor-save-error">
      {{ editor.saveError.value }}
      <button type="button" data-testid="editor-save-retry" @click="save()">{{ copy.retry }}</button>
    </p>

    <p v-if="editor.loadError.value" class="tactical-scene-error" role="alert" data-testid="editor-load-error">
      {{ editor.loadError.value }}
      <button type="button" @click="editor.load()">{{ copy.retry }}</button>
    </p>
    <section v-else-if="editor.loading.value" class="tactical-scene-state" role="status">{{ copy.loading }}</section>

    <div v-else-if="editor.draft.value" class="editor-body">
      <EditorBoard
        :scene="editor.draft.value"
        :tool="editor.tool.value"
        :selection="editor.selection.value"
        @select="(ref, additive) => editor.select(ref, additive)"
        @select-many="(refs, additive) => editor.selectMany(refs, additive)"
        @clear-selection="editor.clearSelection()"
        @place-token="placeToken"
        @draw-wall="drawWall"
        @gesture-begin="editor.beginGesture()"
        @gesture-move="(refs, dx, dy) => editor.movePlaceables(refs, dx, dy)"
        @gesture-resize="resizeToken"
        @gesture-rotate="rotateToken"
      />
      <aside class="editor-sidebar">
        <div class="editor-save-bar">
          <span
            class="editor-status"
            :class="{ dirty: editor.dirty.value }"
            data-testid="editor-status"
          >{{ editor.saving.value ? copy.saving : editor.dirty.value ? copy.unsaved : editor.savedAt.value ? copy.saved : copy.clean }}</span>
          <button
            type="button"
            class="editor-save"
            :disabled="!editor.dirty.value || editor.saving.value"
            data-testid="editor-save"
            @click="save()"
          >{{ editor.saving.value ? copy.saving : copy.save }}</button>
        </div>

        <section class="editor-panel" data-testid="editor-settings">
          <h3>{{ copy.settings }}</h3>
          <label class="editor-field-block">{{ copy.displayName }}
            <input type="text" :value="editor.draft.value.display_name" data-testid="editor-scene-name" @change="setDisplayName($event)">
          </label>
          <label class="editor-field-block">{{ copy.backgroundPath }}
            <input type="text" :value="editor.draft.value.background?.source_path || ''" :placeholder="copy.backgroundHint" data-testid="editor-background-path" @change="setBackground($event)">
          </label>
          <button type="button" :disabled="!editor.draft.value.background" data-testid="editor-clear-background" @click="editor.mutate(scene => { scene.background = null })">{{ copy.clearBackground }}</button>
          <div class="editor-field-grid">
            <label>{{ copy.width }}<input type="number" min="0" step="100" :value="editor.draft.value.dimensions.width" data-testid="editor-scene-width" @change="setSceneNumber('width', $event)"></label>
            <label>{{ copy.height }}<input type="number" min="0" step="100" :value="editor.draft.value.dimensions.height" data-testid="editor-scene-height" @change="setSceneNumber('height', $event)"></label>
            <label>{{ copy.gridType }}
              <select :value="editor.draft.value.grid.type" data-testid="editor-grid-type" @change="setGridType($event)">
                <option value="square">{{ copy.gridSquare }}</option>
                <option value="gridless">{{ copy.gridless }}</option>
                <option value="hex">{{ copy.gridHex }}</option>
              </select>
            </label>
            <label>{{ copy.gridSize }}<input type="number" min="0" step="10" :value="editor.draft.value.grid.size" data-testid="editor-grid-size" @change="setSceneNumber('gridSize', $event)"></label>
          </div>
        </section>

        <section class="editor-panel" data-testid="editor-layers">
          <h3>{{ copy.layers }}</h3>
          <p v-if="!layerRows.length" class="editor-muted">{{ copy.emptyLayers }}</p>
          <ul v-else class="editor-layer-list">
            <li v-for="row in layerRows" :key="`${row.ref.collection}:${row.ref.id}`" :class="{ selected: editor.isSelected(row.ref) }">
              <button type="button" class="editor-layer-select" data-testid="editor-layer-item" :data-id="row.ref.id" @click="editor.select(row.ref, $event.shiftKey)">
                <span class="editor-layer-collection">{{ copy.collections[row.ref.collection] }}</span>
                <span class="editor-layer-id">{{ row.ref.id }}</span>
                <span v-if="row.hidden" class="editor-badge">{{ copy.hiddenBadge }}</span>
                <span v-if="row.locked" class="editor-badge">{{ copy.lockedBadge }}</span>
              </button>
              <span class="editor-layer-order">
                <button type="button" :aria-label="copy.layerUp" :title="copy.layerUp" data-testid="editor-layer-up" @click="editor.reorderPlaceable(row.ref, 'forward')">↑</button>
                <button type="button" :aria-label="copy.layerDown" :title="copy.layerDown" data-testid="editor-layer-down" @click="editor.reorderPlaceable(row.ref, 'backward')">↓</button>
              </span>
            </li>
          </ul>
        </section>

        <PlaceableInspector
          :scene="editor.draft.value"
          :selection="editor.selection.value"
          @set-field="(ref, field, value) => editor.setPlaceableField(ref, field, value)"
          @remove="refs => editor.removePlaceables(refs)"
        />
      </aside>
    </div>
  </section>
</template>