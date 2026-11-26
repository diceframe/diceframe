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
    // 打开休息页会先读一次选项（短休的恢复预算也算声明），所以默认实现要按路径
    // 分流；用 Once 的用例会被这一次读选项吃掉。
    mocks.api.mockReset().mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve({
            ok: true,
            rest_options: { land_terrain: null, free_cast: [], short_recovery: [] },
          })
        : Promise.resolve({
            ok: true,
            card: { ...character, character_name: 'Arden Vale' },
          })
    ))
  })

  it('opens a direct rest entry without submitting or changing the character', async () => {
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: { character, target: 'game', gameKey: 'web|room|bot', userId: 'player/1', ruleId: 'dnd2024_srd', language: 'zh-CN', initialFocus: 'rest' },
      global: { stubs: { PortraitPicker: true } },
    })
    await flushPromises()
    expect(wrapper.get('.center-tabs button:nth-child(4)').classes()).toContain('active')
    expect(wrapper.find('.rest-center').exists()).toBe(true)
    // 只读选项（休息 + 仪式），绝不提交：打开休息入口不等于休息。
    const calls = mocks.api.mock.calls.map(([path]) => String(path))
    expect(calls).toEqual([
      '/games/web%7Croom%7Cbot/character/player%2F1/rest/options',
      '/games/web%7Croom%7Cbot/character/player%2F1/ritual/options',
    ])
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

  const shortRestOptions = {
    ok: true,
    rest_options: {
      land_terrain: null,
      free_cast: [],
      short_recovery: [{
        resource_id: 'natural_recovery',
        mode: 'spell_slots',
        budget: 3,
        uses_left: 1,
        max_slot_level: 5,
        slots: [
          { level: 1, expended: 2, maximum: 4 },
          { level: 3, expended: 1, maximum: 2 },
        ],
      }],
    },
  }

  it('submits live rest intent with hit-die counts, confirmation, and revision', async () => {
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve(shortRestOptions)
        : Promise.resolve({
            ok: true,
            character: { ...character, hp: 12, ruleset_revision: 4 },
            rest: 'short',
            events: [],
            revision: 4,
          })
    ))
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
    await flushPromises()
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

    const submit = mocks.api.mock.calls.find(
      ([path]) => String(path).endsWith('/character/player%2F1/rest'),
    )
    expect(submit).toBeTruthy()
    const body = JSON.parse(submit![1].body)
    expect(body).toMatchObject({
      rest: 'short', hit_dice: { d10: 1 }, confirm_elapsed_time: true,
      expected_revision: 3,
    })
    expect(body.operation_id).toEqual(expect.any(String))
    expect(body).not.toHaveProperty('hit_die_rolls')
    // 没选恢复就不发这一块：服务端把 `{}` 当"没有计划"静默放行，发它等于
    // 每次短休都塞一个无意义的字段。
    expect(body).not.toHaveProperty('recovery')
    expect(wrapper.emitted('saved')?.[0]?.[1]).toBe('rest')
  })

  it('offers the Short Rest recovery budget and submits only what was chosen', async () => {
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve(shortRestOptions)
        : Promise.resolve({ ok: true, character: { ...character, ruleset_revision: 4 }, events: [] })
    ))
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()

    // 预算与"已用/上限"都来自服务端投影，前端只画。
    const block = wrapper.get('.rest-recovery')
    expect(block.text()).toContain('预算 3 环')
    expect(block.text()).toContain('今日剩余 1 次')
    const fields = block.findAll('.rest-recovery-field input')
    expect(fields).toHaveLength(2)

    // 3 环那格买回 1 个 = 刚好用完预算；再想多买会被挡住。
    await fields[1].setValue(1)
    await wrapper.get('.rest-confirm input').setValue(true)
    await wrapper.get('.rest-center > button').trigger('click')
    await flushPromises()

    const submit = mocks.api.mock.calls.find(
      ([path]) => String(path).endsWith('/character/player%2F1/rest'),
    )
    expect(JSON.parse(submit![1].body).recovery).toEqual({
      natural_recovery: { slots: { '3': 1 } },
    })
  })

  it('blocks a Short Rest that would exceed the recovery budget', async () => {
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve(shortRestOptions)
        : Promise.resolve({ ok: true, character, events: [] })
    ))
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()
    const fields = wrapper.get('.rest-recovery').findAll('.rest-recovery-field input')
    // 3 环 1 个已经用满 3 点预算，再买 1 个 1 环就超了。
    await fields[1].setValue(1)
    await fields[0].setValue(1)
    await flushPromises()

    expect(wrapper.get('.rest-recovery').text()).toContain('超出预算')
    await wrapper.get('.rest-confirm input').setValue(true)
    expect(wrapper.get('.rest-center > button').attributes('disabled')).toBeDefined()
  })

  it('blocks buying back more slots than a level has expended', async () => {
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve(shortRestOptions)
        : Promise.resolve({ ok: true, character, events: [] })
    ))
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()
    const fields = wrapper.get('.rest-recovery').findAll('.rest-recovery-field input')
    // 1 环只消耗了 2 个，买回 3 个仍在 3 环预算内——超的是每级上限，
    // 不是预算。服务端会拒（cannot recover more level 1 slots than are expended），
    // 但玩家不该先点得动再吃一个英文报错。
    await fields[0].setValue(3)
    await flushPromises()

    expect(wrapper.get('.rest-recovery').text()).toContain('比已消耗还多 1 个')
    await wrapper.get('.rest-confirm input').setValue(true)
    expect(wrapper.get('.rest-center > button').attributes('disabled')).toBeDefined()
  })

  it('caps a points pool at what was actually spent', async () => {
    // 点数池与法术位是两条分支：上限不是"预算"一个数，而是 min(已消耗, 预算)。
    // 这张卡花了 3 点，预算只有 2 点，所以最多回 2 点。
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve({
            ok: true,
            rest_options: {
              land_terrain: null,
              free_cast: [],
              short_recovery: [{
                resource_id: 'sorcerous_restoration',
                mode: 'points',
                budget: 2,
                uses_left: 1,
                track: 'sorcery_points',
                spent: 3,
              }],
            },
          })
        : Promise.resolve({ ok: true, character, events: [] })
    ))
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: {
          ...character,
          ruleset_revision: 3,
          class_resources: [
            { id: 'sorcerous_restoration', name: '术法复苏', current: 1, maximum: 1 },
          ],
        },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()

    const block = wrapper.get('.rest-recovery')
    expect(block.text()).toContain('术法复苏')
    expect(block.text()).toContain('预算 2 点')

    const field = block.get('.rest-recovery-field input')
    // 花掉 3 点也只想回 3 点：预算 2 点挡住了，按钮跟着禁用。
    await field.setValue(3)
    await flushPromises()
    expect(block.text()).toContain('超出预算')
    await wrapper.get('.rest-confirm input').setValue(true)
    expect(wrapper.get('.rest-center > button').attributes('disabled')).toBeDefined()

    await field.setValue(2)
    await flushPromises()
    await wrapper.get('.rest-center > button').trigger('click')
    await flushPromises()

    const submit = mocks.api.mock.calls.find(
      ([path]) => String(path).endsWith('/character/player%2F1/rest'),
    )
    expect(JSON.parse(submit![1].body).recovery).toEqual({
      sorcerous_restoration: { points: 2 },
    })
  })

  it('re-reads the recovery budget when someone else moves the character', async () => {
    // 队伍休息要 DM 批准：批准时角色卡是被别人推进的，面板不能一直画着
    // 休息前的"今日剩余 1 次"。版本号一变就重读。
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve(shortRestOptions)
        : Promise.resolve({ ok: true, character, events: [] })
    ))
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()
    const reads = () => mocks.api.mock.calls
      .filter(([path]) => String(path).endsWith('/rest/options')).length
    expect(reads()).toBe(1)

    // 同一个版本号重复渲染不该再问一次。
    await wrapper.setProps({ character: { ...character, ruleset_revision: 3 } })
    await flushPromises()
    expect(reads()).toBe(1)

    await wrapper.setProps({ character: { ...character, ruleset_revision: 4 } })
    await flushPromises()
    expect(reads()).toBe(2)
  })

  it('tells the player a Short Rest lost its recovery options, but still lets them rest', async () => {
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve({ ok: false, error: '读取休息选项失败' })
        : Promise.resolve({ ok: true, character, events: [] })
    ))
    const wrapper = mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })

    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()

    // 静默降级是错的：玩家会以为"本来就没东西可回"。
    const error = wrapper.get('.rest-options-error')
    expect(error.text()).toContain('不会恢复法术位')
    expect(error.text()).toContain('读取休息选项失败')
    // 但读不到选项不该把人锁在休息外面。
    await wrapper.get('.rest-confirm input').setValue(true)
    expect(wrapper.get('.rest-center > button').attributes('disabled')).toBeUndefined()
  })

  it('shows party rest readiness and keeps the dialog open while waiting', async () => {
    mocks.api.mockImplementation((path: string) => (
      String(path).endsWith('/rest/options')
        ? Promise.resolve({
            ok: true,
            rest_options: { land_terrain: null, free_cast: [], short_recovery: [] },
          })
        : Promise.resolve({
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
    ))
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

  it('asks for the rest choices once the rest page is opened, and only once', async () => {
    const wrapper = mountLongRest()
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()
    const optionsCalls = () => mocks.api.mock.calls
      .map(([path]) => String(path))
      .filter(path => path.endsWith('/rest/options'))
    // 短休现在也有声明（恢复预算），所以打开这一页就要问一次；但只问一次。
    expect(optionsCalls()).toHaveLength(1)
    expect(optionsCalls()[0]).toBe(
      '/games/web%7Croom%7Cbot/character/player%2F1/rest/options',
    )
    await wrapper.findAll('.rest-type-option')[1].get('input').setValue(true)
    await flushPromises()
    expect(optionsCalls()).toHaveLength(1)
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

  const awaitingPartyRest = {
    active: true,
    status: 'awaiting_approval',
    rest: 'long',
    ready_count: 2,
    active_count: 2,
    participants: [
      { user_id: 'player/1', character_name: 'Arden', status: 'submitted' },
      { user_id: 'player/2', character_name: 'Bree', status: 'submitted' },
    ],
    duration_minutes: 480,
  }

  function mountAwaiting(isGm: boolean) {
    mocks.api.mockImplementation(() => Promise.resolve({ ok: true, rest_session: awaitingPartyRest }))
    return mount(ProfessionalCharacterCenter, {
      props: {
        character: { ...character, ruleset_revision: 3 },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
        restSession: awaitingPartyRest, isGm,
      },
      global: { stubs: { PortraitPicker: true } },
    })
  }

  it('tells a player the party is waiting for the GM instead of resolving', async () => {
    const wrapper = mountAwaiting(false)
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()

    expect(wrapper.find('.party-rest-status').text()).toContain('等待 DM 裁决')
    expect(wrapper.get('.rest-center > button').text()).toContain('等待 DM 裁决')
    // 玩家没有裁决权：连按钮都不呈现，而不是呈现一个点了会 403 的按钮。
    expect(wrapper.find('[data-testid="rest-approve"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="rest-reject"]').exists()).toBe(false)
    // 面板仍会按会话把休息类型锁到长休并读一次选项（既有行为），
    // 但绝不能出现裁决请求。
    expect(mocks.api.mock.calls.some(([path]) => String(path).endsWith('/rest/decide'))).toBe(false)
  })

  it('lets the GM approve a waiting rest and reports the elapsed time', async () => {
    const wrapper = mountAwaiting(true)
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()

    expect(wrapper.get('.rest-decision').text()).toContain('8 小时')
    await wrapper.get('[data-testid="rest-approve"]').trigger('click')
    await flushPromises()

    const decide = mocks.api.mock.calls.find(([path]) => String(path).endsWith('/rest/decide'))
    expect(decide).toBeTruthy()
    expect(String(decide![0])).toBe('/games/web%7Croom%7Cbot/rest/decide')
    const body = JSON.parse(decide![1].body)
    expect(body.approve).toBe(true)
    expect(body.operation_id).toBeTruthy()
    expect(wrapper.emitted('rest-pending')).toHaveLength(1)
  })
})

describe('ProfessionalCharacterCenter · timed ritual', () => {
  const ritualOptions = {
    ok: true,
    ritual_options: [{
      resource_id: 'magical_cunning',
      minutes: 1,
      uses_left: 1,
      slot_level: 3,
      maximum: 2,
      expended: 2,
      regain: 1,
      full_budget: false,
    }],
  }

  function mountRitual() {
    mocks.api.mockImplementation((path: string) => {
      const target = String(path)
      if (target.endsWith('/rest/options')) {
        return Promise.resolve({
          ok: true,
          rest_options: { land_terrain: null, free_cast: [], short_recovery: [] },
        })
      }
      if (target.endsWith('/ritual/options')) return Promise.resolve(ritualOptions)
      if (target.endsWith('/ritual')) {
        return Promise.resolve({
          ok: true,
          character: { ...character, ruleset_revision: 5 },
          duration_minutes: 1,
        })
      }
      return Promise.resolve({ ok: true })
    })
    return mount(ProfessionalCharacterCenter, {
      props: {
        // revision 必须有非 0 值：仪式把它原样交给服务端做乐观锁，
        // 传 0 的话"取自 props"与"写死 0"两种实现都会绿。
        character: {
          ...character,
          ruleset_revision: 7,
          class_resources: [
            { id: 'magical_cunning', name: '玄妙巧计', current: 1, maximum: 1 },
          ],
        },
        target: 'game', gameKey: 'web|room|bot', userId: 'player/1',
        ruleId: 'dnd2024_srd', language: 'zh-CN',
      },
      global: { stubs: { PortraitPicker: true } },
    })
  }

  it('draws what the rite would give back, from the server projection', async () => {
    const wrapper = mountRitual()
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()

    const block = wrapper.get('.ritual-center')
    // 显示名取自角色卡上的 class_resources，不是裸资源 id。
    expect(block.text()).toContain('玄妙巧计')
    expect(block.text()).not.toContain('magical_cunning')
    expect(block.text()).toContain('1 分钟')
    expect(block.text()).toContain('恢复 1 个 3 环')
    expect(block.text()).toContain('今日剩余 1 次')
  })

  it('requires the time confirmation before performing the rite', async () => {
    const wrapper = mountRitual()
    await wrapper.get('.center-tabs button:nth-child(4)').trigger('click')
    await flushPromises()

    const button = wrapper.get('.ritual-submit')
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('.ritual-center .rest-confirm input').setValue(true)
    expect(button.attributes('disabled')).toBeUndefined()

    await button.trigger('click')
    await flushPromises()

    const submit = mocks.api.mock.calls.find(([path]) => String(path).endsWith('/ritual'))
    expect(submit).toBeTruthy()
    expect(JSON.parse(submit![1].body)).toMatchObject({
      resource_id: 'magical_cunning',
      confirm_elapsed_time: true,
      expected_revision: 7,
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })
})
