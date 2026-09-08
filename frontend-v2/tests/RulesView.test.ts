import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  confirm: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  api: mocks.api,
}))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: mocks.success, error: mocks.error }),
}))
vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: mocks.confirm }),
}))

import RulesView from '../src/features/admin/RulesView.vue'
import { blockFromDraft, draftFromBlock } from '../src/features/admin/combatExtensionDraft'

function builtinRules() {
  return {
    rules: [{
      rule_id: 'freeform_fantasy',
      rule_name: 'Classic Fantasy',
      description: 'Baseline rule',
      dice_system: 'd20',
      combat_model: 'hp_based',
      custom: false,
      attr_count: 6,
    }],
  }
}

function ruleTemplate() {
  return {
    ok: true,
    rule: {
      rule_id: 'freeform_fantasy',
      rule_name: 'Classic Fantasy',
      description: 'Baseline rule',
      dice_system: 'd20',
      combat_model: 'hp_based',
      attributes: [],
      combat: {
        scheduler: {
          kind: 'threshold',
          threshold: 100,
          overflow: 'carry',
          consume: 'reset',
          gauge: 'action_gauge',
          speed: 'action_speed',
          speed_formula: { op: 'attribute', id: 'dex' },
        },
        resources: [
          { id: 'hp', source: 'hp' },
          { id: 'qi', source: 'special_stat', stat: 'qi', maximum: 100, costable: true },
        ],
        actions: [{
          id: 'ability:qi_palm',
          kind: 'ability',
          name: '内力掌',
          costs: [{ resource: 'qi', amount: { op: 'constant', value: 8 } }],
          consume_item: { item: '回春丹', qty: 1 },
          effects: [{ kind: 'damage', amount: { op: 'constant', value: 10 }, damage_type: 'bludgeoning' }],
        }, {
          id: 'ability:escape_step',
          kind: 'ability',
          name: '遁术',
          costs: [],
          effects: [{
            kind: 'modify_stat',
            resource: 'action_speed',
            amount: { op: 'constant', value: 50 },
            duration: 1,
          }],
        }],
      },
    },
  }
}

describe('RulesView combat editor integration', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    mocks.api.mockReset()
    mocks.success.mockReset()
    mocks.error.mockReset()
    mocks.confirm.mockReset().mockResolvedValue(true)
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/rules' && (!init || !init.method || init.method === 'GET')) return builtinRules()
      if (path === '/rules/freeform_fantasy' && (!init || !init.method || init.method === 'GET')) return ruleTemplate()
      if (path === '/rules' && init?.method === 'POST') {
        return { ok: true, rule: { rule_id: 'custom_rule_test', rule_name: 'Custom Rule' } }
      }
      if (String(path).startsWith('/rules/custom_rule_') && init?.method === 'PUT') {
        return { ok: true, rule: { rule_id: String(path).split('/').pop(), rule_name: 'Custom Rule' } }
      }
      throw new Error(`unexpected api call: ${path} ${init?.method || 'GET'}`)
    })
  })

  it('mounts the combat editor in the rule modal and persists the combat block', async () => {
    const wrapper = mount(RulesView, {
      global: {
        plugins: [i18n],
        stubs: {
          Modal: { template: '<div class="modal"><slot /><slot name="actions" /></div>' },
        },
      },
    })
    await flushPromises()

    const newRuleButton = wrapper.findAll('button').find(button => button.text().includes('新建规则'))
    expect(newRuleButton).toBeTruthy()
    await newRuleButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('战斗扩展（可选声明）')
    const inputValues = wrapper.findAll('input').map(input => (input.element as HTMLInputElement).value)
    expect(inputValues).toContain('内力掌')
    expect(inputValues).toContain('回春丹')

    const qiCost = wrapper.findAll('.cee-sub input[type="number"]')
      .find(input => (input.element as HTMLInputElement).value === '8')
    expect(qiCost).toBeTruthy()
    await qiCost!.setValue('9')

    const saveButton = wrapper.findAll('button').find(button => button.text().includes('保存规则'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()

    const putCall = [...mocks.api.mock.calls].reverse().find(call => String(call[0]).startsWith('/rules/custom_rule_') && call[1]?.method === 'PUT')
    expect(putCall).toBeTruthy()
    const body = JSON.parse(String((putCall?.[1] as RequestInit | undefined)?.body || '{}'))
    expect(body.combat).toMatchObject({
      scheduler: {
        kind: 'threshold',
        threshold: 100,
        overflow: 'carry',
        consume: 'reset',
        gauge: 'action_gauge',
        speed: 'action_speed',
      },
    })
    expect(body.combat.actions[0]).toMatchObject({
      id: 'ability:qi_palm',
      consume_item: { item: '回春丹', qty: 1 },
      costs: [{ resource: 'qi', amount: { op: 'constant', value: 9 } }],
    })
    expect(body.combat.scheduler.speed_formula).toEqual({ op: 'attribute', id: 'dex' })
    expect(body.combat.actions[1].effects[0]).toMatchObject({
      kind: 'modify_stat',
      resource: 'action_speed',
      duration: 1,
    })
    wrapper.unmount()
  })

  it('preserves future combat metadata on an otherwise empty block', async () => {
    const template = ruleTemplate()
    template.rule.combat = {
      resources: [{ id: 'hp', source: 'hp', future_resource_flag: 'keep' }],
      actions: [],
      future_capability: { version: 2 },
    } as unknown as typeof template.rule.combat
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/rules' && (!init || !init.method || init.method === 'GET')) return builtinRules()
      if (path === '/rules/freeform_fantasy' && (!init || !init.method || init.method === 'GET')) return template
      if (path === '/rules' && init?.method === 'POST') {
        return { ok: true, rule: { rule_id: 'custom_rule_test', rule_name: 'Custom Rule' } }
      }
      if (String(path).startsWith('/rules/custom_rule_') && init?.method === 'PUT') {
        return { ok: true, rule: { rule_id: String(path).split('/').pop(), rule_name: 'Custom Rule' } }
      }
      throw new Error(`unexpected api call: ${path} ${init?.method || 'GET'}`)
    })
    const wrapper = mount(RulesView, {
      global: {
        plugins: [i18n],
        stubs: { Modal: { template: '<div class="modal"><slot /><slot name="actions" /></div>' } },
      },
    })
    await flushPromises()
    const newRuleButton = wrapper.findAll('button').find(button => button.text().includes('新建规则'))
    await newRuleButton!.trigger('click')
    await flushPromises()
    const saveButton = wrapper.findAll('button').find(button => button.text().includes('保存规则'))
    await saveButton!.trigger('click')
    await flushPromises()

    const putCall = [...mocks.api.mock.calls].reverse().find(call => String(call[0]).startsWith('/rules/custom_rule_') && call[1]?.method === 'PUT')
    const body = JSON.parse(String((putCall?.[1] as RequestInit | undefined)?.body || '{}'))
    expect(body.combat.future_capability).toEqual({ version: 2 })
    expect(body.combat.resources[0].future_resource_flag).toBe('keep')
    wrapper.unmount()
  })

  it('drops combat-only resource metadata when its source changes', () => {
    const draft = draftFromBlock({
      resources: [{ id: 'barrier', source: 'combat_state', maximum: 10, damage_priority: 'before_hp' }],
      actions: [],
    })!
    draft.resources[0].source = 'hp'

    const block = blockFromDraft(draft)

    expect(block.resources).toEqual([{
      id: 'barrier',
      source: 'hp',
      maximum: 10,
    }])
  })
})
