import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const submit = vi.hoisted(() => vi.fn())
vi.mock('../src/api/rulesets', () => ({ submitRulesetIntent: submit }))

import SequentialActionComposer from '../src/features/play/SequentialActionComposer.vue'

function composer() {
  return mount(SequentialActionComposer, {
    props: {
      gameKey: 'web|room|bot', actorId: 'player-1', sequentialChecks: true,
      actions: [
        { type: 'skill', label: '观察', skill_id: 'notice', target: { id: 'door' }, expected_version: 4 },
        { type: 'skill', label: '撬锁', skill_id: 'lockpick', expected_version: 4 },
      ],
    },
  })
}

describe('SequentialActionComposer', () => {
  beforeEach(() => { submit.mockReset().mockResolvedValue({ ok: true }) })

  it('submits ordered server-owned steps and keeps client free of dice/results', async () => {
    const wrapper = mount(SequentialActionComposer, {
      props: {
        gameKey: 'web|room|bot', actorId: 'player-1', sequentialChecks: true,
        actions: [
          { type: 'skill', label: '观察', actor_id: 'player-1', skill_id: 'notice', expected_version: 4 },
          { type: 'skill', label: '撬锁', actor_id: 'player-1', skill_id: 'lockpick', expected_version: 4 },
        ],
      },
    })
    await wrapper.findAll('.sequential-options button')[0]!.trigger('click')
    await wrapper.findAll('.sequential-options button')[1]!.trigger('click')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()

    expect(submit).toHaveBeenCalledOnce()
    const intent = submit.mock.calls[0][1]
    expect(intent).toMatchObject({ type: 'sequential_action', mode: 'sequential_checks' })
    expect(intent.steps).toEqual([
      expect.objectContaining({ step_id: 'step-1', order: 0, skill_id: 'notice', depends_on: [] }),
      expect.objectContaining({ step_id: 'step-2', order: 1, skill_id: 'lockpick', depends_on: ['step-1'] }),
    ])
    expect(intent.steps[0]).not.toHaveProperty('dice')
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('retries a lost response with the same identity and DTO, then starts a fresh command after success', async () => {
    const executed = new Set<string>()
    let effects = 0
    submit.mockImplementation(async (_game, intent) => {
      if (!executed.has(intent.intent_id)) {
        executed.add(intent.intent_id)
        effects++
        throw new Error('response lost after commit')
      }
      return { ok: true }
    })
    const wrapper = composer()
    await wrapper.findAll('.sequential-options button')[0]!.trigger('click')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(wrapper.get('.error').text()).toContain('response lost')
    expect(wrapper.emitted('changed')).toBeUndefined()
    const first = structuredClone(submit.mock.calls[0]![1])
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(submit.mock.calls[1]![1]).toEqual(first)
    expect(effects).toBe(1)
    expect(wrapper.emitted('changed')).toHaveLength(1)
    await wrapper.findAll('.sequential-options button')[0]!.trigger('click')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(submit.mock.calls[2]![1].intent_id).not.toBe(first.intent_id)
    expect(effects).toBe(2)
  })

  it.each(['draft', 'game', 'actor', 'mode', 'target'])('starts a fresh identity after changing %s', async (change) => {
    submit.mockRejectedValue(new Error('offline'))
    const wrapper = composer()
    await wrapper.findAll('.sequential-options button')[0]!.trigger('click')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    const first = structuredClone(submit.mock.calls[0]![1])
    if (change === 'draft') await wrapper.findAll('.sequential-options button')[1]!.trigger('click')
    if (change === 'game') await wrapper.setProps({ gameKey: 'web|other|bot' })
    if (change === 'actor') await wrapper.setProps({ actorId: 'player-2' })
    if (change === 'mode') await wrapper.setProps({ sequentialChecks: false, compositeAction: true })
    if (change === 'target') (wrapper.props('actions')[0]!.target as { id: string }).id = 'window'
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(submit.mock.calls[1]![1].intent_id).not.toBe(first.intent_id)
    expect(submit.mock.calls[0]![1]).toEqual(first) // First DTO was not mutated in place.
  })

  it('locks draft edits and duplicate submits while a request is pending', async () => {
    let resolve!: (value: { ok: boolean }) => void
    submit.mockImplementation(() => new Promise(done => { resolve = done }))
    const wrapper = composer()
    await wrapper.findAll('.sequential-options button')[0]!.trigger('click')
    await wrapper.get('.primary').trigger('click')
    expect(wrapper.findAll('.sequential-selected button')[0]!.attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.sequential-options button')[1]!.attributes('disabled')).toBeDefined()
    await wrapper.get('.primary').trigger('click')
    expect(submit).toHaveBeenCalledOnce()
    resolve({ ok: true })
    await flushPromises()
    expect(wrapper.findAll('.sequential-selected button')).toHaveLength(0)
  })
})
