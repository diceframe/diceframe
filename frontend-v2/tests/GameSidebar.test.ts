import { mount, shallowMount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { GameDetail } from '../src/api/types'
import GameSidebar from '../src/components/GameSidebar.vue'

vi.mock('../src/composables/useLocale', () => ({
  useLocale: () => ({ t: (key: string) => key }),
}))

describe('GameSidebar navigation', () => {
  it('places the map above perceptions and omits the redundant adventure entry', async () => {
    const wrapper = shallowMount(GameSidebar, {
      props: {
        detail: { state: 'paused', adventure_binding: { adventure_id: 'test:quest', version: '1.0.0' } } as GameDetail,
        privateMessages: [],
      },
    })
    const sections = wrapper.findAll('.game-sidebar > details').map(section => section.get('summary strong').text())
    expect(sections).toEqual(['mapTitle', 'characterPerception', 'statusInfo'])
    expect(wrapper.text()).not.toContain('gameAdventureBinding')
    await wrapper.get('.sidebar-map-open').trigger('click')
    expect(wrapper.emitted('open-map')).toHaveLength(1)
    wrapper.unmount()
  })

  it('gives every disclosure row the same fixed-size decorative icon', () => {
    const wrapper = mount(GameSidebar, {
      props: { detail: { state: 'paused', plot_tracker: { quests: { q: { title: 'Find the key', status: 'active' } } } } as unknown as GameDetail, privateMessages: [] },
      global: { stubs: { MapGraph: true } },
    })
    const rows = wrapper.findAll('.game-sidebar > details > summary')
    expect(rows.map(row => row.get('strong').text())).toEqual(['plotTracker', 'mapTitle', 'characterPerception', 'statusInfo'])
    for (const row of rows) {
      const icon = row.get('svg.sidebar-row-icon')
      expect([icon.attributes('width'), icon.attributes('height'), icon.attributes('stroke-width'), icon.attributes('aria-hidden')]).toEqual(['16', '16', '1.5', 'true'])
    }
    wrapper.unmount()
  })
})
