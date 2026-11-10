import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type { PublicAction } from '../src/api/types'
import { useGame } from '../src/composables/useGame'

const GAME_KEY = 'quick-tunnel-game'
const mocks = vi.hoisted(() => ({ api: vi.fn(), events: vi.fn(), peer: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: { game: GAME_KEY, user: 'guest', share: '1' } }) }))
vi.mock('../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/client')>()),
  api: mocks.api,
  gameEventSource: mocks.events,
  hasAccessToken: () => false,
}))
vi.mock('../src/peer/game/bridge', () => ({ activePeerGameClient: mocks.peer }))
vi.mock('../src/api/mapBackgrounds', () => ({
  resolveMapBackgroundAsset: async (map: unknown) => map,
  revokeMapBackgroundAsset: () => undefined,
}))

let wrapper: ReturnType<typeof mount> | undefined
let actions: PublicAction[]

function useHostname(hostname: string) {
  vi.stubGlobal('window', {
    location: { hostname },
    setInterval: globalThis.setInterval,
    setTimeout: globalThis.setTimeout,
  })
}

async function okApi(path: string) {
  if (path.endsWith('/characters')) return { players: [] }
  if (path.endsWith('/log')) return { log: [] }
  if (path.endsWith('/private-log')) return { messages: [] }
  if (path.endsWith('/table-talk')) return { exchanges: [] }
  if (path.endsWith('/map')) return { locations: [] }
  return { game_key: GAME_KEY, gm_uid: 'host', multiplayer: { submitted_actions: actions } }
}

async function loadGame() {
  let game!: ReturnType<typeof useGame>
  wrapper = mount(defineComponent({ setup() { game = useGame(); return () => null } }))
  await game.refresh()
  await game.connect()
  mocks.api.mockClear()
  return game
}

beforeEach(() => {
  vi.useFakeTimers()
  actions = []
  mocks.api.mockReset().mockImplementation(okApi)
  mocks.events.mockReset().mockImplementation(async () => ({ close: vi.fn(), addEventListener: vi.fn() }))
  mocks.peer.mockReset().mockReturnValue(null)
  useHostname('table.trycloudflare.com')
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it('shows another player action within two seconds without an SSE event or a local submission', async () => {
  const game = await loadGame()
  expect(game.detail.value?.multiplayer?.submitted_actions).toEqual([])
  actions = [{ user_id: 'host', text: 'Inspect the door' }]

  await vi.advanceTimersByTimeAsync(1999)
  expect(mocks.api).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)

  expect(game.detail.value?.multiplayer?.submitted_actions).toEqual(actions)
  expect(mocks.events).not.toHaveBeenCalled()
})

it('lets a slow snapshot finish before polling again', async () => {
  await loadGame()
  let resolve!: (value: unknown) => void
  const pending = new Promise(done => { resolve = done })
  mocks.api.mockImplementation((path: string) => path === `/games/${GAME_KEY}` ? pending : okApi(path))

  await vi.advanceTimersByTimeAsync(6000)
  expect(mocks.api.mock.calls.filter(([path]) => path === `/games/${GAME_KEY}`)).toHaveLength(1)
  resolve(await okApi(`/games/${GAME_KEY}`))
  await flushPromises()
  await vi.advanceTimersByTimeAsync(2000)
  expect(mocks.api.mock.calls.filter(([path]) => path === `/games/${GAME_KEY}`)).toHaveLength(2)
})

it('replaces the poller on reconnect and stops it on unmount', async () => {
  const game = await loadGame()
  await game.connect()
  await vi.advanceTimersByTimeAsync(2000)
  expect(mocks.api).toHaveBeenCalledTimes(6)

  wrapper!.unmount()
  wrapper = undefined
  await vi.advanceTimersByTimeAsync(6000)
  expect(mocks.api).toHaveBeenCalledTimes(6)
})

it('stops polling when the host closes player access', async () => {
  const game = await loadGame()
  mocks.api.mockRejectedValue(new ApiError('Player entrance closed', 403, 'PLAYER_ACCESS_CLOSED'))
  await vi.advanceTimersByTimeAsync(2000)
  expect(game.accessClosed.value).toBe(true)
  const calls = mocks.api.mock.calls.length
  await vi.advanceTimersByTimeAsync(10000)
  expect(mocks.api).toHaveBeenCalledTimes(calls)
})

it.each(['localhost', '127.0.0.1', 'play.example.com', 'table.trycloudflare.com.example.com'])(
  'keeps SSE for %s', async (hostname) => {
    useHostname(hostname)
    await loadGame()
    expect(mocks.events).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.api).not.toHaveBeenCalled()
  },
)

it('keeps peer subscriptions ahead of the Quick Tunnel fallback', async () => {
  const unsubscribe = vi.fn()
  const subscribe = vi.fn(() => unsubscribe)
  mocks.peer.mockReturnValue({ gameKey: GAME_KEY, subscribe })
  await loadGame()
  expect(subscribe).toHaveBeenCalledTimes(1)
  expect(mocks.events).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2000)
  expect(mocks.api).not.toHaveBeenCalled()

  wrapper!.unmount()
  wrapper = undefined
  expect(unsubscribe).toHaveBeenCalledTimes(1)
})
