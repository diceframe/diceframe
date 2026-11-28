import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import TokenDetail from '../src/features/tactical-scene/TokenDetail.vue'
import type { TacticalSceneTokenDocument, TacticalSceneTokenProjection } from '../src/api/tacticalScene'

function projection(over: Partial<TacticalSceneTokenProjection> = {}): TacticalSceneTokenProjection {
  return {
    canonical_id: 'tok-1',
    actor_ref: null,
    position: { x: 300, y: 400, elevation: 0, level: 0 },
    size: { width: 1, height: 1 },
    rotation: 45,
    texture: null,
    alpha: 1,
    display: {},
    disposition: -1,
    bars: {},
    locked: false,
    ...over,
  }
}

function gmToken(over: Partial<TacticalSceneTokenDocument> = {}): TacticalSceneTokenDocument {
  return {
    schema_version: 1,
    kind: 'token',
    ...projection(),
    hidden: false,
    ring: {},
    sight: {},
    detection_modes: [],
    light: {},
    delta: {},
    external_ids: {},
    flags: {},
    extensions: {},
    provenance: {},
    ...over,
  }
}

function mountDetail(props: InstanceType<typeof TokenDetail>['$props']) {
  i18n.global.locale.value = 'en'
  return mount(TokenDetail, { global: { plugins: [i18n] }, props })
}

afterEach(() => { i18n.global.locale.value = 'en' })

describe('TokenDetail projection rendering', () => {
  it('renders disposition, bars attribute and the not-imported actor state from a FVTT reference', () => {
    const wrapper = mountDetail({
      token: projection({
        actor_ref: { source_kind: 'foundry', source_id: 's', kind: 'actor', id: 'actor00000000001', digest: '' },
        bars: { bar1: { attribute: 'attributes.hp' } },
      }),
      isGm: false,
    })
    expect(wrapper.get('[data-testid="token-detail-disposition"]').text()).toBe('Hostile')
    expect(wrapper.get('[data-testid="token-actor-not-imported"]').text()).toBe('Actor not imported')
    expect(wrapper.find('[data-testid="token-actor-bound"]').exists()).toBe(false)
    const bar = wrapper.get('[data-testid="token-bar"]')
    expect(bar.text()).toContain('attributes.hp')
    expect(bar.get('[data-testid="token-bar-no-value"]').text()).toContain('No values stored')
  })

  it('shows a bound actor and a value bar when the token carries its own numbers', () => {
    const wrapper = mountDetail({
      token: projection({
        actor_ref: { source_kind: 'diceframe', source_id: 'g', kind: 'enemy', id: 'goblin-1', digest: '' },
        bars: { bar1: { attribute: 'attributes.hp', value: 3, max: 10 } },
      }),
      isGm: false,
    })
    const bound = wrapper.get('[data-testid="token-actor-bound"]')
    expect(bound.text()).toContain('Enemy')
    expect(bound.text()).toContain('goblin-1')
    expect(wrapper.find('[data-testid="token-bar-no-value"]').exists()).toBe(false)
    expect(wrapper.get('.token-bar-fill').attributes('style')).toContain('30%')
  })

  it('reports an unbound token and empty bars', () => {
    const wrapper = mountDetail({ token: projection(), isGm: false })
    expect(wrapper.get('[data-testid="token-actor-unbound"]').text()).toBe('No actor bound')
    expect(wrapper.get('[data-testid="token-bars-empty"]').text()).toContain('no resource bars')
  })

  it('renders a fill bar with the value/max text', () => {
    const wrapper = mountDetail({ token: projection({ bars: { bar1: { value: 8, max: 20 } } }), isGm: false })
    expect(wrapper.get('.token-bar-value').text()).toBe('8 / 20')
  })
})

describe('TokenDetail GM / seat differences', () => {
  it('shows hidden, ownership and the residual name only for the GM document', () => {
    const wrapper = mountDetail({
      token: gmToken({ hidden: true, locked: true, extensions: { name: 'Goblin' } }),
      isGm: true,
      ownership: { default: 0, gm: 3 },
    })
    expect(wrapper.get('[data-testid="token-detail-name"]').text()).toBe('Goblin')
    expect(wrapper.get('[data-testid="token-detail-hidden"]').text()).toBe('Hidden')
    expect(wrapper.get('[data-testid="token-detail-locked"]').text()).toBe('Locked')
    const ownership = wrapper.get('[data-testid="token-detail-ownership"]')
    expect(ownership.text()).toContain('Default')
    expect(ownership.text()).toContain('Owner')
    expect(wrapper.get('[data-testid="token-detail-bind"]').text()).toBe('Bind actor')
  })

  it('never shows hidden, ownership or a bind button for a seat projection', () => {
    const wrapper = mountDetail({ token: projection({ locked: true }), isGm: false, ownership: null })
    expect(wrapper.find('[data-testid="token-detail-hidden"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="token-detail-ownership"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="token-detail-bind"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="token-detail-locked"]').text()).toBe('Locked')
    expect(wrapper.get('[data-testid="token-detail-name"]').text()).toBe('tok-1')
  })

  it('emits bind and close', async () => {
    const wrapper = mountDetail({ token: gmToken(), isGm: true })
    await wrapper.get('[data-testid="token-detail-bind"]').trigger('click')
    expect(wrapper.emitted('bind')).toHaveLength(1)
    await wrapper.get('[data-testid="token-detail-close"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})