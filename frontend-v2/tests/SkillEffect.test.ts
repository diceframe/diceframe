import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import CharacterPanel from '../src/components/CharacterPanel.vue'
import SkillEditor from '../src/components/admin/SkillEditor.vue'
import type { CharacterSkill } from '../src/api/types'

describe('SkillEditor effect editing', () => {
  function mountEditor(skills: CharacterSkill[]) {
    i18n.global.locale.value = 'zh-CN'
    return mount(SkillEditor, {
      global: { plugins: [i18n] },
      props: { modelValue: skills, meta: { max_skills: 6 } },
    })
  }

  it('keeps the effect textarea collapsed until 效果 is toggled', async () => {
    const wrapper = mountEditor([{ name: '火焰球', value: 80 }])
    expect(wrapper.find('.skill-effect-input').exists()).toBe(false)

    const toggle = wrapper.get('.skill-effect-toggle')
    expect(toggle.text()).toBe('效果')
    await toggle.trigger('click')

    const textarea = wrapper.get('.skill-effect-input')
    expect(textarea.attributes('maxlength')).toBe('500')
  })

  it('writes the effect back through v-model without touching name/value', async () => {
    const wrapper = mountEditor([{ name: '火焰球', value: 80 }])
    await wrapper.get('.skill-effect-toggle').trigger('click')
    await wrapper.get('.skill-effect-input').setValue('向目标发射火球，造成火焰伤害。')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CharacterSkill[]
    expect(emitted?.[0]).toEqual({
      name: '火焰球',
      value: 80,
      effect: '向目标发射火球，造成火焰伤害。',
    })
  })

  it('drops the effect field when the text is cleared', async () => {
    const wrapper = mountEditor([{ name: '火焰球', value: 80, effect: '旧说明' }])
    await wrapper.get('.skill-effect-toggle').trigger('click')
    await wrapper.get('.skill-effect-input').setValue('')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CharacterSkill[]
    expect(emitted?.[0]).toEqual({ name: '火焰球', value: 80 })
    expect('effect' in (emitted?.[0] || {})).toBe(false)
  })

  it('pre-opens rows that already carry an effect', () => {
    const wrapper = mountEditor([{ name: '急救', value: 85, effect: '处理伤口。' }])
    expect(wrapper.get('.skill-effect-toggle').classes()).toContain('active')
  })
})

describe('CharacterPanel effect display', () => {
  it('renders the effect as 效果：… instead of a generic key: value line', async () => {
    i18n.global.locale.value = 'zh-CN'
    const player = {
      user_id: 'player-1',
      character_name: '艾琳',
      character_sheet: {
        hp: 10,
        max_hp: 10,
        attributes: {},
        skills: [{ name: '火焰球', value: 80, effect: '向目标发射火球，造成火焰伤害。' }],
      },
    }
    const wrapper = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: { player, ruleMeta: { rule_id: 'freeform_fantasy' } },
    })
    const tooltip = wrapper.get('.character-detail-block .chips span').attributes('title') || ''
    expect(tooltip).toContain('效果：向目标发射火球，造成火焰伤害。')
    expect(tooltip).not.toContain('effect:')
  })

  it('keeps effect-less skills unchanged', () => {
    i18n.global.locale.value = 'zh-CN'
    const player = {
      user_id: 'player-1',
      character_name: '艾琳',
      character_sheet: {
        hp: 10,
        max_hp: 10,
        attributes: {},
        skills: [{ name: '侦查', value: 80 }],
      },
    }
    const wrapper = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: { player, ruleMeta: { rule_id: 'freeform_fantasy' } },
    })
    expect(wrapper.get('.character-detail-block .chips span').attributes('title')).toBeUndefined()
  })
})
