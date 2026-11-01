import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  stopAssistant: vi.fn(),
  getRendezvousConfig: vi.fn(),
  confirm: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  api: mocks.api,
  apiBlob: vi.fn(),
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('../src/composables/useAssistant', () => ({
  useAssistant: () => ({ stop: mocks.stopAssistant }),
}))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), info: vi.fn(), error: vi.fn() }),
}))
vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: mocks.confirm }),
}))
vi.mock('../src/api/peer', () => ({ getRendezvousConfig: mocks.getRendezvousConfig }))
vi.mock('../src/api/sceneImages', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/sceneImages')>(),
  resolveGameSceneImageUrl: vi.fn().mockResolvedValue(''),
  pruneGameSceneImageCache: vi.fn(),
  revokeSceneImageUrl: vi.fn(),
  sceneImageStyle: vi.fn().mockReturnValue({}),
}))
vi.mock('../src/composables/useBackgroundImages', () => ({ ruleSceneUrl: vi.fn().mockReturnValue('') }))

import OverviewView from '../src/features/overview/OverviewView.vue'
import { clearOverviewGamesCache } from '../src/features/overview/overviewCache'
import { resolveGameSceneImageUrl, sceneImageStyle } from '../src/api/sceneImages'

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/overview', name: 'overview', component: OverviewView },
      { path: '/create', name: 'create', component: { template: '<div />' } },
      { path: '/library', name: 'library', component: { template: '<div />' } },
    ],
  })
  await router.push('/overview')
  const wrapper = mount(OverviewView, {
    attachTo: document.body,
    global: {
      plugins: [i18n, router],
      stubs: { NIcon: { template: '<span />' }, NDrawer: { template: '<div><slot /></div>' }, NDrawerContent: { template: '<div><slot /></div>' } },
    },
  })
  await flushPromises()
  return wrapper
}

describe('OverviewView loading recovery', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  beforeEach(() => {
    clearOverviewGamesCache()
    localStorage.removeItem('diceframe:onboarding:dismissed:v1')
    localStorage.removeItem('diceframe:onboarding:collapsed:v1')
    i18n.global.locale.value = 'zh-CN'
    mocks.api.mockReset()
    mocks.confirm.mockReset()
    mocks.confirm.mockResolvedValue(true)
    mocks.getRendezvousConfig.mockResolvedValue({ entry_visible: false })
    vi.mocked(resolveGameSceneImageUrl).mockResolvedValue('')
    vi.mocked(sceneImageStyle).mockReturnValue({})
  })

  it('offers a retry action after loading fails and reloads the save list', async () => {
    mocks.api.mockImplementationOnce(async () => { throw new Error('overview offline') })
      .mockResolvedValueOnce({ games: [{ game_key: 'web|retry|gm', world_name: 'Recovered save', state: 'ended' }] })
    const wrapper = await render()

    expect(wrapper.get('[role="alert"]').text()).toContain('overview offline')
    await wrapper.get('[data-testid="overview-load-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Recovered save')
    expect(mocks.api).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('offers invitation joining even when experimental direct connect is unavailable', async () => {
    mocks.api.mockResolvedValue({ games: [{ game_key: 'web|join|gm', world_name: 'Joinable save', state: 'ended' }] })
    const wrapper = await render()
    expect(wrapper.find('[data-testid="overview-create-adventure"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="overview-adventure-library"]').attributes('href')).toContain('/library')
    expect(wrapper.get('[data-testid="overview-open-join"]').text()).toBe('加入好友')
    expect(wrapper.findAll('button').some(button => button.text() === '加入游戏')).toBe(false)
    wrapper.unmount()
  })

  it('places the beginner entry between the header and the recent adventure', async () => {
    mocks.api.mockResolvedValue({ games: [{ game_key: 'web|guide|gm', world_name: 'Existing adventure', state: 'ended' }] })
    const wrapper = await render()
    const main = wrapper.get('.overview-main')
    const children = Array.from(main.element.children)
    const guide = wrapper.get('[data-testid="getting-started-collapsed"]')
    expect(children.indexOf(guide.element)).toBeGreaterThan(children.indexOf(wrapper.get('.overview-hero').element))
    expect(children.indexOf(guide.element)).toBeLessThan(children.indexOf(wrapper.get('[data-testid="overview-featured"]').element))
    expect(mocks.api).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('offers an expanded beginner card only after the first archive load succeeds', async () => {
    let resolve!: (value: unknown) => void
    mocks.api.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const wrapper = await render()
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(false)
    resolve({ games: [] })
    await flushPromises()
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="overview-empty"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('can dismiss and reopen the beginner guide from the title action', async () => {
    mocks.api.mockResolvedValue({ games: [] })
    const wrapper = await render()
    const openGuide = wrapper.get('[data-testid="overview-open-guide"]')
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    await wrapper.get('[data-testid="getting-started-close"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    expect(document.activeElement).toBe(openGuide.element)
    wrapper.unmount()

    const remounted = await render()
    expect(remounted.find('[data-testid="getting-started-card"]').exists()).toBe(false)
    expect(remounted.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    await remounted.get('[data-testid="overview-open-guide"]').trigger('click')
    await flushPromises()
    expect(remounted.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    expect(remounted.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    expect(document.activeElement).toBe(remounted.get('[data-testid="getting-started-start"]').element)
    remounted.unmount()
  })

  it('reopens a dismissed existing-save guide as a full card', async () => {
    mocks.api.mockResolvedValue({ games: [{ game_key: 'web|guide|saved', world_name: 'Saved adventure', state: 'ended' }] })
    const wrapper = await render()
    expect(wrapper.find('[data-testid="getting-started-collapsed"]').exists()).toBe(true)
    await wrapper.get('[data-testid="getting-started-close"]').trigger('click')
    expect(wrapper.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    await wrapper.get('[data-testid="overview-open-guide"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('[data-testid="getting-started-start"]').element)
    wrapper.unmount()

    const remounted = await render()
    expect(remounted.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    expect(remounted.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    remounted.unmount()
  })

  it('honors a reopen request made while the archive is loading', async () => {
    localStorage.setItem('diceframe:onboarding:dismissed:v1', '1')
    let resolve!: (value: unknown) => void
    mocks.api.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const wrapper = await render()
    await wrapper.get('[data-testid="overview-open-guide"]').trigger('click')
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(false)
    resolve({ games: [{ game_key: 'web|guide|loading', world_name: 'Loaded adventure', state: 'ended' }] })
    await flushPromises()
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps close and reopen usable when localStorage is unavailable', async () => {
    const storagePrototype = Object.getPrototypeOf(localStorage) as Storage
    vi.spyOn(storagePrototype, 'getItem').mockImplementation(() => { throw new Error('storage denied') })
    vi.spyOn(storagePrototype, 'setItem').mockImplementation(() => { throw new Error('storage denied') })
    mocks.api.mockResolvedValue({ games: [] })
    const wrapper = await render()
    await wrapper.get('[data-testid="getting-started-close"]').trigger('click')
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    await wrapper.get('[data-testid="overview-open-guide"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="getting-started-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="getting-started-collapsed"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each([false, true])('places new adventure after content browsing when peer availability is %s', async (entryVisible) => {
    mocks.getRendezvousConfig.mockResolvedValue({ entry_visible: entryVisible })
    mocks.api.mockResolvedValue({ games: [{ game_key: 'web|order|gm', world_name: 'Existing save', state: 'ended' }] })
    const wrapper = await render()
    const entries = wrapper.findAll('.overview-actions > button, .overview-actions > a')
    const browse = entries.findIndex(entry => entry.attributes('data-testid') === 'overview-adventure-library')
    const create = entries.findIndex(entry => entry.attributes('data-testid') === 'overview-create-adventure')
    expect(browse).toBeGreaterThanOrEqual(0)
    expect(create).toBeGreaterThan(browse)
    await wrapper.get('[data-testid="overview-create-adventure"]').trigger('click')
    await flushPromises()
    expect(wrapper.vm.$router.currentRoute.value.name).toBe('create')
    wrapper.unmount()
  })

  it('does not present an empty library while saves are still loading', async () => {
    let resolve!: (value: unknown) => void
    mocks.api.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const wrapper = await render()
    expect(wrapper.find('[data-testid="overview-loading"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="overview-empty"]').exists()).toBe(false)
    resolve({ games: [] })
    await flushPromises()
    expect(wrapper.find('[data-testid="overview-empty"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('opens only one save action menu and closes it with focus recovery', async () => {
    mocks.api.mockResolvedValue({ games: [{ game_key: 'web|menu|gm', world_name: 'Menu save', state: 'ended' }] })
    const wrapper = await render()
    const menu = wrapper.find('.overview-game-tools')
    await menu.get('summary').trigger('click')
    expect((menu.element as HTMLDetailsElement).open).toBe(true)

    const archive = wrapper.find('.overview-archive-tools')
    ;(archive.element as HTMLDetailsElement).open = true
    archive.element.dispatchEvent(new Event('toggle'))
    await flushPromises()
    expect((archive.element as HTMLDetailsElement).open).toBe(true)
    expect((menu.element as HTMLDetailsElement).open).toBe(false)

    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    expect((archive.element as HTMLDetailsElement).open).toBe(false)
    ;(archive.element as HTMLDetailsElement).open = true
    archive.element.dispatchEvent(new Event('toggle'))
    await flushPromises()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect((archive.element as HTMLDetailsElement).open).toBe(false)
    expect(document.activeElement).toBe(archive.get('summary').element)
    await menu.get('summary').trigger('click')
    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    expect((menu.element as HTMLDetailsElement).open).toBe(false)

    await menu.get('summary').trigger('click')
    await menu.get('button').trigger('click')
    expect((menu.element as HTMLDetailsElement).open).toBe(false)
    wrapper.unmount()
  })

  it('lets users leave selection mode without leaving empty selection labels on cards', async () => {
    mocks.api.mockResolvedValue({ games: [{ game_key: 'web|select|gm', world_name: 'Selectable', state: 'ended' }] })
    const wrapper = await render()
    await wrapper.get('[data-testid="overview-select-saves"]').trigger('click')
    expect(wrapper.find('.game-select input').exists()).toBe(true)
    await wrapper.get('[data-testid="overview-exit-selection"]').trigger('click')
    expect(wrapper.find('.game-select input').exists()).toBe(false)
    wrapper.unmount()
  })

  it('marks only the selected save card for scenic background feedback', async () => {
    mocks.api.mockResolvedValue({ games: [
      { game_key: 'web|select|one', world_name: 'First save', state: 'ended' },
      { game_key: 'web|select|two', world_name: 'Second save', state: 'ended' },
    ] })
    const wrapper = await render()
    await wrapper.get('[data-testid="overview-select-saves"]').trigger('click')
    const cards = wrapper.findAll('.game-card')
    await cards[0].get('.game-select input').setValue(true)
    expect(cards[0].classes()).toContain('is-selected')
    expect(cards[1].classes()).not.toContain('is-selected')
    await wrapper.get('[data-testid="overview-exit-selection"]').trigger('click')
    expect(cards[0].classes()).not.toContain('is-selected')
    wrapper.unmount()
  })

  it('opens cover editing for the chosen save without changing the active game', async () => {
    mocks.api.mockResolvedValue({ games: [
      { game_key: 'web|cover|one', world_name: 'First save', rule_id: 'freeform_fantasy' },
      { game_key: 'web|cover|two', world_name: 'Second save', rule_id: 'freeform_coc' },
    ] })
    localStorage.setItem('currentGame', 'web|active|gm')
    const wrapper = await render()
    const menu = wrapper.findAll('.overview-game-tools')[1]
    await menu.get('summary').trigger('click')
    await menu.get('[data-testid="save-change-cover"]').trigger('click')
    await flushPromises()
    const dialog = wrapper.findComponent({ name: 'GameSceneImageDialog' })
    expect(dialog.exists()).toBe(true)
    expect(dialog.props()).toMatchObject({ gameKey: 'web|cover|two', ruleId: 'freeform_coc', gameName: 'Second save' })
    expect((menu.element as HTMLDetailsElement).open).toBe(false)
    expect(localStorage.getItem('currentGame')).toBe('web|active|gm')
    dialog.vm.$emit('close')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GameSceneImageDialog' }).exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps failed batch deletions selected and clears current game only after success', async () => {
    mocks.api
      .mockResolvedValueOnce({ games: [
        { game_key: 'web|delete|one', world_name: 'Delete me', state: 'ended' },
        { game_key: 'web|delete|two', world_name: 'Keep selected', state: 'ended' },
      ] })
      .mockResolvedValueOnce({ deleted: ['web|delete|one'], failed: ['web|delete|two'] })
      .mockResolvedValueOnce({ games: [{ game_key: 'web|delete|two', world_name: 'Keep selected', state: 'ended' }] })
    localStorage.setItem('currentGame', 'web|delete|one')
    const wrapper = await render()
    await wrapper.get('[data-testid="overview-select-saves"]').trigger('click')
    await wrapper.findAll('.game-select input')[0].setValue(true)
    await wrapper.findAll('.game-select input')[1].setValue(true)
    await wrapper.get('button.danger').trigger('click')
    await flushPromises()
    expect(localStorage.getItem('currentGame')).toBeNull()
    expect(wrapper.text()).toContain('1 已选择')
    wrapper.unmount()
  })

  it('updates the confirmed cover even if the archive index refresh fails', async () => {
    const key = 'web|cover-refresh|gm'
    mocks.api.mockResolvedValueOnce({ games: [{ game_key: key, world_name: 'Cover save', rule_id: 'freeform_coc' }] })
    const wrapper = await render()
    await wrapper.get('.overview-game-tools summary').trigger('click')
    await wrapper.get('[data-testid="save-change-cover"]').trigger('click')
    await flushPromises()
    mocks.api.mockRejectedValueOnce(new Error('index refresh offline'))
    vi.mocked(resolveGameSceneImageUrl).mockResolvedValueOnce('blob:confirmed-cover')
    vi.mocked(sceneImageStyle).mockImplementation(url => ({ '--df-bg-scene-image': `url("${url}")` }))
    const reference = { kind: 'upload', asset_id: 'confirmed-cover' }
    wrapper.findComponent({ name: 'GameSceneImageDialog' }).vm.$emit('saved', key, reference)
    await flushPromises()
    expect(wrapper.get('.game-card-art').attributes('style')).toContain('blob:confirmed-cover')
    wrapper.unmount()
  })
})
