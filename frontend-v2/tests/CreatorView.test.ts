import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n, type Locale } from '../src/i18n'
import CreatorView from '../src/features/creator/CreatorView.vue'
import { creatorApi } from '../src/api/creator'

describe('CreatorView', () => {
  beforeEach(() => {
    vi.spyOn(creatorApi, 'list').mockResolvedValue({ ok: true, projects: [] })
  })
  async function render() {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: ['creator', 'adventures', 'worlds', 'lorebook', 'rules', 'modules'].map(name => ({
        path: `/${name}`, name, component: { template: '<div />' },
      })),
    })
    await router.push('/creator')
    const wrapper = mount(CreatorView, { global: { plugins: [i18n, router] } })
    return { wrapper, router }
  }

  it('aggregates only existing creation tools with named routes', async () => {
    i18n.global.locale.value = 'zh-CN'
    const { wrapper } = await render()
    expect(wrapper.get('h1').text()).toBe('创作工坊')
    expect(wrapper.findAll('[data-testid^="creator-card-"]')).toHaveLength(8)
    expect(wrapper.get('[data-testid="creator-card-adventures"]').attributes('href')).toBe('/adventures')
    expect(wrapper.get('[data-testid="creator-card-worlds"]').attributes('href')).toBe('/worlds')
    expect(wrapper.get('[data-testid="creator-card-textReview"]').element.tagName).toBe('BUTTON')
    expect(wrapper.text()).not.toMatch(/Profile|profile|玩法模板/i)
  })

  it('uses one unified package entry and keeps single-adventure backup separate', async () => {
    i18n.global.locale.value = 'zh-CN'
    const { wrapper } = await render()
    expect(wrapper.text()).toContain('分享与安装')
    expect(wrapper.get('[data-testid="creator-card-exportAdventureModule"]').element.tagName).toBe('BUTTON')
    expect(wrapper.text()).toContain('统一内容扩展包')
    expect(wrapper.find('[data-testid="creator-card-exportResourcePack"]').exists()).toBe(false)
    // A single adventure is exported from its row in the adventure list.
    expect(wrapper.get('[data-testid="creator-card-exportAdventureBundle"]').attributes('href')).toBe('/adventures')
    expect(wrapper.find('[data-testid="creator-card-package"]').exists()).toBe(false)
  })

  it('keeps all five supported locales populated', async () => {
    const expected: Record<Locale, string> = { 'zh-CN': '创作工坊', en: 'Creator workshop', ja: 'クリエイターワークショップ', de: 'Kreativwerkstatt', ru: 'Мастерская автора' }
    for (const [rawLocale, title] of Object.entries(expected)) {
      const locale = rawLocale as Locale
      i18n.global.locale.value = locale
      const { wrapper } = await render()
      expect(wrapper.get('h1').text()).toBe(title)
      expect(wrapper.findAll('[data-testid^="creator-card-"]')).toHaveLength(8)
      wrapper.unmount()
    }
  })
})
