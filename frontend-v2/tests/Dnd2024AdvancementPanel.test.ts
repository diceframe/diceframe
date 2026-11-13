import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  preview: vi.fn(), apply: vi.fn(), livePreview: vi.fn(), liveApply: vi.fn(),
}))

vi.mock('../src/features/rulesets/dnd2024/api', () => ({
  previewRulesetAdvancement: mocks.preview,
  applyRulesetAdvancement: mocks.apply,
  previewCharacterCardAdvancement: vi.fn(),
  applyCharacterCardAdvancement: vi.fn(),
  previewLiveCharacterAdvancement: mocks.livePreview,
  applyLiveCharacterAdvancement: mocks.liveApply,
}))

import Dnd2024AdvancementPanel from '../src/features/rulesets/dnd2024/progression/Dnd2024AdvancementPanel.vue'

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find(candidate => candidate.text() === text)
  if (!button) throw new Error(`Missing button: ${text}`)
  return button
}

const character = {
  character_name: 'Arden',
  ruleset_character: {
    rule_binding: { runtime_id: 'core:dnd2024' },
    build: { level: 1 },
  },
}

describe('D&D 2024 advancement panel', () => {
  beforeEach(() => {
    mocks.preview.mockReset().mockResolvedValue({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: true, errors: [], requirements: [], from_level: 1, to_level: 2,
        class_ref: 'class:fighter', source_ref: 'srd-5.2.1:p42:fighter-features',
        content_version: 'srd-5.2.1+r2',
        diff: {
          proficiency_bonus: { before: 2, after: 2 }, hp: { gain: 8 },
          gained_feature_ids: ['action_surge'], spell_slot_changes: {}, abilities: {},
        },
        snapshot: {},
      },
    })
    mocks.apply.mockReset().mockResolvedValue({
      ok: true, rule_id: 'dnd2024_srd',
      character: { ...character, level: 2 },
    })
    mocks.livePreview.mockReset().mockImplementation(mocks.preview)
    mocks.liveApply.mockReset().mockResolvedValue({
      ok: true, rule_id: 'dnd2024_srd', revision: 4,
      character: { ...character, level: 2, ruleset_revision: 4 },
    })
  })

  it('shows an exact before/after preview and applies only a valid plan', async () => {
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character, language: 'en' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Lv. 1 → Lv. 2')
    expect(wrapper.text()).toContain('action surge')
    expect(wrapper.get('section').attributes('aria-labelledby')).toBe('dnd-advancement-title')
    await buttonByText(wrapper, 'Apply level').trigger('click')
    await flushPromises()

    expect(mocks.apply).toHaveBeenCalledWith(
      'dnd2024_srd', character, { hp_method: 'fixed' }, 'en',
    )
    expect(wrapper.emitted('applied')?.[0]?.[0]).toMatchObject({ level: 2 })
  })

  it('keeps apply disabled while a required subclass is missing', async () => {
    mocks.preview.mockResolvedValueOnce({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: false, errors: ['subclass_ref is required at this level'],
        requirements: [{
          id: 'subclass_ref', kind: 'single', required: true,
          options: [{ value: 'subclass:champion', name: 'Champion' }],
        }],
        from_level: 2, to_level: 3, class_ref: 'class:fighter',
        source_ref: 'srd-5.2.1:p42:fighter-features', content_version: 'srd-5.2.1+r2',
        diff: { proficiency_bonus: { before: 2, after: 2 }, hp: { gain: 8 }, gained_feature_ids: [], spell_slot_changes: {}, abilities: {} }, snapshot: {},
      },
    })
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character, language: 'en' },
    })
    await flushPromises()

    expect((buttonByText(wrapper, 'Apply level').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('Champion')
  })

  it('uses the live entity endpoint when game and player IDs are present', async () => {
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: {
        ruleId: 'dnd2024_srd', character, language: 'zh-CN',
        gameKey: 'web|room|bot', userId: 'gm', revision: 3,
      },
    })
    await flushPromises()
    await buttonByText(wrapper, '确认升级').trigger('click')
    await flushPromises()

    expect(mocks.livePreview).toHaveBeenCalledWith(
      'web|room|bot', 'gm', { hp_method: 'fixed' },
    )
    expect(mocks.liveApply).toHaveBeenCalledWith(
      'web|room|bot', 'gm', { hp_method: 'fixed' }, 3, expect.any(String),
    )
    expect(mocks.apply).not.toHaveBeenCalled()
  })

  it('explains exact spell counts and locks extra selections', async () => {
    mocks.preview.mockResolvedValueOnce({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: false,
        errors: [
          'prepared_spell_refs must contain exactly 5 spells',
          'spellbook_refs must contain at least 8 spells',
          'prepared_spell_refs may replace at most 1 spell(s) at this level',
        ],
        requirements: [{
          id: 'class_spell_choices', kind: 'spell_selection', required: true,
          cantrip_count: 0, prepared_spell_count: 5, spellbook_minimum: 8,
          additions: 5, replace_limit: 1, cantrip_replace_limit: 0,
          spellbook_additions: 2,
          cantrips: [],
          leveled_spells: Array.from({ length: 9 }, (_, index) => ({
            ref: `spell:spell-${index}`, name: `Spell ${index}`, level: 1,
          })),
        }],
        from_level: 1, to_level: 2, class_ref: 'class:wizard',
        source_ref: 'srd-5.2.1:p42:wizard-features', content_version: 'srd-5.2.1+r2',
        diff: { proficiency_bonus: { before: 2, after: 2 }, hp: { gain: 6 }, gained_feature_ids: [], spell_slot_changes: {}, abilities: {} }, snapshot: {},
      },
    })
    const wizard = {
      ...character,
      ruleset_character: {
        ...character.ruleset_character,
        build: { level: 1, class_levels: [{ class_ref: 'class:wizard', level: 1 }] },
        spellcasting: { class: {
          prepared_spell_refs: Array.from({ length: 5 }, (_, index) => `spell:spell-${index}`),
          spellbook_refs: Array.from({ length: 8 }, (_, index) => `spell:spell-${index}`),
        } },
      },
    }
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character: wizard, language: 'zh-CN' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('准备法术需要正好 5 个')
    expect(wrapper.text()).toContain('法术书至少要有 8 个法术')
    expect(wrapper.text()).toContain('法师请先选法术书')
    // 额度来自内容：面板必须把"能新增几个、能换掉几个"说清楚，玩家才不会
    // 在整表重排之后才看到一句服务端拒绝。
    expect(wrapper.text()).toContain('本级新增 5 个法术')
    expect(wrapper.text()).toContain('最多替换 1 个旧法术')
    expect(wrapper.text()).toContain('法术书需新增 2 个')
    expect(wrapper.text()).toContain('本级最多替换 1 个旧法术')
    // 印刷下限是 8，但本级还要再加 2 个：上限是 10，不是 8。
    expect(wrapper.text()).toContain('法术书：已选 8 / 需要 10')
    const extraSpellChoices = wrapper.findAll('label').filter(label => label.text().includes('Spell 8'))
    expect(extraSpellChoices).toHaveLength(2)
    // 准备法术那一栏因为不在书里而锁着，法术书这一栏必须还能加。
    const bookPicks = extraSpellChoices.filter(
      label => label.get('input').attributes('disabled') === undefined,
    )
    expect(bookPicks).toHaveLength(1)
  })

  it('keeps room for the new spells when the book was copied past its floor', async () => {
    mocks.preview.mockResolvedValueOnce({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: false,
        errors: [],
        requirements: [{
          id: 'class_spell_choices', kind: 'spell_selection', required: true,
          cantrip_count: 0, prepared_spell_count: 5, spellbook_minimum: 8,
          additions: 5, replace_limit: 1, cantrip_replace_limit: 0,
          spellbook_additions: 2,
          cantrips: [],
          leveled_spells: Array.from({ length: 11 }, (_, index) => ({
            ref: `spell:spell-${index}`, name: `Spell ${index}`, level: 1,
          })),
        }],
        from_level: 1, to_level: 2, class_ref: 'class:wizard',
        source_ref: 'srd-5.2.1:p42:wizard-features', content_version: 'srd-5.2.1+r2',
        diff: { proficiency_bonus: { before: 2, after: 2 }, hp: { gain: 6 }, gained_feature_ids: [], spell_slot_changes: {}, abilities: {} }, snapshot: {},
      },
    })
    // 抄写过的书 9 本，仍高于 8 的印刷下限。若把下限当上限，9 >= 8 会锁死
    // 每一个新法术，玩家只能删旧法术"腾容量"——那才真的会丢法术。
    const wizard = {
      ...character,
      ruleset_character: {
        ...character.ruleset_character,
        build: { level: 1, class_levels: [{ class_ref: 'class:wizard', level: 1 }] },
        spellcasting: { class: {
          prepared_spell_refs: Array.from({ length: 5 }, (_, index) => `spell:spell-${index}`),
          spellbook_refs: Array.from({ length: 9 }, (_, index) => `spell:spell-${index}`),
        } },
      },
    }
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character: wizard, language: 'zh-CN' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('法术书：已选 9 / 需要 11')
    const next = wrapper.findAll('label').filter(label => label.text().includes('Spell 9'))
    expect(next.length).toBeGreaterThan(0)
    expect(next.some(label => label.get('input').attributes('disabled') === undefined)).toBe(true)
  })
})
