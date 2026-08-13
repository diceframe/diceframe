import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import MapWorkspace from '../src/components/play/MapWorkspace.vue'
import type { MapData } from '../src/api/types'

const map: MapData = {
  current_location_id: 'station',
  locations: [
    { id: 'station', name: '阿卡姆车站', content: '冒险的起点。', connected_to: ['university'], keywords: ['火车'] },
    { id: 'university', name: '密斯卡托尼克大学', content: '藏书丰富。', connected_to: ['station'] },
  ],
}

describe('MapWorkspace', () => {
  it('显示地点详情、当前位置和搜索结果', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(MapWorkspace, {
      global: { plugins: [i18n], stubs: { Teleport: true } },
      props: { map, currentScene: '阿卡姆车站' },
    })

    expect(wrapper.text()).toContain('冒险的起点。')
    expect(wrapper.text()).toContain('当前位置')
    expect(wrapper.get('.map-workspace-title-icon').find('.n-icon').exists()).toBe(true)
    await wrapper.get('.map-search input').setValue('大学')
    expect(wrapper.findAll('.map-location-list-item')).toHaveLength(1)
    expect(wrapper.text()).toContain('密斯卡托尼克大学')
  })

  it('点击关闭按钮发出 close', async () => {
    const wrapper = mount(MapWorkspace, {
      global: { plugins: [i18n], stubs: { Teleport: true } },
      props: { map },
    })
    await wrapper.get('.map-workspace-close').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
  })
})
