import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LorebookView from '../src/features/lorebook/LorebookView.vue'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
}))

vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api, errorMessage: (cause: unknown) => String((cause as Error)?.message || cause) }
})
vi.mock('../src/peer/game/bridge', () => ({
  activePeerGameClient: () => null,
  setActivePeerGameClient: vi.fn(),
}))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: vi.fn().mockResolvedValue(true) }),
}))

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

function mountView(attachToBody = false) {
  return mount(LorebookView, {
    global: { plugins: [i18n] },
    attachTo: attachToBody ? document.body : undefined,
  })
}

describe('LorebookView perspective inspector', () => {
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
      throw new Error(`unexpected path ${p}`)
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.unstubAllGlobals()
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
      if (p.includes('/lorebook/')) return lorebook
      if (p.includes('/worlds')) return worlds
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView()
    await flushPromises()
    await flushPromises()

    const bookSelect = wrapper.findAll('.lore-context-select')[1]
    expect((bookSelect.element as HTMLSelectElement).value).toBe('world:w1')
    expect(bookSelect.find('option').attributes('value')).toBe('world:w1')

    mocks.api.mockClear()
    await bookSelect.trigger('change')
    await flushPromises()

    expect(mocks.api.mock.calls.some(call => String(call[0]) === '/lorebook/w1')).toBe(true)
    expect(mocks.api.mock.calls.some(call => String(call[0]) === '/lorebooks/world%3Aw1/entries')).toBe(false)
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
    const modeButtons = () => [...document.body.querySelectorAll('.dialog .lore-filter-options button')]
    expect(modeButtons().map(b => b.textContent?.trim())).toEqual(['GM 秘密', '全队公开', '指定角色'])
    expect(modeButtons()[0].classList.contains('active')).toBe(true)

    // 新条目默认 GM 秘密：角色输入框不出现
    expect(document.body.querySelector('input[placeholder="逗号分隔角色名或 uid"]')).toBeNull()

    // 指定角色：输入框出现，队员 chips 点选写入 canonical uid
    await new DOMWrapper(modeButtons()[2]).trigger('click')
    const names = document.body.querySelector('input[placeholder="逗号分隔角色名或 uid"]')
    expect(names).toBeTruthy()

    const playerChips = () => [...document.body.querySelectorAll('.dialog .lore-filter-options button')]
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
    const modeButtons = () => [...document.body.querySelectorAll('.dialog .lore-filter-options button')]
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
    const modeButtons = () => [...document.body.querySelectorAll('.dialog .lore-filter-options button')]
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

    const modeButtons = () => [...document.body.querySelectorAll('.dialog .lore-filter-options button')]
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
      throw new Error(`unexpected path ${p}`)
    })

    const wrapper = mountView(true)
    await flushPromises()
    await flushPromises()

    const row = wrapper.findAll('.lore-row').find(r => r.text().includes('旧版点名'))!
    await row.find('.memory-row-actions button').trigger('click')
    await flushPromises()

    const modeButtons = () => [...document.body.querySelectorAll('.dialog .lore-filter-options button')]
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
})
