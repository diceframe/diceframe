import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type { TacticalSceneDocument, TacticalSceneWriteResponse } from '../src/api/tacticalScene'
import SceneEditor from '../src/features/tactical-scene/SceneEditor.vue'

function gmScene(): TacticalSceneDocument {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: 'scene-a',
    display_name: 'Chapel',
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
    revision: 1,
  }
}

function readResponse(scene: TacticalSceneDocument, revision: number) {
  return { ok: true as const, scene_id: scene.canonical_id, scene, active_scene_ref: null, revision }
}

function writeResponse(scene: TacticalSceneDocument | null, revision: number): TacticalSceneWriteResponse {
  return { ok: true, operation: 'tactical_scene_update', scene_id: 'scene-a', revision, active_scene_ref: null, scene }
}

interface PatchCall { scene: TacticalSceneDocument; expected: number }

function mockApi(options: { staleOnce?: boolean } = {}) {
  const patches: PatchCall[] = []
  let stale = options.staleOnce ?? false
  const apiMock = vi.fn((path: string, init?: RequestInit) => {
    if (!init?.method) return Promise.resolve(readResponse(gmScene(), 5))
    if (init.method === 'PATCH') {
      const body = JSON.parse(String(init.body)) as { scene: TacticalSceneDocument; expected_revision: number }
      patches.push({ scene: body.scene, expected: body.expected_revision })
      if (stale) {
        stale = false
        return Promise.reject(new ApiError('raw', 409, 'tactical_scene_stale_revision'))
      }
      return Promise.resolve(writeResponse(body.scene, body.expected_revision + 1))
    }
    return Promise.resolve(writeResponse(gmScene(), 6))
  })
  vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
  return { apiMock, patches }
}

async function mountEditor(options: { staleOnce?: boolean } = {}) {
  i18n.global.locale.value = 'en'
  const mock = mockApi(options)
  const wrapper = mount(SceneEditor, {
    props: { gameKey: 'g', sceneId: 'scene-a' },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  const svg = wrapper.get('[data-testid="editor-board-svg"]')
  const element = svg.element as SVGSVGElement
  element.getBoundingClientRect = () => ({
    x: 0, y: 0, left: 0, top: 0, right: 900, bottom: 675, width: 900, height: 675, toJSON: () => ({}),
  } as DOMRect)
  return { wrapper, svg, ...mock }
}

function pointer(element: Element, type: string, clientX: number, clientY: number, extra: PointerEventInit = {}) {
  element.dispatchEvent(new PointerEvent(type, { pointerId: 1, clientX, clientY, bubbles: true, cancelable: true, ...extra }))
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
})

describe('SceneEditor M1 editing flow', () => {
  it('loads the GM document and shows the GM-only hint', async () => {
    const { wrapper } = await mountEditor()
    expect(wrapper.get('[data-testid="editor-gm-hint"]').text()).toContain('GM only')
    expect(wrapper.get('[data-testid="editor-status"]').text()).toBe('No changes')
    expect(wrapper.get('[data-testid="editor-save"]').attributes('disabled')).toBeDefined()
  })

  it('places a token by clicking with the token tool and marks the draft dirty', async () => {
    const { wrapper, svg } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    const tokens = wrapper.findAll('[data-testid="editor-token"]')
    expect(tokens).toHaveLength(1)
    expect(wrapper.get('[data-testid="editor-status"]').text()).toBe('Unsaved changes')
    expect(wrapper.get('[data-testid="editor-save"]').attributes('disabled')).toBeUndefined()
    // The token is centered on the click: scene center (700,500) minus half a cell.
    const transform = tokens[0].attributes('transform')!
    expect(transform).toContain('translate(650,450)')
  })

  it('draws a wall by dragging with the wall tool', async () => {
    const { wrapper, svg } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-wall"]').trigger('click')
    pointer(svg.element, 'pointerdown', 300, 300)
    pointer(svg.element, 'pointermove', 500, 300)
    await nextTick()
    expect(wrapper.find('[data-testid="editor-wall-preview"]').exists()).toBe(true)
    pointer(svg.element, 'pointerup', 500, 300)
    await nextTick()
    const walls = wrapper.findAll('[data-testid="editor-wall"]')
    expect(walls).toHaveLength(1)
    expect(wrapper.find('[data-testid="editor-wall-preview"]').exists()).toBe(false)
  })

  it('saves the draft through tactical_scene_update with expected_revision and refreshes state', async () => {
    const { wrapper, svg, patches } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    await wrapper.get('[data-testid="editor-tool-wall"]').trigger('click')
    pointer(svg.element, 'pointerdown', 300, 300)
    pointer(svg.element, 'pointermove', 500, 300)
    pointer(svg.element, 'pointerup', 500, 300)
    await nextTick()

    await wrapper.get('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(patches).toHaveLength(1)
    expect(patches[0].expected).toBe(5)
    expect(Object.keys(patches[0].scene.placeables.tokens)).toHaveLength(1)
    expect(Object.keys(patches[0].scene.placeables.walls)).toHaveLength(1)
    expect(wrapper.get('[data-testid="editor-status"]').text()).toBe('Saved')
    expect(wrapper.find('[data-testid="editor-conflict"]').exists()).toBe(false)
  })

  it('undo removes the last draft change without touching the backend', async () => {
    const { wrapper, svg, patches } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    expect(wrapper.findAll('[data-testid="editor-token"]')).toHaveLength(1)
    await wrapper.get('[data-testid="editor-undo"]').trigger('click')
    expect(wrapper.findAll('[data-testid="editor-token"]')).toHaveLength(0)
    expect(patches).toHaveLength(0)
    expect(wrapper.get('[data-testid="editor-status"]').text()).toBe('No changes')
  })

  it('moves a selected token by dragging and resizes it through the handle', async () => {
    const { wrapper, svg } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    await wrapper.get('[data-testid="editor-tool-select"]').trigger('click')

    const token = wrapper.get('[data-testid="editor-token"]')
    pointer(token.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointermove', 550, 337.5)
    pointer(svg.element, 'pointerup', 550, 337.5)
    await nextTick()
    const moved = wrapper.get('[data-testid="editor-token"]').attributes('transform')!
    expect(moved).not.toContain('translate(650,450)')

    // One selected token exposes resize / rotate handles.
    const handle = wrapper.get('[data-testid="editor-resize-handle"]')
    const box = (handle.element as SVGRectElement).getBoundingClientRect
    expect(box).toBeDefined()
    pointer(handle.element, 'pointerdown', 550, 337.5)
    pointer(svg.element, 'pointermove', 650, 437.5)
    pointer(svg.element, 'pointerup', 650, 437.5)
    await nextTick()
    const widthInput = wrapper.get('[data-testid="editor-token-width"]').element as HTMLInputElement
    expect(Number(widthInput.value)).toBeGreaterThan(1)
  })
})

describe('SceneEditor conflict and failure feedback', () => {
  it('shows the conflict banner on stale_revision and reload discards the draft', async () => {
    const { wrapper, svg } = await mountEditor({ staleOnce: true })
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    await wrapper.get('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    const banner = wrapper.get('[data-testid="editor-conflict"]')
    expect(banner.text()).toContain('The scene changed elsewhere')
    expect(wrapper.findAll('[data-testid="editor-token"]')).toHaveLength(1)

    await wrapper.get('[data-testid="editor-conflict-reload"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="editor-conflict"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="editor-token"]')).toHaveLength(0)
    expect(wrapper.get('[data-testid="editor-status"]').text()).toBe('No changes')
  })

  it('explicit overwrite keeps the draft and saves with the fresh revision', async () => {
    const { wrapper, svg, patches } = await mountEditor({ staleOnce: true })
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    await wrapper.get('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="editor-conflict"]').exists()).toBe(true)

    await wrapper.get('[data-testid="editor-conflict-overwrite"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="editor-conflict"]').exists()).toBe(false)
    expect(patches).toHaveLength(2)
    expect(patches[1].expected).toBe(5)
    expect(Object.keys(patches[1].scene.placeables.tokens)).toHaveLength(1)
    expect(wrapper.get('[data-testid="editor-status"]').text()).toBe('Saved')
  })

  it('shows a non-conflict save error with a retry button', async () => {
    i18n.global.locale.value = 'en'
    let fail = true
    const apiMock = vi.fn((path: string, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(readResponse(gmScene(), 5))
      if (fail) return Promise.reject(new ApiError('raw', 500, 'save_failed'))
      const body = JSON.parse(String(init.body)) as { scene: TacticalSceneDocument; expected_revision: number }
      return Promise.resolve(writeResponse(body.scene, 6))
    })
    vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
    const wrapper = mount(SceneEditor, { props: { gameKey: 'g', sceneId: 'scene-a' }, global: { plugins: [i18n] } })
    await flushPromises()
    await wrapper.get('[data-testid="editor-scene-name"]').setValue('Renamed')
    await wrapper.get('[data-testid="editor-scene-name"]').trigger('change')
    await wrapper.get('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="editor-save-error"]').text()).toContain('Saving failed')
    expect(wrapper.find('[data-testid="editor-conflict"]').exists()).toBe(false)
    fail = false
    await wrapper.get('[data-testid="editor-save-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="editor-save-error"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="editor-status"]').text()).toBe('Saved')
  })
})

describe('SceneEditor inspector and layers', () => {
  it('edits the selected token through the inspector and deletes it', async () => {
    const { wrapper, svg } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    // addPlaceable selects the new token; the inspector shows its fields.
    await wrapper.get('[data-testid="editor-token-x"]').setValue('999')
    await wrapper.get('[data-testid="editor-token-x"]').trigger('change')
    expect(wrapper.get('[data-testid="editor-token"]').attributes('transform')).toContain('translate(999,450)')

    await wrapper.get('[data-testid="editor-token-hidden"]').setValue(true)
    expect(wrapper.get('[data-testid="editor-layer-item"]').text()).toContain('Hidden')

    await wrapper.get('[data-testid="editor-delete-selection"]').trigger('click')
    expect(wrapper.findAll('[data-testid="editor-token"]')).toHaveLength(0)
    expect(wrapper.get('[data-testid="editor-inspector"]').text()).toContain('Nothing selected')
  })

  it('reorders layers and multi-selects with shift', async () => {
    const { wrapper, svg } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 300, 300)
    pointer(svg.element, 'pointerup', 300, 300)
    pointer(svg.element, 'pointerdown', 600, 400)
    pointer(svg.element, 'pointerup', 600, 400)
    await nextTick()
    const items = wrapper.findAll('[data-testid="editor-layer-item"]')
    expect(items).toHaveLength(2)
    const firstId = items[0].attributes('data-id')!
    await wrapper.findAll('[data-testid="editor-layer-down"]')[0].trigger('click')
    expect(wrapper.findAll('[data-testid="editor-layer-item"]')[1].attributes('data-id')).toBe(firstId)

    await wrapper.get('[data-testid="editor-tool-select"]').trigger('click')
    const tokens = wrapper.findAll('[data-testid="editor-token"]')
    // After the reorder the first rendered token is the already-selected one;
    // shift-click the other token to add it to the selection.
    pointer(tokens[1].element, 'pointerdown', 300, 300, { shiftKey: true })
    pointer(svg.element, 'pointerup', 300, 300)
    await nextTick()
    expect(wrapper.get('[data-testid="editor-multi-count"]').text()).toContain('2')
  })

  it('protects an unsaved draft when closing', async () => {
    const { wrapper, svg } = await mountEditor()
    await wrapper.get('[data-testid="editor-tool-token"]').trigger('click')
    pointer(svg.element, 'pointerdown', 450, 337.5)
    pointer(svg.element, 'pointerup', 450, 337.5)
    await nextTick()
    await wrapper.get('[data-testid="editor-close"]').trigger('click')
    expect(wrapper.find('[data-testid="editor-close-confirm"]').exists()).toBe(true)
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.get('[data-testid="editor-close-confirm-yes"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})