import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import SectionWorkspaceShell from '@/components/navigation/SectionWorkspaceShell.vue'
import type { AppNavGroupId } from '@/navigation/appNavigation'
import { i18n } from '@/i18n'

async function mountShell(routeName = 'worlds', groupId: AppNavGroupId = 'content') {
  i18n.global.locale.value = 'zh-CN'
  const routes = ['lorebook', 'worlds', 'adventures', 'modules', 'rules', 'memory', 'logs', 'plugins', 'settings', 'overview', 'play'].map(name => ({
    path: `/${name}`,
    name,
    component: { template: '<div />' },
  }))
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push({ name: routeName })
  await router.isReady()
  return mount(SectionWorkspaceShell, {
    props: { groupId },
    slots: { default: '<div class="test-content">content</div>' },
    global: { plugins: [i18n, router] },
  })
}

describe('SectionWorkspaceShell', () => {
  it('keeps content tools in a persistent canonical order', async () => {
    const wrapper = await mountShell()
    const links = wrapper.get('nav[aria-label="内容"]').findAll('a')

    expect(links.map(link => link.text())).toEqual(expect.arrayContaining(['世界书资料、人物与隐藏真相', '世界舞台、封面与叙事风格']))
  expect(links.map(link => link.attributes('href'))).toEqual([
    '/lorebook', '/worlds', '/adventures', '/modules', '/rules',
    ])
    expect(links[1].attributes('aria-current')).toBe('page')
    expect(wrapper.text()).toContain('content')
  })

  it('reuses the same shell for management tools', async () => {
    const wrapper = await mountShell('settings', 'management')
    const links = wrapper.get('nav[aria-label="管理"]').findAll('a')

    expect(wrapper.find('.content-workspace-nav').attributes('aria-label')).toBe('管理')
    expect(wrapper.find('.content-workspace-heading').exists()).toBe(false)
    expect(links.map(link => link.attributes('href'))).toEqual([
      '/memory', '/logs', '/plugins', '/settings',
    ])
    expect(links[3].attributes('aria-current')).toBe('page')
    expect(wrapper.find('.content-workspace-flow').exists()).toBe(false)
  })
})
