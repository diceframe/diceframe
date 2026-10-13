import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LorebookView from '../src/features/lorebook/LorebookView.vue'
import LoreImportDialog from '../src/features/lorebook/LoreImportDialog.vue'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api }
})
vi.mock('../src/peer/game/bridge', () => ({ activePeerGameClient: () => null }))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('../src/composables/useConfirm', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))

const primary = { id: 'world:w1', name: '主世界书', primary: true, scope: 'world' }
const imported = { id: 'book:imported', name: '导入世界书', scope: 'world' }
const preview = { format: 'lorebook_v3', counts: { entries: 1, mapped: 1, warnings: 0, unsupported: 0 }, warnings: [] }
const decision = { bookId: null, binding: { scope_kind: 'world', scope_id: 'w1' } }
let wrapper: ReturnType<typeof mount> | undefined

async function openImport() {
  wrapper = mount(LorebookView, { global: { plugins: [i18n], stubs: { teleport: true } } })
  await flushPromises()
  const file = new File(['{}'], 'lorebook.json', { type: 'application/json' })
  Object.defineProperty(file, 'text', { value: async () => '{}' })
  const input = wrapper.get('input[type=file]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
  expect(wrapper.find('[role=dialog]').exists()).toBe(true)
  return wrapper
}

function importCalls() {
  return mocks.api.mock.calls.filter(([path]) => path === '/lorebooks/import')
}

describe('Lorebook import completion', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
    mocks.api.mockReset()
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/worlds') return { worlds: [{ id: 'w1', name: '测试世界', language: 'zh-CN' }] }
      if (path === '/lorebooks/import/preview') return preview
      if (path === '/lorebooks/import') return { ok: true, book_id: imported.id }
      if (path.startsWith('/lorebooks?')) return { books: [primary, imported] }
      if (path.includes('/entries')) return { entries: [] }
      if (path.includes('/preview')) return { projections: {}, summary: { total: 0 } }
      throw new Error(`unexpected path ${path}`)
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  it('keeps the dialog locked until refresh settles, then preserves the next book selection', async () => {
    const normalApi = mocks.api.getMockImplementation()!
    let resolveBooks!: (value: { books: typeof primary[] }) => void
    const pendingBooks = new Promise<{ books: typeof primary[] }>(resolve => { resolveBooks = resolve })
    let importStarted = false
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/lorebooks/import') importStarted = true
      if (importStarted && path.startsWith('/lorebooks?')) return pendingBooks
      return normalApi(path)
    })
    const view = await openImport()
    const dialog = view.getComponent(LoreImportDialog)
    dialog.vm.$emit('confirm', decision)
    await flushPromises()

    expect(view.find('[role=dialog]').exists()).toBe(true)
    expect(view.get('.lore-import-dialog__buttons button').attributes('disabled')).toBeDefined()
    expect(view.get('.lore-import-dialog__buttons button:last-child').attributes('disabled')).toBeDefined()
    dialog.vm.$emit('confirm', decision)
    dialog.vm.$emit('close')
    await flushPromises()
    expect(importCalls()).toHaveLength(1)
    expect(view.find('[role=dialog]').exists()).toBe(true)

    resolveBooks({ books: [primary, { ...primary, ...imported, primary: false }] })
    await flushPromises()
    expect(view.find('[role=dialog]').exists()).toBe(false)
    const select = view.get('select[aria-label="当前世界书"]')
    expect((select.element as HTMLSelectElement).value).toBe(imported.id)
    await select.setValue(primary.id)
    await flushPromises()
    expect((select.element as HTMLSelectElement).value).toBe(primary.id)
    expect(view.get('.lore-book-meta').text()).toContain('主世界书')
    expect(view.findAll('.lore-context-row').at(-1)!.find('.lore-menu-list button.danger').exists()).toBe(false)
  })

  it('unlocks a rejected import so the same explicit decision can be retried', async () => {
    const normalApi = mocks.api.getMockImplementation()!
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/lorebooks/import' && importCalls().length === 1) throw new Error('temporary failure')
      return normalApi(path)
    })
    const view = await openImport()
    const dialog = view.getComponent(LoreImportDialog)
    dialog.vm.$emit('confirm', decision)
    await flushPromises()
    expect(view.find('[role=dialog]').exists()).toBe(true)
    expect(view.get('.lore-import-dialog__buttons button:last-child').attributes('disabled')).toBeUndefined()
    expect(view.text()).toContain('temporary failure')
    dialog.vm.$emit('confirm', decision)
    await flushPromises()
    expect(importCalls()).toHaveLength(2)
    expect(view.find('[role=dialog]').exists()).toBe(false)
  })
})
