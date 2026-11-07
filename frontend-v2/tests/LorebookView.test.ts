import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import LorebookView from '../src/features/lorebook/LorebookView.vue'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  route: { query: {} as Record<string, unknown> },
  router: { replace: vi.fn() },
  confirm: vi.fn(),
}))

vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api, errorMessage: (cause: unknown) => String((cause as Error)?.message || cause) }
})
vi.mock('../src/peer/game/bridge', () => ({
  activePeerGameClient: () => null,
  setActivePeerGameClient: vi.fn(),
}))
vi.mock('vue-router', () => ({
  onBeforeRouteLeave: vi.fn(),
  onBeforeRouteUpdate: vi.fn(),
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: mocks.confirm }),
}))

// These fixtures model an older server that only exposes the legacy adapter:
// aiohttp answers an unknown canonical route with a bare 404 (no JSON body).
function oldServerRouteMissing(path: string): void {
  if (path.startsWith('/lorebooks/')) throw new ApiError('HTTP 404', 404)
}

const worlds = {
  worlds: [{ id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 3 }],
}

const games = {
  games: [{ game_key: 'g1', world_id: 'w1', language: 'zh-CN' }],
}

const characters = {
  players: [
    { user_id: 'u1', character_name: '莱拉' },
    { user_id: 'u2', character_name: '布兰' },
  ],
  rule_meta: {},
}

const lorebook = {
  entries: [
    { id: 'a', world_id: 'w1', name: '城门守卫', type: 'npc', content: '公开背景', visible_to: ['public'] },
    { id: 'b', world_id: 'w1', name: '秘血教派', type: 'faction', content: '莱拉的私人线索', visible_to: ['莱拉'] },
    { id: 'c', world_id: 'w1', name: '幕后黑手', type: 'npc', content: 'GM 秘密', visible_to: [] },
  ],
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>(resolver => { resolve = resolver })
  return { promise, resolve }
}

function previewFor(path: string) {
  const params = new URLSearchParams(path.split('?')[1] || '')
  const viewer = params.get('viewer') || 'gm'
  const projections = {
    a: { visible: true, audience: 'public', subjects: [] },
    b: { visible: viewer === 'gm' || viewer === 'u1', audience: 'character', subjects: ['莱拉'] },
    c: { visible: viewer === 'gm', audience: 'gm', subjects: [] },
  }
  const visible = Object.values(projections).filter(p => p.visible).length
  return {
    ok: true,
    world_id: 'w1',
    viewer: { kind: viewer === 'gm' || viewer === 'party' ? viewer : 'character', uid: viewer },
    projections,
    summary: { total: 3, visible, public: 1, character_only: 1, gm_secret: 1 },
  }
}

  function mountView(attachToBody = false, openInspector = true) {
    if (openInspector && localStorage.getItem('lore_inspector_open') === null) localStorage.setItem('lore_inspector_open', '1')
  return mount(LorebookView, {
    global: { plugins: [i18n] },
    attachTo: attachToBody ? document.body : undefined,
  })
}

describe('LorebookView perspective inspector', () => {
  const scopedBooks = [
    { id: 'world:w1', name: '第一世界主册', scope: 'world', language: 'zh-CN', primary: true },
    { id: 'side:w1', name: '第一世界人物志', scope: 'world', language: 'zh-CN' },
    { id: 'world:w2', name: '第二世界主册', scope: 'world', language: 'zh-CN', primary: true },
    { id: 'world:w1-en', name: 'English primary book', scope: 'world', language: 'en', primary: true },
    { id: 'side:w1-en', name: 'English characters', scope: 'world', language: 'en' },
    { id: 'global:zh', name: '中文通用资料', scope: 'global', language: 'zh-CN' },
    { id: 'global:en', name: 'English reference', scope: 'global', language: 'en' },
  ]
  function scopeFixtures() {
    mocks.router.replace.mockImplementation(({ query }: { query: Record<string, unknown> }) => { mocks.route.query = query })
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/worlds') return { worlds: [
        { id: 'w1', name: '第一世界', language: 'zh-CN' },
        { id: 'w2', name: '第二世界', language: 'zh-CN' },
        { id: 'w1-en', name: 'English World', language: 'en' },
      ] }
      if (path === '/games') return { games: [] }
      if (path.includes('/characters')) return { players: [] }
      if (path.startsWith('/lorebooks?')) {
        const query = new URLSearchParams(path.split('?')[1])
        const world = query.get('world_id') || ''
        return { books: query.get('scope') === 'all' ? scopedBooks
          : scopedBooks.filter(book => book.scope === 'global' || book.id === `world:${world}` || book.id === `side:${world}`) }
      }
      if (path.endsWith('/entries')) return { entries: [] }
      return { ok: true }
    })
  }

  it('keeps a library book link scoped to its world and content language', async () => {
    mocks.route.query = { world: 'w1', book: 'side:w1' }
    scopeFixtures()
    const wrapper = mountView(true, false)
    await flushPromises(); await flushPromises()
    expect(wrapper.findAll('.lore-context-row--book option').map(option => option.attributes('value'))).toEqual(['world:w1', 'side:w1', 'global:zh'])
    expect(wrapper.get('.lore-context-row--book select').element).toHaveProperty('value', 'side:w1')
    wrapper.unmount()
  })

  it('looks up only the linked out-of-scope book without exposing the rest of the catalogue', async () => {
    mocks.route.query = { world: 'w1', book: 'orphan' }
    scopeFixtures()
    const scopedApi = mocks.api.getMockImplementation()!
    mocks.api.mockImplementation((path: string) => path === '/lorebooks?scope=all'
      ? Promise.resolve({ books: [...scopedBooks, { id: 'orphan', name: '独立目标资料册', scope: 'world', language: 'zh-CN' }] })
      : scopedApi(path))
    const wrapper = mountView(true, false)
    await flushPromises(); await flushPromises()
    expect(wrapper.get('.lore-context-row--book select').element).toHaveProperty('value', 'orphan')
    expect(wrapper.findAll('.lore-context-row--book option').map(option => option.attributes('value'))).toEqual(['world:w1', 'side:w1', 'global:zh', 'orphan'])
    expect(mocks.api.mock.calls.some(([path]) => path === '/lorebooks?scope=all')).toBe(true)
    wrapper.unmount()
  })

  it('discards the previous book and entry target when manually changing the browsing world', async () => {
    mocks.route.query = { world: 'w1', book: 'side:w1', entry: 'old-entry', extra: 'keep' }
    scopeFixtures()
    const wrapper = mountView(true, false)
    await flushPromises(); await flushPromises()
    await wrapper.get('.lore-context-row--world select').setValue('w2')
    await flushPromises(); await flushPromises()
    expect(mocks.route.query).toEqual({ world: 'w2', extra: 'keep' })
    expect(wrapper.get('.lore-context-row--book select').element).toHaveProperty('value', 'world:w2')
    expect(wrapper.findAll('.lore-context-row--book option').map(option => option.attributes('value'))).toEqual(['world:w2', 'global:zh'])
    wrapper.unmount()
  })

  it('selects translated-world books when changing content language from a linked book', async () => {
    mocks.route.query = { world: 'w1', book: 'side:w1', entry: 'old-entry' }
    scopeFixtures()
    const wrapper = mountView(true, false)
    await flushPromises(); await flushPromises()
    await wrapper.get('.lore-language-filter select').setValue('en')
    await flushPromises(); await flushPromises()
    expect(mocks.route.query).toEqual({ world: 'w1-en' })
    expect(wrapper.get('.lore-context-row--book select').element).toHaveProperty('value', 'world:w1-en')
    expect(wrapper.findAll('.lore-context-row--book option').map(option => option.attributes('value'))).toEqual(['world:w1-en', 'side:w1-en', 'global:en'])
    expect(localStorage.getItem('currentGame')).toBe('g1')
    wrapper.unmount()
  })

  it('does not delete an entry in a newly selected book when a prior confirmation finishes late', async () => {
    const decision = deferred<boolean>()
    mocks.confirm.mockReturnValueOnce(decision.promise)
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/lorebooks?')) return { books: [{ id: 'world:w1', name: '主资料册' }, { id: 'side', name: '侧资料册' }] }
      if (path.includes('/preview')) return previewFor(path)
      if (path.includes('/characters')) return characters
      if (path.includes('/games')) return games
      if (path.endsWith('/entries')) return lorebook
      if (path.includes('/worlds')) return worlds
      throw new Error(`unexpected path ${path}`)
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('.lore-row .danger').trigger('click')
    await wrapper.get('.lore-context-row--book select').setValue('side')
    await flushPromises()
    decision.resolve(true)
    await flushPromises()
    expect(mocks.api.mock.calls.some(call => (call[1] as RequestInit)?.method === 'DELETE')).toBe(false)
    wrapper.unmount()
  })

  it('keeps an unsaved detail draft when the user chooses to continue editing', async () => {
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('.lore-row').trigger('click')
    await wrapper.get('[data-testid="lore-detail-edit"]').trigger('click')
    await wrapper.get('[data-testid="lore-inline-editor"] input').setValue('尚未保存')
    mocks.confirm.mockResolvedValueOnce(false)
    await wrapper.get('[data-testid="lore-detail-back"]').trigger('click')
    await flushPromises()
    expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ positiveText: '放弃修改', negativeText: '继续编辑' }))
    expect((wrapper.get('[data-testid="lore-inline-editor"] input').element as HTMLInputElement).value).toBe('尚未保存')
    mocks.confirm.mockResolvedValueOnce(true)
    await wrapper.get('[data-testid="lore-detail-back"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="lore-inline-editor"]').exists()).toBe(false)
    expect(mocks.api.mock.calls.some(call => (call[1] as RequestInit)?.method)).toBe(false)
    wrapper.unmount()
  })

  it('opens a readable adjacent detail and edits without hiding the list', async () => {
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('.lore-row').trigger('click')
    expect(wrapper.get('[data-testid="lore-entry-detail"]').text()).toContain('公开背景')
    await wrapper.get('[data-testid="lore-detail-edit"]').trigger('click')
    expect(wrapper.find('[data-testid="lore-inline-editor"]').exists()).toBe(true)
    expect(wrapper.findAll('.lore-row')).toHaveLength(3)
    expect(document.body.querySelector('[aria-modal="true"]')).toBeNull()
    await wrapper.get('[data-testid="lore-detail-back"]').trigger('click')
    expect(wrapper.find('[data-testid="lore-inline-editor"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps world and book selection as separate aligned management cards', async () => {
    const wrapper = mountView(true, false)
    await flushPromises()
    const grid = wrapper.get('.lore-selection-grid')
    const world = grid.get('.lore-context-row--world')
    const book = grid.get('.lore-context-row--book')
    expect(world.get('.lore-context-label').text()).toContain('世界')
    expect(book.get('.lore-context-label').text()).toContain('资料册')
    expect(world.find('.lore-context-controls [data-testid="world-actions"]').exists()).toBe(true)
    expect(book.find('.lore-context-controls [data-testid="book-actions"]').exists()).toBe(true)
    expect(book.find('.lore-context-controls .lore-book-meta').exists()).toBe(true)
    expect(grid.find(':scope > .lore-book-meta').exists()).toBe(false)
    expect(wrapper.find('.archive-hero .section-kicker').exists()).toBe(true)
    wrapper.unmount()
  })

  it('makes lorebook deletion explicit while explaining primary-book protection', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/lorebooks?')) return { books: [{ id: 'world:w1', name: '主资料册', primary: true }, { id: 'side', name: '侧资料册', primary: false }] }
      if (path.includes('/preview')) return previewFor(path)
      if (path.includes('/characters')) return characters
      if (path.includes('/games')) return games
      if (path.endsWith('/entries')) return lorebook
      if (path.includes('/worlds')) return worlds
      return { ok: true }
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('[data-testid="book-actions"]').trigger('click')
    expect(wrapper.get('.lore-menu-note').text()).toContain('主资料册随世界管理')
    expect(wrapper.findAll('.lore-menu-list button').filter(button => button.text() === '删除资料册')).toHaveLength(0)
    await wrapper.get('[data-testid="book-actions"]').trigger('click')
    await wrapper.get('.lore-context-row--book select').setValue('side')
    await flushPromises()
    await wrapper.get('[data-testid="book-actions"]').trigger('click')
    expect(wrapper.get('.lore-context-row--book .lore-menu-list button.danger').text()).toBe('删除资料册')
    expect(wrapper.find('.lore-context-row--book .lore-menu-note').exists()).toBe(false)
    wrapper.unmount()
  })

  it('focuses an entry when only the entry query changes without discarding an editor draft', async () => {
    const wrapper = mountView(true, false)
    await flushPromises()
    mocks.route.query.entry = 'b'
    await flushPromises()
    expect(wrapper.get('.lore-row.selected').text()).toContain('秘血教派')
    await wrapper.get('.lore-row.selected').trigger('click')
    await wrapper.get('[data-testid="lore-detail-edit"]').trigger('click')
    await wrapper.get('[data-testid="lore-inline-editor"] input').setValue('保留中的草稿')
    mocks.route.query.entry = 'a'
    await flushPromises()
    expect(wrapper.get('[data-testid="lore-inline-editor"] input').element).toHaveProperty('value', '保留中的草稿')
    wrapper.unmount()
  })

  it('uses the shared world definition editor with full independent fields', async () => {
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      const p = String(path)
      if (p.includes('/definition')) {
        return {
          ok: true,
          world: {
            world_id: 'w1', world_name: '测试世界', description: '完整简述',
            world_setting: '完整世界背景', starter_scene: '默认开场',
            language: 'zh-CN', source: 'user', editable: true,
          },
        }
      }
      if ((init?.method || 'GET') === 'PUT') return { ok: true }
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return worlds
      throw new Error(`unexpected path ${p}`)
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('[data-testid="world-actions"]').trigger('click')
    await wrapper.findAll('.lore-context-row--world .lore-menu-list button').at(1)?.trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="world-setting"]').element).toHaveProperty('value', '完整世界背景')
    expect(wrapper.get('[data-testid="starter-scene"]').element).toHaveProperty('value', '默认开场')
    await wrapper.get('[data-testid="world-setting"]').setValue('更新后的背景')
    await wrapper.get('[data-testid="starter-scene"]').setValue('更新后的开场')
    await wrapper.get('.lore-world-editor .world-definition-actions button.primary').trigger('click')
    await flushPromises()

    const save = mocks.api.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'PUT')
    expect(JSON.parse(String((save?.[1] as RequestInit).body))).toMatchObject({
      name: '测试世界', description: '完整简述', language: 'zh-CN',
      world_setting: '更新后的背景', starter_scene: '更新后的开场',
    })
    expect(wrapper.find('.lore-world-editor').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps a builtin world read-only in the inline editor', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/definition')) {
        return {
          ok: true,
          world: {
            world_id: 'w1', world_name: '测试世界', description: '内置简述',
            world_setting: '内置完整背景', starter_scene: '内置开场',
            language: 'zh-CN', source: 'builtin', editable: false,
          },
        }
      }
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return worlds
      return { ok: true }
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('[data-testid="world-actions"]').trigger('click')
    await wrapper.findAll('.lore-context-row--world .lore-menu-list button').at(1)?.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="world-setting"]').attributes('readonly')).toBeDefined()
    expect(wrapper.find('.world-definition-actions .world-card-clone').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps the edited world selected when its content language changes', async () => {
    let saved = false
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if ((init?.method || 'GET') === 'PUT') { saved = true; return { ok: true } }
      if (path.includes('/definition')) return { ok: true, world: {
        world_id: 'w1', world_name: '测试世界', description: '简介',
        world_setting: '完整背景', starter_scene: '默认开场',
        language: 'zh-CN', source: 'user', editable: true,
      } }
      if (path.includes('/preview')) return previewFor(path)
      if (path.includes('/characters')) return characters
      if (path.includes('/games')) return games
      if (path.endsWith('/entries') || path.includes('/lorebook/')) return lorebook
      if (path === '/worlds') return { worlds: [
        { id: 'w1', name: '测试世界', language: saved ? 'en' : 'zh-CN' },
        { id: 'w2', name: 'Another world', language: 'en' },
      ] }
      if (path.startsWith('/lorebooks?')) return { books: [] }
      throw new Error(`unexpected path ${path}`)
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.findAll('.lore-context-row--world .lore-menu-list button').at(1)?.trigger('click')
    await flushPromises()
    await wrapper.get('.world-metadata-editor select').setValue('en')
    await wrapper.get('.world-definition-actions button.primary').trigger('click')
    await flushPromises()
    expect(wrapper.get('.lore-context-row--world select').element).toHaveProperty('value', 'w1')
    expect(wrapper.get('.lore-language-filter select').element).toHaveProperty('value', 'en')
    expect(localStorage.getItem('currentGame')).toBe('g1')
    wrapper.unmount()
  })

  it('clears an editor when its canonical book changes so it cannot save to a different book', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/lorebooks?')) return { books: [{ id: 'world:w1', name: '主资料册' }, { id: 'side', name: '侧资料册' }] }
      if (path.includes('/preview')) return previewFor(path)
      if (path.includes('/characters')) return characters
      if (path.includes('/games')) return games
      if (path.endsWith('/entries')) return lorebook
      if (path.includes('/worlds')) return worlds
      throw new Error(`unexpected path ${path}`)
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('.lore-row').trigger('click')
    await wrapper.get('[data-testid="lore-detail-edit"]').trigger('click')
    await wrapper.get('.lore-context-row--book select').setValue('side')
    await flushPromises()
    expect(wrapper.find('[data-testid="lore-inline-editor"]').exists()).toBe(false)
    expect(mocks.api.mock.calls.some(call => (call[1] as RequestInit)?.method)).toBe(false)
    wrapper.unmount()
  })

  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('currentGame', 'g1')
    i18n.global.locale.value = 'zh-CN'
    mocks.api.mockReset()
    mocks.confirm.mockReset().mockResolvedValue(true)
    mocks.router.replace.mockReset()
    mocks.route = reactive({ query: {} })
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.startsWith('/lorebooks/usage?')) return {
        ok: true,
        library: { total: 1, enabled: 1, disabled: 0, books: [{ id: 'world:w1', name: '主资料册', enabled: true, scope: 'world', entry_count: 3 }] },
        game: { status: 'ready', game_key: 'g1', world_id: 'w1', world_name: '测试世界', count: 1, books: [{ id: 'world:w1', name: '主资料册', enabled: true, scope: 'world', entry_count: 3 }] },
      }
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return worlds
      oldServerRouteMissing(p)
      throw new Error(`unexpected path ${p}`)
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.unstubAllGlobals()
  })

  it('integrates the backend usage summary into the lorebook context', async () => {
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    const usage = wrapper.get('[data-testid="lorebook-usage"]')
    expect(usage.text()).toContain('资料库已启用 1 / 1 本')
    expect(usage.text()).toContain('本局使用 1 本')
    expect(wrapper.find('[data-testid="lorebook-usage-library"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="lorebook-usage-game"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('browses a valid book independently when its canonical world is unavailable', async () => {
    mocks.route.query = { world: 'missing-world', book: 'world:missing-world' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/worlds') return { worlds: [] }
      if (p === '/games') return { games: [] }
      if (p.includes('/characters')) return { players: [] }
      if (p.startsWith('/lorebooks?')) return { books: [{ id: 'world:missing-world', name: '孤立资料册', scope: 'world', entry_count: 2 }] }
      if (p.includes('/lorebooks/world%3Amissing-world/entries')) return { entries: [
        { id: 'orphan-one', name: '孤立条目一', type: 'npc', content: '保留内容一' },
        { id: 'orphan-two', name: '孤立条目二', type: 'npc', content: '保留内容二' },
      ] }
      if (p.startsWith('/lorebooks/usage?')) return { ok: true, library: { total: 1, enabled: 1, disabled: 0, books: [] }, game: { status: 'none', count: 0, books: [] } }
      return { ok: true }
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await flushPromises()
    expect(wrapper.get('.lore-context-row--world select').element).toHaveProperty('value', '')
    expect(wrapper.get('.lore-book-static').text()).toContain('孤立资料册')
    expect(wrapper.text()).toContain('世界 “missing-world” 不存在')
    expect(wrapper.text()).toContain('按资料册独立浏览')
    expect(wrapper.get('.lore-row').text()).toContain('孤立条目一')
    expect(wrapper.get('.lore-tools .primary.success').attributes('disabled')).toBeUndefined()
    await wrapper.get('.lore-language-filter select').setValue('en')
    await flushPromises()
    expect(wrapper.get('.lore-context-row--world select').element).toHaveProperty('value', '')
    wrapper.unmount()
  })

  it('reopens the exact book when a reused route changes from a valid world to a missing world', async () => {
    mocks.route.query = { world: 'w1', book: 'side' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/worlds') return { worlds: [{ id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 1 }] }
      if (p === '/games') return { games: [] }
      if (p.includes('/characters')) return { players: [] }
      if (p.startsWith('/lorebooks?')) return { books: [{ id: 'side', name: '侧资料册', scope: 'world', entry_count: 1 }] }
      if (p.includes('/lorebooks/side/entries')) return { entries: [{ id: 'side-entry', name: '侧资料册条目', type: 'npc', content: 'side' }] }
      if (p.startsWith('/lorebooks/usage?')) return { ok: true, library: { total: 1, enabled: 1, disabled: 0, books: [] }, game: { status: 'none', count: 0, books: [] } }
      return { ok: true }
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await flushPromises()
    expect(wrapper.get('.lore-row').text()).toContain('侧资料册条目')
    mocks.route.query.world = 'missing-world'
    await flushPromises()
    await flushPromises()
    expect(wrapper.get('.lore-context-row--world select').element).toHaveProperty('value', '')
    expect(wrapper.get('.lore-book-static').text()).toContain('侧资料册')
    expect(wrapper.get('.lore-row').text()).toContain('侧资料册条目')
    delete mocks.route.query.book
    await flushPromises()
    await flushPromises()
    expect(wrapper.get('.error-banner').text()).toContain('世界 “missing-world” 不存在')
    expect(wrapper.findAll('.lore-row')).toHaveLength(0)
    wrapper.unmount()
  })

  it('shows the normal empty-book state and primary badge for an orphaned empty primary book', async () => {
    mocks.route.query = { world: 'missing-world', book: 'world:missing-world' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/worlds') return { worlds: [] }
      if (p === '/games') return { games: [] }
      if (p.includes('/characters')) return { players: [] }
      if (p.startsWith('/lorebooks?')) return { books: [{ id: 'world:missing-world', name: '孤立主资料册', primary: true, scope: 'world', entry_count: 0 }] }
      if (p.includes('/lorebooks/world%3Amissing-world/entries')) return { entries: [] }
      if (p.startsWith('/lorebooks/usage?')) return { ok: true, library: { total: 1, enabled: 1, disabled: 0, books: [] }, game: { status: 'none', count: 0, books: [] } }
      return { ok: true }
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await flushPromises()
    expect(wrapper.get('.lore-context-row--book .lore-book-meta').text()).toContain('主资料册')
    expect(wrapper.get('.lore-entry-list-pane .empty-panel h2').text()).toBe('暂无资料册条目')
    wrapper.unmount()
  })

  it('keeps independent browsing and a later manual book selection through refresh', async () => {
    mocks.route.query = { book: 'world:missing-world' }
    const books = [
      { id: 'world:missing-world', name: '孤立资料册', primary: true, scope: 'world', ref: { kind: 'lorebook', source_kind: 'world', source_id: 'missing-world' } },
      { id: 'side', name: '另一本资料册', scope: 'world', bindings: [{ scope_kind: 'world', scope_id: 'w1' }], ref: { kind: 'lorebook', source_kind: 'world', source_id: 'w1' } },
    ]
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/worlds') return { worlds: [{ id: 'w1', name: '已存在世界', language: 'zh-CN' }] }
      if (p === '/games') return { games: [] }
      if (p.includes('/characters')) return { players: [] }
      if (p.startsWith('/lorebooks?')) return { books: p.includes('scope=all') ? books : [] }
      if (p.includes('/entries')) return { entries: [{ id: 'entry', name: '资料条目' }] }
      return { ok: true }
    })
    const wrapper = mountView(true, false)
    await flushPromises()
    await wrapper.get('.lore-language-filter select').setValue('en')
    await flushPromises()
    expect(wrapper.get('.lore-context-row--world select').element).toHaveProperty('value', '')
    await wrapper.get('.lore-context-row--book select').setValue('side')
    await flushPromises()
    expect(wrapper.find('.lore-context-warning').exists()).toBe(false)
    expect(wrapper.get('[data-testid="lore-scope-chip"]').text()).not.toContain('关联世界不可用')
    const refresh = wrapper.findAll('.lore-header-actions button').find(button => button.text() === '刷新')!
    await refresh.trigger('click')
    await flushPromises()
    await flushPromises()
    expect(wrapper.get('.lore-context-row--book select').element).toHaveProperty('value', 'side')
    expect(wrapper.get('.lore-context-row--world select').element).toHaveProperty('value', '')
    wrapper.unmount()
  })

  function stubNarrowViewport() {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  }

  it('keeps the canonical primary book id and path when the book list request fails', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.startsWith('/lorebooks?')) throw new Error('book list unavailable')
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p === '/lorebooks/world%3Aw1/entries') return lorebook
      if (p.includes('/worlds')) return worlds
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    expect(wrapper.get('.lore-book-static').text()).toContain('测试世界')
    expect(mocks.api.mock.calls.some(call => String(call[0]) === '/lorebook/w1')).toBe(false)
    expect(mocks.api.mock.calls.some(call => String(call[0]) === '/lorebooks/world%3Aw1/entries')).toBe(true)
    wrapper.unmount()
  })

  it('keeps world, lorebook, and entry context visible with a scoped book state', async () => {
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    const crumbs = wrapper.get('[data-testid="lore-context-crumbs"]')
    expect(crumbs.text()).toContain('世界')
    expect(crumbs.text()).toContain('测试世界')
    expect(crumbs.text()).toContain('资料册')
    expect(crumbs.text()).toContain('条目')

    expect(wrapper.get('[data-testid="world-actions"]').text()).toBe('世界操作')
    expect(wrapper.get('[data-testid="book-actions"]').text()).toBe('资料册操作')
    expect(wrapper.get('.lore-world-gallery-link').attributes('href')).toBe('#/worlds')
    expect(wrapper.get('.lore-world-gallery-link').text()).toContain('返回世界画廊')
    expect(wrapper.get('[data-testid="lore-scope-chip"]').text()).toContain('使用范围')
    expect(wrapper.get('[data-testid="lore-scope-chip"]').text()).toContain('当前世界')
    wrapper.unmount()
  })

  it('preselects a valid route world without replacing the current game world', async () => {
    mocks.route.query = { world: 'w1' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return { games: [{ game_key: 'g1', world_id: 'other-world', language: 'zh-CN' }] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return worlds
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w1')
    expect(mocks.api.mock.calls.some(call => String(call[0]).includes('/games/') && (call[1] as RequestInit)?.method)).toBe(false)
    wrapper.unmount()
  })

  it('does not fall back to the game world when the route world is invalid', async () => {
    mocks.route.query = { world: 'missing-world' }
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('世界 “missing-world” 不存在')
    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('')
    expect(mocks.api.mock.calls.some(call => {
      const path = String(call[0])
      return path.startsWith('/lorebooks') && !path.startsWith('/lorebooks/usage?')
    })).toBe(false)
    wrapper.unmount()
  })

  it('updates the browsing target when the route query changes in place', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [] }
      if (p.includes('/lorebooks/world%3A')) return { entries: [] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return { worlds: [
        { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 3 },
        { id: 'w2', name: '第二世界', language: 'zh-CN', entry_count: 0 },
      ] }
      throw new Error(`unexpected path ${p}`)
    })
    mocks.route.query = { world: 'w1' }
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    mocks.route.query.world = 'w2'
    await flushPromises()
    await flushPromises()

    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w2')
    expect(wrapper.get('[data-testid="lore-context-crumbs"]').text()).toContain('第二世界')
    wrapper.unmount()
  })

  it('syncs a manual world choice back to the existing browsing query', async () => {
    mocks.route.query = { world: 'w1' }
    mocks.router.replace.mockImplementation(({ query }: { query: Record<string, unknown> }) => { mocks.route.query = query })
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [] }
      if (p.includes('/lorebooks/world%3A')) return { entries: [] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return { worlds: [
        { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 3 },
        { id: 'w2', name: '第二世界', language: 'zh-CN', entry_count: 1 },
      ] }
      throw new Error(`unexpected path ${p}`)
    })
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    await wrapper.get('.lore-context-select').setValue('w2')
    await flushPromises()

    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w2')
    expect(mocks.router.replace).toHaveBeenCalledWith({ query: { world: 'w2' } })
    wrapper.unmount()
  })

  it('labels the world option as a total and names the book count only when there are several', async () => {
    mocks.route.query = {}
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [] }
      if (p.includes('/lorebooks/world%3A')) return { entries: [] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return { worlds: [
        { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 3, book_count: 1 },
        { id: 'w2', name: '多书世界', language: 'zh-CN', entry_count: 11, book_count: 2 },
      ] }
      throw new Error(`unexpected path ${p}`)
    })
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    const labels = wrapper.findAll('.lore-context-select option').map(option => option.text())
    // 单书世界的两个数字必然相等，不写本数以免看起来像重复。
    expect(labels).toContain('测试世界 · 3 条')
    // 多书世界点明本数，读得出「11 条」是合计而不是主册自己的。
    expect(labels).toContain('多书世界 · 2 本 · 11 条')
    wrapper.unmount()
  })

  it('syncs a language-filter choice to a canonical translated world and keeps it after remount', async () => {
    mocks.route.query = { world: 'w1' }
    mocks.router.replace.mockImplementation(({ query }: { query: Record<string, unknown> }) => { mocks.route.query = query })
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [] }
      if (p.includes('/lorebooks/world%3A')) return { entries: [] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return { worlds: [
        { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 3 },
        { id: 'w1-en', name: 'Test World', language: 'en', entry_count: 3 },
      ] }
      throw new Error(`unexpected path ${p}`)
    })
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    await wrapper.get('.lore-language-filter select').setValue('en')
    await flushPromises()
    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w1-en')
    expect(mocks.router.replace).toHaveBeenCalledWith({ query: { world: 'w1-en' } })
    wrapper.unmount()

    // A back/refresh-style remount reads the canonical query target again.
    mocks.router.replace.mockReset()
    const remounted = mountView()
    await flushPromises()
    await flushPromises()
    expect((remounted.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w1-en')
    remounted.unmount()
  })

  it('clears the previous editable entries when an in-place route target becomes invalid', async () => {
    mocks.route.query = { world: 'w1' }
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('城门守卫')
    mocks.route.query.world = 'missing-world'
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('世界 “missing-world” 不存在')
    expect(wrapper.find('.lore-row').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('城门守卫')
    wrapper.unmount()
  })

  it('ignores a late lore response from the previous browsing world', async () => {
    mocks.route.query = { world: 'w1' }
    let resolveOldEntries!: (value: { entries: Array<Record<string, unknown>> }) => void
    const oldEntries = new Promise<{ entries: Array<Record<string, unknown>> }>(resolve => { resolveOldEntries = resolve })
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [] }
      if (p.includes('/lorebooks/world%3Aw1/entries')) return oldEntries
      if (p.includes('/lorebooks/world%3Aw2/entries')) return { entries: [
        { id: 'w2-entry', world_id: 'w2', name: '第二世界条目', type: 'npc', content: 'new target' },
      ] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return { worlds: [
        { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 1 },
        { id: 'w2', name: '第二世界', language: 'zh-CN', entry_count: 1 },
      ] }
      throw new Error(`unexpected path ${p}`)
    })
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    mocks.route.query.world = 'w2'
    await flushPromises()
    await flushPromises()
    expect(wrapper.text()).toContain('第二世界条目')

    resolveOldEntries({ entries: [{ id: 'old-entry', world_id: 'w1', name: '旧世界条目', type: 'npc', content: 'stale' }] })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('第二世界条目')
    expect(wrapper.text()).not.toContain('旧世界条目')
    wrapper.unmount()
  })

  it('ignores a late entry response when switching books in the same world', async () => {
    mocks.route.query = { world: 'w1' }
    let resolvePrimaryEntries!: (value: { entries: Array<Record<string, unknown>> }) => void
    const primaryEntries = new Promise<{ entries: Array<Record<string, unknown>> }>(resolve => { resolvePrimaryEntries = resolve })
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [
        { id: 'world:w1', name: '主资料册', primary: true, scope: 'world', entry_count: 1 },
        { id: 'book:w1-side', name: '侧资料册', primary: false, scope: 'world', entry_count: 1 },
      ] }
      if (p.includes('/lorebooks/world%3Aw1/entries')) return primaryEntries
      if (p.includes('/lorebooks/book%3Aw1-side/entries')) return { entries: [
        { id: 'side-entry', world_id: 'w1', name: '侧资料册条目', type: 'npc', content: 'selected book' },
      ] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return worlds
      throw new Error(`unexpected path ${p}`)
    })
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    await wrapper.get('.lore-context-select[aria-label="当前资料册"]').setValue('book:w1-side')
    await flushPromises()
    expect(wrapper.text()).toContain('侧资料册条目')

    resolvePrimaryEntries({ entries: [{ id: 'primary-entry', world_id: 'w1', name: '主资料册旧响应', type: 'npc', content: 'stale' }] })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('侧资料册条目')
    expect(wrapper.text()).not.toContain('主资料册旧响应')
    wrapper.unmount()
  })

  it('ignores a late worlds response when a newer world list is already active', async () => {
    mocks.route.query = { world: 'w1' }
    const firstWorlds = deferred<{ worlds: Array<Record<string, unknown>> }>()
    const secondWorlds = deferred<{ worlds: Array<Record<string, unknown>> }>()
    let worldRequestCount = 0
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/worlds') {
        worldRequestCount += 1
        return (worldRequestCount === 1 ? firstWorlds.promise : secondWorlds.promise)
      }
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [] }
      if (p.includes('/lorebooks/world%3A')) return { entries: [] }
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView()
    await flushPromises()
    mocks.route.query.world = 'w2'
    await flushPromises()

    secondWorlds.resolve({ worlds: [
      { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 1 },
      { id: 'w2', name: '第二世界', language: 'zh-CN', entry_count: 1 },
    ] })
    await flushPromises()
    await flushPromises()
    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w2')

    firstWorlds.resolve({ worlds: [{ id: 'w1', name: '旧列表世界', language: 'zh-CN', entry_count: 99 }] })
    await flushPromises()
    await flushPromises()

    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w2')
    expect(wrapper.get('[data-testid="lore-context-crumbs"]').text()).toContain('第二世界')
    expect(wrapper.text()).not.toContain('旧列表世界')
    wrapper.unmount()
  })

  it('ignores a late same-world lorebook list response after a newer list is active', async () => {
    mocks.route.query = { world: 'w1' }
    const firstBooks = deferred<{ books: Array<Record<string, unknown>> }>()
    const thirdBooks = deferred<{ books: Array<Record<string, unknown>> }>()
    let bookRequestCount = 0
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) {
        bookRequestCount += 1
        if (bookRequestCount === 1) return firstBooks.promise
        if (bookRequestCount === 3) return thirdBooks.promise
        return { books: [{ id: 'world:w1', name: '主资料册', primary: true, scope: 'world', entry_count: 1 }] }
      }
      if (p.includes('/lorebooks/world%3Aw1/entries')) return { entries: [] }
      if (p.includes('/lorebooks/world%3Aw2/entries')) return { entries: [] }
      if (p.includes('/worlds')) return { worlds: [
        { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 1 },
        { id: 'w2', name: '第二世界', language: 'zh-CN', entry_count: 1 },
      ] }
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView()
    await flushPromises()
    mocks.route.query.world = 'w2'
    await flushPromises()
    await flushPromises()
    mocks.route.query.world = 'w1'
    await flushPromises()
    await flushPromises()

    thirdBooks.resolve({ books: [
      { id: 'world:w1', name: '新主资料册', primary: true, scope: 'world', entry_count: 1 },
      { id: 'book:w1-new', name: '新侧资料册', primary: false, scope: 'game', entry_count: 2 },
    ] })
    await flushPromises()
    await flushPromises()
    expect(wrapper.get('[aria-label="当前资料册"]').text()).toContain('新侧资料册')

    firstBooks.resolve({ books: [{ id: 'world:w1', name: '旧主资料册', primary: true, scope: 'world', entry_count: 99 }] })
    await flushPromises()
    await flushPromises()

    expect(wrapper.get('[aria-label="当前资料册"]').text()).toContain('新侧资料册')
    expect(wrapper.text()).not.toContain('旧主资料册')
    wrapper.unmount()
  })

  it('moves the browsing query to the selected translated world when the language filter changes', async () => {
    mocks.route.query = { world: 'w1' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.startsWith('/lorebooks?')) return { books: [] }
      if (p.includes('/lorebooks/world%3A')) return { entries: [] }
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return { worlds: [
        { id: 'w1', name: '测试世界', language: 'zh-CN', entry_count: 3 },
        { id: 'w1-en', name: 'Test World', language: 'en', entry_count: 3 },
      ] }
      throw new Error(`unexpected path ${p}`)
    })
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    await wrapper.get('.lore-language-filter select').setValue('en')
    await flushPromises()

    expect((wrapper.get('.lore-context-select').element as HTMLSelectElement).value).toBe('w1-en')
    expect(wrapper.get('[data-testid="lore-context-crumbs"]').text()).toContain('Test World')
    wrapper.unmount()
  })

  it('collapses the inspector by default on narrow screens and opens on demand', async () => {
    stubNarrowViewport()
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(false)
    expect(wrapper.find('.lore-inspector-backdrop').exists()).toBe(false)

    const toggle = wrapper.findAll('.lore-header-actions button').find(b => b.text() === '视角')
    await toggle!.trigger('click')
    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(true)
    expect(wrapper.find('.lore-inspector-backdrop').exists()).toBe(true)

    await wrapper.find('.lore-inspector-backdrop').trigger('click')
    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(false)
  })

  it('keeps the perspective inspector closed by default on desktop', async () => {
    const wrapper = mountView(false, false)
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(false)
    wrapper.unmount()
  })

  it('persists a manual close across remounts', async () => {
    const first = mountView()
    await flushPromises()
    expect(first.find('.lore-perspective-inspector').exists()).toBe(true)

    await first.find('.lore-inspector-close').trigger('click')
    expect(first.find('.lore-perspective-inspector').exists()).toBe(false)
    expect(localStorage.getItem('lore_inspector_open')).toBe('0')
    first.unmount()

    const second = mountView()
    await flushPromises()
    expect(second.find('.lore-perspective-inspector').exists()).toBe(false)
    second.unmount()
  })

  it('does not auto-open on narrow screens even with a saved open', async () => {
    localStorage.setItem('lore_inspector_open', '1')
    stubNarrowViewport()
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(false)
  })

  it('defaults to collapsed on narrow screens when localStorage reads throw', async () => {
    const realGetItem = Storage.prototype.getItem
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === 'lore_inspector_open') throw new Error('storage denied')
      return realGetItem.call(this, key)
    })
    stubNarrowViewport()
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(false)
  })

  it('keeps mounting and closing when localStorage writes are rejected', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage denied')
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(true)
    await wrapper.find('.lore-inspector-close').trigger('click')
    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders audience badges and the summary from backend projections only', async () => {
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    const badges = wrapper.findAll('.lore-visibility-badge').map(b => b.text())
    expect(badges).toContain('全队已知')
    expect(badges).toContain('仅莱拉可知')
    expect(badges).toContain('GM 秘密')

    const inspector = wrapper.find('.lore-perspective-inspector')
    expect(inspector.text()).toContain('3 / 3')
    const viewerButtons = wrapper.findAll('.lore-viewer-options button').map(b => b.text())
    expect(viewerButtons).toEqual(['GM 全知', '全队', '莱拉', '布兰'])
  })

  it('switches the viewer with canonical ids and game context', async () => {
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    const lairaButton = wrapper.findAll('.lore-viewer-options button').find(b => b.text() === '莱拉')
    await lairaButton!.trigger('click')
    await flushPromises()

    const previewCalls = mocks.api.mock.calls.map(call => String(call[0])).filter(p => p.includes('/preview'))
    expect(previewCalls.some(p => p.includes('viewer=u1') && p.includes('game_key=g1'))).toBe(true)
    // 莱拉视角：公开条目 + 自己的条目可见，GM 秘密不可见
    expect(wrapper.find('.lore-summary-list').text()).toContain('2 / 3')

    await wrapper.findAll('.lore-viewer-options button').find(b => b.text() === '全队')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.lore-summary-list').text()).toContain('1 / 3')
  })

  it('filters the entry list by the current perspective', async () => {
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    await wrapper.findAll('.lore-viewer-options button').find(b => b.text() === '全队')!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.lore-row')).toHaveLength(3)

    const filterButtons = wrapper.findAll('.lore-filter-options button')
    await filterButtons.find(b => b.text() === '此视角可见')!.trigger('click')
    expect(wrapper.findAll('.lore-row')).toHaveLength(1)
    expect(wrapper.find('.lore-row').text()).toContain('城门守卫')

    await filterButtons.find(b => b.text() === '此视角未知')!.trigger('click')
    const rows = wrapper.findAll('.lore-row')
    expect(rows).toHaveLength(2)
    expect(rows.map(r => r.text()).join(' ')).not.toContain('城门守卫')
  })

  it('selects an entry and shows its visibility in the inspector', async () => {
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    const row = wrapper.findAll('.lore-row').find(r => r.text().includes('秘血教派'))!
    await row.trigger('click')
    const inspectorText = wrapper.find('.lore-perspective-inspector').text()
    expect(inspectorText).toContain('秘血教派')
    expect(inspectorText).toContain('仅莱拉可知')
    expect(inspectorText).toContain('当前视角可见')
  })

  it('keeps the primary entry draft in the workspace while the inspector is toggled', async () => {
    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    const row = wrapper.findAll('.lore-row').find(r => r.text().includes('城门守卫'))!
    await row.find('.memory-row-actions button').trigger('click')
    await flushPromises()

    const editor = new DOMWrapper(document.body.querySelector('.lore-entry-editor-dialog')!)
    expect(editor.exists()).toBe(true)
    const name = editor.find('.lore-entry-simple input')
    await name.setValue('城门守卫（草稿）')

    const toggle = wrapper.findAll('.lore-header-actions button').find(b => b.text() === '视角')!
    await toggle.trigger('click')
    expect(wrapper.find('.lore-perspective-inspector').exists()).toBe(false)
    await toggle.trigger('click')

    expect((document.body.querySelector('.lore-entry-editor-dialog .lore-entry-simple input') as HTMLInputElement).value).toBe('城门守卫（草稿）')
    wrapper.unmount()
  })

  it('locks character viewers in standalone mode', async () => {
    localStorage.setItem('currentGame', '')
    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('未连接存档')
    const previewCalls = mocks.api.mock.calls.map(call => String(call[0])).filter(p => p.includes('/preview'))
    expect(previewCalls.length).toBeGreaterThan(0)
    expect(previewCalls.every(p => p.includes('viewer=gm'))).toBe(true)
  })

  it('edits visibility through the explicit three-mode control', async () => {
    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    const bodyButton = (text: string) =>
      new DOMWrapper([...document.body.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!)

    await bodyButton('新增条目').trigger('click')
    const modeButtons = () => [...document.body.querySelectorAll('.lore-entry-editor-dialog .lore-filter-options button')]
    expect(modeButtons().map(b => b.textContent?.trim())).toEqual(['GM 秘密', '全队公开', '指定角色'])
    expect(modeButtons()[0].classList.contains('active')).toBe(true)

    // 新条目默认 GM 秘密：角色输入框不出现
    expect(document.body.querySelector('input[placeholder="逗号分隔角色名或 uid"]')).toBeNull()

    // 指定角色：输入框出现，队员 chips 点选写入 canonical uid
    await new DOMWrapper(modeButtons()[2]).trigger('click')
    const names = document.body.querySelector('input[placeholder="逗号分隔角色名或 uid"]')
    expect(names).toBeTruthy()

    const playerChips = () => [...document.body.querySelectorAll('.lore-entry-editor-dialog .lore-filter-options button')]
      .filter(b => ['莱拉', '布兰'].includes(b.textContent?.trim() || ''))
    expect(playerChips().map(b => b.textContent?.trim())).toEqual(['莱拉', '布兰'])
    await new DOMWrapper(playerChips()[0]!).trigger('click')
    expect(playerChips()[0]!.classList.contains('active')).toBe(true)

    await bodyButton('保存').trigger('click')
    await flushPromises()

    const savedCall = mocks.api.mock.calls.find(call =>
      String(call[0]) === '/lorebook' && (call[1] as { method?: string }).method === 'POST',
    )
    expect(savedCall).toBeTruthy()
    const savedBody = JSON.parse((savedCall![1] as { body: string }).body)
    expect(savedBody.visible_to).toEqual(['u1'])
    wrapper.unmount()
  })

  it('writes the canonical public marker when party-wide is picked', async () => {
    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    const bodyButton = (text: string) =>
      new DOMWrapper([...document.body.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!)

    await bodyButton('新增条目').trigger('click')
    const modeButtons = () => [...document.body.querySelectorAll('.lore-entry-editor-dialog .lore-filter-options button')]
    await new DOMWrapper(modeButtons()[1]).trigger('click')
    await new DOMWrapper(modeButtons()[0]).trigger('click')
    await new DOMWrapper(modeButtons()[1]).trigger('click')

    await bodyButton('保存').trigger('click')
    await flushPromises()

    const savedCall = mocks.api.mock.calls.find(call =>
      String(call[0]) === '/lorebook' && (call[1] as { method?: string }).method === 'POST',
    )
    const savedBody = JSON.parse((savedCall![1] as { body: string }).body)
    expect(savedBody.visible_to).toEqual(['*'])
    wrapper.unmount()
  })

  it('strips public markers typed into the named-characters field', async () => {
    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    const bodyButton = (text: string) =>
      new DOMWrapper([...document.body.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!)

    await bodyButton('新增条目').trigger('click')
    const modeButtons = () => [...document.body.querySelectorAll('.lore-entry-editor-dialog .lore-filter-options button')]
    // radio 语义：可被读屏识别当前档位
    expect(modeButtons().map(b => b.getAttribute('role'))).toEqual(['radio', 'radio', 'radio'])
    expect(modeButtons()[0].getAttribute('aria-checked')).toBe('true')

    await new DOMWrapper(modeButtons()[2]).trigger('click')
    const names = document.body.querySelector('input[placeholder="逗号分隔角色名或 uid"]') as HTMLInputElement
    await new DOMWrapper(names).setValue('*, public, 公开, u1, Alice')

    await bodyButton('保存').trigger('click')
    await flushPromises()

    const savedCall = mocks.api.mock.calls.find(call =>
      String(call[0]) === '/lorebook' && (call[1] as { method?: string }).method === 'POST',
    )
    const savedBody = JSON.parse((savedCall![1] as { body: string }).body)
    expect(savedBody.visible_to).toEqual(['u1', 'Alice'])
    wrapper.unmount()
  })

  it('recognizes historical public aliases and canonicalizes on save', async () => {
    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    // 城门守卫夹具带着历史别名 ['public']：打开编辑器应识别为「全队公开」
    const row = wrapper.findAll('.lore-row').find(r => r.text().includes('城门守卫'))!
    await row.find('.memory-row-actions button').trigger('click')
    await flushPromises()

    const modeButtons = () => [...document.body.querySelectorAll('.lore-entry-editor-dialog .lore-filter-options button')]
    expect(modeButtons()[1].classList.contains('active')).toBe(true)

    const save = new DOMWrapper(
      [...document.body.querySelectorAll('button')].find(b => b.textContent?.trim() === '保存')!,
    )
    await save.trigger('click')
    await flushPromises()

    const savedCall = mocks.api.mock.calls.find(call =>
      String(call[0]) === '/lorebook/a' && (call[1] as { method?: string }).method === 'PUT',
    )
    expect(savedCall).toBeTruthy()
    const savedBody = JSON.parse((savedCall![1] as { body: string }).body)
    expect(savedBody.visible_to).toEqual(['*'])
    wrapper.unmount()
  })

  it('shows historical comma-separated visibility in the named-characters field', async () => {
    // 老数据 visible_to 可能是逗号分隔字符串：编辑框必须真实显示，不能空白
    const historical = {
      entries: [
        { id: 'd', world_id: 'w1', name: '旧版点名', type: 'npc', content: '历史字符串可见性', visible_to: 'Alice,Bob' },
      ],
    }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.includes('/lorebook/')) return historical
      if (p.includes('/worlds')) return worlds
      oldServerRouteMissing(p)
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    const row = wrapper.findAll('.lore-row').find(r => r.text().includes('旧版点名'))!
    await row.find('.memory-row-actions button').trigger('click')
    await flushPromises()

    const modeButtons = () => [...document.body.querySelectorAll('.lore-entry-editor-dialog .lore-filter-options button')]
    // "Alice,Bob" 不含公开标记：档位识别为「指定角色」
    expect(modeButtons()[2].classList.contains('active')).toBe(true)
    const names = document.body.querySelector('input[placeholder="逗号分隔角色名或 uid"]') as HTMLInputElement
    expect(names).toBeTruthy()
    expect(names.value).toBe('Alice、Bob')

    const save = new DOMWrapper(
      [...document.body.querySelectorAll('button')].find(b => b.textContent?.trim() === '保存')!,
    )
    await save.trigger('click')
    await flushPromises()

    const savedCall = mocks.api.mock.calls.find(call =>
      String(call[0]) === '/lorebook/d' && (call[1] as { method?: string }).method === 'PUT',
    )
    expect(savedCall).toBeTruthy()
    const savedBody = JSON.parse((savedCall![1] as { body: string }).body)
    // 历史字符串保存时 canonicalize 成 string[]，点名不能丢
    expect(savedBody.visible_to).toEqual(['Alice', 'Bob'])
    wrapper.unmount()
  })
})

// 「触发方式」= is_constant + vector_activation 的产品组合，不是 legacy match_mode。
describe('LorebookView product trigger mode', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('currentGame', 'g1')
    i18n.global.locale.value = 'zh-CN'
    mocks.api.mockReset()
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return worlds
      oldServerRouteMissing(p)
      throw new Error(`unexpected path ${p}`)
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  function bodyButton(text: string) {
    const found = [...document.body.querySelectorAll('button')].find(b => b.textContent?.trim() === text)
    expect(found, `button ${text}`).toBeTruthy()
    return new DOMWrapper(found!)
  }

  function triggerSelect() {
    const select = document.body.querySelector('.lore-entry-trigger-mode') as HTMLSelectElement | null
    expect(select, 'trigger mode select').toBeTruthy()
    return select!
  }

  function selectedLabel(select: HTMLSelectElement) {
    return select.options[select.selectedIndex]?.textContent?.trim()
  }

  function vectorSelect() {
    return document.body.querySelector('[data-field="vector_activation"] select') as HTMLSelectElement
  }

  function constantCheckbox() {
    return document.body.querySelector('[data-field="is_constant"] input') as HTMLInputElement
  }

  /** 保存载荷按后端实际存下的内容断言：最后一次写到 /lorebook 的 body。 */
  function lastSavedEntry(method: 'POST' | 'PUT') {
    const calls = mocks.api.mock.calls.filter(call =>
      String(call[0]).startsWith('/lorebook') && (call[1] as { method?: string })?.method === method,
    )
    expect(calls.length, `${method} /lorebook call`).toBeGreaterThan(0)
    return JSON.parse(String((calls[calls.length - 1][1] as { body: string }).body)) as Record<string, unknown>
  }

  it('starts a new entry from the canonical activation defaults', async () => {
    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    await bodyButton('新增条目').trigger('click')
    expect(selectedLabel(triggerSelect())).toBe('关键词 + 语义')
    // legacy match_mode 已移出第一屏，只在高级区可读写
    expect(document.body.querySelector('.lore-entry-simple [data-field="match_mode"]')).toBeNull()
    expect(document.body.querySelector('.lore-entry-advanced [data-field="match_mode"]')).toBeTruthy()

    await bodyButton('保存').trigger('click')
    await flushPromises()

    const saved = lastSavedEntry('POST')
    expect(saved.priority).toBe(100)
    expect(saved.order).toBe(100)
    expect(saved.prompt_slot).toBe('world_background')
    expect(saved.vector_activation).toBe('hybrid')
    expect(saved.is_constant).toBe(false)
    wrapper.unmount()
  })

  it('writes the canonical pair for every product trigger mode', async () => {
    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()
    await bodyButton('新增条目').trigger('click')

    const steps = [
      { mode: 'keyword', label: '关键词', is_constant: false, vector_activation: 'off' },
      // 「始终生效」保留模型当前的 vector_activation（此处上一步是 off）
      { mode: 'always', label: '始终生效', is_constant: true, vector_activation: 'off' },
      { mode: 'hybrid', label: '关键词 + 语义', is_constant: false, vector_activation: 'hybrid' },
      { mode: 'semantic', label: '仅语义', is_constant: false, vector_activation: 'vector_only' },
    ]
    for (const step of steps) {
      const select = triggerSelect()
      await new DOMWrapper(select).setValue(step.mode)
      await flushPromises()

      expect(selectedLabel(triggerSelect())).toBe(step.label)
      // 高级区镜像同一对 canonical 字段，UI 内部不能自相矛盾
      expect(constantCheckbox().checked).toBe(step.is_constant)
      expect(vectorSelect().value).toBe(step.vector_activation)

      await bodyButton('保存').trigger('click')
      await flushPromises()
      const saved = lastSavedEntry('POST')
      expect(saved.is_constant).toBe(step.is_constant)
      expect(saved.vector_activation).toBe(step.vector_activation)
    }
    wrapper.unmount()
  })

  it('reads the product trigger mode back from a loaded entry', async () => {
    const fixtures = {
      entries: [
        { id: 'k', world_id: 'w1', name: '关键词条目', is_constant: false, vector_activation: 'off' },
        { id: 'a', world_id: 'w1', name: '常驻条目', is_constant: true, vector_activation: 'off' },
        { id: 'h', world_id: 'w1', name: '混合条目', is_constant: false, vector_activation: 'hybrid' },
        { id: 's', world_id: 'w1', name: '语义条目', is_constant: false, vector_activation: 'vector_only' },
      ],
    }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.includes('/lorebook/')) return fixtures
      if (p.includes('/worlds')) return worlds
      oldServerRouteMissing(p)
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    const expected: Array<[string, string, string]> = [
      ['关键词条目', 'keyword', '关键词'],
      ['常驻条目', 'always', '始终生效'],
      ['混合条目', 'hybrid', '关键词 + 语义'],
      ['语义条目', 'semantic', '仅语义'],
    ]
    for (const [name, mode, label] of expected) {
      const row = wrapper.findAll('.lore-row').find(item => item.text().includes(name))
      expect(row, `row ${name}`).toBeTruthy()
      await row!.find('.memory-row-actions button').trigger('click')
      await flushPromises()
      expect(triggerSelect().value).toBe(mode)
      expect(selectedLabel(triggerSelect())).toBe(label)
      await bodyButton('取消').trigger('click')
      await flushPromises()
    }
    wrapper.unmount()
  })

  it('shows after reload exactly the activation values that were saved', async () => {
    // 后端原样存回 payload（setdefault 只补缺失值），读路径不得把 off 改写回 hybrid。
    let stored: Record<string, unknown> | undefined
    mocks.api.mockImplementation(async (path: string, init?: { method?: string; body?: string }) => {
      const p = String(path)
      if (p === '/lorebook' && init?.method === 'POST') {
        stored = JSON.parse(String(init.body)) as Record<string, unknown>
        return { ok: true }
      }
      if (p.includes('/preview')) return previewFor(p)
      if (p.includes('/characters')) return characters
      if (p.includes('/games')) return games
      if (p.includes('/lorebook/')) return { entries: stored ? [{ id: 'new', world_id: 'w1', ...stored }] : [] }
      if (p.includes('/worlds')) return worlds
      oldServerRouteMissing(p)
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    await bodyButton('新增条目').trigger('click')
    await new DOMWrapper(triggerSelect()).setValue('keyword')
    await flushPromises()
    await bodyButton('保存').trigger('click')
    await flushPromises()

    expect(stored?.vector_activation).toBe('off')
    expect(stored?.is_constant).toBe(false)

    const row = wrapper.findAll('.lore-row').find(item => item.text().includes('未命名条目'))
    expect(row, 'reloaded row').toBeTruthy()
    await row!.find('.memory-row-actions button').trigger('click')
    await flushPromises()

    expect(triggerSelect().value).toBe('keyword')
    expect(selectedLabel(triggerSelect())).toBe('关键词')
    expect(vectorSelect().value).toBe('off')
    expect(constantCheckbox().checked).toBe(false)
    wrapper.unmount()
  })

  describe('legacy adapter fallback', () => {
    function mockCanonicalFailure(error: unknown) {
      mocks.api.mockImplementation(async (path: string) => {
        const p = String(path)
        if (p.startsWith('/lorebooks/')) throw error
        if (p.includes('/preview')) return previewFor(p)
        if (p.includes('/characters')) return characters
        if (p.includes('/games')) return games
        if (p.includes('/lorebook/')) return lorebook
        if (p.includes('/worlds')) return worlds
        throw new Error(`unexpected path ${p}`)
      })
    }
    function legacyCalls() {
      // Entry reads/writes only; the preview endpoint is a separate legacy read.
      return mocks.api.mock.calls.filter(call => /^\/lorebook(\/[^/?]+)?$/.test(String(call[0])))
    }

    it.each([
      ['server write failure', new ApiError('写入失败', 500)],
      ['permission error', new ApiError('forbidden', 403, 'FORBIDDEN')],
      ['resource 404 with a JSON body', new ApiError('book not found', 404)],
      ['network failure', new TypeError('Failed to fetch')],
    ])('does not fall back to the legacy adapter on %s', async (_label, error) => {
      mockCanonicalFailure(error)
      mount(LorebookView, { global: { plugins: [i18n] } })
      await flushPromises()
      expect(legacyCalls().map(call => String(call[0]))).toEqual([])
    })
  })
})
