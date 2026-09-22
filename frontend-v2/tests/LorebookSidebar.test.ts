import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LorebookSidebar from '../src/features/lorebook/LorebookSidebar.vue'
import { i18n } from '../src/i18n'

// jsdom 的 navigator 是 en-US；这些断言固定在产品默认语言 zh-CN 上。
i18n.global.locale.value = 'zh-CN'

const BOOKS = [
  { id: 'primary', name: 'Primary', primary: true, scope: 'world', enabled: true },
  { id: 'secondary', name: 'Secondary', scope: 'global', enabled: true },
  { id: 'off', name: 'Retired', scope: 'game', enabled: false },
]

function mountSidebar(props: Record<string, unknown> = {}) {
  return mount(LorebookSidebar, { props: { books: BOOKS, activeId: 'secondary', ...props }, global: { plugins: [i18n] } })
}

/** Book 条目按钮（排除筛选/操作按钮）。 */
function bookItems(wrapper: ReturnType<typeof mountSidebar>) {
  return wrapper.findAll('.lorebook-sidebar__item')
}

describe('LorebookSidebar', () => {
  it('uses the selected active id instead of assuming the primary book', () => {
    const wrapper = mountSidebar()
    const items = bookItems(wrapper)
    expect(items[0].classes()).not.toContain('active')
    expect(items[1].classes()).toContain('active')
  })

  it('shows name, Primary, scope and enabled state for every book', () => {
    const wrapper = mountSidebar()
    const items = bookItems(wrapper)
    expect(items[0].text()).toContain('Primary')
    expect(items[0].text()).toContain('当前世界')
    expect(items[0].text()).toContain('启用中')
    expect(items[1].text()).toContain('全局')
    expect(items[2].text()).toContain('已停用')
    expect(items[2].classes()).toContain('disabled')
  })

  it('filters books by search text', async () => {
    const wrapper = mountSidebar()
    await wrapper.find('.lorebook-sidebar__search').setValue('retired')
    const items = bookItems(wrapper)
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('Retired')
  })

  it('filters books by scope', async () => {
    const wrapper = mountSidebar()
    await wrapper.find('.lorebook-sidebar__scope').setValue('global')
    const items = bookItems(wrapper)
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('Secondary')
  })

  it('offers delete only for non-primary books', () => {
    const wrapper = mountSidebar()
    const rows = wrapper.findAll('.lorebook-sidebar__row')
    expect(rows[0].find('button.danger').exists()).toBe(false)
    expect(rows[1].find('button.danger').exists()).toBe(true)
  })

  it('emits the management actions with the book they target', async () => {
    const wrapper = mountSidebar()
    const row = wrapper.findAll('.lorebook-sidebar__row')[1]
    await row.get('[aria-label="重命名 Secondary"]').trigger('click')
    await row.get('[aria-label="绑定 Secondary"]').trigger('click')
    await row.get('[aria-label="停用 Secondary"]').trigger('click')
    await row.get('[aria-label="删除 Secondary"]').trigger('click')
    expect(wrapper.emitted('rename')?.[0]).toEqual([BOOKS[1]])
    expect(wrapper.emitted('bindings')?.[0]).toEqual([BOOKS[1]])
    expect(wrapper.emitted('toggle-enabled')?.[0]).toEqual([BOOKS[1]])
    expect(wrapper.emitted('remove')?.[0]).toEqual([BOOKS[1]])
  })

  it('emits create / import / export from the workspace actions', async () => {
    const wrapper = mountSidebar()
    await wrapper.get('.lorebook-sidebar__new').trigger('click')
    const actions = wrapper.findAll('.lorebook-sidebar__actions button')
    await actions[1].trigger('click')
    await actions[2].trigger('click')
    expect(wrapper.emitted('create')).toHaveLength(1)
    expect(wrapper.emitted('import')).toHaveLength(1)
    expect(wrapper.emitted('export')).toHaveLength(1)
  })
})
