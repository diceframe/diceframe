import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))

vi.mock('../src/api/client', () => ({
  api: mocks.api,
  errorMessage: (cause: unknown) => String((cause as Error)?.message || cause),
}))

import ProfessionalCharacterCenter from '../src/features/rulesets/ProfessionalCharacterCenter.vue'

const character = {
  character_name: 'Arden',
  portrait: {},
  hp: 12,
  max_hp: 12,
  attributes: { str: 16, dex: 12, con: 14, int: 10, wis: 13, cha: 8 },
  ruleset_character: {
    rule_binding: { runtime_id: 'core:dnd2024', content_version: 'srd-test' },
    identity: {
      name: 'Arden', species_ref: 'species:human', background_ref: 'background:guard',
      alignment: 'lawful_good', size: 'medium',
    },
    build: {
      level: 1,
      class_levels: [{ class_ref: 'class:fighter', level: 1 }],
    },
    abilities: { str: 16, dex: 12, con: 14, int: 10, wis: 13, cha: 8 },
    resources: { hp: 12, max_hp: 12, hit_dice: { d10: 1 } },
    derived: { armor_class: 18, proficiency_bonus: 2 },
    proficiencies: { skill_refs: ['skill:athletics'] },
    progression: { mode: 'single_class' },
  },
}

describe('ProfessionalCharacterCenter', () => {
  beforeEach(() => {
    mocks.api.mockReset().mockResolvedValue({
      ok: true,
      card: { ...character, character_name: 'Arden Vale' },
    })
  })

  it('opens a direct rest entry without submitting or changing the character', async () => {
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: { character, target: 'game', gameKey: 'web|room|bot', userId: 'player/1', ruleId: 'dnd2024_srd', language: 'zh-CN', initialFocus: 'rest' },
      global: { stubs: { PortraitPicker: true } },
    })
    await flushPromises()
    expect(wrapper.get('.center-tabs button:nth-child(4)').classes()).toContain('active')
    expect(wrapper.find('.rest-center').exists()).toBe(true)
    expect(mocks.api).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('only submits profile fields', async () => {
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character,
        target: 'card',
        cardId: 'card-1',
        ruleId: 'dnd2024_srd',
        language: 'zh-CN',
      },
      global: {
        stubs: {
          PortraitPicker: {
            props: ['modelValue'],
            template: '<div data-test="portrait-picker" />',
          },
        },
      },
    })

    expect(wrapper.text()).not.toContain('第一次玩，先记住三件事')
    await wrapper.get('.center-tabs button:nth-child(2)').trigger('click')
    await wrapper.get('input[required]').setValue('Arden Vale')
    const textareas = wrapper.findAll('textarea')
    await textareas[2].setValue('Left home to protect a frontier town.')
    await wrapper.get('footer .primary').trigger('click')
    await flushPromises()

    expect(mocks.api).toHaveBeenCalledOnce()
    const [path, request] = mocks.api.mock.calls[0]
    expect(path).toBe('/character-cards/card-1/profile')
    const body = JSON.parse(request.body)
    expect(body.character_name).toBe('Arden Vale')
    expect(body.profile.backstory).toContain('frontier town')
    expect(body.portrait).toBeNull()
    expect(body).not.toHaveProperty('hp')
    expect(body).not.toHaveProperty('attributes')
    expect(body).not.toHaveProperty('ruleset_character')
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('submits live rest intent with hit-die counts, confirmation, and revision', async () => {
    mocks.api.mockResolvedValueOnce({
      ok: true,
      character: { ...character, hp: 12, ruleset_revision: 4 },
      rest: 'short',
      events: [],
      revision: 4,
    })
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game',
        gameKey: 'web|room|bot',
        userId: 'player/1',
        ruleId: 'dnd2024_srd',
        language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    expect(wrapper.text()).toContain('生命骰')
    expect(wrapper.text()).toContain('1 / 1')
    const restOptions = wrapper.findAll('.rest-type-option')
    expect(restOptions).toHaveLength(2)
    expect(restOptions[0].classes()).toContain('selected')
    expect(restOptions[0].get('input').attributes('type')).toBe('radio')
    expect(restOptions[1].get('input').attributes('type')).toBe('radio')
    await wrapper.get('.hit-dice-grid input').setValue(1)
    await wrapper.get('.rest-confirm input').setValue(true)
    await wrapper.get('.rest-center > button').trigger('click')
    await flushPromises()

    const [path, request] = mocks.api.mock.calls[0]
    expect(path).toBe('/games/web%7Croom%7Cbot/character/player%2F1/rest')
    const body = JSON.parse(request.body)
    expect(body).toMatchObject({
      rest: 'short', hit_dice: { d10: 1 }, confirm_elapsed_time: true,
      expected_revision: 3,
    })
    expect(body.operation_id).toEqual(expect.any(String))
    expect(body).not.toHaveProperty('hit_die_rolls')
    expect(wrapper.emitted('saved')?.[0]?.[1]).toBe('rest')
  })

  it('shows party rest readiness and keeps the dialog open while waiting', async () => {
    mocks.api.mockResolvedValueOnce({
      ok: true,
      pending: true,
      resolved: false,
      rest: 'short',
      rest_session: {
        active: true, status: 'collecting', rest: 'short', ready_count: 1, active_count: 2,
        participants: [
          { user_id: 'player/1', character_name: 'Arden', status: 'submitted' },
          { user_id: 'player/2', character_name: 'Mira', status: 'waiting' },
        ],
      },
    })
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
        restSession: {
          active: true, status: 'collecting', rest: 'short', ready_count: 0, active_count: 2,
          participants: [
            { user_id: 'player/1', character_name: 'Arden', status: 'waiting' },
            { user_id: 'player/2', character_name: 'Mira', status: 'waiting' },
          ],
        },
      },
      global: { stubs: { PortraitPicker: true } },
    })

    expect(wrapper.get('.center-tabs button:nth-child(4)').classes()).toContain('active')
    expect(wrapper.text()).toContain('队伍短休准备：0/2')
    expect(wrapper.text()).toContain('Mira · 等待')
    await wrapper.get('.rest-confirm input').setValue(true)
    await wrapper.get('.rest-center > button').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('rest-pending')).toHaveLength(1)
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('keeps long profile text readable and renders spell slots as level cards', async () => {
    const detailedCharacter = {
      ...character,
      ruleset_character: {
        ...character.ruleset_character,
        profile: {
          ideals: '保护每一个在边境线上努力生活的人，不让任何人再次独自面对战争。',
        },
        spellcasting: {
          class: {
            ability: 'int',
            slots_current: { '1': 2, '2': 1 },
            slots_max: { '1': 4, '2': 2 },
            cantrip_refs: ['spell:light'],
            prepared_spell_refs: ['spell:shield'],
          },
        },
      },
    }
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: detailedCharacter,
        target: 'card',
        cardId: 'card-1',
        ruleId: 'dnd2024_srd',
        language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(2)').trigger('click')
    expect((wrapper.findAll('textarea')[3].element as HTMLTextAreaElement).value).toContain('保护每一个')
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    expect(wrapper.findAll('.spell-slot-card')).toHaveLength(3)
    expect(wrapper.text()).toContain('1 环')
    expect(wrapper.text()).toContain('2 / 4')
    expect(wrapper.text()).not.toContain('slots_current')
  })

  it('lists origin feat cantrips and spell even without class spellcasting', async () => {
    const featCharacter = {
      ...character,
      ruleset_character: {
        ...character.ruleset_character,
        spellcasting: {
          magic_initiate: [{
            feat_ref: 'feat:magic_initiate_wizard', spellcasting_ability: 'wis',
            cantrip_ids: ['fire_bolt', 'chill_touch'], level_1_spell_id: 'shield', free_cast_used: true,
          }],
        },
      },
    }
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: { character: featCharacter, target: 'card', cardId: 'card-1', ruleId: 'dnd2024_srd', language: 'zh-CN' },
      global: { stubs: { PortraitPicker: true } },
    })
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    expect(wrapper.text()).toContain('起源专长法术')
    expect(wrapper.text()).toContain('fire bolt')
    expect(wrapper.text()).toContain('chill touch')
    expect(wrapper.text()).toContain('shield')
    expect(wrapper.text()).toContain('免费施放已用')
  })

  it('keeps the action footer outside the only scrolling content region', async () => {
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character,
        target: 'card',
        cardId: 'card-1',
        ruleId: 'dnd2024_srd',
        language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(2)').trigger('click')
    const root = wrapper.get('.professional-character-center')
    const scrollRegion = wrapper.get('.center-scroll-region')
    const footer = wrapper.get('footer')

    expect(scrollRegion.find('.profile-panel').exists()).toBe(true)
    expect(scrollRegion.find('footer').exists()).toBe(false)
    expect(footer.element.parentElement).toBe(root.element)
  })

  it('renders the server-projected class features and class resources for a monk', async () => {
    // 角色页只渲染服务端投影：能力名、武艺骰、攻击属性、资源 current/max
    // 都来自 class_features / class_resources，前端不判断职业也不做任何计算。
    const monkCharacter = {
      ...character,
      class_features: [
        {
          id: 'martial_arts', name: '武艺', summary: '徒手打击可使用力量或敏捷。',
          minimum_level: 1,
          values: { unarmed_damage_die: '1d6', unarmed_ability_choice: ['str', 'dex'] },
        },
        { id: 'monks_focus', name: '武僧专注', summary: '获得专注点。' },
        { id: 'flurry_of_blows', name: '疾风连击', summary: '消耗 1 点专注进行两次徒手打击。' },
        { id: 'patient_defense', name: '坚守防御', summary: '' },
        { id: 'step_of_the_wind', name: '疾风步', summary: '' },
      ],
      class_resources: [
        { id: 'focus_points', name: '专注点', current: 2, maximum: 2 },
      ],
    }
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: monkCharacter,
        target: 'card',
        cardId: 'card-1',
        ruleId: 'dnd2024_srd',
        language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    expect(wrapper.get('.class-feature-section').text()).toContain('武艺')
    expect(wrapper.get('.class-feature-section').text()).toContain('疾风连击')
    expect(wrapper.get('.class-feature-section').text()).toContain('坚守防御')
    expect(wrapper.get('.class-feature-section').text()).toContain('疾风步')
    expect(wrapper.get('.class-feature-section').text()).toContain('武艺骰 1d6')
    expect(wrapper.get('.class-feature-section').text()).toContain('攻击属性')
    expect(wrapper.findAll('.class-feature-card')).toHaveLength(5)

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    const panel = wrapper.get('.magic-panel')
    expect(panel.text()).toContain('职业资源')
    expect(panel.text()).toContain('专注点')
    expect(wrapper.get('.class-resource-section').text()).toContain('2 / 2')
  })

  it('shows no class feature or class resource section without a server projection', async () => {
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character,
        target: 'card',
        cardId: 'card-1',
        ruleId: 'dnd2024_srd',
        language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    expect(wrapper.find('.class-feature-section').exists()).toBe(false)
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    expect(wrapper.find('.class-resource-section').exists()).toBe(false)
    // 只有服务端投影的生命骰卡片：不会伪造 0/0 的空职业资源卡。
    expect(wrapper.findAll('.spell-slot-card')).toHaveLength(1)
    expect(wrapper.get('.magic-panel').text()).not.toContain('专注点')
  })

  const longRestOptions = {
    ok: true,
    rest_options: {
      land_terrain: {
        owner: 'subclass:circle_of_the_land',
        feature_id: 'circle_of_the_land_spells',
        current: 'polar',
        options: ['arid', 'polar', 'temperate', 'tropical'],
        required: true,
      },
      free_cast: [{
        slot_id: 'spell_mastery_1',
        feature_id: 'spell_mastery',
        spell_level: 1,
        recharge: 'unlimited',
        spell_ref: 'spell:magic_missile',
        spell_name: 'Magic Missile',
        rest_replace: 1,
        valid: true,
        options: [
          { ref: 'spell:magic_missile', name: 'Magic Missile', level: 1 },
          { ref: 'spell:burning_hands', name: 'Burning Hands', level: 1 },
        ],
      }],
    },
  }

  function mountLongRest() {
    mocks.api.mockImplementation((path: string) => {
      if (String(path).endsWith('/rest/options')) return Promise.resolve(longRestOptions)
      return Promise.resolve({
        ok: true, character: { ...character, ruleset_revision: 4 }, events: [],
      })
    })
    return mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })
  }

  it('asks for the long-rest choices only once a long rest is selected', async () => {
    const wrapper = mountLongRest()
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    // 短休没有任何声明：打开面板不发请求。
    expect(mocks.api).not.toHaveBeenCalled()
    await wrapper.findAll('.rest-type-option')[1].get('input').setValue(true)
    await flushPromises()
    expect(mocks.api.mock.calls[0][0]).toBe(
      '/games/web%7Croom%7Cbot/character/player%2F1/rest/options',
    )
    expect(wrapper.find('.rest-terrain').exists()).toBe(true)
    expect(wrapper.find('.rest-study').exists()).toBe(true)
    // 地形预填当前值，研究默认"不替换"：不选就什么都不改。
    expect((wrapper.find('.rest-terrain-option.selected').text())).toContain('polar')
    expect(wrapper.findAll('.rest-study-row')).toHaveLength(1)
  })

  it('submits the terrain and the studied spell with a long rest', async () => {
    const wrapper = mountLongRest()
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await wrapper.findAll('.rest-type-option')[1].get('input').setValue(true)
    await flushPromises()

    await wrapper.findAll('.rest-terrain-option input')[0].setValue(true)
    const selects = wrapper.findAll('.rest-study-row select')
    await selects[0].setValue('spell_mastery_1')
    await flushPromises()
    await wrapper.findAll('.rest-study-row select')[1].setValue('spell:burning_hands')
    await wrapper.get('.rest-confirm input').setValue(true)
    await wrapper.get('.rest-center > button').trigger('click')
    await flushPromises()

    const submit = mocks.api.mock.calls.find(([path]) => String(path).endsWith('/character/player%2F1/rest'))
    expect(submit).toBeTruthy()
    const body = JSON.parse(submit![1].body)
    expect(body).toMatchObject({ rest: 'long' })
    // 空值一律不发：只有真的选了地形与研究才出现在请求里。
    expect(body.land_terrain).toBe('arid')
    expect(body.free_cast).toEqual({ spell_mastery_1: 'spell:burning_hands' })
    expect(body.hit_dice).toEqual({})
  })
})
