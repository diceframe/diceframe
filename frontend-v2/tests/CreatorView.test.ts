import { flushPromises, mount } from '@vue/test-utils'
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
      routes: [
        ...['creator', 'adventures', 'worlds', 'lorebook', 'rules', 'modules'].map(name => ({
          path: `/${name}`, name, component: { template: '<div />' },
        })),
        { path: '/creator/studio/:projectId', name: 'creator-studio', component: { template: '<div />' } },
      ],
    })
    await router.push('/creator')
    const wrapper = mount(CreatorView, {
      global: { plugins: [i18n, router], stubs: { CreatorAssistantWindow: true } },
    })
    return { wrapper, router }
  }

  it('aggregates only existing creation tools with named routes', async () => {
    i18n.global.locale.value = 'zh-CN'
    const { wrapper } = await render()
    expect(wrapper.get('h1').text()).toBe('创作属于你的世界')
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
    expect(wrapper.text()).toContain('导出内容包')
    expect(wrapper.find('[data-testid="creator-card-exportResourcePack"]').exists()).toBe(false)
    // A single adventure is exported from its row in the adventure list.
    expect(wrapper.get('[data-testid="creator-card-exportAdventureBundle"]').attributes('href')).toBe('/adventures')
    expect(wrapper.find('[data-testid="creator-card-package"]').exists()).toBe(false)
  })

  it('creates projects from the hero input with the selected template kind', async () => {
    i18n.global.locale.value = 'zh-CN'
    const created: Array<{ name: string; source?: Record<string, string> }> = []
    vi.spyOn(creatorApi, 'create').mockImplementation(async (name, source) => {
      created.push({ name, source })
      return { ok: true, project: { id: 'p1', name, revision: 0, source: source || {}, file_count: 0 } }
    })
    const updateSpy = vi.spyOn(creatorApi, 'update').mockResolvedValue({
      ok: true,
      project: { id: 'p1', name: '我的模组', revision: 1, source: { kind: 'adventure' }, file_count: 5, files: {}, digest: 'd' },
    })
    const { wrapper } = await render()
    await wrapper.get('textarea.creator-hero-input').setValue('我的模组')
    await wrapper.get('form.creator-hero-form').trigger('submit')
    await flushPromises()
    // source.kind 必须在创建时直传后端，作品卡片的类型图标/文案依赖它。
    expect(created[0]).toEqual({ name: '我的模组', source: { kind: 'adventure' } })
    expect(updateSpy).toHaveBeenCalledTimes(1)
    expect(updateSpy.mock.calls[0]?.[1]?.files).toHaveProperty('plugin.json')
    expect(wrapper.get('[data-testid="creator-myworks"]').text()).toContain('冒险')
    // 切换类型 chip 后再创建，source.kind 必须跟着变。
    const chips = wrapper.findAll('.creator-hero-kind-chip')
    await chips[1]!.trigger('click')
    await wrapper.get('textarea.creator-hero-input').setValue('我的规则')
    await wrapper.get('form.creator-hero-form').trigger('submit')
    await flushPromises()
    expect(created[1]).toEqual({ name: '我的规则', source: { kind: 'rule' } })
  })

  it('keeps all five supported locales populated', async () => {
    const expected: Record<Locale, string> = {
      'zh-CN': '创作属于你的世界',
      en: 'Create your own world',
      ja: 'あなただけの世界を作ろう',
      de: 'Erschaffe deine eigene Welt',
      ru: 'Создай свой собственный мир',
    }
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
