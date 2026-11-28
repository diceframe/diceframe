import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import TacticalSceneView from '../src/features/tactical-scene/TacticalSceneView.vue'
import * as client from '../src/api/client'
import { ApiError } from '../src/api/client'
import type { TacticalSceneSeatProjection } from '../src/api/tacticalScene'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

function scene(canonicalId: string, displayName: string): TacticalSceneSeatProjection {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: canonicalId,
    display_name: displayName,
    navigation_name: '',
    sort: 0,
    background: null,
    foreground: null,
    thumbnail: null,
    dimensions: { width: 0, height: 0, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 0, y: 0, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '', alpha: 0, distance: 5 },
    environment: { darkness: 0, global_illumination: false, fog: { exploration: false, reset_policy: '' }, weather: '' },
    levels: [],
    revision: 1,
    placeables: { tokens: {}, walls: {}, lights: {} },
  }
}

function listResponse(scenes: TacticalSceneSeatProjection[], activeSceneRef: string | null) {
  return { ok: true as const, scenes, active_scene_ref: activeSceneRef, revision: 3 }
}

const gamesResponse = { games: [{ game_key: 'one', world_name: 'One' }, { game_key: 'two', world_name: 'Two' }] }

async function mountView(apiMock: ReturnType<typeof vi.fn>) {
  vi.spyOn(client, 'api').mockImplementation(apiMock as typeof client.api)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/tactical-scene', name: 'tactical-scene', component: TacticalSceneView },
      { path: '/play', name: 'play', component: { template: '<div />' } },
    ],
  })
  await router.push({ name: 'tactical-scene', query: { game: 'one' } })
  await router.isReady()
  const wrapper = mount(TacticalSceneView, { global: { plugins: [i18n, router] } })
  await flushPromises()
  return { wrapper, router }
}

afterEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'en'
  localStorage.clear()
})

describe('TacticalSceneView shell', () => {
  it('lists scenes for the selected game and marks the active one', async () => {
    const apiMock = vi.fn((path: string) => {
      if (path === '/games') return Promise.resolve(gamesResponse)
      if (path === '/games/one/tactical-scenes') return Promise.resolve(listResponse([scene('scene-a', 'Ambush'), scene('scene-b', 'Camp')], 'scene-b'))
      if (path === '/games/two/tactical-scenes') return Promise.resolve(listResponse([scene('scene-c', 'Harbor')], null))
      return Promise.reject(new Error(`unexpected path ${path}`))
    })
    const { wrapper } = await mountView(apiMock)
    expect(apiMock).toHaveBeenCalledWith('/games/one/tactical-scenes')
    const items = wrapper.findAll('[data-testid="tactical-scene-item"]')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('Ambush')
    expect(items[0].text()).not.toContain('Current scene')
    expect(items[1].text()).toContain('Camp')
    expect(items[1].text()).toContain('Current scene')

    await wrapper.get('select').setValue('two')
    await flushPromises()
    expect(wrapper.text()).toContain('Harbor')
    expect(wrapper.text()).not.toContain('Ambush')
    expect(wrapper.text()).not.toContain('Current scene')
  })

  it('shows an empty state when the game has no scenes', async () => {
    const apiMock = vi.fn((path: string) => path === '/games'
      ? Promise.resolve(gamesResponse)
      : Promise.resolve(listResponse([], null)))
    const { wrapper } = await mountView(apiMock)
    expect(wrapper.text()).toContain('This game has no tactical scenes yet.')
    expect(wrapper.find('[data-testid="tactical-scene-item"]').exists()).toBe(false)
  })

  it('shows an empty state when no games exist', async () => {
    const apiMock = vi.fn().mockResolvedValue({ games: [] })
    const { wrapper } = await mountView(apiMock)
    expect(wrapper.text()).toContain('No games are available yet.')
    expect(wrapper.find('select').exists()).toBe(false)
  })

  it('surfaces a localized 404 without crashing and recovers on retry', async () => {
    let fail = true
    const apiMock = vi.fn((path: string) => {
      if (path === '/games') return Promise.resolve(gamesResponse)
      return fail
        ? Promise.reject(new ApiError('raw backend text', 404, 'tactical_scene_not_found'))
        : Promise.resolve(listResponse([scene('scene-a', 'Ambush')], 'scene-a'))
    })
    const { wrapper } = await mountView(apiMock)
    const alert = wrapper.get('[role="alert"]')
    expect(alert.text()).toContain('Game or scene not found')
    expect(wrapper.find('[data-testid="tactical-scene-item"]').exists()).toBe(false)

    fail = false
    await alert.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Ambush')
  })

  it('keeps the newest game when list responses resolve out of order', async () => {
    const one = deferred<unknown>(), two = deferred<unknown>()
    const apiMock = vi.fn((path: string) => {
      if (path === '/games') return Promise.resolve(gamesResponse)
      return path.includes('/one/') ? one.promise : two.promise
    })
    const { wrapper } = await mountView(apiMock)
    await wrapper.get('select').setValue('two')
    two.resolve(listResponse([scene('scene-c', 'Harbor')], null))
    await flushPromises()
    one.resolve(listResponse([scene('scene-a', 'Ambush')], null))
    await flushPromises()
    expect(wrapper.text()).toContain('Harbor')
    expect(wrapper.text()).not.toContain('Ambush')
  })

  it('renders Chinese copy when the locale is zh-CN', async () => {
    i18n.global.locale.value = 'zh-CN'
    const apiMock = vi.fn((path: string) => path === '/games'
      ? Promise.resolve(gamesResponse)
      : Promise.resolve(listResponse([scene('scene-a', '伏击')], 'scene-a')))
    const { wrapper } = await mountView(apiMock)
    expect(wrapper.get('h1').text()).toContain('战术场景')
    expect(wrapper.get('[data-testid="tactical-scene-item"]').text()).toContain('当前场景')
  })
})

describe('TacticalSceneView GM-only editor entry', () => {
  const gmGames = { games: [{ game_key: 'one', world_name: 'One', gm_uid: 'gm-1' }] }

  function gmDocument() {
    return {
      ...scene('scene-a', 'Ambush'),
      source_ref: null,
      external_ids: {},
      ownership: {},
      flags: {},
      extensions: {},
      provenance: {},
      placeables: { tokens: {}, tiles: {}, walls: {}, lights: {}, sounds: {}, notes: {}, drawings: {}, templates: {}, regions: {} },
    }
  }

  function gmApiMock() {
    return vi.fn((path: string) => {
      if (path === '/games') return Promise.resolve(gmGames)
      if (path === '/games/one/tactical-scenes') return Promise.resolve(listResponse([scene('scene-a', 'Ambush')], 'scene-a'))
      if (path === '/games/one/tactical-scenes/scene-a') {
        return Promise.resolve({ ok: true, scene_id: 'scene-a', scene: gmDocument(), active_scene_ref: 'scene-a', revision: 4 })
      }
      return Promise.reject(new Error(`unexpected path ${path}`))
    })
  }

  it('shows create and edit controls for the owner session and opens the editor', async () => {
    localStorage.setItem('trpg_access_token', 'owner-token')
    const { wrapper } = await mountView(gmApiMock())
    expect(wrapper.find('[data-testid="tactical-scene-create-open"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tactical-scene-edit"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tactical-scene-gm-note"]').exists()).toBe(false)

    await wrapper.get('[data-testid="tactical-scene-edit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="scene-editor"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tactical-board"]').exists()).toBe(false)
  })

  it('hides editor controls from a visitor without an owner session', async () => {
    const { wrapper } = await mountView(gmApiMock())
    expect(wrapper.find('[data-testid="tactical-scene-create-open"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="tactical-scene-edit"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="tactical-scene-gm-note"]').text()).toContain('GM only')
  })

  it('a named player preview does not inherit the owner GM controls', async () => {
    localStorage.setItem('trpg_access_token', 'owner-token')
    vi.spyOn(client, 'api').mockImplementation(gmApiMock() as typeof client.api)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/tactical-scene', name: 'tactical-scene', component: TacticalSceneView },
        { path: '/play', name: 'play', component: { template: '<div />' } },
      ],
    })
    await router.push({ name: 'tactical-scene', query: { game: 'one', user: 'player-1' } })
    await router.isReady()
    const wrapper = mount(TacticalSceneView, { global: { plugins: [i18n, router] } })
    await flushPromises()
    expect(wrapper.find('[data-testid="tactical-scene-edit"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="tactical-scene-create-open"]').exists()).toBe(false)
  })
})