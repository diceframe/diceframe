import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  choices: vi.fn(), validate: vi.fn(), derive: vi.fn(), finalize: vi.fn(),
}))

vi.mock('../src/features/rulesets/dnd2024/api', () => ({
  fetchRulesetBuilderChoices: mocks.choices,
  validateRulesetBuilderDraft: mocks.validate,
  deriveRulesetBuilderCharacter: mocks.derive,
  finalizeRulesetBuilderCharacter: mocks.finalize,
}))

import Dnd2024CharacterBuilder from '../src/features/rulesets/dnd2024/create/Dnd2024CharacterBuilder.vue'

const legalDraft = {
  class_ref: 'class:fighter', species_ref: 'species:human', background_ref: 'background:soldier',
  species_size: 'medium', species_skill_refs: ['skill:acrobatics'], species_feat_refs: ['feat:alert'],
  class_skill_refs: ['skill:perception', 'skill:survival'], class_tool_refs: [],
  equipment_package_ref: 'equipment_package:fighter_a',
  background_equipment_package_ref: 'equipment_package:soldier_a',
  ability_method: 'standard_array',
  base_abilities: { str: 15, dex: 14, con: 13, int: 8, wis: 10, cha: 12 },
  background_ability_bonuses: { str: 2, con: 1 },
  language_refs: ['language:common', 'language:dwarvish', 'language:elvish'],
  alignment: 'neutral_good',
}

const choices = {
  ability_methods: [{ id: 'standard_array', values: [15, 14, 13, 12, 10, 8] }],
  classes: [], species: [], backgrounds: [], class_skills: [], class_skill_count: 0,
  equipment_packages: [], background_equipment_packages: [], background_ability_refs: [],
  species_sizes: [], species_choices: [], species_skills: [], species_skill_count: 0,
  species_feats: [], species_feat_count: 0, class_tools: [], class_tool_count: 0,
  recommended_base_abilities: {}, skills: [], languages: [], origin_feats: [],
  class_spells: {}, recommended_class_spells: {}, feat_choices: [],
  quick_presets: [{
    ref: 'quick_character_preset:guardian', id: 'guardian', name: '可靠守护者',
    summary: '直观而坚韧。', recommendation_reason: '适合第一次进入战斗。',
    automation_level: 'deterministic', source_ref: 'diceframe-original:test',
    difficulty: 'beginner', fantasy_tags: ['melee', 'durable'], draft: legalDraft,
  }],
}

function mountBuilder(props: Record<string, unknown>) {
  return mount(Dnd2024CharacterBuilder, {
    global: { plugins: [createPinia()] },
    props: {
      ruleId: 'dnd2024_srd', language: 'zh-CN',
      experience: {
        profile: 'dnd2024', builder_mode: 'professional',
        modes: ['quick', 'guided', 'expert'], content_version: 'srd-5.2.1+r4', locale: 'zh-CN',
      },
      ...props,
    },
  })
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find(item => item.text().includes(text))
  expect(button, `应找到按钮: ${text}`).toBeTruthy()
  return button!
}

// 用真实交互走到引导模式指定步骤：选预设 → 填名字 → 切引导 → 点「下一步」
async function reachGuidedStep(wrapper: VueWrapper, step: number, nextLabel = '下一步') {
  await wrapper.findAll('button').find(item => item.text().includes('可靠守护者'))!.trigger('click')
  const nameInput = wrapper.findAll('label').find(item => item.text().includes('角色名'))!.find('input')
  await nameInput.setValue('阿岚')
  await wrapper.findAll('[role="tab"]').find(item => item.text() === '引导创建')!.trigger('click')
  for (let current = 1; current < step; current += 1) {
    await buttonByText(wrapper, nextLabel).trigger('click')
    await wrapper.vm.$nextTick()
  }
}

describe('D&D 2024 professional character builder', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.choices.mockReset().mockResolvedValue({ ok: true, rule_id: 'dnd2024_srd', choices })
    mocks.validate.mockReset().mockResolvedValue({ ok: true, valid: true, errors: [] })
    mocks.derive.mockReset().mockResolvedValue({ ok: true, character: {} })
    mocks.finalize.mockReset().mockResolvedValue({
      ok: true, rule_id: 'dnd2024_srd', character: {
        character_name: '阿岚', rule_id: 'dnd2024_srd', ruleset_character: { rule_binding: {} },
      },
    })
  })

  it('finishes a server-validated quick preset with only a required name', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()

    await wrapper.findAll('button').find(item => item.text().includes('可靠守护者'))!.trigger('click')
    const nameInput = wrapper.findAll('label').find(item => item.text().includes('角色名'))!.find('input')
    await nameInput.setValue('阿岚')
    await buttonByText(wrapper, '完成并使用这个角色').trigger('click')
    await flushPromises()

    expect(mocks.validate).toHaveBeenCalledOnce()
    const submittedDraft = mocks.validate.mock.calls[0]?.[1]
    expect(submittedDraft).toMatchObject({ ...legalDraft, name: '阿岚', locale: 'zh-CN' })
    expect(mocks.finalize).toHaveBeenCalledOnce()
    expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({
      character_name: '阿岚', rule_id: 'dnd2024_srd',
    })
    // Default starting level is 1: the option is still reported, so the host
    // never has to guess, but the draft the server validates is unchanged.
    expect(wrapper.emitted('submit')?.[0]?.[1]).toEqual({ targetLevel: 1 })
  })

  it('reports a higher starting level without changing the level-1 draft', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()

    await wrapper.findAll('button').find(item => item.text().includes('可靠守护者'))!.trigger('click')
    const nameInput = wrapper.findAll('label').find(item => item.text().includes('角色名'))!.find('input')
    await nameInput.setValue('阿岚')
    await wrapper.get('.start-level-field select').setValue('3')
    expect(wrapper.text()).toContain('第 2 级到第 3 级')
    await buttonByText(wrapper, '完成并使用这个角色').trigger('click')
    await flushPromises()

    // The builder still validates and finalizes a level-1 card; the target is a
    // flow parameter, so validation's level-1 rule keeps holding.
    expect(mocks.validate.mock.calls[0]?.[1]).toMatchObject({ level: 1 })
    expect(mocks.finalize.mock.calls[0]?.[1]).toMatchObject({ level: 1 })
    expect(wrapper.emitted('submit')?.[0]?.[1]).toEqual({ targetLevel: 3 })
  })

  it('shows a quick-mode finish failure instead of silently staying put', async () => {
    mocks.validate.mockRejectedValueOnce(new Error('操作太频繁，请等待约 14 秒后再试。'))
    const wrapper = mountBuilder({})
    await flushPromises()

    await wrapper.findAll('button').find(item => item.text().includes('可靠守护者'))!.trigger('click')
    const nameInput = wrapper.findAll('label').find(item => item.text().includes('角色名'))!.find('input')
    await nameInput.setValue('阿岚')
    await buttonByText(wrapper, '完成并使用这个角色').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('操作太频繁')
    expect(mocks.finalize).not.toHaveBeenCalled()
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('exposes quick, guided, and expert modes without loading arbitrary components', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()

    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.map(tab => tab.text())).toEqual(['快速创建', '引导创建', '专家创建'])
    await tabs[2].trigger('click')
    // 模式切换后工作区指向对应模式面板
    expect(wrapper.get('[role="tabpanel"]').attributes('aria-labelledby')).toBe('builder-mode-expert')
  })

  it('explains standard alignment abbreviations and localizes common builder enums', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()
    // 预设卡片展示数据自带的简介与推荐理由
    const preset = wrapper.findAll('button').find(item => item.text().includes('可靠守护者'))!
    expect(preset.text()).toContain('直观而坚韧。')
    expect(preset.text()).toContain('适合第一次进入战斗。')

    await reachGuidedStep(wrapper, 1)
    const alignment = wrapper.findAll('label').find(item => item.text().includes('阵营（'))!
    expect(alignment.findAll('option').map(item => item.text())).toEqual([
      'LG · 守序善良', 'NG · 中立善良', 'CG · 混乱善良',
      'LN · 守序中立', 'N · 绝对中立', 'CN · 混乱中立',
    ])
    expect(alignment.text()).toContain('缩写与常见 D&D 资料一致')

    await buttonByText(wrapper, '下一步').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[role="tabpanel"]').text()).toContain('标准数组')
  })

  it('exposes keyboard-operable builder tabs with an explicit active mode', async () => {
    const wrapper = mount(Dnd2024CharacterBuilder, {
      attachTo: document.body,
      global: { plugins: [createPinia()] },
      props: {
        ruleId: 'dnd2024_srd', language: 'zh-CN',
        experience: {
          profile: 'dnd2024', builder_mode: 'professional',
          modes: ['quick', 'guided', 'expert'], content_version: 'srd-5.2.1+r4', locale: 'zh-CN',
        },
      },
    })
    await flushPromises()

    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    ;(tabs[0].element as HTMLButtonElement).focus()
    await tabs[0].trigger('keydown', { key: 'ArrowRight' })
    await wrapper.vm.$nextTick()

    expect(tabs[1].attributes('aria-selected')).toBe('true')
    expect(tabs[1].element).toBe(document.activeElement)
    expect(wrapper.get('[role="tabpanel"]').attributes('aria-labelledby')).toBe('builder-mode-guided')
    wrapper.unmount()
  })

  it('disables conflicting and excess proficiency choices at their exact limits', async () => {
    mocks.choices.mockResolvedValue({
      ok: true,
      rule_id: 'dnd2024_srd',
      choices: {
        ...choices,
        quick_presets: [{
          ...choices.quick_presets[0],
          draft: { ...legalDraft, class_skill_refs: [], species_skill_refs: [] },
        }],
        class_skills: [
          { ref: 'skill:perception', id: 'perception', name: '察觉' },
          { ref: 'skill:survival', id: 'survival', name: '求生' },
        ],
        class_skill_count: 1,
        species_skills: [
          { ref: 'skill:perception', id: 'perception', name: '察觉' },
          { ref: 'skill:acrobatics', id: 'acrobatics', name: '杂技' },
        ],
        species_skill_count: 1,
      },
    })
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachGuidedStep(wrapper, 3)

    const fieldsets = wrapper.findAll('fieldset')
    const classSkills = fieldsets.find(item => item.text().includes('职业技能'))!
    const speciesSkills = fieldsets.find(item => item.text().includes('物种技能'))!
    const classInputs = classSkills.findAll('input')
    await classInputs[0].setValue(true)

    expect((classInputs[0].element as HTMLInputElement).disabled).toBe(false)
    expect((classInputs[1].element as HTMLInputElement).disabled).toBe(true)
    expect((speciesSkills.findAll('input')[0].element as HTMLInputElement).disabled).toBe(true)
    expect((speciesSkills.findAll('input')[1].element as HTMLInputElement).disabled).toBe(false)

    await speciesSkills.findAll('input')[1].setValue(true)
    expect(speciesSkills.text()).toContain('1/1')
    expect((speciesSkills.findAll('input')[1].element as HTMLInputElement).disabled).toBe(false)
  })

  it('keeps wizard prepared spells inside the selected spellbook', async () => {
    mocks.choices.mockResolvedValue({
      ok: true,
      rule_id: 'dnd2024_srd',
      choices: {
        ...choices,
        class_spells: {
          requirements: {
            class_ref: 'class:wizard', level: 1, cantrip_count: 1,
            prepared_spell_count: 1, spellbook_minimum: 2, maximum_spell_level: 1,
            slot_profile: 'full', spell_slots: { 1: 2 },
          },
          cantrips: [{
            ref: 'spell:light', id: 'light', name: 'Light', level: 0,
            school: 'evocation', class_refs: ['class:wizard'], casting_time: 'Action',
            range: 'Touch', components: ['V'], ritual: false, concentration: false,
            duration: '1 hour', source_ref: 'srd-5.2.1:p200:light',
          }],
          leveled_spells: [
            { ref: 'spell:sleep', id: 'sleep', name: 'Sleep', level: 1, school: 'enchantment', class_refs: ['class:wizard'], casting_time: 'Action', range: '60 feet', components: ['V'], ritual: false, concentration: true, duration: '1 minute', source_ref: 'srd-5.2.1:p300:sleep' },
            { ref: 'spell:shield', id: 'shield', name: 'Shield', level: 1, school: 'abjuration', class_refs: ['class:wizard'], casting_time: 'Reaction', range: 'Self', components: ['V'], ritual: false, concentration: false, duration: '1 round', source_ref: 'srd-5.2.1:p299:shield' },
          ],
        },
        recommended_class_spells: {
          cantrip_ids: ['light'], spellbook_ids: ['sleep', 'shield'], prepared_spell_ids: ['sleep'],
        },
      },
    })
    const wrapper = mount(Dnd2024CharacterBuilder, {
      global: { plugins: [createPinia()] },
      props: {
        ruleId: 'dnd2024_srd', language: 'en',
        initial: {
          character_name: 'Arden', ruleset_character: {
            locale: 'en',
            identity: { name: 'Arden', species_ref: 'species:human', background_ref: 'background:sage' },
            build: {
              class_levels: [{ class_ref: 'class:wizard', level: 1 }],
              base_abilities: { str: 8, dex: 14, con: 13, int: 15, wis: 12, cha: 10 },
              class_spell_choices: {
                cantrip_refs: ['spell:light'], spellbook_refs: ['spell:sleep', 'spell:shield'],
                prepared_spell_refs: ['spell:sleep'],
              },
            },
          },
        },
        experience: {
          profile: 'dnd2024', builder_mode: 'professional',
          modes: ['quick', 'guided', 'expert'], content_version: 'srd-5.2.1+r2', locale: 'en',
        },
      },
    })
    await flushPromises()
    // 通过真实导航走到第 3 步（initial 已提供完整草稿，服务端校验可通过）
    await buttonByText(wrapper, 'Next').trigger('click')
    await wrapper.vm.$nextTick()
    await buttonByText(wrapper, 'Next').trigger('click')
    await wrapper.vm.$nextTick()

    // 按标题文本定位法术分组，不依赖样式类名
    const groupInputs = (title: string) => {
      const header = wrapper.findAll('b').find(item => item.text().includes(title))!
      const container = header.element.parentElement as HTMLElement
      return Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
    }
    const spellbook = groupInputs('Spellbook')
    await new DOMWrapper(spellbook[0]).setValue(false)
    await wrapper.vm.$nextTick()

    const prepared = groupInputs('Prepared spells')
    expect(prepared[0].disabled).toBe(true)
    expect(prepared[0].checked).toBe(false)
  })
})

describe('D&D 2024 ability page: point buy vs background bonuses', () => {
  // Sorcerer（主属性 CHA）+ Sage 类背景（只允许 CON / INT / WIS）的典型场景
  const sorcererPointBuyDraft = {
    ...legalDraft,
    ability_method: 'point_buy',
    base_abilities: { str: 10, dex: 13, con: 14, int: 8, wis: 12, cha: 13 },
    background_ability_bonuses: {},
  }
  const sorcererChoices = {
    ...choices,
    ability_methods: [
      { id: 'standard_array', values: [15, 14, 13, 12, 10, 8] },
      { id: 'point_buy' },
      { id: 'rolled' },
    ],
    recommended_base_abilities: { str: 10, dex: 13, con: 14, int: 8, wis: 12, cha: 15 },
    background_ability_refs: ['ability:con', 'ability:int', 'ability:wis'],
    quick_presets: [{ ...choices.quick_presets[0], draft: sorcererPointBuyDraft }],
  }

  beforeEach(() => {
    localStorage.clear()
    mocks.choices.mockReset().mockResolvedValue({ ok: true, rule_id: 'dnd2024_srd', choices: sorcererChoices })
    mocks.validate.mockReset().mockResolvedValue({ ok: true, valid: true, errors: [] })
    mocks.derive.mockReset().mockResolvedValue({ ok: true, character: {} })
    mocks.finalize.mockReset().mockResolvedValue({
      ok: true, rule_id: 'dnd2024_srd', character: {
        character_name: '阿岚', rule_id: 'dnd2024_srd', ruleset_character: { rule_binding: {} },
      },
    })
  })

  async function reachAbilityStep(wrapper: VueWrapper, { nameLabel = '角色名', nextLabel = '下一步', tabLabel = '引导创建' } = {}) {
    await wrapper.findAll('button').find(item => item.text().includes('可靠守护者'))!.trigger('click')
    const nameInput = wrapper.findAll('label').find(item => item.text().includes(nameLabel))!.find('input')
    await nameInput.setValue('阿岚')
    await wrapper.findAll('[role="tab"]').find(item => item.text() === tabLabel)!.trigger('click')
    await buttonByText(wrapper, nextLabel).trigger('click')
    await wrapper.vm.$nextTick()
  }

  it('Test A: point buy always shows all six abilities (CHA not hidden by Sage)', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachAbilityStep(wrapper)

    const panel = wrapper.get('[role="tabpanel"]')
    for (const name of ['力量', '敏捷', '体质', '智力', '感知', '魅力']) {
      expect(panel.text()).toContain(name)
    }
    expect(panel.text()).toContain('基础属性分配')
    expect(panel.text()).toContain('购点已使用 23 / 27（剩余 4）')
  })

  it('Test B: background bonus scope stays CON / INT / WIS', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachAbilityStep(wrapper)

    const bonusBlock = wrapper.findAll('fieldset').find(item => item.text().includes('背景属性提升'))!
    expect(bonusBlock.text()).toContain('当前背景可提升：体质 / 智力 / 感知')
    for (const select of bonusBlock.findAll('select')) {
      expect(select.text()).not.toContain('魅力')
    }
  })

  it('Test C: warns when the background cannot raise the class primary ability', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachAbilityStep(wrapper)

    const panel = wrapper.get('[role="tabpanel"]')
    expect(panel.text()).toContain('当前背景无法提升职业推荐主属性「魅力」')
    expect(panel.text()).toContain('这是合法组合')
    expect(panel.text()).toContain('基础属性分配中提高该属性')
  })

  it('Test D: no warning when the background includes the primary ability', async () => {
    mocks.choices.mockResolvedValue({
      ok: true,
      rule_id: 'dnd2024_srd',
      choices: {
        ...sorcererChoices,
        background_ability_refs: ['ability:cha', 'ability:con', 'ability:dex'],
      },
    })
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachAbilityStep(wrapper)

    expect(wrapper.get('[role="tabpanel"]').text()).not.toContain('当前背景无法提升职业推荐主属性')
  })

  it('Test E: recommended +2/+1 never leaves the background-legal abilities', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachAbilityStep(wrapper)

    await buttonByText(wrapper, '在背景允许属性中推荐 +2/+1').trigger('click')
    await wrapper.vm.$nextTick()

    const label = (name: string) =>
      wrapper.findAll('.ability-grid label').find(item => item.text().includes(name))!
    expect(label('体质').text()).toContain('+ 2 = 16')
    expect(label('感知').text()).toContain('+ 1 = 13')
    expect(label('智力').text()).toContain('+ 0 = 8')
    expect(label('魅力').text()).toContain('+ 0 = 13')
  })

  it('Test F: point buy can still raise CHA and updates the budget', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachAbilityStep(wrapper)

    const chaLabel = wrapper.findAll('.ability-grid label').find(item => item.text().includes('魅力'))!
    await chaLabel.find('input').setValue(15)

    const panel = wrapper.get('[role="tabpanel"]')
    expect(panel.text()).toContain('购点已使用 27 / 27（剩余 0）')
    expect(chaLabel.text()).toContain('= 15')
  })

  it('hides the class-recommendation shortcut when scores are rolled', async () => {
    const wrapper = mountBuilder({})
    await flushPromises()
    await reachAbilityStep(wrapper)

    // point buy 下快捷按钮可见
    expect(wrapper.findAll('button').some(item => item.text().includes('使用职业推荐'))).toBe(true)

    // 切到掷骰生成后按钮必须消失：不允许把职业推荐数组伪装成掷骰结果
    const rolled = wrapper.findAll('.ability-methods label').find(item => item.text().includes('掷骰生成'))!
    await rolled.find('input').setValue(true)
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('button').some(item => item.text().includes('使用职业推荐'))).toBe(false)
  })

  it('falls back to English (never Chinese) when language is de', async () => {
    const wrapper = mountBuilder({ language: 'de' })
    await flushPromises()
    await reachAbilityStep(wrapper, { nameLabel: 'Character name', nextLabel: 'Next', tabLabel: 'Guided' })

    const panel = wrapper.get('[role="tabpanel"]').text()
    expect(panel).toContain('Base ability scores')
    expect(panel).toContain('Background ability bonuses')
    expect(panel).toContain('Class recommendation: prioritize Charisma')
    expect(panel).not.toContain('基础属性分配')
    expect(panel).not.toContain('背景属性提升')
    expect(panel).not.toContain('职业推荐')
  })
})
