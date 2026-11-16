import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GameDetail, RulesetGameplayResponse } from '@/api/types'
import RulesetPanels from '@/features/play/RulesetPanels.vue'
import { useRulesetGameplay } from '@/features/play/useRulesetGameplay'

const { fetchActions, submitIntent } = vi.hoisted(() => ({ fetchActions: vi.fn(), submitIntent: vi.fn() }))
vi.mock('@/api/rulesets', () => ({ fetchRulesetAvailableActions: fetchActions, submitRulesetIntent: submitIntent }))

function detail(capabilities = {}): GameDetail {
  return {
    round_number: 1,
    ruleset_runtime: {
      id: 'test:panels', version: 1, requested_minimum_version: 1,
      capabilities: {
        experience_profile: 'test', character_builder: 'legacy', character_lifecycle: 'legacy',
        authoritative_intents: true, deterministic_combat: false, versioned_state: true,
        session_zero: false, tutorial_coach: false, narrative_turns: true,
        ...capabilities,
      },
    },
  } as GameDetail
}

function response(label: string): RulesetGameplayResponse {
  return {
    ok: true, game_key: 'game-a', rule_id: 'test', ruleset_runtime: detail().ruleset_runtime!,
    gameplay: {
      state_schema_version: 1, state_version: 1,
      combat: {
        status: 'none', round: 0, turn_index: 0, current_actor_id: '', initiative: [],
        position_mode: '', economy: {}, reactions: {}, pending_decisions: [], actors: [],
      },
      panels: [{ component: 'text', label: 'Clock', value: label }],
    },
    available_actions: [{ type: 'clock.advance', label, expected_version: 1 }],
  }
}

function deferred() {
  let resolve!: (value: RulesetGameplayResponse) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<RulesetGameplayResponse>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const wrappers: VueWrapper[] = []
function setup(capabilities = {}) {
  const context = {
    gameKey: ref('game-a'), viewer: ref('gm'), isGm: ref(true),
    detail: ref<GameDetail | null>(detail(capabilities)), stateSignal: ref(0),
  }
  let state!: ReturnType<typeof useRulesetGameplay>
  const wrapper = mount(defineComponent({
    setup() {
      state = useRulesetGameplay(context)
      return () => h(RulesetPanels, { gameKey: context.gameKey.value, panels: state.gameplay.value?.panels })
    },
  }))
  wrappers.push(wrapper)
  return { context, state, wrapper }
}

beforeEach(() => {
  vi.useFakeTimers()
  fetchActions.mockReset().mockResolvedValue(response('Current'))
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('viewer-scoped ruleset gameplay synchronization', () => {
  it.each([
    ['sequential checks', { sequential_checks: true }],
    ['composite actions', { composite_action: true }],
    ['only authoritative intents', {}],
  ])('renders non-combat panels with %s and shares the action response', async (_name, capabilities) => {
    const { state, wrapper } = setup(capabilities)
    await flushPromises()
    expect(fetchActions).toHaveBeenCalledExactlyOnceWith('game-a')
    expect(wrapper.get('[data-testid="ruleset-panels"]').text()).toContain('Current')
    expect(state.availableActions.value[0]?.label).toBe('Current')
  })

  it('does not query without backend admission, and clears when that capability is revoked', async () => {
    const { context, state } = setup({ authoritative_intents: false, sequential_checks: true })
    await flushPromises()
    expect(fetchActions).not.toHaveBeenCalled()
    context.detail.value = detail()
    await flushPromises()
    expect(state.gameplay.value?.panels).toEqual(response('Current').gameplay.panels)
    context.detail.value = detail({ authoritative_intents: false })
    expect(state.gameplay.value).toBeNull()
    expect(state.availableActions.value).toEqual([])
    await vi.advanceTimersByTimeAsync(30000)
    expect(fetchActions).toHaveBeenCalledTimes(1)
  })

  it.each(['game', 'viewer', 'gm-role', 'runtime'] as const)('clears immediately and ignores late success or failure after a %s switch', async (field) => {
    const { context, state, wrapper } = setup()
    await flushPromises()
    const lateSuccess = deferred(), lateFailure = deferred(), current = deferred()
    fetchActions.mockReturnValueOnce(lateSuccess.promise).mockReturnValueOnce(lateFailure.promise).mockReturnValueOnce(current.promise)
    const first = state.refresh(), second = state.refresh()
    if (field === 'game') context.gameKey.value = 'game-b'
    if (field === 'viewer') context.viewer.value = 'player'
    if (field === 'gm-role') context.isGm.value = false
    if (field === 'runtime') context.detail.value!.ruleset_runtime!.id = 'test:other'
    // No old GM/game snapshot remains visible even before the new request settles.
    expect(state.gameplay.value).toBeNull()
    expect(state.availableActions.value).toEqual([])
    current.resolve(response('New context'))
    await flushPromises()
    lateSuccess.resolve(response('Private old snapshot'))
    lateFailure.reject(new Error('Old request failed'))
    await Promise.all([first, second])
    await flushPromises()
    expect(wrapper.text()).toContain('New context')
    expect(wrapper.text()).not.toContain('Private old snapshot')
    expect(state.availableActions.value[0]?.label).toBe('New context')
  })

  it('fences a game A -> B -> A switch within one tick', async () => {
    const old = deferred()
    fetchActions.mockReturnValueOnce(old.promise)
    const { context, wrapper } = setup()
    context.gameKey.value = 'game-b'
    context.gameKey.value = 'game-a'
    await flushPromises()
    old.resolve(response('Old A'))
    await flushPromises()
    expect(wrapper.text()).toContain('Current')
    expect(wrapper.text()).not.toContain('Old A')
  })

  it('keeps the latest same-context refresh and clears a current failure', async () => {
    const old = deferred()
    fetchActions.mockReturnValueOnce(old.promise)
    const { state, wrapper } = setup()
    await state.refresh()
    old.resolve(response('Old'))
    await flushPromises()
    expect(wrapper.text()).toContain('Current')
    fetchActions.mockRejectedValueOnce(new Error('Unavailable'))
    await state.refresh()
    await flushPromises()
    expect(state.gameplay.value).toBeNull()
    expect(state.availableActions.value).toEqual([])
    expect(wrapper.find('[data-testid="ruleset-panels"]').exists()).toBe(false)
  })

  it('refreshes non-combat gameplay on rounds, ruleset signals and visible polling; stops on unmount', async () => {
    const { context, state, wrapper } = setup()
    await flushPromises()
    context.detail.value!.round_number = 2
    await flushPromises()
    context.stateSignal.value += 1
    await flushPromises()
    expect(fetchActions).toHaveBeenCalledTimes(3)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    await vi.advanceTimersByTimeAsync(30000)
    expect(fetchActions).toHaveBeenCalledTimes(3)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    await vi.advanceTimersByTimeAsync(30000)
    expect(fetchActions).toHaveBeenCalledTimes(4)
    const late = deferred()
    fetchActions.mockReturnValueOnce(late.promise)
    const pending = state.refresh()
    wrapper.unmount()
    late.resolve(response('After unmount'))
    await pending
    await vi.advanceTimersByTimeAsync(30000)
    expect(fetchActions).toHaveBeenCalledTimes(5)
    expect(state.availableActions.value[0]?.label).toBe('Current')
  })
})
