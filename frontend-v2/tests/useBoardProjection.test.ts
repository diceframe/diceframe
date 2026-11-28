import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import { useBoardProjection } from '../src/features/tactical-scene/useBoardProjection'
import type { TacticalSceneSeatProjection } from '../src/api/tacticalScene'

class FakeEventSource {
  onopen: (() => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  close() { this.closed = true }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent) }
}

function scene(id: string, displayName: string, revision = 1): TacticalSceneSeatProjection {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: id,
    display_name: displayName,
    navigation_name: '',
    sort: 0,
    background: null,
    foreground: null,
    thumbnail: null,
    dimensions: { width: 0, height: 0, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 0, y: 0, zoom: 1 } },
    grid: { type: 'gridless', size: 0, scale: 1, units: '', color: '', alpha: 0, distance: 0 },
    environment: { darkness: 0, global_illumination: false, fog: { exploration: false, reset_policy: '' }, weather: '' },
    levels: [],
    revision,
    placeables: { tokens: {}, walls: {}, lights: {} },
  }
}

function readResponse(projection: TacticalSceneSeatProjection, revision: number) {
  return { ok: true as const, scene_id: projection.canonical_id, scene: projection, active_scene_ref: projection.canonical_id, revision }
}

function updatedEvent(sceneId: string, revision: number, projection: TacticalSceneSeatProjection | null) {
  return {
    event_type: 'tactical_scene_updated',
    run_id: 'run-1',
    scene_id: sceneId,
    revision,
    visibility: 'seat',
    source_operation: 'tactical_scene_update',
    projection,
  }
}

function mountProjection(apiMock: ReturnType<typeof vi.fn>, sources: FakeEventSource[]) {
  vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
  vi.spyOn(client, 'gameEventSource').mockImplementation(async () => {
    const source = new FakeEventSource()
    sources.push(source)
    return source as unknown as EventSource
  })
  const gameKey = ref('one')
  const sceneId = ref('scene-a')
  let state!: ReturnType<typeof useBoardProjection>
  const Host = defineComponent({
    setup() {
      state = useBoardProjection(gameKey, sceneId)
      return () => h('div')
    },
  })
  const wrapper = mount(Host, { global: { plugins: [i18n] } })
  return { wrapper, state, gameKey, sceneId }
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
})

describe('useBoardProjection', () => {
  it('loads the server projection and revision for the selected scene', async () => {
    const apiMock = vi.fn().mockResolvedValue(readResponse(scene('scene-a', 'Chapel'), 3))
    const { state, wrapper } = mountProjection(apiMock, [])
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/games/one/tactical-scenes/scene-a')
    expect(state.scene.value?.display_name).toBe('Chapel')
    expect(state.revision.value).toBe(3)
    expect(state.error.value).toBe('')
    wrapper.unmount()
  })

  it('surfaces a localized error and recovers on retry', async () => {
    let fail = true
    const apiMock = vi.fn().mockImplementation(() => fail
      ? Promise.reject(new ApiError('raw', 404, 'tactical_scene_not_found'))
      : Promise.resolve(readResponse(scene('scene-a', 'Chapel'), 1)))
    const { state, wrapper } = mountProjection(apiMock, [])
    await flushPromises()
    expect(state.scene.value).toBeNull()
    expect(state.error.value).toBe('Game or scene not found')
    fail = false
    state.retry()
    await flushPromises()
    expect(state.error.value).toBe('')
    expect(state.scene.value?.display_name).toBe('Chapel')
    wrapper.unmount()
  })

  it('applies a newer-revision SSE projection directly without re-reading', async () => {
    const apiMock = vi.fn().mockResolvedValue(readResponse(scene('scene-a', 'Chapel', 3), 3))
    const sources: FakeEventSource[] = []
    const { state, wrapper } = mountProjection(apiMock, sources)
    await flushPromises()
    expect(sources).toHaveLength(1)
    sources[0]!.emit(updatedEvent('scene-a', 4, scene('scene-a', 'Chapel v2', 4)))
    await flushPromises()
    expect(state.scene.value?.display_name).toBe('Chapel v2')
    expect(state.revision.value).toBe(4)
    expect(apiMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('ignores stale revisions and events for other scenes', async () => {
    const apiMock = vi.fn().mockResolvedValue(readResponse(scene('scene-a', 'Chapel', 3), 3))
    const sources: FakeEventSource[] = []
    const { state, wrapper } = mountProjection(apiMock, sources)
    await flushPromises()
    sources[0]!.emit(updatedEvent('scene-a', 3, scene('scene-a', 'Stale', 3)))
    sources[0]!.emit(updatedEvent('scene-a', 2, scene('scene-a', 'Older', 2)))
    sources[0]!.emit(updatedEvent('scene-b', 9, scene('scene-b', 'Other', 9)))
    sources[0]!.emit({ event_type: 'something_else' })
    await flushPromises()
    expect(state.scene.value?.display_name).toBe('Chapel')
    expect(state.revision.value).toBe(3)
    wrapper.unmount()
  })

  it('re-reads when the event carries no projection (deleted scene)', async () => {
    const apiMock = vi.fn()
      .mockResolvedValueOnce(readResponse(scene('scene-a', 'Chapel', 3), 3))
      .mockRejectedValueOnce(new ApiError('raw', 404, 'tactical_scene_not_found'))
    const sources: FakeEventSource[] = []
    const { state, wrapper } = mountProjection(apiMock, sources)
    await flushPromises()
    sources[0]!.emit(updatedEvent('scene-a', 4, null))
    await flushPromises()
    expect(apiMock).toHaveBeenCalledTimes(2)
    expect(state.scene.value).toBeNull()
    expect(state.error.value).toBe('Game or scene not found')
    wrapper.unmount()
  })

  it('does not crash when the backend is unreachable', async () => {
    const apiMock = vi.fn().mockRejectedValue(new Error('network down'))
    vi.spyOn(client, 'gameEventSource').mockRejectedValue(new Error('no sse'))
    const { state, wrapper } = mountProjection(apiMock, [])
    await flushPromises()
    expect(state.scene.value).toBeNull()
    expect(state.error.value).toBe('network down')
    wrapper.unmount()
  })

  it('closes the old event source and reloads when the game changes', async () => {
    const apiMock = vi.fn().mockImplementation((path: string) => Promise.resolve(
      path.includes('/one/') ? readResponse(scene('scene-a', 'One', 1), 1) : readResponse(scene('scene-a', 'Two', 1), 1),
    ))
    const sources: FakeEventSource[] = []
    const { state, gameKey, wrapper } = mountProjection(apiMock, sources)
    await flushPromises()
    expect(sources).toHaveLength(1)
    gameKey.value = 'two'
    await flushPromises()
    expect(sources[0]!.closed).toBe(true)
    expect(sources).toHaveLength(2)
    expect(state.scene.value?.display_name).toBe('Two')
    expect(apiMock).toHaveBeenCalledWith('/games/two/tactical-scenes/scene-a')
    wrapper.unmount()
  })
})