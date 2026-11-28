import { computed, ref, type Ref } from 'vue'
import { ApiError, errorMessage } from '@/api/client'
import {
  activateTacticalScene,
  deleteTacticalScene,
  fetchTacticalScene,
  updateTacticalScene,
  type TacticalSceneCollection,
  type TacticalSceneDocument,
  type TacticalScenePlaceableDocument,
  type TacticalSceneProjection,
  type TacticalSceneWriteResponse,
} from '@/api/tacticalScene'

// GM scene editor state (frontend-03). The draft is a local copy of the last
// authoritative GM document; nothing is written until an explicit save, which
// replays the whole draft through tactical_scene_update with expected_revision.
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §6.8 / §10.3 F2.

export type EditorTool = 'select' | 'pan' | 'token' | 'wall'

export interface PlaceableRef {
  collection: TacticalSceneCollection
  id: string
}

const UNDO_LIMIT = 50
const STALE_CODE = 'tactical_scene_stale_revision'

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function clampInt(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, Math.round(value)))
}

// The editor needs the full GM document; a seat projection (no ownership) must
// never become an editable draft. The server enforces GM-only writes regardless.
export function isGmSceneDocument(scene: TacticalSceneProjection): scene is TacticalSceneDocument {
  return 'ownership' in scene && 'placeables' in scene
}

export function samePlaceableRef(a: PlaceableRef, b: PlaceableRef): boolean {
  return a.collection === b.collection && a.id === b.id
}

export function useSceneEditor(gameKey: Ref<string>, sceneId: Ref<string>) {
  const base = ref<TacticalSceneDocument | null>(null)
  const draft = ref<TacticalSceneDocument | null>(null)
  const baseRevision = ref(0)
  const loading = ref(false)
  const loadError = ref('')
  const saving = ref(false)
  const saveError = ref('')
  const saveErrorCode = ref('')
  const conflict = ref(false)
  const savedAt = ref(0)
  const selection = ref<PlaceableRef[]>([])
  const tool = ref<EditorTool>('select')
  const undoStack = ref<TacticalSceneDocument[]>([])

  const dirty = computed(() =>
    !!draft.value && !!base.value && JSON.stringify(draft.value) !== JSON.stringify(base.value))
  const canUndo = computed(() => undoStack.value.length > 0)

  function placeableAt(ref: PlaceableRef): TacticalScenePlaceableDocument | null {
    const entries = draft.value?.placeables[ref.collection]
    const found = entries?.[ref.id]
    return found ?? null
  }

  function pruneSelection() {
    selection.value = selection.value.filter(ref => placeableAt(ref) !== null)
  }

  function resetDraft(scene: TacticalSceneDocument) {
    base.value = scene
    draft.value = clone(scene)
    undoStack.value = []
    conflict.value = false
    saveError.value = ''
    saveErrorCode.value = ''
    savedAt.value = 0
    pruneSelection()
  }

  async function load() {
    const key = gameKey.value
    const id = sceneId.value
    if (!key || !id) return
    loading.value = true
    loadError.value = ''
    try {
      const response = await fetchTacticalScene(key, id)
      if (!isGmSceneDocument(response.scene)) {
        loadError.value = errorMessage(new ApiError('', 403, 'tactical_scene_forbidden'))
        base.value = null
        draft.value = null
        return
      }
      baseRevision.value = response.revision
      resetDraft(response.scene)
    } catch (cause) {
      loadError.value = errorMessage(cause)
      base.value = null
      draft.value = null
    } finally {
      loading.value = false
    }
  }

  // ---- draft mutations (never touch the backend) --------------------------

  function pushUndo() {
    if (!draft.value) return
    undoStack.value.push(clone(draft.value))
    if (undoStack.value.length > UNDO_LIMIT) undoStack.value.shift()
  }

  function mutate(change: (scene: TacticalSceneDocument) => void) {
    if (!draft.value) return
    pushUndo()
    change(draft.value)
  }

  // A drag pushes one undo snapshot when it starts moving, then applies
  // incremental changes without growing the stack on every pointer event.
  function beginGesture() {
    pushUndo()
  }

  function applyGesture(change: (scene: TacticalSceneDocument) => void) {
    if (draft.value) change(draft.value)
  }

  function undo() {
    const previous = undoStack.value.pop()
    if (previous) {
      draft.value = previous
      pruneSelection()
    }
  }

  function discard() {
    if (base.value) resetDraft(base.value)
  }

  function isSelected(ref: PlaceableRef): boolean {
    return selection.value.some(item => samePlaceableRef(item, ref))
  }

  function select(ref: PlaceableRef, additive = false) {
    if (additive) {
      selection.value = isSelected(ref)
        ? selection.value.filter(item => !samePlaceableRef(item, ref))
        : [...selection.value, ref]
      return
    }
    selection.value = [ref]
  }

  function selectMany(refs: PlaceableRef[], additive = false) {
    if (!additive) {
      selection.value = refs
      return
    }
    const merged = [...selection.value]
    for (const ref of refs) {
      if (!merged.some(item => samePlaceableRef(item, ref))) merged.push(ref)
    }
    selection.value = merged
  }

  function clearSelection() {
    selection.value = []
  }

  function addPlaceable(collection: TacticalSceneCollection, document: TacticalScenePlaceableDocument) {
    mutate(scene => {
      scene.placeables[collection][document.canonical_id] = document
    })
    selection.value = [{ collection, id: document.canonical_id }]
  }

  // Inspector field writes. Values are clamped to the typed contract
  // (src/tactical_scene/contract.py) so a draft can never fail its own save on
  // a field the editor controls; opaque collections stay uninterpreted.
  function setPlaceableField(ref: PlaceableRef, field: string, value: number | string | boolean) {
    mutate(scene => {
      const number = typeof value === 'number' && Number.isFinite(value) ? value : null
      if (ref.collection === 'tokens') {
        const token = scene.placeables.tokens[ref.id]
        if (!token) return
        if (field === 'x' && number !== null) token.position.x = number
        else if (field === 'y' && number !== null) token.position.y = number
        else if (field === 'width' && number !== null) token.size.width = Math.max(0.25, number)
        else if (field === 'height' && number !== null) token.size.height = Math.max(0.25, number)
        else if (field === 'rotation' && number !== null) token.rotation = number
        else if (field === 'disposition' && number !== null) token.disposition = Math.round(number)
        else if (field === 'hidden' && typeof value === 'boolean') token.hidden = value
        else if (field === 'locked' && typeof value === 'boolean') token.locked = value
        return
      }
      if (ref.collection === 'walls') {
        const wall = scene.placeables.walls[ref.id]
        if (!wall) return
        if (field === 'x0' && number !== null) wall.endpoints.x0 = number
        else if (field === 'y0' && number !== null) wall.endpoints.y0 = number
        else if (field === 'x1' && number !== null) wall.endpoints.x1 = number
        else if (field === 'y1' && number !== null) wall.endpoints.y1 = number
        else if (field === 'direction' && number !== null) wall.direction = clampInt(number, 0, 2)
        else if (field === 'door' && number !== null) wall.door = clampInt(number, 0, 2)
        else if (field === 'door_state' && number !== null) wall.door_state = clampInt(number, 0, 2)
        return
      }
      if (ref.collection === 'lights') {
        const light = scene.placeables.lights[ref.id]
        if (!light) return
        if (field === 'x' && number !== null) light.position.x = number
        else if (field === 'y' && number !== null) light.position.y = number
        else if (field === 'dim' && number !== null) light.radius.dim = Math.max(0, number)
        else if (field === 'bright' && number !== null) light.radius.bright = Math.max(0, number)
        else if (field === 'intensity' && number !== null) light.intensity = Math.min(1, Math.max(0, number))
        else if (field === 'angle' && number !== null) light.angle = Math.min(360, Math.max(0, number))
        else if (field === 'color' && typeof value === 'string' && (value === '' || /^#[0-9a-fA-F]{6}$/u.test(value))) light.color = value
        else if (field === 'hidden' && typeof value === 'boolean') light.hidden = value
        return
      }
      if (ref.collection === 'notes') {
        const note = scene.placeables.notes[ref.id]
        if (!note) return
        if (field === 'x' && number !== null) note.position.x = number
        else if (field === 'y' && number !== null) note.position.y = number
        else if (field === 'text' && typeof value === 'string') note.text = value
      }
    })
  }

  function removePlaceables(refs: PlaceableRef[]) {
    if (!refs.length) return
    mutate(scene => {
      for (const ref of refs) delete scene.placeables[ref.collection][ref.id]
    })
    pruneSelection()
  }

  function movePlaceables(refs: PlaceableRef[], dx: number, dy: number) {
    applyGesture(scene => {
      for (const ref of refs) {
        // Typed lookups per collection: opaque collections keep their
        // type-specific fields uninterpreted and are never moved.
        if (ref.collection === 'tokens') {
          const token = scene.placeables.tokens[ref.id]
          if (token && !token.locked) {
            token.position.x += dx
            token.position.y += dy
          }
        } else if (ref.collection === 'walls') {
          const wall = scene.placeables.walls[ref.id]
          if (wall) {
            wall.endpoints.x0 += dx
            wall.endpoints.y0 += dy
            wall.endpoints.x1 += dx
            wall.endpoints.y1 += dy
          }
        } else if (ref.collection === 'lights') {
          const light = scene.placeables.lights[ref.id]
          if (light) {
            light.position.x += dx
            light.position.y += dy
          }
        } else if (ref.collection === 'notes') {
          const note = scene.placeables.notes[ref.id]
          if (note) {
            note.position.x += dx
            note.position.y += dy
          }
        }
      }
    })
  }

  // Draw order within a collection is the dict key order (renderers iterate
  // insertion order); "forward" moves toward the end, i.e. on top. No new
  // z-order field is invented.
  function reorderPlaceable(ref: PlaceableRef, direction: 'forward' | 'backward') {
    mutate(scene => {
      const entries = scene.placeables[ref.collection] as Record<string, TacticalScenePlaceableDocument>
      const keys = Object.keys(entries)
      const index = keys.indexOf(ref.id)
      const target = direction === 'forward' ? index + 1 : index - 1
      if (index < 0 || target < 0 || target >= keys.length) return
      const ordered = keys.map(key => ({ key, value: entries[key]! }))
      const [moved] = ordered.splice(index, 1)
      ordered.splice(target, 0, moved!)
      for (const key of keys) delete entries[key]
      for (const item of ordered) entries[item.key] = item.value
    })
  }

  // ---- save / conflict ----------------------------------------------------

  function applySaved(response: TacticalSceneWriteResponse) {
    baseRevision.value = response.revision
    if (response.scene) resetDraft(response.scene)
    else {
      conflict.value = false
      saveError.value = ''
      saveErrorCode.value = ''
    }
    savedAt.value = Date.now()
  }

  function recordSaveFailure(cause: unknown) {
    const code = cause instanceof ApiError ? cause.code || '' : ''
    saveErrorCode.value = code
    saveError.value = errorMessage(cause)
    conflict.value = code === STALE_CODE
  }

  async function save(): Promise<boolean> {
    const key = gameKey.value
    const id = sceneId.value
    if (!draft.value || !key || !id || saving.value) return false
    saving.value = true
    saveError.value = ''
    saveErrorCode.value = ''
    try {
      const response = await updateTacticalScene(key, id, draft.value, baseRevision.value)
      applySaved(response)
      return true
    } catch (cause) {
      recordSaveFailure(cause)
      return false
    } finally {
      saving.value = false
    }
  }

  // Explicit overwrite after a conflict: re-read the slot revision, keep the
  // draft and save it wholesale. Never silent — the user chose this action.
  async function saveOverwrite(): Promise<boolean> {
    const key = gameKey.value
    const id = sceneId.value
    if (!draft.value || !key || !id || saving.value) return false
    saving.value = true
    saveError.value = ''
    saveErrorCode.value = ''
    try {
      const fresh = await fetchTacticalScene(key, id)
      baseRevision.value = fresh.revision
      if (isGmSceneDocument(fresh.scene)) base.value = fresh.scene
      conflict.value = false
      const response = await updateTacticalScene(key, id, draft.value, baseRevision.value)
      applySaved(response)
      return true
    } catch (cause) {
      recordSaveFailure(cause)
      return false
    } finally {
      saving.value = false
    }
  }

  async function activate(): Promise<boolean> {
    const key = gameKey.value
    const id = sceneId.value
    if (!key || !id || saving.value) return false
    saving.value = true
    saveError.value = ''
    saveErrorCode.value = ''
    try {
      const response = await activateTacticalScene(key, id, baseRevision.value)
      baseRevision.value = response.revision
      conflict.value = false
      savedAt.value = Date.now()
      return true
    } catch (cause) {
      recordSaveFailure(cause)
      return false
    } finally {
      saving.value = false
    }
  }

  async function deleteScene(): Promise<boolean> {
    const key = gameKey.value
    const id = sceneId.value
    if (!key || !id || saving.value) return false
    saving.value = true
    saveError.value = ''
    saveErrorCode.value = ''
    try {
      await deleteTacticalScene(key, id, baseRevision.value)
      return true
    } catch (cause) {
      recordSaveFailure(cause)
      return false
    } finally {
      saving.value = false
    }
  }

  return {
    base,
    draft,
    baseRevision,
    loading,
    loadError,
    saving,
    saveError,
    saveErrorCode,
    conflict,
    savedAt,
    selection,
    tool,
    dirty,
    canUndo,
    load,
    mutate,
    beginGesture,
    applyGesture,
    undo,
    discard,
    isSelected,
    select,
    selectMany,
    clearSelection,
    placeableAt,
    addPlaceable,
    setPlaceableField,
    removePlaceables,
    movePlaceables,
    reorderPlaceable,
    save,
    saveOverwrite,
    activate,
    deleteScene,
  }
}