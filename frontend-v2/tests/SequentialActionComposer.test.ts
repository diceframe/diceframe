import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const submit = vi.hoisted(() => vi.fn())
vi.mock('../src/api/rulesets', () => ({ submitRulesetIntent: submit }))

import SequentialActionComposer from '../src/features/play/SequentialActionComposer.vue'

describe('SequentialActionComposer', () => {
  beforeEach(() => submit.mockReset().mockResolvedValue({ ok: true }))

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
})
