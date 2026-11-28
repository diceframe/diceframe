import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import Board from '../src/features/tactical-scene/Board.vue'
import type {
  TacticalSceneDocument,
  TacticalSceneLightDocument,
  TacticalSceneLightProjection,
  TacticalSceneSeatProjection,
  TacticalSceneTokenDocument,
  TacticalSceneTokenProjection,
  TacticalSceneWallDocument,
  TacticalSceneWallProjection,
} from '../src/api/tacticalScene'

function tokenProjection(id: string, over: Partial<TacticalSceneTokenProjection> = {}): TacticalSceneTokenProjection {
  return {
    canonical_id: id,
    actor_ref: null,
    position: { x: 300, y: 400, elevation: 0, level: 0 },
    size: { width: 1, height: 1 },
    rotation: 0,
    texture: null,
    alpha: 1,
    display: {},
    disposition: 0,
    bars: {},
    locked: false,
    ...over,
  }
}

function seatScene(over: Partial<TacticalSceneSeatProjection> = {}): TacticalSceneSeatProjection {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: 'scene-a',
    display_name: 'Chapel',
    navigation_name: '',
    sort: 0,
    background: { asset_id: 'bg', digest: '', source_path: '/img/chapel.webp', mime: 'image/webp' },
    foreground: null,
    thumbnail: null,
    dimensions: { width: 1200, height: 900, padding: 0.25, offset_x: 0, offset_y: 0, initial_view: { x: 600, y: 450, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '#000000', alpha: 0.2, distance: 5 },
    environment: { darkness: 0, global_illumination: false, fog: { exploration: false, reset_policy: '' }, weather: '' },
    levels: [],
    revision: 1,
    placeables: {
      tokens: {
        'tok-1': tokenProjection('tok-1', {
          texture: { asset_id: 't1', digest: '', source_path: '/img/goblin.webp', mime: 'image/webp' },
          rotation: 45,
          alpha: 0.9,
          disposition: -1,
        }),
        'tok-2': tokenProjection('tok-2', { position: { x: 600, y: 450, elevation: 0, level: 0 }, size: { width: 2, height: 2 }, disposition: 1 }),
      },
      walls: {
        'wall-1': { canonical_id: 'wall-1', endpoints: { x0: 0, y0: 0, x1: 1200, y1: 0 }, direction: 0, door: 0, door_state: 0, restrictions: { move: 1, sight: 1, sound: 1, light: 1 } },
        'wall-2': { canonical_id: 'wall-2', endpoints: { x0: 0, y0: 0, x1: 0, y1: 900 }, direction: 0, door: 1, door_state: 0, restrictions: { move: 1, sight: 1, sound: 1, light: 1 } },
      },
      lights: {
        'light-1': { canonical_id: 'light-1', position: { x: 300, y: 450 }, rotation: 0, elevation: 0, radius: { dim: 40, bright: 20 }, color: '#ffaa00', intensity: 0.6, angle: 360 },
      },
    },
    ...over,
  }
}

function fullToken(projection: TacticalSceneTokenProjection, hidden: boolean): TacticalSceneTokenDocument {
  return {
    schema_version: 1,
    kind: 'token',
    ...projection,
    hidden,
    ring: {},
    sight: {},
    detection_modes: [],
    light: {},
    delta: {},
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
  }
}

function fullWall(projection: TacticalSceneWallProjection): TacticalSceneWallDocument {
  return { schema_version: 1, kind: 'wall', ...projection, external_ids: {}, flags: {}, extensions: {}, provenance: {} }
}

function fullLight(projection: TacticalSceneLightProjection): TacticalSceneLightDocument {
  return {
    schema_version: 1,
    kind: 'light',
    ...projection,
    hidden: false,
    provides_vision: false,
    blocked_by_walls: false,
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
  }
}

function gmScene(): TacticalSceneDocument {
  const seat = seatScene()
  const hiddenToken = fullToken(tokenProjection('tok-hidden', { position: { x: 900, y: 300, elevation: 0, level: 0 } }), true)
  return {
    ...seat,
    source_ref: null,
    external_ids: {},
    ownership: {},
    flags: {},
    extensions: {},
    provenance: {},
    placeables: {
      tokens: {
        'tok-1': fullToken(seat.placeables.tokens['tok-1']!, false),
        'tok-2': fullToken(seat.placeables.tokens['tok-2']!, false),
        'tok-hidden': hiddenToken,
      },
      walls: {
        'wall-1': fullWall(seat.placeables.walls['wall-1']!),
        'wall-2': fullWall(seat.placeables.walls['wall-2']!),
      },
      lights: { 'light-1': fullLight(seat.placeables.lights['light-1']!) },
      tiles: {},
      sounds: {},
      notes: {},
      drawings: {},
      templates: {},
      regions: {},
    },
  }
}

function mountBoard(scene: TacticalSceneSeatProjection | TacticalSceneDocument) {
  i18n.global.locale.value = 'en'
  return mount(Board, { global: { plugins: [i18n] }, props: { scene } })
}

function viewBoxNumbers(wrapper: ReturnType<typeof mountBoard>): number[] {
  return wrapper.get('[data-testid="tactical-board-svg"]').attributes('viewBox')!.split(' ').map(Number)
}

afterEach(() => { i18n.global.locale.value = 'en' })

describe('TacticalBoard layers', () => {
  it('renders background, square grid, walls, tokens and lights from the seat projection', () => {
    const wrapper = mountBoard(seatScene())
    expect(wrapper.get('[data-testid="board-background-image"]').attributes('href')).toBe('/img/chapel.webp')
    expect(wrapper.findAll('[data-testid="board-grid"] line').length).toBeGreaterThan(0)
    const walls = wrapper.findAll('[data-testid="board-wall"]')
    expect(walls).toHaveLength(2)
    expect(walls[0].classes()).not.toContain('board-wall-door')
    expect(walls[1].classes()).toContain('board-wall-door')
    expect(walls[0].attributes('x2')).toBe('1200')
    const tokens = wrapper.findAll('[data-testid="board-token"]')
    expect(tokens).toHaveLength(2)
    expect(wrapper.get('[data-testid="board-tokens"] image').attributes('href')).toBe('/img/goblin.webp')
    // texture-less token falls back to a disposition-tinted circle
    expect(wrapper.findAll('[data-testid="board-tokens"] circle')).toHaveLength(1)
    const light = wrapper.get('[data-testid="board-light"]')
    const circles = light.findAll('circle')
    expect(circles.map(circle => Number(circle.attributes('r')))).toEqual([40, 20])
    expect(Number(circles[0].attributes('opacity'))).toBeCloseTo(0.14 * 0.6, 6)
    expect(Number(circles[1].attributes('opacity'))).toBeCloseTo(0.3 * 0.6, 6)
    expect(circles[0].attributes('fill')).toBe('#ffaa00')
  })

  it('draws no grid lines for gridless and shows the display-only hint for hex', () => {
    const gridless = mountBoard(seatScene({ grid: { type: 'gridless', size: 100, scale: 1, units: 'ft', color: '', alpha: 0, distance: 5 } }))
    expect(gridless.findAll('[data-testid="board-grid"] line')).toHaveLength(0)
    expect(gridless.find('[data-testid="board-hex-hint"]').exists()).toBe(false)
    const hex = mountBoard(seatScene({ grid: { type: 'hex', size: 100, scale: 1, units: 'ft', color: '', alpha: 0.2, distance: 5 } }))
    expect(hex.findAll('[data-testid="board-grid"] line')).toHaveLength(0)
    expect(hex.get('[data-testid="board-hex-hint"]').text()).toContain('Hex grid')
  })

  it('renders the GM document as-is, including hidden tokens (no client-side filtering)', () => {
    const wrapper = mountBoard(gmScene())
    expect(wrapper.findAll('[data-testid="board-token"]')).toHaveLength(3)
  })

  it('degrades to placeholders when the background or a token texture fails to load', async () => {
    const wrapper = mountBoard(seatScene())
    await wrapper.get('[data-testid="board-background-image"]').trigger('error')
    expect(wrapper.find('[data-testid="board-background-image"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="board-background-fallback"]').text()).toContain('Background image unavailable')
    expect(wrapper.findAll('[data-testid="board-tokens"] circle')).toHaveLength(1)
    await wrapper.get('[data-testid="board-tokens"] image').trigger('error')
    expect(wrapper.findAll('[data-testid="board-tokens"] circle')).toHaveLength(2)
  })

  it('shows a neutral canvas without a background reference and skips zero-radius lights', () => {
    const scene = seatScene({ background: null })
    scene.placeables.lights['light-1']!.radius = { dim: 0, bright: 0 }
    const wrapper = mountBoard(scene)
    expect(wrapper.find('[data-testid="board-background-image"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="board-background-fallback"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="board-light"]')).toHaveLength(0)
  })
})

describe('TacticalBoard view interaction', () => {
  it('fits the padded scene rect on mount and reset restores it after zooming', async () => {
    const wrapper = mountBoard(seatScene())
    await nextTick()
    const initial = viewBoxNumbers(wrapper)
    // padded rect: 1800×1350 centered at (600, 450)
    expect(initial[0] + initial[2] / 2).toBeCloseTo(600, 6)
    expect(initial[1] + initial[3] / 2).toBeCloseTo(450, 6)
    expect(initial[2]).toBeCloseTo(1800 * 1.04, 6)

    await wrapper.get('[data-testid="board-zoom-in"]').trigger('click')
    const zoomed = viewBoxNumbers(wrapper)
    expect(zoomed[2]).toBeLessThan(initial[2])

    await wrapper.get('[data-testid="board-reset-view"]').trigger('click')
    const reset = viewBoxNumbers(wrapper)
    expect(reset[2]).toBeCloseTo(initial[2], 6)
    expect(reset[0]).toBeCloseTo(initial[0], 6)
  })

  it('wheel zoom keeps the cursor world point anchored', async () => {
    const wrapper = mountBoard(seatScene())
    const svg = wrapper.get('[data-testid="tactical-board-svg"]')
    const element = svg.element as SVGSVGElement
    element.getBoundingClientRect = () => ({
      x: 0, y: 0, left: 0, top: 0, right: 900, bottom: 675, width: 900, height: 675, toJSON: () => ({}),
    } as DOMRect)
    await nextTick()
    const before = viewBoxNumbers(wrapper)
    element.dispatchEvent(new WheelEvent('wheel', { deltaY: -120, clientX: 450, clientY: 337.5, bubbles: true, cancelable: true }))
    await nextTick()
    const after = viewBoxNumbers(wrapper)
    expect(after[2]).toBeLessThan(before[2])
    // cursor at the viewport center: the center world point stays fixed
    expect(after[0] + after[2] / 2).toBeCloseTo(before[0] + before[2] / 2, 6)
    expect(after[1] + after[3] / 2).toBeCloseTo(before[1] + before[3] / 2, 6)
  })

  it('pointer drag pans the viewBox with the drag direction', async () => {
    const wrapper = mountBoard(seatScene())
    const svg = wrapper.get('[data-testid="tactical-board-svg"]')
    const element = svg.element as SVGSVGElement
    await nextTick()
    const before = viewBoxNumbers(wrapper)
    element.dispatchEvent(new PointerEvent('pointerdown', { pointerId: 1, clientX: 100, clientY: 100, bubbles: true }))
    element.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, clientX: 160, clientY: 100, bubbles: true }))
    element.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1, bubbles: true }))
    await nextTick()
    const after = viewBoxNumbers(wrapper)
    expect(after[0]).toBeLessThan(before[0])
    expect(after[1]).toBeCloseTo(before[1], 6)
    expect(after[2]).toBeCloseTo(before[2], 6)
  })

  it('re-fits the view when the selected scene changes', async () => {
    const wrapper = mountBoard(seatScene())
    await nextTick()
    const before = viewBoxNumbers(wrapper)
    await wrapper.setProps({
      scene: seatScene({
        canonical_id: 'scene-b',
        dimensions: { width: 600, height: 600, padding: 0, offset_x: 200, offset_y: 100, initial_view: { x: 0, y: 0, zoom: 1 } },
      }),
    })
    const after = viewBoxNumbers(wrapper)
    expect(after[0] + after[2] / 2).toBeCloseTo(500, 6)
    expect(after[1] + after[3] / 2).toBeCloseTo(400, 6)
    expect(after[2]).not.toBeCloseTo(before[2], 6)
  })
})

describe('TacticalBoard token selection', () => {
  it('emits select-token on click and marks the selected token', async () => {
    const wrapper = mountBoard(seatScene())
    const tokens = wrapper.findAll('[data-testid="board-token"]')
    await tokens[0]!.trigger('click')
    expect(wrapper.emitted('select-token')).toEqual([['tok-1']])
    await wrapper.setProps({ selectedTokenId: 'tok-1' })
    expect(wrapper.findAll('[data-testid="board-token"]')[0]!.classes()).toContain('selected')
    expect(wrapper.find('.board-token-selected-ring').exists()).toBe(true)
  })

  it('is keyboard reachable: Enter and Space select the focused token', async () => {
    const wrapper = mountBoard(seatScene())
    const token = wrapper.findAll('[data-testid="board-token"]')[1]!
    expect(token.attributes('role')).toBe('button')
    expect(token.attributes('tabindex')).toBe('0')
    await token.trigger('keydown', { key: 'Enter' })
    await token.trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('select-token')).toEqual([['tok-2'], ['tok-2']])
  })
})