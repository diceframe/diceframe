import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CharactersView from '../src/features/admin/CharactersView.vue'
import { i18n } from '../src/i18n'
import { characterJourneyCopy } from '../src/features/characters/copy'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  apiBlob: vi.fn(),
  confirm: vi.fn(async () => true),
  currentGame: 'local-game',
  route: { query: {} as Record<string, string> },
  routeProxy: null as { query: Record<string, string> } | null,
}))

vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api, apiBlob: mocks.apiBlob, errorMessage: (cause: unknown) => String((cause as Error)?.message || cause) }
})
vi.mock('../src/stores/gameContext', () => ({ readCurrentGame: () => mocks.currentGame }))
vi.mock('../src/composables/useToast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }) }))
vi.mock('../src/composables/useConfirm', () => ({ useConfirm: () => ({ confirm: mocks.confirm }) }))
vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  mocks.routeProxy = reactive(mocks.route)
  return { useRoute: () => mocks.routeProxy }
})

const card = { id: 'card-1', character_name: 'Alice', rule_id: 'core', class: 'Scout', race: 'Human' }

function mountView() {
  return mount(CharactersView, { global: { plugins: [i18n], stubs: { teleport: true, RouterLink: { template: '<a><slot /></a>' } } } })
}

describe('CharactersView collection journey', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
    mocks.currentGame = 'local-game'
    mocks.route.query = {}
    mocks.api.mockReset()
    mocks.confirm.mockReset()
    mocks.confirm.mockResolvedValue(true)
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [card] }
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p.endsWith('/characters')) return { players: [{ user_id: 'u1', character: { name: 'Alice' } }], npcs: [], rule_id: 'core' }
      return { ok: true }
    })
  })

  it('provides copy for every supported locale', () => {
    for (const locale of ['zh-CN', 'en', 'de', 'ja', 'ru']) {
      expect(characterJourneyCopy(locale).libraryTab).toBeTruthy()
      expect(characterJourneyCopy(locale).currentTab).toBeTruthy()
    }
  })

  it('uses the canonical query game before the local current-game hint', async () => {
    mocks.route.query = { game: 'query-game' }
    mountView()
    await flushPromises()

    expect(mocks.api).toHaveBeenCalledWith('/games/query-game/characters')
    expect(mocks.api).not.toHaveBeenCalledWith('/games/local-game/characters')
  })

  it('locks card creation to the loaded game rule binding', async () => {
    mocks.route.query = { game: 'bound-game' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [card] }
      if (p === '/rules') return { rules: [{ rule_id: 'other', name: 'Other' }] }
      if (p.endsWith('/characters')) return { players: [], npcs: [], rule_meta: { rule_id: 'core', rule_name: 'Core' }, rule_attrs: [], rule_attrs_total: 60 }
      if (p.includes('/character-schema')) return { ok: true, rule_meta: { rule_id: 'core' }, rule_attrs: [] }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('select').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="new-character-card"]').attributes('disabled')).toBeUndefined()
  })

  it('does not create a card with a failed or stale character schema', async () => {
    mocks.route.query = { game: 'bound-game' }
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/character-cards') return { cards: [card] }
      if (path === '/rules') return { rules: [{ rule_id: 'core' }] }
      if (path.endsWith('/characters')) return { players: [], rule_meta: { rule_id: 'core' } }
      if (path.includes('/character-schema')) throw new Error('schema unavailable')
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.get('[data-testid="new-character-card"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Alice')
  })

  it('reloads the same game schema and waits for it before creating another card', async () => {
    mocks.route.query = { game: 'bound-game' }
    let schemaReads = 0
    let resolveRefresh!: (value: unknown) => void
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/character-cards') return { cards: [card] }
      if (path === '/rules') return { rules: [{ rule_id: 'core' }] }
      if (path.endsWith('/characters')) return { players: [], rule_meta: { rule_id: 'core' } }
      if (path.includes('/character-schema')) {
        schemaReads++
        if (schemaReads === 1) return { ok: true, skill_pool: ['Original'] }
        return new Promise(resolve => { resolveRefresh = resolve })
      }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.get('[data-testid="new-character-card"]').attributes('disabled')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '刷新')!.trigger('click')
    await flushPromises()
    expect(schemaReads).toBe(2)
    expect(wrapper.get('[data-testid="new-character-card"]').attributes('disabled')).toBeDefined()
    resolveRefresh({ ok: true, skill_pool: ['Refreshed'] })
    await flushPromises()
    expect(wrapper.get('[data-testid="new-character-card"]').attributes('disabled')).toBeUndefined()
  })

  it('disables new card creation when the game read fails while the library remains usable', async () => {
    mocks.route.query = { game: 'unavailable-game' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [card] }
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p.endsWith('/characters')) throw new Error('game unavailable')
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Alice')
    expect(wrapper.get('[data-testid="new-character-card"]').attributes('disabled')).toBeDefined()
  })

  it('does not show an empty library while its own read is still pending', async () => {
    let resolveCards!: (value: unknown) => void
    const cardsRequest = new Promise(resolve => { resolveCards = resolve })
    mocks.api.mockImplementation((path: string) => {
      const p = String(path)
      if (p === '/character-cards') return cardsRequest
      if (p === '/rules') return Promise.resolve({ rules: [] })
      return Promise.resolve({ players: [], npcs: [] })
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.character-library-grid > .muted').exists()).toBe(false)
    resolveCards({ cards: [] })
    await flushPromises()
    expect(wrapper.find('.character-library-grid > .muted').exists()).toBe(true)
  })

  it('keeps the edit-user deep link in the current-game collection', async () => {
    mocks.route.query = { game: 'query-game', edit_user: 'u1' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [] }
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p.endsWith('/characters')) return { players: [{ user_id: 'u1', character_name: 'Alice', character_sheet: { level: 2, hp: 10, max_hp: 20, attributes: {}, skills: [] } }], npcs: [], rule_meta: { rule_id: 'core' } }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('#character-current-tab').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.find('[role="dialog"] input').exists()).toBe(true)
    expect((wrapper.find('[role="dialog"] input').element as HTMLInputElement).value).toBe('Alice')
  })

  it('edits and saves dnd5e damage dice without dropping other equipment fields', async () => {
    mocks.route.query = { game: 'dnd5e-game', edit_user: 'u1' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [] }
      if (p === '/rules') return { rules: [{ rule_id: 'dnd5e', name: 'D&D' }] }
      if (p.endsWith('/characters')) return {
        players: [{ user_id: 'u1', character_name: 'Alice', character_sheet: {
          level: 1, hp: 10, max_hp: 10, attributes: {}, skills: [],
          equipment: [{ name: '长剑', type: 'weapon', damage: 7, damage_dice: '1d8', damage_type: 'slashing', item_key: 'longsword', slot: 'main_hand', quality: 'common' }],
        } }], npcs: [], rule_meta: { rule_id: 'dnd5e', mechanics: 'dnd5e_core' }, rule_attrs: [],
      }
      if (p.includes('/character-schema')) return { ok: true, rule_meta: { rule_id: 'dnd5e', mechanics: 'dnd5e_core' }, rule_attrs: [] }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.item-dice').exists()).toBe(true)
    await wrapper.get('.item-dice').setValue('2d6+1')
    await wrapper.findAll('[role="dialog"] button').find(button => button.text().includes('保存'))!.trigger('click')
    const saveCall = mocks.api.mock.calls.find(([path]) => String(path).includes('/character/'))
    const body = JSON.parse(String(saveCall?.[1]?.body))
    expect(body.equipment[0]).toEqual(expect.objectContaining({ damage_dice: '2d6+1', damage_type: 'slashing', item_key: 'longsword' }))
  })

  it('retains the server skill identity through the character editor', async () => {
    mocks.route.query = { game: 'growth-game', edit_user: 'u1' }
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/character-cards') return { cards: [] }
      if (path === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (path.endsWith('/characters')) return {
        players: [{ user_id: 'u1', character_name: 'Alice', character_sheet: {
          level: 1, hp: 10, max_hp: 10, attributes: {}, skills: [{ id: 'spot_hidden', name: '侦查', value: 60 }],
        } }], npcs: [], rule_meta: { rule_id: 'core' }, rule_attrs: [],
      }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.findAll('[role="dialog"] button').find(button => button.text().includes('保存'))!.trigger('click')
    const saveCall = mocks.api.mock.calls.find(([path]) => String(path).includes('/character/'))
    expect(JSON.parse(String(saveCall?.[1]?.body)).skills).toEqual([{ id: 'spot_hidden', name: '侦查', value: 60 }])
  })

  it('keeps the character editor open when the save route reports a domain error', async () => {
    mocks.route.query = { game: 'save-error-game', edit_user: 'u1' }
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [] }
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p.endsWith('/characters')) return {
        players: [{ user_id: 'u1', character_name: 'Alice', character_sheet: {
          level: 1, hp: 10, max_hp: 10, attributes: {}, skills: [], equipment: [],
        } }], npcs: [], rule_meta: { rule_id: 'core' }, rule_attrs: [],
      }
      if (p.includes('/character/') && init?.method === 'PUT') return { ok: false, error: '保存失败：伤害骰无效' }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()

    const dialog = wrapper.get('[role="dialog"]')
    await dialog.findAll('button').find(button => button.text().includes('保存'))!.trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.get('[role="dialog"] [role="alert"]').text()).toContain('保存失败：伤害骰无效')
  })

  it('does not add damage dice when editing a rule without the dnd5e capability', async () => {
    mocks.route.query = { game: 'generic-game', edit_user: 'u1' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [] }
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p.endsWith('/characters')) return {
        players: [{ user_id: 'u1', character_name: 'Alice', character_sheet: {
          level: 1, hp: 10, max_hp: 10, attributes: {}, skills: [],
          equipment: [{ name: '杖', type: 'weapon', damage: 3, damage_dice: '1d6', item_key: 'staff', slot: 'main_hand', quality: 'common' }],
        } }], npcs: [], rule_meta: { rule_id: 'core', mechanics: 'freeform_d20_core' }, rule_attrs: [],
      }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.item-dice').exists()).toBe(false)
    await wrapper.findAll('[role="dialog"] button').find(button => button.text().includes('保存'))!.trigger('click')
    const saveCall = mocks.api.mock.calls.find(([path]) => String(path).includes('/character/'))
    const body = JSON.parse(String(saveCall?.[1]?.body))
    expect(body.equipment[0]).not.toHaveProperty('damage_dice')
    expect(body.equipment[0]).toEqual(expect.objectContaining({ item_key: 'staff', damage: 3 }))
  })

  it('shows a current-pane guide without requesting an undefined game', async () => {
    mocks.currentGame = ''
    mocks.route.query = { pane: 'current' }
    const wrapper = mountView()
    await flushPromises()

    expect(mocks.api.mock.calls.some(([path]) => String(path).includes('/games/'))).toBe(false)
    expect(wrapper.find('[data-testid="characters-current-guide"]').exists()).toBe(true)
  })

  it('keeps the library visible when the selected game read fails', async () => {
    mocks.route.query = { game: 'broken-game' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [card] }
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p.endsWith('/characters')) throw new Error('game unavailable')
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Alice')
    expect(wrapper.find('[data-testid="characters-current-error"]').exists()).toBe(true)
  })

  it('keeps library data independent when the card read fails', async () => {
    mocks.route.query = { game: 'game-without-cards' }
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') throw new Error('library unavailable')
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p.endsWith('/characters')) return { players: [{ user_id: 'u1', character_name: 'Current Alice' }], npcs: [], rule_id: 'core' }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.current-character-section').text()).toContain('Current Alice')
    expect(wrapper.find('.error-banner').text()).toContain('library unavailable')
  })

  it('ignores a late response from the previous game scope', async () => {
    mocks.route.query = { game: 'old-game' }
    let resolveOld!: (value: unknown) => void
    let resolveNew!: (value: unknown) => void
    const oldRequest = new Promise(resolve => { resolveOld = resolve })
    const newRequest = new Promise(resolve => { resolveNew = resolve })
    mocks.api.mockImplementation((path: string) => {
      const p = String(path)
      if (p === '/character-cards') return Promise.resolve({ cards: [card] })
      if (p === '/rules') return Promise.resolve({ rules: [{ rule_id: 'core', name: 'Core' }] })
      if (p === '/games/old-game/characters') return oldRequest
      if (p === '/games/new-game/characters') return newRequest
      return Promise.resolve({ ok: true })
    })
    const wrapper = mountView()
    await nextTick()
    mocks.routeProxy!.query = { game: 'new-game' }
    await nextTick()
    resolveNew({ players: [{ user_id: 'new', character_name: 'New Scope' }], npcs: [], rule_id: 'core' })
    await flushPromises()
    resolveOld({ players: [{ user_id: 'old', character_name: 'Old Scope' }], npcs: [], rule_id: 'core' })
    await flushPromises()

    expect(wrapper.text()).toContain('New Scope')
    expect(wrapper.text()).not.toContain('Old Scope')
    expect(mocks.api).toHaveBeenCalledWith('/games/new-game/characters')
  })

  it('does not apply a delayed removal confirmation to a newly selected game', async () => {
    mocks.route.query = { game: 'old-game' }
    let resolveConfirm!: (value: boolean) => void
    mocks.confirm.mockImplementation(() => new Promise(resolve => { resolveConfirm = resolve }))
    mocks.api.mockImplementation(async (path: string) => {
      const p = String(path)
      if (p === '/character-cards') return { cards: [] }
      if (p === '/rules') return { rules: [{ rule_id: 'core', name: 'Core' }] }
      if (p === '/games/old-game/characters') return { players: [{ user_id: 'old-user', character_name: 'Old', character_sheet: {} }], npcs: [], rule_meta: { rule_id: 'core' } }
      if (p === '/games/new-game/characters') return { players: [{ user_id: 'new-user', character_name: 'New', character_sheet: {} }], npcs: [], rule_meta: { rule_id: 'core' } }
      return { ok: true }
    })
    const wrapper = mountView()
    await flushPromises()
    const remove = wrapper.findAll('button').find(button => /移除|Remove/.test(button.text()))
    expect(remove).toBeTruthy()
    await remove!.trigger('click')
    mocks.routeProxy!.query = { game: 'new-game' }
    await nextTick()
    resolveConfirm(true)
    await flushPromises()

    expect(mocks.api.mock.calls.some(([path, options]) => String(path).includes('/character/') && (options as { method?: string })?.method === 'DELETE')).toBe(false)
  })

  it('exposes accessible collection tabs and keeps the library query controls mounted', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[role="tablist"]').exists()).toBe(true)
    expect(wrapper.findAll('[role="tab"]').length).toBe(2)
    expect(wrapper.find('[aria-label*="网格"]').exists() || wrapper.find('[aria-label*="grid" i]').exists()).toBe(true)
    const search = wrapper.find('input[type="search"]')
    expect(search.exists()).toBe(true)
    await search.setValue('Alice')
    await wrapper.findAll('[role="tab"]')[1].trigger('click')
    await wrapper.findAll('[role="tab"]')[0].trigger('click')
    expect((search.element as HTMLInputElement).value).toBe('Alice')
  })
})
