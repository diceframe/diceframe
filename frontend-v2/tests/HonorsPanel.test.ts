import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'
import { normalizeSettingsSection } from '@/utils/settingsSections'
import { searchSettings, settingsGroupFor } from '@/features/admin/settings/settingsSearch'
import {
  buildBadgeBackSvg,
  buildBadgeFrontSvg,
  buildBadgeMaskDataUrl,
} from '@/features/honors/badgeArtwork'
import HonorsPanel from '@/features/honors/HonorsPanel.vue'
import type { HonorsBadgeDefinition, HonorsStateResponse, HonorsWallResponse } from '@/api/honors'

const sampleCatalog: HonorsBadgeDefinition[] = [
  {
    id: 'pioneer',
    shape: 'shield',
    wall_mode: 'listed',
    theme: 'starlight',
    craft: { 'zh-CN': '星尘深蓝珐琅 × 拉丝香槟金', en: 'Starlight Enamel × Brushed Gold' },
    name: { 'zh-CN': '先驱者', en: 'Pioneer' },
    subtitle: { 'zh-CN': '早期启航限量纪念', en: 'Early Voyage Edition' },
    description: { 'zh-CN': '先驱说明', en: 'Pioneer description' },
  },
  {
    id: 'voyage_mark',
    shape: 'compass',
    wall_mode: 'counter',
    theme: 'bronze',
    name: { 'zh-CN': '启航印记', en: 'Voyage Mark' },
    subtitle: { 'zh-CN': '全服活动纪念章', en: 'Community Milestone Mark' },
  },
]

const honorsApiMocks = vi.hoisted(() => ({
  getHonorsState: vi.fn(),
  activateHonorsToken: vi.fn(),
  setFeaturedHonorBadge: vi.fn(),
  clearHonorsCredential: vi.fn(),
  getHonorsWall: vi.fn(),
}))

vi.mock('@/api/honors', () => honorsApiMocks)

vi.mock('@/composables/useToast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}))

vi.mock('@/composables/useConfirm', () => ({
  useConfirm: () => ({
    confirm: vi.fn().mockResolvedValue(true),
  }),
}))

describe('honors settings wiring & artwork', () => {
  it('registers honors section and search entry', () => {
    expect(normalizeSettingsSection('honors')).toBe('honors')
    expect(settingsGroupFor('honors')).toBe('general')
    const results = searchSettings('勋章', 'zh-CN')
    expect(results.some(item => item.section === 'honors')).toBe(true)
  })

  it('builds distinct front/back SVG artwork and non-circular mask for shaped badges', () => {
    const front = buildBadgeFrontSvg(sampleCatalog[0], 'zh-CN', 'unit1', false)
    const back = buildBadgeBackSvg(sampleCatalog[0], {
      holderName: '星界旅人',
      holderId: 'NO. DF-0042',
      issuedLabel: 'ISSUED · 2026.11',
      awardedPrefix: '谨此授予',
      uid: 'unit1',
    })
    const mask = buildBadgeMaskDataUrl(sampleCatalog[0])

    expect(front).toContain('clip-unit1')
    expect(front).toContain('先驱者')
    expect(back).toContain('星界旅人')
    expect(back).toContain('NO. DF-0042')
    expect(mask).toContain('data:image/svg+xml')
  })
})

describe('HonorsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    const initialState: HonorsStateResponse = {
      ok: true,
      catalog: sampleCatalog,
      active: null,
      remote_configured: false,
      redeem_url: 'https://diceframe.com/redeem',
    }
    const initialWall: HonorsWallResponse = {
      ok: true,
      source: 'offline',
      page: 1,
      limit: 60,
      total: 0,
      items: [],
      counters: { voyage_mark: 12 },
      catalog: sampleCatalog,
      revoked: [],
    }
    honorsApiMocks.getHonorsState.mockResolvedValue(initialState)
    honorsApiMocks.getHonorsWall.mockResolvedValue(initialWall)
  })

  it('renders showcase badges and activates an offline credential token', async () => {
    honorsApiMocks.activateHonorsToken.mockResolvedValue({
      ok: true,
      catalog: sampleCatalog,
      active: {
        id: 'DF-0042',
        name: '星界旅人',
        badges: ['pioneer'],
        iat: 1764300000,
        featured_badge_id: 'pioneer',
        activated_at: 1764300000,
      },
      remote_configured: false,
      redeem_url: 'https://diceframe.com/redeem',
    })

    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(HonorsPanel, {
      global: {
        plugins: [i18n],
        stubs: {
          NModal: { template: '<div v-if="show" class="modal-stub"><slot /></div>', props: ['show'] },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('先驱者')
    expect(wrapper.text()).toContain('12')

    const input = wrapper.find('input')
    await input.setValue('DFB1.sample.signature')
    const activateBtn = wrapper.findAll('button').find(btn => btn.text().includes('点亮'))
    expect(activateBtn).toBeTruthy()
    await activateBtn!.trigger('click')
    await flushPromises()

    expect(honorsApiMocks.activateHonorsToken).toHaveBeenCalledWith('DFB1.sample.signature')
    expect(wrapper.text()).toContain('星界旅人')
    expect(wrapper.text()).toContain('NO. DF-0042')
  })

  it('guides users to the website when pasting an event redemption code (DF-...)', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(HonorsPanel, {
      global: {
        plugins: [i18n],
        stubs: {
          NModal: { template: '<div v-if="show" class="modal-stub"><slot /></div>', props: ['show'] },
        },
      },
    })
    await flushPromises()

    const input = wrapper.find('input')
    await input.setValue('DF-CUXP-A87D-M9NY')
    expect(wrapper.find('.redeem-code-alert').exists()).toBe(true)
    expect(wrapper.find('.redeem-code-alert').text()).toContain('DFB1.')

    const activateBtn = wrapper.findAll('button').find(btn => btn.text().includes('点亮'))
    await activateBtn!.trigger('click')
    await flushPromises()

    expect(honorsApiMocks.activateHonorsToken).not.toHaveBeenCalled()
  })
})
