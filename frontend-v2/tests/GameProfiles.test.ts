import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { i18n } from '../src/i18n'
import ProfileBoard from '../src/features/game-profiles/ProfileBoard.vue'
import PlayEntry from '../src/features/game-profiles/PlayEntry.vue'
import type { ProfileView } from '../src/features/game-profiles/types'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../src/api/client', async original => ({ ...await original<typeof import('../src/api/client')>(), api: mocks.api }))
vi.mock('../src/features/play/PlayView.vue', () => ({ default: { template: '<div data-testid="legacy-play">Narrative game</div>' } }))
vi.mock('../src/features/gal/GalPlayView.vue', () => ({ default: { template: '<div data-testid="gal-play">Gal game</div>' } }))

const view = (overrides: Partial<ProfileView> = {}): ProfileView => ({
  ok: true, profile: { id: 'core:demo-survival', version: 1, title: 'Shelter', ui_sections: ['actors', 'resources', 'history'] },
  game_name: 'Shelter', gm_uid: 'gm', revision: 3, run_id: 'run_one', status: 'active', is_gm: false,
  actor_uid: 'p1', control_revision: 2, control_mode: 'human', progression: { mode: 'calendar', phase: 'day', cycle: 1, day: 2, acted: {} },
  resources: { food: 4 }, clocks: {}, actors: [{ uid: 'p1', name: 'Scout', health: 5, inventory: [], control: { mode: 'human', revision: 2 } }],
  actions: [{ id: 'forage' }], can_advance: false, history: [], ...overrides,
})
beforeEach(() => { mocks.api.mockReset(); i18n.global.locale.value = 'en' })
const global = { plugins: [i18n], stubs: { RouterLink: { template: '<a><slot /></a>' } } }

it('offers only server-authorized moves and submits the exact saved run, revision, seat and control version', async () => {
  const initial = view()
  mocks.api.mockResolvedValue({ ok: true, view: view({ revision: 4, actions: [], resources: { food: 7 } }) })
  const wrapper = mount(ProfileBoard, { props: { gameKey: 'web|shelter|bot', initial }, global })
  expect(wrapper.find('.scene-strip').exists()).toBe(true)
  expect(wrapper.find('.scene-title').text()).toBe('Shelter')
  expect(wrapper.find('.scene-subtitle').text()).toContain('Survive five days')
  expect(wrapper.find('.scene-chips').text()).toContain('active')
  expect(wrapper.find('.scene-chips').text()).toContain('Day: 2')
  expect(wrapper.find('[data-testid=profile-advance]').exists()).toBe(false)
  expect(wrapper.find('[data-testid=profile-control]').exists()).toBe(false)
  expect(wrapper.find('[data-testid=profile-move-build]').exists()).toBe(false)
  await wrapper.get('[data-testid=profile-move-forage]').trigger('click'); await flushPromises()
  expect(JSON.parse(mocks.api.mock.calls[0]![1].body)).toEqual({ type: 'move', move_id: 'forage', actor_uid: 'p1', expected_run_id: 'run_one', expected_revision: 3, expected_control_revision: 2 })
  expect(wrapper.get('[data-testid=profile-resource-food]').text()).toBe('7')
  expect(wrapper.find('[data-testid=profile-move-forage]').exists()).toBe(false)
  wrapper.unmount()
})

it('blocks actions during actor refresh, keeps the old authoritative state on failure and refreshes fences before the next action', async () => {
  const wrapper = mount(ProfileBoard, { props: { gameKey: 'web|shelter|bot', initial: view({ is_gm: true, actors: [...view().actors, { uid: 'p2', name: 'Leader', inventory: [], control: { mode: 'human', revision: 7 } }] }) }, global })
  let reject!: (cause: Error) => void
  mocks.api.mockReturnValueOnce(new Promise((_resolve, failure) => { reject = failure }))
  await wrapper.get('[data-testid=profile-actor]').setValue('p2')
  expect(wrapper.get('[data-testid=profile-move-forage]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-testid=profile-move-forage]').trigger('click')
  expect(mocks.api).toHaveBeenCalledTimes(1)
  reject(new Error('Refresh unavailable')); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('Refresh unavailable')
  mocks.api.mockResolvedValueOnce(view({ is_gm: true, actor_uid: 'p2', control_revision: 7, revision: 8, run_id: 'run_two' }))
  await wrapper.get('[data-testid=profile-actor]').setValue('p2'); await flushPromises()
  mocks.api.mockResolvedValueOnce({ ok: true, view: view({ actions: [] }) })
  await wrapper.get('[data-testid=profile-move-forage]').trigger('click'); await flushPromises()
  expect(JSON.parse(mocks.api.mock.calls[2]![1].body)).toMatchObject({ actor_uid: 'p2', expected_control_revision: 7, expected_revision: 8, expected_run_id: 'run_two' })
  wrapper.unmount()
})

async function entry(game: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/play', name: 'play', component: PlayEntry }, { path: '/overview', component: { template: '<div>Overview</div>' } }] })
  await router.push({ path: '/play', query: { game } }); await router.isReady()
  return { router, wrapper: mount(PlayEntry, { global: { plugins: [i18n, router] } }) }
}

it('keeps narrative and peer games on their established entry without probing protected profile subroutes', async () => {
  mocks.api.mockResolvedValue({ game_profile: null, has_room_password: true })
  const { wrapper } = await entry('legacy'); await flushPromises()
  expect(mocks.api.mock.calls.map(call => call[0])).toEqual(['/games/legacy'])
  expect(wrapper.find('[data-testid=legacy-play]').exists()).toBe(true)
  wrapper.unmount()
})

it('routes a game to the play view its gameplay mode declares', async () => {
  mocks.api.mockResolvedValueOnce({ game_profile: null, play_mode: 'gal' })
  const { wrapper } = await entry('gal'); await flushPromises()
  expect(wrapper.find('[data-testid=gal-play]').exists()).toBe(true)
  expect(wrapper.find('[data-testid=legacy-play]').exists()).toBe(false)
  wrapper.unmount()
})

it('keeps tabletop and unknown modes on the established play view', async () => {
  for (const play_mode of ['free', 'adventure', 'werewolf', undefined]) {
    mocks.api.mockReset(); mocks.api.mockResolvedValueOnce({ game_profile: null, play_mode })
    const { wrapper } = await entry(`table-${play_mode}`); await flushPromises()
    expect(wrapper.find('[data-testid=legacy-play]').exists()).toBe(true)
    expect(wrapper.find('[data-testid=gal-play]').exists()).toBe(false)
    wrapper.unmount()
  }
})

it('fails closed for unsupported profile state without showing a narrative composer', async () => {
  mocks.api.mockResolvedValueOnce({ game_profile: { unsupported: true } }).mockRejectedValueOnce(new Error('Unsupported profile version'))
  const { wrapper } = await entry('future'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('Unsupported profile version')
  expect(wrapper.find('[data-testid=legacy-play]').exists()).toBe(false)
  wrapper.unmount()
})

it('ignores a stale routing response after navigation to another game', async () => {
  let resolve!: (value: object) => void
  mocks.api.mockReturnValueOnce(new Promise(done => { resolve = done })).mockResolvedValueOnce({ game_profile: null })
  const { wrapper, router } = await entry('first')
  await router.push({ path: '/play', query: { game: 'second' } }); await flushPromises()
  resolve({ game_profile: { id: 'core:demo-heist' } }); await flushPromises()
  expect(mocks.api.mock.calls.map(call => call[0])).toEqual(['/games/first', '/games/second'])
  expect(wrapper.find('[data-testid=legacy-play]').exists()).toBe(true)
  wrapper.unmount()
})

it('keeps the established play view cached while visiting another page and returning to the same table', async () => {
  mocks.api.mockResolvedValue({ game_profile: null })
  const { wrapper, router } = await entry('legacy'); await flushPromises()
  const legacy = wrapper.get('[data-testid=legacy-play]').element
  await router.push('/overview'); await flushPromises()
  await router.push({ name: 'play', query: { game: 'legacy' } }); await flushPromises()
  expect(mocks.api).toHaveBeenCalledTimes(1)
  expect(wrapper.get('[data-testid=legacy-play]').element).toBe(legacy)
  wrapper.unmount()
})
