import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorldLorebookSummary from '../src/features/create/WorldLorebookSummary.vue'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../src/api/client', () => ({ api: mocks.api }))
const harborBooks = { books: [
  { id: 'world:harbor', name: '雾港基础资料', enabled: true, entry_count: 4, bindings: [{ scope_kind: 'world', scope_id: 'harbor', role: 'primary' }] },
  { id: 'npc', name: '港口人物', enabled: true, entry_count: 3, bindings: [{ scope_kind: 'world', scope_id: 'harbor' }] },
  { id: 'global', name: '通用词典', enabled: true, bindings: [{ scope_kind: 'global', scope_id: '' }] },
  { id: 'unbound', name: '未绑定资料', enabled: true, bindings: [] },
] }
const props = { worldId: 'harbor', source: '__builtin__', manageHref: '#/lorebook?world=harbor' }

describe('WorldLorebookSummary', () => {
  beforeEach(() => { i18n.global.locale.value = 'zh-CN'; mocks.api.mockReset() })
  it('shows inherited sources and lets the game choose the enabled books', async () => {
    mocks.api.mockResolvedValue(harborBooks)
    const wrapper = mount(WorldLorebookSummary, { props: { ...props, modelValue: ['world:harbor', 'npc', 'global'] }, global: { plugins: [i18n] } })
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledWith('/lorebooks?world_id=harbor')
    expect(wrapper.findAll('li')).toHaveLength(3)
    expect(wrapper.text()).toContain('港口人物')
    expect(wrapper.text()).toContain('全局共用')
    expect(wrapper.text()).toContain('选择只影响本局')
    expect(wrapper.get('summary').text()).toContain('已选 3 册')
    expect(wrapper.text()).not.toContain('未绑定资料')
    expect(wrapper.find('a').attributes('href')).toBe(props.manageHref)
    expect(wrapper.findAll('input[type=checkbox]')).toHaveLength(3)
    expect(wrapper.findAll('input[type=checkbox]').every(input => (input.element as HTMLInputElement).checked)).toBe(true)
    await wrapper.findAll('input[type=checkbox]')[1]!.setValue(false)
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toEqual(['world:harbor', 'global'])
    wrapper.unmount()
  })
  it('offers all source books as shared references for an independent world', async () => {
    mocks.api.mockResolvedValue(harborBooks)
    const wrapper = mount(WorldLorebookSummary, { props: { ...props, worldId: 'new', source: 'copy:harbor' }, global: { plugins: [i18n] } })
    await flushPromises()
    expect(wrapper.findAll('li')).toHaveLength(3)
    expect(wrapper.text()).toContain('条目不会复制')
    expect(wrapper.text()).toContain('港口人物')
    wrapper.unmount()
  })
  it('links a package world to the pack page instead of a dead world route', async () => {
    mocks.api.mockResolvedValue(harborBooks)
    const wrapper = mount(WorldLorebookSummary, {
      props: { ...props, worldId: 'frost_pass', manageHref: '#/modules/dnd-frostwhisper', manageKind: 'module' },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    const link = wrapper.get('footer a')
    expect(link.attributes('href')).toBe('#/modules/dnd-frostwhisper')
    expect(link.text()).toContain('查看包内资料')
    wrapper.unmount()
  })
  it('explains that a source-only world cannot be edited in place', async () => {
    mocks.api.mockResolvedValue({ books: [] })
    const wrapper = mount(WorldLorebookSummary, {
      props: { ...props, worldId: 'frost_pass', manageHref: '', manageKind: 'none' },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect(wrapper.find('footer a').exists()).toBe(false)
    expect(wrapper.get('footer .create-lorebook-readonly').text()).toContain('只读')
    wrapper.unmount()
  })
  it('preserves an explicit empty selection across retry', async () => {
    mocks.api.mockResolvedValue(harborBooks)
    const wrapper = mount(WorldLorebookSummary, { props: { ...props, modelValue: [] }, global: { plugins: [i18n] } })
    await flushPromises()
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toEqual([])
    expect(wrapper.emitted('ready')?.at(-1)?.[0]).toBe(true)
    wrapper.unmount()
  })
  it('reports a failed read as unknown and supports a read-only retry', async () => {
    mocks.api.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(harborBooks)
    const wrapper = mount(WorldLorebookSummary, { props, global: { plugins: [i18n] } })
    await flushPromises()
    expect(wrapper.find('[role=alert]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('目前没有匹配')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role=alert]').exists()).toBe(false)
    expect(wrapper.findAll('li')).toHaveLength(3)
    wrapper.unmount()
  })
  it('ignores a late response for the previous world', async () => {
    let resolveHarbor!: (value: typeof harborBooks) => void
    mocks.api.mockImplementation((path: string) => path.includes('harbor') ? new Promise(resolve => { resolveHarbor = resolve }) : Promise.resolve({ books: [{ id: 'world:forest', name: '森林资料', enabled: true, bindings: [{ scope_kind: 'world', scope_id: 'forest', role: 'primary' }] }] }))
    const wrapper = mount(WorldLorebookSummary, { props, global: { plugins: [i18n] } })
    await wrapper.setProps({ worldId: 'forest', manageHref: '#/lorebook?world=forest' })
    await flushPromises()
    expect(wrapper.text()).toContain('森林资料')
    resolveHarbor(harborBooks)
    await flushPromises()
    expect(wrapper.text()).toContain('森林资料')
    expect(wrapper.text()).not.toContain('雾港基础资料')
    wrapper.unmount()
  })
})
