import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import CharacterPanel from '../src/components/CharacterPanel.vue'

describe('CharacterPanel portrait editing', () => {
  const player = {
    user_id: 'player-1',
    character_name: '艾琳',
    character_sheet: {
      portrait: { kind: 'builtin' as const, id: 'freeform_fantasy:1' },
      hp: 10,
      max_hp: 10,
      attributes: {},
      skills: [],
    },
  }

  it('emits only when the current player may edit the portrait', async () => {
    i18n.global.locale.value = 'zh-CN'
    const editable = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: { player, ruleMeta: { rule_id: 'freeform_fantasy' }, portraitEditable: true },
    })
    const editButton = editable.get('button[title]')
    expect(editButton.attributes('title')).toContain('头像')
    await editButton.trigger('click')
    expect(editable.emitted('portrait-click')).toHaveLength(1)

    const readonly = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: { player, ruleMeta: { rule_id: 'freeform_fantasy' }, portraitEditable: false },
    })
    expect(readonly.find('button[title]').exists()).toBe(false)
  })

  it('puts the same fixed-size decorative icon on the skills and equipment rows', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CharacterPanel, { global: { plugins: [i18n] }, props: { player, ruleMeta: { rule_id: 'freeform_fantasy' } } })
    const rows = wrapper.findAll('.character-detail-block > summary')
    expect(rows.map(row => row.text())).toEqual(['技能', '装备与背包'])
    for (const row of rows) {
      const icon = row.get('svg.sidebar-row-icon')
      expect([icon.attributes('width'), icon.attributes('height'), icon.attributes('stroke-width'), icon.attributes('aria-hidden')]).toEqual(['16', '16', '1.5', 'true'])
    }
    wrapper.unmount()
  })

  it('does not invent an HP bar when HP is absent and keeps other real projections', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: {
        player: {
          user_id: 'player-no-hp',
          character_name: '无血条角色',
          character_sheet: {
            attributes: { insight: 4 },
            skills: ['调查'],
            equipment: [{ name: '旧钥匙' }],
          },
        },
        ruleMeta: { rule_id: 'freeform_story' },
      },
    })

    expect(wrapper.find('.character-vitals .hp').exists()).toBe(false)
    expect(wrapper.find('.character-vitals .hpbar').exists()).toBe(false)
    expect(wrapper.find('.character-attributes').text()).toContain('insight 4')
    expect(wrapper.text()).toContain('调查')
    expect(wrapper.text()).toContain('旧钥匙')
  })

  it('shows a resource without a maximum as a value, without a fake 0 maximum bar', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: {
        player: {
          user_id: 'player-resource',
          character_name: '资源角色',
          character_sheet: { resources: { focus: { current: 3 } } },
        },
        ruleMeta: { rule_id: 'freeform_story', resource_schema: [{ key: 'focus', label: '专注' }] },
      },
    })

    const vitals = wrapper.find('.character-vitals')
    expect(vitals.text()).toContain('专注')
    expect(vitals.text()).toContain('3')
    expect(vitals.text()).not.toContain('3 / 0')
    expect(vitals.find('.hpbar').exists()).toBe(false)
  })

  it('shows the supported damage formula and its fixed fallback in equipment details', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: {
        player: {
          user_id: 'player-dice',
          character_name: '骰式角色',
          character_sheet: {
            attributes: {}, skills: [],
            equipment: [{ name: '长剑', type: 'weapon', damage: 7, damage_dice: '2d6+1', slot: 'main_hand' }],
          },
        },
        ruleMeta: { rule_id: 'dnd5e', mechanics: 'dnd5e_core' },
      },
    })
    await wrapper.get('.item-view-all').trigger('click')
    const details = document.body.querySelector('.item-detail-meta')
    expect(details?.textContent).toContain('2d6+1')
    expect(details?.textContent).not.toContain('(7)')
  })

  it('keeps the fixed damage label for non-dice equipment', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(CharacterPanel, {
      global: { plugins: [i18n] },
      props: { player: { user_id: 'fixed', character_name: '固定值', character_sheet: { attributes: {}, skills: [], equipment: [{ name: '锤', damage: 7 }] } }, ruleMeta: { rule_id: 'freeform_fantasy' } },
    })
    await wrapper.get('.item-view-all').trigger('click')
    const details = Array.from(document.body.querySelectorAll('.item-detail-meta')).at(-1)
    expect(details?.textContent).toContain('伤害 7')
    wrapper.unmount()
  })
})
