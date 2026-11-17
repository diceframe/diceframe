import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const { apiMock, choicesMock, validateMock, finalizeMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  choicesMock: vi.fn(),
  validateMock: vi.fn(),
  finalizeMock: vi.fn(),
}))

vi.mock('@/api/client', () => ({ api: apiMock, errorMessage: (cause: unknown) => String(cause) }))
vi.mock('@/api/rulesets', () => ({
  fetchRulesetBuilderChoices: choicesMock,
  validateRulesetBuilderDraft: validateMock,
  finalizeRulesetBuilderCharacter: finalizeMock,
}))

import GenericCharacterBuilder from '@/features/rulesets/GenericCharacterBuilder.vue'

const CHOICES = {
  ok: true,
  rule_id: 'fabula_ultima',
  choices: {
    attributes: [
      { key: 'dex', name: '敏捷', options: [6, 8, 10, 12] },
      { key: 'ins', name: '洞察', options: [6, 8, 10, 12] },
      { key: 'mig', name: '力量', options: [6, 8, 10, 12] },
      { key: 'wlp', name: '意志', options: [6, 8, 10, 12] },
    ],
    attribute_total: 32,
    classes: ['奥灵使', '元素使', '守护者'],
    class_levels_total: 5,
    skills: { mode: 'total', total: 30, max_per_skill: 5, max_count: 6 },
  },
}

async function mountBuilder() {
  const wrapper = mount(GenericCharacterBuilder, {
    props: { ruleId: 'fabula_ultima', language: 'zh-CN' },
  })
  await flushPromises()
  return wrapper
}

describe('generic ruleset character builder', () => {
  beforeEach(() => {
    apiMock.mockReset().mockResolvedValue({ ok: true, rule: { skills: { s1: { name: '奥灵回响' } } } })
    choicesMock.mockReset().mockResolvedValue(CHOICES)
    validateMock.mockReset().mockResolvedValue({ ok: true, valid: true, errors: [] })
    finalizeMock.mockReset().mockResolvedValue({
      ok: true,
      character: { attributes: { dex: 8, ins: 8, mig: 8, wlp: 8 }, hp: 60, ruleset_character: {} },
    })
  })

  it('renders the attributes, classes and budgets the runtime returned', async () => {
    const wrapper = await mountBuilder()

    expect(choicesMock).toHaveBeenCalledWith('fabula_ultima', {}, 'zh-CN')
    const labels = wrapper.findAll('.attr span').map(node => node.text())
    expect(labels).toEqual(['敏捷', '洞察', '力量', '意志'])
    const options = wrapper.findAll('.attr select')[0].findAll('option').map(node => node.text())
    expect(options).toEqual(['d6', 'd8', 'd10', 'd12'])
    // 默认分配必须自己就合法：总和正好是 attribute_total。
    expect(wrapper.find('.budget').text()).toContain('32 / 32')
    expect(wrapper.findAll('.row-name')[0].text()).toBe('奥灵使')
  })

  it('validates first, then finalizes and submits the character with its name', async () => {
    const wrapper = await mountBuilder()

    await wrapper.find('.field input').setValue('德斯迪蒙娜')
    await wrapper.find('.primary').trigger('click')
    await flushPromises()

    const posted = validateMock.mock.calls[0][1] as Record<string, unknown>
    expect(posted.character_name).toBe('德斯迪蒙娜')
    expect(posted.attributes).toEqual({ dex: 8, ins: 8, mig: 8, wlp: 8 })
    expect(posted.class_levels).toEqual({ 奥灵使: 5 })
    expect(finalizeMock).toHaveBeenCalledTimes(1)

    const emitted = wrapper.emitted('submit')
    expect(emitted).toBeTruthy()
    const [character] = emitted![0] as [Record<string, unknown>]
    expect(character.character_name).toBe('德斯迪蒙娜')
    expect(character.ruleset_character).toEqual({})
  })

  it('keeps the card in the builder when the ruleset rejects it', async () => {
    validateMock.mockResolvedValue({ ok: true, valid: false, errors: ['属性总和必须是 32（当前 34）'] })
    const wrapper = await mountBuilder()

    await wrapper.find('.field input').setValue('德斯迪蒙娜')
    await wrapper.find('.primary').trigger('click')
    await flushPromises()

    expect(finalizeMock).not.toHaveBeenCalled()
    expect(wrapper.emitted('submit')).toBeFalsy()
    expect(wrapper.find('.error-banner').text()).toContain('属性总和必须是 32')
  })
})
