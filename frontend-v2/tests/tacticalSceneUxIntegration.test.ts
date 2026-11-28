import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { i18n } from '@/i18n'
import { resolveSceneAssetEndpoint } from '@/features/tactical-scene/useSceneAssetUrl'
import BoardBackground from '@/features/tactical-scene/BoardBackground.vue'
import EncounterPrep from '@/features/rulesets/dnd2024/combat/EncounterPrep.vue'
import GameSidebar from '@/components/GameSidebar.vue'
import type { TacticalSceneProjection } from '@/api/tacticalScene'

const apiBlobMock = vi.fn()
const fetchActiveTacticalSceneMock = vi.fn()

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiBlob: (...args: unknown[]) => apiBlobMock(...args),
  }
})

vi.mock('@/api/tacticalScene', async () => {
  const actual = await vi.importActual<typeof import('@/api/tacticalScene')>('@/api/tacticalScene')
  return {
    ...actual,
    fetchActiveTacticalScene: (...args: unknown[]) => fetchActiveTacticalSceneMock(...args),
  }
})

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: { template: '<div />' } },
      { path: '/play', name: 'play', component: { template: '<div />' } },
      { path: '/tactical-scene', name: 'tactical-scene', component: { template: '<div />' } },
      { path: '/dnd5e-import', name: 'dnd5e-import', component: { template: '<div />' } },
    ],
  })
}

function squareScene(): TacticalSceneProjection {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: 'scene_tavern',
    display_name: 'Red Tavern',
    navigation_name: 'Red Tavern',
    sort: 0,
    background: {
      asset_id: 'abc123',
      digest: 'abc123',
      source_path: 'map-backgrounds/abc123.webp',
      mime: 'image/webp',
    },
    foreground: null,
    thumbnail: null,
    dimensions: {
      width: 1600,
      height: 1200,
      padding: 0,
      offset_x: 0,
      offset_y: 0,
      initial_view: { x: 0, y: 0, zoom: 1 },
    },
    grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '#000', alpha: 0.25, distance: 5 },
    environment: { darkness: 0, global_illumination: true, fog: { exploration: false, reset_policy: '' }, weather: '' },
    placeables: {
      tokens: {
        tok_1: {
          canonical_id: 'tok_1',
          actor_ref: { source_kind: 'diceframe', source_id: '', kind: 'player', id: 'p1', digest: '' },
          position: { x: 100, y: 100, elevation: 0, level: 0 },
          size: { width: 1, height: 1 },
          rotation: 0,
          texture: null,
          alpha: 1,
          display: {},
          disposition: 1,
          bars: {},
          locked: false,
        },
      },
      walls: {},
      lights: {},
    },
    levels: [],
    revision: 2,
  }
}

describe('tactical scene UX integration', () => {
  beforeEach(() => {
    apiBlobMock.mockReset()
    fetchActiveTacticalSceneMock.mockReset()
  })

  it('resolves DiceFrame upload and generated image paths to authenticated endpoints', () => {
    expect(resolveSceneAssetEndpoint('map-backgrounds/deadbeef.webp', 'g-1'))
      .toBe('/games/g-1/map-background-asset/deadbeef')
    expect(resolveSceneAssetEndpoint('generated-images/img42.webp'))
      .toBe('/generated-images/img42')
    expect(resolveSceneAssetEndpoint('/api/games/g-1/map-background-asset/deadbeef'))
      .toBe('/games/g-1/map-background-asset/deadbeef')
    expect(resolveSceneAssetEndpoint('worlds/foo/bar.webp', 'g-1')).toBeNull()
  })

  it('loads map-backgrounds via apiBlob in BoardBackground and revokes on unmount', async () => {
    const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-bg')
    const revokeUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    apiBlobMock.mockResolvedValue({ blob: async () => new Blob(['img'], { type: 'image/webp' }) })

    const wrapper = mount({
      components: { BoardBackground },
      template: '<svg><BoardBackground :background="bg" :rect="rect" game-key="g-1" /></svg>',
      data: () => ({
        bg: squareScene().background,
        rect: { x: 0, y: 0, width: 1600, height: 1200 },
      }),
    }, { global: { plugins: [i18n] } })
    await flushPromises()

    expect(apiBlobMock).toHaveBeenCalledWith('/games/g-1/map-background-asset/abc123')
    expect(wrapper.find('[data-testid="board-background-image"]').attributes('href')).toBe('blob:mock-bg')

    wrapper.unmount()
    expect(revokeUrl).toHaveBeenCalledWith('blob:mock-bg')
    createUrl.mockRestore()
    revokeUrl.mockRestore()
  })

  it('lets GM start a 2D grid combat from EncounterPrep when an active square scene is ready', async () => {
    fetchActiveTacticalSceneMock.mockResolvedValue({
      ok: true,
      scene_id: 'scene_tavern',
      scene: squareScene(),
      active_scene_ref: 'scene_tavern',
      revision: 2,
    })
    const submit = vi.fn().mockResolvedValue(true)
    const router = makeRouter()

    const wrapper = mount(EncounterPrep, {
      props: {
        gameKey: 'g-1',
        actorId: 'gm1',
        isGm: true,
        busy: false,
        submit,
        data: {
          ok: true,
          gameplay: {
            state_version: 3,
            combat: { status: 'idle' },
            encounter_access: { mode: 'sandbox' },
            encounter_presets: [
              { id: 'preset_goblin', name: 'Goblin Ambush', difficulty: 'easy', description: 'Goblins', enemies: [{ id: 'enemy:g1', name: 'Goblin' }] },
            ],
          },
          available_actions: [{ type: 'combat.start' }],
        } as never,
      },
      global: { plugins: [i18n, router] },
    })
    await flushPromises()

    await wrapper.find('.manual-encounter-toggle').trigger('click')
    const checkbox = wrapper.find('[data-testid="encounter-grid-checkbox"]')
    expect((checkbox.element as HTMLInputElement).disabled).toBe(false)
    await checkbox.setValue(true)
    await wrapper.find('button.combat-primary').trigger('click')

    expect(submit).toHaveBeenCalledWith(expect.objectContaining({
      type: 'combat.start',
      position_mode: 'grid',
      encounter_preset_id: 'preset_goblin',
    }))
  })

  it('renders a direct Tactical Scene link inside GameSidebar map drawer', async () => {
    const router = makeRouter()
    const wrapper = mount(GameSidebar, {
      props: {
        detail: { game_key: 'g-42', state: 'active_action', scene: 'Tavern' } as never,
        privateMessages: [],
      },
      global: {
        plugins: [i18n, router],
        stubs: { CharacterPanel: true, MapGraph: true },
      },
    })
    const link = wrapper.find('[data-testid="sidebar-open-tactical-scene"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toContain('/tactical-scene?game=g-42')
  })
})
